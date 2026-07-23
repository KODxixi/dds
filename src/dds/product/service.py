"""Persistent product workflow for proactive research jobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Iterable
from uuid import uuid4

from dds.agents.base import AgentTask
from dds.agents.requirement import RequirementAgent
from dds.agents.research_planner import ResearchPlannerAgent
from dds.data.workbook_integrity import WorkbookIntegrityScanner
from dds.research import ResearchQuery, ResearchSource, SourceUnavailableError
from dds.research.qualification import qualify_candidates


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ProductSettings:
    root: Path
    max_upload_bytes: int = 25 * 1024 * 1024

    @property
    def jobs_root(self) -> Path:
        return self.root / "jobs"

    @property
    def materials_root(self) -> Path:
        return self.root / "materials"


class ResearchJobService:
    """Atomic local persistence plus deterministic source orchestration."""

    _lock = RLock()

    def __init__(self, settings: ProductSettings, sources: Iterable[ResearchSource]) -> None:
        self.settings = settings
        self.sources = tuple(sources)

    def _job_path(self, job_id: str) -> Path:
        if not job_id or any(token in job_id for token in ("/", "\\", "..")):
            raise ValueError("invalid job_id")
        return self.settings.jobs_root / f"{job_id}.json"

    def _write(self, payload: dict[str, Any]) -> None:
        self.settings.jobs_root.mkdir(parents=True, exist_ok=True)
        target = self._job_path(str(payload["job_id"]))
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)

    def get(self, job_id: str) -> dict[str, Any]:
        path = self._job_path(job_id)
        if not path.is_file():
            raise KeyError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        if not self.settings.jobs_root.exists():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.settings.jobs_root.glob("*.json"))
        ]

    def create(self, project_context: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid4().hex
        payload = {
            "job_id": job_id,
            "status": "pending",
            "project_context": dict(project_context),
            "requirements": [],
            "candidates": [],
            "materials": [],
            "logs": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._log(payload, "job_created", "研究任务已创建")
        self._write(payload)
        return payload

    def upload(self, job_id: str, filename: str, content: bytes, content_type: str) -> dict[str, Any]:
        if Path(filename).name != filename or filename in {"", ".", ".."}:
            raise ValueError("invalid filename")
        if len(content) > self.settings.max_upload_bytes:
            raise OverflowError("upload exceeds configured size limit")
        job = self.get(job_id)
        digest = sha256(content).hexdigest()
        destination = self.settings.materials_root / job_id / f"{digest[:16]}-{filename}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        record = {
            "filename": filename,
            "content_type": content_type,
            "bytes": len(content),
            "sha256": digest,
            "stored_name": destination.name,
        }
        if destination.suffix.lower() == ".xlsx":
            record["integrity"] = WorkbookIntegrityScanner().scan(destination).to_dict()
        job["materials"].append(record)
        self._log(job, "material_uploaded", f"已接收资料：{filename}")
        self._write(job)
        return record

    def run(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            blocked_materials = [
                material
                for material in job.get("materials", [])
                if material.get("integrity", {}).get("status") == "blocked"
            ]
            if blocked_materials:
                job["status"] = "blocked"
                job["input_gate"] = {
                    "status": "blocked",
                    "blocked_materials": blocked_materials,
                }
                self._log(
                    job,
                    "material_integrity_blocked",
                    f"{len(blocked_materials)} 个 XLSX 文件未通过完整性检查",
                )
                self._write(job)
                return self.summary(job)
            job["status"] = "running"
            context = job["project_context"]
            requirement_parameters: dict[str, Any] = {
                "project_context": context,
            }
            if context.get("requested_level") is not None:
                requirement_parameters.update(
                    {
                        "requested_level": context["requested_level"],
                        "input_profile": context,
                    }
                )
            requirement_result = asyncio.run(
                RequirementAgent().execute(
                    AgentTask(
                        task_id=job_id,
                        task_type="requirements",
                        parameters=requirement_parameters,
                    )
                )
            )
            if not requirement_result.success:
                job["status"] = "blocked"
                job["errors"] = requirement_result.errors
                if requirement_result.data.get("analysis_profile"):
                    job["analysis_profile"] = requirement_result.data["analysis_profile"]
                    job["input_gate"] = {
                        "status": "blocked",
                        "reasons": requirement_result.data["analysis_profile"].get(
                            "classification_blockers", []
                        ),
                    }
                self._write(job)
                return self.summary(job)
            if requirement_result.data.get("analysis_profile"):
                job["analysis_profile"] = requirement_result.data["analysis_profile"]
                job["decision_scope"] = requirement_result.data["decision_scope"]
                self._log(
                    job,
                    "input_profile_classified",
                    (
                        "Input classified as "
                        f"{job['analysis_profile']['display_name']}; "
                        f"decision scope {job['decision_scope']}"
                    ),
                )
            requirements = requirement_result.data["data_requirements"]
            plan = ResearchPlannerAgent().plan(requirements, project_context=context)
            job["requirements"] = requirements
            job["research_plan"] = plan
            self._log(job, "requirements_planned", f"已拆解 {len(requirements)} 项数据需求")

            metric_ids = tuple(item["metric_id"] for item in plan["source_plan"] if item["status"] == "planned")
            geography = "/".join(
                item for item in (str(context.get("city") or ""), str(context.get("district") or "")) if item
            )
            query_text = " ".join(
                item for item in (geography, str(context.get("project_type") or ""), str(context.get("decision_question") or "")) if item
            )
            for source in self.sources:
                availability = source.availability()
                if not availability.get("available"):
                    self._log(job, "source_unavailable", f"数据源不可用：{source.source_id}", availability)
                    continue
                self._log(job, "source_started", f"开始检索：{source.source_id}")
                try:
                    result = source.search(
                        ResearchQuery(
                            query=query_text,
                            metric_ids=metric_ids,
                            geography=geography,
                            max_results=20,
                        )
                    )
                except SourceUnavailableError as exc:
                    self._log(job, "source_unavailable", str(exc), availability)
                    continue
                job["candidates"].extend(item.to_dict() for item in result.candidates)
                self._log(
                    job,
                    "source_completed",
                    f"{source.source_id} 返回 {len(result.candidates)} 条候选证据",
                )
            job["candidates"], job["readiness"] = qualify_candidates(
                job["candidates"], plan["source_plan"]
            )
            job["status"] = "research_completed"
            qualified_count = sum(
                item["qualification_status"] == "qualified"
                for item in job["candidates"]
            )
            self._log(
                job,
                "candidates_qualified",
                f"候选证据资格判定完成：{qualified_count}/{len(job['candidates'])} 合格",
            )
            self._log(job, "research_completed", "主动研究完成，已生成证据覆盖状态")
            self._write(job)
            return self.summary(job)

    def summary(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "project_context": job["project_context"],
            "material_count": len(job.get("materials", [])),
            "requirement_count": len(job.get("requirements", [])),
            "candidate_count": len(job.get("candidates", [])),
            "qualified_candidate_count": sum(
                item.get("qualification_status") == "qualified"
                for item in job.get("candidates", [])
            ),
            "input_gate_status": job.get("input_gate", {}).get("status", "ready"),
            "evidence_status": job.get("readiness", {}).get(
                "evidence_status", "not_assessed"
            ),
            "decision_ready": job.get("readiness", {}).get("decision_ready", False),
            "analysis_profile": job.get("analysis_profile"),
            "decision_scope": job.get("decision_scope"),
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }

    def source_status(self) -> list[dict[str, object]]:
        return [source.availability() for source in self.sources]

    def _log(
        self,
        job: dict[str, Any],
        event: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        job["logs"].append(
            {"timestamp": _now(), "event": event, "message": message, "details": details or {}}
        )
        job["updated_at"] = _now()
