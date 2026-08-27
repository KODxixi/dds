"""Canonical DDS evidence qualification for general report delivery.

Research agents may discover sources and create candidate records, but they cannot
promote those records.  This module is the DDS-owned validation boundary.  It
checks source bytes, provenance and claim closure and emits the content-addressed
``dds.report-delivery-validation/1.0`` attestation.

The qualification is deliberately narrower than decision validation.  It permits
evidence to support a general research/planning report while keeping every claim
ineligible for investment, pricing, land-bid, IRR or ROI decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


VALIDATION_SCHEMA = "dds.report-delivery-validation/1.0"
AUTHORITY = "DDS"
VALID_STATUS = "validated_for_report_delivery"
REJECTED_STATUS = "rejected"
_IMPLEMENTATION = "dds.report-delivery-evidence-validator/1.0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?key|secret|token|password|authorization|credential)",
    re.I,
)
_SECRET_VALUE_RE = re.compile(
    r"(?:ark-[a-z0-9-]{20,}|AKLT[a-z0-9]{12,}|Bearer\s+[A-Za-z0-9._~+/=-]{12,})",
    re.I,
)
_IMAGE_DATA_URI_RE = re.compile(
    r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+",
    re.I,
)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_RE = re.compile(r"^(?:\\\\|//)[^/\\]+[/\\]")
_SOURCE_METADATA_FIELDS = (
    "source_id",
    "title",
    "publisher",
    "published_at",
    "captured_at",
    "geography",
    "time_window",
    "duplicate_cluster",
    "snapshot_ref",
    "snapshot_hash",
    "raw_hash",
    "limitations",
)


class ReportDeliveryEvidenceError(RuntimeError):
    """Raised when report-delivery evidence cannot receive DDS qualification."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(value: Any) -> str:
    token = str(value or "").strip().lower().removeprefix("sha256:")
    return token if _SHA256_RE.fullmatch(token) else ""


def _list(value: Any) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(value)


def _text_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return True


def _path_chain_is_real(root: Path, target: Path) -> bool:
    try:
        relative = target.relative_to(root)
    except ValueError:
        return False
    cursor = root
    if _is_link_or_junction(cursor):
        return False
    for part in relative.parts:
        cursor = cursor / part
        if _is_link_or_junction(cursor):
            return False
    return True


def _contains_secret(value: Any, *, key: str = "") -> bool:
    if key and _SECRET_KEY_RE.search(key) and value not in (None, "", [], {}):
        return True
    if isinstance(value, str) and _IMAGE_DATA_URI_RE.fullmatch(value):
        # Base64 image bytes are opaque binary. Random JPEG/PNG payloads can
        # contain token-like character runs (for example ``AKlT...``), so only
        # scan human-readable fields for credential patterns.
        return False
    if isinstance(value, Mapping):
        return any(_contains_secret(child, key=str(name)) for name, child in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret(child) for child in value)
    return bool(_SECRET_VALUE_RE.search(str(value or "")))


def _unsafe_identifier(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(
        not text
        or _WINDOWS_ABSOLUTE_RE.match(text)
        or _UNC_RE.match(text)
        or text.lower().startswith("file:")
        or "\\" in text
        or text.startswith("/")
        or _SECRET_VALUE_RE.search(text)
    )


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if not _unsafe_identifier(text):
        return text
    return "redacted:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _canonical_https_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("web_url_not_https")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("web_url_not_canonical")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("web_url_not_canonical") from exc
    if port not in (None, 443):
        raise ValueError("web_url_not_canonical")
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    netloc = hostname
    path = quote(unquote(parsed.path), safe="/:@-._~!$&'()*+,;=")
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(_SECRET_KEY_RE.search(key) for key, _value in query_pairs):
        raise ValueError("web_url_contains_secret")
    query = urlencode(sorted(query_pairs), doseq=True)
    canonical = urlunsplit(("https", netloc, path, query, ""))
    if text != canonical:
        raise ValueError("web_url_not_canonical")
    return canonical


def _subject_payload(evidence: Mapping[str, Any]) -> dict[str, Any]:
    volatile = {
        "batch_hash",
        "package_hash",
        "created_at",
        "updated_at",
        "audit",
        "candidate_validation",
        "report_delivery_validation",
    }
    return {
        str(key): deepcopy(value)
        for key, value in evidence.items()
        if str(key) not in volatile
    }


def _source_rows(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if evidence.get("schema_version") == "dds.evidence-package/1.0":
        rows = evidence.get("source_registry") or evidence.get("sources")
    else:
        rows = evidence.get("sources")
    return [row for row in _list(rows) if isinstance(row, Mapping)]


def _claim_rows(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = (
        evidence.get("claims")
        if evidence.get("schema_version") == "dds.evidence-package/1.0"
        else evidence.get("candidates")
    )
    return [row for row in _list(rows) if isinstance(row, Mapping)]


def _source_kind(source: Mapping[str, Any]) -> str:
    canonical = str(
        source.get("canonical_url")
        or source.get("url")
        or source.get("canonical_ref")
        or ""
    ).strip()
    if canonical.lower().startswith(("http://", "https://")):
        return "web"
    return "local"


def _snapshot_file(project: Path, source: Mapping[str, Any]) -> tuple[Path | None, str | None]:
    ref = str(source.get("snapshot_ref") or "").strip().replace("\\", "/")
    pure = PurePosixPath(ref)
    if not ref or pure.is_absolute() or ".." in pure.parts or ":" in ref:
        return None, "snapshot_ref_invalid"
    work = project / "work"
    root = (work / "source_snapshots").resolve()
    target = (work / Path(*pure.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, "snapshot_outside_source_snapshots"
    if not target.is_file():
        return None, "snapshot_missing"
    if not _path_chain_is_real(root, target):
        return None, "snapshot_is_link"
    return target, None


def _local_relative_ref(source: Mapping[str, Any], *, project_id: str) -> str:
    raw = str(
        source.get("input_ref")
        or source.get("relative_path")
        or source.get("canonical_ref")
        or ""
    ).strip().replace("\\", "/")
    prefix = f"dds://project/{quote(project_id, safe='')}/input/"
    if raw.startswith(prefix):
        raw = "inbox/" + unquote(raw[len(prefix) :])
    elif raw.startswith("dds://project/") and "/input/" in raw:
        raw = "inbox/" + unquote(raw.split("/input/", 1)[1])
    while raw.startswith("./"):
        raw = raw[2:]
    if _WINDOWS_ABSOLUTE_RE.match(raw) or _UNC_RE.match(raw) or raw.startswith("/"):
        raise ValueError("local_source_outside_inbox")
    pure = PurePosixPath(raw)
    if pure.parts and pure.parts[0].casefold() == "inbox":
        pure = PurePosixPath(*pure.parts[1:])
    if not pure.parts or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise ValueError("local_source_outside_inbox")
    return "inbox/" + pure.as_posix()


def _fetch_web_bytes(
    url: str,
    fetcher: Callable[[str], Any] | None,
) -> tuple[bytes, str]:
    if fetcher is not None:
        response = fetcher(url)
        if isinstance(response, Mapping):
            payload = response.get("content") or response.get("body") or response.get("bytes")
            final_url = str(response.get("url") or response.get("final_url") or url)
        elif isinstance(response, tuple) and len(response) == 2:
            payload, final_url = response
        else:
            payload, final_url = response, url
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if not isinstance(payload, (bytes, bytearray)):
            raise ReportDeliveryEvidenceError("web_fetch_invalid_response")
        return bytes(payload), str(final_url)

    request = Request(
        url,
        headers={
            "User-Agent": "DDS-Report-Evidence-Validator/1.0",
            "Accept": "text/html,application/json,text/plain,application/pdf,*/*;q=0.1",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:  # noqa: S310 - validated HTTPS only
            payload = response.read(32 * 1024 * 1024 + 1)
            final_url = response.geturl()
    except Exception as exc:  # Network details may carry tokens or local paths.
        raise ReportDeliveryEvidenceError("web_fetch_failed") from exc
    if len(payload) > 32 * 1024 * 1024:
        raise ReportDeliveryEvidenceError("web_response_too_large")
    return payload, final_url


def _metadata_errors(source: Mapping[str, Any]) -> list[str]:
    errors = [
        f"source_metadata_missing:{field}"
        for field in _SOURCE_METADATA_FIELDS
        if source.get(field) in (None, "", [], {})
    ]
    if source.get("rights_status") in (None, "") and source.get("rights") in (None, ""):
        errors.append("source_metadata_missing:rights")
    if source.get("author_type") in (None, ""):
        errors.append("source_metadata_missing:author_type")
    if source.get("source_type") in (None, ""):
        errors.append("source_metadata_missing:source_type")
    if not _text_list(source.get("limitations")):
        errors.append("source_limitations_missing")
    if not _sha256(source.get("snapshot_hash")):
        errors.append("snapshot_hash_invalid")
    if not _sha256(source.get("raw_hash")):
        errors.append("raw_hash_invalid")
    return errors


def _validate_source(
    source: Mapping[str, Any],
    *,
    project: Path,
    project_id: str,
    phase: str,
    fetcher: Callable[[str], Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    source_id = str(source.get("source_id") or "").strip()
    errors = _metadata_errors(source)
    if _unsafe_identifier(source_id):
        errors.append("unsafe_identifier")
    if _contains_secret(source):
        errors.append("source_contains_secret")
    snapshot, snapshot_error = _snapshot_file(project, source)
    if snapshot_error:
        errors.append(snapshot_error)
        snapshot_bytes = b""
    else:
        assert snapshot is not None
        snapshot_bytes = snapshot.read_bytes()
        if hashlib.sha256(snapshot_bytes).hexdigest() != _sha256(source.get("snapshot_hash")):
            errors.append("snapshot_hash_mismatch")

    kind = _source_kind(source)
    canonical_ref = ""
    raw_hash = _sha256(source.get("raw_hash"))
    if kind == "web":
        candidate_url = source.get("canonical_url") or source.get("url") or source.get("canonical_ref")
        try:
            canonical_ref = _canonical_https_url(candidate_url)
        except ValueError as exc:
            errors.append(str(exc))
        if snapshot_bytes and raw_hash and hashlib.sha256(snapshot_bytes).hexdigest() != raw_hash:
            errors.append("snapshot_raw_hash_mismatch")
        if phase == "freeze" and canonical_ref:
            try:
                fetched, final_url = _fetch_web_bytes(canonical_ref, fetcher)
                final_canonical = _canonical_https_url(final_url)
            except (ReportDeliveryEvidenceError, ValueError) as exc:
                errors.append(str(exc))
            else:
                if final_canonical != canonical_ref:
                    errors.append("web_final_url_mismatch")
                if hashlib.sha256(fetched).hexdigest() != raw_hash:
                    errors.append("web_raw_hash_mismatch")
    else:
        try:
            canonical_ref = _local_relative_ref(source, project_id=project_id)
        except ValueError as exc:
            errors.append(str(exc))
        if canonical_ref:
            inbox = (project / "inbox").resolve()
            relative = PurePosixPath(canonical_ref).relative_to("inbox")
            local = (inbox / Path(*relative.parts)).resolve()
            try:
                local.relative_to(inbox)
            except ValueError:
                errors.append("local_source_outside_inbox")
            else:
                if not local.is_file():
                    errors.append("local_source_missing")
                elif not _path_chain_is_real(inbox, local):
                    errors.append("local_source_is_link")
                elif hashlib.sha256(local.read_bytes()).hexdigest() != raw_hash:
                    errors.append("local_raw_hash_mismatch")

    if errors:
        return None, sorted(set(errors))
    return (
        {
            "source_id": _safe_identifier(source_id),
            "source_kind": kind,
            "canonical_ref": canonical_ref,
            "snapshot_ref": str(source["snapshot_ref"]).replace("\\", "/"),
            "snapshot_hash": _sha256(source["snapshot_hash"]),
            "raw_hash": raw_hash,
            "duplicate_cluster": str(source["duplicate_cluster"]),
            "limitations_hash": _hash(_text_list(source["limitations"])),
            "limitation_count": len(_text_list(source["limitations"])),
        },
        [],
    )


def _claim_errors(claim: Mapping[str, Any], source_ids: set[str]) -> list[str]:
    support_key = "support_source_refs" if "support_source_refs" in claim else "source_refs"
    support = _text_list(claim.get(support_key))
    counters_value = claim.get("counter_source_refs")
    counters = _text_list(counters_value)
    errors: list[str] = []
    if not support:
        errors.append("claim_support_source_refs_missing")
    if not isinstance(counters_value, Sequence) or isinstance(
        counters_value, (str, bytes, bytearray)
    ):
        errors.append("counter_source_refs_missing")
    if set(support + counters) - source_ids:
        errors.append("unresolved_source_ref")
    if not _text_list(claim.get("limitations")):
        errors.append("claim_limitations_missing")
    status = str(claim.get("counter_evidence_status") or "").strip()
    if status not in {
        "searched_none_found",
        "counter_evidence_found",
        "not_applicable_with_reason",
    }:
        errors.append("counter_evidence_status_missing")
    if not str(claim.get("counter_evidence_note") or "").strip():
        errors.append("counter_evidence_note_missing")
    if not str(claim.get("status") or "").strip():
        errors.append("claim_status_missing")
    if claim.get("decision_eligibility") is not False:
        errors.append("claim_decision_eligibility_must_be_false")
    if _contains_secret(claim):
        errors.append("claim_contains_secret")
    return sorted(set(errors))


def _claim_attestation(claim: Mapping[str, Any]) -> dict[str, Any]:
    support_key = "support_source_refs" if "support_source_refs" in claim else "source_refs"
    limitations = _text_list(claim.get("limitations"))
    return {
        "claim_id": _safe_identifier(claim.get("claim_id") or claim.get("record_id")),
        "support_source_refs": [
            _safe_identifier(value) for value in _text_list(claim.get(support_key))
        ],
        "counter_source_refs": [
            _safe_identifier(value)
            for value in _text_list(claim.get("counter_source_refs"))
        ],
        "counter_evidence_status": str(claim.get("counter_evidence_status") or ""),
        "counter_evidence_note_hash": _hash(
            str(claim.get("counter_evidence_note") or "")
        ),
        "limitations_hash": _hash(limitations),
        "limitation_count": len(limitations),
        "claim_status": str(claim.get("status") or ""),
        "decision_eligibility": False,
    }


def _base_result(evidence: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    schema = str(evidence.get("schema_version") or "")
    kind = (
        "EvidencePackage"
        if schema == "dds.evidence-package/1.0"
        else str(evidence.get("kind") or "EvidenceCandidateBatch")
    )
    return {
        "schema_version": VALIDATION_SCHEMA,
        "authority": AUTHORITY,
        "validator": _IMPLEMENTATION,
        "status": status,
        "subject_kind": kind,
        "subject_hash": _hash(_subject_payload(evidence)),
        "project_id": _safe_identifier(evidence.get("project_id")),
        "as_of": str(evidence.get("as_of") or ""),
        "decision_eligibility": False,
        "validated_for_decision": False,
        "allowed_use": "general_research_and_planning_report_only",
        "prohibited_uses": [
            "investment_decision",
            "pricing_commitment",
            "land_bid",
            "irr_or_roi_decision",
        ],
    }


def validate_report_delivery_evidence(
    evidence: Mapping[str, Any],
    *,
    project_dir: str | Path,
    phase: str = "freeze",
    fetcher: Callable[[str], Any] | None = None,
    prior_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate candidates/package and return a deterministic DDS attestation.

    ``freeze`` performs real HTTPS retrieval for web sources.  ``compile`` never
    performs network I/O and requires the prior, content-addressed DDS attestation.
    Both phases re-check local source and snapshot bytes.
    """
    if not isinstance(evidence, Mapping):
        raise TypeError("evidence must be a mapping")
    if phase not in {"freeze", "compile"}:
        raise ValueError("phase must be freeze or compile")
    schema = str(evidence.get("schema_version") or "")
    if schema not in {"dds.evidence-candidate-batch/1.0", "dds.evidence-package/1.0"}:
        raise ReportDeliveryEvidenceError("unsupported_evidence_schema")
    if _contains_secret(evidence):
        result = _base_result(evidence, status=REJECTED_STATUS)
        result.update(
            {
                "reason_codes": ["evidence_contains_secret"],
                "source_attestations": [],
                "claim_attestations": [],
                "claim_ids": [],
            }
        )
        result["validation_hash"] = _hash(result)
        return result

    if phase == "compile":
        if prior_validation is None:
            raise ReportDeliveryEvidenceError("prior_validation_required")
        verify_report_delivery_validation(prior_validation, evidence)

    project = Path(project_dir).expanduser().resolve()
    project_id = str(evidence.get("project_id") or "").strip()
    errors: list[str] = []
    if not project_id:
        errors.append("project_id_missing")
    elif _unsafe_identifier(project_id):
        errors.append("unsafe_identifier")
    if not str(evidence.get("as_of") or "").strip():
        errors.append("as_of_missing")
    if schema == "dds.evidence-candidate-batch/1.0":
        if evidence.get("status") != "pending_dds_validation":
            errors.append("candidate_batch_not_pending_dds_validation")
        if evidence.get("freeze_eligible") is not False:
            errors.append("candidate_batch_claims_freeze_eligibility")
        if _text_list(evidence.get("missing_claim_ids")):
            errors.append("critical_claim_missing")
        if _text_list(evidence.get("conflict_claim_ids")):
            errors.append("authoritative_conflict_unresolved")
    else:
        package_sources = [
            deepcopy(dict(row))
            for row in _list(evidence.get("sources"))
            if isinstance(row, Mapping)
        ]
        package_registry = [
            deepcopy(dict(row))
            for row in _list(evidence.get("source_registry"))
            if isinstance(row, Mapping)
        ]
        package_sources.sort(key=lambda row: str(row.get("source_id") or ""))
        package_registry.sort(key=lambda row: str(row.get("source_id") or ""))
        if package_sources != package_registry:
            errors.append("source_registry_mismatch")

    sources = _source_rows(evidence)
    claims = _claim_rows(evidence)
    if not sources:
        errors.append("sources_missing")
    if not claims:
        errors.append("claims_missing")
    ids = [str(source.get("source_id") or "").strip() for source in sources]
    if any(not source_id for source_id in ids):
        errors.append("source_id_missing")
    if len(ids) != len(set(ids)):
        errors.append("duplicate_source_id")
    if any(_unsafe_identifier(source_id) for source_id in ids):
        errors.append("unsafe_identifier")

    attestations: list[dict[str, Any]] = []
    source_errors: list[dict[str, str]] = []
    # Compile deliberately passes no fetcher and skips the network branch.
    source_phase = phase
    for source in sorted(sources, key=lambda row: str(row.get("source_id") or "")):
        attestation, row_errors = _validate_source(
            source,
            project=project,
            project_id=project_id,
            phase=source_phase,
            fetcher=fetcher if phase == "freeze" else None,
        )
        if attestation:
            attestations.append(attestation)
        source_id = _safe_identifier(source.get("source_id") or "<unknown>")
        for reason in row_errors:
            source_errors.append({"source_id": source_id, "reason_code": reason})
            errors.append(reason)

    claim_errors: list[dict[str, str]] = []
    claim_attestations: list[dict[str, Any]] = []
    source_ids = set(ids)
    claim_ids: list[str] = []
    for claim in sorted(
        claims,
        key=lambda row: str(row.get("claim_id") or row.get("record_id") or ""),
    ):
        claim_id = str(claim.get("claim_id") or claim.get("record_id") or "").strip()
        if not claim_id:
            errors.append("claim_id_missing")
            claim_id = "<unknown>"
        elif _unsafe_identifier(claim_id):
            errors.append("unsafe_identifier")
        safe_claim_id = _safe_identifier(claim_id)
        claim_ids.append(safe_claim_id)
        claim_attestations.append(_claim_attestation(claim))
        for reason in _claim_errors(claim, source_ids):
            claim_errors.append({"claim_id": safe_claim_id, "reason_code": reason})
            errors.append(reason)

    if errors:
        result = _base_result(evidence, status=REJECTED_STATUS)
        result.update(
            {
                "reason_codes": sorted(set(errors)),
                "source_errors": source_errors,
                "claim_errors": claim_errors,
                "source_attestations": attestations,
                "claim_attestations": claim_attestations,
                "claim_ids": sorted(set(claim_ids)),
            }
        )
    else:
        result = _base_result(evidence, status=VALID_STATUS)
        result.update(
            {
                "reason_codes": [],
                "source_attestations": attestations,
                "claim_attestations": claim_attestations,
                "claim_ids": sorted(set(claim_ids)),
                "network_policy": (
                    "https_refetched_and_frozen" if phase == "freeze" else "offline_attestation_verification"
                ),
            }
        )
    result["validation_hash"] = _hash(result)

    if phase == "compile" and not errors:
        # The freeze attestation is the authority-bearing artifact. Compile only
        # rechecks its referenced bytes; it must not mint a second qualification
        # or change the network-policy statement.
        return deepcopy(dict(prior_validation))
    return result


def require_report_delivery_validation(
    evidence: Mapping[str, Any],
    *,
    project_dir: str | Path,
    phase: str = "freeze",
    fetcher: Callable[[str], Any] | None = None,
    prior_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the attestation or raise a redacted, stable reason-code error."""
    result = validate_report_delivery_evidence(
        evidence,
        project_dir=project_dir,
        phase=phase,
        fetcher=fetcher,
        prior_validation=prior_validation,
    )
    if result.get("status") != VALID_STATUS:
        reasons = ",".join(result.get("reason_codes") or ["report_delivery_evidence_rejected"])
        raise ReportDeliveryEvidenceError(reasons)
    return result


def verify_report_delivery_validation(
    validation: Mapping[str, Any], evidence: Mapping[str, Any]
) -> bool:
    """Verify DDS authority, content binding and the permanent decision ceiling."""
    if not isinstance(validation, Mapping):
        raise ReportDeliveryEvidenceError("validation_not_object")
    expected_hash = str(validation.get("validation_hash") or "")
    payload = {key: deepcopy(value) for key, value in validation.items() if key != "validation_hash"}
    if not _SHA256_RE.fullmatch(expected_hash) or expected_hash != _hash(payload):
        raise ReportDeliveryEvidenceError("validation_hash_mismatch")
    if validation.get("schema_version") != VALIDATION_SCHEMA:
        raise ReportDeliveryEvidenceError("validation_schema_mismatch")
    if validation.get("authority") != AUTHORITY or validation.get("validator") != _IMPLEMENTATION:
        raise ReportDeliveryEvidenceError("validation_authority_mismatch")
    if validation.get("status") != VALID_STATUS:
        raise ReportDeliveryEvidenceError("validation_status_mismatch")
    if validation.get("decision_eligibility") is not False or validation.get("validated_for_decision") is not False:
        raise ReportDeliveryEvidenceError("validation_decision_ceiling_broken")
    if validation.get("subject_hash") != _hash(_subject_payload(evidence)):
        raise ReportDeliveryEvidenceError("validation_subject_hash_mismatch")
    return True


__all__ = [
    "AUTHORITY",
    "REJECTED_STATUS",
    "ReportDeliveryEvidenceError",
    "VALIDATION_SCHEMA",
    "VALID_STATUS",
    "require_report_delivery_validation",
    "validate_report_delivery_evidence",
    "verify_report_delivery_validation",
]
