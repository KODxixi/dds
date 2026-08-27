"""
DDS 动态置信度引擎 — AIPM 认知层核心模块
将原有静态阈值（0.0/0.4/0.5/0.6）替换为基于数据质量的动态计算

四维驱动：
  1. 信号强度 (signal_strength) — 数据是否有真实信号？还是规则推演/合成？
  2. 数据覆盖度 (coverage) — 样本量、字段填充率、城市覆盖
  3. 来源可信度 (source_trust) — L1 绝对信任(1.0) → L3 辅助(0.35)
  4. 数据新鲜度 (freshness) — 数据距今多久？是否过期？

输出：
  - score: 0.0-1.0 动态置信度
  - level: 极高/高/中/低/极低
  - breakdown: 四维分解
  - caveats: 不确定性标注（对标 CRIC"AI 不确定时主动标注"）
  - recommendation: 是否可据此决策

用法：
  from confidence_engine import compute_confidence, ConfidenceInput
  conf = compute_confidence(ConfidenceInput(
      signal_strength=0.8, coverage=0.6, source_trust=0.7, freshness=0.9
  ))
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


# ── 数据信任层级（来自 DDS 四层信任体系）──
TRUST_LEVELS = {
    "L1": 1.0,   # 政府出让文件、网签成交、备案价、一房一价
    "L2": 0.7,   # GIS/高德 POI、购买结构化库、建安成本、开发商信用
    "L3": 0.35,  # 社交媒体舆情、客户偏好、带看量、业主评价
    "rule": 0.3, # 规则推演（如防烂尾评分基于销售状态派生）
    "synthetic": 0.15, # 合成数据（如 ABM 虚拟人格）
    "unknown": 0.1,
}


@dataclass
class ConfidenceInput:
    """置信度计算输入"""
    # 四维核心
    signal_strength: float  # 0.0-1.0：是否有真实信号？0=纯推演，1=真实成交/备案
    coverage: float         # 0.0-1.0：样本量/字段填充率/城市覆盖
    source_trust: float     # 0.0-1.0：L1=1.0, L2=0.7, L3=0.35, rule=0.3, synthetic=0.15
    freshness: float        # 0.0-1.0：数据新鲜度，1=今天，0=超过2年

    # 元信息
    agent: str = ""              # 哪个 agent 产生的
    data_source: str = ""        # 数据来源描述（如 "LandChina API"）
    source_level: str = ""       # L1/L2/L3/rule/synthetic
    sample_count: int = 0        # 样本量
    field_fill_rate: float = 0.0 # 关键字段填充率
    last_updated: Optional[str] = None  # 数据最后更新时间 ISO 格式
    notes: list[str] = field(default_factory=list)  # 额外备注


@dataclass
class ConfidenceOutput:
    """置信度计算结果"""
    score: float          # 0.0-1.0
    level: str            # 极高/高/中/低/极低
    grade: str            # A+ / A / B / C / D
    breakdown: dict       # 四维分解
    caveats: list[str]    # 不确定性标注
    actionable: bool      # 是否可据此决策
    recommendation: str   # 使用建议
    target_score: float   # 目标置信度（接入 L1 数据后可达）
    target_note: str      # 如何提升置信度


def compute_confidence(inp: ConfidenceInput) -> ConfidenceOutput:
    """四维加权动态置信度计算"""
    # 权重：信号强度 35% + 覆盖度 25% + 来源可信 25% + 新鲜度 15%
    w_signal, w_coverage, w_trust, w_freshness = 0.35, 0.25, 0.25, 0.15

    score = (
        inp.signal_strength * w_signal +
        inp.coverage * w_coverage +
        inp.source_trust * w_trust +
        inp.freshness * w_freshness
    )
    score = round(min(1.0, max(0.0, score)), 2)

    # 分级
    if score >= 0.85:
        level, grade = "极高", "A+"
    elif score >= 0.70:
        level, grade = "高", "A"
    elif score >= 0.55:
        level, grade = "中", "B"
    elif score >= 0.35:
        level, grade = "低", "C"
    else:
        level, grade = "极低", "D"

    # 不确定性标注
    caveats = _generate_caveats(inp, score)

    # 是否可据此决策
    actionable = score >= 0.55

    # 使用建议
    recommendation = _generate_recommendation(score, grade, inp)

    # 目标置信度（接入 L1 数据后）
    target_score = min(1.0, round(score + (1.0 - inp.source_trust) * 0.5, 2))
    target_note = _target_note(inp)

    return ConfidenceOutput(
        score=score,
        level=level,
        grade=grade,
        breakdown={
            "signal_strength": {"weight": w_signal, "value": inp.signal_strength, "contribution": round(inp.signal_strength * w_signal, 3)},
            "coverage": {"weight": w_coverage, "value": inp.coverage, "contribution": round(inp.coverage * w_coverage, 3)},
            "source_trust": {"weight": w_trust, "value": inp.source_trust, "contribution": round(inp.source_trust * w_trust, 3)},
            "freshness": {"weight": w_freshness, "value": inp.freshness, "contribution": round(inp.freshness * w_freshness, 3)},
        },
        caveats=caveats,
        actionable=actionable,
        recommendation=recommendation,
        target_score=target_score,
        target_note=target_note,
    )


def _generate_caveats(inp: ConfidenceInput, score: float) -> list[str]:
    """生成不确定性标注"""
    caveats = []
    if inp.signal_strength < 0.5:
        caveats.append(f"⚠ 信号强度低({inp.signal_strength:.0%})：当前结论基于{'规则推演' if inp.source_level == 'rule' else '有限数据'}，非真实成交信号")
    if inp.coverage < 0.5:
        caveats.append(f"⚠ 数据覆盖不足({inp.coverage:.0%})：样本量{inp.sample_count}，关键字段填充率{inp.field_fill_rate:.0%}")
    if inp.source_trust < 0.5:
        caveats.append(f"⚠ 数据源可信度低({inp.source_trust:.0%})：来源为{inp.source_level or '未知'}，建议交叉验证")
    if inp.freshness < 0.5:
        caveats.append(f"⚠ 数据陈旧({inp.freshness:.0%})：最后更新于{inp.last_updated or '未知'}，可能已过时")
    if score < 0.55:
        caveats.append("🔴 整体置信度不足，不建议据此做出重大投拓决策")
    return caveats


def _generate_recommendation(score: float, grade: str, inp: ConfidenceInput) -> str:
    """生成使用建议"""
    if grade in ("A+", "A"):
        return "可据此做出投拓决策，建议定期复核数据新鲜度"
    elif grade == "B":
        return "可参考，但需交叉验证关键结论（建议补充 L1 数据）"
    elif grade == "C":
        gaps = []
        if inp.signal_strength < 0.5:
            gaps.append("补充真实成交信号")
        if inp.coverage < 0.5:
            gaps.append("扩大样本量")
        if inp.source_trust < 0.5:
            gaps.append("使用更高信任级别的数据源")
        return "仅供参考，不建议据此决策。" + "；".join(gaps) if gaps else "建议补充更多数据"
    else:
        return "不建议据此做出任何决策。数据基础严重不足，需优先补充数据"


def _target_note(inp: ConfidenceInput) -> str:
    """如何提升置信度"""
    notes = []
    if inp.source_trust < 1.0:
        notes.append("接入 L1 真实成交数据（网签/备案价）可提升来源可信度至 1.0")
    if inp.coverage < 0.8:
        notes.append(f"扩大样本量（当前{inp.sample_count}）可提升覆盖度")
    if inp.signal_strength < 0.8:
        notes.append("以真实成交替代规则推演/合成数据可提升信号强度")
    return "；".join(notes) if notes else "当前置信度已接近上限"


# ── 便捷工厂函数 ──

def confidence_from_abm(abm_result: dict, n_samples: int, use_real_data: bool = False) -> ConfidenceOutput:
    """从 ABM 结果计算动态置信度"""
    arch_stats = abm_result.get("archetype_stats", {})
    if use_real_data:
        # 只有可审计的真实样本才允许通过样本量提高证据覆盖度。
        archetype_coverage = (
            sum(1 for s in arch_stats.values() if s.get("n", 0) >= 30)
            / max(len(arch_stats), 1)
        )
        sample_score = min(n_samples / 1000, 1.0)
        coverage = archetype_coverage * 0.6 + sample_score * 0.4
        signal = 0.7
        source = TRUST_LEVELS["L2"]
    else:
        # 合成人格的 N 只属于 simulation_stability，不能计入证据覆盖度。
        coverage = float(abm_result.get("real_evidence_coverage") or 0.0)
        coverage = max(0.0, min(1.0, coverage))
        signal = 0.3
        source = TRUST_LEVELS["synthetic"]

    return compute_confidence(ConfidenceInput(
        signal_strength=signal,
        coverage=coverage,
        source_trust=source,
        freshness=0.9,  # ABM 每次重新计算
        agent="abm_market_agent",
        data_source="ABM 蒙特卡洛模拟" if not use_real_data else "L1 真实成交数据",
        source_level="synthetic" if not use_real_data else "L2",
        sample_count=n_samples,
        notes=["MVP 阶段使用合成人口分布" if not use_real_data else "基于真实成交数据"],
    ))


def confidence_from_premium(driver: dict) -> ConfidenceOutput:
    """从溢价引擎驱动计算动态置信度"""
    signal = 0.7 if driver.get("status") == "ok" else 0.0
    source = TRUST_LEVELS.get(driver.get("source_level", "L2"), 0.7)
    return compute_confidence(ConfidenceInput(
        signal_strength=signal,
        coverage=0.6 if driver.get("status") == "ok" else 0.0,
        source_trust=source,
        freshness=0.8,
        agent="premium_engine",
        data_source=driver.get("evidence", ""),
        source_level=driver.get("source_level", "L2"),
        notes=[f"置信度目标: {driver.get('confidence_target', '')}" if driver.get("confidence_target") else ""],
    ))


def confidence_from_compliance(verdict: str, hard_constraints_missing: bool,
                                blocker_count: int = 0) -> ConfidenceOutput:
    """从合规 Agent 计算动态置信度"""
    signal = 0.9 if verdict == "clear" else (0.5 if verdict == "conditional" else 0.2)
    source = TRUST_LEVELS["rule"]  # 规则推演
    coverage = 0.8 if not hard_constraints_missing else 0.4

    return compute_confidence(ConfidenceInput(
        signal_strength=signal,
        coverage=coverage,
        source_trust=source,
        freshness=0.95,
        agent="compliance_agent",
        data_source="规划条件法规库",
        source_level="rule",
        notes=["缺一级法定硬约束" if hard_constraints_missing else ""] +
              ([f"存在 {blocker_count} 个硬阻断" if blocker_count > 0 else ""] if blocker_count else []),
    ))


def confidence_from_value(samples: int, premium_ratio: float, avg_price: float) -> ConfidenceOutput:
    """从价值 Agent 计算动态置信度"""
    signal = 0.7 if premium_ratio > 0 else 0.4
    coverage = min(1.0, samples / 20)
    source = TRUST_LEVELS["L2"]  # 购买结构库

    return compute_confidence(ConfidenceInput(
        signal_strength=signal,
        coverage=coverage,
        source_trust=source,
        freshness=0.85,
        agent="value_agent",
        data_source="购买结构化数据 + 高德 POI",
        source_level="L2",
        sample_count=samples,
        notes=[f"周边均价 {avg_price:.0f} 元/㎡", f"对标溢价率 {premium_ratio:.1%}"],
    ))


# ── 快速信任层级查询 ──

def trust_level(source: str) -> float:
    """根据来源描述返回信任层级"""
    source_lower = source.lower()
    if any(kw in source_lower for kw in ["landchina", "网签", "备案", "住建局", "一房一价", "政府"]):
        return TRUST_LEVELS["L1"]
    if any(kw in source_lower for kw in ["高德", "购买", "结构化", "gis", "poi", "建安成本", "信用"]):
        return TRUST_LEVELS["L2"]
    if any(kw in source_lower for kw in ["舆情", "社交", "带看", "业主", "评价"]):
        return TRUST_LEVELS["L3"]
    if any(kw in source_lower for kw in ["规则", "推演", "派生"]):
        return TRUST_LEVELS["rule"]
    if any(kw in source_lower for kw in ["合成", "模拟", "abm", "虚拟"]):
        return TRUST_LEVELS["synthetic"]
    return TRUST_LEVELS["unknown"]


def compute_freshness(last_updated_iso: Optional[str] = None,
                       max_age_days: int = 365) -> float:
    """根据最后更新时间计算数据新鲜度"""
    if not last_updated_iso:
        return 0.5  # 未知
    try:
        last = datetime.fromisoformat(last_updated_iso.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - last).days
        return max(0.0, min(1.0, 1.0 - age_days / max_age_days))
    except (ValueError, TypeError):
        return 0.5


# ── 序列化输出 ──

def to_dict(co: ConfidenceOutput) -> dict:
    return {
        "score": co.score,
        "level": co.level,
        "grade": co.grade,
        "breakdown": co.breakdown,
        "caveats": co.caveats,
        "actionable": co.actionable,
        "recommendation": co.recommendation,
        "target_score": co.target_score,
        "target_note": co.target_note,
    }
