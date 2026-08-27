#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate and publish DDS intelligence snapshots to TOS.

The command is dry-run by default, so it is safe in developer workspaces and
ECS containers without the Volcengine SDK.  ``--execute`` is the only path that
loads runtime configuration and creates a TOS client.

ECS container examples::

    python cloud/volcengine/sync_intelligence_snapshots.py \
      --input /app/data_out/intelligence/pending
    python cloud/volcengine/sync_intelligence_snapshots.py \
      --input /app/data_out/intelligence/pending --execute
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from intelligence_store import prepare_snapshot, publish_snapshot, snapshot_bytes


DEFAULT_STATUS_PATH = Path(__file__).parent / "status" / "intelligence_sync_status.jsonl"
_STATUS_FIELDS = ("sha256", "object_key", "byte_size", "status", "version_id")
_STATUS_LOCK = threading.Lock()
_BASE_REQUIRED = (
    "kind",
    "payload",
    "captured_at",
    "rights_status",
    "source_refs",
    "status",
)
_KIND_REQUIRED = {
    "raw_macro": ("city",),
    "raw_social": ("city", "platform"),
    "normalized": ("city", "platform"),
    "evidence": ("report_id",),
}


def _require_explicit(spec: Mapping[str, Any], field: str) -> Any:
    if field not in spec:
        raise ValueError(f"{field} is required and must be explicit")
    value = spec[field]
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{field} is required and must be explicit")
    return value


def _prepare_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, Mapping):
        raise ValueError("each intelligence snapshot spec must be a JSON object")
    for field in _BASE_REQUIRED:
        _require_explicit(spec, field)

    kind = str(spec["kind"])
    if kind not in _KIND_REQUIRED:
        raise ValueError(f"unsupported intelligence snapshot kind: {kind}")
    for field in _KIND_REQUIRED[kind]:
        _require_explicit(spec, field)

    source_refs = spec["source_refs"]
    if isinstance(source_refs, (str, bytes)) or not isinstance(source_refs, Sequence):
        raise ValueError("source_refs must be an explicit JSON array")

    kwargs: dict[str, Any] = {
        "kind": kind,
        "payload": spec["payload"],
        "captured_at": spec["captured_at"],
        "rights_status": spec["rights_status"],
        "source_refs": source_refs,
        "status": spec["status"],
    }
    if kind in {"raw_macro", "raw_social", "normalized"}:
        kwargs["city"] = spec["city"]
    if kind in {"raw_social", "normalized"}:
        kwargs["platform"] = spec["platform"]
    if kind == "evidence":
        kwargs["report_id"] = spec["report_id"]

    snapshot = prepare_snapshot(**kwargs)
    for field in ("sha256", "object_key"):
        if field in spec and spec[field] != snapshot[field]:
            raise ValueError(f"prepared snapshot {field} does not match its content")
    return snapshot


def _decode_specs(path: Path) -> list[Mapping[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid intelligence JSON: {path.name}") from exc

    if isinstance(value, Mapping) and "snapshots" in value:
        value = value["snapshots"]
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        if not all(isinstance(item, Mapping) for item in value):
            raise ValueError(f"intelligence JSON array must contain objects: {path.name}")
        return value
    raise ValueError(f"intelligence JSON must be an object or array: {path.name}")


def _input_files(input_path: str | Path) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        if path.suffix.lower() != ".json":
            raise ValueError("intelligence input file must use the .json extension")
        return [path]
    if path.is_dir():
        files = sorted(candidate for candidate in path.rglob("*.json") if candidate.is_file())
        if not files:
            raise ValueError("intelligence input directory contains no JSON files")
        return files
    raise ValueError("intelligence input path does not exist")


def _append_status(path: Path, result: Mapping[str, Any]) -> None:
    row = {field: result.get(field) for field in _STATUS_FIELDS}
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with _STATUS_LOCK:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")


def _public_result(kind: str, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": kind, **{field: result.get(field) for field in _STATUS_FIELDS},
            "uploaded": bool(result.get("uploaded")),
            "verified": bool(result.get("verified"))}


def _operation_result(result: Mapping[str, Any], *, execute: bool) -> dict[str, Any]:
    """Separate publication state from the snapshot's semantic readiness."""

    operation = dict(result)
    if not execute:
        operation["status"] = "dry_run"
    elif result.get("verified"):
        operation["status"] = (
            "uploaded_verified" if result.get("uploaded") else "existing_verified"
        )
    else:
        operation["status"] = "failed"
    return operation


def sync_inputs(
    input_path: str | Path,
    *,
    store: Any | None = None,
    execute: bool = False,
    status_path: str | Path = DEFAULT_STATUS_PATH,
) -> dict[str, Any]:
    """Prepare every input before performing any optional TOS write."""

    specs: list[Mapping[str, Any]] = []
    for path in _input_files(input_path):
        specs.extend(_decode_specs(path))
    snapshots = [_prepare_spec(spec) for spec in specs]

    items: list[dict[str, Any]] = []
    status_file = Path(status_path)
    for snapshot in snapshots:
        try:
            result = publish_snapshot(snapshot, store=store, execute=execute)
        except Exception:
            failure = {
                "sha256": snapshot["sha256"],
                "object_key": snapshot["object_key"],
                "byte_size": len(snapshot_bytes(snapshot)),
                "status": "failed",
                "version_id": None,
            }
            _append_status(status_file, failure)
            raise
        operation = _operation_result(result, execute=execute)
        _append_status(status_file, operation)
        items.append(_public_result(str(snapshot["kind"]), operation))

    return {
        "status": "complete" if execute else "dry_run",
        "count": len(items),
        "items": items,
    }


def _build_tos_store() -> Any:
    from sync_dynamic_outputs import TosObjectStore
    import tos_client

    tos_client.load_dotenv(Path(__file__).parent / ".env.volcengine")
    tos_client.disable_proxy()
    config = tos_client.get_tos_config()
    bucket = str(config.get("bucket") or "").strip()
    if not bucket:
        raise RuntimeError("blocked: DDS_TOS_BUCKET is not configured")
    try:
        client = tos_client.get_thread_local_client()
    except ModuleNotFoundError as exc:
        if exc.name == "tos":
            raise RuntimeError(
                "blocked: Volcengine TOS SDK is not installed in this runtime"
            ) from exc
        raise
    return TosObjectStore(client, bucket)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and publish DDS intelligence snapshots (dry-run by default)",
        epilog=(
            "ECS: python cloud/volcengine/sync_intelligence_snapshots.py "
            "--input /app/data_out/intelligence/pending [--execute]"
        ),
    )
    parser.add_argument("--input", required=True, help="JSON file or directory of JSON specs")
    parser.add_argument(
        "--status-log",
        default=str(DEFAULT_STATUS_PATH),
        help="sanitized JSONL status path",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="load ECS runtime identity and publish to TOS",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    store_factory: Callable[[], Any] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    factory = store_factory or _build_tos_store
    try:
        store = factory() if args.execute else None
        result = sync_inputs(
            args.input,
            store=store,
            execute=args.execute,
            status_path=args.status_log,
        )
    except Exception as exc:
        blocked = isinstance(exc, ModuleNotFoundError) or str(exc).startswith("blocked:")
        message = str(exc) if blocked else "intelligence snapshot sync failed"
        print(
            json.dumps(
                {
                    "status": "blocked" if blocked else "error",
                    "error_type": type(exc).__name__,
                    "message": message,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2 if blocked else 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
