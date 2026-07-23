"""Unified structured entry point for Input 1/2/3 deterministic engines."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from dds.analysis_profile import resolve_intervention_profile
from dds.customer import (
    CustomerIntelligenceBundle,
    customer_intelligence_from_mapping,
)
from dds.engines.absorption import (
    MarketScenario,
    SimulationAssumptions,
    StandardAbsorptionEngine,
    StandardMarketParameters,
    StrategyCandidate,
)
from dds.engines.project_cashflow import (
    ProductBatch,
    ProjectCashFlowEngine,
    ProjectCostParameters,
    ProjectDemandParameters,
    ProjectSimulationAssumptions,
)
from dds.engines.scheme_comparison import (
    ComparisonPolicy,
    SchemeCandidate,
    SchemeComparisonEngine,
    SchemeScenarioMetrics,
)


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    analysis_profile: dict[str, Any]
    simulation_kind: str
    result: dict[str, Any]
    decision_scope: str
    customer_intelligence: dict[str, Any] | None = None
    delivery_ready: bool = False
    evidence_type: str = "model_simulation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _sequence(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")
    return [_mapping(item, field) for item in value]


class ScenarioService:
    """Dispatch only to the engine selected and confirmed by the user."""

    def run(
        self,
        *,
        selected_mode: int,
        mode_confirmed: bool,
        input_profile: Mapping[str, Any],
        payload: Mapping[str, Any],
        customer_intelligence: (
            Mapping[str, Any] | CustomerIntelligenceBundle | None
        ) = None,
    ) -> ScenarioRun:
        profile = resolve_intervention_profile(
            input_profile,
            selected_mode=selected_mode,
            confirmed=mode_confirmed,
        )
        if not profile["eligible"]:
            raise ValueError(
                "intervention is not runnable: "
                + ",".join(
                    profile["missing_inputs"] or ["user_confirmation_required"]
                )
            )
        customer_bundle = (
            customer_intelligence_from_mapping(customer_intelligence)
            if customer_intelligence is not None
            else None
        )
        input_city = str(input_profile.get("city") or "").strip()
        if (
            customer_bundle is not None
            and input_city
            and customer_bundle.city != input_city
        ):
            raise ValueError("customer intelligence city must match input_profile.city")
        raw = dict(payload)
        if selected_mode == 1:
            result = self._run_input_1(raw)
            kind = "standard_100_operating_lab"
        elif selected_mode == 2:
            result = self._run_input_2(raw)
            kind = "project_inventory_cashflow"
        elif selected_mode == 3:
            result = self._run_input_3(raw)
            kind = "scheme_risk_pareto"
        else:
            raise ValueError("selected_mode must be 1, 2, or 3")
        return ScenarioRun(
            analysis_profile=profile,
            simulation_kind=kind,
            result=asdict(result),
            decision_scope=profile["decision_scope"],
            customer_intelligence=(
                customer_bundle.to_dict() if customer_bundle is not None else None
            ),
        )

    def _run_input_1(self, payload: dict[str, Any]):
        market = StandardMarketParameters(
            **_mapping(payload.get("market_parameters"), "market_parameters")
        )
        strategies = tuple(
            StrategyCandidate(**item)
            for item in _sequence(payload.get("strategies"), "strategies")
        )
        scenarios = tuple(
            MarketScenario(**item)
            for item in _sequence(payload.get("scenarios"), "scenarios")
        )
        assumptions_raw = payload.get("assumptions")
        assumptions = (
            SimulationAssumptions(**_mapping(assumptions_raw, "assumptions"))
            if assumptions_raw is not None
            else None
        )
        return StandardAbsorptionEngine((market,)).run_operating_lab(
            market.city,
            strategies,
            scenarios,
            assumptions=assumptions,
        )

    def _run_input_2(self, payload: dict[str, Any]):
        batches = tuple(
            ProductBatch(**item)
            for item in _sequence(payload.get("batches"), "batches")
        )
        demand = ProjectDemandParameters(
            **_mapping(payload.get("demand"), "demand")
        )
        costs = ProjectCostParameters(
            **_mapping(payload.get("costs"), "costs")
        )
        assumptions_raw = payload.get("assumptions")
        assumptions = (
            ProjectSimulationAssumptions(
                **_mapping(assumptions_raw, "assumptions")
            )
            if assumptions_raw is not None
            else None
        )
        return ProjectCashFlowEngine().simulate(
            batches,
            demand,
            costs,
            assumptions=assumptions,
        )

    def _run_input_3(self, payload: dict[str, Any]):
        schemes = []
        for raw_scheme in _sequence(payload.get("schemes"), "schemes"):
            outcomes = tuple(
                SchemeScenarioMetrics(**item)
                for item in _sequence(
                    raw_scheme.pop("outcomes", None),
                    "schemes[].outcomes",
                )
            )
            schemes.append(SchemeCandidate(outcomes=outcomes, **raw_scheme))
        policy_raw = payload.get("policy")
        policy = (
            ComparisonPolicy(**_mapping(policy_raw, "policy"))
            if policy_raw is not None
            else None
        )
        return SchemeComparisonEngine().compare(
            tuple(schemes),
            policy=policy,
        )


__all__ = ["ScenarioRun", "ScenarioService"]
