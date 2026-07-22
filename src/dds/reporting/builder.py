"""Stable forwarding surface for the offline report compiler.

The compiler implementation remains in :mod:`dds.reporting.compiler`.  This
module exists so callers can adopt the target V2 layout without introducing a
second build path.
"""

from dds.reporting.compiler import (
    EVIDENCE_PACKAGE_SCHEMA,
    PACKAGE_REQUIRED_FIELDS,
    PROFILE_PATH,
    REPORT_COMPILER_INPUT_VERSION,
    SUPPORTED_PROFILE,
    TEMPLATE_PATH,
    FrozenEvidencePackageError,
    ProjectReportError,
    build_frozen_package,
    compile_frozen_package,
    compiler_component_paths,
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
    "evaluate_delivery_status",
    "freeze_evidence_package",
    "freeze_report_seed_assets",
    "load_template_profile",
    "render_frozen_package",
    "validate_frozen_package",
]
