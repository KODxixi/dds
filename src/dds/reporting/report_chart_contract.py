# ruff: noqa: F401,F403
"""V1 import-compatible wrapper for the chart contract."""

try:
    from .contracts.report_chart_contract import *
    from .contracts.report_chart_contract import __all__
except ImportError:  # pragma: no cover - top-level V1 import style
    from contracts.report_chart_contract import *
    from contracts.report_chart_contract import __all__
