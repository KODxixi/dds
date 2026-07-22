"""Compatibility entry point for the pre-V4 multi-agent work-report pipeline.

``generate_report`` and ``run_report_sync`` remain available to avoid breaking
older callers, but their artifact is always ``legacy_work_report`` and always
``delivery_ready=False``.  Formal DDS delivery belongs to the evidence-first
services pipeline, frozen V4 compiler, and hash-bound browser QA.
"""

from __future__ import annotations

import asyncio
import logging
from os import PathLike
from typing import Any, Optional

from dds.agents.base import AgentResult, AgentTask
from dds.agents.content_generator import ContentGeneratorAgent
from dds.agents.data_orchestrator import DataOrchestratorAgent
from dds.agents.orchestrator import (
    OrchestratorAgent,
    TASK_CONTENT_GENERATION,
    TASK_DATA_ORCHESTRATION,
    TASK_GENERATE_REPORT,
    TASK_QUALITY_AUDIT,
    TASK_REPORT_EXPORT,
    TASK_REQUIREMENT_ANALYSIS,
)
from dds.agents.quality_auditor import QualityAuditorAgent
from dds.agents.report_exporter import LEGACY_ARTIFACT_KIND, ReportExporterAgent
from dds.agents.requirement import RequirementAgent


logger = logging.getLogger(__name__)
_LEGACY_WARNING = (
    "generate_report 仅生成 legacy_work_report 兼容工作稿；"
    "正式交付必须通过 frozen V4 compiler 与 hash-bound browser QA。"
)


def create_agent_system() -> OrchestratorAgent:
    """Build the legacy five-agent work-report pipeline."""
    orchestrator = OrchestratorAgent()
    orchestrator.register_agent(RequirementAgent(), TASK_REQUIREMENT_ANALYSIS)
    orchestrator.register_agent(DataOrchestratorAgent(), TASK_DATA_ORCHESTRATION)
    orchestrator.register_agent(ContentGeneratorAgent(), TASK_CONTENT_GENERATION)
    orchestrator.register_agent(QualityAuditorAgent(), TASK_QUALITY_AUDIT)
    orchestrator.register_agent(ReportExporterAgent(), TASK_REPORT_EXPORT)
    logger.info("DDS legacy_work_report 兼容 Agent 系统组装完成")
    return orchestrator


async def generate_report(
    project_context: dict[str, Any],
    existing_data: Optional[dict[str, Any]] = None,
    report_id: Optional[str] = None,
    output_dir: str | PathLike[str] | None = None,
) -> dict[str, Any]:
    """Generate a compatibility work report, never a formal V4 delivery.

    ``success=True`` means only that the legacy work-report file was produced.
    The returned ``delivery_ready`` flag is unconditionally ``False``.
    """
    orchestrator = create_agent_system()
    parameters: dict[str, Any] = {
        "project_context": project_context,
        "existing_data": existing_data or {},
    }
    if report_id is not None:
        parameters["report_id"] = report_id
    if output_dir is not None:
        parameters["output_dir"] = output_dir

    task = AgentTask(
        task_type=TASK_GENERATE_REPORT,
        parameters=parameters,
        timeout_seconds=300.0,
    )
    result: AgentResult = await orchestrator.run(task)
    warnings = list(
        dict.fromkeys(
            [*result.warnings, *(result.data.get("warnings") or []), _LEGACY_WARNING]
        )
    )
    artifact_metadata = {
        "artifact_kind": LEGACY_ARTIFACT_KIND,
        "delivery_ready": False,
        "formal_delivery_pipeline": "frozen_v4_compiler_and_hash_bound_browser_qa",
    }
    response: dict[str, Any] = {
        "success": result.success,
        "report_id": result.data.get("report_id", ""),
        "errors": list(result.errors),
        "warnings": warnings,
        "artifact_kind": LEGACY_ARTIFACT_KIND,
        "delivery_ready": False,
        "metadata": artifact_metadata,
    }

    if result.success:
        export = result.data.get("export", {})
        validation = result.data.get("validation", {})
        response["output_path"] = export.get("output_path", "")
        response["filename"] = export.get("filename", "")
        response["output_dir"] = export.get("output_dir", "")
        response["overall_confidence"] = validation.get("overall_confidence", 0.0)
        response["confidence_level"] = (
            validation.get("summary", {}).get("overall_confidence", {}).get("level", "")
        )
        response["duration_seconds"] = result.data.get("duration_seconds", 0.0)
        response["section_confidences"] = validation.get("section_confidences", {})
        response["export"] = {
            **export,
            "artifact_kind": LEGACY_ARTIFACT_KIND,
            "delivery_ready": False,
            "metadata": artifact_metadata,
        }
    else:
        response["output_path"] = ""
    return response


def run_report_sync(
    project_context: dict[str, Any],
    existing_data: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Synchronous compatibility wrapper around :func:`generate_report`."""
    return asyncio.run(
        generate_report(project_context=project_context, existing_data=existing_data, **kwargs)
    )


__all__ = [
    "create_agent_system",
    "generate_report",
    "run_report_sync",
]
