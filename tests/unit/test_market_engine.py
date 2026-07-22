from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dds.data.catalog import DatasetAsset, DatasetKind
from dds.data.repository import QueryResult
from dds.engines.market import MarketEngine, MarketReadiness


def make_query_result(count: int, *, mixed: bool = False, duplicate: bool = False):
    asset = DatasetAsset(
        kind=DatasetKind.NEW_HOME_LISTINGS,
        path=Path("fixture.parquet").resolve(),
        city="武汉",
        format="parquet",
        size_bytes=1,
        modified_at=datetime(2026, 6, 25, tzinfo=timezone.utc).isoformat(),
        source_hash="f" * 64,
        authority="fixture",
    )
    records = []
    for index in range(count):
        records.append(
            {
                "project_id": str(index),
                "project_name": f"P{index}",
                "district": "武昌",
                "subdistrict": "中北路",
                "sales_status": "在售",
                "property_type": "住宅",
                "price": 20_000 + index * 1_000,
                "area_min": 90,
                "area_max": 140,
                "distance_km": float(index),
                "source_ref": asset.source_ref,
                "source_hash": asset.source_hash,
                "market_observation_type": "new_home_listing",
            }
        )
    if mixed:
        records.append(
            {
                **records[0],
                "project_id": "transaction",
                "market_observation_type": "transaction",
            }
        )
    if duplicate:
        records.append(dict(records[0]))
    return QueryResult(
        records=tuple(records),
        dataset=asset,
        query_signature="q1",
        filters={"city": "武汉", "districts": ["武昌"], "observed_on": None},
    )


@pytest.mark.parametrize(
    ("count", "expected", "has_price"),
    [
        (2, MarketReadiness.INSUFFICIENT, False),
        (3, MarketReadiness.DEGRADED, True),
        (4, MarketReadiness.DEGRADED, True),
        (5, MarketReadiness.READY, True),
    ],
)
def test_market_sample_gates(count, expected, has_price):
    query = make_query_result(count)
    result = MarketEngine().analyze(query)
    assert result.readiness is expected
    assert (result.price_median is not None) is has_price
    assert result.effective_sample_size == count
    assert result.screening_rules == query.filters
    assert result.screening_rules is not query.filters
    assert result.data_time_coverage == {
        "dataset_modified_at": query.dataset.modified_at,
        "observed_on": None,
    }


def test_market_refuses_mixed_observation_and_duplicate_source():
    result = MarketEngine().analyze(
        make_query_result(5, mixed=True, duplicate=True)
    )
    assert result.readiness is MarketReadiness.READY
    assert result.effective_sample_size == 5
    assert result.excluded["wrong_observation_type"] == 1
    assert result.excluded["duplicate_source_record"] == 1
    assert len(result.evidence_refs) == 5


def test_market_missing_source_is_not_a_valid_competitor():
    query = make_query_result(5)
    records = list(query.records)
    records[0] = {**records[0], "source_ref": ""}
    result = MarketEngine().analyze(
        QueryResult(
            records=tuple(records),
            dataset=query.dataset,
            query_signature=query.query_signature,
            filters=query.filters,
        )
    )
    assert result.readiness is MarketReadiness.DEGRADED
    assert result.excluded["missing_source"] == 1
