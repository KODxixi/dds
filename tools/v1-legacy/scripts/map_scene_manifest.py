"""Compile DDS report snapshots into the stable map scene contract."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


SCHEMA = "dds-map-scene/v2"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _point(value: Any) -> dict[str, float] | None:
    source = _dict(value)
    for candidate in (source.get("gcj02"), source.get("wgs84"), source.get("location"), source):
        item = _dict(candidate)
        try:
            lng = float(item.get("lng", item.get("lon", item.get("longitude"))))
            lat = float(item.get("lat", item.get("latitude")))
        except (TypeError, ValueError):
            continue
        if -180 <= lng <= 180 and -90 <= lat <= 90:
            return {"lng": lng, "lat": lat}
    return None


def _feature(item: Mapping[str, Any], *, feature_id: str, category: str, source: str) -> dict[str, Any] | None:
    point = _point(item)
    if not point:
        return None
    raw = _dict(item)
    gcj02 = _point(raw.get("gcj02")) or point
    original = _point(raw.get("wgs84")) or point
    label = raw.get("display_name") or raw.get("project_name") or raw.get("name") or raw.get("title") or feature_id
    return {
        "id": str(raw.get("id") or raw.get("code") or feature_id),
        "geometry": {"type": "Point", "coordinates": [gcj02["lng"], gcj02["lat"]]},
        "category": category,
        "label": str(label),
        "source": str(raw.get("source") or source),
        "gcj02": gcj02,
        "originalCoordinate": original,
        "originalCrs": str(raw.get("coordinate_system") or raw.get("crs") or "unknown"),
        "observedAt": raw.get("observed_at") or raw.get("updated_at") or raw.get("date"),
        "evidenceGrade": raw.get("evidence_grade") or raw.get("grade"),
        "confidence": raw.get("confidence"),
        "scenarioStatus": str(raw.get("scenario_status") or "observed"),
        "metrics": {
            key: raw.get(key)
            for key in (
                "distance_m",
                "distance_km",
                "unit_price_cny",
                "unit_price",
                "price",
                "city",
                "district",
                "sub_district",
                "address",
                "property_type",
                "building_type",
                "sale_status",
                "status",
                "floor_area_ratio",
                "plot_ratio",
                "total_units",
                "opening_date",
                "severity",
            )
            if raw.get(key) is not None
        },
    }


def _amenity_items(report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    output: list[tuple[str, Mapping[str, Any]]] = []
    for category, payload in _dict(report.get("amenities")).items():
        if isinstance(payload, list):
            items = payload
        else:
            items = _list(_dict(payload).get("items")) or _list(_dict(payload).get("pois"))
        output.extend((str(category), item) for item in items if isinstance(item, Mapping))
    return output


def compile_scene_manifest(project: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a renderer-neutral manifest without mutating either input."""

    project_data = _dict(deepcopy(project))
    report_data = _dict(deepcopy(report))
    parcel = _dict(report_data.get("parcel"))
    location = _dict(project_data.get("location")) or parcel
    site_point = _point(location) or _point(parcel)
    site = {
        "id": "site",
        "label": location.get("display_name") or location.get("address") or parcel.get("address") or "目标地块",
        "gcj02": _point(location.get("gcj02")) or site_point,
        "originalCoordinate": _point(location.get("wgs84")) or site_point,
        "originalCrs": location.get("coordinate_system") or "unknown",
        "boundary": parcel.get("site_boundary") or parcel.get("geometry") or report_data.get("site_boundary"),
        "scenarioStatus": "observed",
    }

    competitors = []
    for index, item in enumerate(_list(_dict(report_data.get("market")).get("competitors"))):
        if isinstance(item, Mapping):
            feature = _feature(item, feature_id=f"competitor-{index + 1}", category="competitor", source="market")
            if feature:
                competitors.append(feature)

    resources = []
    for index, (category, item) in enumerate(_amenity_items(report_data)):
        feature = _feature(item, feature_id=f"resource-{index + 1}", category=category, source="amap-web-service")
        if feature:
            resources.append(feature)

    decision_full = _dict(report_data.get("decision_full"))
    blueprint = _dict(decision_full.get("blueprint_logic"))
    risks = []
    for index, item in enumerate(_list(blueprint.get("risk_curve")) + _list(report_data.get("risks"))):
        raw = _dict(item)
        risks.append({
            "id": str(raw.get("id") or f"risk-{index + 1}"),
            "label": str(raw.get("risk") or raw.get("name") or raw.get("text") or f"风险 {index + 1}"),
            "category": "risk",
            "source": str(raw.get("source") or "decision-engine"),
            "scenarioStatus": str(raw.get("scenario_status") or "inferred"),
            "severity": raw.get("level") or raw.get("severity"),
            "trigger": raw.get("trigger"),
            "gcj02": _point(raw) or site_point,
        })

    abm = _dict(decision_full.get("abm_market_agent"))
    unit_mix = _dict(decision_full.get("unit_mix_agent"))
    migration = _dict(decision_full.get("migration_agent"))
    time_travel = _dict(decision_full.get("time_travel_agent"))
    financial = _dict(blueprint.get("financial_indicator"))
    evidence_packs = _list(report_data.get("evidence_packs"))

    layer_specs = [
        {"id": "parcel", "kind": "site", "features": [site] if site_point else [], "zooms": [3, 20]},
        {"id": "competitors", "kind": "points", "features": competitors, "zooms": [11, 20]},
        {"id": "resources", "kind": "points", "features": resources, "zooms": [12, 20]},
        {"id": "risks", "kind": "uncertainty", "features": risks, "zooms": [11, 20]},
    ]
    chapters = [
        {"id": "decision", "mode": "3d", "preset": "context", "layers": ["parcel", "competitors"]},
        {"id": "site", "mode": "3d", "preset": "site", "layers": ["parcel", "searchArea", "resources"]},
        {"id": "market", "mode": "2d", "preset": "evidence", "layers": ["parcel", "competitors", "heat"]},
        {"id": "customer", "mode": "2d", "preset": "context", "layers": ["parcel", "resources"]},
        {"id": "product", "mode": "2d", "preset": "site", "layers": ["parcel"]},
        {"id": "design", "mode": "3d", "preset": "site", "layers": ["parcel", "buildings"]},
        {"id": "finance", "mode": "2d", "preset": "evidence", "layers": ["parcel", "competitors", "heat", "risks"]},
        {"id": "risk", "mode": "2d", "preset": "context", "layers": ["parcel", "risks"]},
        {"id": "provenance", "mode": "2d", "preset": "site", "layers": ["parcel"]},
    ]
    return {
        "schema": SCHEMA,
        "projectId": str(project_data.get("id") or project_data.get("project_id") or ""),
        "coordinateSystem": "gcj02",
        "site": site,
        "layers": layer_specs,
        "metrics": {
            "decision": _dict(report_data.get("decision")),
            "market": _dict(report_data.get("market")),
            "customer": {"topPersonas": abm.get("top_personas"), "wtp": abm.get("wtp_summary"), "sellthrough": abm.get("sellthrough_curve")},
            "product": {"unitMix": unit_mix.get("unit_mix"), "totalUnits": unit_mix.get("total_units"), "revenueCny": unit_mix.get("total_expected_revenue_cny")},
            "finance": financial,
            "risk": {"items": risks},
        },
        "timelines": {"migration": migration.get("yearly_distribution") or [], "history": time_travel.get("snapshots") or []},
        "scenarios": _list(report_data.get("scenarios")),
        "evidence": {"items": evidence_packs, "count": len(evidence_packs)},
        "chapters": chapters,
        "stats": {"competitors": len(competitors), "resources": len(resources), "risks": len(risks)},
    }


__all__ = ["SCHEMA", "compile_scene_manifest"]
