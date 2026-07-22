"""Deterministic decision engines.

Agents may choose inputs and explain outputs, but calculations live here so that
the same frozen evidence always produces the same result.
"""

from .abm import (
    ABMEngine,
    ABMResult,
    CityModelParameters,
    SegmentParameters,
    SimulatedProduct,
    UnsupportedCityError,
)
from .confidence import (
    DIMENSION_WEIGHTS,
    ConfidenceAssessment,
    ConfidenceEngine,
    assess_evidence_confidence,
)
from .market import MarketAnalysisResult, MarketEngine, MarketReadiness
from .premium import PremiumEngine, PremiumResult, PremiumStatus
from .product import ProductEngine, ProductMode, ProductResult
from .resolver import EvidenceResolution, EvidenceResolver, resolve_evidence

__all__ = [
    "ABMEngine",
    "ABMResult",
    "CityModelParameters",
    "SegmentParameters",
    "SimulatedProduct",
    "UnsupportedCityError",
    "DIMENSION_WEIGHTS",
    "ConfidenceAssessment",
    "ConfidenceEngine",
    "assess_evidence_confidence",
    "MarketAnalysisResult",
    "MarketEngine",
    "MarketReadiness",
    "PremiumEngine",
    "PremiumResult",
    "PremiumStatus",
    "ProductEngine",
    "ProductMode",
    "ProductResult",
    "EvidenceResolution",
    "EvidenceResolver",
    "resolve_evidence",
]
