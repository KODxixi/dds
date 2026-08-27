"""Compatibility entry point for the arch-front-html DDS bridge."""

import importlib.util
import sys
from pathlib import Path

_LEGACY = Path(__file__).resolve().parents[1] / "tools" / "v1-legacy" / "scripts"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

_path = _LEGACY / "build_project_report.py"
_spec = importlib.util.spec_from_file_location("_dds_v1_legacy_build_project_report", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load compatibility builder: {_path}")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

_NAMES = (
    "_validate_project_dir",
    "_raw_file_records",
    "_apply_evidence_families",
    "_build_manifest",
    "_write_stable_json",
    "_write_source_snapshot_metadata",
    "_build_research_request",
    "_freeze_report_seed_assets",
    "_build_evidence_package",
    "_validate_frozen_package",
    "compile_frozen_package",
    "evaluate_delivery_status",
)
for _name in _NAMES:
    globals()[_name] = getattr(_module, _name)


def __getattr__(name):
    """Forward newly requested private bridge helpers to the legacy engine."""
    return getattr(_module, name)

__all__ = list(_NAMES)
