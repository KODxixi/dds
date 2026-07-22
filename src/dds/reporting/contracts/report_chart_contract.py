"""Deterministic DDS chart and page visual-language contract.

The public demo used the letters A/B/C for selection.  Production payloads use
semantic names so they cannot be confused with design option 1/2/3.  Routing is
renderer-neutral and fails soft: unsupported chart types remain readable and
auditable instead of silently becoming a different graphic.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


CHART_CONTRACT_VERSION = "dds.chart-spec/1.1"

DECISION_NARRATIVE = "decision_narrative"
EVIDENCE_AUDIT = "evidence_audit"
SPATIAL_DIAGRAM = "spatial_diagram"
VISUAL_LANGUAGES = {
    DECISION_NARRATIVE,
    EVIDENCE_AUDIT,
    SPATIAL_DIAGRAM,
}

_VISUAL_LANGUAGE_ALIASES = {
    "a": DECISION_NARRATIVE,
    "main": DECISION_NARRATIVE,
    "primary": DECISION_NARRATIVE,
    "decision": DECISION_NARRATIVE,
    "decision_narrative": DECISION_NARRATIVE,
    "b": EVIDENCE_AUDIT,
    "evidence": EVIDENCE_AUDIT,
    "audit": EVIDENCE_AUDIT,
    "evidence_audit": EVIDENCE_AUDIT,
    "c": SPATIAL_DIAGRAM,
    "spatial": SPATIAL_DIAGRAM,
    "diagram": SPATIAL_DIAGRAM,
    "spatial_design": SPATIAL_DIAGRAM,
    "spatial_diagram": SPATIAL_DIAGRAM,
}

# Types admitted by the canonical ChartSpec contract.  Renderers may still use
# ``fallback_policy`` when they do not yet provide a specialized presentation.
SUPPORTED_CHART_TYPES = {
    "ranked_bar",
    "diverging_bar",
    "combo_bar_line",
    "time_series",
    "scatter_bubble",
    "stacked_bar",
    "waterfall",
    "tornado",
    "cashflow_curve",
    "heatmap",
    "funnel",
    "radar",
    "risk_matrix",
    "distribution",
    "geo_layer",
}
ARGUMENT_FIELDS = (
    "claim_id",
    "dataset_ref",
    "dimensions",
    "measures",
    "transform",
    "annotations",
    "decision_message",
    "inference_status",
)
_CHART_TYPE_ALIASES = {
    "bar": "ranked_bar",
    "horizontal_bar": "ranked_bar",
    "rankedbar": "ranked_bar",
    "divergingbar": "diverging_bar",
}

_SPATIAL_CHAPTERS = {
    "site",
    "gis",
    "concept",
    "concept_options",
    "masterplan",
    "architecture",
    "landscape",
    "case_transfer",
    "archlib_cases",
    "traditional_spatial_culture",
}
_SPATIAL_MARKERS = {
    "fire",
    "fire_safety",
    "civil_defense",
    "human_defense",
    "garage",
    "parking",
    "vertical",
    "vertical_design",
    "wind",
    "flood",
    "facade",
    "earthwork",
    "daylight",
    "structure",
    "map",
    "site_plan",
    "masterplan",
    "spatial_diagram",
    "spatial_section",
    "section",
    "elevation",
    "unit_plan",
    "circulation",
    "sunlight",
    "sun_path",
    "view_corridor",
    "noise_map",
    "landscape",
    "case_mechanism",
}


def _marker(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


def normalize_visual_language(value: Any) -> str | None:
    """Return a semantic visual language or ``None`` for an unknown value."""

    return _VISUAL_LANGUAGE_ALIASES.get(_marker(value))


def _block_types(page: Mapping[str, Any]) -> set[str]:
    blocks = page.get("blocks")
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes, bytearray)):
        return set()
    return {
        _marker(block.get("type") or block.get("layout"))
        for block in blocks
        if isinstance(block, Mapping)
    }


def resolve_page_visual_language(page: Mapping[str, Any]) -> str:
    """Route a page to A-main, B-evidence, or C-spatial semantics.

    Evidence appendices are deliberately first and cannot be overridden.
    Structural spatial signals are second and likewise cannot be restyled by
    an explicit page preference.  Only non-spatial narrative pages may select
    A, B, or C explicitly.
    """

    if (
        _marker(page.get("story_role")) == "evidence_appendix"
        or _marker(page.get("appendix_policy")) == "evidence"
    ):
        return EVIDENCE_AUDIT

    chapter = _marker(page.get("chapter_id"))
    layout = _marker(page.get("layout"))
    visual_evidence = _marker(page.get("visual_evidence"))
    if (
        chapter in _SPATIAL_CHAPTERS
        or chapter in _SPATIAL_MARKERS
        or any(token in chapter for token in ("architecture", "landscape", "spatial"))
        or layout in _SPATIAL_MARKERS
        or visual_evidence in _SPATIAL_MARKERS
        or bool(_block_types(page) & _SPATIAL_MARKERS)
    ):
        return SPATIAL_DIAGRAM

    explicit = normalize_visual_language(page.get("visual_language"))
    if explicit:
        return explicit

    return DECISION_NARRATIVE


def _refs(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        candidates = list(value)
    else:
        candidates = []
    result: list[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_chart_spec(
    candidate: Mapping[str, Any],
    *,
    grammar: str,
    page_id: str,
    chart_index: int,
    page_source_refs: Any = None,
    page_confidence: Any = None,
) -> dict[str, Any]:
    """Normalize one ChartSpec without inventing evidence or numeric values."""

    normalized = deepcopy(dict(candidate))
    requested_type = _marker(normalized.get("type")) or "unspecified"
    canonical_type = _CHART_TYPE_ALIASES.get(requested_type, requested_type)
    supported = canonical_type in SUPPORTED_CHART_TYPES
    source_refs = _refs(normalized.get("source_refs")) or _refs(page_source_refs)

    normalized["chart_id"] = str(
        normalized.get("chart_id") or f"{page_id}-chart-{chart_index + 1}"
    )
    normalized["contract_version"] = CHART_CONTRACT_VERSION
    normalized["grammar"] = grammar if grammar in VISUAL_LANGUAGES else DECISION_NARRATIVE
    normalized["type"] = canonical_type if supported else "unsupported"
    normalized["render_status"] = "ready" if supported else "unsupported"
    if not supported:
        normalized["requested_type"] = requested_type
    normalized["source_refs"] = source_refs
    if "confidence" not in normalized and page_confidence is not None:
        normalized["confidence"] = deepcopy(page_confidence)
    normalized.setdefault("evidence_status", "registered" if source_refs else "unverified")
    normalized.setdefault(
        "a11y_summary",
        str(
            normalized.get("alt_text")
            or normalized.get("description")
            or normalized.get("title")
            or "图表证据；请结合本页来源和置信度阅读。"
        ),
    )
    normalized.setdefault(
        "fallback_policy",
        "render_evidence_rows"
        if isinstance(normalized.get("series"), Sequence)
        and not isinstance(normalized.get("series"), (str, bytes, bytearray))
        else "render_readable_notice",
    )
    if supported:
        normalized["integrity"] = {
            "has_sources": bool(source_refs),
            "has_units": bool(normalized.get("units")),
            "has_time_window": bool(normalized.get("time_window")),
            "zero_baseline_required": canonical_type
            in {"ranked_bar", "stacked_bar"},
        }
    return normalized


__all__ = [
    "CHART_CONTRACT_VERSION",
    "ARGUMENT_FIELDS",
    "DECISION_NARRATIVE",
    "EVIDENCE_AUDIT",
    "SPATIAL_DIAGRAM",
    "SUPPORTED_CHART_TYPES",
    "VISUAL_LANGUAGES",
    "normalize_chart_spec",
    "normalize_visual_language",
    "resolve_page_visual_language",
]
