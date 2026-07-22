# ruff: noqa: F401,F403
"""V1 import-compatible wrapper for the offline cinematic renderer."""

try:
    from .renderer import *
    from .renderer import __all__
except ImportError:  # pragma: no cover - top-level V1 import style
    from renderer import *
    from renderer import __all__
