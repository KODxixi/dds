"""User-material intake that never upgrades narrative text to observed fact."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from dds.data.catalog import hash_file
from dds.domain import EvidenceRecord, EvidenceType


class UserMaterialAdapter:
    TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json"}

    def __init__(self, material_root: Path) -> None:
        self.material_root = material_root.resolve()

    def collect(self, paths: Iterable[Path]) -> tuple[EvidenceRecord, ...]:
        evidence: list[EvidenceRecord] = []
        for candidate in paths:
            path = candidate.resolve()
            try:
                path.relative_to(self.material_root)
            except ValueError as exc:
                raise ValueError(f"material path escapes configured root: {path}") from exc
            if not path.is_file():
                raise FileNotFoundError(path)
            if path.suffix.lower() not in self.TEXT_SUFFIXES:
                raise ValueError(f"unsupported user material type: {path.suffix}")
            content = path.read_text(encoding="utf-8")
            source_hash = hash_file(path)
            evidence.append(
                EvidenceRecord(
                    evidence_id=f"user-material:{source_hash[:24]}",
                    metric_id="user_material.narrative",
                    value=content,
                    unit="",
                    evidence_type=EvidenceType.SOCIAL_OBSERVATION,
                    source_id="user_provided_material",
                    source_ref=path.as_uri(),
                    source_hash=source_hash,
                    observed_at=None,
                    as_of=None,
                    geography="",
                    method="verbatim local material intake",
                    sample_size=1,
                    limitations=[
                        "User-provided narrative is not independently verified and must not be labelled observed_fact."
                    ],
                    confidence=None,
                )
            )
        return tuple(evidence)


__all__ = ["UserMaterialAdapter"]
