"""降级引擎 - 当数据缺失时提供多级备选策略。

核心原则：绝对不允许静默留空。
降级优先级：真实数据 → 城市基准 → 同类型项目平均 → 全国基准 → 待人工补充提示
"""

from __future__ import annotations

import logging
from typing import Any

from dds.contracts import (
    FALLBACK_STRATEGIES,
    FIELD_DESCRIPTIONS,
    FieldOrigin,
)

logger = logging.getLogger(__name__)


class FallbackEngine:
    """多级降级策略引擎。

    当数据缺失时，按优先级提供备选值，确保报告永远有内容。
    每个降级值都带有明确标记和置信度惩罚。
    """

    # 数据源优先级
    STRATEGY_PRIORITY = [
        "city_benchmark",
        "project_type_average",
        "national_benchmark",
        "human_input_required",
    ]

    def __init__(self, city: str = "", project_type: str = ""):
        self.city = city
        self.project_type = project_type

    def get_fallback(self, section_id: str, field: str) -> tuple[Any, FieldOrigin]:
        """获取最合适的降级值。

        Args:
            section_id: 章节 ID
            field: 字段名

        Returns:
            (fallback_value, FieldOrigin)
        """
        # 尝试各级降级策略
        for strategy in self.STRATEGY_PRIORITY:
            value = self._generate_fallback_value(section_id, field, strategy)
            if value is not None:
                strategy_config = FALLBACK_STRATEGIES[strategy]
                origin = FieldOrigin(
                    source=strategy,
                    label=strategy_config["label"],
                    confidence_penalty=strategy_config["confidence_penalty"],
                )
                logger.debug(
                    f"字段 [{field}] 使用降级策略: {strategy}, "
                    f"置信度惩罚: {strategy_config['confidence_penalty']}"
                )
                return value, origin

        # 兜底：使用 human_input_required
        return self._get_human_input_fallback(section_id, field)

    def _generate_fallback_value(
        self, section_id: str, field: str, strategy: str
    ) -> Any:
        """根据策略生成降级值。

        在实际生产环境中，这里应该连接到基准数据库。
        当前版本提供合理的占位文案。
        """
        description = FIELD_DESCRIPTIONS.get(field, field)

        if strategy == "city_benchmark" and self.city:
            return self._get_city_benchmark_value(section_id, field, description)

        if strategy == "project_type_average" and self.project_type:
            return self._get_project_type_average_value(section_id, field, description)

        if strategy == "national_benchmark":
            return self._get_national_benchmark_value(section_id, field, description)

        if strategy == "human_input_required":
            return self._format_human_input_template(description)

        return None

    def _get_city_benchmark_value(self, section_id: str, field: str, description: str) -> str:
        """生成城市基准数据描述。"""
        return f"基于{self.city}市同类项目的统计基准：{description}"

    def _get_project_type_average_value(self, section_id: str, field: str, description: str) -> str:
        """生成同类型项目平均数据描述。"""
        return f"基于{self.project_type}类型项目的行业平均：{description}"

    def _get_national_benchmark_value(self, section_id: str, field: str, description: str) -> str:
        """生成全国基准数据描述。"""
        return f"基于全国地产行业通用基准：{description}"

    def _get_human_input_fallback(self, section_id: str, field: str) -> tuple[str, FieldOrigin]:
        """生成待人工补充的提示文案。"""
        description = FIELD_DESCRIPTIONS.get(field, field)
        template = FALLBACK_STRATEGIES["human_input_required"]["template"]
        value = template.format(description=description)

        origin = FieldOrigin(
            source="human_input_required",
            label=FALLBACK_STRATEGIES["human_input_required"]["label"],
            confidence_penalty=FALLBACK_STRATEGIES["human_input_required"]["confidence_penalty"],
        )
        return value, origin

    def _format_human_input_template(self, description: str) -> str:
        """格式化待人工补充模板。"""
        template = FALLBACK_STRATEGIES["human_input_required"]["template"]
        return template.format(description=description)

    def get_fallback_by_strategy(
        self, strategy: str, section_id: str, field: str
    ) -> tuple[Any, FieldOrigin]:
        """按指定策略获取降级值。"""
        if strategy not in FALLBACK_STRATEGIES:
            raise ValueError(f"未知降级策略: {strategy}")

        value = self._generate_fallback_value(section_id, field, strategy)
        strategy_config = FALLBACK_STRATEGIES[strategy]

        if value is None:
            value = self._format_human_input_template(FIELD_DESCRIPTIONS.get(field, field))

        origin = FieldOrigin(
            source=strategy,
            label=strategy_config["label"],
            confidence_penalty=strategy_config["confidence_penalty"],
        )
        return value, origin

    @staticmethod
    def is_fallback_value(value: Any) -> bool:
        """判断一个值是否为降级值。"""
        if not isinstance(value, str):
            return False

        # 检查是否包含降级标记
        markers = [config["label"] for config in FALLBACK_STRATEGIES.values()]
        return any(marker in value for marker in markers)

    @staticmethod
    def get_confidence_penalty_for_strategy(strategy: str) -> float:
        """获取指定策略的置信度惩罚。"""
        return FALLBACK_STRATEGIES.get(strategy, {}).get("confidence_penalty", 0.8)
