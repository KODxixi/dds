"""Read-only local dataset sources for the pluggable registry.

The land, macro and transaction Parquet libraries were previously orphaned
adapters reachable only through bespoke imports.  Wrapping them as
:class:`ResearchSource` implementations lets agents schedule them through the
same unified ``search`` path as the external sources.
"""

from __future__ import annotations

from typing import Any

from dds.config import Settings

from .external import (
    ResearchCandidate,
    ResearchQuery,
    ResearchResult,
    SourceSpec,
    SourceUnavailableError,
)


def _city_from(query: ResearchQuery) -> str:
    city = query.geography.split("/", 1)[0].strip()
    if not city:
        raise SourceUnavailableError("geography is required for local dataset sources")
    return city


def _candidate(
    source_id: str,
    query: ResearchQuery,
    item: Any,
    *,
    snippet_prefix: str,
    source_role: str,
    rights_status: str,
) -> ResearchCandidate:
    return ResearchCandidate(
        source_id=source_id,
        source_ref=item.source_ref,
        title=item.metric_id,
        snippet=f"{snippet_prefix} {item.value} {item.unit}",
        source_hash=item.source_hash,
        metric_ids=query.metric_ids,
        published_at=str(item.effective_at or ""),
        geography=item.geography,
        source_role=source_role,
        rights_status=rights_status,
    )


class LandSource:
    """Official residential land benchmark prices, per configured city."""

    source_id = "local-land"
    spec = SourceSpec(
        source_id="local-land",
        kind="local_parquet",
        label="基准地价库",
        capabilities=("land",),
        geography_scope="配置城市",
        access_level="read_only",
        rights_status="official_public_data",
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def availability(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": self.spec.kind,
            "available": self.settings.datasets_root.exists(),
            "missing_configuration": [],
        }

    def search(self, query: ResearchQuery) -> ResearchResult:
        from dds.data.adapters import LandAdapter

        try:
            bundle = LandAdapter(self.settings).collect(_city_from(query))
        except FileNotFoundError as exc:
            raise SourceUnavailableError(str(exc)) from exc
        candidates = tuple(
            _candidate(
                self.source_id,
                query,
                item,
                snippet_prefix="基准地价",
                source_role="official_land_benchmark",
                rights_status="official_public_data",
            )
            for item in bundle.evidence
        )
        return ResearchResult(self.source_id, query.query, candidates)


class MacroSource:
    """Official statistical indicators, per configured city."""

    source_id = "local-macro"
    spec = SourceSpec(
        source_id="local-macro",
        kind="local_parquet",
        label="宏观统计库",
        capabilities=("macro",),
        geography_scope="配置城市",
        access_level="read_only",
        rights_status="official_public_data",
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def availability(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": self.spec.kind,
            "available": self.settings.datasets_root.exists(),
            "missing_configuration": [],
        }

    def search(self, query: ResearchQuery) -> ResearchResult:
        from dds.data.adapters import MacroAdapter

        try:
            bundle = MacroAdapter(self.settings).collect(
                _city_from(query),
                limit_per_source=max(1, query.max_results),
            )
        except FileNotFoundError as exc:
            raise SourceUnavailableError(str(exc)) from exc
        candidates = tuple(
            _candidate(
                self.source_id,
                query,
                item,
                snippet_prefix="宏观指标",
                source_role="official_statistical_release",
                rights_status="official_public_data",
            )
            for item in bundle.evidence
        )
        return ResearchResult(self.source_id, query.query, candidates)


class TransactionSource:
    """Purchased transaction-row archive, never merged into listing rows."""

    source_id = "local-transactions"
    spec = SourceSpec(
        source_id="local-transactions",
        kind="local_parquet",
        label="成交备案库",
        capabilities=("transactions",),
        geography_scope="配置城市",
        access_level="read_only",
        rights_status="licensed_internal_analysis",
    )

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def availability(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "kind": self.spec.kind,
            "available": self.settings.datasets_root.exists(),
            "missing_configuration": [],
        }

    def search(self, query: ResearchQuery) -> ResearchResult:
        from dds.data.adapters import TransactionAdapter

        try:
            bundle = TransactionAdapter(self.settings).collect(
                _city_from(query),
                limit=max(1, query.max_results),
            )
        except FileNotFoundError as exc:
            raise SourceUnavailableError(str(exc)) from exc
        candidates = tuple(
            _candidate(
                self.source_id,
                query,
                item,
                snippet_prefix="成交均价",
                source_role="purchased_transaction_archive",
                rights_status="licensed_internal_analysis",
            )
            for item in bundle.evidence
        )
        return ResearchResult(self.source_id, query.query, candidates)


__all__ = [
    "LandSource",
    "MacroSource",
    "TransactionSource",
]
