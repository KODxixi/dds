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

from dds.analysis_profile import resolve_intervention_profile
from dds.agents.base import AgentTask
from dds.agents.requirement import RequirementAgent
from dds.agents.research_planner import ResearchPlannerAgent
from dds.data.workbook_integrity import WorkbookIntegrityScanner
from dds.research import ResearchQuery, ResearchSource, SourceUnavailableError
from dds.research.qualification import qualify_candidates


_FUTURE_EVENT_QUERY_TERMS = {
    "major_employer_or_headquarters": "重大雇主 总部 园区 入驻 员工",
    "industrial_cluster": "产业集群 招商 企业 入驻",
    "transport_infrastructure": "轨道交通 地铁 通勤 开通",
    "public_service_investment": "公共服务 教育 医疗 商业 投用",
    "regulatory_or_supply_change": "规划 供地 住房供应 政策",
}
_FUTURE_HORIZON_QUERY_TERMS = {
    "current_operation": "当前运营 已投用",
    "0_to_3_years": "未来三年 在建",
    "3_to_5_years": "未来五年 规划",
}


class InterventionBriefFrozenError(RuntimeError):
    """Raised when a confirmed intervention task is asked to change."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def intervention_brief_hash(
    job_id: str,
    intervention_brief: dict[str, Any],
) -> str:
    frozen_payload = json.dumps(
        {
            "job_id": job_id,
            "intervention_brief": intervention_brief,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(frozen_payload).hexdigest()


def _metric_query_text(
    context: dict[str, Any],
    source_plan_item: dict[str, Any],
    geography: str,
) -> str:
    parts = [
        geography,
        str(context.get("project_name") or ""),
        str(context.get("address") or ""),
        str(context.get("project_type") or ""),
        str(source_plan_item.get("field_name") or ""),
        str(source_plan_item.get("description") or ""),
        str(context.get("decision_question") or ""),
    ]
    for field_name in ("project_entities", "key_entities", "nearby_landmarks"):
        raw_value = context.get(field_name) or ()
        values = raw_value if isinstance(raw_value, (list, tuple, set)) else (raw_value,)
        parts.extend(str(item) for item in values if str(item).strip())

    if source_plan_item.get("metric_id") == "SC2.future_demand_event_scan":
        event_scan = dict(
            source_plan_item.get("metadata", {}).get("future_event_scan") or {}
        )
        parts.append("未来需求事件 客群迁移 就业人口 居住转化")
        parts.extend(
            _FUTURE_EVENT_QUERY_TERMS.get(str(item), str(item))
            for item in event_scan.get("event_classes", ())
        )
        parts.extend(
            _FUTURE_HORIZON_QUERY_TERMS.get(str(item), str(item))
            for item in event_scan.get("time_horizons", ())
        )

    return " ".join(dict.fromkeys(item.strip() for item in parts if item.strip()))


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
        profile = resolve_intervention_profile(project_context)
        payload = {
            "job_id": job_id,
            "status": "pending",
            "project_context": dict(project_context),
            "analysis_profile": profile,
            "intervention_brief": None,
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

    def confirm_intervention(
        self,
        job_id: str,
        brief: dict[str, Any],
    ) -> dict[str, Any]:
        job = self.get(job_id)
        if job.get("intervention_brief") is not None:
            raise InterventionBriefFrozenError(
                "intervention brief is already confirmed and immutable; "
                "create a new intervention task for a different mode or scope"
            )
        selected_mode = int(brief.get("selected_mode") or 0)
        context = dict(job["project_context"])
        context["selected_mode"] = selected_mode
        context["mode_confirmed"] = True
        context["materials"] = list(job.get("materials", []))
        profile = resolve_intervention_profile(
            context,
            selected_mode=selected_mode,
            confirmed=True,
        )
        frozen_brief = {
            "schema_version": "dds.intervention-brief/1.0",
            "selected_mode": selected_mode,
            "user_goal": str(brief.get("user_goal") or "").strip(),
            "decision_audience": str(
                brief.get("decision_audience") or ""
            ).strip(),
            "decision_questions": [
                str(item).strip()
                for item in brief.get("decision_questions") or []
                if str(item).strip()
            ],
            "available_materials": [
                item["filename"] for item in job.get("materials", [])
            ],
            "priorities": dict(brief.get("priorities") or {}),
            "prohibited_conclusions": [
                str(item).strip()
                for item in brief.get("prohibited_conclusions") or []
                if str(item).strip()
            ],
            "confirmed_at": _now(),
        }
        if not frozen_brief["user_goal"]:
            raise ValueError("user_goal is required")
        job["intervention_brief_hash"] = intervention_brief_hash(
            job_id,
            frozen_brief,
        )
        job["project_context"] = context
        job["analysis_profile"] = profile
        job["intervention_brief"] = frozen_brief
        job["decision_scope"] = profile["decision_scope"]
        job["status"] = (
            "pending" if profile["eligible"] else "blocked"
        )
        job["input_gate"] = {
            "status": "ready" if profile["eligible"] else "blocked",
            "reasons": profile["missing_inputs"],
        }
        self._log(
            job,
            "intervention_confirmed",
            f"用户已确认 {profile['display_name']}",
        )
        self._write(job)
        return self.summary(job)

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
        context = dict(job["project_context"])
        context["materials"] = list(job["materials"])
        current_profile = job.get("analysis_profile") or {}
        job["project_context"] = context
        job["analysis_profile"] = resolve_intervention_profile(
            context,
            selected_mode=current_profile.get("selected_mode"),
            confirmed=current_profile.get("mode_confirmed", False),
        )
        self._log(job, "material_uploaded", f"已接收资料：{filename}")
        self._write(job)
        return record

    def run(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            profile = job.get("analysis_profile") or {}
            if profile.get("mode_status") != "confirmed":
                job["status"] = "blocked"
                job["input_gate"] = {
                    "status": "blocked",
                    "reasons": (
                        profile.get("missing_inputs")
                        or ["intervention_confirmation_required"]
                    ),
                }
                self._log(
                    job,
                    "intervention_not_confirmed",
                    "必须先由用户确认 DDS 介入方式",
                )
                self._write(job)
                return self.summary(job)
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
                "selected_mode": profile["selected_mode"],
                "mode_confirmed": True,
                "input_profile": context,
            }
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
                            "missing_inputs", []
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
                        "Intervention confirmed as "
                        f"{job['analysis_profile']['display_name']}; "
                        f"decision scope {job['decision_scope']}"
                    ),
                )
            requirements = requirement_result.data["data_requirements"]
            plan = ResearchPlannerAgent().plan(requirements, project_context=context)
            job["requirements"] = requirements
            job["research_plan"] = plan
            self._log(job, "requirements_planned", f"已拆解 {len(requirements)} 项数据需求")

            planned_items = [
                item for item in plan["source_plan"] if item["status"] == "planned"
            ]
            default_geography = "/".join(
                item
                for item in (
                    str(context.get("city") or ""),
                    str(context.get("district") or ""),
                )
                if item
            )
            for source in self.sources:
                availability = source.availability()
                if not availability.get("available"):
                    self._log(job, "source_unavailable", f"数据源不可用：{source.source_id}", availability)
                    continue
                self._log(job, "source_started", f"开始检索：{source.source_id}")
                returned_count = 0
                failures: list[str] = []
                for plan_item in planned_items:
                    metric_id = str(plan_item["metric_id"])
                    geography = str(
                        plan_item.get("geography") or default_geography
                    )
                    try:
                        result = source.search(
                            ResearchQuery(
                                query=_metric_query_text(
                                    context,
                                    plan_item,
                                    geography,
                                ),
                                metric_ids=(metric_id,),
                                geography=geography,
                                max_results=max(
                                    1,
                                    min(
                                        10,
                                        int(plan_item.get("missing_count") or 1)
                                        * 3,
                                    ),
                                ),
                            )
                        )
                    except SourceUnavailableError as exc:
                        failures.append(f"{metric_id}: {exc}")
                        continue
                    for candidate in result.candidates:
                        candidate_payload = candidate.to_dict()
                        candidate_payload["metric_ids"] = [metric_id]
                        job["candidates"].append(candidate_payload)
                        returned_count += 1
                if failures and not returned_count:
                    self._log(
                        job,
                        "source_unavailable",
                        f"{source.source_id} 未完成分指标检索",
                        {**availability, "errors": failures},
                    )
                    continue
                if failures:
                    self._log(
                        job,
                        "source_partial",
                        f"{source.source_id} 有 {len(failures)} 项指标检索失败",
                        {"errors": failures},
                    )
                self._log(
                    job,
                    "source_completed",
                    f"{source.source_id} 返回 {returned_count} 条分指标候选证据",
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
            "intervention_brief_hash": job.get("intervention_brief_hash"),
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
