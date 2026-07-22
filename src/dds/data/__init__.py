"""DDS data layer: collect evidence and make every missing value explicit.

The legacy orchestrator/fetcher/fallback API remains available.  Dataset
infrastructure is exported lazily so importing :mod:`dds.data` performs no
catalog scans, directory creation, or optional backend initialisation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dds.data.fallback import FallbackEngine
from dds.data.fetcher import DataFetcher
from dds.data.orchestrator import DataOrchestrator

if TYPE_CHECKING:
    from dds.data.catalog import DatasetCatalog
    from dds.data.evidence_store import EvidenceStore
    from dds.data.repository import DatasetRepository

_LAZY_EXPORTS = {
    "DatasetCatalog": ("dds.data.catalog", "DatasetCatalog"),
    "DatasetRepository": ("dds.data.repository", "DatasetRepository"),
    "EvidenceStore": ("dds.data.evidence_store", "EvidenceStore"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()).union(_LAZY_EXPORTS))


__all__ = [
    "DataOrchestrator",
    "DataFetcher",
    "FallbackEngine",
    "DatasetCatalog",
    "DatasetRepository",
    "EvidenceStore",
]
