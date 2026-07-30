"""End-to-end boundary tests for the evidence-first agent workflow."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from dds.agents.base import AgentResult, AgentStatus, AgentTask
from dds.agents.content_writer import ContentWriterAgent
from dds.agents.evidence_first import (
    TASK_EVIDENCE_FIRST_REPORT,
    EvidenceFirstOrchestratorAgent,
    EvidenceFirstWorkflow,
)
from dds.agents.requirement import RequirementAgent
from dds.agents.research_planner import ResearchPlannerAgent
from dds.api.runs import RunStatus, RunStore
from dds.contracts import SECTION_REQUIREMENTS
from dds.domain import DataRequirement, EvidenceRecord, ResolvedStatus, SectionResult
from dds.engines.resolver import EvidenceResolver


PROJECT_CONTEXT = {
    "project_id": "P-EVIDENCE-FIRST",
    "project_name": "Evidence First Fixture",
    "city": "Wuhan",
    "project_type": "residential",
    "base_date": "2026-07-22",
}


@pytest.mark.asyncio
async def test_requirement_agent_graph_is_complete_ordered_and_field_scoped():
    result = await RequirementAgent().run(
        AgentTask(
            task_type="requirement_analysis",
            parameters={"project_context": PROJECT_CONTEXT},
        )
    )

    assert result.success
    graph = result.data["data_requirement_graph"]
    assert graph["graph_id"] == "dds-evidence-requirements-v1"
    assert graph["section_order"] == ["SC2", "AD1", "AD2", "AD3", "AD4", "VA1"]
    assert graph["edges"] == [
        {
            "from_section": source,
            "to_section": target,
            "constraint": "requires_upstream_section_result",
        }
        for source, target in zip(graph["section_order"], graph["section_order"][1:])
    ]

    nodes = graph["nodes"]
    assert len(nodes) == len({item["metric_id"] for item in nodes})
    for section_id in graph["section_order"]:
        section_nodes = [item for item in nodes if item["section_id"] == section_id]
        assert [item["field_name"] for item in section_nodes] == list(
            SECTION_REQUIREMENTS[section_id]
        )
        assert all(item["geography"] == "Wuhan" for item in section_nodes)
        assert all(item["as_of"] == "2026-07-22" for item in section_nodes)
        assert all(
            item["metadata"]["time_window"]["lookback_days"] > 0
            for item in section_nodes
        )

    competitors = next(item for item in nodes if item["metric_id"] == "SC2.competitors")
    assert competitors["min_evidence_count"] == 5
    assert competitors["metadata"]["minimum_sample_size"] == 5
    assert competitors["evidence_types"] == ["observed_fact"]
    assert "social_observation" not in competitors["evidence_types"]

    customer_segments = next(
        item for item in nodes if item["metric_id"] == "SC2.customer_segments"
    )
    assert customer_segments["evidence_types"] == [
        "observed_fact",
        "social_observation",
    ]

    future_demand = next(
        item
        for item in nodes
        if item["metric_id"] == "SC2.future_demand_event_scan"
    )
    assert future_demand["required"] is True
    assert future_demand["min_evidence_count"] == 2
    assert future_demand["max_age_days"] == 730
    assert future_demand["evidence_types"] == [
        "observed_fact",
        "analysis_inference",
    ]
    event_scan = future_demand["metadata"]["future_event_scan"]
    assert future_demand["metadata"]["allowed_source_roles"] == [
        "government_record",
        "official_planning_document",
        "statutory_document",
        "corporate_official",
        "verified_first_party_document",
        "documented_analysis",
        "public_web_candidate",
        "web_search_candidate",
    ]
    assert event_scan["minimum_observed_evidence_count"] == 1
    assert event_scan["requires_counter_factors"] is True
    assert event_scan["must_separate"] == [
        "current_observed_population",
        "planned_capacity",
        "addressable_customer_hypothesis",
    ]
    assert "major_employer_or_headquarters" in event_scan["event_classes"]

    for field_name in SECTION_REQUIREMENTS["AD4"]:
        ad4_field = next(
            item for item in nodes if item["metric_id"] == f"AD4.{field_name}"
        )
        assert "analysis_inference" in ad4_field["evidence_types"]
        assert "model_simulation" not in ad4_field["evidence_types"]


class _SpyRequirement(RequirementAgent):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self.trace = trace

    async def execute(self, task: AgentTask) -> AgentResult:
        self.trace.append("requirement")
        return await super().execute(task)


class _SpyPlanner(ResearchPlannerAgent):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self.trace = trace

    async def execute(self, task: AgentTask) -> AgentResult:
        self.trace.append("research_planner")
        return await super().execute(task)


class _CompleteCollector:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def collect(
        self,
        *,
        research_plan: dict[str, Any],
        requirements: list[DataRequirement],
        project_context: Any,
        existing_evidence: list[EvidenceRecord],
    ) -> list[EvidenceRecord]:
        self.trace.append("collector")
        assert research_plan["collection_performed"] is False
        assert project_context.city == "Wuhan"
        assert existing_evidence == []
        records = []
        for requirement in requirements:
            for index in range(max(1, requirement.min_evidence_count)):
                records.append(
                    EvidenceRecord(
                        evidence_id=f"{requirement.metric_id}-{index}",
                        metric_id=requirement.metric_id,
                        value=120,
                        unit=requirement.unit,
                        evidence_type="observed_fact",
                        source_id="e2e-fixture",
                        source_ref=f"fixture://{requirement.metric_id}/{index}",
                        source_hash=f"sha256:{requirement.metric_id}:{index}",
                        observed_at=date(2026, 7, 22),
                        geography="Wuhan",
                        method="deterministic e2e fixture",
                        sample_size=20,
                    )
                )
        return records


class _SpyResolver(EvidenceResolver):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self.trace = trace

    def resolve_many(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.trace.append("resolver")
        return super().resolve_many(*args, **kwargs)


class _OneSectionAssembler:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def assemble(
        self,
        *,
        resolutions: dict[str, Any],
        evidence_records: list[EvidenceRecord],
        requirements: list[DataRequirement],
        project_context: Any,
    ) -> list[SectionResult]:
        self.trace.append("section_assembler")
        assert evidence_records
        assert requirements
        assert project_context.city == "Wuhan"
        resolution = resolutions["SC2.competitors"]
        field = resolution.to_resolved_field()
        return [
            SectionResult(
                section_id="SC2",
                data={"competitors": field},
                conclusions=["The recommendation uses only resolved evidence."],
                evidence_refs=list(resolution.evidence_refs),
                status=ResolvedStatus.RESOLVED,
            )
        ]


class _SpyWriter(ContentWriterAgent):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self.trace = trace
        self.numeric_claims: list[str] = []

    async def execute(self, task: AgentTask) -> AgentResult:
        self.trace.append("content_writer")
        result = await super().execute(task)
        self.numeric_claims = list(result.data.get("numeric_claims", []))
        return result


@pytest.mark.asyncio
async def test_workflow_order_idempotency_and_writer_numeric_boundary(tmp_path):
    trace: list[str] = []
    writer = _SpyWriter(trace)
    store = RunStore(tmp_path / "runs")
    workflow = EvidenceFirstWorkflow(
        requirement_agent=_SpyRequirement(trace),
        research_planner=_SpyPlanner(trace),
        collector=_CompleteCollector(trace),
        resolver=_SpyResolver(trace),
        section_assembler=_OneSectionAssembler(trace),
        content_writer=writer,
        run_store=store,
    )
    agent = EvidenceFirstOrchestratorAgent(workflow)
    parameters = {
        "idempotency_key": "e2e-evidence-first-success",
        "project_context": PROJECT_CONTEXT,
        # The workflow never forwards arbitrary drafts to the writer.
        "draft": "Invent an unsupported value of 999.",
    }

    first = await agent.run(
        AgentTask(task_type=TASK_EVIDENCE_FIRST_REPORT, parameters=parameters)
    )

    assert first.success
    assert first.status is AgentStatus.COMPLETED
    assert first.data["workflow_status"] == "completed"
    assert first.data["stage_order"] == [
        "requirement",
        "research_planner",
        "collector",
        "resolver",
        "section_assembler",
        "content_writer",
    ]
    assert trace == first.data["stage_order"]
    assert writer.numeric_claims == ["120"]
    assert "120" in first.data["content"]["SC2"]
    assert "999" not in first.data["content"]["SC2"]

    before_replay = list(trace)
    second = await agent.run(
        AgentTask(task_type=TASK_EVIDENCE_FIRST_REPORT, parameters=parameters)
    )
    assert second.success
    assert second.data["idempotent_replay"] is True
    assert second.data["run_id"] == first.data["run_id"]
    assert trace == before_replay

    record = store.load(first.data["run_id"])
    assert record is not None
    assert record.status is RunStatus.COMPLETED
    assert record.checkpoint == "completed"
    assert record.attempts == 1


class _NeverAssembler:
    def __init__(self) -> None:
        self.called = False

    def assemble(self, **kwargs: Any) -> list[SectionResult]:
        self.called = True
        raise AssertionError("assembler must not run past an evidence gate")


class _NeverWriter(ContentWriterAgent):
    def __init__(self) -> None:
        super().__init__()
        self.called = False

    async def execute(self, task: AgentTask) -> AgentResult:
        self.called = True
        raise AssertionError("writer must not run past an evidence gate")


@pytest.mark.asyncio
async def test_missing_collector_returns_waiting_before_assembler_and_writer(tmp_path):
    assembler = _NeverAssembler()
    writer = _NeverWriter()
    result = await EvidenceFirstOrchestratorAgent(
        EvidenceFirstWorkflow(
            collector=None,
            section_assembler=assembler,
            content_writer=writer,
            run_store=RunStore(tmp_path / "runs"),
        )
    ).run(
        AgentTask(
            task_type=TASK_EVIDENCE_FIRST_REPORT,
            parameters={
                "idempotency_key": "missing-collector",
                "project_context": PROJECT_CONTEXT,
            },
        )
    )

    assert not result.success
    assert result.status is AgentStatus.WAITING
    assert result.data["workflow_status"] == "waiting"
    assert result.data["checkpoint"] == "collector_waiting"
    assert result.data["stage_order"] == ["requirement", "research_planner"]
    assert result.data["supplement_actions"]
    assert assembler.called is False
    assert writer.called is False


class _CriticalCollector:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def collect(self, **kwargs: Any) -> list[EvidenceRecord]:
        common = {
            "metric_id": "SC2.competitors",
            "evidence_type": "observed_fact",
            "source_id": "critical-fixture",
            "geography": "Wuhan",
            "method": "e2e critical fixture",
        }
        if self.mode == "untraceable":
            return [
                EvidenceRecord(
                    evidence_id="critical-untraceable",
                    value=120,
                    observed_at="2026-07-22",
                    **common,
                )
            ]
        if self.mode == "conflict":
            return [
                EvidenceRecord(
                    evidence_id=f"critical-conflict-{index}",
                    value=value,
                    source_ref=f"fixture://conflict/{index}",
                    observed_at="2026-07-22",
                    **common,
                )
                for index, value in enumerate((120, 140))
            ]
        return [
            EvidenceRecord(
                evidence_id="critical-stale",
                value=120,
                source_ref="fixture://stale/1",
                observed_at="2020-01-01",
                **common,
            )
        ]


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("untraceable", "unknown"),
        ("conflict", "conflict"),
        ("stale", "stale"),
    ],
)
@pytest.mark.asyncio
async def test_critical_resolution_fail_closed_before_business_and_writer(
    mode,
    expected_status,
):
    assembler = _NeverAssembler()
    writer = _NeverWriter()
    result = await EvidenceFirstOrchestratorAgent(
        EvidenceFirstWorkflow(
            collector=_CriticalCollector(mode),
            section_assembler=assembler,
            content_writer=writer,
        )
    ).run(
        AgentTask(
            task_type=TASK_EVIDENCE_FIRST_REPORT,
            parameters={
                "idempotency_key": f"critical-{mode}",
                "project_context": PROJECT_CONTEXT,
                "critical_metric_ids": ["SC2.competitors"],
            },
        )
    )

    assert not result.success
    assert result.status is AgentStatus.WAITING
    assert result.data["workflow_status"] == "blocked"
    assert result.data["checkpoint"] == "evidence_blocked"
    assert result.data["stage_order"] == [
        "requirement",
        "research_planner",
        "collector",
        "resolver",
    ]
    assert result.data["resolutions"]["SC2.competitors"]["status"] == expected_status
    assert (
        result.data["supplement_actions"][0]["blocked_resolution_status"]
        == expected_status
    )
    assert assembler.called is False
    assert writer.called is False
