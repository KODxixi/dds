"""契约强制校验层 - 任何报告输出前必须通过校验。

核心原则：不通过契约校验的报告不允许输出。
校验内容：
1. 12 个 decision-unit 完整性
2. 每个 section 必填字段完整性
3. 置信度计算完整性
4. 数据来源标记一致性
"""

from __future__ import annotations

import logging
from typing import Any

from dds.contracts import (
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    ContractViolationError,
    SectionData,
    ValidationResult,
    compute_evidence_confidence,
)

logger = logging.getLogger(__name__)


class ContractEnforcer:
    """强制保证输出符合 DDS 契约规范。

    在报告生成后、输出前执行全面校验，
    不通过则抛出异常，拒绝输出。
    """

    def validate_report(
        self,
        sections: dict[str, SectionData],
        report_metadata: dict[str, Any],
    ) -> ValidationResult:
        """完整报告校验，不通过则拒绝输出。

        Args:
            sections: 所有 section 数据
            report_metadata: 报告元数据

        Returns:
            ValidationResult 包含校验结果、错误、警告
        """
        errors = []
        warnings = []

        # 1. 检查 12 个 decision-unit 是否全部存在
        for section_id in VALID_SECTION_IDS:
            if section_id not in sections:
                errors.append(f"缺少必填章节: {section_id}")
                continue

            section = sections[section_id]

            # 2. 检查 section 内部数据完整性
            field_errors = self._validate_section_fields(section)
            errors.extend(field_errors)

            # 3. 检查置信度计算
            if "confidence" not in section.__dict__ or not section.confidence:
                errors.append(f"章节 {section_id} 缺少置信度计算")

            # 4. 检查降级标记 - 收集警告（不阻止输出，但要明确告知）
            fallback_count = sum(
                1 for origin in section.data_origin.values()
                if origin.source != "real"
            )
            if fallback_count > 0:
                warnings.append(
                    f"章节 {section_id} 包含 {fallback_count} 个降级数据字段，"
                    f"已自动标注来源"
                )

        # 5. 检查 CS 章节 - 必须包含数据缺口汇总
        if "CS" in sections:
            cs_section = sections["CS"]
            if "data_gaps" not in cs_section.data:
                errors.append("CS 章节必须包含数据缺口汇总")
            if "sources" not in cs_section.data:
                errors.append("CS 章节必须包含数据源清单")
            if "methods" not in cs_section.data:
                errors.append("CS 章节必须包含分析方法说明")
            if "assumptions" not in cs_section.data:
                errors.append("CS 章节必须包含前提假设说明")

        # 6. 计算整体置信度
        overall_confidence = self._calculate_overall_confidence(sections)
        section_confidences = {
            sid: section.confidence.get("score", 0.0)
            for sid, section in sections.items()
        }

        # 7. 整体置信度检查
        if overall_confidence < 0.3:
            warnings.append(
                f"报告整体置信度较低 ({overall_confidence:.2f})，"
                f"建议补充更多真实数据"
            )

        result = ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            overall_confidence=overall_confidence,
            section_confidences=section_confidences,
        )

        if result.valid:
            logger.info(f"报告契约校验通过，整体置信度: {overall_confidence:.2f}")
        else:
            logger.error(f"报告契约校验失败，错误数: {len(errors)}")
            for error in errors:
                logger.error(f"  - {error}")

        return result

    def enforce_contract(
        self,
        sections: dict[str, SectionData],
        report_metadata: dict[str, Any],
    ) -> ValidationResult:
        """强制执行契约，不通过则抛出异常。"""
        result = self.validate_report(sections, report_metadata)
        result.raise_if_invalid()
        return result

    def _validate_section_fields(self, section: SectionData) -> list[str]:
        """校验单个 section 的字段完整性。"""
        errors = []
        required_fields = SECTION_REQUIREMENTS.get(section.section_id, [])

        for field in required_fields:
            if field not in section.data:
                errors.append(f"章节 {section.section_id} 缺少必填字段: {field}")

        # 即使字段存在，也要检查不是空值
        for field in required_fields:
            value = section.data.get(field)
            if self._is_empty_value(value):
                errors.append(
                    f"章节 {section.section_id} 字段 [{field}] 为空值，"
                    f"DataOrchestrator 应该已填充降级数据，请检查"
                )

        return errors

    def _is_empty_value(self, value: Any) -> bool:
        """判断一个值是否为空。"""
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        if isinstance(value, (list, dict)) and len(value) == 0:
            return True
        return False

    def _calculate_overall_confidence(self, sections: dict[str, SectionData]) -> float:
        """计算整体置信度（加权平均）。

        不同分组有不同权重：
        - SC (战略语境): 0.25
        - AD (产品方案): 0.40
        - VA (价值校验): 0.30
        - CS (置信状态): 0.05
        """
        group_weights = {
            "SC": 0.25,
            "AD": 0.40,
            "VA": 0.30,
            "CS": 0.05,
        }

        section_to_group = {
            "SC1": "SC", "SC2": "SC", "SC3": "SC",
            "AD1": "AD", "AD2": "AD", "AD3": "AD",
            "AD4": "AD", "AD5": "AD",
            "VA1": "VA", "VA2": "VA", "VA3": "VA",
            "CS": "CS",
        }

        weighted_scores = {}
        for section_id, section in sections.items():
            group = section_to_group.get(section_id, "SC")
            weight = group_weights.get(group, 0.25)
            score = section.confidence.get("score", 0.0)

            if group not in weighted_scores:
                weighted_scores[group] = []
            weighted_scores[group].append((score, weight))

        # 每组内部平均，再按组权重加权
        total_score = 0.0
        total_weight = 0.0

        for group, scores in weighted_scores.items():
            if not scores:
                continue
            # 组内平均
            group_avg = sum(s[0] for s in scores) / len(scores)
            # 使用组权重
            group_weight = group_weights.get(group, 0.25)
            total_score += group_avg * group_weight
            total_weight += group_weight

        return total_score / total_weight if total_weight > 0 else 0.0

    def get_validation_summary(self, result: ValidationResult) -> dict[str, Any]:
        """生成可读性好的校验摘要。"""
        confidence_level = compute_evidence_confidence(source=result.overall_confidence)

        return {
            "status": "PASS" if result.valid else "FAIL",
            "overall_confidence": {
                "score": result.overall_confidence,
                "level": confidence_level["level"],
                "label": confidence_level["level_label"],
            },
            "section_confidences": {
                sid: {
                    "score": score,
                    "label": compute_evidence_confidence(source=score)["level_label"],
                }
                for sid, score in result.section_confidences.items()
            },
            "errors_count": len(result.errors),
            "warnings_count": len(result.warnings),
            "errors": result.errors,
            "warnings": result.warnings,
        }
