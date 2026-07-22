"""Evidence-first orchestration with strict agent and service boundaries.

The workflow deliberately contains no collection, resolution, business, or
writing logic.  It only calls injected components in the mandated order and
stops before downstream stages whenever critical evidence is not admissible.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
import inspect
import logging
from typing import Any, Protocol

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.agents.content_writer import ContentWriterAgent, TASK_CONTENT_WRITING
from dds.agents.research_planner import ResearchPlannerAgent, TASK_RESEARCH_PLANNING
from dds.agents.requirement import RequirementAgent
from dds.api.runs import RunStatus, RunStore
from dds.domain import (
    DataRequirement,
    EvidenceRecord,
    EvidenceResolutionStatus,
    ProjectContext,
    SectionResult,
)
from dds.engines.resolver import EvidenceResolution, EvidenceResolver


logger = logging.getLogger(__name__)

TASK_EVIDENCE_FIRST_REPORT = "evidence_first_report"


class EvidenceCollector(Protocol):
    """Injected collector boundary; implementations may only return evidence."""

    def collect(
        self,
        *,
        research_plan: Mapping[str, Any],
        requirements: Sequence[DataRequirement],
        project_context: ProjectContext,
        existing_evidence: Sequence[EvidenceRecord],
    ) -> Sequence[EvidenceRecord] | Awaitable[Sequence[EvidenceRecord]]:
        """Collect records without resolving or interpreting them."""


class SectionAssembler(Protocol):
    """Injected deterministic business boundary producing section contracts."""

    def assemble(
        self,
        *,
        resolutions: Mapping[str, EvidenceResolution],
        evidence_records: Sequence[EvidenceRecord],
        requirements: Sequence[DataRequirement],
        project_context: ProjectContext,
    ) -> Sequence[SectionResult] | Awaitable[Sequence[SectionResult]]:
        """Assemble business outputs without writing narrative prose."""


class _StageFailure(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _copy_result(result: AgentResult, *, task_id: str) -> AgentResult:
    data = deepcopy(result.data)
    data["idempotent_replay"] = True
    return AgentResult(
        task_id=task_id,
        success=result.success,
        agent_id=result.agent_id,
        status=result.status,
        data=data,
        errors=list(result.errors),
        warnings=list(result.warnings),
        confidence=result.confidence,
    )


class EvidenceFirstWorkflow:
    """Run the evidence-first chain and enforce every cross-stage boundary.

    Required order:
    RequirementAgent -> ResearchPlannerAgent -> collector -> EvidenceResolver
    -> section assembler -> ContentWriterAgent.

    ``RunStore`` is optional and remains the sole persistence implementation
    for idempotency, checkpoints, retry attempts, and recovery metadata.
    """

    def __init__(
        self,
        *,
        collector: EvidenceCollector | Callable[..., Any] | None,
        section_assembler: SectionAssembler | Callable[..., Any] | None,
        requirement_agent: RequirementAgent | None = None,
        research_planner: ResearchPlannerAgent | None = None,
        resolver: EvidenceResolver | None = None,
        content_writer: ContentWriterAgent | None = None,
        run_store: RunStore | None = None,
    ) -> None:
        self.requirement_agent = requirement_agent or RequirementAgent()
        self.research_planner = research_planner or ResearchPlannerAgent()
        self.collector = collector
        self.resolver = resolver or EvidenceResolver()
        self.section_assembler = section_assembler
        self.content_writer = content_writer or ContentWriterAgent()
        self.run_store = run_store
        self._completed: dict[str, AgentResult] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute or idempotently replay one evidence-first report task."""

        parameters = dict(task.parameters or {})
        idempotency_key = str(
            parameters.get("idempotency_key")
            or parameters.get("report_id")
            or task.task_id
        ).strip()
        if not idempotency_key:
            return self._failed(task, "created", "idempotency_key must not be empty")

        lock = self._locks.setdefault(idempotency_key, asyncio.Lock())
        async with lock:
            cached = self._completed.get(idempotency_key)
            if cached is not None:
                logger.info("idempotent replay for %s", idempotency_key)
                return _copy_result(cached, task_id=task.task_id)
            return await self._run_once(task, idempotency_key, parameters)

    async def _run_once(
        self,
        task: AgentTask,
        idempotency_key: str,
        parameters: Mapping[str, Any],
    ) -> AgentResult:
        raw_context = parameters.get("project_context") or {}
        context_hint = ProjectContext.from_mapping(raw_context)
        run_id = task.task_id
        checkpoint = "created"

        if self.run_store is not None:
            record = self.run_store.create_or_get(
                idempotency_key,
                project_id=context_hint.project_id,
            )
            run_id = record.run_id
            if record.status is RunStatus.COMPLETED:
                return AgentResult(
                    task_id=task.task_id,
                    success=True,
                    agent_id="evidence_first_orchestrator",
                    status=AgentStatus.COMPLETED,
                    data={
                        "run_id": run_id,
                        "workflow_status": "completed",
                        "checkpoint": record.checkpoint,
                        "idempotent_replay": True,
                        "artifacts": dict(record.artifacts),
                        "evidence_package_hash": record.evidence_package_hash,
                    },
                )
            checkpoint = record.checkpoint
            recovery = {
                "resumed_from_status": record.status.value,
                "resumed_from_checkpoint": record.checkpoint,
            }
            self.run_store.transition(
                run_id,
                status=RunStatus.RUNNING,
                checkpoint=checkpoint,
                recovery=recovery,
                increment_attempt=True,
            )

        stage_order: list[str] = []
        try:
            logger.info("[%s] requirement", run_id)
            stage_order.append("requirement")
            requirement_result = await self.requirement_agent.run(
                AgentTask(
                    task_type="requirement_analysis",
                    parameters={
                        "project_context": raw_context,
                        "existing_data": parameters.get("existing_data") or {},
                    },
                    timeout_seconds=task.timeout_seconds,
                )
            )
            if not requirement_result.success:
                return self._waiting(
                    task,
                    run_id,
                    "waiting",
                    "requirement_waiting",
                    requirement_result.errors or ["requirements are incomplete"],
                    requirement_result.data.get("supplement_actions", []),
                    stage_order,
                )
            checkpoint = "requirement_completed"
            self._checkpoint(run_id, checkpoint)

            context = ProjectContext.from_mapping(
                requirement_result.data.get("project_context") or raw_context
            )
            graph = requirement_result.data.get("data_requirement_graph") or {
                "nodes": requirement_result.data.get("data_requirements") or []
            }
            requirements = self._requirements_from_graph(graph)

            existing_evidence = self._normalize_input_evidence(
                parameters.get("evidence_records")
                or parameters.get("evidence")
                or []
            )
            logger.info("[%s] research planning", run_id)
            stage_order.append("research_planner")
            planning_result = await self.research_planner.run(
                AgentTask(
                    task_type=TASK_RESEARCH_PLANNING,
                    parameters={
                        "requirements": requirements,
                        "project_context": context,
                        "evidence_records": existing_evidence,
                    },
                    timeout_seconds=task.timeout_seconds,
                )
            )
            if not planning_result.success:
                raise _StageFailure(
                    "research_planner",
                    "; ".join(planning_result.errors) or "research planning failed",
                )
            research_plan = dict(planning_result.data)
            checkpoint = "research_planned"
            self._checkpoint(run_id, checkpoint)

            if self.collector is None:
                return self._waiting(
                    task,
                    run_id,
                    "waiting",
                    "collector_waiting",
                    ["no EvidenceCollector is configured"],
                    research_plan.get("supplement_actions", []),
                    stage_order,
                )

            logger.info("[%s] evidence collection", run_id)
            stage_order.append("collector")
            collector_callable = getattr(self.collector, "collect", self.collector)
            collected_output = await _await_if_needed(
                collector_callable(
                    research_plan=research_plan,
                    requirements=requirements,
                    project_context=context,
                    existing_evidence=existing_evidence,
                )
            )
            collected = self._validate_collector_output(collected_output)
            evidence_records = self._merge_evidence(existing_evidence, collected)
            checkpoint = "evidence_collected"
            self._checkpoint(run_id, checkpoint)

            logger.info("[%s] evidence resolution", run_id)
            stage_order.append("resolver")
            resolutions = self.resolver.resolve_many(
                requirements,
                evidence_records,
                project_context=context,
            )
            checkpoint = "evidence_resolved"
            self._checkpoint(run_id, checkpoint)

            critical_ids = self._critical_metric_ids(requirements, parameters)
            blocked = self._blocked_resolutions(
                critical_ids,
                resolutions,
                evidence_records,
            )
            if blocked:
                supplement_actions = self._resolution_actions(
                    blocked,
                    research_plan.get("supplement_actions", []),
                )
                return self._waiting(
                    task,
                    run_id,
                    "blocked",
                    "evidence_blocked",
                    [
                        "critical evidence is not decision-admissible: "
                        + ", ".join(sorted(blocked))
                    ],
                    supplement_actions,
                    stage_order,
                    resolutions=resolutions,
                )

            if self.section_assembler is None:
                return self._waiting(
                    task,
                    run_id,
                    "waiting",
                    "assembler_waiting",
                    ["no SectionAssembler is configured"],
                    [],
                    stage_order,
                    resolutions=resolutions,
                )

            logger.info("[%s] section assembly", run_id)
            stage_order.append("section_assembler")
            assembler_callable = getattr(
                self.section_assembler,
                "assemble",
                self.section_assembler,
            )
            assembled_output = await _await_if_needed(
                assembler_callable(
                    resolutions=resolutions,
                    evidence_records=evidence_records,
                    requirements=requirements,
                    project_context=context,
                )
            )
            sections = self._validate_assembler_output(
                assembled_output,
                evidence_records,
            )
            checkpoint = "sections_assembled"
            self._checkpoint(run_id, checkpoint)

            logger.info("[%s] content writing", run_id)
            stage_order.append("content_writer")
            content: dict[str, str] = {}
            for section in sections:
                writer_result = await self.content_writer.run(
                    AgentTask(
                        task_type=TASK_CONTENT_WRITING,
                        section_id=section.section_id,
                        parameters={
                            "section_result": section,
                            "evidence_records": evidence_records,
                        },
                        timeout_seconds=task.timeout_seconds,
                    )
                )
                if not writer_result.success:
                    raise _StageFailure(
                        "content_writer",
                        "; ".join(writer_result.errors) or "content writing failed",
                    )
                content[section.section_id] = str(writer_result.data.get("content") or "")
            checkpoint = "content_written"
            self._checkpoint(run_id, checkpoint)

            if self.run_store is not None:
                self.run_store.transition(
                    run_id,
                    status=RunStatus.COMPLETED,
                    checkpoint="completed",
                    recovery={},
                )
            result = AgentResult(
                task_id=task.task_id,
                success=True,
                agent_id="evidence_first_orchestrator",
                status=AgentStatus.COMPLETED,
                data={
                    "run_id": run_id,
                    "workflow_status": "completed",
                    "checkpoint": "completed",
                    "idempotent_replay": False,
                    "project_context": context.to_dict(),
                    "data_requirement_graph": graph,
                    "requirements": [item.to_dict() for item in requirements],
                    "research_plan": research_plan,
                    "evidence_records": [item.to_dict() for item in evidence_records],
                    "resolutions": {
                        key: value.to_dict() for key, value in resolutions.items()
                    },
                    "sections": {
                        item.section_id: item.to_dict() for item in sections
                    },
                    "content": content,
                    "stage_order": stage_order,
                    "supplement_actions": [],
                },
                warnings=list(requirement_result.warnings)
                + list(planning_result.warnings),
            )
            self._completed[idempotency_key] = result
            return result
        except _StageFailure as exc:
            checkpoint = f"{exc.stage}_failed"
            return self._failed(task, checkpoint, str(exc), run_id=run_id)
        except Exception as exc:  # boundary exceptions must update recovery state
            logger.exception("[%s] evidence-first workflow failed at %s", run_id, checkpoint)
            return self._failed(
                task,
                checkpoint,
                f"{type(exc).__name__}: {exc}",
                run_id=run_id,
            )

    def _requirements_from_graph(
        self,
        graph: Mapping[str, Any],
    ) -> list[DataRequirement]:
        if not isinstance(graph, Mapping):
            raise _StageFailure("requirement", "data_requirement_graph must be a mapping")
        raw_nodes = graph.get("nodes") or []
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            raise _StageFailure("requirement", "data_requirement_graph.nodes must be a sequence")
        requirements = [DataRequirement.from_mapping(item) for item in raw_nodes]
        if not requirements:
            raise _StageFailure("requirement", "data_requirement_graph has no nodes")
        metric_ids = [item.metric_id for item in requirements]
        if any(not item for item in metric_ids) or len(metric_ids) != len(set(metric_ids)):
            raise _StageFailure(
                "requirement",
                "every requirement needs a unique metric_id",
            )
        if any(not item.section_id or not item.field_name for item in requirements):
            raise _StageFailure(
                "requirement",
                "every requirement needs section_id and field_name",
            )

        section_order = list(graph.get("section_order") or [])
        if section_order:
            if len(section_order) != len(set(section_order)):
                raise _StageFailure("requirement", "section_order contains duplicates")
            order = {section_id: index for index, section_id in enumerate(section_order)}
            if any(item.section_id not in order for item in requirements):
                raise _StageFailure(
                    "requirement",
                    "requirement section is absent from section_order",
                )
            for edge in graph.get("edges") or []:
                source = str(edge.get("from_section") or "")
                target = str(edge.get("to_section") or "")
                if source not in order or target not in order or order[source] >= order[target]:
                    raise _StageFailure(
                        "requirement",
                        f"invalid requirement dependency edge: {source}->{target}",
                    )
        return requirements

    def _normalize_input_evidence(self, raw: Any) -> list[EvidenceRecord]:
        if isinstance(raw, Mapping):
            raw = raw.values()
        if raw is None:
            return []
        if isinstance(raw, (str, bytes)):
            raise _StageFailure("collector", "input evidence must be a sequence")
        return [EvidenceRecord.from_mapping(item) for item in raw]

    def _validate_collector_output(self, raw: Any) -> list[EvidenceRecord]:
        if isinstance(raw, EvidenceRecord):
            records = [raw]
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            records = list(raw)
        else:
            raise _StageFailure(
                "collector",
                "collector must return EvidenceRecord or a sequence of EvidenceRecord",
            )
        invalid = [type(item).__name__ for item in records if not isinstance(item, EvidenceRecord)]
        if invalid:
            raise _StageFailure(
                "collector",
                "collector returned non-EvidenceRecord values: " + ", ".join(invalid),
            )
        return records

    def _merge_evidence(
        self,
        existing: Sequence[EvidenceRecord],
        collected: Sequence[EvidenceRecord],
    ) -> list[EvidenceRecord]:
        merged: list[EvidenceRecord] = []
        by_id: dict[str, EvidenceRecord] = {}
        for record in [*existing, *collected]:
            if not record.evidence_id:
                merged.append(record)
                continue
            prior = by_id.get(record.evidence_id)
            if prior is not None:
                if prior.to_dict() != record.to_dict():
                    raise _StageFailure(
                        "collector",
                        f"duplicate evidence_id has different payload: {record.evidence_id}",
                    )
                continue
            by_id[record.evidence_id] = record
            merged.append(record)
        return merged

    def _critical_metric_ids(
        self,
        requirements: Sequence[DataRequirement],
        parameters: Mapping[str, Any],
    ) -> set[str]:
        explicit = parameters.get("critical_metric_ids")
        if explicit is not None:
            if isinstance(explicit, (str, bytes)):
                explicit = [explicit]
            requested = {str(item) for item in explicit}
            known = {item.metric_id for item in requirements}
            unknown = requested - known
            if unknown:
                raise _StageFailure(
                    "resolver",
                    "unknown critical_metric_ids: " + ", ".join(sorted(unknown)),
                )
            return requested
        return {item.metric_id for item in requirements if item.required}

    def _blocked_resolutions(
        self,
        critical_ids: set[str],
        resolutions: Mapping[str, EvidenceResolution],
        evidence_records: Sequence[EvidenceRecord],
    ) -> dict[str, str]:
        evidence_by_id = {
            item.evidence_id: item for item in evidence_records if item.evidence_id
        }
        blocked: dict[str, str] = {}
        fail_statuses = {
            EvidenceResolutionStatus.UNKNOWN,
            EvidenceResolutionStatus.CONFLICT,
            EvidenceResolutionStatus.STALE,
        }
        for metric_id in critical_ids:
            resolution = resolutions.get(metric_id)
            if resolution is None:
                blocked[metric_id] = EvidenceResolutionStatus.UNKNOWN.value
                continue
            if resolution.status in fail_statuses:
                blocked[metric_id] = resolution.status.value
                continue
            if not resolution.evidence_refs:
                blocked[metric_id] = "untraceable"
                continue
            referenced = [evidence_by_id.get(item) for item in resolution.evidence_refs]
            if any(item is None or not item.has_traceable_source for item in referenced):
                blocked[metric_id] = "untraceable"
        return blocked

    def _resolution_actions(
        self,
        blocked: Mapping[str, str],
        planned_actions: Any,
    ) -> list[dict[str, Any]]:
        planned = {
            str(item.get("metric_id") or ""): dict(item)
            for item in (planned_actions or [])
            if isinstance(item, Mapping)
        }
        actions = []
        for metric_id, reason in sorted(blocked.items()):
            action = planned.get(metric_id) or {
                "action_id": f"supplement-{metric_id}",
                "action": "collect_traceable_evidence",
                "metric_id": metric_id,
                "status": "pending",
            }
            action["blocked_resolution_status"] = reason
            action["requires_source_ref_or_hash"] = True
            actions.append(action)
        return actions

    def _validate_assembler_output(
        self,
        raw: Any,
        evidence_records: Sequence[EvidenceRecord],
    ) -> list[SectionResult]:
        if isinstance(raw, SectionResult):
            sections = [raw]
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            sections = list(raw)
        else:
            raise _StageFailure(
                "section_assembler",
                "assembler must return SectionResult or a sequence of SectionResult",
            )
        if not sections or any(not isinstance(item, SectionResult) for item in sections):
            raise _StageFailure(
                "section_assembler",
                "assembler may only return non-empty SectionResult values",
            )
        section_ids = [item.section_id for item in sections]
        if any(not item for item in section_ids) or len(section_ids) != len(set(section_ids)):
            raise _StageFailure(
                "section_assembler",
                "assembled sections need unique, non-empty section_id values",
            )

        known_refs = {
            item.evidence_id
            for item in evidence_records
            if item.evidence_id and item.has_traceable_source
        }
        for section in sections:
            refs = set(section.evidence_refs)
            for field_name in section.data:
                refs.update(section.field_resolution(field_name).evidence_refs)
            unknown_refs = refs - known_refs
            if unknown_refs:
                raise _StageFailure(
                    "section_assembler",
                    f"{section.section_id} cites unknown evidence: "
                    + ", ".join(sorted(unknown_refs)),
                )
        return sections

    def _checkpoint(self, run_id: str, checkpoint: str) -> None:
        if self.run_store is not None:
            self.run_store.transition(
                run_id,
                status=RunStatus.RUNNING,
                checkpoint=checkpoint,
            )

    def _waiting(
        self,
        task: AgentTask,
        run_id: str,
        workflow_status: str,
        checkpoint: str,
        errors: Sequence[str],
        supplement_actions: Any,
        stage_order: Sequence[str],
        *,
        resolutions: Mapping[str, EvidenceResolution] | None = None,
    ) -> AgentResult:
        actions = [dict(item) for item in (supplement_actions or [])]
        if self.run_store is not None:
            self.run_store.transition(
                run_id,
                status=RunStatus.BLOCKED,
                checkpoint=checkpoint,
                error="; ".join(errors),
                recovery={
                    "resume_from": checkpoint,
                    "supplement_actions": actions,
                },
            )
        return AgentResult(
            task_id=task.task_id,
            success=False,
            agent_id="evidence_first_orchestrator",
            status=AgentStatus.WAITING,
            data={
                "run_id": run_id,
                "workflow_status": workflow_status,
                "checkpoint": checkpoint,
                "stage_order": list(stage_order),
                "supplement_actions": actions,
                "resolutions": {
                    key: value.to_dict()
                    for key, value in (resolutions or {}).items()
                },
                "sections": {},
                "content": {},
            },
            errors=list(errors),
        )

    def _failed(
        self,
        task: AgentTask,
        checkpoint: str,
        error: str,
        *,
        run_id: str | None = None,
    ) -> AgentResult:
        resolved_run_id = run_id or task.task_id
        if self.run_store is not None and run_id is not None:
            self.run_store.transition(
                run_id,
                status=RunStatus.FAILED,
                checkpoint=checkpoint,
                error=error,
                recovery={"retry_from": checkpoint},
            )
        return AgentResult(
            task_id=task.task_id,
            success=False,
            agent_id="evidence_first_orchestrator",
            status=AgentStatus.FAILED,
            data={
                "run_id": resolved_run_id,
                "workflow_status": "failed",
                "checkpoint": checkpoint,
                "sections": {},
                "content": {},
            },
            errors=[error],
        )


class EvidenceFirstOrchestratorAgent(BaseAgent):
    """BaseAgent adapter around :class:`EvidenceFirstWorkflow`."""

    def __init__(
        self,
        workflow: EvidenceFirstWorkflow | None = None,
        **workflow_kwargs: Any,
    ) -> None:
        super().__init__()
        self.workflow = workflow or EvidenceFirstWorkflow(**workflow_kwargs)

    @property
    def agent_id(self) -> str:
        return "evidence_first_orchestrator"

    @property
    def agent_name(self) -> str:
        return "Evidence-First Orchestrator Agent"

    def can_handle(self, task_type: str) -> bool:
        return task_type == TASK_EVIDENCE_FIRST_REPORT

    async def execute(self, task: AgentTask) -> AgentResult:
        if not self.can_handle(task.task_type):
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=[f"unsupported task_type: {task.task_type}"],
            )
        return await self.workflow.run(task)


__all__ = [
    "TASK_EVIDENCE_FIRST_REPORT",
    "EvidenceCollector",
    "SectionAssembler",
    "EvidenceFirstWorkflow",
    "EvidenceFirstOrchestratorAgent",
]
