"""Deterministic, fully offline DDS report freeze and compile kernel."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .assets import AssetResolver
    from .report_delivery_evidence import (
        ReportDeliveryEvidenceError,
        verify_report_delivery_validation,
    )
    from .report_document import build_report_document, sanitize_portable_value
    from .ui_recipe import (
        load_decision_report_recipe,
        recipe_component_paths,
    )
except ImportError:  # pragma: no cover - top-level V1 compatibility import
    from assets import AssetResolver
    from report_delivery_evidence import (
        ReportDeliveryEvidenceError,
        verify_report_delivery_validation,
    )
    from report_document import build_report_document, sanitize_portable_value
    from ui_recipe import load_decision_report_recipe, recipe_component_paths


EVIDENCE_PACKAGE_SCHEMA = "dds.evidence-package/1.0"
REPORT_COMPILER_INPUT_VERSION = "dds.report-seed/1.0"
SUPPORTED_PROFILE = "dds.liquid-glass-v4/1.0.0"

PACKAGE_REQUIRED_FIELDS = (
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

_LOCATOR_FIELDS = (
    "data_or_object_ref",
    "snapshot_ref",
    "source_path",
    "local_path",
    "path",
)

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = PACKAGE_ROOT / "templates" / "dds_report_liquid_glass_v4.html"
PROFILE_PATH = PACKAGE_ROOT / "profiles" / "report_template_profile_v4.json"


class ProjectReportError(RuntimeError):
    """Base error retained for V1 builder compatibility."""


class FrozenEvidencePackageError(ProjectReportError):
    """Raised when frozen compiler input is incomplete, changed, or invalid."""


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
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_as_of(value: str | date) -> str:
    text = value.isoformat() if isinstance(value, date) else str(value).strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ProjectReportError(
            "as_of must be an ISO date in YYYY-MM-DD form"
        ) from exc
    if parsed.isoformat() != text:
        raise ProjectReportError("as_of must be an ISO date in YYYY-MM-DD form")
    return text


def load_template_profile() -> dict[str, Any]:
    """Load and validate the bundled renderer profile without network access."""
    try:
        value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectReportError(f"cannot load report profile: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ProjectReportError("report profile must be a JSON object")
    profile = deepcopy(dict(value))
    identity = f"{profile.get('profile_id')}/{profile.get('profile_version')}"
    if identity != SUPPORTED_PROFILE:
        raise ProjectReportError(
            f"report profile identity mismatch: expected {SUPPORTED_PROFILE}, got {identity}"
        )
    runtime = profile.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("offline_runtime") is not True:
        raise ProjectReportError("report profile must require an offline runtime")
    recipe = load_decision_report_recipe()
    recipe_profile = recipe["profile"]
    design_system = profile.get("design_system")
    expected_design_system = {
        "id": "gary-ui",
        "recipe": "decision-report",
        "recipe_version": recipe_profile["version"],
        "integration_mode": recipe["integration_mode"],
        "compiled_template_hash": recipe["compiled_template_hash"],
        "projection_hash": recipe["projection_hash"],
        "source": "gary-ui://patterns/recipes/decision-report",
    }
    if design_system != expected_design_system:
        raise ProjectReportError(
            "report profile design_system does not match the vendored Gary recipe"
        )
    return profile


def compiler_component_paths() -> dict[str, Path]:
    """Return every file whose bytes affect freeze, compile, or rendering."""
    contracts = PACKAGE_ROOT / "contracts"
    return {
        "assets.py": PACKAGE_ROOT / "assets.py",
        "compiler.py": PACKAGE_ROOT / "compiler.py",
        "dds_cinematic_deck.py": PACKAGE_ROOT / "dds_cinematic_deck.py",
        "dds_report_liquid_glass_v4.html": TEMPLATE_PATH,
        "evidence_contract.py": contracts / "evidence_contract.py",
        "report_chart_contract.py": contracts / "report_chart_contract.py",
        "report_diagram_contract.py": contracts / "report_diagram_contract.py",
        "report_document.py": PACKAGE_ROOT / "report_document.py",
        "report_structure_contract.py": contracts / "report_structure_contract.py",
        "report_template_profile_v4.json": PROFILE_PATH,
        "renderer.py": PACKAGE_ROOT / "renderer.py",
        "ui_recipe.py": PACKAGE_ROOT / "ui_recipe.py",
        **recipe_component_paths(),
    }


def compiler_fingerprint(profile: str = SUPPORTED_PROFILE) -> dict[str, Any]:
    """Hash all semantic compiler inputs; physical paths never enter the hash."""
    if profile != SUPPORTED_PROFILE:
        raise ProjectReportError(f"unsupported profile: {profile}")
    load_template_profile()
    components: dict[str, str] = {}
    for name, path in sorted(compiler_component_paths().items()):
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise ProjectReportError(f"compiler component unavailable: {name}") from exc
        components[name] = hashlib.sha256(payload).hexdigest()
    fingerprint_input = {"profile": profile, "components": components}
    return {
        **fingerprint_input,
        "fingerprint": _json_hash(fingerprint_input),
    }


def compute_package_hash(package: Mapping[str, Any]) -> str:
    """Return the V1-compatible semantic package hash."""
    if not isinstance(package, Mapping):
        raise TypeError("package must be a mapping")
    volatile = {
        "package_hash",
        "created_at",
        "updated_at",
        "audit",
        "report_delivery_validation",
    }
    payload = {
        key: deepcopy(value) for key, value in package.items() if key not in volatile
    }
    return _contract_hash(payload)


def _sha256_token(value: Any) -> str:
    token = str(value or "").strip().lower().removeprefix("sha256:")
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else ""


def _mapping_rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def evaluate_evidence_delivery_status(package: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the frozen evidence attestation required only for formal delivery."""
    reasons: list[str] = []
    validation = package.get("report_delivery_validation")
    if not isinstance(validation, Mapping):
        return {"ready": False, "reason_codes": ["evidence_attestation_missing"]}

    sources = _mapping_rows(package.get("source_registry"))
    claims = _mapping_rows(package.get("claims"))
    source_attestations = _mapping_rows(validation.get("source_attestations"))
    claim_attestations = _mapping_rows(validation.get("claim_attestations"))
    source_attestation_by_id = {
        str(row.get("source_id") or ""): row
        for row in source_attestations
        if str(row.get("source_id") or "")
    }
    claim_attestation_by_id = {
        str(row.get("claim_id") or ""): row
        for row in claim_attestations
        if str(row.get("claim_id") or "")
    }

    if not sources:
        reasons.append("qualified_sources_missing")
    if not claims:
        reasons.append("closed_claims_missing")

    qualified_source_ids: set[str] = set()
    source_ids: list[str] = []
    for source in sources:
        source_id = str(source.get("source_id") or "").strip() or "<unknown>"
        source_ids.append(source_id)
        qualification = str(source.get("qualification_status") or "").strip()
        if not qualification:
            reasons.append(f"source_qualification_missing:{source_id}")
        elif qualification != "qualified":
            reasons.append(f"source_not_qualified:{source_id}")
        else:
            qualified_source_ids.add(source_id)

        declared_hashes = {
            token
            for field in (
                "raw_hash",
                "source_hash",
                "sha256",
                "content_hash",
                "snapshot_hash",
            )
            if (token := _sha256_token(source.get(field)))
        }
        if not declared_hashes:
            reasons.append(f"source_hash_missing:{source_id}")

        attestation = source_attestation_by_id.get(source_id)
        if attestation is None:
            reasons.append(f"source_attestation_missing:{source_id}")
            continue
        content_hash = _sha256_token(attestation.get("content_hash"))
        if not content_hash:
            reasons.append(f"source_attestation_hash_invalid:{source_id}")
        elif declared_hashes and content_hash not in declared_hashes:
            reasons.append(f"source_hash_attestation_mismatch:{source_id}")

    if (
        len(source_ids) != len(set(source_ids))
        or set(source_attestation_by_id) != set(source_ids)
        or len(source_attestations) != len(source_ids)
    ):
        reasons.append("source_attestation_coverage_mismatch")

    claim_ids: list[str] = []
    allowed_counter_statuses = {
        "searched_none_found",
        "counter_evidence_found",
        "not_applicable_with_reason",
    }
    for claim in claims:
        claim_id = str(
            claim.get("claim_id") or claim.get("record_id") or ""
        ).strip() or "<unknown>"
        claim_ids.append(claim_id)
        attestation = claim_attestation_by_id.get(claim_id)
        support_key = (
            "support_source_refs"
            if "support_source_refs" in claim
            else "source_refs"
        )
        support = {
            str(item).strip()
            for item in claim.get(support_key) or []
            if str(item).strip()
        }
        counter = {
            str(item).strip()
            for item in claim.get("counter_source_refs") or []
            if str(item).strip()
        }
        closed = (
            str(claim.get("status") or "").strip() == "qualified"
            and attestation is not None
            and str(attestation.get("claim_status") or "").strip() == "qualified"
            and bool(support)
            and set(attestation.get("support_source_refs") or []) == support
            and set(attestation.get("counter_source_refs") or []) == counter
            and support.issubset(qualified_source_ids)
            and counter.issubset(qualified_source_ids)
            and attestation.get("counter_evidence_status")
            in allowed_counter_statuses
            and _sha256_token(attestation.get("counter_evidence_note_hash"))
            and _sha256_token(attestation.get("limitations_hash"))
            and isinstance(attestation.get("limitation_count"), int)
            and attestation.get("limitation_count", 0) > 0
            and attestation.get("decision_eligibility") is False
        )
        if not closed:
            reasons.append(f"claim_not_closed:{claim_id}")

    if (
        len(claim_ids) != len(set(claim_ids))
        or set(claim_attestation_by_id) != set(claim_ids)
        or len(claim_attestations) != len(claim_ids)
    ):
        reasons.append("claim_attestation_coverage_mismatch")

    try:
        verify_report_delivery_validation(validation, package)
    except ReportDeliveryEvidenceError as exc:
        reasons.append(f"evidence_attestation_invalid:{exc}")
    return {
        "ready": not reasons,
        "reason_codes": list(dict.fromkeys(reasons)),
    }


def validate_frozen_package(
    package: Mapping[str, Any],
    *,
    profile: str = SUPPORTED_PROFILE,
) -> None:
    """Validate the immutable EvidencePackage and current compiler identity."""
    if not isinstance(package, Mapping):
        raise FrozenEvidencePackageError("frozen EvidencePackage must be an object")
    if package.get("schema_version") != EVIDENCE_PACKAGE_SCHEMA:
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage has an unsupported schema_version"
        )
    for field in PACKAGE_REQUIRED_FIELDS:
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
    expected_hash = str(package.get("package_hash") or "")
    if not expected_hash:
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage is missing package_hash"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage package_hash must be a lowercase SHA-256"
        )
    if expected_hash != compute_package_hash(package):
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage package_hash does not match its content"
        )
    if package.get("compiler_input_version") != REPORT_COMPILER_INPUT_VERSION:
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage has an incompatible compiler_input_version"
        )
    if package.get("compiler_fingerprint") != compiler_fingerprint(profile):
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage compiler fingerprint does not match the current "
            "report compiler, contracts, renderer, template, and profile"
        )


def _asset_locator(entry: Mapping[str, Any]) -> str:
    return next(
        (
            str(entry.get(field) or "").strip()
            for field in _LOCATOR_FIELDS
            if str(entry.get(field) or "").strip()
        ),
        "",
    )


def _decode_image_data_uri(uri: str, *, asset_id: Any) -> tuple[bytes, str]:
    header, encoded = uri.split(",", 1)
    mime = header[5:].split(";", 1)[0]
    if not mime.startswith("image/"):
        raise ProjectReportError(f"asset data_uri is not an image: {asset_id}")
    try:
        return base64.b64decode(encoded, validate=True), mime
    except (ValueError, TypeError) as exc:
        raise ProjectReportError(
            f"asset has an invalid base64 data_uri: {asset_id}"
        ) from exc


def freeze_report_seed_assets(
    report_seed: Mapping[str, Any] | None,
    *,
    asset_resolver: AssetResolver | None = None,
    snapshot_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    """Embed allowlisted local images and remove every machine-local locator."""
    if report_seed is None:
        return None
    if not isinstance(report_seed, Mapping):
        raise TypeError("report_seed must be a mapping or None")

    frozen = deepcopy(dict(report_seed))
    supplied = frozen.get("asset_registry")
    if not isinstance(supplied, Sequence) or isinstance(
        supplied, (str, bytes, bytearray)
    ):
        return sanitize_portable_value(frozen)

    resolver = asset_resolver or AssetResolver()
    snapshots = Path(snapshot_dir).resolve() if snapshot_dir is not None else None
    result: list[Any] = []
    for raw_entry in supplied:
        if not isinstance(raw_entry, Mapping):
            result.append(deepcopy(raw_entry))
            continue

        entry = deepcopy(dict(raw_entry))
        asset_id = entry.get("asset_id") or entry.get("id")
        existing_uri = str(entry.get("data_uri") or "")
        raw_bytes: bytes | None = None
        mime = str(entry.get("mime") or "").strip()
        if existing_uri.startswith("data:image/") and ";base64," in existing_uri:
            raw_bytes, mime = _decode_image_data_uri(existing_uri, asset_id=asset_id)
        elif existing_uri:
            # Remote and non-image URIs are never carried into an offline report.
            entry["data_uri"] = ""
        else:
            locator = _asset_locator(entry)
            candidate = resolver.resolve(locator) if locator else None
            if candidate is not None:
                raw_bytes = candidate.read_bytes()
                mime = mime or mimetypes.guess_type(candidate.name)[0] or ""

        portable_snapshot_ref = ""
        if raw_bytes is not None and mime.startswith("image/"):
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            suffix = mimetypes.guess_extension(mime) or ".bin"
            update = {
                "mime": mime,
                "content_hash": content_hash,
                "byte_size": len(raw_bytes),
                "embed_status": "embedded",
                "data_uri": (
                    f"data:{mime};base64," + base64.b64encode(raw_bytes).decode("ascii")
                ),
            }
            if snapshots is not None:
                snapshots.mkdir(parents=True, exist_ok=True)
                snapshot = snapshots / f"{content_hash}{suffix}"
                snapshot.write_bytes(raw_bytes)
                portable_snapshot_ref = f"assets/{snapshot.name}"
            entry.update(update)
        elif not str(entry.get("data_uri") or "").startswith("data:image/"):
            entry["embed_status"] = "missing"
            entry["data_uri"] = ""

        for field in _LOCATOR_FIELDS:
            entry.pop(field, None)
        if portable_snapshot_ref:
            entry["snapshot_ref"] = portable_snapshot_ref
        result.append(entry)

    frozen["asset_registry"] = result
    return sanitize_portable_value(frozen)


def freeze_evidence_package(
    package: Mapping[str, Any],
    *,
    asset_resolver: AssetResolver | None = None,
    snapshot_dir: str | Path | None = None,
    profile: str = SUPPORTED_PROFILE,
) -> dict[str, Any]:
    """Freeze an EvidencePackage using only caller-provided local resources."""
    if not isinstance(package, Mapping):
        raise TypeError("package must be a mapping")
    frozen = deepcopy(dict(package))
    module_inputs = frozen.get("module_inputs")
    if not isinstance(module_inputs, Mapping):
        raise FrozenEvidencePackageError(
            "frozen EvidencePackage module_inputs must be an object"
        )
    inputs = deepcopy(dict(module_inputs))
    seed = inputs.get("report_seed")
    if seed is not None and not isinstance(seed, Mapping):
        raise FrozenEvidencePackageError("report_seed must be an object")
    inputs["report_seed"] = freeze_report_seed_assets(
        seed,
        asset_resolver=asset_resolver,
        snapshot_dir=snapshot_dir,
    )
    frozen["module_inputs"] = inputs
    frozen["compiler_input_version"] = REPORT_COMPILER_INPUT_VERSION
    frozen["compiler_fingerprint"] = compiler_fingerprint(profile)
    sanitized = sanitize_portable_value(frozen)
    if not isinstance(sanitized, Mapping):  # pragma: no cover - mapping in, mapping out
        raise FrozenEvidencePackageError("frozen EvidencePackage must remain an object")
    frozen = dict(sanitized)
    frozen["package_hash"] = compute_package_hash(frozen)
    validate_frozen_package(frozen, profile=profile)
    return frozen


def _sequence_field(source: Mapping[str, Any] | None, field: str) -> list[Any]:
    if source is None:
        return []
    value = source.get(field)
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ProjectReportError(f"report_seed {field} must be an array")
    return deepcopy(list(value))


def build_frozen_package(
    report_seed: Mapping[str, Any],
    *,
    project_id: str,
    as_of: str | date,
    request_id: str | None = None,
    package_id: str | None = None,
    asset_resolver: AssetResolver | None = None,
    snapshot_dir: str | Path | None = None,
    profile: str = SUPPORTED_PROFILE,
) -> dict[str, Any]:
    """Build the smallest valid deterministic package around a report seed."""
    if not isinstance(report_seed, Mapping):
        raise TypeError("report_seed must be a mapping")
    normalized_project = str(project_id).strip()
    if not normalized_project:
        raise ProjectReportError("project_id must not be empty")
    normalized_as_of = _normalize_as_of(as_of)
    frozen_seed = freeze_report_seed_assets(
        report_seed,
        asset_resolver=asset_resolver,
        snapshot_dir=snapshot_dir,
    )
    identity_hash = _contract_hash(
        {
            "project_id": normalized_project,
            "as_of": normalized_as_of,
            "report_seed": frozen_seed,
        }
    )
    normalized_seed = frozen_seed if isinstance(frozen_seed, Mapping) else None
    sources = _sequence_field(normalized_seed, "source_registry")
    claims = _sequence_field(normalized_seed, "claims")
    gaps = _sequence_field(normalized_seed, "evidence_gaps")
    package: dict[str, Any] = {
        "schema_version": EVIDENCE_PACKAGE_SCHEMA,
        "package_id": package_id or f"package-{identity_hash[:24]}",
        "request_id": request_id or f"request-{identity_hash[:24]}",
        "project_id": normalized_project,
        "status": "ready",
        "validation_status": "valid",
        "as_of": normalized_as_of,
        "compiler_input_version": REPORT_COMPILER_INPUT_VERSION,
        "compiler_fingerprint": compiler_fingerprint(profile),
        "sources": sources,
        "source_registry": deepcopy(sources),
        "claims": claims,
        "module_inputs": {"report_seed": frozen_seed},
        "gaps": gaps,
        "errors": [],
        "created_at": f"{normalized_as_of}T00:00:00Z",
    }
    package["package_hash"] = compute_package_hash(package)
    validate_frozen_package(package, profile=profile)
    return package


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
        if isinstance(pages, Sequence) and not isinstance(
            pages, (str, bytes, bytearray)
        ):
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
    meta.setdefault(
        "compiled_at",
        package.get("created_at") or f"{package['as_of']}T00:00:00Z",
    )
    meta.setdefault("evidence_package_hash", package.get("package_hash"))
    seed["meta"] = meta
    return sanitize_portable_value(seed)


def compile_frozen_package(
    package: Mapping[str, Any],
    *,
    profile: str = SUPPORTED_PROFILE,
) -> dict[str, Any]:
    """Compile a validated package; this function performs no network I/O."""
    if profile != SUPPORTED_PROFILE:
        raise ProjectReportError(f"unsupported profile: {profile}")
    validate_frozen_package(package, profile=profile)
    document = build_report_document(_report_input_from_package(package))
    document["template_profile_version"] = profile
    return document


def render_frozen_package(
    package: Mapping[str, Any],
    *,
    profile: str = SUPPORTED_PROFILE,
    asset_resolver: AssetResolver | None = None,
) -> str:
    """Compile and render a package as one self-contained offline HTML file."""
    try:
        from .renderer import render_cinematic_deck_html
    except ImportError:  # pragma: no cover - top-level V1 compatibility import
        from renderer import render_cinematic_deck_html

    document = compile_frozen_package(package, profile=profile)
    return render_cinematic_deck_html(
        {"report_document": document},
        asset_resolver=asset_resolver,
    )


def compute_report_document_hash(document: Mapping[str, Any]) -> str:
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    return _json_hash(document)


def evaluate_delivery_status(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the V1 send gate for adaptive ReportDocuments."""
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
        str(unit) for unit in qa.get("required_units") or [] if str(unit).strip()
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


# Private spellings are retained because V1 tests and bridge code import them.
_compiler_fingerprint = compiler_fingerprint
_validate_frozen_package = validate_frozen_package
_freeze_report_seed_assets = freeze_report_seed_assets


__all__ = [
    "EVIDENCE_PACKAGE_SCHEMA",
    "FrozenEvidencePackageError",
    "PACKAGE_REQUIRED_FIELDS",
    "PROFILE_PATH",
    "ProjectReportError",
    "REPORT_COMPILER_INPUT_VERSION",
    "SUPPORTED_PROFILE",
    "TEMPLATE_PATH",
    "build_frozen_package",
    "compile_frozen_package",
    "compiler_component_paths",
    "compiler_fingerprint",
    "compute_package_hash",
    "compute_report_document_hash",
    "evaluate_evidence_delivery_status",
    "evaluate_delivery_status",
    "freeze_evidence_package",
    "freeze_report_seed_assets",
    "load_template_profile",
    "render_frozen_package",
    "validate_frozen_package",
]
