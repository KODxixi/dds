from __future__ import annotations

import json
import sys
from pathlib import Path


CLOUD_DIR = Path(__file__).resolve().parent
if str(CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(CLOUD_DIR))

import dynamic_sync_worker


class ExplodingStore:
    def __getattr__(self, name: str):
        raise AssertionError(f"dry-run must not touch TOS: {name}")


def test_worker_defaults_to_one_off_dry_run_without_building_tos(tmp_path: Path):
    report = tmp_path / "data_out" / "reports" / "sample.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"ok":true}', encoding="utf-8")
    status_path = tmp_path / "status.jsonl"

    result = dynamic_sync_worker.run_cycle(
        report.parents[1],
        execute=False,
        store_factory=lambda: ExplodingStore(),
        status_path=status_path,
    )

    assert result["status"] == "dry_run"
    assert result["file_count"] == 1
    row = json.loads(status_path.read_text(encoding="utf-8"))
    assert set(row) <= {
        "at",
        "status",
        "snapshot_id",
        "file_count",
        "total_size",
        "uploaded_count",
        "skipped_count",
        "error_type",
    }


def test_execute_cycle_builds_store_once_and_syncs_dynamic_outputs(tmp_path: Path):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "sample.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"ok":true}', encoding="utf-8")
    calls = []

    class FakeStore:
        def __init__(self):
            self.objects = {}
            self.version = 0

        def stat(self, key):
            return None

        def upload_file(self, key, local_path):
            self.version += 1
            self.objects[key] = Path(local_path).read_bytes()
            return {"version_id": f"v{self.version}"}

        def put_bytes(self, key, content, *, content_type):
            self.version += 1
            self.objects[key] = bytes(content)
            return {"version_id": f"v{self.version}"}

    store = FakeStore()

    def factory():
        calls.append("factory")
        return store

    result = dynamic_sync_worker.run_cycle(
        data_out,
        execute=True,
        store_factory=factory,
        status_path=tmp_path / "status.jsonl",
    )

    assert calls == ["factory"]
    assert result["status"] == "complete"
    assert result["uploaded_count"] == 1
    assert any(key.endswith("/complete.json") for key in store.objects)


def test_failure_log_never_contains_exception_message_or_credentials(tmp_path: Path):
    data_out = tmp_path / "data_out"
    data_out.mkdir()
    status_path = tmp_path / "status.jsonl"
    secret = "SECRET_ACCESS_KEY_MUST_NOT_LEAK"

    def failing_factory():
        raise RuntimeError(secret)

    result = dynamic_sync_worker.run_cycle(
        data_out,
        execute=True,
        store_factory=failing_factory,
        status_path=status_path,
    )

    assert result == {"status": "failed", "error_type": "RuntimeError"}
    assert secret not in status_path.read_text(encoding="utf-8")


def test_compose_runs_worker_on_shared_volume_without_public_port():
    compose = (
        CLOUD_DIR / "service" / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    assert "dds-sync:" in compose
    assert "dynamic_sync_worker.py" in compose
    assert "--execute" in compose
    assert "--interval-seconds" in compose
    assert "dds-data-out:/app/data_out" in compose
