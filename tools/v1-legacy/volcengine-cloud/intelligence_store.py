#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Content-addressed TOS storage contract for DDS intelligence snapshots.

The module is intentionally SDK-agnostic.  Callers inject an object store with
the same small ``stat`` / ``put_bytes`` / ``get_bytes`` interface used by
``sync_dynamic_outputs.py``.  Consequently dry-runs and unit tests work when
the Volcengine TOS SDK is not installed and no cloud connection is available.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "dds.intelligence.snapshot.v1"
ALLOWED_STATUSES = frozenset({"ready", "partial", "missing", "blocked"})
ALLOWED_KINDS = frozenset({"raw_macro", "raw_social", "normalized", "evidence"})

_FORBIDDEN_SECRET_PARTS = frozenset(
    {
        "credential",
        "credentials",
        "token",
        "key",
        "secret",
        "cookie",
        "password",
        "passwd",
    }
)
_PII_KEYS = frozenset(
    {
        "username",
        "user_name",
        "nickname",
        "avatar",
        "avatar_url",
        "phone",
        "phone_number",
        "mobile",
        "mobile_number",
        "email",
        "wechat",
        "wechat_id",
        "account",
        "account_id",
        "author_id",
        "author_name",
        "openid",
        "open_id",
        "unionid",
        "union_id",
        "exact_address",
        "ip",
        "ip_address",
        "profile_url",
    }
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^0-9A-Za-z]+")
_SLUG_SEPARATOR = re.compile(r"[^\w-]+", flags=re.UNICODE)
_SLUG_DASHES = re.compile(r"[-_]{2,}")
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_EMAIL = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])"
)
_SECRET_TEXT = re.compile(
    r"(?i)(?:"
    r"[?&](?:x[-_]?tos[-_]?signature|x[-_]?amz[-_]?signature|"
    r"access[-_]?token|credential|secret|token|password|passwd|cookie|api[-_]?key)="
    r"|\bauthorization\s*:\s*(?:bearer|basic)\s+"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]+"
    r")"
)


def canonical_json(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def safe_slug(value: Any, *, label: str) -> str:
    """Convert a path segment to a traversal-safe, stable Unicode slug."""

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    slug = _SLUG_SEPARATOR.sub("-", raw)
    slug = _SLUG_DASHES.sub("-", slug).strip("-_.")
    if not slug or slug in {".", ".."}:
        raise ValueError(f"{label} must contain a safe path segment")
    if "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"unsafe {label}: {value}")
    return slug[:96].rstrip("-_.")


def _normalized_key(key: Any) -> tuple[str, frozenset[str]]:
    text = _CAMEL_BOUNDARY.sub("_", str(key or ""))
    text = _KEY_SEPARATOR.sub("_", text).strip("_").lower()
    return text, frozenset(part for part in text.split("_") if part)


def _redact_text(value: str) -> str:
    value = _PHONE.sub(lambda match: f"{match.group(1)}****{match.group(2)}", value)
    return _EMAIL.sub(lambda match: f"{match.group(1)}***@{match.group(2)}", value)


def redact_for_storage(value: Any, *, path: str = "payload") -> Any:
    """Recursively reject secrets and redact PII before serialization.

    Secret-bearing fields fail closed because replacing a credential with a
    marker can hide an upstream ingestion bug.  PII/account identifiers are
    retained structurally with a redaction marker so schemas remain auditable.
    """

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized, parts = _normalized_key(key)
            child_path = f"{path}.{key}"
            if parts.intersection(_FORBIDDEN_SECRET_PARTS):
                raise ValueError(f"secret field is prohibited at {child_path}")
            if normalized in _PII_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_for_storage(child, path=child_path)
        return redacted
    if isinstance(value, (list, tuple)):
        return [
            redact_for_storage(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if isinstance(value, str):
        if _SECRET_TEXT.search(value):
            raise ValueError(f"secret text is prohibited at {path}")
        return _redact_text(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"snapshot numbers must be finite at {path}")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError(f"unsupported snapshot value at {path}: {type(value).__name__}")


def _normalize_captured_at(value: str | datetime | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("captured_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


def _snapshot_identity(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"sha256", "object_key"}
    }


def _object_key(identity: Mapping[str, Any], digest: str) -> str:
    captured_date = str(identity["captured_at"])[:10]
    kind = identity["kind"]
    if kind == "raw_social":
        return (
            "raw/intelligence/social/"
            f"{identity['platform']}/{identity['city']}/{captured_date}/{digest}.json"
        )
    if kind == "raw_macro":
        return (
            "raw/intelligence/macro/"
            f"{identity['city']}/{captured_date}/{digest}.json"
        )
    if kind == "normalized":
        scope = identity.get("platform", "general")
        return (
            "normalized/intelligence/"
            f"{scope}/{identity['city']}/{captured_date}/{digest}.json"
        )
    return f"evidence/intelligence/{identity['report_id']}/{digest}.json"


def prepare_snapshot(
    *,
    kind: str,
    payload: Mapping[str, Any] | Sequence[Any],
    captured_at: str | datetime | None,
    rights_status: str,
    source_refs: Sequence[Any],
    status: str,
    city: str | None = None,
    platform: str | None = None,
    report_id: str | None = None,
) -> dict[str, Any]:
    """Build one validated, redacted and content-addressed snapshot."""

    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported intelligence snapshot kind: {kind}")
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
    normalized_rights = str(rights_status or "").strip().lower()
    if not normalized_rights:
        raise ValueError("rights_status is required")
    if isinstance(source_refs, (str, bytes)) or not isinstance(source_refs, Sequence):
        raise ValueError("source_refs must be a sequence")
    if not isinstance(payload, (Mapping, list, tuple)):
        raise ValueError("payload must be an object or array")

    identity: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "captured_at": _normalize_captured_at(captured_at),
        "rights_status": _redact_text(normalized_rights),
        "source_refs": redact_for_storage(list(source_refs), path="source_refs"),
        "status": status,
        "payload": redact_for_storage(payload),
    }

    if kind in {"raw_macro", "raw_social", "normalized"}:
        identity["city"] = safe_slug(city, label="city")
    if kind == "raw_social":
        identity["platform"] = safe_slug(platform, label="platform")
    elif kind == "normalized":
        identity["platform"] = (
            safe_slug(platform, label="platform") if platform else "general"
        )
    elif kind == "evidence":
        identity["report_id"] = safe_slug(report_id, label="report_id")

    digest = hashlib.sha256(canonical_json(identity)).hexdigest()
    return {
        **identity,
        "sha256": digest,
        "object_key": _object_key(identity, digest),
    }


def _validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "kind",
        "captured_at",
        "rights_status",
        "source_refs",
        "status",
        "payload",
        "sha256",
        "object_key",
    }
    missing = sorted(required.difference(snapshot))
    if missing:
        raise ValueError(f"snapshot is missing required fields: {missing}")
    if snapshot["schema_version"] != SCHEMA_VERSION:
        raise ValueError("snapshot schema_version is unsupported")
    if snapshot["kind"] not in ALLOWED_KINDS:
        raise ValueError("snapshot kind is unsupported")
    if snapshot["status"] not in ALLOWED_STATUSES:
        raise ValueError("snapshot status is invalid")
    identity = _snapshot_identity(snapshot)
    digest = hashlib.sha256(canonical_json(identity)).hexdigest()
    if snapshot["sha256"] != digest:
        raise ValueError("snapshot SHA256 verification failed")
    if snapshot["object_key"] != _object_key(identity, digest):
        raise ValueError("snapshot object key verification failed")


def snapshot_bytes(snapshot: Mapping[str, Any]) -> bytes:
    """Serialize a snapshot after checking its digest and object key."""

    _validate_snapshot(snapshot)
    return canonical_json(dict(snapshot))


def _remote_size(response: Any) -> int | None:
    if isinstance(response, Mapping):
        value = response.get("size", response.get("content_length"))
    else:
        value = getattr(response, "size", None)
        if value is None:
            value = getattr(response, "content_length", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _version_id(response: Any) -> str | None:
    if isinstance(response, Mapping):
        value = response.get("version_id")
    else:
        value = getattr(response, "version_id", None)
    return str(value) if value not in {None, ""} else None


def publish_snapshot(
    snapshot: Mapping[str, Any],
    *,
    store: Any | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Create an offline manifest or idempotently upload and verify a snapshot."""

    content = snapshot_bytes(snapshot)
    object_key = str(snapshot["object_key"])
    result: dict[str, Any] = {
        "status": "dry_run",
        "snapshot_status": snapshot["status"],
        "schema_version": SCHEMA_VERSION,
        "sha256": snapshot["sha256"],
        "object_key": object_key,
        "byte_size": len(content),
        "uploaded": False,
        "verified": False,
        "version_id": None,
    }
    if not execute:
        return result
    if store is None:
        raise ValueError("object store is required when execute=True")

    remote = store.stat(object_key)
    if remote is None:
        response = store.put_bytes(
            object_key,
            content,
            content_type="application/json",
        )
        result["uploaded"] = True
        result["version_id"] = _version_id(response)
    else:
        remote_size = _remote_size(remote)
        if remote_size is not None and remote_size != len(content):
            raise ValueError("snapshot verification failed: remote size mismatch")
        result["version_id"] = _version_id(remote)

    remote_content = store.get_bytes(
        object_key,
        version_id=result["version_id"],
    )
    if bytes(remote_content) != content:
        raise ValueError("snapshot verification failed: remote content mismatch")
    try:
        decoded = json.loads(bytes(remote_content).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("snapshot verification failed: remote JSON is invalid") from exc
    if not isinstance(decoded, dict):
        raise ValueError("snapshot verification failed: remote JSON must be an object")
    _validate_snapshot(decoded)

    result["status"] = snapshot["status"]
    result["verified"] = True
    return result


__all__ = [
    "ALLOWED_KINDS",
    "ALLOWED_STATUSES",
    "SCHEMA_VERSION",
    "canonical_json",
    "prepare_snapshot",
    "publish_snapshot",
    "redact_for_storage",
    "safe_slug",
    "snapshot_bytes",
]
