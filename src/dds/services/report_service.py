"""Assemble the twelve-unit truth model from engine outputs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from dds.contracts import REPORT_UNITS, SECTION_REQUIREMENTS
from dds.domain import (
    EvidenceRecord,
    ProjectContext,
    ReportRun,
    ResolvedField,
    ResolvedStatus,
    SectionResult,
)
from dds.engines.market import MarketAnalysisResult, MarketReadiness
from dds.engines.premium import PremiumResult, PremiumStatus
from dds.engines.product import (
    DESIGN_TASK_KEYS,
    ProductMode,
    ProductResult,
    ProductStatus,
)


def _unknown(reason: str) -> ResolvedField:
    return ResolvedField(
        status=ResolvedStatus.UNKNOWN,
        value=None,
        evidence_refs=[],
        assumptions=[],
        confidence=None,
        reason=reason,
    )


def _not_applicable(reason: str) -> ResolvedField:
    return ResolvedField(
        status=ResolvedStatus.NOT_APPLICABLE,
        value=None,
        evidence_refs=[],
        assumptions=[],
        confidence=None,
        reason=reason,
    )


def _product_status(result: ProductResult) -> ResolvedStatus:
    if result.status is ProductStatus.READY:
        return ResolvedStatus.RESOLVED
    if result.status is ProductStatus.CONCEPT_ONLY:
        return ResolvedStatus.PARTIAL
    return ResolvedStatus.UNKNOWN


def _product_field(
    result: ProductResult,
    *,
    value: object,
    status: ResolvedStatus,
    reason: str = "",
) -> ResolvedField:
    return ResolvedField(
        status=status,
        value=value,
        evidence_refs=list(result.evidence_refs),
        assumptions=list(result.assumptions),
        confidence=None,
        reason=reason,
    )


class ReportService:
    """Build structure and visible gaps; it never authors new evidence or numbers."""

    def empty_sections(self) -> dict[str, SectionResult]:
        sections: dict[str, SectionResult] = {}
        for unit in REPORT_UNITS:
            section_id = unit["section_id"]
            gap = unit["gap"]
            sections[section_id] = SectionResult(
                section_id=section_id,
                data={field: _unknown(gap) for field in SECTION_REQUIREMENTS[section_id]},
                conclusions=[],
                evidence_refs=[],
                assumptions=[],
                counter_evidence=[],
                gaps=[gap],
                actions=[f"补齐 {unit['title']} 所需证据并重新运行质量门。"],
                confidence=None,
                status=ResolvedStatus.UNKNOWN,
            )
        return sections

    def market_section(self, result: MarketAnalysisResult) -> SectionResult:
        resolved_status = (
            ResolvedStatus.RESOLVED
            if result.readiness is MarketReadiness.READY
            else ResolvedStatus.PARTIAL
            if result.readiness is MarketReadiness.DEGRADED
            else ResolvedStatus.UNKNOWN
        )
        competitor_value = [asdict(item) for item in result.selected]
        fields = {
            "macro_indicators": _unknown("本轮纵向切片未采集宏观指标。"),
            "competitors": ResolvedField(
                status=resolved_status,
                value=competitor_value if competitor_value else None,
                evidence_refs=list(result.evidence_refs),
                assumptions=list(result.assumptions),
                confidence=None,
                reason="; ".join(result.gaps),
            ),
            "customer_segments": _unknown("尚未接入真实客户研究证据。"),
            "positive_cases": _unknown("尚未完成设计标杆的独立证据建模。"),
            "negative_cases": _unknown("尚未完成反案例的独立证据建模。"),
        }
        return SectionResult(
            section_id="SC2",
            data=fields,
            conclusions=list(result.conclusions),
            evidence_refs=list(result.evidence_refs),
            assumptions=list(result.assumptions),
            counter_evidence=list(result.counter_evidence),
            gaps=list(result.gaps),
            actions=list(result.actions),
            confidence=None,
            status=ResolvedStatus.PARTIAL,
        )

    @staticmethod
    def _concept_payload(concept) -> dict[str, object]:
        return {
            "direction_id": concept.direction_id,
            "positioning": concept.positioning,
            "target_customer": concept.target_customer,
            "value_proposition": concept.value_proposition,
            "design_focus": list(concept.design_brief),
            "switch_trigger": concept.switch_trigger,
            "evidence_type": concept.evidence_type,
        }

    def _product_ad1(self, result: ProductResult) -> SectionResult:
        data: dict[str, ResolvedField]
        section_status = _product_status(result)
        if result.mode is ProductMode.PROPOSE_CONCEPT_SCHEME:
            concept_status = (
                ResolvedStatus.RESOLVED
                if result.status is ProductStatus.READY
                else ResolvedStatus.PARTIAL
            )
            concepts_by_id = {item.direction_id: item for item in result.concepts}
            data = {}
            for index, direction_id in enumerate(("A", "B", "C"), start=1):
                concept = concepts_by_id.get(direction_id)
                data[f"option_{index}"] = (
                    _product_field(
                        result,
                        value=self._concept_payload(concept),
                        status=concept_status,
                        reason="; ".join(result.gaps),
                    )
                    if concept is not None
                    else _unknown(f"缺少方向 {direction_id} 的概念输入。")
                )
            matrix_rows = [self._concept_payload(item) for item in result.concepts]
            data["comparison_matrix"] = _product_field(
                result,
                value={
                    "dimensions": list(result.comparison_dimensions),
                    "rows": matrix_rows,
                    "evidence_type": "analysis_inference",
                },
                status=concept_status,
                reason="; ".join(result.gaps),
            )
        else:
            audit_status = _product_status(result)
            if result.audit is None:
                option_1 = _unknown("缺少甲方既有方案，无法审计。")
                comparison_matrix = _unknown("没有可进入比较矩阵的既有方案。")
            else:
                option_1 = _product_field(
                    result,
                    value={
                        "option_id": "existing_scheme",
                        "mode": result.mode.value,
                        "audit": asdict(result.audit),
                        "evidence_type": "analysis_inference",
                    },
                    status=audit_status,
                    reason="; ".join(result.gaps),
                )
                comparison_matrix = _product_field(
                    result,
                    value={
                        "dimensions": list(result.comparison_dimensions),
                        "rows": [
                            {
                                "option_id": "existing_scheme",
                                "reconciled": result.audit.reconciled,
                                "reconciliation_errors": dict(
                                    result.audit.reconciliation_errors
                                ),
                                "statutory_review_boundary": (
                                    "已完成法定条件输入检查"
                                    if result.status is ProductStatus.READY
                                    else "法定条件或证据未完成，只保留审计结论"
                                ),
                                "evidence_type": "analysis_inference",
                            }
                        ],
                        "evidence_type": "analysis_inference",
                    },
                    status=audit_status,
                    reason="; ".join(result.gaps),
                )
            data = {
                "option_1": option_1,
                "option_2": _not_applicable("audit_existing_scheme 模式不生成虚构备选方案。"),
                "option_3": _not_applicable("audit_existing_scheme 模式不生成虚构备选方案。"),
                "comparison_matrix": comparison_matrix,
            }
        return SectionResult(
            section_id="AD1",
            data=data,
            conclusions=list(result.conclusions),
            evidence_refs=list(result.evidence_refs),
            assumptions=list(result.assumptions),
            counter_evidence=list(result.counter_evidence),
            gaps=list(result.gaps),
            actions=list(result.actions),
            confidence=None,
            status=section_status,
        )

    def _product_ad2(self, result: ProductResult) -> SectionResult:
        section_status = _product_status(result)
        if result.mode is ProductMode.AUDIT_EXISTING_SCHEME:
            threshold_status = (
                ResolvedStatus.RESOLVED
                if result.status is ProductStatus.READY
                else ResolvedStatus.PARTIAL
                if result.audit is not None
                else ResolvedStatus.UNKNOWN
            )
            thresholds = (
                {
                    "reconciliation_tolerance": result.audit.tolerance,
                    "reconciled": result.audit.reconciled,
                    "rule": "套数、占比、面积和货值任一误差越界即退回修正",
                    "evidence_type": "analysis_inference",
                }
                if result.audit is not None
                else None
            )
            data = {
                "recommended_option": _not_applicable(
                    "audit_existing_scheme 模式只审计既有方案，不进行三方案推荐。"
                ),
                "elimination_reasons": _not_applicable(
                    "没有生成另外两个方案，因此不存在淘汰结论。"
                ),
                "validation_thresholds": _product_field(
                    result,
                    value=thresholds,
                    status=threshold_status,
                    reason="; ".join(result.gaps),
                )
                if thresholds is not None
                else _unknown("缺少既有方案输入，无法建立审计阈值。"),
            }
        else:
            decision_status = (
                ResolvedStatus.RESOLVED
                if result.status is ProductStatus.READY
                else ResolvedStatus.PARTIAL
            )
            concepts_by_id = {item.direction_id: item for item in result.concepts}
            recommendation = concepts_by_id.get(result.recommended_direction_id or "")
            if recommendation is None or not result.evidence_refs:
                recommended_field = _unknown(
                    "缺少支持方案选择的市场 EvidenceRecord，recommended_option 保持 unknown。"
                )
                elimination_field = _unknown(
                    "没有证据化推荐，任何方案均不得被标记为淘汰。"
                )
            else:
                recommended_field = _product_field(
                    result,
                    value={
                        "direction_id": recommendation.direction_id,
                        "positioning": recommendation.positioning,
                        "selection_boundary": (
                            "当前选择输入，须继续接受成本、支付意愿与法定条件验证"
                        ),
                        "evidence_type": "analysis_inference",
                    },
                    status=decision_status,
                    reason="; ".join(result.gaps),
                )
                elimination_field = _product_field(
                    result,
                    value=[
                        {
                            "direction_id": item.direction_id,
                            "decision": (
                                "recommended"
                                if item.direction_id == recommendation.direction_id
                                else "not_selected_not_eliminated"
                            ),
                            "reason": result.elimination_reasons.get(
                                item.direction_id,
                                "没有证据化淘汰理由。",
                            ),
                            "evidence_type": "analysis_inference",
                        }
                        for item in result.concepts
                    ],
                    status=decision_status,
                    reason="; ".join(result.gaps),
                )
            threshold_status = (
                ResolvedStatus.RESOLVED
                if result.status is ProductStatus.READY
                else ResolvedStatus.PARTIAL
            )
            data = {
                "recommended_option": recommended_field,
                "elimination_reasons": elimination_field,
                "validation_thresholds": _product_field(
                    result,
                    value=[
                        {
                            "direction_id": item.direction_id,
                            "switch_trigger": item.switch_trigger,
                            "evidence_type": "analysis_inference",
                        }
                        for item in result.concepts
                    ],
                    status=threshold_status,
                    reason="; ".join(result.gaps),
                ),
            }
        return SectionResult(
            section_id="AD2",
            data=data,
            conclusions=list(result.conclusions),
            evidence_refs=list(result.evidence_refs),
            assumptions=list(result.assumptions),
            counter_evidence=list(result.counter_evidence),
            gaps=list(result.gaps),
            actions=list(result.actions),
            confidence=None,
            status=section_status,
        )

    def _product_ad3(self, result: ProductResult) -> SectionResult:
        status = _product_status(result)
        if result.mode is ProductMode.AUDIT_EXISTING_SCHEME and result.audit is not None:
            product_mix: object | None = dict(result.audit.computed_shares)
            area_segments: object | None = {
                "total_units": result.audit.total_units,
                "saleable_area_m2": result.audit.saleable_area_m2,
            }
            price_bands: object | None = (
                {"saleable_value_cny": result.audit.saleable_value_cny}
                if result.audit.saleable_value_cny is not None
                else None
            )
            data = {
                "product_mix": _product_field(
                    result,
                    value=product_mix,
                    status=status,
                    reason="; ".join(result.gaps),
                ),
                "area_segments": _product_field(
                    result,
                    value=area_segments,
                    status=status,
                    reason="; ".join(result.gaps),
                ),
                "price_bands": _product_field(
                    result,
                    value=price_bands,
                    status=status if price_bands is not None else ResolvedStatus.UNKNOWN,
                    reason=(
                        "缺少有来源的产品价格输入"
                        if price_bands is None
                        else "; ".join(result.gaps)
                    ),
                ),
                "sales_rhythm": _unknown("尚未接入有来源的去化和首开节奏输入。"),
            }
        else:
            data = {
                "product_mix": _unknown(
                    "方向性概念不能替代有来源的户配、套数与占比输入。"
                ),
                "area_segments": _unknown(
                    "缺少容量、户配和法定条件证据，不输出面积段或套数。"
                ),
                "price_bands": _unknown(
                    "缺少价格与货值证据，不从概念方案推导任何数值。"
                ),
                "sales_rhythm": _unknown("尚未接入有来源的去化和首开节奏输入。"),
            }
        ad3_gaps = list(result.gaps)
        if result.mode is ProductMode.PROPOSE_CONCEPT_SCHEME:
            ad3_gaps.append("方向性概念不能替代有证据的户配、面积、价格和去化输入。")
        ad3_gaps.append("缺少有来源的去化和首开节奏输入。")
        return SectionResult(
            section_id="AD3",
            data=data,
            conclusions=list(result.conclusions),
            evidence_refs=list(result.evidence_refs),
            assumptions=list(result.assumptions),
            counter_evidence=list(result.counter_evidence),
            gaps=list(dict.fromkeys(ad3_gaps)),
            actions=list(
                dict.fromkeys(
                    [*result.actions, "补充户配、价格、货值与去化节奏的独立证据。"]
                )
            ),
            confidence=None,
            status=(
                ResolvedStatus.UNKNOWN
                if result.status is ProductStatus.BLOCKED
                else ResolvedStatus.PARTIAL
            ),
        )

    def _product_ad4(self, result: ProductResult) -> SectionResult:
        if result.mode is ProductMode.PROPOSE_CONCEPT_SCHEME:
            task_status = (
                ResolvedStatus.RESOLVED
                if result.status is ProductStatus.READY
                else ResolvedStatus.PARTIAL
            )
            data = {
                task_key: _product_field(
                    result,
                    value=[
                        {
                            "direction_id": item.direction_id,
                            "positioning": item.positioning,
                            "tasks": list(item.design_tasks.get(task_key, ())),
                            "boundary": "concept_only_no_ratio_geometry",
                            "evidence_type": item.evidence_type,
                        }
                        for item in result.concepts
                    ],
                    status=task_status,
                    reason="; ".join(result.gaps),
                )
                for task_key in DESIGN_TASK_KEYS
            }
            gaps = list(result.gaps)
            actions = list(result.actions)
            section_status = _product_status(result)
        else:
            reason = "既有方案输入仅包含户配汇总，未提供对应设计资料，不能伪造概念任务书。"
            data = {task_key: _unknown(reason) for task_key in DESIGN_TASK_KEYS}
            gaps = [*result.gaps, reason]
            actions = [
                *result.actions,
                "提供既有总图、户型、立面、景观和示范区资料后执行 AD4 审计。",
            ]
            section_status = ResolvedStatus.UNKNOWN
        return SectionResult(
            section_id="AD4",
            data=data,
            conclusions=list(result.conclusions),
            evidence_refs=list(result.evidence_refs),
            assumptions=[*result.assumptions, "所有设计判断均为 analysis_inference。"],
            counter_evidence=list(result.counter_evidence),
            gaps=list(dict.fromkeys(gaps)),
            actions=list(dict.fromkeys(actions)),
            confidence=None,
            status=section_status,
        )

    def product_sections(self, result: ProductResult) -> dict[str, SectionResult]:
        """Map one product-engine result onto all four AD decision units."""

        return {
            "AD1": self._product_ad1(result),
            "AD2": self._product_ad2(result),
            "AD3": self._product_ad3(result),
            "AD4": self._product_ad4(result),
        }

    def product_section(self, result: ProductResult) -> SectionResult:
        """Compatibility entry point retained for callers that requested AD3 only."""

        return self._product_ad3(result)

    def premium_section(self, result: PremiumResult) -> SectionResult:
        assessable = result.status is PremiumStatus.ASSESSABLE
        status = ResolvedStatus.RESOLVED if assessable else ResolvedStatus.UNKNOWN
        scenarios = [asdict(item) for item in result.scenarios] if assessable else None
        data = {
            "premium_factors": ResolvedField(
                status=status,
                value=scenarios,
                evidence_refs=list(result.evidence_refs),
                assumptions=list(result.assumptions),
                reason="; ".join(result.gaps),
            ),
            "cost_value_chain": ResolvedField(
                status=status,
                value=scenarios,
                evidence_refs=list(result.evidence_refs),
                assumptions=list(result.assumptions),
                reason="; ".join(result.gaps),
            ),
        }
        return SectionResult(
            section_id="VA1",
            data=data,
            conclusions=(
                ["已形成保守、基准、乐观三种可复算溢价场景。"]
                if assessable
                else ["输入不足，VA1 保持 not_assessable，未产生任何溢价数字。"]
            ),
            evidence_refs=list(result.evidence_refs),
            assumptions=list(result.assumptions),
            counter_evidence=list(result.counter_evidence),
            gaps=list(result.gaps),
            actions=list(result.actions),
            confidence=None,
            status=status,
        )

    def assemble(
        self,
        *,
        run_id: str,
        project_context: ProjectContext,
        evidence: Iterable[EvidenceRecord],
        market: MarketAnalysisResult | None = None,
        product: ProductResult | None = None,
        premium: PremiumResult | None = None,
    ) -> ReportRun:
        sections = self.empty_sections()
        if market is not None:
            sections["SC2"] = self.market_section(market)
        if product is not None:
            sections.update(self.product_sections(product))
        if premium is not None:
            sections["VA1"] = self.premium_section(premium)
        evidence_records = list(evidence)
        sections["CS"] = SectionResult(
            section_id="CS",
            data={
                "sources": ResolvedField(
                    status=ResolvedStatus.RESOLVED if evidence_records else ResolvedStatus.UNKNOWN,
                    value=[item.source_ref for item in evidence_records] or None,
                    evidence_refs=[item.evidence_id for item in evidence_records],
                    reason="没有冻结证据" if not evidence_records else "",
                ),
                "methods": ResolvedField(
                    status=ResolvedStatus.RESOLVED if evidence_records else ResolvedStatus.UNKNOWN,
                    value=[item.method for item in evidence_records] or None,
                    evidence_refs=[item.evidence_id for item in evidence_records],
                ),
                "assumptions": ResolvedField(
                    status=ResolvedStatus.RESOLVED,
                    value=list(project_context.assumptions),
                    evidence_refs=[],
                ),
                "confidence_gaps": ResolvedField(
                    status=ResolvedStatus.RESOLVED,
                    value=[gap for section in sections.values() for gap in section.gaps],
                    evidence_refs=[],
                ),
                "data_gaps": ResolvedField(
                    status=ResolvedStatus.RESOLVED,
                    value=[gap for section in sections.values() for gap in section.gaps],
                    evidence_refs=[],
                ),
            },
            conclusions=["来源、方法、缺口与补证动作均保留在结构化运行对象中。"],
            evidence_refs=[item.evidence_id for item in evidence_records],
            assumptions=list(project_context.assumptions),
            counter_evidence=[],
            gaps=[],
            actions=[],
            confidence=None,
            status=ResolvedStatus.PARTIAL,
        )
        return ReportRun(
            run_id=run_id,
            project_context=project_context,
            sections=sections,
            evidence_records=evidence_records,
            requirements=[],
            status=ResolvedStatus.PARTIAL,
            gate_status={},
            metadata={"assembled_from_engine_results": True},
        )


__all__ = ["ReportService"]
