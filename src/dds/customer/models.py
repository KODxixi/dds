"""Auditable customer-intelligence contracts for China real-estate analysis.

The report path only accepts aggregated, de-identified artifacts. Heavy
population-synthesis, choice-estimation, and LLM runners stay outside the DDS
runtime and exchange frozen manifests through these contracts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
import math
from typing import Any, Mapping, Sequence


class CustomerEvidenceLevel(IntEnum):
    """Maximum customer claim authority supported by the evidence."""

    BLOCKED = 0
    LOCAL_BASELINE = 1
    CHOICE_CALIBRATED = 2
    FUNNEL_CALIBRATED = 3


_FORBIDDEN_KEYS = {
    "raw",
    "payload",
    "records",
    "personas_raw",
    "transcript",
    "transcripts",
    "chain_of_thought",
    "phone",
    "mobile",
    "email",
    "id_card",
    "identity_number",
    "exact_address",
}


def _text(value: Any, field_name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field_name} is required")
    return result


def _strings(values: Sequence[Any], field_name: str) -> tuple[str, ...]:
    result = tuple(str(item).strip() for item in values if str(item).strip())
    if not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _assert_no_raw(value: Any, path: str = "bundle") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"{path}.{key} is forbidden in customer artifacts")
            _assert_no_raw(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_raw(item, f"{path}[{index}]")


def _serialise(value: Any) -> Any:
    if isinstance(value, IntEnum):
        return int(value)
    if isinstance(value, Mapping):
        return {str(key): _serialise(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialise(item) for item in value]
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class LocalPopulationPrior:
    city: str
    as_of: str
    version: str
    household_marginals: Mapping[str, Mapping[str, float]]
    evidence_refs: tuple[str, ...]
    source_hashes: tuple[str, ...]
    rights_status: str
    district: str = ""
    submarket: str = ""
    seed_sample_ref: str = ""
    crosswalk_ref: str = ""
    weighting_method: str = "entropy_balancing"
    schema_version: str = "dds.local-population-prior/1.0"

    def __post_init__(self) -> None:
        _text(self.city, "city")
        _text(self.as_of, "as_of")
        _text(self.version, "version")
        _text(self.rights_status, "rights_status")
        _text(self.weighting_method, "weighting_method")
        _strings(self.evidence_refs, "evidence_refs")
        _strings(self.source_hashes, "source_hashes")
        if not self.household_marginals:
            raise ValueError("household_marginals must not be empty")
        for dimension, categories in self.household_marginals.items():
            _text(dimension, "household_marginals dimension")
            if not categories:
                raise ValueError(f"household_marginals.{dimension} must not be empty")
            if any(
                not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0
                for value in categories.values()
            ):
                raise ValueError("household marginal values must be finite and non-negative")

    @property
    def supports_household_synthesis(self) -> bool:
        return bool(self.seed_sample_ref and self.crosswalk_ref)

    def to_dict(self) -> dict[str, Any]:
        return _serialise(asdict(self))


@dataclass(frozen=True, slots=True)
class CustomerSegment:
    segment_id: str
    label: str
    weight: float
    household_stage: str
    income_band: str
    asset_band: str
    current_housing: str
    purchase_stage: str
    primary_needs: tuple[str, ...]
    purchase_barriers: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    commute_zone: str = ""
    education_need: str = ""
    eldercare_need: str = ""
    channel_touchpoints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.segment_id, "segment_id")
        _text(self.label, "label")
        _text(self.household_stage, "household_stage")
        _text(self.income_band, "income_band")
        _text(self.asset_band, "asset_band")
        _text(self.current_housing, "current_housing")
        _text(self.purchase_stage, "purchase_stage")
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError("segment weight must be positive and finite")
        _strings(self.primary_needs, "primary_needs")
        _strings(self.purchase_barriers, "purchase_barriers")
        _strings(self.evidence_refs, "evidence_refs")

    def to_dict(self) -> dict[str, Any]:
        return _serialise(asdict(self))


@dataclass(frozen=True, slots=True)
class SyntheticCohortManifest:
    cohort_id: str
    city: str
    as_of: str
    version: str
    sample_size: int
    seed: int
    segment_weights: Mapping[str, float]
    generator: str
    artifact_ref: str
    input_hash: str
    output_hash: str
    evidence_refs: tuple[str, ...]
    rights_status: str
    district: str = ""
    schema_version: str = "dds.synthetic-cohort-manifest/1.0"

    def __post_init__(self) -> None:
        for value, name in (
            (self.cohort_id, "cohort_id"),
            (self.city, "city"),
            (self.as_of, "as_of"),
            (self.version, "version"),
            (self.generator, "generator"),
            (self.artifact_ref, "artifact_ref"),
            (self.input_hash, "input_hash"),
            (self.output_hash, "output_hash"),
            (self.rights_status, "rights_status"),
        ):
            _text(value, name)
        _strings(self.evidence_refs, "evidence_refs")
        if self.sample_size < 100:
            raise ValueError("synthetic cohort sample_size must be at least 100")
        if not self.segment_weights:
            raise ValueError("segment_weights must not be empty")
        if any(
            not math.isfinite(float(value)) or float(value) <= 0
            for value in self.segment_weights.values()
        ):
            raise ValueError("segment weights must be positive and finite")
        if not math.isclose(sum(self.segment_weights.values()), 1.0, abs_tol=1e-6):
            raise ValueError("segment weights must sum to 1")

    def to_dict(self) -> dict[str, Any]:
        return _serialise(asdict(self))


@dataclass(frozen=True, slots=True)
class ChoiceModelArtifact:
    model_id: str
    city: str
    as_of: str
    version: str
    segment_feature_coefficients: Mapping[str, Mapping[str, float]]
    alternative_intercepts: Mapping[str, float]
    outside_option_intercept: float
    price_feature: str
    feature_units: Mapping[str, str]
    availability_rules: Mapping[str, Any]
    validation_metrics: Mapping[str, float]
    evidence_refs: tuple[str, ...]
    artifact_hash: str
    district: str = ""
    schema_version: str = "dds.choice-model-artifact/1.0"

    def __post_init__(self) -> None:
        for value, name in (
            (self.model_id, "model_id"),
            (self.city, "city"),
            (self.as_of, "as_of"),
            (self.version, "version"),
            (self.price_feature, "price_feature"),
            (self.artifact_hash, "artifact_hash"),
        ):
            _text(value, name)
        _strings(self.evidence_refs, "evidence_refs")
        if not self.segment_feature_coefficients:
            raise ValueError("segment_feature_coefficients must not be empty")
        for segment_id, coefficients in self.segment_feature_coefficients.items():
            _text(segment_id, "segment coefficient id")
            if self.price_feature not in coefficients:
                raise ValueError(f"{segment_id} is missing the price coefficient")
            price_coefficient = float(coefficients[self.price_feature])
            if not math.isfinite(price_coefficient) or price_coefficient >= 0:
                raise ValueError("price coefficients must be finite and negative")
            if any(not math.isfinite(float(value)) for value in coefficients.values()):
                raise ValueError("choice coefficients must be finite")
        if self.price_feature not in self.feature_units:
            raise ValueError("price feature unit is required")
        if not math.isfinite(self.outside_option_intercept):
            raise ValueError("outside_option_intercept must be finite")
        _assert_no_raw(self.availability_rules, "availability_rules")

    def coefficients_for(self, segment_id: str) -> Mapping[str, float]:
        try:
            return self.segment_feature_coefficients[segment_id]
        except KeyError as exc:
            raise ValueError(f"choice model has no coefficients for {segment_id}") from exc

    def wtp_wan(self, segment_id: str, attribute: str) -> float:
        coefficients = self.coefficients_for(segment_id)
        if attribute == self.price_feature or attribute not in coefficients:
            raise ValueError(f"{attribute} is not an identified non-price attribute")
        return -float(coefficients[attribute]) / float(coefficients[self.price_feature])

    def to_dict(self) -> dict[str, Any]:
        return _serialise(asdict(self))


@dataclass(frozen=True, slots=True)
class PersonaExperimentSpec:
    experiment_id: str
    city: str
    as_of: str
    version: str
    cohort_ref: str
    scheme_ids: tuple[str, ...]
    tasks: tuple[str, ...]
    seed: int
    model_provider: str
    model_name: str
    prompt_template_hash: str
    evidence_refs: tuple[str, ...]
    local_only: bool = True
    schema_version: str = "dds.persona-experiment-spec/1.0"

    def __post_init__(self) -> None:
        for value, name in (
            (self.experiment_id, "experiment_id"),
            (self.city, "city"),
            (self.as_of, "as_of"),
            (self.version, "version"),
            (self.cohort_ref, "cohort_ref"),
            (self.model_provider, "model_provider"),
            (self.model_name, "model_name"),
            (self.prompt_template_hash, "prompt_template_hash"),
        ):
            _text(value, name)
        _strings(self.scheme_ids, "scheme_ids")
        _strings(self.tasks, "tasks")
        _strings(self.evidence_refs, "evidence_refs")

    def to_dict(self) -> dict[str, Any]:
        return _serialise(asdict(self))


@dataclass(frozen=True, slots=True)
class PersonaExperimentResult:
    experiment_id: str
    city: str
    as_of: str
    version: str
    cohort_ref: str
    seed: int
    prompt_template_hash: str
    aggregate_results: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    artifact_hash: str
    evidence_type: str = "model_simulation"
    schema_version: str = "dds.persona-experiment-result/1.0"

    def __post_init__(self) -> None:
        for value, name in (
            (self.experiment_id, "experiment_id"),
            (self.city, "city"),
            (self.as_of, "as_of"),
            (self.version, "version"),
            (self.cohort_ref, "cohort_ref"),
            (self.prompt_template_hash, "prompt_template_hash"),
            (self.artifact_hash, "artifact_hash"),
        ):
            _text(value, name)
        _strings(self.evidence_refs, "evidence_refs")
        if self.evidence_type != "model_simulation":
            raise ValueError("persona experiment results must remain model_simulation")
        if not self.aggregate_results:
            raise ValueError("aggregate_results must not be empty")
        _assert_no_raw(self.aggregate_results, "aggregate_results")

    def to_dict(self) -> dict[str, Any]:
        return _serialise(asdict(self))


@dataclass(frozen=True, slots=True)
class CustomerIntelligenceBundle:
    bundle_id: str
    city: str
    as_of: str
    version: str
    evidence_level: CustomerEvidenceLevel
    local_population: LocalPopulationPrior
    segments: tuple[CustomerSegment, ...]
    evidence_refs: tuple[str, ...]
    artifact_hashes: tuple[str, ...]
    rights_status: str
    allowed_uses: tuple[str, ...]
    prohibited_uses: tuple[str, ...]
    synthetic_cohort: SyntheticCohortManifest | None = None
    choice_simulation: Mapping[str, Any] | None = None
    persona_experiment: PersonaExperimentResult | None = None
    funnel_calibration: Mapping[str, Any] | None = None
    district: str = ""
    schema_version: str = "dds.customer-intelligence-bundle/1.0"

    def __post_init__(self) -> None:
        for value, name in (
            (self.bundle_id, "bundle_id"),
            (self.city, "city"),
            (self.as_of, "as_of"),
            (self.version, "version"),
            (self.rights_status, "rights_status"),
        ):
            _text(value, name)
        _strings(self.evidence_refs, "evidence_refs")
        _strings(self.artifact_hashes, "artifact_hashes")
        _strings(self.allowed_uses, "allowed_uses")
        prohibited = set(_strings(self.prohibited_uses, "prohibited_uses"))
        required_prohibitions = {
            "direct_monthly_sales_from_choice_share",
            "raw_personal_data_export",
        }
        if not required_prohibitions.issubset(prohibited):
            raise ValueError(
                "prohibited_uses must forbid direct monthly sales and raw personal data export"
            )
        if self.evidence_level is CustomerEvidenceLevel.BLOCKED:
            raise ValueError("blocked customer evidence cannot enter a report bundle")
        if not self.segments:
            raise ValueError("at least one evidence-backed customer segment is required")
        if not math.isclose(sum(item.weight for item in self.segments), 1.0, abs_tol=1e-6):
            raise ValueError("customer segment weights must sum to 1")
        if self.city != self.local_population.city or self.as_of != self.local_population.as_of:
            raise ValueError("bundle and local population geography/as_of must match")
        if self.evidence_level < CustomerEvidenceLevel.CHOICE_CALIBRATED:
            if self.synthetic_cohort is not None or self.choice_simulation is not None:
                raise ValueError("C1 bundles cannot claim a cohort or choice simulation")
        else:
            if self.synthetic_cohort is None or not self.choice_simulation:
                raise ValueError("C2/C3 bundles require a cohort and choice simulation")
        if self.evidence_level is CustomerEvidenceLevel.FUNNEL_CALIBRATED:
            if not self.funnel_calibration:
                raise ValueError("C3 bundles require funnel_calibration")
        elif self.funnel_calibration is not None:
            raise ValueError("only C3 bundles may contain funnel_calibration")
        _assert_no_raw(self.to_dict(), "customer_intelligence")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "bundle_id": self.bundle_id,
            "city": self.city,
            "district": self.district,
            "as_of": self.as_of,
            "version": self.version,
            "evidence_level": int(self.evidence_level),
            "evidence_level_name": self.evidence_level.name.lower(),
            "local_population": self.local_population.to_dict(),
            "segments": [item.to_dict() for item in self.segments],
            "synthetic_cohort": (
                self.synthetic_cohort.to_dict() if self.synthetic_cohort else None
            ),
            "choice_simulation": _serialise(self.choice_simulation),
            "persona_experiment": (
                self.persona_experiment.to_dict() if self.persona_experiment else None
            ),
            "funnel_calibration": _serialise(self.funnel_calibration),
            "evidence_refs": list(self.evidence_refs),
            "artifact_hashes": list(self.artifact_hashes),
            "rights_status": self.rights_status,
            "allowed_uses": list(self.allowed_uses),
            "prohibited_uses": list(self.prohibited_uses),
        }
        _assert_no_raw(payload, "customer_intelligence")
        return payload


def _evidence_level(value: Any) -> CustomerEvidenceLevel:
    if isinstance(value, CustomerEvidenceLevel):
        return value
    if isinstance(value, int):
        return CustomerEvidenceLevel(value)
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "c0": CustomerEvidenceLevel.BLOCKED,
        "blocked": CustomerEvidenceLevel.BLOCKED,
        "c1": CustomerEvidenceLevel.LOCAL_BASELINE,
        "local_baseline": CustomerEvidenceLevel.LOCAL_BASELINE,
        "c2": CustomerEvidenceLevel.CHOICE_CALIBRATED,
        "choice_calibrated": CustomerEvidenceLevel.CHOICE_CALIBRATED,
        "c3": CustomerEvidenceLevel.FUNNEL_CALIBRATED,
        "funnel_calibrated": CustomerEvidenceLevel.FUNNEL_CALIBRATED,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown customer evidence level: {value!r}") from exc


def customer_intelligence_from_mapping(
    value: Mapping[str, Any] | CustomerIntelligenceBundle,
) -> CustomerIntelligenceBundle:
    """Parse a JSON-compatible aggregate artifact through the strict contracts."""

    if isinstance(value, CustomerIntelligenceBundle):
        return value
    raw = dict(value)
    _assert_no_raw(raw, "customer_intelligence")
    local_raw = dict(raw.pop("local_population"))
    local_raw["evidence_refs"] = tuple(local_raw.get("evidence_refs") or ())
    local_raw["source_hashes"] = tuple(local_raw.get("source_hashes") or ())
    local = LocalPopulationPrior(**local_raw)
    segment_items = []
    for item in raw.pop("segments"):
        segment_raw = dict(item)
        for field_name in (
            "primary_needs",
            "purchase_barriers",
            "evidence_refs",
            "channel_touchpoints",
        ):
            segment_raw[field_name] = tuple(segment_raw.get(field_name) or ())
        segment_items.append(CustomerSegment(**segment_raw))
    cohort_raw = raw.pop("synthetic_cohort", None)
    cohort = None
    if cohort_raw is not None:
        cohort_values = dict(cohort_raw)
        cohort_values["evidence_refs"] = tuple(
            cohort_values.get("evidence_refs") or ()
        )
        cohort = SyntheticCohortManifest(**cohort_values)
    persona_raw = raw.pop("persona_experiment", None)
    persona = None
    if persona_raw is not None:
        persona_values = dict(persona_raw)
        persona_values["evidence_refs"] = tuple(
            persona_values.get("evidence_refs") or ()
        )
        persona = PersonaExperimentResult(**persona_values)
    raw.pop("evidence_level_name", None)
    raw["evidence_level"] = _evidence_level(raw.get("evidence_level"))
    raw["local_population"] = local
    raw["segments"] = tuple(segment_items)
    raw["synthetic_cohort"] = cohort
    raw["persona_experiment"] = persona
    for field_name in (
        "evidence_refs",
        "artifact_hashes",
        "allowed_uses",
        "prohibited_uses",
    ):
        raw[field_name] = tuple(raw.get(field_name) or ())
    return CustomerIntelligenceBundle(**raw)


__all__ = [
    "ChoiceModelArtifact",
    "CustomerEvidenceLevel",
    "CustomerIntelligenceBundle",
    "CustomerSegment",
    "LocalPopulationPrior",
    "PersonaExperimentResult",
    "PersonaExperimentSpec",
    "SyntheticCohortManifest",
    "customer_intelligence_from_mapping",
]
