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


class RecordingSource:
    source_id = "recording-api"

    def __init__(self):
        self.queries = []

    def availability(self) -> dict[str, object]:
        return {"source_id": self.source_id, "available": True, "kind": "test"}

    def search(self, query):
        self.queries.append(query)
        return ResearchResult(
            source_id=self.source_id,
            query=query.query,
            candidates=(
                ResearchCandidate(
                    source_id=self.source_id,
                    source_ref=f"https://data.example.test/{len(self.queries)}",
                    title="分指标候选",
                    snippet="仅供检索召回，尚未完成来源资格复核",
                    source_hash=f"{len(self.queries):064x}",
                    metric_ids=("SC2.competitors", "SC2.customer_segments"),
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
    assert result["qualified_candidate_count"] == 0
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


def test_confirmed_intervention_returns_job_bound_frozen_brief_hash(tmp_path):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )
    job_id = client.post(
        "/api/research-jobs",
        json={
            "project_name": "冻结任务",
            "city": "深圳",
            "address": "深圳市测试路 1 号",
        },
    ).json()["job_id"]

    confirmed = client.post(
        f"/api/research-jobs/{job_id}/intervention",
        json={"selected_mode": 1, "user_goal": "独立研判项目机会"},
    )

    assert confirmed.status_code == 200
    assert len(confirmed.json()["intervention_brief_hash"]) == 64


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
    assert refreshed["analysis_profile"]["recommended_mode"] == 1
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


def test_confirmed_intervention_brief_is_immutable_but_same_project_can_use_new_job(
    tmp_path,
):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )
    project = {
        "project_name": "同一项目",
        "city": "深圳",
        "address": "深圳市宝安中心区 A002-0113 宗地",
    }
    first_job = client.post("/api/research-jobs", json=project).json()
    second_job = client.post("/api/research-jobs", json=project).json()

    first_confirmation = client.post(
        f"/api/research-jobs/{first_job['job_id']}/intervention",
        json={"selected_mode": 1, "user_goal": "独立研判市场与客群机会"},
    )
    second_confirmation = client.post(
        f"/api/research-jobs/{second_job['job_id']}/intervention",
        json={"selected_mode": 3, "user_goal": "审查两个已提交方案"},
    )
    overwrite_attempt = client.post(
        f"/api/research-jobs/{first_job['job_id']}/intervention",
        json={"selected_mode": 3, "user_goal": "覆盖第一次确认"},
    )

    assert first_confirmation.status_code == 200
    assert second_confirmation.status_code == 200
    assert overwrite_attempt.status_code == 409
    persisted_first = client.get(
        f"/api/research-jobs/{first_job['job_id']}"
    ).json()
    persisted_second = client.get(
        f"/api/research-jobs/{second_job['job_id']}"
    ).json()
    assert persisted_first["analysis_profile"]["selected_mode"] == 1
    assert persisted_first["intervention_brief"]["selected_mode"] == 1
    assert persisted_first["intervention_brief"]["user_goal"] == (
        "独立研判市场与客群机会"
    )
    assert persisted_second["analysis_profile"]["selected_mode"] == 3
    assert persisted_second["intervention_brief"]["selected_mode"] == 3


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


def test_product_researches_each_metric_and_targets_future_demand_events(tmp_path):
    source = RecordingSource()
    client = TestClient(
        create_app(
            ProductSettings(root=tmp_path),
            research_sources=(source,),
        )
    )
    created = client.post(
        "/api/research-jobs",
        json={
            "project_name": "企鹅岛邻近住宅项目",
            "city": "深圳",
            "district": "宝安",
            "address": "宝安中心滨海片区",
            "project_type": "住宅",
            "decision_question": "未来三至五年的新增客群来自哪里？",
        },
    ).json()
    client.post(
        f"/api/research-jobs/{created['job_id']}/intervention",
        json={"selected_mode": 1, "user_goal": "独立判断未来客群与产品机会"},
    )

    result = client.post(
        f"/api/research-jobs/{created['job_id']}/run"
    )
    assert result.status_code == 200
    detail = client.get(
        f"/api/research-jobs/{created['job_id']}"
    ).json()

    planned_metric_ids = {
        item["metric_id"]
        for item in detail["research_plan"]["source_plan"]
        if item["status"] == "planned"
    }
    assert {query.metric_ids[0] for query in source.queries} == planned_metric_ids
    assert all(len(query.metric_ids) == 1 for query in source.queries)
    assert all(len(candidate["metric_ids"]) == 1 for candidate in detail["candidates"])
    assert {
        candidate["metric_ids"][0] for candidate in detail["candidates"]
    } == planned_metric_ids

    future_query = next(
        query
        for query in source.queries
        if query.metric_ids == ("SC2.future_demand_event_scan",)
    )
    assert "企鹅岛邻近住宅项目" in future_query.query
    assert "宝安中心滨海片区" in future_query.query
    assert "重大雇主" in future_query.query
    assert "轨道交通" in future_query.query
    assert "未来五年" in future_query.query
