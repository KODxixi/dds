"""SQLite truth source for DDS Decision Field jobs and resumable events."""

from __future__ import annotations

import json
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


ACTIVE_STATUSES = {"queued", "running"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class JobIdempotencyConflictError(RuntimeError):
    pass


class DecisionFieldJobStore:
    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        if self.database != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def create_job(
        self,
        *,
        kind: str,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        normalized_metadata = deepcopy(metadata or {})
        owner_id = str(normalized_metadata.get("owner_id") or "")
        project_id = str(normalized_metadata.get("project_id") or "")
        operation = str(normalized_metadata.get("operation") or kind)
        key = str(idempotency_key or "").strip()
        fingerprint = str(request_fingerprint or "").strip()
        if key and not fingerprint:
            raise ValueError("request_fingerprint is required with idempotency_key")

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if key:
                    existing = self._connection.execute(
                        """
                        SELECT request_fingerprint, job_id
                        FROM job_idempotency
                        WHERE owner_id = ? AND project_id = ? AND operation = ?
                          AND idempotency_key = ?
                        """,
                        (owner_id, project_id, operation, key),
                    ).fetchone()
                    if existing:
                        if existing["request_fingerprint"] != fingerprint:
                            raise JobIdempotencyConflictError(
                                "idempotency key was used for another job request"
                            )
                        job = self._get_job_locked(str(existing["job_id"]))
                        self._connection.execute("COMMIT")
                        if job is None:
                            raise RuntimeError("idempotency record points to a missing job")
                        return job, True

                job_id = f"job_{uuid4().hex}"
                created_at = _now()
                self._connection.execute(
                    """
                    INSERT INTO jobs (
                        id, kind, status, stage, progress, message,
                        metadata_json, result_json, error_json,
                        external_job_id, retry_state, created_at, updated_at
                    ) VALUES (?, ?, 'queued', 'queued', 0, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        job_id,
                        str(kind),
                        "任务已创建",
                        self._json(normalized_metadata),
                        created_at,
                        created_at,
                    ),
                )
                job = self._get_job_locked(job_id)
                assert job is not None
                self._append_event_locked(job)
                if key:
                    self._connection.execute(
                        """
                        INSERT INTO job_idempotency (
                            owner_id, project_id, operation, idempotency_key,
                            request_fingerprint, job_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            owner_id,
                            project_id,
                            operation,
                            key,
                            fingerprint,
                            job_id,
                            created_at,
                        ),
                    )
                self._connection.execute("COMMIT")
                return job, False
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_job(self, job_id: str, *, owner_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._get_job_locked(job_id)
            if job is None:
                return None
            if owner_id is not None and str(job["metadata"].get("owner_id") or "") != str(owner_id):
                return None
            return job

    def update_job(self, job_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "stage",
            "progress",
            "message",
            "metadata",
            "result",
            "error",
            "external_job_id",
            "retry_state",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported job fields: {sorted(unknown)}")
        with self._lock:
            current = self._get_job_locked(job_id)
            if current is None:
                raise KeyError(job_id)
            current.update(deepcopy(changes))
            current["updated_at"] = _now()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    UPDATE jobs SET
                        status = ?, stage = ?, progress = ?, message = ?,
                        metadata_json = ?, result_json = ?, error_json = ?,
                        external_job_id = ?, retry_state = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        current["status"],
                        current["stage"],
                        int(current["progress"]),
                        current["message"],
                        self._json(current["metadata"]),
                        self._json_or_none(current.get("result")),
                        self._json_or_none(current.get("error")),
                        current.get("external_job_id"),
                        current.get("retry_state"),
                        current["updated_at"],
                        job_id,
                    ),
                )
                self._append_event_locked(current)
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            return deepcopy(current)

    def list_events(
        self,
        job_id: str,
        *,
        owner_id: str | None = None,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            if self.get_job(job_id, owner_id=owner_id) is None:
                return []
            rows = self._connection.execute(
                """
                SELECT seq, snapshot_json, created_at
                FROM job_events
                WHERE job_id = ? AND seq > ?
                ORDER BY seq ASC
                """,
                (job_id, int(after_seq)),
            ).fetchall()
            events = []
            for row in rows:
                snapshot = json.loads(row["snapshot_json"])
                snapshot["seq"] = int(row["seq"])
                snapshot["event_created_at"] = row["created_at"]
                events.append(snapshot)
            return events

    def recover_interrupted_jobs(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at, id"
            ).fetchall()
            recovered = []
            for row in rows:
                job_id = str(row["id"])
                self.update_job(
                    job_id,
                    status="error",
                    stage="interrupted",
                    message="服务重启，任务已中断，请明确重试",
                    error={
                        "code": "JOB_INTERRUPTED",
                        "message": "服务重启，任务已中断，请明确重试",
                        "retryable": True,
                        "previous_job_id": job_id,
                    },
                    retry_state="ready_for_retry",
                )
                recovered.append(job_id)
            return recovered

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def _migrate(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    external_job_id TEXT,
                    retry_state TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_job_events_job_seq
                    ON job_events(job_id, seq);
                CREATE TABLE IF NOT EXISTS job_idempotency (
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (owner_id, project_id, operation, idempotency_key)
                );
                """
            )

    def _get_job_locked(self, job_id: str) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "stage": row["stage"],
            "progress": int(row["progress"]),
            "message": row["message"],
            "metadata": json.loads(row["metadata_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "external_job_id": row["external_job_id"],
            "retry_state": row["retry_state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _append_event_locked(self, job: dict[str, Any]) -> None:
        self._connection.execute(
            "INSERT INTO job_events (job_id, snapshot_json, created_at) VALUES (?, ?, ?)",
            (job["id"], self._json(job), _now()),
        )

    @staticmethod
    def _json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _json_or_none(cls, payload: Any) -> str | None:
        return None if payload is None else cls._json(payload)
