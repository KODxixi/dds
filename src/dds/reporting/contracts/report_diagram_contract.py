"""Canonical DDS ConceptDiagramSpec normalization and precision boundaries."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Real
from typing import Any


DIAGRAM_CONTRACT_VERSION = "dds.concept-diagram-spec/1.0"
SUPPORTED_DIAGRAM_TYPES = {
    "site_constraints",
    "program_zoning",
    "adjacency",
    "circulation",
    "landscape_axis",
    "massing_sequence",
    "height_interface",
    "masterplan",
    "phasing",
    "spatial_section",
}

_SCHEMATIC_LABEL = "非比例概念关系图"
_SEQUENCE_FIELDS = (
    "program_blocks",
    "relations",
    "circulation",
    "constraints",
    "annotations",
    "legend",
)


def _marker(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


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


def _is_coordinate(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _has_valid_boundary(site_geometry: Mapping[str, Any]) -> bool:
    boundary = site_geometry.get("boundary")
    if not isinstance(boundary, Sequence) or isinstance(
        boundary, (str, bytes, bytearray)
    ):
        return False
    if len(boundary) < 3:
        return False
    return all(
        isinstance(point, Sequence)
        and not isinstance(point, (str, bytes, bytearray))
        and len(point) >= 2
        and _is_coordinate(point[0])
        and _is_coordinate(point[1])
        for point in boundary
    )


def normalize_diagram_spec(
    candidate: Mapping[str, Any],
    *,
    page_id: str,
    diagram_index: int,
) -> dict[str, Any]:
    """Normalize a diagram without overstating geometry precision or provenance.

    Coordinate-ready precision requires both a valid polygon-like boundary and
    at least one source reference.  Every other supported diagram is explicitly
    schematic and carries a mandatory non-scale label.
    """

    result = deepcopy(dict(candidate))
    requested_type = _marker(result.get("diagram_type")) or "unspecified"
    supported = requested_type in SUPPORTED_DIAGRAM_TYPES
    geometry = (
        deepcopy(dict(result.get("site_geometry")))
        if isinstance(result.get("site_geometry"), Mapping)
        else {}
    )
    source_refs = _refs(result.get("source_refs"))
    has_boundary = _has_valid_boundary(geometry)
    source_bound_geometry = has_boundary and bool(source_refs)

    result["diagram_id"] = str(
        result.get("diagram_id") or f"{page_id}-diagram-{diagram_index + 1}"
    )
    result["contract_version"] = DIAGRAM_CONTRACT_VERSION
    result["diagram_type"] = requested_type if supported else "unsupported"
    if not supported:
        result["requested_type"] = requested_type
    result.setdefault("claim_id", "")
    result["site_geometry"] = geometry
    for field in _SEQUENCE_FIELDS:
        result.setdefault(field, [])
    result["source_refs"] = source_refs
    result["source_status"] = "registered" if source_refs else "unverified"
    result["integrity"] = {
        "has_sources": bool(source_refs),
        "has_site_boundary": has_boundary,
    }

    if source_bound_geometry:
        result["precision_status"] = "source_geometry"
        result.pop("required_label", None)
    else:
        result["precision_status"] = "conceptual_not_to_scale"
        result["required_label"] = _SCHEMATIC_LABEL

    if not supported:
        result["render_status"] = "unsupported"
    elif source_bound_geometry:
        result["render_status"] = "ready"
    else:
        result["render_status"] = "schematic_only"

    return result


__all__ = [
    "DIAGRAM_CONTRACT_VERSION",
    "SUPPORTED_DIAGRAM_TYPES",
    "normalize_diagram_spec",
]
