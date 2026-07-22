from __future__ import annotations

from dds.domain import (
    DataRequirement,
    EvidenceRecord,
    EvidenceResolutionStatus,
    EvidenceType,
    ProjectContext,
)
from dds.engines.confidence import ConfidenceEngine
from dds.engines.resolver import EvidenceResolver


AS_OF = "2026-07-22"


def _requirement(**overrides):
    values = {
        "metric_id": "SC2.price_median",
        "section_id": "SC2",
        "field_name": "price_median",
        "unit": "CNY/m2",
        "evidence_types": (EvidenceType.OBSERVED_FACT,),
        "min_evidence_count": 2,
        "geography": "武汉",
        "as_of": AS_OF,
        "max_age_days": 365,
    }
    values.update(overrides)
    return DataRequirement(**values)


def _record(evidence_id: str, value: float = 22000, **overrides):
    values = {
        "evidence_id": evidence_id,
        "metric_id": "SC2.price_median",
        "value": value,
        "unit": "CNY/m2",
        "evidence_type": EvidenceType.OBSERVED_FACT,
        "source_id": f"source-{evidence_id}",
        "source_ref": f"https://example.test/{evidence_id}",
        "source_hash": f"sha256:{evidence_id}",
        "observed_at": AS_OF,
        "geography": "武汉",
        "method": "transaction export",
        "sample_size": 1000,
    }
    values.update(overrides)
    return EvidenceRecord(**values)


def test_no_source_is_unknown_and_declared_agent_confidence_is_ignored() -> None:
    evidence = _record(
        "e1",
        source_ref="",
        source_hash="",
        confidence={"score": 1.0, "total": 1.0},
    )

    assessment = ConfidenceEngine().assess(
        [evidence],
        requirements=[_requirement(min_evidence_count=1)],
        as_of=AS_OF,
    )
    resolution = EvidenceResolver().resolve(
        _requirement(min_evidence_count=1),
        [evidence],
        as_of=AS_OF,
    )

    assert assessment.score == 0.0
    assert assessment.cap == 0.0
    assert resolution.status is EvidenceResolutionStatus.UNKNOWN
    assert resolution.value is None
    assert resolution.fail_closed is True


def test_stale_evidence_fails_closed() -> None:
    evidence = _record("old", observed_at="2020-01-01")

    resolution = EvidenceResolver().resolve(
        _requirement(min_evidence_count=1, max_age_days=30),
        [evidence],
        as_of=AS_OF,
    )

    assert resolution.status is EvidenceResolutionStatus.STALE
    assert resolution.value is None
    assert resolution.stale_evidence_refs == ("old",)
    assert resolution.to_resolved_field().evidence_refs == ["old"]


def test_conflicting_current_sources_fail_closed_without_auto_selection() -> None:
    evidence = [_record("e1", 22000), _record("e2", 24000)]

    resolution = EvidenceResolver().resolve(
        _requirement(),
        evidence,
        as_of=AS_OF,
    )

    assert resolution.status is EvidenceResolutionStatus.CONFLICT
    assert resolution.value is None
    assert resolution.conflicting_values == (22000, 24000)
    assert resolution.fail_closed is True


def test_all_real_corroborated_evidence_is_verified_with_full_confidence() -> None:
    evidence = [_record("e1"), _record("e2")]
    context = ProjectContext(city="武汉", base_date=AS_OF)

    resolution = EvidenceResolver().resolve(
        _requirement(),
        evidence,
        project_context=context,
    )

    assert resolution.status is EvidenceResolutionStatus.VERIFIED
    assert resolution.value == 22000
    assert resolution.confidence.score == 1.0
    assert set(resolution.confidence.dimensions) == {
        "source",
        "coverage",
        "freshness",
        "corroboration",
        "geography",
        "method",
        "stability",
    }
    assert all(value == 1.0 for value in resolution.confidence.dimensions.values())

