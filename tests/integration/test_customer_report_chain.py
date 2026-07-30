from __future__ import annotations

from dataclasses import replace
import json

import pytest

from dds.analysis_profile import resolve_intervention_profile
from dds.customer import (
    CustomerEvidenceLevel,
    CustomerIntelligenceBundle,
    CustomerSegment,
    DemandDriverEvent,
    FutureCustomerScenario,
    LocalPopulationPrior,
    PersonaExperimentResult,
    SyntheticCohortManifest,
)
from dds.domain import EvidenceRecord, EvidenceType, ProjectContext, ResolvedField
from dds.reporting import compile_frozen_package, render_frozen_package
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


def future_evidence() -> list[EvidenceRecord]:
    return [
        EvidenceRecord(
            evidence_id="e-headquarters-operating",
            metric_id="SC2.future_demand_event_scan",
            value={"status": "current_operation"},
            evidence_type=EvidenceType.OBSERVED_FACT,
            source_id="source-headquarters-operating",
            source_ref="https://example.gov.cn/headquarters-operating",
            source_hash="sha256:headquarters-operating",
            as_of="2026-07-23",
            geography="武汉/汉阳区",
            method="government operation bulletin",
        ),
        EvidenceRecord(
            evidence_id="e-headquarters-plan",
            metric_id="SC2.future_demand_event_scan",
            value={"status": "planned_capacity"},
            evidence_type=EvidenceType.OBSERVED_FACT,
            source_id="source-headquarters-plan",
            source_ref="https://example.gov.cn/headquarters-plan",
            source_hash="sha256:headquarters-plan",
            as_of="2026-07-23",
            geography="武汉/汉阳区",
            method="statutory planning document",
        ),
        EvidenceRecord(
            evidence_id="e-future-customer-model",
            metric_id="AD3.customer_response",
            value={"scenario": "headquarters-family-improver"},
            evidence_type=EvidenceType.MODEL_SIMULATION,
            source_id="source-future-customer-model",
            source_ref="dds:model/future-customer",
            source_hash="sha256:future-customer-model",
            as_of="2026-07-23",
            geography="武汉/汉阳区",
            method="bounded future customer scenario",
        ),
    ]


def future_event() -> DemandDriverEvent:
    return DemandDriverEvent(
        event_id="local-tech-headquarters",
        label="本地科技总部投入运营",
        event_type="major_employer_or_headquarters",
        geography="武汉/汉阳区",
        status="current_operation",
        time_window="2026—2028",
        observed_facts=(
            "总部已投入运营并形成稳定通勤人口。",
            "后续容量仍属于规划边界，不能视为当前人口。",
        ),
        evidence_refs=("e-headquarters-operating", "e-headquarters-plan"),
        counter_factors=("园区宿舍与租赁住房会分流部分居住需求。",),
        allowed_uses=("future_customer_scenario",),
        prohibited_uses=("direct_buyer_count",),
    )


def future_scenario() -> FutureCustomerScenario:
    return FutureCustomerScenario(
        scenario_id="headquarters-family-improver",
        event_ids=("local-tech-headquarters",),
        segment_label="总部管理层与成家型核心员工",
        time_horizon="0—3 年",
        entry_trigger="总部稳定运营、家庭形成并产生改善需求",
        housing_path="先租住或园区居住，家庭稳定后比较周边改善住宅",
        behavior_changes=("通勤半径收窄", "更重视私密性与家庭配套"),
        product_implications=("验证大户型总价", "强化归家私密性与会所效率"),
        exit_conditions=("园区住房充分吸收需求", "总价超过客群承受上限"),
        evidence_refs=("e-headquarters-operating", "e-future-customer-model"),
        prohibited_uses=("headcount_or_conversion_rate_as_fact",),
    )


def future_bundle(*, include_scenario: bool = True) -> CustomerIntelligenceBundle:
    current = bundle()
    return replace(
        current,
        future_demand_scan_status="material_events_found",
        demand_driver_events=(future_event(),),
        future_customer_scenarios=(
            (future_scenario(),) if include_scenario else ()
        ),
        evidence_refs=(
            *current.evidence_refs,
            "e-headquarters-operating",
            "e-headquarters-plan",
            *(
                ("e-future-customer-model",)
                if include_scenario
                else ()
            ),
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
    return resolve_intervention_profile(
        raw,
        selected_mode=level,
        confirmed=True,
    )


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


def test_customer_confidence_uses_evidence_level_not_intervention_mode() -> None:
    scores_by_mode: dict[int, set[float]] = {}
    for mode in (1, 2, 3):
        run = ReportService().assemble(
            run_id=f"customer-confidence-input-{mode}",
            project_context=ProjectContext(
                project_id=f"customer-confidence-{mode}",
                project_name=f"Customer Confidence Input {mode}",
                city="武汉",
                district="汉阳区",
                base_date="2026-07-23",
            ),
            evidence=evidence(),
            analysis_profile=profile(mode),
            customer_intelligence=bundle(),
        )
        pages = [
            page
            for page in ReportCompilerAdapter().build_report_seed(run)["page_manifest"]
            if page["page_id"].endswith("-customer-intelligence")
        ]
        scores_by_mode[mode] = {
            float(page["confidence"]["score"]) for page in pages
        }

    assert scores_by_mode == {
        1: {0.65},
        2: {0.65},
        3: {0.65},
    }


def test_c1_customer_baseline_cannot_make_input_2_va3_or_cs_ready() -> None:
    baseline = replace(
        bundle(),
        evidence_level=CustomerEvidenceLevel.LOCAL_BASELINE,
        synthetic_cohort=None,
        choice_simulation=None,
        persona_experiment=None,
        artifact_hashes=("sha256:baseline",),
        allowed_uses=("product_direction",),
    )
    run = ReportService().assemble(
        run_id="customer-c1-input-2",
        project_context=ProjectContext(
            project_id="customer-c1-input-2",
            project_name="Customer C1 Input 2",
            city="武汉",
            district="汉阳区",
            base_date="2026-07-23",
        ),
        evidence=evidence(),
        analysis_profile=profile(2),
        customer_intelligence=baseline,
    )
    adapter = ReportCompilerAdapter()
    seed = adapter.build_report_seed(run)
    customer_pages = [
        page
        for page in seed["page_manifest"]
        if page.get("story_role") == "customer_intelligence"
    ]
    document = compile_frozen_package(adapter.build_frozen_compiler_package(run))
    serialized = json.dumps(customer_pages, ensure_ascii=False, sort_keys=True)
    data_gaps = run.sections["CS"].data["data_gaps"].value

    assert not [
        page for page in customer_pages if page["section_id"] == "VA3"
    ]
    assert all(
        page["unit_role"] == "decision_chain_support"
        for page in customer_pages
    )
    assert "证据等级 C1" in serialized
    assert "当前为 C2 方案比较证据" not in serialized
    assert data_gaps
    assert all(
        {
            "section_id",
            "field",
            "status",
            "reason",
        }.issubset(item)
        for item in data_gaps
    )
    assert {
        (item["section_id"], item["field"])
        for item in data_gaps
    }.issuperset(
        {
            ("VA3", "risks"),
            ("VA3", "owners"),
            ("VA3", "triggers"),
            ("VA3", "acceptance_criteria"),
        }
    )
    assert document["qa"]["unit_readiness"]["VA3"] != "ready"
    assert document["qa"]["unit_readiness"]["CS"] != "ready"
    assert document["qa"]["delivery_ready"] is False


def test_customer_model_pages_support_but_do_not_resolve_core_units() -> None:
    run = ReportService().assemble(
        run_id="customer-supporting-input-2",
        project_context=ProjectContext(
            project_id="customer-supporting-input-2",
            project_name="Customer Supporting Input 2",
            city="武汉",
            district="汉阳区",
            base_date="2026-07-23",
        ),
        evidence=evidence(),
        analysis_profile=profile(2),
        customer_intelligence=bundle(),
    )
    seed = ReportCompilerAdapter().build_report_seed(run)
    pages = [
        page
        for page in seed["page_manifest"]
        if page.get("story_role") == "customer_intelligence"
    ]

    assert pages
    assert all(page["unit_role"] == "decision_chain_support" for page in pages)
    assert {
        page["section_id"]: page["unit_status"]
        for page in pages
        if page["section_id"] in {"AD3", "VA2", "VA3", "CS"}
    } == {
        "AD3": "partial",
        "VA2": "partial",
        "VA3": "partial",
        "CS": "partial",
    }


def test_empty_persona_objections_do_not_create_va3_page() -> None:
    customer_bundle = bundle()
    empty_persona = replace(
        customer_bundle.persona_experiment,
        aggregate_results={
            "first-home": {
                "choice_reasons": ["通勤"],
                "objections": [],
                "triggers": ["  "],
                "validation_questions": [],
            },
            "improver": {
                "choice_reasons": ["三房"],
                "objections": None,
                "triggers": [],
                "validation_questions": [""],
            },
        },
    )
    run = ReportService().assemble(
        run_id="customer-empty-objections-input-2",
        project_context=ProjectContext(
            project_id="customer-empty-objections-input-2",
            project_name="Customer Empty Objections Input 2",
            city="武汉",
            district="汉阳区",
            base_date="2026-07-23",
        ),
        evidence=evidence(),
        analysis_profile=profile(2),
        customer_intelligence=replace(
            customer_bundle,
            persona_experiment=empty_persona,
        ),
    )
    pages = ReportCompilerAdapter().build_report_seed(run)["page_manifest"]

    assert not [
        page
        for page in pages
        if page.get("story_role") == "customer_intelligence"
        and page["section_id"] == "VA3"
    ]


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


def test_future_demand_events_project_to_fact_and_behavior_action_pages() -> None:
    run = ReportService().assemble(
        run_id="future-customer-input-2",
        project_context=ProjectContext(
            project_id="future-customer-2",
            project_name="Future Customer Input 2",
            city="武汉",
            district="汉阳区",
            base_date="2026-07-23",
        ),
        evidence=[*evidence(), *future_evidence()],
        analysis_profile=profile(2),
        customer_intelligence=future_bundle(),
    )

    seed = ReportCompilerAdapter().build_report_seed(run)
    pages = {page["page_id"]: page for page in seed["page_manifest"]}
    event_page = pages["sc2-future-demand-events"]
    assert event_page["page_role"] == "future_demand_event"
    assert event_page["evidence_type"] == "observed_fact"
    assert event_page["diagram_specs"][0]["diagram_type"] == "phasing"
    assert "本地科技总部投入运营" in json.dumps(
        event_page, ensure_ascii=False
    )
    assert "园区宿舍与租赁住房会分流" in json.dumps(
        event_page, ensure_ascii=False
    )

    outlook_page = pages["sc2-future-customer-actions"]
    assert outlook_page["page_role"] == "future_customer_outlook"
    assert outlook_page["evidence_type"] == "model_simulation"
    assert outlook_page["future_customer_chain"] == {
        "event": ["本地科技总部投入运营"],
        "customer": ["总部管理层与成家型核心员工"],
        "behavior": ["通勤半径收窄", "更重视私密性与家庭配套"],
        "product_action": ["验证大户型总价", "强化归家私密性与会所效率"],
    }
    serialized = json.dumps(outlook_page, ensure_ascii=False)
    assert "先租住或园区居住" in serialized
    assert "园区住房充分吸收需求" in serialized
    assert "不得把园区人数或规划容量直接写成项目客户" in serialized


def test_material_future_demand_event_without_scenario_is_blocked() -> None:
    with pytest.raises(
        ValueError,
        match="material future demand events require future customer scenarios",
    ):
        ReportService().assemble(
            run_id="future-customer-missing-scenario",
            project_context=ProjectContext(
                project_id="future-customer-missing-scenario",
                city="武汉",
                district="汉阳区",
                base_date="2026-07-23",
            ),
            evidence=[*evidence(), *future_evidence()[:2]],
            analysis_profile=profile(2),
            customer_intelligence=future_bundle(include_scenario=False),
        )


def test_compiler_rejects_material_event_when_scenario_was_dropped() -> None:
    run = ReportService().assemble(
        run_id="future-customer-compiler-guard",
        project_context=ProjectContext(
            project_id="future-customer-compiler-guard",
            city="武汉",
            district="汉阳区",
            base_date="2026-07-23",
        ),
        evidence=[*evidence(), *future_evidence()],
        analysis_profile=profile(2),
        customer_intelligence=future_bundle(),
    )
    run.metadata["customer_intelligence"]["future_customer_scenarios"] = []

    with pytest.raises(
        ValueError,
        match="the event-to-customer chain cannot be omitted",
    ):
        ReportCompilerAdapter().build_report_seed(run)
