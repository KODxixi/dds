"""DDS 契约定义：报告结构、证据类型、置信度计算。

从旧版 DDS 迁移并增强，确保报告框架的稳定性和一致性。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

# ============================================================================
# 报告框架契约
# ============================================================================

FRAMEWORK_ID = "dds.sc-ad-va-cs/1.0"
FRAMEWORK_VERSION = "dds.report-framework/sc-ad-va-cs/1.0"

REPORT_GROUPS = (
    {
        "group_id": "SC",
        "name": "Strategic Context",
        "label": "战略语境与开发边界",
        "units": ("SC1", "SC2", "SC3"),
    },
    {
        "group_id": "AD",
        "name": "Architecture Design",
        "label": "产品与建筑方案",
        "units": ("AD1", "AD2", "AD3", "AD4", "AD5"),
    },
    {
        "group_id": "VA",
        "name": "Value Audit",
        "label": "价值与实施校验",
        "units": ("VA1", "VA2", "VA3"),
    },
    {
        "group_id": "CS",
        "name": "Confidence State",
        "label": "证据与置信状态",
        "units": ("CS",),
    },
)

REPORT_UNITS = (
    {
        "section_id": "SC1",
        "group_id": "SC",
        "title": "投决命题与证据边界",
        "question": "这份报告替谁解决什么决策，当前证据允许判断到哪一步？",
        "gap": "尚未冻结项目身份、投决命题、基准日与证据使用边界。",
    },
    {
        "section_id": "SC2",
        "group_id": "SC",
        "title": "市场机会、客群洞察与竞品实证",
        "question": "市场真正缺什么，哪些正反案例能够证明机会与风险？",
        "gap": "尚未形成宏观、板块、面积段、客群、竞品和正反案例的交叉证据。",
    },
    {
        "section_id": "SC3",
        "group_id": "SC",
        "title": "场地、法定条件与工程边界",
        "question": "地块允许做什么，哪些物理、规范与工程条件决定方案成败？",
        "gap": "尚未形成可核验的红线、道路、竖向、日照、消防、人防与场地边界。",
    },
    {
        "section_id": "AD1",
        "group_id": "AD",
        "title": "方案1／2／3强排比选",
        "question": "至少三个方向性方案在同一口径下分别获得和牺牲什么？",
        "gap": "尚未形成方案1／2／3的同口径容量、空间、成本、运营与风险比选。",
    },
    {
        "section_id": "AD2",
        "group_id": "AD",
        "title": "主推方案与决策闸门",
        "question": "明确主推哪一个方案，淘汰其他方案的理由和切换条件是什么？",
        "gap": "尚未给出唯一主推方案、淘汰理由、验证门槛与回退机制。",
    },
    {
        "section_id": "AD3",
        "group_id": "AD",
        "title": "产品定位、面积段与货量兑现",
        "question": "市场机会如何转化为可销售、可分期、可施工的产品与货量？",
        "gap": "尚未把客群、面积段、总价带、户型谱系、货量与首开节奏闭合。",
    },
    {
        "section_id": "AD4",
        "group_id": "AD",
        "title": "建筑与空间落地",
        "question": "主推策略如何落实到总图、户型、立面、景观、会所和示范区？",
        "gap": "尚未形成可下发的总图、户型、立面、景观、会所、示范区与案例迁移任务。",
    },
    {
        "section_id": "AD5",
        "group_id": "AD",
        "title": "传统空间文化与市场感知",
        "question": "哪些是实测物理事实，哪些是市场抗性，哪些仅为传统文化解释？",
        "gap": "传统空间文化输入不足；必须按 G0–G4 降级并登记禁止用途。",
    },
    {
        "section_id": "VA1",
        "group_id": "VA",
        "title": "设计价值溢价",
        "question": "哪些设计投入以何种机制改善体验、竞争力、流速与长期价值？",
        "gap": "尚未建立上游证据、设计动作、成本投入与价值结果之间的可审计链路。",
    },
    {
        "section_id": "VA2",
        "group_id": "VA",
        "title": "去化、现金流与投资验证",
        "question": "主推方案能否卖得动、赚得到并保持资金安全？",
        "gap": "缺少去化、售价、可售、地价、建安、税费、融资或节奏输入，只能保留压力测试。",
    },
    {
        "section_id": "VA3",
        "group_id": "VA",
        "title": "风险与实施闭环",
        "question": "谁在何时关闭什么风险，验收、触发、回退和人审机制是什么？",
        "gap": "尚未形成责任人、触发条件、验收标准、回退机制与补证计划。",
    },
    {
        "section_id": "CS",
        "group_id": "CS",
        "title": "来源、方法与置信状态",
        "question": "每项判断能否展开、复算、追溯、质疑并审计？",
        "gap": "尚未形成完整来源登记、方法、假设、反证、缺口与置信状态。",
    },
)

GROUP_BY_ID = {item["group_id"]: item for item in REPORT_GROUPS}
UNIT_BY_ID = {item["section_id"]: item for item in REPORT_UNITS}
SECTION_ORDER = {item["section_id"]: index for index, item in enumerate(REPORT_UNITS)}
VALID_SECTION_IDS = tuple(item["section_id"] for item in REPORT_UNITS)

# 每个 decision-unit 的必填数据字段定义
SECTION_REQUIREMENTS = {
    "SC1": ["project_id", "decision_question", "evidence_boundary", "base_date"],
    "SC2": ["macro_indicators", "competitors", "customer_segments", "positive_cases", "negative_cases"],
    "SC3": ["redline", "regulations", "engineering_constraints"],
    "AD1": ["option_1", "option_2", "option_3", "comparison_matrix"],
    "AD2": ["recommended_option", "elimination_reasons", "validation_thresholds"],
    "AD3": ["product_mix", "area_segments", "price_bands", "sales_rhythm"],
    "AD4": ["site_plan", "floor_plans", "facade", "landscape", "show_area"],
    "AD5": ["traditional_factors", "market_perception", "prohibited_uses"],
    "VA1": ["premium_factors", "cost_value_chain"],
    "VA2": ["sales_forecast", "cash_flow", "investment_metrics"],
    "VA3": ["risks", "owners", "triggers", "acceptance_criteria"],
    "CS": ["sources", "methods", "assumptions", "confidence_gaps", "data_gaps"],
}

# ============================================================================
# 证据类型契约
# ============================================================================

EVIDENCE_TYPES = (
    "observed_fact",
    "social_observation",
    "analysis_inference",
    "model_simulation",
    "traditional_interpretation",
)

EVIDENCE_TYPE_LABELS = {
    "observed_fact": "实测／观察事实",
    "social_observation": "社媒观察",
    "analysis_inference": "分析推断",
    "model_simulation": "模型模拟",
    "traditional_interpretation": "传统文化解释",
}

# ============================================================================
# 置信度计算契约
# ============================================================================

CONFIDENCE_WEIGHTS = {
    "source": 0.25,
    "coverage": 0.15,
    "freshness": 0.15,
    "independent_cross": 0.15,
    "geographic_relevance": 0.15,
    "method_fit": 0.10,
    "stability": 0.05,
}

CONFIDENCE_LEVEL_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "undecidable": "不可判定",
}


def _clamp(value: Any) -> float:
    """Return a finite number constrained to the empirical 0..1 domain."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(1.0, max(0.0, number))


def confidence_level(score: float) -> str:
    """Map a 0..1 score to DDS's stable decision-support bands."""
    score = _clamp(score)
    if score >= 0.75:
        return "high"
    if score >= 0.55:
        return "medium"
    if score >= 0.35:
        return "low"
    return "undecidable"


def normalize_evidence_type(value: Any, default: str = "analysis_inference") -> str:
    """Return one of the five contract values, never an ad-hoc evidence type."""
    normalized = str(value or "").strip().lower()
    if normalized in EVIDENCE_TYPES:
        return normalized
    normalized_default = str(default or "").strip().lower()
    return (
        normalized_default
        if normalized_default in EVIDENCE_TYPES
        else "analysis_inference"
    )


def compute_evidence_confidence(
    source: float | Mapping[str, Any] = 0.0,
    coverage: float | None = None,
    freshness: float | None = None,
    independent_cross: float | None = None,
    geographic_relevance: float | None = None,
    method_fit: float | None = None,
    stability: float | None = None,
    *,
    cap: float | None = None,
    actionable_threshold: float = 0.55,
    **dimensions: Any,
) -> dict[str, Any]:
    """Compute the unified seven-dimension empirical confidence."""
    supplied: dict[str, Any]
    if isinstance(source, Mapping):
        supplied = dict(source)
    else:
        supplied = {"source": source}

    explicit = {
        "coverage": coverage,
        "freshness": freshness,
        "independent_cross": independent_cross,
        "geographic_relevance": geographic_relevance,
        "method_fit": method_fit,
        "stability": stability,
    }
    supplied.update({key: value for key, value in explicit.items() if value is not None})
    supplied.update(dimensions)

    values = {key: _clamp(supplied.get(key, 0.0)) for key in CONFIDENCE_WEIGHTS}
    breakdown = {
        key: {
            "value": value,
            "weight": CONFIDENCE_WEIGHTS[key],
            "contribution": round(value * CONFIDENCE_WEIGHTS[key], 6),
        }
        for key, value in values.items()
    }
    uncapped_score = sum(item["contribution"] for item in breakdown.values())
    applied_cap = _clamp(cap) if cap is not None else 1.0
    score = round(min(uncapped_score, applied_cap), 6)
    level = confidence_level(score)
    threshold = _clamp(actionable_threshold)

    return {
        "score": score,
        "level": level,
        "level_label": CONFIDENCE_LEVEL_LABELS[level],
        "breakdown": breakdown,
        "actionable": score >= threshold,
        "uncapped_score": round(uncapped_score, 6),
        "cap": applied_cap,
    }


# Descriptive aliases
compute_empirical_confidence = compute_evidence_confidence
build_evidence_confidence = compute_evidence_confidence

# ============================================================================
# 数据结构定义
# ============================================================================


@dataclass
class FieldOrigin:
    """字段来源标记"""
    source: str  # real, city_benchmark, project_average, national_benchmark, human_input
    label: str
    confidence_penalty: float = 0.0


@dataclass
class SectionData:
    """单个 Decision-Unit 的完整数据结构"""
    section_id: str
    data: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    data_origin: dict[str, FieldOrigin] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def is_complete(self) -> bool:
        """检查该 section 的必填字段是否全部填充（包括降级数据）"""
        required = SECTION_REQUIREMENTS.get(self.section_id, [])
        return all(field in self.data for field in required)

    def real_data_ratio(self) -> float:
        """真实数据占比（非降级数据）"""
        if not self.data_origin:
            return 0.0
        real_count = sum(
            1 for origin in self.data_origin.values()
            if origin.source == "real"
        )
        return real_count / len(self.data_origin) if self.data_origin else 0.0


@dataclass
class ValidationResult:
    """契约校验结果"""
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    section_confidences: dict[str, float] = field(default_factory=dict)

    def raise_if_invalid(self) -> None:
        """如果校验不通过，抛出异常"""
        if not self.valid:
            raise ContractViolationError(f"报告不符合 DDS 契约规范: {self.errors}")


class ContractViolationError(RuntimeError):
    """报告违反 DDS 契约时抛出"""
    pass


# ============================================================================
# 降级策略定义
# ============================================================================

FALLBACK_STRATEGIES = {
    "city_benchmark": {
        "quality": "synthetic",
        "confidence_penalty": 0.3,
        "label": "【城市基准数据】",
    },
    "project_type_average": {
        "quality": "synthetic",
        "confidence_penalty": 0.4,
        "label": "【同类型项目平均值】",
    },
    "national_benchmark": {
        "quality": "synthetic",
        "confidence_penalty": 0.5,
        "label": "【全国基准数据】",
    },
    "human_input_required": {
        "quality": "placeholder",
        "confidence_penalty": 0.8,
        "label": "【待人工补充】",
        "template": "该字段暂无数据，请提供{description}相关资料。",
    },
}

# 字段描述映射（用于生成降级提示文案）
FIELD_DESCRIPTIONS = {
    "project_id": "项目编号",
    "decision_question": "决策问题定义",
    "evidence_boundary": "证据边界",
    "base_date": "基准日期",
    "macro_indicators": "宏观经济指标",
    "competitors": "竞品分析",
    "customer_segments": "客群细分",
    "positive_cases": "正面案例",
    "negative_cases": "负面案例",
    "redline": "红线条件",
    "regulations": "法规限制",
    "engineering_constraints": "工程约束",
    "option_1": "方案一",
    "option_2": "方案二",
    "option_3": "方案三",
    "comparison_matrix": "对比矩阵",
    "recommended_option": "推荐方案",
    "elimination_reasons": "淘汰理由",
    "validation_thresholds": "验证门槛",
    "product_mix": "产品组合",
    "area_segments": "面积段分布",
    "price_bands": "价格带",
    "sales_rhythm": "去化节奏",
    "site_plan": "总图设计",
    "floor_plans": "户型图",
    "facade": "立面设计",
    "landscape": "景观设计",
    "show_area": "示范区",
    "traditional_factors": "传统因素",
    "market_perception": "市场认知",
    "prohibited_uses": "禁止用途",
    "premium_factors": "溢价因素",
    "cost_value_chain": "成本价值链路",
    "sales_forecast": "销售预测",
    "cash_flow": "现金流分析",
    "investment_metrics": "投资指标",
    "risks": "风险清单",
    "owners": "责任人",
    "triggers": "触发条件",
    "acceptance_criteria": "验收标准",
    "sources": "数据来源",
    "methods": "分析方法",
    "assumptions": "前提假设",
    "confidence_gaps": "置信度缺口",
    "data_gaps": "数据缺口",
}


__all__ = [
    # 框架
    "FRAMEWORK_ID",
    "FRAMEWORK_VERSION",
    "REPORT_GROUPS",
    "REPORT_UNITS",
    "GROUP_BY_ID",
    "UNIT_BY_ID",
    "SECTION_ORDER",
    "VALID_SECTION_IDS",
    "SECTION_REQUIREMENTS",
    # 证据
    "EVIDENCE_TYPES",
    "EVIDENCE_TYPE_LABELS",
    "normalize_evidence_type",
    # 置信度
    "CONFIDENCE_WEIGHTS",
    "CONFIDENCE_LEVEL_LABELS",
    "confidence_level",
    "compute_evidence_confidence",
    "compute_empirical_confidence",
    "build_evidence_confidence",
    # 数据结构
    "FieldOrigin",
    "SectionData",
    "ValidationResult",
    "ContractViolationError",
    # 降级
    "FALLBACK_STRATEGIES",
    "FIELD_DESCRIPTIONS",
]
