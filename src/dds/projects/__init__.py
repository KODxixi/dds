"""Real-project intake and read-only V1 migration."""

from .intake import ExtractionLocator, ProjectManifest, SourceAsset, V1ProjectImporter
from .legacy_report import (
    LegacyMigrationResult,
    LegacyProjectReportMigrator,
    discover_v1_source_materials,
)

__all__ = [
    "ExtractionLocator",
    "LegacyMigrationResult",
    "LegacyProjectReportMigrator",
    "ProjectManifest",
    "SourceAsset",
    "V1ProjectImporter",
    "discover_v1_source_materials",
]
