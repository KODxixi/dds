"""DDS-internal composition around GarchOS public workflow objects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from garchos_openapi_contract import OpenAPIContract


def build_analysis_context(
    *,
    project_context: dict[str, Any],
    case_matches: list[dict[str, Any]],
    workflow_id: str | None = None,
    job_metadata: dict[str, Any] | None = None,
    contract: OpenAPIContract | None = None,
) -> dict[str, Any]:
    """Compose private analysis state after validating every public component."""

    active_contract = contract or OpenAPIContract.from_environment()
    active_contract.validate_schema("ProjectContext", project_context)
    for case_match in case_matches:
        active_contract.validate_schema("CaseMatch", case_match)
    context = {
        "project_context": deepcopy(project_context),
        "case_matches": deepcopy(case_matches),
        "job_metadata": deepcopy(job_metadata or {}),
    }
    if workflow_id is not None:
        active_contract.validate_schema("StableId", workflow_id)
        context["workflow_id"] = workflow_id
    return context


def build_visual_brief(
    *,
    scenario: dict[str, Any],
    analysis_context: dict[str, Any],
    contract: OpenAPIContract | None = None,
) -> dict[str, Any]:
    """Derive a deterministic VisualBrief from a selected, validated scenario."""

    active_contract = contract or OpenAPIContract.from_environment()
    active_contract.validate_schema("DecisionScenario", scenario)
    project_context = analysis_context.get("project_context") or {}
    active_contract.validate_schema("ProjectContext", project_context)
    constraints = []
    for item in project_context.get("constraints") or []:
        label = str(item.get("label") or item.get("code") or "约束")
        value = item.get("value")
        unit = str(item.get("unit") or "")
        constraints.append(f"{label}: {value}{unit}")
    if not constraints:
        constraints = [
            f"以已选方案 {scenario['scenario_id']} revision {scenario['revision']} 为设计基线"
        ]

    artifact_ids: list[str] = []
    for case_match in analysis_context.get("case_matches") or []:
        for evidence in case_match.get("evidence_refs") or []:
            artifact_id = str(evidence.get("asset_id") or "")
            if artifact_id and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
    for evidence in scenario.get("evidence_refs") or []:
        artifact_id = str(evidence.get("asset_id") or "")
        if artifact_id and artifact_id not in artifact_ids:
            artifact_ids.append(artifact_id)

    visual_brief = {
        "scenario_id": scenario["scenario_id"],
        "scenario_revision": scenario["revision"],
        "immutable_constraints": constraints,
        "reference_artifact_ids": artifact_ids[:10],
        "prohibited_interpretations": [
            "不得把 AI 概念示意解释为已审批、已施工或可直接建造的方案",
            "不得凭概念图补写容积率、货值、去化或经营结果",
        ],
        "creative_direction": str(scenario["summary"]),
        "output_spec": {"count": 4, "aspect_ratio": "16:9", "watermark": True},
    }
    active_contract.validate_schema("VisualBrief", visual_brief)
    return visual_brief


def can_authorize(*, confidence: float) -> bool:
    """Model confidence is advisory and can never grant an authorization."""

    del confidence
    return False
