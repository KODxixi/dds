"""Traceable external research sources.

Search results remain *candidates* until the evidence resolver qualifies their
contents. This prevents a search snippet from silently becoming an observed
fact while still allowing agents to collect sources proactively.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from typing import Any, Callable, Protocol
from urllib.request import Request, urlopen


class SourceUnavailableError(RuntimeError):
    """Raised when a configured research source cannot be used."""


@dataclass(frozen=True, slots=True)
class ResearchQuery:
    query: str
    metric_ids: tuple[str, ...] = ()
    geography: str = ""
    max_results: int = 10


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    source_id: str
    source_ref: str
    title: str
    snippet: str
    source_hash: str
    metric_ids: tuple[str, ...] = ()
    published_at: str = ""
    geography: str = ""
    source_role: str = "public_web_candidate"
    document_status: str = "effective"
    rights_status: str = "public_web"
    sample_size: int | float | None = None
    allowed_uses: tuple[str, ...] = ()
    conflict: bool = False
    qualification_status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResearchResult:
    source_id: str
    query: str
    candidates: tuple[ResearchCandidate, ...] = ()


class ResearchSource(Protocol):
    source_id: str

    def availability(self) -> dict[str, object]: ...

    def search(self, query: ResearchQuery) -> ResearchResult: ...


Transport = Callable[[str, dict[str, str], bytes], bytes]


def _default_transport(url: str, headers: dict[str, str], payload: bytes) -> bytes:
    request = Request(url, data=payload, headers=headers, method="POST")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS provider URL
        return response.read()


def _candidate_hash(value: dict[str, Any]) -> str:
    frozen = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(frozen).hexdigest()


class TavilyResearchSource:
    """Tavily web search adapter with explicit credential availability."""

    source_id = "tavily-web"
    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")
        self.transport = transport or _default_transport

    def availability(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": "web_search_api",
            "available": bool(self.api_key),
            "missing_configuration": [] if self.api_key else ["TAVILY_API_KEY"],
        }

    def search(self, query: ResearchQuery) -> ResearchResult:
        if not self.api_key:
            raise SourceUnavailableError("TAVILY_API_KEY is not configured")
        payload = json.dumps(
            {
                "api_key": self.api_key,
                "query": query.query,
                "search_depth": "advanced",
                "max_results": max(1, min(query.max_results, 20)),
                "include_answer": False,
                "include_raw_content": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        raw = self.transport(
            self.endpoint,
            {"Content-Type": "application/json"},
            payload,
        )
        decoded = json.loads(raw.decode("utf-8"))
        candidates = []
        for item in decoded.get("results", []):
            if not isinstance(item, dict) or not str(item.get("url") or "").startswith("https://"):
                continue
            frozen = {
                "title": str(item.get("title") or ""),
                "url": str(item["url"]),
                "content": str(item.get("content") or ""),
                "published_date": str(item.get("published_date") or ""),
            }
            candidates.append(
                ResearchCandidate(
                    source_id=self.source_id,
                    source_ref=frozen["url"],
                    title=frozen["title"],
                    snippet=frozen["content"],
                    published_at=frozen["published_date"],
                    source_hash=_candidate_hash(frozen),
                    metric_ids=query.metric_ids,
                    geography=query.geography,
                )
            )
        return ResearchResult(self.source_id, query.query, tuple(candidates))


class CuratedListingDatabaseSource:
    """Read-only external database source backed by the V2 curated listings."""

    source_id = "curated-listing-database"

    def __init__(self, settings: Any | None = None) -> None:
        from dds.config import Settings

        self.settings = settings or Settings.from_env()

    def availability(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": "external_database",
            "available": self.settings.datasets_root.exists(),
            "mode": "read_only",
        }

    def search(self, query: ResearchQuery) -> ResearchResult:
        if not self.settings.datasets_root.exists():
            raise SourceUnavailableError("configured curated datasets are unavailable")
        if not query.geography:
            raise SourceUnavailableError("geography is required for listing database research")

        from dds.data.adapters import ListingEvidenceAdapter
        from dds.data.catalog import DatasetCatalog
        from dds.data.repository import CompetitorQuery, DatasetRepository

        bundle = ListingEvidenceAdapter(
            DatasetRepository(DatasetCatalog(self.settings))
        ).collect(CompetitorQuery(city=query.geography.split("/", 1)[0], limit=query.max_results))
        candidates = tuple(
            ResearchCandidate(
                source_id=self.source_id,
                source_ref=item.source_ref,
                title=item.metric_id,
                snippet=f"挂牌价格 {item.value} {item.unit}",
                source_hash=item.source_hash,
                metric_ids=query.metric_ids,
                published_at=str(item.effective_at or ""),
                geography=item.geography,
                source_role="licensed_structured_listing",
                rights_status="licensed_internal_analysis",
            )
            for item in bundle.evidence
        )
        return ResearchResult(self.source_id, query.query, candidates)


def sources_from_environment() -> tuple[ResearchSource, ...]:
    """Return every supported source, including unavailable ones for UI visibility."""

    from .volcengine import VolcengineDataSearchSource

    return (
        CuratedListingDatabaseSource(),
        VolcengineDataSearchSource(),
        TavilyResearchSource(),
    )
