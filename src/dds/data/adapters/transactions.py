"""Verified transaction-row adapter; never merged into listing observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from dds.config import Settings
from dds.data.catalog import DatasetAsset, DatasetKind
from dds.domain import EvidenceRecord, EvidenceType

from ._local import asset_from_path, connect_duckdb, row_digest, safe_name


@dataclass(frozen=True, slots=True)
class TransactionEvidenceBundle:
    asset: DatasetAsset
    evidence: tuple[EvidenceRecord, ...]
    filters: dict[str, Any]


class TransactionAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def collect(
        self,
        city: str,
        *,
        districts: tuple[str, ...] = (),
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 500,
    ) -> TransactionEvidenceBundle:
        city = safe_name(city, "city")
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        if date_from and date_to and date_from > date_to:
            raise ValueError("date_from must not exceed date_to")
        path = self.settings.v1_vault_root / "成交数据" / f"成交-{city}.parquet"
        asset = asset_from_path(
            path,
            kind=DatasetKind.TRANSACTIONS,
            city=city,
            authority="purchased_transaction_archive",
        )
        clauses = ['"城市" = ?']
        params: list[Any] = [str(asset.path), city]
        if districts:
            placeholders = ",".join("?" for _ in districts)
            clauses.append(f'"区县" IN ({placeholders})')
            params.extend(districts)
        if date_from:
            clauses.append('TRY_CAST("成交时间" AS DATE) >= ?')
            params.append(date_from.isoformat())
        if date_to:
            clauses.append('TRY_CAST("成交时间" AS DATE) <= ?')
            params.append(date_to.isoformat())
        params.append(limit)
        sql = f'''
            SELECT
                "小区名称" AS project_name,
                "城市" AS city,
                "区县" AS district,
                "商圈" AS subdistrict,
                "户型" AS unit_type,
                TRY_CAST("面积" AS DOUBLE) AS area_m2,
                TRY_CAST("成交总额" AS DOUBLE) AS total_price_wan,
                TRY_CAST("成交均价" AS DOUBLE) AS unit_price_cny_m2,
                "成交时间" AS transaction_date,
                "数据来源" AS source_label,
                "入库日期" AS ingested_at
            FROM read_parquet(?)
            WHERE {' AND '.join(clauses)}
            ORDER BY TRY_CAST("成交时间" AS DATE) DESC, "小区名称"
            LIMIT ?
        '''
        connection = connect_duckdb()
        try:
            cursor = connection.execute(sql, params)
            columns = [item[0] for item in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            connection.close()
        evidence: list[EvidenceRecord] = []
        for row in rows:
            price = row.get("unit_price_cny_m2")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            digest = row_digest(row)
            source_label = str(row.get("source_label") or asset.authority)
            evidence.append(
                EvidenceRecord(
                    evidence_id=f"transaction:{digest[:24]}",
                    metric_id="market.transaction.unit_price",
                    value=float(price),
                    unit="CNY/m2",
                    evidence_type=EvidenceType.OBSERVED_FACT,
                    source_id=source_label,
                    source_ref=asset.source_ref,
                    source_hash=asset.source_hash,
                    observed_at=row.get("transaction_date"),
                    as_of=row.get("ingested_at"),
                    geography="/".join(
                        str(item)
                        for item in (row.get("city"), row.get("district"), row.get("subdistrict"))
                        if item
                    ),
                    method="row-level transaction archive query; no listing rows included",
                    sample_size=1,
                    limitations=[
                        "Purchased transaction archive; source methodology must be checked before external delivery."
                    ],
                    confidence=None,
                )
            )
        return TransactionEvidenceBundle(
            asset=asset,
            evidence=tuple(evidence),
            filters={
                "city": city,
                "districts": list(districts),
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "limit": limit,
                "observation_type": "transaction",
            },
        )


__all__ = ["TransactionAdapter", "TransactionEvidenceBundle"]
