#!/usr/bin/env python3
"""Compile a DDS report payload into a Loca-ready GeoJSON scene manifest."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "dds-loca-scene/v1"


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def point_from(value: Any, *, prefer_gcj02: bool = True) -> tuple[list[float], str] | None:
    if not value:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        lng, lat = finite_number(value[0]), finite_number(value[1])
        source = "reported"
    elif isinstance(value, dict):
        candidates: list[tuple[Any, str]] = []
        if prefer_gcj02:
            candidates.extend([(value.get("gcj02"), "gcj02"), (value.get("location", {}).get("gcj02") if isinstance(value.get("location"), dict) else None, "gcj02")])
        candidates.extend([
            (value.get("wgs84"), "wgs84"),
            (value.get("location", {}).get("wgs84") if isinstance(value.get("location"), dict) else None, "wgs84"),
            (value.get("position"), "reported"),
            (value.get("coordinate"), "reported"),
            (value.get("coordinates"), "reported"),
            (value.get("location") if isinstance(value.get("location"), (list, tuple)) else None, "reported"),
            (value, "reported"),
        ])
        for candidate, source in candidates:
            if isinstance(candidate, (list, tuple)) and len(candidate) >= 2:
                lng, lat = finite_number(candidate[0]), finite_number(candidate[1])
            elif isinstance(candidate, dict):
                lng = finite_number(candidate.get("lng", candidate.get("lon", candidate.get("longitude"))))
                lat = finite_number(candidate.get("lat", candidate.get("latitude")))
            else:
                continue
            if lng is not None and lat is not None:
                break
        else:
            return None
    else:
        return None
    if lng is None or lat is None or not (-180 <= lng <= 180 and -90 <= lat <= 90):
        return None
    return [lng, lat], source


def feature(geometry_type: str, coordinates: Any, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": geometry_type, "coordinates": coordinates},
        "properties": {key: value for key, value in properties.items() if value not in (None, "", [])},
    }


def collection(features: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": list(features)}


def label_of(item: dict[str, Any], fallback: str) -> str:
    return str(item.get("display_name") or item.get("project_name") or item.get("name") or item.get("title") or item.get("label") or fallback)


def flatten_resources(report: dict[str, Any]) -> list[dict[str, Any]]:
    direct = report.get("site", {}).get("resources") if isinstance(report.get("site"), dict) else None
    if not direct and isinstance(report.get("site"), dict):
        direct = report["site"].get("pois")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    amenities = report.get("amenities")
    result: list[dict[str, Any]] = []
    if isinstance(amenities, dict):
        for category, group in amenities.items():
            if not isinstance(group, dict):
                continue
            for item in as_list(group.get("items")):
                if isinstance(item, dict):
                    result.append({**item, "category": item.get("category") or category, "categoryLabel": group.get("label")})
    return result


def build_scene(payload: dict[str, Any], max_links: int = 12) -> dict[str, Any]:
    project = first_dict(payload.get("project"), payload.get("data", {}).get("project") if isinstance(payload.get("data"), dict) else None)
    report = first_dict(payload.get("report"), payload.get("data", {}).get("report") if isinstance(payload.get("data"), dict) else None, payload)
    location = first_dict(project.get("location"), report.get("scene_context", {}).get("location") if isinstance(report.get("scene_context"), dict) else None, report.get("location"), report.get("parcel"))
    site_result = point_from(location)
    if not site_result:
        raise ValueError("DDS payload does not contain a valid project location")
    site, site_crs = site_result

    site_feature = feature("Point", site, {
        "id": location.get("id") or project.get("id") or "site",
        "label": label_of(location, "Target site"),
        "layer": "site",
        "source": location.get("source") or "project",
        "coordinateStatus": site_crs,
        "wgs84": location.get("wgs84"),
        "gcj02": location.get("gcj02"),
    })

    competitors_raw = as_list(report.get("market", {}).get("competitors") if isinstance(report.get("market"), dict) else None) or as_list(report.get("competitors"))
    competitor_features: list[dict[str, Any]] = []
    for index, item in enumerate(competitors_raw):
        if not isinstance(item, dict):
            continue
        result = point_from(item)
        if not result:
            continue
        point, crs = result
        price = finite_number(item.get("unit_price_cny", item.get("unit_price", item.get("avg_price"))))
        distance = finite_number(item.get("distance_km"))
        competitor_features.append(feature("Point", point, {
            "id": item.get("id") or item.get("loupan_id") or f"competitor-{index + 1}",
            "label": label_of(item, f"Competitor {index + 1}"),
            "layer": "competitors",
            "source": item.get("source") or "market",
            "coordinateStatus": crs,
            "distanceKm": distance,
            "unitPriceCny": price,
            "weight": price,
            "category": item.get("property_type"),
            "status": item.get("sales_status"),
            "sourceUrl": item.get("source_url") or item.get("url_anjuke"),
        }))

    resource_features: list[dict[str, Any]] = []
    for index, item in enumerate(flatten_resources(report)):
        result = point_from(item)
        if not result:
            continue
        point, crs = result
        resource_features.append(feature("Point", point, {
            "id": item.get("id") or f"resource-{index + 1}",
            "label": label_of(item, f"Resource {index + 1}"),
            "layer": "resources",
            "source": item.get("source") or "amap-poi",
            "coordinateStatus": crs,
            "distanceM": finite_number(item.get("distance_m", item.get("distance"))),
            "category": item.get("category"),
            "categoryLabel": item.get("categoryLabel"),
        }))

    risk_items: list[Any] = []
    risk = report.get("risk")
    if isinstance(risk, dict):
        risk_items.extend(as_list(risk.get("items")))
    risk_items.extend(as_list(report.get("risks")))
    risk_items.extend(as_list(report.get("hard_stops")))
    risk_items.extend(as_list(project.get("evidence_gaps")))
    risk_features: list[dict[str, Any]] = []
    non_spatial_risks: list[dict[str, Any]] = []
    for index, raw in enumerate(risk_items):
        item = raw if isinstance(raw, dict) else {"label": str(raw)}
        result = point_from(item)
        if not result:
            non_spatial_risks.append(item)
            continue
        point, crs = result
        risk_features.append(feature("Point", point, {
            "id": item.get("id") or f"risk-{index + 1}",
            "label": label_of(item, f"Risk {index + 1}"),
            "layer": "risks",
            "coordinateStatus": crs,
            "severity": item.get("severity") or item.get("level"),
            "source": item.get("source") or "risk",
        }))
    if non_spatial_risks:
        risk_features.append(feature("Point", site, {
            "id": "risk-aggregate-site",
            "label": f"{len(non_spatial_risks)} non-spatial risks",
            "layer": "risks",
            "coordinateStatus": site_crs,
            "count": len(non_spatial_risks),
            "severity": "medium",
            "items": [label_of(item, "Risk") for item in non_spatial_risks[:20]],
            "source": "dds-evidence-gaps",
        }))

    ranked = sorted(
        competitor_features,
        key=lambda item: item["properties"].get("distanceKm") if item["properties"].get("distanceKm") is not None else float("inf"),
    )[: max(0, max_links)]
    link_features = [feature("LineString", [site, item["geometry"]["coordinates"]], {
        "id": f"link-{item['properties']['id']}",
        "label": item["properties"]["label"],
        "layer": "links",
        "distanceKm": item["properties"].get("distanceKm"),
        "unitPriceCny": item["properties"].get("unitPriceCny"),
    }) for item in ranked]

    heat_features = [feature("Point", item["geometry"]["coordinates"], {**item["properties"], "layer": "heat"}) for item in competitor_features if item["properties"].get("weight") is not None]

    camera = {"center": site, "zoom": 13.6, "pitch": 48, "rotation": -10}
    layers = {
        "site": collection([site_feature]),
        "competitors": collection(competitor_features),
        "resources": collection(resource_features),
        "risks": collection(risk_features),
        "links": collection(link_features),
        "pulseLines": collection([]),
        "lines": collection([]),
        "heat": collection(heat_features),
    }
    stats = {key: len(value["features"]) for key, value in layers.items()}
    stats["sourceCompetitors"] = len(competitors_raw)
    stats["invalidCompetitors"] = len(competitors_raw) - len(competitor_features)
    stats["nonSpatialRisks"] = len(non_spatial_risks)
    return {"schema": SCHEMA, "coordinateSystem": site_crs, "camera": camera, "layers": layers, "stats": stats}


def self_test() -> None:
    fixture = {
        "project": {
            "id": "demo",
            "location": {"display_name": "Demo site", "gcj02": {"lng": 117.16, "lat": 36.68}},
            "evidence_gaps": [{"field": "vision", "severity": "medium"}],
        },
        "report": {
            "market": {"competitors": [{"project_name": "A", "lng": 117.17, "lat": 36.69, "distance_km": 1.2, "unit_price_cny": 30000}]},
            "amenities": {"school": {"label": "School", "items": [{"name": "S", "lng": 117.15, "lat": 36.67}]}},
        },
    }
    scene = build_scene(fixture)
    assert scene["schema"] == SCHEMA
    assert scene["stats"]["competitors"] == 1
    assert scene["stats"]["resources"] == 1
    assert scene["stats"]["links"] == 1
    assert scene["stats"]["risks"] == 1
    print("self-test passed")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="DDS report-data JSON")
    parser.add_argument("--output", "-o", type=Path, help="Output scene manifest; stdout when omitted")
    parser.add_argument("--max-links", type=int, default=12, help="Maximum nearest competitor links")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON")
    parser.add_argument("--self-test", action="store_true", help="Run the embedded compiler test")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        self_test()
        return 0
    if not args.input:
        raise SystemExit("input JSON path is required")
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    scene = build_scene(payload, max_links=args.max_links)
    content = json.dumps(scene, ensure_ascii=False, indent=None if args.compact else 2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
        print(json.dumps(scene["stats"], ensure_ascii=False))
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

