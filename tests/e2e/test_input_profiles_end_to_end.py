from __future__ import annotations

from decimal import Decimal
import json

import pytest

from dds.agents.base import AgentTask
from dds.agents.requirement import RequirementAgent
from dds.contracts import SECTION_REQUIREMENTS
from dds.domain import (
    EvidenceRecord,
    EvidenceType,
    ProjectContext,
    ResolvedField,
    ResolvedStatus,
    SectionResult,
)
from dds.engines.absorption import (
    MarketScenario,
    StandardAbsorptionEngine,
    StandardMarketParameters,
    StrategyCandidate,
)
from dds.engines.project_cashflow import (
    ProductBatch,
    ProjectCashFlowEngine,
    ProjectCostParameters,
    ProjectDemandParameters,
    ProjectSimulationAssumptions,
)
from dds.engines.scheme_comparison import (
    ComparisonPolicy,
    SchemeCandidate,
    SchemeComparisonEngine,
    SchemeScenarioMetrics,
)
from dds.engine.contract_enforcer import ContractEnforcer
from dds.reporting import compile_frozen_package, compute_report_document_hash
from dds.services.report_compiler_adapter import ReportCompilerAdapter
from dds.services.report_service import ReportService


PROFILE_CASES = (
    (
        1,
        {"address": "Test City Opportunity Road 1"},
        "opportunity_screening",
    ),
    (
        2,
        {
            "address": "Test City Constraint Road 2",
            "constraint_sources": [
                {
                    "source_ref": "client://planning-condition-v1",
                    "status": "verified",
                }
            ],
        },
        "constraint_driven_predevelopment",
    ),
    (
        3,
        {
            "address": "Test City Scheme Road 3",
            "constraint_sources": [
                {
                    "source_ref": "client://planning-condition-v1",
                    "status": "verified",
                }
            ],
            "core_development_boundaries_ready": True,
            "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
        },
        "scheme_selection",
    ),
)


def _resolved_section(
    section_id: str,
    evidence: list[EvidenceRecord],
) -> SectionResult:
    fields: dict[str, ResolvedField] = {}
    refs: list[str] = []
    for field_name in SECTION_REQUIREMENTS[section_id]:
        evidence_id = f"e-{section_id}-{field_name}"
        evidence.append(
            EvidenceRecord(
                evidence_id=evidence_id,
                metric_id=f"{section_id}.{field_name}",
                value=f"verified-{field_name}",
                source_id=f"source-{section_id}-{field_name}",
                source_ref=f"https://example.test/{evidence_id}",
                observed_at="2026-07-20",
                geography="Test City",
                method="fixture measurement",
                sample_size=100,
            )
        )
        refs.append(evidence_id)
        fields[field_name] = ResolvedField(
            status=ResolvedStatus.RESOLVED,
            value=f"verified-{field_name}",
            evidence_refs=[evidence_id],
        )
    return SectionResult(
        section_id=section_id,
        data=fields,
        conclusions=[f"{section_id} has an auditable conclusion."],
        evidence_refs=refs,
        status=ResolvedStatus.RESOLVED,
    )


def _resolved_cs(evidence: list[EvidenceRecord]) -> SectionResult:
    return SectionResult(
        section_id="CS",
        data={
            "sources": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=[item.source_ref for item in evidence],
            ),
            "methods": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=["fixture measurement"],
            ),
            "assumptions": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=[],
            ),
            "confidence_gaps": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=[],
            ),
            "data_gaps": ResolvedField(
                status=ResolvedStatus.RESOLVED,
                value=[],
            ),
        },
        conclusions=["Evidence, methods, and gaps are frozen."],
        evidence_refs=[item.evidence_id for item in evidence],
        status=ResolvedStatus.RESOLVED,
    )


@pytest.mark.parametrize(
    ("level", "input_profile", "decision_scope"),
    PROFILE_CASES,
)
@pytest.mark.asyncio
async def test_input_profile_full_truth_and_compiler_chain(
    level: int,
    input_profile: dict[str, object],
    decision_scope: str,
) -> None:
    requirement_result = await RequirementAgent().run(
        AgentTask(
            task_type="requirement_analysis",
            parameters={
                "requested_level": level,
                "input_profile": input_profile,
                "project_context": {
                    "project_id": f"input-{level}",
                    "project_name": f"Input {level} fixture",
                    "city": "Test City",
                    "base_date": "2026-07-23",
                },
            },
        )
    )
    assert requirement_result.success
    profile = requirement_result.data["analysis_profile"]
    assert profile["effective_level"] == level
    assert profile["decision_scope"] == decision_scope
    assert profile["eligible"] is True

    run = ReportService().assemble(
        run_id=f"input-{level}-run",
        project_context=ProjectContext(
            project_id=f"input-{level}",
            project_name=f"Input {level} fixture",
            city="Test City",
            base_date="2026-07-23",
        ),
        evidence=[],
        analysis_profile=profile,
        requirements=requirement_result.data["data_requirements"],
    )
    evidence: list[EvidenceRecord] = []
    for section_id in profile["required_units"]:
        if section_id != "CS" and section_id != "VA2":
            run.sections[section_id] = _resolved_section(section_id, evidence)
    if level == 1:
        strategies = (
            StrategyCandidate(
                "fast", "fast turnover", "1.3", "94", "1.5", "0.5", ("slow launch",)
            ),
            StrategyCandidate(
                "balanced", "balanced", "1", "104", "1", "1", ("weak conversion",)
            ),
            StrategyCandidate(
                "premium", "premium", "0.78", "122", "1.2", "2", ("weak visits",)
            ),
        )
        scenarios = (
            MarketScenario("conservative", "0.72", "0.96", "0.08"),
            MarketScenario("base", "1", "1", "0.04"),
            MarketScenario("optimistic", "1.25", "1.02", "0.02"),
        )
        simulation = StandardAbsorptionEngine(
            (
                StandardMarketParameters(
                    city="Test City",
                    version="test-market/1.0",
                    base_monthly_demand_per_100=6,
                    tail_decay_rate="0.35",
                    supporting_evidence_refs=("e-SC2-competitors",),
                    calibration_method="observed comparable absorption",
                ),
            )
        ).run_operating_lab("Test City", strategies, scenarios)
        va2_refs = {}
        for field_name in SECTION_REQUIREMENTS["VA2"]:
            evidence_id = f"e-VA2-{field_name}"
            evidence.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    metric_id=f"VA2.{field_name}",
                    value={
                        "model_version": simulation.results[0].model_version,
                        "recommended_strategy_id": simulation.recommended_strategy_id,
                    },
                    evidence_type=EvidenceType.MODEL_SIMULATION,
                    source_id=f"model-VA2-{field_name}",
                    source_ref=f"dds:model/VA2/{field_name}",
                    observed_at="2026-07-23",
                    geography="Test City",
                    method="deterministic standard-100 simulation",
                    sample_size=9,
                )
            )
            va2_refs[field_name] = [evidence_id]
        run.sections["VA2"] = ReportService().absorption_section(
            simulation,
            evidence_refs_by_field=va2_refs,
        )
    elif level == 2:
        project_result = ProjectCashFlowEngine().simulate(
            (
                ProductBatch(
                    "phase-1",
                    "main",
                    1,
                    120,
                    100,
                    20_000,
                    0.65,
                    ("e-AD3-product_mix",),
                ),
                ProductBatch(
                    "phase-2",
                    "value",
                    7,
                    80,
                    135,
                    24_000,
                    0.35,
                    ("e-AD3-product_mix",),
                ),
            ),
            ProjectDemandParameters(
                version="project-demand/1.0",
                initial_customer_pool=180,
                monthly_new_leads=45,
                visit_to_subscription_rate=0.18,
                subscription_to_contract_rate=0.86,
                monthly_pool_decay_rate=0.08,
                monthly_channel_capacity=18,
                seasonality=(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
                tail_decay_rate=0.45,
                evidence_refs=("e-SC2-customer_segments",),
            ),
            ProjectCostParameters(
                land_cost_cny=80_000_000,
                construction_cost_cny_m2=5_000,
                design_incremental_cost_cny=3_000_000,
                monthly_marketing_cost_cny=800_000,
                sales_tax_rate=0.05,
                monthly_financing_rate=0.006,
                evidence_refs=("e-SC3-engineering_constraints",),
            ),
            assumptions=ProjectSimulationAssumptions(horizon_months=30),
        )
        va2_refs = {}
        for field_name in SECTION_REQUIREMENTS["VA2"]:
            evidence_id = f"e-VA2-{field_name}"
            evidence.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    metric_id=f"VA2.{field_name}",
                    value={
                        "model_version": project_result.model_version,
                        "total_released_units": (
                            project_result.total_released_units
                        ),
                    },
                    evidence_type=EvidenceType.MODEL_SIMULATION,
                    source_id=f"model-VA2-{field_name}",
                    source_ref=f"dds:model/VA2/{field_name}",
                    observed_at="2026-07-23",
                    geography="Test City",
                    method="deterministic project cash flow simulation",
                    sample_size=30,
                )
            )
            va2_refs[field_name] = [evidence_id]
        run.sections["VA2"] = ReportService().project_cashflow_section(
            project_result,
            evidence_refs_by_field=va2_refs,
        )
    else:
        def scheme_outcomes(
            scheme_id: str,
            value_shift: int,
            npv_shift: int,
        ) -> tuple[SchemeScenarioMetrics, ...]:
            return (
                SchemeScenarioMetrics(
                    "conservative",
                    "0.25",
                    "0.48",
                    180_000_000 + value_shift,
                    20_000_000 + npv_shift,
                    100_000_000,
                    "0.32",
                    (f"outcome:{scheme_id}:conservative",),
                    "project-cashflow/1.0",
                ),
                SchemeScenarioMetrics(
                    "base",
                    "0.50",
                    "0.68",
                    220_000_000 + value_shift,
                    45_000_000 + npv_shift,
                    85_000_000,
                    "0.18",
                    (f"outcome:{scheme_id}:base",),
                    "project-cashflow/1.0",
                ),
                SchemeScenarioMetrics(
                    "optimistic",
                    "0.25",
                    "0.84",
                    250_000_000 + value_shift,
                    70_000_000 + npv_shift,
                    70_000_000,
                    "0.07",
                    (f"outcome:{scheme_id}:optimistic",),
                    "project-cashflow/1.0",
                ),
            )

        comparison = SchemeComparisonEngine().compare(
            (
                SchemeCandidate(
                    "A",
                    "0.86",
                    "0.84",
                    "0.42",
                    True,
                    (),
                    scheme_outcomes("A", 10_000_000, 8_000_000),
                    ("e-AD1-option_1",),
                ),
                SchemeCandidate(
                    "B",
                    "0.82",
                    "0.80",
                    "0.48",
                    True,
                    (),
                    scheme_outcomes("B", 0, 0),
                    ("e-AD1-option_2",),
                ),
            ),
            policy=ComparisonPolicy(
                cvar_alpha="0.25",
                risk_aversion_lambda="0.5",
            ),
        )
        va2_refs = {}
        for field_name in SECTION_REQUIREMENTS["VA2"]:
            evidence_id = f"e-VA2-{field_name}"
            evidence.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    metric_id=f"VA2.{field_name}",
                    value={
                        "policy_version": comparison.policy.policy_version,
                        "recommended_scheme_id": (
                            comparison.recommended_scheme_id
                        ),
                    },
                    evidence_type=EvidenceType.MODEL_SIMULATION,
                    source_id=f"model-VA2-{field_name}",
                    source_ref=f"dds:model/VA2/{field_name}",
                    observed_at="2026-07-23",
                    geography="Test City",
                    method="deterministic Pareto scheme comparison",
                    sample_size=2,
                )
            )
            va2_refs[field_name] = [evidence_id]
        run.sections["VA2"] = ReportService().scheme_comparison_section(
            comparison,
            evidence_refs_by_field=va2_refs,
        )
    run.sections["CS"] = _resolved_cs(evidence)
    run.evidence_records = evidence
    run.evidence = evidence
    run.status = ResolvedStatus.RESOLVED

    validation = ContractEnforcer().validate_report(run)
    assert validation.valid, validation.errors
    assert validation.delivery_ready is True
    assert set(validation.section_confidences) == set(profile["required_units"])

    adapter = ReportCompilerAdapter()
    first_package = adapter.build_frozen_compiler_package(run)
    second_package = adapter.build_frozen_compiler_package(run)
    assert first_package == second_package
    first_document = compile_frozen_package(first_package)
    second_document = compile_frozen_package(second_package)
    assert compute_report_document_hash(first_document) == compute_report_document_hash(
        second_document
    )
    assert first_document["qa"]["required_units"] == profile["required_units"]
    assert first_document["qa"]["missing_required_units"] == []
    assert first_document["qa"]["delivery_ready"] is True
    assert {
        page["section_id"] for page in first_document["page_manifest"]
    } == set(profile["required_units"])
    assert first_document["meta"]["analysis_profile"]["effective_level"] == level
    assert first_document["meta"]["decision_scope"] == decision_scope
    assert "??" not in json.dumps(first_document, ensure_ascii=False)

    if level == 1:
        authority = set(profile["quantitative_authority"])
        assert "absolute_project_revenue" not in authority
        assert "project_cash_flow" not in authority
        assert "risk_adjusted_npv" not in authority
        va2_page = next(
            page
            for page in first_document["page_manifest"]
            if page["section_id"] == "VA2"
        )
        strategy_table = next(
            block for block in va2_page["blocks"] if block["type"] == "table"
        )
        assert len(strategy_table["rows"]) == 9
        assert "CNY" not in str(strategy_table)
    if level == 2:
        va2_pages = [
            page
            for page in first_document["page_manifest"]
            if page["section_id"] == "VA2"
        ]
        monthly_rows = [
            row
            for page in va2_pages
            for block in page["blocks"]
            if block["type"] == "table"
            for row in block["rows"]
        ]
        assert len(monthly_rows) == 30
        assert Decimal(monthly_rows[0]["contracted_value_cny"]) >= 0
    if level == 3:
        va2_page = next(
            page
            for page in first_document["page_manifest"]
            if page["section_id"] == "VA2"
        )
        comparison_table = next(
            block for block in va2_page["blocks"] if block["type"] == "table"
        )
        assert len(comparison_table["rows"]) == 2
        assert comparison_table["rows"][0]["scheme"] == "A"
        assert comparison_table["rows"][0]["feasible"] is True
