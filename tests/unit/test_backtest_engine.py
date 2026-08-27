from __future__ import annotations

import pytest

from dds.engines.backtest import (
    BacktestRecord,
    aggregate,
    backtest_points,
    haversine_km,
)


def test_haversine_same_point_is_zero():
    assert haversine_km(120.0, 30.0, 120.0, 30.0) == pytest.approx(0.0, abs=1e-6)


def test_haversine_one_degree_latitude_is_about_111km():
    # 1 degree of latitude is ~111.19 km on the reference sphere.
    distance = haversine_km(120.0, 30.0, 120.0, 31.0)
    assert distance == pytest.approx(111.19, abs=1.0)


def test_backtest_equal_prices_zero_error_and_far_outlier_excluded():
    points = [
        (100.0, 30.0, 120.0),
        (100.0, 30.001, 120.0),
        (100.0, 30.0, 120.001),
        (100.0, 30.002, 120.0),
        (100.0, 30.0, 120.002),
        (500.0, 45.0, 100.0),  # >1000km away, no comps within radius
    ]
    records = backtest_points(points, radius_km=5.0, min_comps=4, band=0.15)
    # The far outlier is dropped (insufficient comps); the 5 clustered remain.
    assert len(records) == 5
    assert all(record.comp_count >= 4 for record in records)
    # All prices equal within the cluster -> zero error and full coverage.
    assert all(record.ape_median == 0.0 for record in records)
    assert all(record.within_band for record in records)
    assert all(record.within_comps_range for record in records)


def test_backtest_respects_min_comps():
    points = [
        (100.0, 30.0, 120.0),
        (110.0, 30.0, 120.0),
    ]
    records = backtest_points(points, radius_km=5.0, min_comps=5, band=0.15)
    assert records == []


def test_aggregate_metrics():
    records = [
        BacktestRecord(
            ape_median=0.10, ape_mean=0.12,
            within_band=True, within_comps_range=True, comp_count=5,
        ),
        BacktestRecord(
            ape_median=0.20, ape_mean=0.18,
            within_band=False, within_comps_range=True, comp_count=6,
        ),
        BacktestRecord(
            ape_median=0.30, ape_mean=0.35,
            within_band=False, within_comps_range=False, comp_count=4,
        ),
    ]
    summary = aggregate(records)
    assert summary is not None
    assert summary.sample_count == 3
    assert summary.mape_median_pct == 20.0  # (0.10 + 0.20 + 0.30) / 3 * 100
    assert summary.mape_mean_pct == 21.7  # (0.12 + 0.18 + 0.35) / 3 * 100
    assert summary.median_ape_pct == 20.0  # median of [0.10, 0.20, 0.30]
    assert summary.band_coverage_pct == 33.3  # 1 / 3
    assert summary.minmax_coverage_pct == 66.7  # 2 / 3
    assert summary.median_comps == 5
    # band80/band90 = APE at p80/p90 -> both 0.30 (n=3) -> 30.0
    assert summary.band80_pct == 30.0
    assert summary.band90_pct == 30.0


def test_aggregate_empty_is_none():
    assert aggregate([]) is None
