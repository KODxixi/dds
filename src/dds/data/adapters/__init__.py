"""Source adapters that convert repository rows into evidence records."""

from dds.data.adapters.land import LandAdapter, LandEvidenceBundle
from dds.data.adapters.local_listings import (
    ListingEvidenceAdapter,
    ListingEvidenceBundle,
)
from dds.data.adapters.macro import MacroAdapter, MacroEvidenceBundle
from dds.data.adapters.transactions import (
    TransactionAdapter,
    TransactionEvidenceBundle,
)
from dds.data.adapters.user_materials import UserMaterialAdapter

__all__ = [
    "ListingEvidenceAdapter",
    "ListingEvidenceBundle",
    "TransactionAdapter",
    "TransactionEvidenceBundle",
    "LandAdapter",
    "LandEvidenceBundle",
    "MacroAdapter",
    "MacroEvidenceBundle",
    "UserMaterialAdapter",
]
