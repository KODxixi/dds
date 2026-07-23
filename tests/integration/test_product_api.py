from __future__ import annotations

from datetime import date
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient
import pytest

from dds.api.app import create_app
from dds.product.service import ProductSettings
from dds.research.external import ResearchCandidate, ResearchResult


class FixtureSource:
    source_id = "fixture-api"

    def availability(self) -> dict[str, object]:
        return {"source_id": self.source_id, "available": True, "kind": "external_api"}

    def search(self, query):
        return ResearchResult(
            source_id=self.source_id,
            query=query.query,
            candidates=(
                ResearchCandidate(
                    source_id=self.source_id,
                    source_ref="https://data.example.test/wuhan",
                    title="武汉市场公开数据",
                    snippet="可追溯候选资料",
                    source_hash="a" * 64,
                    metric_ids=query.metric_ids,
                    published_at=date.today().isoformat(),
                ),
            ),
        )


def _broken_workbook_bytes() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="成本" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="e"><f>#REF!+1</f><v>#REF!</v></c>'
            '</row></sheetData></worksheet>',
        )
    return stream.getvalue()


def test_non_technical_api_creates_runs_uploads_and_performs_research(tmp_path):
    app = create_app(
        ProductSettings(root=tmp_path, max_upload_bytes=1024),
        research_sources=(FixtureSource(),),
    )
    client = TestClient(app)

    created = client.post(
        "/api/research-jobs",
        json={
            "project_name": "测试项目",
            "city": "武汉",
            "address": "武汉市测试路 1 号",
            "project_type": "住宅",
            "decision_question": "应该做什么产品？",
        },
    )
    assert created.status_code == 201
    job = created.json()
    assert job["status"] == "pending"

    uploaded = client.post(
        f"/api/research-jobs/{job['job_id']}/materials?filename=brief.txt",
        content="项目资料".encode(),
        headers={"content-type": "text/plain"},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["sha256"]

    confirmed = client.post(
        f"/api/research-jobs/{job['job_id']}/intervention",
        json={"selected_mode": 1, "user_goal": "判断项目机会和产品方向"},
    )
    assert confirmed.status_code == 200

    executed = client.post(f"/api/research-jobs/{job['job_id']}/run")
    assert executed.status_code == 200
    result = executed.json()
    assert result["status"] == "research_completed"
    assert result["candidate_count"] > 0
    assert result["qualified_candidate_count"] > 0
    assert result["evidence_status"] == "evidence_gaps"
    assert result["decision_ready"] is False
    assert result["requirement_count"] > 0

    logs = client.get(f"/api/research-jobs/{job['job_id']}/logs").json()
    assert [item["event"] for item in logs] == [
        "job_created",
        "material_uploaded",
        "intervention_confirmed",
        "input_profile_classified",
        "requirements_planned",
        "source_started",
        "source_completed",
        "candidates_qualified",
        "research_completed",
    ]


def test_upload_rejects_traversal_and_oversize(tmp_path):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path, max_upload_bytes=4), research_sources=())
    )
    job_id = client.post(
        "/api/research-jobs",
        json={"project_name": "P", "city": "上海"},
    ).json()["job_id"]

    assert client.post(
        f"/api/research-jobs/{job_id}/materials?filename=../secret.txt",
        content=b"x",
    ).status_code == 400
    assert client.post(
        f"/api/research-jobs/{job_id}/materials?filename=large.txt",
        content=b"12345",
    ).status_code == 413
    uploaded = client.post(
        f"/api/research-jobs/{job_id}/materials?filename=schemes.pptx",
        content=b"x",
    )
    assert uploaded.status_code == 201
    refreshed = client.get(f"/api/research-jobs/{job_id}").json()
    assert refreshed["analysis_profile"]["recommended_mode"] == 3
    assert refreshed["analysis_profile"]["selected_mode"] is None


def test_broken_workbook_blocks_job_before_external_research(tmp_path):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=(FixtureSource(),))
    )
    job_id = client.post(
        "/api/research-jobs",
        json={"project_name": "P", "city": "襄阳", "address": "襄阳市测试路 1 号"},
    ).json()["job_id"]

    uploaded = client.post(
        f"/api/research-jobs/{job_id}/materials?filename=cost.xlsx",
        content=_broken_workbook_bytes(),
        headers={"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["integrity"]["status"] == "blocked"

    confirmed = client.post(
        f"/api/research-jobs/{job_id}/intervention",
        json={"selected_mode": 1, "user_goal": "独立研判项目机会"},
    )
    assert confirmed.status_code == 200

    result = client.post(f"/api/research-jobs/{job_id}/run").json()
    assert result["status"] == "blocked"
    assert result["input_gate_status"] == "blocked"
    logs = client.get(f"/api/research-jobs/{job_id}/logs").json()
    assert [item["event"] for item in logs] == [
        "job_created",
        "material_uploaded",
        "intervention_confirmed",
        "material_integrity_blocked",
    ]


def test_home_page_is_served_as_chinese_liquid_glass_ui(tmp_path):
    client = TestClient(create_app(ProductSettings(root=tmp_path), research_sources=()))
    response = client.get("/")
    assert response.status_code == 200
    assert "DDS Research Control Center" in response.text
    assert "主动研究" in response.text
    assert "--liquid-glass-bg" in response.text


@pytest.mark.parametrize(
    ("selected_mode", "profile_fields", "decision_scope"),
    [
        (
            1,
            {"address": "Wuhan Opportunity Road 1"},
            "independent_opportunity_research",
        ),
        (
            2,
            {
                "address": "Wuhan Constraint Road 2",
                "constraint_sources": [
                    {
                        "source_ref": "client://planning-condition-v1",
                        "status": "verified",
                    }
                ],
            },
            "constraint_collaboration",
        ),
        (
            3,
            {
                "address": "Wuhan Scheme Road 3",
                "constraint_sources": [
                    {
                        "source_ref": "client://planning-condition-v1",
                        "status": "verified",
                    }
                ],
                "core_development_boundaries_ready": True,
                "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
            },
            "scheme_review",
        ),
    ],
)
def test_api_runs_input_profiles(
    tmp_path,
    selected_mode,
    profile_fields,
    decision_scope,
):
    client = TestClient(
        create_app(
            ProductSettings(root=tmp_path / f"input-{selected_mode}"),
            research_sources=(FixtureSource(),),
        )
    )
    created = client.post(
        "/api/research-jobs",
        json={
            "project_name": f"Input {selected_mode} Project",
            "city": "Wuhan",
            **profile_fields,
        },
    )
    assert created.status_code == 201

    confirmed = client.post(
        f"/api/research-jobs/{created.json()['job_id']}/intervention",
        json={
            "selected_mode": selected_mode,
            "user_goal": f"执行 Input {selected_mode} 介入任务",
        },
    )
    assert confirmed.status_code == 200

    result = client.post(
        f"/api/research-jobs/{created.json()['job_id']}/run"
    ).json()

    assert result["status"] == "research_completed"
    assert result["analysis_profile"]["selected_mode"] == selected_mode
    assert result["analysis_profile"]["mode_status"] == "confirmed"
    assert result["analysis_profile"]["eligible"] is True
    assert result["decision_scope"] == decision_scope


def test_api_profile_run_blocks_missing_location(tmp_path):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )
    created = client.post(
        "/api/research-jobs",
        json={
            "project_name": "Missing Location",
            "city": "Wuhan",
        },
    )

    confirmed = client.post(
        f"/api/research-jobs/{created.json()['job_id']}/intervention",
        json={"selected_mode": 1, "user_goal": "独立研判项目机会"},
    )
    assert confirmed.status_code == 200

    result = client.post(
        f"/api/research-jobs/{created.json()['job_id']}/run"
    ).json()

    assert result["status"] == "blocked"
    assert result["input_gate_status"] == "blocked"
    assert result["analysis_profile"]["selected_mode"] == 1
    assert result["analysis_profile"]["missing_inputs"] == [
        "address_or_coordinates"
    ]
