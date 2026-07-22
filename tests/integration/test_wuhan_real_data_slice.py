from __future__ import annotations

import pytest

from dds.config import Settings
from dds.data.catalog import DatasetCatalog, DatasetNotFoundError
from dds.data.repository import CompetitorQuery, DatasetRepository
from dds.engines.market import MarketEngine, MarketReadiness


@pytest.mark.integration
def test_wuhan_listing_snapshot_supports_sc2_real_slice():
    settings = Settings.from_env()
    catalog = DatasetCatalog(settings)
    try:
        asset = catalog.resolve_new_home_listings("武汉")
    except DatasetNotFoundError:
        pytest.skip("configured read-only V1 Wuhan dataset is unavailable")
    result = DatasetRepository(catalog).query_competitors(
        CompetitorQuery(city="武汉", limit=20)
    )
    analysis = MarketEngine(
        settings.minimum_competitors, settings.degraded_competitors
    ).analyze(result)
    assert asset.source_hash
    assert analysis.readiness is MarketReadiness.READY
    assert analysis.effective_sample_size >= 5
    assert all(item.source_hash == asset.source_hash for item in analysis.selected)
    assert len(analysis.evidence_refs) == analysis.effective_sample_size
