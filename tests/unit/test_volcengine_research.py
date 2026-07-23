from __future__ import annotations

import json

import pytest

from dds.research import ResearchQuery, SourceUnavailableError
from dds.research.volcengine import (
    SAFE_FIELDS,
    VolcengineDataSearchSource,
    VolcengineGatewayClient,
)


def _rpc_result(value: dict[str, object]) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(value)}]},
        }
    ).encode()


def test_source_describes_schema_before_query_and_discards_sensitive_fields():
    calls: list[dict[str, object]] = []

    def transport(url: str, headers: dict[str, str], payload: bytes) -> bytes:
        request = json.loads(payload)
        calls.append(request)
        assert headers["Volc-Access-Key"] == "access"
        assert headers["Volc-Secret-Key"] == "secret"
        tool = request["params"]["name"]
        if tool == "describe_datasource":
            return _rpc_result(
                {
                    "dimensions": [
                        {"field": field, "type": "text", "filterable": True}
                        for field in SAFE_FIELDS
                    ]
                }
            )
        assert request["params"]["arguments"]["filters"] == (
            "region_city_name:like:武汉"
        )
        return _rpc_result(
            {
                "data": [
                    {
                        "company_id": 42,
                        "company_name": "武汉示例企业",
                        "chain_id": 7,
                        "chain_name": "智能家居",
                        "node_id": 8,
                        "node_name": "住宅建设",
                        "region_city_name": "武汉市",
                        "reg_status": "在业/存续",
                        "legal_person_name": "不得保留",
                        "email": "private@example.com",
                    }
                ],
                "metadata": {"data_updated_at": "2026-07-22T00:00:00Z"},
            }
        )

    source = VolcengineDataSearchSource(
        VolcengineGatewayClient(
            "access",
            "secret",
            transport=transport,
        )
    )
    result = source.search(
        ResearchQuery(
            query="武汉住宅产业",
            geography="武汉/武昌区",
            metric_ids=("SC3.industry",),
            max_results=5,
        )
    )

    assert [call["params"]["name"] for call in calls] == [
        "describe_datasource",
        "query_datasource",
    ]
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.source_ref == (
        "volcengine://industry_chain_company_info/company/42?chain_id=7&node_id=8"
    )
    assert candidate.metric_ids == ("SC3.industry",)
    assert candidate.source_hash
    assert "不得保留" not in candidate.snippet
    assert "private@example.com" not in candidate.snippet


def test_source_is_explicitly_unavailable_without_credentials():
    source = VolcengineDataSearchSource(
        VolcengineGatewayClient("", "", transport=lambda *_: b"")
    )

    assert source.availability()["available"] is False
    with pytest.raises(SourceUnavailableError, match="AK/SK"):
        source.search(ResearchQuery(query="test", geography="武汉"))


def test_client_rejects_schema_drift_before_query():
    def transport(url: str, headers: dict[str, str], payload: bytes) -> bytes:
        return _rpc_result({"dimensions": [{"field": "company_id"}]})

    client = VolcengineGatewayClient("access", "secret", transport=transport)

    with pytest.raises(SourceUnavailableError, match="missing expected fields"):
        client.query_datasource(
            "industry_chain_company_info",
            filters="region_city_name:like:武汉",
        )
