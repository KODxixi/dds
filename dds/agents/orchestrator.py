"""协调 Agent (OrchestratorAgent) - 任务分发、状态追踪、依赖管理、结果汇总。

这是整个 Agent 体系的中枢，负责：
1. 接收用户请求，创建顶层生成报告任务
2. 分解为子任务并路由到专业 Agent
3. 管理依赖图，确保按正确顺序执行（SC→AD→VA→CS）
4. 失败重试机制
5. 汇总所有 section 数据，触发质量校验，最终输出完整报告
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from dds.agents.base import (
    AgentError,
    AgentMessage,
    AgentResult,
    AgentStatus,
    AgentTask,
    BaseAgent,
)
from dds.contracts import VALID_SECTION_IDS

logger = logging.getLogger(__name__)


# 任务类型常量
TASK_REQUIREMENT_ANALYSIS = "requirement_analysis"
TASK_DATA_COLLECTION = "data_collection"
TASK_DATA_ORCHESTRATION = "data_orchestration"
TASK_CONTENT_GENERATION = "content_generation"
TASK_QUALITY_AUDIT = "quality_audit"
TASK_REPORT_EXPORT = "report_export"
TASK_GENERATE_REPORT = "generate_report"

# 章节执行依赖图（上游 section 完成后才能处理下游）
SECTION_DEPENDENCIES: dict[str, list[str]] = {
    "SC1": [],
    "SC2": ["SC1"],
    "SC3": ["SC1"],
    "AD1": ["SC1", "SC2", "SC3"],
    "AD2": ["AD1"],
    "AD3": ["AD2"],
    "AD4": ["AD3"],
    "AD5": ["AD3"],
    "VA1": ["AD4", "AD5"],
    "VA2": ["VA1"],
    "VA3": ["VA2"],
    "CS": ["VA3"],  # CS 最后执行，汇总所有章节
}

# 章节分组（用于并行调度）
SECTION_GROUPS = {
    "SC": ["SC1", "SC2", "SC3"],
    "AD": ["AD1", "AD2", "AD3", "AD4", "AD5"],
    "VA": ["VA1", "VA2", "VA3"],
    "CS": ["CS"],
}


@dataclass
class TaskRecord:
    """任务追踪记录"""
    task: AgentTask
    agent_id: str
    status: AgentStatus = AgentStatus.PENDING
    result: Optional[AgentResult] = None
    attempts: int = 0


@dataclass
class ReportGenerationState:
    """报告生成过程的全局状态"""
    report_id: str
    project_context: dict[str, Any] = field(default_factory=dict)
    existing_data: dict[str, Any] = field(default_factory=dict)
    sections: dict[str, Any] = field(default_factory=dict)  # sid -> SectionData
    task_records: dict[str, TaskRecord] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class OrchestratorAgent(BaseAgent):
    """协调 Agent - 整个多Agent系统的中枢调度器。

    不直接执行业务逻辑，而是：
    1. 持有所有专业 Agent 的引用
    2. 构建依赖图并按拓扑序调度任务
    3. 管理失败重试
    4. 汇总最终产物
    """

    def __init__(self):
        super().__init__()
        self._agents: dict[str, BaseAgent] = {}
        # Agent ID -> task type 的映射（由 register_agent 建立）
        self._task_type_map: dict[str, str] = {}

    @property
    def agent_id(self) -> str:
        return "orchestrator"

    @property
    def agent_name(self) -> str:
        return "协调 Agent"

    def register_agent(self, agent: BaseAgent, task_type: str) -> None:
        """注册专业 Agent。"""
        self._agents[task_type] = agent
        self._task_type_map[agent.agent_id] = task_type
        logger.info(f"协调器注册 Agent: {agent.agent_id} -> {task_type}")

    def _get_agent_for_task(self, task_type: str) -> BaseAgent:
        if task_type not in self._agents:
            raise AgentError(
                f"任务类型 [{task_type}] 没有已注册的 Agent",
                agent_id=self.agent_id,
            )
        return self._agents[task_type]

    async def execute(self, task: AgentTask) -> AgentResult:
        """执行顶层任务。支持两种顶层任务类型：
        1. generate_report: 完整报告生成流程
        """
        if task.task_type == TASK_GENERATE_REPORT:
            return await self._generate_report(task)
        else:
            raise AgentError(
                f"协调 Agent 不支持任务类型: {task.task_type}",
                agent_id=self.agent_id,
                task_id=task.task_id,
            )

    # ─────────────────────────────────────────────────────────
    # 核心流程：生成完整报告
    # ─────────────────────────────────────────────────────────

    async def _generate_report(self, task: AgentTask) -> AgentResult:
        """完整报告生成主流程。

        阶段：
        1. 需求分析 → project_context
        2. 数据编排（一次性处理所有 section）→ sections: dict[str, SectionData]
        3. 内容生成（分 section 并行）→ 填充 sections 的 content 字段
        4. 质量校验 → 契约强制校验
        5. 报告导出 → HTML 产物
        """
        report_id = task.parameters.get("report_id", f"RPT-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        state = ReportGenerationState(
            report_id=report_id,
            project_context=task.parameters.get("project_context", {}),
            existing_data=task.parameters.get("existing_data", {}),
        )
        self._logger.info(f"开始生成报告 [{report_id}]")

        # 阶段 1: 需求分析
        self._logger.info("[阶段 1/5] 需求分析")
        req_result = await self._run_subtask(
            TASK_REQUIREMENT_ANALYSIS,
            parameters={
                "project_context": state.project_context,
                "existing_data": state.existing_data,
            },
            state=state,
        )
        if not req_result.success:
            return self._fail_report(state, f"需求分析失败: {req_result.errors}")
        state.project_context = req_result.data.get("project_context", state.project_context)

        # 阶段 2: 数据编排（一次性准备所有 12 个 section 的数据）
        self._logger.info("[阶段 2/5] 数据采集与编排")
        orch_result = await self._run_subtask(
            TASK_DATA_ORCHESTRATION,
            parameters={
                "project_context": state.project_context,
                "existing_data": state.existing_data,
                "sc1_fields": req_result.data.get("sc1_fields"),
            },
            state=state,
        )
        if not orch_result.success:
            return self._fail_report(state, f"数据编排失败: {orch_result.errors}")
        state.sections = orch_result.data.get("sections", {})

        # 阶段 3: 内容生成（每个 section 生成 narrative 内容）
        self._logger.info("[阶段 3/5] 内容生成（分章节）")
        content_ok = await self._generate_all_section_content(state)
        if not content_ok:
            return self._fail_report(state, "部分章节内容生成失败")

        # 阶段 4: 质量审核
        self._logger.info("[阶段 4/5] 质量审核与契约校验")
        audit_result = await self._run_subtask(
            TASK_QUALITY_AUDIT,
            parameters={
                "sections": state.sections,
                "project_context": state.project_context,
            },
            state=state,
        )
        if not audit_result.success:
            return self._fail_report(state, f"质量校验失败: {audit_result.errors}")
        if not audit_result.data.get("valid", False):
            return self._fail_report(state, f"契约校验未通过: {audit_result.data.get('errors', [])}")
        state.metadata["validation"] = audit_result.data

        # 阶段 5: 报告导出
        self._logger.info("[阶段 5/5] 报告导出")
        export_result = await self._run_subtask(
            TASK_REPORT_EXPORT,
            parameters={
                "sections": state.sections,
                "project_context": state.project_context,
                "metadata": state.metadata,
                "validation": audit_result.data,
            },
            state=state,
        )
        if not export_result.success:
            return self._fail_report(state, f"报告导出失败: {export_result.errors}")

        state.completed_at = datetime.now().isoformat()
        duration = (
            datetime.fromisoformat(state.completed_at) -
            datetime.fromisoformat(state.started_at)
        ).total_seconds()

        self._logger.info(
            f"报告 [{report_id}] 生成完成，耗时 {duration:.1f}s，"
            f"整体置信度={audit_result.data.get('overall_confidence', 0):.2f}"
        )

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "report_id": report_id,
                "sections": state.sections,
                "validation": audit_result.data,
                "export": export_result.data,
                "duration_seconds": duration,
                "errors": state.errors,
                "warnings": state.warnings,
            },
            warnings=state.warnings,
        )

    async def _generate_all_section_content(self, state: ReportGenerationState) -> bool:
        """按依赖图顺序生成所有 section 的内容。

        同一层级的 section 可以并行生成（SC2/SC3 可并行；AD4/AD5 可并行）。
        """
        # 拓扑序执行：按批次并行
        completed: set[str] = set()
        remaining = set(VALID_SECTION_IDS)

        while remaining:
            # 找出所有依赖已满足的 section
            ready = [
                sid for sid in remaining
                if all(dep in completed for dep in SECTION_DEPENDENCIES.get(sid, []))
            ]
            if not ready:
                state.errors.append(f"依赖死锁：无法处理章节 {remaining}")
                return False

            # 并行执行 ready 批次
            self._logger.info(f"并行生成章节内容: {ready}")
            results = await asyncio.gather(*[
                self._generate_section_content(sid, state)
                for sid in ready
            ], return_exceptions=True)

            for sid, res in zip(ready, results):
                if isinstance(res, Exception):
                    state.errors.append(f"章节 {sid} 内容生成异常: {res}")
                    return False
                if not res.success:
                    state.errors.append(f"章节 {sid} 内容生成失败: {res.errors}")
                    return False
                # 将内容合并到 section 数据中
                if sid in state.sections:
                    section_data = state.sections[sid]
                    if hasattr(section_data, "data"):
                        section_data.data["narrative"] = res.data.get("content", "")
                    elif isinstance(section_data, dict):
                        section_data["narrative"] = res.data.get("content", "")
                completed.add(sid)
                remaining.discard(sid)

        return True

    async def _generate_section_content(
        self, section_id: str, state: ReportGenerationState
    ) -> AgentResult:
        """生成单个 section 的叙事内容。"""
        section_data = state.sections.get(section_id)
        return await self._run_subtask(
            TASK_CONTENT_GENERATION,
            parameters={
                "section_id": section_id,
                "section_data": section_data,
                "project_context": state.project_context,
            },
            state=state,
        )

    async def _run_subtask(
        self,
        task_type: str,
        parameters: dict[str, Any],
        state: ReportGenerationState,
        section_id: Optional[str] = None,
    ) -> AgentResult:
        """运行子任务，带重试逻辑。"""
        agent = self._get_agent_for_task(task_type)
        task = AgentTask(
            task_type=task_type,
            section_id=section_id,
            parameters=parameters,
            max_retries=2,
            timeout_seconds=120.0 if task_type == TASK_CONTENT_GENERATION else 60.0,
        )
        record = TaskRecord(task=task, agent_id=agent.agent_id)
        state.task_records[task.task_id] = record

        last_result: Optional[AgentResult] = None
        for attempt in range(task.max_retries + 1):
            record.attempts = attempt + 1
            record.status = AgentStatus.RUNNING
            self._logger.debug(
                f"执行子任务 [{task_type}]"
                f"{f' section={section_id}' if section_id else ''}"
                f" (尝试 {attempt + 1}/{task.max_retries + 1})"
            )
            result = await agent.run(task)
            record.result = result
            record.status = result.status

            if result.success:
                return result

            last_result = result
            if attempt < task.max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))  # 简单退避
                record.status = AgentStatus.RETRYING

        return last_result or AgentResult(
            task_id=task.task_id,
            success=False,
            agent_id=agent.agent_id,
            status=AgentStatus.FAILED,
            errors=["子任务失败（重试已耗尽）"],
        )

    def _fail_report(self, state: ReportGenerationState, reason: str) -> AgentResult:
        """构造失败的顶层结果。"""
        state.errors.append(reason)
        state.completed_at = datetime.now().isoformat()
        self._logger.error(f"报告生成失败: {reason}")
        return AgentResult(
            task_id="",
            success=False,
            agent_id=self.agent_id,
            status=AgentStatus.FAILED,
            errors=list(state.errors),
            warnings=list(state.warnings),
            data={"report_id": state.report_id},
        )

