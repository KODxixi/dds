from __future__ import annotations

import json

import pytest

import dds.data.evidence_store as evidence_store_module
from dds.data.evidence_store import (
    EvidencePackageIntegrityError,
    EvidenceStore,
)


def _freeze_kwargs() -> dict:
    return {
        "project_context": {"project_id": "P1", "city": "武汉"},
        "evidence": [
            {
                "evidence_id": "E1",
                "metric_id": "market.price",
                "value": 20_000,
                "unit": "CNY/m2",
                "source_hash": "abc",
            }
        ],
        "query_manifests": [{"query_signature": "q1"}],
    }


def test_identical_freeze_validates_and_returns_without_rewriting(
    tmp_path, monkeypatch
) -> None:
    store = EvidenceStore(tmp_path)
    kwargs = _freeze_kwargs()
    first = store.freeze("P1", **kwargs)
    manifest_before = first.manifest_path.read_bytes()
    evidence_before = first.evidence_path.read_bytes()
    frozen_at = json.loads(manifest_before)["frozen_at"]

    def forbidden_write(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("idempotent freeze attempted to rewrite package files")

    monkeypatch.setattr(evidence_store_module, "_atomic_write", forbidden_write)
    second = store.freeze("P1", **kwargs)

    assert second == first
    assert second.manifest_path.read_bytes() == manifest_before
    assert second.evidence_path.read_bytes() == evidence_before
    assert json.loads(second.manifest_path.read_bytes())["frozen_at"] == frozen_at


def test_existing_corrupt_package_is_never_silently_overwritten(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    kwargs = _freeze_kwargs()
    frozen = store.freeze("P1", **kwargs)
    payload = json.loads(frozen.evidence_path.read_text(encoding="utf-8"))
    payload["evidence"][0]["value"] = 99_999
    frozen.evidence_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    corrupt_bytes = frozen.evidence_path.read_bytes()
    manifest_bytes = frozen.manifest_path.read_bytes()

    with pytest.raises(EvidencePackageIntegrityError, match="hash mismatch"):
        store.freeze("P1", **kwargs)

    assert frozen.evidence_path.read_bytes() == corrupt_bytes
    assert frozen.manifest_path.read_bytes() == manifest_bytes
    with pytest.raises(EvidencePackageIntegrityError, match="hash mismatch"):
        store.validate(frozen.package_dir)


def test_project_id_cannot_escape_store(tmp_path) -> None:
    store = EvidenceStore(tmp_path)
    with pytest.raises(ValueError):
        store.freeze(
            "../outside", project_context={}, evidence=[], query_manifests=[]
        )

