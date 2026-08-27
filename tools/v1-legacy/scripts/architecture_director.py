"""Architecture Director runtime packet builder.

This module wraps the existing DDS report JSON with a structured, auditable
Architecture Director layer. It does not overwrite source facts; it creates a
DeliveryBrief, evidence nodes, case candidate board, design brief, marketing
report, execution strategy, and knowledge-gap register from current evidence.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha1
import re
from typing import Any


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _text(value: Any, fallback: str = "待接入") -> str:
    if value is None:
        return fallback
    s = str(value).strip()
    return s if s else fallback


def _short(value: Any, limit: int = 140) -> str:
    s = _text(value, "")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _confidence(sample_count: int, tier_weight: float = 0.7, cross_check: float = 0.55) -> float:
    if sample_count >= 20:
        sample = 1.0
    elif sample_count >= 10:
        sample = 0.75
    elif sample_count >= 5:
        sample = 0.55
    elif sample_count >= 1:
        sample = 0.25
    else:
        sample = 0.0
    score = 0.35 * tier_weight + 0.20 * sample + 0.15 * 0.75 + 0.15 * cross_check + 0.10 * 0.75 + 0.05 * 0.7
    return round(max(0.0, min(1.0, score)), 2)


def _confidence_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.55:
        return "medium"
    if score >= 0.35:
        return "low"
    return "undecidable"


def _risk_coeff(sample_count: int, has_l1: bool, has_conflict: bool = False) -> int:
    risk = 30
    if sample_count < 5:
        risk += 20
    if sample_count < 3:
        risk += 15
    if not has_l1:
        risk += 15
    if has_conflict:
        risk += 20
    return min(100, risk)


def _risk_label(coeff: int) -> str:
    if coeff <= 25:
        return "A"
    if coeff <= 45:
        return "B"
    if coeff <= 65:
        return "C"
    return "D"


def _stable_case_id(comp: dict[str, Any], idx: int) -> str:
    raw = "|".join([
        _text(comp.get("project_name") or comp.get("name"), f"case_{idx}"),
        _text(comp.get("url_amap") or comp.get("url") or comp.get("url_anjuke"), ""),
        _text(comp.get("lng"), ""),
        _text(comp.get("lat"), ""),
        _text(comp.get("distance_km"), ""),
    ])
    return "case_" + sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]


_ROLE_GATES = {
    "architecture_director": "红线与规划条件、竞品时序及核心财务输入均有来源，并由建筑总监签字后，设计 thesis 才能从草案升级为基线。",
    "planning_constraint_agent": "红线、真北、法定出入口、消防登高面、高差与周边道路齐全，且完成至少两个总图量化比选后，才可锁定总图。",
    "product_unit_master": "竞品面积／总价／成交／去化时序与客群证据补齐，且产品套数和去化模型口径一致后，才可锁定户型配比。",
    "luxury_aesthetic_agent": "材料、节点样板、成本限额、维护周期与真实客群偏好均可核验后，审美策略才可进入扩初。",
    "case_benchmark_agent": "项目身份、图片权属、气候、客群与尺度核验完成，并具备至少一个同城同类案例和一个反例后，案例才可进入主证明。",
    "market_value_agent": "至少五个有效住宅竞品具备30／90／180天价格、供应、促销、库存和去化序列，且异常值处理可复现后，才可用于定价。",
    "marketing_translation_agent": "每条卖点均绑定可交付设计动作、来源与禁用边界，并通过设计和法务复核后，才可对外传播。",
    "execution_pm_agent": "每个阻断缺口均有责任人、截止时间、交付物和验收人，依赖项关闭后，才可推进下一阶段。",
    "risk_trust_agent": "全部 blocking gaps 关闭；低置信结论已补证或降级；财务、社媒和传统文化边界检查通过后，才可升级决策状态。",
}

_ROLE_MARKET_EFFECTS = {
    "architecture_director": "形成一致产品命题与协同边界，证据未闭合前仅用于组织设计推演。",
    "planning_constraint_agent": "资源面、到达、噪声与楼栋排序影响可售性和楼栋价值梯度。",
    "product_unit_master": "影响面积段、总价覆盖与客群命中，但必须由真实市场和客群证据校准。",
    "luxury_aesthetic_agent": "影响可感知品质、到访转化和交付口碑，不能由氛围图单独证明。",
    "case_benchmark_agent": "案例只用于提出和检验空间机制，不构成本地价格或需求证明。",
    "market_value_agent": "限定可讨论的价格带、供需与去化假设；截面数据不能替代趋势。",
    "marketing_translation_agent": "影响卖点理解与转化路径，传播热度不能替代真实购买行为。",
    "execution_pm_agent": "保障关键资料、设计动作与市场验证按闸门顺序落地。",
    "risk_trust_agent": "暴露样本污染、口径冲突与陈旧性，阻止弱证据升级为共识。",
}

_ROLE_FINANCIAL_EFFECTS = {
    "architecture_director": "当前不直接改写售价、ROI或IRR，只作为后续成本与价值测试输入。",
    "planning_constraint_agent": "仅实测土方、日照、消防、地下室效率与可售资源排序可进入成本和货值。",
    "product_unit_master": "产品配比和可售面积核验后才影响货值；合成人格不得直接改写WTP或拿地价。",
    "luxury_aesthetic_agent": "只有成本限额和成交、问卷或行为证据支持的动作，才可作为溢价情景输入。",
    "case_benchmark_agent": "不得外推案例售价、成本、去化、ROI或IRR。",
    "market_value_agent": "市场证据只作为售价与去化情景输入，不直接生成拿地上限。",
    "marketing_translation_agent": "传播声量不得直接进入WTP、ROI、IRR或地价公式。",
    "execution_pm_agent": "未通过资料与责任闸门前，不释放投决参数或成本承诺。",
    "risk_trust_agent": "阻断无来源的售价、去化、ROI、IRR和地价结论进入正式投决。",
}


def _clean_visual_project_name(value: Any) -> str:
    raw = re.sub(r"^\d{8}", "", _text(value, "")).strip(" _-")
    raw = re.sub(r"_+", " · ", raw)
    parts = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\s*[·|]+\s*", raw)]
    ignored = {"design", "goa", "g", "豪宅分享", "案例分享", "住宅", "高档住宅"}
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part or part.casefold() in ignored:
            continue
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(part)
    return " · ".join(cleaned[:5]) or raw


def _visual_semantic_conflict(name: str, mechanism: str, target_text: str) -> str:
    residential_target = any(
        marker in target_text for marker in ("住宅", "居住", "住区", "改善", "豪宅", "社区")
    )
    non_residential = next(
        (
            marker
            for marker in ("总部", "办公", "商业建筑", "产业园", "厂房", "写字楼")
            if marker in mechanism
        ),
        "",
    )
    if residential_target and non_residential:
        return f"项目目录为住宅语境，但图像描述出现“{non_residential}”，项目身份与图像语义冲突。"
    if ("Miami" in name or "迈阿密" in name) and "上海" in name:
        return "项目名同时包含迈阿密与上海，地域元数据冲突。"
    return ""


def _load_learning_summary() -> dict[str, Any]:
    try:
        from architecture_learning import summarize_learning_events
        summary = summarize_learning_events()
        return summary if isinstance(summary, dict) else {}
    except Exception as exc:
        return {"status": "unavailable", "event_count": 0, "error": str(exc)}


def _learning_adjustments(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    adjustments: dict[str, dict[str, Any]] = {}
    for item in _l(summary.get("retrieval_adjustments")):
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        delta = _num(item.get("delta"), 0.0) or 0.0
        adjustments[evidence_id] = {
            "delta": max(-0.4, min(0.3, delta)),
            "reason": item.get("reason") or "learning_event",
        }
    return adjustments


def _packet_id(report: dict[str, Any]) -> str:
    parcel = _d(report.get("parcel"))
    raw = "|".join([
        _text(parcel.get("city"), "DDS"),
        _text(parcel.get("address"), "parcel"),
        _text(_d(report.get("meta")).get("generated_at"), datetime.now().isoformat(timespec="seconds")),
    ])
    return "adp_" + sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _source_matrix(report: dict[str, Any]) -> list[dict[str, Any]]:
    meta = _d(report.get("meta"))
    local = _d(meta.get("local_data"))
    market = _d(report.get("market"))
    parcel = _d(report.get("parcel"))
    sources = [
        {
            "source_id": "S1",
            "tier": "L2",
            "name": "DDS local listing pool",
            "path_or_url": local.get("csv_file") or "Vault/2026新楼盘/",
            "sample_count": market.get("sample_size") or len(_l(market.get("competitors"))),
            "freshness": meta.get("data_timestamp") or meta.get("generated_at"),
            "use": "competitor, price and product evidence"
        },
        {
            "source_id": "S2",
            "tier": "L2",
            "name": "GIS / AMap location evidence",
            "path_or_url": "https://lbs.amap.com/",
            "sample_count": len(_l(_d(report.get("amenities")).get("school"))) + len(_l(_d(report.get("amenities")).get("transport"))),
            "freshness": meta.get("generated_at"),
            "use": "location, POI and map preview evidence"
        },
        {
            "source_id": "S3",
            "tier": "Derived",
            "name": "DDS decision engine",
            "path_or_url": "scripts/dds_decision_engine.py",
            "sample_count": None,
            "freshness": meta.get("generated_at"),
            "use": "ABM, premium, CEO and risk synthesis"
        },
        {
            "source_id": "S4",
            "tier": "L2",
            "name": "ArchDDS case and benchmark rules",
            "path_or_url": "skills/arch-dds/references/case-selection.md",
            "sample_count": len(_l(_d(report.get("market")).get("competitors"))),
            "freshness": meta.get("generated_at"),
            "use": "case candidate board gate and visual proof rules"
        }
    ]
    sources.append({
        "source_id": "S6",
        "tier": "L2",
        "name": "ArchLib visual role index",
        "path_or_url": "data_out/archlib_visual_roles.jsonl",
        "sample_count": "48471 indexed / 21295 usable in latest DDS-side summary",
        "freshness": meta.get("generated_at"),
        "use": "role-aware intention images, evidence images, masterplans, unit plans, facades, luxury and sales-center references"
    })
    macro_sources = _l(_d(report.get("macro_context")).get("source_registry"))
    if macro_sources:
        macro_source = _d(macro_sources[0])
        sources.append({
            "source_id": "S7",
            "tier": "L1" if macro_source.get("source_tier") in {"official", "statistics"} else "L2",
            "name": macro_source.get("source_title") or macro_source.get("source_name") or "macro source",
            "path_or_url": macro_source.get("source_url") or "macro_context.source_registry",
            "sample_count": 1,
            "freshness": macro_source.get("published_at") or meta.get("generated_at"),
            "use": "city macro cycle and construction context",
        })
    if parcel.get("city") == "武汉":
        sources.append({
            "source_id": "S5",
            "tier": "L2",
            "name": "Wuhan transaction data",
            "path_or_url": "Vault/成交数据/成交-武汉.csv",
            "sample_count": "20307 in data pool manifest",
            "freshness": meta.get("generated_at"),
            "use": "transaction and resale context when routed into charts"
        })
    professional = _d(report.get("professional_intelligence"))
    if professional:
        counts = _d(professional.get("counts"))
        sources.append({
            "source_id": "S8",
            "tier": "L3",
            "name": "DCBBS professional intelligence",
            "path_or_url": "https://www.dcbbs.com/",
            "sample_count": counts.get("selected") or len(_l(professional.get("items"))),
            "freshness": meta.get("generated_at"),
            "use": (
                "professional discovery, case-mechanism reference and original-source tracing; "
                "never direct evidence for price, absorption, cost, ROI, IRR, land bid or C-end consensus"
            ),
        })
    return sources


def _evidence_nodes(report: dict[str, Any], source_matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    market = _d(report.get("market"))
    decision = _d(report.get("decision"))
    full = _d(report.get("decision_full"))
    parcel = _d(report.get("parcel"))
    social = _d(report.get("social_intelligence"))
    traditional = _d(report.get("traditional_spatial_culture"))
    competitor_series = _d(report.get("competitor_series"))
    product = _d(report.get("product")) or _d(full.get("unit_mix_agent"))
    absorption = _d(report.get("absorption_forecast"))
    legacy_absorption = _d(_d(full.get("blueprint_logic")).get("absorption_simulation"))
    investment = _d(report.get("investment_case"))
    macro = _d(report.get("macro_context"))
    ceo = _d(decision.get("ceo")) or _d(full.get("ceo_aggregator"))
    sample_count = int(_num(market.get("sample_size") or len(_l(market.get("competitors"))), 0) or 0)
    conf = _confidence(sample_count)
    risk = _risk_coeff(sample_count, has_l1=False)
    nodes = []

    def add(
        claim: str,
        claim_type: str,
        refs: list[str],
        confidence: float,
        risk_coeff: int,
        note: str,
        supports_roles: list[str],
        conflicts: list[str] | None = None,
    ) -> None:
        nodes.append({
            "evidence_id": f"E{len(nodes)+1}",
            "claim": claim,
            "claim_type": claim_type,
            "source_refs": refs,
            "confidence": confidence,
            "confidence_label": _confidence_label(confidence),
            "risk_coeff": risk_coeff,
            "risk_label": _risk_label(risk_coeff),
            "conflicts": conflicts or [],
            "needs_human_review": confidence < 0.55 or risk_coeff >= 66,
            "note": note,
            "supports_roles": supports_roles,
        })

    avg = market.get("avg_price") or market.get("avg_unit_price") or market.get("price_avg")
    add(
        f"周边竞品价格中枢约为 {_text(avg)} 元/㎡，有效样本 {sample_count} 个。",
        "fact" if sample_count else "missing",
        ["S1"],
        conf,
        risk,
        "市场结论必须随样本数和距离半径一起呈现。",
        ["architecture_director", "product_unit_master", "market_value_agent", "marketing_translation_agent", "risk_trust_agent"],
    )

    land = _d(decision.get("land_price_range"))
    if land:
        add(
            "DDS 已形成保守/均衡/激进拿地边界，但属于 Derived 推演，不能当作 L1 成交或挂牌事实。",
            "model_output",
            ["S1", "S3"],
            round(min(conf, 0.68), 2),
            max(risk, 50),
            "进入投委会前需要 L1 挂牌、税费、融资、成本和去化假设复核。",
            ["architecture_director", "execution_pm_agent", "risk_trust_agent"],
        )
    else:
        add(
            "拿地边界待接入。",
            "missing",
            ["S3"],
            0.25,
            70,
            "缺少 land_price_range。",
            ["architecture_director", "execution_pm_agent", "risk_trust_agent"],
        )

    if ceo:
        add(
            f"CEO 综合评分为 {_text(ceo.get('total_score'))}，等级 {_text(ceo.get('grade'))}。",
            "model_output",
            ["S3"],
            _num(ceo.get("overall_confidence"), 0.6) or 0.6,
            max(risk, 45),
            "CEO 分数用于排序和质询，不替代最终投资判断。",
            ["architecture_director", "execution_pm_agent", "risk_trust_agent"],
        )

    add(
        "设计策略必须从证据触发，经过设计动作，再解释价值机制和风险。",
        "inference",
        ["S4"],
        0.72,
        35,
        "这是 ArchDDS candidate-board 和 report-framework 的运行规则。",
        ["architecture_director", "planning_constraint_agent", "product_unit_master", "luxury_aesthetic_agent", "case_benchmark_agent", "marketing_translation_agent", "execution_pm_agent", "risk_trust_agent"],
    )

    has_boundary = bool(parcel.get("site_boundary") or report.get("site_boundary"))
    has_true_north = parcel.get("true_north") is not None or report.get("true_north_deg") is not None
    add(
        (
            "当前只有坐标级场地输入，缺少红线、真北、法定出入口、道路高程和消防条件。"
            if not (has_boundary and has_true_north)
            else "场地已具备红线与真北输入，但仍需核对法定出入口、道路高程与消防条件。"
        ),
        "missing" if not (has_boundary and has_true_north) else "fact",
        ["S2"],
        0.82 if not (has_boundary and has_true_north) else 0.62,
        78 if not (has_boundary and has_true_north) else 52,
        "坐标只能支持定位，不能支持总图、风水形势或成本判断。",
        ["architecture_director", "planning_constraint_agent", "execution_pm_agent", "risk_trust_agent"],
    )

    social_missing = social.get("status") in {None, "", "missing", "blocked"}
    add(
        (
            "目标窗口内缺少可用的公开或授权C端社媒记录，合成人格尚未被现实意见校准。"
            if social_missing
            else f"社媒模块已接入 {social.get('record_count', 0)} 条可用记录。"
        ),
        "missing" if social_missing else "fact",
        ["S3"],
        0.88 if social_missing else _num(_d(social.get("evidence_confidence")).get("score"), 0.5) or 0.5,
        72 if social_missing else 50,
        "社媒只能有界校准偏好与风险，不能直接改写WTP、ROI、IRR或地价。",
        ["product_unit_master", "luxury_aesthetic_agent", "marketing_translation_agent", "risk_trust_agent"],
    )

    series_snapshot = competitor_series.get("series_status") == "snapshot_only" or competitor_series.get("status") in {"missing", "partial", None}
    add(
        (
            "竞品仅有价格截面，缺少30／90／180天供应、加推、促销、库存和去化序列。"
            if series_snapshot
            else "竞品已具备连续价格、供应、促销、库存与去化序列。"
        ),
        "missing" if series_snapshot else "fact",
        ["S1"],
        0.86 if series_snapshot else 0.68,
        74 if series_snapshot else 46,
        "价格截面可描述当前样本，不能证明趋势或去化速度。",
        ["product_unit_master", "market_value_agent", "marketing_translation_agent", "risk_trust_agent"],
    )

    product_units = int(_num(product.get("total_units"), 0) or 0)
    add(
        (
            f"产品模块以 {product_units} 套、{int(_num(product.get('n_personas'), 0) or 0)} 个合成人格进行产品配比推演。"
            if product_units
            else "产品配比尚无可审计的套数、面积段和货值基准。"
        ),
        "model_output" if product_units else "missing",
        ["S3"],
        0.46 if product_units else 0.82,
        60 if product_units else 72,
        "模拟稳定度不能替代真实客群、成交和支付力证据。",
        ["architecture_director", "product_unit_master", "market_value_agent", "risk_trust_agent"],
    )

    legacy_units = int(_num(legacy_absorption.get("total_units"), 0) or 0)
    if product_units and legacy_units and product_units != legacy_units:
        add(
            f"产品模块为 {product_units} 套，但旧去化模拟为 {legacy_units} 套，模型口径冲突。",
            "conflict",
            ["S3"],
            0.92,
            84,
            "冲突关闭前不得输出清盘周期、月均流速或去化承诺。",
            ["architecture_director", "product_unit_master", "market_value_agent", "execution_pm_agent", "risk_trust_agent"],
            ["产品与去化模型的总套数分母不一致。"],
        )
    else:
        absorption_missing = absorption.get("status") in {None, "", "missing", "blocked"}
        add(
            "去化预测缺少总套数、真实月均流速、价格条件和销售回测。" if absorption_missing else "去化模型口径已与产品套数对齐。",
            "missing" if absorption_missing else "model_output",
            ["S3"],
            0.86 if absorption_missing else 0.52,
            76 if absorption_missing else 56,
            "去化只能以情景呈现，不能把合成模拟当作实测销售。",
            ["product_unit_master", "market_value_agent", "execution_pm_agent", "risk_trust_agent"],
        )

    investment_blocked = investment.get("status") in {None, "", "missing", "blocked"}
    add(
        "投资测算缺少售价、可售面积、地价、建安、税费、融资和节奏等完整输入。" if investment_blocked else "投资测算已具备可追溯输入和情景。",
        "missing" if investment_blocked else "model_output",
        ["S3"],
        0.9 if investment_blocked else 0.58,
        82 if investment_blocked else 54,
        "输入未闭合时ROI、IRR、回收期和拿地边界必须保持blocked。",
        ["architecture_director", "execution_pm_agent", "risk_trust_agent"],
    )

    traditional_g0 = traditional.get("input_level") in {None, "", "G0"}
    add(
        "传统空间文化模块停留在G0，禁止判断坐向、四象、明堂、水口或吉凶。" if traditional_g0 else f"传统空间文化输入等级为 {traditional.get('input_level')}。",
        "missing" if traditional_g0 else "traditional_interpretation",
        ["S2"],
        0.9 if traditional_g0 else 0.5,
        76 if traditional_g0 else 55,
        "法律安全与实测物理始终优先，传统解释不得进入财务公式。",
        ["planning_constraint_agent", "risk_trust_agent"],
    )

    add(
        "ArchLib可提供角色化空间机制与视觉证据，但项目身份、图注、权属和交付结果仍需独立核验。",
        "inference",
        ["S4", "S6"],
        0.55,
        58,
        "案例匹配分是检索相似度，不是市场事实置信度。",
        ["architecture_director", "planning_constraint_agent", "product_unit_master", "luxury_aesthetic_agent", "case_benchmark_agent", "marketing_translation_agent", "execution_pm_agent", "risk_trust_agent"],
    )

    macro_partial = macro.get("status") in {None, "", "missing", "partial"}
    add(
        "宏观模块已有年度官方统计，但缺少近180天房地产与建筑业月度／季度运营序列。" if macro_partial else "宏观模块具备近期多源房地产与建筑业运营序列。",
        "missing" if macro_partial else "fact",
        ["S7"] if any(src.get("source_id") == "S7" for src in source_matrix) else ["S3"],
        0.72 if macro_partial else 0.68,
        68 if macro_partial else 48,
        "年度结构判断不能替代当下成交、库存、开工和投资趋势。",
        ["architecture_director", "market_value_agent", "risk_trust_agent"],
    )
    professional = _d(report.get("professional_intelligence"))
    for raw_node in _l(professional.get("evidence_nodes")):
        node = _d(raw_node)
        if not node.get("claim"):
            continue
        raw_confidence = node.get("confidence")
        if isinstance(raw_confidence, dict):
            confidence = _num(raw_confidence.get("score"), 0.35) or 0.35
        else:
            confidence = _num(raw_confidence, 0.35) or 0.35
        confidence = round(max(0.0, min(1.0, confidence)), 2)
        risk_coeff = int(round((1.0 - confidence) * 100))
        nodes.append({
            "evidence_id": _text(node.get("evidence_id"), f"E{len(nodes)+1}"),
            "claim": _text(node.get("claim")),
            "claim_type": _text(node.get("evidence_type") or node.get("claim_type"), "inference"),
            "source_refs": _l(node.get("source_refs")),
            "confidence": confidence,
            "confidence_label": _confidence_label(confidence),
            "risk_coeff": risk_coeff,
            "risk_label": _risk_label(risk_coeff),
            "conflicts": _l(node.get("counter_evidence_refs")),
            "needs_human_review": bool(node.get("needs_human_review", True)),
            "note": "DCBBS 聚合记录保持 L3；只有已核验原始来源的对应 claim scope 可升档。",
            "supports_roles": _l(node.get("supports_roles")) or ["case_benchmark_agent", "risk_trust_agent"],
            "decision_eligibility": _text(node.get("decision_eligibility"), "discovery_only"),
        })
    return nodes


def _case_candidate_board(report: dict[str, Any], learning_summary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    comps = _l(_d(report.get("market")).get("competitors"))
    adjustments = _learning_adjustments(learning_summary or {})
    board = []
    for idx, comp in enumerate(comps[:12], 1):
        name = comp.get("project_name") or comp.get("name") or f"周边案例 {idx}"
        candidate_id = _stable_case_id(comp, idx)
        distance = _num(comp.get("distance_km"), 99) or 99
        has_price = comp.get("unit_price_cny") not in (None, "")
        has_image = bool(comp.get("thumbnail"))
        has_map = bool(comp.get("url_amap") or (comp.get("lng") and comp.get("lat")))
        base_score = 40 + (20 if has_price else 0) + (15 if has_image else 0) + (15 if has_map else 0) + max(0, 10 - int(min(distance, 10)))
        learning = adjustments.get(candidate_id) or adjustments.get(str(name))
        learning_delta = _num(_d(learning).get("delta"), 0.0) or 0.0
        score = int(max(0, min(100, round(base_score + learning_delta * 100))))
        fit = "强匹配" if score >= 80 else "可用参考" if score >= 65 else "替代参考" if score >= 50 else "不采用"
        board.append({
            "evidence_id": candidate_id,
            "case_kind": "market_competitor",
            "locality": "same_city",
            "Strategy": "竞品压力 / 区位证据 / 案例可视化",
            "Selected case": name,
            "Fit": fit,
            "Score": score,
            "base_score": base_score,
            "learning_delta": round(learning_delta, 3),
            "learning_reason": _d(learning).get("reason") or "",
            "original_rank": idx,
            "Why": f"距离 {comp.get('distance_km', '待核验')}km，价格字段 {'完整' if has_price else '缺失'}，图片字段 {'可用' if has_image else '待接入'}。",
            "Backup": comps[idx].get("project_name") if idx < len(comps) and isinstance(comps[idx], dict) else "待候选",
            "Rejected": "无图片/无坐标/无价格的竞品不得作为主证明案例" if score < 65 else "低相关案例进入备选池",
            "source_refs": ["S1", "S2", "S4"],
            "source_path": comp.get("url_amap") or comp.get("url") or comp.get("url_anjuke") or "market.competitors",
            "image_role": "evidence_image" if has_image else "non_case_document_pending",
        })
    board.sort(key=lambda x: (x.get("Score", 0), x.get("learning_delta", 0), -x.get("original_rank", 99)), reverse=True)
    return board


def _professional_case_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate selected professional documents into mechanism-only candidates.

    These records are discovery aids, not market analogues.  Keeping them on the
    candidate board makes the design mechanism traceable without allowing an
    archive hit to become price, absorption or investment evidence.
    """
    professional = _d(report.get("professional_intelligence"))
    board: list[dict[str, Any]] = []
    resources = [
        _d(item)
        for item in _l(professional.get("items"))
        if _d(item).get("record_kind") == "resource"
    ]
    for index, item in enumerate(resources[:6], 1):
        title = _text(item.get("title"), f"专业资料 {index}")
        raw_score = int(_num(item.get("retrieval_score"), 0) or 0)
        score = max(45, min(72, 45 + raw_score // 4))
        locality = _text(item.get("locality"), "unknown")
        origin = _d(item.get("original_source"))
        source_refs = ["S8", _text(item.get("source_id"))]
        if origin.get("source_id"):
            source_refs.append(_text(origin.get("source_id")))
        summary = _text(item.get("summary"), "需回到原文提炼可迁移的空间或产品机制。")
        board.append({
            "evidence_id": f"case:{_text(item.get('source_id'), str(index))}",
            "case_kind": "dcbbs_document",
            "locality": locality,
            "Strategy": "专业资料发现 / 案例机制候选 / 原始来源追索",
            "Selected case": title,
            "Fit": "机制候选",
            "Score": score,
            "base_score": score,
            "learning_delta": 0.0,
            "original_rank": index,
            "Why": summary,
            "mechanism": "仅提取经原文复核、可转译成设计任务的空间与产品机制。",
            "transfer_actions": ["回到原始文件定位图页与原句，由设计负责人形成可测量任务。"],
            "transfer_conditions": ["完成项目身份、地域、图注、权属与交付结果核验。"],
            "conflicts": ["DCBBS 为专业聚合档案，库内上传日期不等于报告发布日期。"],
            "prohibited_analogies": [
                "不得直接类比售价、去化、库存、成本、回报或拿地边界。",
                "不得将专业媒体、机构或开发商内容标记为 C 端共识。",
            ],
            "source_refs": source_refs,
            "image_role": "non_case_document",
            "rights_status": _text(item.get("rights_status"), "restricted/internal-research"),
            "confidence": 0.35,
            "decision_eligibility": "mechanism_only",
        })
    return board


def _strategy_tags(report: dict[str, Any], user_text: str | None = None) -> list[str]:
    blob = " ".join([
        _text(user_text, ""),
        _text(_d(report.get("parcel")).get("vision"), ""),
        _text(_d(report.get("parcel")).get("address"), ""),
        _text(_d(report.get("decision")).get("summary"), ""),
    ])
    tag_map = {
        "低密": ["低密", "别墅", "庭院", "会所", "礼序"],
        "改善": ["改善", "大平层", "户型", "会所", "景观"],
        "豪宅": ["豪宅", "顶豪", "礼序", "私密", "石材", "会所"],
        "度假": ["度假", "热带", "泳池", "庭院", "会所"],
        "TOD": ["TOD", "塔楼", "复合", "公区", "城市界面"],
        "风险": ["风险", "竞品", "价值", "总图", "户型"],
    }
    tags: list[str] = []
    for key, values in tag_map.items():
        if key.lower() in blob.lower():
            tags.extend(values)
    defaults = ["豪宅", "礼序", "会所", "户型", "总图", "立面", "景观"]
    tags.extend(defaults)
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out[:12]


def _visual_case_candidates(report: dict[str, Any], learning_summary: dict[str, Any] | None = None,
                            user_text: str | None = None) -> list[dict[str, Any]]:
    try:
        from archlib_visual_roles import search_visual_assets
    except Exception:
        return []

    roles = [
        "intention_image", "evidence_image", "masterplan", "unit_plan",
        "facade_detail", "luxury_aesthetic", "sales_center",
    ]
    parcel = _d(report.get("parcel"))
    city = _text(parcel.get("city"), "")
    province = {
        "济南": "山东", "济宁": "山东", "青岛": "山东",
        "武汉": "湖北", "杭州": "浙江", "宁波": "浙江",
        "南京": "江苏", "苏州": "江苏", "合肥": "安徽",
        "成都": "四川", "西安": "陕西", "长沙": "湖南",
        "广州": "广东", "深圳": "广东", "三亚": "海南", "海口": "海南",
    }.get(city, "")
    tags = list(dict.fromkeys([
        city,
        province,
        _text(parcel.get("district"), ""),
        *_strategy_tags(report, user_text),
    ]))
    tags = [tag for tag in tags if tag]
    intent = " ".join([_text(user_text, ""), _text(parcel.get("vision"), ""), _text(parcel.get("address"), "")]).strip()
    try:
        result = search_visual_assets(intent=intent, strategy_tags=tags, must_have_roles=roles, top_k=120)
    except Exception:
        return []

    adjustments = _learning_adjustments(learning_summary or {})
    board: list[dict[str, Any]] = []
    target_text = " ".join([
        _text(user_text, ""),
        _text(parcel.get("vision"), ""),
        _text(parcel.get("address"), ""),
    ])
    tropical_markers = ("三亚", "海口", "热带", "海滨", "海岸", "棕榈", "度假别墅", "泳池")
    target_is_tropical = any(marker in target_text for marker in tropical_markers)
    seen_project_roles: set[tuple[str, str]] = set()
    seen_project_names: set[str] = set()
    role_counts: dict[str, int] = {}
    for idx, asset in enumerate(_l(result.get("items")), 1):
        if not isinstance(asset, dict):
            continue
        evidence_id = str(asset.get("asset_id") or f"visual_{idx}")
        learning = adjustments.get(evidence_id) or adjustments.get(str(asset.get("project") or ""))
        learning_delta = _num(_d(learning).get("delta"), 0.0) or 0.0
        asset_score = _num(asset.get("asset_score"), 0.5) or 0.5
        visual_quality = _num(asset.get("visual_quality_score"), 0.5) or 0.5
        visual_quality = max(0.0, min(1.0, visual_quality))
        role = asset.get("image_role") or "evidence_image"
        raw_name = asset.get("project") or asset.get("path") or f"ArchLib 视觉候选 {idx}"
        name = _clean_visual_project_name(raw_name)
        if any(marker in str(name) for marker in ("文件夹", "框架梳理", "_thumb_pdf", "未命名项目")):
            continue
        if any(
            str(name).startswith(marker)
            for marker in ("豪宅展示区", "野奢住宅", "现代豪宅", "高端住宅入口")
        ):
            continue
        if role_counts.get(str(role), 0) >= 2:
            continue
        project_role = (str(name).strip().lower(), str(role))
        project_name_key = str(name).strip().casefold()
        if project_role in seen_project_roles or project_name_key in seen_project_names:
            continue
        asset_blob = " ".join([
            str(name),
            _text(asset.get("path"), ""),
            _text(asset.get("one_liner"), ""),
            " ".join(str(item) for item in _l(asset.get("design_keywords"))),
        ])
        locality = (
            "same_city" if city and city in asset_blob
            else "same_region" if province and province in asset_blob
            else "cross_city"
        )
        climate_mismatch = (not target_is_tropical) and any(
            marker in asset_blob for marker in tropical_markers
        )
        mechanism = _short(asset.get("one_liner"), 120) or f"{role} 视觉机制待人工复核"
        semantic_conflict = _visual_semantic_conflict(name, mechanism, target_text)
        if semantic_conflict:
            continue
        seen_project_roles.add(project_role)
        seen_project_names.add(project_name_key)
        role_counts[str(role)] = role_counts.get(str(role), 0) + 1
        geography_adjustment = 14 if locality == "same_city" else 7 if locality == "same_region" else -5
        climate_adjustment = -18 if climate_mismatch else 0
        bounded_query_score = max(0.0, min(1.2, asset_score))
        base_score = int(round(45 + visual_quality * 25 + (bounded_query_score - 0.5) * 10))
        score = int(max(0, min(100, round(
            base_score + geography_adjustment + climate_adjustment + learning_delta * 100
        ))))
        if climate_mismatch:
            score = min(score, 64)
        fit = "强匹配" if score >= 80 else "可用参考" if score >= 65 else "替代参考" if score >= 50 else "不采用"
        climate_fit = "mismatch" if climate_mismatch else "not_assessed"
        role_label = {
            "masterplan": "总图机制", "unit_plan": "户型机制",
            "facade_detail": "立面细节", "luxury_aesthetic": "豪宅审美",
            "sales_center": "展示区与售楼中心", "intention_image": "设计意向",
            "evidence_image": "实证图片",
        }.get(str(role), str(role))
        locality_label = {
            "same_city": "同城", "same_region": "同省", "cross_city": "异地",
        }.get(locality, locality)
        transfer_action = {
            "masterplan": "转译为入口、组团、资源面、消防与分期的总图比选。",
            "unit_plan": "转译为面积效率、采光、收纳与家政动线的户型校核。",
            "facade_detail": "转译为材料、分格、节点耐久与成本上限。",
            "luxury_aesthetic": "只转译礼序、私密、尺度和材料触感。",
            "sales_center": "转译为到达、停顿、转折、展示与后续运营路径。",
        }.get(role, "只提取可执行空间机制，不复制案例形态。")
        rejected = (
            "气候、客群或产品类型不一致，只能作机制参考，不得作为本地市场或产品强证明。"
            if climate_mismatch
            else "若与地块客群、尺度、成本或产品策略不匹配，只作机制参考，不进入主证明。"
        )
        board.append({
            "evidence_id": evidence_id,
            "Strategy": "ArchLib 视觉角色 / 意向图 / 设计机制",
            "Selected case": name,
            "Fit": fit,
            "Score": score,
            "base_score": base_score,
            "geography_adjustment": geography_adjustment,
            "climate_adjustment": climate_adjustment,
            "learning_delta": round(learning_delta, 3),
            "learning_reason": _d(learning).get("reason") or "",
            "original_rank": 100 + idx,
            "Why": f"视觉角色 {role_label}，地域关系 {locality_label}，视角 {asset.get('view_type') or '待标注'}，场景 {asset.get('scene_part') or '待标注'}；{mechanism}",
            "Backup": "同角色次级候选",
            "Rejected": rejected,
            "case_kind": "archlib_visual",
            "locality": locality,
            "climate_fit": climate_fit,
            "decision_eligibility": "mechanism_only",
            "metadata_consistency_status": "consistent",
            "identity_status": "unverified",
            "mechanism": mechanism,
            "transfer_action": transfer_action,
            "source_refs": ["S4", "S6"],
            "source_path": asset.get("path") or "data_out/archlib_visual_roles.jsonl",
            "source_jsonl": asset.get("source_jsonl") or "data_out/archlib_visual_roles.jsonl",
            "image_role": role,
            "visual_asset": {
                "asset_id": evidence_id,
                "path": asset.get("path") or "",
                "project": asset.get("project") or "",
                "view_type": asset.get("view_type") or "",
                "scene_part": asset.get("scene_part") or "",
                "image_role": role,
                "arch_style": _l(asset.get("arch_style")),
                "facade_material": _l(asset.get("facade_material")),
                "design_keywords": _l(asset.get("design_keywords"))[:10],
                "one_liner": asset.get("one_liner") or "",
                "visual_quality_score": asset.get("visual_quality_score"),
                "asset_score": asset.get("asset_score"),
            },
        })
        if len(board) >= 10:
            break
    return board
def _knowledge_gaps(report: dict[str, Any], evidence_nodes: list[dict[str, Any]], case_board: list[dict[str, Any]],
                    learning_summary: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    parcel = _d(report.get("parcel"))
    has_boundary = bool(parcel.get("site_boundary") or report.get("site_boundary"))
    has_true_north = parcel.get("true_north") is not None or report.get("true_north_deg") is not None
    if not (has_boundary and has_true_north):
        gaps.append({
            "gap_id": "l1_planning_and_listing_missing",
            "severity": "blocking_for_final_investment",
            "blocking_section": "architecture_director planning_constraint_agent execution_pm_agent risk_trust_agent",
            "recommended_action": "补入挂牌文件、红线、控规、真北、法定出入口与客户任务书，或把结论限定为坐标级初筛。"
        })
    series = _d(report.get("competitor_series"))
    if series.get("series_status") == "snapshot_only" or series.get("status") in {None, "", "missing", "partial"}:
        gaps.append({
            "gap_id": "competitor_time_series_missing",
            "severity": "blocking_for_pricing",
            "blocking_section": "product_unit_master market_value_agent marketing_translation_agent risk_trust_agent",
            "recommended_action": "补齐竞品30／90／180天价格、供应、加推、促销、库存与去化序列。",
        })
    social = _d(report.get("social_intelligence"))
    if social.get("status") in {None, "", "missing", "blocked"}:
        gaps.append({
            "gap_id": "social_consumer_evidence_missing",
            "severity": "confidence_affecting",
            "blocking_section": "product_unit_master luxury_aesthetic_agent marketing_translation_agent risk_trust_agent",
            "recommended_action": "接入目标窗口内公开或授权C端记录；无数据时保留专家先验并明确missing。",
        })
    investment = _d(report.get("investment_case"))
    if investment.get("status") in {None, "", "missing", "blocked"}:
        gaps.append({
            "gap_id": "investment_inputs_blocked",
            "severity": "blocking_for_final_investment",
            "blocking_section": "architecture_director execution_pm_agent risk_trust_agent",
            "recommended_action": "补齐售价、可售面积、地价、建安、税费、融资与销售节奏，并保留来源。",
        })
    product_units = int(_num((_d(report.get("product")) or _d(_d(report.get("decision_full")).get("unit_mix_agent"))).get("total_units"), 0) or 0)
    legacy_units = int(_num(_d(_d(_d(report.get("decision_full")).get("blueprint_logic")).get("absorption_simulation")).get("total_units"), 0) or 0)
    if product_units and legacy_units and product_units != legacy_units:
        gaps.append({
            "gap_id": "product_absorption_denominator_conflict",
            "severity": "blocking_for_absorption",
            "blocking_section": "architecture_director product_unit_master market_value_agent execution_pm_agent risk_trust_agent",
            "recommended_action": f"统一产品与去化模型总套数口径：当前分别为 {product_units} 与 {legacy_units} 套。",
        })
    if any(x.get("confidence_label") in ("low", "undecidable") for x in evidence_nodes):
        gaps.append({
            "gap_id": "low_confidence_claims",
            "severity": "confidence_affecting",
            "blocking_section": "architecture_director risk_trust_agent",
            "recommended_action": "对低置信 claim 补样本、补来源或降级为假设。"
        })
    if not case_board or all(x.get("Fit") != "强匹配" for x in case_board):
        gaps.append({
            "gap_id": "case_board_needs_stronger_visual_proof",
            "severity": "confidence_affecting",
            "blocking_section": "case_benchmark_agent planning_constraint_agent luxury_aesthetic_agent marketing_translation_agent",
            "recommended_action": "从 ArchLib 视觉库按 image_role 和 strategy_tag 重建候选板。"
        })
    learning_count = int(_num(_d(learning_summary).get("event_count"), 0) or 0)
    if learning_count <= 0 and "learning_event" not in report:
        gaps.append({
            "gap_id": "learning_event_store_empty",
            "severity": "evolution_pending",
            "blocking_section": "learning_loop",
            "recommended_action": "记录案例采纳/否决、段落重写和真实回测。"
        })
    return gaps


def _case_refs_for_roles(case_board: list[dict[str, Any]], roles: list[str], limit: int = 4) -> list[dict[str, Any]]:
    wanted = set(roles)
    refs: list[dict[str, Any]] = []
    matching = [
        item for item in case_board
        if not wanted or str(item.get("image_role") or "") in wanted
    ]
    specialized = bool(wanted - {"evidence_image"})
    matching.sort(
        key=lambda item: (
            specialized and str(item.get("case_kind") or "") == "archlib_visual",
            str(item.get("locality") or "") == "same_city",
            item.get("Fit") == "强匹配",
            _num(item.get("Score"), 0) or 0,
        ),
        reverse=True,
    )
    for item in matching:
        role = str(item.get("image_role") or "")
        refs.append({
            "evidence_id": item.get("evidence_id") or item.get("Selected case") or "",
            "case": item.get("Selected case") or "候选案例",
            "fit": item.get("Fit") or "",
            "score": item.get("Score"),
            "image_role": role,
            "mechanism": item.get("mechanism") or item.get("Why") or "",
            "transfer_action": item.get("transfer_action") or "",
            "non_transfer_boundary": item.get("Rejected") or "",
            "source_refs": _l(item.get("source_refs")),
        })
        if len(refs) >= limit:
            break
    if refs:
        return refs
    for item in case_board[:limit]:
        refs.append({
            "evidence_id": item.get("evidence_id") or item.get("Selected case") or "",
            "case": item.get("Selected case") or "候选案例",
            "fit": item.get("Fit") or "",
            "score": item.get("Score"),
            "image_role": item.get("image_role") or "",
            "source_refs": _l(item.get("source_refs")),
        })
    return refs


def _role_council(report: dict[str, Any], evidence_nodes: list[dict[str, Any]], case_board: list[dict[str, Any]],
                  gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    market = _d(report.get("market"))
    decision = _d(report.get("decision"))
    parcel = _d(report.get("parcel"))
    sample_count = int(_num(market.get("sample_size") or len(_l(market.get("competitors"))), 0) or 0)
    gap_count = len(gaps)
    base_conf = _confidence(sample_count)
    base_risk = _risk_coeff(sample_count, has_l1=False)
    def council_item(role_id: str, role_label: str, decision_text: str, why: str, source_refs: list[str],
                     visual_roles: list[str], next_actions: list[str], confidence_delta: float = 0.0,
                     risk_delta: int = 0, human_review: bool | None = None) -> dict[str, Any]:
        selected_nodes = [
            node
            for node in evidence_nodes
            if role_id in _l(node.get("supports_roles"))
        ]
        selected_evidence_refs = [
            node.get("evidence_id") for node in selected_nodes if node.get("evidence_id")
        ]
        role_gaps = [
            gap
            for gap in gaps
            if role_id in str(gap.get("blocking_section") or "")
        ]
        observed = [
            _text(node.get("claim"), "")
            for node in selected_nodes
            if node.get("claim_type") not in {"missing", "conflict", "counter_evidence"}
            and _text(node.get("claim"), "")
        ]
        counter = [
            _text(node.get("claim"), "")
            for node in selected_nodes
            if node.get("claim_type") in {"missing", "conflict", "counter_evidence"}
            and _text(node.get("claim"), "")
        ]
        for node in selected_nodes:
            counter.extend(_text(item, "") for item in _l(node.get("conflicts")) if _text(item, ""))
        observed = list(dict.fromkeys(observed)) or [_short(why, 220)]
        counter = list(dict.fromkeys(counter)) or ["尚缺该专业结论的独立反例或交叉验证，当前按partial使用。"]
        role_source_refs = list(source_refs)
        for node in selected_nodes:
            role_source_refs.extend(_l(node.get("source_refs")))
        role_source_refs = list(dict.fromkeys(ref for ref in role_source_refs if ref))
        confidence = round(max(0.18, min(0.9, base_conf + confidence_delta)), 2)
        risk = max(0, min(100, base_risk + risk_delta + len(role_gaps) * 4))
        has_blocking_gap = any("blocking" in str(gap.get("severity") or "") for gap in role_gaps)
        needs_review = bool(human_review) if human_review is not None else confidence < 0.55 or risk >= 66 or has_blocking_gap
        return {
            "role_id": role_id,
            "role_label": role_label,
            "decision": _short(decision_text, 260),
            "why": _short(why, 320),
            "source_refs": role_source_refs,
            "evidence_refs": selected_evidence_refs,
            "observed_evidence": observed[:5],
            "counter_evidence": counter[:5],
            "case_refs": _case_refs_for_roles(case_board, visual_roles, limit=4),
            "visual_roles": visual_roles,
            "confidence": confidence,
            "risk_coeff": risk,
            "human_review": needs_review,
            "next_actions": next_actions[:4],
            "market_effect": _ROLE_MARKET_EFFECTS[role_id],
            "financial_effect": _ROLE_FINANCIAL_EFFECTS[role_id],
            "decision_gate": _ROLE_GATES[role_id],
            "validation_gaps": [
                gap.get("recommended_action") or gap.get("gap_id") for gap in role_gaps
            ],
            "status": "partial" if role_gaps or needs_review else "ready",
        }

    city = parcel.get("city") or _d(report.get("meta")).get("city") or "目标城市"
    address = parcel.get("address") or "目标地块"
    summary = decision.get("summary") or "当前自动报告已生成初步判断，需把事实、案例和设计动作继续压实。"
    case_roles = {str(x.get("image_role") or "") for x in case_board}
    has_masterplan = "masterplan" in case_roles
    has_unit = "unit_plan" in case_roles
    has_luxury = bool(case_roles & {"luxury_aesthetic", "facade_detail", "sales_center"})
    strongest_case = case_board[0].get("Selected case") if case_board else "待补强主案例"

    return [
        council_item(
            "architecture_director",
            "建筑总监",
            f"{city}{address} 可以进入可审设计 thesis，但投资、规划和对外口径必须保留人审闸门。",
            f"主判断来自 DDS 决策摘要、证据节点和案例候选板：{summary}",
            ["S1", "S2", "S3", "S4", "S6"],
            ["masterplan", "unit_plan", "facade_detail", "luxury_aesthetic", "evidence_image"],
            ["锁定一句设计 thesis", "把 thesis 拆成总图/户型/立面/会所/营销任务", "对所有低置信节点补来源"],
            confidence_delta=0.02,
            risk_delta=2,
        ),
        council_item(
            "planning_constraint_agent",
            "总图规划大师",
            "先做资源面排序和到达礼序，再决定入口、组团、人车、消防和分期。",
            "总图判断必须回到 GIS、控规和 masterplan 参照；当前 masterplan 证据" + ("已接入。" if has_masterplan else "仍需补强。"),
            ["S2", "S4", "S6"],
            ["masterplan", "evidence_image", "intention_image"],
            ["补控规/红线/出入口条件", "绘制资源面与噪声面", "输出 2-3 个总图策略假设"],
            confidence_delta=-0.04 if has_masterplan else -0.12,
            risk_delta=6 if has_masterplan else 14,
        ),
        council_item(
            "product_unit_master",
            "户型/产品大师",
            "以价格带、客群和总价承受力反推面积段与户型配比。",
            "户型不是风格问题，必须用市场样本、竞品空档和 unit_plan 证据证明可卖性；当前 unit_plan 证据" + ("已接入。" if has_unit else "仍需补强。"),
            ["S1", "S3", "S4", "S6"],
            ["unit_plan", "evidence_image"],
            ["补竞品面积段与总价", "定义主力/利润/形象户型", "把户型动作转成货值影响"],
            confidence_delta=-0.02 if has_unit else -0.1,
            risk_delta=4 if has_unit else 12,
        ),
        council_item(
            "luxury_aesthetic_agent",
            "豪宅审美总监",
            "保留礼序、私密、尺度、材质、会所和景观这些可执行审美动作，避免只贴意向图。",
            "豪宅质感需要 facade_detail、luxury_aesthetic 和 sales_center 形成证据链；当前审美证据" + ("已接入。" if has_luxury else "仍需补强。"),
            ["S4", "S6"],
            ["facade_detail", "luxury_aesthetic", "sales_center", "intention_image"],
            ["建立材质与节点词典", "筛掉无法落地的氛围图", "为每个卖点绑定一张证据图"],
            confidence_delta=0.0 if has_luxury else -0.08,
            risk_delta=3 if has_luxury else 10,
        ),
        council_item(
            "case_benchmark_agent",
            "全球案例馆",
            f"以 {strongest_case} 为首屏候选，但所有案例必须说明 fit、score、source 和不采用原因。",
            "案例板同时接入市场竞品和 ArchLib 视觉角色，采纳/否决反馈会进入学习层，不改写事实层。",
            ["S1", "S4", "S6"],
            ["intention_image", "evidence_image", "masterplan", "unit_plan", "facade_detail", "luxury_aesthetic", "sales_center"],
            ["把候选案例分为主证明/辅助证明/氛围参考", "记录采纳和否决", "补充同尺度、同客群、同气候案例"],
            confidence_delta=0.03 if case_board else -0.16,
            risk_delta=0 if case_board else 16,
        ),
        council_item(
            "market_value_agent",
            "市场价值官",
            f"当前样本数 {sample_count}，市场结论只能在样本半径和样本质量范围内使用。",
            "价格、距离、竞品和去化是产品定位的边界；样本不足时，结论必须降级为假设。",
            ["S1", "S3"],
            ["evidence_image"],
            ["补足有效竞品 >= 5", "拆分价格带与面积段", "给出乐观/中性/保守三档价值影响"],
            confidence_delta=-0.02,
            risk_delta=8 if sample_count < 5 else 0,
        ),
        council_item(
            "marketing_translation_agent",
            "营销转译官",
            "只把已被设计动作和证据支持的内容转译成买家利益，不使用保证性和唯一性话术。",
            "营销页需要从价值地图、归家礼序、竞品价格带和案例证据墙生成，而不是从形容词生成。",
            ["S1", "S3", "S4", "S6"],
            ["sales_center", "luxury_aesthetic", "intention_image", "evidence_image"],
            ["产出一页价值地图", "写 5 条有证据的卖点", "标注禁止外宣的风险句"],
            confidence_delta=0.0,
            risk_delta=4,
        ),
        council_item(
            "execution_pm_agent",
            "执行策略官",
            "按 L1 文件、竞品补样本、案例补强、设计任务书、营销材料的顺序推进。",
            "执行风险主要来自缺口而不是页面呈现；缺口越多，越需要阶段闸门。",
            ["S3", "S4", "S6"],
            ["masterplan", "unit_plan", "facade_detail"],
            ["排定 48 小时补证据清单", "定义人审通过条件", "输出可交付物目录"],
            confidence_delta=-0.04,
            risk_delta=6,
        ),
        council_item(
            "risk_trust_agent",
            "可信与风险审计官",
            "低置信、缺 L1、缺强案例的结论必须标记为初筛，不能进入最终投资或公开传播。",
            "DDS 需要保留来源、置信、风险、缺口和学习反馈，确保系统会演进但不自我篡改事实。",
            ["S1", "S2", "S3", "S4", "S6"],
            ["evidence_image", "non_case_document_pending"],
            ["列出 blocking gaps", "对低置信 claim 降级", "将人审意见写入 learning_events"],
            confidence_delta=-0.06,
            risk_delta=12,
            human_review=True,
        ),
    ]


def _design_brief(report: dict[str, Any], evidence_nodes: list[dict[str, Any]], case_board: list[dict[str, Any]]) -> dict[str, Any]:
    parcel = _d(report.get("parcel"))
    decision = _d(report.get("decision"))
    market = _d(report.get("market"))
    summary = decision.get("summary") or decision.get("recommendation") or "先完成证据盘点，再输出设计 thesis。"
    return {
        "project_judgment": _short(summary, 420),
        "design_thesis": "用区位/竞品/客群证据决定空间动作，而不是先选风格图。",
        "planning_tasks": [
            f"以 {parcel.get('address') or parcel.get('city') or '地块'} 为中心复核半径、到达界面和资源面。",
            "把总图任务拆为入口礼序、资源排序、人车/消防、产品货值梯度和分期。"
        ],
        "product_tasks": [
            f"围绕市场均价 {_text(market.get('avg_price') or market.get('avg_unit_price'))} 元/㎡校准面积段和总价。",
            "户型配比必须绑定 ABM 客群、WTP 和竞品空档。"
        ],
        "aesthetic_tasks": [
            "豪宅审美只保留能转成可见设计动作的参考: 礼序、私密、尺度、材质、会所、景观。",
            "案例图必须给 fit score、来源路径和不采用原因。"
        ],
        "case_candidate_board": case_board[:8],
        "review_gates": [
            "L1 控规/挂牌/客户任务书",
            "有效竞品样本 >= 5",
            "主案例 fit score >= 65",
            "所有数字有 source_refs"
        ],
        "evidence_refs": [x["evidence_id"] for x in evidence_nodes]
    }


def _marketing_report(report: dict[str, Any], evidence_nodes: list[dict[str, Any]], case_board: list[dict[str, Any]]) -> dict[str, Any]:
    strongest_cases = [x for x in case_board if x.get("Score", 0) >= 65][:4]
    return {
        "customer_value": [
            "把地块资源、到达礼序、产品尺度和会所运营转译成买家可感知利益。",
            "所有卖点必须能回到图纸、数据、案例或客户事实。"
        ],
        "evidence_claims": [
            {"claim": x["claim"], "source_refs": x["source_refs"], "confidence": x["confidence_label"]}
            for x in evidence_nodes[:6]
        ],
        "visual_anchors": strongest_cases,
        "channel_materials": ["一页价值地图", "竞品价格带图", "归家礼序节点图", "案例证据墙", "风险解释卡"],
        "forbidden_claims": ["顶级", "唯一", "保证升值", "必然热销", "无风险"]
    }


def _execution_strategy(report: dict[str, Any], gaps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "risks": [
            {"risk": g["gap_id"], "level": g["severity"], "action": g["recommended_action"]}
            for g in gaps
        ],
        "owners": [
            {"owner": "投拓/开发", "task": "补 L1 挂牌、控规、成本和税费假设"},
            {"owner": "设计", "task": "把 thesis 转成总图/户型/立面/景观任务书"},
            {"owner": "案例馆", "task": "按 candidate board 重建强匹配案例"},
            {"owner": "营销", "task": "将设计动作转成证据化话术和素材清单"}
        ],
        "next_review_inputs": ["L1 文件", "竞品补样本", "ArchLib 视觉角色索引", "用户采纳/否决反馈"],
        "gate": "confidence >= 0.55 and risk_coeff < 66 for conditional proceed"
    }


def build_architecture_director_packet(report: dict[str, Any], user_text: str | None = None) -> dict[str, Any]:
    learning_summary = _load_learning_summary()
    source_matrix = _source_matrix(report)
    evidence_nodes = _evidence_nodes(report, source_matrix)
    market_case_board = _case_candidate_board(report, learning_summary)
    visual_case_board = _visual_case_candidates(report, learning_summary, user_text)
    professional_case_board = _professional_case_candidates(report)
    case_board = sorted(
        market_case_board + visual_case_board + professional_case_board,
        key=lambda x: (x.get("Score", 0), x.get("learning_delta", 0), -x.get("original_rank", 999)),
        reverse=True,
    )
    gaps = _knowledge_gaps(report, evidence_nodes, case_board, learning_summary)
    role_council = _role_council(report, evidence_nodes, case_board, gaps)
    parcel = _d(report.get("parcel"))
    meta = _d(report.get("meta"))
    packet_id = _packet_id(report)
    intent = "投拓报告+设计任务书+营销报告+执行策略"
    brief = {
        "user_text": user_text or parcel.get("vision") or parcel.get("address") or "DDS report request",
        "intent": intent,
        "audience": _d(meta.get("persona")).get("label") or "开发商/设计团队/投委会",
        "parcel_context": parcel,
        "requested_outputs": ["web_report", "16_9_deck", "case_board", "json", "knowledge_gap_brief"],
        "assumptions": ["当前为自动初筛；最终投资、规划、合规和对外话术需人审。"],
        "missing_fields": [g["gap_id"] for g in gaps]
    }
    packet = {
        "id": packet_id,
        "title": f"{parcel.get('city') or 'DDS'} · {parcel.get('address') or 'Architecture Director Packet'}",
        "status": "reviewable_with_gaps" if gaps else "reviewable",
        "version": "dds_architecture_director_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "brief": brief,
        "source_matrix": source_matrix,
        "evidence_nodes": evidence_nodes,
        "case_candidates": case_board,
        "role_council": role_council,
        "sections": {
            "design_brief": _design_brief(report, evidence_nodes, case_board),
            "marketing_report": _marketing_report(report, evidence_nodes, case_board),
            "execution_strategy": _execution_strategy(report, gaps),
            "role_council": role_council,
            "knowledge_gap_brief": gaps
        },
        "charts": [
            {"id": "market_price_band", "status": "ready_if_market_competitors_present", "source_refs": ["S1"]},
            {"id": "risk_confidence_matrix", "status": "ready", "source_refs": ["S3"]},
            {"id": "case_fit_board", "status": "ready_if_case_candidates_present", "source_refs": ["S4"]}
        ],
        "risks": _execution_strategy(report, gaps)["risks"],
        "knowledge_gaps": gaps,
        "learning_summary": learning_summary,
        "learning_applied": any((_num(x.get("learning_delta"), 0.0) or 0.0) != 0 for x in case_board),
        "exports": report.get("export", {}),
        "qa": {
            "source_coverage": len([x for x in evidence_nodes if x.get("source_refs")]) / max(1, len(evidence_nodes)),
            "case_candidate_count": len(case_board),
            "role_count": len(role_council),
            "gap_count": len(gaps),
            "caveats": ["Packet is generated from existing DDS evidence and does not replace human review."]
        }
    }
    return packet


def attach_architecture_director_packet(report: dict[str, Any], user_text: str | None = None) -> dict[str, Any]:
    packet = build_architecture_director_packet(report, user_text=user_text)
    report["architecture_director"] = packet
    report["evidence_packs"] = [{
        "pack_id": packet["id"],
        "intent": packet["brief"]["intent"],
        "selected_evidence_ids": [x["evidence_id"] for x in packet["evidence_nodes"] if not x.get("needs_human_review")],
        "review_evidence_ids": [x["evidence_id"] for x in packet["evidence_nodes"] if x.get("needs_human_review")],
        "case_candidate_count": len(packet["case_candidates"]),
        "learning_event_count": int(_num(_d(packet.get("learning_summary")).get("event_count"), 0) or 0),
        "pack_score": round(100 * packet["qa"]["source_coverage"] - 2 * packet["qa"]["gap_count"], 1),
    }]
    return report
