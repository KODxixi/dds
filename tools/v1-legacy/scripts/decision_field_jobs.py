"""Small observable job runner for the local DDS Flask process."""

from __future__ import annotations

import threading
import time
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

from decision_field_job_store import (
    DecisionFieldJobStore,
    JobIdempotencyConflictError,
)


TERMINAL_STATES = {"done", "error"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class JobQueueFullError(RuntimeError):
    """Raised when accepting another asynchronous job would exceed capacity."""

    code = "JOB_QUEUE_FULL"
    retryable = True

    def __init__(self, *, max_workers: int, max_queue_size: int) -> None:
        super().__init__("任务队列已满，请稍后重试")
        self.details = {
            "max_workers": max_workers,
            "max_queue_size": max_queue_size,
        }


class JobManager:
    def __init__(
        self,
        *,
        synchronous: bool = False,
        max_workers: int = 2,
        max_queue_size: int = 8,
        max_retained_terminal_jobs: int = 200,
        terminal_ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
        job_store: DecisionFieldJobStore | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        if max_queue_size < 0:
            raise ValueError("max_queue_size cannot be negative")
        if max_retained_terminal_jobs < 1:
            raise ValueError("max_retained_terminal_jobs must be at least 1")
        if terminal_ttl_seconds <= 0:
            raise ValueError("terminal_ttl_seconds must be positive")
        self.synchronous = synchronous
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.max_retained_terminal_jobs = max_retained_terminal_jobs
        self.terminal_ttl_seconds = float(terminal_ttl_seconds)
        self._clock = clock
        self._store = job_store or DecisionFieldJobStore()
        self._store.recover_interrupted_jobs()
        self._terminal_order: dict[str, int] = {}
        self._terminal_since: dict[str, float] = {}
        self._next_terminal_order = 0
        self._lock = threading.RLock()
        self._capacity = (
            None
            if synchronous
            else threading.BoundedSemaphore(max_workers + max_queue_size)
        )
        self._executor = None if synchronous else ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="dds-decision-field",
        )

    def start(
        self,
        kind: str,
        worker: Callable[[Callable[[str, int, str], None]], dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        normalized_metadata = deepcopy(metadata or {})
        if idempotency_key and not request_fingerprint:
            request_fingerprint = hashlib.sha256(
                json.dumps(
                    {"kind": kind, "metadata": normalized_metadata},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        try:
            job, replayed = self._store.create_job(
                kind=kind,
                metadata=normalized_metadata,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
        except Exception:
            raise
        job_id = job["id"]
        if replayed:
            replay = deepcopy(job)
            replay["idempotent_replay"] = True
            return replay

        capacity_acquired = False
        if not self.synchronous:
            assert self._capacity is not None
            capacity_acquired = self._capacity.acquire(blocking=False)
            if not capacity_acquired:
                self._store.delete_job(job_id)
                raise JobQueueFullError(
                    max_workers=self.max_workers,
                    max_queue_size=self.max_queue_size,
                )
        if self.synchronous:
            self._run(job_id, worker)
        else:
            assert self._executor is not None
            try:
                self._executor.submit(self._run_and_release, job_id, worker)
            except Exception:
                self._store.delete_job(job_id)
                if capacity_acquired:
                    assert self._capacity is not None
                    self._capacity.release()
                raise
        accepted = self.get(job_id) or job
        accepted["idempotent_replay"] = False
        return accepted

    def shutdown(self, *, wait: bool = True) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=wait)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._prune_terminal_locked()
            return self._store.get_job(job_id)

    def stream(
        self,
        job_id: str,
        *,
        interval: float = 0.25,
        after_seq: int = 0,
    ) -> Iterator[dict[str, Any]]:
        cursor = int(after_seq)
        while True:
            job = self.get(job_id)
            if not job:
                return
            events = self._store.list_events(job_id, after_seq=cursor)
            for event in events:
                cursor = int(event["seq"])
                yield event
            if job["status"] in TERMINAL_STATES and not self._store.list_events(
                job_id, after_seq=cursor
            ):
                return
            time.sleep(interval)

    def _run(
        self,
        job_id: str,
        worker: Callable[[Callable[[str, int, str], None]], dict[str, Any]],
    ) -> None:
        self._update(
            job_id,
            status="running",
            stage="starting",
            progress=2,
            message="任务开始执行",
        )

        def progress(stage: str, value: int, message: str) -> None:
            self._update(
                job_id,
                status="running",
                stage=str(stage),
                progress=max(0, min(99, int(value))),
                message=str(message),
            )

        try:
            result = worker(progress)
            self._update(
                job_id,
                status="done",
                stage="done",
                progress=100,
                message="任务已完成",
                result=deepcopy(result),
                error=None,
            )
        except Exception as exc:
            public_message = "任务执行失败，请检查服务状态后重试"
            self._update(
                job_id,
                status="error",
                stage="error",
                message=public_message,
                error={"type": type(exc).__name__, "message": public_message},
            )

    def _run_and_release(
        self,
        job_id: str,
        worker: Callable[[Callable[[str, int, str], None]], dict[str, Any]],
    ) -> None:
        try:
            self._run(job_id, worker)
        finally:
            assert self._capacity is not None
            self._capacity.release()

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._store.update_job(job_id, **changes)
            if job["status"] in TERMINAL_STATES and job_id not in self._terminal_order:
                self._next_terminal_order += 1
                self._terminal_order[job_id] = self._next_terminal_order
                self._terminal_since[job_id] = self._clock()
            self._prune_terminal_locked()

    def _prune_terminal_locked(self) -> None:
        expired = [
            job_id
            for job_id, finished_at in self._terminal_since.items()
            if self._clock() - finished_at >= self.terminal_ttl_seconds
        ]
        for job_id in expired:
            self._store.delete_job(job_id)
            self._terminal_order.pop(job_id, None)
            self._terminal_since.pop(job_id, None)

        excess = len(self._terminal_order) - self.max_retained_terminal_jobs
        if excess <= 0:
            return
        oldest = sorted(self._terminal_order, key=self._terminal_order.__getitem__)[:excess]
        for job_id in oldest:
            self._store.delete_job(job_id)
            self._terminal_order.pop(job_id, None)
            self._terminal_since.pop(job_id, None)
