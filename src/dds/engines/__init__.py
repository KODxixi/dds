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
from .absorption import (
    MarketScenario,
    MonthlyResult,
    OperatingSimulation,
    SimulationAssumptions,
    StandardAbsorptionEngine,
    StandardMarketParameters,
    StrategyCandidate,
    StrategyScenarioResult,
    UnsupportedMarketParametersError,
    pareto_front,
)
from .confidence import (
    DIMENSION_WEIGHTS,
    ConfidenceAssessment,
    ConfidenceEngine,
    assess_evidence_confidence,
)
from .market import MarketAnalysisResult, MarketEngine, MarketReadiness
from .premium import PremiumEngine, PremiumResult, PremiumStatus
from .project_cashflow import (
    MonthlyProductResult,
    ProductBatch,
    ProjectCashFlowEngine,
    ProjectCashFlowResult,
    ProjectCostParameters,
    ProjectDemandParameters,
    ProjectMonthlyResult,
    ProjectSimulationAssumptions,
)
from .product import ProductEngine, ProductMode, ProductResult
from .resolver import EvidenceResolution, EvidenceResolver, resolve_evidence
from .scheme_comparison import (
    ComparisonPolicy,
    SchemeCandidate,
    SchemeComparisonEngine,
    SchemeComparisonResult,
    SchemeMetrics,
    SchemeScenarioMetrics,
)

__all__ = [
    "ABMEngine",
    "ABMResult",
    "CityModelParameters",
    "SegmentParameters",
    "SimulatedProduct",
    "UnsupportedCityError",
    "MarketScenario",
    "MonthlyResult",
    "OperatingSimulation",
    "SimulationAssumptions",
    "StandardAbsorptionEngine",
    "StandardMarketParameters",
    "StrategyCandidate",
    "StrategyScenarioResult",
    "UnsupportedMarketParametersError",
    "pareto_front",
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
    "MonthlyProductResult",
    "ProductBatch",
    "ProjectCashFlowEngine",
    "ProjectCashFlowResult",
    "ProjectCostParameters",
    "ProjectDemandParameters",
    "ProjectMonthlyResult",
    "ProjectSimulationAssumptions",
    "ProductEngine",
    "ProductMode",
    "ProductResult",
    "EvidenceResolution",
    "EvidenceResolver",
    "resolve_evidence",
    "ComparisonPolicy",
    "SchemeCandidate",
    "SchemeComparisonEngine",
    "SchemeComparisonResult",
    "SchemeMetrics",
    "SchemeScenarioMetrics",
]
