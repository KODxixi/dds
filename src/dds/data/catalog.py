"""Local dataset discovery with explicit provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from pathlib import Path
import re

from dds.config import Settings


class DatasetKind(str, Enum):
    NEW_HOME_LISTINGS = "new_home_listings"
    TRANSACTIONS = "transactions"
    LAND = "land"
    MACRO = "macro"
    USER_MATERIAL = "user_material"


@dataclass(frozen=True, slots=True)
class DatasetAsset:
    kind: DatasetKind
    path: Path
    city: str | None
    format: str
    size_bytes: int
    modified_at: str
    source_hash: str
    authority: str

    @property
    def source_ref(self) -> str:
        return self.path.as_uri()


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_city(city: str) -> str:
    normalized = city.strip()
    if not normalized:
        raise ValueError("city must not be empty")
    if any(token in normalized for token in ("/", "\\", "..")):
        raise ValueError("city must be a name, not a path")
    if not re.fullmatch(r"[\w\-\u3400-\u9fff]+", normalized):
        raise ValueError(f"unsupported characters in city: {city!r}")
    return normalized


class DatasetNotFoundError(FileNotFoundError):
    pass


class DatasetCatalog:
    """Resolve configured read-only data assets without implicit downloads."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def resolve_new_home_listings(self, city: str) -> DatasetAsset:
        city = _safe_city(city)
        candidates = (
            self.settings.v1_vault_root / "2026新楼盘" / f"新楼盘-{city}.parquet",
            self.settings.v1_vault_root / "2026新楼盘" / f"新楼盘-{city}.csv",
        )
        for path in candidates:
            if path.is_file():
                stat = path.stat()
                return DatasetAsset(
                    kind=DatasetKind.NEW_HOME_LISTINGS,
                    path=path.resolve(),
                    city=city,
                    format=path.suffix.lower().lstrip("."),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    source_hash=hash_file(path),
                    authority="purchased_dataset_archive",
                )
        searched = ", ".join(str(path) for path in candidates)
        raise DatasetNotFoundError(
            f"No configured new-home dataset for {city}; searched: {searched}"
        )


__all__ = [
    "DatasetAsset",
    "DatasetCatalog",
    "DatasetKind",
    "DatasetNotFoundError",
    "hash_file",
]
