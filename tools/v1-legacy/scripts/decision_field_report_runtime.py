"""Server-only wiring for persistent report revisions and GarchOS artifacts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from decision_field_artifact_reader import ArtifactAccessError, ArtifactReader
from decision_field_report_revisions import DecisionFieldReportRevisionStore
from garchos_openapi_contract import ContractConfigurationError, OpenAPIContract


class UnavailableArtifactReader:
    def read(self, **_kwargs):
        raise ArtifactAccessError(
            "GarchOS artifact content channel is not configured",
            code="ARTIFACT_CHANNEL_UNAVAILABLE",
            status=503,
            retryable=True,
        )


def create_report_revision_store(
    environ: Mapping[str, str] | None = None,
    *,
    transport: Any | None = None,
) -> DecisionFieldReportRevisionStore | None:
    values = os.environ if environ is None else environ
    openapi_path = str(values.get("GARCHOS_WORKFLOW_OPENAPI") or "").strip()
    if not openapi_path:
        return None
    try:
        contract = OpenAPIContract(openapi_path)
    except ContractConfigurationError:
        return None
    default_root = Path(__file__).resolve().parents[1] / "data_out" / "report_revisions"
    root = Path(str(values.get("DDS_REPORT_REVISION_ROOT") or default_root))
    base_url = str(values.get("GARCHOS_ARTIFACT_BASE_URL") or "").strip()
    service_secret = str(
        values.get("DDS_GARCHOS_ARTIFACT_SECRET")
        or values.get("GARCHOS_ARTIFACT_SERVICE_SECRET")
        or ""
    ).strip()
    if base_url and service_secret:
        reader: Any = ArtifactReader(
            base_url=base_url,
            service_secret=service_secret,
            service_name="dds",
            transport=transport,
            timeout_seconds=float(values.get("DDS_GARCHOS_ARTIFACT_TIMEOUT_SECONDS") or 15),
            contract=contract,
        )
    else:
        reader = UnavailableArtifactReader()
    return DecisionFieldReportRevisionStore(
        root,
        artifact_reader=reader,
        contract=contract,
    )


__all__ = [
    "ArtifactAccessError",
    "UnavailableArtifactReader",
    "create_report_revision_store",
]
