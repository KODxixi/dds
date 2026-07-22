"""Section-domain compatibility exports.

Section data is implemented by :class:`dds.domain.models.SectionResult`; the
twelve-unit framework is implemented by :mod:`dds.contracts`.  Re-exporting
those objects here avoids a second, drifting framework definition.
"""

from dds.contracts import (
    FRAMEWORK_ID,
    FRAMEWORK_VERSION,
    REPORT_GROUPS,
    REPORT_UNITS,
    SECTION_ORDER,
    SECTION_REQUIREMENTS,
    VALID_SECTION_IDS,
)
from dds.domain.models import SectionResult


__all__ = [
    "FRAMEWORK_ID",
    "FRAMEWORK_VERSION",
    "REPORT_GROUPS",
    "REPORT_UNITS",
    "SECTION_ORDER",
    "SECTION_REQUIREMENTS",
    "VALID_SECTION_IDS",
    "SectionResult",
]
