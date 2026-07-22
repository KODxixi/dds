"""数据管理层 - 主动管理数据完整性，消除静默空值。

核心组件：
- DataOrchestrator: 数据编排器，管理完整的数据流程
- DataFetcher: 多数据源自动搜索
- FallbackEngine: 多级降级策略，确保永不空值
"""

from dds.data.fallback import FallbackEngine
from dds.data.fetcher import DataFetcher
from dds.data.orchestrator import DataOrchestrator

__all__ = [
    "DataOrchestrator",
    "DataFetcher",
    "FallbackEngine",
]
