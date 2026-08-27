"""Volcengine structured-data research source with privacy-minimized candidates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from hashlib import sha256
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .external import (
    ResearchCandidate,
    ResearchQuery,
    ResearchResult,
    SourceSpec,
    SourceUnavailableError,
)


DEFAULT_GATEWAY_URL = (
    "https://sd6k08f59gqcea6qe13vg.apigateway-cn-beijing.volceapi.com/mcp"
)
DEFAULT_DATASOURCE_ID = "industry_chain_company_info"
SAFE_FIELDS = (
    "company_id",
    "company_name",
    "chain_id",
    "chain_name",
    "node_id",
    "node_name",
    "region_city_name",
    "reg_status",
)

GatewayTransport = Callable[[str, dict[str, str], bytes], bytes]


def _default_transport(url: str, headers: dict[str, str], payload: bytes) -> bytes:
    request = Request(url, data=payload, headers=headers, method="POST")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS endpoint
        return response.read()


def _credential(primary: str, alias: str) -> str:
    return os.getenv(primary) or os.getenv(alias) or ""


def _stable_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class VolcengineGatewayClient:
    """Minimal JSON-RPC client for the approved high-quality dataset gateway."""

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        *,
        transport: GatewayTransport | None = None,
        endpoint: str = DEFAULT_GATEWAY_URL,
    ) -> None:
        self.access_key = (
            access_key
            if access_key is not None
            else _credential("VOLCENGINE_ACCESS_KEY", "VOLC_ACCESS_KEY")
        )
        self.secret_key = (
            secret_key
            if secret_key is not None
            else _credential("VOLCENGINE_SECRET_KEY", "VOLC_SECRET_KEY")
        )
        self.transport = transport or _default_transport
        self.endpoint = endpoint

    @property
    def configured(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def describe_datasource(self, datasource_id: str = "all") -> dict[str, Any]:
        return self._call(
            "describe_datasource",
            {"datasource_id": datasource_id, "locale": "zh-CN"},
        )

    def query_datasource(
        self,
        datasource_id: str,
        *,
        filters: str,
        page: int = 1,
        select_fields: tuple[str, ...] = SAFE_FIELDS,
    ) -> dict[str, Any]:
        if page < 1:
            raise ValueError("page must be at least 1")
        description = self.describe_datasource(datasource_id)
        dimensions = description.get("dimensions")
        if not isinstance(dimensions, list):
            raise SourceUnavailableError(
                f"datasource {datasource_id!r} did not return a field description"
            )
        known_fields = {
            str(item.get("field"))
            for item in dimensions
            if isinstance(item, Mapping) and item.get("field")
        }
        unknown_fields = sorted(set(select_fields) - known_fields)
        if unknown_fields:
            raise SourceUnavailableError(
                f"datasource {datasource_id!r} is missing expected fields: {unknown_fields}"
            )
        return self._call(
            "query_datasource",
            {
                "datasource_id": datasource_id,
                "select_fields": ",".join(select_fields),
                "filters": filters,
                "page": page,
            },
        )

    def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise SourceUnavailableError("Volcengine AK/SK are not configured")
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            raw = self.transport(
                self.endpoint,
                {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Volc-Access-Key": self.access_key,
                    "Volc-Secret-Key": self.secret_key,
                },
                payload,
            )
            response = json.loads(raw.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceUnavailableError(f"Volcengine gateway request failed: {exc}") from exc
        if response.get("error"):
            raise SourceUnavailableError(
                f"Volcengine gateway rejected {tool_name}: {response['error']}"
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise SourceUnavailableError("Volcengine gateway returned no result")
        content = result.get("content")
        if not isinstance(content, list):
            raise SourceUnavailableError("Volcengine gateway returned no content")
        texts = [
            item.get("text")
            for item in content
            if isinstance(item, Mapping) and isinstance(item.get("text"), str)
        ]
        if not texts:
            raise SourceUnavailableError("Volcengine gateway returned no text content")
        try:
            decoded = json.loads("\n".join(texts))
        except json.JSONDecodeError as exc:
            raise SourceUnavailableError("Volcengine gateway returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise SourceUnavailableError("Volcengine tool result must be an object")
        return decoded


class VolcengineDataSearchSource:
    """City-level industry candidates from Volcengine public structured data."""

    source_id = "volcengine-data-search"
    spec = SourceSpec(
        source_id="volcengine-data-search",
        kind="structured_public_data_api",
        label="火山引擎产业链数据",
        capabilities=("web",),
        geography_scope="全国城市",
        access_level="read_only",
        rights_status="provider_terms_internal_analysis",
        requires_config=("VOLCENGINE_ACCESS_KEY", "VOLCENGINE_SECRET_KEY"),
    )

    def __init__(self, client: VolcengineGatewayClient | None = None) -> None:
        self.client = client or VolcengineGatewayClient()

    def availability(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": "structured_public_data_api",
            "available": self.client.configured,
            "missing_configuration": (
                []
                if self.client.configured
                else ["VOLCENGINE_ACCESS_KEY", "VOLCENGINE_SECRET_KEY"]
            ),
            "privacy_mode": "allowlist",
        }

    def search(self, query: ResearchQuery) -> ResearchResult:
        city = query.geography.split("/", 1)[0].strip()
        if not city:
            raise SourceUnavailableError("geography is required for Volcengine research")
        response = self.client.query_datasource(
            DEFAULT_DATASOURCE_ID,
            filters=f"region_city_name:like:{city}",
        )
        metadata = response.get("metadata")
        updated_at = (
            str(metadata.get("data_updated_at") or "")
            if isinstance(metadata, Mapping)
            else ""
        )
        rows = response.get("data")
        candidates: list[ResearchCandidate] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or not row.get("company_id"):
                continue
            safe = {field: row.get(field) for field in SAFE_FIELDS}
            company_id = quote(str(safe["company_id"]), safe="")
            chain_id = quote(str(safe.get("chain_id") or "unknown"), safe="")
            node_id = quote(str(safe.get("node_id") or "unknown"), safe="")
            source_ref = (
                f"volcengine://{DEFAULT_DATASOURCE_ID}/company/{company_id}"
                f"?chain_id={chain_id}&node_id={node_id}"
            )
            snippet = "；".join(
                part
                for part in (
                    f"企业：{safe.get('company_name') or ''}",
                    f"产业链：{safe.get('chain_name') or ''}",
                    f"节点：{safe.get('node_name') or ''}",
                    f"城市：{safe.get('region_city_name') or ''}",
                    f"状态：{safe.get('reg_status') or ''}",
                )
                if not part.endswith("：")
            )
            candidates.append(
                ResearchCandidate(
                    source_id=self.source_id,
                    source_ref=source_ref,
                    title=str(safe.get("company_name") or "产业链企业"),
                    snippet=snippet,
                    source_hash=_stable_hash(
                        {
                            "datasource_id": DEFAULT_DATASOURCE_ID,
                            "record": safe,
                            "data_updated_at": updated_at,
                        }
                    ),
                    metric_ids=query.metric_ids,
                    published_at=updated_at,
                    geography=query.geography,
                    source_role="public_structured_company_data",
                    rights_status="provider_terms_internal_analysis",
                )
            )
            if len(candidates) >= max(1, min(query.max_results, 10)):
                break
        return ResearchResult(self.source_id, query.query, tuple(candidates))


__all__ = [
    "DEFAULT_DATASOURCE_ID",
    "DEFAULT_GATEWAY_URL",
    "SAFE_FIELDS",
    "VolcengineDataSearchSource",
    "VolcengineGatewayClient",
]
