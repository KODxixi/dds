"""User-confirmed DDS intervention modes and independent data readiness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping

from dds.contracts import VALID_SECTION_IDS


class InterventionMode(IntEnum):
    INPUT_1 = 1
    INPUT_2 = 2
    INPUT_3 = 3


@dataclass(frozen=True)
class InterventionDefinition:
    mode: InterventionMode
    display_name: str
    decision_scope: str
    required_units: tuple[str, ...]
    optional_units: tuple[str, ...]
    excluded_units: tuple[str, ...]
    quantitative_authority: tuple[str, ...]

    @property
    def unit_policy(self) -> dict[str, str]:
        policy = {unit: "excluded" for unit in VALID_SECTION_IDS}
        policy.update({unit: "optional" for unit in self.optional_units})
        policy.update({unit: "required" for unit in self.required_units})
        return policy


INTERVENTION_REGISTRY: dict[InterventionMode, InterventionDefinition] = {
    InterventionMode.INPUT_1: InterventionDefinition(
        mode=InterventionMode.INPUT_1,
        display_name="Input 1 · 独立研判",
        decision_scope="independent_opportunity_research",
        required_units=("SC1", "SC2", "AD1", "AD2", "AD3", "AD4", "VA2", "VA3", "CS"),
        optional_units=("SC3", "AD5", "VA1"),
        excluded_units=(),
        quantitative_authority=(
            "standard_100_unit_absorption",
            "relative_price_index",
            "relative_value_index",
            "relative_npv_index",
        ),
    ),
    InterventionMode.INPUT_2: InterventionDefinition(
        mode=InterventionMode.INPUT_2,
        display_name="Input 2 · 约束协同",
        decision_scope="constraint_collaboration",
        required_units=("SC1", "SC2", "SC3", "AD1", "AD2", "AD3", "AD4", "VA2", "VA3", "CS"),
        optional_units=("AD5", "VA1"),
        excluded_units=(),
        quantitative_authority=(
            "sourced_project_capacity",
            "project_absorption_scenarios",
            "preliminary_cash_flow",
        ),
    ),
    InterventionMode.INPUT_3: InterventionDefinition(
        mode=InterventionMode.INPUT_3,
        display_name="Input 3 · 方案审查",
        decision_scope="scheme_review",
        required_units=("SC1", "SC2", "SC3", "AD1", "AD2", "AD3", "AD4", "VA1", "VA2", "VA3", "CS"),
        optional_units=("AD5",),
        excluded_units=(),
        quantitative_authority=(
            "scheme_normalized_value",
            "project_cash_flow",
            "risk_adjusted_npv",
            "pareto_strategy_selection",
        ),
    ),
}


def _mode(value: Any) -> InterventionMode | None:
    if isinstance(value, InterventionMode):
        return value
    text = str(value or "").strip().lower().replace("input", "").replace("_", " ")
    if not text:
        return None
    try:
        return InterventionMode(int(text.strip()))
    except (TypeError, ValueError) as exc:
        raise ValueError("selected_mode must be 1, 2, or 3") from exc


def recommend_intervention_mode(
    input_profile: Mapping[str, Any] | None,
) -> tuple[InterventionMode, list[str]]:
    """Recommend a mode from supplied material without selecting it."""

    raw = dict(input_profile or {})
    schemes = raw.get("schemes") or raw.get("scheme_inputs") or []
    if isinstance(schemes, (list, tuple)) and len(schemes) >= 2:
        return InterventionMode.INPUT_3, ["two_or_more_schemes_supplied"]
    materials = raw.get("materials") or raw.get("available_materials") or []
    material_names = [
        str(item.get("filename") if isinstance(item, Mapping) else item).lower()
        for item in materials
    ]
    constraints = raw.get("constraint_sources") or raw.get("constraints") or []
    if isinstance(constraints, (list, tuple)) and constraints:
        return InterventionMode.INPUT_2, ["constraint_material_supplied"]
    if any(
        any(
            keyword in name
            for keyword in (
                "规划",
                "任务书",
                "成本",
                "条件",
                "招标",
                "审查",
                "红线",
                "方案",
                "总图",
                "强排",
                "户配",
            )
        )
        for name in material_names
    ):
        return InterventionMode.INPUT_2, [
            "project_material_detected_requires_constraint_collaboration"
        ]
    return InterventionMode.INPUT_1, ["site_or_project_question_supplied"]


def _has_location(raw: Mapping[str, Any]) -> bool:
    return bool(
        raw.get("address")
        or (
            (raw.get("latitude") is not None or raw.get("lat") is not None)
            and (raw.get("longitude") is not None or raw.get("lng") is not None)
        )
    )


def _data_readiness(raw: Mapping[str, Any]) -> dict[str, str]:
    constraints = raw.get("constraint_sources") or raw.get("constraints") or []
    schemes = raw.get("schemes") or raw.get("scheme_inputs") or []
    materials = raw.get("materials") or raw.get("available_materials") or []
    future_demand = (
        raw.get("future_demand_sources")
        or raw.get("future_demand_events")
        or []
    )
    return {
        "location": "ready" if _has_location(raw) else "missing",
        "constraints": "ready" if constraints else "missing",
        "schemes": (
            "ready"
            if isinstance(schemes, (list, tuple)) and len(schemes) >= 2
            else "missing"
        ),
        "project_materials": "ready" if materials else "missing",
        "market_evidence": "not_assessed",
        "customer_evidence": "not_assessed",
        "future_demand_evidence": "ready" if future_demand else "not_assessed",
        "cost_and_finance": "not_assessed",
    }


def resolve_intervention_profile(
    input_profile: Mapping[str, Any] | None = None,
    *,
    selected_mode: InterventionMode | int | str | None = None,
    confirmed: bool | None = None,
) -> dict[str, Any]:
    """Resolve a recommendation and preserve the user's confirmed selection."""

    raw = dict(input_profile or {})
    recommended, reasons = recommend_intervention_mode(raw)
    selected = _mode(
        selected_mode if selected_mode is not None else raw.get("selected_mode")
    )
    is_confirmed = bool(
        confirmed if confirmed is not None else raw.get("mode_confirmed", False)
    )
    readiness = _data_readiness(raw)
    missing: list[str] = []
    if selected is not None and is_confirmed:
        if readiness["location"] != "ready":
            missing.append("address_or_coordinates")
        if selected is InterventionMode.INPUT_2 and readiness["constraints"] != "ready":
            missing.append("constraint_material")
        if selected is InterventionMode.INPUT_3 and readiness["schemes"] != "ready":
            missing.append("two_comparable_schemes")

    if selected is None or not is_confirmed:
        status = "awaiting_confirmation"
    elif missing:
        status = "blocked"
    else:
        status = "confirmed"
    definition = INTERVENTION_REGISTRY[selected or recommended]
    return {
        "schema_version": "dds.intervention-profile/2.0",
        "recommended_mode": int(recommended),
        "recommendation_reasons": reasons,
        "selected_mode": int(selected) if selected is not None else None,
        "mode_status": status,
        "mode_confirmed": status in {"confirmed", "blocked"},
        "eligible": status == "confirmed",
        "missing_inputs": missing,
        "data_readiness": readiness,
        "display_name": definition.display_name,
        "decision_scope": definition.decision_scope,
        "unit_policy": definition.unit_policy,
        "required_units": list(definition.required_units),
        "optional_units": list(definition.optional_units),
        "excluded_units": list(definition.excluded_units),
        "quantitative_authority": list(definition.quantitative_authority),
        "model_version": "dds.intervention-mode/2.0",
    }


def intervention_definition_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> InterventionDefinition | None:
    profile = (metadata or {}).get("analysis_profile")
    if not isinstance(profile, Mapping):
        return None
    try:
        selected = _mode(profile.get("selected_mode"))
    except ValueError:
        return None
    return INTERVENTION_REGISTRY.get(selected) if selected is not None else None


def required_units_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    definition = intervention_definition_from_metadata(metadata)
    return definition.required_units if definition else tuple(VALID_SECTION_IDS)


def included_units_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    profile = (metadata or {}).get("analysis_profile")
    if not isinstance(profile, Mapping):
        return tuple(VALID_SECTION_IDS)
    units = tuple(str(unit) for unit in profile.get("included_units") or ())
    return units or required_units_from_metadata(metadata)


def optional_units_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    definition = intervention_definition_from_metadata(metadata)
    return definition.optional_units if definition else ()


def analysis_profile_contract_errors(
    metadata: Mapping[str, Any] | None,
) -> list[str]:
    profile = (metadata or {}).get("analysis_profile")
    if not isinstance(profile, Mapping):
        return []
    definition = intervention_definition_from_metadata(metadata)
    if definition is None:
        return ["analysis_profile.selected_mode is invalid or unconfirmed"]
    if profile.get("mode_status") != "confirmed":
        return ["analysis_profile.mode_status must be confirmed"]
    expected = {
        "decision_scope": definition.decision_scope,
        "required_units": list(definition.required_units),
        "optional_units": list(definition.optional_units),
        "unit_policy": definition.unit_policy,
        "quantitative_authority": list(definition.quantitative_authority),
    }
    errors = [
        f"analysis_profile.{field} does not match intervention registry"
        for field, expected_value in expected.items()
        if profile.get(field) != expected_value
    ]
    if (metadata or {}).get("decision_scope") != definition.decision_scope:
        errors.append("report_metadata.decision_scope does not match intervention registry")
    return errors


__all__ = [
    "INTERVENTION_REGISTRY",
    "InterventionDefinition",
    "InterventionMode",
    "analysis_profile_contract_errors",
    "included_units_from_metadata",
    "intervention_definition_from_metadata",
    "optional_units_from_metadata",
    "recommend_intervention_mode",
    "required_units_from_metadata",
    "resolve_intervention_profile",
]
