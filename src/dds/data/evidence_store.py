"""Content-addressed, tamper-evident evidence packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping

from dds.config import Settings


class EvidencePackageIntegrityError(ValueError):
    pass


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_normalize(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _normalize(value.to_dict())
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def semantic_hash(value: Any) -> str:
    return sha256(canonical_json(value)).hexdigest()


def _safe_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or not re.fullmatch(r"[\w.\-\u3400-\u9fff]+", value):
        raise ValueError("project_id contains unsupported path characters")
    if value in {".", ".."}:
        raise ValueError("project_id must not be a relative path")
    return value


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class FrozenEvidencePackage:
    package_dir: Path
    content_hash: str
    evidence_count: int
    manifest_path: Path
    evidence_path: Path


class EvidenceStore:
    """Freeze evidence once and validate it before every downstream compile."""

    SCHEMA_VERSION = "dds-evidence-package/2.0"

    def __init__(self, projects_root: Path | None = None) -> None:
        settings = Settings.from_env()
        self.projects_root = (projects_root or settings.projects_root).resolve()

    def freeze(
        self,
        project_id: str,
        *,
        project_context: Any,
        evidence: Iterable[Any],
        query_manifests: Iterable[Any] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> FrozenEvidencePackage:
        project_id = _safe_project_id(project_id)
        records = [_normalize(item) for item in evidence]
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "project_context": _normalize(project_context),
            "evidence": records,
            "queries": [_normalize(item) for item in query_manifests],
            "metadata": _normalize(dict(metadata or {})),
        }
        content_hash = semantic_hash(payload)
        package_dir = self.projects_root / project_id / "evidence" / content_hash
        evidence_path = package_dir / "evidence.json"
        manifest_path = package_dir / "manifest.json"
        if package_dir.exists():
            return self.validate(package_dir)

        package_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            package_dir.mkdir()
        except FileExistsError:
            # Another freezer won the content-addressed directory race.  Never
            # overwrite its files: it must already be a complete valid package.
            return self.validate(package_dir)

        manifest = {
            "schema_version": self.SCHEMA_VERSION,
            "project_id": project_id,
            "content_hash": content_hash,
            "evidence_count": len(records),
            "frozen_at": datetime.now(timezone.utc).isoformat(),
            "evidence_file": evidence_path.name,
        }
        _atomic_write(evidence_path, canonical_json(payload) + b"\n")
        _atomic_write(manifest_path, canonical_json(manifest) + b"\n")
        return FrozenEvidencePackage(
            package_dir=package_dir,
            content_hash=content_hash,
            evidence_count=len(records),
            manifest_path=manifest_path,
            evidence_path=evidence_path,
        )

    def validate(self, package_dir: Path) -> FrozenEvidencePackage:
        package_dir = package_dir.resolve()
        manifest_path = package_dir / "manifest.json"
        if not manifest_path.is_file():
            raise EvidencePackageIntegrityError("manifest.json is missing")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidencePackageIntegrityError("manifest.json is unreadable") from exc
        if not isinstance(manifest, Mapping):
            raise EvidencePackageIntegrityError("manifest.json must be an object")
        if manifest.get("schema_version") != self.SCHEMA_VERSION:
            raise EvidencePackageIntegrityError("manifest schema_version is invalid")
        if manifest.get("project_id") != package_dir.parent.parent.name:
            raise EvidencePackageIntegrityError("manifest project_id does not match package path")
        if not str(manifest.get("frozen_at") or "").strip():
            raise EvidencePackageIntegrityError("manifest frozen_at is missing")
        evidence_name = manifest.get("evidence_file")
        if not isinstance(evidence_name, str) or not evidence_name:
            raise EvidencePackageIntegrityError("manifest evidence_file is missing")
        if Path(evidence_name).name != evidence_name:
            raise EvidencePackageIntegrityError("evidence_file must be a local filename")
        evidence_path = package_dir / evidence_name
        if not evidence_path.is_file():
            raise EvidencePackageIntegrityError("evidence payload is missing")
        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidencePackageIntegrityError("evidence payload is unreadable") from exc
        if not isinstance(payload, Mapping):
            raise EvidencePackageIntegrityError("evidence payload must be an object")
        if payload.get("schema_version") != self.SCHEMA_VERSION:
            raise EvidencePackageIntegrityError("evidence schema_version is invalid")
        actual_hash = semantic_hash(payload)
        expected_hash = manifest.get("content_hash")
        if actual_hash != expected_hash or package_dir.name != expected_hash:
            raise EvidencePackageIntegrityError(
                "evidence package hash mismatch; payload may have been modified"
            )
        evidence = payload.get("evidence")
        if not isinstance(evidence, list):
            raise EvidencePackageIntegrityError("evidence must be a list")
        if manifest.get("evidence_count") != len(evidence):
            raise EvidencePackageIntegrityError("evidence_count does not match payload")
        return FrozenEvidencePackage(
            package_dir=package_dir,
            content_hash=actual_hash,
            evidence_count=len(evidence),
            manifest_path=manifest_path,
            evidence_path=evidence_path,
        )


__all__ = [
    "EvidencePackageIntegrityError",
    "EvidenceStore",
    "FrozenEvidencePackage",
    "canonical_json",
    "semantic_hash",
]
