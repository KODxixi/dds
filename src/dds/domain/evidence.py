"""Evidence-domain compatibility exports.

No model is defined here.  ``dds.domain.models`` remains the single source of
truth for evidence records and requirements.
"""

from dds.domain.models import (
    DataRequirement,
    EvidenceRecord,
    EvidenceType,
    normalize_evidence_type,
)


__all__ = [
    "DataRequirement",
    "EvidenceRecord",
    "EvidenceType",
    "normalize_evidence_type",
]
