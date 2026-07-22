"""Decision-resolution compatibility exports.

The resolution vocabulary and field model are canonical in
``dds.domain.models``.  This module is deliberately an alias-only public
surface.
"""

from dds.domain.models import (
    EvidenceResolutionStatus,
    ResolutionStatus,
    ResolvedField,
    ResolvedStatus,
    normalize_resolved_status,
)


__all__ = [
    "EvidenceResolutionStatus",
    "ResolutionStatus",
    "ResolvedField",
    "ResolvedStatus",
    "normalize_resolved_status",
]
