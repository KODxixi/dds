"""Compatibility entry point for the arch-front-html DDS bridge.

The current V2 reporting package does not yet expose the complete legacy
freeze-orchestrator surface required by the skill.  The repository's retained
read-only V4-compatible engine is used here as the adapter target.
"""

import importlib.util
import sys
from pathlib import Path

_LEGACY = Path(__file__).resolve().parents[1] / "tools" / "v1-legacy" / "scripts"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

_path = _LEGACY / "report_document.py"
_spec = importlib.util.spec_from_file_location("_dds_v1_legacy_report_document", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load compatibility renderer: {_path}")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
build_report_document = _module.build_report_document
sanitize_portable_value = _module.sanitize_portable_value

__all__ = ["build_report_document", "sanitize_portable_value"]
