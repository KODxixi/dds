from __future__ import annotations

import pytest

from dds.config import Settings
from dds.research import (
    LandSource,
    SourceUnavailableError,
    build_source_registry,
)
from dds.research.external import ResearchQuery


def test_build_source_registry_registers_all_sources_default_enabled():
    registry = build_source_registry()
    source_ids = {source.source_id for source in registry.all()}
    assert source_ids == {
        "curated-listing-database",
        "local-land",
        "local-macro",
        "local-transactions",
        "volcengine-data-search",
        "tavily-web",
    }
    assert [source.source_id for source in registry.enabled()] == [
        source.source_id for source in registry.all()
    ]


def test_registry_enabled_excludes_disabled_but_all_keeps_visibility():
    registry = build_source_registry()
    registry.disable("tavily-web")
    assert "tavily-web" not in {s.source_id for s in registry.enabled()}
    assert "tavily-web" in {s.source_id for s in registry.all()}


def test_registry_status_merges_spec_availability_and_enabled():
    registry = build_source_registry()
    by_id = {entry["source_id"]: entry for entry in registry.status()}
    listing = by_id["curated-listing-database"]
    assert listing["label"] == "新房挂牌库"
    assert listing["kind"] == "external_database"
    assert listing["enabled"] is True
    assert "available" in listing


def test_disabled_sources_environment_variable(monkeypatch):
    monkeypatch.setenv("DDS_DISABLED_SOURCES", "tavily-web, local-macro")
    registry = build_source_registry()
    enabled_ids = {source.source_id for source in registry.enabled()}
    assert "tavily-web" not in enabled_ids
    assert "local-macro" not in enabled_ids
    assert "local-land" in enabled_ids


def test_duplicate_registration_is_rejected():
    registry = build_source_registry()
    with pytest.raises(ValueError, match="already registered"):
        registry.register(LandSource())


def test_local_source_empty_geography_is_unavailable():
    with pytest.raises(SourceUnavailableError, match="geography is required"):
        LandSource().search(ResearchQuery(query="土地基准地价"))


def test_local_source_missing_dataset_is_unavailable(tmp_path):
    settings = Settings.from_env(repository_root=tmp_path)
    with pytest.raises(SourceUnavailableError):
        LandSource(settings).search(
            ResearchQuery(query="土地基准地价", geography="武汉")
        )
