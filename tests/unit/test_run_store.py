from __future__ import annotations

import pytest

from dds.api import RunStatus, RunStore


def test_idempotency_returns_same_run_and_checkpoint_survives(tmp_path):
    store = RunStore(tmp_path)
    first = store.create_or_get("client-request-1", project_id="P1")
    store.transition(
        first.run_id,
        status=RunStatus.RUNNING,
        checkpoint="evidence_frozen",
        evidence_package_hash="abc",
        increment_attempt=True,
    )
    second = store.create_or_get("client-request-1", project_id="P1")
    assert second.run_id == first.run_id
    assert second.checkpoint == "evidence_frozen"
    assert second.attempts == 1


def test_failed_retry_does_not_erase_old_artifact(tmp_path):
    store = RunStore(tmp_path)
    run = store.create_or_get("request-2")
    store.transition(
        run.run_id,
        status=RunStatus.RUNNING,
        checkpoint="compiled",
        artifacts={"html": "reports/stable.html"},
    )
    failed = store.transition(
        run.run_id,
        status=RunStatus.FAILED,
        checkpoint="compile_failed",
        error="template error",
        recovery={"resume_from": "evidence_frozen"},
    )
    assert failed.artifacts["html"] == "reports/stable.html"
    assert failed.recovery["resume_from"] == "evidence_frozen"


def test_completed_run_cannot_be_reopened(tmp_path):
    store = RunStore(tmp_path)
    run = store.create_or_get("request-3")
    store.transition(
        run.run_id, status=RunStatus.COMPLETED, checkpoint="delivered"
    )
    with pytest.raises(ValueError, match="cannot be reopened"):
        store.transition(
            run.run_id, status=RunStatus.RUNNING, checkpoint="retrying"
        )
