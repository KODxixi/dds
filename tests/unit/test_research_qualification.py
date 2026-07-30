from __future__ import annotations

from dds.research.qualification import qualify_candidates


def _plan(required_count=1, **overrides):
    plan = {
        "metric_id": "SC2.competitors",
        "required_count": required_count,
        "as_of": "2026-07-22",
        "max_age_days": 180,
    }
    plan.update(overrides)
    return [plan]


def _candidate(**overrides):
    candidate = {
        "source_ref": "https://example.gov.cn/source",
        "source_hash": "a" * 64,
        "metric_ids": ["SC2.competitors"],
        "published_at": "2026-07-01",
        "rights_status": "public_web",
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


def test_candidate_evidence_type_must_be_allowed():
    candidates, readiness = qualify_candidates(
        [_candidate(evidence_type="analysis_inference")],
        _plan(allowed_evidence_types=["observed_fact"]),
    )

    assert candidates[0]["qualification_status"] == "rejected"
    assert candidates[0]["qualification_reasons"] == [
        "evidence_type_not_allowed"
    ]
    assert readiness["evidence_status"] == "evidence_gaps"


def test_public_web_snippet_needs_source_review_even_when_type_matches():
    candidates, readiness = qualify_candidates(
        [
            _candidate(
                evidence_type="observed_fact",
                source_role="public_web_candidate",
            )
        ],
        _plan(allowed_evidence_types=["observed_fact"]),
    )

    assert candidates[0]["qualification_status"] == "needs_review"
    assert candidates[0]["qualification_reasons"] == [
        "web_search_snippet_requires_source_review"
    ]
    assert readiness["coverage"][0]["qualified_count"] == 0


def _future_plan(required_count=2):
    return [
        {
            "metric_id": "SC2.future_demand_event_scan",
            "required_count": required_count,
            "as_of": "2026-07-22",
            "max_age_days": 730,
            "allowed_evidence_types": [
                "observed_fact",
                "analysis_inference",
            ],
            "allowed_source_roles": [
                "government_record",
                "documented_analysis",
            ],
            "metadata": {
                "future_event_scan": {
                    "time_horizons": [
                        "current_operation",
                        "0_to_3_years",
                        "3_to_5_years",
                    ],
                    "requires_counter_factors": True,
                    "minimum_observed_evidence_count": 1,
                }
            },
        }
    ]


def _future_candidate(**overrides):
    candidate = _candidate(
        metric_ids=["SC2.future_demand_event_scan"],
        evidence_type="observed_fact",
        source_role="government_record",
        status="operating",
        time_window="current_operation",
        counter_factors=["员工宿舍将分流部分居住需求"],
    )
    candidate.update(overrides)
    return candidate


def test_future_demand_coverage_requires_event_boundary_and_observed_source():
    candidates, readiness = qualify_candidates(
        [
            _future_candidate(),
            _future_candidate(
                source_ref="analysis://future-demand/funnel",
                source_hash="b" * 64,
                evidence_type="analysis_inference",
                source_role="documented_analysis",
                status="planned",
                time_window="0_to_3_years",
            ),
        ],
        _future_plan(),
    )

    assert [item["qualification_status"] for item in candidates] == [
        "qualified",
        "qualified",
    ]
    assert readiness["evidence_status"] == "evidence_ready"
    assert readiness["coverage"][0]["observed_qualified_count"] == 1
    assert readiness["coverage"][0]["observed_missing_count"] == 0


def test_future_demand_candidate_without_status_window_or_counter_factor_needs_review():
    candidates, readiness = qualify_candidates(
        [
            _future_candidate(
                status="",
                time_window="",
                counter_factors=[],
            )
        ],
        _future_plan(required_count=1),
    )

    assert candidates[0]["qualification_status"] == "needs_review"
    assert candidates[0]["qualification_reasons"] == [
        "future_event_status_missing",
        "future_event_time_window_missing",
        "future_event_counter_factors_missing",
    ]
    assert readiness["coverage"][0]["status"] == "gap"


def test_future_demand_inference_does_not_replace_required_observed_source():
    candidates, readiness = qualify_candidates(
        [
            _future_candidate(
                evidence_type="analysis_inference",
                source_role="documented_analysis",
            )
        ],
        _future_plan(required_count=1),
    )

    assert candidates[0]["qualification_status"] == "qualified"
    assert readiness["coverage"][0]["qualified_count"] == 1
    assert readiness["coverage"][0]["missing_count"] == 0
    assert readiness["coverage"][0]["observed_missing_count"] == 1
    assert readiness["coverage"][0]["status"] == "gap"


def test_future_observed_fact_requires_authoritative_source_role():
    candidates, readiness = qualify_candidates(
        [
            _future_candidate(
                source_role="documented_analysis",
                evidence_type="observed_fact",
            )
        ],
        _future_plan(required_count=1),
    )

    assert candidates[0]["qualification_status"] == "needs_review"
    assert candidates[0]["qualification_reasons"] == [
        "future_observed_source_not_authoritative"
    ]
    assert readiness["coverage"][0]["observed_missing_count"] == 1
