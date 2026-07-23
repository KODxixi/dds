"""Deterministic bridge from truth-domain ReportRun to the V4 compiler."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping, Sequence

from dds.contracts import UNIT_BY_ID, VALID_SECTION_IDS
from dds.analysis_profile import (
    optional_units_from_metadata,
    required_units_from_metadata,
)
from dds.domain import ReportRun, ResolvedField, ResolvedStatus, SectionResult
from dds.reporting import AssetResolver, build_frozen_package
from dds.reporting.edition import ReportEdition, decision_pages


def _portable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return _portable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _portable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_portable(item) for item in value]
    return value


def _portable_source_id(raw_evidence_id: str) -> tuple[str, str]:
    evidence_id_hash = sha256(raw_evidence_id.encode("utf-8")).hexdigest()
    return f"evidence-{evidence_id_hash[:24]}", evidence_id_hash


def _source_id_map(run: ReportRun) -> dict[str, str]:
    raw_ids = [str(item.evidence_id or "") for item in run.evidence_records]
    if any(not item for item in raw_ids):
        raise ValueError("every EvidenceRecord requires a non-empty evidence_id")
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("EvidenceRecord.evidence_id values must be unique")
    mapping = {
        raw_id: _portable_source_id(raw_id)[0]
        for raw_id in raw_ids
    }
    if len(mapping) != len(set(mapping.values())):
        raise ValueError("portable evidence source_id collision")
    return mapping


def _mapped_source_refs(
    source_refs: Sequence[str],
    source_id_map: Mapping[str, str],
) -> list[str]:
    mapped: list[str] = []
    for value in source_refs:
        raw_id = str(value)
        if raw_id not in source_id_map:
            raise ValueError(
                f"section source_ref has no EvidenceRecord: {raw_id!r}"
            )
        portable_id = source_id_map[raw_id]
        if portable_id not in mapped:
            mapped.append(portable_id)
    return mapped


def _section_status(section: SectionResult) -> str:
    if section.status is ResolvedStatus.RESOLVED and not section.gaps:
        return "ready"
    if section.status is ResolvedStatus.UNKNOWN:
        return "missing"
    if any(
        isinstance(item, ResolvedField) and item.status is ResolvedStatus.HUMAN_INPUT
        for item in section.data.values()
    ):
        return "blocked"
    return "partial"


def _confidence_score(section: SectionResult) -> float:
    confidence = section.confidence
    if isinstance(confidence, Mapping):
        raw = confidence.get("score", 0.0)
    elif isinstance(confidence, (int, float)):
        raw = confidence
    else:
        raw = 0.0
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _resolved_value(section: SectionResult, field: str) -> Any:
    value = section.data.get(field)
    return value.value if isinstance(value, ResolvedField) else value


def _section_blocks(
    section: SectionResult,
    source_id_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    portable_refs = _mapped_source_refs(section.evidence_refs, source_id_map)
    if section.section_id == "SC2":
        competitors = _resolved_value(section, "competitors")
        if isinstance(competitors, Sequence) and not isinstance(competitors, (str, bytes)):
            rows = []
            for item in competitors:
                if not isinstance(item, Mapping):
                    continue
                rows.append(
                    {
                        "project": item.get("project_name"),
                        "district": item.get("district"),
                        "listing_price": item.get("price"),
                        "unit": "CNY/m2",
                        "distance_km": item.get("distance_km"),
                    }
                )
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "title": "结构化有效竞品",
                        "columns": ["project", "district", "listing_price", "unit", "distance_km"],
                        "rows": rows[:12],
                        "source_refs": list(portable_refs),
                    }
                )
    elif section.section_id == "AD3":
        product_mix = _resolved_value(section, "product_mix")
        if isinstance(product_mix, Sequence) and not isinstance(product_mix, (str, bytes)):
            rows = []
            for item in product_mix:
                if not isinstance(item, Mapping):
                    continue
                rows.append(
                    {
                        "direction": item.get("direction_id"),
                        "positioning": item.get("positioning"),
                        "customer": item.get("target_customer"),
                        "value": item.get("value_proposition"),
                        "trigger": item.get("switch_trigger"),
                    }
                )
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "title": "产品定位三方向比较",
                        "columns": ["direction", "positioning", "customer", "value", "trigger"],
                        "rows": rows,
                        "source_refs": list(portable_refs),
                    }
                )
    elif section.section_id == "VA1":
        scenarios = _resolved_value(section, "premium_factors")
        if isinstance(scenarios, Sequence) and not isinstance(scenarios, (str, bytes)):
            rows = []
            for item in scenarios:
                if not isinstance(item, Mapping):
                    continue
                rows.append(
                    {
                        "scenario": item.get("name"),
                        "combined_rate": item.get("combined_rate"),
                        "unit_price": item.get("resulting_unit_price_cny_m2"),
                        "gross_value": item.get("gross_incremental_value_cny"),
                        "cost": item.get("incremental_cost_cny"),
                        "net_value": item.get("net_incremental_value_cny"),
                    }
                )
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "title": "Premium sensitivity scenarios",
                        "columns": [
                            "scenario",
                            "combined_rate",
                            "unit_price",
                            "gross_value",
                            "cost",
                            "net_value",
                        ],
                        "rows": rows,
                        "source_refs": list(portable_refs),
                    }
                )
    elif section.section_id == "VA2":
        forecast = _resolved_value(section, "sales_forecast")
        if isinstance(forecast, Mapping):
            curves = forecast.get("curves")
            if isinstance(curves, Sequence) and not isinstance(
                curves, (str, bytes)
            ):
                rows = [
                    {
                        "strategy": item.get("strategy_id"),
                        "scenario": item.get("scenario_id"),
                        "absorption_12m": item.get("absorption_12m"),
                        "clearance_month": item.get("clearance_month"),
                        "value_index": item.get("realized_value_index"),
                        "npv_index": item.get("risk_adjusted_npv_index"),
                    }
                    for item in curves
                    if isinstance(item, Mapping)
                ]
                if rows:
                    blocks.append(
                        {
                            "type": "table",
                            "title": "Standard 100-unit operating scenarios",
                            "columns": [
                                "strategy",
                                "scenario",
                                "absorption_12m",
                                "clearance_month",
                                "value_index",
                                "npv_index",
                            ],
                            "rows": rows,
                            "source_refs": list(portable_refs),
                        }
                    )
            monthly = forecast.get("monthly")
            if isinstance(monthly, Sequence) and not isinstance(
                monthly, (str, bytes)
            ):
                rows = [
                    {
                        "month": item.get("month"),
                        "subscriptions": item.get("subscriptions"),
                        "cancellations": item.get("cancellations"),
                        "contracts": item.get("contracts"),
                        "contracted_value_cny": item.get(
                            "contracted_sales_value_cny"
                        ),
                    }
                    for item in monthly
                    if isinstance(item, Mapping)
                ]
                if rows:
                    blocks.append(
                        {
                            "type": "table",
                            "title": "Project monthly subscriptions and contracts",
                            "columns": [
                                "month",
                                "subscriptions",
                                "cancellations",
                                "contracts",
                                "contracted_value_cny",
                            ],
                            "rows": rows,
                            "source_refs": list(portable_refs),
                        }
                    )
            schemes = forecast.get("schemes")
            if isinstance(schemes, Sequence) and not isinstance(
                schemes, (str, bytes)
            ):
                rows = [
                    {
                        "scheme": item.get("scheme_id"),
                        "feasible": item.get("feasible"),
                        "absorption_12m": item.get("absorption_12m"),
                        "realized_value_cny": item.get(
                            "realized_value_cny"
                        ),
                        "risk_adjusted_npv_cny": item.get(
                            "risk_adjusted_npv_cny"
                        ),
                        "peak_funding_cny": item.get(
                            "peak_funding_cny"
                        ),
                        "tail_inventory_rate": item.get(
                            "tail_inventory_rate"
                        ),
                    }
                    for item in schemes
                    if isinstance(item, Mapping)
                ]
                if rows:
                    blocks.append(
                        {
                            "type": "table",
                            "title": "Risk-adjusted Pareto scheme comparison",
                            "columns": [
                                "scheme",
                                "feasible",
                                "absorption_12m",
                                "realized_value_cny",
                                "risk_adjusted_npv_cny",
                                "peak_funding_cny",
                                "tail_inventory_rate",
                            ],
                            "rows": rows,
                            "source_refs": list(portable_refs),
                        }
                    )
    for conclusion in section.conclusions:
        text = str(conclusion).strip()
        if text:
            blocks.append(
                {
                    "type": "narrative",
                    "text": text,
                    "source_refs": list(portable_refs),
                }
            )
    if not blocks and section.gaps:
        blocks.append(
            {
                "type": "gap",
                "text": "；".join(str(item) for item in section.gaps),
                "source_refs": [],
            }
        )
    return _portable(blocks)


_CUSTOMER_PAGE_TITLES = {
    1: {
        "SC2": "地方客群机会与需求假设",
        "AD3": "数字人定位与概念路线推演",
        "VA2": "标准盘客群响应压力测试",
        "VA3": "客群异议、敏感性与切换触发",
        "CS": "客群证据、模型与禁止用途",
    },
    2: {
        "SC2": "地方客群、支付能力与约束匹配",
        "AD3": "数字人产品与约束响应",
        "VA2": "项目货量需求信号与现金流边界",
        "VA3": "客群异议、敏感性与切换触发",
        "CS": "客群证据、模型与禁止用途",
    },
    3: {
        "SC2": "统一客群基线与样本冻结",
        "AD3": "同 cohort 多方案行为比选",
        "VA2": "客群选择与风险调整方案比较",
        "VA3": "客群异议、敏感性与切换触发",
        "CS": "客群证据、模型与禁止用途",
    },
}


def _joined(value: Any) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "、".join(str(item) for item in value)
    return str(value or "")


def _decision_impact(section_id: str) -> str:
    if section_id.startswith("SC"):
        return "据此确认本轮研判边界，并安排仍会改变判断的补证。"
    if section_id.startswith("AD"):
        return "据此调整产品定位、空间方案或设计投入。"
    if section_id.startswith("VA"):
        return "据此调整货量、投资边界、风险条件或方案切换点。"
    return "据此确认结论的允许用途、责任人和下一步补证。"


def _decision_question(section_id: str) -> str:
    if section_id.startswith("SC"):
        return "哪些已核验事实会改变当前机会与需求判断？"
    if section_id.startswith("AD"):
        return "本页结论如何改变定位、产品或设计方案？"
    if section_id.startswith("VA"):
        return "价值、货量与风险是否支持当前方案继续推进？"
    return "当前结论可以用于什么，仍禁止用于什么？"


def _customer_page(
    *,
    section_id: str,
    level: int,
    blocks: list[dict[str, Any]],
    source_refs: list[str],
    evidence_type: str,
    takeaway: str,
) -> dict[str, Any]:
    title = _CUSTOMER_PAGE_TITLES[level][section_id]
    return {
        "page_id": f"{section_id.lower()}-customer-intelligence",
        "chapter_id": section_id.lower(),
        "section_id": section_id,
        "unit_id": section_id,
        "unit_status": "ready",
        "layout": "summary",
        "title": title,
        "decision_question": _decision_question(section_id),
        "takeaway": takeaway,
        "decision_impact": _decision_impact(section_id),
        "blocks": _portable(blocks),
        "source_refs": source_refs,
        "confidence": {"score": {1: 0.45, 2: 0.65, 3: 0.8}[level]},
        "evidence_type": evidence_type,
    }


def _customer_pages(
    metadata: Mapping[str, Any],
    source_id_map: Mapping[str, str],
    included_units: Sequence[str],
) -> list[dict[str, Any]]:
    bundle = metadata.get("customer_intelligence")
    if not isinstance(bundle, Mapping):
        return []
    level = int(bundle.get("evidence_level") or 0)
    if level < 1:
        return []
    profile = metadata.get("analysis_profile")
    input_level = (
        int(profile.get("selected_mode") or 1)
        if isinstance(profile, Mapping)
        else 1
    )
    input_level = max(1, min(3, input_level))
    raw_refs = [str(item) for item in bundle.get("evidence_refs") or ()]
    portable_refs = _mapped_source_refs(raw_refs, source_id_map)
    pages: list[dict[str, Any]] = []
    segment_labels = {
        str(item.get("segment_id") or ""): str(
            item.get("label") or item.get("segment_id") or "未命名客群"
        )
        for item in bundle.get("segments") or ()
        if isinstance(item, Mapping)
    }

    if "SC2" in included_units:
        segment_rows = []
        for item in bundle.get("segments") or ():
            if not isinstance(item, Mapping):
                continue
            segment_rows.append(
                {
                    "客群": item.get("label"),
                    "样本占比": f"{float(item.get('weight') or 0):.0%}",
                    "支付与居住基础": (
                        f"{item.get('income_band') or '收入待核验'}；"
                        f"{item.get('current_housing') or '居住状态待核验'}"
                    ),
                    "核心需求": _joined(item.get("primary_needs")),
                    "主要阻力": _joined(item.get("purchase_barriers")),
                }
            )
        blocks: list[dict[str, Any]] = [
            {
                "type": "narrative",
                "text": (
                    f"客群证据等级 C{level}；地域为 "
                    f"{bundle.get('city')}/{bundle.get('district') or '全市'}，"
                    f"数据截至 {bundle.get('as_of')}。"
                ),
                "source_refs": portable_refs,
            },
            {
                "type": "narrative",
                "text": (
                    "本地人口先验采用边际约束校准；"
                    "客群权重只在冻结地域、时点和样本范围内有效。"
                ),
                "source_refs": portable_refs,
            },
        ]
        if segment_rows:
            blocks.append(
                {
                    "type": "table",
                    "title": "谁是核心客群，他们为什么会买",
                    "columns": [
                        "客群",
                        "样本占比",
                        "支付与居住基础",
                        "核心需求",
                        "主要阻力",
                    ],
                    "rows": segment_rows,
                    "source_refs": portable_refs,
                }
            )
        pages.append(
            _customer_page(
                section_id="SC2",
                level=input_level,
                blocks=blocks,
                source_refs=portable_refs,
                evidence_type="observed_fact",
                takeaway=(
                    "、".join(
                        f"{row['客群']}占{row['样本占比']}"
                        for row in segment_rows
                    )
                    + "；不同客群的需求与支付约束必须分开判断。"
                ),
            )
        )

    persona = bundle.get("persona_experiment")
    if isinstance(persona, Mapping) and "AD3" in included_units:
        aggregate = persona.get("aggregate_results")
        persona_rows = []
        if isinstance(aggregate, Mapping):
            for segment_id, item in aggregate.items():
                if not isinstance(item, Mapping):
                    continue
                persona_rows.append(
                    {
                        "客群": segment_labels.get(str(segment_id), segment_id),
                        "为什么选": _joined(item.get("choice_reasons")),
                        "为什么不选": _joined(item.get("objections")),
                        "什么会改变选择": _joined(item.get("triggers")),
                    }
                )
        blocks = [
            {
                "type": "narrative",
                "text": (
                    "以下内容为合成人格的聚合模型模拟，不是实际客户原话；"
                    f"cohort={persona.get('cohort_ref')}，seed={persona.get('seed')}，"
                    f"prompt={persona.get('prompt_template_hash')}。"
                ),
                "source_refs": portable_refs,
            }
        ]
        if persona_rows:
            blocks.append(
                {
                    "type": "table",
                    "title": "数字人推演给出的行为信号",
                    "columns": [
                        "客群",
                        "为什么选",
                        "为什么不选",
                        "什么会改变选择",
                    ],
                    "rows": persona_rows,
                    "source_refs": portable_refs,
                }
            )
            questions = [
                _joined(item.get("validation_questions"))
                for item in aggregate.values()
                if isinstance(item, Mapping) and item.get("validation_questions")
            ]
            if questions:
                blocks.append(
                    {
                        "type": "narrative",
                        "text": "下一轮到访核验：" + "；".join(questions),
                        "source_refs": portable_refs,
                    }
                )
        pages.append(
            _customer_page(
                section_id="AD3",
                level=input_level,
                blocks=blocks,
                source_refs=portable_refs,
                evidence_type="model_simulation",
                takeaway="不同客群的选择理由、异议和切换触发不同，产品与沟通策略必须分群验证。",
            )
        )

    choice = bundle.get("choice_simulation")
    if isinstance(choice, Mapping) and "VA2" in included_units:
        shares = dict(choice.get("product_choice_shares") or {})
        share_rows = [
            {"方案": key, "选择份额": f"{float(value):.0%}"}
            for key, value in (
                shares
            ).items()
        ]
        share_rows.append(
            {
                "方案": "不买／延期",
                "选择份额": f"{float(choice.get('exit_share') or 0):.0%}",
            }
        )
        blocks = [
            {
                "type": "narrative",
                "text": (
                    f"选择模拟使用 parameter={choice.get('parameter_version')}、"
                    f"seed={choice.get('seed')}；同一 Input 3 比选必须复用同一 "
                    "cohort、模型、任务和种子。"
                ),
                "source_refs": portable_refs,
            },
            {
                "type": "table",
                "title": "产品选择与退出份额",
                "columns": ["方案", "选择份额"],
                "rows": share_rows,
                "source_refs": portable_refs,
            },
        ]
        budget = choice.get("affordable_budget_median_wan")
        if budget is not None:
            blocks.append(
                {
                    "type": "narrative",
                    "text": f"可负担预算中位数为 {budget} 万元；该值不是 WTP。",
                    "source_refs": portable_refs,
                }
            )
        if level < 3:
            blocks.append(
                {
                    "type": "narrative",
                    "text": (
                        "当前未达到 C3 漏斗校准；choice share 不得换算月销量、"
                        "去化周期或现金流。"
                    ),
                    "source_refs": portable_refs,
                }
            )
        pages.append(
            _customer_page(
                section_id="VA2",
                level=input_level,
                blocks=blocks,
                source_refs=portable_refs,
                evidence_type="model_simulation",
                takeaway=(
                    (
                        f"{max(shares, key=shares.get)} 的相对选择份额最高，"
                        if shares
                        else "当前未形成领先方案，"
                    )
                    + f"{float(choice.get('exit_share') or 0):.0%} 选择不买或延期；"
                    "结果只能用于相对比较。"
                ),
            )
        )

    if "VA3" in included_units:
        objections: list[dict[str, Any]] = []
        if isinstance(persona, Mapping) and isinstance(
            persona.get("aggregate_results"), Mapping
        ):
            for segment_id, item in persona["aggregate_results"].items():
                if isinstance(item, Mapping):
                    objections.append(
                        {
                            "客群": segment_labels.get(str(segment_id), segment_id),
                            "主要异议": _joined(item.get("objections")),
                            "切换触发": _joined(item.get("triggers")),
                        }
                    )
        blocks = [
            {
                "type": "narrative",
                "text": (
                    "定性理由与定量选择冲突时必须作为反证保留；"
                    "不得由 Writer 删除或改写为确定结论。"
                ),
                "source_refs": portable_refs,
            }
        ]
        if objections:
            blocks.append(
                {
                    "type": "table",
                    "title": "分群异议与切换触发",
                    "columns": ["客群", "主要异议", "切换触发"],
                    "rows": objections,
                    "source_refs": portable_refs,
                }
            )
        pages.append(
            _customer_page(
                section_id="VA3",
                level=input_level,
                blocks=blocks,
                source_refs=portable_refs,
                evidence_type="analysis_inference",
                takeaway="客群异议与切换触发必须转化为产品、价格和到访验证任务，不能写成成交承诺。",
            )
        )

    if "CS" in included_units:
        cohort = bundle.get("synthetic_cohort")
        cohort = cohort if isinstance(cohort, Mapping) else {}
        blocks = [
            {
                "type": "table",
                "title": "客群模型冻结记录",
                "columns": ["记录项", "冻结值"],
                "rows": [
                    {"记录项": "客群包版本", "冻结值": bundle.get("version")},
                    {"记录项": "证据等级", "冻结值": f"C{level}"},
                    {"记录项": "合成样本", "冻结值": cohort.get("cohort_id")},
                    {"记录项": "随机种子", "冻结值": cohort.get("seed")},
                    {
                        "记录项": "产物哈希",
                        "冻结值": _joined(bundle.get("artifact_hashes")),
                    },
                    {
                        "记录项": "数据权利状态",
                        "冻结值": bundle.get("rights_status"),
                    },
                ],
                "source_refs": portable_refs,
            },
            {
                "type": "narrative",
                "text": "允许用途：" + _joined(bundle.get("allowed_uses")),
                "source_refs": portable_refs,
            },
            {
                "type": "narrative",
                "text": "禁止用途：" + _joined(bundle.get("prohibited_uses")),
                "source_refs": portable_refs,
            },
        ]
        pages.append(
            _customer_page(
                section_id="CS",
                level=input_level,
                blocks=blocks,
                source_refs=portable_refs,
                evidence_type="analysis_inference",
                takeaway="当前为 C2 方案比较证据：可观察相对偏好，不可推导月销量或输出个人数据。",
            )
        )
    return pages


class ReportCompilerAdapter:
    """Create compiler input using only an already assembled ReportRun."""

    def build_report_seed(
        self,
        run: ReportRun,
        *,
        required_units: Sequence[str] | None = None,
        edition: ReportEdition | str = ReportEdition.DECISION_REPORT,
    ) -> dict[str, Any]:
        report_edition = ReportEdition(str(edition))
        required = tuple(dict.fromkeys(str(item) for item in (
            required_units or required_units_from_metadata(run.metadata)
        )))
        if not required:
            raise ValueError("required_units must not be empty")
        unknown = [item for item in required if item not in VALID_SECTION_IDS]
        if unknown:
            raise ValueError(f"unknown required units: {unknown}")
        source_id_map = _source_id_map(run)
        pages: list[dict[str, Any]] = []
        for section_id in required:
            section = run.sections.get(section_id)
            if not isinstance(section, SectionResult):
                section = SectionResult(
                    section_id=section_id,
                    data={},
                    gaps=[UNIT_BY_ID[section_id]["gap"]],
                    status=ResolvedStatus.UNKNOWN,
                )
            status = _section_status(section)
            title = UNIT_BY_ID[section_id]["title"]
            takeaway = (
                str(section.conclusions[0])
                if section.conclusions
                else str(section.gaps[0])
                if section.gaps
                else "本单元尚未形成有证据支持的结论。"
            )
            evidence_type = (
                "observed_fact"
                if section_id == "SC2"
                else "analysis_inference"
            )
            pages.append(
                {
                    "page_id": f"{section_id.lower()}-primary",
                    "chapter_id": section_id.lower(),
                    "section_id": section_id,
                    "unit_id": section_id,
                    "unit_status": status,
                    "layout": "gap" if status in {"missing", "blocked"} else "summary",
                    "title": title,
                    "decision_question": _decision_question(section_id),
                    "takeaway": takeaway,
                    "decision_impact": _decision_impact(section_id),
                    "blocks": _section_blocks(section, source_id_map),
                    "source_refs": _mapped_source_refs(
                        section.evidence_refs,
                        source_id_map,
                    ),
                    "confidence": {"score": _confidence_score(section)},
                    "evidence_type": evidence_type,
                }
            )
        source_registry = [
            {
                "source_id": source_id_map[item.evidence_id],
                "name": item.metric_id or source_id_map[item.evidence_id],
                "source_type": item.evidence_type.value,
                "trust_tier": "dds_recalculated",
                "status": "frozen",
                "canonical_ref": (
                    f"dds:evidence/{source_id_map[item.evidence_id]}"
                ),
                "evidence_id_sha256": _portable_source_id(item.evidence_id)[1],
                "used_for": item.metric_id,
                "limitations": list(item.limitations),
                "sha256": item.source_hash,
                "captured_at": _portable(item.effective_at),
                "rights_status": "source_policy_required",
                "source_scope": item.geography,
            }
            for item in sorted(run.evidence_records, key=lambda value: value.evidence_id)
        ]
        context = run.project_context
        profile = run.metadata.get("analysis_profile")
        included = list(required)
        for section_id in optional_units_from_metadata(run.metadata):
            section = run.sections.get(str(section_id))
            if isinstance(section, SectionResult) and _section_status(section) not in {"missing", "blocked"}:
                included.append(str(section_id))
        included = list(dict.fromkeys(included))
        if included != list(required):
            pages_by_unit = {str(page["section_id"]): page for page in pages}
            for section_id in included:
                if section_id in pages_by_unit:
                    continue
                section = run.sections[section_id]
                pages.append({
                    "page_id": f"{section_id.lower()}-primary",
                    "chapter_id": section_id.lower(),
                    "section_id": section_id,
                    "unit_id": section_id,
                    "unit_status": _section_status(section),
                    "layout": "summary",
                    "title": UNIT_BY_ID[section_id]["title"],
                    "decision_question": _decision_question(section_id),
                    "takeaway": str(section.conclusions[0]) if section.conclusions else "条件单元已有实质内容。",
                    "decision_impact": _decision_impact(section_id),
                    "blocks": _section_blocks(section, source_id_map),
                    "source_refs": _mapped_source_refs(section.evidence_refs, source_id_map),
                    "confidence": {"score": _confidence_score(section)},
                    "evidence_type": "analysis_inference",
                })
        pages.extend(_customer_pages(run.metadata, source_id_map, included))
        if report_edition is ReportEdition.DECISION_REPORT:
            pages = decision_pages(
                pages,
                blocking_page_ids=tuple(
                    str(item)
                    for item in run.metadata.get(
                        "decision_blocking_page_ids", ()
                    )
                ),
            )
            included = list(
                dict.fromkeys(str(page["section_id"]) for page in pages)
            )
        report_required = (
            included
            if report_edition is ReportEdition.DECISION_REPORT
            else list(required)
        )
        return {
            "meta": {
                "as_of": _portable(context.base_date),
                "compiled_at": f"{_portable(context.base_date)}T00:00:00Z",
                "run_id": run.run_id,
                "analysis_profile": profile,
                "decision_scope": run.metadata.get("decision_scope"),
                "report_edition": report_edition.value,
            },
            "project": {
                "project_id": context.project_id,
                "name": context.project_name or context.project_id,
                "city": context.city,
                "district": context.district,
                "project_type": context.project_type,
            },
            "project_panorama": {
                "required_units": report_required,
                "included_units": included,
                "intervention_required_units": list(required),
                "unit_policy": profile.get("unit_policy", {}) if isinstance(profile, Mapping) else {},
            },
            "page_manifest_authoritative": True,
            "page_manifest": pages,
            "source_registry": source_registry,
            "evidence_gaps": [
                {
                    "section_id": section_id,
                    "gap": str(gap),
                }
                for section_id in required
                for gap in getattr(run.sections.get(section_id), "gaps", [])
            ] if report_edition is ReportEdition.EVIDENCE_WORKBOOK else [],
        }

    def build_frozen_compiler_package(
        self,
        run: ReportRun,
        *,
        required_units: Sequence[str] | None = None,
        as_of: str | date | None = None,
        asset_resolver: AssetResolver | None = None,
        edition: ReportEdition | str = ReportEdition.DECISION_REPORT,
    ) -> dict[str, Any]:
        seed = self.build_report_seed(
            run,
            required_units=required_units,
            edition=edition,
        )
        frozen_as_of = as_of or run.project_context.base_date
        if frozen_as_of is None:
            raise ValueError("as_of or project_context.base_date is required")
        return build_frozen_package(
            seed,
            project_id=run.project_context.project_id,
            request_id=run.run_id,
            as_of=frozen_as_of,
            asset_resolver=asset_resolver,
        )


__all__ = ["ReportCompilerAdapter"]
