"""Load the server-owned project_ref registry for deterministic report jobs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REGISTRY_SCHEMA = "dds.project-report-registry/1.0"
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


class ProjectReportRegistryError(ValueError):
    """Raised when the trusted server registry is malformed or escapes its root."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectReportRegistryError(f"cannot read project report registry: {path}") from exc
    if not isinstance(payload, dict):
        raise ProjectReportRegistryError("project report registry must be a JSON object")
    return payload


def load_project_report_registry(
    registry_path: str | Path, *, workspace_root: str | Path
) -> dict[str, Path]:
    """Resolve enabled, workspace-relative entries without exposing paths to callers."""
    path = Path(registry_path)
    if not path.exists():
        return {}
    payload = _load_object(path)
    if payload.get("schema_version") != REGISTRY_SCHEMA:
        raise ProjectReportRegistryError("unsupported project report registry schema")
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        raise ProjectReportRegistryError("project report registry projects must be an object")

    root = Path(workspace_root).resolve()
    result: dict[str, Path] = {}
    for raw_ref, raw_entry in sorted(projects.items()):
        ref = str(raw_ref).strip()
        if not _SAFE_REF.fullmatch(ref) or ref in {".", ".."}:
            raise ProjectReportRegistryError(f"invalid project_ref: {raw_ref!r}")
        if not isinstance(raw_entry, dict):
            raise ProjectReportRegistryError(f"registry entry must be an object: {ref}")
        if raw_entry.get("enabled", True) is False:
            continue
        relative = raw_entry.get("relative_path")
        if not isinstance(relative, str) or not relative.strip():
            raise ProjectReportRegistryError(f"relative_path is required: {ref}")
        relative = relative.strip()
        if (
            Path(relative).is_absolute()
            or _WINDOWS_DRIVE.match(relative)
            or relative.lower().startswith("file:")
        ):
            raise ProjectReportRegistryError(f"relative_path must stay inside workspace: {ref}")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ProjectReportRegistryError(
                f"relative_path escapes workspace: {ref}"
            ) from exc
        if not target.is_dir() or not (target / "inbox").is_dir():
            raise ProjectReportRegistryError(f"registered project inbox is missing: {ref}")
        result[ref] = target
    return result
