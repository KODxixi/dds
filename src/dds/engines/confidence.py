"""Deterministic confidence assessment derived only from EvidenceRecord facts.

The engine intentionally ignores ``EvidenceRecord.confidence``.  Agent- or
adapter-supplied totals are not inputs to the score and therefore cannot wash
missing provenance, stale observations, or weak corroboration.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from dds.contracts import CONFIDENCE_LEVEL_LABELS, confidence_level
from dds.domain import DataRequirement, EvidenceRecord, EvidenceType, ProjectContext


DIMENSION_WEIGHTS = {
    "source": 0.25,
    "coverage": 0.15,
    "freshness": 0.15,
    "corroboration": 0.15,
    "geography": 0.15,
    "method": 0.10,
    "stability": 0.05,
}

_TYPE_CAPS = {
    EvidenceType.OBSERVED_FACT: 1.0,
    EvidenceType.SOCIAL_OBSERVATION: 0.65,
    EvidenceType.ANALYSIS_INFERENCE: 0.70,
    EvidenceType.MODEL_SIMULATION: 0.75,
    EvidenceType.TRADITIONAL_INTERPRETATION: 0.35,
}


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(1.0, max(0.0, number))


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


def _geography_match(actual: str, target: str) -> float:
    actual_value = str(actual or "").strip().casefold()
    target_value = str(target or "").strip().casefold()
    if not actual_value or not target_value:
        return 0.0
    if actual_value == target_value:
        return 1.0
    if actual_value in target_value or target_value in actual_value:
        return 0.7
    return 0.0


@dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    """Seven evidence-derived dimensions plus the weighted, capped score."""

    score: float
    level: str
    dimensions: Mapping[str, float]
    breakdown: Mapping[str, Mapping[str, float]]
    actionable: bool
    cap: float
    evidence_count: int
    traceable_evidence_count: int
    required_metric_count: int
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "level": self.level,
            "level_label": CONFIDENCE_LEVEL_LABELS[self.level],
            "dimensions": dict(self.dimensions),
            "breakdown": {
                key: dict(value) for key, value in self.breakdown.items()
            },
            "actionable": self.actionable,
            "cap": self.cap,
            "evidence_count": self.evidence_count,
            "traceable_evidence_count": self.traceable_evidence_count,
            "required_metric_count": self.required_metric_count,
            "warnings": list(self.warnings),
            "derived_from_evidence": True,
        }


class ConfidenceEngine:
    """Calculate confidence without accepting a caller-supplied total score."""

    def __init__(
        self,
        *,
        actionable_threshold: float = 0.55,
        default_max_age_days: int = 730,
        corroborating_sources: int = 2,
    ) -> None:
        self.actionable_threshold = _clamp(actionable_threshold)
        self.default_max_age_days = max(1, int(default_max_age_days))
        self.corroborating_sources = max(2, int(corroborating_sources))

    def assess(
        self,
        evidence_records: Iterable[EvidenceRecord | Mapping[str, Any]],
        *,
        requirements: Iterable[DataRequirement | Mapping[str, Any] | str] = (),
        project_context: ProjectContext | Mapping[str, Any] | None = None,
        as_of: date | datetime | str | None = None,
    ) -> ConfidenceAssessment:
        records = [EvidenceRecord.from_mapping(item) for item in evidence_records]
        requirement_list = [self._coerce_requirement(item) for item in requirements]
        required = [item for item in requirement_list if item.required]
        context = ProjectContext.from_mapping(project_context)
        reference = (
            _parse_datetime(as_of or context.base_date)
            or datetime.now(timezone.utc)
        )
        traceable = [item for item in records if item.has_traceable_source]
        warnings = self._warnings(records, required, reference)

        dimensions = {
            "source": self._source_dimension(records),
            "coverage": self._coverage_dimension(records, required),
            "freshness": self._freshness_dimension(
                traceable,
                required,
                reference,
            ),
            "corroboration": self._corroboration_dimension(traceable, required),
            "geography": self._geography_dimension(traceable, required, context),
            "method": self._method_dimension(traceable),
            "stability": self._stability_dimension(traceable),
        }
        breakdown = {
            name: {
                "value": round(_clamp(value), 6),
                "weight": weight,
                "contribution": round(_clamp(value) * weight, 6),
            }
            for name, (value, weight) in (
                (name, (dimensions[name], DIMENSION_WEIGHTS[name]))
                for name in DIMENSION_WEIGHTS
            )
        }
        uncapped = sum(item["contribution"] for item in breakdown.values())
        cap = self._evidence_cap(traceable, dimensions["coverage"])
        score = round(min(uncapped, cap), 6)
        level = confidence_level(score)
        return ConfidenceAssessment(
            score=score,
            level=level,
            dimensions={key: round(_clamp(value), 6) for key, value in dimensions.items()},
            breakdown=breakdown,
            actionable=score >= self.actionable_threshold,
            cap=cap,
            evidence_count=len(records),
            traceable_evidence_count=len(traceable),
            required_metric_count=len(required),
            warnings=tuple(warnings),
        )

    # Friendly aliases for callers migrating from earlier scoring helpers.
    calculate = assess
    score = assess

    def _coerce_requirement(
        self,
        value: DataRequirement | Mapping[str, Any] | str,
    ) -> DataRequirement:
        if isinstance(value, str):
            return DataRequirement(metric_id=value)
        return DataRequirement.from_mapping(value)

    def _source_dimension(self, records: list[EvidenceRecord]) -> float:
        if not records:
            return 0.0
        values = []
        for item in records:
            # A human-readable label is not enough.  ID + reference/hash is the
            # minimum traceability contract; retaining both ref and hash earns
            # the full reproducibility score.
            value = 0.5 if str(item.source_id or "").strip() else 0.0
            value += 0.25 if str(item.source_ref or "").strip() else 0.0
            value += 0.25 if str(item.source_hash or "").strip() else 0.0
            if not item.has_traceable_source:
                value = 0.0
            values.append(value)
        return sum(values) / len(values)

    def _coverage_dimension(
        self,
        records: list[EvidenceRecord],
        requirements: list[DataRequirement],
    ) -> float:
        traceable = [item for item in records if item.has_traceable_source]
        if requirements:
            scores = []
            for requirement in requirements:
                count = sum(
                    1 for item in traceable if item.metric_id == requirement.metric_id
                )
                target = max(1, requirement.min_evidence_count)
                scores.append(min(1.0, count / target))
            return sum(scores) / len(scores)
        if not records:
            return 0.0
        return len(traceable) / len(records)

    def _freshness_dimension(
        self,
        records: list[EvidenceRecord],
        requirements: list[DataRequirement],
        reference: datetime,
    ) -> float:
        if not records:
            return 0.0
        by_metric = {item.metric_id: item for item in requirements}
        scores = []
        for item in records:
            observed = _parse_datetime(item.effective_at)
            if observed is None:
                scores.append(0.0)
                continue
            age_days = (reference - observed).total_seconds() / 86400
            if age_days < -1:
                scores.append(0.0)
                continue
            max_age = (
                by_metric[item.metric_id].max_age_days
                if item.metric_id in by_metric
                and by_metric[item.metric_id].max_age_days is not None
                else self.default_max_age_days
            )
            max_age = max(1, int(max_age))
            scores.append(max(0.0, 1.0 - max(0.0, age_days) / max_age))
        return sum(scores) / len(scores)

    def _corroboration_dimension(
        self,
        records: list[EvidenceRecord],
        requirements: list[DataRequirement],
    ) -> float:
        metrics = (
            [item.metric_id for item in requirements]
            if requirements
            else sorted({item.metric_id for item in records if item.metric_id})
        )
        if not metrics:
            return 0.0
        scores = []
        denominator = self.corroborating_sources - 1
        for metric_id in metrics:
            source_count = len(
                {
                    item.source_id
                    for item in records
                    if item.metric_id == metric_id and item.source_id
                }
            )
            scores.append(
                min(1.0, max(0.0, (source_count - 1) / denominator))
            )
        return sum(scores) / len(scores)

    def _geography_dimension(
        self,
        records: list[EvidenceRecord],
        requirements: list[DataRequirement],
        context: ProjectContext,
    ) -> float:
        if not records:
            return 0.0
        targets = {item.metric_id: item.geography for item in requirements}
        scores = [
            _geography_match(
                item.geography,
                targets.get(item.metric_id) or context.target_geography,
            )
            for item in records
        ]
        return sum(scores) / len(scores)

    def _method_dimension(self, records: list[EvidenceRecord]) -> float:
        if not records:
            return 0.0
        return sum(bool(str(item.method or "").strip()) for item in records) / len(records)

    def _stability_dimension(self, records: list[EvidenceRecord]) -> float:
        if not records:
            return 0.0
        scores = []
        for item in records:
            try:
                sample_size = max(0.0, float(item.sample_size or 0.0))
            except (TypeError, ValueError):
                sample_size = 0.0
            sample_score = (
                min(1.0, math.log10(sample_size + 1.0) / 3.0)
                if sample_size
                else 0.0
            )
            limitation_penalty = min(0.75, len(item.limitations) * 0.15)
            scores.append(max(0.0, sample_score - limitation_penalty))
        return sum(scores) / len(scores)

    def _evidence_cap(
        self,
        records: list[EvidenceRecord],
        coverage: float,
    ) -> float:
        if not records or coverage <= 0.0:
            return 0.0
        return max(_TYPE_CAPS[item.evidence_type] for item in records)

    def _warnings(
        self,
        records: list[EvidenceRecord],
        requirements: list[DataRequirement],
        reference: datetime,
    ) -> list[str]:
        warnings = []
        for item in records:
            identifier = item.evidence_id or "<missing-id>"
            if not item.has_traceable_source:
                warnings.append(f"{identifier}: untraceable source")
            if _parse_datetime(item.effective_at) is None:
                warnings.append(f"{identifier}: missing observed_at/as_of")
            if not item.method:
                warnings.append(f"{identifier}: missing method")
        for requirement in requirements:
            matching = [item for item in records if item.metric_id == requirement.metric_id]
            if len(matching) < max(1, requirement.min_evidence_count):
                warnings.append(f"{requirement.metric_id}: insufficient coverage")
            if requirement.max_age_days is not None:
                for item in matching:
                    observed = _parse_datetime(item.effective_at)
                    if observed is None:
                        continue
                    age_days = (reference - observed).total_seconds() / 86400
                    if age_days > requirement.max_age_days:
                        warnings.append(f"{item.evidence_id}: stale evidence")
        return list(dict.fromkeys(warnings))


def assess_evidence_confidence(
    evidence_records: Iterable[EvidenceRecord | Mapping[str, Any]],
    **kwargs: Any,
) -> ConfidenceAssessment:
    """Functional convenience wrapper around :class:`ConfidenceEngine`."""

    return ConfidenceEngine().assess(evidence_records, **kwargs)


__all__ = [
    "DIMENSION_WEIGHTS",
    "ConfidenceAssessment",
    "ConfidenceEngine",
    "assess_evidence_confidence",
]
