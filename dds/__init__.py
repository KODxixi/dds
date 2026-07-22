"""DDS v2 - 数据驱动的地产决策报告引擎。

本版本核心改进：
1. 数据主动管理：检测缺失、自动搜索降级、永不空值
2. 契约强制校验：输出前必须通过 12 个 decision-unit 校验
3. 置信度透明化：每个字段标记来源，每个 section 有置信度
"""

__version__ = "2.0.0"

from dds.contracts import (
    CONFIDENCE_LEVEL_LABELS,
    CONFIDENCE_WEIGHTS,
    FALLBACK_STRATEGIES,
    FIELD_DESCRIPTIONS,
    REPORT_GROUPS,
    REPORT_UNITS,
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
    ContractViolationError,
    FieldOrigin,
    SectionData,
    ValidationResult,
    build_evidence_confidence,
    compute_empirical_confidence,
    compute_evidence_confidence,
    confidence_level,
    normalize_evidence_type,
)

__all__ = [
    "__version__",
    # 框架
    "REPORT_GROUPS",
    "REPORT_UNITS",
    "SECTION_REQUIREMENTS",
    "VALID_SECTION_IDS",
    # 证据
    "EVIDENCE_TYPES",
    "CONFIDENCE_LEVEL_LABELS",
    "CONFIDENCE_WEIGHTS",
    "normalize_evidence_type",
    "confidence_level",
    "compute_evidence_confidence",
    "compute_empirical_confidence",
    "build_evidence_confidence",
    # 数据结构
    "FieldOrigin",
    "SectionData",
    "ValidationResult",
    "ContractViolationError",
    # 降级
    "FALLBACK_STRATEGIES",
    "FIELD_DESCRIPTIONS",
]
