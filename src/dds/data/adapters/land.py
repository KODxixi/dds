"""Official land benchmark adapter with source URL preservation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dds.config import Settings
from dds.data.catalog import DatasetAsset, DatasetKind
from dds.domain import EvidenceRecord, EvidenceType

from ._local import asset_from_path, connect_duckdb, row_digest, safe_name


@dataclass(frozen=True, slots=True)
class LandEvidenceBundle:
    assets: tuple[DatasetAsset, ...]
    evidence: tuple[EvidenceRecord, ...]


class LandAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def collect(self, city: str, *, grade: str | None = None) -> LandEvidenceBundle:
        city = safe_name(city, "city")
        root = self.settings.datasets_root / "land"
        paths = sorted(root.glob(f"{city}-*.parquet"))
        if not paths:
            raise FileNotFoundError(f"No configured land dataset for {city}")
        assets: list[DatasetAsset] = []
        evidence: list[EvidenceRecord] = []
        for path in paths:
            asset = asset_from_path(
                path,
                kind=DatasetKind.LAND,
                city=city,
                authority="official_land_benchmark",
            )
            assets.append(asset)
            clauses = ['"城市" = ?']
            params: list[Any] = [str(asset.path), city]
            if grade:
                clauses.append('"级别" = ?')
                params.append(grade)
            sql = f'''
                SELECT
                    "城市" AS city,
                    "适用范围" AS scope,
                    "级别" AS grade,
                    "住宅用地级别地价_元每建筑平方米" AS value,
                    "地价口径" AS price_basis,
                    "基准日" AS base_date,
                    "发布日期" AS published_at,
                    "数据来源" AS source_label,
                    "来源URL" AS source_url
                FROM read_parquet(?)
                WHERE {' AND '.join(clauses)}
                ORDER BY "级别"
            '''
            connection = connect_duckdb()
            try:
                cursor = connection.execute(sql, params)
                columns = [item[0] for item in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                connection.close()
            for row in rows:
                value = row.get("value")
                if not isinstance(value, (int, float)) or value <= 0:
                    continue
                digest = row_digest(row)
                evidence.append(
                    EvidenceRecord(
                        evidence_id=f"land:{digest[:24]}",
                        metric_id="land.residential_benchmark_floor_price",
                        value=float(value),
                        unit="CNY/buildable-m2",
                        evidence_type=EvidenceType.OBSERVED_FACT,
                        source_id=str(row.get("source_label") or asset.authority),
                        source_ref=str(row.get("source_url") or asset.source_ref),
                        source_hash=asset.source_hash,
                        observed_at=None,
                        as_of=row.get("base_date"),
                        geography="/".join(
                            str(item)
                            for item in (row.get("city"), row.get("scope"), row.get("grade"))
                            if item
                        ),
                        method=str(row.get("price_basis") or "official benchmark land price"),
                        sample_size=1,
                        limitations=[
                            "Official benchmark land price is not an observed parcel transaction price."
                        ],
                        confidence=None,
                    )
                )
        return LandEvidenceBundle(tuple(assets), tuple(evidence))


__all__ = ["LandAdapter", "LandEvidenceBundle"]
