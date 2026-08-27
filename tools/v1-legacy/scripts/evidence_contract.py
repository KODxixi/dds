"""Shared evidence vocabulary and empirical-confidence calculation for DDS.

The module is intentionally pure: it does not read files, call services, or
modify an existing report.  Callers may therefore reuse the same contract in
ingestion, analysis, and report compilation without introducing side effects.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


EVIDENCE_TYPES = (
    "observed_fact",
    "social_observation",
    "analysis_inference",
    "model_simulation",
    "traditional_interpretation",
)

EVIDENCE_TYPE_LABELS = {
    "observed_fact": "实测／观察事实",
    "social_observation": "社媒观察",
    "analysis_inference": "分析推断",
    "model_simulation": "模型模拟",
    "traditional_interpretation": "传统文化解释",
}

CONFIDENCE_WEIGHTS = {
    "source": 0.25,
    "coverage": 0.15,
    "freshness": 0.15,
    "independent_cross": 0.15,
    "geographic_relevance": 0.15,
    "method_fit": 0.10,
    "stability": 0.05,
}

CONFIDENCE_LEVEL_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "undecidable": "不可判定",
}

_ALIASES = {
    "source_quality": "source",
    "source_strength": "source",
    "sample_coverage": "coverage",
    "data_freshness": "freshness",
    "independent_cross_validation": "independent_cross",
    "cross_check": "independent_cross",
    "geographic_fit": "geographic_relevance",
    "geo_relevance": "geographic_relevance",
    "method_adaptation": "method_fit",
    "simulation_stability": "stability",
}


def _clamp(value: Any) -> float:
    """Return a finite number constrained to the empirical 0..1 domain."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(1.0, max(0.0, number))


def confidence_level(score: float) -> str:
    """Map a 0..1 score to DDS's stable decision-support bands."""
    score = _clamp(score)
    if score >= 0.75:
        return "high"
    if score >= 0.55:
        return "medium"
    if score >= 0.35:
        return "low"
    return "undecidable"


def normalize_evidence_type(value: Any, default: str = "analysis_inference") -> str:
    """Return one of the five contract values, never an ad-hoc evidence type."""
    normalized = str(value or "").strip().lower()
    if normalized in EVIDENCE_TYPES:
        return normalized
    normalized_default = str(default or "").strip().lower()
    return (
        normalized_default
        if normalized_default in EVIDENCE_TYPES
        else "analysis_inference"
    )


def compute_evidence_confidence(
    source: float | Mapping[str, Any] = 0.0,
    coverage: float | None = None,
    freshness: float | None = None,
    independent_cross: float | None = None,
    geographic_relevance: float | None = None,
    method_fit: float | None = None,
    stability: float | None = None,
    *,
    cap: float | None = None,
    actionable_threshold: float = 0.55,
    **dimensions: Any,
) -> dict[str, Any]:
    """Compute the unified seven-dimension empirical confidence.

    ``source`` may be either the source dimension itself or a mapping carrying
    all dimensions.  Supporting a mapping keeps the contract convenient for
    JSON pipelines while the explicit parameters keep call sites auditable.
    Values that are absent, invalid, infinite, or out of range are constrained
    to 0..1 before weighting.  ``cap`` is useful for evidence classes (such as
    social observation) whose representativeness imposes a hard ceiling.
    """
    supplied: dict[str, Any]
    if isinstance(source, Mapping):
        supplied = dict(source)
    else:
        supplied = {"source": source}

    explicit = {
        "coverage": coverage,
        "freshness": freshness,
        "independent_cross": independent_cross,
        "geographic_relevance": geographic_relevance,
        "method_fit": method_fit,
        "stability": stability,
    }
    supplied.update({key: value for key, value in explicit.items() if value is not None})
    supplied.update(dimensions)
    for alias, canonical in _ALIASES.items():
        if canonical not in supplied and alias in supplied:
            supplied[canonical] = supplied[alias]

    values = {key: _clamp(supplied.get(key, 0.0)) for key in CONFIDENCE_WEIGHTS}
    breakdown = {
        key: {
            "value": value,
            "weight": CONFIDENCE_WEIGHTS[key],
            "contribution": round(value * CONFIDENCE_WEIGHTS[key], 6),
        }
        for key, value in values.items()
    }
    uncapped_score = sum(item["contribution"] for item in breakdown.values())
    applied_cap = _clamp(cap) if cap is not None else 1.0
    score = round(min(uncapped_score, applied_cap), 6)
    level = confidence_level(score)
    threshold = _clamp(actionable_threshold)

    return {
        "score": score,
        "level": level,
        "level_label": CONFIDENCE_LEVEL_LABELS[level],
        "breakdown": breakdown,
        "actionable": score >= threshold,
        "uncapped_score": round(uncapped_score, 6),
        "cap": applied_cap,
    }


# Descriptive aliases for callers that prefer the wording used in specifications.
compute_empirical_confidence = compute_evidence_confidence
build_evidence_confidence = compute_evidence_confidence


__all__ = [
    "CONFIDENCE_LEVEL_LABELS",
    "CONFIDENCE_WEIGHTS",
    "EVIDENCE_TYPES",
    "EVIDENCE_TYPE_LABELS",
    "build_evidence_confidence",
    "compute_empirical_confidence",
    "compute_evidence_confidence",
    "confidence_level",
    "normalize_evidence_type",
]
