from __future__ import annotations

from dds.contracts import FieldOrigin
from dds.data.fetcher import DataFetcher


async def test_unconfigured_fetcher_preserves_the_missing_gap():
    fetcher = DataFetcher(city="Wuhan")

    assert fetcher.provider_names == ()
    assert await fetcher.fetch("SC2", "competitor_matrix") == (None, None)


async def test_fetcher_rejects_untraceable_output_then_uses_explicit_provider():
    calls: list[str] = []

    async def untraceable_provider(**request):
        calls.append("untraceable")
        return "invented", FieldOrigin(source="real", label="Real")

    async def official_provider(**request):
        calls.append("official")
        assert request["section_id"] == "SC2"
        assert request["city"] == "Wuhan"
        return "verified", FieldOrigin(
            source="official",
            label="Official dataset",
            evidence_refs=["evidence:sc2:competitors"],
            source_ref="dataset://wuhan/listings",
            source_hash="a" * 64,
        )

    fetcher = DataFetcher(
        city="Wuhan",
        providers={
            "untraceable": untraceable_provider,
            "official": official_provider,
        },
    )

    value, origin = await fetcher.fetch("SC2", "competitor_matrix")

    assert calls == ["untraceable", "official"]
    assert value == "verified"
    assert origin is not None
    assert origin.is_evidence_backed
