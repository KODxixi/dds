"""Decision-report and evidence-workbook output contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping, Sequence


class ReportEdition(StrEnum):
    DECISION_REPORT = "decision_report"
    EVIDENCE_WORKBOOK = "evidence_workbook"


def page_value_errors(page: Mapping[str, Any]) -> list[str]:
    """Return reader-facing value-gate failures for a decision page."""

    errors: list[str] = []
    title = str(page.get("title") or "").strip()
    decision_question = str(page.get("decision_question") or "").strip()
    takeaway = str(page.get("takeaway") or "").strip()
    blocks = page.get("blocks")
    source_refs = page.get("source_refs")
    decision_impact = str(
        page.get("decision_impact") or page.get("action") or ""
    ).strip()
    if not title:
        errors.append("title_missing")
    if not decision_question:
        errors.append("decision_question_missing")
    if not takeaway or takeaway == title:
        errors.append("decision_takeaway_missing")
    if (
        not isinstance(blocks, Sequence)
        or isinstance(blocks, (str, bytes))
        or not blocks
    ):
        errors.append("primary_evidence_or_design_missing")
    if (
        not isinstance(source_refs, Sequence)
        or isinstance(source_refs, (str, bytes))
        or not source_refs
    ):
        errors.append("source_refs_missing")
    if not decision_impact:
        errors.append("decision_impact_or_action_missing")
    return errors


def decision_pages(
    pages: Sequence[Mapping[str, Any]],
    *,
    blocking_page_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Keep meaningful pages plus explicitly decision-blocking gates."""

    blockers = {str(item) for item in blocking_page_ids}
    result: list[dict[str, Any]] = []
    for raw in pages:
        page = dict(raw)
        status = str(page.get("unit_status") or "")
        page_id = str(page.get("page_id") or "")
        if status in {"missing", "blocked"}:
            if page_id in blockers:
                page["layout"] = "gap"
                result.append(page)
            continue
        errors = page_value_errors(page)
        if not errors:
            page["value_gate"] = {"status": "passed", "errors": []}
            result.append(page)
    return result


__all__ = ["ReportEdition", "decision_pages", "page_value_errors"]
