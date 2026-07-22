"""需求分析 Agent (RequirementAgent) - 项目画像提取、决策问题定义、证据边界划定。

职责：
1. 解析用户输入，提取城市、项目类型、规模等关键属性
2. 明确 SC1 章节核心字段：决策问题、证据边界、基准日期
3. 规范化 project_context，作为下游所有 Agent 的公共上下文
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.contracts import UNIT_BY_ID

# 识别的项目类型
PROJECT_TYPES = {"住宅", "商业", "办公", "综合体", "酒店", "产业", "物流", "文旅", "公寓"}


class RequirementAgent(BaseAgent):
    """需求分析 Agent。"""

    @property
    def agent_id(self) -> str:
        return "requirement"

    @property
    def agent_name(self) -> str:
        return "需求分析 Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.parameters.get("project_context", {}) or {}
        existing_data = task.parameters.get("existing_data", {}) or {}

        # 提取并规范化项目属性
        normalized = dict(ctx)

        # 城市
        city = normalized.get("city") or normalized.get("城市", "")
        if city:
            normalized["city"] = str(city).strip()

        # 项目类型
        ptype = normalized.get("project_type") or normalized.get("项目类型", "")
        if ptype:
            normalized["project_type"] = str(ptype).strip()

        # 项目名称/编号
        project_name = normalized.get("project_name") or normalized.get("项目名称", "")
        project_id = normalized.get("project_id") or f"PRJ-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        normalized["project_id"] = project_id
        if project_name:
            normalized["project_name"] = project_name

        # 基准日期
        base_date = normalized.get("base_date") or datetime.now().strftime("%Y-%m-%d")
        normalized["base_date"] = base_date

        # 决策问题：优先从 existing_data.SC1 读取，否则根据上下文合成
        decision_question = None
        sc1_data = existing_data.get("SC1") if isinstance(existing_data, dict) else None
        if isinstance(sc1_data, dict):
            decision_question = sc1_data.get("decision_question")

        if not decision_question:
            decision_question = self._compose_decision_question(normalized)

        # 证据边界（默认根据项目类型推断）
        evidence_boundary = normalized.get("evidence_boundary") or (
            f"以{normalized.get('city', '目标城市')}市{normalized.get('project_type', '同类型')}"
            f"项目近 24 个月公开数据与可比案例为主要证据来源，"
            "宏观数据以国家统计局及地方官方发布为准。"
        )

        warnings = []
        if not normalized.get("city"):
            warnings.append("未指定城市，将使用全国基准数据，置信度将降低")
        if not normalized.get("project_type"):
            warnings.append("未指定项目类型，部分章节将使用通用模板")

        # 把 SC1 预填字段写回，供下游直接使用
        sc1_fields = {
            "project_id": project_id,
            "decision_question": decision_question,
            "evidence_boundary": evidence_boundary,
            "base_date": base_date,
        }

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "project_context": normalized,
                "sc1_fields": sc1_fields,
            },
            warnings=warnings,
        )

    def _compose_decision_question(self, ctx: dict[str, Any]) -> str:
        city = ctx.get("city", "目标城市")
        ptype = ctx.get("project_type", "地产")
        name = ctx.get("project_name", "该地块")
        return f"{city}市{name}{ptype}项目开发投资决策分析：在当前市场条件与场地约束下，是否推进开发、采用何种产品方案可最大化投资价值并控制风险？"
