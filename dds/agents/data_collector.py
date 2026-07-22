"""数据采集 Agent (DataCollectorAgent) - 多源数据采集。

当前为骨架实现：
- 优先从 existing_data 获取用户已提供的真实数据
- 通过 DataFetcher 尝试外部数据源（目前 DataFetcher 全部返回 None）
- 把真实数据标记为 FieldOrigin(source='real')，供 DataOrchestratorAgent 进一步编排

未来扩展：接入实际的楼盘数据库、宏观经济 API、向量证据库等。
"""

from __future__ import annotations

from typing import Any

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.contracts import SECTION_REQUIREMENTS, FieldOrigin


class DataCollectorAgent(BaseAgent):
    """数据采集 Agent。"""

    @property
    def agent_id(self) -> str:
        return "data_collector"

    @property
    def agent_name(self) -> str:
        return "数据采集 Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        section_id = task.section_id
        ctx = task.parameters.get("project_context", {})
        existing_data = task.parameters.get("existing_data", {}) or {}

        # 单 section 模式 vs 全量模式
        target_sections: list[str]
        if section_id:
            target_sections = [section_id]
        else:
            target_sections = list(SECTION_REQUIREMENTS.keys())

        collected: dict[str, dict[str, Any]] = {}
        origins: dict[str, dict[str, FieldOrigin]] = {}
        stats = {"real_fields": 0, "total_fields": 0}

        for sid in target_sections:
            section_existing = existing_data.get(sid, {}) if isinstance(existing_data, dict) else {}
            section_data: dict[str, Any] = {}
            section_origins: dict[str, FieldOrigin] = {}

            for field in SECTION_REQUIREMENTS.get(sid, []):
                stats["total_fields"] += 1
                value = section_existing.get(field) if isinstance(section_existing, dict) else None

                if value is not None and not self._is_empty(value):
                    section_data[field] = value
                    section_origins[field] = FieldOrigin(
                        source="real",
                        label="用户提供真实数据",
                        confidence_penalty=0.0,
                    )
                    stats["real_fields"] += 1
                else:
                    # 未来这里会调用 DataFetcher 去外部采集
                    # 当前 DataFetcher 全部返回 None，交给下游 DataOrchestratorAgent 降级
                    pass

            collected[sid] = section_data
            origins[sid] = section_origins

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "collected": collected,
                "origins": origins,
                "stats": stats,
            },
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
