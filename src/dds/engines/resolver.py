"""Fail-closed evidence resolution for individual DDS metrics."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from dds.domain import (
    DataRequirement,
    EvidenceRecord,
    EvidenceResolutionStatus,
    EvidenceType,
    ProjectContext,
    ResolvedField,
    ResolvedStatus,
)
from dds.engines.confidence import ConfidenceAssessment, ConfidenceEngine


def _parse_datetime(value: date | datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class EvidenceResolution:
    """Resolved metric value and its fail-closed evidence semantic status."""

    metric_id: str
    status: EvidenceResolutionStatus
    value: Any
    evidence_refs: tuple[str, ...]
    confidence: ConfidenceAssessment
    reason: str = ""
    conflicting_values: tuple[Any, ...] = ()
    stale_evidence_refs: tuple[str, ...] = ()

    @property
    def fail_closed(self) -> bool:
        return self.status in {
            EvidenceResolutionStatus.UNKNOWN,
            EvidenceResolutionStatus.CONFLICT,
            EvidenceResolutionStatus.STALE,
        }

    def to_resolved_field(self) -> ResolvedField:
        status_map = {
            EvidenceResolutionStatus.VERIFIED: ResolvedStatus.RESOLVED,
            EvidenceResolutionStatus.ESTIMATED: ResolvedStatus.PARTIAL,
            EvidenceResolutionStatus.SIMULATED: ResolvedStatus.PARTIAL,
            EvidenceResolutionStatus.UNKNOWN: ResolvedStatus.UNKNOWN,
            EvidenceResolutionStatus.CONFLICT: ResolvedStatus.UNKNOWN,
            EvidenceResolutionStatus.STALE: ResolvedStatus.UNKNOWN,
        }
        return ResolvedField(
            status=status_map[self.status],
            value=None if self.fail_closed else self.value,
            evidence_refs=list(self.evidence_refs),
            assumptions=[],
            confidence=self.confidence.to_dict(),
            reason=self.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "status": self.status.value,
            "value": self.value,
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence.to_dict(),
            "reason": self.reason,
            "conflicting_values": list(self.conflicting_values),
            "stale_evidence_refs": list(self.stale_evidence_refs),
            "fail_closed": self.fail_closed,
        }


class EvidenceResolver:
    """Resolve one metric while refusing conflicts, stale data, or no source."""

    def __init__(
        self,
        confidence_engine: ConfidenceEngine | None = None,
        *,
        default_max_age_days: int = 730,
        numeric_conflict_tolerance: float = 0.0,
    ) -> None:
        self.confidence_engine = confidence_engine or ConfidenceEngine(
            default_max_age_days=default_max_age_days
        )
        self.default_max_age_days = max(1, int(default_max_age_days))
        self.numeric_conflict_tolerance = max(
            0.0,
            float(numeric_conflict_tolerance),
        )

    def resolve(
        self,
        requirement: DataRequirement | Mapping[str, Any] | str,
        evidence_records: Iterable[EvidenceRecord | Mapping[str, Any]],
        *,
        project_context: ProjectContext | Mapping[str, Any] | None = None,
        as_of: date | datetime | str | None = None,
    ) -> EvidenceResolution:
        requirement_value = self._coerce_requirement(requirement)
        context = ProjectContext.from_mapping(project_context)
        candidates = [
            EvidenceRecord.from_mapping(item)
            for item in evidence_records
            if self._metric_id(item) == requirement_value.metric_id
        ]
        reference = (
            _parse_datetime(as_of or requirement_value.as_of or context.base_date)
            or datetime.now(timezone.utc)
        )
        assessment = self.confidence_engine.assess(
            candidates,
            requirements=[requirement_value],
            project_context=context,
            as_of=reference,
        )
        if not candidates:
            return self._result(
                requirement_value.metric_id,
                EvidenceResolutionStatus.UNKNOWN,
                None,
                (),
                assessment,
                "没有匹配该 metric_id 的 EvidenceRecord",
            )

        untraceable = [item for item in candidates if not item.has_traceable_source]
        if untraceable:
            identifiers = [item.evidence_id or "<missing-id>" for item in untraceable]
            return self._result(
                requirement_value.metric_id,
                EvidenceResolutionStatus.UNKNOWN,
                None,
                tuple(item.evidence_id for item in candidates if item.evidence_id),
                assessment,
                f"存在无来源证据，fail-closed: {identifiers}",
            )

        if requirement_value.evidence_types:
            allowed = set(requirement_value.evidence_types)
            disallowed = [
                item for item in candidates if item.evidence_type not in allowed
            ]
            if disallowed:
                return self._result(
                    requirement_value.metric_id,
                    EvidenceResolutionStatus.UNKNOWN,
                    None,
                    tuple(item.evidence_id for item in candidates if item.evidence_id),
                    assessment,
                    "证据类型不符合 DataRequirement.evidence_types",
                )

        current, stale = self._partition_stale(
            candidates,
            requirement_value,
            reference,
        )
        if not current:
            return self._result(
                requirement_value.metric_id,
                EvidenceResolutionStatus.STALE,
                None,
                tuple(item.evidence_id for item in candidates if item.evidence_id),
                assessment,
                "所有可追溯证据均已陈旧或缺少 observed_at/as_of",
                stale_evidence_refs=tuple(
                    item.evidence_id for item in stale if item.evidence_id
                ),
            )

        conflict_values = self._conflicting_values(current, requirement_value)
        if conflict_values:
            current_assessment = self.confidence_engine.assess(
                current,
                requirements=[requirement_value],
                project_context=context,
                as_of=reference,
            )
            return self._result(
                requirement_value.metric_id,
                EvidenceResolutionStatus.CONFLICT,
                None,
                tuple(item.evidence_id for item in current if item.evidence_id),
                current_assessment,
                "多个当前、可追溯来源给出冲突值，禁止自动择一",
                conflicting_values=tuple(conflict_values),
                stale_evidence_refs=tuple(
                    item.evidence_id for item in stale if item.evidence_id
                ),
            )

        current_assessment = self.confidence_engine.assess(
            current,
            requirements=[requirement_value],
            project_context=context,
            as_of=reference,
        )
        evidence_types = {item.evidence_type for item in current}
        if EvidenceType.OBSERVED_FACT in evidence_types:
            status = (
                EvidenceResolutionStatus.VERIFIED
                if current_assessment.actionable
                else EvidenceResolutionStatus.ESTIMATED
            )
        elif EvidenceType.MODEL_SIMULATION in evidence_types:
            status = EvidenceResolutionStatus.SIMULATED
        else:
            status = EvidenceResolutionStatus.ESTIMATED
        return self._result(
            requirement_value.metric_id,
            status,
            current[0].value,
            tuple(item.evidence_id for item in current if item.evidence_id),
            current_assessment,
            self._resolution_reason(status, stale),
            stale_evidence_refs=tuple(
                item.evidence_id for item in stale if item.evidence_id
            ),
        )

    def resolve_many(
        self,
        requirements: Iterable[DataRequirement | Mapping[str, Any] | str],
        evidence_records: Iterable[EvidenceRecord | Mapping[str, Any]],
        **kwargs: Any,
    ) -> dict[str, EvidenceResolution]:
        evidence = [EvidenceRecord.from_mapping(item) for item in evidence_records]
        result = {}
        for requirement in requirements:
            coerced = self._coerce_requirement(requirement)
            result[coerced.metric_id] = self.resolve(
                coerced,
                evidence,
                **kwargs,
            )
        return result

    def _coerce_requirement(
        self,
        value: DataRequirement | Mapping[str, Any] | str,
    ) -> DataRequirement:
        if isinstance(value, str):
            return DataRequirement(metric_id=value)
        return DataRequirement.from_mapping(value)

    def _metric_id(self, value: EvidenceRecord | Mapping[str, Any]) -> str:
        if isinstance(value, EvidenceRecord):
            return value.metric_id
        return str(value.get("metric_id") or "")

    def _partition_stale(
        self,
        records: list[EvidenceRecord],
        requirement: DataRequirement,
        reference: datetime,
    ) -> tuple[list[EvidenceRecord], list[EvidenceRecord]]:
        max_age_days = (
            requirement.max_age_days
            if requirement.max_age_days is not None
            else self.default_max_age_days
        )
        current = []
        stale = []
        for item in records:
            observed = _parse_datetime(item.effective_at)
            if observed is None:
                stale.append(item)
                continue
            age_days = (reference - observed).total_seconds() / 86400
            if age_days < -1 or age_days > max_age_days:
                stale.append(item)
            else:
                current.append(item)
        return current, stale

    def _conflicting_values(
        self,
        records: list[EvidenceRecord],
        requirement: DataRequirement,
    ) -> list[Any]:
        if len(records) < 2:
            return []
        units = {item.unit for item in records}
        if len(units) > 1:
            return [item.value for item in records]
        values = [item.value for item in records]
        if all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in values):
            tolerance = float(
                requirement.metadata.get(
                    "conflict_tolerance",
                    self.numeric_conflict_tolerance,
                )
            )
            if max(float(item) for item in values) - min(float(item) for item in values) > tolerance:
                return list(dict.fromkeys(values))
            return []
        canonical_values: list[Any] = []
        seen: set[str] = set()
        for item in values:
            canonical = json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if canonical not in seen:
                seen.add(canonical)
                canonical_values.append(item)
        return canonical_values if len(canonical_values) > 1 else []

    def _resolution_reason(
        self,
        status: EvidenceResolutionStatus,
        stale: list[EvidenceRecord],
    ) -> str:
        base = {
            EvidenceResolutionStatus.VERIFIED: "当前可追溯 observed_fact 通过证据置信阈值",
            EvidenceResolutionStatus.ESTIMATED: "可追溯证据可形成估计，但不足以标为 verified",
            EvidenceResolutionStatus.SIMULATED: "结果来自 model_simulation，不得改标为 observed_fact",
        }[status]
        if stale:
            return f"{base}；另有 {len(stale)} 条陈旧证据未参与取值"
        return base

    def _result(
        self,
        metric_id: str,
        status: EvidenceResolutionStatus,
        value: Any,
        evidence_refs: tuple[str, ...],
        confidence: ConfidenceAssessment,
        reason: str,
        *,
        conflicting_values: tuple[Any, ...] = (),
        stale_evidence_refs: tuple[str, ...] = (),
    ) -> EvidenceResolution:
        return EvidenceResolution(
            metric_id=metric_id,
            status=status,
            value=value,
            evidence_refs=evidence_refs,
            confidence=confidence,
            reason=reason,
            conflicting_values=conflicting_values,
            stale_evidence_refs=stale_evidence_refs,
        )


def resolve_evidence(
    requirement: DataRequirement | Mapping[str, Any] | str,
    evidence_records: Iterable[EvidenceRecord | Mapping[str, Any]],
    **kwargs: Any,
) -> EvidenceResolution:
    return EvidenceResolver().resolve(requirement, evidence_records, **kwargs)


__all__ = [
    "EvidenceResolution",
    "EvidenceResolver",
    "resolve_evidence",
]

