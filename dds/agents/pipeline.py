"""DDS 多 Agent 系统快速入口。

提供一个简洁的工厂函数 `create_agent_system()` 和顶层 `generate_report()` 异步函数，
让调用方无需手动注册/组装每个 Agent。

典型用法：
    import asyncio
    from dds.agents.pipeline import generate_report

    result = asyncio.run(generate_report(
        project_context={"city": "北京", "project_type": "住宅", "project_name": "朝阳某地块"},
        existing_data={...},  # 可选：用户已有真实数据
    ))
    print(result["export"]["output_path"])
"""

from __future__ import annotations

import asyncio
import logging
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
from dds.agents.report_exporter import ReportExporterAgent
from dds.agents.requirement import RequirementAgent

logger = logging.getLogger(__name__)


def create_agent_system() -> OrchestratorAgent:
    """构造并返回一个已注册全部专业 Agent 的协调 Agent。

    所有子 Agent 通过该协调器统一调度。
    """
    orchestrator = OrchestratorAgent()
    orchestrator.register_agent(RequirementAgent(), TASK_REQUIREMENT_ANALYSIS)
    orchestrator.register_agent(DataOrchestratorAgent(), TASK_DATA_ORCHESTRATION)
    orchestrator.register_agent(ContentGeneratorAgent(), TASK_CONTENT_GENERATION)
    orchestrator.register_agent(QualityAuditorAgent(), TASK_QUALITY_AUDIT)
    orchestrator.register_agent(ReportExporterAgent(), TASK_REPORT_EXPORT)
    logger.info("DDS Agent 系统组装完成（5 个专业 Agent 已注册）")
    return orchestrator


async def generate_report(
    project_context: dict[str, Any],
    existing_data: Optional[dict[str, Any]] = None,
    report_id: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> dict[str, Any]:
    """顶层异步入口：一键生成完整的 DDS 决策报告。

    Args:
        project_context: 项目上下文 dict，至少应包含 city、project_type
        existing_data: 用户已有的真实数据，按 section_id 组织
        report_id: 自定义报告编号（为空则自动生成）
        output_dir: HTML 输出目录（为空则用默认 output/ 目录）

    Returns:
        dict: {
            "success": bool,
            "report_id": str,
            "output_path": str,          # HTML 报告绝对路径
            "overall_confidence": float,
            "confidence_level": str,
            "duration_seconds": float,
            "errors": list[str],
            "warnings": list[str],
        }
    """
    orchestrator = create_agent_system()

    parameters: dict[str, Any] = {
        "project_context": project_context,
        "existing_data": existing_data or {},
    }
    if report_id:
        parameters["report_id"] = report_id
    if output_dir:
        parameters["output_dir"] = output_dir

    task = AgentTask(
        task_type=TASK_GENERATE_REPORT,
        parameters=parameters,
        timeout_seconds=300.0,  # 整体流程超时 5 分钟
    )

    result: AgentResult = await orchestrator.run(task)

    # 统一返回结构，避免调用方处理 AgentResult dataclass
    response: dict[str, Any] = {
        "success": result.success,
        "report_id": result.data.get("report_id", ""),
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }

    if result.success:
        export = result.data.get("export", {})
        validation = result.data.get("validation", {})
        response["output_path"] = export.get("output_path", "")
        response["filename"] = export.get("filename", "")
        response["overall_confidence"] = validation.get("overall_confidence", 0.0)
        lvl = validation.get("summary", {}).get("overall_confidence", {}).get("level", "")
        response["confidence_level"] = lvl
        response["duration_seconds"] = result.data.get("duration_seconds", 0.0)
        response["section_confidences"] = validation.get("section_confidences", {})
    else:
        response["output_path"] = ""

    return response


def run_report_sync(
    project_context: dict[str, Any],
    existing_data: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """同步版本的 generate_report，便于在普通脚本中直接调用。"""
    return asyncio.run(
        generate_report(project_context=project_context, existing_data=existing_data, **kwargs)
    )
