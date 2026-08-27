"""Filesystem-backed project persistence for the local DDS app."""

from __future__ import annotations

import json
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from decision_field_models import PROJECT_STATES, new_project, now_iso, transition


SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")


class RevisionConflictError(RuntimeError):
    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(f"project revision conflict: expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual


class IdempotencyConflictError(RuntimeError):
    pass


class ProjectContextMismatchError(RuntimeError):
    pass


class ProjectStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(
        self,
        location: dict[str, Any],
        *,
        project_id: str | None = None,
        owner_id: str = "local",
    ) -> dict[str, Any]:
        project = new_project(location, project_id=project_id, owner_id=owner_id)
        path = self._session_path(project["id"])
        with self._lock:
            if path.exists():
                raise ValueError("project already exists")
            self._write_json(path, project)
        return deepcopy(project)

    def create_or_replay_contract_project(
        self,
        project: dict[str, Any],
        *,
        workflow_id: str,
        context_fingerprint: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically bind one GarchOS project/workflow/context to one DDS project."""

        project_id = str(project.get("id") or "")
        owner_id = str(project.get("owner_id") or "")
        path = self._session_path(project_id)
        with self._lock:
            if path.exists():
                current = json.loads(path.read_text(encoding="utf-8"))
                if str(current.get("owner_id") or "") != owner_id:
                    raise KeyError(project_id)
                binding = current.get("contract_binding") or {}
                previous = (binding.get("idempotency") or {}).get(idempotency_key)
                if previous is not None and previous != request_fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key was used for another project bootstrap"
                    )
                if (
                    binding.get("workflow_id") != workflow_id
                    or binding.get("context_fingerprint") != context_fingerprint
                ):
                    raise ProjectContextMismatchError(
                        "DDS project is already bound to another Workflow or ProjectContext"
                    )
                return deepcopy(current), True

            prepared = deepcopy(project)
            prepared["contract_binding"] = {
                "workflow_id": workflow_id,
                "context_fingerprint": context_fingerprint,
                "idempotency": {idempotency_key: request_fingerprint},
            }
            self._write_json(path, prepared)
            return deepcopy(prepared), False

    def get(self, project_id: str) -> dict[str, Any]:
        path = self._session_path(project_id)
        with self._lock:
            if not path.exists():
                raise KeyError(project_id)
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, project: dict[str, Any]) -> dict[str, Any]:
        project_id = str(project.get("id") or "")
        if project.get("state") not in PROJECT_STATES:
            raise ValueError("project has invalid state")
        updated = deepcopy(project)
        updated["updated_at"] = now_iso()
        with self._lock:
            self._write_json(self._session_path(project_id), updated)
        return deepcopy(updated)

    def update(
        self,
        project_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            current = self.get(project_id)
            return self.save(updater(current))

    def compare_and_update(
        self,
        project_id: str,
        *,
        expected_revision: int,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            current = self.get(project_id)
            actual = int(current.get("revision") or 1)
            if actual != int(expected_revision):
                raise RevisionConflictError(expected=int(expected_revision), actual=actual)
            updated = updater(deepcopy(current))
            updated["revision"] = actual + 1
            return self.save(updated)

    def apply_idempotent_update(
        self,
        project_id: str,
        *,
        key: str,
        fingerprint: str,
        expected_revision: int,
        updater: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            current = self.get(project_id)
            records = current.get("idempotency_records") or {}
            previous = records.get(key)
            if previous:
                if previous.get("fingerprint") != fingerprint:
                    raise IdempotencyConflictError("idempotency key was used for another request")
                return deepcopy(current), True
            actual = int(current.get("revision") or 1)
            if actual != int(expected_revision):
                raise RevisionConflictError(expected=int(expected_revision), actual=actual)
            updated = updater(deepcopy(current))
            updated.setdefault("idempotency_records", {})[key] = {
                "fingerprint": fingerprint,
                "applied_revision": actual + 1,
                "created_at": now_iso(),
            }
            updated["revision"] = actual + 1
            return self.save(updated), False

    def set_report(
        self,
        project_id: str,
        report: dict[str, Any],
        *,
        live_url: str,
        export_url: str | None,
        json_url: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            project = self.get(project_id)
            report_path = self._project_dir(project_id) / "report.json"
            self._write_json(report_path, report)
            summary = str((report.get("decision") or {}).get("summary") or "")
            revision = {
                "revision": len(project.get("decision_revisions") or []) + 1,
                "summary": summary,
                "created_at": now_iso(),
                "report_path": "report.json",
            }
            project.setdefault("decision_revisions", []).append(revision)
            project["report"] = {
                "live_url": live_url,
                "export_url": export_url,
                "json_url": json_url,
                "revision": revision["revision"],
                "summary": summary,
                "ready_at": revision["created_at"],
            }
            transition(project, "decision_ready")
            return self.save(project)

    def get_report(self, project_id: str) -> dict[str, Any]:
        path = self._project_dir(project_id) / "report.json"
        with self._lock:
            if not path.exists():
                raise KeyError(project_id)
            return json.loads(path.read_text(encoding="utf-8"))

    def recover_interrupted_analyses(self) -> list[str]:
        """Turn analyses left in-flight by a previous process into retryable state."""

        recovered_ids: list[str] = []
        with self._lock:
            for session_path in sorted(self.root.glob("*/session.json")):
                project = json.loads(session_path.read_text(encoding="utf-8"))
                if project.get("state") != "analyzing":
                    continue
                previous_job_id = project.get("analysis_job_id")
                project["analysis_job_id"] = None
                project["last_error"] = {
                    "code": "ANALYSIS_INTERRUPTED",
                    "message": "服务重启，分析任务已中断，请重新发起",
                    "retryable": True,
                    "previous_job_id": previous_job_id,
                }
                self.save(transition(project, "error_recoverable"))
                recovered_ids.append(str(project["id"]))
        return recovered_ids

    def _project_dir(self, project_id: str) -> Path:
        if not SAFE_ID.fullmatch(str(project_id or "")):
            raise ValueError("invalid project id")
        path = self.root / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _session_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "session.json"

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temp_path.replace(path)
