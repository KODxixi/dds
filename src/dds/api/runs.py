"""Idempotent, checkpointed report-run state persisted as canonical JSON."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Mapping


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(slots=True)
class RunRecord:
    run_id: str
    idempotency_key: str
    status: RunStatus = RunStatus.PENDING
    checkpoint: str = "created"
    attempts: int = 0
    project_id: str = ""
    evidence_package_hash: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    recovery: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RunRecord":
        payload = dict(value)
        payload["status"] = RunStatus(payload.get("status", RunStatus.PENDING.value))
        return cls(**payload)


def _run_id(idempotency_key: str) -> str:
    key = idempotency_key.strip()
    if not key:
        raise ValueError("idempotency_key must not be empty")
    return sha256(key.encode("utf-8")).hexdigest()[:24]


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"


class RunStore:
    """Small local state store suitable for retries and process restarts."""

    _lock = RLock()

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, run_id: str) -> Path:
        if not run_id or any(token in run_id for token in ("/", "\\", "..")):
            raise ValueError("invalid run_id")
        return self.root / f"{run_id}.json"

    def _write(self, record: RunRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._path(record.run_id)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=self.root, prefix=f".{record.run_id}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical(record.to_dict()))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def create_or_get(self, idempotency_key: str, *, project_id: str = "") -> RunRecord:
        run_id = _run_id(idempotency_key)
        with self._lock:
            existing = self.load(run_id)
            if existing is not None:
                if existing.idempotency_key != idempotency_key.strip():
                    raise ValueError("idempotency hash collision")
                return existing
            record = RunRecord(
                run_id=run_id,
                idempotency_key=idempotency_key.strip(),
                project_id=project_id,
            )
            self._write(record)
            return record

    def load(self, run_id: str) -> RunRecord | None:
        path = self._path(run_id)
        if not path.is_file():
            return None
        return RunRecord.from_mapping(json.loads(path.read_text(encoding="utf-8")))

    def transition(
        self,
        run_id: str,
        *,
        status: RunStatus,
        checkpoint: str,
        evidence_package_hash: str | None = None,
        artifacts: Mapping[str, str] | None = None,
        error: str | None = None,
        recovery: Mapping[str, Any] | None = None,
        increment_attempt: bool = False,
    ) -> RunRecord:
        with self._lock:
            record = self.load(run_id)
            if record is None:
                raise KeyError(run_id)
            if record.status is RunStatus.COMPLETED and status is not RunStatus.COMPLETED:
                raise ValueError("a completed run cannot be reopened in place")
            record.status = status
            record.checkpoint = checkpoint
            record.evidence_package_hash = (
                evidence_package_hash or record.evidence_package_hash
            )
            if artifacts:
                # A failed retry cannot erase a previously valid artifact.
                record.artifacts.update(dict(artifacts))
            record.error = error
            record.recovery = dict(recovery or record.recovery)
            if increment_attempt:
                record.attempts += 1
            record.updated_at = datetime.now(timezone.utc).isoformat()
            self._write(record)
            return record


__all__ = ["RunRecord", "RunStatus", "RunStore"]
