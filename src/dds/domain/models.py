"""Truth-preserving domain models used across DDS data and report stages.

The models deliberately distinguish a structurally present field from an
evidence-backed, decision-ready field.  Unknown values are first-class states;
they are never replaced with plausible-looking synthetic benchmarks.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Mapping


class EvidenceType(str, Enum):
    """Closed vocabulary for the nature of an evidence item."""

    OBSERVED_FACT = "observed_fact"
    SOCIAL_OBSERVATION = "social_observation"
    ANALYSIS_INFERENCE = "analysis_inference"
    MODEL_SIMULATION = "model_simulation"
    TRADITIONAL_INTERPRETATION = "traditional_interpretation"


class ResolvedStatus(str, Enum):
    """Resolution state of a field, section, or report."""

    RESOLVED = "resolved"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    HUMAN_INPUT = "human_input"
    NOT_APPLICABLE = "not_applicable"


class EvidenceResolutionStatus(str, Enum):
    """Fail-closed semantic outcome of resolving a metric's evidence."""

    VERIFIED = "verified"
    ESTIMATED = "estimated"
    SIMULATED = "simulated"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"
    STALE = "stale"


# Early callers used the longer name.  Keep it as a true alias so equality,
# isinstance checks, and serialisation remain identical.
ResolutionStatus = ResolvedStatus


def normalize_evidence_type(
    value: EvidenceType | str | None,
    default: EvidenceType | str = EvidenceType.ANALYSIS_INFERENCE,
) -> EvidenceType:
    """Return a valid :class:`EvidenceType` without inventing a new type."""

    if isinstance(value, EvidenceType):
        return value
    try:
        return EvidenceType(str(value or "").strip().lower())
    except ValueError:
        if isinstance(default, EvidenceType):
            return default
        try:
            return EvidenceType(str(default).strip().lower())
        except ValueError:
            return EvidenceType.ANALYSIS_INFERENCE


_STATUS_ALIASES = {
    "verified": ResolvedStatus.RESOLVED,
    "complete": ResolvedStatus.RESOLVED,
    "ready": ResolvedStatus.RESOLVED,
    "unresolved": ResolvedStatus.UNKNOWN,
    "missing": ResolvedStatus.UNKNOWN,
    "human_input_required": ResolvedStatus.HUMAN_INPUT,
    "needs_human_input": ResolvedStatus.HUMAN_INPUT,
    "n/a": ResolvedStatus.NOT_APPLICABLE,
    "na": ResolvedStatus.NOT_APPLICABLE,
}


def normalize_resolved_status(
    value: ResolvedStatus | str | None,
    default: ResolvedStatus | str = ResolvedStatus.UNKNOWN,
) -> ResolvedStatus:
    """Normalise legacy status spellings to the closed resolution vocabulary."""

    if isinstance(value, ResolvedStatus):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in _STATUS_ALIASES:
        return _STATUS_ALIASES[normalized]
    try:
        return ResolvedStatus(normalized)
    except ValueError:
        if isinstance(default, ResolvedStatus):
            return default
        try:
            return ResolvedStatus(str(default).strip().lower())
        except ValueError:
            return ResolvedStatus.UNKNOWN


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    return [value]


def _serialise(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _serialise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialise(item) for item in value]
    return value


@dataclass
class ProjectContext:
    """Stable project identity and evidence-scoping context."""

    project_id: str = ""
    project_name: str = ""
    city: str = ""
    district: str = ""
    project_type: str = ""
    decision_question: str = ""
    evidence_boundary: str = ""
    base_date: date | datetime | str | None = None
    geography: str = ""
    assumptions: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.assumptions = [str(item) for item in _as_list(self.assumptions)]
        self.parameters = dict(self.parameters or {})
        self.metadata = dict(self.metadata or {})
        self.extra = dict(self.extra or {})
        if not self.geography:
            self.geography = self.city

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "ProjectContext" | None) -> "ProjectContext":
        if isinstance(value, cls):
            return value
        payload = dict(value or {})
        known = {item.name for item in fields(cls)}
        extras = dict(payload.pop("extra", {}) or {})
        extras.update({key: payload.pop(key) for key in list(payload) if key not in known})
        payload["extra"] = extras
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            item.name: _serialise(getattr(self, item.name))
            for item in fields(self)
            if item.name != "extra"
        }
        payload.update(
            {
                key: value
                for key, value in _serialise(self.extra).items()
                if key not in payload
            }
        )
        return payload

    @property
    def target_geography(self) -> str:
        return self.geography or self.city


@dataclass
class DataRequirement:
    """Evidence requirement for a single report metric/field."""

    metric_id: str = ""
    section_id: str = ""
    field_name: str = ""
    description: str = ""
    required: bool = True
    unit: str = ""
    evidence_types: tuple[EvidenceType | str, ...] = field(default_factory=tuple)
    min_evidence_count: int = 1
    geography: str = ""
    as_of: date | datetime | str | None = None
    max_age_days: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.field_name:
            self.field_name = self.metric_id.rsplit(".", 1)[-1]
        self.evidence_types = tuple(
            normalize_evidence_type(item) for item in (self.evidence_types or ())
        )
        self.min_evidence_count = max(0, int(self.min_evidence_count or 0))
        self.metadata = dict(self.metadata or {})

    @property
    def field(self) -> str:
        """Compatibility alias used by early data engines."""

        return self.field_name

    @property
    def allowed_evidence_types(self) -> tuple[EvidenceType | str, ...]:
        """Compatibility alias for the closed evidence type constraint."""

        return self.evidence_types

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "DataRequirement") -> "DataRequirement":
        if isinstance(value, cls):
            return value
        payload = dict(value)
        if "field" in payload and "field_name" not in payload:
            payload["field_name"] = payload.pop("field")
        if "allowed_evidence_types" in payload and "evidence_types" not in payload:
            payload["evidence_types"] = payload.pop("allowed_evidence_types")
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialise(getattr(self, item.name)) for item in fields(self)}


@dataclass
class EvidenceRecord:
    """Auditable evidence with explicit provenance and observation scope."""

    evidence_id: str = ""
    metric_id: str = ""
    value: Any = None
    unit: str = ""
    evidence_type: EvidenceType | str = EvidenceType.OBSERVED_FACT
    source_id: str = ""
    source_ref: str = ""
    source_hash: str = ""
    observed_at: date | datetime | str | None = None
    as_of: date | datetime | str | None = None
    geography: str = ""
    method: str = ""
    sample_size: int | float | None = None
    limitations: list[str] = field(default_factory=list)
    confidence: dict[str, Any] | float | None = None

    def __post_init__(self) -> None:
        self.evidence_type = normalize_evidence_type(self.evidence_type)
        self.limitations = [str(item) for item in _as_list(self.limitations)]
        if isinstance(self.confidence, Mapping):
            self.confidence = dict(self.confidence)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "EvidenceRecord") -> "EvidenceRecord":
        if isinstance(value, cls):
            return value
        return cls(**dict(value))

    @property
    def effective_at(self) -> date | datetime | str | None:
        return self.observed_at or self.as_of

    @property
    def has_traceable_source(self) -> bool:
        """A label alone is not provenance; a reference or hash is required."""

        return bool(
            str(self.source_id or "").strip()
            and (str(self.source_ref or "").strip() or str(self.source_hash or "").strip())
        )

    @property
    def is_benchmark(self) -> bool:
        haystack = " ".join(
            [self.metric_id, self.source_id, self.source_ref, self.method]
        ).lower()
        return any(token in haystack for token in ("benchmark", "基准", "平均值"))

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialise(getattr(self, item.name)) for item in fields(self)}


@dataclass
class ResolvedField:
    """A field value plus the evidence and assumptions used to resolve it."""

    status: ResolvedStatus | str = ResolvedStatus.UNKNOWN
    value: Any = None
    evidence_refs: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    confidence: dict[str, Any] | float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        self.status = normalize_resolved_status(self.status)
        self.evidence_refs = [str(item) for item in _as_list(self.evidence_refs) if str(item)]
        self.assumptions = [str(item) for item in _as_list(self.assumptions)]
        if isinstance(self.confidence, Mapping):
            self.confidence = dict(self.confidence)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "ResolvedField") -> "ResolvedField":
        if isinstance(value, cls):
            return value
        return cls(**dict(value))

    @property
    def is_resolved(self) -> bool:
        return self.status in (ResolvedStatus.RESOLVED, ResolvedStatus.NOT_APPLICABLE)

    @property
    def needs_human_input(self) -> bool:
        return self.status == ResolvedStatus.HUMAN_INPUT

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialise(getattr(self, item.name)) for item in fields(self)}

    def __str__(self) -> str:
        if self.value is not None:
            return str(self.value)
        return self.reason or self.status.value


@dataclass
class SectionResult:
    """Truth contract output for one DDS decision unit."""

    section_id: str = ""
    data: dict[str, ResolvedField | Any] = field(default_factory=dict)
    conclusions: list[Any] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    counter_evidence: list[Any] = field(default_factory=list)
    gaps: list[Any] = field(default_factory=list)
    actions: list[Any] = field(default_factory=list)
    confidence: dict[str, Any] | float | None = None
    status: ResolvedStatus | str = ResolvedStatus.PARTIAL

    def __post_init__(self) -> None:
        self.data = dict(self.data or {})
        self.conclusions = _as_list(self.conclusions)
        self.evidence_refs = [str(item) for item in _as_list(self.evidence_refs) if str(item)]
        self.assumptions = [str(item) for item in _as_list(self.assumptions)]
        self.counter_evidence = _as_list(self.counter_evidence)
        self.gaps = _as_list(self.gaps)
        self.actions = _as_list(self.actions)
        self.status = normalize_resolved_status(self.status, ResolvedStatus.PARTIAL)
        if isinstance(self.confidence, Mapping):
            self.confidence = dict(self.confidence)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "SectionResult") -> "SectionResult":
        if isinstance(value, cls):
            return value
        payload = dict(value)
        payload["data"] = {
            key: (
                ResolvedField.from_mapping(item)
                if isinstance(item, Mapping) and "status" in item
                else item
            )
            for key, item in dict(payload.get("data", {})).items()
        }
        return cls(**payload)

    def field_resolution(self, field_name: str) -> ResolvedField:
        value = self.data.get(field_name)
        if isinstance(value, ResolvedField):
            return value
        if isinstance(value, Mapping) and "status" in value:
            return ResolvedField.from_mapping(value)
        if value is None or (isinstance(value, str) and not value.strip()):
            return ResolvedField(status=ResolvedStatus.UNKNOWN, reason="field is empty")
        return ResolvedField(
            status=ResolvedStatus.RESOLVED,
            value=value,
            evidence_refs=self.evidence_refs,
            assumptions=self.assumptions,
            confidence=self.confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _serialise(getattr(self, item.name)) for item in fields(self)}


@dataclass
class ReportRun:
    """A complete report run, including its evidence ledger and gate state."""

    run_id: str = ""
    project_context: ProjectContext | Mapping[str, Any] = field(default_factory=ProjectContext)
    sections: dict[str, SectionResult | Any] = field(default_factory=dict)
    evidence_records: list[EvidenceRecord | Mapping[str, Any]] = field(default_factory=list)
    requirements: list[DataRequirement | Mapping[str, Any]] = field(default_factory=list)
    status: ResolvedStatus | str = ResolvedStatus.PARTIAL
    gate_status: dict[str, str] = field(default_factory=dict)
    started_at: date | datetime | str | None = None
    completed_at: date | datetime | str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Constructor compatibility for callers that used ``evidence=...``.
    evidence: list[EvidenceRecord | Mapping[str, Any]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.project_context = ProjectContext.from_mapping(self.project_context)
        self.sections = {
            str(key): (
                SectionResult.from_mapping(value)
                if isinstance(value, Mapping)
                else value
            )
            for key, value in dict(self.sections or {}).items()
        }
        raw_evidence: Iterable[EvidenceRecord | Mapping[str, Any]] = (
            self.evidence_records or self.evidence or []
        )
        self.evidence_records = [EvidenceRecord.from_mapping(item) for item in raw_evidence]
        self.evidence = self.evidence_records
        self.requirements = [DataRequirement.from_mapping(item) for item in (self.requirements or [])]
        self.status = normalize_resolved_status(self.status, ResolvedStatus.PARTIAL)
        self.gate_status = dict(self.gate_status or {})
        self.metadata = dict(self.metadata or {})

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "ReportRun") -> "ReportRun":
        if isinstance(value, cls):
            return value
        payload = dict(value)
        if "context" in payload and "project_context" not in payload:
            payload["project_context"] = payload.pop("context")
        if "evidence" in payload and "evidence_records" not in payload:
            payload["evidence_records"] = payload["evidence"]
        return cls(**payload)

    @property
    def context(self) -> ProjectContext:
        return self.project_context

    @property
    def evidence_by_id(self) -> dict[str, EvidenceRecord]:
        return {
            item.evidence_id: item
            for item in self.evidence_records
            if item.evidence_id
        }

    def to_dict(self) -> dict[str, Any]:
        payload = {
            item.name: _serialise(getattr(self, item.name))
            for item in fields(self)
            if item.name != "evidence"
        }
        return payload


__all__ = [
    "EvidenceType",
    "EvidenceResolutionStatus",
    "ResolvedStatus",
    "ResolutionStatus",
    "normalize_evidence_type",
    "normalize_resolved_status",
    "ProjectContext",
    "DataRequirement",
    "EvidenceRecord",
    "ResolvedField",
    "SectionResult",
    "ReportRun",
]


