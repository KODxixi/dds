"""Renderer-neutral DDS ReportDocument and PageManifest compiler.

The compiler creates bounded 16:9 page units while imposing no total report
page limit.  It does not render HTML, mutate the legacy report, or connect to
Flask.  Existing renderers can adopt this contract in a later task.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

try:  # Support package and existing top-level script imports.
    from .evidence_contract import (
        EVIDENCE_TYPES,
        compute_evidence_confidence,
        confidence_level,
        normalize_evidence_type,
    )
except ImportError:  # pragma: no cover - existing app.py import convention
    from evidence_contract import (
        EVIDENCE_TYPES,
        compute_evidence_confidence,
        confidence_level,
        normalize_evidence_type,
    )

try:  # Keep the chart contract independent from the renderer.
    from .report_chart_contract import (
        CHART_CONTRACT_VERSION,
        VISUAL_LANGUAGES,
        normalize_chart_spec,
        resolve_page_visual_language,
    )
except ImportError:  # pragma: no cover - existing app.py import convention
    from report_chart_contract import (
        CHART_CONTRACT_VERSION,
        VISUAL_LANGUAGES,
        normalize_chart_spec,
        resolve_page_visual_language,
    )

try:  # Visible SC / AD / VA / CS report structure.
    from .report_structure_contract import (
        FRAMEWORK_VERSION,
        SECTION_ORDER,
        VALID_SECTION_IDS,
        apply_section_metadata,
        compile_adaptive_manifest,
        framework_gap_page,
        framework_manifest,
        sort_and_number_pages,
        unit_contract_satisfied,
    )
except ImportError:  # pragma: no cover - existing app.py import convention
    from report_structure_contract import (
        FRAMEWORK_VERSION,
        SECTION_ORDER,
        VALID_SECTION_IDS,
        apply_section_metadata,
        compile_adaptive_manifest,
        framework_gap_page,
        framework_manifest,
        sort_and_number_pages,
        unit_contract_satisfied,
    )

try:  # Architectural concept diagrams stay renderer-neutral.
    from .report_diagram_contract import (
        DIAGRAM_CONTRACT_VERSION,
        normalize_diagram_spec,
    )
except ImportError:  # pragma: no cover - existing app.py import convention
    from report_diagram_contract import (
        DIAGRAM_CONTRACT_VERSION,
        normalize_diagram_spec,
    )


SCHEMA_VERSION = "dds.report-document/1.2"
TEMPLATE_ID = "dds-intelligence-report-v1"
TEMPLATE_PROFILE_VERSION = "dds.apple-dark-16x9/1.1.0"
TABLE_ROWS_PER_PAGE = 10

TABLE_TITLES = {
    "indicators": "指标",
    "competitors": "竞品",
    "items": "明细",
    "unit_mix": "产品配比",
    "scenarios": "情景",
}

PAGE_REQUIRED_FIELDS = (
    "page_id",
    "chapter_id",
    "unit_id",
    "group_id",
    "display_code",
    "section_id",
    "section_group",
    "section_group_title",
    "section_group_label",
    "section_title",
    "page_code",
    "layout",
    "title",
    "takeaway",
    "decision_question",
    "decision_impact",
    "blocks",
    "chart_specs",
    "diagram_specs",
    "asset_refs",
    "source_refs",
    "confidence",
    "evidence_type",
    "load_priority",
    "print_policy",
    "story_role",
    "appendix_policy",
    "visual_evidence",
    "visual_language",
)

MODULE_SPECS = (
    {
        "id": "decision",
        "title": "投决摘要与使用边界",
        "evidence_type": "analysis_inference",
        "aliases": (
            "decision",
            "decision_full.decision_summary",
            "decision_summary",
        ),
        "gap": "缺少明确的进入／退出判断、决策门槛和下一步动作。",
    },
    {
        "id": "site",
        "title": "地块、区位与物理证据",
        "evidence_type": "observed_fact",
        "aliases": (
            "site",
            "parcel",
            "scene_context",
            "project_context.site",
        ),
        "gap": "缺少可核验坐标、地块边界、法定约束或场地物理资料。",
    },
    {
        "id": "macro",
        "title": "宏观环境与资料缺口",
        "evidence_type": "observed_fact",
        "aliases": (
            "macro",
            "macro_context",
            "macroeconomics",
            "macro_indicators",
            "decision_full.macro_agent",
        ),
        "gap": "缺少可追溯宏观指标及其统计期、地域口径和来源。",
    },
    {
        "id": "social_intelligence",
        "title": "社媒现实信号",
        "evidence_type": "social_observation",
        "aliases": ("social_intelligence", "social_observations"),
        "gap": "缺少目标城市、目标时间窗内的公开或授权社媒记录。",
    },
    {
        "id": "persona_evidence",
        "title": "现实虚拟人／客群证据画像",
        "evidence_type": "social_observation",
        "aliases": (
            "persona_evidence",
            "persona_evidence_profiles",
            "real_behavior_personas",
            "social_intelligence.persona_evidence_profiles",
        ),
        "gap": "缺少带显式 segment/topic 的真实观察，不能生成证据画像。",
    },
    {
        "id": "traditional_spatial_culture",
        "title": "传统空间文化／风水边界",
        "evidence_type": "traditional_interpretation",
        "aliases": (
            "traditional_spatial_culture",
            "feng_shui",
            "traditional_culture",
        ),
        "gap": "缺少地块红线、真北、道路与水体，传统空间研判被阻断。",
    },
    {
        "id": "synthetic_personas",
        "title": "真实证据校准的合成人格／ABM",
        "evidence_type": "model_simulation",
        "aliases": (
            "synthetic_personas",
            "decision_full.abm_market_agent",
            "decision.abm_market_agent",
        ),
        "gap": "ABM 尚未运行，或缺少可审计的客群先验与模拟设置。",
    },
    {
        "id": "market",
        "title": "市场证据",
        "evidence_type": "observed_fact",
        "aliases": ("market", "market_analysis", "decision_full.market_agent"),
        "gap": "缺少可核验竞品、成交、价格带或供求数据。",
    },
    {
        "id": "competitor_series",
        "title": "竞品价格、供应与去化趋势",
        "evidence_type": "observed_fact",
        "aliases": (
            "competitor_series",
            "market.competitor_series",
            "market.competitors",
        ),
        "gap": "缺少竞品价格、供应、加推、促销、库存和去化的连续时序。",
    },
    {
        "id": "product",
        "title": "产品与客群适配",
        "evidence_type": "analysis_inference",
        "aliases": (
            "product",
            "product_strategy",
            "unit_mix",
            "decision_full.unit_mix_agent",
        ),
        "gap": "缺少产品假设、面积段、总价约束或客群证据。",
    },
    {
        "id": "premium_analysis",
        "title": "溢价来源与可兑现边界",
        "evidence_type": "model_simulation",
        "aliases": (
            "premium_analysis",
            "decision_full.premium_engine",
            "premium_engine",
        ),
        "gap": "缺少可追溯的溢价基准、驱动项、反证和兑现条件。",
    },
    {
        "id": "absorption_forecast",
        "title": "保守／基准／乐观去化预测",
        "evidence_type": "model_simulation",
        "aliases": (
            "absorption_forecast",
            "decision_full.blueprint_logic.absorption_simulation",
        ),
        "gap": "缺少总套数、月均流速、价格条件或真实销售回测。",
    },
    {
        "id": "investment_case",
        "title": "投资回报、现金流与拿地边界",
        "evidence_type": "model_simulation",
        "aliases": (
            "investment_case",
            "decision_full.investment_case",
            "decision_full.blueprint_logic.financial_indicator",
        ),
        "gap": "缺少售价、可售面积、地价、建安、税费和节奏等完整输入，财务结论被阻断。",
    },
    {
        "id": "finance",
        "title": "财务边界",
        "evidence_type": "model_simulation",
        "aliases": (
            "finance",
            "financial",
            "financial_analysis",
            "decision.finance",
            "decision_full.finance_agent",
        ),
        "gap": "缺少地价、成本、售价、节奏和融资等可追溯财务假设。",
    },
    {
        "id": "risk",
        "title": "风险与阻断",
        "evidence_type": "analysis_inference",
        "aliases": (
            "risk",
            "risks",
            "risk_register",
            "decision.risks",
            "decision_full.risk_agent",
        ),
        "gap": "缺少风险登记、证据缺口和人工复核门槛。",
    },
)

# Architecture/value units are report-compiler inputs, not evidence-domain
# replacements.  They become first-class only after the evidence modules have
# been validated and frozen.
UNIT_INPUT_SPECS = (
    {
        "id": "concept_options",
        "section_id": "AD1",
        "title": "方案1／2／3强排比选",
        "evidence_type": "analysis_inference",
    },
    {
        "id": "recommended_scheme",
        "section_id": "AD2",
        "title": "主推方案、淘汰理由与决策闸门",
        "evidence_type": "analysis_inference",
    },
    {
        "id": "architecture_design",
        "section_id": "AD4",
        "title": "建筑与空间落地",
        "evidence_type": "analysis_inference",
    },
    {
        "id": "design_value_premium",
        "section_id": "VA1",
        "title": "设计价值溢价",
        "evidence_type": "model_simulation",
    },
    {
        "id": "confidence_state",
        "section_id": "CS",
        "title": "来源、方法与置信状态",
        "evidence_type": "analysis_inference",
    },
)

_RAW_MARKERS = {
    "rawjson",
    "rawpayload",
    "jsondump",
    "原始json",
    "原始数据转储",
}
_FORBIDDEN_PAYLOAD_KEYS = {
    "raw",
    "payload",
    "personasraw",
    "records",
    "rawjson",
    "rawpayload",
    "jsondump",
    "thumbnail",
    "thumbnails",
    "imageurl",
    "imageurls",
    "posterurl",
    "coverurl",
    "rawpath",
    "localpath",
    "bodyhtml",
    "previewhtml",
    "mediaurls",
    "previewimageurls",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(?:secret|password|passwd|token|credential|security.?code|api.?key|amap.?js.?key)",
    re.I,
)
_LOCAL_ONLY_FIELD_MARKERS = {
    "absolutepath",
    "filepath",
    "filesystempath",
    "internallocator",
    "localfile",
    "localpath",
    "sourcepath",
}
_LOCATOR_FIELD_MARKERS = {
    "canonicalref",
    "canonicalurl",
    "dataorobjectref",
    "href",
    "locator",
    "objectref",
    "path",
    "pathorurl",
    "snapshotref",
    "sourceurl",
    "url",
}
_SAFE_OBJECT_SCHEMES = {
    "archlib",
    "asset",
    "data",
    "dataset",
    "dds",
    "s3",
    "source",
    "tos",
}
_FILE_URI_RE = re.compile(r"(?i)\bfile:(?://)?[^\s\"'<>]+")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)[a-z]:[\\/][^\s\"'<>|]+")
_UNC_PATH_RE = re.compile(
    r"(?i)(?<![:/\\])(?:\\\\|//)[a-z0-9._$-]+[\\/][^\s\"'<>|]+"
)
_IMAGE_DATA_URI_RE = re.compile(
    r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+",
    re.I,
)
_SCHEME_RE = re.compile(r"(?i)^([a-z][a-z0-9+.-]*):")
_LOCAL_REFERENCE_REDACTION = "[local reference removed]"
_SUMMARY_KEYS = (
    "headline",
    "summary",
    "takeaway",
    "recommendation",
    "conclusion",
    "status_note",
    "note",
)
_TABLE_KEYS = {
    "decision": ("recommendations", "gates", "hard_stop", "items"),
    "site": ("constraints", "observations", "items"),
    "macro": ("indicators", "series", "observations"),
    "social_intelligence": ("persona_evidence_profiles",),
    "persona_evidence": ("persona_evidence_profiles", "profiles", "items"),
    "traditional_spatial_culture": (
        "traditional_readings",
        "physical_observations",
        "consumer_perception_links",
    ),
    "synthetic_personas": ("items", "personas", "top_personas"),
    "market": ("competitors", "transactions", "indicators", "items"),
    "competitor_series": ("items", "series", "competitors"),
    "product": ("unit_mix", "products", "recommendations", "items"),
    "premium_analysis": ("drivers", "scenarios", "items"),
    "absorption_forecast": ("scenarios", "series", "items"),
    "investment_case": ("scenarios", "metrics", "cashflows", "assumptions", "items"),
    "finance": ("scenarios", "cashflows", "assumptions", "items"),
    "risk": ("risks", "risk_register", "items", "evidence_gaps"),
}

_ROLE_EFFECTS = {
    "architecture_director": "project_thesis",
    "planning_constraint_agent": "masterplan_and_compliance",
    "product_unit_master": "unit_mix_and_product",
    "luxury_aesthetic_agent": "facade_landscape_and_club",
    "case_benchmark_agent": "precedent_selection",
    "market_value_agent": "market_positioning",
    "marketing_translation_agent": "launch_and_sales_story",
    "execution_pm_agent": "delivery_sequence",
    "risk_trust_agent": "decision_gate",
}

_CORE_DECISION_UNIT_IDS = frozenset(
    unit_id for unit_id in VALID_SECTION_IDS if unit_id != "CS"
)
_SUPPORTING_UNIT_ROLES = frozenset(
    {"decision_chain_support", "market_outcome_case", "design_intent_case"}
)
_CHAIN_ROLE_TARGET_UNITS = {
    "architecturedirector": "SC1",
    "marketvalueagent": "SC2",
    "planningconstraintagent": "SC3",
    "conceptdesignagent": "AD1",
    "massingoptionsagent": "AD1",
    "schemerecommendationagent": "AD2",
    "productunitmaster": "AD3",
    "luxuryaestheticagent": "AD4",
    "casebenchmarkagent": "AD4",
    "traditionalspatialcultureagent": "AD5",
    "fengshuiagent": "AD5",
    "marketingtranslationagent": "VA1",
    "designvalueagent": "VA1",
    "financeagent": "VA2",
    "investmentagent": "VA2",
    "absorptionagent": "VA2",
    "executionpmagent": "VA3",
    "risktrustagent": "VA3",
}
_CHAIN_TARGET_TOKENS = {
    "SC1": (
        "projectthesis",
        "decisionboundary",
        "sourceboundary",
        "inputboundary",
        "投决命题",
        "证据边界",
    ),
    "SC2": (
        "marketvalue",
        "marketopportunity",
        "competitor",
        "customerinsight",
        "socialintelligence",
        "市场机会",
        "竞品",
        "客群",
        "供需",
    ),
    "SC3": (
        "planningconstraint",
        "siteconstraint",
        "engineeringconstraint",
        "statutorycondition",
        "场地边界",
        "规划条件",
        "消防",
        "人防",
        "日照",
    ),
    "AD1": (
        "conceptoption",
        "massingoption",
        "schemecomparison",
        "方案比选",
        "强排比选",
    ),
    "AD2": (
        "schemerecommendation",
        "recommendedscheme",
        "mainrecommendation",
        "主推方案",
        "方案推荐",
    ),
    "AD3": (
        "productunit",
        "unitmix",
        "productstrategy",
        "产品定位",
        "面积段",
        "货量",
        "户型",
    ),
    "AD4": (
        "architecturaldesign",
        "masterplan",
        "unitplan",
        "facade",
        "landscape",
        "clubhouse",
        "建筑落地",
        "总图",
        "立面",
        "景观",
        "会所",
    ),
    "AD5": (
        "traditionalspatialculture",
        "fengshui",
        "传统空间文化",
        "风水",
    ),
    "VA1": (
        "designvalue",
        "valuepremium",
        "marketingtranslation",
        "设计价值溢价",
        "价值投入",
    ),
    "VA2": (
        "absorptionforecast",
        "investmentcase",
        "cashflow",
        "financialvalidation",
        "去化预测",
        "现金流",
        "投资验证",
    ),
    "VA3": (
        "executionpm",
        "risktrust",
        "implementation",
        "riskclosure",
        "实施闭环",
        "风险闭环",
    ),
}

_CASE_ROLE_ALIASES = {
    "market_outcome": "market_outcome",
    "marketoutcome": "market_outcome",
    "market_result": "market_outcome",
    "marketresult": "market_outcome",
    "competitor_outcome": "market_outcome",
    "竞品结果": "market_outcome",
    "市场结果": "market_outcome",
    "design_intent": "design_intent",
    "designintent": "design_intent",
    "design_reference": "design_intent",
    "designreference": "design_intent",
    "precedent": "design_intent",
    "archlib_visual": "design_intent",
    "archlibvisual": "design_intent",
    "dcbbs_document": "design_intent",
    "dcbbsdocument": "design_intent",
    "professional_mechanism": "design_intent",
    "professionalmechanism": "design_intent",
    "设计意向": "design_intent",
    "设计对标": "design_intent",
    "market_competitor": "market_outcome",
    "marketcompetitor": "market_outcome",
}
_OUTCOME_POLARITY_ALIASES = {
    "positive": "positive",
    "success": "positive",
    "upside": "positive",
    "正向": "positive",
    "成功": "positive",
    "negative": "negative",
    "failure": "negative",
    "downside": "negative",
    "负向": "negative",
    "失败": "negative",
    "mixed": "mixed",
    "分化": "mixed",
    "mixed_result": "mixed",
    "neutral": "neutral",
    "中性": "neutral",
    "unknown": "unknown",
    "未知": "unknown",
}

_CASE_TRANSFER_ACTIONS = {
    "masterplan": "把案例机制转译为入口、组团、消防、资源面与分期的方案比选项。",
    "unit_plan": "把案例机制转译为面积效率、采光、收纳、家政与公私动线的户型校核项。",
    "facade_detail": "把案例机制转译为材料、分格、节点耐久与成本上限的立面任务。",
    "luxury_aesthetic": "只转译礼序、私密、尺度和材料触感，不复制造型与成本等级。",
    "sales_center": "把案例机制转译为到达、停顿、转折、展示与后续运营的首展路径。",
    "intention_image": "只提取可执行的空间关系与体验目标，不把氛围图当成事实或交付承诺。",
    "evidence_image": "仅作为事实或交付质感证据；若无同尺度、同客群验证，不进入形态决策。",
}

_CASE_TRANSFER_CONDITIONS = {
    "masterplan": [
        "红线、真北、法定出入口、消防登高面、道路标高与地形高差已核验。",
        "案例与本项目的容积率、地块尺度、分期和地下室边界可比。",
    ],
    "unit_plan": [
        "目标总价、面积段、层高、结构柱网、采光规范与设备条件已锁定。",
        "户型动作已回填可售面积、货值和主力客群覆盖，不以风格图代替平面校核。",
    ],
    "facade_detail": [
        "立面成本限额、材料供应、节点防水耐久、样板与维护周期已核验。",
        "仅迁移分格、深度、材料和节点机制，不迁移造型结果。",
    ],
    "luxury_aesthetic": [
        "客群偏好有真实观察支持，且消防、运维与成本边界不被牺牲。",
        "礼序、私密、尺度和触感均已转成可量化设计指标。",
    ],
    "sales_center": [
        "展示区永久／临建策略、到达流线、后续运营与拆改成本已锁定。",
        "体验路径已转成平面、节点、材料和运营任务书。",
    ],
    "intention_image": [
        "意向仅作为体验目标，已转成尺寸、材料、节点、成本与验收标准。",
    ],
    "evidence_image": [
        "原始图、图注、项目身份、拍摄时间与来源权利状态完成一致性核验。",
    ],
}

_CASE_PROHIBITIONS = {
    "masterplan": "不得复制异地容积率、地块尺度、消防条件、分期和组团形态。",
    "unit_plan": "不得脱离本地总价、结构、采光和交付标准复制户型。",
    "facade_detail": "不得把材料质感图直接等同于成本可控、耐久可交付的立面。",
    "luxury_aesthetic": "不得把氛围、品牌或超配投入直接等同于本项目溢价。",
    "sales_center": "不得把临时展示效果直接承诺为永久交付或长期运营结果。",
    "intention_image": "不得把意向图当成实测事实、施工图或交付承诺。",
    "evidence_image": "不得用单张图片证明价格、客群、成本、去化或投资回报。",
}

_CASE_ROLE_CONFLICTS = {
    "masterplan": "容积率、地块尺度、出入口、消防、地下室和分期条件尚未证明可比。",
    "unit_plan": "面积段、总价、结构柱网、采光规范和交付标准尚未证明可比。",
    "facade_detail": "材料供应、节点做法、耐久维护和立面成本尚未证明可比。",
    "luxury_aesthetic": "客群审美、气候适应、运营维护和超配投入尚未证明可比。",
    "sales_center": "临建／永久属性、展示动线、后续运营和拆改成本尚未证明可比。",
    "intention_image": "图像只表达体验目标，尚未形成尺寸、材料、节点和成本证据。",
    "evidence_image": "图片只能证明可见事实，不能证明价格、客群、成本或去化结果。",
}

_ROLE_DECISION_GATES = {
    "architecture_director": "红线与规划条件、竞品时序及核心财务输入均有来源，并由建筑总监签字后，设计 thesis 才能从草案升级为基线。",
    "planning_constraint_agent": "红线、真北、法定出入口、消防登高面、高差与周边道路齐全，且至少完成两个总图量化比选后，才可锁定总图。",
    "product_unit_master": "竞品面积／总价／成交／去化时序和客群证据补齐，且产品套数与去化模型口径一致后，才可锁定户型配比。",
    "luxury_aesthetic_agent": "材料、节点样板、成本限额、维护周期与真实客群偏好均可核验后，审美策略才可进入扩初。",
    "case_benchmark_agent": "项目身份、图片权属、气候、客群与尺度核验完成，并具备至少一个同城同类案例和一个反例后，案例才可进入主证明。",
    "market_value_agent": "至少五个有效住宅竞品具备30／90／180天价格、供应、促销、库存和去化序列，且异常值处理可复现后，才可用于定价。",
    "marketing_translation_agent": "每条卖点均绑定可交付设计动作、来源和禁用边界，并通过设计与法务复核后，才可对外传播。",
    "execution_pm_agent": "每个阻断缺口均有责任人、截止时间、交付物和验收人，且依赖项已关闭后，才可推进下一阶段。",
    "risk_trust_agent": "全部 blocking gaps 关闭；低置信结论已补证或降级；财务、社媒和传统文化边界检查通过后，才可升级决策状态。",
}

_ROLE_MARKET_EFFECTS = {
    "architecture_director": "形成一致的产品命题与专业协同边界，但在证据未闭合前仅用于组织设计推演。",
    "planning_constraint_agent": "资源面、到达、噪声与楼栋排序影响可售性和楼栋价值梯度。",
    "product_unit_master": "直接影响面积段、总价覆盖和目标客群命中，但需真实市场与客群证据校准。",
    "luxury_aesthetic_agent": "影响可感知品质、到访转化和交付口碑，不能由氛围图单独证明。",
    "case_benchmark_agent": "案例只用于提出和检验空间机制，不构成本地价格或需求证明。",
    "market_value_agent": "限定可讨论的价格带、供需和去化假设，截面数据不能替代趋势。",
    "marketing_translation_agent": "影响卖点理解与转化路径，但传播热度不能替代真实购买行为。",
    "execution_pm_agent": "保障关键资料、设计动作和市场验证按闸门顺序落地。",
    "risk_trust_agent": "暴露样本污染、口径冲突和陈旧性，防止弱证据升级为市场共识。",
}

_ROLE_FINANCIAL_EFFECTS = {
    "architecture_director": "当前不直接改写售价、ROI或IRR，只作为后续成本与价值测试的输入。",
    "planning_constraint_agent": "仅实测土方、日照、消防、地下室效率和可售资源排序可进入成本与货值模型。",
    "product_unit_master": "产品配比与可售面积核验后才影响货值；合成人格不得直接改写WTP或拿地价。",
    "luxury_aesthetic_agent": "只有成本限额和成交、问卷或行为证据支持的设计动作，才可作为溢价情景输入。",
    "case_benchmark_agent": "不得外推案例售价、成本、去化、ROI或IRR。",
    "market_value_agent": "市场证据只作为售价和去化情景输入，不直接生成拿地上限。",
    "marketing_translation_agent": "传播声量不得直接进入WTP、ROI、IRR或地价公式。",
    "execution_pm_agent": "未通过资料和责任闸门前，不释放投决参数或成本承诺。",
    "risk_trust_agent": "阻断无来源的售价、去化、ROI、IRR与地价结论进入正式投决。",
}

_ROLE_ACTION_ACCEPTANCE = {
    "architecture_director": "形成带版本号的 thesis／任务书，逐条绑定证据、案例、责任人和人审签字。",
    "planning_constraint_agent": "提交含红线、真北、道路、高差、噪声、消防和量化比选矩阵的总图包，由规划负责人签字。",
    "product_unit_master": "提交竞品面积／总价／去化矩阵与户型货值表，由市场和产品负责人双签。",
    "luxury_aesthetic_agent": "提交材料、节点、成本、样板和维护清单，由设计与成本负责人双签。",
    "case_benchmark_agent": "案例身份、来源、权属、机制和边界逐项核验，并保留至少一个同城案例和一个反例。",
    "market_value_agent": "有效住宅样本不少于5个，30／90／180天时序、来源和异常值处理均可复现。",
    "marketing_translation_agent": "每条卖点绑定图纸节点、证据与禁用口径，并经设计和法务确认。",
    "execution_pm_agent": "交付物、责任人、截止时间、依赖和验收人齐全，状态已回填。",
    "risk_trust_agent": "blocking gaps 为0；低于0.55的结论全部补证或降级；审计记录可追溯。",
}

_ROLE_ACTION_FALLBACK = {
    "architecture_director": "冻结设计thesis及其下游任务，不进入方案基线。",
    "planning_constraint_agent": "冻结总图、入口、组团与货值排序，不进入强排锁定。",
    "product_unit_master": "冻结户型配比、套数与货值，不进入定价或去化模型。",
    "luxury_aesthetic_agent": "冻结材料、节点与溢价表述，不进入扩初或成本承诺。",
    "case_benchmark_agent": "将相关案例降级到证据附录，不进入主证明。",
    "market_value_agent": "冻结价格、去化与拿地参数，保持为市场假设。",
    "marketing_translation_agent": "禁止相关卖点对外发布，保留内部待证状态。",
    "execution_pm_agent": "暂停对应阶段，未关闭依赖不得推进。",
    "risk_trust_agent": "保持decision blocked，禁止升级为可投或可公开结论。",
}

_ACTION_ACCEPTANCE_RULES = (
    ("锁定一句设计 thesis", "提交1页设计thesis，包含目标客群、场地矛盾、核心机制、反证和3项量化指标，由建筑总监签字。"),
    ("把 thesis 拆成", "提交总图、户型、立面、会所和营销5份任务书；每份含输入、输出、责任人、成本边界、截止时间和验收人。"),
    ("低置信节点补来源", "所有证据置信度低于0.55的节点均新增独立来源或完成降级记录，风险审计人签字。"),
    ("补控规/红线/出入口条件", "取得带版本号和来源的红线、控规、真北及法定出入口文件，并由投拓／规划复核。"),
    ("绘制资源面与噪声面", "提交同一坐标基准下的资源、噪声、道路、高差和消防叠合图，标注量化阈值与数据日期。"),
    ("总图策略假设", "至少提交2个总图方案，按日照、消防、资源面、货值、土方和分期形成可复算比选矩阵。"),
    ("竞品面积段与总价", "补齐不少于5个有效住宅竞品的面积、总价、成交与去化时序，记录来源、日期和异常值处理。"),
    ("定义主力/利润/形象户型", "形成主力／利润／形象户型定义表，明确面积、套数、总价、目标客群、货值和退出条件。"),
    ("户型动作转成货值影响", "提交户型动作前后可售面积、单套总价、总货值与成本变化表，并由产品和财务复核。"),
    ("材质与节点词典", "形成材料、分格、节点、供应商、成本上限、耐久和维护周期词典，至少完成1个样板节点。"),
    ("筛掉无法落地", "全部意向图标注采纳／否决、尺寸、材料、节点、成本和非迁移原因，未标注项不得进入主报告。"),
    ("卖点绑定一张证据图", "每个卖点绑定已核验图片、图纸节点、来源、权利状态与交付责任人。"),
    ("候选案例分为", "案例板完成主证明／辅助证明／氛围参考三级分类，并给出进入与退出规则。"),
    ("记录采纳和否决", "每个候选案例写入采纳或否决事件，包含理由、责任人、时间和后续检索调整。"),
    ("补充同尺度", "至少补1个同城同类案例和1个反例，并核验尺度、客群、气候、成本与权属。"),
    ("有效竞品", "有效住宅竞品不少于5个，30／90／180天价格、供应、促销、库存和去化序列可复现。"),
    ("拆分价格带", "提交价格带×面积段×总价×去化矩阵，明确样本半径、异常值、数据期与置信度。"),
    ("乐观/中性/保守", "三档情景分别列出价格、月均流速、总套数、触发条件、反证和失效点，禁止单点承诺。"),
    ("一页价值地图", "提交1页价值地图，每个价值点绑定物理事实、设计动作、客户利益和禁用边界。"),
    ("写 5 条", "提交5条卖点；每条均有来源、可交付节点、反证、禁用词和设计／法务双签。"),
    ("禁止外宣", "形成禁止外宣清单，覆盖无来源溢价、唯一性、健康财富承诺及未交付设计内容。"),
    ("48 小时", "48小时补证清单写明任务、依赖、责任人、截止时间、交付物和验收人，并回填状态。"),
    ("人审通过条件", "形成角色级人审清单，逐项写明通过／拒绝阈值、签字人和证据留存位置。"),
    ("可交付物目录", "输出版本化交付物目录，列明文件名、格式、负责人、依赖、状态和验收日期。"),
    ("blocking gaps", "阻断缺口清单覆盖全部blocking项，逐项给出责任人、截止时间、关闭证据和复核人。"),
    ("低置信 claim 降级", "所有低于0.55的claim均补证或降级为假设，并记录对页面、模型和对外口径的影响。"),
    ("learning_events", "人审意见写入可追溯learning_event，包含原结论、修改、理由、证据和回测触发条件。"),
)


def _action_acceptance(action: str, role_id: str) -> str:
    for marker, acceptance in _ACTION_ACCEPTANCE_RULES:
        if marker in action:
            return acceptance
    base = _ROLE_ACTION_ACCEPTANCE.get(
        role_id,
        "提交可核验交付物，逐条绑定证据、责任人、版本和人审结论。",
    )
    return f"{base} 本动作验收对象：{action}。"


def _path_get(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _has_data(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence)) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        if not value:
            return False
        if isinstance(value, Mapping) and str(value.get("status") or "").lower() in {
            "missing",
            "blocked",
            "not_assessable",
            "not_available",
        }:
            return False
    return True


def _portable_locator(value: Any) -> str | None:
    """Return a public/object/relative locator, never a machine-local path."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return ""
    if (
        _FILE_URI_RE.match(text)
        or _WINDOWS_ABSOLUTE_RE.match(text)
        or _UNC_PATH_RE.match(text)
        or text.startswith(("/", "~"))
    ):
        return None
    scheme = _SCHEME_RE.match(text)
    if scheme:
        name = scheme.group(1).lower()
        if name in {"http", "https"} or name in _SAFE_OBJECT_SCHEMES:
            return text
        return None
    parts = [part for part in re.split(r"[\\/]", text) if part not in {"", "."}]
    if ".." in parts:
        return None
    return text


def _portable_text(value: str) -> str:
    """Redact local locators even when they appear inside narrative text."""
    if _IMAGE_DATA_URI_RE.fullmatch(value):
        # Base64 is opaque binary. Applying path regexes to it can corrupt valid
        # image bytes when random payload characters resemble ``//host/path``.
        return value
    if _portable_locator(value) is None:
        return _LOCAL_REFERENCE_REDACTION
    sanitized = _FILE_URI_RE.sub(_LOCAL_REFERENCE_REDACTION, value)
    sanitized = _WINDOWS_ABSOLUTE_RE.sub(_LOCAL_REFERENCE_REDACTION, sanitized)
    return _UNC_PATH_RE.sub(_LOCAL_REFERENCE_REDACTION, sanitized)


def sanitize_portable_value(value: Any, *, depth: int = 0) -> Any:
    """Deep-copy JSON data while removing machine-local locator material."""
    if depth > 12:
        return None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", key_text.lower())
            if compact in _LOCAL_ONLY_FIELD_MARKERS:
                continue
            if compact in _LOCATOR_FIELD_MARKERS:
                locator = _portable_locator(item)
                if locator is None:
                    continue
                result[key_text] = locator
                continue
            result[key_text] = sanitize_portable_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_portable_value(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return _portable_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return deepcopy(value)
    return _portable_text(str(value))


def _safe_copy(value: Any, *, depth: int = 0) -> Any:
    """Copy JSON-like data while excluding credentials and opaque raw payloads."""
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", key_text.lower())
            if (
                _SENSITIVE_KEY_RE.search(key_text)
                or compact in _RAW_MARKERS
                or compact in _FORBIDDEN_PAYLOAD_KEYS
                or compact in _LOCAL_ONLY_FIELD_MARKERS
            ):
                continue
            if compact in _LOCATOR_FIELD_MARKERS:
                locator = _portable_locator(item)
                if locator is None:
                    continue
                result[key_text] = locator
            else:
                result[key_text] = _safe_copy(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_copy(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return _portable_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return deepcopy(value)
    return _portable_text(str(value))


def _safe_text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return default


def _compact_display_title(value: Any, explicit: Any = "", *, limit: int = 18) -> str:
    preferred = _safe_text(explicit).strip()
    if preferred:
        return preferred[:limit] + ("…" if len(preferred) > limit else "")
    raw = _safe_text(value, "报告页").strip()
    lead = re.split(r"[：:，,；;]", raw, maxsplit=1)[0].strip() or raw
    return lead[:limit] + ("…" if len(lead) > limit else "")


def _safe_scalar(value: Any, default: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return default if value is None else value
    return default


def _extract_modules(report: Mapping[str, Any]) -> dict[str, Any]:
    supplied_modules = report.get("modules") or report.get("module_data") or {}
    if not isinstance(supplied_modules, Mapping):
        supplied_modules = {}
    modules: dict[str, Any] = {}
    for spec in MODULE_SPECS:
        module_id = spec["id"]
        value = supplied_modules.get(module_id)
        # A top-level v4 module is authoritative even when its explicit status is
        # missing/blocked. Falling through to a legacy nested simulation would
        # silently resurrect stale outputs (for example a 500-unit sellout model
        # after the current evidence gate has marked absorption as missing).
        if value is None and module_id in report and isinstance(report.get(module_id), Mapping):
            value = report.get(module_id)
        if not _has_data(value):
            explicit_status = (
                str(value.get("status") or "").lower()
                if isinstance(value, Mapping)
                else ""
            )
            if explicit_status not in {"missing", "blocked", "not_assessable"}:
                value = None
                for alias in spec["aliases"]:
                    candidate = _path_get(report, alias)
                    if _has_data(candidate):
                        value = candidate
                        break
            elif isinstance(value, Mapping):
                modules[module_id] = _safe_copy(value)
                continue
        if not _has_data(value):
            modules[module_id] = {
                "status": "missing",
                "evidence_gaps": [spec["gap"]],
            }
        elif isinstance(value, Mapping):
            modules[module_id] = _safe_copy(value)
            modules[module_id].setdefault("status", "ready")
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            modules[module_id] = {"status": "ready", "items": _safe_copy(value)}
        else:
            modules[module_id] = {"status": "ready", "value": _safe_copy(value)}
    return modules


def _is_missing_module(module: Any) -> bool:
    return not isinstance(module, Mapping) or str(module.get("status") or "") in {
        "missing",
        "blocked",
        "not_available",
        "not_assessable",
    }


def _apply_confidence_gates(
    confidence: Mapping[str, Any],
    *,
    evidence_type: Any = "",
    decision_eligibility: Any = "",
) -> dict[str, Any]:
    """Keep evidence confidence separate from decision actionability."""
    result = _safe_copy(confidence)
    score_actionable = float(result.get("score") or 0.0) >= 0.55
    if "evidence_actionable" in result:
        evidence_actionable = bool(result.get("evidence_actionable"))
    elif "actionable" in result:
        evidence_actionable = bool(result.get("actionable"))
    else:
        evidence_actionable = score_actionable

    eligibility = str(
        decision_eligibility or result.get("decision_eligibility") or ""
    ).strip()
    eligibility_marker = re.sub(r"[_\s]+", "-", eligibility.lower())
    evidence_marker = str(
        evidence_type or result.get("evidence_type") or ""
    ).strip().lower()
    real_evidence_gate = bool(
        result.get("real_evidence_gate") or result.get("empirical_evidence_gate")
    )
    restricted_without_empirical_gate = not real_evidence_gate and (
        evidence_marker == "traditional_interpretation"
        or eligibility_marker in {"not-applicable-demo", "model-only"}
    )
    explicit_actionable = (
        bool(result.get("actionable")) if "actionable" in result else True
    )
    actionable = (
        score_actionable
        and evidence_actionable
        and explicit_actionable
        and not restricted_without_empirical_gate
    )

    result["evidence_actionable"] = evidence_actionable
    result["decision_eligibility"] = eligibility or (
        "eligible" if actionable else "not_eligible"
    )
    result["actionable"] = actionable
    return result


def _score_dict(
    value: Any,
    *,
    missing: bool = False,
    evidence_type: Any = "",
    decision_eligibility: Any = "",
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        if "score" in value:
            try:
                score = float(value.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if not math.isfinite(score):
                score = 0.0
            score = min(1.0, max(0.0, score))
            result = _safe_copy(value)
            result["score"] = round(score, 6)
            result["level"] = confidence_level(score)
            result.pop("level_label", None)
            return _apply_confidence_gates(
                result,
                evidence_type=evidence_type,
                decision_eligibility=decision_eligibility,
            )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        if not math.isfinite(score):
            score = 0.0
        score = min(1.0, max(0.0, score))
        return _apply_confidence_gates(
            {
                "score": round(score, 6),
                "level": confidence_level(score),
            },
            evidence_type=evidence_type,
            decision_eligibility=decision_eligibility,
        )
    if missing:
        result = compute_evidence_confidence({}, cap=0.0)
    else:
        result = compute_evidence_confidence(
            source=0.50,
            coverage=0.45,
            freshness=0.50,
            independent_cross=0.35,
            geographic_relevance=0.60,
            method_fit=0.60,
            stability=0.50,
        )
    return _apply_confidence_gates(
        result,
        evidence_type=evidence_type,
        decision_eligibility=decision_eligibility,
    )


def _module_confidence(module: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("evidence_confidence", "confidence", "overall_confidence"):
        if key in module:
            return _score_dict(module[key], missing=_is_missing_module(module))
    return _score_dict(None, missing=_is_missing_module(module))


def _source_refs(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 6:
        return []
    refs: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"source_refs", "source_ids"}:
                if isinstance(item, str):
                    refs.append(item)
                elif isinstance(item, Sequence):
                    refs.extend(str(ref) for ref in item if isinstance(ref, (str, int)))
            elif key == "source_id" and isinstance(item, (str, int)):
                refs.append(str(item))
            else:
                refs.extend(_source_refs(item, depth=depth + 1))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            refs.extend(_source_refs(item, depth=depth + 1))
    return list(dict.fromkeys(ref for ref in refs if ref))


def _summary_text(module: Mapping[str, Any]) -> str:
    for key in _SUMMARY_KEYS:
        value = module.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:800]
    return ""


def _scalar_metrics(module: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    ignored = {
        "status",
        "confidence",
        "evidence_confidence",
        "overall_confidence",
        "summary",
        "headline",
        "takeaway",
        "recommendation",
        "note",
    }
    for key, value in module.items():
        if key in ignored or _SENSITIVE_KEY_RE.search(str(key)):
            continue
        if isinstance(value, (str, int, float, bool)) and not isinstance(value, str) or (
            isinstance(value, str) and len(value) <= 80
        ):
            metrics.append({"label": str(key), "value": deepcopy(value)})
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            metrics.append({"label": f"{key}_count", "value": len(value)})
        if len(metrics) >= 8:
            break
    return metrics


def _table_from_items(title: str, items: Any) -> dict[str, Any] | None:
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return None
    rows_source = list(items)
    if not rows_source:
        return None
    columns: list[str] = []
    for item in rows_source:
        if not isinstance(item, Mapping):
            continue
        for key, value in item.items():
            compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(key).lower())
            if (
                key in {"records", "personas_raw", "raw", "payload"}
                or compact in _RAW_MARKERS
                or compact in _FORBIDDEN_PAYLOAD_KEYS
                or _SENSITIVE_KEY_RE.search(str(key))
            ):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                if str(key) not in columns:
                    columns.append(str(key))
            elif isinstance(value, Mapping) and "score" in value:
                if str(key) not in columns:
                    columns.append(str(key))
            if len(columns) >= 8:
                break
        if len(columns) >= 8:
            break
    if not columns:
        columns = ["item"]
    rows: list[dict[str, Any]] = []
    for item in rows_source:
        if isinstance(item, Mapping):
            row: dict[str, Any] = {}
            for column in columns:
                value = item.get(column)
                if isinstance(value, Mapping) and "score" in value:
                    value = value.get("score")
                elif isinstance(value, (Mapping, Sequence)) and not isinstance(value, str):
                    value = len(value)
                row[column] = _safe_copy(value)
            rows.append(row)
        else:
            rows.append({columns[0]: _safe_copy(item)})
    return {"type": "table", "title": title, "columns": columns, "rows": rows}


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = []
        for item in value:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, Mapping):
                text = (
                    item.get("statement")
                    or item.get("claim")
                    or item.get("question")
                    or item.get("recommended_action")
                    or item.get("action")
                    or item.get("gap_id")
                )
                if text:
                    values.append(str(text))
            elif isinstance(item, (int, float, bool)):
                values.append(str(item))
    elif isinstance(value, (int, float, bool)):
        values = [str(value)]
    else:
        values = []
    return list(dict.fromkeys(item.strip() for item in values if item and item.strip()))


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _semantic_marker(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _resolve_chain_target_unit(role: Mapping[str, Any]) -> tuple[str, str, str]:
    """Resolve a reasoning chain without treating every chain as AD2.

    Explicit routing is authoritative.  Otherwise only known role identities or
    an unambiguous semantic marker are accepted.  An unresolved chain is routed
    to SC1 as an evidence-boundary issue and remains supporting-only.
    """
    explicit = _safe_text(role.get("target_unit_id")).strip().upper().replace(" ", "")
    if explicit == "CS1":
        explicit = "CS"
    if explicit in _CORE_DECISION_UNIT_IDS:
        return explicit, "explicit", "target_unit_id"

    identity_fields = ("role_id", "chain_id")
    for field in identity_fields:
        marker = _semantic_marker(role.get(field)).removeprefix("chain")
        if marker in _CHAIN_ROLE_TARGET_UNITS:
            return _CHAIN_ROLE_TARGET_UNITS[marker], "role_map", field

    hits: dict[str, list[str]] = {}
    for field in (
        "role_id",
        "chain_id",
        "role_label",
        "question",
        "decision_question",
    ):
        marker = _semantic_marker(role.get(field))
        if not marker:
            continue
        for unit_id, tokens in _CHAIN_TARGET_TOKENS.items():
            if any(_semantic_marker(token) in marker for token in tokens):
                hits.setdefault(unit_id, []).append(field)
    if len(hits) == 1:
        unit_id = next(iter(hits))
        return unit_id, "keyword_map", ",".join(dict.fromkeys(hits[unit_id]))

    return "SC1", "unresolved_boundary", "unresolved_role"


def _normalize_case_role(candidate: Mapping[str, Any], *, is_archlib: bool) -> str:
    raw = _safe_text(
        candidate.get("case_role")
        or candidate.get("evidence_role")
        or candidate.get("case_kind")
    ).strip()
    if raw:
        role = _CASE_ROLE_ALIASES.get(raw.lower()) or _CASE_ROLE_ALIASES.get(
            _semantic_marker(raw)
        )
        if role:
            return role
    return "design_intent" if is_archlib else "market_outcome"


def _normalize_outcome_polarity(value: Any) -> str:
    marker = _safe_text(value).strip().lower()
    if not marker:
        return "unknown"
    return (
        _OUTCOME_POLARITY_ALIASES.get(marker)
        or _OUTCOME_POLARITY_ALIASES.get(_semantic_marker(marker))
        or "unknown"
    )


def _normalize_outcome_metrics(value: Any) -> list[dict[str, Any]]:
    """Keep outcome metrics structured and source-aware without inventing data."""
    candidates: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        metric_keys = {"metric", "metric_id", "name", "label", "value"}
        if metric_keys.intersection(value):
            candidates = [dict(value)]
        else:
            for key, item in value.items():
                if isinstance(item, Mapping):
                    candidates.append({"metric": str(key), **dict(item)})
                elif isinstance(item, (str, int, float, bool)) or item is None:
                    candidates.append({"metric": str(key), "value": item})
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        candidates = [dict(item) for item in value if isinstance(item, Mapping)]

    result: list[dict[str, Any]] = []
    for index, metric in enumerate(candidates):
        metric_name = _safe_text(
            metric.get("metric")
            or metric.get("metric_id")
            or metric.get("name")
            or metric.get("label")
        ).strip()
        if not metric_name or "value" not in metric:
            continue
        normalized = {
            "metric": metric_name,
            "value": _safe_copy(metric.get("value")),
            "unit": _safe_text(metric.get("unit")),
            "period": _safe_text(metric.get("period") or metric.get("time_window")),
            "geography": _safe_text(metric.get("geography")),
            "source_refs": _normalize_refs(metric.get("source_refs")),
        }
        if metric.get("confidence") is not None:
            normalized["confidence"] = _score_dict(metric.get("confidence"))
        normalized["metric_id"] = _safe_text(
            metric.get("metric_id"), f"outcome-metric-{index + 1}"
        )
        result.append(normalized)
    return result


def _stable_id(prefix: str, value: Any) -> str:
    marker = str(value or prefix).strip()
    digest = hashlib.sha256(marker.encode("utf-8")).hexdigest()[:14]
    return f"{prefix}:{digest}"


def _knowledge_gaps(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    director = report.get("architecture_director")
    director = director if isinstance(director, Mapping) else {}
    values: list[dict[str, Any]] = []
    for candidate in (
        report.get("evidence_gaps"),
        report.get("knowledge_gaps"),
        director.get("knowledge_gaps"),
        _path_get(director, "sections.knowledge_gap_brief"),
    ):
        values.extend(_sequence_of_mappings(candidate))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, gap in enumerate(values):
        gap_id = str(gap.get("gap_id") or gap.get("id") or f"gap:{index + 1}")
        if gap_id in seen:
            continue
        seen.add(gap_id)
        result.append(
            {
                "gap_id": gap_id,
                "severity": _safe_text(gap.get("severity"), "confidence_affecting"),
                "blocking_section": _safe_text(gap.get("blocking_section")),
                "statement": _safe_text(
                    gap.get("statement")
                    or gap.get("question")
                    or gap.get("recommended_action")
                    or gap.get("required_action")
                    or gap.get("gap_id")
                ),
                "recommended_action": _safe_text(
                    gap.get("recommended_action") or gap.get("required_action")
                ),
                "owner": _safe_text(gap.get("owner") or gap.get("owner_agent")),
                "acceptance": _safe_text(gap.get("acceptance")),
                "source_refs": _normalize_refs(gap.get("source_refs")),
                "status": _safe_text(gap.get("status"), "open"),
            }
        )
    return result


def _case_role_links(director: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    links: dict[str, dict[str, Any]] = {}
    for role in _sequence_of_mappings(director.get("role_council")):
        chain_id = "chain:" + _slug(role.get("role_id") or role.get("role_label") or "role")
        for ref in _sequence_of_mappings(role.get("case_refs")):
            case_id = str(ref.get("evidence_id") or ref.get("case_id") or "")
            if not case_id:
                continue
            target = links.setdefault(
                case_id,
                {
                    "owners": [],
                    "actions": [],
                    "decision_refs": [],
                },
            )
            owner = _safe_text(role.get("role_label") or role.get("role_id"))
            if owner and owner not in target["owners"]:
                target["owners"].append(owner)
            for action in _string_list(role.get("next_actions")):
                if action not in target["actions"]:
                    target["actions"].append(action)
            if chain_id not in target["decision_refs"]:
                target["decision_refs"].append(chain_id)
    return links


def _case_locality(project_name: str, city: str) -> str:
    if city and city in project_name:
        return "same_city"
    if project_name:
        return "cross_city"
    return "unknown"


def _compile_case_evidence(
    report: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    director = report.get("architecture_director")
    director = director if isinstance(director, Mapping) else {}
    supplied = report.get("case_evidence") or director.get("case_candidates")
    candidates = _sequence_of_mappings(supplied)
    if not candidates:
        return [], [], []
    parcel = report.get("parcel") if isinstance(report.get("parcel"), Mapping) else {}
    project = report.get("project") if isinstance(report.get("project"), Mapping) else {}
    city = _safe_text(parcel.get("city") or project.get("city"))
    role_links = _case_role_links(director)
    cases: list[dict[str, Any]] = []
    archlib_cases: list[dict[str, Any]] = []
    asset_registry: list[dict[str, Any]] = []
    seen_cases: set[str] = set()
    seen_assets: set[str] = set()
    for index, candidate in enumerate(candidates):
        visual = candidate.get("visual_asset")
        visual = visual if isinstance(visual, Mapping) else {}
        strategy = _safe_text(candidate.get("Strategy") or candidate.get("strategy"))
        source_path = _safe_text(
            visual.get("path") or candidate.get("source_path") or candidate.get("asset")
        )
        is_archlib = bool(visual) or "archlib" in strategy.lower() or "archlib" in source_path.lower()
        case_role = _normalize_case_role(candidate, is_archlib=is_archlib)
        project_name = _safe_text(
            candidate.get("Selected case")
            or candidate.get("selected_case")
            or candidate.get("project")
            or visual.get("project"),
            f"案例 {index + 1}",
        )
        case_id = _safe_text(
            candidate.get("case_id")
            or candidate.get("evidence_id")
            or visual.get("asset_id"),
            _stable_id("case", f"{project_name}|{source_path}|{index}"),
        )
        if case_id in seen_cases:
            continue
        seen_cases.add(case_id)
        role = _safe_text(candidate.get("image_role") or visual.get("image_role"), "evidence_image")
        fit_score = _numeric(candidate.get("Score") or candidate.get("score") or candidate.get("fit_score"))
        fit_score = round(max(0.0, min(100.0, fit_score if fit_score is not None else 0.0)), 1)
        fit = _safe_text(candidate.get("Fit") or candidate.get("fit"), "待复核")
        why = _safe_text(
            candidate.get("Why") or candidate.get("why") or candidate.get("why_selected")
        )
        mechanism = _safe_text(
            candidate.get("mechanism")
            or candidate.get("tactic")
            or candidate.get("caption")
            or visual.get("one_liner")
            or why
        )
        prohibited = _string_list(
            candidate.get("prohibited_analogies")
            or candidate.get("conflicts")
            or candidate.get("Rejected")
            or candidate.get("rejected")
        )
        if not prohibited:
            prohibited = [
                _CASE_PROHIBITIONS.get(
                    role,
                    "不得把案例形态、成本、客群与销售结果直接复制到本项目。",
                )
            ]
        role_prohibition = _CASE_PROHIBITIONS.get(role)
        if role_prohibition and role_prohibition not in prohibited:
            prohibited.append(role_prohibition)
        link = role_links.get(case_id, {})
        transfer_actions = _string_list(
            candidate.get("transfer_actions")
            or candidate.get("transfer_action")
            or candidate.get("design_response")
            or candidate.get("tactic")
            or link.get("actions")
        )
        if not transfer_actions:
            transfer_actions = [
                _CASE_TRANSFER_ACTIONS.get(
                    role,
                    "只提取可验证的空间机制，并由设计负责人转译为本项目任务书。",
                )
            ]
        locality = _safe_text(candidate.get("locality")) or _case_locality(project_name, city)
        transfer_conditions = _string_list(candidate.get("transfer_conditions"))
        if not transfer_conditions:
            transfer_conditions = list(
                _CASE_TRANSFER_CONDITIONS.get(
                    role,
                    ["案例机制已转成可量化任务，并由专业负责人完成适配性复核。"],
                )
            )
        conflicts = _string_list(candidate.get("conflicts"))
        if locality == "cross_city":
            conflicts.append("案例为异地样本，气候、客群、规范、成本与供应链尚未证明可比。")
        if _safe_text(candidate.get("climate_fit")) == "mismatch":
            conflicts.append("气候条件与目标城市不一致，只允许迁移机制。")
        identity_status = _safe_text(
            candidate.get("identity_status") or candidate.get("metadata_consistency_status"),
            "unverified",
        )
        if identity_status != "verified":
            conflicts.append("尚未获得独立项目身份、图注与交付结果的交叉验证。")
        role_conflict = _CASE_ROLE_CONFLICTS.get(role)
        if role_conflict:
            conflicts.append(role_conflict)
        conflicts = list(dict.fromkeys(conflicts))
        source_refs = _normalize_refs(candidate.get("source_refs"))
        if is_archlib:
            case_source_id = f"archlib:{case_id}"
            source_refs = [case_source_id, *[ref for ref in source_refs if ref != case_source_id]]
        if case_role == "market_outcome":
            outcome_polarity = _normalize_outcome_polarity(
                candidate.get("outcome_polarity") or candidate.get("polarity")
            )
            outcome_metrics = _normalize_outcome_metrics(candidate.get("outcome_metrics"))
            outcome_period = _safe_text(
                candidate.get("outcome_period") or candidate.get("measurement_period")
            )
            outcome_claim = _safe_text(
                candidate.get("outcome_claim")
                or candidate.get("market_result")
                or candidate.get("observed_outcome")
                or why
            )
            outcome_source_refs = _normalize_refs(candidate.get("outcome_source_refs"))
            for metric in outcome_metrics:
                outcome_source_refs.extend(_normalize_refs(metric.get("source_refs")))
            outcome_source_refs.extend(source_refs)
            outcome_source_refs = list(dict.fromkeys(outcome_source_refs))
            outcome_boundary_note = (
                "市场结果案例只证明指定周期、地域与产品口径下的真实结果；不得把单例直接外推为本项目售价或去化承诺。"
            )
        else:
            outcome_polarity = "not_applicable"
            outcome_metrics = []
            outcome_period = ""
            outcome_claim = ""
            outcome_source_refs = []
            outcome_boundary_note = (
                "设计意向案例只用于空间机制与表达转译，不进入市场结果、售价或去化结论。"
            )
        asset_refs: list[str] = []
        asset_id = _safe_text(visual.get("asset_id") or candidate.get("asset_id"))
        data_uri = _safe_text(visual.get("data_uri") or candidate.get("data_uri"))
        if (source_path or data_uri) and not asset_id:
            asset_id = _stable_id("asset", source_path or data_uri[:64])
        if asset_id:
            asset_refs.append(asset_id)
            if asset_id not in seen_assets:
                seen_assets.add(asset_id)
                asset_registry.append(
                    {
                        "asset_id": asset_id,
                        "source_ref": source_refs[0] if source_refs else "",
                        "mime": _safe_text(visual.get("mime") or candidate.get("mime")),
                        "width": _safe_scalar(visual.get("width") or candidate.get("width"), None),
                        "height": _safe_scalar(visual.get("height") or candidate.get("height"), None),
                        "rights_status": _safe_text(
                            visual.get("rights_status") or candidate.get("rights_status"),
                            "internal_reference",
                        ),
                        "embed_status": "embedded" if data_uri.startswith("data:image/") else "pending",
                        "data_uri": data_uri if data_uri.startswith("data:image/") else "",
                        "data_or_object_ref": source_path,
                    }
                )
        taxonomy_parts = [part for part in re.split(r"[\\/]", source_path) if part]
        raw_confidence = candidate.get("confidence")
        if raw_confidence is None:
            confidence_score = {
                "same_city": 0.58,
                "same_region": 0.54,
                "cross_city": 0.46,
            }.get(locality, 0.44)
            visual_quality = _numeric(visual.get("visual_quality_score"))
            if visual_quality is not None:
                confidence_score += max(-0.05, min(0.08, (visual_quality - 0.5) * 0.16))
            if identity_status == "conflict":
                confidence_score -= 0.25
            raw_confidence = round(max(0.0, min(0.65, confidence_score)), 3)
        case_confidence = _score_dict(raw_confidence)
        if case_role == "market_outcome":
            status = (
                "ready"
                if outcome_polarity != "unknown"
                and outcome_claim
                and outcome_period
                and outcome_metrics
                and outcome_source_refs
                else "partial"
            )
            decision_eligibility = (
                "market_outcome_evidence"
                if status == "ready"
                else "evidence_gap_only"
            )
        else:
            status = (
                "ready"
                if why
                and mechanism
                and transfer_actions
                and transfer_conditions
                and conflicts
                and prohibited
                and source_refs
                else "partial"
            )
            decision_eligibility = "mechanism_only"
        entry = {
            "case_id": case_id,
            "case_role": case_role,
            "case_kind": _safe_text(candidate.get("case_kind"))
            or ("archlib_visual" if is_archlib else "market_competitor"),
            "project_name": project_name,
            "case_family": role,
            "taxonomy_path": "/".join(taxonomy_parts[:3]),
            "locality": locality,
            "fit": fit,
            "fit_score": fit_score,
            "fit_score_kind": "retrieval_similarity",
            "why_selected": why,
            "mechanism": mechanism,
            "matched_project_features": _string_list(
                candidate.get("matched_project_features") or visual.get("design_keywords")
            ),
            "transfer_actions": transfer_actions[:4],
            "transfer_conditions": transfer_conditions[:4],
            "conflicts": conflicts[:4],
            "prohibited_analogies": prohibited,
            "expected_outcome": _safe_text(candidate.get("expected_outcome")),
            "outcome_polarity": outcome_polarity,
            "outcome_claim": outcome_claim,
            "outcome_period": outcome_period,
            "outcome_metrics": outcome_metrics,
            "outcome_source_refs": outcome_source_refs,
            "outcome_boundary_note": outcome_boundary_note,
            "owner": " / ".join(_string_list(link.get("owners"))) or "设计负责人",
            "decision_refs": _normalize_refs(link.get("decision_refs")),
            "source_refs": source_refs,
            "evidence_refs": _normalize_refs(candidate.get("evidence_refs")),
            "asset_refs": asset_refs,
            "confidence": case_confidence,
            "confidence_note": (
                "匹配分衡量检索与机制相似度；证据置信度另行衡量项目身份、来源、地域和交叉验证，二者不得互换。"
            ),
            "decision_eligibility": decision_eligibility,
            "status": status,
        }
        cases.append(entry)
        if is_archlib:
            archlib_cases.append(entry)
    archlib_cases.sort(
        key=lambda item: (
            item.get("locality") == "same_city",
            item.get("status") == "ready",
            item.get("fit_score", 0),
        ),
        reverse=True,
    )
    return cases, archlib_cases, asset_registry


def _compile_decision_chains(
    report: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    supplied = _sequence_of_mappings(report.get("decision_chains"))
    director = report.get("architecture_director")
    director = director if isinstance(director, Mapping) else {}
    roles = supplied or _sequence_of_mappings(director.get("role_council"))
    if not roles:
        return []
    case_lookup = {str(item.get("case_id")): item for item in cases}
    evidence_lookup = {
        str(item.get("evidence_id")): item
        for item in _sequence_of_mappings(director.get("evidence_nodes"))
        if item.get("evidence_id")
    }
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, role in enumerate(roles):
        role_id = _safe_text(role.get("role_id") or role.get("chain_id"), f"role-{index + 1}")
        chain_id = _safe_text(role.get("chain_id"), "chain:" + _slug(role_id))
        if chain_id in seen:
            continue
        seen.add(chain_id)
        target_unit_id, target_resolution, target_inference_basis = (
            _resolve_chain_target_unit(role)
        )
        refs = _sequence_of_mappings(role.get("case_refs"))
        all_case_refs = _normalize_refs(
            role.get("case_refs")
            if all(not isinstance(item, Mapping) for item in role.get("case_refs", []))
            else [
                item.get("evidence_id") or item.get("case_id")
                for item in refs
            ]
        )
        market_outcome_case_refs = [
            case_id
            for case_id in all_case_refs
            if str(case_lookup.get(case_id, {}).get("case_role")) == "market_outcome"
        ]
        design_intent_case_refs = [
            case_id
            for case_id in all_case_refs
            if str(case_lookup.get(case_id, {}).get("case_role")) == "design_intent"
        ]
        if target_unit_id == "SC2":
            case_refs = market_outcome_case_refs
        elif target_unit_id == "AD4":
            case_refs = design_intent_case_refs
        else:
            case_refs = list(all_case_refs)
        excluded_case_refs = [
            case_id for case_id in all_case_refs if case_id not in case_refs
        ]
        case_mechanisms: list[str] = []
        for case_id in case_refs:
            case = case_lookup.get(case_id, {})
            if str(case.get("case_role")) == "market_outcome":
                statement = _safe_text(case.get("outcome_claim"))
            else:
                statement = _safe_text(case.get("mechanism"))
            if statement and statement not in case_mechanisms:
                case_mechanisms.append(statement)
        relevant_gaps = [
            gap
            for gap in gaps
            if not gap.get("blocking_section")
            or str(gap.get("blocking_section")) in str(role_id)
            or str(gap.get("blocking_section")) in str(role.get("decision") or "")
        ]
        if not relevant_gaps:
            relevant_gaps = list(gaps)
        validation_gaps = _string_list(
            role.get("validation_gaps")
            or [gap.get("recommended_action") or gap.get("statement") for gap in relevant_gaps]
        )
        human_review = bool(role.get("human_review")) or any(
            "blocking" in str(gap.get("severity") or "") for gap in relevant_gaps
        )
        evidence_refs = _normalize_refs(
            role.get("evidence_refs") or role.get("observed_evidence_refs")
        )
        evidence_nodes = [
            evidence_lookup[ref]
            for ref in evidence_refs
            if ref in evidence_lookup
        ]
        observed_evidence = _string_list(role.get("observed_evidence"))
        if not observed_evidence:
            observed_evidence = [
                _safe_text(node.get("claim"))
                for node in evidence_nodes
                if _safe_text(node.get("claim_type")).lower()
                not in {"missing", "conflict", "counter_evidence"}
                and _safe_text(node.get("claim"))
            ]
        if not observed_evidence:
            observed_evidence = _string_list(role.get("why"))
        counter_evidence = _string_list(
            role.get("counter_evidence") or role.get("conflicts")
        )
        if not counter_evidence:
            for node in evidence_nodes:
                claim_type = _safe_text(node.get("claim_type")).lower()
                if claim_type in {"missing", "conflict", "counter_evidence"}:
                    counter_evidence.extend(_string_list(node.get("claim")))
                counter_evidence.extend(_string_list(node.get("conflicts")))
        if not counter_evidence:
            counter_evidence = [
                _safe_text(gap.get("statement") or gap.get("gap_id"))
                for gap in relevant_gaps
                if _safe_text(gap.get("statement") or gap.get("gap_id"))
            ]
        counter_evidence = list(dict.fromkeys(counter_evidence))
        source_refs = _normalize_refs(role.get("source_refs"))
        for node in evidence_nodes:
            source_refs.extend(_normalize_refs(node.get("source_refs")))
        if target_unit_id in {"SC2", "AD4"}:
            excluded_case_source_refs = {
                source_ref
                for case_id in excluded_case_refs
                for source_ref in _normalize_refs(
                    case_lookup.get(case_id, {}).get("source_refs")
                )
            }
            included_case_source_refs = {
                source_ref
                for case_id in case_refs
                for source_ref in _normalize_refs(
                    case_lookup.get(case_id, {}).get("outcome_source_refs")
                    or case_lookup.get(case_id, {}).get("source_refs")
                )
            }
            source_refs = [
                source_ref
                for source_ref in source_refs
                if source_ref not in excluded_case_source_refs
                or source_ref in included_case_source_refs
            ]
            source_refs.extend(included_case_source_refs)
        source_refs = list(dict.fromkeys(source_refs))
        confidence = _score_dict(role.get("confidence"))
        decision_gate = _safe_text(
            role.get("decision_gate"),
            _ROLE_DECISION_GATES.get(
                role_id,
                "由责任人关闭全部验证缺口并完成可追溯人审后，才可进入下一阶段。",
            ),
        )
        evidence_actionable = bool(confidence.get("evidence_actionable"))
        decision_eligible = bool(confidence.get("actionable"))
        chain_status = (
            "partial"
            if validation_gaps or human_review or not decision_eligible
            else "ready"
        )
        confidence["evidence_actionable"] = evidence_actionable
        confidence["actionable"] = chain_status == "ready"
        confidence["actionability_scope"] = (
            "decision_ready" if chain_status == "ready" else "next_evidence_action_only"
        )
        result.append(
            {
                "chain_id": chain_id,
                "target_unit_id": target_unit_id,
                "target_resolution": target_resolution,
                "target_inference_basis": target_inference_basis,
                "question": _safe_text(
                    role.get("question")
                    or role.get("decision_question")
                    or role.get("role_label"),
                    role_id,
                ),
                "observed_evidence": observed_evidence,
                "counter_evidence": counter_evidence,
                "evidence_refs": evidence_refs,
                "inference": _safe_text(
                    role.get("inference") or role.get("decision") or role.get("conclusion")
                ),
                "case_refs": case_refs,
                "all_case_refs": all_case_refs,
                "market_outcome_case_refs": market_outcome_case_refs,
                "design_intent_case_refs": design_intent_case_refs,
                "excluded_case_refs": excluded_case_refs,
                "case_mechanisms": case_mechanisms,
                "design_actions": _string_list(
                    role.get("design_actions")
                    or role.get("next_actions")
                    or role.get("design_response")
                ),
                "market_effect": _safe_text(
                    role.get("market_effect"), _ROLE_MARKET_EFFECTS.get(role_id, "")
                ),
                "financial_effect": _safe_text(
                    role.get("financial_effect"), _ROLE_FINANCIAL_EFFECTS.get(role_id, "")
                ),
                "decision_effect": _safe_text(
                    role.get("decision_effect"),
                    _ROLE_EFFECTS.get(role_id, "project_decision"),
                ),
                "validation_gaps": validation_gaps,
                "decision_gate": decision_gate,
                "owner": _safe_text(role.get("owner") or role.get("role_label"), role_id),
                "source_refs": source_refs,
                "confidence": confidence,
                "risk_coeff": _numeric(role.get("risk_coeff")),
                "human_review": human_review,
                "status": chain_status,
            }
        )
    return result


def _compile_action_register(
    report: Mapping[str, Any], chains: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for chain in chains:
        chain_id = _safe_text(chain.get("chain_id"))
        role_id = chain_id.removeprefix("chain:").replace("-", "_")
        for action in _string_list(chain.get("design_actions")):
            actions.append(
                {
                    "action_id": _stable_id("action", f"{chain.get('chain_id')}|{action}"),
                    "action": action,
                    "decision_refs": [str(chain.get("chain_id"))],
                    "case_refs": _normalize_refs(chain.get("case_refs")),
                    "evidence_refs": _normalize_refs(chain.get("evidence_refs")),
                    "owner": _safe_text(chain.get("owner"), "待指定"),
                    "stage": _safe_text(chain.get("decision_effect"), "project_decision"),
                    "trigger": _safe_text(chain.get("decision_gate"), "进入下一阶段前"),
                    "acceptance": _action_acceptance(action, role_id),
                    "fallback": _ROLE_ACTION_FALLBACK.get(
                        role_id,
                        "未满足验收条件时保持 partial，不得自动升级。",
                    ),
                    "source_refs": _normalize_refs(chain.get("source_refs")),
                    "status": "open",
                }
            )
    explicit = []
    for value in (
        report.get("action_register"),
        report.get("decision_hooks"),
        _path_get(report, "architecture_director.decision_hooks"),
    ):
        explicit.extend(_sequence_of_mappings(value))
    for index, item in enumerate(explicit):
        action = _safe_text(item.get("action") or item.get("task"))
        if not action:
            continue
        owner = _safe_text(item.get("owner") or item.get("owner_agent"), "待指定")
        actions.append(
            {
                "action_id": _safe_text(
                    item.get("action_id"), _stable_id("action", f"hook|{index}|{action}")
                ),
                "gap_ref": _safe_text(item.get("gap_ref")),
                "action": action,
                "decision_refs": _normalize_refs(item.get("decision_refs")),
                "case_refs": _normalize_refs(item.get("case_refs")),
                "evidence_refs": _normalize_refs(
                    item.get("evidence_refs") or item.get("gap_ref")
                ),
                "owner": owner,
                "stage": _safe_text(item.get("stage"), "decision_hook"),
                "trigger": _safe_text(item.get("trigger"), "条件触发"),
                "acceptance": _safe_text(
                    item.get("acceptance") or item.get("target"),
                    "动作完成并留存复核证据。",
                ),
                "fallback": _safe_text(
                    item.get("fallback"), "未满足时暂停对应决策。"
                ),
                "source_refs": _normalize_refs(item.get("source_refs")),
                "status": _safe_text(item.get("status"), "open"),
            }
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in actions:
        marker = str(item.get("action_id") or item.get("action"))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _compile_audit_list(report: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    values = _sequence_of_mappings(report.get(key))
    director = report.get("architecture_director")
    director = director if isinstance(director, Mapping) else {}
    if key == "assumptions":
        brief = director.get("brief") if isinstance(director.get("brief"), Mapping) else {}
        for index, item in enumerate(_string_list(brief.get("assumptions"))):
            values.append({"assumption_id": f"director:{index + 1}", "statement": item})
    result: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        safe = _safe_copy(item)
        if key == "assumptions":
            safe.setdefault("assumption_id", f"assumption:{index + 1}")
        else:
            safe.setdefault("question_id", f"question:{index + 1}")
        result.append(safe)
    return result


_GAP_FIELD_LABELS = {
    "coordinates": "可核验项目坐标",
    "site_boundary": "结构化地块红线",
    "true_north": "真北基准",
    "true_north_deg": "真北基准",
    "roads": "周边道路关系",
    "water": "周边水体关系",
    "entrance": "场地主入口",
    "dem": "数字高程模型（DEM）",
    "dsm": "数字表面模型（DSM）",
    "building_height": "周边建筑高度",
    "noise": "噪声实测",
    "flood": "洪涝风险资料",
    "masterplan": "经核准总图",
    "facing_azimuth": "建筑坐向",
}


def _gap_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in (
            "statement",
            "question",
            "message",
            "description",
            "required_action",
            "recommended_action",
        ):
            text = _safe_text(value.get(key))
            if text:
                return text
        field = _safe_text(value.get("field"))
        if field:
            label = _GAP_FIELD_LABELS.get(field, field.replace("_", " "))
            parts = [f"缺少{label}"]
            required_for = _safe_text(value.get("required_for"))
            if required_for:
                parts.append(f"进入 {required_for} 前必须补齐")
            if value.get("blocking") is True:
                parts.append("当前为阻断项")
            return "；".join(parts) + "。"
    text = _safe_text(value)
    return text or "该模块资料缺失。"


def _module_takeaway(module_id: str, module: Mapping[str, Any]) -> str:
    """Return a concrete, evidence-bounded headline instead of a generic hint."""
    summary = _summary_text(module)
    if summary:
        return summary
    if module_id in {"market", "competitor_series"}:
        items = module.get("items") or module.get("competitors") or []
        usable = [item for item in items if isinstance(item, Mapping)]
        prices = [
            value
            for item in usable
            if (value := _numeric(item.get("unit_price_cny"))) and value > 100
        ]
        series_status = str(module.get("series_status") or "")
        if prices:
            mean = round(sum(prices) / len(prices))
            suffix = (
                "；现有资料仅为价格截面，不能替代供应、促销、库存与去化时序。"
                if series_status == "snapshot_only"
                else "；需与来源和统计期同时阅读。"
            )
            return f"已登记 {len(usable)} 个竞品，样本均价约 {mean:,} 元/㎡{suffix}"
        if usable:
            return f"已登记 {len(usable)} 个竞品，但缺少可用于价格比较的完整字段。"
    if module_id == "macro":
        assessment = module.get("cycle_assessment")
        if isinstance(assessment, Mapping):
            for key in ("summary", "statement", "takeaway"):
                value = assessment.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        indicators = module.get("indicators") or []
        if isinstance(indicators, Sequence) and not isinstance(indicators, str):
            return f"当前宏观模块已登记 {len(indicators)} 项指标；结论受统计期、来源覆盖与交叉验证约束。"
    gaps = module.get("evidence_gaps") or []
    if isinstance(gaps, Sequence) and not isinstance(gaps, str) and gaps:
        return f"当前判断存在资料边界：{_gap_text(gaps[0])}"
    return "模块已有数据，但尚未形成可独立进入决策的完整证据链。"


def _module_title(module_id: str, module: Mapping[str, Any], fallback: str) -> str:
    if module_id == "macro" and str(module.get("status") or "") == "partial":
        return "宏观结构性分化与资料缺口"
    if module_id == "competitor_series" and str(module.get("series_status") or "") == "snapshot_only":
        return "竞品价格锚点与时序缺口"
    return fallback


def _module_chart_specs(module_id: str, module: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compile small, evidence-backed charts for the presentation layer."""
    if module_id in {"market", "competitor_series"}:
        items = module.get("items") or module.get("competitors") or []
        candidates = [item for item in items if isinstance(item, Mapping)]
        rows = []
        for item in candidates:
            value = _numeric(item.get("unit_price_cny"))
            if value is None or value <= 100:
                continue
            rows.append(
                {
                    "label": str(item.get("project_name") or item.get("name") or "未命名竞品"),
                    "value": round(value),
                    "note": (
                        f"{_numeric(item.get('distance_km')):.2f} km"
                        if _numeric(item.get("distance_km")) is not None
                        else "距离未登记"
                    ),
                }
            )
        rows.sort(key=lambda row: row["value"], reverse=True)
        if rows:
            return [
                {
                    "chart_id": f"{module_id}-price-ladder",
                    "type": "ranked_bar",
                    "title": "价格锚点（前 8 个）",
                    "unit": "元/㎡",
                    "series": rows[:8],
                    "source_note": "完整竞品清单置于证据附录。",
                }
            ]
    if module_id == "macro":
        indicators = module.get("indicators") or []
        series = []
        for item in indicators:
            if not isinstance(item, Mapping):
                continue
            growth = _numeric(item.get("growth_pct"))
            if growth is None:
                continue
            series.append(
                {
                    "label": str(item.get("indicator_name") or item.get("indicator_id") or "指标"),
                    "value": round(growth, 2),
                    "note": str(item.get("period") or "统计期未登记"),
                }
            )
        series.sort(key=lambda row: abs(row["value"]), reverse=True)
        if series:
            return [
                {
                    "chart_id": "macro-growth-signals",
                    "type": "diverging_bar",
                    "title": "宏观支持与压力信号",
                    "unit": "%",
                    "series": series[:8],
                    "source_note": "同比指标不等同于投资结论。",
                }
            ]
    return []


def _module_blocks(module_id: str, module: Mapping[str, Any]) -> list[dict[str, Any]]:
    if _is_missing_module(module):
        gaps = module.get("evidence_gaps") or []
        text = gaps[0] if isinstance(gaps, Sequence) and gaps else "该模块资料缺失。"
        return [
            {
                "type": "gap",
                "status": "missing",
                "text": _gap_text(text),
                "required_action": "补齐来源、统计期、地域口径和采集方法后重新编译。",
            }
        ]

    blocks: list[dict[str, Any]] = []
    blocks.append({"type": "narrative", "text": _module_takeaway(module_id, module)})
    metrics = _scalar_metrics(module)
    if metrics:
        blocks.append({"type": "metrics", "metrics": metrics})
    return blocks


def _module_appendix_pages(module_id: str, module: Mapping[str, Any], spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep raw evidence tables available without letting them consume the deck."""
    pages: list[dict[str, Any]] = []
    for key in _TABLE_KEYS[module_id]:
        items = module.get(key)
        if not _has_data(items):
            continue
        table = _table_from_items(key, items)
        if not table:
            continue
        pages.append(
            {
                "page_id": f"{module_id}-{key}-evidence",
                "chapter_id": module_id,
                "layout": "appendix_table",
                "title": f"{spec['title']} · {TABLE_TITLES.get(key, key)}证据清单",
                "takeaway": "完整清单仅用于来源核查与附录阅读，不替代主报告判断。",
                "blocks": [table],
                "chart_specs": [],
                "asset_refs": [],
                "source_refs": _source_refs(items) or _source_refs(module),
                "confidence": _module_confidence(module),
                "evidence_type": spec["evidence_type"],
                "load_priority": "deferred",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "evidence_appendix",
                "appendix_policy": "evidence",
                "visual_evidence": "table",
                "unit_status": _safe_text(module.get("status"), "ready"),
            }
        )
    return pages


def _module_pages(modules: Mapping[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    appendices: list[dict[str, Any]] = []
    for index, spec in enumerate(MODULE_SPECS):
        module = modules[spec["id"]]
        missing = _is_missing_module(module)
        pages.append(
            {
                "page_id": f"{spec['id']}-overview",
                "chapter_id": spec["id"],
                "layout": "gap" if missing else "module_summary",
                "title": _module_title(spec["id"], module, spec["title"]),
                "takeaway": spec["gap"] if missing else _module_takeaway(spec["id"], module),
                "blocks": _module_blocks(spec["id"], module),
                "chart_specs": [] if missing else _module_chart_specs(spec["id"], module),
                "asset_refs": [],
                "source_refs": _source_refs(module),
                "confidence": _module_confidence(module),
                "evidence_type": spec["evidence_type"],
                "load_priority": "high" if index < 2 else "normal",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "primary_narrative",
                "appendix_policy": "presentation",
                "visual_evidence": "chart" if _module_chart_specs(spec["id"], module) else "text",
                "unit_status": "missing" if missing else _safe_text(module.get("status"), "ready"),
            }
        )
        if not missing and not (
            spec["id"] == "competitor_series"
            and _has_data(modules.get("market", {}).get("competitors"))
        ):
            appendices.extend(_module_appendix_pages(spec["id"], module, spec))
    return pages + appendices


def _framework_unit_ready(unit_id: str, module: Mapping[str, Any]) -> bool:
    if str(module.get("status") or "").lower() != "ready":
        return False
    if unit_id == "concept_options":
        return len(_sequence_of_mappings(module.get("options") or module.get("schemes"))) >= 3
    if unit_id == "recommended_scheme":
        rejected = _string_list(
            module.get("rejected_options") or module.get("eliminated_options")
        )
        return bool(
            module.get("recommendation") or module.get("recommended_scheme")
        ) and len(rejected) >= 2 and bool(module.get("decision_gate")) and bool(
            module.get("fallback") or module.get("fallback_scheme")
        )
    if unit_id == "architecture_design":
        required = bool(module.get("masterplan")) and bool(
            module.get("unit_plan") or module.get("unit_strategy")
        )
        expression = sum(
            bool(module.get(key))
            for key in ("facade", "landscape", "clubhouse", "demo_zone")
        )
        return required and expression >= 2
    if unit_id == "design_value_premium":
        upstream = set(_normalize_refs(module.get("upstream_refs")))
        return {"SC2", "AD2", "AD3", "AD4"}.issubset(upstream) and bool(
            module.get("design_actions")
        ) and bool(module.get("value_mechanisms")) and bool(
            module.get("investment_tiers") or module.get("incremental_costs")
        )
    if unit_id == "confidence_state":
        return bool(module.get("source_refs") or module.get("source_registry")) and bool(
            module.get("methods") or module.get("method_registry")
        ) and bool(module.get("assumptions")) and bool(
            module.get("confidence") or module.get("claim_states")
        )
    return False


def _framework_unit_contract_snapshot(
    unit_id: str, module: Mapping[str, Any]
) -> dict[str, Any]:
    """Expose the minimal fields needed to independently re-check readiness."""
    if unit_id == "concept_options":
        return {
            "options_count": len(
                _sequence_of_mappings(module.get("options") or module.get("schemes"))
            )
        }
    if unit_id == "recommended_scheme":
        return {
            "recommendation": bool(
                module.get("recommendation") or module.get("recommended_scheme")
            ),
            "rejected_options_count": len(
                _string_list(
                    module.get("rejected_options") or module.get("eliminated_options")
                )
            ),
            "decision_gate": bool(module.get("decision_gate")),
            "fallback": bool(module.get("fallback") or module.get("fallback_scheme")),
        }
    if unit_id == "architecture_design":
        return {
            "masterplan": bool(module.get("masterplan")),
            "unit_plan": bool(module.get("unit_plan") or module.get("unit_strategy")),
            "expression_count": sum(
                bool(module.get(key))
                for key in ("facade", "landscape", "clubhouse", "demo_zone")
            ),
        }
    if unit_id == "design_value_premium":
        return {
            "upstream_refs": _normalize_refs(module.get("upstream_refs")),
            "design_actions": bool(module.get("design_actions")),
            "value_mechanisms": bool(module.get("value_mechanisms")),
            "investment": bool(
                module.get("investment_tiers") or module.get("incremental_costs")
            ),
        }
    if unit_id == "confidence_state":
        return {
            "sources": bool(module.get("source_refs") or module.get("source_registry")),
            "methods": bool(module.get("methods") or module.get("method_registry")),
            "assumptions": bool(module.get("assumptions")),
            "confidence": bool(module.get("confidence") or module.get("claim_states")),
        }
    return {}


def _framework_input_pages(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compile first-class architecture/value inputs when a project supplies them."""
    pages: list[dict[str, Any]] = []
    for spec in UNIT_INPUT_SPECS:
        value = report.get(spec["id"])
        if not isinstance(value, Mapping):
            continue
        module = _safe_copy(value)
        status = str(module.get("status") or "partial").lower()
        ready = _framework_unit_ready(spec["id"], module)
        blocks = _block_candidates(module.get("blocks"))
        options = module.get("options") or module.get("schemes")
        if not blocks and isinstance(options, Sequence) and not isinstance(
            options, (str, bytes, bytearray)
        ):
            table = _table_from_items("方案1／2／3", options)
            if table:
                blocks.append(table)
        if not blocks:
            blocks.append(
                {
                    "type": "narrative",
                    "text": _module_takeaway(spec["id"], module),
                }
            )
            metrics = _scalar_metrics(module)
            if metrics:
                blocks.append({"type": "metrics", "metrics": metrics})
        if not ready:
            blocks.append(
                {
                    "type": "gap",
                    "status": "blocked" if status == "blocked" else "partial",
                    "text": f"{spec['title']}输入尚未满足完整性契约。",
                    "required_action": "补齐该单元的必需字段、来源、反证、责任人和人审状态。",
                }
            )
        pages.append(
            {
                "page_id": f"{spec['id']}-overview",
                "chapter_id": spec["id"],
                "section_id": spec["section_id"],
                "unit_role": spec["id"],
                "unit_contract": _framework_unit_contract_snapshot(spec["id"], module),
                "unit_status": "ready" if ready else ("blocked" if status == "blocked" else "partial"),
                "unit_contract_validated": ready,
                "layout": _safe_text(module.get("layout"), "module_summary"),
                "title": _safe_text(module.get("title"), spec["title"]),
                "takeaway": _safe_text(
                    module.get("takeaway") or module.get("summary"),
                    _module_takeaway(spec["id"], module),
                ),
                "blocks": blocks,
                "chart_specs": _safe_copy(module.get("chart_specs") or []),
                "asset_refs": _normalize_refs(module.get("asset_refs")),
                "source_refs": _source_refs(module),
                "confidence": _module_confidence(module),
                "evidence_type": spec["evidence_type"],
                "load_priority": "high" if spec["id"] == "recommended_scheme" else "normal",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "primary_narrative",
                "appendix_policy": "presentation",
                "visual_evidence": _safe_text(module.get("visual_evidence"), "text"),
            }
        )
    return pages


def _knowledge_pages(
    chains: Sequence[Mapping[str, Any]],
    market_outcome_cases: Sequence[Mapping[str, Any]],
    design_intent_cases: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    assumptions: Sequence[Mapping[str, Any]],
    open_questions: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    chain_pages: list[dict[str, Any]] = []
    for index, chain in enumerate(chains):
        chain_pages.append(
            {
                "page_id": f"decision-chain-{index + 1}-{_slug(chain.get('chain_id'))}",
                "chapter_id": "decision_chain",
                "section_id": _safe_text(chain.get("target_unit_id"), "SC1"),
                "unit_role": "decision_chain_support",
                "unit_status": "supporting",
                "layout": "reasoning_chain",
                "title": _safe_text(chain.get("question"), "决策推理闭环"),
                "takeaway": _safe_text(
                    chain.get("inference"),
                    "本页必须同时呈现事实、反证、案例机制、项目动作和决策闸门。",
                ),
                "blocks": [
                    {
                        "type": "reasoning_chain",
                        "target_unit_id": _safe_text(chain.get("target_unit_id"), "SC1"),
                        "target_resolution": _safe_text(chain.get("target_resolution")),
                        "target_inference_basis": _safe_text(
                            chain.get("target_inference_basis")
                        ),
                        "question": _safe_text(chain.get("question")),
                        "observed_evidence": _safe_copy(chain.get("observed_evidence") or []),
                        "counter_evidence": _safe_copy(chain.get("counter_evidence") or []),
                        "inference": _safe_text(chain.get("inference")),
                        "case_mechanisms": _safe_copy(chain.get("case_mechanisms") or []),
                        "design_actions": _safe_copy(chain.get("design_actions") or []),
                        "market_effect": _safe_text(chain.get("market_effect")),
                        "financial_effect": _safe_text(chain.get("financial_effect")),
                        "decision_effect": _safe_text(chain.get("decision_effect")),
                        "decision_gate": _safe_text(chain.get("decision_gate")),
                        "validation_gaps": _safe_copy(chain.get("validation_gaps") or []),
                        "owner": _safe_text(chain.get("owner")),
                        "case_refs": _safe_copy(chain.get("case_refs") or []),
                        "all_case_refs": _safe_copy(chain.get("all_case_refs") or []),
                        "market_outcome_case_refs": _safe_copy(
                            chain.get("market_outcome_case_refs") or []
                        ),
                        "design_intent_case_refs": _safe_copy(
                            chain.get("design_intent_case_refs") or []
                        ),
                        "excluded_case_refs": _safe_copy(
                            chain.get("excluded_case_refs") or []
                        ),
                        "evidence_refs": _safe_copy(chain.get("evidence_refs") or []),
                    }
                ],
                "chart_specs": [],
                "asset_refs": [],
                "source_refs": _normalize_refs(chain.get("source_refs")),
                "confidence": _score_dict(chain.get("confidence")),
                "evidence_type": "analysis_inference",
                "load_priority": "high" if index < 2 else "normal",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "primary_narrative",
                "appendix_policy": "presentation",
                "visual_evidence": "reasoning_chain",
            }
        )

    case_pages: list[dict[str, Any]] = []
    for index, case in enumerate(market_outcome_cases):
        complete = str(case.get("status") or "") == "ready"
        blocks: list[dict[str, Any]] = [
            {
                "type": "market_outcome_case",
                "case_id": _safe_text(case.get("case_id")),
                "case_role": "market_outcome",
                "project": _safe_text(case.get("project_name")),
                "locality": _safe_text(case.get("locality")),
                "why_selected": _safe_text(case.get("why_selected")),
                "outcome_polarity": _safe_text(case.get("outcome_polarity"), "unknown"),
                "outcome_claim": _safe_text(case.get("outcome_claim")),
                "outcome_period": _safe_text(case.get("outcome_period")),
                "outcome_metrics": _safe_copy(case.get("outcome_metrics") or []),
                "outcome_source_refs": _normalize_refs(
                    case.get("outcome_source_refs")
                ),
                "outcome_boundary_note": _safe_text(
                    case.get("outcome_boundary_note")
                ),
                "decision_eligibility": _safe_text(case.get("decision_eligibility")),
            }
        ]
        if not complete:
            blocks.append(
                {
                    "type": "gap",
                    "status": "partial",
                    "text": "该市场案例缺少极性、统计期、结果指标或结果来源，不能进入市场结论。",
                    "required_action": "补齐真实发生的去化、流速、价格或折扣结果及其统计期和来源。",
                }
            )
        case_pages.append(
            {
                "page_id": f"market-case-{index + 1}-{_slug(case.get('case_id'))}",
                "chapter_id": "market_cases",
                "section_id": "SC2",
                "unit_role": "market_outcome_case",
                "unit_status": "supporting",
                "layout": "market_outcome_case",
                "title": _safe_text(case.get("project_name"), "市场结果案例"),
                "takeaway": _safe_text(
                    case.get("outcome_claim"),
                    "结果数据不足，本案例仅登记为待补证市场样本。",
                ),
                "blocks": blocks,
                "chart_specs": [],
                "asset_refs": _normalize_refs(case.get("asset_refs")),
                "source_refs": _normalize_refs(
                    case.get("outcome_source_refs") or case.get("source_refs")
                ),
                "confidence": _score_dict(case.get("confidence")),
                "evidence_type": "observed_fact",
                "load_priority": "normal" if complete else "deferred",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "primary_narrative",
                "appendix_policy": "presentation",
                "visual_evidence": "market_outcome",
            }
        )

    for index, case in enumerate(design_intent_cases):
        complete = str(case.get("status") or "") == "ready"
        transfer_actions = _string_list(case.get("transfer_actions"))
        blocks = [
            {
                "type": "case_mechanism",
                "case_id": _safe_text(case.get("case_id")),
                "case_role": "design_intent",
                "project": _safe_text(case.get("project_name")),
                "case_family": _safe_text(case.get("case_family")),
                "locality": _safe_text(case.get("locality")),
                "fit": _safe_text(case.get("fit")),
                "fit_score": _safe_scalar(case.get("fit_score"), 0),
                "fit_score_kind": _safe_text(case.get("fit_score_kind")),
                "confidence_score": _safe_scalar(
                    _score_dict(case.get("confidence")).get("score"), 0
                ),
                "confidence_note": _safe_text(case.get("confidence_note")),
                "why_selected": _safe_text(case.get("why_selected")),
                "mechanism": _safe_text(case.get("mechanism")),
                "matched_project_features": _safe_copy(
                    case.get("matched_project_features") or []
                ),
                "transfer_actions": _safe_copy(transfer_actions),
                "transfer_conditions": _safe_copy(case.get("transfer_conditions") or []),
                "conflicts": _safe_copy(case.get("conflicts") or []),
                "prohibited_analogies": _safe_copy(case.get("prohibited_analogies") or []),
                "expected_outcome": _safe_text(case.get("expected_outcome")),
                "outcome_boundary_note": _safe_text(case.get("outcome_boundary_note")),
                "owner": _safe_text(case.get("owner")),
                "decision_eligibility": _safe_text(case.get("decision_eligibility")),
                "asset_ref": (
                    str(case.get("asset_refs", [""])[0])
                    if isinstance(case.get("asset_refs"), Sequence)
                    and case.get("asset_refs")
                    else ""
                ),
            }
        ]
        if not complete:
            blocks.append(
                {
                    "type": "gap",
                    "status": "partial",
                    "text": "该设计意向案例尚未形成完整的机制、迁移条件、冲突和来源边界。",
                    "required_action": "补齐案例身份、空间机制、迁移动作、非迁移边界和来源后再用于方案转译。",
                }
            )
        case_pages.append(
            {
                "page_id": f"design-case-{index + 1}-{_slug(case.get('case_id'))}",
                "chapter_id": (
                    "archlib_cases"
                    if str(case.get("case_kind")) == "archlib_visual"
                    else "design_cases"
                ),
                "section_id": "AD4",
                "unit_role": "design_intent_case",
                "unit_status": "supporting",
                "layout": "case_mechanism",
                "title": _safe_text(case.get("project_name"), "设计意向案例"),
                "takeaway": transfer_actions[0]
                if transfer_actions
                else "案例只提供机制校准，不直接证明本项目市场结果。",
                "blocks": blocks,
                "chart_specs": [],
                "asset_refs": _normalize_refs(case.get("asset_refs")),
                "source_refs": _normalize_refs(case.get("source_refs")),
                "confidence": _score_dict(case.get("confidence")),
                "evidence_type": "analysis_inference",
                "load_priority": "normal" if complete else "deferred",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "primary_narrative",
                "appendix_policy": "presentation",
                "visual_evidence": "case_mechanism",
            }
        )

    action_pages: list[dict[str, Any]] = []
    for offset in range(0, len(actions), 3):
        chunk = list(actions[offset : offset + 3])
        if not chunk:
            continue
        action_pages.append(
            {
                "page_id": f"action-register-{offset // 3 + 1}",
                "chapter_id": "action_register",
                "layout": "decision_actions",
                "title": "把结论变成责任、触发条件与验收闸门",
                "takeaway": "未绑定责任人、触发条件、验收标准和失败回退的建议，不视为完成闭环。",
                "blocks": [{"type": "decision_actions", "actions": _safe_copy(chunk)}],
                "chart_specs": [],
                "asset_refs": [],
                "source_refs": _source_refs(chunk),
                "confidence": _score_dict(0.62),
                "evidence_type": "analysis_inference",
                "load_priority": "normal",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "primary_narrative",
                "appendix_policy": "presentation",
                "visual_evidence": "action_register",
            }
        )

    audit_appendix: list[dict[str, Any]] = []
    for key, title, items in (
        ("assumptions", "假设登记", assumptions),
        ("open_questions", "开放问题与补证责任", open_questions),
        ("evidence_gaps", "证据缺口与阻断", gaps),
    ):
        table = _table_from_items(title, items)
        if not table:
            continue
        audit_appendix.append(
            {
                "page_id": f"knowledge-audit-{key}",
                "chapter_id": "knowledge_audit",
                "layout": "appendix_table",
                "title": title,
                "takeaway": "该清单用于复算、追责和下一轮补证，不替代主报告判断。",
                "blocks": [table],
                "chart_specs": [],
                "asset_refs": [],
                "source_refs": _source_refs(items),
                "confidence": _score_dict(0.55),
                "evidence_type": "analysis_inference",
                "load_priority": "deferred",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
                "story_role": "evidence_appendix",
                "appendix_policy": "evidence",
                "visual_evidence": "table",
            }
        )
    return {
        "chains": chain_pages,
        "cases": case_pages,
        "actions": action_pages,
        "appendix": audit_appendix,
    }


def _compose_report_pages(
    module_pages: Sequence[Mapping[str, Any]],
    knowledge_pages: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    primary = [
        deepcopy(dict(page))
        for page in module_pages
        if page.get("story_role") != "evidence_appendix"
    ]
    appendix = [
        deepcopy(dict(page))
        for page in module_pages
        if page.get("story_role") == "evidence_appendix"
    ]
    output: list[dict[str, Any]] = []
    cases_inserted = False
    actions_inserted = False
    for page in primary:
        output.append(page)
        chapter = str(page.get("chapter_id") or "")
        if chapter == "decision":
            output.extend(deepcopy(list(knowledge_pages.get("chains") or [])))
        if chapter == "product":
            output.extend(deepcopy(list(knowledge_pages.get("cases") or [])))
            cases_inserted = True
        if chapter == "finance":
            output.extend(deepcopy(list(knowledge_pages.get("actions") or [])))
            actions_inserted = True
    if not cases_inserted:
        output.extend(deepcopy(list(knowledge_pages.get("cases") or [])))
    if not actions_inserted:
        output.extend(deepcopy(list(knowledge_pages.get("actions") or [])))
    output.extend(appendix)
    output.extend(deepcopy(list(knowledge_pages.get("appendix") or [])))
    return output


def _compact_marker(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").lower())


def _is_raw_page(page: Mapping[str, Any]) -> bool:
    for key in ("page_id", "chapter_id", "layout", "title"):
        marker = _compact_marker(page.get(key))
        if marker in _RAW_MARKERS or ("raw" in marker and "json" in marker):
            return True
    blocks = page.get("blocks") or []
    if isinstance(blocks, Sequence) and not isinstance(blocks, str):
        meaningful = [block for block in blocks if isinstance(block, Mapping)]
        if meaningful and all(_is_raw_block(block) for block in meaningful):
            return True
    return False


def _is_raw_block(block: Mapping[str, Any]) -> bool:
    marker = _compact_marker(block.get("type") or block.get("layout"))
    return (
        marker in _RAW_MARKERS
        or marker in {"raw", "payload", "rawpayload"}
        or ("raw" in marker and "json" in marker)
    )


def _normalize_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = [str(item) for item in value if isinstance(item, (str, int))]
    else:
        values = []
    return list(dict.fromkeys(item for item in values if item))


def _normalize_block(block: Mapping[str, Any]) -> dict[str, Any] | None:
    if _is_raw_block(block):
        return None
    block_type = _safe_text(block.get("type"), "content").strip().lower() or "content"
    if block_type == "table":
        rows = block.get("rows") or []
        if isinstance(rows, Mapping):
            rows = [dict(rows)]
        elif isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
            rows = list(rows)
        elif rows is None:
            rows = []
        else:
            rows = [rows]
        columns = block.get("columns") or block.get("headers") or []
        if isinstance(columns, str):
            columns = [columns]
        elif isinstance(columns, Mapping):
            columns = list(columns.keys())
        elif isinstance(columns, Sequence) and not isinstance(columns, (bytes, bytearray)):
            columns = list(columns)
        else:
            columns = []
        return {
            "type": "table",
            "title": _safe_text(block.get("title")),
            "columns": _safe_copy(columns),
            "rows": _safe_copy(rows),
            "source_refs": _normalize_refs(block.get("source_refs")),
        }
    allowed_keys = {
        "type",
        "title",
        "text",
        "body",
        "status",
        "required_action",
        "metrics",
        "items",
        "callout",
        "callouts",
        "diagram_kind",
        "layers",
        "geometry",
        "orientation",
        "source_refs",
        "confidence",
        "evidence_type",
        "src",
        "url",
        "asset",
        "alt",
        "question",
        "target_unit_id",
        "target_resolution",
        "target_inference_basis",
        "observed_evidence",
        "counter_evidence",
        "evidence_refs",
        "inference",
        "case_refs",
        "all_case_refs",
        "market_outcome_case_refs",
        "design_intent_case_refs",
        "excluded_case_refs",
        "case_mechanisms",
        "design_actions",
        "market_effect",
        "financial_effect",
        "decision_effect",
        "decision_gate",
        "validation_gaps",
        "owner",
        "case_id",
        "case_role",
        "project",
        "case_family",
        "locality",
        "fit",
        "fit_score",
        "fit_score_kind",
        "confidence_score",
        "confidence_note",
        "why_selected",
        "mechanism",
        "matched_project_features",
        "transfer_actions",
        "transfer_conditions",
        "conflicts",
        "prohibited_analogies",
        "expected_outcome",
        "outcome_polarity",
        "outcome_claim",
        "outcome_period",
        "outcome_metrics",
        "outcome_source_refs",
        "outcome_boundary_note",
        "decision_eligibility",
        "asset_ref",
        "actions",
    }
    normalized = {
        str(key): _safe_copy(value)
        for key, value in block.items()
        if key in allowed_keys and not _SENSITIVE_KEY_RE.search(str(key))
    }
    normalized["type"] = block_type
    return normalized


def _normalize_chart_specs(
    value: Any,
    *,
    visual_language: str = "decision_narrative",
    page_id: str = "page",
    page_source_refs: Any = None,
    page_confidence: Any = None,
) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        candidates = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        candidates = list(value)
    else:
        candidates = []
    return [
        normalize_chart_spec(
            _safe_copy(candidate),
            grammar=visual_language,
            page_id=page_id,
            chart_index=index,
            page_source_refs=page_source_refs,
            page_confidence=page_confidence,
        )
        for index, candidate in enumerate(candidates)
        if isinstance(candidate, Mapping)
    ]


def _normalize_diagram_specs(
    value: Any,
    *,
    page_id: str = "page",
) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        candidates = [value]
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        candidates = list(value)
    else:
        candidates = []
    return [
        normalize_diagram_spec(
            _safe_copy(candidate),
            page_id=page_id,
            diagram_index=index,
        )
        for index, candidate in enumerate(candidates)
        if isinstance(candidate, Mapping)
    ]


def _block_candidates(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, str):
        return [{"type": "narrative", "text": value}]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return list(value)
    return []


def _normalize_confidence(
    value: Any,
    layout: str,
    *,
    evidence_type: Any = "",
    decision_eligibility: Any = "",
) -> dict[str, Any]:
    return _score_dict(
        value,
        missing=layout == "gap",
        evidence_type=evidence_type,
        decision_eligibility=decision_eligibility,
    )


def _normalize_page(page: Mapping[str, Any], fallback_index: int) -> dict[str, Any] | None:
    if _is_raw_page(page):
        return None
    layout = _safe_text(page.get("layout"), "content").strip().lower() or "content"
    if _compact_marker(layout) in _RAW_MARKERS:
        return None
    blocks_source = _block_candidates(page.get("blocks"))
    if isinstance(page.get("table"), Mapping):
        blocks_source.append({"type": "table", **page["table"]})
    elif page.get("rows") is not None:
        blocks_source.append(
            {
                "type": "table",
                "columns": page.get("columns") or page.get("headers") or [],
                "rows": page.get("rows") or [],
            }
        )
    blocks = []
    for block in blocks_source:
        if isinstance(block, Mapping):
            normalized = _normalize_block(block)
            if normalized is not None:
                blocks.append(normalized)
        elif isinstance(block, str) and block.strip():
            blocks.append({"type": "narrative", "text": block.strip()})
    if not blocks:
        blocks = [{"type": "gap", "status": "missing", "text": "页面没有可编译内容。"}]
        layout = "gap"
    chapter_id = _safe_text(page.get("chapter_id"), "supplement").strip() or "supplement"
    evidence_type = normalize_evidence_type(page.get("evidence_type"))
    if evidence_type not in EVIDENCE_TYPES:
        evidence_type = "analysis_inference"
    print_policy = {
        "include": True,
        "page_break_after": True,
        "allow_internal_scroll": False,
    }
    if isinstance(page.get("print_policy"), Mapping):
        print_policy.update(_safe_copy(page["print_policy"]))
        print_policy["allow_internal_scroll"] = False

    page_id = _safe_text(page.get("page_id"), f"{chapter_id}-{fallback_index + 1}")
    source_refs = _normalize_refs(page.get("source_refs"))
    full_title = _safe_text(page.get("title"), f"{chapter_id} {fallback_index + 1}")
    confidence = _normalize_confidence(
        page.get("confidence"),
        layout,
        evidence_type=evidence_type,
        decision_eligibility=page.get("decision_eligibility"),
    )
    routing_page = {
        **page,
        "page_id": page_id,
        "chapter_id": chapter_id,
        "layout": layout,
        "blocks": blocks,
        "story_role": _safe_text(page.get("story_role"), "primary_narrative"),
        "appendix_policy": _safe_text(page.get("appendix_policy"), "presentation"),
        "visual_evidence": _safe_text(page.get("visual_evidence"), "text"),
    }
    visual_language = resolve_page_visual_language(routing_page)
    structure = apply_section_metadata(routing_page)
    chart_specs = _normalize_chart_specs(
        page.get("chart_specs"),
        visual_language=visual_language,
        page_id=page_id,
        page_source_refs=source_refs,
        page_confidence=confidence,
    )
    diagram_specs = _normalize_diagram_specs(
        page.get("diagram_specs"),
        page_id=page_id,
    )
    source_refs = list(
        dict.fromkeys(
            source_refs
            + [
                reference
                for block in blocks
                for reference in _normalize_refs(block.get("source_refs"))
            ]
            + [
                reference
                for chart in chart_specs
                for reference in _normalize_refs(chart.get("source_refs"))
            ]
            + [
                reference
                for diagram in diagram_specs
                for reference in _normalize_refs(diagram.get("source_refs"))
            ]
        )
    )

    return {
        "page_id": page_id,
        "chapter_id": chapter_id,
        "source_chapter_id": chapter_id,
        "framework_version": structure["framework_version"],
        "unit_id": structure["unit_id"],
        "group_id": structure["group_id"],
        "section_id": structure["section_id"],
        "section_group": structure["section_group"],
        "section_group_title": structure["section_group_title"],
        "section_group_label": structure["section_group_label"],
        "section_title": structure["section_title"],
        "section_classification": structure["section_classification"],
        "unit_status": _safe_text(
            page.get("unit_status"), "missing" if layout == "gap" else "partial"
        ),
        "unit_role": _safe_text(page.get("unit_role")),
        "unit_contract": _safe_copy(page.get("unit_contract") or {}),
        **(
            {"unit_contract_validated": page.get("unit_contract_validated") is True}
            if "unit_contract_validated" in page
            else {}
        ),
        "page_code": _safe_text(page.get("page_code")),
        "display_code": _safe_text(page.get("display_code") or page.get("page_code")),
        "layout": layout,
        "title": full_title,
        "display_title": _compact_display_title(
            full_title, page.get("display_title")
        ),
        "takeaway": _safe_text(
            page.get("takeaway"), "本页结论需与来源和置信度同时阅读。"
        ),
        "decision_question": _safe_text(page.get("decision_question"), full_title),
        "decision_impact": _safe_text(page.get("decision_impact")),
        "observed_evidence_refs": _normalize_refs(page.get("observed_evidence_refs")),
        "counter_evidence_refs": _normalize_refs(page.get("counter_evidence_refs")),
        "recommendation": _safe_text(page.get("recommendation")),
        "decision_gate": _safe_text(page.get("decision_gate")),
        "owner": _safe_text(page.get("owner")),
        "upstream_page_refs": _normalize_refs(page.get("upstream_page_refs")),
        "downstream_page_refs": _normalize_refs(page.get("downstream_page_refs")),
        "blocks": blocks,
        "chart_specs": chart_specs,
        "diagram_specs": diagram_specs,
        "asset_refs": _normalize_refs(page.get("asset_refs")),
        "source_refs": source_refs,
        "confidence": confidence,
        "evidence_type": evidence_type,
        "load_priority": _safe_scalar(page.get("load_priority"), "normal"),
        "print_policy": print_policy,
        "story_role": _safe_text(page.get("story_role"), "primary_narrative"),
        "appendix_policy": _safe_text(page.get("appendix_policy"), "presentation"),
        "visual_evidence": _safe_text(page.get("visual_evidence"), "text"),
        "visual_language": visual_language,
    }


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", text).strip("-")
    return text or "page"


def _allocate_id(requested: Any, used: set[str]) -> str:
    base = _slug(requested)
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    result = f"{base}-{suffix}"
    used.add(result)
    return result


def _deduplicate_pages(pages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove exact repeated narrative while merging its evidence references."""
    output: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for raw_page in pages:
        page = deepcopy(dict(raw_page))
        if page.get("story_role") == "evidence_appendix":
            output.append(page)
            continue
        payload = {
            "unit_id": page.get("unit_id") or page.get("section_id"),
            "title": page.get("title"),
            "takeaway": page.get("takeaway"),
            "blocks": page.get("blocks"),
            "chart_specs": page.get("chart_specs"),
            "diagram_specs": page.get("diagram_specs"),
        }
        signature = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode(
                "utf-8"
            )
        ).hexdigest()
        position = seen.get(signature)
        if position is None:
            seen[signature] = len(output)
            output.append(page)
            continue
        existing = output[position]
        existing["source_refs"] = list(
            dict.fromkeys(
                _normalize_refs(existing.get("source_refs"))
                + _normalize_refs(page.get("source_refs"))
            )
        )
        existing["asset_refs"] = list(
            dict.fromkeys(
                _normalize_refs(existing.get("asset_refs"))
                + _normalize_refs(page.get("asset_refs"))
            )
        )
    return output


def _split_tables(page: Mapping[str, Any], rows_per_page: int) -> list[dict[str, Any]]:
    tables = [block for block in page["blocks"] if block.get("type") == "table"]
    non_tables = [block for block in page["blocks"] if block.get("type") != "table"]
    if not tables:
        return [deepcopy(dict(page))]
    chunk_count = max(
        1,
        max(math.ceil(len(table.get("rows", [])) / rows_per_page) for table in tables),
    )
    split: list[dict[str, Any]] = []
    for chunk_index in range(chunk_count):
        blocks = deepcopy(non_tables if chunk_index == 0 else [])
        for table in tables:
            rows = table.get("rows", [])
            start = chunk_index * rows_per_page
            chunk = rows[start : start + rows_per_page]
            if chunk or (chunk_index == 0 and not rows):
                table_copy = deepcopy(table)
                table_copy["rows"] = deepcopy(chunk)
                blocks.append(table_copy)
        if not blocks:
            continue
        page_copy = deepcopy(dict(page))
        page_copy["blocks"] = blocks
        if chunk_index:
            page_copy["page_id"] = f"{page['page_id']}-continuation-{chunk_index + 1}"
            page_copy["title"] = f"{page['title']}（续 {chunk_index + 1}）"
            page_copy["load_priority"] = "deferred"
        split.append(page_copy)
    return split


def compile_page_manifest(
    report_or_pages: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    modules: Mapping[str, Any] | None = None,
    table_rows_per_page: int = TABLE_ROWS_PER_PAGE,
    ensure_framework: bool = False,
) -> list[dict[str, Any]]:
    """Compile pages without a total-page cap; split every table at ten rows."""
    rows_per_page = min(TABLE_ROWS_PER_PAGE, max(1, int(table_rows_per_page)))
    raw_pages: list[Mapping[str, Any]]
    if isinstance(report_or_pages, Mapping):
        if "page_id" in report_or_pages and (
            "blocks" in report_or_pages or "layout" in report_or_pages
        ):
            raw_pages = [report_or_pages]
        else:
            resolved_modules = dict(modules or _extract_modules(report_or_pages))
            case_evidence, archlib_cases, _asset_registry = _compile_case_evidence(
                report_or_pages
            )
            market_outcome_cases = [
                case
                for case in case_evidence
                if str(case.get("case_role")) == "market_outcome"
            ]
            design_intent_cases = [
                case
                for case in case_evidence
                if str(case.get("case_role")) == "design_intent"
            ]
            gaps = _knowledge_gaps(report_or_pages)
            chains = _compile_decision_chains(report_or_pages, case_evidence, gaps)
            actions = _compile_action_register(report_or_pages, chains)
            assumptions = _compile_audit_list(report_or_pages, "assumptions")
            open_questions = _compile_audit_list(report_or_pages, "open_questions")
            knowledge = _knowledge_pages(
                chains,
                market_outcome_cases,
                design_intent_cases,
                actions,
                assumptions,
                open_questions,
                gaps,
            )
            raw_pages = _compose_report_pages(
                _module_pages(resolved_modules), knowledge
            )
            raw_pages.extend(_framework_input_pages(report_or_pages))
            custom = report_or_pages.get("pages") or report_or_pages.get("page_manifest") or []
            if isinstance(custom, Mapping):
                raw_pages.append(custom)
            elif isinstance(custom, Sequence) and not isinstance(custom, (str, bytes, bytearray)):
                raw_pages.extend(page for page in custom if isinstance(page, Mapping))
    elif isinstance(report_or_pages, Sequence) and not isinstance(
        report_or_pages, (str, bytes, bytearray)
    ):
        raw_pages = [page for page in report_or_pages if isinstance(page, Mapping)]
    else:
        raise TypeError("report_or_pages must be a report mapping or page sequence")

    manifest: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, raw_page in enumerate(raw_pages):
        page = _normalize_page(raw_page, index)
        if page is None:
            continue
        for split_page in _split_tables(page, rows_per_page):
            split_page["page_id"] = _allocate_id(split_page["page_id"], used_ids)
            manifest.append(split_page)
    if ensure_framework:
        contract_pages = [
            page
            for page in manifest
            if str(page.get("unit_role") or "") not in _SUPPORTING_UNIT_ROLES
        ]
        for section_id in VALID_SECTION_IDS:
            if unit_contract_satisfied(section_id, contract_pages):
                continue
            gap = _normalize_page(framework_gap_page(section_id), len(manifest))
            if gap is None:  # pragma: no cover - canonical gap pages are never raw
                continue
            gap["page_id"] = _allocate_id(gap["page_id"], used_ids)
            manifest.append(gap)
            contract_pages.append(gap)
    return sort_and_number_pages(_deduplicate_pages(manifest))


def _source_candidates(report: Mapping[str, Any]) -> list[Any]:
    candidates: list[Any] = []
    for path in (
        "source_registry",
        "sources",
        "provenance.sources",
        "architecture_director.source_matrix",
        "macro_context.source_registry",
        "social_intelligence.source_registry",
        "traditional_spatial_culture.source_registry",
        "professional_intelligence.source_registry",
    ):
        value = _path_get(report, path)
        if isinstance(value, Mapping):
            candidates.extend(value.values())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            candidates.extend(value)
    return candidates


def _compile_source_registry(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    registry: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, source in enumerate(_source_candidates(report)):
        if isinstance(source, Mapping):
            safe = _safe_copy(source)
            identity = str(
                safe.get("source_id")
                or safe.get("id")
                or safe.get("url")
                or safe.get("path")
                or safe.get("path_or_url")
                or safe.get("name")
                or ""
            )
            if not identity:
                identity = f"source-{index + 1}"
            source_id = str(safe.get("source_id") or safe.get("id") or "")
            if not source_id:
                source_id = "source:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            entry = {"source_id": source_id, **safe}
            entry.pop("id", None)
        elif isinstance(source, str):
            identity = source
            source_id = "source:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            entry = {"source_id": source_id, "name": source}
        else:
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        registry.append(entry)
    return registry


def _case_source_registry(
    cases: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for case in cases:
        if str(case.get("case_kind") or "") != "archlib_visual":
            continue
        case_id = str(case.get("case_id") or "")
        if not case_id:
            continue
        entries.append(
            {
                "source_id": f"archlib:{case_id}",
                "name": f"ArchLib · {case.get('project_name') or '未命名案例'}",
                "source_type": "archlib_case_evidence",
                "trust_tier": "L2",
                "used_for": "设计机制、空间关系与交付质感校准",
                "limitations": "不直接证明本项目售价、去化、成本、市场接受度或财务回报。",
                "taxonomy_path": str(case.get("taxonomy_path") or ""),
                "case_id": case_id,
            }
        )
    return entries


def _existing_evidence_nodes(report: Mapping[str, Any]) -> list[Any]:
    nodes: list[Any] = []
    for path in (
        "evidence_nodes",
        "evidence_graph.nodes",
        "architecture_director.evidence_nodes",
        "professional_intelligence.evidence_nodes",
    ):
        value = _path_get(report, path)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            nodes.extend(value)
    return nodes


def _compile_evidence_graph(
    report: Mapping[str, Any],
    manifest: Sequence[Mapping[str, Any]],
    *,
    decision_chains: Sequence[Mapping[str, Any]] = (),
    case_evidence: Sequence[Mapping[str, Any]] = (),
    actions: Sequence[Mapping[str, Any]] = (),
    gaps: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, node in enumerate(_existing_evidence_nodes(report)):
        if not isinstance(node, Mapping):
            continue
        node_id = _safe_text(
            node.get("evidence_id") or node.get("node_id"),
            f"evidence:{index + 1}",
        )
        if node_id in seen:
            continue
        seen.add(node_id)
        nodes.append(
            {
                "evidence_id": node_id,
                "claim": _safe_text(node.get("claim") or node.get("statement")),
                "evidence_type": normalize_evidence_type(
                    node.get("evidence_type") or node.get("claim_type")
                ),
                "source_refs": _normalize_refs(node.get("source_refs")),
                "confidence": _score_dict(node.get("confidence")),
                "conflicts": _safe_copy(node.get("conflicts") or []),
                "needs_human_review": bool(node.get("needs_human_review")),
            }
        )
    if not nodes:
        for page in manifest:
            node_id = f"page-evidence:{page['page_id']}"
            nodes.append(
                {
                    "evidence_id": node_id,
                    "claim": page["takeaway"],
                    "evidence_type": page["evidence_type"],
                    "source_refs": list(page["source_refs"]),
                    "confidence": deepcopy(page["confidence"]),
                    "conflicts": [],
                    "needs_human_review": page["confidence"].get("score", 0) < 0.55,
                }
            )
    def add_node(node_id: str, node_type: str, claim: str, source_refs: Any, confidence: Any) -> None:
        if not node_id or node_id in seen:
            return
        seen.add(node_id)
        nodes.append(
            {
                "evidence_id": node_id,
                "node_type": node_type,
                "claim": claim,
                "evidence_type": "analysis_inference",
                "source_refs": _normalize_refs(source_refs),
                "confidence": _score_dict(confidence),
                "conflicts": [],
                "needs_human_review": False,
            }
        )

    for chain in decision_chains:
        add_node(
            str(chain.get("chain_id") or ""),
            "decision_chain",
            _safe_text(chain.get("inference") or chain.get("question")),
            chain.get("source_refs"),
            chain.get("confidence"),
        )
    for case in case_evidence:
        add_node(
            str(case.get("case_id") or ""),
            "case_evidence",
            _safe_text(case.get("mechanism") or case.get("project_name")),
            case.get("source_refs"),
            case.get("confidence"),
        )
    for action in actions:
        add_node(
            str(action.get("action_id") or ""),
            "decision_action",
            _safe_text(action.get("action")),
            action.get("source_refs"),
            0.55,
        )
    for gap in gaps:
        add_node(
            str(gap.get("gap_id") or ""),
            "evidence_gap",
            _safe_text(gap.get("statement") or gap.get("recommended_action")),
            gap.get("source_refs"),
            0.0,
        )

    existing_edges = _path_get(report, "evidence_graph.edges")
    edges = (
        _safe_copy(existing_edges)
        if isinstance(existing_edges, Sequence) and not isinstance(existing_edges, str)
        else []
    )
    if not isinstance(edges, list):
        edges = []

    def add_edge(source: Any, target: Any, edge_type: str) -> None:
        source_id = str(source or "")
        target_id = str(target or "")
        if not source_id or not target_id:
            return
        edge = {"source": source_id, "target": target_id, "type": edge_type}
        if edge not in edges:
            edges.append(edge)

    for chain in decision_chains:
        chain_id = chain.get("chain_id")
        for evidence_ref in _normalize_refs(chain.get("evidence_refs")):
            add_edge(evidence_ref, chain_id, "supports")
        for case_ref in _normalize_refs(chain.get("case_refs")):
            add_edge(case_ref, chain_id, "exemplifies")
        for gap in gaps:
            add_edge(gap.get("gap_id"), chain_id, "challenges")
    for action in actions:
        for decision_ref in _normalize_refs(action.get("decision_refs")):
            add_edge(decision_ref, action.get("action_id"), "drives")
        for case_ref in _normalize_refs(action.get("case_refs")):
            add_edge(case_ref, action.get("action_id"), "exemplifies")
    return {"nodes": nodes, "edges": edges}


def _project(report: Mapping[str, Any]) -> dict[str, Any]:
    project = report.get("project")
    if isinstance(project, Mapping):
        return _safe_copy(project)
    parcel = report.get("parcel") or {}
    context = report.get("project_context") or {}
    result: dict[str, Any] = {}
    if isinstance(context, Mapping):
        for key in ("project_id", "name", "stage", "brief"):
            if key in context:
                result[key] = _safe_copy(context[key])
    if isinstance(parcel, Mapping):
        for key in ("city", "district", "address", "lng", "lat", "analysis_mode", "vision"):
            if key in parcel:
                result[key] = _safe_copy(parcel[key])
    return result


def _meta(report: Mapping[str, Any]) -> dict[str, Any]:
    supplied = report.get("meta") if isinstance(report.get("meta"), Mapping) else {}
    meta = _safe_copy(supplied)
    meta.setdefault("compiled_at", datetime.now(timezone.utc).isoformat())
    meta.setdefault("contract", SCHEMA_VERSION)
    return meta


def _compile_unit_data(
    manifest: Sequence[Mapping[str, Any]],
    qa: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Summarize the twelve report units without renaming evidence modules."""
    contract = framework_manifest()
    readiness = qa.get("unit_readiness") if isinstance(qa, Mapping) else {}
    if not isinstance(readiness, Mapping):
        readiness = {}
    result: list[dict[str, Any]] = []
    for unit in contract["units"]:
        unit_id = str(unit["unit_id"])
        pages = [
            page
            for page in manifest
            if str(page.get("unit_id") or page.get("section_id") or "") == unit_id
        ]
        source_refs = list(
            dict.fromkeys(
                reference
                for page in pages
                for reference in _normalize_refs(page.get("source_refs"))
            )
        )
        payload_refs = list(
            dict.fromkeys(
                str(page.get("source_chapter_id") or page.get("chapter_id") or "")
                for page in pages
                if page.get("source_chapter_id") or page.get("chapter_id")
            )
        )
        gaps = list(
            dict.fromkeys(
                _safe_text(block.get("text"))
                for page in pages
                for block in page.get("blocks", [])
                if isinstance(block, Mapping)
                and block.get("type") == "gap"
                and _safe_text(block.get("text"))
            )
        )
        scores = [
            float(_score_dict(page.get("confidence")).get("score") or 0.0)
            for page in pages
            if page.get("layout") != "gap"
        ]
        confidence = round(sum(scores) / len(scores), 6) if scores else 0.0
        status = str(readiness.get(unit_id) or "missing")
        non_gap = sum(1 for page in pages if page.get("layout") != "gap")
        completeness = round(non_gap / len(pages), 4) if pages else 0.0
        result.append(
            {
                "framework_version": FRAMEWORK_VERSION,
                "group_id": str(unit["group_id"]),
                "unit_id": unit_id,
                "unit_title": str(unit["title"]),
                "status": status,
                "completeness": completeness,
                "decision_eligibility": (
                    "eligible"
                    if status == "ready" and confidence >= 0.55
                    else "conditional"
                    if status in {"ready", "partial"}
                    else "not_eligible"
                ),
                "evidence_confidence": confidence,
                "source_refs": source_refs,
                "evidence_gaps": gaps,
                "payload_refs": payload_refs,
                "page_refs": [str(page.get("page_id") or "") for page in pages],
            }
        )
    return result


def _case_role_contract_valid(case: Mapping[str, Any]) -> bool:
    role = str(case.get("case_role") or "")
    if role == "market_outcome":
        structurally_valid = (
            str(case.get("outcome_polarity") or "")
            in {"positive", "negative", "mixed", "neutral", "unknown"}
            and isinstance(case.get("outcome_metrics"), list)
            and isinstance(case.get("outcome_source_refs"), list)
            and case.get("decision_eligibility")
            in {"market_outcome_evidence", "evidence_gap_only"}
        )
        if str(case.get("status") or "") != "ready":
            return structurally_valid
        return structurally_valid and bool(
            case.get("outcome_claim")
            and case.get("outcome_period")
            and case.get("outcome_metrics")
            and case.get("outcome_source_refs")
        )
    if role == "design_intent":
        return bool(
            case.get("outcome_polarity") == "not_applicable"
            and case.get("outcome_metrics") == []
            and case.get("outcome_source_refs") == []
            and case.get("decision_eligibility") == "mechanism_only"
        )
    return False


def _qa(
    manifest: Sequence[Mapping[str, Any]],
    modules: Mapping[str, Any],
    *,
    authoritative_manifest: bool = False,
    decision_chains: Sequence[Mapping[str, Any]] = (),
    case_evidence: Sequence[Mapping[str, Any]] = (),
    archlib_cases: Sequence[Mapping[str, Any]] = (),
    actions: Sequence[Mapping[str, Any]] = (),
    required_units: Sequence[str] = VALID_SECTION_IDS,
) -> dict[str, Any]:
    required = tuple(dict.fromkeys(str(unit) for unit in required_units))
    unknown_required = [unit for unit in required if unit not in VALID_SECTION_IDS]
    if unknown_required:
        raise ValueError(f"unknown required units: {unknown_required}")
    page_ids = [str(page["page_id"]) for page in manifest]
    table_row_counts = [
        len(block.get("rows", []))
        for page in manifest
        for block in page["blocks"]
        if block.get("type") == "table"
    ]
    missing_modules = [
        spec["id"] for spec in MODULE_SPECS if _is_missing_module(modules[spec["id"]])
    ]
    appendix_positions = [
        index
        for index, page in enumerate(manifest)
        if page.get("story_role") == "evidence_appendix"
    ]
    first_appendix = min(appendix_positions) if appendix_positions else len(manifest)
    section_ids = [str(page.get("section_id") or "") for page in manifest]
    page_codes = [str(page.get("page_code") or "") for page in manifest]
    section_positions = [SECTION_ORDER.get(section_id, len(SECTION_ORDER)) for section_id in section_ids]
    unit_readiness: dict[str, str] = {}
    for unit_id in VALID_SECTION_IDS:
        unit_pages = [
            page
            for page in manifest
            if str(page.get("unit_id") or page.get("section_id") or "") == unit_id
            and str(page.get("unit_role") or "") not in _SUPPORTING_UNIT_ROLES
        ]
        statuses = {
            str(page.get("unit_status") or "partial").lower() for page in unit_pages
        }
        has_gap = any(
            page.get("chapter_id") == "framework_gap"
            or page.get("layout") == "gap"
            or status in {"missing", "not_assessable", "not_available"}
            for page, status in (
                (page, str(page.get("unit_status") or "partial").lower())
                for page in unit_pages
            )
        )
        if "blocked" in statuses:
            unit_readiness[unit_id] = "blocked"
        elif has_gap and "ready" in statuses:
            unit_readiness[unit_id] = "partial"
        elif has_gap or not unit_pages:
            unit_readiness[unit_id] = "missing"
        elif statuses == {"ready"}:
            unit_readiness[unit_id] = "ready"
        else:
            unit_readiness[unit_id] = "partial"
    structurally_complete_chains = [
        chain
        for chain in decision_chains
        if chain.get("observed_evidence")
        and chain.get("counter_evidence")
        and chain.get("inference")
        and chain.get("design_actions")
        and chain.get("source_refs")
        and chain.get("evidence_refs")
        and chain.get("market_effect")
        and chain.get("financial_effect")
        and chain.get("decision_gate")
        and chain.get("owner")
    ]
    structurally_complete_ids = {
        str(chain.get("chain_id")) for chain in structurally_complete_chains
    }
    checks = {
        "page_ids_unique": len(page_ids) == len(set(page_ids)),
        "required_page_fields": all(
            all(field in page for field in PAGE_REQUIRED_FIELDS) for page in manifest
        ),
        "table_rows_bounded": all(count <= TABLE_ROWS_PER_PAGE for count in table_row_counts),
        "no_opaque_payload_pages": all(not _is_raw_page(page) for page in manifest),
        # An authoritative PageManifest may deliberately distil multiple
        # evidence modules into one decision page. Requiring one visible page
        # per raw module would re-introduce the repetitive report this mode is
        # designed to avoid; the twelve SC/AD/VA/CS units remain mandatory.
        "required_chapters_present": authoritative_manifest or {
            spec["id"] for spec in MODULE_SPECS
        }.issubset({str(page["chapter_id"]) for page in manifest}),
        "required_framework_units_present": set(required).issubset(
            set(section_ids)
        ),
        "framework_order_valid": section_positions == sorted(section_positions),
        "framework_metadata_complete": all(
            page.get("framework_version") == FRAMEWORK_VERSION
            and page.get("unit_id") in VALID_SECTION_IDS
            and page.get("group_id") in {"SC", "AD", "VA", "CS"}
            and bool(page.get("display_code"))
            and page.get("section_id") in VALID_SECTION_IDS
            and bool(page.get("section_group"))
            and bool(page.get("section_group_title"))
            and bool(page.get("section_group_label"))
            and bool(page.get("section_title"))
            and bool(page.get("page_code"))
            for page in manifest
        ),
        "no_unclassified_framework_pages": all(
            page.get("section_classification") != "fallback" for page in manifest
        ),
        "page_codes_unique": len(page_codes) == len(set(page_codes)),
        "story_metadata_complete": all(
            page.get("story_role") in {"primary_narrative", "evidence_appendix"}
            and page.get("appendix_policy") in {"presentation", "evidence"}
            and bool(page.get("visual_evidence"))
            for page in manifest
        ),
        "visual_language_valid": all(
            page.get("visual_language") in VISUAL_LANGUAGES
            and all(
                chart.get("grammar") == page.get("visual_language")
                for chart in page.get("chart_specs", [])
                if isinstance(chart, Mapping)
            )
            for page in manifest
        ),
        "chart_types_supported": all(
            chart.get("render_status") == "ready"
            for page in manifest
            for chart in page.get("chart_specs", [])
            if isinstance(chart, Mapping)
        ),
        "diagram_types_supported": all(
            diagram.get("render_status") in {"ready", "schematic_only"}
            for page in manifest
            for diagram in page.get("diagram_specs", [])
            if isinstance(diagram, Mapping)
        ),
        "presentation_before_appendix": all(
            page.get("story_role") == "evidence_appendix"
            for page in manifest[first_appendix:]
        ),
        "knowledge_chain_fields_valid": len(structurally_complete_chains)
        == len(decision_chains),
        "case_roles_partitioned": all(
            _case_role_contract_valid(case) for case in case_evidence
        ),
        "archlib_case_fields_valid": all(
            bool(case.get("case_id"))
            and bool(case.get("mechanism"))
            and bool(case.get("transfer_actions"))
            and bool(case.get("transfer_conditions"))
            and bool(case.get("conflicts"))
            and bool(case.get("prohibited_analogies"))
            and bool(case.get("source_refs"))
            and bool(case.get("confidence_note"))
            for case in archlib_cases
        ),
        "action_register_fields_valid": all(
            bool(action.get("action_id"))
            and bool(action.get("action"))
            and bool(action.get("owner"))
            and bool(action.get("trigger"))
            and bool(action.get("acceptance"))
            and bool(action.get("fallback"))
            and bool(
                action.get("evidence_refs")
                or action.get("source_refs")
                or action.get("decision_refs")
            )
            for action in actions
        ),
    }
    complete_chains = [
        chain
        for chain in decision_chains
        if str(chain.get("chain_id")) in structurally_complete_ids
        and chain.get("status") == "ready"
        and not chain.get("validation_gaps")
        and not chain.get("human_review")
        and bool(_score_dict(chain.get("confidence")).get("actionable"))
    ]
    structural_rate = (
        round(len(structurally_complete_chains) / len(decision_chains), 4)
        if decision_chains
        else 0.0
    )
    closure_rate = (
        round(len(complete_chains) / len(decision_chains), 4)
        if decision_chains
        else 0.0
    )
    structural_passed = all(checks.values())
    missing_required_units = [
        unit for unit in required if unit_readiness.get(unit) == "missing"
    ]
    decision_ready = structural_passed and all(
        unit_readiness.get(unit) == "ready" for unit in required
    )
    return {
        "status": "ready" if decision_ready else "ready_with_gaps",
        "framework_version": FRAMEWORK_VERSION,
        "required_units": list(required),
        "missing_required_units": missing_required_units,
        "delivery_ready": decision_ready,
        "page_count": len(manifest),
        "presentation_page_count": first_appendix,
        "evidence_appendix_page_count": len(appendix_positions),
        "missing_modules": missing_modules,
        "framework_gap_units": sorted(
            {
                str(page.get("section_id"))
                for page in manifest
                if page.get("chapter_id") == "framework_gap"
            },
            key=lambda section_id: SECTION_ORDER.get(section_id, len(SECTION_ORDER)),
        ),
        "unit_readiness": unit_readiness,
        "blocked_units": [
            unit_id for unit_id, status in unit_readiness.items() if status == "blocked"
        ],
        "partial_units": [
            unit_id for unit_id, status in unit_readiness.items() if status == "partial"
        ],
        "missing_units": [
            unit_id for unit_id, status in unit_readiness.items() if status == "missing"
        ],
        "structural_passed": structural_passed,
        "decision_ready": decision_ready,
        "max_table_rows": max(table_row_counts, default=0),
        "knowledge_closure": {
            "decision_chain_count": len(decision_chains),
            "market_outcome_case_count": sum(
                1
                for case in case_evidence
                if str(case.get("case_role")) == "market_outcome"
            ),
            "design_intent_case_count": sum(
                1
                for case in case_evidence
                if str(case.get("case_role")) == "design_intent"
            ),
            "archlib_case_count": len(archlib_cases),
            "action_count": len(actions),
            "structurally_complete_chain_count": len(structurally_complete_chains),
            "structural_chain_rate": structural_rate,
            "closed_chain_count": len(complete_chains),
            "closure_rate": closure_rate,
        },
        "checks": checks,
        "passed": structural_passed,
    }


def build_report_document(report_json: Mapping[str, Any]) -> dict[str, Any]:
    """Build a renderer-neutral ReportDocument without mutating ``report_json``."""
    if not isinstance(report_json, Mapping):
        raise TypeError("report_json must be a mapping")
    report = dict(report_json)
    modules = _extract_modules(report)
    case_evidence, archlib_cases, asset_registry = _compile_case_evidence(report)
    declared_assets = report.get("asset_registry")
    if isinstance(declared_assets, Mapping):
        declared_assets = list(declared_assets.values())
    if isinstance(declared_assets, Sequence) and not isinstance(
        declared_assets, (str, bytes, bytearray)
    ):
        seen_asset_ids = {
            str(item.get("asset_id") or item.get("id") or "")
            for item in asset_registry
            if isinstance(item, Mapping)
        }
        for entry in declared_assets:
            if not isinstance(entry, Mapping):
                continue
            safe = _safe_copy(entry)
            asset_id = str(safe.get("asset_id") or safe.get("id") or "").strip()
            if not asset_id or asset_id in seen_asset_ids:
                continue
            safe["asset_id"] = asset_id
            safe.pop("id", None)
            asset_registry.append(safe)
            seen_asset_ids.add(asset_id)
    market_outcome_cases = [
        case
        for case in case_evidence
        if str(case.get("case_role")) == "market_outcome"
    ]
    design_intent_cases = [
        case
        for case in case_evidence
        if str(case.get("case_role")) == "design_intent"
    ]
    evidence_gaps = _knowledge_gaps(report)
    decision_chains = _compile_decision_chains(
        report, case_evidence, evidence_gaps
    )
    action_register = _compile_action_register(report, decision_chains)
    assumptions = _compile_audit_list(report, "assumptions")
    open_questions = _compile_audit_list(report, "open_questions")
    panorama = _safe_copy(report.get("project_panorama") or {})
    required_units = (
        list(panorama.get("required_units") or [])
        if isinstance(panorama, Mapping)
        else []
    )
    included_units = (
        list(panorama.get("included_units") or required_units)
        if isinstance(panorama, Mapping)
        else list(required_units)
    )
    adaptive_mode = bool(required_units)
    if report.get("page_manifest_authoritative"):
        authoritative_pages = report.get("page_manifest") or report.get("pages") or []
        manifest = compile_page_manifest(
            authoritative_pages,
            ensure_framework=not adaptive_mode,
        )
    else:
        manifest = compile_page_manifest(
            report,
            modules=modules,
            ensure_framework=not adaptive_mode,
        )
    if adaptive_mode:
        adaptive = compile_adaptive_manifest(
            manifest,
            required_units=required_units,
            included_units=included_units,
        )
        manifest = adaptive["pages"]
    source_registry = _compile_source_registry(report)
    registered_source_ids = {
        str(item.get("source_id"))
        for item in source_registry
        if isinstance(item, Mapping) and item.get("source_id")
    }
    for entry in _case_source_registry(case_evidence):
        if entry["source_id"] in registered_source_ids:
            continue
        source_registry.append(entry)
        registered_source_ids.add(entry["source_id"])
    evidence_graph = _compile_evidence_graph(
        report,
        manifest,
        decision_chains=decision_chains,
        case_evidence=case_evidence,
        actions=action_register,
        gaps=evidence_gaps,
    )
    qa = _qa(
        manifest,
        modules,
        authoritative_manifest=bool(report.get("page_manifest_authoritative")),
        decision_chains=decision_chains,
        case_evidence=case_evidence,
        archlib_cases=archlib_cases,
        actions=action_register,
        required_units=required_units or VALID_SECTION_IDS,
    )
    unit_data = _compile_unit_data(manifest, qa)
    macro_context = _safe_copy(report.get("macro_context") or modules["macro"])
    social_intelligence = _safe_copy(
        report.get("social_intelligence") or modules["social_intelligence"]
    )
    persona_profiles = report.get("persona_evidence_profiles")
    if not isinstance(persona_profiles, Sequence) or isinstance(
        persona_profiles, (str, bytes, bytearray)
    ):
        persona_profiles = (
            social_intelligence.get("persona_evidence_profiles", [])
            if isinstance(social_intelligence, Mapping)
            else []
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "chart_contract_version": CHART_CONTRACT_VERSION,
        "diagram_contract_version": DIAGRAM_CONTRACT_VERSION,
        "template_id": TEMPLATE_ID,
        "template_profile_version": TEMPLATE_PROFILE_VERSION,
        "report_framework": framework_manifest(),
        "project_panorama": panorama,
        "unit_data": unit_data,
        "meta": _meta(report),
        "project": _project(report),
        "macro_context": macro_context,
        "social_intelligence": social_intelligence,
        "professional_intelligence": _safe_copy(
            report.get("professional_intelligence") or {}
        ),
        "persona_evidence_profiles": _safe_copy(persona_profiles),
        "synthetic_personas": _safe_copy(
            report.get("synthetic_personas") or modules["synthetic_personas"]
        ),
        "traditional_spatial_culture": _safe_copy(
            report.get("traditional_spatial_culture")
            or modules["traditional_spatial_culture"]
        ),
        "competitor_series": _safe_copy(
            report.get("competitor_series") or modules["competitor_series"]
        ),
        "premium_analysis": _safe_copy(
            report.get("premium_analysis") or modules["premium_analysis"]
        ),
        "absorption_forecast": _safe_copy(
            report.get("absorption_forecast") or modules["absorption_forecast"]
        ),
        "investment_case": _safe_copy(
            report.get("investment_case") or modules["investment_case"]
        ),
        "concept_options": _safe_copy(report.get("concept_options") or {}),
        "recommended_scheme": _safe_copy(report.get("recommended_scheme") or {}),
        "architecture_design": _safe_copy(report.get("architecture_design") or {}),
        "design_value_premium": _safe_copy(
            report.get("design_value_premium") or {}
        ),
        "confidence_state": _safe_copy(report.get("confidence_state") or {}),
        "decision_chains": _safe_copy(decision_chains),
        "case_evidence": _safe_copy(case_evidence),
        "market_outcome_case_evidence": _safe_copy(market_outcome_cases),
        "design_intent_case_evidence": _safe_copy(design_intent_cases),
        "archlib_case_evidence": _safe_copy(archlib_cases),
        "action_register": _safe_copy(action_register),
        "assumptions": _safe_copy(assumptions),
        "open_questions": _safe_copy(open_questions),
        "evidence_gaps": _safe_copy(evidence_gaps),
        "asset_registry": _safe_copy(asset_registry),
        "modules": modules,
        "evidence_graph": evidence_graph,
        "page_manifest": manifest,
        "source_registry": source_registry,
        "qa": qa,
    }
    return sanitize_portable_value(document)


__all__ = [
    "FRAMEWORK_VERSION",
    "MODULE_SPECS",
    "UNIT_INPUT_SPECS",
    "PAGE_REQUIRED_FIELDS",
    "SCHEMA_VERSION",
    "CHART_CONTRACT_VERSION",
    "DIAGRAM_CONTRACT_VERSION",
    "TEMPLATE_PROFILE_VERSION",
    "TABLE_ROWS_PER_PAGE",
    "sanitize_portable_value",
    "TEMPLATE_ID",
    "build_report_document",
    "compile_page_manifest",
    "framework_manifest",
]
