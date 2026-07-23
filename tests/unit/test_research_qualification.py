from __future__ import annotations

from dds.research.qualification import qualify_candidates


def _plan(required_count=1):
    return [
        {
            "metric_id": "SC2.competitors",
            "required_count": required_count,
            "as_of": "2026-07-22",
            "max_age_days": 180,
        }
    ]


def _candidate(**overrides):
    candidate = {
        "source_ref": "https://example.gov.cn/source",
        "source_hash": "a" * 64,
        "metric_ids": ["SC2.competitors"],
        "published_at": "2026-07-01",
    }
    candidate.update(overrides)
    return candidate


def test_qualified_candidate_can_satisfy_metric_coverage():
    candidates, readiness = qualify_candidates([_candidate()], _plan())
    assert candidates[0]["qualification_status"] == "qualified"
    assert readiness["evidence_status"] == "evidence_ready"
    assert readiness["evidence_ready"] is True
    assert readiness["decision_ready"] is False


def test_missing_date_requires_review_and_does_not_cover_metric():
    candidates, readiness = qualify_candidates([_candidate(published_at="")], _plan())
    assert candidates[0]["qualification_status"] == "needs_review"
    assert candidates[0]["qualification_reasons"] == [
        "published_at_missing_or_invalid"
    ]
    assert readiness["coverage"][0]["missing_count"] == 1


def test_expired_candidate_is_rejected():
    candidates, readiness = qualify_candidates(
        [_candidate(published_at="2025-01-01")], _plan()
    )
    assert candidates[0]["qualification_status"] == "rejected"
    assert candidates[0]["qualification_reasons"] == [
        "outside_required_time_window"
    ]
    assert readiness["evidence_status"] == "evidence_gaps"
