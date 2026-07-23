from __future__ import annotations

import json

import pytest

from dds.research.external import (
    ResearchQuery,
    SourceUnavailableError,
    TavilyResearchSource,
)


def test_tavily_source_calls_remote_api_and_freezes_traceable_candidates():
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], payload: bytes) -> bytes:
        captured.update(url=url, headers=headers, payload=json.loads(payload))
        return json.dumps(
            {
                "results": [
                    {
                        "title": "武汉市住房发展年度报告",
                        "url": "https://example.gov.cn/report/2026",
                        "content": "公开报告摘要",
                        "published_date": "2026-06-30",
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

    source = TavilyResearchSource(api_key="secret", transport=transport)
    result = source.search(
        ResearchQuery(
            query="武汉 新房 市场 2026",
            metric_ids=("SC2.competitors",),
            geography="武汉",
        )
    )

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"] == {"Content-Type": "application/json"}
    assert captured["payload"]["api_key"] == "secret"
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source_ref == "https://example.gov.cn/report/2026"
    assert candidate.source_hash
    assert candidate.qualification_status == "candidate"
    assert candidate.metric_ids == ("SC2.competitors",)


def test_tavily_source_without_key_is_explicitly_unavailable():
    with pytest.raises(SourceUnavailableError, match="TAVILY_API_KEY"):
        TavilyResearchSource(api_key="").search(ResearchQuery(query="test"))
