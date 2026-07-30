from __future__ import annotations

import pytest

from dds.analysis_profile import resolve_intervention_profile
from dds.contracts import SECTION_REQUIREMENTS
from dds.domain import (
    EvidenceRecord,
    ProjectContext,
    ResolvedField,
    ResolvedStatus,
    SectionResult,
)
from dds.reporting import compile_frozen_package, evaluate_delivery_status
from dds.services.delivery_service import DeliveryService
from dds.services.report_compiler_adapter import ReportCompilerAdapter
from dds.services.report_service import ReportService


def _confirmed_input_1_profile() -> dict[str, object]:
    return resolve_intervention_profile(
        {"address": "Test City Opportunity Road 1"},
        selected_mode=1,
        confirmed=True,
    )


def _evidence() -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="e-frozen-source",
        metric_id="project.truth",
        value="verified",
        source_id="source-project-truth",
        source_ref="https://example.test/project-truth",
        observed_at="2026-07-30",
        geography="Test City",
        method="verified project record",
        sample_size=1,
    )


def _resolved_section(
    section_id: str,
    *,
    confidence: float | None = 0.8,
) -> SectionResult:
    return SectionResult(
        section_id=section_id,
        data={
            field_name: ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=f"verified-{section_id}-{field_name}",
                evidence_refs=["e-frozen-source"],
            )
            for field_name in SECTION_REQUIREMENTS[section_id]
        },
        conclusions=[f"{section_id} is resolved."],
        evidence_refs=["e-frozen-source"],
        confidence=confidence,
        status=ResolvedStatus.RESOLVED,
    )


def _assemble(
    *,
    missing_field: tuple[str, str] | None = None,
    missing_confidence: str | None = None,
    include_evidence: bool = True,
):
    profile = _confirmed_input_1_profile()
    sections = {
        section_id: _resolved_section(
            section_id,
            confidence=None if section_id == missing_confidence else 0.8,
        )
        for section_id in profile["required_units"]
        if section_id != "CS"
    }
    if missing_field is not None:
        section_id, field_name = missing_field
        sections[section_id].data[field_name] = ResolvedField(
            status=ResolvedStatus.UNKNOWN,
            reason="required fixture field is unresolved",
        )
        sections[section_id].status = ResolvedStatus.PARTIAL
    return ReportService().assemble(
        run_id="run-cs-readiness",
        project_context=ProjectContext(
            project_id="project-cs-readiness",
            project_name="CS readiness fixture",
            city="Test City",
            base_date="2026-07-30",
        ),
        evidence=[_evidence()] if include_evidence else [],
        analysis_profile=profile,
        section_results=sections,
    )


def test_complete_required_units_resolve_cs_and_report_run() -> None:
    run = _assemble()

    cs = run.sections["CS"]
    assert cs.status is ResolvedStatus.RESOLVED
    assert set(cs.data) == set(SECTION_REQUIREMENTS["CS"])
    assert cs.data["data_gaps"].value == []
    assert cs.data["confidence_gaps"].value == []
    assert run.status is ResolvedStatus.RESOLVED

    profile = run.metadata["analysis_profile"]
    assert all(
        run.sections[section_id].status is ResolvedStatus.RESOLVED
        for section_id in profile["required_units"]
    )
    assert run.sections["SC3"].status is ResolvedStatus.UNKNOWN
    assert run.sections["AD5"].status is ResolvedStatus.UNKNOWN
    assert run.sections["VA1"].status is ResolvedStatus.UNKNOWN


def test_complete_report_service_run_reaches_formal_delivery_renderer(tmp_path) -> None:
    run = _assemble()
    package = ReportCompilerAdapter().build_frozen_compiler_package(run)
    document = compile_frozen_package(package)

    assert evaluate_delivery_status(document)["ready"] is True
    html, html_hash, document_hash = DeliveryService(tmp_path).render(package)
    assert html.startswith("<!doctype html>")
    assert len(html_hash) == 64
    assert len(document_hash) == 64


def test_required_field_gap_keeps_cs_and_report_run_partial() -> None:
    run = _assemble(missing_field=("SC2", "competitors"))

    cs = run.sections["CS"]
    assert cs.status is ResolvedStatus.PARTIAL
    assert cs.data["data_gaps"].value == [
        {
            "section_id": "SC2",
            "field": "competitors",
            "status": "unknown",
            "reason": "required fixture field is unresolved",
        }
    ]
    assert run.status is ResolvedStatus.PARTIAL


def test_required_confidence_gap_keeps_cs_and_report_run_partial() -> None:
    run = _assemble(missing_confidence="AD2")

    cs = run.sections["CS"]
    assert cs.status is ResolvedStatus.PARTIAL
    assert cs.data["data_gaps"].value == []
    assert cs.data["confidence_gaps"].value == [
        {
            "section_id": "AD2",
            "status": "not_assessed",
            "reason": "尚未形成可追溯的证据派生置信度",
        }
    ]
    assert run.status is ResolvedStatus.PARTIAL


def test_missing_cs_source_fields_keep_cs_and_report_run_partial() -> None:
    run = _assemble(include_evidence=False)

    cs = run.sections["CS"]
    assert cs.data["sources"].status is ResolvedStatus.UNKNOWN
    assert cs.data["methods"].status is ResolvedStatus.UNKNOWN
    assert cs.status is ResolvedStatus.PARTIAL
    assert run.status is ResolvedStatus.PARTIAL


def test_missing_profile_falls_back_to_all_report_units() -> None:
    profile = _confirmed_input_1_profile()
    sections = {
        section_id: _resolved_section(section_id)
        for section_id in profile["required_units"]
        if section_id != "CS"
    }

    run = ReportService().assemble(
        run_id="run-cs-readiness-no-profile",
        project_context=ProjectContext(
            project_id="project-cs-readiness",
            project_name="CS readiness fixture",
            city="Test City",
            base_date="2026-07-30",
        ),
        evidence=[_evidence()],
        section_results=sections,
    )

    assert run.sections["CS"].status is ResolvedStatus.PARTIAL
    assert {
        gap["section_id"]
        for gap in run.sections["CS"].data["data_gaps"].value
    } == {"SC3", "AD5", "VA1"}
    assert run.status is ResolvedStatus.PARTIAL


@pytest.mark.parametrize(
    "section_results",
    [
        {"CS": _resolved_section("CS")},
        {"SC1": _resolved_section("SC2")},
        {"UNKNOWN": _resolved_section("SC1")},
    ],
)
def test_section_result_entrypoint_rejects_cs_mismatch_and_unknown_units(
    section_results,
) -> None:
    with pytest.raises(ValueError, match="section_results"):
        ReportService().assemble(
            run_id="run-invalid-section-results",
            project_context=ProjectContext(
                project_id="project-cs-readiness",
                base_date="2026-07-30",
            ),
            evidence=[_evidence()],
            analysis_profile=_confirmed_input_1_profile(),
            section_results=section_results,
        )
