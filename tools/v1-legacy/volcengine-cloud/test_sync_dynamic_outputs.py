from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


CLOUD_DIR = Path(__file__).resolve().parent
if str(CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(CLOUD_DIR))

import sync_dynamic_outputs


class ExplodingStore:
    def __getattr__(self, name: str):
        raise AssertionError(f"dry-run must not access object storage: {name}")


class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, object]] = {}
        self.operations: list[tuple[str, str]] = []
        self.get_requests: list[tuple[str, str | None]] = []
        self._version = 0

    def _put(self, operation: str, key: str, content: bytes) -> dict[str, str]:
        self._version += 1
        version_id = f"version-{self._version}"
        self.objects[key] = {"content": content, "version_id": version_id}
        self.operations.append((operation, key))
        return {"version_id": version_id}

    def upload_file(self, key: str, local_path: Path) -> dict[str, str]:
        return self._put("upload_file", key, Path(local_path).read_bytes())

    def stat(self, key: str) -> dict[str, object] | None:
        self.operations.append(("stat", key))
        stored = self.objects.get(key)
        if stored is None:
            return None
        return {
            "version_id": stored["version_id"],
            "size": len(stored["content"]),
        }

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> dict[str, str]:
        assert content_type == "application/json"
        return self._put("put_bytes", key, bytes(content))

    def get_bytes(self, key: str, *, version_id: str | None = None) -> bytes:
        self.operations.append(("get_bytes", key))
        self.get_requests.append((key, version_id))
        stored = self.objects.get(key)
        if stored is None:
            raise KeyError(key)
        return bytes(stored["content"])


def test_sync_dry_run_scans_only_dynamic_output_roots_without_cloud_access(tmp_path):
    data_out = tmp_path / "data_out"
    expected = {
        "reports/latest/report.json": b'{"report": 1}\n',
        "projects/project-a/session.json": b'{"state": "ready"}\n',
        "metrics/runtime.jsonl": b'{"latency_ms": 12}\n',
        "ceo_learning/events.jsonl": b'{"weight": 0.7}\n',
        "architecture_learning/events.jsonl": b'{"action": "review"}\n',
        "lineage/run.json": b'{"run": "tracked"}\n',
    }
    for relative_path, content in expected.items():
        path = data_out / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    ignored = data_out / "cache" / "local-only.bin"
    ignored.parent.mkdir(parents=True)
    ignored.write_bytes(b"ignore me")

    result = sync_dynamic_outputs.sync_outputs(
        data_out,
        store=ExplodingStore(),
        execute=False,
    )

    assert result["status"] == "dry_run"
    assert result["file_count"] == len(expected)
    assert [item["relative_path"] for item in result["files"]] == sorted(expected)
    for item in result["files"]:
        digest = hashlib.sha256(expected[item["relative_path"]]).hexdigest()
        assert item["sha256"] == digest
        assert item["object_key"] == f"dds/dynamic/content/sha256/{digest[:2]}/{digest}"
        assert item["version_id"] is None


def test_sync_rejects_symlink_that_escapes_data_out_before_cloud_access(tmp_path):
    data_out = tmp_path / "data_out"
    reports = data_out / "reports"
    reports.mkdir(parents=True)
    outside = tmp_path / "outside-secret.json"
    outside.write_text('{"must_not_upload": true}', encoding="utf-8")
    link = reports / "escaped.json"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")

    with pytest.raises(ValueError, match="escapes data root"):
        sync_dynamic_outputs.sync_outputs(
            data_out,
            store=ExplodingStore(),
            execute=False,
        )


def test_execute_uploads_content_manifest_then_complete_marker_last(tmp_path):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "report.json"
    project = data_out / "projects" / "p1" / "session.json"
    report.parent.mkdir(parents=True)
    project.parent.mkdir(parents=True)
    report.write_bytes(b'{"report": 1}\n')
    project.write_bytes(b'{"state": "ready"}\n')
    store = FakeObjectStore()

    result = sync_dynamic_outputs.sync_outputs(data_out, store=store, execute=True)

    assert result["status"] == "complete"
    manifest_key = result["manifest_key"]
    complete_key = result["complete_key"]
    assert store.operations[-2:] == [
        ("put_bytes", manifest_key),
        ("put_bytes", complete_key),
    ]
    assert complete_key.endswith(f"/{result['snapshot_id']}/complete.json")

    manifest_bytes = store.objects[manifest_key]["content"]
    assert isinstance(manifest_bytes, bytes)
    manifest = json.loads(manifest_bytes)
    assert manifest["status"] == "prepared"
    assert manifest["snapshot_id"] == result["snapshot_id"]
    assert manifest["file_count"] == 2
    assert all(item["version_id"].startswith("version-") for item in manifest["files"])
    assert all(item["object_key"] in store.objects for item in manifest["files"])

    complete = json.loads(store.objects[complete_key]["content"])
    assert complete["status"] == "complete"
    assert complete["manifest_key"] == manifest_key
    assert complete["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()


def test_execute_skips_existing_content_object_and_reuses_its_version_id(tmp_path):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "report.json"
    report.parent.mkdir(parents=True)
    content = b'{"stable": true}\n'
    report.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    content_key = f"dds/dynamic/content/sha256/{digest[:2]}/{digest}"
    store = FakeObjectStore()
    store.objects[content_key] = {
        "content": content,
        "version_id": "existing-version-42",
    }

    result = sync_dynamic_outputs.sync_outputs(data_out, store=store, execute=True)

    assert ("upload_file", content_key) not in store.operations
    assert result["uploaded_count"] == 0
    assert result["skipped_count"] == 1
    manifest = json.loads(store.objects[result["manifest_key"]]["content"])
    assert manifest["files"][0]["version_id"] == "existing-version-42"


def test_execute_rejects_uploaded_object_without_version_id_and_never_completes(tmp_path):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_bytes(b'{"needs": "version"}\n')

    class MissingVersionStore(FakeObjectStore):
        def upload_file(self, key: str, local_path: Path) -> dict[str, str]:
            self._put("upload_file", key, Path(local_path).read_bytes())
            return {}

    store = MissingVersionStore()

    with pytest.raises(ValueError, match="version_id"):
        sync_dynamic_outputs.sync_outputs(data_out, store=store, execute=True)

    assert not any(key.endswith("/complete.json") for key in store.objects)
    assert not any(key.endswith("/manifest.json") for key in store.objects)


def test_execute_rejects_existing_content_with_wrong_remote_size(tmp_path):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "report.json"
    report.parent.mkdir(parents=True)
    content = b'{"size": "must-match"}\n'
    report.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    content_key = f"dds/dynamic/content/sha256/{digest[:2]}/{digest}"
    store = FakeObjectStore()
    store.objects[content_key] = {
        "content": b"wrong-size",
        "version_id": "existing-but-invalid",
    }

    with pytest.raises(ValueError, match="remote size"):
        sync_dynamic_outputs.sync_outputs(data_out, store=store, execute=True)

    assert ("upload_file", content_key) not in store.operations
    assert not any(key.endswith("/complete.json") for key in store.objects)


def test_execute_rejects_existing_content_with_same_size_but_wrong_hash(tmp_path):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "report.json"
    report.parent.mkdir(parents=True)
    content = b'{"state":"good"}\n'
    report.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    content_key = f"dds/dynamic/content/sha256/{digest[:2]}/{digest}"
    store = FakeObjectStore()
    store.objects[content_key] = {
        "content": b'{"state":"evil"}\n',
        "version_id": "same-size-but-invalid",
    }

    with pytest.raises(ValueError, match="SHA256"):
        sync_dynamic_outputs.sync_outputs(data_out, store=store, execute=True)

    assert not any(key.endswith("/complete.json") for key in store.objects)


def test_execute_uploads_from_a_stable_snapshot_when_source_changes_mid_sync(tmp_path):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "report.json"
    report.parent.mkdir(parents=True)
    original = b'{"state":"before"}\n'
    report.write_bytes(original)

    class MutatingStore(FakeObjectStore):
        def stat(self, key: str):
            report.write_bytes(b'{"state":"after"}\n')
            return super().stat(key)

    store = MutatingStore()
    result = sync_dynamic_outputs.sync_outputs(data_out, store=store, execute=True)
    manifest = json.loads(store.objects[result["manifest_key"]]["content"])
    entry = manifest["files"][0]

    assert store.objects[entry["object_key"]]["content"] == original
    assert entry["sha256"] == hashlib.sha256(original).hexdigest()


def test_restore_rejects_manifest_without_complete_marker(tmp_path):
    snapshot_id = "snapshot-incomplete"
    prefix = "dds/dynamic"
    manifest_key = f"{prefix}/manifests/{snapshot_id}/manifest.json"
    store = FakeObjectStore()
    store.put_bytes(
        manifest_key,
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dds_dynamic_outputs",
                "status": "prepared",
                "snapshot_id": snapshot_id,
                "files": [],
            }
        ).encode("utf-8"),
        content_type="application/json",
    )
    destination = tmp_path / "restore"

    with pytest.raises(ValueError, match="complete marker"):
        sync_dynamic_outputs.restore_outputs(
            snapshot_id,
            destination,
            store=store,
            execute=True,
        )

    assert not destination.exists()


def test_restore_rejects_unsafe_snapshot_id_before_cloud_access(tmp_path):
    with pytest.raises(ValueError, match="unsafe snapshot_id"):
        sync_dynamic_outputs.restore_outputs(
            "../other-prefix",
            tmp_path / "restore",
            store=ExplodingStore(),
            execute=True,
        )


def test_restore_rejects_manifest_path_escape_before_writing(tmp_path):
    snapshot_id = "snapshot-path-escape"
    prefix = "dds/dynamic"
    manifest_key = f"{prefix}/manifests/{snapshot_id}/manifest.json"
    complete_key = f"{prefix}/manifests/{snapshot_id}/complete.json"
    content = b"must stay remote"
    digest = hashlib.sha256(content).hexdigest()
    content_key = f"{prefix}/content/sha256/{digest[:2]}/{digest}"
    store = FakeObjectStore()
    store.objects[content_key] = {"content": content, "version_id": "blob-v1"}
    manifest_bytes = json.dumps(
        {
            "schema_version": 1,
            "kind": "dds_dynamic_outputs",
            "status": "prepared",
            "snapshot_id": snapshot_id,
            "files": [
                {
                    "relative_path": "../escaped.txt",
                    "size": len(content),
                    "sha256": digest,
                    "object_key": content_key,
                    "version_id": "blob-v1",
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    store.put_bytes(manifest_key, manifest_bytes, content_type="application/json")
    store.put_bytes(
        complete_key,
        json.dumps(
            {
                "schema_version": 1,
                "status": "complete",
                "snapshot_id": snapshot_id,
                "manifest_key": manifest_key,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            }
        ).encode("utf-8"),
        content_type="application/json",
    )
    destination = tmp_path / "restore"

    with pytest.raises(ValueError, match="unsafe relative path"):
        sync_dynamic_outputs.restore_outputs(
            snapshot_id,
            destination,
            store=store,
            execute=True,
        )

    assert not (tmp_path / "escaped.txt").exists()


def test_restore_completed_snapshot_recreates_dynamic_output_files(tmp_path):
    source = tmp_path / "source"
    report = source / "reports" / "latest" / "report.json"
    metric = source / "metrics" / "runtime.jsonl"
    report.parent.mkdir(parents=True)
    metric.parent.mkdir(parents=True)
    report.write_bytes(b'{"report": "original"}\n')
    metric.write_bytes(b'{"latency_ms": 8}\n')
    store = FakeObjectStore()
    snapshot = sync_dynamic_outputs.sync_outputs(source, store=store, execute=True)
    destination = tmp_path / "restored"

    result = sync_dynamic_outputs.restore_outputs(
        snapshot["snapshot_id"],
        destination,
        store=store,
        execute=True,
    )

    assert result["status"] == "restored"
    assert result["restored_count"] == 2
    assert (destination / "reports/latest/report.json").read_bytes() == report.read_bytes()
    assert (destination / "metrics/runtime.jsonl").read_bytes() == metric.read_bytes()
    assert list(destination.rglob("*.tmp")) == []
    manifest = json.loads(store.objects[snapshot["manifest_key"]]["content"])
    for item in manifest["files"]:
        assert (item["object_key"], item["version_id"]) in store.get_requests


def test_restore_sha_failure_leaves_no_partial_destination(tmp_path):
    source = tmp_path / "source"
    first = source / "reports" / "a.json"
    second = source / "projects" / "b.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first-good")
    second.write_bytes(b"second-good")
    store = FakeObjectStore()
    snapshot = sync_dynamic_outputs.sync_outputs(source, store=store, execute=True)
    manifest = json.loads(store.objects[snapshot["manifest_key"]]["content"])
    corrupt_key = next(
        item["object_key"]
        for item in manifest["files"]
        if item["relative_path"] == "projects/b.json"
    )
    store.objects[corrupt_key]["content"] = b"corrupted-after-manifest"
    destination = tmp_path / "restored"

    with pytest.raises(ValueError, match="SHA256"):
        sync_dynamic_outputs.restore_outputs(
            snapshot["snapshot_id"],
            destination,
            store=store,
            execute=True,
        )

    assert not destination.exists()
    assert list(tmp_path.glob(".dds-restore-*")) == []


def test_restore_execute_rejects_manifest_entry_without_version_id(tmp_path):
    source = tmp_path / "source"
    report = source / "reports" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_bytes(b'{"versioned": true}\n')
    store = FakeObjectStore()
    snapshot = sync_dynamic_outputs.sync_outputs(source, store=store, execute=True)
    manifest_key = snapshot["manifest_key"]
    complete_key = snapshot["complete_key"]
    manifest = json.loads(store.objects[manifest_key]["content"])
    manifest["files"][0]["version_id"] = None
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    store.objects[manifest_key]["content"] = manifest_bytes
    complete = json.loads(store.objects[complete_key]["content"])
    complete["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    store.objects[complete_key]["content"] = json.dumps(complete).encode("utf-8")
    destination = tmp_path / "restored"

    with pytest.raises(ValueError, match="version_id"):
        sync_dynamic_outputs.restore_outputs(
            snapshot["snapshot_id"],
            destination,
            store=store,
            execute=True,
        )

    assert not destination.exists()


def test_cli_sync_defaults_to_offline_dry_run_without_constructing_tos_client(
    tmp_path,
    monkeypatch,
    capsys,
):
    data_out = tmp_path / "data_out"
    report = data_out / "reports" / "report.json"
    report.parent.mkdir(parents=True)
    report.write_bytes(b'{"offline": true}\n')
    secret = "must-never-appear-in-cli-output"

    def forbidden_store():
        raise AssertionError(secret)

    monkeypatch.setattr(
        sync_dynamic_outputs,
        "_build_tos_store",
        forbidden_store,
        raising=False,
    )

    exit_code = sync_dynamic_outputs.main(["sync", "--root", str(data_out)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert secret not in captured.out + captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "dry_run"
    assert payload["file_count"] == 1


def test_tos_adapter_uses_existing_sdk_and_exact_version_for_download(tmp_path):
    calls: list[tuple[object, ...]] = []

    class Response:
        version_id = "tos-version-7"
        content_length = 4

        def read(self):
            return b"data"

    class FakeSdkClient:
        def head_object(self, bucket, key):
            calls.append(("head_object", bucket, key))
            return Response()

        def put_object_from_file(self, bucket, key, path):
            calls.append(("put_object_from_file", bucket, key, path))
            return Response()

        def put_object(self, bucket, key, *, content, content_type):
            calls.append(("put_object", bucket, key, content, content_type))
            return Response()

        def get_object(self, bucket, key, *, version_id):
            calls.append(("get_object", bucket, key, version_id))
            return Response()

    local_file = tmp_path / "file.bin"
    local_file.write_bytes(b"data")
    store = sync_dynamic_outputs.TosObjectStore(FakeSdkClient(), "bucket-a")

    assert store.stat("content/key").version_id == "tos-version-7"
    assert store.upload_file("content/key", local_file).version_id == "tos-version-7"
    assert store.put_bytes(
        "manifest.json",
        b"{}",
        content_type="application/json",
    ).version_id == "tos-version-7"
    assert store.get_bytes("content/key", version_id="tos-version-7") == b"data"
    assert calls[-1] == ("get_object", "bucket-a", "content/key", "tos-version-7")
