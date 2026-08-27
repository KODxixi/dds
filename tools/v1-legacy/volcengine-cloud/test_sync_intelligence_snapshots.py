from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import pytest


CLOUD_DIR = Path(__file__).resolve().parent
if str(CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(CLOUD_DIR))

import pipeline_scheduler
import sync_intelligence_snapshots


class ExplodingStore:
    def __getattr__(self, name: str):
        raise AssertionError(f"dry-run must not access TOS: {name}")


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
        return self.objects[key]


def _spec(kind: str = "raw_social", **overrides):
    specs = {
        "raw_social": {
            "kind": "raw_social",
            "payload": {"text": "地铁方便，电话 13800138000"},
            "captured_at": "2026-07-12T03:20:00+08:00",
            "rights_status": "public",
            "source_refs": ["https://example.test/social/1"],
            "status": "partial",
            "city": "济南",
            "platform": "小红书",
        },
        "raw_macro": {
            "kind": "raw_macro",
            "payload": {"cycle": "筑底"},
            "captured_at": "2026-07-12T03:20:00+08:00",
            "rights_status": "public",
            "source_refs": ["https://example.test/macro/1"],
            "status": "ready",
            "city": "武汉",
        },
        "normalized": {
            "kind": "normalized",
            "payload": {"claims": []},
            "captured_at": "2026-07-12T03:20:00+08:00",
            "rights_status": "derived",
            "source_refs": ["raw:intelligence:1"],
            "status": "ready",
            "city": "济南",
            "platform": "general",
        },
        "evidence": {
            "kind": "evidence",
            "payload": {"nodes": []},
            "captured_at": "2026-07-12T03:20:00+08:00",
            "rights_status": "derived",
            "source_refs": ["normalized:intelligence:1"],
            "status": "partial",
            "report_id": "report-jinan-001",
        },
    }
    result = dict(specs[kind])
    result.update(overrides)
    return result


def _write_json(path: Path, value) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_dry_run_directory_prepares_all_four_kinds_without_sdk_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    input_dir = tmp_path / "pending"
    input_dir.mkdir()
    for index, kind in enumerate(
        ("raw_macro", "raw_social", "normalized", "evidence"), start=1
    ):
        _write_json(input_dir / f"{index:02d}-{kind}.json", _spec(kind))

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "tos" or name.startswith("volcengine"):
            raise AssertionError("dry-run imported a cloud SDK")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    status_log = tmp_path / "status.jsonl"

    result = sync_intelligence_snapshots.sync_inputs(
        input_dir,
        store=ExplodingStore(),
        execute=False,
        status_path=status_log,
    )

    assert result["status"] == "dry_run"
    assert result["count"] == 4
    assert {item["kind"] for item in result["items"]} == {
        "raw_macro",
        "raw_social",
        "normalized",
        "evidence",
    }
    assert all(item["status"] == "dry_run" for item in result["items"])
    assert all("payload" not in item for item in result["items"])
    rows = [json.loads(line) for line in status_log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4
    assert all(set(row) <= {"sha256", "object_key", "byte_size", "status", "version_id"} for row in rows)


@pytest.mark.parametrize(
    ("kind", "missing_field"),
    [
        ("raw_macro", "city"),
        ("raw_social", "platform"),
        ("normalized", "platform"),
        ("evidence", "report_id"),
        ("raw_social", "captured_at"),
        ("raw_social", "rights_status"),
        ("raw_social", "source_refs"),
        ("raw_social", "status"),
    ],
)
def test_specs_reject_missing_explicit_provenance_fields(
    tmp_path: Path, kind: str, missing_field: str
):
    spec = _spec(kind)
    spec.pop(missing_field)
    source = _write_json(tmp_path / "invalid.json", spec)

    with pytest.raises(ValueError, match=missing_field):
        sync_intelligence_snapshots.sync_inputs(
            source,
            execute=False,
            status_path=tmp_path / "status.jsonl",
        )


def test_execute_uses_injected_store_and_writes_only_sanitized_status(
    tmp_path: Path,
):
    secret_text = "private-account-42"
    source = _write_json(
        tmp_path / "social.json",
        _spec(
            "raw_social",
            payload={
                "author_name": secret_text,
                "text": "联系 13800138000 或 buyer@example.com",
            },
        ),
    )
    status_log = tmp_path / "status.jsonl"
    store = FakeObjectStore()

    result = sync_intelligence_snapshots.sync_inputs(
        source,
        store=store,
        execute=True,
        status_path=status_log,
    )

    assert result["status"] == "complete"
    assert result["items"][0]["verified"] is True
    assert result["items"][0]["uploaded"] is True
    assert any(op[0] == "put_bytes" for op in store.operations)
    log_text = status_log.read_text(encoding="utf-8")
    assert secret_text not in log_text
    assert "13800138000" not in log_text
    assert "buyer@example.com" not in log_text
    row = json.loads(log_text)
    assert set(row) == {"sha256", "object_key", "byte_size", "status", "version_id"}
    assert row["status"] == "uploaded_verified"
    assert row["version_id"] == "v1"


def test_main_defaults_to_dry_run_and_never_builds_tos_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source = _write_json(tmp_path / "macro.json", _spec("raw_macro"))
    status_log = tmp_path / "status.jsonl"

    def exploding_factory():
        raise AssertionError("default CLI must not build a TOS store")

    exit_code = sync_intelligence_snapshots.main(
        ["--input", str(source), "--status-log", str(status_log)],
        store_factory=exploding_factory,
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry_run"
    assert output["count"] == 1


def test_execute_without_sdk_returns_clear_blocked_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source = _write_json(tmp_path / "macro.json", _spec("raw_macro"))

    def blocked_factory():
        raise RuntimeError("blocked: Volcengine TOS SDK is not installed")

    exit_code = sync_intelligence_snapshots.main(
        ["--input", str(source), "--execute", "--status-log", str(tmp_path / "s.jsonl")],
        store_factory=blocked_factory,
    )

    assert exit_code == 2
    output = json.loads(capsys.readouterr().err)
    assert output == {
        "status": "blocked",
        "error_type": "RuntimeError",
        "message": "blocked: Volcengine TOS SDK is not installed",
    }


def test_scheduler_declares_publish_contract_and_honest_collection_states():
    schedule = {item["name"]: item for item in pipeline_scheduler.get_schedule()}

    publish = schedule["intelligence_publish"]
    assert publish["status"] == "ready"
    assert publish["local_scripts"] == [
        "cloud/volcengine/sync_intelligence_snapshots.py"
    ]
    assert publish["tos_prefix"] == "raw/intelligence/"

    macro = schedule["macro_intelligence"]
    social = schedule["social_intelligence"]
    assert macro["status"] == "contract_ready"
    assert social["status"] == "blocked"
    assert macro["local_scripts"] == []
    assert social["local_scripts"] == []
    assert "采集器" in macro["source_note"]
    assert "采集器" in social["source_note"]


def test_scheduler_publish_run_is_dry_run_safe_and_collection_run_is_honest(
    tmp_path: Path,
):
    pending = tmp_path / "pending"
    dry_run = pipeline_scheduler.run_pipeline(
        "intelligence_publish",
        dry_run=True,
        input_path=pending,
    )

    assert dry_run["mode"] == "dry-run"
    assert len(dry_run["commands"]) == 1
    assert "sync_intelligence_snapshots.py" in dry_run["commands"][0]
    assert str(pending) in dry_run["commands"][0]
    assert "--execute" not in dry_run["commands"][0]

    macro = pipeline_scheduler.run_pipeline("macro_intelligence", dry_run=True)
    social = pipeline_scheduler.run_pipeline("social_intelligence", dry_run=False)
    assert macro["commands"] == []
    assert macro["status"] == "contract_ready"
    assert social["executed"] is False
    assert social["status"] == "blocked"
