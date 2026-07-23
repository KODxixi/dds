"""Input 3 multi-scheme risk-adjusted Pareto comparison."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from dds.engines.project_cashflow import ProjectCashFlowResult


_QUANT = Decimal("0.0001")
_MONEY = Decimal("0.01")


def _d(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class SchemeScenarioMetrics:
    scenario_id: str
    probability: Decimal | float | str
    absorption_12m: Decimal | float | str
    realized_value_cny: Decimal | float | str
    cash_npv_cny: Decimal | float | str
    peak_funding_cny: Decimal | float | str
    tail_inventory_rate: Decimal | float | str
    evidence_refs: tuple[str, ...]
    source_model_version: str

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or not self.source_model_version.strip():
            raise ValueError("scenario_id and source_model_version are required")
        if not Decimal("0") < _d(self.probability) <= Decimal("1"):
            raise ValueError("scenario probability must be within (0, 1]")
        if not Decimal("0") <= _d(self.absorption_12m) <= Decimal("1"):
            raise ValueError("absorption_12m must be within [0, 1]")
        if _d(self.realized_value_cny) < 0 or _d(self.peak_funding_cny) < 0:
            raise ValueError("value and peak funding must be non-negative")
        if not Decimal("0") <= _d(self.tail_inventory_rate) <= Decimal("1"):
            raise ValueError("tail inventory rate must be within [0, 1]")
        if not self.evidence_refs:
            raise ValueError("scenario metrics require evidence refs")

    @classmethod
    def from_project_result(
        cls,
        *,
        scenario_id: str,
        probability: Decimal | float | str,
        result: ProjectCashFlowResult,
        evidence_refs: tuple[str, ...],
    ) -> "SchemeScenarioMetrics":
        contracts_12m = sum(
            (
                item.contracts
                for item in result.months
                if item.month <= 12
            ),
            Decimal("0"),
        )
        absorption = (
            contracts_12m / result.total_released_units
            if result.total_released_units > 0
            else Decimal("0")
        )
        tail = (
            (
                result.ending_available_inventory
                + result.ending_reserved_inventory
            )
            / result.total_released_units
            if result.total_released_units > 0
            else Decimal("1")
        )
        return cls(
            scenario_id=scenario_id,
            probability=probability,
            absorption_12m=_q(absorption),
            realized_value_cny=result.contracted_sales_value_cny,
            cash_npv_cny=result.net_cash_npv_cny,
            peak_funding_cny=result.peak_funding_requirement_cny,
            tail_inventory_rate=_q(tail),
            evidence_refs=evidence_refs,
            source_model_version=result.model_version,
        )


@dataclass(frozen=True, slots=True)
class SchemeCandidate:
    scheme_id: str
    planning_efficiency: Decimal | float | str
    product_market_fit: Decimal | float | str
    implementation_complexity: Decimal | float | str
    hard_constraints_passed: bool
    hard_constraint_failures: tuple[str, ...]
    outcomes: tuple[SchemeScenarioMetrics, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scheme_id.strip():
            raise ValueError("scheme_id is required")
        for name, value in (
            ("planning_efficiency", self.planning_efficiency),
            ("product_market_fit", self.product_market_fit),
            ("implementation_complexity", self.implementation_complexity),
        ):
            if not Decimal("0") <= _d(value) <= Decimal("1"):
                raise ValueError(f"{name} must be within [0, 1]")
        if not self.outcomes:
            raise ValueError("scheme requires scenario outcomes")
        probability = sum(
            (_d(item.probability) for item in self.outcomes),
            Decimal("0"),
        )
        if probability != Decimal("1"):
            raise ValueError("scheme scenario probabilities must sum to 1")
        if len({item.scenario_id for item in self.outcomes}) != len(
            self.outcomes
        ):
            raise ValueError("scheme scenario_id values must be unique")
        if not self.evidence_refs:
            raise ValueError("scheme requires evidence refs")
        if self.hard_constraints_passed and self.hard_constraint_failures:
            raise ValueError(
                "passed scheme cannot contain hard constraint failures"
            )
        if not self.hard_constraints_passed and not self.hard_constraint_failures:
            raise ValueError(
                "failed scheme must explain hard constraint failures"
            )


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    cvar_alpha: Decimal | float | str = Decimal("0.25")
    risk_aversion_lambda: Decimal | float | str = Decimal("0.5")
    policy_version: str = "dds.scheme-pareto/1.0"
    objective_directions: tuple[tuple[str, str], ...] = (
        ("absorption_12m", "maximize"),
        ("realized_value_cny", "maximize"),
        ("risk_adjusted_npv_cny", "maximize"),
        ("peak_funding_cny", "minimize"),
        ("tail_inventory_rate", "minimize"),
        ("planning_efficiency", "maximize"),
        ("product_market_fit", "maximize"),
        ("implementation_complexity", "minimize"),
    )

    def __post_init__(self) -> None:
        if not Decimal("0") < _d(self.cvar_alpha) <= Decimal("1"):
            raise ValueError("cvar_alpha must be within (0, 1]")
        if _d(self.risk_aversion_lambda) < 0:
            raise ValueError("risk_aversion_lambda must be non-negative")
        if not self.policy_version.strip():
            raise ValueError("policy_version is required")


@dataclass(frozen=True, slots=True)
class SchemeMetrics:
    scheme_id: str
    feasible: bool
    absorption_12m: Decimal
    realized_value_cny: Decimal
    expected_cash_npv_cny: Decimal
    cvar_cash_npv_cny: Decimal
    risk_adjusted_npv_cny: Decimal
    peak_funding_cny: Decimal
    tail_inventory_rate: Decimal
    planning_efficiency: Decimal
    product_market_fit: Decimal
    implementation_complexity: Decimal
    hard_constraint_failures: tuple[str, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SchemeComparisonResult:
    policy: ComparisonPolicy
    metrics: tuple[SchemeMetrics, ...]
    pareto_scheme_ids: tuple[str, ...]
    recommended_scheme_id: str | None
    eliminated_reasons: tuple[tuple[str, str], ...]
    no_recommendation_reason: str
    formula: str


def _weighted(
    outcomes: Sequence[SchemeScenarioMetrics],
    field: str,
) -> Decimal:
    return sum(
        (
            _d(item.probability) * _d(getattr(item, field))
            for item in outcomes
        ),
        Decimal("0"),
    )


def _lower_tail_cvar(
    outcomes: Sequence[SchemeScenarioMetrics],
    alpha: Decimal,
) -> Decimal:
    remaining = alpha
    weighted_tail = Decimal("0")
    for item in sorted(
        outcomes,
        key=lambda value: (
            _d(value.cash_npv_cny),
            value.scenario_id,
        ),
    ):
        take = min(remaining, _d(item.probability))
        weighted_tail += take * _d(item.cash_npv_cny)
        remaining -= take
        if remaining == 0:
            break
    return weighted_tail / alpha


def _dominates(left: SchemeMetrics, right: SchemeMetrics) -> bool:
    left_values = (
        left.absorption_12m,
        left.realized_value_cny,
        left.risk_adjusted_npv_cny,
        -left.peak_funding_cny,
        -left.tail_inventory_rate,
        left.planning_efficiency,
        left.product_market_fit,
        -left.implementation_complexity,
    )
    right_values = (
        right.absorption_12m,
        right.realized_value_cny,
        right.risk_adjusted_npv_cny,
        -right.peak_funding_cny,
        -right.tail_inventory_rate,
        right.planning_efficiency,
        right.product_market_fit,
        -right.implementation_complexity,
    )
    return all(a >= b for a, b in zip(left_values, right_values)) and any(
        a > b for a, b in zip(left_values, right_values)
    )


class SchemeComparisonEngine:
    """Apply frozen hard constraints, CVaR, Pareto, and stable selection."""

    def compare(
        self,
        schemes: Sequence[SchemeCandidate],
        *,
        policy: ComparisonPolicy | None = None,
    ) -> SchemeComparisonResult:
        if len(schemes) < 2:
            raise ValueError("Input 3 comparison requires at least two schemes")
        if len({item.scheme_id for item in schemes}) != len(schemes):
            raise ValueError("scheme_id values must be unique")
        config = policy or ComparisonPolicy()
        alpha = _d(config.cvar_alpha)
        risk_lambda = _d(config.risk_aversion_lambda)
        metrics = []
        for scheme in schemes:
            expected_npv = _weighted(scheme.outcomes, "cash_npv_cny")
            cvar = _lower_tail_cvar(scheme.outcomes, alpha)
            downside = max(Decimal("0"), expected_npv - cvar)
            metrics.append(
                SchemeMetrics(
                    scheme_id=scheme.scheme_id,
                    feasible=scheme.hard_constraints_passed,
                    absorption_12m=_q(
                        _weighted(scheme.outcomes, "absorption_12m")
                    ),
                    realized_value_cny=_money(
                        _weighted(scheme.outcomes, "realized_value_cny")
                    ),
                    expected_cash_npv_cny=_money(expected_npv),
                    cvar_cash_npv_cny=_money(cvar),
                    risk_adjusted_npv_cny=_money(
                        expected_npv - risk_lambda * downside
                    ),
                    peak_funding_cny=_money(
                        _weighted(scheme.outcomes, "peak_funding_cny")
                    ),
                    tail_inventory_rate=_q(
                        _weighted(scheme.outcomes, "tail_inventory_rate")
                    ),
                    planning_efficiency=_q(
                        _d(scheme.planning_efficiency)
                    ),
                    product_market_fit=_q(
                        _d(scheme.product_market_fit)
                    ),
                    implementation_complexity=_q(
                        _d(scheme.implementation_complexity)
                    ),
                    hard_constraint_failures=scheme.hard_constraint_failures,
                    evidence_refs=tuple(
                        dict.fromkeys(
                            [
                                *scheme.evidence_refs,
                                *(
                                    ref
                                    for outcome in scheme.outcomes
                                    for ref in outcome.evidence_refs
                                ),
                            ]
                        )
                    ),
                )
            )
        feasible = [item for item in metrics if item.feasible]
        frontier = [
            item
            for item in feasible
            if not any(
                _dominates(other, item)
                for other in feasible
                if other is not item
            )
        ]
        frontier.sort(key=lambda item: item.scheme_id)
        recommended = max(
            frontier,
            key=lambda item: (
                item.risk_adjusted_npv_cny,
                item.absorption_12m,
                item.realized_value_cny,
                -item.peak_funding_cny,
                item.scheme_id,
            ),
            default=None,
        )
        eliminated = []
        for item in metrics:
            if not item.feasible:
                eliminated.append(
                    (
                        item.scheme_id,
                        "hard_constraints_failed:"
                        + ",".join(item.hard_constraint_failures),
                    )
                )
            elif item not in frontier:
                dominators = sorted(
                    other.scheme_id
                    for other in feasible
                    if other is not item and _dominates(other, item)
                )
                eliminated.append(
                    (
                        item.scheme_id,
                        "pareto_dominated_by:" + ",".join(dominators),
                    )
                )
        return SchemeComparisonResult(
            policy=config,
            metrics=tuple(sorted(metrics, key=lambda item: item.scheme_id)),
            pareto_scheme_ids=tuple(item.scheme_id for item in frontier),
            recommended_scheme_id=(
                recommended.scheme_id if recommended is not None else None
            ),
            eliminated_reasons=tuple(eliminated),
            no_recommendation_reason=(
                ""
                if recommended is not None
                else "all_schemes_failed_hard_constraints"
            ),
            formula=(
                "expected=sum(probability*outcome); "
                "cvar=lower-tail weighted NPV at alpha; "
                "risk_adjusted_npv=expected-lambda*(expected-cvar); "
                "Pareto directions are frozen in policy; infeasible schemes "
                "are excluded before dominance and recommendation"
            ),
        )


__all__ = [
    "ComparisonPolicy",
    "SchemeCandidate",
    "SchemeComparisonEngine",
    "SchemeComparisonResult",
    "SchemeMetrics",
    "SchemeScenarioMetrics",
]
