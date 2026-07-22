from __future__ import annotations

from dds.domain import EvidenceRecord, EvidenceType, ProjectContext, ResolvedStatus
from dds.engines.product import (
    DESIGN_TASK_KEYS,
    ProductEngine,
    ProductStatus,
    UnitMixRow,
)
from dds.services.report_service import ReportService


def _evidence(evidence_id: str, metric_id: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        metric_id=metric_id,
        value={"fixture": True},
        evidence_type=EvidenceType.OBSERVED_FACT,
        source_id="fixture",
        source_ref="fixture://product-positioning",
        source_hash="a" * 64,
        observed_at="2026-07-22",
        geography="武汉",
        method="integration fixture",
    )


def _assemble(product, evidence=()):
    return ReportService().assemble(
        run_id="product-sections",
        project_context=ProjectContext(
            project_id="product-fixture",
            city="武汉",
            decision_question="产品定位双模式",
            base_date="2026-07-22",
        ),
        evidence=evidence,
        product=product,
    )


def test_propose_mode_maps_three_options_selection_boundaries_and_five_briefs():
    result = ProductEngine().propose_concept_schemes(
        market_evidence_refs=("market:E1",),
        preferred_direction_id="B",
        statutory_inputs_complete=False,
    )
    assert result.status is ProductStatus.CONCEPT_ONLY
    assert len(result.concepts) == 3
    assert all(item.evidence_type == "analysis_inference" for item in result.concepts)
    assert all(set(item.design_tasks) == set(DESIGN_TASK_KEYS) for item in result.concepts)

    run = _assemble(result, (_evidence("market:E1", "SC2.competitors"),))
    ad1, ad2, ad3, ad4 = (run.sections[key] for key in ("AD1", "AD2", "AD3", "AD4"))

    assert [ad1.data[f"option_{index}"].value["direction_id"] for index in (1, 2, 3)] == [
        "A",
        "B",
        "C",
    ]
    matrix = ad1.data["comparison_matrix"].value
    assert matrix["dimensions"] == list(result.comparison_dimensions)
    assert len(matrix["rows"]) == 3
    assert {row["evidence_type"] for row in matrix["rows"]} == {"analysis_inference"}
    assert ad1.status is ResolvedStatus.PARTIAL

    assert ad2.data["recommended_option"].value["direction_id"] == "B"
    assert ad2.data["recommended_option"].status is ResolvedStatus.PARTIAL
    assert len(ad2.data["elimination_reasons"].value) == 3
    assert len(ad2.data["validation_thresholds"].value) == 3
    assert all(
        item["evidence_type"] == "analysis_inference"
        for item in ad2.data["validation_thresholds"].value
    )

    # Directional concepts are not allowed to become quantitative product facts.
    assert all(field.status is ResolvedStatus.UNKNOWN for field in ad3.data.values())
    assert all(field.value is None for field in ad3.data.values())
    assert ad3.status is ResolvedStatus.PARTIAL

    assert set(ad4.data) == set(DESIGN_TASK_KEYS)
    assert ad4.status is ResolvedStatus.PARTIAL
    for field in ad4.data.values():
        assert field.status is ResolvedStatus.PARTIAL
        assert len(field.value) == 3
        assert {item["direction_id"] for item in field.value} == {"A", "B", "C"}
        assert all(item["boundary"] == "concept_only_no_ratio_geometry" for item in field.value)
        assert all(item["evidence_type"] == "analysis_inference" for item in field.value)


def test_audit_mode_audits_only_existing_scheme_and_does_not_invent_alternatives():
    result = ProductEngine().audit_existing_scheme(
        (
            UnitMixRow("主流", 90, 60, 20_000, 0.6, ("scheme:E1",)),
            UnitMixRow("改善", 130, 40, 24_000, 0.4, ("scheme:E1",)),
        ),
        declared_total_units=100,
        declared_saleable_area_m2=10_600,
        declared_saleable_value_cny=232_800_000,
        statutory_inputs_complete=True,
    )
    assert result.status is ProductStatus.READY
    assert result.concepts == ()

    run = _assemble(result, (_evidence("scheme:E1", "AD3.product_mix"),))
    ad1, ad2, ad3, ad4 = (run.sections[key] for key in ("AD1", "AD2", "AD3", "AD4"))

    assert ad1.data["option_1"].value["option_id"] == "existing_scheme"
    assert ad1.data["option_1"].value["evidence_type"] == "analysis_inference"
    assert ad1.data["option_2"].status is ResolvedStatus.NOT_APPLICABLE
    assert ad1.data["option_2"].value is None
    assert ad1.data["option_3"].status is ResolvedStatus.NOT_APPLICABLE
    assert ad1.data["option_3"].value is None
    assert len(ad1.data["comparison_matrix"].value["rows"]) == 1

    assert ad2.data["recommended_option"].status is ResolvedStatus.NOT_APPLICABLE
    assert ad2.data["elimination_reasons"].status is ResolvedStatus.NOT_APPLICABLE
    assert ad2.data["validation_thresholds"].value["reconciliation_tolerance"] == 0.005
    assert ad3.data["product_mix"].value == {"主流": 0.6, "改善": 0.4}
    assert ad3.data["area_segments"].value == {
        "total_units": 100,
        "saleable_area_m2": 10_600,
    }
    assert ad3.data["price_bands"].value == {"saleable_value_cny": 232_800_000.0}

    # No drawings were supplied, so audit mode cannot manufacture AD4 briefs.
    assert ad4.status is ResolvedStatus.UNKNOWN
    assert all(field.status is ResolvedStatus.UNKNOWN for field in ad4.data.values())
    assert all(field.value is None for field in ad4.data.values())


def test_missing_market_evidence_fails_closed_for_recommendation_and_elimination():
    result = ProductEngine().propose_concept_schemes(
        market_evidence_refs=(),
        preferred_direction_id="B",
        statutory_inputs_complete=False,
    )
    assert result.status is ProductStatus.BLOCKED
    assert result.recommended_direction_id is None

    run = _assemble(result)
    ad1, ad2, ad3, ad4 = (run.sections[key] for key in ("AD1", "AD2", "AD3", "AD4"))
    assert ad1.status is ResolvedStatus.UNKNOWN
    assert all(ad1.data[f"option_{index}"].status is ResolvedStatus.PARTIAL for index in (1, 2, 3))
    assert ad2.status is ResolvedStatus.UNKNOWN
    assert ad2.data["recommended_option"].status is ResolvedStatus.UNKNOWN
    assert ad2.data["recommended_option"].value is None
    assert ad2.data["elimination_reasons"].status is ResolvedStatus.UNKNOWN
    assert ad2.data["elimination_reasons"].value is None
    assert all(field.value is None for field in ad3.data.values())
    assert ad4.status is ResolvedStatus.UNKNOWN
    assert not ad2.evidence_refs
