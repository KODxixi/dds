from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from dds.projects import LegacyProjectReportMigrator
from dds.reporting import page_value_errors


V1_SHENZHEN = Path(
    r"D:\Vault-assets\AI_Projects\DDS\实际项目\Test_深圳高层"
)
PENGUIN_SOURCE_ID = "src-penguin-official-test"
PENGUIN_CLAIM_ID = "claim-penguin-future-demand-test"


def _migrate_single_text_asset(
    tmp_path: Path,
    *,
    asset_name: str,
    asset_text: str,
) -> tuple[dict, str]:
    source_root = tmp_path / "v1-source"
    work_root = source_root / "work"
    work_root.mkdir(parents=True)
    asset_path = source_root / asset_name
    asset_path.write_text(asset_text, encoding="utf-8")
    source_id = (
        "SRC-"
        + sha256(asset_path.read_bytes()).hexdigest()[:20].upper()
    )
    seed_path = work_root / "report_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "meta": {"as_of": "2026-07-30"},
                "project": {
                    "project_id": "sz-test",
                    "name": "深圳南山测试项目",
                },
                "page_manifest_authoritative": True,
                "page_manifest": [
                    {
                        "page_id": "sc1-source-boundary",
                        "chapter_id": "sc1",
                        "section_id": "SC1",
                        "unit_status": "ready",
                        "layout": "summary",
                        "title": "资料准入边界",
                        "decision_question": "这份资料能否支持当前项目决策？",
                        "takeaway": "解析成功不等于证据准入。",
                        "decision_impact": "完成项目身份与地域复核后再授权使用。",
                        "blocks": [
                            {
                                "type": "narrative",
                                "text": "当前只证明文件可解析。",
                            }
                        ],
                        "source_refs": [source_id],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    batch_path = work_root / "evidence_candidate_batch.json"
    batch_path.write_text(
        json.dumps(
            {
                "sources": [],
                "candidates": [],
                "missing_claim_ids": [],
            }
        ),
        encoding="utf-8",
    )

    result = LegacyProjectReportMigrator().migrate(
        source_root,
        tmp_path / "v2-output",
        project_id="sz-test",
        project_name="深圳南山测试项目",
        imported_at="2026-07-30T12:00:00+08:00",
        as_of="2026-07-30",
        selected_mode=1,
        intervention_brief={
            "confirmed_at": "2026-07-30T12:00:00+08:00",
        },
        input_profile={"address": "深圳市南山区"},
        legacy_seed_path=seed_path,
        evidence_batch_path=batch_path,
    )
    source = next(
        item
        for item in result.decision_seed["source_registry"]
        if item["source_id"] == source_id
    )
    return source, source_id


@pytest.mark.integration
def test_shenzhen_v1_migrates_to_confirmed_input_2_sc_volume(tmp_path):
    seed_path = V1_SHENZHEN / "work" / "report_seed.json"
    batch_path = V1_SHENZHEN / "work" / "evidence_candidate_batch.json"
    if not seed_path.is_file() or not batch_path.is_file():
        pytest.skip("V1 Shenzhen acceptance project is unavailable")
    legacy_seed = json.loads(seed_path.read_text(encoding="utf-8"))
    sc_pages = [
        page
        for page in legacy_seed["page_manifest"]
        if page["section_id"] in {"SC1", "SC2", "SC3"}
    ]

    output_root = tmp_path / "shenzhen-v2"
    output_root.mkdir()
    legacy_report = output_root / "report.html"
    legacy_report.write_bytes(b"legacy formal-looking report")

    result = LegacyProjectReportMigrator().migrate(
        V1_SHENZHEN,
        output_root,
        project_id="SZ-A002-0113",
        project_name="深圳宝安中心区 A002-0113 前策与约束协同",
        imported_at="2026-07-23T12:00:00+08:00",
        as_of="2026-07-23",
        selected_mode=2,
        intervention_brief={
            "user_goal": "在方案启动前完成市场、客群、场地与约束基线",
            "decision_audience": "设计、投资与项目操盘负责人",
            "decision_questions": [
                "哪些市场与客群假设值得进入产品策划？",
                "哪些约束必须前置进入设计任务书？",
            ],
            "priorities": {
                "market_and_customer": 0.35,
                "site_and_constraints": 0.40,
                "evidence_boundary": 0.25,
            },
            "prohibited_conclusions": [
                "未经用户提交候选方案而形成的方案比选或主推结论",
                "未经真实成交和成本校准的售价、去化或收益承诺"
            ],
            "confirmed_at": "2026-07-23T12:00:00+08:00",
        },
        input_profile={
            "address": "深圳市宝安中心区 A002-0113 宗地",
            "constraint_sources": [{"source_ref": "client://招标法定资料"}],
        },
        legacy_seed_path=seed_path,
        evidence_batch_path=batch_path,
        page_manifest_override=sc_pages,
        supplemental_sources=[
            {
                "source_id": PENGUIN_SOURCE_ID,
                "source_type": "government_web",
                "title": "腾讯企鹅岛已形成员工通勤需求",
                "publisher": "深圳市交通运输局",
                "canonical_url": (
                    "https://jtys.sz.gov.cn/jtzx/wycx/gjcx/cxtx/"
                    "content/post_12902975.html"
                ),
                "published_at": "2026-07-21",
                "captured_at": "2026-07-24T09:00:00+08:00",
                "snapshot_excerpt": "企鹅岛已有数万名员工日常通勤。",
            }
        ],
        supplemental_claims=[
            {
                "claim_id": PENGUIN_CLAIM_ID,
                "section_id": "SC2",
                "field_name": "future_demand_event_scan",
                "statement": "企鹅岛运营已构成现实就业需求事件。",
                "status": "qualified",
                "evidence_type": "observed_fact",
                "source_refs": [PENGUIN_SOURCE_ID],
                "allowed_uses": ["future_customer_hypothesis"],
                "prohibited_uses": ["direct_employee_headcount_to_home_sales"],
            }
        ],
    )

    profile = result.decision_seed["meta"]["analysis_profile"]
    assert profile["selected_mode"] == 2
    assert profile["mode_status"] == "confirmed"
    assert len(result.report_document["page_manifest"]) == 3
    assert result.decision_seed["project_panorama"]["included_units"] == [
        "SC1",
        "SC2",
        "SC3",
    ]
    assert result.decision_seed["project_panorama"]["required_units"] == (
        profile["required_units"]
    )
    assert result.report_document["qa"]["missing_required_units"] == [
        "AD1",
        "AD2",
        "AD3",
        "AD4",
        "VA2",
        "VA3",
        "CS",
    ]
    assert result.report_document["qa"]["delivery_ready"] is False
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
    assert not (result.output_root / "report.html").exists()
    archived_report = result.output_root / "invalid-legacy-report.html"
    assert archived_report.read_bytes() == b"legacy formal-looking report"
    preview_path = result.output_root / "chapter-preview.html"
    assert preview_path.is_file()
    preview_html = preview_path.read_text(encoding="utf-8")
    assert 'data-dds-artifact="chapter-preview"' in preview_html
    assert "章节工作稿 · 非正式交付" in preview_html
    assert (
        '.print-page::after {\n    content: "章节工作稿 · 非正式交付";'
    ) in preview_html
    assert (result.output_root / "evidence-workbook.json").is_file()
    snapshot_path = (
        result.output_root
        / "web-source-snapshots"
        / f"{PENGUIN_SOURCE_ID}.json"
    )
    assert snapshot_path.is_file()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    source = next(
        item
        for item in result.decision_seed["source_registry"]
        if item["source_id"] == PENGUIN_SOURCE_ID
    )
    assert source["qualification_status"] == "qualified"
    assert source["snapshot_hash"] == snapshot["snapshot_hash"]
    assert source["snapshot_ref"] == (
        f"web-source-snapshots/{PENGUIN_SOURCE_ID}.json"
    )
    assert "future_demand_event" in source["allowed_uses"]
    assert {
        item["claim_id"] for item in result.decision_seed["claims"]
    } >= {PENGUIN_CLAIM_ID}
    assert {
        item["claim_id"] for item in result.frozen_package["claims"]
    } >= {PENGUIN_CLAIM_ID}
    assert {
        item["claim_id"]
        for item in result.evidence_workbook["claim_candidates"]
    } >= {PENGUIN_CLAIM_ID}


def test_extracted_asset_stays_unqualified_until_evidence_admission(tmp_path):
    source, _ = _migrate_single_text_asset(
        tmp_path,
        asset_name="深圳南山项目说明.txt",
        asset_text="深圳南山测试项目的内部项目说明。",
    )

    assert source["extraction_status"] == "extracted"
    assert source["status"] == "needs_review"
    assert source["qualification_status"] == "needs_review"
    assert source["allowed_uses"] == []
    assert source["qualification_reasons"] == [
        "evidence_admission_pending",
    ]


def test_extracted_other_project_asset_cannot_support_decision_fields(tmp_path):
    source, _ = _migrate_single_text_asset(
        tmp_path,
        asset_name="上海浦东另一项目任务书.txt",
        asset_text="上海浦东另一项目的任务书，不属于深圳南山测试项目。",
    )

    assert source["extraction_status"] == "extracted"
    assert source["status"] in {"needs_review", "rejected"}
    assert source["qualification_status"] in {"needs_review", "rejected"}
    assert source["allowed_uses"] == []
    assert any(
        "project identity and geography are unverified" in limitation.lower()
        for limitation in source["limitations"]
    )


def test_migration_refuses_to_overwrite_existing_legacy_report_archive(tmp_path):
    output_root = tmp_path / "existing-output"
    output_root.mkdir()
    legacy_report = output_root / "report.html"
    archived_report = output_root / "invalid-legacy-report.html"
    legacy_report.write_bytes(b"legacy report")
    archived_report.write_bytes(b"existing archive")

    with pytest.raises(
        FileExistsError,
        match="invalid-legacy-report.html",
    ):
        LegacyProjectReportMigrator().migrate(
            tmp_path / "unused-source",
            output_root,
            project_id="collision-test",
            project_name="迁移归档冲突测试",
            imported_at="2026-07-30T12:00:00+08:00",
            as_of="2026-07-30",
            selected_mode=1,
            intervention_brief={"confirmed_at": "2026-07-30T12:00:00+08:00"},
            input_profile={"address": "深圳"},
            legacy_seed_path=tmp_path / "unused-seed.json",
            evidence_batch_path=tmp_path / "unused-evidence.json",
        )

    assert legacy_report.read_bytes() == b"legacy report"
    assert archived_report.read_bytes() == b"existing archive"
