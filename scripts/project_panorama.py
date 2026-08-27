"""Compatibility entry point for the arch-front-html DDS bridge."""

import importlib.util
import sys
from pathlib import Path

_LEGACY = Path(__file__).resolve().parents[1] / "tools" / "v1-legacy" / "scripts"
if str(_LEGACY) not in sys.path:
    sys.path.insert(0, str(_LEGACY))

_path = _LEGACY / "project_panorama.py"
_spec = importlib.util.spec_from_file_location("_dds_v1_legacy_project_panorama", _path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load compatibility panorama builder: {_path}")
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
for _name in dir(_module):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_module, _name)
