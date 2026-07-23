"""Versioned, isolated agent-based demand simulation.

The engine deliberately ships without a universal city fallback.  A city is
supported only after an explicit, evidenced parameter profile is registered.
Outputs are model simulations and can never be relabelled as observed demand.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import statistics
from typing import Mapping, Sequence

from dds.customer import ChoiceModelArtifact


def _gumbel(rng: random.Random) -> float:
    """Draw a standard Gumbel variate from the run-local RNG."""

    uniform = min(1.0 - 1e-12, max(1e-12, rng.random()))
    return -math.log(-math.log(uniform))


class UnsupportedCityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SegmentParameters:
    segment_id: str
    weight: float
    annual_income_mean_wan: float
    annual_income_sigma_wan: float
    asset_multiple: float
    down_payment_ratio: float
    preference_weights: Mapping[str, float]
    risk_aversion: float

    def __post_init__(self) -> None:
        if not self.segment_id.strip():
            raise ValueError("segment_id must not be empty")
        if self.weight <= 0:
            raise ValueError("segment weight must be positive")
        if self.annual_income_mean_wan <= 0 or self.annual_income_sigma_wan < 0:
            raise ValueError("income distribution must be non-negative")
        if self.asset_multiple <= 0:
            raise ValueError("asset_multiple must be positive")
        if not 0 < self.down_payment_ratio <= 1:
            raise ValueError("down_payment_ratio must be within (0, 1]")
        if not 0 <= self.risk_aversion <= 1:
            raise ValueError("risk_aversion must be within [0, 1]")
        if any(not math.isfinite(value) for value in self.preference_weights.values()):
            raise ValueError("preference weights must be finite")


@dataclass(frozen=True, slots=True)
class CityModelParameters:
    city: str
    version: str
    segments: tuple[SegmentParameters, ...]
    supporting_evidence_refs: tuple[str, ...]
    choice_model: ChoiceModelArtifact | None = None

    def __post_init__(self) -> None:
        if not self.city.strip() or not self.version.strip():
            raise ValueError("city and parameter version are required")
        if not self.segments:
            raise ValueError("at least one segment is required")
        if not self.supporting_evidence_refs:
            raise ValueError("city parameters require supporting evidence references")
        if self.choice_model is not None and self.choice_model.city != self.city:
            raise ValueError("choice model city must match the city parameter profile")


@dataclass(frozen=True, slots=True)
class SimulatedProduct:
    product_id: str
    unit_area_m2: float
    unit_price_cny_m2: float
    attributes: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.product_id.strip():
            raise ValueError("product_id must not be empty")
        if self.unit_area_m2 <= 0 or self.unit_price_cny_m2 <= 0:
            raise ValueError("area and price must be positive")
        if any(not 0 <= value <= 1 for value in self.attributes.values()):
            raise ValueError("product attributes must be within [0, 1]")

    @property
    def total_price_wan(self) -> float:
        return self.unit_area_m2 * self.unit_price_cny_m2 / 10_000


@dataclass(frozen=True, slots=True)
class ABMResult:
    city: str
    parameter_version: str
    seed: int
    sample_size: int
    product_choice_shares: Mapping[str, float]
    exit_share: float
    affordable_budget_p25_wan: float
    affordable_budget_median_wan: float
    affordable_budget_p75_wan: float
    supporting_evidence_refs: tuple[str, ...]
    choice_model_version: str | None = None
    wtp_by_segment_attribute_wan: Mapping[str, float] | None = None
    evidence_type: str = "model_simulation"

    @property
    def wtp_p25_wan(self) -> float:
        """Compatibility alias; this value is affordability, not true WTP."""

        return self.affordable_budget_p25_wan

    @property
    def wtp_median_wan(self) -> float:
        """Compatibility alias; this value is affordability, not true WTP."""

        return self.affordable_budget_median_wan

    @property
    def wtp_p75_wan(self) -> float:
        """Compatibility alias; this value is affordability, not true WTP."""

        return self.affordable_budget_p75_wan


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


class ABMEngine:
    """MNL-style simulation with a per-run RNG and immutable parameters."""

    def __init__(self, profiles: Sequence[CityModelParameters]) -> None:
        self._profiles = {item.city: item for item in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("city profiles must be unique")

    @property
    def supported_cities(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def simulate(
        self,
        city: str,
        products: Sequence[SimulatedProduct],
        *,
        sample_size: int = 1_000,
        seed: int = 42,
    ) -> ABMResult:
        profile = self._profiles.get(city)
        if profile is None:
            raise UnsupportedCityError(
                f"ABM parameters are not registered for {city}; supported={self.supported_cities}"
            )
        if not products:
            raise ValueError("at least one product is required")
        if sample_size < 100:
            raise ValueError("sample_size must be at least 100")
        rng = random.Random(seed)
        segment_weights = [item.weight for item in profile.segments]
        choices = {item.product_id: 0 for item in products}
        exits = 0
        affordable_budgets: list[float] = []
        choice_model = profile.choice_model
        wtp_by_segment_attribute: dict[str, float] = {}
        if choice_model is not None:
            for segment in profile.segments:
                coefficients = choice_model.coefficients_for(segment.segment_id)
                for attribute in coefficients:
                    if attribute == choice_model.price_feature:
                        continue
                    key = f"{segment.segment_id}.{attribute}"
                    wtp_by_segment_attribute[key] = round(
                        choice_model.wtp_wan(segment.segment_id, attribute),
                        6,
                    )

        for _ in range(sample_size):
            segment = rng.choices(profile.segments, weights=segment_weights, k=1)[0]
            income = max(
                0.1,
                rng.gauss(
                    segment.annual_income_mean_wan,
                    segment.annual_income_sigma_wan,
                ),
            )
            assets = income * segment.asset_multiple * rng.uniform(0.8, 1.2)
            budget = assets / segment.down_payment_ratio
            affordable_budgets.append(budget)
            utilities: list[tuple[str, float]] = []
            for product in products:
                if choice_model is None:
                    attribute_utility = sum(
                        segment.preference_weights.get(name, 0.0) * value
                        for name, value in product.attributes.items()
                    )
                    affordability = min(1.0, budget / product.total_price_wan)
                    price_penalty = max(
                        0.0,
                        (product.total_price_wan - budget) / max(budget, 0.1),
                    )
                    utility = (
                        attribute_utility
                        + affordability
                        - price_penalty * (1 + segment.risk_aversion)
                        + _gumbel(rng)
                    )
                else:
                    coefficients = choice_model.coefficients_for(segment.segment_id)
                    attribute_utility = sum(
                        coefficients.get(name, 0.0) * value
                        for name, value in product.attributes.items()
                    )
                    price_utility = (
                        coefficients[choice_model.price_feature]
                        * product.total_price_wan
                    )
                    utility = (
                        choice_model.alternative_intercepts.get(product.product_id, 0.0)
                        + attribute_utility
                        + price_utility
                        + _gumbel(rng)
                    )
                utilities.append((product.product_id, utility))
            # Explicit outside option represents delaying or abandoning purchase.
            outside_intercept = (
                choice_model.outside_option_intercept if choice_model is not None else 0.0
            )
            utilities.append(("__exit__", outside_intercept + _gumbel(rng)))
            chosen = max(utilities, key=lambda item: item[1])[0]
            if chosen == "__exit__":
                exits += 1
            else:
                choices[chosen] += 1

        return ABMResult(
            city=city,
            parameter_version=profile.version,
            seed=seed,
            sample_size=sample_size,
            product_choice_shares={
                key: round(value / sample_size, 8) for key, value in choices.items()
            },
            exit_share=round(exits / sample_size, 8),
            affordable_budget_p25_wan=round(_percentile(affordable_budgets, 0.25), 4),
            affordable_budget_median_wan=round(statistics.median(affordable_budgets), 4),
            affordable_budget_p75_wan=round(_percentile(affordable_budgets, 0.75), 4),
            supporting_evidence_refs=tuple(
                dict.fromkeys(
                    profile.supporting_evidence_refs
                    + (choice_model.evidence_refs if choice_model is not None else ())
                )
            ),
            choice_model_version=choice_model.version if choice_model is not None else None,
            wtp_by_segment_attribute_wan=(
                wtp_by_segment_attribute if choice_model is not None else None
            ),
        )


__all__ = [
    "ABMEngine",
    "ABMResult",
    "CityModelParameters",
    "SegmentParameters",
    "SimulatedProduct",
    "UnsupportedCityError",
]
