"""Central configuration for the DDS V2 runtime."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _repository_root() -> Path:
    # settings.py -> config -> dds -> src -> repository root
    return Path(__file__).resolve().parents[3]


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default.resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    """Explicit runtime paths and quality thresholds.

    The V1 vault is a read-only source.  All caches, frozen evidence and reports
    are written below the V2 repository unless a caller explicitly overrides the
    destination.
    """

    repository_root: Path
    v1_vault_root: Path
    projects_root: Path
    cache_root: Path
    exports_root: Path
    minimum_competitors: int = 5
    degraded_competitors: int = 3

    @classmethod
    def from_env(cls, repository_root: Path | None = None) -> "Settings":
        root = (repository_root or _repository_root()).resolve()
        data_root = root / "data"
        minimum = int(os.getenv("DDS_MINIMUM_COMPETITORS", "5"))
        degraded = int(os.getenv("DDS_DEGRADED_COMPETITORS", "3"))
        if minimum < 1:
            raise ValueError("DDS_MINIMUM_COMPETITORS must be at least 1")
        if degraded < 1 or degraded > minimum:
            raise ValueError(
                "DDS_DEGRADED_COMPETITORS must be between 1 and the minimum"
            )
        return cls(
            repository_root=root,
            v1_vault_root=_env_path(
                "DDS_V1_VAULT_ROOT",
                root.parent / "DDS" / "Vault",
            ),
            projects_root=_env_path("DDS_PROJECTS_ROOT", data_root / "projects"),
            cache_root=_env_path("DDS_CACHE_ROOT", data_root / "cache"),
            exports_root=_env_path("DDS_EXPORTS_ROOT", data_root / "exports"),
            minimum_competitors=minimum,
            degraded_competitors=degraded,
        )

    def ensure_runtime_directories(self) -> None:
        """Create V2-owned runtime directories, never the V1 source root."""

        for directory in (self.projects_root, self.cache_root, self.exports_root):
            directory.mkdir(parents=True, exist_ok=True)
