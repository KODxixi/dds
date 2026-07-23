"""Single source of truth for DDS Input 1/2/3 analysis depth."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping

from dds.contracts import VALID_SECTION_IDS


class AnalysisLevel(IntEnum):
    INPUT_1 = 1
    INPUT_2 = 2
    INPUT_3 = 3


@dataclass(frozen=True)
class ProfileDefinition:
    level: AnalysisLevel
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


_COMMON_REQUIRED = ("SC1", "SC2", "AD1", "AD2", "AD3", "AD4", "VA2", "VA3", "CS")

PROFILE_REGISTRY: dict[AnalysisLevel, ProfileDefinition] = {
    AnalysisLevel.INPUT_1: ProfileDefinition(
        level=AnalysisLevel.INPUT_1,
        decision_scope="opportunity_screening",
        required_units=_COMMON_REQUIRED,
        optional_units=("SC3", "AD5", "VA1"),
        excluded_units=(),
        quantitative_authority=(
            "standard_100_unit_absorption",
            "relative_price_index",
            "relative_value_index",
            "relative_npv_index",
        ),
    ),
    AnalysisLevel.INPUT_2: ProfileDefinition(
        level=AnalysisLevel.INPUT_2,
        decision_scope="constraint_driven_predevelopment",
        required_units=("SC1", "SC2", "SC3", "AD1", "AD2", "AD3", "AD4", "VA2", "VA3", "CS"),
        optional_units=("AD5", "VA1"),
        excluded_units=(),
        quantitative_authority=(
            "sourced_project_capacity",
            "project_absorption_scenarios",
            "preliminary_cash_flow",
        ),
    ),
    AnalysisLevel.INPUT_3: ProfileDefinition(
        level=AnalysisLevel.INPUT_3,
        decision_scope="scheme_selection",
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


def _level(value: Any, *, default: AnalysisLevel = AnalysisLevel.INPUT_1) -> AnalysisLevel:
    if isinstance(value, AnalysisLevel):
        return value
    text = str(value or "").strip().lower().replace("input", "").replace("_", " ")
    try:
        return AnalysisLevel(int(text.strip()))
    except (TypeError, ValueError):
        return default


def assess_analysis_level(input_profile: Mapping[str, Any]) -> tuple[AnalysisLevel, list[str]]:
    """Classify deterministically; weak evidence never promotes the whole project."""

    has_location = bool(
        input_profile.get("address")
        or (input_profile.get("latitude") is not None and input_profile.get("longitude") is not None)
        or (input_profile.get("lat") is not None and input_profile.get("lng") is not None)
    )
    if not has_location:
        return AnalysisLevel.INPUT_1, ["location_unresolved"]
    reasons = ["location_resolved"]
    constraints = input_profile.get("constraint_sources") or input_profile.get("constraints") or []
    constraint_items = constraints if isinstance(constraints, (list, tuple)) else []
    valid_constraints = [
        item for item in constraint_items
        if isinstance(item, Mapping)
        and (item.get('source_ref') or item.get('source_hash'))
        and str(item.get('status') or 'verified').lower()
        not in {'conflict', 'stale', 'expired'}
    ]
    constraint_ready = bool(valid_constraints)
    if constraint_items and not constraint_ready:
        reasons.append('constraints_unusable')
    if constraint_ready:
        reasons.append("traceable_constraints_present")
    schemes = input_profile.get("schemes") or input_profile.get("scheme_inputs") or []
    comparable_schemes = len(schemes) if isinstance(schemes, (list, tuple)) else 0
    core_boundaries = bool(input_profile.get("core_development_boundaries_ready"))
    if constraint_ready and core_boundaries and comparable_schemes >= 2:
        return AnalysisLevel.INPUT_3, reasons + ["core_boundaries_ready", "two_comparable_schemes"]
    if constraint_ready:
        return AnalysisLevel.INPUT_2, reasons
    return AnalysisLevel.INPUT_1, reasons


def resolve_analysis_profile(
    input_profile: Mapping[str, Any] | None = None,
    *,
    requested_level: AnalysisLevel | int | str | None = None,
) -> dict[str, Any]:
    raw = dict(input_profile or {})
    assessed, reasons = assess_analysis_level(raw)
    requested = _level(requested_level or raw.get("requested_level"), default=assessed)
    effective = min(requested, assessed)
    definition = PROFILE_REGISTRY[effective]
    blockers = [reason for reason in reasons if reason == 'location_unresolved']
    return {
        'classification_status': 'blocked' if blockers else 'resolved',
        'classification_blockers': blockers,
        'eligible': not blockers,
        "schema_version": "dds.analysis-profile/1.0",
        "requested_level": int(requested),
        "assessed_level": int(assessed),
        "effective_level": int(effective),
        "display_name": f"Input {int(effective)}",
        "decision_scope": definition.decision_scope,
        "classification_reasons": reasons,
        "unit_policy": definition.unit_policy,
        "required_units": list(definition.required_units),
        "optional_units": list(definition.optional_units),
        "excluded_units": list(definition.excluded_units),
        "quantitative_authority": list(definition.quantitative_authority),
        "model_version": "dds.input-profile/1.0",
    }


def required_units_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    profile = (metadata or {}).get("analysis_profile")
    if not isinstance(profile, Mapping):
        return tuple(VALID_SECTION_IDS)
    definition = profile_definition_from_metadata(metadata)
    return definition.required_units if definition else tuple(VALID_SECTION_IDS)


def included_units_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    profile = (metadata or {}).get("analysis_profile")
    if not isinstance(profile, Mapping):
        return tuple(VALID_SECTION_IDS)
    units = tuple(str(unit) for unit in profile.get("included_units") or ())
    return units or required_units_from_metadata(metadata)


def optional_units_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[str, ...]:
    definition = profile_definition_from_metadata(metadata)
    return definition.optional_units if definition else ()


def profile_definition_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> ProfileDefinition | None:
    profile = (metadata or {}).get("analysis_profile")
    if not isinstance(profile, Mapping):
        return None
    try:
        level = AnalysisLevel(int(profile.get("effective_level")))
    except (TypeError, ValueError):
        return None
    return PROFILE_REGISTRY[level]


def analysis_profile_contract_errors(
    metadata: Mapping[str, Any] | None,
) -> list[str]:
    profile = (metadata or {}).get("analysis_profile")
    if not isinstance(profile, Mapping):
        return []
    definition = profile_definition_from_metadata(metadata)
    if definition is None:
        return ["analysis_profile.effective_level is invalid"]
    errors = []
    expected = {
        "decision_scope": definition.decision_scope,
        "required_units": list(definition.required_units),
        "optional_units": list(definition.optional_units),
        "unit_policy": definition.unit_policy,
        "quantitative_authority": list(definition.quantitative_authority),
    }
    for field, expected_value in expected.items():
        if profile.get(field) != expected_value:
            errors.append(
                f"analysis_profile.{field} does not match profile registry"
            )
    if (metadata or {}).get("decision_scope") != definition.decision_scope:
        errors.append(
            "report_metadata.decision_scope does not match profile registry"
        )
    return errors


__all__ = [
    "AnalysisLevel",
    "PROFILE_REGISTRY",
    "ProfileDefinition",
    "analysis_profile_contract_errors",
    "assess_analysis_level",
    "included_units_from_metadata",
    "optional_units_from_metadata",
    "profile_definition_from_metadata",
    "required_units_from_metadata",
    "resolve_analysis_profile",
]
