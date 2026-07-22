"""Reliable end-to-end tests for the legacy DDS work-report API."""

from pathlib import Path
from typing import Any

import pytest

import dds
from dds import SECTION_REQUIREMENTS, create_agent_system, generate_report


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PACKAGE_ROOT = (ROOT / "src" / "dds").resolve()
EXPECTED_SECTION_IDS = set(SECTION_REQUIREMENTS)
BASE_DATE = "2026-07-22"


def _verified_inputs() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build deterministic, fully traceable values for the strict decision gate."""
    sections: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for section_id, fields in SECTION_REQUIREMENTS.items():
        section: dict[str, Any] = {}
        for field_name in fields:
            metric_id = f"{section_id}.{field_name}"
            evidence_id = f"fixture-{section_id}-{field_name}"
            value: Any = [] if field_name == "data_gaps" else f"verified-{field_name}"
            section[field_name] = value
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "metric_id": metric_id,
                    "value": value,
                    "unit": "fixture",
                    "evidence_type": "observed_fact",
                    "source_id": "dds-e2e-fixture",
                    "source_ref": f"fixture://{metric_id}",
                    "source_hash": "a" * 64,
                    "observed_at": BASE_DATE,
                    "geography": "Beijing",
                    "method": "deterministic fixture",
                    "sample_size": 1000,
                }
            )
        sections[section_id] = section
    return sections, evidence


def test_agent_system_assembly() -> None:
    """The src package and five legacy specialist agents remain available."""
    assert Path(dds.__file__).resolve().parent == EXPECTED_PACKAGE_ROOT
    orchestrator = create_agent_system()
    assert set(orchestrator._agents) == {
        "requirement_analysis",
        "data_orchestration",
        "content_generation",
        "quality_audit",
        "report_export",
    }


@pytest.mark.asyncio
async def test_end_to_end_legacy_work_report_generation(tmp_path: Path) -> None:
    """The compatibility pipeline works while remaining ineligible for delivery."""
    report_id = "TEST-E2E-001"
    existing_data, evidence_records = _verified_inputs()
    result = await generate_report(
        project_context={
            "project_id": "E2E-PROJECT",
            "city": "Beijing",
            "project_type": "Residential",
            "project_name": "Chaoyang test site",
            "base_date": BASE_DATE,
            "evidence_records": evidence_records,
        },
        existing_data=existing_data,
        report_id=report_id,
        output_dir=tmp_path,
    )

    assert result["success"], result["errors"]
    assert result["report_id"] == report_id
    assert result["artifact_kind"] == "legacy_work_report"
    assert result["delivery_ready"] is False
    assert result["metadata"]["delivery_ready"] is False
    assert result["export"]["delivery_ready"] is False
    assert len(result["warnings"]) >= 2
    assert all(isinstance(warning, str) for warning in result["warnings"])
    assert set(result["section_confidences"]) == EXPECTED_SECTION_IDS

    output_path = Path(result["output_path"]).resolve()
    assert output_path.parent == tmp_path.resolve()
    assert Path(result["output_dir"]).resolve() == tmp_path.resolve()
    assert result["filename"] == output_path.name
    assert "Work_Report" in result["filename"]
    assert output_path.is_file()

    html_content = output_path.read_text(encoding="utf-8")
    for marker in (
        "DDS v2",
        report_id,
        "Beijing",
        "Residential",
        'id="sec-SC1"',
        'id="sec-CS"',
        'name="dds-artifact-kind" content="legacy_work_report"',
        'name="dds-delivery-ready" content="false"',
        "conf-list",
    ):
        assert marker in html_content
    assert "不得作为正式交付物" in html_content
    assert output_path.stat().st_size > 5000
