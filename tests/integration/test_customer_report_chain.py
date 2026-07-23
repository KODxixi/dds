from __future__ import annotations

import json

import pytest

from dds.analysis_profile import resolve_analysis_profile
from dds.customer import (
    CustomerEvidenceLevel,
    CustomerIntelligenceBundle,
    CustomerSegment,
    LocalPopulationPrior,
    PersonaExperimentResult,
    SyntheticCohortManifest,
)
from dds.domain import EvidenceRecord, EvidenceType, ProjectContext, ResolvedField
from dds.reporting import render_frozen_package
from dds.services import ReportService
from dds.services.report_compiler_adapter import ReportCompilerAdapter


def evidence() -> list[EvidenceRecord]:
    return [
        EvidenceRecord(
            evidence_id=evidence_id,
            metric_id=metric_id,
            value={"qualified": True},
            evidence_type=EvidenceType.OBSERVED_FACT,
            source_id=f"source-{index}",
            source_ref=f"dds:source/{index}",
            source_hash=f"sha256:{index}",
            as_of="2026-07-23",
            geography="武汉/汉阳区",
            method="authorized aggregate customer research",
            sample_size=500,
        )
        for index, (evidence_id, metric_id) in enumerate(
            (
                ("e-population", "SC2.customer_segments"),
                ("e-segment-first", "SC2.customer_segments"),
                ("e-segment-improver", "SC2.customer_segments"),
                ("e-choice-survey", "AD3.customer_response"),
            ),
            start=1,
        )
    ]


def bundle() -> CustomerIntelligenceBundle:
    prior = LocalPopulationPrior(
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="wuhan-prior/1.0",
        household_marginals={
            "household_stage": {"new_family": 0.45, "improver": 0.55}
        },
        seed_sample_ref="dds:sample/wuhan",
        crosswalk_ref="dds:geo/wuhan",
        evidence_refs=("e-population",),
        source_hashes=("sha256:population",),
        rights_status="authorized_aggregate",
    )
    segments = (
        CustomerSegment(
            segment_id="first-home",
            label="首置通勤家庭",
            weight=0.45,
            household_stage="new_family",
            income_band="20-30万/年",
            asset_band="80-150万",
            current_housing="rent",
            purchase_stage="active_search",
            primary_needs=("通勤", "两房"),
            purchase_barriers=("首付", "交付风险"),
            evidence_refs=("e-segment-first",),
        ),
        CustomerSegment(
            segment_id="improver",
            label="改善置换家庭",
            weight=0.55,
            household_stage="family_with_children",
            income_band="30-50万/年",
            asset_band="200-350万",
            current_housing="owner",
            purchase_stage="comparison",
            primary_needs=("三房", "教育"),
            purchase_barriers=("旧房出售", "总价"),
            evidence_refs=("e-segment-improver",),
        ),
    )
    cohort = SyntheticCohortManifest(
        cohort_id="wuhan-cohort",
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="cohort/1.0",
        sample_size=1_000,
        seed=42,
        segment_weights={"first-home": 0.45, "improver": 0.55},
        generator="PopulationSim/0.10.0",
        artifact_ref="dds:cohort/wuhan.parquet",
        input_hash="sha256:cohort-input",
        output_hash="sha256:cohort-output",
        evidence_refs=("e-population",),
        rights_status="authorized_deidentified",
    )
    persona = PersonaExperimentResult(
        experiment_id="wuhan-persona",
        city="武汉",
        as_of="2026-07-23",
        version="persona/1.0",
        cohort_ref=cohort.cohort_id,
        seed=42,
        prompt_template_hash="sha256:prompt",
        aggregate_results={
            "first-home": {
                "choice_reasons": ["通勤"],
                "objections": ["首付"],
                "triggers": ["月供下降"],
                "validation_questions": ["到访后是否仍接受面积"],
            },
            "improver": {
                "choice_reasons": ["三房"],
                "objections": ["旧房出售"],
                "triggers": ["置换补贴"],
                "validation_questions": ["教育需求是否真实"],
            },
        },
        evidence_refs=("e-segment-first", "e-segment-improver"),
        artifact_hash="sha256:persona",
    )
    return CustomerIntelligenceBundle(
        bundle_id="wuhan-customer",
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="customer/1.0",
        evidence_level=CustomerEvidenceLevel.CHOICE_CALIBRATED,
        local_population=prior,
        segments=segments,
        synthetic_cohort=cohort,
        choice_simulation={
            "parameter_version": "choice/1.0",
            "seed": 42,
            "product_choice_shares": {"A": 0.42, "B": 0.31},
            "exit_share": 0.27,
            "affordable_budget_median_wan": 238,
        },
        persona_experiment=persona,
        evidence_refs=(
            "e-population",
            "e-segment-first",
            "e-segment-improver",
            "e-choice-survey",
        ),
        artifact_hashes=(
            "sha256:cohort-output",
            "sha256:choice",
            "sha256:persona",
        ),
        rights_status="authorized_deidentified",
        allowed_uses=("product_response", "scheme_comparison"),
        prohibited_uses=(
            "direct_monthly_sales_from_choice_share",
            "raw_personal_data_export",
        ),
    )


def profile(level: int) -> dict:
    raw: dict[str, object] = {"address": "武汉市汉阳区测试路 1 号"}
    if level >= 2:
        raw["constraint_sources"] = [
            {"source_ref": "dds:constraint/1", "status": "verified"}
        ]
    if level >= 3:
        raw["core_development_boundaries_ready"] = True
        raw["schemes"] = [{"scheme_id": "A"}, {"scheme_id": "B"}]
    return resolve_analysis_profile(raw, requested_level=level)


@pytest.mark.parametrize(
    ("level", "expected_titles"),
    (
        (
            1,
            {
                "地方客群机会与需求假设",
                "数字人定位与概念路线推演",
                "标准盘客群响应压力测试",
            },
        ),
        (
            2,
            {
                "地方客群、支付能力与约束匹配",
                "数字人产品与约束响应",
                "项目货量需求信号与现金流边界",
            },
        ),
        (
            3,
            {
                "统一客群基线与样本冻结",
                "同 cohort 多方案行为比选",
                "客群选择与风险调整方案比较",
            },
        ),
    ),
)
def test_customer_bundle_projects_to_each_input_profile(
    level: int,
    expected_titles: set[str],
) -> None:
    run = ReportService().assemble(
        run_id=f"customer-input-{level}",
        project_context=ProjectContext(
            project_id=f"customer-{level}",
            project_name=f"Customer Input {level}",
            city="武汉",
            district="汉阳区",
            base_date="2026-07-23",
        ),
        evidence=evidence(),
        analysis_profile=profile(level),
        customer_intelligence=bundle(),
    )
    customer_field = run.sections["SC2"].data["customer_segments"]
    assert isinstance(customer_field, ResolvedField)
    assert customer_field.is_resolved is True
    seed = ReportCompilerAdapter().build_report_seed(run)
    customer_pages = [
        page
        for page in seed["page_manifest"]
        if page["page_id"].endswith("-customer-intelligence")
    ]
    assert len(customer_pages) == 5
    assert expected_titles.issubset({page["title"] for page in customer_pages})
    evidence_types = {page["section_id"]: page["evidence_type"] for page in customer_pages}
    assert evidence_types["SC2"] == "observed_fact"
    assert evidence_types["AD3"] == "model_simulation"
    assert evidence_types["VA2"] == "model_simulation"
    serialized = json.dumps(seed, ensure_ascii=False, sort_keys=True)
    assert "不是实际客户原话" in serialized
    assert "不得换算月销量" in serialized
    assert "sha256:prompt" in serialized
    assert "transcript" not in serialized.lower()
    assert "phone" not in serialized.lower()
    html = render_frozen_package(
        ReportCompilerAdapter().build_frozen_compiler_package(run)
    )
    assert all(title in html for title in expected_titles)
    assert "不得换算月销量" in html
    assert "transcript" not in html.lower()


def test_customer_segments_cannot_be_resolved_from_model_simulation() -> None:
    records = evidence()
    records[1].evidence_type = EvidenceType.MODEL_SIMULATION
    with pytest.raises(ValueError, match="observed or social"):
        ReportService().assemble(
            run_id="invalid-customer",
            project_context=ProjectContext(
                project_id="invalid",
                city="武汉",
                district="汉阳区",
                base_date="2026-07-23",
            ),
            evidence=records,
            analysis_profile=profile(1),
            customer_intelligence=bundle(),
        )
