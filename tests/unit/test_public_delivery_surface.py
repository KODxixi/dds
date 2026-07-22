from __future__ import annotations

from pathlib import Path

import pytest

from dds.agents.base import AgentResult, AgentStatus, AgentTask
from dds.agents.report_exporter import LEGACY_ARTIFACT_KIND, ReportExporterAgent
from dds.reporting import (
    ReportDeliveryEvidenceError,
    require_report_delivery_validation,
    validate_report_delivery_evidence,
    verify_report_delivery_validation,
)
from dds.reporting import report_delivery_evidence as delivery_evidence_module


def test_reporting_publicly_exports_v1_delivery_evidence_compatibility_api() -> None:
    assert ReportDeliveryEvidenceError is delivery_evidence_module.ReportDeliveryEvidenceError
    assert (
        validate_report_delivery_evidence
        is delivery_evidence_module.validate_report_delivery_evidence
    )
    assert (
        require_report_delivery_validation
        is delivery_evidence_module.require_report_delivery_validation
    )
    assert (
        verify_report_delivery_validation
        is delivery_evidence_module.verify_report_delivery_validation
    )


@pytest.mark.asyncio
async def test_legacy_exporter_uses_configured_root_and_marks_work_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exports_root = tmp_path / "configured-exports"
    monkeypatch.setenv("DDS_EXPORTS_ROOT", str(exports_root))
    task = AgentTask(
        task_type="report_export",
        parameters={
            "sections": {"SC1": {"narrative": "兼容工作稿内容"}},
            "project_context": {
                "city": "武汉",
                "project_type": "住宅",
                "project_name": "公共出口测试",
                "base_date": "2026-07-22",
            },
            "validation": {"overall_confidence": 0.5},
            "report_id": "PUBLIC-SURFACE",
        },
    )

    result = await ReportExporterAgent().run(task)

    assert result.success is True
    assert result.data["artifact_kind"] == LEGACY_ARTIFACT_KIND
    assert result.data["delivery_ready"] is False
    assert result.data["metadata"]["delivery_ready"] is False
    assert Path(result.data["output_dir"]) == exports_root.resolve()
    assert "Work_Report" in result.data["filename"]
    output = Path(result.data["output_path"])
    assert output.parent == exports_root.resolve()
    content = output.read_text(encoding="utf-8")
    assert '<meta name="dds-artifact-kind" content="legacy_work_report">' in content
    assert '<meta name="dds-delivery-ready" content="false">' in content
    assert "不得作为正式交付物" in content


@pytest.mark.asyncio
async def test_legacy_pipeline_cannot_promote_export_to_delivery_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dds.agents import pipeline

    class FakeOrchestrator:
        async def run(self, task: AgentTask) -> AgentResult:
            return AgentResult(
                task_id=task.task_id,
                success=True,
                agent_id="fake",
                status=AgentStatus.COMPLETED,
                data={
                    "report_id": "LEGACY-1",
                    "export": {
                        "output_path": "work.html",
                        "filename": "unsafe-name.html",
                        "output_dir": "exports",
                        "delivery_ready": True,
                    },
                    "validation": {"overall_confidence": 1.0},
                },
            )

    monkeypatch.setattr(pipeline, "create_agent_system", FakeOrchestrator)
    result = await pipeline.generate_report(project_context={"city": "武汉"})

    assert result["success"] is True
    assert result["artifact_kind"] == LEGACY_ARTIFACT_KIND
    assert result["delivery_ready"] is False
    assert result["metadata"]["delivery_ready"] is False
    assert result["export"]["delivery_ready"] is False
    assert result["export"]["metadata"]["delivery_ready"] is False
    assert any("兼容工作稿" in warning for warning in result["warnings"])


def test_readme_points_formal_delivery_to_services() -> None:
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(
        encoding="utf-8"
    )
    assert "EvidenceRecord / EvidencePackage" in readme
    assert "ReportRun" in readme
    assert "ReportCompilerAdapter.build_frozen_compiler_package" in readme
    assert "DeliveryService" in readme
    assert "hash-bound browser QA" in readme
    assert "legacy_work_report" in readme
    assert '"delivery_ready": False' in readme
