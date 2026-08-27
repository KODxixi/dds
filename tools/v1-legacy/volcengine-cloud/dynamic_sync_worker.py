#!/usr/bin/env python3
"""Periodic, content-addressed TOS sync for DDS runtime outputs.

The worker has no cloud side effects unless ``--execute`` is explicit.  It is
designed for the private DDS container on the same ECS as GarchOS/Caddy and
records only sanitized operational metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sync_dynamic_outputs import _build_tos_store, sync_outputs


DEFAULT_ROOT = Path("/app/data_out")
DEFAULT_STATUS_PATH = DEFAULT_ROOT / "metrics" / "dynamic_sync_status.jsonl"
MIN_INTERVAL_SECONDS = 60

_STATUS_FIELDS = (
    "status",
    "snapshot_id",
    "file_count",
    "total_size",
    "uploaded_count",
    "skipped_count",
    "error_type",
)


def _status_row(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **{
            field: result.get(field)
            for field in _STATUS_FIELDS
            if result.get(field) is not None
        },
    }


def _append_status(path: str | Path, result: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        _status_row(result),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with target.open("a", encoding="utf-8") as stream:
        stream.write(encoded + "\n")


def run_cycle(
    data_root: str | Path = DEFAULT_ROOT,
    *,
    execute: bool = False,
    store_factory: Callable[[], Any] = _build_tos_store,
    status_path: str | Path = DEFAULT_STATUS_PATH,
) -> dict[str, Any]:
    """Run one safe sync cycle and return a sanitized result."""

    try:
        store = store_factory() if execute else None
        result = sync_outputs(data_root, store=store, execute=execute)
    except Exception as exc:  # Status must not copy exception messages/secrets.
        result = {"status": "failed", "error_type": type(exc).__name__}
    _append_status(status_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Periodically sync DDS runtime outputs to TOS"
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--status-log", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=0,
        help="0 runs one cycle; recurring mode requires at least 60 seconds",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    interval = int(args.interval_seconds)
    if interval < 0 or 0 < interval < MIN_INTERVAL_SECONDS:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": "InvalidInterval",
                    "minimum_interval_seconds": MIN_INTERVAL_SECONDS,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    while True:
        result = run_cycle(
            args.root,
            execute=bool(args.execute),
            status_path=args.status_log,
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if interval == 0:
            return 0 if result.get("status") != "failed" else 1
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
