from __future__ import annotations

import pytest

from dds.agents.base import AgentStatus, AgentTask
from dds.agents.requirement import RequirementAgent
from dds.analysis_profile import resolve_analysis_profile


def test_requested_level_is_clamped_to_assessed_evidence_level() -> None:
    profile = resolve_analysis_profile(
        {"address": "测试城测试路 1 号", "requested_level": 3},
    )

    assert profile["requested_level"] == 3
    assert profile["assessed_level"] == 1
    assert profile["effective_level"] == 1
    assert profile["decision_scope"] == "opportunity_screening"
    assert "absolute_project_revenue" not in profile["quantitative_authority"]


def test_input_2_requires_traceable_constraints_but_not_complete_documents() -> None:
    profile = resolve_analysis_profile(
        {
            "address": "测试城测试路 1 号",
            "constraint_sources": [{"source_ref": "client://planning-condition-v1"}],
        },
        requested_level=2,
    )

    assert profile["effective_level"] == 2
    assert profile["unit_policy"]["SC3"] == "required"
    assert profile["unit_policy"]["VA1"] == "optional"


def test_input_3_requires_core_boundaries_and_two_comparable_schemes() -> None:
    profile = resolve_analysis_profile(
        {
            "address": "测试城测试路 1 号",
            "constraint_sources": [{"source_ref": "client://planning-condition-v1"}],
            "core_development_boundaries_ready": True,
            "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
        },
        requested_level=3,
    )

    assert profile["effective_level"] == 3
    assert profile["decision_scope"] == "scheme_selection"
    assert profile["unit_policy"]["VA1"] == "required"


def test_missing_location_blocks_profile_even_when_input_3_is_requested() -> None:
    profile = resolve_analysis_profile(
        {
            "requested_level": 3,
            "constraint_sources": [{"source_ref": "client://planning-condition-v1"}],
            "core_development_boundaries_ready": True,
            "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
        }
    )

    assert profile["effective_level"] == 1
    assert profile["eligible"] is False
    assert profile["classification_status"] == "blocked"
    assert profile["classification_blockers"] == ["location_unresolved"]


@pytest.mark.parametrize("status", ["conflict", "stale", "expired"])
def test_unusable_constraints_do_not_promote_input_level(status: str) -> None:
    profile = resolve_analysis_profile(
        {
            "address": "Test City Road 1",
            "requested_level": 3,
            "constraint_sources": [
                {
                    "source_ref": "client://planning-condition-v1",
                    "status": status,
                }
            ],
        }
    )

    assert profile["effective_level"] == 1
    assert profile["eligible"] is True
    assert "constraints_unusable" in profile["classification_reasons"]


@pytest.mark.asyncio
async def test_requirement_agent_waits_for_profile_location() -> None:
    result = await RequirementAgent().run(
        AgentTask(
            task_type="requirement_analysis",
            parameters={
                "requested_level": 1,
                "project_context": {
                    "project_id": "missing-location",
                    "city": "Test City",
                },
            },
        )
    )

    assert result.success is False
    assert result.status is AgentStatus.WAITING
    assert result.data["missing_fields"] == ["address_or_coordinates"]
