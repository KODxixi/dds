from __future__ import annotations

import pytest

from dds.customer import (
    ChoiceModelArtifact,
    CustomerEvidenceLevel,
    CustomerIntelligenceBundle,
    CustomerSegment,
    DemandDriverEvent,
    FutureCustomerScenario,
    LocalPopulationPrior,
    PersonaExperimentResult,
    SyntheticCohortManifest,
    customer_intelligence_from_mapping,
)


def prior(*, with_seed: bool = True) -> LocalPopulationPrior:
    return LocalPopulationPrior(
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="wuhan-prior/1.0",
        household_marginals={
            "household_stage": {"new_family": 0.45, "improver": 0.55},
            "current_housing": {"rent": 0.35, "owner": 0.65},
        },
        seed_sample_ref="dds:sample/wuhan-households" if with_seed else "",
        crosswalk_ref="dds:geo/wuhan-submarket" if with_seed else "",
        evidence_refs=("e-population",),
        source_hashes=("sha256:population",),
        rights_status="authorized_aggregate",
    )


def segments() -> tuple[CustomerSegment, ...]:
    return (
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


def cohort() -> SyntheticCohortManifest:
    return SyntheticCohortManifest(
        cohort_id="wuhan-2026",
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="cohort/1.0",
        sample_size=1_000,
        seed=42,
        segment_weights={"first-home": 0.45, "improver": 0.55},
        generator="PopulationSim/0.10.0",
        artifact_ref="dds:cohort/wuhan-2026.parquet",
        input_hash="sha256:input",
        output_hash="sha256:output",
        evidence_refs=("e-population", "e-household-survey"),
        rights_status="authorized_deidentified",
    )


def choice_model() -> ChoiceModelArtifact:
    return ChoiceModelArtifact(
        model_id="wuhan-choice",
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="choice/1.0",
        segment_feature_coefficients={
            "first-home": {"total_price_wan": -0.03, "commute": 0.8},
            "improver": {"total_price_wan": -0.02, "education": 0.6},
        },
        alternative_intercepts={"A": 0.2, "B": 0.1},
        outside_option_intercept=-0.15,
        price_feature="total_price_wan",
        feature_units={
            "total_price_wan": "CNY 10k",
            "commute": "index",
            "education": "index",
        },
        availability_rules={"requires_displayed_choice_set": True},
        validation_metrics={"holdout_log_loss": 0.54},
        evidence_refs=("e-choice-survey",),
        artifact_hash="sha256:choice",
    )


def demand_driver_event() -> DemandDriverEvent:
    return DemandDriverEvent(
        event_id="penguin-island-operation",
        label="企鹅岛总部投入运营",
        event_type="major_employer_or_headquarters",
        geography="深圳/宝安",
        status="current_operation",
        time_window="2025-2028",
        observed_facts=("2025年首批员工入驻", "2026年已形成数万员工通勤"),
        evidence_refs=("e-penguin-operation", "e-penguin-traffic"),
        counter_factors=("园区内部员工公寓会吸收一部分居住需求",),
        allowed_uses=("future_customer_hypothesis",),
        prohibited_uses=("direct_employee_headcount_to_home_sales",),
        scale_metrics={"planned_employment": 64_000},
    )


def future_customer_scenario(
    *,
    event_ids: tuple[str, ...] = ("penguin-island-operation",),
    evidence_type: str = "model_simulation",
    prohibited_uses: tuple[str, ...] = (
        "headcount_or_conversion_rate_as_fact",
    ),
) -> FutureCustomerScenario:
    return FutureCustomerScenario(
        scenario_id="penguin-family-improver",
        event_ids=event_ids,
        segment_label="家庭化高级技术与产品骨干",
        time_horizon="0_to_3_years",
        entry_trigger="婚育或家庭改善",
        housing_path="园区公寓或租住后进入改善购房",
        behavior_changes=("缩短总部通勤", "增加双人办公需求"),
        product_implications=("验证总价上限", "验证家庭成长空间"),
        exit_conditions=("总价不可承受", "教育条件不匹配"),
        evidence_refs=("e-penguin-operation", "e-project-brief"),
        prohibited_uses=prohibited_uses,
        evidence_type=evidence_type,
    )


def test_local_prior_without_seed_stays_baseline_only() -> None:
    assert prior(with_seed=False).supports_household_synthesis is False
    assert prior(with_seed=True).supports_household_synthesis is True


def test_choice_model_requires_negative_price_coefficient() -> None:
    with pytest.raises(ValueError, match="negative"):
        ChoiceModelArtifact(
            model_id="invalid",
            city="武汉",
            as_of="2026-07-23",
            version="1",
            segment_feature_coefficients={"segment": {"price": 0.01}},
            alternative_intercepts={},
            outside_option_intercept=0,
            price_feature="price",
            feature_units={"price": "CNY 10k"},
            availability_rules={"outside_option": True},
            validation_metrics={},
            evidence_refs=("e1",),
            artifact_hash="sha256:model",
        )
    assert choice_model().wtp_wan("first-home", "commute") == pytest.approx(26.6666667)


def test_c1_bundle_rejects_choice_claims() -> None:
    with pytest.raises(ValueError, match="C1"):
        CustomerIntelligenceBundle(
            bundle_id="invalid-c1",
            city="武汉",
            district="汉阳区",
            as_of="2026-07-23",
            version="1",
            evidence_level=CustomerEvidenceLevel.LOCAL_BASELINE,
            local_population=prior(),
            segments=segments(),
            synthetic_cohort=cohort(),
            choice_simulation={"product_choice_shares": {"A": 0.5}},
            evidence_refs=("e-population",),
            artifact_hashes=("sha256:bundle",),
            rights_status="authorized_aggregate",
            allowed_uses=("customer_hypothesis",),
            prohibited_uses=(
                "direct_monthly_sales_from_choice_share",
                "raw_personal_data_export",
            ),
        )


def test_c2_bundle_is_aggregated_and_serializable() -> None:
    bundle = CustomerIntelligenceBundle(
        bundle_id="wuhan-c2",
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="1",
        evidence_level=CustomerEvidenceLevel.CHOICE_CALIBRATED,
        local_population=prior(),
        segments=segments(),
        synthetic_cohort=cohort(),
        choice_simulation={
            "model_id": choice_model().model_id,
            "parameter_version": choice_model().version,
            "seed": 42,
            "product_choice_shares": {"A": 0.42, "B": 0.31},
            "exit_share": 0.27,
            "affordable_budget_median_wan": 238.0,
        },
        persona_experiment=PersonaExperimentResult(
            experiment_id="persona-1",
            city="武汉",
            as_of="2026-07-23",
            version="1",
            cohort_ref=cohort().cohort_id,
            seed=42,
            prompt_template_hash="sha256:prompt",
            aggregate_results={
                "first-home": {
                    "choice_reasons": ["通勤"],
                    "objections": ["首付"],
                }
            },
            evidence_refs=("e-segment-first",),
            artifact_hash="sha256:persona",
        ),
        evidence_refs=("e-population", "e-choice-survey"),
        artifact_hashes=("sha256:bundle", "sha256:choice"),
        rights_status="authorized_deidentified",
        allowed_uses=("scheme_comparison", "product_response"),
        prohibited_uses=(
            "direct_monthly_sales_from_choice_share",
            "raw_personal_data_export",
        ),
    )
    payload = bundle.to_dict()
    assert payload["evidence_level"] == 2
    assert payload["synthetic_cohort"]["seed"] == 42
    assert "transcripts" not in str(payload).lower()
    assert customer_intelligence_from_mapping(payload) == bundle


def test_future_customer_scenario_stays_simulation_and_forbids_headcount_fact() -> None:
    with pytest.raises(ValueError, match="model_simulation"):
        future_customer_scenario(evidence_type="analysis_inference")
    with pytest.raises(ValueError, match="headcount or conversion rate"):
        future_customer_scenario(prohibited_uses=("direct_sales_forecast",))


def test_future_customer_scenario_requires_a_known_demand_event() -> None:
    with pytest.raises(ValueError, match="unknown demand events"):
        CustomerIntelligenceBundle(
            bundle_id="future-customer-unknown-event",
            city="武汉",
            district="汉阳区",
            as_of="2026-07-23",
            version="1",
            evidence_level=CustomerEvidenceLevel.LOCAL_BASELINE,
            local_population=prior(),
            segments=segments(),
            future_demand_scan_status="material_events_found",
            demand_driver_events=(demand_driver_event(),),
            future_customer_scenarios=(
                future_customer_scenario(event_ids=("missing-event",)),
            ),
            evidence_refs=("e-population",),
            artifact_hashes=("sha256:bundle",),
            rights_status="authorized_aggregate",
            allowed_uses=("customer_hypothesis",),
            prohibited_uses=(
                "direct_monthly_sales_from_choice_share",
                "raw_personal_data_export",
            ),
        )


def test_future_customer_event_and_scenario_round_trip_with_explicit_boundaries() -> None:
    bundle = CustomerIntelligenceBundle(
        bundle_id="future-customer-bundle",
        city="武汉",
        district="汉阳区",
        as_of="2026-07-23",
        version="1",
        evidence_level=CustomerEvidenceLevel.LOCAL_BASELINE,
        local_population=prior(),
        segments=segments(),
        future_demand_scan_status="material_events_found",
        demand_driver_events=(demand_driver_event(),),
        future_customer_scenarios=(future_customer_scenario(),),
        evidence_refs=("e-population", "e-penguin-operation"),
        artifact_hashes=("sha256:bundle",),
        rights_status="authorized_aggregate",
        allowed_uses=("customer_hypothesis",),
        prohibited_uses=(
            "direct_monthly_sales_from_choice_share",
            "raw_personal_data_export",
        ),
    )

    payload = bundle.to_dict()

    assert payload["future_demand_scan_status"] == "material_events_found"
    assert payload["demand_driver_events"][0]["counter_factors"]
    assert (
        payload["future_customer_scenarios"][0]["evidence_type"]
        == "model_simulation"
    )
    assert "headcount_or_conversion_rate_as_fact" in (
        payload["future_customer_scenarios"][0]["prohibited_uses"]
    )
    assert customer_intelligence_from_mapping(payload) == bundle


def test_raw_transcript_is_rejected() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        PersonaExperimentResult(
            experiment_id="persona-raw",
            city="武汉",
            as_of="2026-07-23",
            version="1",
            cohort_ref="cohort",
            seed=42,
            prompt_template_hash="sha256:prompt",
            aggregate_results={"transcripts": ["raw customer text"]},
            evidence_refs=("e1",),
            artifact_hash="sha256:persona",
        )
