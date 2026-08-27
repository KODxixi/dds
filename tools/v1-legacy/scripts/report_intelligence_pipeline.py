"""Assemble DDS v4 intelligence fields before PageManifest rendering.

The adapter is deliberately deterministic and network-free.  It consumes
records already supplied by the caller or cached locally/TOS, preserves
explicit missing/partial states, and keeps cultural interpretation outside
investment mathematics.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

try:  # Package imports for tests and tooling.
    from .investment_engine import build_investment_case
    from .macro_intelligence import build_macro_context, load_local_macro_records
    from .report_document import build_report_document
    from .social_intelligence import build_social_intelligence
    from .traditional_spatial_culture import build_traditional_spatial_culture
except ImportError:  # Existing app.py adds scripts/ to sys.path.
    from investment_engine import build_investment_case
    try:
        from macro_intelligence import build_macro_context, load_local_macro_records
    except ImportError:  # Macro module may be deployed after the base contract.
        def load_local_macro_records(city: str) -> list[dict[str, Any]]:
            return []

        def build_macro_context(records, city: str, as_of=None) -> dict[str, Any]:
            return {
                "status": "missing",
                "city": city,
                "indicators": [],
                "claims": [],
                "counter_signals": [],
                "source_registry": [],
                "evidence_gaps": ["缺少可追溯宏观指标。"],
                "evidence_confidence": {"score": 0.0, "level": "undecidable"},
            }

    from report_document import build_report_document
    from social_intelligence import build_social_intelligence
    from traditional_spatial_culture import build_traditional_spatial_culture


_PRIVATE_KEYS = {"personas_raw"}
_TRADITIONAL_INVESTMENT_MARKERS = (
    "traditional",
    "fengshui",
    "feng_shui",
    "wuxing",
    "bagua",
    "flying_star",
    "eight_mansion",
    "bazi",
    "auspicious",
    "lucky",
    "风水",
    "五行",
    "八卦",
    "飞星",
    "八宅",
    "八字",
    "吉凶",
    "财位",
    "煞气",
    "龙脉",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _prune_private(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _prune_private(item)
            for key, item in value.items()
            if str(key) not in _PRIVATE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_prune_private(item) for item in value]
    return deepcopy(value)


def _find_abm(report: Mapping[str, Any]) -> dict[str, Any]:
    decision_full = _mapping(report.get("decision_full"))
    decision = _mapping(report.get("decision"))
    return _mapping(
        decision_full.get("abm_market_agent")
        or decision.get("abm_market_agent")
        or report.get("abm_market_agent")
    )


def _synthetic_personas(report: Mapping[str, Any]) -> dict[str, Any]:
    abm = _find_abm(report)
    if not abm:
        return {
            "status": "missing",
            "evidence_type": "model_simulation",
            "items": [],
            "evidence_confidence": {"score": 0.0, "level": "undecidable"},
            "simulation_stability": {"score": 0.0, "level": "undecidable"},
            "evidence_gaps": ["ABM 尚未运行。"],
        }
    return {
        "status": "ready",
        "label": "真实证据校准的合成人格" if abm.get("evidence_calibrated") else "专家先验合成人格",
        "evidence_type": "model_simulation",
        "items": _prune_private(abm.get("personas") or abm.get("top_personas") or []),
        "evidence_confidence": _prune_private(
            abm.get("evidence_confidence") or abm.get("confidence") or {"score": 0.0}
        ),
        "simulation_stability": _prune_private(
            abm.get("simulation_stability") or {"score": 0.0}
        ),
        "method": abm.get("method"),
        "representativeness_limits": [
            "合成人格不是现实个人的数字孪生。",
            "支付力、WTP 与成交概率不得由社媒文本直接改写。",
        ],
    }


def _competitor_series(report: Mapping[str, Any]) -> dict[str, Any]:
    market = _mapping(report.get("market"))
    competitors = _sequence(market.get("competitors") or market.get("nearby_competitors"))
    items: list[dict[str, Any]] = []
    for index, candidate in enumerate(competitors):
        if not isinstance(candidate, Mapping):
            continue
        name = candidate.get("project_name") or candidate.get("name")
        if not name:
            continue
        refs = _sequence(candidate.get("source_refs"))
        if not refs:
            refs = [f"market:competitor:{index + 1}"]
        items.append(
            {
                "project_name": str(name),
                "unit_price_cny": _finite(
                    candidate.get("unit_price_cny") or candidate.get("price")
                ),
                "distance_km": _finite(candidate.get("distance_km")),
                "status": candidate.get("sale_status") or candidate.get("status"),
                "observed_at": candidate.get("observed_at") or candidate.get("captured_at"),
                "source_refs": [str(ref) for ref in refs],
                "evidence_type": "observed_fact",
            }
        )
    return {
        "status": "partial" if items else "missing",
        "series_status": "snapshot_only" if items else "missing",
        "items": items,
        "evidence_gaps": (
            ["已有竞品截面，但缺少价格、供应、加推、促销、库存和去化的连续时序。"]
            if items
            else ["缺少可核验竞品记录。"]
        ),
    }


def _premium_analysis(report: Mapping[str, Any]) -> dict[str, Any]:
    decision_full = _mapping(report.get("decision_full"))
    source = _mapping(report.get("premium_analysis") or decision_full.get("premium_engine"))
    if not source:
        return {
            "status": "missing",
            "evidence_type": "model_simulation",
            "drivers": [],
            "evidence_gaps": ["溢价引擎无可追溯输入。"],
        }
    entry = _mapping(source.get("entry_premium"))
    return {
        "status": source.get("status") or "partial",
        "evidence_type": "model_simulation",
        "total_pct": _finite(entry.get("total_pct")),
        "drivers": _prune_private(entry.get("drivers") or []),
        "basis": entry.get("note") or source.get("basis"),
        "disclaimer": source.get("disclaimer") or "溢价为条件化模型推演，不是成交事实或收益承诺。",
        "source_refs": list(source.get("source_refs") or ["decision_full.premium_engine"]),
        "evidence_confidence": _prune_private(
            source.get("evidence_confidence") or source.get("confidence") or {"score": 0.0}
        ),
    }


def _absorption_forecast(report: Mapping[str, Any]) -> dict[str, Any]:
    decision_full = _mapping(report.get("decision_full"))
    blueprint = _mapping(decision_full.get("blueprint_logic"))
    source = _mapping(report.get("absorption_forecast") or blueprint.get("absorption_simulation"))
    base_months = _finite(source.get("sellout_months"))
    base_rate = _finite(
        source.get("monthly_absorption_rate_units") or source.get("monthly_rate")
    )
    if not source or not base_months or not base_rate or base_months <= 0 or base_rate <= 0:
        return {
            "status": "missing",
            "evidence_type": "model_simulation",
            "scenarios": [],
            "evidence_gaps": ["缺少总套数、月均去化或清盘周期的模型输入。"],
        }
    abm = _find_abm(report)
    scenarios = []
    for name, rate_factor in (("conservative", 0.75), ("base", 1.0), ("optimistic", 1.25)):
        scenarios.append(
            {
                "scenario": name,
                "monthly_absorption_rate_units": round(base_rate * rate_factor, 2),
                "sellout_months": round(base_months / rate_factor, 1),
                "rate_factor": rate_factor,
            }
        )
    return {
        "status": "ready",
        "evidence_type": "model_simulation",
        "total_units": source.get("total_units"),
        "scenarios": scenarios,
        "source_refs": list(source.get("source_refs") or ["decision_full.blueprint_logic.absorption_simulation"]),
        "evidence_confidence": _prune_private(
            abm.get("evidence_confidence") or abm.get("confidence") or {"score": 0.0}
        ),
        "simulation_stability": _prune_private(
            abm.get("simulation_stability") or {"score": 0.0}
        ),
        "limitations": [
            "保守/基准/乐观仅对月流速作透明倍率情景，不代表真实成交承诺。",
            "需用项目实际来访、认购、签约与退房数据滚动回测。",
        ],
    }


def _contains_traditional_marker(key: Any) -> bool:
    normalized = re.sub(r"[\s.-]+", "_", str(key or "").lower())
    return any(marker in normalized for marker in _TRADITIONAL_INVESTMENT_MARKERS)


def _sanitize_investment(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_investment(item)
            for key, item in value.items()
            if not _contains_traditional_marker(key)
            and str(key) not in _PRIVATE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_investment(item) for item in value]
    if isinstance(value, str) and any(
        marker in value.lower() for marker in _TRADITIONAL_INVESTMENT_MARKERS
    ):
        return "[blocked: traditional interpretation cannot enter investment case]"
    return deepcopy(value)


def _investment_case(
    report: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    investment_inputs = context.get("investment_inputs") or report.get("investment_inputs")
    if isinstance(investment_inputs, Mapping):
        refs = context.get("investment_source_refs") or report.get("investment_source_refs") or []
        result = build_investment_case(investment_inputs, source_refs=refs)
        if isinstance(result.get("evidence_confidence"), (int, float)):
            score = float(result["evidence_confidence"])
            result["evidence_confidence"] = {
                "score": score,
                "level": "high" if score >= 0.75 else ("medium" if score >= 0.55 else ("low" if score >= 0.35 else "undecidable")),
                "actionable": score >= 0.55,
            }
        if isinstance(result.get("simulation_stability"), (int, float)):
            score = float(result["simulation_stability"])
            result["simulation_stability"] = {
                "score": score,
                "level": "high" if score >= 0.75 else ("medium" if score >= 0.55 else ("low" if score >= 0.35 else "undecidable")),
                "affects_evidence_confidence": False,
            }
        result["boundary_check"] = {
            "passed": result.get("status") != "blocked" or not any(
                str(item).startswith("prohibited_input:")
                for item in result.get("evidence_gaps", [])
            ),
            "policy": "traditional interpretation prohibited from investment mathematics",
        }
        return result

    decision_full = _mapping(report.get("decision_full"))
    blueprint = _mapping(decision_full.get("blueprint_logic"))
    supplied = _mapping(
        report.get("investment_case") or blueprint.get("investment_case")
    )
    if supplied:
        result = _sanitize_investment(supplied)
        result.setdefault("status", "partial")
        if isinstance(result.get("evidence_confidence"), (int, float)):
            score = float(result["evidence_confidence"])
            result["evidence_confidence"] = {
                "score": score,
                "level": "high" if score >= 0.75 else ("medium" if score >= 0.55 else ("low" if score >= 0.35 else "undecidable")),
                "actionable": score >= 0.55,
            }
        if isinstance(result.get("simulation_stability"), (int, float)):
            score = float(result["simulation_stability"])
            result["simulation_stability"] = {
                "score": score,
                "level": "high" if score >= 0.75 else ("medium" if score >= 0.55 else ("low" if score >= 0.35 else "undecidable")),
                "affects_evidence_confidence": False,
            }
    else:
        summary = _mapping(decision_full.get("decision_summary"))
        finance = _mapping(decision_full.get("finance_agent"))
        if not summary and not finance:
            return {
                "status": "missing",
                "evidence_type": "model_simulation",
                "metrics": {},
                "evidence_gaps": ["缺少可追溯售价、成本、融资、现金流和土地报价输入。"],
                "boundary_check": {"passed": True, "traditional_fields_removed": 0},
            }
        result = {
            "status": "partial",
            "decision": _sanitize_investment(summary),
            "metrics": _sanitize_investment(finance),
            "evidence_gaps": ["需补齐收入、成本、融资与现金流后计算 ROI/IRR/回收期边界。"],
        }
    result["evidence_type"] = "model_simulation"
    result["boundary_check"] = {
        "passed": True,
        "policy": "traditional interpretation prohibited from investment mathematics",
    }
    return result


def _traditional_context(validated: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "site_boundary",
        "true_north_deg",
        "facing_azimuth_deg",
        "roads",
        "water",
        "terrain",
        "environment",
        "masterplan",
        "building_orientations",
        "main_entrance",
        "onsite_compass",
        "expert_review",
        "construction_year",
        "occupancy_year",
    )
    return {key: deepcopy(validated[key]) for key in keys if validated.get(key) is not None}


def _decision_contract(
    legacy: Mapping[str, Any],
    *,
    macro: Mapping[str, Any],
    social: Mapping[str, Any],
    traditional: Mapping[str, Any],
    competitors: Mapping[str, Any],
    investment: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the only reader-facing decision summary for v4 reports.

    Legacy summaries mixed product advice with fixed-ratio land bids and could
    remain visible even after the fail-closed investment engine blocked those
    numbers.  The v4 decision page therefore uses a small compatibility
    whitelist and derives financial eligibility only from ``investment_case``.
    """

    investment_status = str(investment.get("status") or "missing")
    legacy_summary = str(legacy.get("summary") or "").strip()
    legacy_financial_keys = {
        "land_price_range",
        "land_to_price_ratio",
        "irr",
        "roi",
        "land_bid",
        "land_bid_boundary",
    }
    legacy_financial_markers = (
        "irr",
        "roi",
        "拿地",
        "地价",
        "楼面价",
        "融资成本",
        "投资回报",
        "利润率",
    )
    unsafe_legacy_summary = any(key in legacy for key in legacy_financial_keys) or any(
        marker in legacy_summary.lower() for marker in legacy_financial_markers
    )
    investment_ready = investment_status in {"ready", "review"}
    if investment_ready:
        summary = (
            "财务模型已使用可追溯的收入、成本与节奏输入生成情景结果；"
            "拿地报价、ROI 与 IRR 只能按投资测算页的边界、来源和置信度使用，"
            "并保留人工复核。"
        )
        decision_state = "conditional_review"
    else:
        summary = (
            "当前证据仅支持继续补证、市场研判与产品推演，"
            "不支持形成拿地报价、ROI 或 IRR 结论。"
        )
        decision_state = "evidence_gate"

    preserved: dict[str, Any] = {}
    for key in (
        "recommended_area_range",
        "top_personas",
        "llm_used",
        "risk_grade",
        "far_source",
    ):
        if key in legacy:
            preserved[key] = _prune_private(legacy[key])

    gaps = [str(item) for item in _sequence(investment.get("evidence_gaps"))[:8]]
    source_refs: set[str] = set()
    for source in _sequence(macro.get("source_registry")):
        if isinstance(source, Mapping) and source.get("source_id"):
            source_refs.add(str(source["source_id"]))
    competitor_source_refs: set[str] = set()
    for item in _sequence(competitors.get("items")):
        if isinstance(item, Mapping):
            competitor_source_refs.update(
                str(ref) for ref in _sequence(item.get("source_refs"))
            )
    # The decision page links a bounded sample.  The full competitor evidence
    # remains in competitor_series/evidence_graph and should not flood the
    # 16:9 footer or source drawer.
    source_refs.update(sorted(competitor_source_refs)[:3])
    source_refs.update(str(ref) for ref in _sequence(investment.get("source_refs")))

    confidence = investment.get("evidence_confidence")
    if not isinstance(confidence, Mapping):
        confidence = macro.get("evidence_confidence")
    if not isinstance(confidence, Mapping):
        confidence = {"score": 0.0, "level": "undecidable", "actionable": False}

    result = {
        "status": "ready" if investment_ready else "partial",
        "headline": summary,
        "summary": summary,
        **(
            {"legacy_context_summary": legacy_summary}
            if legacy_summary and not unsafe_legacy_summary
            else {}
        ),
        "decision_state": decision_state,
        "decision_eligibility": "human_review_required",
        "investment_status": investment_status,
        "macro_cycle_state": _mapping(macro.get("cycle_assessment")).get("state"),
        "social_status": social.get("status") or "missing",
        "traditional_input_level": traditional.get("input_level") or "G0",
        "competitor_series_status": competitors.get("series_status") or "missing",
        "supporting_source_count": len(source_refs | competitor_source_refs),
        "legacy_financial_copy_suppressed": unsafe_legacy_summary,
        "legacy_summary_preserved": bool(legacy_summary) and not unsafe_legacy_summary,
        "evidence_confidence": _prune_private(confidence),
        "evidence_gaps": gaps,
        "source_refs": sorted(source_refs),
        **preserved,
    }
    return result


def _replace_legacy_financial_summary(
    report: dict[str, Any],
    decision: Mapping[str, Any],
    investment: Mapping[str, Any],
) -> None:
    """Keep old response shape while preventing superseded finance from leaking."""

    decision_full = report.get("decision_full")
    if not isinstance(decision_full, dict):
        return
    decision_full["decision_summary"] = deepcopy(dict(decision))
    blueprint = decision_full.get("blueprint_logic")
    if not isinstance(blueprint, dict):
        return
    blueprint["investment_case"] = deepcopy(dict(investment))
    for key in (
        "financial_indicator",
        "land_value_sensitivity",
        "quarterly_cash_flow",
    ):
        if key in blueprint:
            blueprint[key] = {
                "status": "superseded",
                "replacement": "investment_case",
                "reason": "legacy_financial_defaults_are_not_decision_eligible",
            }


def attach_report_intelligence(
    report_json: Mapping[str, Any],
    validated: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a privacy-safe report enriched with the DDS v4 contract."""
    if not isinstance(report_json, Mapping):
        raise TypeError("report_json must be a mapping")
    context = dict(validated or {})
    enriched = _prune_private(report_json)
    # Direct callers outside app.py may opt in explicitly.  The main API already
    # attaches this before Architecture Director so the packet can consume it.
    if (
        not isinstance(enriched.get("professional_intelligence"), Mapping)
        and (
            context.get("dcbbs_mode") is not None
            or context.get("dcbbs_db_path") is not None
        )
    ):
        try:
            from .dcbbs_intelligence import attach_dcbbs_professional_intelligence
        except ImportError:
            from dcbbs_intelligence import attach_dcbbs_professional_intelligence
        attach_dcbbs_professional_intelligence(enriched, context)
    parcel = _mapping(enriched.get("parcel"))
    meta = _mapping(enriched.get("meta"))
    city = str(context.get("city") or parcel.get("city") or "").strip()
    district = context.get("district") or parcel.get("district")
    macro_as_of = (
        context.get("as_of")
        or meta.get("generated_at")
        or meta.get("as_of")
    )

    macro_records = context.get("macro_records")
    existing_macro = _mapping(enriched.get("macro_context"))
    if macro_records is None and existing_macro and existing_macro.get("status") != "missing":
        macro = _prune_private(existing_macro)
    else:
        if macro_records is None:
            macro_records = load_local_macro_records(city) if city else []
        macro = build_macro_context(macro_records or [], city, as_of=macro_as_of)

    social_records = context.get("social_records")
    existing_social = _mapping(enriched.get("social_intelligence"))
    if social_records is None and existing_social:
        social = _prune_private(existing_social)
    else:
        if social_records is None:
            social_records = []
        social = build_social_intelligence(
            social_records,
            city,
            str(district) if district else None,
            int(context.get("social_window_days") or 180),
        )
    traditional = build_traditional_spatial_culture(
        parcel,
        _traditional_context(context),
        social,
    )
    if context.get("traditional_culture_mode") == "off":
        traditional = {
            **traditional,
            "status": "disabled",
            "traditional_readings": [],
            "consumer_perception_links": [],
            "evidence_gaps": [
                *list(traditional.get("evidence_gaps") or []),
                {"field": "traditional_culture_mode", "reason": "disabled_by_request"},
            ],
        }

    synthetic = _synthetic_personas(enriched)
    competitors = _competitor_series(enriched)
    premium = _premium_analysis(enriched)
    absorption = _absorption_forecast(enriched)
    investment = _investment_case(enriched, context)
    decision = _decision_contract(
        _mapping(enriched.get("decision")),
        macro=macro,
        social=social,
        traditional=traditional,
        competitors=competitors,
        investment=investment,
    )

    enriched.update(
        {
            "decision": decision,
            "macro_context": macro,
            "social_intelligence": social,
            "persona_evidence_profiles": deepcopy(
                social.get("persona_evidence_profiles") or []
            ),
            "synthetic_personas": synthetic,
            "traditional_spatial_culture": traditional,
            "competitor_series": competitors,
            "premium_analysis": premium,
            "absorption_forecast": absorption,
            "investment_case": investment,
        }
    )
    _replace_legacy_financial_summary(enriched, decision, investment)

    document = build_report_document(enriched)
    enriched["report_document"] = document
    for key in ("evidence_graph", "page_manifest", "source_registry", "qa"):
        enriched[key] = deepcopy(document[key])
    return enriched


__all__ = ["attach_report_intelligence"]
