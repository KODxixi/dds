"""数据编排器 - 主动管理每个 decision-unit 的数据完整性。

核心流程：检测缺失 → 搜索补全 → 降级填充 → 计算置信度
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from dds.contracts import (
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    FieldOrigin,
    SectionData,
    compute_evidence_confidence,
)
from dds.data.fallback import FallbackEngine
from dds.data.fetcher import DataFetcher

logger = logging.getLogger(__name__)


class DataOrchestrator:
    """主动数据管理核心。

    确保每个 section 都有完整的数据，缺失时自动搜索或降级。
    同时计算每个 section 的置信度。
    """

    def __init__(self, city: str = "", project_type: str = "", data_dir: str = ""):
        self.city = city
        self.project_type = project_type
        self.data_fetcher = DataFetcher(city=city, project_type=project_type, data_dir=data_dir)
        self.fallback_engine = FallbackEngine(city=city, project_type=project_type)

    async def ensure_section_data(
        self,
        section_id: str,
        project_context: dict[str, Any],
        existing_data: Optional[dict[str, Any]] = None,
    ) -> SectionData:
        """确保该 section 有完整数据，否则触发搜索或降级。

        Args:
            section_id: 章节 ID
            project_context: 项目上下文
            existing_data: 已有的数据（如果有）

        Returns:
            SectionData - 完整的 section 数据
        """
        if section_id not in VALID_SECTION_IDS:
            raise ValueError(f"无效的 section ID: {section_id}")

        # 1. 提取已有数据
        existing_data = existing_data or {}
        final_data = {}
        data_origin = {}
        warnings = []

        # 2. 检测必填字段
        required_fields = SECTION_REQUIREMENTS.get(section_id, [])

        # 3. 逐个处理必填字段
        for field in required_fields:
            value = existing_data.get(field)

            if value is not None and not self._is_empty_value(value):
                # 已有数据，标记为真实数据
                final_data[field] = value
                data_origin[field] = FieldOrigin(
                    source="real",
                    label="真实数据",
                    confidence_penalty=0.0,
                )
                continue

            # 4. 数据缺失 - 尝试从外部数据源获取
            fetched_value, fetched_origin = await self.data_fetcher.fetch(
                section_id=section_id,
                field=field,
                context=project_context,
            )

            if fetched_value is not None and fetched_origin is not None:
                final_data[field] = fetched_value
                data_origin[field] = fetched_origin
                continue

            # 5. 外部数据源也没找到 - 使用降级策略
            fallback_value, fallback_origin = self.fallback_engine.get_fallback(
                section_id=section_id,
                field=field,
            )
            final_data[field] = fallback_value
            data_origin[field] = fallback_origin
            warnings.append(f"字段 [{field}] 使用了降级策略: {fallback_origin.label}")

        # 6. 计算置信度
        confidence = self._calculate_section_confidence(section_id, final_data, data_origin)

        # 7. 找出真正缺失的字段（虽然我们填充了降级值）
        missing_fields = [
            field for field, origin in data_origin.items()
            if origin.source != "real"
        ]

        result = SectionData(
            section_id=section_id,
            data=final_data,
            confidence=confidence,
            missing_fields=missing_fields,
            data_origin=data_origin,
            warnings=warnings,
        )

        logger.info(
            f"Section {section_id} 数据准备完成: "
            f"置信度={confidence['score']:.2f}, "
            f"真实数据字段数={len(result.data) - len(missing_fields)}/{len(result.data)}, "
            f"降级警告数={len(warnings)}"
        )

        return result

    async def ensure_all_sections(
        self,
        project_context: dict[str, Any],
        existing_sections: Optional[dict[str, dict[str, Any]]] = None,
    ) -> dict[str, SectionData]:
        """确保所有 12 个 section 都有完整数据。"""
        existing_sections = existing_sections or {}
        results = {}

        for section_id in VALID_SECTION_IDS:
            section_data = await self.ensure_section_data(
                section_id=section_id,
                project_context=project_context,
                existing_data=existing_sections.get(section_id),
            )
            results[section_id] = section_data

        # 填充 CS 章节的 data_gaps（汇总所有 section 的缺失字段）
        cs = results.get("CS")
        if cs:
            all_gaps = []
            for sid, section in results.items():
                if sid == "CS" or not section.missing_fields:
                    continue
                for field in section.missing_fields:
                    origin = section.data_origin.get(field)
                    strategy = origin.source if origin else "unknown"
                    all_gaps.append({
                        "section": sid,
                        "field": field,
                        "strategy": strategy,
                    })
            cs.data["data_gaps"] = all_gaps

        return results

    def _is_empty_value(self, value: Any) -> bool:
        """判断一个值是否为空。"""
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        if isinstance(value, (list, dict)) and len(value) == 0:
            return True
        return False

    def _calculate_section_confidence(
        self, section_id: str, data: dict[str, Any], data_origin: dict[str, FieldOrigin]
    ) -> dict[str, Any]:
        """计算 section 的置信度。

        基于：
        1. 真实数据比例（权重 0.4）
        2. 各降级策略的惩罚（权重 0.4）
        3. 数据完整性（权重 0.2）
        """
        if not data_origin:
            return compute_evidence_confidence(source=0.0)

        # 1. 真实数据比例
        real_count = sum(1 for origin in data_origin.values() if origin.source == "real")
        real_ratio = real_count / len(data_origin) if data_origin else 0.0

        # 2. 平均置信度惩罚
        total_penalty = sum(
            origin.confidence_penalty for origin in data_origin.values()
        )
        avg_penalty = total_penalty / len(data_origin) if data_origin else 0.0
        penalty_score = max(0.0, 1.0 - avg_penalty)

        # 3. 数据完整性（必填字段都填充了就是 1.0）
        completeness = 1.0 if data else 0.0

        # 加权计算最终得分
        score = real_ratio * 0.4 + penalty_score * 0.4 + completeness * 0.2

        # 计算 7 维度置信度分解
        source_quality = real_ratio  # 数据源质量
        coverage = completeness  # 数据覆盖度
        freshness = 0.8  # 新鲜度（暂时固定）
        independent_cross = 0.5  # 交叉验证（暂时固定）
        geographic_relevance = 0.7 if self.city else 0.3  # 地理相关性
        method_fit = 0.6  # 方法适配度
        stability = 0.7  # 稳定性

        return compute_evidence_confidence(
            source=source_quality,
            coverage=coverage,
            freshness=freshness,
            independent_cross=independent_cross,
            geographic_relevance=geographic_relevance,
            method_fit=method_fit,
            stability=stability,
            cap=score,
        )

    def calculate_overall_confidence(self, sections: dict[str, SectionData]) -> dict[str, Any]:
        """计算整份报告的整体置信度。"""
        if not sections:
            return compute_evidence_confidence(source=0.0)

        section_scores = [
            section.confidence.get("score", 0.0)
            for section in sections.values()
        ]

        avg_score = sum(section_scores) / len(section_scores)

        return {
            "score": avg_score,
            "level": compute_evidence_confidence(source=avg_score)["level"],
            "level_label": compute_evidence_confidence(source=avg_score)["level_label"],
            "section_breakdown": {
                sid: section.confidence.get("score", 0.0)
                for sid, section in sections.items()
            },
            "generated_at": datetime.now().isoformat(),
        }

    def get_data_quality_report(self, sections: dict[str, SectionData]) -> dict[str, Any]:
        """生成数据质量报告，用于 CS 章节展示。"""
        quality_by_section = {}
        total_fields = 0
        real_fields = 0
        fallback_by_strategy = {}

        for section_id, section in sections.items():
            section_fields = len(section.data)
            section_real = sum(
                1 for origin in section.data_origin.values()
                if origin.source == "real"
            )

            total_fields += section_fields
            real_fields += section_real

            for origin in section.data_origin.values():
                if origin.source != "real":
                    fallback_by_strategy[origin.source] = (
                        fallback_by_strategy.get(origin.source, 0) + 1
                    )

            quality_by_section[section_id] = {
                "total_fields": section_fields,
                "real_fields": section_real,
                "real_ratio": section_real / section_fields if section_fields else 0.0,
                "confidence_score": section.confidence.get("score", 0.0),
                "warnings_count": len(section.warnings),
            }

        overall_confidence = self.calculate_overall_confidence(sections)

        return {
            "overall_confidence": overall_confidence,
            "quality_by_section": quality_by_section,
            "summary": {
                "total_fields": total_fields,
                "real_fields": real_fields,
                "real_ratio": real_fields / total_fields if total_fields else 0.0,
                "fallback_fields": total_fields - real_fields,
                "fallback_by_strategy": fallback_by_strategy,
            },
            "generated_at": datetime.now().isoformat(),
        }
