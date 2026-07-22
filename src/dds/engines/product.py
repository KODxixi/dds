"""AD1-AD4 product-positioning engine with two explicit operating modes.

The engine deliberately separates directional design inferences from
quantitative product facts. A concept direction may exist before its unit
mix, price or statutory geometry is known; those quantitative fields remain
unknown until independently evidenced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Mapping, Sequence


DESIGN_TASK_KEYS = (
    "site_plan",
    "floor_plans",
    "facade",
    "landscape",
    "show_area",
)


class ProductMode(str, Enum):
    AUDIT_EXISTING_SCHEME = "audit_existing_scheme"
    PROPOSE_CONCEPT_SCHEME = "propose_concept_scheme"


class ProductStatus(str, Enum):
    READY = "ready"
    CONCEPT_ONLY = "concept_only"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class UnitMixRow:
    segment: str
    unit_area_m2: float
    units: int
    unit_price_cny_m2: float | None = None
    declared_share: float | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.segment.strip():
            raise ValueError("segment must not be empty")
        if not math.isfinite(self.unit_area_m2) or self.unit_area_m2 <= 0:
            raise ValueError("unit_area_m2 must be positive")
        if self.units < 0:
            raise ValueError("units must not be negative")
        if self.unit_price_cny_m2 is not None and self.unit_price_cny_m2 < 0:
            raise ValueError("unit_price_cny_m2 must not be negative")
        if self.declared_share is not None and not 0 <= self.declared_share <= 1:
            raise ValueError("declared_share must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class SchemeAudit:
    total_units: int
    saleable_area_m2: float
    saleable_value_cny: float | None
    computed_shares: Mapping[str, float]
    reconciliation_errors: Mapping[str, float]
    tolerance: float
    reconciled: bool


@dataclass(frozen=True, slots=True)
class ConceptDirection:
    direction_id: str
    positioning: str
    target_customer: str
    value_proposition: str
    design_brief: tuple[str, ...]
    switch_trigger: str
    evidence_type: str = "analysis_inference"
    # Appended with a default to preserve the original positional constructor.
    # Values are conceptual tasks, never dimensions or statutory geometry.
    design_tasks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProductResult:
    mode: ProductMode
    status: ProductStatus
    audit: SchemeAudit | None
    concepts: tuple[ConceptDirection, ...]
    recommended_direction_id: str | None
    evidence_refs: tuple[str, ...]
    conclusions: tuple[str, ...]
    assumptions: tuple[str, ...]
    counter_evidence: tuple[str, ...]
    gaps: tuple[str, ...]
    actions: tuple[str, ...]
    comparison_dimensions: tuple[str, ...] = (
        "target_customer",
        "value_proposition",
        "design_focus",
        "switch_trigger",
    )
    elimination_reasons: Mapping[str, str] = field(default_factory=dict)


class ProductEngine:
    """Audit supplied schemes or issue non-geometric concept directions."""

    def __init__(self, reconciliation_tolerance: float = 0.005) -> None:
        if not 0 <= reconciliation_tolerance <= 0.05:
            raise ValueError("reconciliation_tolerance must be between 0 and 0.05")
        self.reconciliation_tolerance = reconciliation_tolerance

    def audit_existing_scheme(
        self,
        rows: Sequence[UnitMixRow],
        *,
        declared_total_units: int | None = None,
        declared_saleable_area_m2: float | None = None,
        declared_saleable_value_cny: float | None = None,
        statutory_inputs_complete: bool = False,
    ) -> ProductResult:
        if not rows:
            return ProductResult(
                mode=ProductMode.AUDIT_EXISTING_SCHEME,
                status=ProductStatus.BLOCKED,
                audit=None,
                concepts=(),
                recommended_direction_id=None,
                evidence_refs=(),
                conclusions=("没有可审计的产品户配输入。",),
                assumptions=(),
                counter_evidence=(),
                gaps=("缺少甲方强排或户配明细。",),
                actions=("提供面积段、套数、单价及其资料来源。",),
                comparison_dimensions=(
                    "scheme_input",
                    "unit_mix_reconciliation",
                    "saleable_area_reconciliation",
                    "saleable_value_reconciliation",
                    "statutory_review_boundary",
                ),
            )

        total_units = sum(row.units for row in rows)
        saleable_area = sum(row.units * row.unit_area_m2 for row in rows)
        priced = all(row.unit_price_cny_m2 is not None for row in rows)
        saleable_value = (
            sum(row.units * row.unit_area_m2 * float(row.unit_price_cny_m2) for row in rows)
            if priced
            else None
        )
        shares = {
            row.segment: (row.units / total_units if total_units else 0.0) for row in rows
        }
        errors: dict[str, float] = {}

        def relative_error(actual: float, declared: float) -> float:
            if declared == 0:
                return 0.0 if actual == 0 else float("inf")
            return abs(actual - declared) / abs(declared)

        if declared_total_units is not None:
            errors["total_units"] = relative_error(total_units, declared_total_units)
        if declared_saleable_area_m2 is not None:
            errors["saleable_area_m2"] = relative_error(
                saleable_area, declared_saleable_area_m2
            )
        if declared_saleable_value_cny is not None:
            errors["saleable_value_cny"] = (
                relative_error(saleable_value, declared_saleable_value_cny)
                if saleable_value is not None
                else float("inf")
            )
        for row in rows:
            if row.declared_share is not None:
                errors[f"share:{row.segment}"] = abs(
                    shares[row.segment] - row.declared_share
                )
        reconciled = all(
            value <= self.reconciliation_tolerance for value in errors.values()
        )
        evidence_refs = tuple(
            dict.fromkeys(
                ref
                for row in rows
                for raw_ref in row.evidence_refs
                if (ref := str(raw_ref).strip())
            )
        )
        gaps: list[str] = []
        actions: list[str] = []
        if not priced:
            gaps.append("部分面积段缺少有来源的单价，无法完成货值勾稽。")
            actions.append("补充各面积段单价证据或明确其为经批准的场景假设。")
        if not evidence_refs:
            gaps.append("户配与价格输入没有 EvidenceRecord 引用。")
            actions.append("把甲方强排、面积表和价格依据冻结为 EvidenceRecord。")
        if not statutory_inputs_complete:
            gaps.append("法定规划条件不完整，只能审计输入表，不能给出比例几何结论。")
            actions.append("补充用地边界、容积率、限高、退界、日照及配建条件。")
        if not reconciled:
            gaps.append("套数、占比、面积或货值勾稽误差超过 0.5%。")
            actions.append("修正强排汇总口径后重新计算。")
        status = (
            ProductStatus.READY
            if reconciled and priced and bool(evidence_refs) and statutory_inputs_complete
            else ProductStatus.CONCEPT_ONLY
        )
        audit = SchemeAudit(
            total_units=total_units,
            saleable_area_m2=round(saleable_area, 3),
            saleable_value_cny=round(saleable_value, 2)
            if saleable_value is not None
            else None,
            computed_shares={key: round(value, 8) for key, value in shares.items()},
            reconciliation_errors={
                key: round(value, 8) if math.isfinite(value) else value
                for key, value in errors.items()
            },
            tolerance=self.reconciliation_tolerance,
            reconciled=reconciled,
        )
        return ProductResult(
            mode=ProductMode.AUDIT_EXISTING_SCHEME,
            status=status,
            audit=audit,
            concepts=(),
            recommended_direction_id=None,
            evidence_refs=evidence_refs,
            conclusions=(
                f"已审计 {len(rows)} 个面积段、{total_units} 套输入。",
                "所有设计适配判断均为 analysis_inference，不替代法定审查。",
            ),
            assumptions=("货值=各面积段套数×建筑面积×单价之和。",),
            counter_evidence=("勾稽不通过时不得沿用汇总数字。",)
            if not reconciled
            else (),
            gaps=tuple(gaps),
            actions=tuple(actions),
            comparison_dimensions=(
                "scheme_input",
                "unit_mix_reconciliation",
                "saleable_area_reconciliation",
                "saleable_value_reconciliation",
                "statutory_review_boundary",
            ),
            elimination_reasons={},
        )

    def propose_concept_schemes(
        self,
        *,
        market_evidence_refs: Sequence[str],
        statutory_inputs_complete: bool = False,
        preferred_direction_id: str | None = None,
    ) -> ProductResult:
        concepts = (
            ConceptDirection(
                direction_id="A",
                positioning="稳健适配",
                target_customer="已被有效竞品证据覆盖的主流需求客群",
                value_proposition="控制非必要复杂度，以成熟产品逻辑降低交付风险",
                design_brief=("高频面积段优先", "总图效率优先", "示范区表达与交付一致"),
                switch_trigger="主流面积段去化证据转弱或成本边界被突破时退出",
                design_tasks={
                    "site_plan": (
                        "梳理到达、归家、公共空间与后勤流线关系",
                        "待法定条件齐备后验证容量，不输出比例几何",
                    ),
                    "floor_plans": (
                        "围绕高频需求建立功能清单与空间效率检查项",
                        "面积、开间与套数保持待证，不在概念阶段承诺",
                    ),
                    "facade": (
                        "建立耐久、可维护且可交付的材料与模块原则",
                        "展示标准与大区交付标准采用同一核验清单",
                    ),
                    "landscape": (
                        "优先组织高频归家、停留与儿童活动场景",
                        "景观配置按成本边界设置可增减任务包",
                    ),
                    "show_area": (
                        "以主流产品逻辑验证真实生活场景",
                        "明确样板展示与实际交付差异清单",
                    ),
                },
            ),
            ConceptDirection(
                direction_id="B",
                positioning="均衡升级",
                target_customer="重视空间效率与社区体验的改善客群",
                value_proposition="在可验证成本边界内强化公区、景观和立面体验",
                design_brief=("户型效率与公共体验平衡", "分级配置", "可量化成本清单"),
                switch_trigger="改善需求证据不足或溢价无法覆盖增量成本时切换至 A",
                design_tasks={
                    "site_plan": (
                        "比较效率、公共体验与分期实施之间的关系",
                        "以法定条件为深化前置门，不输出比例几何",
                    ),
                    "floor_plans": (
                        "建立改善客群功能优先级与可变空间任务书",
                        "用后续户配、货值与去化证据校准面积边界",
                    ),
                    "facade": (
                        "定义关键可感知界面与普通界面的分级策略",
                        "将材料、节点和维护成本纳入同一验证表",
                    ),
                    "landscape": (
                        "构建归家、会客、亲子与安静停留的场景层级",
                        "逐项核验体验动作与增量成本的对应关系",
                    ),
                    "show_area": (
                        "验证空间效率与社区体验的核心价值主张",
                        "设置可复制到交付区的材料和场景边界",
                    ),
                },
            ),
            ConceptDirection(
                direction_id="C",
                positioning="差异化突破",
                target_customer="对稀缺体验具有支付意愿但尚需补证的细分客群",
                value_proposition="用明确主题和场景形成差异化，不承诺未经验证的售价溢价",
                design_brief=("单一核心体验", "展示与交付闭环", "设置可撤回的阶段门"),
                switch_trigger="支付意愿、成本或竞品差异证据任一不成立时切换至 B",
                design_tasks={
                    "site_plan": (
                        "围绕单一核心体验组织空间与运营关系",
                        "通过法定条件和成本门后再进入容量深化",
                    ),
                    "floor_plans": (
                        "把细分客群体验假设转成可测试的功能任务",
                        "支付意愿未补证前不锁定面积、套数或尺寸",
                    ),
                    "facade": (
                        "形成可识别但可撤回的主题与构造策略",
                        "同步验证交付一致性、耐久性与维护边界",
                    ),
                    "landscape": (
                        "用核心体验串联到达、停留和社群活动",
                        "把差异化动作拆成可独立验证的成本单元",
                    ),
                    "show_area": (
                        "将核心体验假设做成可观察、可访谈的验证场景",
                        "在证据不足时保留降级到均衡方案的接口",
                    ),
                },
            ),
        )
        evidence_refs = tuple(
            dict.fromkeys(
                ref
                for raw_ref in market_evidence_refs
                if (ref := str(raw_ref).strip())
            )
        )
        gaps: list[str] = []
        actions: list[str] = []
        if not evidence_refs:
            gaps.append("缺少 ③ 市场竞品 EvidenceRecord，不能选择主推方案。")
            actions.append("先完成 SC2 结构化竞品筛选并冻结证据。")
        if not statutory_inputs_complete:
            gaps.append("法定条件不完整，三个方案仅为方向性概念，禁止比例几何结论。")
            actions.append("补齐法定条件后再进入容量与强排深化。")
        valid_ids = {item.direction_id for item in concepts}
        if preferred_direction_id is not None and preferred_direction_id not in valid_ids:
            raise ValueError("preferred_direction_id must be A, B or C")
        recommendation = preferred_direction_id if evidence_refs else None
        status = (
            ProductStatus.READY
            if recommendation and statutory_inputs_complete
            else ProductStatus.CONCEPT_ONLY
            if evidence_refs
            else ProductStatus.BLOCKED
        )
        return ProductResult(
            mode=ProductMode.PROPOSE_CONCEPT_SCHEME,
            status=status,
            audit=None,
            concepts=concepts,
            recommended_direction_id=recommendation,
            evidence_refs=evidence_refs,
            conclusions=(
                "已形成三个使用统一比较维度的方向性概念方案。",
                "未由证据支持的选择保持未决，不由 Agent 自动补造主推结论。",
            ),
            assumptions=(
                "方案判断均标记为 analysis_inference。",
                "preferred_direction_id 是本轮选择输入；竞品证据用于约束比较，不自动证明方案最优。",
            )
            if recommendation
            else ("方案判断均标记为 analysis_inference。",),
            counter_evidence=("若支付意愿或成本证据不支持，差异化方案必须降级。",),
            gaps=tuple(gaps),
            actions=tuple(actions),
            comparison_dimensions=(
                "target_customer",
                "value_proposition",
                "design_focus",
                "switch_trigger",
            ),
            elimination_reasons={
                concept.direction_id: (
                    "当前主推方案，未淘汰。"
                    if concept.direction_id == recommendation
                    else "未被本轮选择输入指定为主推；不视为已被证据化淘汰，达到切换阈值时重新比较。"
                )
                for concept in concepts
            }
            if recommendation
            else {
                concept.direction_id: "缺少证据化选择依据，所有方案均不得淘汰。"
                for concept in concepts
            },
        )


__all__ = [
    "ConceptDirection",
    "DESIGN_TASK_KEYS",
    "ProductEngine",
    "ProductMode",
    "ProductResult",
    "ProductStatus",
    "SchemeAudit",
    "UnitMixRow",
]
