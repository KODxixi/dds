"""Build the internal DDS project panorama used to choose report depth."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

try:
    from .report_structure_contract import VALID_SECTION_IDS
except ImportError:  # pragma: no cover - existing scripts import convention
    from report_structure_contract import VALID_SECTION_IDS


PANORAMA_SCHEMA = "dds.project-panorama/1.0"

STAGE_KEYWORDS = {
    "investment_screening": ("拿地", "投拓", "投资筛选", "投委会"),
    "market_planning": ("市场", "客群", "竞品", "产品定位"),
    "concept_planning": ("概念策划", "功能策划", "业态策划"),
    "architectural_concept": ("建筑概念", "总图", "体块", "方案设计"),
}

DEFAULT_UNITS = {
    "investment_screening": ("SC1", "SC2", "SC3", "VA2", "VA3", "CS"),
    "market_planning": ("SC1", "SC2", "AD3", "VA1", "VA3", "CS"),
    "concept_planning": (
        "SC1",
        "SC2",
        "SC3",
        "AD2",
        "AD3",
        "AD4",
        "VA1",
        "VA3",
        "CS",
    ),
    "architectural_concept": (
        "SC1",
        "SC2",
        "SC3",
        "AD1",
        "AD2",
        "AD3",
        "AD4",
        "VA1",
        "VA3",
        "CS",
    ),
}


def build_project_panorama(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize project intent into the internal adaptive-report gate."""
    goal = str(payload.get("goal") or "").strip()
    stage = next(
        (
            name
            for name, keywords in STAGE_KEYWORDS.items()
            if any(keyword in goal for keyword in keywords)
        ),
        "concept_planning",
    )

    geometry = (
        payload.get("geometry")
        if isinstance(payload.get("geometry"), Mapping)
        else {}
    )
    if geometry.get("cad"):
        fidelity = "cad_grade"
    elif geometry.get("boundary"):
        fidelity = "boundary"
    elif geometry.get("location"):
        fidelity = "location"
    else:
        fidelity = "none"

    questions: list[dict[str, str]] = []
    if not str(payload.get("audience") or "").strip():
        questions.append(
            {
                "field": "audience",
                "question": "这份报告最终由谁决策和使用？",
                "impact": "决定篇章密度与专业深度",
            }
        )
    if not str(payload.get("decision_priority") or "").strip():
        questions.append(
            {
                "field": "decision_priority",
                "question": "本次优先控制安全边界、货值、去化、空间产品还是品牌表达？",
                "impact": "决定主线与图表预算",
            }
        )
    if stage == "architectural_concept" and fidelity in {"none", "location"}:
        questions.append(
            {
                "field": "geometry_source",
                "question": "精确总图是否有可确认的红线、CAD 或坐标边界？",
                "impact": "决定能否绘制比例总图",
            }
        )

    requested = [str(unit) for unit in payload.get("requested_units") or []]
    unknown = [unit for unit in requested if unit not in VALID_SECTION_IDS]
    if unknown:
        raise ValueError(f"unknown requested units: {unknown}")
    selected_units = set(DEFAULT_UNITS[stage]).union(requested)
    required_units = [
        unit for unit in VALID_SECTION_IDS if unit in selected_units
    ]

    return {
        "schema_version": PANORAMA_SCHEMA,
        "project_id": str(payload.get("project_id") or "").strip(),
        "as_of": str(payload.get("as_of") or "").strip(),
        "project_stage": stage,
        "geometry_fidelity": fidelity,
        "required_units": required_units,
        "questions": questions,
        "status": "awaiting_user_input" if questions else "confirmed",
    }


__all__ = [
    "DEFAULT_UNITS",
    "PANORAMA_SCHEMA",
    "STAGE_KEYWORDS",
    "build_project_panorama",
]
