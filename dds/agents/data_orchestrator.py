"""数据编排 Agent (DataOrchestratorAgent) - 封装 DDS DataOrchestrator。

职责：
1. 调用 DDS v2 已有的 DataOrchestrator.ensure_all_sections() 一次性准备 12 个 section
2. DataOrchestrator 内部会：检测缺失 → DataFetcher 搜索 → FallbackEngine 降级 → 计算置信度
3. 返回 dict[str, SectionData]，供 ContentGeneratorAgent 使用
"""

from __future__ import annotations

from typing import Any

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.data import DataOrchestrator


class DataOrchestratorAgent(BaseAgent):
    """数据编排 Agent，对 DDS DataOrchestrator 的 Agent 封装。"""

    @property
    def agent_id(self) -> str:
        return "data_orchestrator"

    @property
    def agent_name(self) -> str:
        return "数据编排 Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.parameters.get("project_context", {}) or {}
        existing_data = task.parameters.get("existing_data", {}) or {}

        # 把 RequirementAgent 产出的 sc1_fields 合并进 existing_data["SC1"]
        sc1_fields = task.parameters.get("sc1_fields")
        if sc1_fields:
            sc1 = dict(existing_data.get("SC1", {}) or {})
            for k, v in sc1_fields.items():
                if k not in sc1 or self._is_empty(sc1.get(k)):
                    sc1[k] = v
            existing_data = dict(existing_data)
            existing_data["SC1"] = sc1

        city = ctx.get("city", "")
        project_type = ctx.get("project_type", "")

        orch = DataOrchestrator(city=city, project_type=project_type)
        sections = await orch.ensure_all_sections(
            project_context=ctx,
            existing_sections=existing_data,
        )
        quality_report = orch.get_data_quality_report(sections)

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "sections": sections,
                "quality_report": quality_report,
            },
            confidence=quality_report["overall_confidence"]["score"],
            warnings=[
                f"真实数据占比: {quality_report['summary']['real_ratio']:.1%}",
                f"降级字段数: {quality_report['summary']['fallback_fields']}",
            ],
        )

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        if isinstance(value, (list, dict)) and len(value) == 0:
            return True
        return False
