"""China-aware location gateway for Cesium and AMap interoperability."""

from __future__ import annotations

import math
import re
from typing import Any, Callable


PI = math.pi
AXIS = 6378245.0
ECCENTRICITY = 0.00669342162296594323
COORDINATE_RE = re.compile(
    r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,，\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$"
)


class LocationLookupError(RuntimeError):
    pass


def parse_coordinate_query(text: str | None) -> tuple[float | None, float | None]:
    match = COORDINATE_RE.match(str(text or ""))
    if not match:
        return None, None
    first, second = float(match.group(1)), float(match.group(2))
    first_is_china_lng = 72.0 <= first <= 138.0 and 0.0 <= second <= 56.0
    second_is_china_lng = 72.0 <= second <= 138.0 and 0.0 <= first <= 56.0
    if first_is_china_lng:
        lng, lat = first, second
    elif second_is_china_lng:
        lng, lat = second, first
    elif abs(first) > 90 and abs(second) <= 90:
        lng, lat = first, second
    elif abs(second) > 90 and abs(first) <= 90:
        lng, lat = second, first
    else:
        lng, lat = first, second
    if -180 <= lng <= 180 and -90 <= lat <= 90:
        return round(lng, 7), round(lat, 7)
    return None, None


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    if _outside_china(lng, lat):
        return round(lng, 7), round(lat, 7)
    delta_lng, delta_lat = _gcj_delta(lng, lat)
    return round(lng + delta_lng, 7), round(lat + delta_lat, 7)


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    if _outside_china(lng, lat):
        return round(lng, 7), round(lat, 7)
    wgs_lng, wgs_lat = lng, lat
    for _ in range(12):
        projected_lng, projected_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)
        error_lng = projected_lng - lng
        error_lat = projected_lat - lat
        wgs_lng -= error_lng
        wgs_lat -= error_lat
        if abs(error_lng) < 0.0000001 and abs(error_lat) < 0.0000001:
            break
    return round(wgs_lng, 7), round(wgs_lat, 7)


class LocationGateway:
    def __init__(
        self,
        *,
        amap_key: str,
        amap_getter: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
    ) -> None:
        self.amap_key = str(amap_key or "").strip()
        self._amap_getter = amap_getter

    def suggest(self, query: str, city: str = "") -> list[dict[str, Any]]:
        text = str(query or "").strip()
        if not text:
            return []
        lng, lat = parse_coordinate_query(text)
        if lng is not None and lat is not None:
            return [self._coordinate_location(lng, lat, city=city)]
        if len(text) < 2 or not self._amap_available:
            return []

        try:
            response = self._call_amap(
                "/assistant/inputtips",
                {"keywords": text, "city": city, "citylimit": "true", "datatype": "all"},
            )
        except Exception:
            return []

        results: list[dict[str, Any]] = []
        for tip in response.get("tips") or []:
            point = _parse_amap_location(tip.get("location"))
            if not point:
                continue
            gcj_lng, gcj_lat = point
            results.append(
                self._location(
                    location_id=str(tip.get("id") or f"tip_{len(results)}"),
                    display_name=str(tip.get("name") or tip.get("address") or text),
                    city=city or _normalize_city(tip.get("city")),
                    district=str(tip.get("district") or ""),
                    address=str(tip.get("address") or tip.get("name") or text),
                    gcj_lng=gcj_lng,
                    gcj_lat=gcj_lat,
                    source="amap",
                    confidence=0.84,
                    adcode=str(tip.get("adcode") or "") or None,
                )
            )
            if len(results) >= 5:
                break
        return results

    def resolve(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate = payload.get("candidate")
        if isinstance(candidate, dict) and candidate.get("wgs84") and candidate.get("gcj02"):
            return self._normalize_candidate(candidate)

        if payload.get("lng") is not None and payload.get("lat") is not None:
            lng = float(payload["lng"])
            lat = float(payload["lat"])
            system = str(payload.get("coordinate_system") or "wgs84").lower()
            return self._explicit_location(lng, lat, system=system, city=str(payload.get("city") or ""))

        query = str(payload.get("query") or "").strip()
        city = str(payload.get("city") or "").strip()
        lng, lat = parse_coordinate_query(query)
        if lng is not None and lat is not None:
            return self._coordinate_location(lng, lat, city=city)
        if not query:
            raise LocationLookupError("请输入地址、地块名或坐标")
        if not self._amap_available:
            raise LocationLookupError("地址服务未配置，请直接输入坐标或在地图上选点")

        try:
            response = self._call_amap("/geocode/geo", {"address": query, "city": city})
        except Exception as exc:
            raise LocationLookupError("地址服务暂时不可用，请改用坐标") from exc
        geocodes = response.get("geocodes") or []
        if not geocodes:
            raise LocationLookupError("未找到可靠位置，请选择候选或直接在地图上选点")
        geocode = geocodes[0]
        point = _parse_amap_location(geocode.get("location"))
        if not point:
            raise LocationLookupError("地址结果缺少有效坐标")
        gcj_lng, gcj_lat = point
        return self._location(
            location_id=str(geocode.get("adcode") or "amap_geocode") + "_" + _point_id(gcj_lng, gcj_lat),
            display_name=str(geocode.get("formatted_address") or query),
            city=_normalize_city(geocode.get("city")) or city,
            district=str(geocode.get("district") or ""),
            address=str(geocode.get("formatted_address") or query),
            gcj_lng=gcj_lng,
            gcj_lat=gcj_lat,
            source="amap",
            confidence=0.92,
            adcode=str(geocode.get("adcode") or "") or None,
        )

    def reverse(self, lng: float, lat: float, *, city: str = "") -> dict[str, Any]:
        location = self._explicit_location(lng, lat, system="wgs84", city=city)
        if not self._amap_available:
            return location
        gcj = location["gcj02"]
        try:
            response = self._call_amap(
                "/geocode/regeo",
                {"location": f"{gcj['lng']},{gcj['lat']}", "extensions": "base"},
            )
            component = ((response.get("regeocode") or {}).get("addressComponent") or {})
            formatted = str((response.get("regeocode") or {}).get("formatted_address") or "")
            if formatted:
                location.update(
                    {
                        "display_name": formatted,
                        "address": formatted,
                        "city": _normalize_city(component.get("city")) or city,
                        "district": str(component.get("district") or ""),
                        "adcode": str(component.get("adcode") or "") or None,
                        "source": "amap_reverse",
                        "confidence": 0.9,
                    }
                )
        except Exception:
            pass
        return location

    @property
    def _amap_available(self) -> bool:
        return bool(self.amap_key and self.amap_key != "YOUR_AMAP_KEY_HERE")

    def _call_amap(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        getter = self._amap_getter
        if getter is None:
            from gis_amap import amap_get

            getter = amap_get
        return getter(path, params, self.amap_key)

    def _coordinate_location(self, lng: float, lat: float, *, city: str) -> dict[str, Any]:
        location = self._explicit_location(lng, lat, system="wgs84", city=city)
        location["input_kind"] = "coordinate"
        location["coordinate_assumption"] = "wgs84_default"
        return location

    def _explicit_location(
        self,
        lng: float,
        lat: float,
        *,
        system: str,
        city: str,
    ) -> dict[str, Any]:
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise LocationLookupError("坐标超出有效范围")
        coordinate_assumption = None
        if system == "auto":
            system = "wgs84"
            coordinate_assumption = "wgs84_default"
        if system == "gcj02":
            gcj_lng, gcj_lat = lng, lat
            wgs_lng, wgs_lat = gcj02_to_wgs84(lng, lat)
        elif system == "wgs84":
            wgs_lng, wgs_lat = lng, lat
            gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
        else:
            raise LocationLookupError("coordinate_system 仅支持 auto、wgs84 或 gcj02")
        label = f"{wgs_lng:.6f}°E · {wgs_lat:.6f}°N"
        location = {
            "id": "coord_" + _point_id(wgs_lng, wgs_lat),
            "display_name": label,
            "city": city,
            "district": "",
            "address": label,
            "wgs84": {"lng": round(wgs_lng, 7), "lat": round(wgs_lat, 7)},
            "gcj02": {"lng": round(gcj_lng, 7), "lat": round(gcj_lat, 7)},
            "source": "coordinate",
            "confidence": 1.0,
            "bounds": None,
            "adcode": None,
        }
        if coordinate_assumption:
            location["coordinate_assumption"] = coordinate_assumption
        return location

    def _location(
        self,
        *,
        location_id: str,
        display_name: str,
        city: str,
        district: str,
        address: str,
        gcj_lng: float,
        gcj_lat: float,
        source: str,
        confidence: float,
        adcode: str | None,
    ) -> dict[str, Any]:
        wgs_lng, wgs_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
        return {
            "id": location_id,
            "display_name": display_name,
            "city": city,
            "district": district,
            "address": address,
            "wgs84": {"lng": wgs_lng, "lat": wgs_lat},
            "gcj02": {"lng": round(gcj_lng, 7), "lat": round(gcj_lat, 7)},
            "source": source,
            "confidence": confidence,
            "bounds": None,
            "adcode": adcode,
        }

    @staticmethod
    def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(candidate)
        for system in ("wgs84", "gcj02"):
            point = candidate[system]
            normalized[system] = {"lng": float(point["lng"]), "lat": float(point["lat"])}
        normalized.setdefault("confidence", 0.8)
        normalized.setdefault("bounds", None)
        normalized.setdefault("source", "candidate")
        return normalized


def _parse_amap_location(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, str) or "," not in value:
        return None
    try:
        lng_text, lat_text = value.split(",", 1)
        lng, lat = float(lng_text), float(lat_text)
    except (TypeError, ValueError):
        return None
    if -180 <= lng <= 180 and -90 <= lat <= 90:
        return lng, lat
    return None


def _normalize_city(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").removesuffix("市")


def _point_id(lng: float, lat: float) -> str:
    return f"{lng:.6f}_{lat:.6f}".replace("-", "m").replace(".", "p")


def _outside_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _gcj_delta(lng: float, lat: float) -> tuple[float, float]:
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * PI
    magic = math.sin(rad_lat)
    magic = 1 - ECCENTRICITY * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((AXIS * (1 - ECCENTRICITY)) / (magic * sqrt_magic) * PI)
    d_lng = (d_lng * 180.0) / (AXIS / sqrt_magic * math.cos(rad_lat) * PI)
    return d_lng, d_lat


def _transform_lat(x: float, y: float) -> float:
    result = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    result += 0.2 * math.sqrt(abs(x))
    result += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    result += (20.0 * math.sin(y * PI) + 40.0 * math.sin(y / 3.0 * PI)) * 2.0 / 3.0
    result += (160.0 * math.sin(y / 12.0 * PI) + 320 * math.sin(y * PI / 30.0)) * 2.0 / 3.0
    return result


def _transform_lng(x: float, y: float) -> float:
    result = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    result += 0.1 * math.sqrt(abs(x))
    result += (20.0 * math.sin(6.0 * x * PI) + 20.0 * math.sin(2.0 * x * PI)) * 2.0 / 3.0
    result += (20.0 * math.sin(x * PI) + 40.0 * math.sin(x / 3.0 * PI)) * 2.0 / 3.0
    result += (150.0 * math.sin(x / 12.0 * PI) + 300.0 * math.sin(x / 30.0 * PI)) * 2.0 / 3.0
    return result
