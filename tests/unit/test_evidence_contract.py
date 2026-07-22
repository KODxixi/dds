from __future__ import annotations

import asyncio

import pytest

from dds.contracts import (
    CONFIDENCE_WEIGHTS,
    EVIDENCE_TYPES,
    RESOLUTION_STATUSES,
    EvidenceRecord,
    EvidenceType,
    ProjectContext,
    ReportRun,
    ResolvedField,
    ResolvedStatus,
    SectionResult,
    compute_confidence_from_evidence,
)
from dds.data.fallback import FallbackEngine
from dds.data.orchestrator import DataOrchestrator


def test_public_enums_and_constructor_fields_are_stable() -> None:
    assert EvidenceType.OBSERVED_FACT == "observed_fact"
    assert ResolvedStatus.HUMAN_INPUT == "human_input"
    assert set(EVIDENCE_TYPES) == {item.value for item in EvidenceType}
    assert set(RESOLUTION_STATUSES) == {item.value for item in ResolvedStatus}

    record = EvidenceRecord(
        evidence_id="e1",
        metric_id="SC2.competitors",
        value=12,
        unit="个",
        evidence_type="observed_fact",
        source_id="registry",
        source_ref="https://example.test/source",
        source_hash="sha256:abc",
        observed_at="2026-06-01",
        as_of="2026-05-31",
        geography="北京",
        method="registry export",
        sample_size=12,
        limitations=["test limitation"],
        confidence=None,
    )
    assert record.effective_at == "2026-06-01"
    assert record.has_traceable_source
    assert record.to_dict()["evidence_type"] == "observed_fact"


def test_project_context_round_trip_preserves_unknown_keys() -> None:
    context = ProjectContext.from_mapping(
        {
            "project_id": "P-1",
            "city": "北京",
            "project_type": "住宅",
            "custom_scope": {"radius_km": 3},
        }
    )
    assert context.target_geography == "北京"
    assert context.to_dict()["custom_scope"] == {"radius_km": 3}


def test_no_evidence_means_zero_for_every_confidence_dimension() -> None:
    confidence = compute_confidence_from_evidence(
        [],
        required_metric_ids=["SC2.competitors"],
        project_context={"city": "北京", "base_date": "2026-07-01"},
    )
    assert confidence["score"] == 0.0
    assert confidence["cap"] == 0.0
    assert confidence["derived_from_evidence"] is True
    assert all(
        confidence["breakdown"][dimension]["value"] == 0.0
        for dimension in CONFIDENCE_WEIGHTS
    )


def test_confidence_dimensions_are_derived_from_record_metadata() -> None:
    complete = EvidenceRecord(
        evidence_id="e1",
        metric_id="SC2.competitors",
        value=12,
        source_id="registry",
        source_ref="https://example.test/source",
        observed_at="2026-06-01",
        geography="北京",
        method="registry export",
        sample_size=1000,
    )
    sparse = EvidenceRecord(
        evidence_id="e2",
        metric_id="SC2.competitors",
        value=12,
        source_id="registry",
        source_ref="https://example.test/source-2",
    )
    kwargs = {
        "required_metric_ids": ["SC2.competitors"],
        "project_context": {"city": "北京", "base_date": "2026-07-01"},
    }
    complete_score = compute_confidence_from_evidence([complete], **kwargs)
    sparse_score = compute_confidence_from_evidence([sparse], **kwargs)

    assert complete_score["breakdown"]["freshness"]["value"] == 1.0
    assert complete_score["breakdown"]["geographic_relevance"]["value"] == 1.0
    assert complete_score["breakdown"]["method_fit"]["value"] == 1.0
    assert sparse_score["breakdown"]["freshness"]["value"] == 0.0
    assert sparse_score["breakdown"]["geographic_relevance"]["value"] == 0.0
    assert sparse_score["breakdown"]["method_fit"]["value"] == 0.0
    assert complete_score["score"] > sparse_score["score"]


@pytest.mark.parametrize(
    "strategy",
    ["city_benchmark", "project_type_average", "national_benchmark"],
)
def test_source_less_legacy_benchmark_falls_back_to_explicit_unknown(strategy: str) -> None:
    value, origin = FallbackEngine(city="北京", project_type="住宅").get_fallback_by_strategy(
        strategy,
        "SC2",
        "competitors",
    )
    assert isinstance(value, ResolvedField)
    assert value.status == ResolvedStatus.UNKNOWN
    assert value.value is None
    assert value.evidence_refs == []
    assert origin.source == "unknown"
    assert "没有 EvidenceRecord" in value.reason


def test_orchestrator_does_not_award_fixed_confidence_to_missing_data() -> None:
    section = asyncio.run(
        DataOrchestrator(city="北京", project_type="住宅").ensure_section_data(
            "SC1",
            {"city": "北京", "project_type": "住宅"},
        )
    )
    assert section.confidence["score"] == 0.0
    assert section.confidence["derived_from_evidence"] is True
    assert set(section.missing_fields) == {
        "project_id",
        "decision_question",
        "evidence_boundary",
        "base_date",
    }
    assert all(
        section.field_resolution(field).status == ResolvedStatus.HUMAN_INPUT
        for field in section.missing_fields
    )


def test_report_run_accepts_evidence_alias_without_losing_records() -> None:
    record = EvidenceRecord(
        evidence_id="e1",
        metric_id="m1",
        value=1,
        source_id="s1",
        source_ref="https://example.test/e1",
    )
    run = ReportRun(
        run_id="r1",
        project_context={"project_id": "p1"},
        sections={"SC1": SectionResult(section_id="SC1")},
        evidence=[record],
    )
    assert run.evidence_records == [record]
    assert run.evidence_by_id == {"e1": record}
    assert run.to_dict()["evidence_records"][0]["evidence_id"] == "e1"
