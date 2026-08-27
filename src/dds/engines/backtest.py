"""Price-band backtest via leave-one-out spatial cross-validation.

Ground truth is the local listing/reference price of real comparable listings
(挂牌/参考价, not 网签成交). For every point, the median and mean of the
points within ``radius_km`` are used as an estimate, then compared against the
point's own price. The result measures whether the report's area price estimate
would be close to what the local market actually lists.

The computation is pure and deterministic: it takes points as ``(price,
latitude, longitude)`` tuples so that any data source (the curated listing
repository, a fixture, or an external feed) can drive it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance between two WGS-84 points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(delta_lambda / 2) ** 2
    )
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


@dataclass(frozen=True, slots=True)
class BacktestRecord:
    """One point's leave-one-out comparison result."""

    ape_median: float
    ape_mean: float
    within_band: bool
    within_comps_range: bool
    comp_count: int


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    """Aggregated error and coverage metrics across all backtested points."""

    sample_count: int
    mape_median_pct: float
    mape_mean_pct: float
    median_ape_pct: float
    band_coverage_pct: float
    minmax_coverage_pct: float
    median_comps: int
    band80_pct: float
    band90_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "mape_median_pct": self.mape_median_pct,
            "mape_mean_pct": self.mape_mean_pct,
            "median_ape_pct": self.median_ape_pct,
            "band_coverage_pct": self.band_coverage_pct,
            "minmax_coverage_pct": self.minmax_coverage_pct,
            "median_comps": self.median_comps,
            "band80_pct": self.band80_pct,
            "band90_pct": self.band90_pct,
        }


def backtest_points(
    points: Iterable[Sequence[float] | tuple[float, float, float]],
    *,
    radius_km: float = 5.0,
    min_comps: int = 5,
    band: float = 0.15,
) -> list[BacktestRecord]:
    """Leave-one-out: estimate each point from its neighbours within the radius.

    ``points`` is an iterable of ``(price, latitude, longitude)`` triples.
    Points with fewer than ``min_comps`` neighbours are silently dropped, since
    their estimate would not be meaningful.
    """
    rows = [tuple(float(v) for v in row) for row in points]
    lat_window = radius_km / 111.0  # bounding-box prefilter to avoid O(n^2) haversine
    records: list[BacktestRecord] = []
    for i, (price_i, lat_i, lon_i) in enumerate(rows):
        lon_window = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat_i))))
        comps: list[float] = []
        for j, (price_j, lat_j, lon_j) in enumerate(rows):
            if j == i:
                continue
            if abs(lat_j - lat_i) > lat_window or abs(lon_j - lon_i) > lon_window:
                continue
            if haversine_km(lon_i, lat_i, lon_j, lat_j) <= radius_km:
                comps.append(price_j)
        if len(comps) < min_comps:
            continue
        estimate_median = statistics.median(comps)
        estimate_mean = sum(comps) / len(comps)
        records.append(
            BacktestRecord(
                ape_median=abs(estimate_median - price_i) / price_i,
                ape_mean=abs(estimate_mean - price_i) / price_i,
                within_band=estimate_median * (1 - band)
                <= price_i
                <= estimate_median * (1 + band),
                within_comps_range=min(comps) <= price_i <= max(comps),
                comp_count=len(comps),
            )
        )
    return records


def aggregate(records: Sequence[BacktestRecord]) -> BacktestSummary | None:
    """Aggregate per-point records into summary error/coverage metrics."""
    if not records:
        return None
    count = len(records)
    sorted_ape = sorted(record.ape_median for record in records)

    def percentile(fraction: float) -> float:
        return sorted_ape[min(count - 1, int(fraction * count))]

    return BacktestSummary(
        sample_count=count,
        mape_median_pct=round(
            sum(record.ape_median for record in records) / count * 100, 1
        ),
        mape_mean_pct=round(
            sum(record.ape_mean for record in records) / count * 100, 1
        ),
        median_ape_pct=round(
            statistics.median(record.ape_median for record in records) * 100, 1
        ),
        band_coverage_pct=round(
            sum(record.within_band for record in records) / count * 100, 1
        ),
        minmax_coverage_pct=round(
            sum(record.within_comps_range for record in records) / count * 100, 1
        ),
        median_comps=round(
            statistics.median(record.comp_count for record in records)
        ),
        band80_pct=round(percentile(0.8) * 100, 1),
        band90_pct=round(percentile(0.9) * 100, 1),
    )


__all__ = [
    "BacktestRecord",
    "BacktestSummary",
    "aggregate",
    "backtest_points",
    "haversine_km",
]
