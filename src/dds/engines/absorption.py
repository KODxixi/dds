"""Deterministic Input 1 standard-100-unit operating simulations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence


_QUANT = Decimal("0.0001")


def _decimal(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class StandardMarketParameters:
    city: str
    version: str
    base_monthly_demand_per_100: Decimal | float | str
    tail_decay_rate: Decimal | float | str
    supporting_evidence_refs: tuple[str, ...]
    calibration_method: str

    def __post_init__(self) -> None:
        demand = _decimal(self.base_monthly_demand_per_100)
        decay = _decimal(self.tail_decay_rate)
        if not self.city.strip() or not self.version.strip():
            raise ValueError("city and parameter version are required")
        if demand <= 0:
            raise ValueError("base monthly demand must be positive")
        if not Decimal("0") <= decay < Decimal("1"):
            raise ValueError("tail_decay_rate must be within [0, 1)")
        if not self.supporting_evidence_refs:
            raise ValueError("market parameters require supporting evidence")
        if not self.calibration_method.strip():
            raise ValueError("calibration_method is required")
        if self.calibration_method.strip().lower() == "abm_choice_share":
            raise ValueError("ABM choice share cannot be converted directly to monthly sales")


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    strategy_id: str
    positioning: str
    demand_multiplier: Decimal | float | str
    list_price_index: Decimal | float | str
    marketing_cost_index: Decimal | float | str
    value_investment_index: Decimal | float | str
    switch_triggers: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.positioning.strip():
            raise ValueError("strategy_id and positioning are required")
        if _decimal(self.demand_multiplier) <= 0:
            raise ValueError("demand_multiplier must be positive")
        if _decimal(self.list_price_index) <= 0:
            raise ValueError("list_price_index must be positive")
        if _decimal(self.marketing_cost_index) < 0:
            raise ValueError("marketing_cost_index must be non-negative")
        if _decimal(self.value_investment_index) < 0:
            raise ValueError("value_investment_index must be non-negative")
        if not self.switch_triggers:
            raise ValueError("at least one switch trigger is required")


@dataclass(frozen=True, slots=True)
class MarketScenario:
    scenario_id: str
    demand_multiplier: Decimal | float | str
    price_realization_rate: Decimal | float | str
    cancellation_rate: Decimal | float | str

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError("scenario_id is required")
        if _decimal(self.demand_multiplier) <= 0:
            raise ValueError("scenario demand_multiplier must be positive")
        if not Decimal("0") < _decimal(self.price_realization_rate) <= Decimal("1.5"):
            raise ValueError("price_realization_rate must be within (0, 1.5]")
        if not Decimal("0") <= _decimal(self.cancellation_rate) < Decimal("1"):
            raise ValueError("cancellation_rate must be within [0, 1)")


@dataclass(frozen=True, slots=True)
class SimulationAssumptions:
    total_units: Decimal | float | str = Decimal("100")
    horizon_months: int = 48
    monthly_discount_rate: Decimal | float | str = Decimal("0.006")
    collection_rate: Decimal | float | str = Decimal("1")
    tail_risk_weight: Decimal | float | str = Decimal("0.25")
    seed: int = 42
    model_version: str = "dds.standard-100-absorption/1.0"

    def __post_init__(self) -> None:
        if _decimal(self.total_units) != Decimal("100"):
            raise ValueError("Input 1 standard simulation must use exactly 100 units")
        if self.horizon_months < 12:
            raise ValueError("horizon_months must be at least 12")
        if _decimal(self.monthly_discount_rate) < 0:
            raise ValueError("monthly_discount_rate must be non-negative")
        if not Decimal("0") < _decimal(self.collection_rate) <= Decimal("1"):
            raise ValueError("collection_rate must be within (0, 1]")
        if _decimal(self.tail_risk_weight) < 0:
            raise ValueError("tail_risk_weight must be non-negative")
        if not self.model_version.strip():
            raise ValueError("model_version is required")


@dataclass(frozen=True, slots=True)
class MonthlyResult:
    month: int
    opening_inventory: Decimal
    gross_demand: Decimal
    net_sales: Decimal
    closing_inventory: Decimal
    cumulative_sales: Decimal
    cumulative_absorption_rate: Decimal
    realized_value_index: Decimal
    cash_collection_index: Decimal
    discounted_cash_index: Decimal


@dataclass(frozen=True, slots=True)
class StrategyScenarioResult:
    strategy_id: str
    scenario_id: str
    parameter_version: str
    model_version: str
    seed: int
    months: tuple[MonthlyResult, ...]
    absorption_3m: Decimal
    absorption_6m: Decimal
    absorption_12m: Decimal
    clearance_month: int | None
    realized_value_index: Decimal
    cash_collection_index: Decimal
    cash_npv_index: Decimal
    ending_inventory: Decimal
    tail_risk_index: Decimal
    risk_adjusted_npv_index: Decimal
    supporting_evidence_refs: tuple[str, ...]
    formula: str
    evidence_type: str = "model_simulation"
    monetary_unit: str = "relative_index_base_100"


@dataclass(frozen=True, slots=True)
class OperatingSimulation:
    results: tuple[StrategyScenarioResult, ...]
    pareto_strategy_ids: tuple[str, ...]
    recommended_strategy_id: str | None
    recommendation_rule: str


class UnsupportedMarketParametersError(ValueError):
    pass


class StandardAbsorptionEngine:
    """Run standard 100-unit strategies without inventing project scale."""

    def __init__(self, profiles: Sequence[StandardMarketParameters]) -> None:
        self._profiles = {item.city: item for item in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("market parameter cities must be unique")

    def simulate(
        self,
        city: str,
        strategy: StrategyCandidate,
        scenario: MarketScenario,
        *,
        assumptions: SimulationAssumptions | None = None,
    ) -> StrategyScenarioResult:
        profile = self._profiles.get(city)
        if profile is None:
            raise UnsupportedMarketParametersError(
                f"standard absorption parameters are not registered for {city}"
            )
        config = assumptions or SimulationAssumptions()
        total_units = _decimal(config.total_units)
        remaining = total_units
        cumulative_sales = Decimal("0")
        cumulative_value = Decimal("0")
        cumulative_cash = Decimal("0")
        cumulative_npv = Decimal("0")
        clearance_month: int | None = None
        months: list[MonthlyResult] = []
        demand_base = (
            _decimal(profile.base_monthly_demand_per_100)
            * _decimal(strategy.demand_multiplier)
            * _decimal(scenario.demand_multiplier)
        )
        net_rate = Decimal("1") - _decimal(scenario.cancellation_rate)
        realized_price_index = (
            _decimal(strategy.list_price_index)
            * _decimal(scenario.price_realization_rate)
        )
        discount_rate = _decimal(config.monthly_discount_rate)
        collection_rate = _decimal(config.collection_rate)

        for month in range(1, config.horizon_months + 1):
            opening = remaining
            sold_fraction = cumulative_sales / total_units
            tail_factor = max(
                Decimal("0.2"),
                Decimal("1") - _decimal(profile.tail_decay_rate) * sold_fraction,
            )
            gross_demand = _quantize(demand_base * tail_factor)
            net_sales = _quantize(min(opening, gross_demand * net_rate))
            remaining = _quantize(max(Decimal("0"), opening - net_sales))
            cumulative_sales = _quantize(cumulative_sales + net_sales)
            value = net_sales * realized_price_index / Decimal("100")
            cash = value * collection_rate
            discounted = cash / ((Decimal("1") + discount_rate) ** month)
            cumulative_value += value
            cumulative_cash += cash
            cumulative_npv += discounted
            months.append(
                MonthlyResult(
                    month=month,
                    opening_inventory=_quantize(opening),
                    gross_demand=_quantize(gross_demand),
                    net_sales=_quantize(net_sales),
                    closing_inventory=_quantize(remaining),
                    cumulative_sales=_quantize(cumulative_sales),
                    cumulative_absorption_rate=_quantize(
                        cumulative_sales / total_units
                    ),
                    realized_value_index=_quantize(cumulative_value),
                    cash_collection_index=_quantize(cumulative_cash),
                    discounted_cash_index=_quantize(cumulative_npv),
                )
            )
            if remaining == 0 and clearance_month is None:
                clearance_month = month

        ending_inventory = max(Decimal("0"), total_units - cumulative_sales)
        tail_risk = (
            ending_inventory
            * realized_price_index
            / Decimal("100")
            * _decimal(config.tail_risk_weight)
        )
        risk_adjusted = (
            cumulative_npv
            - _decimal(strategy.marketing_cost_index)
            - _decimal(strategy.value_investment_index)
            - tail_risk
        )

        def absorption_at(month: int) -> Decimal:
            return months[min(month, len(months)) - 1].cumulative_absorption_rate

        return StrategyScenarioResult(
            strategy_id=strategy.strategy_id,
            scenario_id=scenario.scenario_id,
            parameter_version=profile.version,
            model_version=config.model_version,
            seed=config.seed,
            months=tuple(months),
            absorption_3m=absorption_at(3),
            absorption_6m=absorption_at(6),
            absorption_12m=absorption_at(12),
            clearance_month=clearance_month,
            realized_value_index=_quantize(cumulative_value),
            cash_collection_index=_quantize(cumulative_cash),
            cash_npv_index=_quantize(cumulative_npv),
            ending_inventory=_quantize(ending_inventory),
            tail_risk_index=_quantize(tail_risk),
            risk_adjusted_npv_index=_quantize(risk_adjusted),
            supporting_evidence_refs=profile.supporting_evidence_refs,
            formula=(
                "net_sales=min(opening_inventory, calibrated_demand"
                "*(1-cancellation_rate)); closing=opening-net_sales; "
                "value_index=sum(net_sales*realized_price_index/100); "
                "risk_adjusted_npv=npv-marketing-value_investment-tail_risk"
            ),
        )

    def run_operating_lab(
        self,
        city: str,
        strategies: Sequence[StrategyCandidate],
        scenarios: Sequence[MarketScenario],
        *,
        assumptions: SimulationAssumptions | None = None,
        base_scenario_id: str = "base",
    ) -> OperatingSimulation:
        if len(strategies) < 3:
            raise ValueError("Input 1 operating lab requires at least three strategies")
        if len(scenarios) < 3:
            raise ValueError("Input 1 operating lab requires at least three scenarios")
        if len({item.strategy_id for item in strategies}) != len(strategies):
            raise ValueError("strategy_id values must be unique")
        if len({item.scenario_id for item in scenarios}) != len(scenarios):
            raise ValueError("scenario_id values must be unique")
        results = tuple(
            self.simulate(
                city,
                strategy,
                scenario,
                assumptions=assumptions,
            )
            for strategy in strategies
            for scenario in scenarios
        )
        base_results = [
            item for item in results if item.scenario_id == base_scenario_id
        ]
        if len(base_results) != len(strategies):
            raise ValueError("exactly one base scenario result per strategy is required")
        frontier = pareto_front(base_results)
        recommended = max(
            frontier,
            key=lambda item: (
                item.risk_adjusted_npv_index,
                item.absorption_12m,
                item.realized_value_index,
                item.strategy_id,
            ),
            default=None,
        )
        return OperatingSimulation(
            results=results,
            pareto_strategy_ids=tuple(item.strategy_id for item in frontier),
            recommended_strategy_id=(
                recommended.strategy_id if recommended is not None else None
            ),
            recommendation_rule=(
                "Among base-scenario Pareto strategies, maximize risk-adjusted "
                "cash NPV index; break ties by 12-month absorption, realized "
                "value, then stable strategy_id."
            ),
        )


def _dominates(
    candidate: StrategyScenarioResult,
    other: StrategyScenarioResult,
) -> bool:
    candidate_values = (
        candidate.absorption_12m,
        candidate.realized_value_index,
        candidate.cash_npv_index,
        -candidate.tail_risk_index,
    )
    other_values = (
        other.absorption_12m,
        other.realized_value_index,
        other.cash_npv_index,
        -other.tail_risk_index,
    )
    return all(left >= right for left, right in zip(candidate_values, other_values)) and any(
        left > right for left, right in zip(candidate_values, other_values)
    )


def pareto_front(
    results: Iterable[StrategyScenarioResult],
) -> tuple[StrategyScenarioResult, ...]:
    items = tuple(results)
    frontier = [
        item
        for item in items
        if not any(_dominates(other, item) for other in items if other is not item)
    ]
    return tuple(sorted(frontier, key=lambda item: item.strategy_id))


__all__ = [
    "MarketScenario",
    "MonthlyResult",
    "OperatingSimulation",
    "SimulationAssumptions",
    "StandardAbsorptionEngine",
    "StandardMarketParameters",
    "StrategyCandidate",
    "StrategyScenarioResult",
    "UnsupportedMarketParametersError",
    "pareto_front",
]
