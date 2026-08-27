"""DDS decision explainer packet.

Turns a report_json into a reader-facing explanation layer: value reframing,
source-backed claims, confidence, cost-of-wrong, human review gates, and learning
hooks. This is deliberately separate from fact tables and decision engines.
"""
from __future__ import annotations

from datetime import datetime
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
    text = str(value or "").strip()
    return text or fallback


def _short(value: Any, limit: int = 180) -> str:
    text = _text(value, "")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _label(confidence: float) -> str:
    if confidence >= 0.75:
        return "高置信"
    if confidence >= 0.55:
        return "中置信"
    if confidence >= 0.35:
        return "低置信"
    return "暂不判断"


def _overall_confidence(report: dict[str, Any], packet: dict[str, Any]) -> float:
    nodes = _l(packet.get("evidence_nodes"))
    if nodes:
        vals = [_num(n.get("confidence"), 0.0) or 0.0 for n in nodes]
        return round(sum(vals) / max(1, len(vals)), 2)
    sample = _num(_d(report.get("market")).get("sample_size"), 0) or 0
    if sample >= 20:
        return 0.68
    if sample >= 5:
        return 0.52
    return 0.32


def _risk_coeff(report: dict[str, Any], packet: dict[str, Any], confidence: float) -> int:
    gaps = _l(packet.get("knowledge_gaps"))
    sample = int(_num(_d(report.get("market")).get("sample_size"), 0) or 0)
    coeff = 35
    if confidence < 0.55:
        coeff += 18
    if sample < 5:
        coeff += 15
    if any(g.get("severity") == "blocking_for_final_investment" for g in gaps if isinstance(g, dict)):
        coeff += 18
    if not _d(report.get("decision_full")).get("data_foundation"):
        coeff += 8
    return min(100, coeff)


def _decision_state(confidence: float, risk_coeff: int) -> str:
    if confidence >= 0.68 and risk_coeff < 50:
        return "可条件推进"
    if risk_coeff >= 70 or confidence < 0.35:
        return "必须先补证据"
    return "可初筛，不可定案"


def _source_trace(report: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, Any]]:
    matrix = {s.get("source_id"): s for s in _l(packet.get("source_matrix")) if isinstance(s, dict)}
    rows: list[dict[str, Any]] = []
    for node in _l(packet.get("evidence_nodes"))[:8]:
        if not isinstance(node, dict):
            continue
        refs = node.get("source_refs") or []
        rows.append({
            "claim": node.get("claim"),
            "claim_type": node.get("claim_type"),
            "confidence": node.get("confidence"),
            "confidence_label": node.get("confidence_label"),
            "risk_label": node.get("risk_label"),
            "needs_human_review": bool(node.get("needs_human_review")),
            "source_refs": refs,
            "sources": [matrix.get(ref, {"source_id": ref}) for ref in refs],
            "note": node.get("note"),
        })
    if rows:
        return rows
    market = _d(report.get("market"))
    return [{
        "claim": f"当前报告读取到 {market.get('sample_size') or 0} 个竞品样本。",
        "claim_type": "fact",
        "confidence": 0.45,
        "confidence_label": "low",
        "risk_label": "C",
        "needs_human_review": True,
        "source_refs": ["market.competitors"],
        "sources": [{"name": "report_json.market"}],
        "note": "缺少 Architecture Director evidence_nodes，已降级解释。",
    }]


def _review_gates(report: dict[str, Any], packet: dict[str, Any], confidence: float, risk_coeff: int) -> list[dict[str, Any]]:
    gaps = _l(packet.get("knowledge_gaps"))
    gates = [
        {
            "gate": "L1 法定/客户硬约束",
            "status": "fail" if any(g.get("gap_id") == "l1_planning_and_listing_missing" for g in gaps if isinstance(g, dict)) else "pass",
            "why": "挂牌、控规、任务书缺失时，拿地价和规划结论只能作为初筛。",
            "must_human_review": True,
        },
        {
            "gate": "市场样本",
            "status": "pass" if (_num(_d(report.get("market")).get("sample_size"), 0) or 0) >= 5 else "fail",
            "why": "样本不足会直接降低价格带、客群和去化判断可靠性。",
            "must_human_review": False,
        },
        {
            "gate": "认知边界",
            "status": "pass" if confidence >= 0.55 and risk_coeff < 70 else "fail",
            "why": "置信度低或错误代价高时，系统必须说不确定。",
            "must_human_review": risk_coeff >= 70,
        },
    ]
    return gates


def build_decision_explainer(report: dict[str, Any], user_text: str | None = None) -> dict[str, Any]:
    packet = _d(report.get("architecture_director"))
    parcel = _d(report.get("parcel"))
    market = _d(report.get("market"))
    decision = _d(report.get("decision"))
    confidence = _overall_confidence(report, packet)
    risk_coeff = _risk_coeff(report, packet, confidence)
    state = _decision_state(confidence, risk_coeff)
    summary = decision.get("summary") or _d(packet.get("sections")).get("design_brief", {}).get("project_judgment")

    learning_summary: dict[str, Any]
    try:
        from architecture_learning import summarize_learning_events
        learning_summary = summarize_learning_events()
    except Exception as exc:
        learning_summary = {"status": "unavailable", "error": str(exc)}

    source_trace = _source_trace(report, packet)
    review_gates = _review_gates(report, packet, confidence, risk_coeff)
    human_review = [g for g in review_gates if g.get("status") == "fail" or g.get("must_human_review")]
    gaps = _l(packet.get("knowledge_gaps"))

    explainer = {
        "version": "dds_decision_explainer_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision_state": state,
        "summary": _short(summary, 420),
        "value_reframing": {
            "raw_request": user_text or parcel.get("vision") or parcel.get("address") or "DDS report request",
            "reconstructed_need": "不是回答单点问题，而是形成可交付的投拓判断、设计任务书、营销证据和执行闸门。",
            "business_value": "降低错拿地、错定位、错户型、错话术的代价。",
            "delivery_artifacts": ["完整HTML报告", "来源矩阵", "案例证据墙", "设计任务书", "营销报告", "执行策略", "完整JSON证据包"],
        },
        "cognitive_boundary": {
            "overall_confidence": confidence,
            "confidence_label": _label(confidence),
            "risk_coeff": risk_coeff,
            "risk_label": "高" if risk_coeff >= 70 else "中" if risk_coeff >= 45 else "低",
            "cost_of_wrong": [
                "用低置信价格带定地价，可能造成拿地价倒挂。",
                "把 Derived 推演当 L1 事实，可能误导投委会。",
                "案例图无来源或弱相关，会导致设计任务书跑偏。",
            ],
            "can_decide_alone": ["样本充足时的初筛排序", "竞品价格带描述", "报告/HTML/JSON交付物生成"],
            "requires_human_review": [g.get("gate") for g in human_review],
        },
        "source_trace": source_trace,
        "explanation_steps": [
            {"step": "价值还原", "text": "先把坐标/地址/愿景还原成投拓和产品决策，而不是只回答表层问题。"},
            {"step": "证据读取", "text": f"读取 {market.get('sample_size') or 0} 个竞品样本、GIS/POI、DDS模型和已有案例规则。"},
            {"step": "认知判断", "text": f"用置信度 {confidence:.2f} 与错误代价 {risk_coeff}/100 同时约束结论。"},
            {"step": "交付生成", "text": "输出完整HTML、来源矩阵、案例图墙、设计/营销/执行策略，而不是只给聊天文本。"},
            {"step": "进化记录", "text": "用户对案例、段落、缺口的采纳/否决会写入 learning_event，不改写原始事实。"},
        ],
        "review_gates": review_gates,
        "next_actions": [
            {"priority": "P0", "action": "补 L1 挂牌/控规/任务书", "why": "决定最终拿地价和规划可行性。"},
            {"priority": "P1", "action": "复核低置信 evidence_nodes", "why": "低置信结论只能做假设，不能写成定案。"},
            {"priority": "P1", "action": "把强匹配案例转成设计动作", "why": "案例必须服务总图、户型、立面、会所和营销表达。"},
            {"priority": "P2", "action": "记录用户采纳/否决", "why": "让后续检索和模板排序越用越准。"},
        ],
        "learning_hooks": [
            {"action": "knowledge_gap_repeated", "target": "gap_queue_priority", "label": "标记为反复缺口"},
            {"action": "case_accepted", "target": "retrieval_ranking", "label": "采纳该案例/证据"},
            {"action": "case_rejected", "target": "case_negative_samples", "label": "否决该案例/证据"},
            {"action": "section_rewritten", "target": "template_preference", "label": "人工重写本段"},
        ],
        "knowledge_gaps": gaps,
        "learning_summary": learning_summary,
    }
    return explainer


def attach_decision_explainer(report: dict[str, Any], user_text: str | None = None) -> dict[str, Any]:
    report["decision_explainer"] = build_decision_explainer(report, user_text=user_text)
    return report