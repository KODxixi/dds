from __future__ import annotations

import copy

import pytest

from dds.contracts import (
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    ContractViolationError,
    EvidenceRecord,
    ReportRun,
    ResolvedField,
    ResolvedStatus,
    SectionResult,
)
from dds.engine.contract_enforcer import GATE_ORDER, ContractEnforcer
from dds.analysis_profile import resolve_intervention_profile


def _valid_run() -> ReportRun:
    records = []
    sections = {}
    for section_id in VALID_SECTION_IDS:
        data = {}
        section_refs = []
        for field_name in SECTION_REQUIREMENTS[section_id]:
            if section_id == "CS":
                value = [] if field_name == "data_gaps" else [f"declared-{field_name}"]
                data[field_name] = ResolvedField(
                    status=ResolvedStatus.RESOLVED,
                    value=value,
                )
                continue
            evidence_id = f"e-{section_id}-{field_name}"
            records.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    metric_id=f"{section_id}.{field_name}",
                    value=f"value-{field_name}",
                    source_id=f"source-{section_id}-{field_name}",
                    source_ref=f"https://example.test/{evidence_id}",
                    observed_at="2026-06-01",
                    geography="北京",
                    method="measured export",
                    sample_size=1000,
                )
            )
            section_refs.append(evidence_id)
            data[field_name] = ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=f"value-{field_name}",
                evidence_refs=[evidence_id],
            )
        sections[section_id] = SectionResult(
            section_id=section_id,
            data=data,
            evidence_refs=section_refs,
            status=ResolvedStatus.RESOLVED,
        )
    return ReportRun(
        run_id="run-1",
        project_context={
            "project_id": "project-1",
            "city": "北京",
            "base_date": "2026-07-01",
        },
        sections=sections,
        evidence_records=records,
        status=ResolvedStatus.RESOLVED,
    )


def test_valid_report_passes_all_four_gates_and_empty_data_gaps_is_legal() -> None:
    run = _valid_run()
    assert run.sections["CS"].data["data_gaps"].value == []

    result = ContractEnforcer().validate_report(run)

    assert result.valid
    assert tuple(result.gates) == GATE_ORDER
    assert result.gate_status == {
        "structure": "pass",
        "evidence": "pass",
        "decision": "pass",
        "delivery": "pass",
    }
    assert result.overall_confidence > 0.55
    assert result.structure_valid is True
    assert result.evidence_valid is True
    assert result.decision_ready is True
    assert result.delivery_ready is True
    serialized = result.to_dict()
    assert serialized["structure_valid"] is True
    assert serialized["evidence_valid"] is True
    assert serialized["decision_ready"] is True
    assert serialized["delivery_ready"] is True


def test_section_mapping_key_must_match_internal_section_id() -> None:
    run = _valid_run()
    run.sections["SC1"].section_id = "SC2"

    result = ContractEnforcer().validate_report(run)

    assert result.gates["structure"].status == "fail"
    assert any("与内部 section_id SC2 不一致" in item for item in result.errors)
    assert result.gates["delivery"].status == "fail"
    assert result.structure_valid is False
    assert result.evidence_valid is False
    assert result.decision_ready is False
    assert result.delivery_ready is False


def test_explicit_unknown_is_structurally_valid_but_not_decision_ready() -> None:
    run = _valid_run()
    run.sections["SC2"].data["competitors"] = ResolvedField(
        status=ResolvedStatus.UNKNOWN,
        value=None,
        reason="尚无竞品成交数据",
    )
    run.sections["SC2"].status = ResolvedStatus.PARTIAL
    run.sections["CS"].data["data_gaps"] = ResolvedField(
        status=ResolvedStatus.RESOLVED,
        value=[
            {
                "section_id": "SC2",
                "section": "SC2",
                "field": "competitors",
                "status": "unknown",
            }
        ],
    )

    result = ContractEnforcer().validate_report(run)

    assert result.gates["structure"].status == "pass"
    assert result.gates["decision"].status == "fail"
    assert any("competitors:unknown" in item for item in result.errors)
    assert result.gates["delivery"].status == "fail"


def test_unresolved_cs_field_blocks_delivery_even_when_decisions_pass() -> None:
    run = _valid_run()
    run.sections["CS"].data["confidence_gaps"] = ResolvedField(
        status=ResolvedStatus.UNKNOWN,
        value=None,
        reason="置信缺口尚未审阅",
    )

    result = ContractEnforcer().validate_report(run)

    assert result.gates["structure"].status == "pass"
    assert result.gates["evidence"].status == "pass"
    assert result.gates["decision"].status == "pass"
    assert result.gates["delivery"].status == "fail"
    assert any("CS 字段 [confidence_gaps]" in item for item in result.errors)


def test_source_less_benchmark_cannot_pass_evidence_gate() -> None:
    run = _valid_run()
    target = next(
        item for item in run.evidence_records if item.metric_id == "SC2.competitors"
    )
    target.source_id = "city_benchmark"
    target.source_ref = ""
    target.source_hash = ""
    target.method = "city benchmark"

    result = ContractEnforcer().validate_report(run)

    assert result.gates["structure"].status == "pass"
    assert result.gates["evidence"].status == "fail"
    assert any("基准数据但缺少 source_ref/source_hash" in item for item in result.errors)
    assert not result.valid


def test_declared_confidence_cannot_launder_weaker_evidence() -> None:
    run = _valid_run()
    section = run.sections["SC1"]
    section.confidence = {"score": 1.0}
    for record in run.evidence_records:
        if record.metric_id.startswith("SC1."):
            record.observed_at = None
            record.geography = ""
            record.method = ""
            record.sample_size = None

    result = ContractEnforcer().validate_report(run)

    assert result.gates["evidence"].status == "fail"
    assert any("声明置信度" in item and "高于证据派生值" in item for item in result.errors)


def test_missing_evidence_fails_even_when_all_required_keys_exist() -> None:
    run = _valid_run()
    run.evidence_records = []
    run.evidence = []

    result = ContractEnforcer().validate_report(run)

    assert result.gates["structure"].status == "pass"
    assert result.gates["evidence"].status == "fail"
    assert any("没有 EvidenceRecord" in item for item in result.errors)


def test_empty_non_gap_collection_is_not_silently_accepted() -> None:
    run = _valid_run()
    run.sections["CS"].data["sources"] = []

    result = ContractEnforcer().validate_report(run)

    assert result.gates["structure"].status == "fail"
    assert any("CS 字段 [sources] 为空" in item for item in result.errors)


def test_enforce_contract_raises_on_any_failed_gate() -> None:
    run = _valid_run()
    run.sections.pop("VA3")

    with pytest.raises(ContractViolationError):
        ContractEnforcer().enforce_contract(run)


def test_validation_does_not_mutate_report() -> None:
    run = _valid_run()
    snapshot = copy.deepcopy(run.to_dict())

    ContractEnforcer().validate_report(run)

    assert run.to_dict() == snapshot


def test_profile_optional_units_do_not_block_four_gates() -> None:
    run = _valid_run()
    profile = resolve_intervention_profile(
        {"address": "测试城测试路 1 号"},
        selected_mode=1,
        confirmed=True,
    )
    run.metadata["analysis_profile"] = profile
    run.metadata["decision_scope"] = profile["decision_scope"]
    run.sections.pop("SC3")
    run.sections.pop("AD5")
    run.sections.pop("VA1")

    result = ContractEnforcer().validate_report(run)

    assert result.valid
    assert set(result.section_confidences) == set(profile["required_units"])


def test_tampered_profile_cannot_remove_input_3_required_unit() -> None:
    run = _valid_run()
    profile = resolve_intervention_profile(
        {
            "address": "Test City Road 1",
            "constraint_sources": [{"source_ref": "client://planning-v1"}],
            "core_development_boundaries_ready": True,
            "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
        },
        selected_mode=3,
        confirmed=True,
    )
    profile["required_units"].remove("VA1")
    run.metadata.update(
        {
            "analysis_profile": profile,
            "decision_scope": "scheme_review",
        }
    )
    run.sections.pop("VA1")

    result = ContractEnforcer().validate_report(run)

    assert result.structure_valid is False
    assert any("intervention registry" in error for error in result.errors)
    assert any("VA1" in error for error in result.errors)
