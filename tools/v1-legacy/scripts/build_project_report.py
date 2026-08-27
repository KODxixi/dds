"""Build, freeze and export a deterministic DDS project report bundle.

The online/input phase writes project-local governance artifacts under
``<project>/work``.  ``--compile-only`` consumes the frozen EvidencePackage,
including frozen media bytes, without network access or other project reads.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROJECT_MANIFEST_SCHEMA = "dds.project-manifest/1.0"
NORMALIZATION_OVERRIDES_SCHEMA = "dds.normalization-overrides/1.0"
RESEARCH_REQUEST_SCHEMA = "dds.research-request/1.0"
EVIDENCE_PACKAGE_SCHEMA = "dds.evidence-package/1.0"
REPORT_SEED_SCHEMA = "dds.report-seed/1.0"
REPORT_COMPILER_INPUT_VERSION = "dds.report-seed/1.0"
SUPPORTED_PROFILE = "dds.apple-dark-16x9/1.1.0"
SUPPORTED_EXPORTS = ("interactive", "portable_single_file")
SUPPORTED_RESEARCH = ("auto", "cached", "off")
TOS_DELIVERY_REGION = "cn-shanghai"
DELIVERY_STATUS_SCHEMA = "dds.delivery-status/1.0"

_MODULE_KEYS = (
    "macro_context",
    "city_land_market",
    "competitor_series",
    "gis_site",
    "social_intelligence",
    "persona_evidence_profiles",
    "traditional_spatial_culture",
    "archlib_case_evidence",
    "premium_analysis_inputs",
    "absorption_forecast_inputs",
    "investment_case_inputs",
    "report_seed",
)


class ProjectReportError(RuntimeError):
    """Base error for project-report preparation and compilation."""


class ProjectDirectoryError(ProjectReportError):
    """The requested project root cannot be used safely."""


class FrozenEvidencePackageError(ProjectReportError):
    """A compile-only EvidencePackage is missing or fails integrity checks."""


class TosSyncUnavailable(RuntimeError):
    """A requested TOS sync cannot run in the current local runtime."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _json_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _contract_hash(value: Any) -> str:
    """Hash public contracts exactly as the standalone auditors do."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectReportError(f"cannot read {label} at {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ProjectReportError(f"{label} must be a JSON object: {path}")
    return deepcopy(dict(value))


def _write_stable_json(path: Path, value: Any) -> None:
    payload = _canonical_bytes(value)
    if path.is_file() and path.read_bytes() == payload:
        return
    path.write_bytes(payload)


def _write_stable_bytes(path: Path, payload: bytes) -> None:
    if path.is_file() and path.read_bytes() == payload:
        return
    path.write_bytes(payload)


def _sanitize_portable_value(value: Any) -> Any:
    """Use the canonical ReportDocument sanitizer at every persistence edge."""
    try:
        from report_document import sanitize_portable_value
    except ImportError:
        from scripts.report_document import sanitize_portable_value

    return sanitize_portable_value(value)


def _normalize_as_of(value: str | date) -> str:
    text = value.isoformat() if isinstance(value, date) else str(value).strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ProjectReportError("as_of must be an ISO date in YYYY-MM-DD form") from exc
    if parsed.isoformat() != text:
        raise ProjectReportError("as_of must be an ISO date in YYYY-MM-DD form")
    return text


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return True


def _project_child_dir(
    project: Path, path: Path, *, label: str, require_existing: bool = False
) -> Path:
    if path.exists():
        if not path.is_dir() or _is_link_or_junction(path):
            raise ProjectDirectoryError(
                f"project {label} path must be a real directory: {path}"
            )
    elif require_existing:
        raise ProjectDirectoryError(f"project {label} directory is required: {path}")
    try:
        path.resolve(strict=False).relative_to(project.resolve())
    except ValueError as exc:
        raise ProjectDirectoryError(
            f"project {label} path escapes the project directory: {path}"
        ) from exc
    return path


def _validate_project_dir(project_dir: str | Path) -> tuple[Path, Path, Path]:
    project = Path(project_dir).expanduser()
    if not project.exists() or not project.is_dir():
        raise ProjectDirectoryError(f"project directory does not exist: {project}")
    project = project.resolve()
    inbox = project / "inbox"
    if (
        not inbox.exists()
        or not inbox.is_dir()
        or _is_link_or_junction(inbox)
    ):
        raise ProjectDirectoryError(
            f"project inbox directory is required and must not be a symlink: {inbox}"
        )
    work = project / "work"
    _project_child_dir(project, work, label="work")
    return project, inbox, work


def _validate_compile_project_dir(project_dir: str | Path) -> tuple[Path, Path]:
    project = Path(project_dir).expanduser()
    if not project.exists() or not project.is_dir():
        raise ProjectDirectoryError(f"project directory does not exist: {project}")
    project = project.resolve()
    work = project / "work"
    _project_child_dir(project, work, label="work")
    return project, work


def _default_project_id(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).strip()
    slug = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE).strip("-._")
    slug = slug[:48] or "project"
    suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{suffix}"


def _normalize_input_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if parts and parts[0].casefold() == "inbox":
        parts = parts[1:]
    if not parts or any(part == ".." or ":" in part for part in parts):
        raise ProjectReportError(f"normalization override is not an inbox path: {value!r}")
    return "inbox/" + "/".join(parts)


def _normalization_maps(
    overrides: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    derived: dict[str, str] = {}
    families: dict[str, str] = {}

    for key in ("derived_from", "derivations"):
        mapping = overrides.get(key)
        if isinstance(mapping, Mapping):
            for child, parent in mapping.items():
                if isinstance(parent, Mapping):
                    parent = parent.get("derived_from") or parent.get("source")
                if parent:
                    derived[_normalize_input_ref(child)] = _normalize_input_ref(parent)

    mapping = overrides.get("evidence_family")
    if isinstance(mapping, Mapping):
        for path, family in mapping.items():
            if str(family or "").strip():
                families[_normalize_input_ref(path)] = str(family).strip()

    file_overrides = overrides.get("files") or overrides.get("assets")
    if isinstance(file_overrides, Mapping):
        entries = file_overrides.items()
    elif isinstance(file_overrides, Sequence) and not isinstance(
        file_overrides, (str, bytes, bytearray)
    ):
        entries = (
            (entry.get("path") or entry.get("relative_path"), entry)
            for entry in file_overrides
            if isinstance(entry, Mapping)
        )
    else:
        entries = ()
    for raw_path, entry in entries:
        if not raw_path or not isinstance(entry, Mapping):
            continue
        path = _normalize_input_ref(raw_path)
        parent = entry.get("derived_from") or entry.get("source")
        if parent:
            derived[path] = _normalize_input_ref(parent)
        family = entry.get("evidence_family")
        if str(family or "").strip():
            families[path] = str(family).strip()
    return derived, families


def _load_normalization_overrides(work: Path) -> dict[str, Any]:
    path = work / "normalization_overrides.json"
    if path.exists():
        value = _read_json_object(path, label="normalization overrides")
        if value.get("schema_version") != NORMALIZATION_OVERRIDES_SCHEMA:
            raise ProjectReportError(
                "normalization overrides has an unsupported schema_version"
            )
        return value
    value: dict[str, Any] = {
        "schema_version": NORMALIZATION_OVERRIDES_SCHEMA,
        "derived_from": {},
        "evidence_family": {},
    }
    _write_stable_json(path, value)
    return value


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _raw_file_records(inbox: Path) -> list[dict[str, Any]]:
    files: list[Path] = []
    for root, directories, names in os.walk(inbox, followlinks=False):
        root_path = Path(root)
        directories[:] = sorted(
            name
            for name in directories
            if not (root_path / name).is_symlink()
        )
        files.extend(
            root_path / name
            for name in sorted(names)
            if not (root_path / name).is_symlink()
            and (root_path / name).is_file()
        )

    records: list[dict[str, Any]] = []
    for path in sorted(files, key=lambda item: item.relative_to(inbox).as_posix().casefold()):
        digest, size = _file_digest(path)
        input_path = path.relative_to(inbox).as_posix()
        relative_path = f"inbox/{input_path}"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        records.append(
            {
                "relative_path": relative_path,
                "input_relative_path": input_path,
                "sha256": digest,
                "mime": mime,
                "mime_type": mime,
                "extension": path.suffix.lower(),
                "size": size,
                "size_bytes": size,
                "source_id": f"src-{digest[:24]}",
            }
        )
    return records


def _apply_evidence_families(
    records: list[dict[str, Any]], overrides: Mapping[str, Any]
) -> list[dict[str, Any]]:
    derived, explicit_families = _normalization_maps(overrides)
    by_path = {str(record["relative_path"]): record for record in records}
    for child, parent in derived.items():
        if child not in by_path:
            raise ProjectReportError(f"derived_from child does not exist: {child}")
        if parent not in by_path:
            raise ProjectReportError(f"derived_from parent does not exist: {parent}")

    content_families: dict[str, str] = {}
    for path, family in explicit_families.items():
        record = by_path.get(path)
        if not record:
            continue
        digest = str(record["sha256"])
        previous = content_families.get(digest)
        if previous and previous != family:
            raise ProjectReportError(
                f"conflicting evidence families for identical content: {path}"
            )
        content_families[digest] = family

    def root_path(path: str) -> str:
        seen: set[str] = set()
        current = path
        while current in derived:
            if current in seen:
                raise ProjectReportError(f"derived_from cycle detected at {current}")
            seen.add(current)
            current = derived[current]
        return current

    def original_family(path: str) -> str:
        record = by_path.get(path)
        if path in explicit_families:
            return explicit_families[path]
        if record:
            digest = str(record["sha256"])
            return content_families.get(digest) or f"family-{digest[:24]}"
        return "family-" + hashlib.sha256(
            f"unavailable-parent:{path}".encode("utf-8")
        ).hexdigest()[:24]

    for record in records:
        path = str(record["relative_path"])
        is_derived = path in derived
        if path in explicit_families:
            family = explicit_families[path]
        elif is_derived:
            family = original_family(root_path(path))
        else:
            family = original_family(path)
        record["evidence_family"] = family
        record["is_derived"] = is_derived
        record["derived_from"] = derived.get(path)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["evidence_family"]), []).append(record)
    for members in grouped.values():
        originals = sorted(
            (member for member in members if not member["is_derived"]),
            key=lambda item: str(item["relative_path"]).casefold(),
        )
        representative = originals[0] if originals else None
        representative_path = (
            str(representative["relative_path"]) if representative else None
        )
        for member in members:
            independent = member is representative
            member["counts_as_independent_evidence"] = independent
            member["is_duplicate"] = not independent
            member["duplicate_of"] = (
                None if independent else representative_path or member.get("derived_from")
            )
    return records


def _family_summaries(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["evidence_family"]), []).append(record)
    summaries: list[dict[str, Any]] = []
    for family in sorted(grouped):
        members = sorted(grouped[family], key=lambda item: str(item["relative_path"]))
        primary = next(
            (member for member in members if member["counts_as_independent_evidence"]),
            None,
        )
        summaries.append(
            {
                "evidence_family": family,
                "independent_weight": 1 if primary else 0,
                "primary_relative_path": primary.get("relative_path") if primary else None,
                "representative_source_id": primary.get("source_id") if primary else None,
                "member_paths": [member["relative_path"] for member in members],
            }
        )
    return summaries


def _load_existing_manifest(path: Path) -> dict[str, Any]:
    return _read_json_object(path, label="project manifest") if path.exists() else {}


def _build_manifest(
    project: Path,
    manifest_path: Path,
    records: list[dict[str, Any]],
    as_of: str,
) -> dict[str, Any]:
    manifest = _load_existing_manifest(manifest_path)
    defaults: dict[str, Any] = {
        "schema_version": PROJECT_MANIFEST_SCHEMA,
        "project_id": _default_project_id(project.name),
        "name": project.name,
        "aliases": [],
        "city": "",
        "district": "",
        "site_description": "",
        "coordinate": {"wgs84": None, "gcj02": None},
        "status": "draft",
        "audience": ["internal"],
    }
    for key, value in defaults.items():
        manifest.setdefault(key, deepcopy(value))
    if not str(manifest.get("project_id") or "").strip():
        manifest["project_id"] = defaults["project_id"]
    if not str(manifest.get("name") or "").strip():
        manifest["name"] = defaults["name"]
    manifest["schema_version"] = PROJECT_MANIFEST_SCHEMA
    manifest["as_of"] = as_of
    manifest["input_root"] = "inbox"
    manifest["files"] = records
    manifest["evidence_families"] = _family_summaries(records)
    manifest.pop("manifest_hash", None)
    manifest["manifest_hash"] = _json_hash(manifest)
    return manifest


def _owner_hash(owner_id: str) -> str:
    owner = str(owner_id or "").strip()
    if not owner:
        raise ProjectReportError("owner_id must not be empty")
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _source_ref(project_id: str, relative_path: str) -> str:
    return (
        "dds://project/"
        + quote(project_id, safe="")
        + "/input/"
        + quote(relative_path.removeprefix("inbox/"), safe="/")
    )


def _independent_records(
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes, bytearray)):
        return []
    return [
        deepcopy(dict(record))
        for record in files
        if isinstance(record, Mapping)
        and bool(record.get("counts_as_independent_evidence"))
    ]


def _input_refs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    project_id = str(manifest["project_id"])
    return [
        {
            "source_id": record["source_id"],
            "ref": _source_ref(project_id, str(record["relative_path"])),
            "sha256": record["sha256"],
            "evidence_family": record["evidence_family"],
        }
        for record in _independent_records(manifest)
    ]


def _coordinate_values(manifest: Mapping[str, Any]) -> tuple[Any, Any]:
    coordinate = manifest.get("coordinate")
    if isinstance(coordinate, Mapping):
        return deepcopy(coordinate.get("wgs84")), deepcopy(coordinate.get("gcj02"))
    if isinstance(coordinate, Sequence) and not isinstance(
        coordinate, (str, bytes, bytearray)
    ):
        return deepcopy(coordinate), None
    return None, None


def _build_research_request(
    manifest: Mapping[str, Any],
    *,
    owner_id: str,
    research: str,
    report_seed: Mapping[str, Any] | None,
) -> dict[str, Any]:
    wgs84, gcj02 = _coordinate_values(manifest)
    owner_hash = _owner_hash(owner_id)
    analysis_context_hash = _json_hash(
        {
            "manifest_hash": manifest["manifest_hash"],
            "report_seed_hash": _json_hash(report_seed) if report_seed else None,
        }
    )
    subject = {
        "project_name": manifest.get("name") or "",
        "city": manifest.get("city") or "",
        "district": manifest.get("district") or "",
        "address": manifest.get("address") or "",
        "site_description": manifest.get("site_description") or "",
        "wgs84": wgs84,
        "gcj02": gcj02,
        "site_boundary_ref": manifest.get("site_boundary_ref"),
        # A ResearchRequest must keep the missing state explicit instead of
        # silently emitting a blank contract field. Project manifests can
        # replace this sentinel as soon as the statutory land-use condition is
        # known.
        "land_use": manifest.get("land_use") or "unknown_unverified",
    }
    source_policy = {
        "mode": research,
        "allowed_source_classes": ["project_input", "dds_validated_cache"],
        "network_allowed": research == "auto",
        "search_snippets_are_leads_only": True,
    }
    provider_policy = {
        "preferred_region": "cn-shanghai",
        "fallback_region": "cn-beijing" if research == "auto" else "disabled",
        "cross_region_redaction": research == "auto",
        "budget": {"currency": None, "maximum": 0},
        "timeout_seconds": 30 if research == "auto" else 0,
        "max_queries": 8 if research == "auto" else 0,
        "tool_whitelist": (
            [
                "web_search",
                "web_fetch",
                "dds_get_project_context",
                "dds_read_project_document",
                "dds_query_city_dataset",
                "dds_query_competitor_series",
                "dds_query_amap_gis",
                "dds_search_archlib",
                "dds_get_cached_evidence",
            ]
            if research == "auto"
            else []
        ),
    }
    fingerprint_fields = {
        "project_id": manifest["project_id"],
        "owner_hash": owner_hash,
        "subject": subject,
        "as_of": manifest["as_of"],
        "window_days": 180,
        "comparison_windows": [30, 90, 180],
        "modules": list(_MODULE_KEYS),
        "question_set": [
            "哪些项目输入已形成可审计证据，哪些仍需语义核验？",
            "哪些缺口会阻断设计、价值或投资判断？",
        ],
        "source_policy": source_policy,
        "input_refs": _input_refs(manifest),
        "analysis_context_hash": analysis_context_hash,
        "provider_policy": provider_policy,
    }
    fingerprint = _contract_hash(
        {
            key: fingerprint_fields[key]
            for key in (
                "project_id",
                "owner_hash",
                "subject",
                "as_of",
                "window_days",
                "modules",
                "question_set",
                "source_policy",
                "input_refs",
                "analysis_context_hash",
                "provider_policy",
            )
        }
    )
    return {
        "schema_version": RESEARCH_REQUEST_SCHEMA,
        "request_id": f"request-{fingerprint[:24]}",
        "project_id": manifest["project_id"],
        "job_id": f"job-{fingerprint[24:48]}",
        "owner_hash": owner_hash,
        "subject": subject,
        "as_of": manifest["as_of"],
        "window_days": fingerprint_fields["window_days"],
        "comparison_windows": fingerprint_fields["comparison_windows"],
        "modules": fingerprint_fields["modules"],
        "question_set": fingerprint_fields["question_set"],
        "source_policy": source_policy,
        "input_refs": fingerprint_fields["input_refs"],
        "analysis_context_hash": analysis_context_hash,
        "request_fingerprint": fingerprint,
        "provider_policy": provider_policy,
    }


def _source_snapshot_payload(
    manifest: Mapping[str, Any], source: Mapping[str, Any]
) -> dict[str, Any]:
    family = str(source.get("duplicate_cluster") or "")
    family_records = [
        record
        for record in manifest.get("files", [])
        if isinstance(record, Mapping)
        and str(record.get("evidence_family") or "") == family
    ]
    return {
        "schema_version": "dds.local-source-snapshot/1.0",
        "source_id": source["source_id"],
        "canonical_ref": source["canonical_ref"],
        "sha256": source["sha256"],
        "content_addressed": True,
        "captured_at": source["captured_at"],
        "evidence_family": family,
        "members": [
            {
                "relative_path": str(record.get("relative_path") or ""),
                "sha256": str(record.get("sha256") or ""),
                "is_derived": bool(record.get("is_derived")),
            }
            for record in sorted(
                family_records,
                key=lambda item: str(item.get("relative_path") or "").casefold(),
            )
        ],
        "retention_note": (
            "The caller-owned inbox stays read-only; this snapshot freezes hashes "
            "and project-relative lineage, not a duplicate of the source bytes."
        ),
    }


def _source_registry(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    project_id = str(manifest["project_id"])
    city = manifest.get("city") or ""
    district = manifest.get("district") or ""
    sources: list[dict[str, Any]] = []
    all_records = [
        record
        for record in manifest.get("files", [])
        if isinstance(record, Mapping)
    ]
    for record in _independent_records(manifest):
        family = str(record["evidence_family"])
        members = sorted(
            str(item["relative_path"])
            for item in all_records
            if str(item.get("evidence_family") or "") == family
        )
        canonical_ref = _source_ref(project_id, str(record["relative_path"]))
        source = {
                "source_id": record["source_id"],
                "canonical_ref": canonical_ref,
                "title": Path(str(record["relative_path"])).name,
                "publisher": "project_owner",
                "author_type": "provided_input",
                "source_type": record["mime_type"],
                "trust_tier": "provided_unverified",
                "published_at": None,
                "captured_at": f"{manifest['as_of']}T00:00:00Z",
                "geography": {"city": city, "district": district},
                "time_window": {"as_of": manifest["as_of"]},
                "rights_status": "caller_authorized",
                "duplicate_cluster": family,
                "snapshot_ref": f"source_snapshots/{record['source_id']}.json",
                "sha256": record["sha256"],
                "raw_hash": record["sha256"],
                "used_for": ["report_seed"],
                "limitations": ["仅完成文件级完整性与去重，尚未完成语义核验。"],
                "status": "registered",
                "member_refs": [
                    _source_ref(project_id, member) for member in members
                ],
            }
        source["snapshot_hash"] = _json_hash(
            _source_snapshot_payload(manifest, source)
        )
        sources.append(source)
    return sources


def _write_source_snapshot_metadata(
    work: Path, manifest: Mapping[str, Any]
) -> None:
    """Freeze content-addressed metadata without copying large caller files."""
    snapshot_dir = _project_child_dir(
        work.parent, work / "source_snapshots", label="source_snapshots"
    )
    snapshot_dir.mkdir(exist_ok=True)
    for source in _source_registry(manifest):
        payload = _source_snapshot_payload(manifest, source)
        _write_stable_json(snapshot_dir / f"{source['source_id']}.json", payload)


def _package_gaps(
    has_sources: bool, has_seed: bool, has_semantic_claims: bool = False
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not has_sources:
        gaps.append(
            {
                "gap_id": "gap-project-inputs",
                "module_id": "report_seed",
                "status": "open",
                "statement": "项目 inbox 中没有可登记的原始输入。",
                "recommended_action": "补充授权原始资料后重新冻结证据包。",
            }
        )
    if not has_semantic_claims:
        gaps.append(
            {
                "gap_id": "gap-semantic-validation",
                "module_id": "report_seed",
                "status": "open",
                "statement": "文件清单尚未形成可投决的语义 claim。",
                "recommended_action": "完成来源核验、claim 闭合和模块级证据治理。",
            }
        )
    if not has_seed:
        gaps.append(
            {
                "gap_id": "gap-declarative-report-seed",
                "module_id": "report_seed",
                "status": "open",
                "statement": "未提供项目级 report_seed，将使用通用缺口骨架。",
                "recommended_action": "按需在 work/report_seed.json 声明项目差异。",
            }
        )
    return gaps


def _module_report_seed(
    *,
    report_seed: Mapping[str, Any] | None,
    manifest: Mapping[str, Any],
    source_ids: Sequence[str],
    gaps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    seed = deepcopy(dict(report_seed)) if report_seed else {"schema_version": REPORT_SEED_SCHEMA}
    project = {
        key: deepcopy(manifest.get(key))
        for key in (
            "project_id",
            "name",
            "aliases",
            "city",
            "district",
            "site_description",
            "coordinate",
            "status",
            "as_of",
            "audience",
            "address",
            "land_use",
        )
        if key in manifest
    }
    declared_project = seed.get("project")
    if isinstance(declared_project, Mapping):
        project.update(deepcopy(dict(declared_project)))
    seed["project"] = project
    seed.setdefault("status", "partial" if source_ids or report_seed else "missing")
    seed.setdefault("input_refs", list(source_ids))
    seed.setdefault("method", "declarative_seed_and_content_addressed_project_inputs")
    seed.setdefault("assumptions", [])
    seed.setdefault(
        "gaps",
        [gap["gap_id"] for gap in gaps if gap.get("module_id") == "report_seed"],
    )
    seed.setdefault(
        "allowed_outputs",
        ["evidence_inventory", "gap_analysis", "non_actionable_report_skeleton"],
    )
    seed.setdefault("decision_eligibility", False)
    return seed


def compute_package_hash(package: Mapping[str, Any]) -> str:
    """Return the stable content hash, excluding volatile package metadata."""
    if not isinstance(package, Mapping):
        raise TypeError("package must be a mapping")
    # Keep this aligned with the public EvidencePackage audit contract. Runtime
    # timestamps and audit annotations are operational metadata, not semantic
    # compiler input.
    volatile = {"package_hash", "created_at", "updated_at", "audit"}
    payload = {key: deepcopy(value) for key, value in package.items() if key not in volatile}
    return _contract_hash(payload)


def _normalized_seed_sources(
    report_seed: Mapping[str, Any] | None,
    *,
    manifest: Mapping[str, Any],
    fallback_sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Promote curated report-seed sources into the frozen evidence contract."""
    supplied = report_seed.get("source_registry") if isinstance(report_seed, Mapping) else None
    raw_sources = (
        list(supplied)
        if isinstance(supplied, Sequence)
        and not isinstance(supplied, (str, bytes, bytearray))
        and supplied
        else list(fallback_sources)
    )
    city = str(manifest.get("city") or "")
    district = str(manifest.get("district") or "")
    as_of = str(manifest.get("as_of") or "")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, Mapping):
            continue
        source = _sanitize_portable_value(dict(raw))
        source_id = str(source.get("source_id") or f"source-{index:04d}")
        if source_id in seen:
            continue
        seen.add(source_id)
        canonical_ref = str(
            source.get("canonical_ref")
            or source.get("canonical_url")
            or f"dds://project/{quote(str(manifest['project_id']), safe='')}/source/{quote(source_id, safe='')}"
        )
        source_type = str(source.get("source_type") or "curated_snapshot")
        if source.get("author_type"):
            author_type = str(source["author_type"])
        elif source_type == "provided_input":
            author_type = "provided_input"
        elif source_type == "official_web":
            author_type = "official_institution"
        else:
            author_type = "institution_or_dataset"
        trust_tier = str(source.get("trust_tier") or "provided_unverified")
        claims = [str(value) for value in source.get("claims", []) if str(value).strip()]
        raw_hash = str(
            source.get("raw_hash")
            or source.get("content_hash")
            or source.get("sha256")
            or _json_hash(
                {
                    "source_id": source_id,
                    "canonical_ref": canonical_ref,
                    "claims": claims,
                }
            )
        ).removeprefix("sha256:")
        snapshot_hash = str(
            source.get("snapshot_hash")
            or source.get("content_hash")
            or _json_hash(
                {
                    "source_id": source_id,
                    "canonical_ref": canonical_ref,
                    "raw_hash": raw_hash,
                    "as_of": as_of,
                }
            )
        ).removeprefix("sha256:")
        status = str(source.get("status") or "partial")
        if status not in {"ready", "partial", "missing", "blocked", "rejected"}:
            status = "partial"
        limitations = source.get("limitations")
        if not isinstance(limitations, list):
            limitations = [] if limitations in (None, "") else [str(limitations)]
        used_for = source.get("used_for")
        if not isinstance(used_for, list) or not used_for:
            used_for = ["report_seed"]
        normalized_source = {
            **source,
            "source_id": source_id,
            "canonical_ref": canonical_ref,
            "title": str(source.get("title") or source_id),
            "publisher": str(source.get("publisher") or "registered_source"),
            "author_type": author_type,
            "source_type": source_type,
            "trust_tier": trust_tier,
            # Some caller-provided documents do not expose a publication date.
            # The freeze date is an explicit placeholder and the limitation
            # prevents it from being used as a freshness signal.
            "published_at": str(source.get("published_at") or as_of),
            "captured_at": str(source.get("captured_at") or f"{as_of}T00:00:00Z"),
            "geography": deepcopy(
                source.get("geography") or {"city": city, "district": district}
            ),
            "time_window": deepcopy(source.get("time_window") or {"as_of": as_of}),
            "rights_status": str(
                source.get("rights_status")
                or ("public_reference" if canonical_ref.startswith(("http://", "https://")) else "caller_authorized")
            ),
            "duplicate_cluster": str(
                source.get("duplicate_cluster") or f"curated:{source_id}"
            ),
            "snapshot_ref": str(
                source.get("snapshot_ref") or f"source_snapshots/{source_id}.json"
            ),
            "snapshot_hash": snapshot_hash,
            "raw_hash": raw_hash,
            "used_for": [str(value) for value in used_for if str(value).strip()],
            "limitations": [str(value) for value in limitations if str(value).strip()],
            "status": status,
            "claims": claims,
        }
        if not source.get("published_at"):
            normalized_source["limitations"].append(
                "原始发布日期未解析；published_at 暂用冻结日且不得用于新鲜度评分。"
            )
        normalized.append(normalized_source)
    return normalized


def _claim_module(source_id: str) -> str:
    token = source_id.upper()
    if "FINANCE" in token:
        return "investment_case"
    if any(value in token for value in ("AMAP", "GIS", "SITE")):
        return "site"
    if "ARCHLIB" in token:
        return "architecture_design"
    if any(value in token for value in ("MARKET", "COMMERCIAL", "NEWHOUSE")):
        return "competitor_series"
    if any(value in token for value in ("NBS", "OFFICIAL")):
        return "macro_context"
    return "report_seed"


def _claims_from_sources(sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for source in sources:
        source_id = str(source.get("source_id") or "")
        for index, statement in enumerate(source.get("claims") or []):
            text = str(statement).strip()
            if not text:
                continue
            claim_hash = _json_hash(
                {"source_id": source_id, "index": index, "statement": text}
            )
            confidence = source.get("confidence", 0.5)
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                confidence = 0.5
            confidence = max(0.0, min(float(confidence), 1.0))
            claims.append(
                {
                    "claim_id": f"claim-{claim_hash[:24]}",
                    "statement": text,
                    "evidence_type": "observed_fact",
                    "module_id": _claim_module(source_id),
                    "support_source_refs": [source_id],
                    "counter_source_refs": [],
                    "geography": deepcopy(source.get("geography") or {"scope": "registered"}),
                    "time_window": deepcopy(source.get("time_window") or {"status": "as_of"}),
                    "method": "curated_source_claim_extraction",
                    "allowed_uses": ["report_seed", "gap_analysis"],
                    "prohibited_uses": ["unqualified_financial_calculation"],
                    "confidence": confidence,
                    "limitations": deepcopy(source.get("limitations") or []),
                    "decision_eligibility": False,
                    "status": "partial",
                }
            )
    return claims


def _compiler_fingerprint(
    profile: str = SUPPORTED_PROFILE,
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    component_paths = {
        "report_document.py": root / "scripts" / "report_document.py",
        "dds_cinematic_deck.py": root / "scripts" / "dds_cinematic_deck.py",
        "dds_report_apple_16x9.html": root
        / "templates"
        / "dds_report_apple_16x9.html",
        "report_template_profile_v1.json": root
        / "data"
        / "report_template_profile_v1.json",
    }
    components = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in sorted(component_paths.items())
    }
    payload = {"profile": profile, "components": components}
    return {**payload, "fingerprint": _json_hash(payload)}


def _build_evidence_package(
    manifest: Mapping[str, Any],
    request: Mapping[str, Any],
    report_seed: Mapping[str, Any] | None,
    project_panorama: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_sources = _source_registry(manifest)
    sources = _normalized_seed_sources(
        report_seed,
        manifest=manifest,
        fallback_sources=manifest_sources,
    )
    claims = _claims_from_sources(sources)
    gaps = _package_gaps(
        bool(sources), bool(report_seed), has_semantic_claims=bool(claims)
    )
    report_seed_input = _module_report_seed(
        report_seed=report_seed,
        manifest=manifest,
        source_ids=[str(source["source_id"]) for source in sources],
        gaps=gaps,
    )
    package: dict[str, Any] = {
        "schema_version": EVIDENCE_PACKAGE_SCHEMA,
        "package_id": f"package-{str(request['request_fingerprint'])[:24]}",
        "request_id": request["request_id"],
        "project_id": manifest["project_id"],
        # ``status`` describes whether the frozen contract is compilable. Any
        # unresolved research limitations stay explicit in ``gaps`` and the
        # downstream ReportDocument QA; they must not masquerade as a
        # partially valid compiler input.
        "status": "ready",
        "as_of": manifest["as_of"],
        "compiler_input_version": REPORT_COMPILER_INPUT_VERSION,
        "compiler_fingerprint": _compiler_fingerprint(),
        "sources": deepcopy(sources),
        "source_registry": sources,
        "claims": claims,
        "module_inputs": {"report_seed": report_seed_input},
        "gaps": gaps,
        "errors": [],
        "agent_runs": [],
        "validation_status": "valid",
        "created_at": f"{manifest['as_of']}T00:00:00Z",
    }
    if project_panorama:
        package["project_panorama"] = deepcopy(dict(project_panorama))
    package["package_hash"] = compute_package_hash(package)
    return package


def _validate_frozen_package(package: Mapping[str, Any]) -> None:
    if package.get("schema_version") != EVIDENCE_PACKAGE_SCHEMA:
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage has an unsupported schema_version"
        )
    required_fields = (
        "package_id",
        "request_id",
        "project_id",
        "status",
        "validation_status",
        "as_of",
        "compiler_input_version",
        "compiler_fingerprint",
        "sources",
        "source_registry",
        "claims",
        "module_inputs",
        "gaps",
        "errors",
    )
    for field in required_fields:
        if field not in package:
            raise FrozenEvidencePackageError(
                f"frozen EvidencePackage is missing required field: {field}"
            )
    for field in ("package_id", "request_id", "project_id", "as_of"):
        if not str(package.get(field) or "").strip():
            raise FrozenEvidencePackageError(
                f"frozen EvidencePackage has an empty required field: {field}"
            )
    if package.get("status") != "ready":
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage status is not compilable: "
            f"{package.get('status')!r}"
        )
    if package.get("validation_status") != "valid":
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage validation_status must be valid"
        )
    try:
        _normalize_as_of(str(package.get("as_of") or ""))
    except ProjectReportError as exc:
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage has an invalid as_of"
        ) from exc
    if not isinstance(package.get("compiler_fingerprint"), Mapping):
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage compiler_fingerprint must be an object"
        )
    if not isinstance(package.get("module_inputs"), Mapping):
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage module_inputs must be an object"
        )
    for field in ("sources", "source_registry", "claims", "gaps", "errors"):
        value = package.get(field)
        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            raise FrozenEvidencePackageError(
                f"frozen EvidencePackage {field} must be an array"
            )
    expected = str(package.get("package_hash") or "")
    if not expected:
        raise FrozenEvidencePackageError("frozen EvidencePackage is missing package_hash")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage package_hash must be a lowercase SHA-256"
        )
    actual = compute_package_hash(package)
    if expected != actual:
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage package_hash does not match its content"
        )
    if package.get("compiler_input_version") != REPORT_COMPILER_INPUT_VERSION:
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage has an incompatible compiler_input_version"
        )
    if package.get("compiler_fingerprint") != _compiler_fingerprint():
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage compiler fingerprint does not match the current "
            "report compiler and template"
        )


def _read_report_seed(work: Path) -> dict[str, Any] | None:
    path = work / "report_seed.json"
    return _read_json_object(path, label="report seed") if path.exists() else None


def _asset_file_candidate(project: Path, locator: str) -> Path | None:
    if not locator or re.match(r"^(?:https?|tos|s3|data):", locator, re.I):
        return None
    source = Path(locator)
    candidates = [source] if source.is_absolute() else []
    if not source.is_absolute():
        candidates.extend(
            [
                project / source,
                _workspace_root_for(project) / source,
                project / "work" / source,
            ]
        )
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _freeze_report_seed_assets(
    report_seed: dict[str, Any] | None, *, project: Path, work: Path
) -> dict[str, Any] | None:
    """Embed local media in the frozen seed and strip machine-local locators."""
    if report_seed is None:
        return None
    frozen = deepcopy(report_seed)
    supplied = frozen.get("asset_registry")
    if not isinstance(supplied, Sequence) or isinstance(
        supplied, (str, bytes, bytearray)
    ):
        return _sanitize_portable_value(frozen)

    snapshot_root = _project_child_dir(
        project, work / "source_snapshots", label="source_snapshots"
    )
    snapshot_dir = _project_child_dir(
        project, snapshot_root / "assets", label="asset snapshots"
    )
    result: list[Any] = []
    locator_fields = (
        "data_or_object_ref",
        "source_path",
        "local_path",
        "path",
    )
    for raw_entry in supplied:
        if not isinstance(raw_entry, Mapping):
            result.append(deepcopy(raw_entry))
            continue
        entry = deepcopy(dict(raw_entry))
        existing_uri = str(entry.get("data_uri") or "")
        raw_bytes: bytes | None = None
        mime = str(entry.get("mime") or "").strip()
        if existing_uri.startswith("data:image/") and ";base64," in existing_uri:
            header, encoded = existing_uri.split(",", 1)
            try:
                raw_bytes = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise ProjectReportError(
                    f"asset has an invalid base64 data_uri: {entry.get('asset_id')}"
                ) from exc
            mime = header[5:].split(";", 1)[0]
        elif not existing_uri:
            locator = next(
                (
                    str(entry.get(field) or "").strip()
                    for field in locator_fields
                    if str(entry.get(field) or "").strip()
                ),
                "",
            )
            candidate = _asset_file_candidate(project, locator)
            if candidate is not None:
                raw_bytes = candidate.read_bytes()
                mime = mime or mimetypes.guess_type(candidate.name)[0] or ""
        if raw_bytes is not None and mime.startswith("image/"):
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            suffix = mimetypes.guess_extension(mime) or ".bin"
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot = snapshot_dir / f"{content_hash}{suffix}"
            _write_stable_bytes(snapshot, raw_bytes)
            entry.update(
                {
                    "mime": mime,
                    "content_hash": content_hash,
                    "byte_size": len(raw_bytes),
                    "snapshot_ref": f"source_snapshots/assets/{snapshot.name}",
                    "embed_status": "embedded",
                    "data_uri": (
                        f"data:{mime};base64,"
                        + base64.b64encode(raw_bytes).decode("ascii")
                    ),
                }
            )
        elif not existing_uri:
            entry["embed_status"] = "missing"
            entry["data_uri"] = ""
        for field in locator_fields:
            entry.pop(field, None)
        result.append(entry)
    frozen["asset_registry"] = result
    return _sanitize_portable_value(frozen)


def _report_input_from_package(package: Mapping[str, Any]) -> dict[str, Any]:
    module_inputs = package.get("module_inputs")
    module_inputs = module_inputs if isinstance(module_inputs, Mapping) else {}
    seed = module_inputs.get("report_seed")
    seed = deepcopy(dict(seed)) if isinstance(seed, Mapping) else {}
    for wrapper in ("report_document", "report_json", "payload"):
        wrapped = seed.get(wrapper)
        if isinstance(wrapped, Mapping):
            seed = deepcopy(dict(wrapped))
            break
    if "pages" in seed and "page_manifest" not in seed:
        pages = seed.get("pages")
        if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes, bytearray)):
            seed["page_manifest"] = deepcopy(list(pages))

    project = seed.get("project") if isinstance(seed.get("project"), Mapping) else {}
    project = deepcopy(dict(project))
    project.setdefault("project_id", package.get("project_id"))
    project.setdefault("name", package.get("project_id"))
    seed["project"] = project
    seed.setdefault("source_registry", deepcopy(package.get("source_registry") or []))
    seed.setdefault("evidence_gaps", deepcopy(package.get("gaps") or []))
    if isinstance(package.get("project_panorama"), Mapping):
        seed["project_panorama"] = deepcopy(dict(package["project_panorama"]))
    meta = seed.get("meta") if isinstance(seed.get("meta"), Mapping) else {}
    meta = deepcopy(dict(meta))
    meta.setdefault("as_of", package.get("as_of"))
    meta.setdefault("compiled_at", package.get("created_at") or f"{package['as_of']}T00:00:00Z")
    meta.setdefault("evidence_package_hash", package.get("package_hash"))
    seed["meta"] = meta
    return _sanitize_portable_value(seed)


def compile_frozen_package(
    package: Mapping[str, Any], *, profile: str = SUPPORTED_PROFILE
) -> dict[str, Any]:
    """Compile a validated package into a deterministic renderer-neutral document."""
    if profile != SUPPORTED_PROFILE:
        raise ProjectReportError(f"unsupported profile: {profile}")
    _validate_frozen_package(package)
    try:
        from report_document import build_report_document
    except ImportError:  # Imported as ``scripts.build_project_report`` in tests/tools.
        from scripts.report_document import build_report_document

    document = build_report_document(_report_input_from_package(package))
    document["template_profile_version"] = profile
    return document


def compute_report_document_hash(document: Mapping[str, Any]) -> str:
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    return _json_hash(document)


def evaluate_delivery_status(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one canonical send gate for adaptive ReportDocuments."""
    qa = document.get("qa") if isinstance(document.get("qa"), Mapping) else {}
    panorama = (
        document.get("project_panorama")
        if isinstance(document.get("project_panorama"), Mapping)
        else None
    )
    reason_codes: list[str] = []
    if not panorama:
        reason_codes.append("project_panorama_missing")
        required_units: list[str] = []
    else:
        required_units = [
            str(unit)
            for unit in panorama.get("required_units") or []
            if str(unit).strip()
        ]
        if not required_units:
            reason_codes.append("project_panorama_required_units_missing")

    missing = [str(unit) for unit in qa.get("missing_required_units") or []]
    reason_codes.extend(f"missing_required_unit:{unit}" for unit in missing)

    qa_required = [
        str(unit)
        for unit in qa.get("required_units") or []
        if str(unit).strip()
    ]
    if required_units and qa_required != required_units:
        reason_codes.append("qa_required_units_mismatch")
    qa_status = str(qa.get("status") or "")
    if qa_status != "ready" and not missing:
        reason_codes.append(f"qa_status:{qa_status or 'missing'}")
    if qa.get("structural_passed") is not True or qa.get("passed") is not True:
        if not reason_codes:
            reason_codes.append("report_document_structure_failed")
    if qa.get("delivery_ready") is not True and not reason_codes:
        reason_codes.append("report_document_not_delivery_ready")
    return {"ready": not reason_codes, "reason_codes": reason_codes}


def _normalize_exports(exports: Any) -> tuple[str, ...]:
    if exports is None:
        return SUPPORTED_EXPORTS
    pending: list[Any] = [exports]
    flattened: list[str] = []
    while pending:
        value = pending.pop(0)
        if isinstance(value, str):
            flattened.extend(part.strip() for part in value.split(",") if part.strip())
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            pending[0:0] = list(value)
        else:
            raise ProjectReportError(f"invalid export value: {value!r}")
    unique: list[str] = []
    for value in flattened:
        if value not in SUPPORTED_EXPORTS:
            raise ProjectReportError(f"unsupported export: {value}")
        if value not in unique:
            unique.append(value)
    if not unique:
        raise ProjectReportError("at least one export must be selected")
    return tuple(unique)


def _compile_result(
    *,
    project: Path,
    owner_id: str,
    package: Mapping[str, Any],
    profile: str,
    exports: tuple[str, ...],
    mode: str,
    paths: Mapping[str, str],
    sync_tos: bool,
    tos_uploader: Any,
) -> dict[str, Any]:
    try:
        from dds_cinematic_deck import resolve_cinematic_document
    except ImportError:
        from scripts.dds_cinematic_deck import resolve_cinematic_document

    compiled = json.loads(
        _canonical_bytes(compile_frozen_package(package, profile=profile))
    )
    delivery_status = evaluate_delivery_status(compiled)
    if not delivery_status["ready"]:
        return {
            "status": "qa_reject",
            "mode": mode,
            "package_status": package.get("status"),
            "package_hash": package["package_hash"],
            "profile": profile,
            "exports": [],
            "paths": dict(paths),
            "delivery_status": delivery_status,
            "report_document": compiled,
        }
    document = _sanitize_portable_value(
        json.loads(
            _canonical_bytes(
                resolve_cinematic_document({"report_document": compiled})
            )
        )
    )
    export_paths = _materialize_exports(
        project=project,
        owner_id=owner_id,
        package=package,
        document=document,
        exports=exports,
        sync_tos=sync_tos,
        tos_uploader=tos_uploader,
    )
    return {
        "status": "ok",
        "mode": mode,
        "package_status": package.get("status"),
        "package_hash": package["package_hash"],
        "report_document_hash": compute_report_document_hash(document),
        "profile": profile,
        "exports": list(exports),
        "paths": {**dict(paths), **export_paths},
        "report_document": document,
    }


def _safe_filename(value: Any, *, fallback: str = "DDS项目") -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", "_", text).strip(" ._")
    return (text[:96] or fallback).rstrip(" .")


def _workspace_root_for(project: Path) -> Path:
    for candidate in (project.parent, *project.parents):
        if (candidate / "scripts" / "build_project_report.py").is_file():
            return candidate
    return project.parent


def _artifact_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tos_artifact_rows(
    *, package: Mapping[str, Any], artifact_paths: Mapping[str, Path]
) -> list[dict[str, Any]]:
    """Build upload rows without making local paths part of persisted status."""
    project_id = quote(str(package.get("project_id") or "project"), safe="-._~")
    as_of = quote(str(package.get("as_of") or "undated"), safe="-._~")
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for name, path in sorted(artifact_paths.items()):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        rows.append(
            {
                "local_path": str(resolved),
                "object_key": f"project-reports/{project_id}/{as_of}/{quote(name, safe='-._~')}",
                "byte_size": resolved.stat().st_size,
                "sha256": _artifact_hash(resolved),
            }
        )
    return rows


def _load_tos_client_module() -> Any:
    client_path = Path(__file__).resolve().parents[1] / "cloud" / "volcengine" / "tos_client.py"
    if not client_path.is_file():
        raise TosSyncUnavailable("runtime_unavailable")
    spec = importlib.util.spec_from_file_location("dds_project_report_tos_client", client_path)
    if not spec or not spec.loader:
        raise TosSyncUnavailable("runtime_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _default_tos_uploader(
    *, artifacts: Sequence[Mapping[str, Any]], context: Mapping[str, Any]
) -> dict[str, Any]:
    """Upload an explicitly requested delivery using the existing DDS TOS client.

    This adapter is intentionally dormant unless ``--sync-tos`` is supplied.
    It loads the existing local TOS environment only at that point and always
    enforces the Shanghai region selected for DDS delivery storage.
    """
    tos_client = _load_tos_client_module()
    env_path = Path(__file__).resolve().parents[1] / "cloud" / "volcengine" / ".env.volcengine"
    tos_client.load_dotenv(env_path)
    tos_client.disable_proxy()
    cfg = tos_client.get_tos_config()
    if str(cfg.get("region") or "") != TOS_DELIVERY_REGION:
        raise TosSyncUnavailable("region_mismatch")
    identity_ready = bool(cfg.get("role_name")) or bool(cfg.get("ak") and cfg.get("sk"))
    if not cfg.get("bucket") or not cfg.get("endpoint") or not identity_ready:
        raise TosSyncUnavailable("configuration_unavailable")
    try:
        client = tos_client.get_thread_local_client()
    except (ImportError, ModuleNotFoundError) as exc:
        raise TosSyncUnavailable("runtime_unavailable") from exc

    uploaded = 0
    for row in artifacts:
        client.put_object_from_file(
            str(cfg["bucket"]), str(row["object_key"]), str(row["local_path"])
        )
        uploaded += 1
    return {"status": "uploaded", "uploaded_count": uploaded}


def _delivery_status(
    *,
    sync_tos: bool,
    artifacts: Sequence[Mapping[str, Any]],
    package: Mapping[str, Any],
    tos_uploader: Any,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "schema_version": DELIVERY_STATUS_SCHEMA,
        "local_delivery": {"status": "complete"},
        "tos": {
            "region": TOS_DELIVERY_REGION,
            "status": "skipped",
            "reason_code": "not_requested",
        },
    }
    if not sync_tos:
        return status

    uploader = tos_uploader or _default_tos_uploader
    context = {
        "region": TOS_DELIVERY_REGION,
        "project_id": str(package.get("project_id") or ""),
        "as_of": str(package.get("as_of") or ""),
        "package_hash": str(package.get("package_hash") or ""),
    }
    try:
        result = uploader(artifacts=deepcopy(list(artifacts)), context=context)
    except TosSyncUnavailable as exc:
        reason = exc.reason_code
        if reason not in {
            "configuration_unavailable",
            "region_mismatch",
            "runtime_unavailable",
        }:
            reason = "runtime_unavailable"
        status["tos"] = {
            "region": TOS_DELIVERY_REGION,
            "status": "skipped",
            "reason_code": reason,
        }
        return status
    except (ImportError, ModuleNotFoundError):
        status["tos"] = {
            "region": TOS_DELIVERY_REGION,
            "status": "skipped",
            "reason_code": "runtime_unavailable",
        }
        return status
    except Exception:
        status["tos"] = {
            "region": TOS_DELIVERY_REGION,
            "status": "failed",
            "reason_code": "upload_failed",
        }
        return status

    result = result if isinstance(result, Mapping) else {}
    if result.get("status") != "uploaded":
        status["tos"] = {
            "region": TOS_DELIVERY_REGION,
            "status": "failed",
            "reason_code": "upload_incomplete",
        }
        return status
    count = result.get("uploaded_count", len(artifacts))
    if not isinstance(count, int) or isinstance(count, bool):
        count = len(artifacts)
    count = max(0, min(count, len(artifacts)))
    if count != len(artifacts):
        status["tos"] = {
            "region": TOS_DELIVERY_REGION,
            "status": "failed",
            "reason_code": "upload_incomplete",
        }
        return status
    status["tos"] = {
        "region": TOS_DELIVERY_REGION,
        "status": "uploaded",
        "artifact_count": len(artifacts),
        "uploaded_count": count,
    }
    return status


def _materialize_exports(
    *,
    project: Path,
    owner_id: str,
    package: Mapping[str, Any],
    document: Mapping[str, Any],
    exports: tuple[str, ...],
    sync_tos: bool,
    tos_uploader: Any,
) -> dict[str, str]:
    """Write the deterministic delivery bundle and selected single-file HTMLs."""
    try:
        from dds_cinematic_deck import render_cinematic_deck_html
    except ImportError:
        from scripts.dds_cinematic_deck import render_cinematic_deck_html

    document = _sanitize_portable_value(document)
    qa_payload = document.get("qa") if isinstance(document.get("qa"), Mapping) else {}
    missing_assets = list(qa_payload.get("missing_asset_ids") or [])
    if "portable_single_file" in exports and missing_assets:
        raise ProjectReportError(
            "portable export is blocked by unresolved referenced assets: "
            + ", ".join(str(value) for value in missing_assets)
        )

    delivery = _project_child_dir(
        project, project / "delivery", label="delivery"
    )
    delivery.mkdir(exist_ok=True)
    _project_child_dir(
        project,
        project / "work" / "source_snapshots",
        label="source_snapshots",
    ).mkdir(exist_ok=True)

    report_document_path = delivery / "report_document.json"
    page_manifest_path = delivery / "page_manifest.json"
    qa_path = delivery / "qa.json"
    _write_stable_json(report_document_path, document)
    _write_stable_json(page_manifest_path, document.get("page_manifest") or [])
    _write_stable_json(qa_path, document.get("qa") or {})

    project_meta = document.get("project")
    project_name = (
        project_meta.get("name")
        if isinstance(project_meta, Mapping) and project_meta.get("name")
        else project.name
    )
    date_token = str(package["as_of"]).replace("-", "")
    name_token = _safe_filename(project_name)
    html_bytes = render_cinematic_deck_html(
        {"report_document": deepcopy(dict(document))}
    ).encode("utf-8")

    result: dict[str, str] = {
        "delivery": str(delivery),
        "report_document": str(report_document_path),
        "page_manifest": str(page_manifest_path),
        "qa": str(qa_path),
    }
    artifact_paths: dict[str, Path] = {
        report_document_path.name: report_document_path,
        page_manifest_path.name: page_manifest_path,
        qa_path.name: qa_path,
    }
    if "portable_single_file" in exports:
        portable = delivery / f"{date_token}_{name_token}_DDS前策研判_发送版.html"
        _write_stable_bytes(portable, html_bytes)
        result["portable_single_file"] = str(portable)
        artifact_paths[portable.name] = portable
    if "interactive" in exports:
        interactive_dir = (
            _workspace_root_for(project)
            / "data_out"
            / "reports"
            / "interactive"
            / "users"
            / _safe_filename(owner_id, fallback="local")
        )
        interactive_dir.mkdir(parents=True, exist_ok=True)
        interactive = interactive_dir / f"{date_token}_{name_token}_interactive.html"
        _write_stable_bytes(interactive, html_bytes)
        result["interactive"] = str(interactive)
        artifact_paths[interactive.name] = interactive

    hashes = {
        "schema_version": "dds.delivery-hashes/1.0",
        "package_hash": str(package["package_hash"]),
        "report_document_hash": compute_report_document_hash(document),
        "artifacts": {
            name: _artifact_hash(path)
            for name, path in sorted(artifact_paths.items())
        },
    }
    hashes_path = delivery / "hashes.json"
    _write_stable_json(hashes_path, hashes)
    result["hashes"] = str(hashes_path)
    artifact_paths[hashes_path.name] = hashes_path

    status_path = delivery / "delivery_status.json"
    upload_rows = _tos_artifact_rows(package=package, artifact_paths=artifact_paths)
    status = _delivery_status(
        sync_tos=sync_tos,
        artifacts=upload_rows,
        package=package,
        tos_uploader=tos_uploader,
    )
    _write_stable_json(status_path, status)
    result["delivery_status"] = str(status_path)
    return result


def build_project_report(
    *,
    project_dir: str | Path,
    as_of: str | date,
    owner_id: str = "local",
    research: str = "auto",
    profile: str = SUPPORTED_PROFILE,
    exports: Any = None,
    export: Any = None,
    compile_only: bool = False,
    sync_tos: bool = False,
    tos_uploader: Any = None,
) -> dict[str, Any]:
    """Prepare/reuse project contracts, then deterministically compile them."""
    frozen_as_of = _normalize_as_of(as_of)
    if research not in SUPPORTED_RESEARCH:
        raise ProjectReportError(f"unsupported research mode: {research}")
    if profile != SUPPORTED_PROFILE:
        raise ProjectReportError(f"unsupported profile: {profile}")
    if exports is not None and export is not None:
        raise ProjectReportError("use either exports or export, not both")
    selected_exports = _normalize_exports(exports if exports is not None else export)

    if compile_only:
        project, work = _validate_compile_project_dir(project_dir)
        package_path = work / "evidence_package.json"
        if not package_path.is_file():
            raise FrozenEvidencePackageError(
                f"compile-only requires an existing frozen EvidencePackage: {package_path}"
            )
        package = _read_json_object(package_path, label="frozen EvidencePackage")
        _validate_frozen_package(package)
        if str(package.get("as_of")) != frozen_as_of:
            raise FrozenEvidencePackageError(
                "frozen EvidencePackage as_of does not match the requested as_of"
            )
        return _compile_result(
            project=project,
            owner_id=owner_id,
            package=package,
            profile=profile,
            exports=selected_exports,
            mode="compile-only",
            paths={"evidence_package": str(package_path)},
            sync_tos=sync_tos,
            tos_uploader=tos_uploader,
        )

    project, inbox, work = _validate_project_dir(project_dir)
    package_path = work / "evidence_package.json"
    work.mkdir(parents=False, exist_ok=True)
    (work / "source_snapshots").mkdir(exist_ok=True)
    overrides = _load_normalization_overrides(work)
    records = _apply_evidence_families(_raw_file_records(inbox), overrides)
    manifest_path = work / "project_manifest.json"
    manifest = _build_manifest(project, manifest_path, records, frozen_as_of)
    _write_stable_json(manifest_path, manifest)
    _write_source_snapshot_metadata(work, manifest)

    panorama_path = work / "project_panorama.json"
    project_panorama = (
        _read_json_object(panorama_path, label="project panorama")
        if panorama_path.is_file()
        else None
    )
    if project_panorama and project_panorama.get("status") == "awaiting_user_input":
        questions_path = work / "user_questions.json"
        _write_stable_json(
            questions_path,
            {"questions": deepcopy(project_panorama.get("questions") or [])},
        )
        return {
            "status": "awaiting_user_input",
            "mode": "intake",
            "paths": {
                "project_panorama": str(panorama_path),
                "user_questions": str(questions_path),
            },
            "questions": deepcopy(project_panorama.get("questions") or []),
        }

    report_seed = _freeze_report_seed_assets(
        _read_report_seed(work), project=project, work=work
    )
    request = _build_research_request(
        manifest,
        owner_id=owner_id,
        research=research,
        report_seed=report_seed,
    )
    request_path = work / "research_request.json"
    _write_stable_json(request_path, request)

    new_package = _build_evidence_package(
        manifest,
        request,
        report_seed,
        project_panorama=project_panorama,
    )
    if package_path.exists():
        package = _read_json_object(package_path, label="frozen EvidencePackage")
        try:
            _validate_frozen_package(package)
        except FrozenEvidencePackageError:
            # A normal build is the repair/migration path. Compile-only remains
            # strict and will never consume an invalid frozen package.
            reusable = False
        else:
            reusable = (
                package.get("request_id") == request["request_id"]
                and package.get("project_id") == manifest["project_id"]
                and package.get("as_of") == frozen_as_of
                and package.get("project_panorama") == project_panorama
            )
        if not reusable:
            package = new_package
            _write_stable_json(package_path, package)
    else:
        package = new_package
        _write_stable_json(package_path, package)

    return _compile_result(
        project=project,
        owner_id=owner_id,
        package=package,
        profile=profile,
        exports=selected_exports,
        mode="build",
        paths={
            "project_manifest": str(manifest_path),
            "normalization_overrides": str(work / "normalization_overrides.json"),
            "research_request": str(request_path),
            "evidence_package": str(package_path),
        },
        sync_tos=sync_tos,
        tos_uploader=tos_uploader,
    )


def _export_token(value: str) -> tuple[str, ...]:
    try:
        return _normalize_exports(value)
    except ProjectReportError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--owner-id", default="local")
    parser.add_argument("--research", choices=SUPPORTED_RESEARCH, default="auto")
    parser.add_argument("--profile", choices=(SUPPORTED_PROFILE,), default=SUPPORTED_PROFILE)
    parser.add_argument(
        "--export",
        dest="export_groups",
        action="append",
        nargs="+",
        type=_export_token,
        help="interactive and/or portable_single_file; repeat or comma-separate values",
    )
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument(
        "--sync-tos",
        action="store_true",
        help="explicitly attempt Shanghai TOS delivery after local artifacts complete",
    )
    return parser


def _flatten_export_groups(groups: Any) -> tuple[str, ...] | None:
    if not groups:
        return None
    return _normalize_exports(groups)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = build_project_report(
            project_dir=args.project_dir,
            as_of=args.as_of,
            owner_id=args.owner_id,
            research=args.research,
            profile=args.profile,
            exports=_flatten_export_groups(args.export_groups),
            compile_only=args.compile_only,
            sync_tos=args.sync_tos,
        )
    except ProjectReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    summary = {key: value for key, value in result.items() if key != "report_document"}
    print(_canonical_bytes(summary).decode("utf-8").rstrip())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI.
    raise SystemExit(main())


__all__ = [
    "EVIDENCE_PACKAGE_SCHEMA",
    "FrozenEvidencePackageError",
    "ProjectDirectoryError",
    "ProjectReportError",
    "SUPPORTED_EXPORTS",
    "SUPPORTED_PROFILE",
    "build_project_report",
    "compile_frozen_package",
    "compute_package_hash",
    "compute_report_document_hash",
    "evaluate_delivery_status",
    "main",
]
