"""Official macro indicator adapter with per-row provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dds.config import Settings
from dds.data.catalog import DatasetAsset, DatasetKind
from dds.domain import EvidenceRecord, EvidenceType

from ._local import asset_from_path, connect_duckdb, row_digest, safe_name


@dataclass(frozen=True, slots=True)
class MacroEvidenceBundle:
    assets: tuple[DatasetAsset, ...]
    evidence: tuple[EvidenceRecord, ...]


class MacroAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def collect(
        self,
        city: str,
        *,
        metrics: tuple[str, ...] = (),
        limit_per_source: int = 500,
    ) -> MacroEvidenceBundle:
        city = safe_name(city, "city")
        if limit_per_source < 1 or limit_per_source > 10_000:
            raise ValueError("limit_per_source must be between 1 and 10000")
        paths = sorted((self.settings.v1_vault_root / "宏观数据").glob(f"{city}-*.parquet"))
        if not paths:
            raise FileNotFoundError(f"No configured macro dataset for {city}")
        assets: list[DatasetAsset] = []
        evidence: list[EvidenceRecord] = []
        for path in paths:
            asset = asset_from_path(
                path,
                kind=DatasetKind.MACRO,
                city=city,
                authority="official_statistical_release",
            )
            assets.append(asset)
            connection = connect_duckdb()
            try:
                schema = {
                    row[0] for row in connection.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?)", [str(asset.path)]
                    ).fetchall()
                }
                period_column = "统计年份" if "统计年份" in schema else "统计周期"
                if period_column not in schema:
                    continue
                clauses = ['"城市" = ?']
                params: list[Any] = [str(asset.path), city]
                if metrics:
                    placeholders = ",".join("?" for _ in metrics)
                    clauses.append(f'"指标" IN ({placeholders})')
                    params.extend(metrics)
                params.append(limit_per_source)
                sql = f'''
                    SELECT
                        "城市" AS city,
                        CAST("{period_column}" AS VARCHAR) AS period,
                        "指标" AS metric,
                        "数值" AS value,
                        "单位" AS unit,
                        "同比增长_pct" AS yoy_pct,
                        "数据来源" AS source_label,
                        "来源URL" AS source_url,
                        "发布日期" AS published_at,
                        "备注" AS limitations
                    FROM read_parquet(?)
                    WHERE {' AND '.join(clauses)}
                    ORDER BY CAST("{period_column}" AS VARCHAR) DESC, "指标"
                    LIMIT ?
                '''
                cursor = connection.execute(sql, params)
                columns = [item[0] for item in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            finally:
                connection.close()
            for row in rows:
                value = row.get("value")
                if value is None:
                    # A row may still contain only YoY data.  It must not be
                    # converted to a fake level value.
                    continue
                digest = row_digest(row)
                evidence.append(
                    EvidenceRecord(
                        evidence_id=f"macro:{digest[:24]}",
                        metric_id=f"macro.{row.get('metric')}",
                        value=value,
                        unit=str(row.get("unit") or ""),
                        evidence_type=EvidenceType.OBSERVED_FACT,
                        source_id=str(row.get("source_label") or asset.authority),
                        source_ref=str(row.get("source_url") or asset.source_ref),
                        source_hash=asset.source_hash,
                        observed_at=None,
                        as_of=row.get("period"),
                        geography=str(row.get("city") or city),
                        method="official statistical release row extraction",
                        sample_size=1,
                        limitations=[str(row["limitations"])] if row.get("limitations") else [],
                        confidence=None,
                    )
                )
        return MacroEvidenceBundle(tuple(assets), tuple(evidence))


__all__ = ["MacroAdapter", "MacroEvidenceBundle"]
