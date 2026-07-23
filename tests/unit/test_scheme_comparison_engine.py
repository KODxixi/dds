from __future__ import annotations

from decimal import Decimal

import pytest

from dds.engines.scheme_comparison import (
    ComparisonPolicy,
    SchemeCandidate,
    SchemeComparisonEngine,
    SchemeScenarioMetrics,
)


def outcome(
    scenario_id: str,
    probability: str,
    absorption: str,
    value: int,
    npv: int,
    funding: int,
    tail: str,
) -> SchemeScenarioMetrics:
    return SchemeScenarioMetrics(
        scenario_id=scenario_id,
        probability=probability,
        absorption_12m=absorption,
        realized_value_cny=value,
        cash_npv_cny=npv,
        peak_funding_cny=funding,
        tail_inventory_rate=tail,
        evidence_refs=(f"outcome:{scenario_id}",),
        source_model_version="project-cashflow/1.0",
    )


def scheme(
    scheme_id: str,
    *,
    absorption_shift: Decimal = Decimal("0"),
    value_shift: int = 0,
    npv_shift: int = 0,
    funding_shift: int = 0,
    tail_shift: Decimal = Decimal("0"),
    feasible: bool = True,
) -> SchemeCandidate:
    return SchemeCandidate(
        scheme_id=scheme_id,
        planning_efficiency="0.82",
        product_market_fit="0.80",
        implementation_complexity="0.45",
        hard_constraints_passed=feasible,
        hard_constraint_failures=() if feasible else ("height_limit",),
        outcomes=(
            outcome(
                "conservative",
                "0.25",
                str(Decimal("0.45") + absorption_shift),
                180_000_000 + value_shift,
                20_000_000 + npv_shift,
                100_000_000 + funding_shift,
                str(Decimal("0.35") + tail_shift),
            ),
            outcome(
                "base",
                "0.50",
                str(Decimal("0.65") + absorption_shift),
                220_000_000 + value_shift,
                45_000_000 + npv_shift,
                85_000_000 + funding_shift,
                str(Decimal("0.20") + tail_shift),
            ),
            outcome(
                "optimistic",
                "0.25",
                str(Decimal("0.82") + absorption_shift),
                250_000_000 + value_shift,
                70_000_000 + npv_shift,
                70_000_000 + funding_shift,
                str(Decimal("0.08") + tail_shift),
            ),
        ),
        evidence_refs=(f"scheme:{scheme_id}",),
    )


def test_requires_two_schemes_and_frozen_probability_mass() -> None:
    with pytest.raises(ValueError, match="at least two"):
        SchemeComparisonEngine().compare((scheme("A"),))
    with pytest.raises(ValueError, match="sum to 1"):
        SchemeCandidate(
            scheme_id="bad",
            planning_efficiency=0.8,
            product_market_fit=0.8,
            implementation_complexity=0.4,
            hard_constraints_passed=True,
            hard_constraint_failures=(),
            outcomes=(outcome("base", "0.8", "0.6", 1, 1, 1, "0.2"),),
            evidence_refs=("scheme:bad",),
        )


def test_dominated_scheme_is_not_recommended() -> None:
    stronger = scheme(
        "A",
        absorption_shift=Decimal("0.05"),
        value_shift=10_000_000,
        npv_shift=8_000_000,
        funding_shift=-5_000_000,
        tail_shift=Decimal("-0.03"),
    )
    weaker = scheme("B")
    result = SchemeComparisonEngine().compare((weaker, stronger))

    assert result.pareto_scheme_ids == ("A",)
    assert result.recommended_scheme_id == "A"
    assert ("B", "pareto_dominated_by:A") in result.eliminated_reasons


def test_all_infeasible_schemes_return_no_recommendation() -> None:
    result = SchemeComparisonEngine().compare(
        (scheme("A", feasible=False), scheme("B", feasible=False))
    )

    assert result.pareto_scheme_ids == ()
    assert result.recommended_scheme_id is None
    assert result.no_recommendation_reason == (
        "all_schemes_failed_hard_constraints"
    )


def test_cvar_policy_is_deterministic_and_changes_risk_adjusted_value() -> None:
    schemes = (scheme("A"), scheme("B", npv_shift=1_000_000))
    policy = ComparisonPolicy(cvar_alpha="0.25", risk_aversion_lambda="0.7")
    first = SchemeComparisonEngine().compare(schemes, policy=policy)
    second = SchemeComparisonEngine().compare(schemes, policy=policy)

    assert first == second
    for item in first.metrics:
        assert item.cvar_cash_npv_cny <= item.expected_cash_npv_cny
        assert item.risk_adjusted_npv_cny <= item.expected_cash_npv_cny
    assert dict(policy.objective_directions)["peak_funding_cny"] == "minimize"
