#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Content-addressed sync and restore for DDS runtime outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


DYNAMIC_OUTPUT_ROOTS = (
    "reports",
    "projects",
    "metrics",
    "ceo_learning",
    "architecture_learning",
    "lineage",
)
DEFAULT_PREFIX = "dds/dynamic"
SAFE_SNAPSHOT_ID = re.compile(r"^snapshot-[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")


class TosObjectStore:
    """Small injectable adapter over the existing Volcengine TOS SDK client."""

    def __init__(self, client: Any, bucket: str) -> None:
        self.client = client
        self.bucket = str(bucket)

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        code = str(getattr(exc, "code", "") or "")
        return status == 404 or code in {"NoSuchKey", "NotFound", "404"}

    def stat(self, key: str) -> Any | None:
        try:
            return self.client.head_object(self.bucket, key)
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            raise

    def upload_file(self, key: str, local_path: Path) -> Any:
        return self.client.put_object_from_file(self.bucket, key, str(local_path))

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> Any:
        return self.client.put_object(
            self.bucket,
            key,
            content=content,
            content_type=content_type,
        )

    def get_bytes(self, key: str, *, version_id: str | None = None) -> bytes:
        response = self.client.get_object(
            self.bucket,
            key,
            version_id=version_id,
        )
        return bytes(response.read())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_outputs(data_root: Path, prefix: str) -> list[dict[str, Any]]:
    root = data_root.resolve()
    files: list[dict[str, Any]] = []
    for root_name in DYNAMIC_OUTPUT_ROOTS:
        output_root = data_root / root_name
        if not output_root.exists():
            continue
        for candidate in output_root.rglob("*"):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"output path escapes data root: {candidate}") from exc
            relative_path = candidate.relative_to(data_root).as_posix()
            digest = _sha256_file(resolved)
            files.append(
                {
                    "relative_path": relative_path,
                    "size": resolved.stat().st_size,
                    "sha256": digest,
                    "object_key": f"{prefix}/content/sha256/{digest[:2]}/{digest}",
                    "version_id": None,
                    "_local_path": resolved,
                }
            )
    files.sort(key=lambda item: item["relative_path"])
    return files


def _snapshot_outputs(data_root: Path, snapshot_root: Path) -> None:
    """Copy approved dynamic outputs before hashing or uploading them.

    The live DDS process can append metrics and learning files while a sync is
    running.  A private copy gives one sync run a stable byte sequence and
    prevents a manifest digest from describing an earlier version of a file.
    """

    root = data_root.resolve()
    for root_name in DYNAMIC_OUTPUT_ROOTS:
        output_root = data_root / root_name
        if not output_root.exists():
            continue
        for candidate in output_root.rglob("*"):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=True)
            try:
                relative = resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"output path escapes data root: {candidate}") from exc
            target = snapshot_root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved, target)


def _snapshot_id(files: list[dict[str, Any]]) -> str:
    identity = [
        {
            "relative_path": item["relative_path"],
            "size": item["size"],
            "sha256": item["sha256"],
        }
        for item in files
    ]
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "snapshot-" + hashlib.sha256(encoded).hexdigest()[:24]


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _version_id(response: Any) -> str | None:
    if isinstance(response, dict):
        value = response.get("version_id")
    else:
        value = getattr(response, "version_id", None)
    return str(value) if value not in {None, ""} else None


def _remote_size(response: Any) -> int | None:
    if isinstance(response, dict):
        value = response.get("size")
    else:
        value = getattr(response, "size", None)
        if value is None:
            value = getattr(response, "content_length", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _decode_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid {label} JSON object")
    return payload


def _validate_relative_output_path(value: Any) -> PurePosixPath:
    text = str(value or "")
    parts = text.split("/")
    path = PurePosixPath(text)
    if (
        not text
        or "\\" in text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or not path.parts
        or path.parts[0] not in DYNAMIC_OUTPUT_ROOTS
    ):
        raise ValueError(f"unsafe relative path in manifest: {text}")
    return path


def sync_outputs(
    data_root: str | Path,
    *,
    store: Any | None = None,
    execute: bool = False,
    prefix: str = DEFAULT_PREFIX,
) -> dict[str, Any]:
    """Build a deterministic sync plan; cloud writes require ``execute=True``."""

    root = Path(data_root)
    normalized_prefix = str(prefix).strip("/")
    with tempfile.TemporaryDirectory(prefix=".dds-sync-") as snapshot_directory:
        snapshot_root = Path(snapshot_directory)
        _snapshot_outputs(root, snapshot_root)
        files = _scan_outputs(snapshot_root, normalized_prefix)
        public_files = [{key: value for key, value in item.items() if not key.startswith("_")} for item in files]
        snapshot_id = _snapshot_id(files)
        result = {
            "status": "dry_run",
            "snapshot_id": snapshot_id,
            "file_count": len(files),
            "total_size": sum(int(item["size"]) for item in files),
            "files": public_files,
        }
        if not execute:
            return result
        if store is None:
            raise ValueError("object store is required when execute=True")
        uploaded_count = 0
        skipped_count = 0
        for local, public in zip(files, public_files, strict=True):
            remote = store.stat(public["object_key"])
            if remote is None:
                response = store.upload_file(public["object_key"], local["_local_path"])
                version_id = _version_id(response)
                if version_id is None:
                    raise ValueError(
                        f"uploaded object has no version_id: {public['object_key']}"
                    )
                public["version_id"] = version_id
                uploaded_count += 1
            else:
                remote_size = _remote_size(remote)
                if remote_size != public["size"]:
                    raise ValueError(
                        f"remote size mismatch for immutable object: {public['object_key']}"
                    )
                version_id = _version_id(remote)
                if version_id is None:
                    raise ValueError(
                        f"existing object has no version_id: {public['object_key']}"
                    )
                remote_content = store.get_bytes(public["object_key"], version_id=version_id)
                if len(remote_content) != public["size"] or hashlib.sha256(remote_content).hexdigest() != public["sha256"]:
                    raise ValueError(
                        f"remote SHA256 mismatch for immutable object: {public['object_key']}"
                    )
                public["version_id"] = version_id
                skipped_count += 1

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        manifest = {
            "schema_version": 1,
            "kind": "dds_dynamic_outputs",
            "status": "prepared",
            "snapshot_id": snapshot_id,
            "created_at": now,
            "file_count": len(public_files),
            "total_size": result["total_size"],
            "files": public_files,
        }
        manifest_bytes = _canonical_json(manifest)
        manifest_key = f"{normalized_prefix}/manifests/{snapshot_id}/manifest.json"
        manifest_response = store.put_bytes(
            manifest_key,
            manifest_bytes,
            content_type="application/json",
        )
        manifest_version_id = _version_id(manifest_response)
        if manifest_version_id is None:
            raise ValueError("uploaded manifest has no version_id")
        complete = {
            "schema_version": 1,
            "status": "complete",
            "snapshot_id": snapshot_id,
            "completed_at": now,
            "manifest_key": manifest_key,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "manifest_version_id": manifest_version_id,
        }
        complete_key = f"{normalized_prefix}/manifests/{snapshot_id}/complete.json"
        store.put_bytes(
            complete_key,
            _canonical_json(complete),
            content_type="application/json",
        )
        result.update(
            {
                "status": "complete",
                "files": public_files,
                "manifest_key": manifest_key,
                "complete_key": complete_key,
                "uploaded_count": uploaded_count,
                "skipped_count": skipped_count,
            }
        )
        return result


def restore_outputs(
    snapshot_id: str,
    destination_root: str | Path,
    *,
    store: Any,
    execute: bool = False,
    prefix: str = DEFAULT_PREFIX,
) -> dict[str, Any]:
    """Restore one completed snapshot into a local DDS data_out directory."""

    if not SAFE_SNAPSHOT_ID.fullmatch(str(snapshot_id or "")):
        raise ValueError(f"unsafe snapshot_id: {snapshot_id}")
    normalized_prefix = str(prefix).strip("/")
    complete_key = f"{normalized_prefix}/manifests/{snapshot_id}/complete.json"
    try:
        complete_bytes = store.get_bytes(complete_key, version_id=None)
    except KeyError as exc:
        raise ValueError(f"snapshot has no complete marker: {snapshot_id}") from exc
    complete = _decode_json_object(complete_bytes, "complete marker")
    if complete.get("status") != "complete" or complete.get("snapshot_id") != snapshot_id:
        raise ValueError(f"snapshot has invalid complete marker: {snapshot_id}")
    expected_manifest_key = f"{normalized_prefix}/manifests/{snapshot_id}/manifest.json"
    if complete.get("manifest_key") != expected_manifest_key:
        raise ValueError("complete marker points to an unexpected manifest")
    manifest_bytes = store.get_bytes(
        expected_manifest_key,
        version_id=_version_id({"version_id": complete.get("manifest_version_id")}),
    )
    if hashlib.sha256(manifest_bytes).hexdigest() != complete.get("manifest_sha256"):
        raise ValueError("manifest SHA256 does not match complete marker")
    manifest = _decode_json_object(manifest_bytes, "manifest")
    if manifest.get("snapshot_id") != snapshot_id:
        raise ValueError("manifest snapshot_id does not match requested snapshot")
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        raise ValueError("manifest files must be a list")
    validated_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for item in manifest_files:
        if not isinstance(item, dict):
            raise ValueError("manifest file entry must be an object")
        relative_path = _validate_relative_output_path(item.get("relative_path"))
        relative_text = relative_path.as_posix()
        if relative_text in seen_paths:
            raise ValueError(f"duplicate relative path in manifest: {relative_text}")
        seen_paths.add(relative_text)
        digest = str(item.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid SHA256 in manifest: {relative_text}")
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid size in manifest: {relative_text}") from exc
        if size < 0:
            raise ValueError(f"invalid size in manifest: {relative_text}")
        expected_object_key = (
            f"{normalized_prefix}/content/sha256/{digest[:2]}/{digest}"
        )
        if item.get("object_key") != expected_object_key:
            raise ValueError(f"object key does not match SHA256: {relative_text}")
        version_id = _version_id({"version_id": item.get("version_id")})
        if version_id is None:
            raise ValueError(f"manifest entry has no version_id: {relative_text}")
        validated_files.append(
            {
                "relative_path": relative_path,
                "size": size,
                "sha256": digest,
                "object_key": expected_object_key,
                "version_id": version_id,
            }
        )

    destination = Path(destination_root)
    destination_parent = destination.parent.resolve()
    destination_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".dds-restore-", dir=str(destination_parent))
    )
    try:
        for item in validated_files:
            content = store.get_bytes(
                item["object_key"],
                version_id=item["version_id"],
            )
            digest = hashlib.sha256(content).hexdigest()
            relative_text = item["relative_path"].as_posix()
            if digest != item["sha256"]:
                raise ValueError(f"restored file SHA256 mismatch: {relative_text}")
            if len(content) != item["size"]:
                raise ValueError(f"restored file size mismatch: {relative_text}")
            staged_path = staging.joinpath(*item["relative_path"].parts)
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_bytes(content)

        if not execute:
            return {
                "status": "validated",
                "snapshot_id": snapshot_id,
                "verified_count": len(validated_files),
                "restored_count": 0,
                "manifest_key": expected_manifest_key,
            }

        destination_existed = destination.exists()
        backup = Path(
            tempfile.mkdtemp(prefix=".dds-backup-", dir=str(destination_parent))
        )
        replaced: list[tuple[Path, Path | None]] = []
        try:
            for item in validated_files:
                relative = item["relative_path"]
                staged_path = staging.joinpath(*relative.parts)
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                backup_path: Path | None = None
                if target.exists():
                    if not target.is_file():
                        raise ValueError(f"restore target is not a file: {relative.as_posix()}")
                    backup_path = backup.joinpath(*relative.parts)
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, backup_path)
                replaced.append((target, backup_path))
                os.replace(staged_path, target)
        except Exception:
            for target, backup_path in reversed(replaced):
                if target.exists() and target.is_file():
                    target.unlink()
                if backup_path is not None and backup_path.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup_path, target)
            if not destination_existed and destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)

        return {
            "status": "restored",
            "snapshot_id": snapshot_id,
            "verified_count": len(validated_files),
            "restored_count": len(validated_files),
            "manifest_key": expected_manifest_key,
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _build_tos_store() -> TosObjectStore:
    import tos_client

    tos_client.load_dotenv(Path(__file__).parent / ".env.volcengine")
    tos_client.disable_proxy()
    config = tos_client.get_tos_config()
    bucket = str(config.get("bucket") or "")
    if not bucket:
        raise RuntimeError("DDS_TOS_BUCKET is not configured")
    return TosObjectStore(tos_client.get_thread_local_client(), bucket)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync and restore DDS runtime outputs with content-addressed TOS objects"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="plan or execute an incremental sync")
    sync_parser.add_argument("--root", default="/app/data_out")
    sync_parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    sync_parser.add_argument("--execute", action="store_true")

    restore_parser = subparsers.add_parser("restore", help="validate or restore a completed snapshot")
    restore_parser.add_argument("--snapshot-id", required=True)
    restore_parser.add_argument("--destination", default="/app/data_out")
    restore_parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    restore_parser.add_argument("--execute", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            store = _build_tos_store() if args.execute else None
            result = sync_outputs(
                args.root,
                store=store,
                execute=args.execute,
                prefix=args.prefix,
            )
        else:
            result = restore_outputs(
                args.snapshot_id,
                args.destination,
                store=_build_tos_store(),
                execute=args.execute,
                prefix=args.prefix,
            )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
