"""Assemble the twelve-unit truth model from engine outputs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable, Mapping

from dds.analysis_profile import resolve_intervention_profile
from dds.contracts import REPORT_UNITS, SECTION_REQUIREMENTS
from dds.customer import CustomerIntelligenceBundle
from dds.domain import (
    EvidenceRecord,
    EvidenceType,
    ProjectContext,
    ReportRun,
    ResolvedField,
    ResolvedStatus,
    SectionResult,
)
from dds.engines.market import MarketAnalysisResult, MarketReadiness
from dds.engines.absorption import OperatingSimulation
from dds.engines.project_cashflow import ProjectCashFlowResult
from dds.engines.scheme_comparison import SchemeComparisonResult
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

    def absorption_section(
        self,
        result: OperatingSimulation,
        *,
        evidence_refs_by_field: Mapping[str, Iterable[str]],
    ) -> SectionResult:
        required_fields = set(SECTION_REQUIREMENTS["VA2"])
        supplied_fields = set(evidence_refs_by_field)
        if supplied_fields != required_fields:
            raise ValueError(
                "VA2 evidence refs must exactly cover "
                f"{sorted(required_fields)}"
            )
        normalized_refs = {
            field: list(dict.fromkeys(str(item) for item in refs if str(item)))
            for field, refs in evidence_refs_by_field.items()
        }
        missing_refs = [
            field for field, refs in normalized_refs.items() if not refs
        ]
        if missing_refs:
            raise ValueError(f"VA2 fields require evidence refs: {missing_refs}")
        summaries = [
            {
                "strategy_id": item.strategy_id,
                "scenario_id": item.scenario_id,
                "absorption_3m": item.absorption_3m,
                "absorption_6m": item.absorption_6m,
                "absorption_12m": item.absorption_12m,
                "clearance_month": item.clearance_month,
                "realized_value_index": item.realized_value_index,
                "cash_npv_index": item.cash_npv_index,
                "ending_inventory": item.ending_inventory,
                "tail_risk_index": item.tail_risk_index,
                "risk_adjusted_npv_index": item.risk_adjusted_npv_index,
                "monetary_unit": item.monetary_unit,
                "model_version": item.model_version,
                "parameter_version": item.parameter_version,
                "seed": item.seed,
                "formula": item.formula,
            }
            for item in result.results
        ]
        data = {
            "sales_forecast": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "standard_inventory_units": 100,
                    "curves": summaries,
                    "evidence_type": "model_simulation",
                },
                evidence_refs=normalized_refs["sales_forecast"],
            ),
            "cash_flow": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "basis": "relative_index_base_100",
                    "curves": [
                        {
                            "strategy_id": item.strategy_id,
                            "scenario_id": item.scenario_id,
                            "realized_value_index": item.realized_value_index,
                            "cash_collection_index": item.cash_collection_index,
                            "cash_npv_index": item.cash_npv_index,
                        }
                        for item in result.results
                    ],
                    "absolute_currency_allowed": False,
                },
                evidence_refs=normalized_refs["cash_flow"],
            ),
            "investment_metrics": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "pareto_strategy_ids": list(result.pareto_strategy_ids),
                    "recommended_strategy_id": result.recommended_strategy_id,
                    "recommendation_rule": result.recommendation_rule,
                    "metric": "risk_adjusted_npv_index",
                    "absolute_investment_conclusion_allowed": False,
                },
                evidence_refs=normalized_refs["investment_metrics"],
            ),
        }
        all_refs = list(
            dict.fromkeys(
                ref
                for refs in normalized_refs.values()
                for ref in refs
            )
        )
        return SectionResult(
            section_id="VA2",
            data=data,
            conclusions=[
                (
                    "标准100套操盘沙盘已形成九条情景曲线；推荐仅针对"
                    "机会筛选范围，不构成项目投资或确定售罄承诺。"
                )
            ],
            evidence_refs=all_refs,
            assumptions=[
                "所有金额字段均为基准100相对指数，不是人民币。",
                "月度需求来自有版本、有证据的城市校准参数。",
            ],
            counter_evidence=[
                "实际客户池、供货和成交反馈偏离校准窗口时必须重新运行。"
            ],
            gaps=[],
            actions=[
                "如需约束协同，用户应另行确认并发起 Input 2 任务。"
            ],
            confidence=None,
            status=ResolvedStatus.RESOLVED,
        )

    def project_cashflow_section(
        self,
        result: ProjectCashFlowResult,
        *,
        evidence_refs_by_field: Mapping[str, Iterable[str]],
    ) -> SectionResult:
        required_fields = set(SECTION_REQUIREMENTS["VA2"])
        if set(evidence_refs_by_field) != required_fields:
            raise ValueError(
                "VA2 evidence refs must exactly cover "
                f"{sorted(required_fields)}"
            )
        refs = {
            field: list(dict.fromkeys(str(item) for item in values if str(item)))
            for field, values in evidence_refs_by_field.items()
        }
        missing = [field for field, values in refs.items() if not values]
        if missing:
            raise ValueError(f"VA2 fields require evidence refs: {missing}")
        monthly_sales = [
            {
                "month": item.month,
                "subscriptions": item.subscriptions,
                "cancellations": item.cancellations,
                "contracts": item.contracts,
                "contracted_sales_value_cny": item.contracted_sales_value_cny,
                "products": [
                    {
                        "product_id": product.product_id,
                        "released_units": product.released_units,
                        "subscriptions": product.subscriptions,
                        "cancellations": product.cancellations,
                        "contracts": product.contracts,
                        "closing_available_inventory": (
                            product.closing_available_inventory
                        ),
                        "closing_reserved_inventory": (
                            product.closing_reserved_inventory
                        ),
                    }
                    for product in item.products
                ],
            }
            for item in result.months
        ]
        monthly_cash = [
            {
                "month": item.month,
                "contracted_sales_value_cny": item.contracted_sales_value_cny,
                "cash_collections_cny": item.cash_collections_cny,
                "accounting_revenue_cny": item.accounting_revenue_cny,
                "land_cost_cny": item.land_cost_cny,
                "construction_cost_cny": item.construction_cost_cny,
                "design_incremental_cost_cny": (
                    item.design_incremental_cost_cny
                ),
                "marketing_cost_cny": item.marketing_cost_cny,
                "sales_tax_cny": item.sales_tax_cny,
                "financing_cost_cny": item.financing_cost_cny,
                "net_cash_flow_cny": item.net_cash_flow_cny,
                "cumulative_net_cash_cny": item.cumulative_net_cash_cny,
            }
            for item in result.months
        ]
        data = {
            "sales_forecast": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "basis": "sourced_project_inventory",
                    "monthly": monthly_sales,
                    "total_released_units": result.total_released_units,
                    "total_contracts": result.total_contracts,
                    "ending_available_inventory": (
                        result.ending_available_inventory
                    ),
                    "ending_reserved_inventory": (
                        result.ending_reserved_inventory
                    ),
                    "evidence_type": result.evidence_type,
                },
                evidence_refs=refs["sales_forecast"],
            ),
            "cash_flow": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "basis": "project_currency_cny",
                    "monthly": monthly_cash,
                    "contracted_sales_value_cny": (
                        result.contracted_sales_value_cny
                    ),
                    "cash_collections_cny": result.cash_collections_cny,
                    "accounting_revenue_cny": result.accounting_revenue_cny,
                    "formula": result.formula,
                },
                evidence_refs=refs["cash_flow"],
            ),
            "investment_metrics": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "net_cash_npv_cny": result.net_cash_npv_cny,
                    "peak_funding_requirement_cny": (
                        result.peak_funding_requirement_cny
                    ),
                    "parameter_version": result.parameter_version,
                    "model_version": result.model_version,
                    "investment_decision_allowed": False,
                },
                evidence_refs=refs["investment_metrics"],
            ),
        }
        all_refs = list(
            dict.fromkeys(
                ref for values in refs.values() for ref in values
            )
        )
        return SectionResult(
            section_id="VA2",
            data=data,
            conclusions=[
                "已形成基于真实货量的项目级去化与初步现金流压力测试。"
            ],
            evidence_refs=all_refs,
            assumptions=[
                "结果依赖当前批次、客户池、成本与回款时滞版本。",
                "成交货值、现金到账与会计收入按不同时间序列处理。",
            ],
            counter_evidence=[
                "法定条件、真实销售反馈或成本口径变化时结果必须失效重算。"
            ],
            gaps=[],
            actions=["用实际来访、认购、签约、退房和回款滚动校准。"],
            confidence=None,
            status=ResolvedStatus.RESOLVED,
        )

    def scheme_comparison_section(
        self,
        result: SchemeComparisonResult,
        *,
        evidence_refs_by_field: Mapping[str, Iterable[str]],
    ) -> SectionResult:
        required_fields = set(SECTION_REQUIREMENTS["VA2"])
        if set(evidence_refs_by_field) != required_fields:
            raise ValueError(
                "VA2 evidence refs must exactly cover "
                f"{sorted(required_fields)}"
            )
        refs = {
            field: list(dict.fromkeys(str(item) for item in values if str(item)))
            for field, values in evidence_refs_by_field.items()
        }
        missing = [field for field, values in refs.items() if not values]
        if missing:
            raise ValueError(f"VA2 fields require evidence refs: {missing}")
        schemes = [
            {
                "scheme_id": item.scheme_id,
                "feasible": item.feasible,
                "absorption_12m": item.absorption_12m,
                "realized_value_cny": item.realized_value_cny,
                "expected_cash_npv_cny": item.expected_cash_npv_cny,
                "cvar_cash_npv_cny": item.cvar_cash_npv_cny,
                "risk_adjusted_npv_cny": item.risk_adjusted_npv_cny,
                "peak_funding_cny": item.peak_funding_cny,
                "tail_inventory_rate": item.tail_inventory_rate,
                "planning_efficiency": item.planning_efficiency,
                "product_market_fit": item.product_market_fit,
                "implementation_complexity": (
                    item.implementation_complexity
                ),
                "hard_constraint_failures": list(
                    item.hard_constraint_failures
                ),
            }
            for item in result.metrics
        ]
        policy = {
            "policy_version": result.policy.policy_version,
            "cvar_alpha": result.policy.cvar_alpha,
            "risk_aversion_lambda": result.policy.risk_aversion_lambda,
            "objective_directions": dict(
                result.policy.objective_directions
            ),
        }
        data = {
            "sales_forecast": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "basis": "normalized_scheme_comparison",
                    "schemes": schemes,
                    "pareto_scheme_ids": list(result.pareto_scheme_ids),
                },
                evidence_refs=refs["sales_forecast"],
            ),
            "cash_flow": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "schemes": schemes,
                    "policy": policy,
                    "formula": result.formula,
                },
                evidence_refs=refs["cash_flow"],
            ),
            "investment_metrics": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value={
                    "pareto_scheme_ids": list(result.pareto_scheme_ids),
                    "recommended_scheme_id": (
                        result.recommended_scheme_id
                    ),
                    "eliminated_reasons": [
                        {"scheme_id": scheme_id, "reason": reason}
                        for scheme_id, reason in result.eliminated_reasons
                    ],
                    "no_recommendation_reason": (
                        result.no_recommendation_reason
                    ),
                    "policy": policy,
                },
                evidence_refs=refs["investment_metrics"],
            ),
        }
        all_refs = list(
            dict.fromkeys(
                ref for values in refs.values() for ref in values
            )
        )
        conclusion = (
            f"推荐方案 {result.recommended_scheme_id} 位于 Pareto 前沿。"
            if result.recommended_scheme_id
            else "所有方案均未通过硬约束，本轮不形成推荐。"
        )
        return SectionResult(
            section_id="VA2",
            data=data,
            conclusions=[conclusion],
            evidence_refs=all_refs,
            assumptions=[
                "目标方向、CVaR alpha、风险厌恶 lambda 与硬约束均已冻结。"
            ],
            counter_evidence=[
                "任一方案输入、成本、市场情景或硬约束变化均触发重新比较。"
            ],
            gaps=[],
            actions=["按真实销售反馈滚动校准方案情景与切换条件。"],
            confidence=None,
            status=ResolvedStatus.RESOLVED,
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
        absorption: OperatingSimulation | None = None,
        absorption_evidence_refs: Mapping[str, Iterable[str]] | None = None,
        project_cashflow: ProjectCashFlowResult | None = None,
        project_cashflow_evidence_refs: Mapping[
            str, Iterable[str]
        ] | None = None,
        scheme_comparison: SchemeComparisonResult | None = None,
        scheme_comparison_evidence_refs: Mapping[
            str, Iterable[str]
        ] | None = None,
        analysis_profile: Mapping[str, Any] | None = None,
        requirements: Iterable[Mapping[str, Any]] = (),
        customer_intelligence: CustomerIntelligenceBundle | None = None,
    ) -> ReportRun:
        sections = self.empty_sections()
        evidence_records = list(evidence)
        if market is not None:
            sections["SC2"] = self.market_section(market)
        if product is not None:
            sections.update(self.product_sections(product))
        if premium is not None:
            sections["VA1"] = self.premium_section(premium)
        if absorption is not None:
            sections["VA2"] = self.absorption_section(
                absorption,
                evidence_refs_by_field=absorption_evidence_refs or {},
            )
        if project_cashflow is not None:
            if absorption is not None:
                raise ValueError(
                    "standard absorption and project cash flow cannot both "
                    "populate VA2"
                )
            sections["VA2"] = self.project_cashflow_section(
                project_cashflow,
                evidence_refs_by_field=project_cashflow_evidence_refs or {},
            )
        if scheme_comparison is not None:
            if absorption is not None or project_cashflow is not None:
                raise ValueError(
                    "only one VA2 simulation result can be assembled"
                )
            sections["VA2"] = self.scheme_comparison_section(
                scheme_comparison,
                evidence_refs_by_field=(
                    scheme_comparison_evidence_refs or {}
                ),
            )
        customer_payload: dict[str, Any] | None = None
        if customer_intelligence is not None:
            if customer_intelligence.city != project_context.city:
                raise ValueError("customer intelligence city must match the project")
            if str(customer_intelligence.as_of) != str(project_context.base_date):
                raise ValueError("customer intelligence as_of must match the project base_date")
            if (
                customer_intelligence.district
                and project_context.district
                and customer_intelligence.district != project_context.district
            ):
                raise ValueError("customer intelligence district must match the project")
            evidence_by_id = {item.evidence_id: item for item in evidence_records}
            missing_bundle_refs = [
                ref
                for ref in customer_intelligence.evidence_refs
                if ref not in evidence_by_id
            ]
            if missing_bundle_refs:
                raise ValueError(
                    "customer intelligence references missing evidence: "
                    f"{missing_bundle_refs}"
                )
            segment_refs = tuple(
                dict.fromkeys(
                    ref
                    for segment in customer_intelligence.segments
                    for ref in segment.evidence_refs
                )
            )
            missing_refs = [ref for ref in segment_refs if ref not in evidence_by_id]
            if missing_refs:
                raise ValueError(
                    f"customer segments reference missing evidence: {missing_refs}"
                )
            allowed_segment_evidence = {
                EvidenceType.OBSERVED_FACT,
                EvidenceType.SOCIAL_OBSERVATION,
            }
            invalid_refs = [
                ref
                for ref in segment_refs
                if evidence_by_id[ref].evidence_type not in allowed_segment_evidence
            ]
            if invalid_refs:
                raise ValueError(
                    "SC2 customer segments require observed or social evidence: "
                    f"{invalid_refs}"
                )
            sc2 = sections["SC2"]
            sc2.data["customer_segments"] = ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=[item.to_dict() for item in customer_intelligence.segments],
                evidence_refs=list(segment_refs),
                assumptions=[
                    f"customer evidence level={customer_intelligence.evidence_level.name.lower()}",
                    "客群权重仅在冻结地域、时点和样本范围内有效。",
                ],
                confidence=None,
                reason="",
            )
            sc2.evidence_refs = list(dict.fromkeys(sc2.evidence_refs + list(segment_refs)))
            if sc2.status is ResolvedStatus.UNKNOWN:
                sc2.status = ResolvedStatus.PARTIAL
            customer_payload = customer_intelligence.to_dict()
            simulation_refs = list(customer_intelligence.evidence_refs)
            simulation_value = {
                "evidence_level": customer_payload["evidence_level"],
                "synthetic_cohort": customer_payload.get("synthetic_cohort"),
                "choice_simulation": customer_payload.get("choice_simulation"),
                "persona_experiment": customer_payload.get("persona_experiment"),
                "allowed_uses": customer_payload["allowed_uses"],
                "prohibited_uses": customer_payload["prohibited_uses"],
            }
            for section_id, field_name in (
                ("AD3", "customer_response"),
                ("VA3", "customer_risks"),
            ):
                sections[section_id].data[field_name] = ResolvedField(
                    status=ResolvedStatus.RESOLVED,
                    value=simulation_value,
                    evidence_refs=simulation_refs,
                    assumptions=[
                        "该字段是聚合模型模拟，不替代 SC2 真实客群证据。",
                        "choice share 不得直接换算月销量。",
                    ],
                    confidence=None,
                )
                sections[section_id].evidence_refs = list(
                    dict.fromkeys(
                        sections[section_id].evidence_refs + simulation_refs
                    )
                )
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
        if customer_payload is not None:
            cs_methods = sections["CS"].data["methods"]
            if isinstance(cs_methods, ResolvedField):
                cs_methods.value = list(cs_methods.value or []) + [
                    f"customer_intelligence={customer_payload['version']}",
                    (
                        "local_population_weighting="
                        f"{customer_payload['local_population']['weighting_method']}"
                    ),
                    "persona_results=aggregated_model_simulation",
                ]
                cs_methods.evidence_refs = list(
                    dict.fromkeys(
                        cs_methods.evidence_refs + customer_payload["evidence_refs"]
                    )
                )
            cs_assumptions = sections["CS"].data["assumptions"]
            if isinstance(cs_assumptions, ResolvedField):
                cs_assumptions.value = list(cs_assumptions.value or []) + [
                    f"禁止用途：{item}"
                    for item in customer_payload["prohibited_uses"]
                ]
            sections["CS"].evidence_refs = list(
                dict.fromkeys(
                    sections["CS"].evidence_refs
                    + customer_payload["evidence_refs"]
                )
            )
        profile = dict(analysis_profile or {})
        if profile and not profile.get("schema_version"):
            profile = resolve_intervention_profile(
                profile or project_context.extra.get("input_profile") or project_context.to_dict(),
                selected_mode=profile.get("selected_mode") if profile else None,
                confirmed=profile.get("mode_status") == "confirmed" if profile else None,
            )
        return ReportRun(
            run_id=run_id,
            project_context=project_context,
            sections=sections,
            evidence_records=evidence_records,
            requirements=list(requirements),
            status=ResolvedStatus.PARTIAL,
            gate_status={},
            metadata={
                "assembled_from_engine_results": True,
                **({
                    "analysis_profile": profile,
                    "decision_scope": profile["decision_scope"],
                    "profile_model_version": profile["model_version"],
                    "strategy_objective": "risk_adjusted_operating_value",
                } if profile else {}),
                **(
                    {"customer_intelligence": customer_payload}
                    if customer_payload is not None
                    else {}
                ),
            },
        )


__all__ = ["ReportService"]
