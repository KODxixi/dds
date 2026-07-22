"""Boundary tests for the fail-closed planning and writing agents."""

import pytest

from dds import (
    AgentStatus,
    AgentTask,
    ContentWriterAgent,
    DataRequirement,
    EvidenceRecord,
    RequirementAgent,
    ResearchPlannerAgent,
    ResolvedField,
    SectionResult,
    UnsupportedNumericClaimError,
)


@pytest.mark.asyncio
async def test_requirement_agent_blocks_when_city_is_missing():
    result = await RequirementAgent().run(
        AgentTask(
            task_type="requirement_analysis",
            parameters={"project_context": {"project_type": "Residential"}},
        )
    )

    assert not result.success
    assert result.status is AgentStatus.WAITING
    assert result.data["blocked"] is True
    assert result.data["resolution_status"] == "human_input"
    assert result.data["missing_fields"] == ["city"]


@pytest.mark.asyncio
async def test_research_planner_only_returns_batches_and_gap_actions():
    requirement = DataRequirement(
        metric_id="SC2.competitors",
        section_id="SC2",
        field_name="competitors",
        evidence_types=("observed_fact",),
        min_evidence_count=2,
        geography="Beijing",
    )
    result = await ResearchPlannerAgent().run(
        AgentTask(
            task_type="research_planning",
            parameters={"requirements": [requirement]},
        )
    )

    assert result.success
    assert result.data["collection_performed"] is False
    assert result.data["batches"][0]["collection_performed"] is False
    assert result.data["source_plan"][0]["missing_count"] == 2
    assert result.data["supplement_actions"][0]["status"] == "pending"
    assert "records" not in result.data


def test_content_writer_rejects_numbers_without_evidence_or_formula():
    evidence = EvidenceRecord(
        evidence_id="price-evidence",
        metric_id="SC2.price",
        value=120,
        source_id="fixture",
        source_ref="fixture://price",
    )
    section = SectionResult(
        section_id="SC2",
        data={
            "price": ResolvedField(
                status="resolved",
                value=120,
                evidence_refs=[evidence.evidence_id],
            )
        },
        evidence_refs=[evidence.evidence_id],
        status="resolved",
    )
    writer = ContentWriterAgent()

    assert writer.write(
        section,
        evidence_records=[evidence],
        draft="The evidence-backed price is 120.",
    ).endswith("120.")
    assert writer.write(
        section,
        evidence_records=[evidence],
        formulas={"uplift": "120 * 1.1 = 132"},
        draft="The formula-backed scenario is 132.",
    ).endswith("132.")

    with pytest.raises(UnsupportedNumericClaimError) as exc_info:
        writer.write(
            section,
            evidence_records=[evidence],
            draft="The unsupported scenario is 135.",
        )
    assert exc_info.value.unsupported_numbers == ["135"]
