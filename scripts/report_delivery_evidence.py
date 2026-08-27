"""Compatibility entry point for the arch-front-html DDS bridge."""

import importlib.util
import sys
from pathlib import Path

_LEGACY = Path(__file__).resolve().parents[1] / "tools" / "v1-legacy" / "scripts"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

_path = _LEGACY / "report_delivery_evidence.py"
_spec = importlib.util.spec_from_file_location("_dds_v1_legacy_report_delivery_evidence", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load compatibility evidence validator: {_path}")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
require_report_delivery_validation = _module.require_report_delivery_validation

__all__ = ["require_report_delivery_validation"]
