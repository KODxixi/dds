"""Truth-safe fallback states for unavailable data.

Fallback is disclosure, not data generation.  Without a traceable
``EvidenceRecord`` this module may return only ``unknown`` or
``human_input``; it never manufactures city, project-type, or national
benchmarks from a location label.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from dds.contracts import (
    FALLBACK_STRATEGIES,
    FIELD_DESCRIPTIONS,
    FieldOrigin,
    ResolvedField,
    ResolvedStatus,
)

logger = logging.getLogger(__name__)


class FallbackEngine:
    """Return explicit unresolved states while preserving the legacy tuple API."""

    STRATEGY_PRIORITY = ["human_input", "unknown"]

    # These values can only be supplied by the project team/brief.  All other
    # missing metrics are recorded as unknown pending a real data source.
    HUMAN_INPUT_FIELDS = {
        "project_id",
        "decision_question",
        "evidence_boundary",
        "base_date",
        "redline",
        "engineering_constraints",
        "option_1",
        "option_2",
        "option_3",
        "comparison_matrix",
        "recommended_option",
        "elimination_reasons",
        "validation_thresholds",
        "site_plan",
        "floor_plans",
        "facade",
        "landscape",
        "show_area",
        "owners",
        "acceptance_criteria",
    }

    _UNSUPPORTED_BENCHMARK_STRATEGIES = {
        "city_benchmark",
        "project_type_average",
        "national_benchmark",
    }

    def __init__(self, city: str = "", project_type: str = ""):
        # Kept for API compatibility and human-readable gap context only.  The
        # labels are never treated as sufficient evidence for a benchmark.
        self.city = city
        self.project_type = project_type

    def get_fallback(self, section_id: str, field: str) -> tuple[ResolvedField, FieldOrigin]:
        strategy = self._select_fallback_strategy(section_id, field)
        return self._build_unresolved(section_id, field, strategy)

    def _select_fallback_strategy(self, section_id: str, field: str) -> str:
        del section_id  # classification is field-based, but signature is stable
        return "human_input" if field in self.HUMAN_INPUT_FIELDS else "unknown"

    def _build_unresolved(
        self,
        section_id: str,
        field: str,
        strategy: str,
        *,
        requested_strategy: str = "",
    ) -> tuple[ResolvedField, FieldOrigin]:
        canonical = "human_input" if strategy == "human_input_required" else strategy
        if canonical not in {"unknown", "human_input"}:
            canonical = "unknown"
        config = FALLBACK_STRATEGIES[canonical]
        description = FIELD_DESCRIPTIONS.get(field, field)
        reason = config["template"].format(description=description)
        if requested_strategy in self._UNSUPPORTED_BENCHMARK_STRATEGIES:
            reason = (
                f"{reason} 请求的 {requested_strategy} 没有 EvidenceRecord 来源，"
                "因此未作为基准值使用。"
            )
        status = (
            ResolvedStatus.HUMAN_INPUT
            if canonical == "human_input"
            else ResolvedStatus.UNKNOWN
        )
        value = ResolvedField(
            status=status,
            value=None,
            evidence_refs=[],
            assumptions=[],
            confidence={"score": 0.0, "derived_from_evidence": True},
            reason=reason,
        )
        origin = FieldOrigin(
            source=status.value,
            label=config["label"],
            confidence_penalty=1.0,
            evidence_refs=[],
            resolution_status=status,
        )
        logger.debug(
            "字段 [%s.%s] 无可核验证据，状态=%s",
            section_id,
            field,
            status.value,
        )
        return value, origin

    def _generate_fallback_value(self, section_id: str, field: str, strategy: str) -> ResolvedField:
        """Compatibility hook that still obeys the truth-safe fallback policy."""

        value, _ = self._build_unresolved(
            section_id,
            field,
            strategy,
            requested_strategy=strategy,
        )
        return value

    def _get_city_benchmark_value(self, section_id: str, field: str, description: str) -> ResolvedField:
        del description
        return self._generate_fallback_value(section_id, field, "city_benchmark")

    def _get_project_type_average_value(
        self, section_id: str, field: str, description: str
    ) -> ResolvedField:
        del description
        return self._generate_fallback_value(section_id, field, "project_type_average")

    def _get_national_benchmark_value(
        self, section_id: str, field: str, description: str
    ) -> ResolvedField:
        del description
        return self._generate_fallback_value(section_id, field, "national_benchmark")

    def _get_human_input_fallback(
        self, section_id: str, field: str
    ) -> tuple[ResolvedField, FieldOrigin]:
        return self._build_unresolved(section_id, field, "human_input")

    def _format_human_input_template(self, description: str) -> str:
        return FALLBACK_STRATEGIES["human_input"]["template"].format(
            description=description
        )

    def get_fallback_by_strategy(
        self, strategy: str, section_id: str, field: str
    ) -> tuple[ResolvedField, FieldOrigin]:
        """Keep the old entry point without honouring source-less benchmarks."""

        if strategy not in FALLBACK_STRATEGIES:
            raise ValueError(f"未知降级策略: {strategy}")
        requested = strategy
        if strategy in self._UNSUPPORTED_BENCHMARK_STRATEGIES:
            strategy = "unknown"
        return self._build_unresolved(
            section_id,
            field,
            strategy,
            requested_strategy=requested,
        )

    @staticmethod
    def is_fallback_value(value: Any) -> bool:
        if isinstance(value, ResolvedField):
            return value.status in {
                ResolvedStatus.UNKNOWN,
                ResolvedStatus.HUMAN_INPUT,
                ResolvedStatus.PARTIAL,
            }
        if isinstance(value, Mapping) and "status" in value:
            return str(value.get("status")) in {
                "unknown",
                "human_input",
                "human_input_required",
                "partial",
            }
        if not isinstance(value, str):
            return False
        markers = [
            FALLBACK_STRATEGIES["unknown"]["label"],
            FALLBACK_STRATEGIES["human_input"]["label"],
        ]
        return any(marker in value for marker in markers)

    @staticmethod
    def get_confidence_penalty_for_strategy(strategy: str) -> float:
        if strategy in {"unknown", "human_input", "human_input_required"}:
            return 1.0
        # A requested benchmark without evidence is also fully unavailable.
        return 1.0


__all__ = ["FallbackEngine"]
