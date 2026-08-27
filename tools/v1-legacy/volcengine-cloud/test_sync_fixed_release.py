from __future__ import annotations

import hashlib
import gzip
import json
import sys
from pathlib import Path

import pytest


CLOUD_DIR = Path(__file__).resolve().parent
if str(CLOUD_DIR) not in sys.path:
    sys.path.insert(0, str(CLOUD_DIR))

import sync_fixed_release


class FakeVersionedStore:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = dict(objects)
        self.get_requests: list[tuple[str, str]] = []

    def get_bytes(self, key: str, *, version_id: str) -> bytes:
        assert version_id
        self.get_requests.append((key, version_id))
        return self.objects[(key, version_id)]


def build_release_store(release_id: str = "dds-fixed-test"):
    release_key = f"dds/releases/{release_id}/release_meta.json"
    release_version = "release-version-1"
    manifest_key = f"dds/releases/{release_id}/objects.jsonl"
    manifest_version = "manifest-version-1"
    files = {
        "vault/cities/jinan.csv": b"city,value\nJinan,1\n",
        "reports/catalog.json": b'{"reports": 2}\n',
    }
    rows = []
    objects: dict[tuple[str, str], bytes] = {}
    for index, (relative_path, content) in enumerate(files.items(), start=1):
        object_key = f"immutable/dds/{release_id}/{relative_path}"
        version_id = f"object-version-{index}"
        rows.append(
            {
                "relative_path": relative_path,
                "object_key": object_key,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "version_id": version_id,
            }
        )
        objects[(object_key, version_id)] = content
    manifest_bytes = b"".join(
        json.dumps(row, sort_keys=True).encode("utf-8") + b"\n" for row in rows
    )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    release_meta = {
        "schema_version": "dds.release.v3",
        "release_id": release_id,
        "status": "published",
        "object_manifest": {
            "key": manifest_key,
            "version_id": manifest_version,
            "sha256": manifest_sha256,
            "encoding": "jsonl",
            "object_count": len(rows),
        },
    }
    objects[(release_key, release_version)] = json.dumps(release_meta).encode("utf-8")
    objects[(manifest_key, manifest_version)] = manifest_bytes
    return {
        "store": FakeVersionedStore(objects),
        "release_id": release_id,
        "release_key": release_key,
        "release_version": release_version,
        "manifest_key": manifest_key,
        "manifest_version": manifest_version,
        "manifest_sha256": manifest_sha256,
        "files": files,
        "rows": rows,
    }


def rewrite_manifest(fixture, mutate):
    manifest_address = (fixture["manifest_key"], fixture["manifest_version"])
    rows = [
        json.loads(line)
        for line in fixture["store"].objects[manifest_address].decode("utf-8").splitlines()
        if line.strip()
    ]
    mutate(rows)
    manifest_bytes = b"".join(
        json.dumps(row, sort_keys=True).encode("utf-8") + b"\n" for row in rows
    )
    fixture["store"].objects[manifest_address] = manifest_bytes
    fixture["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    release_address = (fixture["release_key"], fixture["release_version"])
    release_meta = json.loads(fixture["store"].objects[release_address])
    release_meta["object_manifest"]["sha256"] = fixture["manifest_sha256"]
    fixture["store"].objects[release_address] = json.dumps(release_meta).encode("utf-8")


def test_sync_downloads_only_pinned_versions_then_atomically_publishes_cache(tmp_path):
    fixture = build_release_store()
    cache_root = tmp_path / "releases"

    result = sync_fixed_release.sync_release(
        store=fixture["store"],
        release_id=fixture["release_id"],
        release_version_id=fixture["release_version"],
        manifest_key=fixture["manifest_key"],
        manifest_version_id=fixture["manifest_version"],
        manifest_sha256=fixture["manifest_sha256"],
        cache_root=cache_root,
    )

    target = cache_root / fixture["release_id"]
    assert result["status"] == "ready"
    assert result["target"] == str(target)
    for relative_path, content in fixture["files"].items():
        assert (target / relative_path).read_bytes() == content
    assert (fixture["release_key"], fixture["release_version"]) in fixture["store"].get_requests
    assert (fixture["manifest_key"], fixture["manifest_version"]) in fixture["store"].get_requests
    for row in fixture["rows"]:
        assert (row["object_key"], row["version_id"]) in fixture["store"].get_requests
    assert all("current.json" not in key for key, _version in fixture["store"].get_requests)
    assert list(tmp_path.glob(".dds-fixed-release-*")) == []


@pytest.mark.parametrize("missing", ["release", "manifest", "object"])
def test_sync_fails_closed_when_any_required_version_id_is_missing(tmp_path, missing):
    fixture = build_release_store()
    release_version = fixture["release_version"]
    manifest_version = fixture["manifest_version"]
    if missing == "release":
        release_version = ""
    elif missing == "manifest":
        manifest_version = ""
    else:
        rewrite_manifest(fixture, lambda rows: rows[0].pop("version_id"))

    with pytest.raises(ValueError, match="version_id"):
        sync_fixed_release.sync_release(
            store=fixture["store"],
            release_id=fixture["release_id"],
            release_version_id=release_version,
            manifest_key=fixture["manifest_key"],
            manifest_version_id=manifest_version,
            manifest_sha256=fixture["manifest_sha256"],
            cache_root=tmp_path / "releases",
        )

    assert not (tmp_path / "releases" / fixture["release_id"]).exists()


@pytest.mark.parametrize("missing", ["manifest", "object"])
def test_sync_fails_closed_when_any_required_sha256_is_missing(tmp_path, missing):
    fixture = build_release_store()
    manifest_sha256 = fixture["manifest_sha256"]
    if missing == "manifest":
        manifest_sha256 = ""
    else:
        rewrite_manifest(fixture, lambda rows: rows[0].pop("sha256"))
        manifest_sha256 = fixture["manifest_sha256"]

    with pytest.raises(ValueError, match="sha256"):
        sync_fixed_release.sync_release(
            store=fixture["store"],
            release_id=fixture["release_id"],
            release_version_id=fixture["release_version"],
            manifest_key=fixture["manifest_key"],
            manifest_version_id=fixture["manifest_version"],
            manifest_sha256=manifest_sha256,
            cache_root=tmp_path / "releases",
        )

    assert not (tmp_path / "releases" / fixture["release_id"]).exists()


def test_sync_rejects_manifest_content_that_does_not_match_the_pinned_sha(tmp_path):
    fixture = build_release_store()

    with pytest.raises(ValueError, match="manifest sha256"):
        sync_fixed_release.sync_release(
            store=fixture["store"],
            release_id=fixture["release_id"],
            release_version_id=fixture["release_version"],
            manifest_key=fixture["manifest_key"],
            manifest_version_id=fixture["manifest_version"],
            manifest_sha256="0" * 64,
            cache_root=tmp_path / "releases",
        )

    assert not (tmp_path / "releases" / fixture["release_id"]).exists()


def test_sync_rejects_corrupt_object_content_before_publishing_cache(tmp_path):
    fixture = build_release_store()
    row = fixture["rows"][0]
    fixture["store"].objects[(row["object_key"], row["version_id"])] = b"X" * row["size"]

    with pytest.raises(ValueError, match="object sha256"):
        sync_fixed_release.sync_release(
            store=fixture["store"],
            release_id=fixture["release_id"],
            release_version_id=fixture["release_version"],
            manifest_key=fixture["manifest_key"],
            manifest_version_id=fixture["manifest_version"],
            manifest_sha256=fixture["manifest_sha256"],
            cache_root=tmp_path / "releases",
        )

    assert not (tmp_path / "releases" / fixture["release_id"]).exists()


def test_sync_accepts_gzip_manifest_only_when_metadata_declares_uncompressed_hash_scope(tmp_path):
    fixture = build_release_store()
    manifest_address = (fixture["manifest_key"], fixture["manifest_version"])
    raw_manifest = fixture["store"].objects[manifest_address]
    fixture["store"].objects[manifest_address] = gzip.compress(raw_manifest)
    release_address = (fixture["release_key"], fixture["release_version"])
    release_meta = json.loads(fixture["store"].objects[release_address])
    release_meta["object_manifest"]["encoding"] = "jsonl+gzip"
    release_meta["object_manifest"]["hash_scope"] = "uncompressed_jsonl"
    fixture["store"].objects[release_address] = json.dumps(release_meta).encode("utf-8")

    result = sync_fixed_release.sync_release(
        store=fixture["store"],
        release_id=fixture["release_id"],
        release_version_id=fixture["release_version"],
        manifest_key=fixture["manifest_key"],
        manifest_version_id=fixture["manifest_version"],
        manifest_sha256=fixture["manifest_sha256"],
        cache_root=tmp_path / "releases",
    )

    assert result["status"] == "ready"
    assert (tmp_path / "releases" / fixture["release_id"] / "reports/catalog.json").is_file()
