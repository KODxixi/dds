from __future__ import annotations

import pytest

from dds.agents.base import AgentStatus, AgentTask
from dds.agents.requirement import RequirementAgent
from dds.analysis_profile import resolve_intervention_profile


def test_system_recommends_but_does_not_select_a_mode() -> None:
    profile = resolve_intervention_profile(
        {
            "address": "测试城测试路 1 号",
            "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
        }
    )
    assert profile["recommended_mode"] == 3
    assert profile["selected_mode"] is None
    assert profile["mode_status"] == "awaiting_confirmation"
    assert profile["eligible"] is False


def test_scheme_material_recommends_input_3_without_selecting_it() -> None:
    profile = resolve_intervention_profile(
        {
            "address": "测试城测试路 1 号",
            "materials": [{"filename": "三方案比选.pptx"}],
        }
    )
    assert profile["recommended_mode"] == 3
    assert profile["selected_mode"] is None
    assert profile["mode_status"] == "awaiting_confirmation"


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_user_selection_is_never_replaced_by_recommendation(mode: int) -> None:
    raw = {
        "address": "测试城测试路 1 号",
        "constraint_sources": [{"source_ref": "client://condition"}],
        "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
    }
    profile = resolve_intervention_profile(
        raw,
        selected_mode=mode,
        confirmed=True,
    )
    assert profile["selected_mode"] == mode
    assert profile["mode_status"] == "confirmed"


def test_input_3_stays_selected_and_blocks_without_two_schemes() -> None:
    profile = resolve_intervention_profile(
        {"address": "测试城测试路 1 号"},
        selected_mode=3,
        confirmed=True,
    )
    assert profile["recommended_mode"] == 1
    assert profile["selected_mode"] == 3
    assert profile["mode_status"] == "blocked"
    assert profile["missing_inputs"] == ["two_comparable_schemes"]


def test_input_1_stays_selected_even_when_complete_schemes_exist() -> None:
    profile = resolve_intervention_profile(
        {
            "address": "测试城测试路 1 号",
            "constraint_sources": [{"source_ref": "client://condition"}],
            "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
        },
        selected_mode=1,
        confirmed=True,
    )
    assert profile["recommended_mode"] == 3
    assert profile["selected_mode"] == 1
    assert profile["decision_scope"] == "independent_opportunity_research"


@pytest.mark.asyncio
async def test_requirement_agent_waits_for_user_confirmation() -> None:
    result = await RequirementAgent().run(
        AgentTask(
            task_type="requirement_analysis",
            parameters={
                "input_profile": {"address": "Test City Road 1"},
                "project_context": {
                    "project_id": "awaiting-mode",
                    "city": "Test City",
                    "address": "Test City Road 1",
                },
            },
        )
    )
    assert result.success is False
    assert result.status is AgentStatus.WAITING
    assert result.data["missing_fields"] == ["intervention_confirmation"]


@pytest.mark.asyncio
async def test_profile_requirement_graph_closes_with_customer_audit_cs() -> None:
    result = await RequirementAgent().run(
        AgentTask(
            task_type="requirement_analysis",
            parameters={
                "selected_mode": 1,
                "mode_confirmed": True,
                "input_profile": {
                    "address": "Test City Road 1",
                    "selected_mode": 1,
                    "mode_confirmed": True,
                },
                "project_context": {
                    "project_id": "customer-graph",
                    "city": "Test City",
                    "address": "Test City Road 1",
                    "base_date": "2026-07-23",
                },
            },
        )
    )
    assert result.success
    graph = result.data["data_requirement_graph"]
    assert graph["section_order"][-1] == "CS"
    assert {
        (edge["from_section"], edge["to_section"])
        for edge in graph["edges"]
        if edge["to_section"] == "CS"
    } == {
        ("SC2", "CS"),
        ("AD3", "CS"),
        ("VA2", "CS"),
        ("VA3", "CS"),
    }
