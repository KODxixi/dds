"""Read-only adapter for the V1 new-home listing snapshot."""

from __future__ import annotations

from dataclasses import dataclass

from dds.domain import EvidenceRecord, EvidenceType

from ..repository import CompetitorQuery, DatasetRepository, QueryResult


@dataclass(frozen=True, slots=True)
class ListingEvidenceBundle:
    query_result: QueryResult
    evidence: tuple[EvidenceRecord, ...]


class ListingEvidenceAdapter:
    """Convert listing rows to observed facts without calling them transactions."""

    def __init__(self, repository: DatasetRepository | None = None) -> None:
        self.repository = repository or DatasetRepository()

    def collect(self, query: CompetitorQuery) -> ListingEvidenceBundle:
        result = self.repository.query_competitors(query)
        records: list[EvidenceRecord] = []
        for row in result.records:
            price = row.get("price")
            if not isinstance(price, (int, float)):
                continue
            project_id = str(row.get("project_id") or "").strip()
            source_hash = str(row.get("source_hash") or "").strip()
            if not project_id or not source_hash:
                continue
            geography = "/".join(
                str(item)
                for item in (
                    row.get("city"),
                    row.get("district"),
                    row.get("subdistrict"),
                )
                if item
            )
            records.append(
                EvidenceRecord(
                    evidence_id=f"listing:{project_id}:price:{source_hash[:12]}",
                    metric_id=f"market.comparable.{project_id}.listing_price",
                    value=float(price),
                    unit="CNY/m2",
                    evidence_type=EvidenceType.OBSERVED_FACT,
                    source_id="v1_vault.new_home_listings",
                    source_ref=str(row.get("source_ref") or ""),
                    source_hash=source_hash,
                    observed_at=None,
                    as_of=result.dataset.modified_at,
                    geography=geography,
                    method=(
                        "parameterized structured filtering; price parsed from the "
                        "listing latest-price field, falling back to its reference-price field"
                    ),
                    sample_size=1,
                    limitations=[
                        "New-home listing snapshot; not a signed or registered transaction.",
                        "No normalized row-level observation timestamp; as_of is the dataset snapshot modification time.",
                    ],
                    confidence=None,
                )
            )
        return ListingEvidenceBundle(query_result=result, evidence=tuple(records))


__all__ = ["ListingEvidenceAdapter", "ListingEvidenceBundle"]
