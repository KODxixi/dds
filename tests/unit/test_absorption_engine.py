from __future__ import annotations

from decimal import Decimal

import pytest

from dds.engines.absorption import (
    MarketScenario,
    SimulationAssumptions,
    StandardAbsorptionEngine,
    StandardMarketParameters,
    StrategyCandidate,
    UnsupportedMarketParametersError,
)


def engine() -> StandardAbsorptionEngine:
    return StandardAbsorptionEngine(
        (
            StandardMarketParameters(
                city="Test City",
                version="test-market/1.0",
                base_monthly_demand_per_100="6",
                tail_decay_rate="0.35",
                supporting_evidence_refs=("market:E1",),
                calibration_method="observed 180-day comparable absorption",
            ),
        )
    )


def strategies() -> tuple[StrategyCandidate, ...]:
    return (
        StrategyCandidate(
            "fast",
            "fast turnover",
            "1.30",
            "94",
            "1.5",
            "0.5",
            ("3m absorption below 18%",),
        ),
        StrategyCandidate(
            "balanced",
            "balanced improvement",
            "1.00",
            "104",
            "1.0",
            "1.0",
            ("6m absorption below 30%",),
        ),
        StrategyCandidate(
            "premium",
            "premium landmark",
            "0.78",
            "122",
            "1.2",
            "2.0",
            ("qualified visits below threshold",),
        ),
    )


def scenarios() -> tuple[MarketScenario, ...]:
    return (
        MarketScenario("conservative", "0.72", "0.96", "0.08"),
        MarketScenario("base", "1.00", "1.00", "0.04"),
        MarketScenario("optimistic", "1.25", "1.02", "0.02"),
    )


def test_unknown_city_and_direct_abm_conversion_fail_closed() -> None:
    with pytest.raises(UnsupportedMarketParametersError):
        engine().simulate("Unknown", strategies()[0], scenarios()[1])
    with pytest.raises(ValueError, match="choice share"):
        StandardMarketParameters(
            city="Bad",
            version="1",
            base_monthly_demand_per_100=6,
            tail_decay_rate=0.2,
            supporting_evidence_refs=("ABM:E1",),
            calibration_method="abm_choice_share",
        )


def test_nine_curves_are_deterministic_and_inventory_reconciles() -> None:
    first = engine().run_operating_lab(
        "Test City",
        strategies(),
        scenarios(),
    )
    second = engine().run_operating_lab(
        "Test City",
        strategies(),
        scenarios(),
    )

    assert first == second
    assert len(first.results) == 9
    for result in first.results:
        previous_sales = Decimal("0")
        for month in result.months:
            assert Decimal("0") <= month.net_sales <= month.opening_inventory
            assert month.closing_inventory >= 0
            assert month.cumulative_sales >= previous_sales
            assert month.cumulative_sales <= 100
            assert (
                month.opening_inventory - month.net_sales
            ).quantize(Decimal("0.0001")) == month.closing_inventory
            assert month.cash_collection_index >= 0
            previous_sales = month.cumulative_sales
        assert result.ending_inventory + result.months[-1].cumulative_sales == 100
        assert result.monetary_unit == "relative_index_base_100"


def test_ordered_market_demand_has_monotonic_absorption() -> None:
    lab = engine().run_operating_lab("Test City", strategies(), scenarios())
    for strategy in strategies():
        by_scenario = {
            item.scenario_id: item
            for item in lab.results
            if item.strategy_id == strategy.strategy_id
        }
        assert (
            by_scenario["conservative"].absorption_12m
            <= by_scenario["base"].absorption_12m
            <= by_scenario["optimistic"].absorption_12m
        )


def test_fastest_absorption_need_not_be_recommended() -> None:
    lab = engine().run_operating_lab("Test City", strategies(), scenarios())
    base = [item for item in lab.results if item.scenario_id == "base"]
    fastest = max(base, key=lambda item: item.absorption_12m)

    assert fastest.strategy_id == "fast"
    assert lab.recommended_strategy_id != fastest.strategy_id
    assert lab.recommended_strategy_id in lab.pareto_strategy_ids


def test_standard_inventory_and_minimum_matrix_are_hard_constraints() -> None:
    with pytest.raises(ValueError, match="exactly 100"):
        SimulationAssumptions(total_units=200)
    with pytest.raises(ValueError, match="at least three strategies"):
        engine().run_operating_lab("Test City", strategies()[:2], scenarios())
