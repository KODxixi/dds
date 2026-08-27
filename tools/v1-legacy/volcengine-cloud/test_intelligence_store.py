from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


CLOUD_DIR = Path(__file__).resolve().parent
if str(CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(CLOUD_DIR))

import intelligence_store


class ExplodingStore:
    def __getattr__(self, name: str):
        raise AssertionError(f"dry-run must not access object storage: {name}")


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.operations: list[tuple[str, str]] = []
        self._version = 0

    def stat(self, key: str):
        self.operations.append(("stat", key))
        content = self.objects.get(key)
        if content is None:
            return None
        return {"size": len(content), "version_id": "existing-v1"}

    def put_bytes(self, key: str, content: bytes, *, content_type: str):
        assert content_type == "application/json"
        self.operations.append(("put_bytes", key))
        self.objects[key] = bytes(content)
        self._version += 1
        return {"version_id": f"v{self._version}"}

    def get_bytes(self, key: str, *, version_id: str | None = None) -> bytes:
        self.operations.append(("get_bytes", key))
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]


def _social_snapshot(**overrides):
    params = {
        "kind": "raw_social",
        "payload": {"text": "学区一般，但通勤可以", "likes": 18},
        "city": "济南",
        "platform": "小红书",
        "captured_at": "2026-07-12T03:20:00+00:00",
        "rights_status": "public",
        "source_refs": ["https://example.test/post/1"],
        "status": "ready",
    }
    params.update(overrides)
    return intelligence_store.prepare_snapshot(**params)


def test_key_layouts_are_content_addressed_and_safe():
    social = _social_snapshot(city="济南 / ../../上海", platform="小红书/../抖音")
    macro = intelligence_store.prepare_snapshot(
        kind="raw_macro",
        payload={"cycle": "筑底"},
        city="武汉",
        captured_at="2026-07-12T03:20:00Z",
        rights_status="public",
        source_refs=["https://example.test/macro"],
        status="partial",
    )
    normalized = intelligence_store.prepare_snapshot(
        kind="normalized",
        payload={"claims": []},
        city="济南",
        captured_at="2026-07-12T03:20:00Z",
        rights_status="derived",
        source_refs=[],
        status="missing",
    )
    evidence = intelligence_store.prepare_snapshot(
        kind="evidence",
        payload={"nodes": []},
        report_id="../报告 A/../../../secrets",
        captured_at="2026-07-12T03:20:00Z",
        rights_status="derived",
        source_refs=[],
        status="blocked",
    )

    assert social["object_key"].startswith(
        "raw/intelligence/social/小红书-抖音/济南-上海/2026-07-12/"
    )
    assert macro["object_key"].startswith(
        "raw/intelligence/macro/武汉/2026-07-12/"
    )
    assert normalized["object_key"].startswith(
        "normalized/intelligence/general/济南/2026-07-12/"
    )
    assert evidence["object_key"].startswith(
        "evidence/intelligence/报告-a-secrets/"
    )
    for snapshot in (social, macro, normalized, evidence):
        assert ".." not in snapshot["object_key"]
        assert "\\" not in snapshot["object_key"]
        assert snapshot["object_key"].endswith(f"/{snapshot['sha256']}.json")
        assert len(snapshot["sha256"]) == 64


def test_prepare_snapshot_recursively_redacts_pii_and_account_identifiers():
    snapshot = _social_snapshot(
        payload={
            "author": {
                "username": "真实账号",
                "avatar_url": "https://cdn.test/avatar.jpg",
                "author_id": "account-42",
            },
            "comments": [
                {
                    "text": "联系我 13800138000 或 buyer@example.com",
                    "profile": {"wechat_id": "wx_private"},
                }
            ],
            "geography": {"city": "济南", "exact_address": "某小区 3-2-101"},
        }
    )

    stored = json.dumps(snapshot, ensure_ascii=False)
    for raw_value in (
        "真实账号",
        "avatar.jpg",
        "account-42",
        "13800138000",
        "buyer@example.com",
        "wx_private",
        "某小区 3-2-101",
    ):
        assert raw_value not in stored
    assert snapshot["payload"]["author"]["username"] == "[REDACTED]"
    assert snapshot["payload"]["geography"]["city"] == "济南"
    assert "138****8000" in stored
    assert "b***@example.com" in stored


@pytest.mark.parametrize(
    "secret_payload",
    [
        {"credentials": {"user": "a"}},
        {"accessToken": "never-store"},
        {"api_key": "never-store"},
        {"client_secret": "never-store"},
        {"nested": [{"cookie": "session=secret"}]},
    ],
)
def test_prepare_snapshot_rejects_secret_fields_at_any_depth(secret_payload):
    with pytest.raises(ValueError, match="secret field"):
        _social_snapshot(payload=secret_payload)


def test_status_is_explicit_and_only_allowed_lifecycle_values_are_accepted():
    for status in ("ready", "partial", "missing", "blocked"):
        snapshot = _social_snapshot(status=status, payload={})
        assert snapshot["status"] == status

    with pytest.raises(ValueError, match="status"):
        _social_snapshot(status="complete-enough")


def test_dry_run_returns_manifest_without_touching_cloud():
    snapshot = _social_snapshot(status="partial")

    result = intelligence_store.publish_snapshot(
        snapshot,
        store=ExplodingStore(),
        execute=False,
    )

    assert result == {
        "status": "dry_run",
        "snapshot_status": "partial",
        "schema_version": intelligence_store.SCHEMA_VERSION,
        "sha256": snapshot["sha256"],
        "object_key": snapshot["object_key"],
        "byte_size": result["byte_size"],
        "uploaded": False,
        "verified": False,
        "version_id": None,
    }
    assert result["byte_size"] > 0


def test_execute_uploads_verifies_and_is_idempotent():
    snapshot = _social_snapshot()
    store = FakeObjectStore()

    first = intelligence_store.publish_snapshot(snapshot, store=store, execute=True)
    second = intelligence_store.publish_snapshot(snapshot, store=store, execute=True)

    assert first["status"] == "ready"
    assert first["uploaded"] is True
    assert first["verified"] is True
    assert second["uploaded"] is False
    assert second["verified"] is True
    assert [op for op in store.operations if op[0] == "put_bytes"] == [
        ("put_bytes", snapshot["object_key"])
    ]
    remote = json.loads(store.objects[snapshot["object_key"]])
    identity = dict(remote)
    identity.pop("sha256")
    identity.pop("object_key")
    assert hashlib.sha256(
        intelligence_store.canonical_json(identity)
    ).hexdigest() == remote["sha256"]


def test_execute_detects_corrupt_existing_object_instead_of_overwriting_it():
    snapshot = _social_snapshot()
    store = FakeObjectStore()
    store.objects[snapshot["object_key"]] = b"x" * len(
        intelligence_store.snapshot_bytes(snapshot)
    )

    with pytest.raises(ValueError, match="verification"):
        intelligence_store.publish_snapshot(snapshot, store=store, execute=True)

    assert ("put_bytes", snapshot["object_key"]) not in store.operations


def test_snapshot_metadata_is_required_and_preserved():
    snapshot = _social_snapshot()

    assert snapshot["schema_version"] == intelligence_store.SCHEMA_VERSION
    assert snapshot["captured_at"] == "2026-07-12T03:20:00+00:00"
    assert snapshot["rights_status"] == "public"
    assert snapshot["source_refs"] == ["https://example.test/post/1"]
    assert snapshot["kind"] == "raw_social"

    with pytest.raises(ValueError, match="rights_status"):
        _social_snapshot(rights_status="")
    with pytest.raises(ValueError, match="source_refs"):
        _social_snapshot(source_refs="https://example.test/not-a-list")


def test_module_import_does_not_require_tos_sdk(monkeypatch):
    module_path = CLOUD_DIR / "intelligence_store.py"
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "tos" or name.startswith("tos."):
            raise AssertionError("intelligence_store must not import the TOS SDK")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    spec = importlib.util.spec_from_file_location("intelligence_store_no_sdk", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.SCHEMA_VERSION == intelligence_store.SCHEMA_VERSION


@pytest.mark.parametrize(
    "source_ref",
    [
        "https://tos.example/object?X-Tos-Signature=never-store",
        "https://example.test/data?access_token=never-store",
        "https://example.test/data?credential=never-store",
    ],
)
def test_signed_or_credentialed_source_urls_are_rejected(source_ref):
    with pytest.raises(ValueError, match="secret"):
        _social_snapshot(source_refs=[source_ref])


@pytest.mark.parametrize("bad_number", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected_before_hashing(bad_number):
    with pytest.raises(ValueError, match="finite"):
        _social_snapshot(payload={"score": bad_number})
