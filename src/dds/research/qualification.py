"""Deterministic qualification and coverage checks for research candidates."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

_RESTRICTED_RIGHTS = {"forbidden", "unknown", "personal_data", "unlicensed"}
_INVALID_DOCUMENT_STATUS = {"expired", "superseded", "withdrawn"}
_WEB_SNIPPET_ROLES = {"public_web_candidate", "web_search_candidate"}
_FUTURE_DEMAND_METRIC = "SC2.future_demand_event_scan"
_FUTURE_OBSERVED_SOURCE_ROLES = {
    "government_record",
    "official_planning_document",
    "statutory_document",
    "corporate_official",
    "verified_first_party_document",
}


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


def _value(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


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
        candidate_geography = str(candidate.get("geography") or "").strip()
        source_role = str(candidate.get("source_role") or "").strip()
        evidence_type = _value(candidate.get("evidence_type"))
        document_status = str(
            candidate.get("document_status") or "effective"
        ).strip().lower()
        rights_status = str(
            candidate.get("rights_status") or ""
        ).strip().lower()

        if not traceable:
            status = "rejected"
            reasons.append("untraceable_source")
        elif not metric_ids:
            status = "rejected"
            reasons.append("no_required_metric_match")
        elif candidate.get("conflict"):
            status = "conflict"
            reasons.append("unresolved_source_conflict")
        elif document_status in _INVALID_DOCUMENT_STATUS:
            status = "rejected"
            reasons.append("document_not_effective")
        elif any(
            requirement.get("geography")
            and candidate_geography
            and str(requirement["geography"]) not in candidate_geography
            and candidate_geography not in str(requirement["geography"])
            for metric_id in metric_ids
            for requirement in (requirements[metric_id],)
        ):
            status = "rejected"
            reasons.append("outside_required_geography")
        elif any(
            requirement.get("allowed_source_roles")
            and source_role not in requirement["allowed_source_roles"]
            for metric_id in metric_ids
            for requirement in (requirements[metric_id],)
        ):
            status = "rejected"
            reasons.append("source_role_not_allowed")
        elif any(
            requirement.get("allowed_evidence_types")
            and evidence_type
            and evidence_type
            not in {
                _value(item)
                for item in requirement["allowed_evidence_types"]
            }
            for metric_id in metric_ids
            for requirement in (requirements[metric_id],)
        ):
            status = "rejected"
            reasons.append("evidence_type_not_allowed")
        else:
            expired = any(
                requirement.get("max_age_days") is not None
                and published_at is not None
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
                if rights_status in _RESTRICTED_RIGHTS or not rights_status:
                    reasons.append("rights_status_missing_or_restricted")
                if any(
                    requirement.get("allowed_evidence_types")
                    and not evidence_type
                    for metric_id in metric_ids
                    for requirement in (requirements[metric_id],)
                ):
                    reasons.append("evidence_type_missing_or_unclassified")
                if any(
                    requirement.get("min_sample_size")
                    and float(candidate.get("sample_size") or 0)
                    < float(requirement["min_sample_size"])
                    for metric_id in metric_ids
                    for requirement in (requirements[metric_id],)
                ):
                    reasons.append("sample_size_below_requirement")
                if published_at is None:
                    reasons.append("published_at_missing_or_invalid")
                if source_role in _WEB_SNIPPET_ROLES:
                    reasons.append("web_search_snippet_requires_source_review")

                for metric_id in metric_ids:
                    if metric_id != _FUTURE_DEMAND_METRIC:
                        continue
                    requirement_metadata = dict(
                        requirements[metric_id].get("metadata") or {}
                    )
                    future_scan = dict(
                        requirement_metadata.get("future_event_scan")
                        or {}
                    )
                    if not str(
                        candidate.get("event_status")
                        or candidate.get("status")
                        or ""
                    ).strip():
                        reasons.append("future_event_status_missing")
                    time_horizon = str(
                        candidate.get("time_horizon")
                        or candidate.get("event_time_window")
                        or candidate.get("time_window")
                        or ""
                    ).strip()
                    allowed_horizons = {
                        str(item)
                        for item in future_scan.get("time_horizons", ())
                        if str(item).strip()
                    }
                    if not time_horizon:
                        reasons.append("future_event_time_window_missing")
                    elif allowed_horizons and time_horizon not in allowed_horizons:
                        reasons.append("future_event_time_window_not_allowed")
                    counter_factors = candidate.get("counter_factors") or ()
                    if isinstance(counter_factors, str):
                        counter_factors = (counter_factors,)
                    if (
                        future_scan.get("requires_counter_factors")
                        and not any(str(item).strip() for item in counter_factors)
                    ):
                        reasons.append("future_event_counter_factors_missing")
                    if (
                        evidence_type == "observed_fact"
                        and source_role not in _FUTURE_OBSERVED_SOURCE_ROLES
                    ):
                        reasons.append(
                            "future_observed_source_not_authoritative"
                        )

                status = "needs_review" if reasons else "qualified"

        candidate["qualification_status"] = status
        candidate["qualification_reasons"] = reasons
        results.append(candidate)

    coverage = []
    for metric_id, requirement in requirements.items():
        required_count = int(requirement.get("required_count") or 0)
        qualified = [
            item
            for item in results
            if item["qualification_status"] == "qualified"
            and metric_id in item.get("metric_ids", ())
        ]
        qualified_count = len(qualified)
        requirement_metadata = dict(requirement.get("metadata") or {})
        future_scan = dict(
            requirement_metadata.get("future_event_scan") or {}
        )
        observed_required_count = int(
            future_scan.get("minimum_observed_evidence_count") or 0
        )
        observed_qualified_count = sum(
            _value(item.get("evidence_type")) == "observed_fact"
            for item in qualified
        )
        is_covered = (
            qualified_count >= required_count
            and observed_qualified_count >= observed_required_count
        )
        coverage_item = {
            "metric_id": metric_id,
            "required_count": required_count,
            "qualified_count": qualified_count,
            "missing_count": max(0, required_count - qualified_count),
            "status": "covered" if is_covered else "gap",
        }
        if observed_required_count:
            coverage_item.update(
                {
                    "observed_required_count": observed_required_count,
                    "observed_qualified_count": observed_qualified_count,
                    "observed_missing_count": max(
                        0,
                        observed_required_count - observed_qualified_count,
                    ),
                }
            )
        coverage.append(coverage_item)

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
