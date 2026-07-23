from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from dds.customer import ChoiceModelArtifact
from dds.engines.abm import (
    ABMEngine,
    CityModelParameters,
    SegmentParameters,
    SimulatedProduct,
    UnsupportedCityError,
)


def engine():
    profile = CityModelParameters(
        city="测试城",
        version="test-profile/1.0",
        segments=(
            SegmentParameters(
                segment_id="改善",
                weight=1.0,
                annual_income_mean_wan=40,
                annual_income_sigma_wan=8,
                asset_multiple=6,
                down_payment_ratio=0.4,
                preference_weights={"space": 0.8, "landscape": 0.5},
                risk_aversion=0.5,
            ),
        ),
        supporting_evidence_refs=("customer-study:E1",),
    )
    return ABMEngine((profile,))


def products():
    return (
        SimulatedProduct("A", 100, 20_000, {"space": 0.8, "landscape": 0.5}),
        SimulatedProduct("B", 130, 23_000, {"space": 0.9, "landscape": 0.8}),
    )


def test_unknown_city_fails_closed():
    with pytest.raises(UnsupportedCityError):
        engine().simulate("未知城", products())


def test_same_seed_is_stable_and_is_model_simulation():
    first = engine().simulate("测试城", products(), seed=7)
    second = engine().simulate("测试城", products(), seed=7)
    assert first == second
    assert first.evidence_type == "model_simulation"
    assert first.parameter_version == "test-profile/1.0"
    assert first.affordable_budget_median_wan == first.wtp_median_wan
    assert sum(first.product_choice_shares.values()) + first.exit_share == pytest.approx(1)


def test_concurrent_runs_have_isolated_random_generators():
    simulator = engine()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: simulator.simulate("测试城", products(), seed=11),
                range(8),
            )
        )
    assert all(item == results[0] for item in results)


def test_profile_without_evidence_is_rejected():
    with pytest.raises(ValueError, match="evidence"):
        CityModelParameters(
            city="空城",
            version="1",
            segments=engine()._profiles["测试城"].segments,
            supporting_evidence_refs=(),
        )


def test_calibrated_choice_model_supports_signed_coefficients_and_true_wtp():
    choice_model = ChoiceModelArtifact(
        model_id="test-choice",
        city="测试城",
        as_of="2026-07-23",
        version="choice/1.0",
        segment_feature_coefficients={
            "改善": {
                "total_price_wan": -0.02,
                "space": 0.8,
                "landscape": 0.5,
            }
        },
        alternative_intercepts={"A": 0.2, "B": 0.1},
        outside_option_intercept=-0.1,
        price_feature="total_price_wan",
        feature_units={
            "total_price_wan": "CNY 10k",
            "space": "index",
            "landscape": "index",
        },
        availability_rules={"requires_displayed_choice_set": True},
        validation_metrics={"holdout_log_loss": 0.5},
        evidence_refs=("choice-study:E1",),
        artifact_hash="sha256:choice",
    )
    profile = CityModelParameters(
        city="测试城",
        version="test-profile/2.0",
        segments=engine()._profiles["测试城"].segments,
        supporting_evidence_refs=("customer-study:E1",),
        choice_model=choice_model,
    )
    result = ABMEngine((profile,)).simulate("测试城", products(), seed=7)
    assert result.choice_model_version == "choice/1.0"
    assert result.wtp_by_segment_attribute_wan == {
        "改善.space": 40.0,
        "改善.landscape": 25.0,
    }
    assert result.supporting_evidence_refs == (
        "customer-study:E1",
        "choice-study:E1",
    )
