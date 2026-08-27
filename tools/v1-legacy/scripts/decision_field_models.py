"""Core data model helpers for the DDS Decision Field."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


PROJECT_STATES = {
    "briefing",
    "ready_for_analysis",
    "analyzing",
    "decision_ready",
    "reporting",
    "completed",
    "error_recoverable",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_project(
    location: dict[str, Any],
    *,
    project_id: str | None = None,
    owner_id: str = "local",
) -> dict[str, Any]:
    _validate_location(location)
    normalized_owner = str(owner_id or "").strip()
    if not normalized_owner:
        raise ValueError("owner_id is required")
    created_at = now_iso()
    return {
        "id": project_id or f"project_{uuid4().hex}",
        "owner_id": normalized_owner,
        "version": 1,
        "revision": 1,
        "state": "briefing",
        "location": deepcopy(location),
        "brief": {
            "primary_goal": None,
            "project_stage": None,
            "hard_constraints": [],
            "far": None,
            "expected_price": None,
            "vision": None,
        },
        "brief_answered": [],
        "brief_skipped": [],
        "completeness": 0.0,
        "evidence_gaps": [],
        "scene_bookmarks": [],
        "analysis_job_id": None,
        "analysis_context": None,
        "decision_scenarios": [],
        "selected_scenario": None,
        "scenario_selections": [],
        "idempotency_records": {},
        "decision_revisions": [],
        "report": None,
        "last_error": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def transition(project: dict[str, Any], state: str) -> dict[str, Any]:
    if state not in PROJECT_STATES:
        raise ValueError(f"unknown project state: {state}")
    project["state"] = state
    project["updated_at"] = now_iso()
    return project


def _validate_location(location: dict[str, Any]) -> None:
    if not isinstance(location, dict):
        raise ValueError("location must be an object")
    for system in ("wgs84", "gcj02"):
        point = location.get(system)
        if not isinstance(point, dict):
            raise ValueError(f"location.{system} is required")
        try:
            lng = float(point["lng"])
            lat = float(point["lat"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"location.{system} must contain numeric lng/lat") from exc
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            raise ValueError(f"location.{system} is outside coordinate bounds")
