"""Agent 基础类定义 - 消息协议、任务模型、状态管理。

所有 DDS Agent 的公共基类，定义统一的接口协议和生命周期。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# 状态枚举
# ============================================================================


class AgentStatus(str, Enum):
    """Agent 执行状态"""

    PENDING = "pending"        # 等待执行
    RUNNING = "running"        # 执行中
    WAITING = "waiting"        # 等待依赖
    COMPLETED = "completed"    # 完成
    FAILED = "failed"          # 失败
    RETRYING = "retrying"      # 重试中
    CANCELLED = "cancelled"    # 已取消


# ============================================================================
# 消息与任务数据结构
# ============================================================================


@dataclass
class AgentMessage:
    """Agent 间通信消息"""

    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = ""           # 发送方 Agent ID
    receiver: str = ""         # 接收方 Agent ID
    message_type: str = ""     # 消息类型: task, result, error, heartbeat
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    correlation_id: Optional[str] = None  # 关联的任务 ID


@dataclass
class AgentTask:
    """Agent 任务定义"""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = ""        # 任务类型标识
    section_id: Optional[str] = None  # 关联的 DDS section
    parameters: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)  # 依赖的任务 ID
    priority: int = 0          # 优先级（数字越大越优先）
    retry_count: int = 0
    max_retries: int = 2
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    timeout_seconds: float = 60.0


@dataclass
class AgentResult:
    """Agent 执行结果"""

    task_id: str
    success: bool
    agent_id: str
    status: AgentStatus
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: Optional[float] = None
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0


class AgentError(RuntimeError):
    """Agent 执行错误"""

    def __init__(
        self,
        message: str,
        agent_id: str = "",
        task_id: str = "",
        recoverable: bool = True,
    ):
        super().__init__(message)
        self.agent_id = agent_id
        self.task_id = task_id
        self.recoverable = recoverable


# ============================================================================
# BaseAgent 基类
# ============================================================================


class BaseAgent(ABC):
    """所有 DDS Agent 的抽象基类。

    子类必须实现：
    - agent_id: 返回唯一的 Agent 标识
    - execute(): 执行任务的核心逻辑
    """

    def __init__(self):
        self.status = AgentStatus.PENDING
        self.message_bus: Optional["AgentMessageBus"] = None

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """返回 Agent 的唯一标识"""
        raise NotImplementedError

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """返回 Agent 的可读名称"""
        raise NotImplementedError

    @property
    def _logger(self) -> logging.Logger:
        """延迟初始化 logger，避免抽象属性访问顺序问题。"""
        return logging.getLogger(f"{__name__}.{self.agent_id}")

    @abstractmethod
    async def execute(self, task: AgentTask) -> AgentResult:
        """执行任务（子类实现）。

        Args:
            task: 待执行的任务

        Returns:
            AgentResult: 执行结果
        """
        raise NotImplementedError

    async def run(self, task: AgentTask) -> AgentResult:
        """带状态管理和错误处理的执行入口。"""
        self.status = AgentStatus.RUNNING
        started_at = datetime.now()

        try:
            result = await asyncio.wait_for(
                self.execute(task),
                timeout=task.timeout_seconds,
            )
            result.started_at = started_at.isoformat()
            result.completed_at = datetime.now().isoformat()
            result.duration_ms = (datetime.now() - started_at).total_seconds() * 1000
            self.status = AgentStatus.COMPLETED if result.success else AgentStatus.FAILED
            return result

        except asyncio.TimeoutError:
            self.status = AgentStatus.FAILED
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=[f"任务超时 ({task.timeout_seconds}s)"],
                started_at=started_at.isoformat(),
                completed_at=datetime.now().isoformat(),
                duration_ms=task.timeout_seconds * 1000,
            )

        except AgentError as e:
            self.status = AgentStatus.FAILED
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=[str(e)],
                started_at=started_at.isoformat(),
                completed_at=datetime.now().isoformat(),
            )

        except Exception as e:
            self._logger.exception(f"Agent {self.agent_id} 执行任务 {task.task_id} 时发生未预期错误")
            self.status = AgentStatus.FAILED
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=[f"未预期错误: {type(e).__name__}: {str(e)}"],
                started_at=started_at.isoformat(),
                completed_at=datetime.now().isoformat(),
            )

    async def send_message(self, receiver: str, message_type: str, payload: dict[str, Any]) -> None:
        """发送消息给其他 Agent。"""
        if self.message_bus is None:
            raise AgentError("Agent 未连接到消息总线", agent_id=self.agent_id)

        message = AgentMessage(
            sender=self.agent_id,
            receiver=receiver,
            message_type=message_type,
            payload=payload,
        )
        await self.message_bus.publish(message)

    def can_handle(self, task_type: str) -> bool:
        """判断是否能处理该类型的任务。子类可覆盖。"""
        return True

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.agent_id} status={self.status.value}>"


# ============================================================================
# 简单消息总线（进程内）
# ============================================================================


class AgentMessageBus:
    """进程内 Agent 消息总线。

    支持注册 Agent、点对点消息投递和简单的广播。
    """

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._message_log: list[AgentMessage] = []
        self._logger = logging.getLogger(__name__)

    def register(self, agent: BaseAgent) -> None:
        """注册 Agent 到消息总线。"""
        if agent.agent_id in self._agents:
            self._logger.warning(f"Agent {agent.agent_id} 已注册，将被替换")
        self._agents[agent.agent_id] = agent
        agent.message_bus = self
        self._logger.info(f"Agent 已注册: {agent.agent_id} ({agent.agent_name})")

    def unregister(self, agent_id: str) -> None:
        if agent_id in self._agents:
            self._agents[agent_id].message_bus = None
            del self._agents[agent_id]

    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        return self._agents.get(agent_id)

    async def publish(self, message: AgentMessage) -> None:
        """投递消息。"""
        self._message_log.append(message)

        if message.receiver == "*":
            # 广播
            for agent in self._agents.values():
                await self._deliver(agent, message)
        else:
            target = self._agents.get(message.receiver)
            if target:
                await self._deliver(target, message)
            else:
                self._logger.warning(f"消息投递失败，目标 Agent [{message.receiver}] 未注册")

    async def _deliver(self, agent: BaseAgent, message: AgentMessage) -> None:
        """子类/Agent 可通过 on_message 回调处理消息。"""
        handler = getattr(agent, "on_message", None)
        if handler and callable(handler):
            try:
                await handler(message)
            except Exception as e:
                self._logger.error(f"Agent {agent.agent_id} 处理消息失败: {e}")

    def get_message_log(self, limit: int = 100) -> list[AgentMessage]:
        return self._message_log[-limit:]

    @property
    def registered_agents(self) -> list[str]:
        return list(self._agents.keys())
