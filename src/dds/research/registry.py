"""Single registration point for pluggable research sources.

Every research source is a self-describing, provenance-carrying unit.  Agents
and the product service discover and schedule sources through this registry
only; there is no ``if/elif`` source dispatch anywhere else in the product.

A source is added by implementing the :class:`ResearchSource` protocol and
registering it in :func:`build_source_registry`; it is removed by deleting one
registration (or disabling it via ``DDS_DISABLED_SOURCES``), never by editing
core dispatch code.
"""

from __future__ import annotations

import os
from typing import Any

from .external import ResearchSource, SourceSpec


DEFAULT_DISABLED_SOURCES_ENV = "DDS_DISABLED_SOURCES"


class SourceRegistry:
    """Order-preserving registry of research sources with an enable gate."""

    def __init__(self, disabled: set[str] | frozenset[str] | None = None) -> None:
        self._sources: dict[str, ResearchSource] = {}
        self._disabled: set[str] = set(disabled or ())

    def register(self, source: ResearchSource) -> None:
        source_id = source.source_id
        if source_id in self._sources:
            raise ValueError(f"source {source_id!r} is already registered")
        self._sources[source_id] = source

    def disable(self, source_id: str) -> None:
        if source_id not in self._sources:
            raise KeyError(f"unknown source {source_id!r}")
        self._disabled.add(source_id)

    def get(self, source_id: str) -> ResearchSource:
        return self._sources[source_id]

    def all(self) -> tuple[ResearchSource, ...]:
        """Every registered source, enabled or not, for UI visibility."""

        return tuple(self._sources.values())

    def enabled(self) -> tuple[ResearchSource, ...]:
        """Only sources the runtime should actually schedule."""

        return tuple(
            source
            for source_id, source in self._sources.items()
            if source_id not in self._disabled
        )

    def status(self) -> list[dict[str, Any]]:
        """Merged spec + availability + enabled flag, for the sources API."""

        statuses: list[dict[str, Any]] = []
        for source_id, source in self._sources.items():
            spec = getattr(source, "spec", None)
            spec_dict = spec.to_dict() if isinstance(spec, SourceSpec) else {}
            entry: dict[str, Any] = {**spec_dict, **source.availability()}
            entry["source_id"] = source_id
            entry["enabled"] = source_id not in self._disabled
            statuses.append(entry)
        return statuses


def build_source_registry() -> SourceRegistry:
    """Build the canonical registry from the single registered-source list.

    ``DDS_DISABLED_SOURCES`` is a comma-separated list of ``source_id`` values
    to turn off without touching core code.  Every source defaults to enabled.
    """

    from .external import CuratedListingDatabaseSource, TavilyResearchSource
    from .local import LandSource, MacroSource, TransactionSource
    from .volcengine import VolcengineDataSearchSource

    disabled = {
        token.strip()
        for token in os.getenv(DEFAULT_DISABLED_SOURCES_ENV, "").split(",")
        if token.strip()
    }
    registry = SourceRegistry(disabled=disabled)
    for source in (
        CuratedListingDatabaseSource(),
        LandSource(),
        MacroSource(),
        TransactionSource(),
        VolcengineDataSearchSource(),
        TavilyResearchSource(),
    ):
        registry.register(source)
    return registry


__all__ = [
    "DEFAULT_DISABLED_SOURCES_ENV",
    "SourceRegistry",
    "build_source_registry",
]
