"""Offline, source-traceable macro intelligence for DDS reports.

The module accepts records already supplied by a caller, normalizes the local
Chinese macro CSV schema and a compact English schema, then emits facts and
bounded inferences.  It never fetches the network and never fills data gaps
with generic market prose.
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:  # Support package imports and app.py's scripts-on-sys.path convention.
    from .evidence_contract import compute_evidence_confidence
except ImportError:  # pragma: no cover
    from evidence_contract import compute_evidence_confidence


DIMENSIONS = (
    "city_economy_and_purchasing_power",
    "real_estate_investment_sales_price",
    "construction_and_fixed_asset",
    "population_and_transport",
)

SOURCE_TIERS = ("official", "statistics", "market", "social", "unknown")

_SOURCE_SCORES = {
    "official": 0.90,
    "statistics": 0.95,
    "market": 0.70,
    "social": 0.45,
    "unknown": 0.35,
}

_CITY_ALIASES = {
    "济南": "济南",
    "济南市": "济南",
    "jinan": "济南",
    "武汉": "武汉",
    "武汉市": "武汉",
    "wuhan": "武汉",
    "北京": "北京",
    "北京市": "北京",
    "beijing": "北京",
    "上海": "上海",
    "上海市": "上海",
    "shanghai": "上海",
}

_SOURCE_TIER_ALIASES = {
    "official": "official",
    "government": "official",
    "政府": "official",
    "官方": "official",
    "statistics": "statistics",
    "statistical": "statistics",
    "统计": "statistics",
    "统计局": "statistics",
    "market": "market",
    "research": "market",
    "市场": "market",
    "研究机构": "market",
    "social": "social",
    "社媒": "social",
    "unknown": "unknown",
    "未知": "unknown",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    raw = _text(value).replace(",", "").replace("%", "")
    if not raw:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_city(value: Any) -> str:
    raw = _text(value)
    compact = re.sub(r"[\s._-]+", "", raw).lower()
    return _CITY_ALIASES.get(raw, _CITY_ALIASES.get(compact, raw.removesuffix("市")))


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = _text(value)
    if not raw:
        return None
    raw = raw.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    raw = raw.rstrip("-")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
        if pattern == "%Y-%m":
            # The precise end-of-month is unnecessary for the 180-day gate.
            return parsed.replace(day=28)
        if pattern == "%Y":
            return parsed.replace(month=12, day=31)
        return parsed
    return None


def _period_and_frequency(record: Mapping[str, Any]) -> tuple[str, str]:
    period = _text(
        record.get("period")
        or record.get("统计周期")
        or record.get("统计年份")
        or record.get("year")
    )
    explicit = _text(record.get("frequency") or record.get("频率")).lower()
    aliases = {
        "annual": "annual",
        "yearly": "annual",
        "年度": "annual",
        "年": "annual",
        "quarterly": "quarterly",
        "季度": "quarterly",
        "monthly": "monthly",
        "月度": "monthly",
        "月": "monthly",
    }
    if explicit in aliases:
        return period, aliases[explicit]
    annual_cn = re.fullmatch(r"(\d{4})(?:年)?全年", period)
    if annual_cn:
        return annual_cn.group(1), "annual"
    half_cn = re.fullmatch(r"(\d{4})(?:年)?(上|下)半年", period)
    if half_cn:
        return f"{half_cn.group(1)}H{1 if half_cn.group(2) == '上' else 2}", "semiannual"
    if re.fullmatch(r"\d{4}", period):
        return period, "annual"
    if re.fullmatch(r"\d{4}[-/]?Q[1-4]", period, flags=re.I):
        return period, "quarterly"
    if re.fullmatch(r"\d{4}[-/]\d{1,2}", period):
        return period, "monthly"
    return period or "unknown", "unknown"


def _period_end(period: str, frequency: str) -> date | None:
    if frequency == "annual" and re.fullmatch(r"\d{4}", period):
        return date(int(period), 12, 31)
    if frequency == "monthly":
        match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", period)
        if match:
            year, month = (int(value) for value in match.groups())
            if 1 <= month <= 12:
                next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
                return date.fromordinal(next_month.toordinal() - 1)
    if frequency == "quarterly":
        match = re.fullmatch(r"(\d{4})[-/]?Q([1-4])", period, flags=re.I)
        if match:
            year, quarter = (int(value) for value in match.groups())
            month = quarter * 3
            next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
            return date.fromordinal(next_month.toordinal() - 1)
    if frequency == "semiannual":
        match = re.fullmatch(r"(\d{4})H([12])", period, flags=re.I)
        if match:
            year, half = (int(value) for value in match.groups())
            return date(year, 6 if half == 1 else 12, 30 if half == 1 else 31)
    return _parse_date(period)


def _source_tier(record: Mapping[str, Any]) -> str:
    explicit = _text(record.get("source_tier") or record.get("来源层级")).lower()
    if explicit in _SOURCE_TIER_ALIASES:
        return _SOURCE_TIER_ALIASES[explicit]
    source = " ".join(
        _text(record.get(key))
        for key in ("source_name", "数据来源", "source_title", "来源标题", "source_url", "来源URL")
    ).lower()
    if any(token in source for token in ("统计局", "statistics bureau", "national bureau of statistics")):
        return "statistics"
    if any(token in source for token in ("政府", "gov.cn", "住房和城乡建设", "自然资源", "住建")):
        return "official"
    if any(token in source for token in ("研究院", "研究机构", "research", "market monitor", "指数研究")):
        return "market"
    if any(token in source for token in ("小红书", "抖音", "微信公众号", "social")):
        return "social"
    return "unknown"


def _dimension(indicator_name: str, category: str) -> str:
    token = f"{category} {indicator_name}".lower()
    if any(word in token for word in ("人口", "交通", "轨道", "地铁", "常住", "population", "transport", "metro")):
        return "population_and_transport"
    if any(word in token for word in ("房地产", "商品房", "住宅", "房价", "住房", "real_estate", "home", "housing")):
        return "real_estate_investment_sales_price"
    if any(
        word in token
        for word in (
            "建筑",
            "建安",
            "施工",
            "竣工",
            "新开工",
            "固定资产",
            "construction",
            "fixed_asset",
        )
    ):
        return "construction_and_fixed_asset"
    if any(word in token for word in ("gdp", "生产总值", "收入", "工资", "就业", "消费", "零售", "purchasing", "income")):
        return "city_economy_and_purchasing_power"
    return "unclassified"


def _direction(indicator_name: str, dimension: str, value: float | None, growth: float | None) -> str:
    lower = indicator_name.lower()
    if growth is not None:
        if growth < 0:
            return "pressure"
        if growth > 0:
            return "support"
    if dimension == "real_estate_investment_sales_price" and value is not None:
        if "index" in lower or "指数" in indicator_name:
            if value < 100:
                return "pressure"
            if value > 100:
                return "support"
    return "neutral"


def _source_key(record: Mapping[str, Any], tier: str) -> str:
    parts = (
        _text(record.get("source_url") or record.get("来源URL")),
        _text(record.get("source_title") or record.get("来源标题")),
        _text(record.get("source_name") or record.get("数据来源")),
        tier,
    )
    return "|".join(parts)


def _source_entry(record: Mapping[str, Any], tier: str, geography: str) -> dict[str, Any]:
    key = _source_key(record, tier)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return {
        "source_id": f"macro-source:{digest}",
        "source_tier": tier,
        "source_title": _text(record.get("source_title") or record.get("来源标题")) or None,
        "source_name": _text(record.get("source_name") or record.get("数据来源")) or None,
        "source_url": _text(record.get("source_url") or record.get("来源URL")) or None,
        "published_at": _text(record.get("published_at") or record.get("发布日期")) or None,
        "geography": geography,
    }


def _normalize_record(record: Mapping[str, Any], geography: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    indicator_name = _text(record.get("indicator_name") or record.get("indicator") or record.get("指标"))
    if not indicator_name:
        return None
    period, frequency = _period_and_frequency(record)
    category = _text(record.get("category") or record.get("指标组") or record.get("DDS维度类目"))
    tier = _source_tier(record)
    source = _source_entry(record, tier, geography)
    value = _number(record.get("value") if "value" in record else record.get("数值"))
    growth = _number(
        record.get("growth_pct")
        if "growth_pct" in record
        else record.get("同比增长_pct")
    )
    dimension = _dimension(indicator_name, category)
    key_basis = f"{geography}|{period}|{indicator_name}|{source['source_id']}"
    indicator = {
        "indicator_id": "macro-indicator:"
        + hashlib.sha256(key_basis.encode("utf-8")).hexdigest()[:20],
        "indicator_name": indicator_name,
        "category": category or None,
        "dimension": dimension,
        "value": value,
        "unit": _text(record.get("unit") or record.get("单位")) or None,
        "growth_pct": growth,
        "period": period,
        "frequency": frequency,
        "published_at": source["published_at"],
        "geography": geography,
        "evidence_type": "observed_fact",
        "source_refs": [source["source_id"]],
        "source_tier": tier,
        "signal": _direction(indicator_name, dimension, value, growth),
    }
    return indicator, source


def _claim_for_indicator(indicator: Mapping[str, Any], as_of: str | None) -> dict[str, Any]:
    value = indicator.get("value")
    unit = indicator.get("unit") or ""
    growth = indicator.get("growth_pct")
    details = []
    if value is not None:
        details.append(f"{value:g}{unit}")
    if growth is not None:
        details.append(f"同比{growth:+g}%")
    measured = "，".join(details) if details else "已记录但缺少可用数值"
    return {
        "claim_id": f"claim:{indicator['indicator_id'].split(':')[-1]}",
        "statement": f"{indicator['indicator_name']}：{measured}。",
        "dimension": indicator["dimension"],
        "signal": indicator["signal"],
        "evidence_type": "observed_fact",
        "source_refs": list(indicator["source_refs"]),
        "as_of": as_of,
        "period": indicator["period"],
        "geography": indicator["geography"],
    }


def _cycle_assessment(indicators: Sequence[Mapping[str, Any]], as_of: str | None, geography: str) -> dict[str, Any]:
    real_estate = [
        item for item in indicators if item["dimension"] == "real_estate_investment_sales_price"
    ]
    property_pressure = [item for item in real_estate if item["signal"] == "pressure"]
    property_support = [item for item in real_estate if item["signal"] == "support"]
    macro_support = [
        item
        for item in indicators
        if item["dimension"] in {
            "city_economy_and_purchasing_power",
            "population_and_transport",
        }
        and item["signal"] == "support"
    ]

    if len(property_pressure) >= 2 and macro_support:
        state = "structural_divergence"
        conclusion = "城市基本面存在支撑，但房地产投资与销售压力并存，当前属于结构性分化。"
    elif len(property_pressure) >= 2:
        state = "under_pressure"
        conclusion = "房地产投资、销售或价格的多项信号承压，尚无足够证据支持复苏判断。"
    elif property_support and not property_pressure:
        state = "tentative_improvement"
        conclusion = "房地产局部指标改善，但仍需价格、销售、投资和库存的多源近期数据交叉验证。"
    else:
        state = "undetermined"
        conclusion = "现有指标不足以判定房地产周期位置。"

    source_refs = sorted(
        {source for item in indicators for source in item.get("source_refs", [])}
    )
    return {
        "state": state,
        "conclusion": conclusion,
        "evidence_type": "analysis_inference",
        "source_refs": source_refs,
        "as_of": as_of,
        "period": ", ".join(sorted({item["period"] for item in indicators})) or None,
        "geography": geography,
        "cautions": [
            "GDP 或人口增长只说明城市基本面支撑，不能等同于楼市价格、销售或去化上涨。",
            "房地产周期判断必须继续核验近180天价格、成交、库存、土地和建筑活动。",
        ],
    }


def _gap(gap_id: str, gap_type: str, scope: str, description: str, required_for: str) -> dict[str, Any]:
    return {
        "gap_id": gap_id,
        "gap_type": gap_type,
        "scope": scope,
        "description": description,
        "required_for": required_for,
        "auditable": True,
    }


def _empty_result(city: str, as_of: str | None, filtered: int = 0) -> dict[str, Any]:
    gaps = [
        _gap(
            "macro:no-city-records",
            "coverage",
            "all_dimensions",
            f"没有可用于{city}宏观研判的记录。",
            "macro_cycle_assessment",
        )
    ]
    return {
        "status": "missing",
        "city": city,
        "as_of": as_of,
        "source_registry": [],
        "indicators": [],
        "claims": [],
        "support_signals": [],
        "counter_signals": [],
        "dimension_summary": {dimension: {"record_count": 0, "status": "missing"} for dimension in DIMENSIONS},
        "cycle_assessment": {
            "state": "undetermined",
            "conclusion": None,
            "evidence_type": "analysis_inference",
            "source_refs": [],
            "as_of": as_of,
            "period": None,
            "geography": city,
            "cautions": [],
        },
        "coverage": {
            "record_count": 0,
            "annual_records": 0,
            "recent_records": 0,
            "monthly_real_estate_records": 0,
            "monthly_construction_records": 0,
            "filtered_other_geography": filtered,
            "dimension_counts": {dimension: 0 for dimension in DIMENSIONS},
        },
        "evidence_confidence": compute_evidence_confidence({}, cap=0.0),
        "evidence_gaps": gaps,
    }


def _derived_as_of(records: Sequence[Mapping[str, Any]], explicit: Any) -> date | None:
    if explicit is not None:
        parsed = _parse_date(explicit)
        if parsed is None:
            raise ValueError("as_of must be an ISO date or datetime")
        return parsed
    candidates = [
        _parse_date(record.get("published_at") or record.get("发布日期"))
        for record in records
    ]
    available = [candidate for candidate in candidates if candidate]
    return max(available) if available else None


def build_macro_context(
    records: Sequence[Mapping[str, Any]] | None,
    city: str,
    as_of: Any = None,
) -> dict[str, Any]:
    """Build a bounded macro context from caller-supplied records only."""
    target_city = _canonical_city(city)
    input_records = [record for record in (records or []) if isinstance(record, Mapping)]
    effective_as_of = _derived_as_of(input_records, as_of)
    as_of_text = effective_as_of.isoformat() if effective_as_of else None

    normalized: list[dict[str, Any]] = []
    sources_by_id: dict[str, dict[str, Any]] = {}
    filtered_other = 0
    for record in input_records:
        record_city = _canonical_city(record.get("city") or record.get("城市") or target_city)
        if record_city and record_city != target_city:
            filtered_other += 1
            continue
        pair = _normalize_record(record, target_city)
        if pair is None:
            continue
        indicator, source = pair
        normalized.append(indicator)
        sources_by_id[source["source_id"]] = source

    if not normalized:
        return _empty_result(target_city, as_of_text, filtered_other)

    recent: list[dict[str, Any]] = []
    if effective_as_of:
        for indicator in normalized:
            if indicator["frequency"] not in {"monthly", "quarterly", "semiannual"}:
                continue
            period_end = _period_end(indicator["period"], indicator["frequency"])
            if period_end and 0 <= (effective_as_of - period_end).days <= 180:
                recent.append(indicator)

    monthly_property = [
        item
        for item in recent
        if item["frequency"] == "monthly"
        and item["dimension"] == "real_estate_investment_sales_price"
    ]
    monthly_construction = [
        item
        for item in recent
        if item["frequency"] == "monthly"
        and item["dimension"] == "construction_and_fixed_asset"
    ]
    dimension_counts = Counter(item["dimension"] for item in normalized)
    dimension_summary = {
        dimension: {
            "record_count": dimension_counts[dimension],
            "status": "covered" if dimension_counts[dimension] else "missing",
            "support_count": sum(
                1 for item in normalized if item["dimension"] == dimension and item["signal"] == "support"
            ),
            "pressure_count": sum(
                1 for item in normalized if item["dimension"] == dimension and item["signal"] == "pressure"
            ),
        }
        for dimension in DIMENSIONS
    }

    gaps: list[dict[str, Any]] = []
    for dimension in DIMENSIONS:
        if not dimension_counts[dimension]:
            gaps.append(
                _gap(
                    f"macro:dimension:{dimension}",
                    "coverage",
                    dimension,
                    f"缺少 {dimension} 指标。",
                    "dimension_assessment",
                )
            )
    if not recent:
        gaps.append(
            _gap(
                "macro:no-recent-operational-series",
                "freshness",
                "real_estate_and_construction",
                "缺少截至研判日近180天内的月度或季度运营数据。",
                "current_cycle_position",
            )
        )
    if not monthly_property:
        gaps.append(
            _gap(
                "macro:no-monthly-real-estate",
                "coverage",
                "real_estate_investment_sales_price",
                "缺少近期月度房地产价格、成交、库存或投资序列。",
                "absorption_and_price_trend",
            )
        )
    if not monthly_construction:
        gaps.append(
            _gap(
                "macro:no-monthly-construction",
                "coverage",
                "construction_and_fixed_asset",
                "缺少近期月度建筑业或固定资产投资序列。",
                "construction_cycle_position",
            )
        )

    source_registry = sorted(sources_by_id.values(), key=lambda item: item["source_id"])
    if len(source_registry) <= 1:
        gaps.append(
            _gap(
                "macro:single-source-family",
                "source_independence",
                "all_dimensions",
                "现有指标来自单一来源家族，尚未形成独立交叉验证。",
                "high_confidence_macro_assessment",
            )
        )
    covered_dimensions = sum(1 for dimension in DIMENSIONS if dimension_counts[dimension])
    source_score = sum(_SOURCE_SCORES[item["source_tier"]] for item in source_registry) / len(source_registry)
    confidence_dimensions = {
        "source": source_score,
        "coverage": covered_dimensions / len(DIMENSIONS),
        "freshness": min(1.0, len(recent) / max(2, len(normalized))),
        "independent_cross": min(1.0, max(0, len(source_registry) - 1) / 2),
        "geographic_relevance": 1.0,
        "method_fit": 0.85,
        "stability": min(1.0, len({item["period"] for item in normalized}) / 3),
    }
    confidence_cap = 1.0
    if len(source_registry) <= 1:
        confidence_cap = min(confidence_cap, 0.69)
    if not monthly_property or not monthly_construction:
        confidence_cap = min(confidence_cap, 0.69)
    evidence_confidence = compute_evidence_confidence(
        confidence_dimensions, cap=confidence_cap
    )

    cycle = _cycle_assessment(normalized, as_of_text, target_city)
    claims = [_claim_for_indicator(item, as_of_text) for item in normalized]
    if cycle["source_refs"]:
        cycle_key = hashlib.sha256(
            f"{target_city}|{cycle['state']}|{cycle['period']}".encode("utf-8")
        ).hexdigest()[:20]
        claims.append(
            {
                "claim_id": f"claim:macro-cycle:{cycle_key}",
                "statement": cycle["conclusion"],
                "dimension": "macro_cycle",
                "signal": cycle["state"],
                "evidence_type": "analysis_inference",
                "source_refs": list(cycle["source_refs"]),
                "as_of": cycle["as_of"],
                "period": cycle["period"],
                "geography": cycle["geography"],
            }
        )

    complete = (
        covered_dimensions == len(DIMENSIONS)
        and bool(monthly_property)
        and bool(monthly_construction)
        and len(source_registry) >= 2
    )
    return {
        "status": "ready" if complete else "partial",
        "city": target_city,
        "as_of": as_of_text,
        "source_registry": source_registry,
        "indicators": normalized,
        "claims": claims,
        "support_signals": [item for item in normalized if item["signal"] == "support"],
        "counter_signals": [item for item in normalized if item["signal"] == "pressure"],
        "dimension_summary": dimension_summary,
        "cycle_assessment": cycle,
        "coverage": {
            "record_count": len(normalized),
            "annual_records": sum(1 for item in normalized if item["frequency"] == "annual"),
            "recent_records": len(recent),
            "monthly_real_estate_records": len(monthly_property),
            "monthly_construction_records": len(monthly_construction),
            "filtered_other_geography": filtered_other,
            "dimension_counts": {dimension: dimension_counts[dimension] for dimension in DIMENSIONS},
        },
        "evidence_confidence": evidence_confidence,
        "evidence_gaps": gaps,
    }


def load_local_macro_records(city: str, path: str | Path | None = None) -> list[dict[str, str]]:
    """Read and deduplicate matching local macro rows without modifying them.

    A file path keeps backward compatibility.  A directory (and the default)
    merges the cross-city annual table with city-specific quarterly/operating
    series so reports do not silently ignore fresher evidence.
    """
    target_city = _canonical_city(city)
    source_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[1] / "Vault" / "宏观数据"
    )
    if source_path.is_file():
        csv_paths = [source_path]
    elif source_path.is_dir():
        csv_paths = sorted(
            candidate
            for candidate in source_path.glob("*.csv")
            if not candidate.name.startswith("_")
            and (
                candidate.name == "重点城市-官方年度指标.csv"
                or candidate.name.startswith(f"{target_city}-")
            )
        )
    else:
        return []

    matched: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for csv_path in csv_paths:
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeError, csv.Error):
            continue
        for row in rows:
            if _canonical_city(row.get("city") or row.get("城市")) != target_city:
                continue
            period = _text(
                row.get("period")
                or row.get("统计周期")
                or row.get("统计年份")
                or row.get("year")
            )
            identity = (
                target_city,
                period,
                _text(row.get("indicator") or row.get("指标")),
                _text(row.get("source_url") or row.get("来源URL")),
                _text(row.get("value") if "value" in row else row.get("数值")),
                _text(
                    row.get("growth_pct")
                    if "growth_pct" in row
                    else row.get("同比增长_pct")
                ),
            )
            if identity in seen:
                continue
            seen.add(identity)
            matched.append(dict(row))
    return matched


__all__ = [
    "DIMENSIONS",
    "SOURCE_TIERS",
    "build_macro_context",
    "load_local_macro_records",
]
