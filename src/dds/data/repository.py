"""Deterministic, parameterized access to local DDS datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping, Sequence

from .catalog import DatasetAsset, DatasetCatalog


class RepositoryUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CompetitorQuery:
    city: str
    districts: tuple[str, ...] = ()
    property_types: tuple[str, ...] = ("住宅",)
    sales_statuses: tuple[str, ...] = ("在售", "待售")
    price_min: float | None = None
    price_max: float | None = None
    area_min: float | None = None
    area_max: float | None = None
    target_latitude: float | None = None
    target_longitude: float | None = None
    max_distance_km: float | None = None
    observed_on: date | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if not self.city.strip():
            raise ValueError("city must not be empty")
        if self.limit < 1 or self.limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        for low, high, label in (
            (self.price_min, self.price_max, "price"),
            (self.area_min, self.area_max, "area"),
        ):
            if low is not None and high is not None and low > high:
                raise ValueError(f"{label}_min must not exceed {label}_max")
        coords = (self.target_latitude, self.target_longitude)
        if any(value is not None for value in coords) and not all(
            value is not None for value in coords
        ):
            raise ValueError("target latitude and longitude must be supplied together")
        if self.max_distance_km is not None and not all(
            value is not None for value in coords
        ):
            raise ValueError("max_distance_km requires target coordinates")


@dataclass(frozen=True, slots=True)
class QueryResult:
    records: tuple[Mapping[str, Any], ...]
    dataset: DatasetAsset
    query_signature: str
    filters: Mapping[str, Any]
    exclusions: Mapping[str, int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.records)


_SELECT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("楼盘ID", "project_id"),
    ("楼盘名称", "project_name"),
    ("城市名称", "city"),
    ("区域名称", "district"),
    ("子区域名称", "subdistrict"),
    ("地址", "address"),
    ("最新价格", "latest_price_raw"),
    ("参考价格", "reference_price_raw"),
    ("面积范围", "area_range_raw"),
    ("销售状态", "sales_status"),
    ("物业类型", "property_type"),
    ("房地产类型", "real_estate_type"),
    ("商品类型", "product_type"),
    ("百度地图纬度", "latitude_raw"),
    ("百度地图经度", "longitude_raw"),
    ("开发商", "developer"),
    ("开发商品牌", "developer_brand"),
    ("容积率", "floor_area_ratio_raw"),
    ("装修情况", "decoration"),
    ("开盘日期", "opening_date_raw"),
    ("主力成交户型", "primary_unit_raw"),
    ("客群画像", "audience_profile_raw"),
    ("去化周期", "sell_through_raw"),
    ("综合评分", "rating_raw"),
    ("默认图片", "image_url"),
)


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _area_bounds(raw: Any) -> tuple[float | None, float | None]:
    if raw is None:
        return None, None
    values = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(raw))]
    if not values:
        return None, None
    return min(values), max(values)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _signature(asset: DatasetAsset, filters: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"source_hash": asset.source_hash, "filters": filters},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class DatasetRepository:
    """Read local Parquet/CSV sources using fixed identifiers and bound values."""

    def __init__(self, catalog: DatasetCatalog | None = None) -> None:
        self.catalog = catalog or DatasetCatalog()

    @staticmethod
    def _connect() -> Any:
        try:
            import duckdb  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on runtime extras
            raise RepositoryUnavailableError(
                "DuckDB is required for local dataset queries; install dds[data]"
            ) from exc
        return duckdb.connect(database=":memory:")

    def query_competitors(self, query: CompetitorQuery) -> QueryResult:
        asset = self.catalog.resolve_new_home_listings(query.city)
        filters = {
            "city": query.city,
            "districts": list(query.districts),
            "property_types": list(query.property_types),
            "sales_statuses": list(query.sales_statuses),
            "price_min": query.price_min,
            "price_max": query.price_max,
            "area_min": query.area_min,
            "area_max": query.area_max,
            "target_latitude": query.target_latitude,
            "target_longitude": query.target_longitude,
            "max_distance_km": query.max_distance_km,
            "observed_on": query.observed_on.isoformat() if query.observed_on else None,
            "limit": query.limit,
        }

        select_clause = ", ".join(
            f'"{source}" AS "{target}"' for source, target in _SELECT_COLUMNS
        )
        relation = "read_parquet(?)" if asset.format == "parquet" else "read_csv_auto(?)"
        clauses = ['"城市名称" = ?']
        params: list[Any] = [str(asset.path), query.city]

        def add_in(column: str, values: Sequence[str]) -> None:
            if values:
                placeholders = ",".join("?" for _ in values)
                clauses.append(f'"{column}" IN ({placeholders})')
                params.extend(values)

        if query.districts:
            placeholders = ",".join("?" for _ in query.districts)
            clauses.append(
                f'("区域名称" IN ({placeholders}) OR '
                f'"子区域名称" IN ({placeholders}))'
            )
            params.extend(query.districts)
            params.extend(query.districts)
        add_in("物业类型", query.property_types)
        add_in("销售状态", query.sales_statuses)

        sql = (
            f"SELECT {select_clause} FROM {relation} "
            f"WHERE {' AND '.join(clauses)} ORDER BY \"楼盘ID\" LIMIT ?"
        )
        # Pull a bounded superset so Python can apply normalized numeric and
        # geospatial filters without interpolating expressions into SQL.
        fetch_limit = min(max(query.limit * 20, query.limit), 10_000)
        params.append(fetch_limit)
        connection = self._connect()
        try:
            cursor = connection.execute(sql, params)
            names = [item[0] for item in cursor.description]
            raw_records = [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            connection.close()

        exclusions = {
            "missing_price": 0,
            "outside_price_band": 0,
            "outside_area_band": 0,
            "missing_coordinates": 0,
            "outside_distance": 0,
        }
        accepted: list[dict[str, Any]] = []
        for record in raw_records:
            price = _numeric(record.get("latest_price_raw")) or _numeric(
                record.get("reference_price_raw")
            )
            if price is None and (query.price_min is not None or query.price_max is not None):
                exclusions["missing_price"] += 1
                continue
            if query.price_min is not None and price is not None and price < query.price_min:
                exclusions["outside_price_band"] += 1
                continue
            if query.price_max is not None and price is not None and price > query.price_max:
                exclusions["outside_price_band"] += 1
                continue

            area_low, area_high = _area_bounds(record.get("area_range_raw"))
            if query.area_min is not None and (
                area_high is None or area_high < query.area_min
            ):
                exclusions["outside_area_band"] += 1
                continue
            if query.area_max is not None and (
                area_low is None or area_low > query.area_max
            ):
                exclusions["outside_area_band"] += 1
                continue

            distance: float | None = None
            if query.target_latitude is not None and query.target_longitude is not None:
                latitude = _numeric(record.get("latitude_raw"))
                longitude = _numeric(record.get("longitude_raw"))
                if latitude is None or longitude is None:
                    exclusions["missing_coordinates"] += 1
                    continue
                distance = _haversine_km(
                    query.target_latitude,
                    query.target_longitude,
                    latitude,
                    longitude,
                )
                if query.max_distance_km is not None and distance > query.max_distance_km:
                    exclusions["outside_distance"] += 1
                    continue

            record["price"] = price
            record["area_min"] = area_low
            record["area_max"] = area_high
            record["distance_km"] = round(distance, 3) if distance is not None else None
            record["source_ref"] = asset.source_ref
            record["source_hash"] = asset.source_hash
            record["source_authority"] = asset.authority
            accepted.append(record)
            if len(accepted) >= query.limit:
                break

        return QueryResult(
            records=tuple(accepted),
            dataset=asset,
            query_signature=_signature(asset, filters),
            filters=filters,
            exclusions={key: value for key, value in exclusions.items() if value},
        )


__all__ = [
    "CompetitorQuery",
    "DatasetRepository",
    "QueryResult",
    "RepositoryUnavailableError",
]
