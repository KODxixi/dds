"""The target V2 module layout must alias, not duplicate, canonical objects."""

from dds import contracts
from dds.domain import models
from dds.domain.decisions import (
    EvidenceResolutionStatus,
    ResolutionStatus,
    ResolvedField,
    ResolvedStatus,
)
from dds.domain.evidence import DataRequirement, EvidenceRecord, EvidenceType
from dds.domain.project import ProjectContext
from dds.domain.report_status import (
    ContractViolationError,
    GateResult,
    ReportRun,
    ValidationResult,
)
from dds.domain.sections import (
    FRAMEWORK_ID,
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    SectionResult,
)
from dds.reporting import assets, compiler
from dds.reporting.asset_resolver import AssetResolutionError, AssetResolver
from dds.reporting.builder import (
    build_frozen_package,
    compile_frozen_package,
    compiler_fingerprint,
    render_frozen_package,
)


def test_domain_layout_is_alias_only() -> None:
    assert ProjectContext is models.ProjectContext
    assert DataRequirement is models.DataRequirement
    assert EvidenceRecord is models.EvidenceRecord
    assert EvidenceType is models.EvidenceType
    assert ResolvedField is models.ResolvedField
    assert ResolvedStatus is models.ResolvedStatus
    assert ResolutionStatus is models.ResolutionStatus
    assert EvidenceResolutionStatus is models.EvidenceResolutionStatus
    assert SectionResult is models.SectionResult
    assert ReportRun is models.ReportRun


def test_framework_and_report_status_layout_forwards_contracts() -> None:
    assert FRAMEWORK_ID == contracts.FRAMEWORK_ID
    assert VALID_SECTION_IDS is contracts.VALID_SECTION_IDS
    assert SECTION_REQUIREMENTS is contracts.SECTION_REQUIREMENTS
    assert GateResult is contracts.GateResult
    assert ValidationResult is contracts.ValidationResult
    assert ContractViolationError is contracts.ContractViolationError


def test_reporting_layout_forwards_existing_kernel() -> None:
    assert AssetResolver is assets.AssetResolver
    assert AssetResolutionError is assets.AssetResolutionError
    assert build_frozen_package is compiler.build_frozen_package
    assert compile_frozen_package is compiler.compile_frozen_package
    assert compiler_fingerprint is compiler.compiler_fingerprint
    assert render_frozen_package is compiler.render_frozen_package
