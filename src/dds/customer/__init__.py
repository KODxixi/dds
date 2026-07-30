"""China real-estate customer-intelligence contracts."""

from .models import (
    ChoiceModelArtifact,
    CustomerEvidenceLevel,
    CustomerIntelligenceBundle,
    CustomerSegment,
    DemandDriverEvent,
    FutureCustomerScenario,
    LocalPopulationPrior,
    PersonaExperimentResult,
    PersonaExperimentSpec,
    SyntheticCohortManifest,
    customer_intelligence_from_mapping,
)

__all__ = [
    "ChoiceModelArtifact",
    "CustomerEvidenceLevel",
    "CustomerIntelligenceBundle",
    "CustomerSegment",
    "DemandDriverEvent",
    "FutureCustomerScenario",
    "LocalPopulationPrior",
    "PersonaExperimentResult",
    "PersonaExperimentSpec",
    "SyntheticCohortManifest",
    "customer_intelligence_from_mapping",
]
