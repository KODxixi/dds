"""质量审核 Agent (QualityAuditorAgent) - 封装 DDS ContractEnforcer。

职责：
1. 调用 ContractEnforcer.validate_report() 执行契约强制校验
2. 把 ValidationResult 转为 Agent 可消费的结构化字典
3. 整体置信度 < 0.3 时给出警告，但不自动失败（由协调 Agent 决定）
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.engine import ContractEnforcer


class QualityAuditorAgent(BaseAgent):
    """质量审核 Agent。"""

    @property
    def agent_id(self) -> str:
        return "quality_auditor"

    @property
    def agent_name(self) -> str:
        return "质量审核 Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        sections = task.parameters.get("sections", {})
        ctx = task.parameters.get("project_context", {}) or {}

        if not sections:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=["没有待校验的 sections"],
            )

        enforcer = ContractEnforcer()
        try:
            result = enforcer.validate_report(sections, report_metadata=ctx)
        except Exception as e:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=[f"校验过程异常: {type(e).__name__}: {e}"],
            )

        summary = enforcer.get_validation_summary(result)

        return AgentResult(
            task_id=task.task_id,
            success=True,  # Agent 本身执行成功；契约是否通过由 summary["valid"] 决定
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "valid": result.valid,
                "overall_confidence": result.overall_confidence,
                "section_confidences": result.section_confidences,
                "errors": list(result.errors),
                "warnings": list(result.warnings) + summary.get("warnings", []),
                "summary": summary,
            },
            confidence=result.overall_confidence,
            warnings=list(result.warnings),
        )
