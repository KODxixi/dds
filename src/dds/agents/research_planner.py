"""Research planning agent that never performs collection itself."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.domain import DataRequirement, EvidenceRecord, EvidenceType, ProjectContext


TASK_RESEARCH_PLANNING = "research_planning"

_SOURCE_CLASSES: dict[EvidenceType, tuple[str, ...]] = {
    EvidenceType.OBSERVED_FACT: (
        "client_document",
        "government_record",
        "traceable_market_dataset",
    ),
    EvidenceType.SOCIAL_OBSERVATION: ("traceable_social_observation",),
    EvidenceType.ANALYSIS_INFERENCE: ("documented_analysis",),
    EvidenceType.MODEL_SIMULATION: ("deterministic_model",),
    EvidenceType.TRADITIONAL_INTERPRETATION: ("specialist_interpretation",),
}


def _temporal_text(value: date | datetime | str | None) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value or "")


class ResearchPlannerAgent(BaseAgent):
    """Turn data requirements into deterministic source batches and gap actions."""

    @property
    def agent_id(self) -> str:
        return "research_planner"

    @property
    def agent_name(self) -> str:
        return "Research Planner Agent"

    def can_handle(self, task_type: str) -> bool:
        return task_type == TASK_RESEARCH_PLANNING

    async def execute(self, task: AgentTask) -> AgentResult:
        parameters = task.parameters
        raw_requirements = (
            parameters.get("requirements")
            or parameters.get("data_requirements")
            or []
        )
        raw_evidence = (
            parameters.get("evidence_records")
            or parameters.get("evidence")
            or []
        )
        try:
            plan = self.plan(
                raw_requirements,
                project_context=parameters.get("project_context"),
                evidence_records=raw_evidence,
            )
        except (TypeError, ValueError) as exc:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=[str(exc)],
            )

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data=plan,
            warnings=list(plan.get("warnings", [])),
        )

    def plan(
        self,
        requirements: (
            Iterable[DataRequirement | Mapping[str, Any]]
            | Mapping[str, DataRequirement | Mapping[str, Any]]
        ),
        *,
        project_context: ProjectContext | Mapping[str, Any] | None = None,
        evidence_records: (
            Iterable[EvidenceRecord | Mapping[str, Any]]
            | Mapping[str, EvidenceRecord | Mapping[str, Any]]
            | None
        ) = None,
    ) -> dict[str, Any]:
        """Return a collection-free plan grouped by evidence scope."""
        context = ProjectContext.from_mapping(project_context)
        raw_requirements = (
            requirements.values() if isinstance(requirements, Mapping) else requirements
        )
        normalized = [DataRequirement.from_mapping(item) for item in raw_requirements]
        raw_records: Iterable[EvidenceRecord | Mapping[str, Any]]
        if isinstance(evidence_records, Mapping):
            raw_records = evidence_records.values()
        else:
            raw_records = evidence_records or []
        records = [EvidenceRecord.from_mapping(item) for item in raw_records]
        traceable_by_metric: dict[str, list[EvidenceRecord]] = defaultdict(list)
        for record in records:
            if record.metric_id and record.has_traceable_source:
                traceable_by_metric[record.metric_id].append(record)

        source_plan: list[dict[str, Any]] = []
        supplement_actions: list[dict[str, Any]] = []
        pending_batches: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

        for requirement in sorted(
            normalized,
            key=lambda item: (item.section_id, item.metric_id, item.field_name),
        ):
            metric_id = requirement.metric_id or (
                f"{requirement.section_id}.{requirement.field_name}"
                if requirement.section_id and requirement.field_name
                else ""
            )
            if not metric_id:
                raise ValueError("every DataRequirement needs metric_id or section_id + field_name")
            field_name = requirement.field_name or metric_id.rsplit(".", 1)[-1]
            allowed_types = tuple(requirement.evidence_types) or (EvidenceType.OBSERVED_FACT,)
            allowed_values = tuple(sorted(item.value for item in allowed_types))
            available = [
                item
                for item in traceable_by_metric.get(metric_id, [])
                if item.evidence_type in allowed_types
            ]
            required_count = requirement.min_evidence_count if requirement.required else 0
            missing_count = max(0, required_count - len(available))
            geography = requirement.geography or context.target_geography
            as_of = _temporal_text(requirement.as_of or context.base_date)
            source_classes = sorted(
                {
                    source_class
                    for evidence_type in allowed_types
                    for source_class in _SOURCE_CLASSES[evidence_type]
                }
            )
            item = {
                "metric_id": metric_id,
                "section_id": requirement.section_id,
                "field_name": field_name,
                "description": requirement.description,
                "geography": geography,
                "as_of": as_of,
                "max_age_days": requirement.max_age_days,
                "allowed_evidence_types": list(allowed_values),
                "source_classes": source_classes,
                "required_count": required_count,
                "available_count": len(available),
                "missing_count": missing_count,
                "status": "satisfied" if missing_count == 0 else "planned",
            }
            source_plan.append(item)

            if missing_count:
                action = {
                    "action_id": f"supplement-{metric_id}",
                    "action": "collect_traceable_evidence",
                    "metric_id": metric_id,
                    "section_id": requirement.section_id,
                    "field_name": field_name,
                    "missing_count": missing_count,
                    "acceptance": {
                        "geography": geography,
                        "as_of": as_of,
                        "max_age_days": requirement.max_age_days,
                        "evidence_types": list(allowed_values),
                        "requires_source_ref_or_hash": True,
                    },
                    "status": "pending",
                }
                supplement_actions.append(action)
                batch_key = (
                    geography,
                    as_of,
                    requirement.max_age_days,
                    allowed_values,
                    tuple(source_classes),
                )
                pending_batches[batch_key].append(item)

        batches = []
        for index, (scope, items) in enumerate(
            sorted(pending_batches.items(), key=lambda pair: repr(pair[0])),
            start=1,
        ):
            geography, as_of, max_age_days, evidence_types, source_classes = scope
            batches.append(
                {
                    "batch_id": f"research-batch-{index:03d}",
                    "geography": geography,
                    "as_of": as_of,
                    "max_age_days": max_age_days,
                    "evidence_types": list(evidence_types),
                    "source_classes": list(source_classes),
                    "metric_ids": [item["metric_id"] for item in items],
                    "status": "planned",
                    "collection_performed": False,
                }
            )

        warnings = [] if normalized else ["no DataRequirement items were provided"]
        return {
            "source_plan": source_plan,
            "batches": batches,
            "supplement_actions": supplement_actions,
            "collection_performed": False,
            "warnings": warnings,
        }


__all__ = ["TASK_RESEARCH_PLANNING", "ResearchPlannerAgent"]
