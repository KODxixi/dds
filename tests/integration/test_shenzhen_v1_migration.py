from __future__ import annotations

import json
from pathlib import Path

import pytest

from dds.projects import LegacyProjectReportMigrator
from dds.reporting import page_value_errors


V1_SHENZHEN = Path(
    r"D:\Vault-assets\AI_Projects\DDS\实际项目\Test_深圳高层"
)


@pytest.mark.integration
def test_shenzhen_v1_migrates_to_confirmed_input_3_outputs(tmp_path):
    seed_path = V1_SHENZHEN / "work" / "report_seed.json"
    batch_path = V1_SHENZHEN / "work" / "evidence_candidate_batch.json"
    if not seed_path.is_file() or not batch_path.is_file():
        pytest.skip("V1 Shenzhen acceptance project is unavailable")

    result = LegacyProjectReportMigrator().migrate(
        V1_SHENZHEN,
        tmp_path / "shenzhen-v2",
        project_id="SZ-A002-0113",
        project_name="深圳宝安中心区 A002-0113 方案审查",
        imported_at="2026-07-23T12:00:00+08:00",
        as_of="2026-07-23",
        selected_mode=3,
        intervention_brief={
            "user_goal": "按同一基线审查三套总图并确定主推方案和切换条件",
            "decision_audience": "设计、投资与项目操盘负责人",
            "decision_questions": [
                "哪套方案最能把景观资源转化为可兑现货值？",
                "主推方案在什么条件下需要切换？",
            ],
            "priorities": {
                "scheme_comparability": 0.35,
                "product_value": 0.35,
                "implementation_risk": 0.30,
            },
            "prohibited_conclusions": [
                "未经真实成交和成本校准的售价、去化或收益承诺"
            ],
            "confirmed_at": "2026-07-23T12:00:00+08:00",
        },
        input_profile={
            "address": "深圳市宝安中心区 A002-0113 宗地",
            "constraint_sources": [{"source_ref": "client://招标法定资料"}],
            "schemes": [
                {"scheme_id": "A"},
                {"scheme_id": "B"},
                {"scheme_id": "C"},
            ],
        },
        legacy_seed_path=seed_path,
        evidence_batch_path=batch_path,
    )

    profile = result.decision_seed["meta"]["analysis_profile"]
    assert profile["selected_mode"] == 3
    assert profile["mode_status"] == "confirmed"
    assert len(result.report_document["page_manifest"]) == 12
    assert all(
        not page_value_errors(page)
        for page in result.decision_seed["page_manifest"]
    )
    pptx_sources = [
        item
        for item in result.decision_seed["source_registry"]
        if item["name"] == "初始参考_RGX.pptx"
    ]
    assert len(pptx_sources) == 1
    assert pptx_sources[0]["extraction_status"] == "extracted"
    assert {
        item["qualification_status"]
        for item in result.decision_seed["source_registry"]
        if "qualification_status" in item
    } == {"qualified", "needs_review"}
    serialized = json.dumps(
        {
            "seed": result.decision_seed,
            "workbook": result.evidence_workbook,
        },
        ensure_ascii=False,
    )
    assert "synthetic" not in serialized.lower()
    assert "requested_level" not in serialized
    assert "effective_level" not in serialized
    assert (result.output_root / "report.html").is_file()
    assert (result.output_root / "evidence-workbook.json").is_file()
