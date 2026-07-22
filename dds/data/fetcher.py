"""数据抓取器 - 自动从多个数据源搜索缺失数据。

数据源优先级：本地楼盘数据库 -> 向量证据库 -> 城市基准数据 -> 项目历史数据
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from dds.contracts import FieldOrigin

logger = logging.getLogger(__name__)


class DataFetcher:
    """多数据源自动搜索。

    当数据缺失时，按优先级从各个数据源搜索数据。
    找到后返回数据 + 来源标记。
    """

    # 数据源优先级
    SOURCE_PRIORITY = [
        "local_parquet_db",
        "vector_evidence",
        "city_benchmarks",
        "project_history",
    ]

    def __init__(self, city: str = "", project_type: str = "", data_dir: str = ""):
        self.city = city
        self.project_type = project_type
        self.data_dir = data_dir

    async def fetch(
        self,
        section_id: str,
        field: str,
        context: Optional[dict[str, Any]] = None,
    ) -> tuple[Optional[Any], Optional[FieldOrigin]]:
        """按优先级搜索数据，直到找到。

        Args:
            section_id: 章节 ID
            field: 字段名
            context: 项目上下文

        Returns:
            (data, FieldOrigin) - 如果没找到，返回 (None, None)
        """
        context = context or {}
        city = context.get("city", self.city)
        project_type = context.get("project_type", self.project_type)

        for source in self.SOURCE_PRIORITY:
            try:
                result = await self._search_source(source, section_id, field, city, project_type, context)
                if result is not None:
                    logger.debug(f"字段 [{field}] 从数据源 [{source}] 获取到数据")
                    return result
            except Exception as e:
                logger.warning(f"从数据源 [{source}] 获取数据失败: {e}")
                continue

        # 所有数据源都没找到
        return None, None

    async def _search_source(
        self,
        source: str,
        section_id: str,
        field: str,
        city: str,
        project_type: str,
        context: dict[str, Any],
    ) -> tuple[Optional[Any], Optional[FieldOrigin]]:
        """从指定数据源搜索数据。

        在实际生产环境中，这里应该连接到实际的数据库。
        当前版本返回 None，表示需要上层使用降级策略。
        """
        if source == "local_parquet_db":
            return await self._search_local_parquet(section_id, field, city, project_type, context)

        if source == "vector_evidence":
            return await self._search_vector_evidence(section_id, field, city, project_type, context)

        if source == "city_benchmarks":
            return await self._search_city_benchmarks(section_id, field, city, project_type, context)

        if source == "project_history":
            return await self._search_project_history(section_id, field, city, project_type, context)

        return None, None

    async def _search_local_parquet(
        self, section_id: str, field: str, city: str, project_type: str, context: dict[str, Any]
    ) -> tuple[Optional[Any], Optional[FieldOrigin]]:
        """搜索本地楼盘 Parquet 数据库。"""
        # TODO: 实现实际的 Parquet 数据库查询
        return None, None

    async def _search_vector_evidence(
        self, section_id: str, field: str, city: str, project_type: str, context: dict[str, Any]
    ) -> tuple[Optional[Any], Optional[FieldOrigin]]:
        """搜索向量证据库。"""
        # TODO: 实现实际的向量搜索
        return None, None

    async def _search_city_benchmarks(
        self, section_id: str, field: str, city: str, project_type: str, context: dict[str, Any]
    ) -> tuple[Optional[Any], Optional[FieldOrigin]]:
        """搜索城市基准数据。"""
        # TODO: 实现实际的基准数据查询
        return None, None

    async def _search_project_history(
        self, section_id: str, field: str, city: str, project_type: str, context: dict[str, Any]
    ) -> tuple[Optional[Any], Optional[FieldOrigin]]:
        """搜索同项目历史数据。"""
        # TODO: 实现实际的历史数据查询
        return None, None

    def mark_real_data(self, value: Any) -> tuple[Any, FieldOrigin]:
        """标记真实数据的来源。"""
        origin = FieldOrigin(
            source="real",
            label="真实数据",
            confidence_penalty=0.0,
        )
        return value, origin

    def mark_external_source(
        self, value: Any, source_name: str, confidence_penalty: float = 0.1
    ) -> tuple[Any, FieldOrigin]:
        """标记外部来源数据。"""
        origin = FieldOrigin(
            source="external",
            label=f"外部数据({source_name})",
            confidence_penalty=confidence_penalty,
        )
        return value, origin
