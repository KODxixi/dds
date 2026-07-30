"""DDS report, evidence, confidence, and delivery truth contracts.

Structural completeness is intentionally separate from evidential and
decision readiness.  A visible ``unknown`` is valid structure; a plausible
synthetic benchmark without provenance is not valid evidence.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from dds.domain import (
    DataRequirement,
    EvidenceRecord,
    EvidenceResolutionStatus,
    EvidenceType,
    ProjectContext,
    ReportRun,
    ResolutionStatus,
    ResolvedField,
    ResolvedStatus,
    SectionResult,
    normalize_evidence_type as _normalize_evidence_type,
    normalize_resolved_status,
)


# ---------------------------------------------------------------------------
# Report framework contract
# ---------------------------------------------------------------------------

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
        "question": "现状与未来事件将产生什么需求，哪些正反案例能够证明机会与风险？",
        "gap": "尚未形成宏观、板块、未来需求事件、客群、竞品和正反案例的交叉证据。",
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

SECTION_REQUIREMENTS = {
    "SC1": ["project_id", "decision_question", "evidence_boundary", "base_date"],
    "SC2": [
        "macro_indicators",
        "competitors",
        "future_demand_event_scan",
        "customer_segments",
        "positive_cases",
        "negative_cases",
    ],
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


# ---------------------------------------------------------------------------
# Evidence and resolution vocabularies
# ---------------------------------------------------------------------------

EVIDENCE_TYPES = tuple(item.value for item in EvidenceType)
RESOLUTION_STATUSES = tuple(item.value for item in ResolvedStatus)

EVIDENCE_TYPE_LABELS = {
    "observed_fact": "实测／观察事实",
    "social_observation": "社媒观察",
    "analysis_inference": "分析推断",
    "model_simulation": "模型模拟",
    "traditional_interpretation": "传统文化解释",
}


def normalize_evidence_type(value: Any, default: str = "analysis_inference") -> str:
    """Legacy string-returning wrapper around the domain enum normaliser."""

    return _normalize_evidence_type(value, default).value


# ---------------------------------------------------------------------------
# Empirical confidence
# ---------------------------------------------------------------------------

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
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(1.0, max(0.0, number))


def confidence_level(score: float) -> str:
    score = _clamp(score)
    if score >= 0.75:
        return "high"
    if score >= 0.55:
        return "medium"
    if score >= 0.35:
        return "low"
    return "undecidable"


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
    """Compute the unified seven-dimension score from supplied measurements.

    Missing dimensions are zero.  Callers that own evidence should prefer
    :func:`compute_confidence_from_evidence`, which derives every dimension
    from the evidence ledger instead of supplying optimistic constants.
    """

    supplied = dict(source) if isinstance(source, Mapping) else {"source": source}
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


def _coerce_evidence(records: Iterable[EvidenceRecord | Mapping[str, Any]] | None) -> list[EvidenceRecord]:
    result: list[EvidenceRecord] = []
    for item in records or []:
        try:
            result.append(EvidenceRecord.from_mapping(item))
        except (TypeError, ValueError):
            continue
    return result


def _parse_date(value: date | datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness_score(observed_at: date | datetime | str | None, reference: datetime) -> float:
    observed = _parse_date(observed_at)
    if observed is None:
        return 0.0
    age_days = (reference - observed).total_seconds() / 86400
    if age_days < -1:
        return 0.0
    age_days = max(0.0, age_days)
    if age_days <= 365:
        return 1.0
    if age_days <= 730:
        return 0.7
    if age_days <= 1825:
        return 0.4
    return 0.1


def _geography_score(actual: str, target: str) -> float:
    actual_norm = str(actual or "").strip().casefold()
    target_norm = str(target or "").strip().casefold()
    if not actual_norm or not target_norm:
        return 0.0
    if actual_norm == target_norm:
        return 1.0
    if actual_norm in target_norm or target_norm in actual_norm:
        return 0.7
    return 0.0


def derive_evidence_dimensions(
    evidence_records: Iterable[EvidenceRecord | Mapping[str, Any]] | None,
    *,
    required_metric_ids: Iterable[str] | None = None,
    project_context: ProjectContext | Mapping[str, Any] | None = None,
    as_of: date | datetime | str | None = None,
) -> dict[str, float]:
    """Derive all seven confidence dimensions only from evidence metadata."""

    records = _coerce_evidence(evidence_records)
    if not records:
        return {key: 0.0 for key in CONFIDENCE_WEIGHTS}

    context = ProjectContext.from_mapping(project_context)
    reference = _parse_date(as_of or context.base_date) or datetime.now(timezone.utc)
    traceable = [item for item in records if item.has_traceable_source]
    source = len(traceable) / len(records)

    required = {str(item) for item in (required_metric_ids or []) if str(item)}
    evidenced_metrics = {item.metric_id for item in traceable if item.metric_id}
    coverage = (
        len(required.intersection(evidenced_metrics)) / len(required)
        if required
        else len(traceable) / len(records)
    )

    freshness = (
        sum(_freshness_score(item.effective_at, reference) for item in traceable) / len(traceable)
        if traceable
        else 0.0
    )

    cross_metrics = required or evidenced_metrics
    cross_scores = []
    for metric_id in cross_metrics:
        sources = {
            item.source_id
            for item in traceable
            if item.metric_id == metric_id and item.source_id
        }
        cross_scores.append(min(1.0, max(0.0, float(len(sources) - 1))))
    independent_cross = sum(cross_scores) / len(cross_scores) if cross_scores else 0.0

    target_geography = context.target_geography
    geographic_relevance = (
        sum(_geography_score(item.geography, target_geography) for item in traceable) / len(traceable)
        if traceable
        else 0.0
    )
    method_fit = (
        sum(1.0 for item in traceable if str(item.method or "").strip()) / len(traceable)
        if traceable
        else 0.0
    )

    stability_values = []
    for item in traceable:
        try:
            sample_size = max(0.0, float(item.sample_size or 0.0))
        except (TypeError, ValueError):
            sample_size = 0.0
        sample_score = min(1.0, math.log10(sample_size + 1.0) / 3.0) if sample_size else 0.0
        limitation_penalty = min(0.75, len(item.limitations) * 0.15)
        stability_values.append(max(0.0, sample_score - limitation_penalty))
    stability = sum(stability_values) / len(stability_values) if stability_values else 0.0

    return {
        "source": _clamp(source),
        "coverage": _clamp(coverage),
        "freshness": _clamp(freshness),
        "independent_cross": _clamp(independent_cross),
        "geographic_relevance": _clamp(geographic_relevance),
        "method_fit": _clamp(method_fit),
        "stability": _clamp(stability),
    }


_EVIDENCE_TYPE_CAPS = {
    EvidenceType.OBSERVED_FACT: 1.0,
    EvidenceType.SOCIAL_OBSERVATION: 0.65,
    EvidenceType.ANALYSIS_INFERENCE: 0.70,
    EvidenceType.MODEL_SIMULATION: 0.75,
    EvidenceType.TRADITIONAL_INTERPRETATION: 0.35,
}


def compute_confidence_from_evidence(
    evidence_records: Iterable[EvidenceRecord | Mapping[str, Any]] | None,
    *,
    required_metric_ids: Iterable[str] | None = None,
    project_context: ProjectContext | Mapping[str, Any] | None = None,
    as_of: date | datetime | str | None = None,
    actionable_threshold: float = 0.55,
) -> dict[str, Any]:
    """Compute confidence from evidence; zero provenance always means zero."""

    records = _coerce_evidence(evidence_records)
    required = {str(item) for item in (required_metric_ids or []) if str(item)}
    dimensions = derive_evidence_dimensions(
        records,
        required_metric_ids=required,
        project_context=project_context,
        as_of=as_of,
    )
    traceable = [item for item in records if item.has_traceable_source]
    if not traceable or (required and dimensions["coverage"] == 0.0):
        cap = 0.0
    else:
        cap = max(_EVIDENCE_TYPE_CAPS[item.evidence_type] for item in traceable)
    result = compute_evidence_confidence(
        dimensions,
        cap=cap,
        actionable_threshold=actionable_threshold,
    )
    result.update(
        {
            "evidence_count": len(records),
            "traceable_evidence_count": len(traceable),
            "required_metric_count": len(required),
            "evidenced_metric_count": len(
                required.intersection({item.metric_id for item in traceable})
                if required
                else {item.metric_id for item in traceable if item.metric_id}
            ),
            "derived_from_evidence": True,
        }
    )
    return result


compute_empirical_confidence = compute_evidence_confidence
build_evidence_confidence = compute_evidence_confidence


# ---------------------------------------------------------------------------
# Legacy-compatible report containers and validation state
# ---------------------------------------------------------------------------


@dataclass
class FieldOrigin:
    """Legacy field provenance marker, now with auditable evidence references."""

    source: str
    label: str
    confidence_penalty: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    resolution_status: ResolvedStatus | str = ResolvedStatus.RESOLVED
    source_ref: str = ""
    source_hash: str = ""
    evidence_type: EvidenceType | str | None = None

    def __post_init__(self) -> None:
        self.evidence_refs = [str(item) for item in (self.evidence_refs or []) if str(item)]
        self.resolution_status = normalize_resolved_status(self.resolution_status)
        if self.evidence_type is not None:
            self.evidence_type = _normalize_evidence_type(self.evidence_type)

    @property
    def is_evidence_backed(self) -> bool:
        return bool(self.evidence_refs or (self.source and (self.source_ref or self.source_hash)))


@dataclass
class SectionData:
    """Legacy decision-unit container retained for existing agents."""

    section_id: str
    data: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    data_origin: dict[str, FieldOrigin] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    resolved_fields: dict[str, ResolvedField] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.data = dict(self.data or {})
        self.data_origin = {
            key: value if isinstance(value, FieldOrigin) else FieldOrigin(**dict(value))
            for key, value in dict(self.data_origin or {}).items()
        }
        self.resolved_fields = {
            key: value if isinstance(value, ResolvedField) else ResolvedField.from_mapping(value)
            for key, value in dict(self.resolved_fields or {}).items()
        }

    def is_complete(self) -> bool:
        required = SECTION_REQUIREMENTS.get(self.section_id, [])
        return all(item in self.data for item in required)

    def real_data_ratio(self) -> float:
        """Legacy name for the ratio of resolved, evidence-backed fields."""

        if not self.data_origin:
            return 0.0
        real_count = sum(
            1
            for origin in self.data_origin.values()
            if origin.resolution_status == ResolvedStatus.RESOLVED
            and origin.is_evidence_backed
        )
        return real_count / len(self.data_origin)

    def field_resolution(self, field_name: str) -> ResolvedField:
        if field_name in self.resolved_fields:
            return self.resolved_fields[field_name]
        value = self.data.get(field_name)
        if isinstance(value, ResolvedField):
            return value
        if isinstance(value, Mapping) and "status" in value:
            return ResolvedField.from_mapping(value)
        origin = self.data_origin.get(field_name)
        status = origin.resolution_status if origin else ResolvedStatus.RESOLVED
        return ResolvedField(
            status=status,
            value=value,
            evidence_refs=origin.evidence_refs if origin else [],
            confidence=self.confidence,
        )

    def to_section_result(self) -> SectionResult:
        return SectionResult(
            section_id=self.section_id,
            data={key: self.field_resolution(key) for key in self.data},
            evidence_refs=sorted(
                {
                    ref
                    for origin in self.data_origin.values()
                    for ref in origin.evidence_refs
                }
            ),
            gaps=list(self.missing_fields),
            confidence=self.confidence,
            status=(
                ResolvedStatus.RESOLVED
                if not self.missing_fields
                else ResolvedStatus.PARTIAL
            ),
        )


@dataclass
class GateResult:
    """Result of one ordered contract gate."""

    name: str
    status: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status.lower() == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "passed": self.passed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }


@dataclass
class ValidationResult:
    """Contract result with explicit structure/evidence/decision/delivery gates."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    section_confidences: dict[str, float] = field(default_factory=dict)
    gates: dict[str, GateResult] = field(default_factory=dict)

    @property
    def gate_status(self) -> dict[str, str]:
        return {name: gate.status for name, gate in self.gates.items()}

    @property
    def gate_statuses(self) -> dict[str, str]:
        return self.gate_status

    def _gate_passed(self, name: str) -> bool:
        gate = self.gates.get(name)
        return bool(gate and gate.passed)

    @property
    def structure_valid(self) -> bool:
        return self._gate_passed("structure")

    @property
    def evidence_valid(self) -> bool:
        return self._gate_passed("evidence")

    @property
    def decision_ready(self) -> bool:
        return self._gate_passed("decision")

    @property
    def delivery_ready(self) -> bool:
        return self._gate_passed("delivery")

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise ContractViolationError(f"报告不符合 DDS 契约规范: {self.errors}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "overall_confidence": self.overall_confidence,
            "section_confidences": dict(self.section_confidences),
            "gates": {name: gate.to_dict() for name, gate in self.gates.items()},
            "gate_status": self.gate_status,
            "structure_valid": self.structure_valid,
            "evidence_valid": self.evidence_valid,
            "decision_ready": self.decision_ready,
            "delivery_ready": self.delivery_ready,
        }


class ContractViolationError(RuntimeError):
    """Raised when a report fails any mandatory truth-contract gate."""


# ---------------------------------------------------------------------------
# Truth-safe fallback descriptors
# ---------------------------------------------------------------------------

# Benchmark keys remain for legacy configuration lookup, but are explicitly
# disabled without an EvidenceRecord.  FallbackEngine never fabricates them.
FALLBACK_STRATEGIES = {
    "city_benchmark": {
        "quality": "unavailable_without_evidence",
        "confidence_penalty": 1.0,
        "label": "【城市基准：缺少来源，不可用】",
        "enabled_without_evidence": False,
    },
    "project_type_average": {
        "quality": "unavailable_without_evidence",
        "confidence_penalty": 1.0,
        "label": "【同类均值：缺少来源，不可用】",
        "enabled_without_evidence": False,
    },
    "national_benchmark": {
        "quality": "unavailable_without_evidence",
        "confidence_penalty": 1.0,
        "label": "【全国基准：缺少来源，不可用】",
        "enabled_without_evidence": False,
    },
    "unknown": {
        "quality": "unknown",
        "confidence_penalty": 1.0,
        "label": "【未知】",
        "template": "暂无可核验证据：{description}。",
        "enabled_without_evidence": True,
    },
    "human_input": {
        "quality": "human_input",
        "confidence_penalty": 1.0,
        "label": "【待人工补充】",
        "template": "该字段暂无可核验证据，请提供{description}相关资料。",
        "enabled_without_evidence": True,
    },
    "human_input_required": {
        "quality": "human_input",
        "confidence_penalty": 1.0,
        "label": "【待人工补充】",
        "template": "该字段暂无可核验证据，请提供{description}相关资料。",
        "enabled_without_evidence": True,
        "alias_for": "human_input",
    },
}

FIELD_DESCRIPTIONS = {
    "project_id": "项目编号",
    "decision_question": "决策问题定义",
    "evidence_boundary": "证据边界",
    "base_date": "基准日期",
    "macro_indicators": "宏观经济指标",
    "competitors": "竞品分析",
    "customer_segments": "客群细分",
    "future_demand_event_scan": "未来需求事件扫描",
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
    "FRAMEWORK_ID",
    "FRAMEWORK_VERSION",
    "REPORT_GROUPS",
    "REPORT_UNITS",
    "GROUP_BY_ID",
    "UNIT_BY_ID",
    "SECTION_ORDER",
    "VALID_SECTION_IDS",
    "SECTION_REQUIREMENTS",
    "EvidenceType",
    "EvidenceResolutionStatus",
    "EVIDENCE_TYPES",
    "EVIDENCE_TYPE_LABELS",
    "ResolvedStatus",
    "ResolutionStatus",
    "RESOLUTION_STATUSES",
    "normalize_evidence_type",
    "normalize_resolved_status",
    "CONFIDENCE_WEIGHTS",
    "CONFIDENCE_LEVEL_LABELS",
    "confidence_level",
    "compute_evidence_confidence",
    "compute_empirical_confidence",
    "build_evidence_confidence",
    "derive_evidence_dimensions",
    "compute_confidence_from_evidence",
    "ProjectContext",
    "DataRequirement",
    "EvidenceRecord",
    "ResolvedField",
    "SectionResult",
    "ReportRun",
    "FieldOrigin",
    "SectionData",
    "GateResult",
    "ValidationResult",
    "ContractViolationError",
    "FALLBACK_STRATEGIES",
    "FIELD_DESCRIPTIONS",
]

