from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from dds.reporting.report_delivery_evidence import (
    ReportDeliveryEvidenceError,
    require_report_delivery_validation,
    validate_report_delivery_evidence,
    verify_report_delivery_validation,
)


AS_OF = "2026-07-22"
WEB_URL = "https://example.com/research/release"
PUBLIC_IP = "93.184.216.34"


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return _sha(payload)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "inbox").mkdir(parents=True)
    (project / "work" / "source_snapshots").mkdir(parents=True)
    return project


def _metadata(
    source_id: str,
    *,
    snapshot_ref: str,
    snapshot_hash: str,
    raw_hash: str,
) -> dict:
    return {
        "source_id": source_id,
        "title": "公开市场月报",
        "publisher": "示例统计机构",
        "author_type": "official_institution",
        "source_type": "official_web",
        "canonical_url": WEB_URL,
        "published_at": "2026-06-30",
        "captured_at": "2026-07-01T08:00:00Z",
        "geography": {"city": "测试市", "district": "测试区"},
        "time_window": {"start": "2026-06-01", "end": "2026-06-30"},
        "rights_status": "public_reference",
        "duplicate_cluster": "official:market:2026-06",
        "snapshot_ref": snapshot_ref,
        "snapshot_hash": snapshot_hash,
        "raw_hash": raw_hash,
        "limitations": ["仅用于一般研究报告，不替代专项尽调。"],
    }


def _candidate(source: dict, *, source_id: str = "source:web") -> dict:
    return {
        "schema_version": "dds.evidence-candidate-batch/1.0",
        "kind": "EvidenceCandidateBatch",
        "batch_id": "candidate-batch:test:2026-07-22",
        "request_id": "research:test:2026-07-22",
        "project_id": "test-project",
        "as_of": AS_OF,
        "status": "pending_dds_validation",
        "freeze_eligible": False,
        "sources": [source],
        "candidates": [
            {
                "record_id": "record-1",
                "claim_id": "claim-market",
                "statement": "公开月报支持当前市场判断。",
                "source_refs": [source_id],
                "counter_source_refs": [],
                "counter_evidence_status": "searched_none_found",
                "counter_evidence_note": "已检索同口径反向证据。",
                "limitations": ["仅适用于报告基准日的一般策划判断。"],
                "decision_eligibility": False,
                "status": "pending_dds_validation",
            }
        ],
        "missing_claim_ids": [],
        "conflict_claim_ids": [],
    }


def _web_batch(project: Path, raw: bytes = b"official release") -> dict:
    snapshot_ref = "source_snapshots/web-release.bin"
    snapshot_hash = _write(project / "work" / snapshot_ref, raw)
    return _candidate(
        _metadata(
            "source:web",
            snapshot_ref=snapshot_ref,
            snapshot_hash=snapshot_hash,
            raw_hash=_sha(raw),
        )
    )


def _public_dns(hostname: str, port: int) -> list[str]:
    assert hostname in {"example.com", "safe.example"}
    assert port == 443
    return [PUBLIC_IP]


def test_local_source_freezes_content_hash_and_verifies_attestation(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    raw = b"owner supplied brief"
    raw_hash = _write(project / "inbox" / "brief.txt", raw)
    snapshot_ref = "source_snapshots/local.json"
    snapshot_hash = _write(
        project / "work" / snapshot_ref,
        json.dumps({"relative_path": "inbox/brief.txt"}).encode(),
    )
    source = _metadata(
        "source:local",
        snapshot_ref=snapshot_ref,
        snapshot_hash=snapshot_hash,
        raw_hash=raw_hash,
    )
    source.update(
        {
            "source_type": "provided_input",
            "author_type": "provided_input",
            "publisher": "project_owner",
            "canonical_ref": "inbox/brief.txt",
        }
    )
    source.pop("canonical_url")
    batch = _candidate(source, source_id="source:local")

    result = require_report_delivery_validation(batch, project_dir=project)

    assert result["status"] == "validated_for_report_delivery"
    assert result["source_attestations"][0]["content_hash"] == raw_hash
    assert verify_report_delivery_validation(result, batch) is True

    changed_subject = deepcopy(batch)
    changed_subject["as_of"] = "2026-07-23"
    with pytest.raises(
        ReportDeliveryEvidenceError, match="validation_subject_hash_mismatch"
    ):
        verify_report_delivery_validation(result, changed_subject)


def test_web_locator_is_rejected_by_default_without_transport(tmp_path: Path) -> None:
    project = _project(tmp_path)
    batch = _web_batch(project)

    result = validate_report_delivery_evidence(batch, project_dir=project)

    assert result["status"] == "rejected"
    assert "web_transport_required" in result["reason_codes"]


def test_transport_receives_only_the_dns_verified_public_ip_set(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    raw = b"official release"
    calls: list[tuple[str, frozenset[str]]] = []

    def transport(url: str, verified_ips: frozenset[str]) -> dict:
        calls.append((url, verified_ips))
        return {"status": 200, "content": raw, "url": url}

    result = require_report_delivery_validation(
        _web_batch(project, raw),
        project_dir=project,
        transport=transport,
        dns_resolver=_public_dns,
    )

    assert calls == [(WEB_URL, frozenset({PUBLIC_IP}))]
    assert result["network_policy"] == "https_ip_pinned_refetched_and_frozen"
    assert result["source_attestations"][0]["content_hash"] == _sha(raw)
    assert result["source_attestations"][0]["redirect_count"] == 0


def test_private_dns_result_is_blocked_before_transport(tmp_path: Path) -> None:
    project = _project(tmp_path)

    def forbidden_transport(_url, _verified_ips):  # pragma: no cover
        raise AssertionError("private address reached transport")

    result = validate_report_delivery_evidence(
        _web_batch(project),
        project_dir=project,
        transport=forbidden_transport,
        dns_resolver=lambda _host, _port: ["10.0.0.8"],
    )

    assert result["status"] == "rejected"
    assert "web_dns_address_forbidden" in result["reason_codes"]


def test_every_redirect_is_revalidated_and_private_target_is_blocked(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    calls: list[tuple[str, frozenset[str]]] = []

    def resolver(hostname: str, _port: int) -> list[str]:
        return [PUBLIC_IP] if hostname == "example.com" else ["127.0.0.1"]

    def transport(url: str, verified_ips: frozenset[str]) -> dict:
        calls.append((url, verified_ips))
        return {
            "status": 302,
            "headers": {"Location": "https://internal.example/private"},
            "content": b"",
            "url": url,
        }

    result = validate_report_delivery_evidence(
        _web_batch(project),
        project_dir=project,
        transport=transport,
        dns_resolver=resolver,
    )

    assert result["status"] == "rejected"
    assert "web_dns_address_forbidden" in result["reason_codes"]
    assert calls == [(WEB_URL, frozenset({PUBLIC_IP}))]


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.com/research/release", "web_url_not_https"),
        ("https://user@example.com/research/release", "web_url_not_canonical"),
        ("https://example.com:444/research/release", "web_url_not_canonical"),
    ],
)
def test_web_url_rejects_non_https_userinfo_and_non_443_ports(
    tmp_path: Path, url: str, reason: str
) -> None:
    project = _project(tmp_path)
    batch = _web_batch(project)
    batch["sources"][0]["canonical_url"] = url

    result = validate_report_delivery_evidence(
        batch,
        project_dir=project,
        transport=lambda _url, _ips: b"should not be called",
        dns_resolver=lambda _host, _port: [PUBLIC_IP],
    )

    assert result["status"] == "rejected"
    assert reason in result["reason_codes"]


def test_redirect_count_is_bounded(tmp_path: Path) -> None:
    project = _project(tmp_path)
    calls = 0

    def transport(url: str, _verified_ips: frozenset[str]) -> dict:
        nonlocal calls
        calls += 1
        return {
            "status": 302,
            "location": "https://example.com/next",
            "url": url,
        }

    result = validate_report_delivery_evidence(
        _web_batch(project),
        project_dir=project,
        transport=transport,
        dns_resolver=_public_dns,
        max_redirects=1,
    )

    assert result["status"] == "rejected"
    assert "web_redirect_limit_exceeded" in result["reason_codes"]
    assert calls == 2


def test_fetched_content_and_validation_hash_tampering_are_rejected(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    batch = _web_batch(project)
    mismatch = validate_report_delivery_evidence(
        batch,
        project_dir=project,
        transport=lambda url, _ips: {"status": 200, "content": b"tampered", "url": url},
        dns_resolver=_public_dns,
    )
    assert mismatch["status"] == "rejected"
    assert "web_raw_hash_mismatch" in mismatch["reason_codes"]

    valid = require_report_delivery_validation(
        batch,
        project_dir=project,
        transport=lambda url, _ips: {
            "status": 200,
            "content": b"official release",
            "url": url,
        },
        dns_resolver=_public_dns,
    )
    forged = deepcopy(valid)
    forged["source_attestations"][0]["content_hash"] = "0" * 64
    with pytest.raises(ReportDeliveryEvidenceError, match="validation_hash_mismatch"):
        verify_report_delivery_validation(forged, batch)


def test_compile_phase_is_offline_and_returns_frozen_attestation(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    raw = b"official release"
    batch = _web_batch(project, raw)
    frozen = require_report_delivery_validation(
        batch,
        project_dir=project,
        transport=lambda url, _ips: {"status": 200, "content": raw, "url": url},
        dns_resolver=_public_dns,
    )

    def forbidden_transport(_url, _ips):  # pragma: no cover
        raise AssertionError("compile attempted network transport")

    compiled = require_report_delivery_validation(
        batch,
        project_dir=project,
        phase="compile",
        prior_validation=frozen,
        transport=forbidden_transport,
        dns_resolver=lambda _host, _port: (_ for _ in ()).throw(
            AssertionError("compile attempted DNS")
        ),
    )

    assert compiled == frozen

