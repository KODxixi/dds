# ruff: noqa: F401,F403
"""V1 import-compatible wrapper for :mod:`dds.reporting.contracts`."""

try:
    from .contracts.evidence_contract import *
    from .contracts.evidence_contract import __all__
except ImportError:  # pragma: no cover - top-level V1 import style
    from contracts.evidence_contract import *
    from contracts.evidence_contract import __all__
