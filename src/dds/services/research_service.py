"""Research orchestration that freezes query facts before prose generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from dds.data.adapters import ListingEvidenceAdapter
from dds.data.evidence_store import EvidenceStore, FrozenEvidencePackage
from dds.data.repository import CompetitorQuery, QueryResult
from dds.domain import EvidenceRecord, ProjectContext
from dds.engines.market import MarketAnalysisResult, MarketEngine


class EvidenceResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MarketResearchBundle:
    query_result: QueryResult
    analysis: MarketAnalysisResult
    evidence: tuple[EvidenceRecord, ...]

    def query_manifest(self) -> dict[str, Any]:
        return {
            "query_signature": self.query_result.query_signature,
            "filters": dict(self.query_result.filters),
            "exclusions": dict(self.analysis.excluded),
            "effective_sample_size": self.analysis.effective_sample_size,
            "dataset": asdict(self.query_result.dataset),
        }


class ResearchService:
    """Collector + deterministic resolver; no report language is produced here."""

    def __init__(
        self,
        adapter: ListingEvidenceAdapter | None = None,
        market_engine: MarketEngine | None = None,
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        self.adapter = adapter or ListingEvidenceAdapter()
        self.market_engine = market_engine or MarketEngine()
        self.evidence_store = evidence_store or EvidenceStore()

    def research_market(
        self,
        query: CompetitorQuery,
        *,
        target_price: float | None = None,
        target_area_min: float | None = None,
        target_area_max: float | None = None,
    ) -> MarketResearchBundle:
        collected = self.adapter.collect(query)
        analysis = self.market_engine.analyze(
            collected.query_result,
            target_price=target_price,
            target_area_min=target_area_min,
            target_area_max=target_area_max,
        )
        by_id = {item.evidence_id: item for item in collected.evidence}
        missing = [ref for ref in analysis.evidence_refs if ref not in by_id]
        if missing:
            raise EvidenceResolutionError(
                f"market analysis referenced evidence that was not collected: {missing}"
            )
        selected_evidence = tuple(by_id[ref] for ref in analysis.evidence_refs)
        return MarketResearchBundle(
            query_result=collected.query_result,
            analysis=analysis,
            evidence=selected_evidence,
        )

    def freeze_market(
        self,
        project_context: ProjectContext,
        bundle: MarketResearchBundle,
    ) -> FrozenEvidencePackage:
        project_id = project_context.project_id.strip()
        if not project_id:
            raise ValueError("project_context.project_id is required before freezing")
        return self.evidence_store.freeze(
            project_id,
            project_context=project_context,
            evidence=bundle.evidence,
            query_manifests=(bundle.query_manifest(),),
            metadata={
                "slice": "SC2-market",
                "readiness": bundle.analysis.readiness.value,
            },
        )


__all__ = [
    "EvidenceResolutionError",
    "MarketResearchBundle",
    "ResearchService",
]
