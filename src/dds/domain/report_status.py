"""Report-run and quality-gate compatibility exports.

``ReportRun`` and resolution states are implemented by
``dds.domain.models``.  Gate result types and contract failures are
implemented by ``dds.contracts``.  Nothing is redefined in this module.
"""

from dds.contracts import ContractViolationError, GateResult, ValidationResult
from dds.domain.models import ReportRun, ResolutionStatus, ResolvedStatus


__all__ = [
    "ContractViolationError",
    "GateResult",
    "ReportRun",
    "ResolutionStatus",
    "ResolvedStatus",
    "ValidationResult",
]
