"""Deterministic qualification and coverage checks for research candidates."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping


def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def qualify_candidates(
    candidates: Iterable[Mapping[str, Any]],
    source_plan: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Qualify candidates and calculate required metric coverage."""
    requirements = {str(item["metric_id"]): dict(item) for item in source_plan}
    results: list[dict[str, Any]] = []

    for raw_candidate in candidates:
        candidate = dict(raw_candidate)
        reasons: list[str] = []
        metric_ids = tuple(
            metric_id
            for metric_id in candidate.get("metric_ids", ())
            if metric_id in requirements
        )
        traceable = bool(
            str(candidate.get("source_ref") or "").strip()
            or str(candidate.get("source_hash") or "").strip()
        )
        published_at = _date(candidate.get("published_at"))

        if not traceable:
            status = "rejected"
            reasons.append("untraceable_source")
        elif not metric_ids:
            status = "rejected"
            reasons.append("no_required_metric_match")
        elif published_at is None:
            status = "needs_review"
            reasons.append("published_at_missing_or_invalid")
        else:
            expired = any(
                requirement.get("max_age_days") is not None
                and published_at
                < (_date(requirement.get("as_of")) or date.today())
                - timedelta(days=int(requirement["max_age_days"]))
                for metric_id in metric_ids
                for requirement in (requirements[metric_id],)
            )
            if expired:
                status = "rejected"
                reasons.append("outside_required_time_window")
            else:
                status = "qualified"

        candidate["qualification_status"] = status
        candidate["qualification_reasons"] = reasons
        results.append(candidate)

    coverage = []
    for metric_id, requirement in requirements.items():
        required_count = int(requirement.get("required_count") or 0)
        qualified_count = sum(
            item["qualification_status"] == "qualified"
            and metric_id in item.get("metric_ids", ())
            for item in results
        )
        coverage.append(
            {
                "metric_id": metric_id,
                "required_count": required_count,
                "qualified_count": qualified_count,
                "missing_count": max(0, required_count - qualified_count),
                "status": "covered" if qualified_count >= required_count else "gap",
            }
        )

    required = [item for item in coverage if item["required_count"] > 0]
    evidence_ready = bool(required) and all(item["status"] == "covered" for item in required)
    readiness = {
        "research_status": "research_completed",
        "evidence_status": "evidence_ready" if evidence_ready else "evidence_gaps",
        "decision_status": "not_assessed",
        "evidence_ready": evidence_ready,
        "decision_ready": False,
        "coverage": coverage,
    }
    return results, readiness


__all__ = ["qualify_candidates"]
