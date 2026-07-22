"""SC2 market-comparable selection and auditable price statistics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import statistics
from typing import Any, Mapping, Sequence

from dds.data.repository import QueryResult


class MarketReadiness(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class CompetitorCandidate:
    project_id: str
    project_name: str
    district: str | None
    subdistrict: str | None
    sales_status: str | None
    property_type: str | None
    price: float
    area_min: float | None
    area_max: float | None
    distance_km: float | None
    source_ref: str
    source_hash: str
    evidence_ref: str
    score: float


@dataclass(frozen=True, slots=True)
class MarketAnalysisResult:
    readiness: MarketReadiness
    selected: tuple[CompetitorCandidate, ...]
    excluded: Mapping[str, int]
    minimum_required: int
    degraded_minimum: int
    price_low: float | None
    price_median: float | None
    price_high: float | None
    unit: str | None
    evidence_refs: tuple[str, ...]
    formulas: Mapping[str, str]
    conclusions: tuple[str, ...]
    assumptions: tuple[str, ...]
    counter_evidence: tuple[str, ...]
    gaps: tuple[str, ...]
    actions: tuple[str, ...]
    screening_rules: Mapping[str, Any]
    data_time_coverage: Mapping[str, str | None]

    @property
    def effective_sample_size(self) -> int:
        return len(self.selected)

    @property
    def decision_ready(self) -> bool:
        return self.readiness is MarketReadiness.READY


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class MarketEngine:
    """Apply structural filters before ranking; vector similarity is never final."""

    ALLOWED_OBSERVATION_TYPES = {"new_home_listing"}

    def __init__(self, minimum_competitors: int = 5, degraded_minimum: int = 3) -> None:
        if minimum_competitors < 1:
            raise ValueError("minimum_competitors must be positive")
        if degraded_minimum < 1 or degraded_minimum > minimum_competitors:
            raise ValueError("degraded_minimum must be within the sample threshold")
        self.minimum_competitors = minimum_competitors
        self.degraded_minimum = degraded_minimum

    def analyze(
        self,
        query_result: QueryResult,
        *,
        target_price: float | None = None,
        target_area_min: float | None = None,
        target_area_max: float | None = None,
        selection_limit: int = 12,
    ) -> MarketAnalysisResult:
        if selection_limit < 1:
            raise ValueError("selection_limit must be positive")
        districts = set(query_result.filters.get("districts") or [])
        candidates: list[CompetitorCandidate] = []
        exclusions: dict[str, int] = dict(query_result.exclusions)
        seen: set[tuple[str, str]] = set()

        def exclude(reason: str) -> None:
            exclusions[reason] = exclusions.get(reason, 0) + 1

        for raw in query_result.records:
            observation_type = raw.get("market_observation_type", "new_home_listing")
            if observation_type not in self.ALLOWED_OBSERVATION_TYPES:
                exclude("wrong_observation_type")
                continue
            project_id = str(raw.get("project_id") or "").strip()
            project_name = str(raw.get("project_name") or "").strip()
            source_hash = str(raw.get("source_hash") or "").strip()
            source_ref = str(raw.get("source_ref") or "").strip()
            if not project_id or not project_name:
                exclude("missing_identity")
                continue
            if not source_hash or not source_ref:
                exclude("missing_source")
                continue
            deduplication_key = (project_id, source_hash)
            if deduplication_key in seen:
                exclude("duplicate_source_record")
                continue
            seen.add(deduplication_key)
            price = raw.get("price")
            if not isinstance(price, (int, float)) or not math.isfinite(float(price)):
                exclude("missing_price")
                continue

            score = 0.0
            district = raw.get("district")
            subdistrict = raw.get("subdistrict")
            if districts and (district in districts or subdistrict in districts):
                score += 40.0
            distance = raw.get("distance_km")
            if isinstance(distance, (int, float)) and math.isfinite(float(distance)):
                score += max(0.0, 30.0 - float(distance) * 3.0)
            if target_price and target_price > 0:
                price_delta = abs(float(price) - target_price) / target_price
                score += max(0.0, 20.0 - price_delta * 20.0)
            row_area_min = raw.get("area_min")
            row_area_max = raw.get("area_max")
            if (
                target_area_min is not None
                and target_area_max is not None
                and isinstance(row_area_min, (int, float))
                and isinstance(row_area_max, (int, float))
            ):
                overlap = min(float(row_area_max), target_area_max) - max(
                    float(row_area_min), target_area_min
                )
                if overlap >= 0:
                    score += 10.0

            evidence_ref = f"listing:{project_id}:price:{source_hash[:12]}"
            candidates.append(
                CompetitorCandidate(
                    project_id=project_id,
                    project_name=project_name,
                    district=str(district) if district else None,
                    subdistrict=str(subdistrict) if subdistrict else None,
                    sales_status=str(raw.get("sales_status"))
                    if raw.get("sales_status")
                    else None,
                    property_type=str(raw.get("property_type"))
                    if raw.get("property_type")
                    else None,
                    price=float(price),
                    area_min=float(row_area_min)
                    if isinstance(row_area_min, (int, float))
                    else None,
                    area_max=float(row_area_max)
                    if isinstance(row_area_max, (int, float))
                    else None,
                    distance_km=float(distance)
                    if isinstance(distance, (int, float))
                    else None,
                    source_ref=source_ref,
                    source_hash=source_hash,
                    evidence_ref=evidence_ref,
                    score=round(score, 6),
                )
            )

        selected = tuple(
            sorted(candidates, key=lambda item: (-item.score, item.project_id))[
                :selection_limit
            ]
        )
        count = len(selected)
        evidence_refs = tuple(item.evidence_ref for item in selected)
        assumptions = (
            "价格字段按新房挂牌口径处理，不与网签、成交或土地价格混算。",
            "排名仅使用结构化地域、距离、价格与面积匹配；未使用语义相似度替代资格判断。",
        )
        counter_evidence: list[str] = []
        gaps: list[str] = []
        actions: list[str] = []
        formulas: dict[str, str] = {}

        if count >= self.minimum_competitors:
            readiness = MarketReadiness.READY
            prices = [item.price for item in selected]
            price_low = round(_percentile(prices, 0.25), 2)
            price_median = round(float(statistics.median(prices)), 2)
            price_high = round(_percentile(prices, 0.75), 2)
            formulas = {
                "price_low": "P25(valid comparable listing prices)",
                "price_median": "median(valid comparable listing prices)",
                "price_high": "P75(valid comparable listing prices)",
            }
            conclusions = (
                f"结构化筛选后获得 {count} 个有效新房挂牌竞品，达到最低样本门槛。",
                "价格边界仅是挂牌样本统计，不等同于成交价或项目定价结论。",
            )
        elif count >= self.degraded_minimum:
            readiness = MarketReadiness.DEGRADED
            prices = [item.price for item in selected]
            price_low = round(min(prices), 2)
            price_median = round(float(statistics.median(prices)), 2)
            price_high = round(max(prices), 2)
            formulas = {
                "price_low": "min(valid comparable listing prices)",
                "price_median": "median(valid comparable listing prices)",
                "price_high": "max(valid comparable listing prices)",
            }
            conclusions = (
                f"仅获得 {count} 个有效新房挂牌竞品，结果降级为探索性区间。",
                "样本不足以支持正式单点定价。",
            )
            gaps.append(
                f"有效竞品少于 {self.minimum_competitors} 个，缺少稳定的可比样本。"
            )
            actions.append("补充同板块、相近价格带和相近面积段的在售竞品。")
        else:
            readiness = MarketReadiness.INSUFFICIENT
            price_low = price_median = price_high = None
            conclusions = (
                f"仅获得 {count} 个有效新房挂牌竞品，禁止输出单点价格或统计价格区间。",
            )
            gaps.append(
                f"有效竞品少于降级门槛 {self.degraded_minimum} 个。"
            )
            actions.append("扩大经过确认的地域或时间窗口，并补充可核验的新房挂牌数据。")

        if exclusions.get("wrong_observation_type"):
            counter_evidence.append("输入中包含非新房挂牌口径记录，已隔离而未混算。")
        if exclusions.get("duplicate_source_record"):
            counter_evidence.append("输入中存在重复来源记录，已去重。")

        return MarketAnalysisResult(
            readiness=readiness,
            selected=selected,
            excluded=dict(sorted(exclusions.items())),
            minimum_required=self.minimum_competitors,
            degraded_minimum=self.degraded_minimum,
            price_low=price_low,
            price_median=price_median,
            price_high=price_high,
            unit="CNY/m2" if price_median is not None else None,
            evidence_refs=evidence_refs,
            formulas=formulas,
            conclusions=conclusions,
            assumptions=assumptions,
            counter_evidence=tuple(counter_evidence),
            gaps=tuple(gaps),
            actions=tuple(actions),
            screening_rules=dict(query_result.filters),
            data_time_coverage={
                "dataset_modified_at": query_result.dataset.modified_at,
                "observed_on": query_result.filters.get("observed_on"),
            },
        )


__all__ = [
    "CompetitorCandidate",
    "MarketAnalysisResult",
    "MarketEngine",
    "MarketReadiness",
]
