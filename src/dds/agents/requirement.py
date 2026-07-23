"""Normalize project requirements without claiming evidence that was not supplied."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dds.analysis_profile import resolve_analysis_profile
from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.contracts import SECTION_REQUIREMENTS
from dds.domain import DataRequirement, EvidenceType


_REQUIREMENT_SECTION_ORDER = ("SC2", "SC3", "AD1", "AD2", "AD3", "AD4", "AD5", "VA1", "VA2", "VA3")
_REQUIREMENT_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "SC2": (),
    "SC3": ("SC1",),
    "AD1": ("SC2",),
    "AD2": ("AD1",),
    "AD3": ("AD2",),
    "AD4": ("AD3",),
    "VA1": ("AD4",),
    "AD5": ("AD4",),
    "VA2": ("AD3",),
    "VA3": ("VA2",),
}
_OBSERVED_ONLY = (EvidenceType.OBSERVED_FACT,)
_OBSERVED_AND_INFERENCE = (
    EvidenceType.OBSERVED_FACT,
    EvidenceType.ANALYSIS_INFERENCE,
)
_ABM_SUPPORTED = (
    EvidenceType.OBSERVED_FACT,
    EvidenceType.ANALYSIS_INFERENCE,
    EvidenceType.MODEL_SIMULATION,
)

# Evidence admissibility is field-specific. A social observation can describe
# a customer segment but can never qualify a project as a comparable. Model
# simulation is limited to AD3 fields supported by ABM (product, MNL/WTP,
# household mix, and absorption) plus VA1 scenarios.
_REQUIREMENT_EVIDENCE_TYPES: dict[str, tuple[EvidenceType, ...]] = {
    "SC2.macro_indicators": _OBSERVED_ONLY,
    "SC2.competitors": _OBSERVED_ONLY,
    "SC2.customer_segments": (
        EvidenceType.OBSERVED_FACT,
        EvidenceType.SOCIAL_OBSERVATION,
    ),
    "SC2.positive_cases": _OBSERVED_AND_INFERENCE,
    "SC2.negative_cases": _OBSERVED_AND_INFERENCE,
    "AD1.option_1": _OBSERVED_AND_INFERENCE,
    "AD1.option_2": _OBSERVED_AND_INFERENCE,
    "AD1.option_3": _OBSERVED_AND_INFERENCE,
    "AD1.comparison_matrix": _OBSERVED_AND_INFERENCE,
    "AD2.recommended_option": _OBSERVED_AND_INFERENCE,
    "AD2.elimination_reasons": _OBSERVED_AND_INFERENCE,
    "AD2.validation_thresholds": _OBSERVED_AND_INFERENCE,
    "AD3.product_mix": _ABM_SUPPORTED,
    "AD3.area_segments": _ABM_SUPPORTED,
    "AD3.price_bands": _ABM_SUPPORTED,
    "AD3.sales_rhythm": _ABM_SUPPORTED,
    "AD4.site_plan": _OBSERVED_AND_INFERENCE,
    "AD4.floor_plans": _OBSERVED_AND_INFERENCE,
    "AD4.facade": _OBSERVED_AND_INFERENCE,
    "AD4.landscape": _OBSERVED_AND_INFERENCE,
    "AD4.show_area": _OBSERVED_AND_INFERENCE,
    "VA1.premium_factors": _ABM_SUPPORTED,
    "VA1.cost_value_chain": _ABM_SUPPORTED,
}
_REQUIREMENT_MAX_AGE_DAYS = {
    "SC2": 180,
    "AD1": 365,
    "AD2": 365,
    "AD3": 365,
    "AD4": 365,
    "VA1": 365,
}
_REQUIREMENT_DECISION_USE = {
    "SC2": "Establish market opportunity, customer, comparable, and case evidence.",
    "AD1": "Compare three options on one explicit evidence base.",
    "AD2": "Select the preferred option and define elimination and validation gates.",
    "AD3": "Translate the selected direction into product, area, price, and sales logic.",
    "AD4": "Test site, plan, facade, landscape, and show-area feasibility.",
    "VA1": "Test premium drivers against cost and value evidence.",
}


class RequirementAgent(BaseAgent):
    """Normalize project identity and fail closed on missing location scope."""

    @property
    def agent_id(self) -> str:
        return "requirement"

    @property
    def agent_name(self) -> str:
        return "Requirement Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.parameters.get("project_context", {}) or {}
        existing_data = task.parameters.get("existing_data", {}) or {}
        normalized = dict(ctx)

        city = normalized.get("city") or normalized.get("\u57ce\u5e02", "")
        if city:
            normalized["city"] = str(city).strip()

        project_type = normalized.get("project_type") or normalized.get("\u9879\u76ee\u7c7b\u578b", "")
        if project_type:
            normalized["project_type"] = str(project_type).strip()

        project_name = normalized.get("project_name") or normalized.get("\u9879\u76ee\u540d\u79f0", "")
        project_id = normalized.get("project_id") or f"PRJ-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        normalized["project_id"] = project_id
        if project_name:
            normalized["project_name"] = str(project_name).strip()

        base_date = normalized.get("base_date") or datetime.now().strftime("%Y-%m-%d")
        normalized["base_date"] = base_date

        if not normalized.get("city"):
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.WAITING,
                data={
                    "project_context": normalized,
                    "blocked": True,
                    "resolution_status": "human_input",
                    "missing_fields": ["city"],
                },
                errors=["city is required; research is blocked until human input is provided"],
            )

        decision_question = None
        sc1_data = existing_data.get("SC1") if isinstance(existing_data, dict) else None
        if isinstance(sc1_data, dict):
            decision_question = sc1_data.get("decision_question")
        if not decision_question:
            decision_question = self._compose_decision_question(normalized)

        evidence_boundary = normalized.get("evidence_boundary") or (
            "Allowed evidence is limited to traceable client-provided documents, public "
            "government records, market datasets, and comparable cases matching the "
            "project scope. This boundary defines admissible sources only; it does not "
            "claim that any evidence has been obtained or verified."
        )
        normalized["evidence_boundary"] = evidence_boundary

        warnings = []
        if not normalized.get("project_type"):
            warnings.append("project_type is missing; its applicability must be confirmed by human input")

        sc1_fields = {
            "project_id": project_id,
            "decision_question": decision_question,
            "evidence_boundary": evidence_boundary,
            "base_date": base_date,
        }
        profile_requested = (
            "input_profile" in task.parameters
            or "requested_level" in task.parameters
            or ctx.get("requested_level") is not None
        )
        analysis_profile = resolve_analysis_profile(
            task.parameters.get("input_profile") or normalized,
            requested_level=task.parameters.get("requested_level") or normalized.get("requested_level"),
        )
        if profile_requested:
            normalized["analysis_profile"] = analysis_profile
            if not analysis_profile["eligible"]:
                return AgentResult(
                    task_id=task.task_id,
                    success=False,
                    agent_id=self.agent_id,
                    status=AgentStatus.WAITING,
                    data={
                        "project_context": normalized,
                        "analysis_profile": analysis_profile,
                        "blocked": True,
                        "resolution_status": "human_input",
                        "missing_fields": ["address_or_coordinates"],
                    },
                    errors=["address or coordinates are required for Input 1/2/3 classification"],
                )
        requirement_graph = self._build_requirement_graph(
            normalized,
            analysis_profile if profile_requested else None,
        )

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "project_context": normalized,
                "sc1_fields": sc1_fields,
                "data_requirement_graph": requirement_graph,
                "data_requirements": list(requirement_graph["nodes"]),
                **({
                    "analysis_profile": analysis_profile,
                    "decision_scope": analysis_profile["decision_scope"],
                } if profile_requested else {}),
                "blocked": False,
                "resolution_status": "resolved",
            },
            warnings=warnings,
        )

    def _build_requirement_graph(
        self,
        ctx: dict[str, Any],
        analysis_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the serializable SC2-to-VA1 evidence requirement graph."""

        city = str(ctx.get("city") or "").strip()
        district = str(ctx.get("district") or "").strip()
        geography = "/".join(item for item in (city, district) if item)
        as_of = str(ctx.get("base_date") or "")
        nodes: list[dict[str, Any]] = []

        profile = analysis_profile
        if profile is None:
            active_order = ("SC2", "AD1", "AD2", "AD3", "AD4", "VA1")
            required_units = set(active_order)
            optional_units: set[str] = set()
        else:
            required_units = set(profile["required_units"])
            optional_units = set(profile["optional_units"])
            active_order = tuple(
                item for item in _REQUIREMENT_SECTION_ORDER
                if item in required_units | optional_units
            )
        active_units = set(active_order)
        for section_id in _REQUIREMENT_SECTION_ORDER:
            if section_id not in active_units:
                continue
            max_age_days = _REQUIREMENT_MAX_AGE_DAYS.get(section_id, 365)
            for field_name in SECTION_REQUIREMENTS[section_id]:
                minimum_sample = (
                    5
                    if section_id == "SC2" and field_name == "competitors"
                    else 1
                )
                decision_use = (
                    f"{_REQUIREMENT_DECISION_USE.get(section_id, 'Resolve the unit evidence requirement.')} "
                    f"Required field: {field_name}."
                )
                requirement = DataRequirement(
                    metric_id=f"{section_id}.{field_name}",
                    section_id=section_id,
                    field_name=field_name,
                    description=decision_use,
                    required=section_id in required_units,
                    evidence_types=_REQUIREMENT_EVIDENCE_TYPES.get(
                        f"{section_id}.{field_name}", _OBSERVED_AND_INFERENCE
                    ),
                    min_evidence_count=minimum_sample,
                    geography=geography,
                    as_of=as_of,
                    max_age_days=max_age_days,
                    metadata={
                        "purpose": decision_use,
                        "minimum_sample_size": minimum_sample,
                        "time_window": {
                            "as_of": as_of,
                            "lookback_days": max_age_days,
                        },
                        "depends_on_sections": list(
                            _REQUIREMENT_DEPENDENCIES.get(section_id, ())
                        ),
                    },
                )
                nodes.append(requirement.to_dict())

        edges = [
            {
                "from_section": dependency,
                "to_section": section_id,
                "constraint": "requires_upstream_section_result",
            }
            for section_id in _REQUIREMENT_SECTION_ORDER
            if section_id in active_units
            for dependency in _REQUIREMENT_DEPENDENCIES.get(section_id, ())
        ]
        return {
            "graph_id": "dds-evidence-requirements-v2" if profile else "dds-evidence-requirements-v1",
            "analysis_profile": profile,
            "section_order": list(active_order),
            "nodes": nodes,
            "edges": edges,
        }

    def _compose_decision_question(self, ctx: dict[str, Any]) -> str:
        city = ctx.get("city", "target city")
        project_type = ctx.get("project_type", "real estate")
        name = ctx.get("project_name", "the site")
        return (
            f"For {name} in {city}, should the {project_type} project proceed, and which "
            "product direction best balances value and risk within the verified evidence boundary?"
        )
