"""Shared local-source utilities for read-only adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from dds.data.catalog import DatasetAsset, DatasetKind, hash_file


def safe_name(value: str, label: str = "name") -> str:
    normalized = value.strip()
    if not normalized or any(token in normalized for token in ("/", "\\", "..")):
        raise ValueError(f"{label} must be a name, not a path")
    if not re.fullmatch(r"[\w\-\u3400-\u9fff]+", normalized):
        raise ValueError(f"unsupported characters in {label}: {value!r}")
    return normalized


def asset_from_path(
    path: Path,
    *,
    kind: DatasetKind,
    city: str | None,
    authority: str,
) -> DatasetAsset:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    stat = path.stat()
    return DatasetAsset(
        kind=kind,
        path=path,
        city=city,
        format=path.suffix.lower().lstrip("."),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        source_hash=hash_file(path),
        authority=authority,
    )


def row_digest(row: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def connect_duckdb():
    try:
        import duckdb  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DuckDB is required; install dds[data]") from exc
    return duckdb.connect(database=":memory:")
