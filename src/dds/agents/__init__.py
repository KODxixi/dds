"""DDS Agent 层 - 多Agent协作自动生成地产决策报告。

Agent 架构：
- OrchestratorAgent: 任务分发、状态追踪、结果汇总
- RequirementAgent: 需求分析、项目画像提取
- DataCollectorAgent: 多源数据采集（骨架，待接入外部数据源）
- DataOrchestratorAgent: 数据编排、降级策略执行（封装 DDS DataOrchestrator）
- ContentGeneratorAgent: 分章节内容生成（SC/AD/VA/CS）
- QualityAuditorAgent: 契约强制校验、置信度复核（封装 ContractEnforcer）
- ReportExporterAgent: 报告导出（单文件 HTML）

快速入口：
    from dds.agents import generate_report, create_agent_system
"""

from dds.agents.base import (
    AgentError,
    AgentMessage,
    AgentMessageBus,
    AgentResult,
    AgentStatus,
    AgentTask,
    BaseAgent,
)
from dds.agents.content_generator import ContentGeneratorAgent
from dds.agents.content_writer import (
    TASK_CONTENT_WRITING,
    ContentWriterAgent,
    UnsupportedNumericClaimError,
)
from dds.agents.data_collector import DataCollectorAgent
from dds.agents.data_orchestrator import DataOrchestratorAgent
from dds.agents.orchestrator import OrchestratorAgent
from dds.agents.pipeline import create_agent_system, generate_report, run_report_sync
from dds.agents.quality_auditor import QualityAuditorAgent
from dds.agents.report_exporter import ReportExporterAgent
from dds.agents.research_planner import (
    TASK_RESEARCH_PLANNING,
    ResearchPlannerAgent,
)
from dds.agents.requirement import RequirementAgent

__all__ = [
    # 基础类
    "AgentError",
    "AgentMessage",
    "AgentMessageBus",
    "AgentResult",
    "AgentStatus",
    "AgentTask",
    "BaseAgent",
    # Agent 实现
    "OrchestratorAgent",
    "RequirementAgent",
    "DataCollectorAgent",
    "DataOrchestratorAgent",
    "ContentGeneratorAgent",
    "ContentWriterAgent",
    "UnsupportedNumericClaimError",
    "TASK_CONTENT_WRITING",
    "QualityAuditorAgent",
    "ReportExporterAgent",
    "ResearchPlannerAgent",
    "TASK_RESEARCH_PLANNING",
    # 快速入口
    "create_agent_system",
    "generate_report",
    "run_report_sync",
]
