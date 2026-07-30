"""Canonical DDS report structure: four groups and twelve decision units.

The public report language uses SC / AD / VA / CS.  Legacy module and chapter
identifiers remain available as provenance, but they never drive the visible
directory or page code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


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


_CHAPTER_TO_SECTION = {
    # SC1 — decision question and evidence boundary
    "cover": "SC1",
    "projectidentity": "SC1",
    "decision": "SC1",
    "directordecision": "SC1",
    "executive": "SC1",
    "executivesummary": "SC1",
    "summary": "SC1",
    "sourceboundary": "SC1",
    "inputboundary": "SC1",
    # SC2 — macro, market, customer and market-result cases
    "macro": "SC2",
    "macrocontext": "SC2",
    "market": "SC2",
    "competitorseries": "SC2",
    "competitorcases": "SC2",
    "marketcases": "SC2",
    "social": "SC2",
    "socialintelligence": "SC2",
    "personaevidence": "SC2",
    "personaevidenceprofiles": "SC2",
    "syntheticpersonas": "SC2",
    # SC3 — site, statutory and engineering constraints
    "site": "SC3",
    "parcel": "SC3",
    "gis": "SC3",
    "siteconstraints": "SC3",
    "engineeringconstraints": "SC3",
    # AD1 — three directional schemes
    "concept": "AD1",
    "conceptoptions": "AD1",
    "schemecomparison": "AD1",
    "optioncomparison": "AD1",
    "massingoptions": "AD1",
    # AD2 — recommendation and gate
    "recommendedscheme": "AD2",
    "schemerecommendation": "AD2",
    "decisionchain": "AD2",
    "decisiongate": "AD2",
    # AD3 — product and stock
    "product": "AD3",
    "productstrategy": "AD3",
    "unitmix": "AD3",
    "inventory": "AD3",
    # AD4 — architecture and design-intent cases
    "architecture": "AD4",
    "masterplan": "AD4",
    "unitplan": "AD4",
    "facade": "AD4",
    "landscape": "AD4",
    "publicspace": "AD4",
    "clubhouse": "AD4",
    "demozone": "AD4",
    "showcase": "AD4",
    "archlibcases": "AD4",
    "casetransfer": "AD4",
    "designcases": "AD4",
    # AD5 — traditional culture and real market perception
    "traditionalspatialculture": "AD5",
    "traditionalculture": "AD5",
    "fengshui": "AD5",
    # VA1 — design value premium
    "designvaluepremium": "VA1",
    "premiumanalysis": "VA1",
    "premium": "VA1",
    # VA2 — absorption, finance and investment
    "absorptionforecast": "VA2",
    "absorption": "VA2",
    "investmentcase": "VA2",
    "investment": "VA2",
    "finance": "VA2",
    "financial": "VA2",
    "cashflow": "VA2",
    # VA3 — risk, execution and responsibility
    "risk": "VA3",
    "execution": "VA3",
    "actionregister": "VA3",
    "implementation": "VA3",
    # CS — detailed evidence, assumptions and work papers
    "knowledgeaudit": "CS",
    "evidenceappendix": "CS",
    "sourceappendix": "CS",
    "sourceregistry": "CS",
    "sources": "CS",
    "method": "CS",
    "methodology": "CS",
    "assumptions": "CS",
    "provenance": "CS",
    "workpapers": "CS",
}

_SPECIAL_UNIT_ROLES = {
    "AD1": "concept_options",
    "AD2": "recommended_scheme",
    "AD4": "architecture_design",
    "VA1": "design_value_premium",
    "CS": "confidence_state",
}

_INPUT1_AD_UNIT_OVERRIDES = {
    "AD1": {
        "title": "定位与概念路线",
        "question": "哪些市场、客群与场地机会应转化为可验证的产品定位和概念路线？",
        "gap": "尚未把机会、客群和场地判断转成可验证的定位与概念路线。",
    },
    "AD2": {
        "title": "方向选择与验证闸门",
        "question": "当前优先探索哪个方向，什么证据会支持、切换或否定它？",
        "gap": "尚未形成优先方向、验证条件、切换触发与回退机制。",
    },
    "AD3": {
        "title": "客群假设与产品参数",
        "question": "目标客群假设如何转成待验证的面积、总价、功能和产品参数？",
        "gap": "尚未把客群与需求假设转成可验证的产品参数。",
    },
    "AD4": {
        "title": "概念空间机制与设计假设",
        "question": "产品方向如何转成可验证的总图、户型、立面、景观与公共空间机制？",
        "gap": "尚未形成概念空间机制、设计假设及其验证任务。",
    },
    "AD5": {
        "title": "文化线索与市场感知边界",
        "question": "哪些文化与空间线索值得纳入概念探索，哪些解释不得越权成为市场结论？",
        "gap": "尚未登记可用于概念探索的文化线索、证据等级与禁止用途。",
    },
}

_INPUT2_AD_UNIT_OVERRIDES = {
    "AD1": {
        "title": "定位、容量与可建包络",
        "question": "哪些市场、客群与场地输入有资格进入设计，完整容量和可建包络如何建立？",
        "gap": "尚未把已确认的市场、客群、功能指标与法定边界转成定位、容量和可建包络。",
    },
    "AD2": {
        "title": "约束驱动策略与评价基线",
        "question": "哪些约束必须同时求解，后续候选路线应按什么统一口径检验？",
        "gap": "尚未形成约束驱动的策略任务、统一评价维度、验证门槛与回退条件。",
    },
    "AD3": {
        "title": "未来客群与产品参数",
        "question": "未来客群假设如何转成待校准的面积、总价、功能与货量参数？",
        "gap": "尚未把未来客群、家庭阶段与支付边界转成可校准的产品参数。",
    },
    "AD4": {
        "title": "空间机制与技术闭环",
        "question": "空间机制如何转成可校核的设计动作、技术接口与阶段闸门？",
        "gap": "尚未形成空间机制、设计动作、技术接口与验收条件的闭环。",
    },
    "AD5": {
        "title": "设计任务书与阶段边界",
        "question": "本轮向设计团队交付什么，哪些判断必须等待候选方案或补证？",
        "gap": "尚未冻结本轮设计任务、探索边界、禁止结论与下一阶段触发条件。",
    },
}

_MODE_AD_GROUP_LABELS = {
    1: "产品方向与概念路线",
    2: "产品定位与设计任务",
}

_MODE_AD_UNIT_OVERRIDES = {
    1: _INPUT1_AD_UNIT_OVERRIDES,
    2: _INPUT2_AD_UNIT_OVERRIDES,
}


def _marker(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").lower())


def _explicit_section(value: Any) -> str:
    candidate = str(value or "").strip().upper().replace(" ", "")
    return candidate if candidate in UNIT_BY_ID else ""


def framework_manifest(selected_mode: Any = None) -> dict[str, Any]:
    """Return a JSON-safe copy of the visible directory contract."""
    try:
        mode = int(selected_mode) if selected_mode is not None else None
    except (TypeError, ValueError):
        mode = None
    groups = [{**deepcopy(item), "units": list(item["units"])} for item in REPORT_GROUPS]
    units = [{**deepcopy(item), "unit_id": str(item["section_id"])} for item in REPORT_UNITS]
    group_label = _MODE_AD_GROUP_LABELS.get(mode)
    unit_overrides = _MODE_AD_UNIT_OVERRIDES.get(mode, {})
    if group_label:
        for group in groups:
            if group["group_id"] == "AD":
                group["label"] = group_label
                break
    if unit_overrides:
        for unit in units:
            override = unit_overrides.get(str(unit["section_id"]))
            if override:
                unit.update(deepcopy(override))
    return {
        "framework_id": FRAMEWORK_ID,
        "framework_version": FRAMEWORK_VERSION,
        "version": FRAMEWORK_VERSION,
        "groups": groups,
        "units": units,
        "page_contract": {
            "stable_id": "page_id",
            "group_field": "group_id",
            "unit_field": "unit_id",
            "display_field": "display_code",
            "display_pattern": "<unit_id>·<two-digit page number within unit>",
            "legacy_fields": [
                "chapter_id",
                "section_id",
                "section_group",
                "page_code",
            ],
        },
    }


def section_metadata(page: Mapping[str, Any]) -> dict[str, str]:
    """Resolve one page to a canonical visible section.

    Explicit ``section_id``/``unit_id`` always wins.  Evidence appendices are
    routed to CS regardless of their legacy module chapter.
    """
    explicit = _explicit_section(page.get("section_id") or page.get("unit_id"))
    appendix = (
        str(page.get("story_role") or "").lower() == "evidence_appendix"
        or str(page.get("appendix_policy") or "").lower() == "evidence"
        or "appendix" in str(page.get("layout") or "").lower()
    )
    if appendix:
        section_id = "CS"
        classification = "appendix"
    elif explicit:
        section_id = explicit
        classification = "explicit"
    else:
        markers = (
            _marker(page.get("chapter_id")),
            _marker(page.get("layout")),
            _marker(page.get("page_id")),
        )
        section_id = next(
            (_CHAPTER_TO_SECTION[item] for item in markers if item in _CHAPTER_TO_SECTION),
            "",
        )
        if not section_id:
            for marker in markers:
                matches = [
                    (len(token), mapped)
                    for token, mapped in _CHAPTER_TO_SECTION.items()
                    if len(token) >= 4 and token in marker
                ]
                if matches:
                    section_id = max(matches, key=lambda item: item[0])[1]
                    break
        section_id = section_id or "CS"
        classification = (
            "mapped"
            if section_id != "CS" or any(item in _CHAPTER_TO_SECTION for item in markers)
            else "fallback"
        )

    unit = UNIT_BY_ID[section_id]
    group = GROUP_BY_ID[unit["group_id"]]
    return {
        "framework_version": FRAMEWORK_VERSION,
        "unit_id": section_id,
        "group_id": str(group["group_id"]),
        "section_id": section_id,
        "section_group": str(group["group_id"]),
        "section_group_title": str(group["name"]),
        "section_group_label": str(group["label"]),
        "section_title": str(unit["title"]),
        "section_classification": classification,
    }


def apply_section_metadata(page: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(page))
    existing_classification = str(result.get("section_classification") or "")
    existing_section_title = str(result.get("section_title") or "").strip()
    existing_group_label = str(result.get("section_group_label") or "").strip()
    result.update(section_metadata(result))
    if existing_classification:
        result["section_classification"] = existing_classification
    if existing_group_label:
        result["section_group_label"] = existing_group_label
    if existing_section_title:
        result["section_title"] = existing_section_title
    return result


def framework_gap_page(section_id: str) -> dict[str, Any]:
    """Create an explicit gap page instead of silently omitting a unit."""
    unit = UNIT_BY_ID[section_id]
    evidence_type = "traditional_interpretation" if section_id == "AD5" else "analysis_inference"
    return {
        "page_id": f"{section_id.lower()}-framework-gap",
        "chapter_id": "framework_gap",
        "section_id": section_id,
        "unit_status": "missing",
        "unit_role": "framework_gap",
        "layout": "gap",
        "title": str(unit["gap"]),
        "takeaway": str(unit["question"]),
        "blocks": [
            {
                "type": "gap",
                "status": "missing",
                "text": str(unit["gap"]),
                "required_action": "补齐该单元的证据、方案输入、责任人和解锁条件后重新编译。",
            }
        ],
        "chart_specs": [],
        "asset_refs": [],
        "source_refs": [],
        "confidence": {"score": 0.0, "level": "missing", "actionable": False},
        "evidence_type": evidence_type,
        "load_priority": "normal",
        "print_policy": {
            "include": True,
            "page_break_after": True,
            "allow_internal_scroll": False,
        },
        "story_role": "primary_narrative",
        "appendix_policy": "presentation",
        "visual_evidence": "gap",
    }


def unit_contract_satisfied(
    unit_id: str,
    pages: Sequence[Mapping[str, Any]],
) -> bool:
    """Return whether a unit is represented by the correct semantic content.

    Presence alone is insufficient for recommendation, architecture, design
    value and confidence-state units.  This prevents a generic decision chain,
    case card or evidence appendix from creating a false-green directory.
    """
    relevant = [
        page
        for page in pages
        if str(page.get("unit_id") or page.get("section_id") or "") == unit_id
    ]
    if not relevant:
        return False
    required_role = _SPECIAL_UNIT_ROLES.get(unit_id)
    if not required_role:
        return True

    def contract_valid(page: Mapping[str, Any]) -> bool:
        contract = page.get("unit_contract")
        if not isinstance(contract, Mapping):
            return False
        if unit_id == "AD1":
            return int(contract.get("options_count") or 0) >= 3
        if unit_id == "AD2":
            return (
                bool(contract.get("recommendation"))
                and int(contract.get("rejected_options_count") or 0) >= 2
                and bool(contract.get("decision_gate"))
                and bool(contract.get("fallback"))
            )
        if unit_id == "AD4":
            return (
                bool(contract.get("masterplan"))
                and bool(contract.get("unit_plan"))
                and int(contract.get("expression_count") or 0) >= 2
            )
        if unit_id == "VA1":
            upstream = {str(item) for item in contract.get("upstream_refs") or []}
            return {"SC2", "AD2", "AD3", "AD4"}.issubset(upstream) and all(
                bool(contract.get(key))
                for key in ("design_actions", "value_mechanisms", "investment")
            )
        if unit_id == "CS":
            return all(
                bool(contract.get(key))
                for key in ("sources", "methods", "assumptions", "confidence")
            )
        return False

    # A caller-supplied boolean is not proof; required fields remain auditable.
    return any(
        str(page.get("unit_status") or "").lower() == "ready"
        and str(page.get("unit_role") or "") == required_role
        and contract_valid(page)
        for page in relevant
    )


def sort_and_number_pages(
    pages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the visible structure, stable-sort it, and assign page codes."""
    decorated: list[tuple[int, int, int, dict[str, Any]]] = []
    for index, raw_page in enumerate(pages):
        page = apply_section_metadata(raw_page)
        appendix_rank = 1 if page.get("story_role") == "evidence_appendix" else 0
        decorated.append(
            (
                appendix_rank,
                SECTION_ORDER.get(str(page.get("section_id")), len(SECTION_ORDER)),
                index,
                page,
            )
        )
    ordered = [item[3] for item in sorted(decorated, key=lambda item: item[:3])]
    counters: dict[str, int] = {}
    for page in ordered:
        unit_id = str(page["unit_id"])
        counters[unit_id] = counters.get(unit_id, 0) + 1
        display_code = f"{unit_id}·{counters[unit_id]:02d}"
        page["display_code"] = display_code
        page["page_code"] = display_code
    return ordered


def compile_adaptive_manifest(
    pages: Sequence[Mapping[str, Any]],
    *,
    required_units: Sequence[str],
    included_units: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compile only the units required by an adaptive report profile.

    Missing units remain an internal delivery gate.  This entry point never
    manufactures customer-visible framework gap pages.
    """
    required = tuple(dict.fromkeys(str(unit) for unit in required_units))
    unknown = [unit for unit in required if unit not in VALID_SECTION_IDS]
    if unknown:
        raise ValueError(f"unknown required units: {unknown}")

    included = tuple(dict.fromkeys(str(unit) for unit in (included_units or required)))
    unknown_included = [unit for unit in included if unit not in VALID_SECTION_IDS]
    if unknown_included:
        raise ValueError(f"unknown included units: {unknown_included}")
    normalized = [apply_section_metadata(page) for page in pages]
    visible = [page for page in normalized if page.get("section_id") in included]
    visible = sort_and_number_pages(visible)
    present = {str(page.get("section_id")) for page in visible}
    missing = [unit for unit in required if unit not in present]

    return {
        "pages": visible,
        "required_units": list(required),
        "included_units": list(included),
        "missing_required_units": missing,
        "delivery_ready": not missing,
    }


__all__ = [
    "FRAMEWORK_ID",
    "FRAMEWORK_VERSION",
    "REPORT_GROUPS",
    "REPORT_UNITS",
    "SECTION_ORDER",
    "UNIT_BY_ID",
    "VALID_SECTION_IDS",
    "apply_section_metadata",
    "compile_adaptive_manifest",
    "framework_gap_page",
    "framework_manifest",
    "section_metadata",
    "sort_and_number_pages",
    "unit_contract_satisfied",
]
