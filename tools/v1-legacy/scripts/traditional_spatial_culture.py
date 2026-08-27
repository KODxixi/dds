"""Evidence-gated traditional spatial culture (feng-shui) contract.

This module keeps measured physical conditions, consumer perceptions, and
traditional cultural interpretations in separate channels.  Traditional
readings are design-only annotations and can never become investment factors.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

try:  # Support package and existing top-level script imports.
    from .evidence_contract import compute_evidence_confidence
except ImportError:  # pragma: no cover - existing app.py import convention
    from evidence_contract import compute_evidence_confidence


INPUT_LEVELS = ("G0", "G1", "G2", "G3", "G4")

METHOD_PROFILE = {
    "enabled_methods": [
        "form_school_foundation",
        "wuxing_bagua_cultural_mapping",
    ],
    "disabled_methods": [
        "xuan_kong_flying_stars",
        "eight_mansions",
        "bazi",
        "date_selection",
    ],
    "default_policy": "foundation_only",
}

PROHIBITED_USES = (
    "investment_model",
    "land_price",
    "sale_price_premium",
    "sell_through_forecast",
    "roi",
    "irr",
    "npv",
    "compliance_verdict",
    "health_wealth_or_life_outcome_claims",
)

_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "coordinates": (
        "coordinates",
        "location.coordinates",
        "wgs84",
        "location.wgs84",
    ),
    "site_boundary": (
        "site_boundary",
        "parcel_boundary",
        "boundary",
        "redline",
        "site.redline",
        "geometry",
    ),
    "true_north": (
        "true_north",
        "true_north_deg",
        "reference_frame.true_north_deg",
        "north.true_deg",
    ),
    "roads": (
        "roads",
        "road_network",
        "surroundings.roads",
        "environment.roads",
    ),
    "water": (
        "water",
        "water_bodies",
        "water_system",
        "hydrology",
        "surroundings.water",
    ),
    "terrain": (
        "terrain",
        "topography",
        "dem",
        "elevation",
        "site.terrain",
    ),
    "environment": (
        "environment",
        "environmental_context",
        "microclimate",
        "site_environment",
    ),
    "masterplan": (
        "masterplan",
        "site_plan",
        "general_layout",
        "scheme.masterplan",
    ),
    "building_orientations": (
        "building_orientations",
        "orientations",
        "building_facing",
        "scheme.building_orientations",
    ),
    "main_entrance": (
        "main_entrance",
        "primary_entrance",
        "entrance",
        "scheme.main_entrance",
    ),
    "onsite_compass": (
        "onsite_compass",
        "compass_record",
        "field_compass",
        "fieldwork.compass",
    ),
    "expert_review": (
        "expert_review",
        "expert_verification",
        "consultant_review",
        "fieldwork.expert_review",
    ),
}

_LEVEL_REQUIREMENTS = {
    "G1": ("coordinates", "site_boundary", "true_north", "roads", "water"),
    "G2": ("terrain", "environment"),
    "G3": ("masterplan", "building_orientations", "main_entrance"),
    "G4": ("onsite_compass", "expert_review"),
}

_PLACEHOLDER_TOKENS = {
    "missing",
    "pending",
    "unknown",
    "not_available",
    "notavailable",
    "n/a",
    "none",
    "null",
    "placeholder",
    "todo",
    "blocked",
    "待补充",
    "待提供",
    "暂无",
    "未知",
    "未提供",
    "缺失",
}
_METADATA_ONLY_KEYS = {
    "status",
    "available",
    "placeholder",
    "missing",
    "todo",
    "note",
    "reason",
    "required",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value is True
    if isinstance(value, str):
        text = value.strip()
        return bool(text) and text.lower() not in _PLACEHOLDER_TOKENS
    if isinstance(value, Mapping):
        if not value:
            return False
        status = str(value.get("status") or "").strip().lower()
        if status in _PLACEHOLDER_TOKENS:
            return False
        if value.get("available") is False or value.get("placeholder") is True:
            return False
        semantic_items = [
            item for key, item in value.items() if str(key) not in _METADATA_ONLY_KEYS
        ]
        return bool(semantic_items) and any(_has_value(item) for item in semantic_items)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_has_value(item) for item in value)
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False
    return True


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coordinate_pair(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        lng = value.get("lng", value.get("lon", value.get("longitude")))
        lat = value.get("lat", value.get("latitude"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) < 2:
            return None
        lng, lat = value[0], value[1]
    else:
        return None
    lng_number = _finite_number(lng)
    lat_number = _finite_number(lat)
    if lng_number is None or lat_number is None:
        return None
    if not (-180 <= lng_number <= 180 and -90 <= lat_number <= 90):
        return None
    return lng_number, lat_number


def _contains_coordinate_pair(value: Any) -> bool:
    if _coordinate_pair(value) is not None:
        return True
    if isinstance(value, Mapping):
        return any(_contains_coordinate_pair(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_coordinate_pair(item) for item in value)
    return False


def _field_has_value(field: str, value: Any) -> bool:
    if isinstance(value, bool) or not _has_value(value):
        return False
    if field == "coordinates":
        return _coordinate_pair(value) is not None
    if field == "true_north":
        if _finite_number(value) is not None:
            return True
        if isinstance(value, Mapping):
            return any(
                _finite_number(value.get(key)) is not None
                for key in ("degrees", "deg", "azimuth", "value", "true_north_deg")
            )
        return False
    if field == "site_boundary":
        if isinstance(value, Mapping):
            geometry = value.get("geometry") if isinstance(value.get("geometry"), Mapping) else value
            geometry_type = str(geometry.get("type") or "").strip().lower()
            if geometry_type and geometry_type not in {"polygon", "multipolygon", "feature"}:
                return False
            if "coordinates" in geometry:
                return _contains_coordinate_pair(geometry.get("coordinates"))
            if _has_value(geometry.get("wkt")) or _has_value(geometry.get("boundary_ref")):
                return True
            return _has_value(geometry.get("id")) or _has_value(geometry.get("source_ref"))
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return _contains_coordinate_pair(value)
    return _has_value(value)


def _path_get(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _lookup(field: str, *sources: Mapping[str, Any]) -> Any:
    for source in sources:
        for path in _FIELD_PATHS[field]:
            value = _path_get(source, path)
            if _field_has_value(field, value):
                return value
    return None


def _coordinates(parcel: Mapping[str, Any]) -> Any:
    explicit = _lookup("coordinates", parcel)
    if _field_has_value("coordinates", explicit):
        return explicit
    lng = parcel.get("lng")
    lat = parcel.get("lat")
    direct = {"lng": lng, "lat": lat}
    if _field_has_value("coordinates", direct):
        return direct
    location = parcel.get("location")
    if isinstance(location, Mapping):
        lng = location.get("lng")
        lat = location.get("lat")
        nested = {"lng": lng, "lat": lat}
        if _field_has_value("coordinates", nested):
            return nested
    return None


def _input_values(
    parcel: Mapping[str, Any], project_context: Mapping[str, Any]
) -> dict[str, Any]:
    values = {"coordinates": _coordinates(parcel)}
    for field in _FIELD_PATHS:
        if field == "coordinates":
            continue
        # Scheme and expert material normally live in project_context, but
        # accepting either source keeps the pure contract easy to integrate.
        values[field] = _lookup(field, project_context, parcel)
    return values


def _grade(values: Mapping[str, Any]) -> str:
    level = "G0"
    for candidate in ("G1", "G2", "G3", "G4"):
        if all(
            _field_has_value(field, values.get(field))
            for field in _LEVEL_REQUIREMENTS[candidate]
        ):
            level = candidate
        else:
            break
    return level


def _value_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        source_refs = value.get("source_refs") or value.get("sources") or []
        return {
            "kind": "object",
            "keys": sorted(str(key) for key in value.keys() if key not in {"raw", "payload"}),
            "source_refs": list(source_refs) if isinstance(source_refs, Sequence) and not isinstance(source_refs, str) else [],
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {"kind": "collection", "count": len(value), "source_refs": []}
    return {"kind": "scalar", "value": deepcopy(value), "source_refs": []}


def _physical_observations(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for field in (
        "coordinates",
        "site_boundary",
        "true_north",
        "roads",
        "water",
        "terrain",
        "environment",
        "masterplan",
        "building_orientations",
        "main_entrance",
        "onsite_compass",
    ):
        value = values.get(field)
        if not _field_has_value(field, value):
            continue
        summary = _value_summary(value)
        confidence = compute_evidence_confidence(
            source=0.70,
            coverage=0.70,
            freshness=0.60,
            independent_cross=0.30,
            geographic_relevance=1.0,
            method_fit=0.80,
            stability=0.70,
        )
        observations.append(
            {
                "observation_id": f"physical:{field}",
                "feature_type": field,
                "evidence_type": "observed_fact",
                "value_summary": {key: value for key, value in summary.items() if key != "source_refs"},
                "source_refs": summary["source_refs"],
                "confidence": confidence,
                "decision_eligibility": "physical_review",
            }
        )
    return observations


def _reading(
    reading_id: str,
    school: str,
    statement: str,
    rule_ref: str,
    feature_refs: Sequence[str],
    *,
    expert_review_required: bool,
) -> dict[str, Any]:
    return {
        "reading_id": reading_id,
        "evidence_type": "traditional_interpretation",
        "school": school,
        "statement": statement,
        "rule_ref": rule_ref,
        "feature_refs": list(feature_refs),
        "interpretation_consistency": "method_specific",
        "causal_status": "not_established",
        "decision_eligibility": "design_only",
        "expert_review_required": expert_review_required,
    }


def _traditional_readings(level: str) -> list[dict[str, Any]]:
    index = INPUT_LEVELS.index(level)
    if index < 1:
        return []
    readings = [
        _reading(
            "traditional:form-foundation",
            "form_school_foundation",
            "边界、真北、道路与水体资料已具备，可开展形势派基础空间关系核验；不得据此作吉凶或因果判断。",
            "TSC-FORM-001",
            [
                "physical:coordinates",
                "physical:site_boundary",
                "physical:true_north",
                "physical:roads",
                "physical:water",
            ],
            expert_review_required=False,
        )
    ]
    if index >= 2:
        readings.append(
            _reading(
                "traditional:terrain-environment",
                "form_school_foundation",
                "地形与环境资料可把传统空间叙事拆解为坡向、风、排水、围合和视线等可核验设计问题。",
                "TSC-FORM-002",
                ["physical:terrain", "physical:environment"],
                expert_review_required=False,
            )
        )
    if index >= 3:
        readings.append(
            _reading(
                "traditional:wuxing-bagua-mapping",
                "wuxing_bagua_cultural_mapping",
                "五行与八卦仅作为总图功能、材料、景观和叙事的文化映射层，不替代日照、消防、交通与成本校核。",
                "TSC-CULTURE-001",
                [
                    "physical:masterplan",
                    "physical:building_orientations",
                    "physical:main_entrance",
                ],
                expert_review_required=True,
            )
        )
    if index >= 4:
        readings.append(
            _reading(
                "traditional:expert-review",
                "expert_review",
                "现场罗盘与具名专家复核信息已登记，仅允许人工复核特定流派解释，不改变其非因果和设计参考属性。",
                "TSC-REVIEW-001",
                ["physical:onsite_compass"],
                expert_review_required=False,
            )
        )
    return readings


def _consumer_perceptions(
    social_intelligence: Mapping[str, Any]
) -> list[dict[str, Any]]:
    profiles = social_intelligence.get("persona_evidence_profiles") or []
    if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes, bytearray)):
        return []
    links: list[dict[str, Any]] = []
    for index, profile in enumerate(profiles):
        if not isinstance(profile, Mapping):
            continue
        segment = profile.get("segment")
        topic = profile.get("topic")
        if not segment and not topic:
            continue
        links.append(
            {
                "perception_id": profile.get("profile_id") or f"consumer-perception:{index + 1}",
                "evidence_type": "social_observation",
                "segment": segment,
                "topic": topic,
                "unique_authors": int(profile.get("unique_authors") or 0),
                "platform_coverage": deepcopy(profile.get("platform_coverage") or {}),
                "support": deepcopy(profile.get("support") or {}),
                "opposition": deepcopy(profile.get("opposition") or {}),
                "evidence_confidence": deepcopy(profile.get("evidence_confidence") or {"score": 0.0}),
                "causal_status": "consumer_perception_only",
                "decision_eligibility": "market_perception_with_empirical_gate",
            }
        )
    return links


def _completeness(values: Mapping[str, Any], level: str) -> dict[str, Any]:
    all_requirements = [
        field
        for candidate in ("G1", "G2", "G3", "G4")
        for field in _LEVEL_REQUIREMENTS[candidate]
    ]
    available = [
        field
        for field in all_requirements
        if _field_has_value(field, values.get(field))
    ]
    missing = [field for field in all_requirements if field not in available]
    level_index = INPUT_LEVELS.index(level)
    next_level = INPUT_LEVELS[level_index + 1] if level_index < len(INPUT_LEVELS) - 1 else None
    missing_for_next = (
        [
            field
            for field in _LEVEL_REQUIREMENTS[next_level]
            if not _field_has_value(field, values.get(field))
        ]
        if next_level
        else []
    )
    return {
        "available": available,
        "missing": missing,
        "requirements": {
            candidate: {
                field: _field_has_value(field, values.get(field))
                for field in _LEVEL_REQUIREMENTS[candidate]
            }
            for candidate in ("G1", "G2", "G3", "G4")
        },
        "next_level": next_level,
        "missing_for_next_level": missing_for_next,
    }


def _blocked(level: str, completeness: Mapping[str, Any]) -> list[dict[str, Any]]:
    index = INPUT_LEVELS.index(level)
    blocked: list[dict[str, Any]] = []
    if index < 1:
        blocked.append(
            {
                "analysis": "form_school_screening",
                "reason": "G1 inputs are incomplete",
                "missing": [
                    field
                    for field in _LEVEL_REQUIREMENTS["G1"]
                    if field in completeness["missing"]
                ],
            }
        )
    if index < 2:
        blocked.append(
            {
                "analysis": "terrain_environment_interpretation",
                "reason": "G2 inputs are incomplete",
                "missing": [
                    field
                    for field in _LEVEL_REQUIREMENTS["G2"]
                    if field in completeness["missing"]
                ],
            }
        )
    if index < 3:
        blocked.append(
            {
                "analysis": "masterplan_cultural_mapping",
                "reason": "G3 inputs are incomplete",
                "missing": [
                    field
                    for field in _LEVEL_REQUIREMENTS["G3"]
                    if field in completeness["missing"]
                ],
            }
        )
    if index < 4:
        blocked.append(
            {
                "analysis": "expert_review",
                "reason": "G4 field and expert inputs are incomplete",
                "missing": [
                    field
                    for field in _LEVEL_REQUIREMENTS["G4"]
                    if field in completeness["missing"]
                ],
            }
        )
    # These high-order methods remain disabled even at G4 unless a future,
    # separately approved expert workflow explicitly enables them.
    blocked.append(
        {
            "analysis": "high_order_methods",
            "reason": "disabled_by_default_policy",
            "methods": list(METHOD_PROFILE["disabled_methods"]),
        }
    )
    return blocked


def build_traditional_spatial_culture(
    parcel: Mapping[str, Any] | None,
    project_context: Mapping[str, Any] | None = None,
    social_intelligence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a G0..G4 evidence-gated traditional spatial culture module."""
    parcel_data = dict(parcel or {})
    context_data = dict(project_context or {})
    social_data = dict(social_intelligence or {})
    values = _input_values(parcel_data, context_data)
    level = _grade(values)
    status = {
        "G0": "not_assessable",
        "G1": "screening",
        "G2": "screening",
        "G3": "design_review",
        "G4": "expert_reviewed",
    }[level]
    completeness = _completeness(values, level)
    physical = _physical_observations(values)
    perceptions = _consumer_perceptions(social_data)
    readings = _traditional_readings(level)

    return {
        "status": status,
        "input_level": level,
        "method_version": "traditional-spatial-culture/1.0",
        "method_profile": deepcopy(METHOD_PROFILE),
        "input_completeness": completeness,
        "physical_observations": physical,
        "traditional_readings": readings,
        "consumer_perception_links": perceptions,
        "blocked_analyses": _blocked(level, completeness),
        "evidence_gaps": [
            {
                "field": field,
                "required_for": next_level,
                "blocking": True,
            }
            for next_level, fields in _LEVEL_REQUIREMENTS.items()
            for field in fields
            if not _field_has_value(field, values.get(field))
        ],
        "prohibited_uses": list(PROHIBITED_USES),
        "investment_boundary": {
            "traditional_fields_allowed": False,
            "traditional_score_export": None,
            "allowed_empirical_channels": [
                "physical_observations",
                "consumer_perception_links_with_empirical_gate",
            ],
            "precedence": [
                "legal_and_safety",
                "observed_physical_fact",
                "consumer_perception_evidence",
                "traditional_interpretation_design_only",
            ],
        },
    }


__all__ = [
    "INPUT_LEVELS",
    "METHOD_PROFILE",
    "PROHIBITED_USES",
    "build_traditional_spatial_culture",
]
