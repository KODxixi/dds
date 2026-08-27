"""Flask Blueprint for DDS Decision Field project workflows."""

from __future__ import annotations

import json
import hashlib
import re
import threading
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from flask import Blueprint, Response, g, jsonify, request, send_from_directory, stream_with_context

from decision_field_brief import BriefCompiler
from decision_field_artifact_reader import (
    ArtifactAccessError,
    ArtifactIntegrityError,
    ArtifactPolicyError,
)
from decision_field_contracts import build_analysis_context, build_visual_brief
from decision_field_jobs import JobIdempotencyConflictError, JobManager, JobQueueFullError
from decision_field_job_store import DecisionFieldJobStore
from decision_field_report_revisions import (
    DecisionFieldReportRevisionStore,
    ReportIdempotencyConflictError,
    ReportRevisionConflictError,
)
from decision_field_models import new_project, transition
from decision_field_store import (
    IdempotencyConflictError,
    ProjectContextMismatchError,
    ProjectStore,
    RevisionConflictError,
)
from garchos_openapi_contract import (
    ContractConfigurationError,
    ContractValidationError,
    OpenAPIContract,
)
from location_gateway import LocationGateway, LocationLookupError
from map_scene_manifest import compile_scene_manifest


class AnalysisContextLockedError(RuntimeError):
    pass


def create_decision_field_blueprint(
    *,
    project_root: str | Path,
    allowed_cities: Iterable[str] | Callable[[], Iterable[str]],
    report_runner: Callable[[dict[str, Any], Callable[[str, int, str], None]], dict[str, Any]],
    amap_key: str,
    cesium_ion_token: str = "",
    imagery_url: str = "",
    synchronous_jobs: bool = False,
    job_manager: JobManager | None = None,
    report_page_dir: str | Path | None = None,
    report_revision_store: DecisionFieldReportRevisionStore | None = None,
    project_report_registry: Mapping[str, str | Path] | None = None,
    project_report_runner: Callable[..., dict[str, Any]] | None = None,
) -> Blueprint:
    blueprint = Blueprint("decision_field", __name__)
    store = ProjectStore(project_root)
    store.recover_interrupted_analyses()
    brief_compiler = BriefCompiler()
    jobs = job_manager if job_manager is not None else JobManager(
        synchronous=synchronous_jobs,
        job_store=DecisionFieldJobStore(Path(project_root) / "_jobs.sqlite3"),
    )
    location_gateway = LocationGateway(amap_key=amap_key)
    report_page = Path(report_page_dir) if report_page_dir else None
    revisions = report_revision_store
    stable_id = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{3,95}$")
    idempotency_key_pattern = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
    project_report_ref_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
    registered_project_reports: dict[str, Path] = {}
    for raw_ref, raw_path in (project_report_registry or {}).items():
        ref = str(raw_ref).strip()
        if not project_report_ref_pattern.fullmatch(ref) or ref in {".", ".."}:
            raise ValueError(f"invalid registered project_ref: {raw_ref!r}")
        registered_project_reports[ref] = Path(raw_path).expanduser().resolve()
    project_report_locks = {
        ref: threading.Lock() for ref in registered_project_reports
    }

    def owner_id() -> str:
        return str(getattr(g, "dds_user_id", "local") or "local").strip()

    def owned_project(project_id: str) -> dict[str, Any]:
        project = store.get(project_id)
        project_owner = str(project.get("owner_id") or "").strip()
        current_owner = owner_id()
        legacy_local_project = not project_owner and current_owner == "local"
        if project_owner != current_owner and not legacy_local_project:
            raise KeyError(project_id)
        return project

    def owned_job(job_id: str) -> dict[str, Any] | None:
        job = jobs.get(job_id)
        if not job or str((job.get("metadata") or {}).get("owner_id") or "") != owner_id():
            return None
        return job

    def cities() -> list[str]:
        values = allowed_cities() if callable(allowed_cities) else allowed_cities
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            city = str(value).strip()
            if city and city not in seen:
                seen.add(city)
                ordered.append(city)
        return ordered

    def workflow_contract() -> OpenAPIContract:
        return OpenAPIContract.from_environment()

    def request_fingerprint(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def run_registered_project_report(
        *,
        project_dir: Path,
        as_of: str,
        report_profile: str,
        export_profiles: tuple[str, ...],
        research_mode: str,
        requested_owner_id: str,
        progress: Callable[[str, int, str], None],
    ) -> dict[str, Any]:
        kwargs = {
            "project_dir": project_dir,
            "as_of": as_of,
            "owner_id": requested_owner_id,
            "research": research_mode,
            "profile": report_profile,
            "exports": export_profiles,
        }
        progress("manifest", 10, "正在读取已登记项目资料")
        if project_report_runner is not None:
            result = project_report_runner(**kwargs, progress=progress)
        else:
            from build_project_report import build_project_report

            result = build_project_report(**kwargs)
        progress("delivery", 96, "正在登记报告交付物")

        paths = result.get("paths") if isinstance(result, Mapping) else {}
        paths = paths if isinstance(paths, Mapping) else {}
        artifact_names = sorted(
            {
                Path(str(paths[key])).name
                for key in ("interactive", "portable_single_file")
                if paths.get(key)
            }
        )
        return {
            "status": str(result.get("status") or "ok"),
            "package_status": str(result.get("package_status") or "unknown"),
            "package_hash": str(result.get("package_hash") or ""),
            "report_document_hash": str(result.get("report_document_hash") or ""),
            "profile": str(result.get("profile") or report_profile),
            "exports": [str(value) for value in result.get("exports") or export_profiles],
            "artifacts": artifact_names,
        }

    def contract_brief(project_context: dict[str, Any], location: dict[str, Any], project_id: str):
        prepared = new_project(location, project_id=project_id, owner_id=owner_id())
        prepared = brief_compiler.answer(prepared, "primary_goal", "land_boundary")
        prepared = brief_compiler.answer(prepared, "project_stage", "initial_review")

        site = project_context.get("site") or {}
        far = site.get("plot_ratio")
        expected_price = None
        hard_constraints: list[str] = []
        for item in project_context.get("constraints") or []:
            code = str(item.get("code") or "")
            value = item.get("value")
            if code in {"far", "plot_ratio"} and far is None and isinstance(value, (int, float)):
                far = value
            if code in {"expected_price", "target_price"} and isinstance(value, (int, float)):
                expected_price = value
            label = str(item.get("label") or code).strip()
            if label:
                unit = str(item.get("unit") or "")
                hard_constraints.append(f"{label}: {value}{unit}" if value is not None else label)
        if hard_constraints:
            prepared = brief_compiler.answer(prepared, "hard_constraints", hard_constraints)
        if far is not None or expected_price is not None:
            prepared = brief_compiler.answer(
                prepared,
                "financial_assumptions",
                {"far": far, "expected_price": expected_price},
            )
        return brief_compiler.finalize(prepared, direct=True)

    @blueprint.get("/api/v2/bootstrap")
    def bootstrap():
        return jsonify(
            {
                "status": "ok",
                "version": "decision-field-v1",
                "cities": cities(),
                "scene": {
                    "engine": "cesium",
                    "cesium_ion_token": cesium_ion_token,
                    "imagery_mode": "osm-development" if imagery_url else "local-world",
                    "imageryUrl": imagery_url,
                    "imageryMaximumLevel": 19,
                    "imageryCredit": "© OpenStreetMap contributors" if imagery_url else "",
                    "quality": "balanced",
                    "default_view": {"lng": 104.2, "lat": 35.8, "height": 14500000},
                },
                "capabilities": {
                    "address_search": bool(amap_key and amap_key != "YOUR_AMAP_KEY_HERE"),
                    "coordinate_input": True,
                    "map_pick": True,
                    "sse": True,
                    "portable_export": True,
                },
            }
        )

    @blueprint.get("/api/v2/location/suggest")
    def suggest_location():
        query = request.args.get("q", "")
        city = request.args.get("city", "")
        return jsonify({"status": "ok", "suggestions": location_gateway.suggest(query, city)})

    @blueprint.post("/api/v2/location/resolve")
    def resolve_location():
        payload = request.get_json(silent=True) or {}
        try:
            location = location_gateway.resolve(payload)
        except (LocationLookupError, TypeError, ValueError) as exc:
            return _error("LOCATION_NOT_FOUND", str(exc), 422, retryable=True)
        return jsonify({"status": "ok", "location": location})

    @blueprint.post("/api/v2/projects")
    def create_project():
        payload = request.get_json(silent=True) or {}
        location = payload.get("location")
        if not isinstance(location, dict):
            return _error("INVALID_LOCATION", "缺少已确认的地块位置", 400, retryable=True)
        city = str(location.get("city") or "").removesuffix("市")
        if city and city not in cities():
            return _error("CITY_DATA_UNAVAILABLE", f"城市「{city}」暂无 DDS 数据", 409, retryable=True)
        if city:
            location = dict(location)
            location["city"] = city
        contract_mode = any(
            key in payload for key in ("project_id", "workflow_id", "project_context")
        )
        if contract_mode:
            if set(payload) != {"project_id", "workflow_id", "location", "project_context"}:
                return _error(
                    "CONTRACT_VALIDATION_FAILED",
                    "contract project payload contains unsupported fields",
                    400,
                    retryable=False,
                )
            key = request.headers.get("Idempotency-Key", "").strip()
            project_id = str(payload.get("project_id") or "")
            workflow_id = str(payload.get("workflow_id") or "")
            project_context = payload.get("project_context")
            try:
                if not stable_id.fullmatch(project_id) or not stable_id.fullmatch(workflow_id):
                    raise ContractValidationError("project_id and workflow_id must be stable IDs")
                if not idempotency_key_pattern.fullmatch(key):
                    return _error(
                        "IDEMPOTENCY_KEY_REQUIRED",
                        "contract project creation requires a valid Idempotency-Key",
                        400,
                        retryable=False,
                    )
                try:
                    existing_project = store.get(project_id)
                except KeyError:
                    existing_project = None
                if existing_project is not None and str(existing_project.get("owner_id")) != owner_id():
                    return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
                workflow_contract().validate_schema("ProjectContext", project_context)
                if not project_context.get("confirmed_at"):
                    raise ContractValidationError("ProjectContext must be confirmed")
                context_owner = int(project_context["owner_user_id"])
                if owner_id().isdigit() and context_owner != int(owner_id()):
                    raise ContractValidationError("ProjectContext owner_user_id does not match")
                context_hash = request_fingerprint(project_context)
                bootstrap_fingerprint = request_fingerprint(
                    {
                        "owner_id": owner_id(),
                        "project_id": project_id,
                        "workflow_id": workflow_id,
                        "location": location,
                        "project_context": project_context,
                    }
                )
                prepared = contract_brief(project_context, location, project_id)
                project, replayed = store.create_or_replay_contract_project(
                    prepared,
                    workflow_id=workflow_id,
                    context_fingerprint=context_hash,
                    idempotency_key=key,
                    request_fingerprint=bootstrap_fingerprint,
                )
            except KeyError:
                return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
            except IdempotencyConflictError as exc:
                return _error("IDEMPOTENCY_CONFLICT", str(exc), 409, retryable=False)
            except ProjectContextMismatchError as exc:
                return _error("PROJECT_CONTEXT_MISMATCH", str(exc), 409, retryable=False)
            except (TypeError, ValueError, ContractValidationError) as exc:
                return _error("CONTRACT_VALIDATION_FAILED", str(exc), 400, retryable=False)
            except ContractConfigurationError as exc:
                return _error("CONTRACT_UNAVAILABLE", str(exc), 503, retryable=True)
            return (
                jsonify(
                    {
                        "status": "ok",
                        "project": project,
                        "next_question": None,
                        "idempotent_replay": replayed,
                    }
                ),
                200 if replayed else 201,
            )
        try:
            project = store.create(location, owner_id=owner_id())
        except ValueError as exc:
            return _error("INVALID_LOCATION", str(exc), 400, retryable=True)
        return (
            jsonify(
                {
                    "status": "ok",
                    "project": project,
                    "next_question": brief_compiler.next_question(project),
                }
            ),
            201,
        )

    @blueprint.get("/api/v2/projects/<project_id>")
    def get_project(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        return jsonify(
            {
                "status": "ok",
                "project": project,
                "next_question": brief_compiler.next_question(project)
                if project["state"] == "briefing"
                else None,
            }
        )

    @blueprint.get("/api/v2/projects/<project_id>/analysis-context")
    def get_analysis_context(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        context = project.get("analysis_context")
        if not context:
            return _error(
                "ANALYSIS_CONTEXT_REQUIRED",
                "项目尚未写入已验证的分析上下文",
                409,
                retryable=True,
            )
        return jsonify({"status": "ok", "analysis_context": context})

    @blueprint.put("/api/v2/projects/<project_id>/analysis-context")
    def put_analysis_context(project_id: str):
        try:
            owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        payload = request.get_json(silent=True) or {}
        allowed = {"expected_revision", "workflow_id", "project_context", "case_matches"}
        if set(payload) - allowed:
            return _error(
                "CONTRACT_VALIDATION_FAILED",
                "analysis-context 包含未授权字段",
                400,
                retryable=False,
            )
        try:
            expected_revision = int(payload["expected_revision"])
            workflow_id = str(payload["workflow_id"])
            context = build_analysis_context(
                project_context=payload["project_context"],
                case_matches=payload["case_matches"],
                workflow_id=workflow_id,
                contract=workflow_contract(),
            )
            context_owner = int((context.get("project_context") or {}).get("owner_user_id"))
            if owner_id().isdigit() and context_owner != int(owner_id()):
                raise ContractValidationError("ProjectContext owner_user_id does not match")

            def update_context(project):
                if project.get("state") == "analyzing":
                    raise AnalysisContextLockedError(
                        "analysis context cannot change while analysis is running"
                    )
                project["analysis_context"] = context
                project["decision_scenarios"] = []
                project["selected_scenario"] = None
                project["scenario_selections"] = []
                return project

            key = request.headers.get("Idempotency-Key", "").strip()
            if key:
                if not idempotency_key_pattern.fullmatch(key):
                    return _error(
                        "IDEMPOTENCY_KEY_REQUIRED",
                        "analysis-context requires a valid Idempotency-Key",
                        400,
                        retryable=False,
                    )
                semantic_payload = {
                    "owner_id": owner_id(),
                    "project_id": project_id,
                    "workflow_id": workflow_id,
                    "project_context": payload["project_context"],
                    "case_matches": payload["case_matches"],
                }
                project, replayed = store.apply_idempotent_update(
                    project_id,
                    key=key,
                    fingerprint=request_fingerprint(semantic_payload),
                    expected_revision=expected_revision,
                    updater=update_context,
                )
            else:
                project = store.compare_and_update(
                    project_id,
                    expected_revision=expected_revision,
                    updater=update_context,
                )
                replayed = False
        except RevisionConflictError as exc:
            return _error(
                "REVISION_CONFLICT",
                str(exc),
                409,
                retryable=True,
                details={"expected": exc.expected, "actual": exc.actual},
            )
        except IdempotencyConflictError as exc:
            return _error("IDEMPOTENCY_CONFLICT", str(exc), 409, retryable=False)
        except AnalysisContextLockedError:
            return _error(
                "PROJECT_STATE_CONFLICT",
                "分析运行期间不能修改分析上下文",
                409,
                retryable=False,
            )
        except (KeyError, TypeError, ValueError, ContractValidationError) as exc:
            return _error(
                "CONTRACT_VALIDATION_FAILED",
                str(exc),
                400,
                retryable=False,
            )
        except ContractConfigurationError as exc:
            return _error("CONTRACT_UNAVAILABLE", str(exc), 503, retryable=True)
        return jsonify(
            {
                "status": "ok",
                "project": project,
                "analysis_context": context,
                "idempotent_replay": replayed,
            }
        )

    @blueprint.post("/api/v2/projects/<project_id>/answers")
    def answer_project(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        if project["state"] not in {"briefing", "ready_for_analysis"}:
            return _error("PROJECT_STATE_CONFLICT", "当前项目状态不能修改任务书", 409, retryable=True)
        payload = request.get_json(silent=True) or {}
        try:
            project = brief_compiler.answer(
                project,
                str(payload.get("question_id") or ""),
                payload.get("value"),
                skip=bool(payload.get("skip")),
            )
            direct = bool(payload.get("direct_analysis"))
            if direct:
                project = brief_compiler.finalize(project, direct=True)
            elif brief_compiler.next_question(project) is None and brief_compiler.can_analyze(project):
                project = brief_compiler.finalize(project)
            project = store.save(project)
        except (TypeError, ValueError) as exc:
            return _error("INVALID_ANSWER", str(exc), 400, retryable=True)
        return jsonify(
            {
                "status": "ok",
                "project": project,
                "next_question": brief_compiler.next_question(project)
                if project["state"] == "briefing"
                else None,
            }
        )

    @blueprint.post("/api/v2/projects/<project_id>/analysis")
    def start_analysis(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        payload = request.get_json(silent=True) or {}
        requested_workflow_id = str(payload.get("workflow_id") or "").strip()
        contract_mode = bool(requested_workflow_id)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip() or None
        if not brief_compiler.can_analyze(project):
            return _error("BRIEF_INCOMPLETE", "请先回答研判目标和项目阶段", 409, retryable=True)
        if contract_mode:
            analysis_context = project.get("analysis_context") or {}
            if analysis_context.get("workflow_id") != requested_workflow_id:
                return _error(
                    "ANALYSIS_CONTEXT_REQUIRED",
                    "请先写入与 Workflow 匹配的已验证分析上下文",
                    409,
                    retryable=True,
                )
        else:
            analysis_context = project.get("analysis_context") or {}
        context_fingerprint = request_fingerprint(analysis_context)
        analysis_fingerprint = request_fingerprint(
            {
                "owner_id": owner_id(),
                "project_id": project_id,
                "workflow_id": requested_workflow_id or None,
                "context_fingerprint": context_fingerprint,
                "operation": "project_analysis",
            }
        ) if idempotency_key else None
        if project["state"] == "briefing":
            project = brief_compiler.finalize(project, direct=True)
        binding = project.get("analysis_request") or {}
        bound_replay = bool(
            idempotency_key
            and binding.get("idempotency_key") == idempotency_key
            and binding.get("request_fingerprint") == analysis_fingerprint
            and binding.get("context_fingerprint") == context_fingerprint
        )
        if not bound_replay:
            if project["state"] not in {"ready_for_analysis", "decision_ready", "error_recoverable"}:
                return _error(
                    "PROJECT_STATE_CONFLICT",
                    "当前项目状态不能启动分析",
                    409,
                    retryable=False,
                )
            previous_project_state = project["state"]

            def begin_analysis(current):
                if current.get("state") not in {
                    "ready_for_analysis", "decision_ready", "error_recoverable"
                }:
                    raise AnalysisContextLockedError("project state changed before analysis start")
                current["analysis_request"] = {
                    "idempotency_key": idempotency_key,
                    "request_fingerprint": analysis_fingerprint,
                    "context_fingerprint": context_fingerprint,
                    "start_revision": int(current.get("revision") or 1),
                    "previous_state": previous_project_state,
                }
                return transition(current, "analyzing")

            try:
                project = store.compare_and_update(
                    project_id,
                    expected_revision=int(project.get("revision") or 1),
                    updater=begin_analysis,
                )
            except (RevisionConflictError, AnalysisContextLockedError):
                project = store.get(project_id)
                binding = project.get("analysis_request") or {}
                bound_replay = bool(
                    idempotency_key
                    and binding.get("idempotency_key") == idempotency_key
                    and binding.get("request_fingerprint") == analysis_fingerprint
                    and binding.get("context_fingerprint") == context_fingerprint
                )
                if not bound_replay:
                    return _error(
                        "PROJECT_STATE_CONFLICT",
                        "当前项目状态不能启动分析",
                        409,
                        retryable=False,
                    )

        def worker(progress):
            try:
                progress("location", 12, "正在核对地块与城市数据")
                result = report_runner(deepcopy(project), progress)
                report = result.get("report_json") if isinstance(result, dict) else None
                scenarios = (
                    result.get("decision_scenarios")
                    if isinstance(result, dict)
                    else None
                )
                if contract_mode:
                    if not isinstance(scenarios, list) or not scenarios:
                        raise RuntimeError("分析引擎未返回 DecisionScenario")
                    accepted_scenarios = deepcopy(scenarios[:3])
                    contract = workflow_contract()
                    for scenario in accepted_scenarios:
                        contract.validate_schema("DecisionScenario", scenario)
                        if scenario.get("project_id") != project_id:
                            raise ContractValidationError("DecisionScenario project_id mismatch")
                        if scenario.get("workflow_id") != requested_workflow_id:
                            raise ContractValidationError("DecisionScenario workflow_id mismatch")

                    def save_scenarios(current):
                        current["decision_scenarios"] = accepted_scenarios
                        current["selected_scenario"] = None
                        current["analysis_job_id"] = None
                        return transition(current, "decision_ready")

                    current = store.get(project_id)
                    updated = store.compare_and_update(
                        project_id,
                        expected_revision=int(current.get("revision") or 1),
                        updater=save_scenarios,
                    )
                    return {
                        "project_id": project_id,
                        "scenario_ids": [item["scenario_id"] for item in accepted_scenarios],
                        "project_revision": updated["revision"],
                    }
                if not isinstance(report, dict) or not report:
                    raise RuntimeError("分析引擎未返回有效报告")
                export = report.get("export") or {}
                live_url = f"/projects/{project_id}/report"
                updated = store.set_report(
                    project_id,
                    report,
                    live_url=live_url,
                    export_url=export.get("interactive_html_url") or export.get("html_url"),
                    json_url=export.get("full_json_url") or export.get("json_url"),
                )
                return {
                    "project_id": project_id,
                    "report_url": live_url,
                    "export_url": updated["report"].get("export_url"),
                }
            except Exception as exc:
                failed = store.get(project_id)
                failed["last_error"] = {"message": str(exc), "retryable": True}
                store.save(transition(failed, "error_recoverable"))
                raise

        try:
            job = jobs.start(
                "project_analysis",
                worker,
                metadata={
                    "project_id": project_id,
                    "owner_id": owner_id(),
                    "workflow_id": requested_workflow_id or None,
                    "operation": "project_analysis",
                },
                idempotency_key=idempotency_key,
                request_fingerprint=analysis_fingerprint,
            )
        except JobQueueFullError as exc:
            overloaded = store.get(project_id)
            overloaded["analysis_job_id"] = None
            overloaded["last_error"] = {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
                "details": deepcopy(exc.details),
            }
            store.save(transition(overloaded, "error_recoverable"))
            return _error(
                exc.code,
                str(exc),
                503,
                retryable=exc.retryable,
                details=exc.details,
            )
        except JobIdempotencyConflictError as exc:
            conflicted = store.get(project_id)
            conflicted["analysis_job_id"] = None
            if conflicted.get("state") == "analyzing" and not bound_replay:
                transition(
                    conflicted,
                    str((conflicted.get("analysis_request") or {}).get("previous_state") or "error_recoverable"),
                )
            store.save(conflicted)
            return _error("IDEMPOTENCY_CONFLICT", str(exc), 409, retryable=False)
        current = store.get(project_id)
        current["analysis_job_id"] = job["id"]
        current = store.save(current)
        return jsonify({"status": "ok", "job": job, "project": current}), 202

    @blueprint.get("/api/v2/projects/<project_id>/scenarios")
    def get_scenarios(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        return jsonify(
            {
                "status": "ok",
                "scenarios": project.get("decision_scenarios") or [],
                "selected_scenario": project.get("selected_scenario"),
                "revision": int(project.get("revision") or 1),
            }
        )

    @blueprint.post("/api/v2/projects/<project_id>/scenarios/<scenario_id>/select")
    def select_scenario(project_id: str, scenario_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            return _error(
                "IDEMPOTENCY_KEY_REQUIRED",
                "选择方案必须携带 Idempotency-Key",
                400,
                retryable=False,
            )
        payload = request.get_json(silent=True) or {}
        try:
            expected_revision = int(payload["expected_revision"])
        except (KeyError, TypeError, ValueError):
            return _error("INVALID_REVISION", "expected_revision 无效", 400, retryable=False)
        scenarios = project.get("decision_scenarios") or []
        selected = next(
            (item for item in scenarios if item.get("scenario_id") == scenario_id),
            None,
        )
        if not selected:
            return _error("SCENARIO_NOT_FOUND", "方案不存在", 404, retryable=False)
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "project_id": project_id,
                    "scenario_id": scenario_id,
                    "expected_revision": expected_revision,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        def apply_selection(current):
            current["selected_scenario"] = deepcopy(selected)
            current.setdefault("scenario_selections", []).append(
                {
                    "scenario_id": selected["scenario_id"],
                    "scenario_revision": selected["revision"],
                }
            )
            return current

        try:
            updated, replayed = store.apply_idempotent_update(
                project_id,
                key=key,
                fingerprint=fingerprint,
                expected_revision=expected_revision,
                updater=apply_selection,
            )
        except IdempotencyConflictError as exc:
            return _error("IDEMPOTENCY_CONFLICT", str(exc), 409, retryable=False)
        except RevisionConflictError as exc:
            return _error(
                "REVISION_CONFLICT",
                str(exc),
                409,
                retryable=True,
                details={"expected": exc.expected, "actual": exc.actual},
            )
        return jsonify(
            {
                "status": "ok",
                "project": updated,
                "selected_scenario": updated.get("selected_scenario"),
                "idempotent_replay": replayed,
            }
        )

    @blueprint.get("/api/v2/projects/<project_id>/visual-brief")
    def get_visual_brief(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        selected = project.get("selected_scenario")
        context = project.get("analysis_context")
        if not selected:
            return _error("SCENARIO_NOT_SELECTED", "请先选择方案", 409, retryable=True)
        if not context:
            return _error(
                "ANALYSIS_CONTEXT_REQUIRED",
                "缺少已验证分析上下文",
                409,
                retryable=True,
            )
        try:
            visual_brief = build_visual_brief(
                scenario=selected,
                analysis_context=context,
                contract=workflow_contract(),
            )
        except (ContractConfigurationError, ContractValidationError) as exc:
            return _error("CONTRACT_UNAVAILABLE", str(exc), 503, retryable=True)
        return jsonify({"status": "ok", "visual_brief": visual_brief})

    @blueprint.post("/api/v2/project-reports")
    def create_project_report_job():
        payload = request.get_json(silent=True)
        allowed_fields = {
            "project_ref",
            "as_of",
            "report_profile",
            "export_profiles",
            "research_mode",
        }
        if not isinstance(payload, dict):
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "请求体必须是 JSON 对象",
                400,
                retryable=False,
            )
        unexpected = sorted(set(payload) - allowed_fields)
        if unexpected:
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "请求包含未允许字段",
                400,
                retryable=False,
                details={"unexpected_fields": unexpected},
            )

        raw_project_ref = payload.get("project_ref")
        if not isinstance(raw_project_ref, str):
            return _error(
                "INVALID_PROJECT_REF",
                "project_ref 必须是服务端登记的字符串标识",
                400,
                retryable=False,
            )
        project_ref = raw_project_ref.strip()
        if (
            not project_report_ref_pattern.fullmatch(project_ref)
            or project_ref in {".", ".."}
        ):
            return _error(
                "INVALID_PROJECT_REF",
                "project_ref 必须是服务端登记的稳定标识，不能是路径",
                400,
                retryable=False,
            )
        project_dir = registered_project_reports.get(project_ref)
        if project_dir is None:
            return _error(
                "PROJECT_REF_NOT_FOUND",
                "项目标识未在服务端登记",
                404,
                retryable=False,
            )

        as_of = payload.get("as_of")
        try:
            if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
                raise ValueError
        except ValueError:
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "as_of 必须使用 YYYY-MM-DD",
                400,
                retryable=False,
            )

        raw_profile = payload.get(
            "report_profile", "dds.apple-dark-16x9/1.1.0"
        )
        if not isinstance(raw_profile, str):
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "report_profile 必须是字符串",
                400,
                retryable=False,
            )
        report_profile = raw_profile.strip()
        if report_profile != "dds.apple-dark-16x9/1.1.0":
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "report_profile 不受支持",
                400,
                retryable=False,
            )

        raw_exports = payload.get(
            "export_profiles", ["interactive", "portable_single_file"]
        )
        if (
            not isinstance(raw_exports, list)
            or not raw_exports
            or any(not isinstance(value, str) for value in raw_exports)
        ):
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "export_profiles 必须是非空字符串数组",
                400,
                retryable=False,
            )
        allowed_exports = {"interactive", "portable_single_file"}
        export_profiles = tuple(dict.fromkeys(value.strip() for value in raw_exports))
        if any(value not in allowed_exports for value in export_profiles):
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "export_profiles 包含不受支持的输出类型",
                400,
                retryable=False,
            )

        raw_research_mode = payload.get("research_mode", "auto")
        if not isinstance(raw_research_mode, str):
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "research_mode 必须是字符串",
                400,
                retryable=False,
            )
        research_mode = raw_research_mode.strip()
        if research_mode not in {"auto", "cached", "off"}:
            return _error(
                "INVALID_PROJECT_REPORT_REQUEST",
                "research_mode 不受支持",
                400,
                retryable=False,
            )

        idempotency_key = request.headers.get("Idempotency-Key", "").strip() or None
        if idempotency_key and not idempotency_key_pattern.fullmatch(idempotency_key):
            return _error(
                "INVALID_IDEMPOTENCY_KEY",
                "Idempotency-Key 格式无效",
                400,
                retryable=False,
            )
        current_owner = owner_id()
        fingerprint_payload = {
            "owner_id": current_owner,
            "project_ref": project_ref,
            "as_of": as_of,
            "report_profile": report_profile,
            "export_profiles": list(export_profiles),
            "research_mode": research_mode,
        }

        def worker(progress: Callable[[str, int, str], None]) -> dict[str, Any]:
            with project_report_locks[project_ref]:
                return run_registered_project_report(
                    project_dir=project_dir,
                    as_of=as_of,
                    report_profile=report_profile,
                    export_profiles=export_profiles,
                    research_mode=research_mode,
                    requested_owner_id=current_owner,
                    progress=progress,
                )

        try:
            job = jobs.start(
                "project_report",
                worker,
                metadata=fingerprint_payload,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(fingerprint_payload),
            )
        except JobQueueFullError as exc:
            return _error(
                exc.code,
                str(exc),
                429,
                retryable=exc.retryable,
                details=exc.details,
            )
        except JobIdempotencyConflictError:
            return _error(
                "IDEMPOTENCY_CONFLICT",
                "相同 Idempotency-Key 已用于不同报告请求",
                409,
                retryable=False,
            )

        job_id = str(job["id"])
        return (
            jsonify(
                {
                    "status": "accepted",
                    "job_id": job_id,
                    "status_url": f"/api/v2/jobs/{job_id}",
                }
            ),
            202,
        )

    @blueprint.get("/api/v2/jobs/<job_id>")
    def get_job(job_id: str):
        job = owned_job(job_id)
        if not job:
            return _error("JOB_NOT_FOUND", "任务不存在", 404, retryable=False)
        return jsonify({"status": "ok", "job": job})

    @blueprint.get("/api/v2/jobs/<job_id>/events")
    def stream_job(job_id: str):
        if not owned_job(job_id):
            return _error("JOB_NOT_FOUND", "任务不存在", 404, retryable=False)

        @stream_with_context
        def event_stream():
            try:
                after_seq = int(request.args.get("after", "0") or 0)
            except ValueError:
                after_seq = 0
            for snapshot in jobs.stream(job_id, after_seq=max(0, after_seq)):
                data = json.dumps(snapshot, ensure_ascii=False, default=str)
                yield f"event: job\ndata: {data}\n\n"

        return Response(
            event_stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @blueprint.post("/api/v2/projects/<project_id>/report")
    def prepare_report(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        report = project.get("report") or {}
        if not report:
            return _error("REPORT_NOT_READY", "报告尚未生成", 409, retryable=True)
        return jsonify(
            {
                "status": "ok",
                "report_url": report.get("live_url"),
                "revision": report.get("revision"),
                "export_url": report.get("export_url"),
            }
        )

    @blueprint.post("/api/v2/projects/<project_id>/report-revisions")
    def freeze_report_revision(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        if revisions is None:
            return _error(
                "REPORT_REVISION_UNAVAILABLE",
                "不可变报告服务尚未配置",
                503,
                retryable=True,
            )
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key:
            return _error(
                "IDEMPOTENCY_KEY_REQUIRED",
                "冻结报告必须携带 Idempotency-Key",
                400,
                retryable=False,
            )
        payload = request.get_json(silent=True) or {}
        try:
            expected_revision = int(payload["expected_revision"])
        except (KeyError, TypeError, ValueError):
            return _error("INVALID_REVISION", "expected_revision 无效", 400, retryable=False)
        report_payload = {key: value for key, value in payload.items() if key != "expected_revision"}
        selected = project.get("selected_scenario") or {}
        submitted = report_payload.get("selected_scenario") or {}
        if submitted != selected:
            return _error(
                "SCENARIO_SELECTION_CONFLICT",
                "报告方案不是项目已选择的方案 revision",
                409,
                retryable=False,
            )
        context = project.get("analysis_context") or {}
        if report_payload.get("workflow_id") != context.get("workflow_id"):
            return _error(
                "ANALYSIS_CONTEXT_REQUIRED",
                "报告 Workflow 与分析上下文不匹配",
                409,
                retryable=False,
            )
        actual_revision = int(project.get("revision") or 1)
        if actual_revision != expected_revision:
            try:
                replay = revisions.replay_if_exists(
                    owner=owner_id(),
                    project_id=project_id,
                    payload=report_payload,
                    key=key,
                )
            except ReportIdempotencyConflictError as exc:
                return _error("IDEMPOTENCY_CONFLICT", str(exc), 409, retryable=False)
            except (ContractValidationError, ValueError, KeyError) as exc:
                return _error("REPORT_BUNDLE_INVALID", str(exc), 400, retryable=False)
            if replay is not None and project.get("report_bundle") == replay.bundle:
                return jsonify(
                    {
                        "status": "ok",
                        "report_bundle": replay.bundle,
                        "project": project,
                        "idempotent_replay": True,
                    }
                )
            return _error(
                "REVISION_CONFLICT",
                f"project revision conflict: expected {expected_revision}, actual {actual_revision}",
                409,
                retryable=True,
                details={"expected": expected_revision, "actual": actual_revision},
            )
        try:
            revision = revisions.freeze(
                owner=owner_id(),
                project_id=project_id,
                payload=report_payload,
                key=key,
            )
        except ArtifactAccessError as exc:
            return _error(
                exc.code,
                str(exc),
                exc.status,
                retryable=exc.retryable,
            )
        except ArtifactPolicyError as exc:
            return _error("ARTIFACT_SCOPE_MISMATCH", str(exc), 409, retryable=False)
        except ArtifactIntegrityError as exc:
            return _error("ARTIFACT_INTEGRITY_FAILED", str(exc), 409, retryable=False)
        except ReportIdempotencyConflictError as exc:
            return _error("IDEMPOTENCY_CONFLICT", str(exc), 409, retryable=False)
        except ReportRevisionConflictError as exc:
            return _error("REPORT_REVISION_CONFLICT", str(exc), 409, retryable=True)
        except (ContractValidationError, ValueError, KeyError) as exc:
            return _error("REPORT_BUNDLE_INVALID", str(exc), 400, retryable=False)

        if revision.replayed and project.get("report_bundle") == revision.bundle:
            return jsonify(
                {
                    "status": "ok",
                    "report_bundle": revision.bundle,
                    "project": project,
                    "idempotent_replay": True,
                }
            )

        def attach_bundle(current):
            current["report_bundle"] = deepcopy(revision.bundle)
            return transition(current, "completed")

        try:
            updated = store.compare_and_update(
                project_id,
                expected_revision=expected_revision,
                updater=attach_bundle,
            )
        except RevisionConflictError as exc:
            return _error(
                "REVISION_CONFLICT",
                str(exc),
                409,
                retryable=True,
                details={"expected": exc.expected, "actual": exc.actual},
            )
        return (
            jsonify(
                {
                    "status": "ok",
                    "report_bundle": revision.bundle,
                    "project": updated,
                    "idempotent_replay": revision.replayed,
                }
            ),
            201,
        )

    @blueprint.get("/api/v2/report-artifacts/<artifact_id>")
    def get_report_artifact(artifact_id: str):
        if revisions is None:
            return _error(
                "REPORT_REVISION_UNAVAILABLE",
                "不可变报告服务尚未配置",
                503,
                retryable=True,
            )
        workflow_id = str(request.args.get("workflow_id") or "").strip()
        view = str(request.args.get("view") or "metadata").strip()
        if not workflow_id:
            return _error(
                "WORKFLOW_ID_REQUIRED",
                "读取报告 Artifact 必须指定 workflow_id",
                400,
                retryable=False,
            )
        if view not in {"metadata", "content"}:
            return _error(
                "ARTIFACT_VIEW_INVALID",
                "Artifact view 仅支持 metadata 或 content",
                400,
                retryable=False,
            )
        try:
            resolved = revisions.resolve_artifact(
                owner=owner_id(),
                workflow_id=workflow_id,
                artifact_id=artifact_id,
            )
        except (KeyError, ContractValidationError):
            return _error(
                "REPORT_ARTIFACT_NOT_FOUND",
                "报告 Artifact 不存在",
                404,
                retryable=False,
            )
        except ValueError:
            return _error(
                "REPORT_ARTIFACT_INTEGRITY_FAILED",
                "报告 Artifact 完整性校验失败",
                409,
                retryable=False,
            )
        artifact = resolved.artifact
        common_headers = {
            "ETag": f'"sha256-{artifact["content_sha256"]}"',
            "Cache-Control": "private, no-store",
            "X-Content-SHA256": artifact["content_sha256"],
        }
        if view == "metadata":
            response = jsonify({"ok": True, "artifact": artifact})
            response.headers.update(common_headers)
            return response
        extension = "html" if artifact["kind"] == "report_html" else "json"
        response = Response(resolved.content, mimetype="application/octet-stream")
        response.headers.update(common_headers)
        response.headers["Content-Length"] = str(len(resolved.content))
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{artifact_id}.{extension}"'
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'"
        )
        return response

    @blueprint.get("/api/v2/projects/<project_id>/report-data")
    def report_data(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        if not project.get("report"):
            return _error("REPORT_NOT_READY", "报告尚未生成", 409, retryable=True)
        try:
            report = store.get_report(project_id)
        except KeyError:
            return _error("REPORT_NOT_READY", "报告快照不存在，请重新生成", 409, retryable=True)
        scene_manifest = compile_scene_manifest(project, report)
        return jsonify({"status": "ok", "project": project, "report": report, "scene_manifest": scene_manifest})

    @blueprint.get("/projects/<project_id>/report")
    def live_report(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        if not project.get("report"):
            return _error("REPORT_NOT_READY", "报告尚未生成", 409, retryable=True)
        if not report_page or not (report_page / "report.html").exists():
            return _error("REPORT_VIEW_UNAVAILABLE", "报告页面尚未安装", 503, retryable=True)
        return send_from_directory(str(report_page), "report.html")

    @blueprint.get("/projects/<project_id>/map")
    def project_map(project_id: str):
        try:
            owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        map_page = Path(__file__).resolve().parents[1] / "web" / "decision-field"
        if not (map_page / "map.html").exists():
            return _error("MAP_UI_NOT_FOUND", "地图研判页面尚未安装", 404, retryable=False)
        return send_from_directory(str(map_page), "map.html")

    @blueprint.post("/api/v2/projects/<project_id>/export")
    def export_project(project_id: str):
        try:
            project = owned_project(project_id)
        except (KeyError, ValueError):
            return _error("PROJECT_NOT_FOUND", "项目不存在", 404, retryable=False)
        report = project.get("report") or {}
        if not report:
            return _error("REPORT_NOT_READY", "报告尚未生成", 409, retryable=True)
        return jsonify(
            {
                "status": "ok",
                "live_url": report.get("live_url"),
                "html_url": report.get("export_url"),
                "json_url": report.get("json_url"),
            }
        )

    return blueprint


def _error(
    code: str,
    message: str,
    status_code: int,
    *,
    retryable: bool,
    details: dict[str, Any] | None = None,
):
    return (
        jsonify(
            {
                "status": "error",
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            }
        ),
        status_code,
    )
