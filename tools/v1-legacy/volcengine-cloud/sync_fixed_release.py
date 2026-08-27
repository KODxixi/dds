#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Materialize one explicitly pinned DDS release into the runtime cache."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_relative_path(value: Any) -> PurePosixPath:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise ValueError(f"unsafe manifest relative_path: {text}")
    return path


def _load_release_meta(
    raw: bytes,
    *,
    release_id: str,
    manifest_key: str,
    manifest_version_id: str,
    manifest_sha256: str,
) -> str:
    try:
        meta = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid release metadata") from exc
    if not isinstance(meta, dict) or meta.get("release_id") != release_id or meta.get("status") != "published":
        raise ValueError("release metadata does not match pinned release")
    evidence = meta.get("object_manifest")
    if not isinstance(evidence, dict):
        raise ValueError("release metadata object manifest is missing")
    if (
        evidence.get("key") != manifest_key
        or evidence.get("version_id") != manifest_version_id
    ):
        raise ValueError("release metadata does not match pinned manifest")
    if evidence.get("sha256") != manifest_sha256:
        raise ValueError("release metadata manifest sha256 does not match pinned manifest")
    encoding = evidence.get("encoding")
    if encoding == "jsonl":
        return encoding
    if encoding == "jsonl+gzip" and evidence.get("hash_scope") == "uncompressed_jsonl":
        return encoding
    raise ValueError("unsupported manifest encoding or hash scope")


def _parse_manifest(raw: bytes, *, encoding: str, expected_sha256: str) -> list[dict[str, Any]]:
    try:
        payload = gzip.decompress(raw) if encoding == "jsonl+gzip" else raw
    except OSError as exc:
        raise ValueError("invalid gzip manifest") from exc
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("manifest sha256 does not match pinned evidence")
    try:
        rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSONL manifest") from exc
    if not rows:
        raise ValueError("manifest must contain at least one object")
    seen_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("manifest row must be an object")
        relative = _validate_relative_path(row.get("relative_path"))
        relative_text = relative.as_posix()
        if relative_text in seen_paths:
            raise ValueError(f"duplicate manifest relative_path: {relative_text}")
        seen_paths.add(relative_text)
        if not isinstance(row.get("object_key"), str) or not row["object_key"].strip():
            raise ValueError("manifest object_key is required")
        if not str(row.get("version_id") or "").strip():
            raise ValueError("manifest object version_id is required")
        sha256 = str(row.get("sha256") or "").lower()
        if not SHA256_RE.fullmatch(sha256):
            raise ValueError("manifest object sha256 is required")
        if isinstance(row.get("size"), bool) or not isinstance(row.get("size"), int) or row["size"] < 0:
            raise ValueError("manifest object size is required")
        row["relative_path"] = relative_text
        row["sha256"] = sha256
    return rows


def sync_release(
    *,
    store: Any,
    release_id: str,
    release_version_id: str,
    manifest_key: str,
    manifest_version_id: str,
    manifest_sha256: str,
    cache_root: str | Path = "/app/cache/releases",
) -> dict[str, Any]:
    """Download one fixed release and publish its fully staged directory."""

    if not RELEASE_ID_RE.fullmatch(str(release_id or "")):
        raise ValueError("invalid release_id")
    if not str(release_version_id or "").strip():
        raise ValueError("release version_id is required")
    if not str(manifest_version_id or "").strip():
        raise ValueError("manifest version_id is required")
    if not str(manifest_sha256 or "").strip():
        raise ValueError("manifest sha256 is required")
    release_key = f"dds/releases/{release_id}/release_meta.json"
    release_bytes = store.get_bytes(release_key, version_id=release_version_id)
    manifest_bytes = store.get_bytes(manifest_key, version_id=manifest_version_id)
    encoding = _load_release_meta(
        release_bytes,
        release_id=release_id,
        manifest_key=manifest_key,
        manifest_version_id=manifest_version_id,
        manifest_sha256=manifest_sha256,
    )
    rows = _parse_manifest(
        manifest_bytes,
        encoding=encoding,
        expected_sha256=manifest_sha256,
    )

    root = Path(cache_root)
    target = root / release_id
    if target.exists():
        raise FileExistsError(f"fixed release cache already exists: {release_id}")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dds-fixed-release-", dir=str(root.parent)))
    try:
        for row in rows:
            content = store.get_bytes(row["object_key"], version_id=row["version_id"])
            if len(content) != row["size"]:
                raise ValueError(f"object size does not match manifest: {row['relative_path']}")
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError(f"object sha256 does not match manifest: {row['relative_path']}")
            destination = staging.joinpath(*PurePosixPath(row["relative_path"]).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        (staging / ".release-complete.json").write_text(
            json.dumps(
                    {
                        "release_id": release_id,
                    "release_version_id": release_version_id,
                    "manifest_key": manifest_key,
                    "manifest_version_id": manifest_version_id,
                    "manifest_sha256": manifest_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        root.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
        return {"status": "ready", "release_id": release_id, "target": str(target)}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
