"""Compatibility fetcher for explicitly configured, traceable data providers.

The fetcher performs no discovery and ships with no synthetic benchmark
sources. A missing or untraceable provider result remains a visible gap for the
orchestrator to resolve.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from inspect import isawaitable
import logging
from typing import Any, TypeAlias

from dds.contracts import EvidenceRecord, FieldOrigin

logger = logging.getLogger(__name__)

ProviderResult: TypeAlias = (
    EvidenceRecord
    | tuple[Any | None, FieldOrigin | Mapping[str, Any] | None]
    | Mapping[str, Any]
    | None
)
DataProvider: TypeAlias = Callable[..., ProviderResult | Awaitable[ProviderResult]]


class DataFetcher:
    """Query only providers explicitly supplied by the caller.

    Provider order is the insertion order of the providers mapping. Results are
    accepted only when they carry an evidence reference or a concrete source
    locator; labels such as real are not treated as provenance.
    """

    def __init__(
        self,
        city: str = "",
        project_type: str = "",
        data_dir: str = "",
        *,
        providers: Mapping[str, DataProvider | Any] | None = None,
    ) -> None:
        self.city = city
        self.project_type = project_type
        self.data_dir = data_dir
        self._providers: dict[str, DataProvider | Any] = {}
        for raw_name, provider in dict(providers or {}).items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError("provider names must not be empty")
            target = getattr(provider, "fetch", provider)
            if not callable(target):
                raise TypeError(f"provider {name!r} must be callable or expose fetch()")
            self._providers[name] = provider

    @property
    def provider_names(self) -> tuple[str, ...]:
        """Return the explicit provider order without implying a built-in source."""

        return tuple(self._providers)

    async def fetch(
        self,
        section_id: str,
        field: str,
        context: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        """Return the first traceable provider result, otherwise a missing tuple."""

        context_payload = dict(context or {})
        city = str(context_payload.get("city") or self.city)
        project_type = str(context_payload.get("project_type") or self.project_type)

        for provider_name, provider in self._providers.items():
            target = getattr(provider, "fetch", provider)
            try:
                candidate = target(
                    section_id=section_id,
                    field=field,
                    city=city,
                    project_type=project_type,
                    context=dict(context_payload),
                )
                if isawaitable(candidate):
                    candidate = await candidate
            except Exception as exc:
                logger.warning("Provider [%s] failed: %s", provider_name, exc)
                continue

            accepted = self._validated_result(candidate)
            if accepted is not None:
                logger.debug(
                    "Provider [%s] returned traceable evidence for [%s]",
                    provider_name,
                    field,
                )
                return accepted
            if not self._is_missing(candidate):
                logger.warning(
                    "Provider [%s] returned untraceable data for [%s]; treated as missing",
                    provider_name,
                    field,
                )

        return None, None

    @staticmethod
    def _validated_result(candidate: Any) -> ProviderResult:
        if isinstance(candidate, EvidenceRecord):
            return candidate if candidate.has_traceable_source else None

        if isinstance(candidate, tuple) and len(candidate) == 2:
            value, origin = candidate
            if value is None or origin is None:
                return None
            if isinstance(origin, Mapping):
                try:
                    origin = FieldOrigin(**dict(origin))
                except (TypeError, ValueError):
                    return None
            if isinstance(origin, FieldOrigin) and origin.is_evidence_backed:
                return value, origin
            return None

        if isinstance(candidate, Mapping) and candidate.get("found"):
            evidence_refs = [
                str(item)
                for item in (candidate.get("evidence_refs") or [])
                if str(item)
            ]
            source_id = str(
                candidate.get("source_id") or candidate.get("source") or ""
            ).strip()
            source_locator = str(
                candidate.get("source_ref") or candidate.get("source_hash") or ""
            ).strip()
            if evidence_refs or (source_id and source_locator):
                return dict(candidate)
        return None

    @staticmethod
    def _is_missing(candidate: Any) -> bool:
        if candidate is None:
            return True
        if isinstance(candidate, tuple) and len(candidate) == 2:
            return candidate[0] is None
        if isinstance(candidate, Mapping):
            return not bool(candidate.get("found"))
        return False


__all__ = ["DataFetcher", "DataProvider", "ProviderResult"]
