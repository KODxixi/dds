"""DDS V4 reporting kernel: contracts, freeze/compile, and offline HTML render."""

from .assets import AssetResolutionError, AssetResolver
from .compiler import (
    EVIDENCE_PACKAGE_SCHEMA,
    PROFILE_PATH,
    REPORT_COMPILER_INPUT_VERSION,
    SUPPORTED_PROFILE,
    TEMPLATE_PATH,
    FrozenEvidencePackageError,
    ProjectReportError,
    build_frozen_package,
    compile_frozen_package,
    compiler_fingerprint,
    compute_package_hash,
    compute_report_document_hash,
    evaluate_delivery_status,
    freeze_evidence_package,
    freeze_report_seed_assets,
    load_template_profile,
    render_frozen_package,
    validate_frozen_package,
)
from .renderer import (
    PAGE_CHUNK_SIZE,
    compile_cinematic_manifest,
    render_cinematic_deck_html,
    resolve_cinematic_document,
)
from .report_delivery_evidence import (
    ReportDeliveryEvidenceError,
    require_report_delivery_validation,
    validate_report_delivery_evidence,
    verify_report_delivery_validation,
)
from .report_document import build_report_document, compile_page_manifest
from .edition import ReportEdition, decision_pages, page_value_errors


__all__ = [
    "AssetResolutionError",
    "AssetResolver",
    "EVIDENCE_PACKAGE_SCHEMA",
    "FrozenEvidencePackageError",
    "PAGE_CHUNK_SIZE",
    "PROFILE_PATH",
    "ProjectReportError",
    "REPORT_COMPILER_INPUT_VERSION",
    "ReportDeliveryEvidenceError",
    "ReportEdition",
    "SUPPORTED_PROFILE",
    "TEMPLATE_PATH",
    "build_frozen_package",
    "build_report_document",
    "compile_cinematic_manifest",
    "compile_frozen_package",
    "compile_page_manifest",
    "decision_pages",
    "compiler_fingerprint",
    "compute_package_hash",
    "compute_report_document_hash",
    "evaluate_delivery_status",
    "freeze_evidence_package",
    "freeze_report_seed_assets",
    "load_template_profile",
    "page_value_errors",
    "render_cinematic_deck_html",
    "render_frozen_package",
    "require_report_delivery_validation",
    "resolve_cinematic_document",
    "validate_frozen_package",
    "validate_report_delivery_evidence",
    "verify_report_delivery_validation",
]
