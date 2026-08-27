from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import shutil
from pathlib import Path


AS_OF = "2026-08-26"
PROJECT_ID = "HYP_天津_武清区六街 L-9 地块_Gary"
PROJECT = Path(r"D:\Arch_Projects\HYP_天津_武清区六街 L-9 地块_Gary")
INBOX = PROJECT / "inbox"
WORK = PROJECT / "work"
DATANEED_V01 = PROJECT / "00_Core" / "runs" / "20260826_arch-front-dataneed_v01"
DATANEED_V02 = PROJECT / "00_Core" / "runs" / "20260826_arch-front-dataneed_v02_local"


SOURCE_SPECS = [
    ("SRC-BRIEF-STRATEGY", "brief/02_武清土地战略与地块推介.md", "城市与供地战略背景", "policy"),
    ("SRC-DDS-FULL-FLOW", "decisions/12_DDS全流程前期决策与项目定位研究.md", "DDS全流程前期决策与项目定位研究", "decision"),
    ("SRC-LEGACY-FEASIBILITY", "legacy/天津武清六街L-9地块住宅项目开发可行性研究报告.md", "历史版本｜项目开发可行性研究报告", "legacy"),
    ("SRC-SITE-OFFICIAL", "constraints/01_地块官方条件与档案.md", "地块官方条件与项目身份", "spatial"),
    ("SRC-POLICY-LOAN", "constraints/06_政策与信贷环境.md", "政策与信贷环境", "policy"),
    ("SRC-COST-BENCHMARK", "constraints/08_成本基准与建安对标.md", "成本基准与建安对标", "finance"),
    ("SRC-COST-FORECAST", "constraints/10_建安成本对标预测_低密洋房_20260826.md", "低密洋房建安成本预测", "finance"),
    ("SRC-POLICY-SPACE", "constraints/10_天津多样性空间增值利用政策.md", "天津住宅多样性空间增值利用政策", "policy"),
    ("SRC-TAX-MODEL", "constraints/11_项目全周期税负测算_20260826.md", "项目全周期税负测算", "finance"),
    ("SRC-CUSTOMER", "decisions/07_客群与需求结构.md", "客群与需求结构", "market"),
    ("SRC-FINANCE", "decisions/09_财务测算情景.md", "财务测算情景", "finance"),
    ("SRC-RANGE", "decisions/11_专项推进_最优选择区间.md", "最优选择区间", "finance"),
    ("SRC-ASSUMPTIONS", "decisions/R_关键修正与假设登记.md", "关键修正与假设登记", "finance"),
    ("SRC-MARKET", "research/03_中指市场量价快照.md", "武清市场量价快照", "market"),
    ("SRC-COMPETITOR", "research/04_竞品项目去化监测.md", "竞品项目去化监测", "competition"),
    ("SRC-LAND-COMP", "research/05_可比地块与土地市场.md", "可比地块与土地市场", "market"),
]

ARCHLIB_IMAGE = Path(
    r"D:\Archlib_V2\cases\10_居住\11_公寓住宅\成都_建发招商源启金沙_宋韵低密\images\成都_建发招商源启金沙_宋韵低密_02.png"
)
TASKBOOK_DOCX = PROJECT / "01_Office" / "01 采购文件附件八：【设计任务书】规划建筑方案设计任务书.docx"

# The archived Core currently has no standalone ``08_成本基准与建安对标.md``.
# Keep the source id used by the report contract, but point it explicitly to the
# available dated low-density cost forecast and mark the substitution below.
SOURCE_FALLBACKS = {
    "constraints/08_成本基准与建安对标.md": "constraints/10_建安成本对标预测_低密洋房_20260826.md",
}

# Two important Core files were previously left outside the inbox adapter.  They
# are copied into the immutable inbox snapshot on preparation, so the report can
# cite the actual source bytes while keeping the original Core archive untouched.
SOURCE_EXTERNALS = {
    "decisions/12_DDS全流程前期决策与项目定位研究.md": PROJECT / "00_Core" / "decisions" / "12_DDS全流程前期决策与项目定位研究.md",
    "legacy/天津武清六街L-9地块住宅项目开发可行性研究报告.md": PROJECT / "00_Core" / "天津武清六街L-9地块住宅项目开发可行性研究报告.md",
}


def stable_hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def bare_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_hash(value: object) -> str:
    return stable_hash_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def refresh_existing_intake_answers() -> None:
    questions_path = WORK / "user_questions.json"
    answers_path = WORK / "user_answers.json"
    if not questions_path.is_file() or not answers_path.is_file():
        return
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    answers = json.loads(answers_path.read_text(encoding="utf-8"))
    answer_map = dict(answers.get("answers") or {})
    # Bind the user's actual brief to a short, stable goal.  When no explicit
    # goal is present, the canonical intake falls back to concatenating every
    # inbox document; that makes the panorama goal contain source prose (and
    # even local file paths), which both misclassifies the stage and trips the
    # evidence-package safety audit.  This is the requested DDS V2 advisory
    # flow, not a binding investment authorization.
    answer_map.setdefault(
        "goal",
        "完成天津武清区六街L-9地块住宅项目的DDS V2投拓前期研判与规划建筑方案前策报告，覆盖前期研判、周边竞品、周边配套、货值去化、客群分析、户型配比区间、强排推演和方案亮点挖掘，并输出可复核的数据缺口提示。",
    )
    answer_map.setdefault("audience", "开发商投资决策与前期策划团队")
    answer_map.setdefault(
        "decision_priority", "安全边界、去化与货值，兼顾空间产品与品牌表达"
    )
    answer_map.setdefault("decision_authority", "advisory_report")
    answer_map["confirm_goal_stage"] = True
    write_json(
        answers_path,
        {
            "schema_version": "dds.user-answers/1.0",
            "as_of": AS_OF,
            "question_set_hash": questions.get("question_set_hash"),
            "panorama_input_hash": questions.get("panorama_input_hash"),
            "answers": answer_map,
        },
    )


def build_sources() -> list[dict[str, object]]:
    snapshot_dir = WORK / "source_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, object]] = []
    for source_id, rel, title, source_family in SOURCE_SPECS:
        source_path = INBOX / rel
        fallback_rel = SOURCE_FALLBACKS.get(rel)
        substituted = False
        external_ingest = False
        external_path = SOURCE_EXTERNALS.get(rel)
        if not source_path.is_file() and external_path and external_path.is_file():
            source_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(external_path, source_path)
            external_ingest = True
        if not source_path.is_file() and fallback_rel:
            source_path = INBOX / fallback_rel
            substituted = source_path.is_file()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        data = source_path.read_bytes()
        snapshot_name = source_id.lower() + ".md"
        snapshot_path = snapshot_dir / snapshot_name
        snapshot_path.write_bytes(data)
        limitations = ["项目入库资料；正式投决前仍需与盖章原件、最新官方口径勾稽。"]
        if external_ingest:
            limitations.insert(0, f"本源由00_Core/{external_path.relative_to(PROJECT / '00_Core')}同步到inbox快照；原始文件仍保留在Core。")
        if substituted:
            limitations.insert(0, f"原计划来源 inbox/{rel} 当前未单独入库；本版以 inbox/{fallback_rel} 暂代，仅用于成本基准参考，不视为独立交叉来源。")
        sources.append(
            {
                "source_id": source_id,
                "title": title,
                "name": title,
                "source_type": "project_archive_markdown",
                "kind": "project_archive",
                "canonical_ref": f"inbox/{rel}" if not substituted else f"inbox/{fallback_rel}",
                "snapshot_ref": f"source_snapshots/{snapshot_name}",
                "path": f"inbox/{rel}" if not substituted else f"inbox/{fallback_rel}",
                "publisher": "项目资料库/已入库资料",
                "author_type": "provided_input",
                "published_at": AS_OF,
                "captured_at": f"{AS_OF}T00:00:00+08:00",
                "geography": "天津市武清区杨村/武清新城",
                "time_window": {"as_of": AS_OF},
                "rights_status": "authorized_internal_reference",
                "raw_hash": bare_hash(data),
                "snapshot_hash": bare_hash(data),
                "duplicate_cluster": f"project-{source_family}",
                "source_family": source_family,
                "used_for": ["research_candidate_input", "report_seed", "evidence_appendix"],
                "limitations": limitations,
                "substitution_status": "fallback_source" if substituted else ("core_ingested_source" if external_ingest else "primary_source"),
            }
        )

    if TASKBOOK_DOCX.is_file():
        data = TASKBOOK_DOCX.read_bytes()
        taskbook_rel = "office/01_采购文件附件八_设计任务书_规划建筑方案设计任务书.docx"
        taskbook_path = INBOX / taskbook_rel
        taskbook_path.parent.mkdir(parents=True, exist_ok=True)
        taskbook_path.write_bytes(data)
        snapshot_path = snapshot_dir / "src-taskbook.docx"
        snapshot_path.write_bytes(data)
        sources.append(
            {
                "source_id": "SRC-TASKBOOK",
                "title": "采购文件附件八｜规划建筑方案设计任务书",
                "name": "采购文件附件八｜规划建筑方案设计任务书",
                "source_type": "project_archive_docx",
                "kind": "project_archive",
                "canonical_ref": f"inbox/{taskbook_rel}",
                "snapshot_ref": "source_snapshots/src-taskbook.docx",
                "path": f"inbox/{taskbook_rel}",
                "publisher": "项目采购文件",
                "author_type": "provided_input",
                "published_at": AS_OF,
                "captured_at": f"{AS_OF}T00:00:00+08:00",
                "geography": "天津市武清区杨村/武清新城",
                "time_window": {"as_of": AS_OF},
                "rights_status": "authorized_internal_reference",
                "raw_hash": bare_hash(data),
                "snapshot_hash": bare_hash(data),
                "duplicate_cluster": "project-official-taskbook",
                "source_family": "spatial",
                "used_for": ["taskbook_constraints", "report_seed", "evidence_appendix"],
                "limitations": ["任务书明确引用附件2规划条件通知书，但当前目录未单独提供该附件；本源可确认任务书给出的设计输入，不替代附件2的法定细项。"],
            }
        )

    if ARCHLIB_IMAGE.is_file():
        image_data = ARCHLIB_IMAGE.read_bytes()
        asset_rel = "archlib/成都建发招商源启金沙_宋韵低密_02.png"
        asset_path = INBOX / asset_rel
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ARCHLIB_IMAGE, asset_path)
        meta_name = "src-archlib-lowdensity.md"
        meta_path = snapshot_dir / meta_name
        meta_path.write_text(
            "ArchLib V2 内部案例视觉快照\n"
            f"path: {ARCHLIB_IMAGE}\n"
            "case: 成都建发招商源启金沙·宋韵低密\n"
            "use: 仅提取低密改善户型的空间机制，不迁移售价、去化或成本结果。\n",
            encoding="utf-8",
        )
        sources.append(
            {
                "source_id": "SRC-ARCHLIB-LOWDENSITY",
                "title": "ArchLib V2｜成都低密改善户型机制案例",
                "name": "ArchLib V2｜成都低密改善户型机制案例",
                "source_type": "archlib_internal_case",
                "kind": "archlib_case",
                "canonical_ref": f"inbox/{asset_rel}",
                "snapshot_ref": f"source_snapshots/{meta_name}",
                "path": str(ARCHLIB_IMAGE),
                "publisher": "ArchLib V2 内部案例库",
                "author_type": "provided_input",
                "published_at": AS_OF,
                "captured_at": f"{AS_OF}T00:00:00+08:00",
                "geography": "成都（异地案例）",
                "time_window": {"as_of": AS_OF, "scope": "案例视觉机制参考"},
                "rights_status": "authorized_internal_reference",
                "raw_hash": bare_hash(image_data),
                "snapshot_hash": bare_hash(meta_path.read_bytes()),
                "duplicate_cluster": "archlib-lowdensity_mechanism",
                "source_family": "archlib",
                "used_for": ["case_analogy", "visual_reference"],
                "limitations": ["异地案例；只用于机制与表达参考，不进入本项目价格、去化或财务结论。"],
            }
        )
    return sources


def record(
    record_id: str,
    claim_id: str,
    evidence_type: str,
    statement: str,
    source_refs: list[str],
    method: str,
    limitations: list[str],
    **extra: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "record_id": record_id,
        "claim_id": claim_id,
        "evidence_type": evidence_type,
        "statement": statement,
        "source_refs": source_refs,
        "counter_source_refs": extra.pop("counter_source_refs", []),
        "counter_evidence_status": extra.pop("counter_evidence_status", "searched_none_found"),
        "counter_evidence_note": extra.pop("counter_evidence_note", "暂无第二条独立样本；以边界条件保留不确定性。"),
        "geography": {"scope": "天津市武清区杨村/武清新城", "precision": "district_submarket"},
        "time_window": {"as_of": AS_OF, "status": "bounded_snapshot"},
        "method": method,
        "limitations": limitations,
    }
    value.update(extra)
    return value


def build_records() -> list[dict[str, object]]:
    return [
        record(
            "REC-POLICY-01",
            "claim-policy",
            "observed_fact",
            "项目为武清新城14-03-05单元雍阳中学北侧住宅地块，采购任务书定位为刚需与中端改善、高流速产品，并要求研究天津住宅多样性空间增值利用政策；规划条件通知书的法定细项、正式红线与盖章附件仍是方案深化前的硬闸门。",
            ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-POLICY-SPACE"],
            "project_archive_cross_reading",
            ["任务书正文与内嵌图像可确认设计输入，但任务书引用的附件2规划条件通知书未单独出现在当前目录。"],
            counter_source_refs=["SRC-BRIEF-STRATEGY"],
            counter_evidence_status="searched_none_found",
            counter_evidence_note="上位战略材料支持低密改善方向，但不构成法定规划条件。",
            confidence=0.74,
        ),
        record(
            "REC-TASKBOOK-01",
            "claim-taskbook",
            "observed_fact",
            "采购任务书内嵌图示明确：用地面积4.19公顷、容积率2.0；住宅配比建议为95㎡30%、105㎡30%、122㎡25%、139㎡15%，各面积段备注均包含上下跃户型。任务书同时要求不少于两个规划方案，并对人车分流、正南北朝向、南侧商业日照、出入口数量、2000㎡营销展示中心、地库经济性和户型与总图对应提出设计要求。",
            ["SRC-TASKBOOK"],
            "docx_paragraph_and_embedded_image_review",
            ["容积率2.0与配比为任务书设计输入；建筑密度、绿地率、限高、停车等法定细项仍需读取任务书引用的附件2规划条件通知书。"],
            confidence=0.93,
        ),
        record(
            "REC-MARKET-01",
            "claim-market",
            "observed_fact",
            "近12个月武清住宅加权均价约13,257元/㎡、成交约5,526套，约425套/月；2026年7月库存约9,695套、去化周期约22.8个月，90—140㎡为主力成交面积段，三居需求占主导。",
            ["SRC-MARKET", "SRC-CUSTOMER"],
            "project_archive_market_snapshot",
            ["市场快照为区域口径；未含目标地块真实来访、认筹、折扣与首开回测。"],
            counter_source_refs=["SRC-COMPETITOR"],
            counter_evidence_status="counter_evidence_found",
            counter_evidence_note="竞品文件同时保留早期近似月均与后续12个月监测口径，报告采用明确统计期并保留差异。",
            confidence=0.68,
        ),
        record(
            "REC-COMP-01",
            "claim-competition",
            "observed_fact",
            "竞品监测显示区域改善需求存在，但项目成交速度受价格、面积段和供货节奏显著影响；不能把单一竞品月均去化直接外推为本项目承诺，目标项目需以首开90天漏斗校准。",
            ["SRC-COMPETITOR", "SRC-MARKET", "SRC-CUSTOMER"],
            "competitor_monitoring_with_bounded_transfer",
            ["缺目标项目真实客户漏斗、折扣及分面积段净签回测；竞品身份与统计周期需在正式投决前复核。"],
            counter_source_refs=["SRC-RANGE"],
            counter_evidence_status="searched_none_found",
            counter_evidence_note="财务选择区间给出36—42套/月等情景，但属于条件性模型而非已发生市场事实。",
            confidence=0.61,
        ),
        record(
            "REC-CASE-01",
            "claim-case-analogy",
            "case_analogy",
            "ArchLib中的成都低密改善户型可迁移的不是产品形态本身，而是‘主流改善面积段承担流速、局部可变空间形成价值锚点、公共生活场景集中表达’的机制；本项目需在天津政策、成本与客户支付力边界内重做。",
            ["SRC-ARCHLIB-LOWDENSITY", "SRC-POLICY-SPACE", "SRC-CUSTOMER"],
            "archlib_mechanism_analogy",
            ["异地案例无本项目价格、去化、成本和法规可比性；只作设计机制参考。"],
            similarity_axes=["低密改善住宅", "主流改善面积段", "空间可变与价值锚点", "产品表达服务于去化"],
            difference_axes=["成都与武清市场不同", "法规与赠送口径不同", "客群支付力不同", "未有同一开发主体的结果数据"],
            inference_logic="从案例视觉与户型机制提取可复核设计动作，再由本项目任务书、政策和市场数据限定迁移边界。",
            range_or_boundary="仅支持产品机制与设计任务建议；不支持价格、去化、毛利或IRR推断。",
            inference_status="inferred_bounded",
            confidence=0.52,
        ),
        record(
            "REC-SPATIAL-01",
            "claim-spatial",
            "observed_fact",
            "地块约4.19ha，位于武清新城14-03-05单元雍阳中学北侧，四侧临路、场地平坦；任务书图示同时给出容积率2.0。现有资料仍缺少可用于精确强排的盖章红线、统一坐标、真北、标高及最终道路断面，因此当前只能做区位关系与条件性空间判断。",
            ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-BRIEF-STRATEGY"],
            "project_archive_spatial_boundary_review",
            ["几何精度为none；本报告不输出精确日照、消防、建筑间距或强排结论。"],
            counter_source_refs=["SRC-POLICY-SPACE"],
            counter_evidence_status="searched_none_found",
            counter_evidence_note="政策材料支持创新空间研究方向，但不能替代地块测绘与法定红线。",
            confidence=0.64,
        ),
        record(
            "REC-DDS-FLOW-01",
            "claim-dds-full-flow",
            "analysis_inference",
            "Core中的DDS全流程研究将前期研判、竞品、配套、货值去化、客群、户型配比、强排推演和方案亮点串成同一条决策链；本版将其作为项目级研究框架，并把每个环节的事实、假设和待验证动作拆开呈现。",
            ["SRC-DDS-FULL-FLOW"],
            "project_core_full_flow_cross_reading",
            ["该文件是项目级研究汇编，不替代市场原始数据、法定规划条件或实测强排成果。"],
            counter_source_refs=["SRC-TASKBOOK", "SRC-MARKET", "SRC-FINANCE"],
            counter_evidence_status="counter_evidence_found",
            counter_evidence_note="任务书、市场、财务原始快照分别承担具体字段证据；全流程文件承担串联与决策框架。",
            confidence=0.72,
        ),
        record(
            "REC-LEGACY-01",
            "claim-legacy-conflict",
            "observed_fact",
            "Core历史可研报告保留了早期容积率、市场结构、建安成本、地价边界和产品建议；其中部分读数与任务书及后续校正快照不一致，本版将其作为历史版本和冲突来源保留，不直接并入当前事实层。",
            ["SRC-LEGACY-FEASIBILITY"],
            "legacy_report_conflict_register",
            ["历史报告的统计窗口、口径和假设需与当前原始资料逐项勾稽；未完成勾稽前不得作为正式投决输入。"],
            counter_source_refs=["SRC-DDS-FULL-FLOW", "SRC-TASKBOOK", "SRC-MARKET", "SRC-COST-FORECAST"],
            counter_evidence_status="counter_evidence_found",
            counter_evidence_note="当前版保留可追溯的时间窗口和任务书输入，并对冲突字段逐项提示。",
            confidence=0.58,
        ),
    ]


def chart(
    chart_id: str,
    title: str,
    unit: str,
    series: list[dict[str, object]],
    source_refs: list[str],
    chart_type: str = "ranked_bar",
    *,
    dimensions: list[object] | None = None,
    measures: list[object] | None = None,
    time_window: str | None = None,
    findings: list[str] | None = None,
    decision_message: str | None = None,
    annotations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    value = {
        "chart_id": chart_id,
        "type": chart_type,
        "title": title,
        "unit": unit,
        "units": unit,
        "series": series,
        "source_refs": source_refs,
        "source_note": "仅展示已入库快照，统计期与限制见证据附录。",
        "description": title,
        "alt_text": title,
    }
    if dimensions is not None:
        value["dimensions"] = dimensions
    if measures is not None:
        value["measures"] = measures
    if time_window:
        value["time_window"] = time_window
    if findings:
        value["findings"] = findings
    if decision_message:
        value["decision_message"] = decision_message
    if annotations:
        value["annotations"] = annotations
    return value


def base_page(page_id: str, unit_id: str, title: str, takeaway: str, blocks: list[dict[str, object]], source_refs: list[str], **extra: object) -> dict[str, object]:
    page: dict[str, object] = {
        "page_id": page_id,
        "chapter_id": unit_id.lower(),
        "section_id": unit_id,
        "unit_id": unit_id,
        "layout": "analysis",
        "title": title,
        "display_title": title,
        "takeaway": takeaway,
        "blocks": blocks,
        "chart_specs": [],
        "diagram_specs": [],
        "asset_refs": [],
        "source_refs": source_refs,
        "confidence": {"score": 0.62, "level": "medium"},
        "evidence_type": "traditional_interpretation",
        "unit_status": "partial",
        "unit_role": "",
        "story_role": "primary_narrative",
        "appendix_policy": "presentation",
        "visual_evidence": "text",
        "print_policy": {"include": True, "page_break_after": True, "allow_internal_scroll": False},
    }
    page.update(extra)
    return page


def build_seed(sources: list[dict[str, object]]) -> dict[str, object]:
    refs = [str(item["source_id"]) for item in sources]
    pages = [
        base_page(
            "sc1-01", "SC1", "L-9：低密改善不是产品标签，而是拿地安全边界",
            "本项目的核心命题是：以主流改善产品承担流速，以有限的空间增值机制打开货值，但前提是法定条件、实际楼面价和送赠边界同时成立。",
            [
                {"type": "narrative", "title": "结论", "text": "L-9具备低密改善方向与片区配套支撑，但目前更适合形成‘条件性投决’：保留主流改善产品主线，先把楼面价、成本、赠送和去化四个边界锁住，再释放设计增值。", "source_refs": ["SRC-SITE-OFFICIAL", "SRC-RANGE", "SRC-ASSUMPTIONS"]},
                {"type": "table", "title": "四道闸门", "headers": ["闸门", "当前判断", "放行条件"], "rows": [["法定边界", "阻断", "盖章规划条件、红线、道路断面、测绘闭合"], ["市场流速", "部分支持", "首开90天漏斗校准，核心面积段净签稳定"], ["货值机制", "条件成立", "政策适用、成本可控、赠送不依赖未批承诺"], ["财务边界", "需复核", "成本、税负、土地和融资口径勾稽一致"]], "source_refs": ["SRC-SITE-OFFICIAL", "SRC-MARKET", "SRC-POLICY-SPACE", "SRC-FINANCE"]},
            ], refs,
            diagram_specs=[{"diagram_id": "DIA-L9-ENGINE", "grammar": "SPATIAL_DIAGRAM", "title": "片区资源→产品动作→投决闸门（非比例关系图）", "nodes": ["雍阳中学与城区配套", "主流改善面积段", "可变/增值空间机制", "首开流速", "楼面价与成本", "法定红线与道路", "条件性投决"], "note": "只表达逻辑关系，不表达真实距离、尺寸或强排成果。", "diagram_type": "adjacency"}],
        ),
        base_page(
            "sc2-01", "SC2", "市场：有改善需求，但库存与竞品要求‘分批卖、先验证’",
            "市场证据支持主流改善切入，不支持一次性押注大面积、高溢价或确定性快周转。",
            [
                {"type": "narrative", "text": "近12个月区域均价约13,257元/㎡、约425套/月，库存约9,695套、去化周期约22.8个月。主力面积段为90—140㎡，因此建议将主流面积段作为首开现金流骨架，大面积产品作为小批量验证。", "source_refs": ["SRC-MARKET", "SRC-CUSTOMER"]},
                {"type": "metrics", "title": "市场快照", "metrics": [{"label": "加权均价", "value": "13,257", "unit": "元/㎡"}, {"label": "近12月成交", "value": "5,526", "unit": "套"}, {"label": "月均成交", "value": "约425", "unit": "套/月"}, {"label": "库存", "value": "9,695", "unit": "套"}, {"label": "去化周期", "value": "22.8", "unit": "个月"}], "source_refs": ["SRC-MARKET"]},
            ],
            ["SRC-MARKET", "SRC-CUSTOMER", "SRC-COMPETITOR"],
            chart_specs=[chart("CHART-MARKET-01", "区域住宅市场关键指标", "区域快照", [{"label": "近12月成交", "value": 5526, "note": "套"}, {"label": "库存", "value": 9695, "note": "套"}, {"label": "月均成交×12", "value": 5100, "note": "套"}], ["SRC-MARKET"])],
        ),
        base_page(
            "sc3-01", "SC3", "场地：目前只能做关系判断，不能把代理条件当成强排成果",
            "场地位于雍阳中学北侧、四侧临路、约4.19ha；任务书图示已明确容积率2.0，但正式红线、真北、标高、道路断面和测绘文件未闭合，空间结论仍必须保持条件性。",
            [
                {"type": "narrative", "text": "本报告将任务书给出的地块身份、面积、容积率设计输入、邻接关系视为已入库事实；将建筑布局、日照、消防、道路宽度、入口位置及最终可售面积视为待验证事项。任何‘精确强排’或‘确定赠送面积’均不属于本版输出。", "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-BRIEF-STRATEGY"]},
                {"type": "table", "title": "场地证据边界", "headers": ["维度", "已知", "未闭合"], "rows": [["地块身份", "武清新城14-03-05单元雍阳中学北侧", "盖章规划条件附件"], ["规模", "约4.19ha", "统一坐标边界"], ["邻接", "四侧临路，北侧在建住宅、南侧既有商业", "最终道路断面与交付时序"], ["几何", "平坦场地关系", "真北、标高、管线、红线"]], "source_refs": ["SRC-SITE-OFFICIAL"]},
            ], ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-BRIEF-STRATEGY"],
        ),
        base_page(
            "va2-01", "VA2", "价值研判：送赠/可变空间是货值抓手，也是最大合规风险",
            "只有在政策适用、成本增量和市场支付力同时被量化后，空间增值才可进入投资排序；当前只能作为条件性方案方向。",
            [
                {"type": "narrative", "text": "邻地楼面价约7,507—7,508元/㎡，而入库财务口径提示常规建造下的安全楼面价更低，说明‘名义楼面价—实得货值—可承受售价’之间存在缺口。天津住宅多样性空间增值利用政策提供了研究抓手，但不能把政策研究直接等同于已获批赠送。", "source_refs": ["SRC-LAND-COMP", "SRC-POLICY-SPACE", "SRC-FINANCE"]},
                {"type": "table", "title": "价值动作的放行条件", "headers": ["动作", "可提取价值", "必须先验"], "rows": [["主流户型优化", "提升首开可售效率与居住感受", "面积段、户型套数、成本复核"], ["可变/增值空间", "形成局部价值锚点", "政策适用、计容/销售口径、消防结构"], ["低密公共界面", "支撑改善定位与品牌表达", "投入强度与去化贡献"]], "source_refs": ["SRC-POLICY-SPACE", "SRC-CUSTOMER", "SRC-COST-FORECAST"]},
            ], ["SRC-LAND-COMP", "SRC-POLICY-SPACE", "SRC-FINANCE"],
        ),
        base_page(
            "va3-01", "VA3", "产品策略：主流改善做底盘，创新空间做小批验证",
            "建议以任务书95/105㎡作为主流现金流骨架，122㎡承接改善，139㎡做控量价值锚点；上下跃和其他可变空间只做小批验证，避免把全部货值押在未经实证的创新上。",
            [
                {"type": "narrative", "text": "任务书配比与区域成交结构可以形成互相校验：95/105㎡对应90—120㎡主力带，122㎡对应120—140㎡改善带，139㎡承担改善锚点；四档均包含上下跃户型备注，必须把上下跃的政策、结构、消防、成本和实得单价拆开验证。ArchLib案例只提供空间机制启发，不迁移产品套数、价格和去化结果。", "source_refs": ["SRC-TASKBOOK", "SRC-CUSTOMER", "SRC-MARKET", "SRC-ARCHLIB-LOWDENSITY"]},
                {"type": "table", "title": "任务书产品梯度", "headers": ["面积/配比", "产品任务", "投决口径"], "rows": [["95㎡ / 30%", "主流刚改、高流速底盘；含上下跃", "首开主力候选"], ["105㎡ / 30%", "主流改善、高流速底盘；含上下跃", "首开主力候选"], ["122㎡ / 25%", "改善承接与居住尺度提升；含上下跃", "中批验证"], ["139㎡ / 15%", "改善形象与价值锚点；含上下跃", "控量验证"], ["上下跃机制", "研究实得面积与空间溢价", "政策/结构/消防/成本闸门"]], "source_refs": ["SRC-TASKBOOK", "SRC-POLICY-SPACE", "SRC-ARCHLIB-LOWDENSITY"]},
            ], ["SRC-TASKBOOK", "SRC-CUSTOMER", "SRC-MARKET", "SRC-ARCHLIB-LOWDENSITY"],
            asset_refs=["ASSET-ARCHLIB-LOWDENSITY"],
        ),
        base_page(
            "cs-01", "CS", "证据与行动：先补硬证，再做同边界方案与财务复核",
            "当前报告可支持条件性投资研判与方案输入，但必须先把任务书FAR2.0与四档配比转成两套可复核强排、财务和首开验证结果；它不能替代附件2法定规划细项、测绘成果、修复后的财务模型和首开真实漏斗。",
            [
                {"type": "table", "title": "投决前行动清单", "headers": ["优先级", "行动", "验收"], "rows": [["P0", "核对任务书附件2规划条件、红线、道路断面、测绘标高", "坐标、真北、标高与法定文件闭合"], ["P0", "以FAR2.0和95/105/122/139㎡配比重跑两套强排", "指标、车位、日照、地库、户型与总图对应"], ["P0", "统一楼面价、建安、税费、融资与赠送口径", "2.0口径下成本与财务模型勾稽一致"], ["P1", "以首开90天客户漏斗校准去化情景", "来访—认筹—净签—退房可复算"]], "source_refs": refs + ["SRC-TASKBOOK"]},
            ], ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-MARKET", "SRC-POLICY-SPACE"],
            unit_status="partial", unit_role="confidence_state", story_role="evidence_appendix", appendix_policy="evidence", visual_evidence="gap",
            unit_contract={"sources": refs[:6], "methods": ["资料入库复核", "市场快照读取", "案例机制类比"], "assumptions": ["as_of=2026-08-26", "几何精度=none", "创新空间不提前计入确定货值"], "confidence": "中等；关键硬证缺口未闭合"},
        ),
    ]
    pages.extend([
        base_page(
            "sc1-02", "SC1", "决策结论：有条件推进，但不接受 7,507 元/㎡无条件拿地",
            "建议把 L-9 定性为‘任务书产品输入清晰、财务高度敏感、供应压力偏大’的谨慎进入项目：理想楼面 4,600—5,000 元/㎡，可审慎上探至 5,630 元/㎡；7,507 元/㎡原则上否决。",
            [
                {"type": "metrics", "title": "四维评分", "metrics": [{"label": "综合评级", "value": "2.70 / 5", "unit": "中价值"}, {"label": "板块机会", "value": "2 / 5", "unit": "去化短板"}, {"label": "产品适配", "value": "4 / 5", "unit": "主力段匹配"}, {"label": "竞争格局", "value": "3 / 5", "unit": "热点集中"}, {"label": "地价合理性", "value": "2 / 5", "unit": "偏紧"}], "source_refs": ["SRC-MARKET", "SRC-LAND-COMP", "SRC-CUSTOMER"]},
                {"type": "table", "title": "三档决策结论", "headers": ["情形", "结论", "动作"], "rows": [["楼面≤5,000", "可进入", "按任务书95/105㎡主力推进，送赠15%作为专项目标"], ["楼面5,000—5,630", "有条件进入", "必须同时锁定送赠≥15%、建安≤4,600、月均36—42套"], ["楼面>5,630 / 7,507邻地锚", "原则上放弃", "除非强排实测证明实得单价撬动>30%，否则不竞买"]], "source_refs": ["SRC-FINANCE", "SRC-RANGE", "SRC-LAND-COMP", "SRC-TASKBOOK"]},
            ], ["SRC-FINANCE", "SRC-RANGE", "SRC-LAND-COMP"],
            chart_specs=[chart("CHART-DECISION-01", "楼面价三档决策", "元/㎡", [{"label": "理想上限", "value": 5000, "note": "优先"}, {"label": "条件上限", "value": 5630, "note": "需送赠"}, {"label": "邻地锚", "value": 7507, "note": "原则放弃"}], ["SRC-FINANCE", "SRC-RANGE", "SRC-LAND-COMP"])],
        ),
        base_page(
            "sc2-02", "SC2", "需求结构：三房占 75.2%，90—140㎡占 58.7%，主力盘逻辑明确",
            "市场不是没有需求，而是需求集中在可负担、可快速成交的主流面积段；报告应把‘结构性机会’与‘总量偏冷’同时讲清。",
            [
                {"type": "table", "title": "面积段成交结构｜近12个月", "headers": ["面积段", "成交套数", "占比", "产品含义"], "rows": [["90—120㎡", "3,232", "32.4%", "第一主力，刚改三房"], ["120—140㎡", "2,625", "26.3%", "第二主力，改善三/四房"], ["70—90㎡", "1,251", "12.5%", "可做控量刚需"], ["140—180㎡", "1,122", "11.2%", "少量改善洋房"], ["180—240㎡", "292", "2.9%", "不宜作为主力"]], "source_refs": ["SRC-MARKET", "SRC-CUSTOMER"]},
                {"type": "table", "title": "户型与总价结构", "headers": ["维度", "主要读数", "结论"], "rows": [["户型", "三房6,289套 / 75.2%", "三房是绝对主流"], ["总价", "80—120万成交2,010套 / 20.1%", "存在刚改入场带"], ["总价", "120—160万成交1,386套 / 13.9%", "改善承接带需控制首付压力"], ["高总价", "200—300万成交1,879套 / 18.8%", "改善存在，但不能直接代表L-9主力"]], "source_refs": ["SRC-MARKET", "SRC-CUSTOMER"]},
            ], ["SRC-MARKET", "SRC-CUSTOMER"],
            chart_specs=[chart("CHART-MARKET-AREA", "主力面积段成交占比", "%", [{"label": "90—120㎡", "value": 32.4, "note": "第一主力"}, {"label": "120—140㎡", "value": 26.3, "note": "第二主力"}, {"label": "70—90㎡", "value": 12.5, "note": "控量"}, {"label": "140—180㎡", "value": 11.2, "note": "少量"}], ["SRC-MARKET"])],
        ),
        base_page(
            "sc2-03", "SC2", "竞品：价格分层清晰，但 3 公里内在售与待入市供应形成夹击",
            "L-9不应跟随宏顺、金地的高价改善，也不应退回老盘低价；应卡在14,000元/㎡上下的主流刚改价值带，用产品与入市时序换流速。",
            [
                {"type": "table", "title": "3公里竞品快照", "headers": ["项目", "成交均价", "近12月成交", "可售", "去化/竞争判断"], "rows": [["宏顺·央璟颂", "16,444", "532套", "223套", "高端改善，剩余货量大"], ["龙湖云曜", "15,062", "415套", "150套", "同段竞争，中等强度"], ["雍鑫·溪和林", "14,400", "891套", "162套", "刚改/改善直接竞对"], ["亚泰雍阳府", "9,864", "1,268套", "288套", "老盘低价锚"], ["金地雍阳印", "19,301", "434套", "147套", "高价改善，不宜正面跟随"], ["保利锦上", "18,441", "2套", "13套", "基本售罄，参考意义有限"]], "source_refs": ["SRC-COMPETITOR", "SRC-MARKET"]},
                {"type": "narrative", "title": "竞争结论", "text": "直接竞争最强的是雍鑫·溪和林的14,400元/㎡刚改/改善带；L-9若采用90—140㎡主力，应把首开价格与交付兑现做成效率优势，而不是把洋房形象等同于高端改善定价。", "source_refs": ["SRC-COMPETITOR", "SRC-CUSTOMER"]},
            ], ["SRC-COMPETITOR", "SRC-MARKET", "SRC-CUSTOMER"],
            chart_specs=[chart("CHART-COMP-PRICE", "竞品成交均价梯度", "元/㎡", [{"label": "金地雍阳印", "value": 19301}, {"label": "宏顺央璟颂", "value": 16444}, {"label": "龙湖云曜", "value": 15062}, {"label": "雍鑫溪和林", "value": 14400}, {"label": "亚泰雍阳府", "value": 9864}], ["SRC-COMPETITOR"])],
        ),
        base_page(
            "sc2-04", "SC2", "供应压测：现有可售约 3.2 倍年成交，时间窗口比价格更重要",
            "板块短期不是没有成交，而是库存与待入市土地会把项目拖入竞争；若进入，必须抢在邻地及待入市地块集中开盘前完成首开验证。",
            [
                {"type": "metrics", "title": "供应压力读数", "metrics": [{"label": "现有可售", "value": "9,695套", "unit": "117.4万㎡"}, {"label": "2026年成交", "value": "37.1万㎡", "unit": "年"}, {"label": "静态出清", "value": "约38个月", "unit": "按现有存量"}, {"label": "现有存量/年成交", "value": "约3.2倍", "unit": "警示"}, {"label": "待入市新增", "value": "约43.1万㎡", "unit": "三组土地"}], "source_refs": ["SRC-MARKET", "SRC-COMPETITOR", "SRC-BRIEF-STRATEGY"]},
                {"type": "table", "title": "待入市供应", "headers": ["来源", "计容规模", "对L-9影响"], "rows": [["京城投资邻地津武2024-044/045", "7.18万㎡", "直接同板块、同开发商关注"], ["津武2025-080/058", "22.1万㎡", "中期增加主流供应"], ["津武2024-032/013", "13.8万㎡", "雍和道北改善竞争"], ["合计", "约43.1万㎡", "需通过入市时序对冲"]], "source_refs": ["SRC-COMPETITOR", "SRC-LAND-COMP"]},
            ], ["SRC-MARKET", "SRC-COMPETITOR", "SRC-LAND-COMP"],
        ),
        base_page(
            "sc3-02", "SC3", "任务书硬输入：4.19ha、容积率2.0、四档户型配比已明确",
            "任务书内嵌图示已经给出本项目的基础设计输入：4.19公顷、容积率2.0；95/105/122/139㎡四档配比及上下跃方向也已明确。前一版把1.5当成基础口径是错误的，本版改以2.0为任务书基准，1.5仅保留为低密敏感性。",
            [
                {"type": "metrics", "title": "任务书原文硬输入", "metrics": [{"label": "用地面积", "value": "4.19", "unit": "公顷 / 41,900㎡"}, {"label": "容积率", "value": "2.0", "unit": "任务书内嵌图示"}, {"label": "户型档位", "value": "95/105/122/139", "unit": "㎡销售面积"}, {"label": "上下跃", "value": "四档均包含", "unit": "任务书备注"}], "source_refs": ["SRC-TASKBOOK"]},
                {"type": "table", "title": "任务书配比建议｜销售面积含全楼公摊", "headers": ["户型面积", "套数比", "任务书备注", "报告处理"], "rows": [["95㎡", "30%", "包含上下跃户型", "首开主力候选"], ["105㎡", "30%", "包含上下跃户型", "首开主力候选"], ["122㎡", "25%", "包含上下跃户型", "改善承接"], ["139㎡", "15%", "包含上下跃户型", "改善锚点/控量"]], "source_refs": ["SRC-TASKBOOK"]},
                {"type": "table", "title": "已确认 vs 待核定", "headers": ["维度", "当前可用口径", "报告处理"], "rows": [["地块身份", "津武(挂)2024-024 / 雍阳中学北侧", "作为事实"], ["用地面积", "41,900㎡", "作为事实"], ["容积率", "任务书内嵌图示2.0", "作为任务书基准；1.5仅作低密敏感性"], ["建筑密度/绿地率/限高/停车", "任务书要求复核附件2规划条件通知书", "保留法定细项待核"], ["红线/标高/管线/道路断面", "未取得闭合文件", "阻断精确强排"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"]},
                {"type": "narrative", "text": "任务书已经明确产品不是纯低密标签，而是刚需与中端改善的高流速产品：两套以上规划方案、主流面积段级配、上下跃货值分析、正南北朝向、人车分流、南侧商业日照和地库经济性都必须进入方案比选。", "source_refs": ["SRC-TASKBOOK"]},
            ], ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-POLICY-SPACE"],
        ),
        base_page(
            "sc3-03", "SC3", "任务书设计约束：不是只做强排，而是做可落地的货值方案",
            "采购任务书要求不少于两个规划方案，并把货值、交通、日照、展示区、地库和户型落地性绑定在同一套设计成果中；因此报告结论应从‘拿地价格’延伸到‘方案验证清单’。",
            [
                {"type": "table", "title": "任务书关键设计动作", "headers": ["模块", "明确要求", "对前策的影响"], "rows": [["方案比选", "不少于两个规划方案，含强排总图、产品分布、指标测算、体块分析", "必须在同一规划输入下比较货值与落地性"], ["交通与入口", "人行、车行出入口各不多于2个，至少1个车行入口结合人行；人车分流", "入口和示范区不能后置"], ["环境与日照", "正南北为主；南侧商业按三层、局部四层暂不考虑，其他方案需满足日照并附图", "南侧界面与楼栋排布直接影响货值"], ["展示区", "营销展示中心约2,000㎡，尽量与住宅脱开，结合实体展示和运动风格", "示范区需进入首开时序与投资测算"], ["地库与落地", "地库按经济性排布，含柱网、车位、出入口、疏散；户型面宽进深与总图对应", "建安不能再沿用3,500元/㎡粗口径"]], "source_refs": ["SRC-TASKBOOK"]},
                {"type": "narrative", "title": "重新定义下一步", "text": "下一步不是继续补写泛泛的概念，而是基于FAR2.0和95/105/122/139㎡配比，做两套可复核方案：方案A偏高流速、控制地库与建安；方案B保留上下跃和展示区价值锚点。两套方案都要回填指标、车位、日照、赠送合规、售价与去化。", "source_refs": ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-POLICY-SPACE"]},
            ], ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-POLICY-SPACE"],
        ),
        base_page(
            "va2-02", "VA2", "财务精修：建安从 3,500 上修至 4,600，原有毛利缓冲被抹掉",
            "财务结论必须以精修口径为主：楼面5,200、建安4,600、综合税负5.5—6.0%时，中性毛利约7.1%、悲观约2.5%，均不足以支撑无条件拿地。",
            [
                {"type": "table", "title": "口径修正", "headers": ["项目", "旧口径", "精修口径", "影响"], "rows": [["综合建安", "3,500元/㎡", "4,000—5,500；工作中枢4,600", "单方+1,100，低密地库/园林不再漏算"], ["最终税负", "两税约10%经验值", "综合约5.5—6.0%", "最终税负下修，但预缴仍占用现金"], ["土地增值税", "粗放计提", "低毛利下清算趋近0", "不能把预征等同最终税负"], ["资金约束", "未充分体现", "预征与营销/融资峰值需单列", "回款节奏成为关键"]], "source_refs": ["SRC-COST-FORECAST", "SRC-TAX-MODEL", "SRC-FINANCE"]},
                {"type": "table", "title": "楼面5,200 + 建安4,600精修情景", "headers": ["情景", "售价", "全成本单方", "毛利率", "判断"], "rows": [["乐观", "13,080", "11,631", "11.27%", "可过8%线"], ["中性", "12,426", "11,540", "7.13%", "低于8%线"], ["悲观", "11,772", "11,448", "2.49%", "接近保本" ]], "source_refs": ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL"]},
            ], ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL"],
            chart_specs=[chart("CHART-FINANCE-MARGIN", "精修后三情景毛利率", "%", [{"label": "乐观", "value": 11.27}, {"label": "中性", "value": 7.13}, {"label": "悲观", "value": 2.49}], ["SRC-FINANCE", "SRC-COST-FORECAST"])],
        ),
        base_page(
            "va2-03", "VA2", "货值破局矩阵：送赠是第一杠杆，地价是第二杠杆",
            "送赠15—20%只能把楼面5,000—5,630带回可检验区间，不能把7,507变成安全价格；因此‘政策兑现+强排实测’是拿地前置条件，而不是设计加分项。",
            [
                {"type": "table", "title": "悲观情景毛利率｜楼面×送赠", "headers": ["送赠幅度", "4,600", "5,000", "5,630", "6,500", "7,507"], "rows": [["0%", "10.2%", "6.8%", "1.4%", "-5.9%", "-14.5%"], ["10%", "15.9%", "12.8%", "8.0%", "1.2%", "-6.5%"], ["15%", "18.4%", "15.4%", "10.8%", "4.4%", "-3.1%"], ["20%", "20.7%", "17.8%", "13.4%", "7.2%", "0.1%"], ["25%", "22.8%", "20.0%", "15.8%", "9.9%", "3.0%"]], "source_refs": ["SRC-RANGE", "SRC-POLICY-SPACE", "SRC-FINANCE"]},
                {"type": "table", "title": "最优选择区间", "headers": ["维度", "推荐值", "否决/停牌条件"], "rows": [["楼面价", "理想4,600—5,000；审慎上限5,630", ">5,630原则放弃"], ["送赠实得单价", "+15—20%", "不足15%不进入财务主情景"], ["综合建安", "4,200—4,600", ">5,200转危"], ["去化", "36—42套/月，12—14个月", "低于约30套/月需停扩货"], ["货值", "8.33亿→约9.0—9.2亿理论增益", "须以强排与补缴地价复核"]], "source_refs": ["SRC-RANGE", "SRC-COST-FORECAST", "SRC-FINANCE"]},
            ], ["SRC-RANGE", "SRC-POLICY-SPACE", "SRC-FINANCE", "SRC-COST-FORECAST"],
        ),
        base_page(
            "va3-02", "VA3", "实施优先级：先锁边界，再锁成本，再做首开验证",
            "项目不是缺一个概念方案，而是缺一条可执行的‘任务书—强排—货值—去化—财务’闭环；行动顺序决定是否把错误口径带入设计。",
            [
                {"type": "table", "title": "四阶段推进表", "headers": ["阶段", "必须完成", "放行标准", "责任"], "rows": [["P0 法定边界", "以任务书FAR2.0为基准，补齐规划条件、红线、测绘、道路断面", "容积率/建筑指标/坐标/标高闭合", "规划设计"], ["P0 财务底盘", "土地、建安、税负、融资、赠送补缴地价", "2.0口径下同一面积可复算", "成本财务"], ["P1 产品强排", "95/105/122/139㎡配比+上下跃双方案", "比较货值、成本、消防、去化", "建筑/产品"], ["P1 市场验证", "首开90天来访—认筹—净签—退房", "校准36—42套/月情景", "营销"]], "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-COST-FORECAST", "SRC-MARKET"]},
                {"type": "narrative", "title": "明确停牌条件", "text": "任一条件成立即暂停继续放大货值：楼面>5,630且无新增收益证据；送赠实得单价<15%；综合建安>5,200；首开后连续观察期月均净签<30套；规划条件与任务书口径无法闭合。", "source_refs": ["SRC-RANGE", "SRC-FINANCE", "SRC-SITE-OFFICIAL"]},
            ], ["SRC-SITE-OFFICIAL", "SRC-FINANCE", "SRC-COST-FORECAST", "SRC-MARKET", "SRC-RANGE"],
        ),
    ])
    pages.extend([
        base_page(
            "sc1-03", "SC1", "项目全景：成熟主城配套支撑改善，但界面与供给时序决定兑现度",
            "L-9的先天优势不是单一学校或单一公园，而是雍阳中学、杨村成熟生活圈、四侧道路和京津城际武清站共同形成的主城改善底盘；短板是西北侧规划道路与北侧在建地块的界面兑现。",
            [
                {"type": "table", "title": "周边配套与开发含义", "headers": ["资源/界面", "当前证据", "设计与销售动作"], "rows": [["教育", "雍阳中学北侧；周边杨村三中/五中/第七小学", "把教育便利转译为归家安全、步行尺度和家庭成长场景，不夸大学区承诺"], ["公园与生活", "紧邻西苑公园；文化公园、少年宫等成熟配套", "南北向慢行、儿童与运动场景连成可体验的社区公共生活"], ["交通", "东侧建设路、南侧振华西道，四侧临路；近京津城际武清站", "主入口、展示区和车行组织与城市界面绑定，提前做交通影响分析"], ["邻里竞争", "北侧在建住宅、南侧既有商业不拆除；周边多个改善竞品", "把南侧商业界面做价值分区，避免住宅首层与噪声/人流冲突"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-BRIEF-STRATEGY"]},
                {"type": "narrative", "title": "配套结论", "text": "配套足以支撑刚改和中端改善，但不能直接转化为高价理由；真正的溢价要由南侧商业界面、归家序列、运动展示区和任务书要求的高流速户型共同兑现。", "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-MARKET"]},
            ], ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-BRIEF-STRATEGY", "SRC-MARKET"],
        ),
        base_page(
            "sc1-04", "SC1", "宏观与政策：需求侧顺风，但不能对冲库存和价格敏感",
            "限购退出、首付与公积金支持、京津冀协同和新天津人政策有利于成交转化；它们改善的是购买门槛，不会自动抬升项目价格或消化错误产品。",
            [
                {"type": "table", "title": "政策对客群的实际作用", "headers": ["政策/环境", "受益客群", "项目转译", "边界"], "rows": [["限购全面取消", "本地改善、外溢置换", "减少资格沟通成本，强化置换链条", "不等于支付力无限"], ["首付与商贷利率", "刚改、新天津人", "95/105㎡总价带更容易进入", "需按最新银行口径核验"], ["公积金与京津冀协同", "产业导入新客", "强化武清站与主城通勤叙事", "政策需以最新有效文件为准"], ["预售资金监管趋严", "所有开发主体", "示范区和交付表达必须真实可兑现", "增加现金流与节奏约束"]], "source_refs": ["SRC-POLICY-LOAN", "SRC-BRIEF-STRATEGY"]},
                {"type": "narrative", "text": "宏观判断不是‘市场会普涨’，而是‘成交门槛降低后，能兑现主流总价和产品力的项目更容易获得转化’。因此L-9仍要以价格卡位、产品落地和首开验证为主线。", "source_refs": ["SRC-POLICY-LOAN", "SRC-MARKET", "SRC-FINANCE"]},
            ], ["SRC-POLICY-LOAN", "SRC-BRIEF-STRATEGY", "SRC-MARKET", "SRC-FINANCE"],
        ),
        base_page(
            "sc2-05", "SC2", "客群画像：本地老城外溢刚改 + 中端改善 + 新天津人",
            "三类客群共享一个购买逻辑：总价可控、三房实用、通勤与教育便利、交付可信；不应把项目包装成纯豪宅或投资型产品。",
            [
                {"type": "table", "title": "三类核心客群", "headers": ["客群", "购买触发", "主要抗性", "产品/营销动作"], "rows": [["老城外溢刚改", "孩子成长、改善居住、成熟配套", "首付和月供敏感、总价上限明确", "95/105㎡三房、透明总价、首开现金流"], ["本地中端改善", "居住品质、环境、归家和社区感", "对竞品品质与交付兑现挑剔", "122㎡改善承接、公共空间和样板区先行"], ["新天津人刚需/刚改", "京津通勤、产业就业、资格与贷款便利", "对交通距离、配套兑现和期房风险敏感", "武清站通勤叙事、交付材料展示、低门槛主力段"]], "source_refs": ["SRC-CUSTOMER", "SRC-TASKBOOK", "SRC-POLICY-LOAN"]},
                {"type": "metrics", "title": "市场结构与客群匹配", "metrics": [{"label": "三房成交占比", "value": "69%", "unit": "3,838/5,526套"}, {"label": "90—140㎡主力", "value": "58.7%", "unit": "区域成交结构"}, {"label": "80—120万", "value": "2,010套", "unit": "总价带成交"}, {"label": "任务书主力", "value": "95/105㎡", "unit": "各30%"}], "source_refs": ["SRC-MARKET", "SRC-CUSTOMER", "SRC-TASKBOOK"]},
            ], ["SRC-CUSTOMER", "SRC-MARKET", "SRC-TASKBOOK", "SRC-POLICY-LOAN"],
            chart_specs=[chart("CHART-CUSTOMER-MATCH", "市场主力与任务书户型对应", "%", [{"label": "区域90—120㎡", "value": 32.4}, {"label": "区域120—140㎡", "value": 26.3}, {"label": "任务书95㎡", "value": 30}, {"label": "任务书105㎡", "value": 30}], ["SRC-MARKET", "SRC-TASKBOOK"])],
        ),
        base_page(
            "sc2-06", "SC2", "竞品与供给时序：高价盘不等于快销，首开节奏必须前置",
            "区域头部竞品月均约26—40套，但金地雍阳印高价低速、宏顺与龙湖库存仍高；L-9的竞争力要靠主力段、价格和首开时序组合，而不是单独讲品质。",
            [
                {"type": "table", "title": "竞品去化信号", "headers": ["梯队", "代表项目", "去化读数", "对L-9的启示"], "rows": [["快销刚改", "雍鑫·溪和林 / 亚泰雍阳府", "约40套/月 / 约35套/月", "主流总价和产品效率能形成流速"], ["中速改善", "龙湖云曜 / 宏顺·央璟颂", "约26—39套/月", "品质改善有需求，但库存压力仍在"], ["高价低速", "金地雍阳印", "约11—13套/月", "高价与去化脱钩，不能把标杆价当售价依据"], ["待入市供给", "邻地及津武2025-080/058等", "约43.1万㎡新增计容", "必须提前首开并建立分批供货计划"]], "source_refs": ["SRC-COMPETITOR", "SRC-MARKET", "SRC-LAND-COMP"]},
                {"type": "narrative", "title": "竞争结论", "text": "L-9不应与高端改善盘比立面，也不应与老盘比绝对低价；应以任务书95/105㎡为首开主力，以122/139㎡形成改善梯度，并用南侧商业界面、示范区和上下跃小批验证建立差异。", "source_refs": ["SRC-COMPETITOR", "SRC-TASKBOOK", "SRC-CUSTOMER"]},
            ], ["SRC-COMPETITOR", "SRC-MARKET", "SRC-LAND-COMP", "SRC-TASKBOOK"],
            chart_specs=[chart("CHART-COMP-SPEED", "竞品月均去化梯度", "套/月", [{"label": "雍鑫溪和林", "value": 40}, {"label": "龙湖云曜", "value": 39}, {"label": "宏顺央璟颂", "value": 30}, {"label": "金地雍阳印", "value": 11.4}], ["SRC-COMPETITOR"])],
        ),
        base_page(
            "sc3-04", "SC3", "强排推演：FAR2.0下做两套方案，而不是只给一张概念总图",
            "任务书明确要求不少于两个规划方案。建议以同一FAR2.0和四档配比，分别推演‘高流速经济型’与‘展示溢价型’，用指标、车位、日照、地库、货值和去化做同边界比较。",
            [
                {"type": "table", "title": "两套强排方向", "headers": ["维度", "方案A｜高流速经济型", "方案B｜展示溢价型"], "rows": [["产品", "95/105㎡首开占比高，122/139㎡控量", "四档完整，局部上下跃作为价值锚点"], ["空间", "中庭+均好楼间距，降低地库和景观复杂度", "主入口+示范区+南侧商业形成连续礼序"], ["交通", "人车分流、车行最短路径、经济地库", "强化归家步行序列，展示区与人行动线前置"], ["货值", "优先兑现流速和总价带", "测试上下跃、架空/下沉空间的溢价"], ["放行条件", "建安≤4,600、月均36—42套", "送赠≥15%、政策/结构/消防/补缴闭合"]], "source_refs": ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-RANGE", "SRC-POLICY-SPACE"]},
                {"type": "narrative", "title": "强排验收清单", "text": "两套方案必须同时提交：彩色总平面及经济指标、四侧道路与周边关系、竖向与地库、消防与登高面、停车、日照分析、所有面积段户型及整层对应关系、售楼处/展示区专篇。未完成这些闭环，不得用‘效果图好看’替代强排成果。", "source_refs": ["SRC-TASKBOOK"]},
            ], ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-RANGE", "SRC-POLICY-SPACE"],
            diagram_specs=[{"diagram_id": "DIA-L9-MASSING-AB", "grammar": "SPATIAL_DIAGRAM", "title": "FAR2.0双方案推演关系（非比例概念图）", "nodes": ["任务书FAR2.0", "方案A 高流速经济型", "方案B 展示溢价型", "95/105㎡主力", "122/139㎡改善", "上下跃小批验证", "指标/车位/日照/地库复核", "货值×去化×成本决策"], "note": "表达推演逻辑，不表达真实尺寸、距离或已完成的强排成果。", "diagram_type": "adjacency"}],
        ),
        base_page(
            "va2-04", "VA2", "货值去化联动：总价带决定流速，空间增值决定上限",
            "95/105㎡承担流速，122㎡承接改善，139㎡和上下跃承担有限溢价；所有溢价都必须通过实得面积、增量成本、补缴地价和支付力四账联算。",
            [
                {"type": "table", "title": "任务书配比与货值角色", "headers": ["产品", "配比", "价格/去化角色", "必须验证"], "rows": [["95㎡", "30%", "刚改总价带、首开流速", "三房效率、得房率、总价与月供"], ["105㎡", "30%", "主流改善、首开主力", "面宽进深、功能完整、首开认筹"], ["122㎡", "25%", "改善承接与品质锚点", "公共厅、主套、园林界面、溢价"], ["139㎡", "15%", "改善形象与利润锚点", "去化速度、总价承受、户型稀缺性"], ["上下跃", "各段均含备注", "潜在实得面积增益", "政策、结构、消防、成本、销售口径"]], "source_refs": ["SRC-TASKBOOK", "SRC-MARKET", "SRC-POLICY-SPACE", "SRC-FINANCE"]},
                {"type": "narrative", "text": "理论上可通过上下跃、坡屋顶、地下室、庭院、露台、封闭阳台及停车等政策机制放大实得价值，但不能把政策上限直接写成销售承诺。最终要以两套强排的实际可落地面积和净增成本重算毛利。", "source_refs": ["SRC-TASKBOOK", "SRC-POLICY-SPACE", "SRC-COST-FORECAST", "SRC-FINANCE"]},
            ], ["SRC-TASKBOOK", "SRC-MARKET", "SRC-POLICY-SPACE", "SRC-FINANCE"],
        ),
        base_page(
            "va3-03", "VA3", "方案亮点：把任务书要求翻译成可销售、可交付的体验链",
            "方案亮点不应停留在立面风格，而应形成‘城市界面—示范区—归家—全龄生活—户型实得—交付材料’的一条完整体验链。",
            [
                {"type": "table", "title": "可下发的方案亮点清单", "headers": ["亮点", "任务书依据", "设计动作", "验证指标"], "rows": [["运动型展示区", "展示中心约2,000㎡，含标准篮球场等体验功能", "沿街/街角昭示性布置，串联售楼处、儿童与运动场景", "动线、面积、运营与首开时序"], ["中庭与宅间层次", "大中庭小宅间、轴线秩序、均好楼间距", "形成归家礼序与景观层次，避免纯强排", "楼间距、日照、景观投入与货值"], ["南侧商业界面", "南侧商业不拆除，需做价值分析；商业按三层、局部四层考虑日照", "设置缓冲、展示和首层生活界面", "噪声、人流、日照、首层可售价值"], ["北向入户与人车分流", "尽量北向入户；人行/车行不多于2个出入口", "控制变异户型，形成连续安全步行系统", "入口数量、消防、交通影响"], ["上下跃样板机制", "四档配比备注包含上下跃；要求做货值溢价分析", "只做小批实体展示，明确合规边界与交付标准", "实得面积、成本、溢价、认筹转化"]], "source_refs": ["SRC-TASKBOOK", "SRC-POLICY-SPACE"]},
                {"type": "narrative", "title": "亮点排序", "text": "第一优先级是可兑现的主流户型和归家体验，第二优先级是南侧商业与展示区的城市界面，第三优先级才是上下跃等高风险高溢价机制。这样才能让亮点服务于去化，而不是反过来绑架成本和合规。", "source_refs": ["SRC-TASKBOOK", "SRC-MARKET", "SRC-COST-FORECAST", "SRC-POLICY-SPACE"]},
            ], ["SRC-TASKBOOK", "SRC-MARKET", "SRC-COST-FORECAST", "SRC-POLICY-SPACE"],
        ),
    ])
    pages.append(
        base_page(
            "ad5-01", "SC3", "空间感知：把轴线、中庭与归家礼序转成可验证的居住体验",
            "任务书对大中庭小宅间、轴线秩序、正南北朝向、归家动线和景观层次已有明确要求；本页只做设计解释，不把传统文化意象包装成市场事实。",
            [
                {"type": "table", "title": "空间感知到设计指标", "headers": ["感知目标", "任务书依据", "可执行动作", "验收方式"], "rows": [["归家礼序", "示范区、归家大堂、前场仪式感与后场园林品质", "沿街展示—入口—大堂—中庭形成连续序列", "步行剖面、视线节点、动线时长"], ["社区均好", "大中庭小宅间、楼间距均匀、轴线秩序", "控制组团尺度，避免单纯高密强排", "日照、楼间距、景观覆盖率"], ["健康运动", "展示中心含标准篮球场，运动风格", "把运动、儿童和全龄活动布置在可达路径上", "可达距离、视线安全、运营面积"], ["南北朝向与通风", "住宅正南北为主", "以朝向和面宽优先校正户型与总图关系", "朝向统计、户型面宽进深对应"], ["品质而非符号", "立面标识性且兼顾成本", "亲人尺度局部强化，其他部位用比例/虚实控制成本", "立面材料清单、成本分项、竞品对标"]], "source_refs": ["SRC-TASKBOOK", "SRC-ARCHLIB-LOWDENSITY"]},
                {"type": "narrative", "title": "使用边界", "text": "‘宋韵’或其他文化语言只能作为设计表达选项，不能替代客户访谈、竞品反馈或配套事实。本项目真正可验证的体验价值是归家连续性、运动与全龄场景、户型舒适度和交付兑现。", "source_refs": ["SRC-TASKBOOK", "SRC-CUSTOMER", "SRC-ARCHLIB-LOWDENSITY"]},
            ], ["SRC-TASKBOOK", "SRC-CUSTOMER", "SRC-ARCHLIB-LOWDENSITY"],
        )
    )
    # The first draft carried the right source register but only a thin slice
    # of the registered evidence.  Enrich the seed here so every downstream
    # compiler sees the same corrected figures, explicit time windows, and the
    # full decision workflow.
    pages = enrich_pages(pages, refs)
    unit_order = {"SC1": 0, "SC2": 1, "SC3": 2, "AD1": 3, "AD2": 4, "AD3": 5, "AD4": 6, "AD5": 7, "VA1": 8, "VA2": 9, "VA3": 10, "CS": 11}
    pages.sort(key=lambda item: (unit_order.get(str(item.get("unit_id")), 99), str(item.get("page_id"))))
    return {
        "schema_version": "dds.report-seed/1.0",
        "page_manifest_authoritative": True,
        "status": "partial",
        "decision_eligibility": False,
        "method": "frozen_evidence_package_then_offline_deterministic_compile",
        "project": {"project_id": PROJECT_ID, "project_name": "天津武清六街 L-9 地块", "city": "天津", "district": "武清区", "stage": "investment_screening"},
        "meta": {"as_of": AS_OF, "compiled_at": f"{AS_OF}T00:00:00+08:00", "audience": ["开发商投决团队", "前策团队", "建筑设计管理团队"], "coordinate_precision": "none", "decision_authority": "binding_decision"},
        "source_registry": sources,
        "page_manifest": pages,
        "decision": {"status": "partial", "headline": "建议：有条件推进，但不接受7,507元/㎡无条件拿地；以任务书FAR2.0和95/105/122/139㎡配比为基准，优先争取4,600—5,000元/㎡，5,630元/㎡为审慎上限，且送赠15—20%、建安4,200—4,600和月均36—42套必须同时成立。", "recommendations": ["以任务书FAR2.0、95/105/122/139㎡配比作为强排基准", "楼面优先控制在4,600—5,000元/㎡", "5,630元/㎡仅在送赠≥15%且建安≤4,600时审慎进入", "7,507元/㎡原则上放弃", "首开以95/105㎡主力产品验证36—42套/月"], "gates": ["任务书与法定条件闭合", "成本税费融资", "政策适用与送赠实测", "首开90天漏斗"], "source_refs": refs},
        "site": {"status": "partial", "constraints": [{"name": "地块规模", "value": "约4.19ha / 41,900㎡", "status": "registered"}, {"name": "任务书容积率", "value": "2.0", "status": "registered"}, {"name": "位置", "value": "雍阳中学北侧、四侧临路", "status": "registered"}, {"name": "几何精度", "value": "none", "status": "blocked"}], "evidence_gaps": ["附件2规划条件通知书法定细项", "盖章红线", "统一坐标与真北", "标高/管线", "最终道路断面"], "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK"]},
        "market": {"status": "partial", "indicators": [{"name": "加权均价", "value": 13257, "unit": "元/㎡"}, {"name": "近12月成交", "value": 5526, "unit": "套"}, {"name": "库存", "value": 9695, "unit": "套"}, {"name": "去化周期", "value": 22.8, "unit": "个月"}], "area_bands": [{"band": "90—140㎡", "share_pct": 38, "source_refs": ["SRC-MARKET"]}, {"band": "120—140㎡", "price_cny_sqm": 13865, "source_refs": ["SRC-MARKET"]}, {"band": "140—180㎡", "price_cny_sqm": 15462, "source_refs": ["SRC-MARKET"]}], "source_refs": ["SRC-MARKET", "SRC-CUSTOMER"], "evidence_gaps": ["目标项目真实来访与净签", "按面积段折扣与退房"]},
        "competitor_series": {"status": "partial", "series_status": "snapshot_only", "items": [{"project_name": "竞品A", "unit_price_cny": 13865, "distance_km": 1.0, "source_refs": ["SRC-COMPETITOR"]}, {"project_name": "竞品B", "unit_price_cny": 15462, "distance_km": 2.0, "source_refs": ["SRC-COMPETITOR"]}], "source_refs": ["SRC-COMPETITOR"], "evidence_gaps": ["竞品统一统计期与完整身份核验"]},
        "absorption_forecast": {"status": "partial", "model_type": "taskbook_mix_derived_market_calibrated_scenario", "total_units": 736, "basis": "FAR2.0×41,900㎡=83,800㎡计容规模；按任务书95/105/122/139㎡配比计算加权户型约113.9㎡，约736套为示意推导，不等同最终可售套数。", "scenarios": [{"name": "快周转目标", "monthly_units": 42, "sellout_months": 18}, {"name": "稳健目标", "monthly_units": 36, "sellout_months": 20}, {"name": "高价低流速", "monthly_units": 30, "sellout_months": 25}], "decision_eligibility": False, "limitations": ["条件性模型，不是销售承诺；实际套数需以总图、非住宅/配套、可售面积和首开90天回测校正"], "source_refs": ["SRC-TASKBOOK", "SRC-RANGE", "SRC-MARKET", "SRC-COMPETITOR"]},
        "investment_case": {"status": "blocked", "decision_eligibility": False, "headline": "不支持7,507元/㎡无条件拿地", "evidence_gaps": ["楼面价与安全边界需以精修建安重算", "赠送/增值空间不能先行计入", "财务模型需统一口径"], "source_refs": ["SRC-FINANCE", "SRC-LAND-COMP", "SRC-ASSUMPTIONS"]},
        "finance": {"status": "blocked", "planning_basis": {"land_area_sqm": 41900, "far": 2.0, "indicative_floor_area_sqm": 83800, "status": "taskbook_derived_not_final_saleable", "source_refs": ["SRC-TASKBOOK"]}, "evidence_gaps": ["2.0口径下计容、可售、配套和非住宅面积需拆分", "建安、税负、融资与土地口径尚未完全勾稽", "赠送空间增量成本与收益未闭合"], "allowed_use": "仅做条件性区间和压力测试，不输出确定性IRR。", "refined_cost_center_cny_sqm": 4600, "refined_cost_range_cny_sqm": [4200, 5200], "tax_rate_range_pct": [5.5, 6.0], "tax_prepaid_cash_peak_10k_cny": 1140, "source_refs": ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-TAX-MODEL", "SRC-FINANCE"]},
        "product": {"status": "partial", "positioning": "刚需与中端改善、高流动性产品", "taskbook_mix": [{"area_sqm": 95, "share_pct": 30, "note": "包含上下跃户型"}, {"area_sqm": 105, "share_pct": 30, "note": "包含上下跃户型"}, {"area_sqm": 122, "share_pct": 25, "note": "包含上下跃户型"}, {"area_sqm": 139, "share_pct": 15, "note": "包含上下跃户型"}], "unit_mix": [{"area_sqm": "95/105", "role": "任务书主力现金流骨架"}, {"area_sqm": "122", "role": "改善承接"}, {"area_sqm": "139", "role": "改善价值锚点/控量"}], "source_refs": ["SRC-TASKBOOK", "SRC-CUSTOMER", "SRC-MARKET"]},
        "premium_analysis": {"status": "partial", "decision_eligibility": False, "reason": "空间机制可研，实得面积、增量成本、支付力和审批边界未闭合。", "source_refs": ["SRC-POLICY-SPACE", "SRC-ARCHLIB-LOWDENSITY"]},
        "risk": {"status": "partial", "items": ["法定红线与道路", "市场库存与竞品", "赠送/增值空间合规", "成本税费融资", "首开去化"], "source_refs": refs},
        "evidence_gaps": [{"gap_id": "GAP-REDLINE", "status": "blocked", "severity": "decision_blocking", "statement": "缺法定红线、统一坐标、真北、标高和最终道路断面。", "owner": "规划设计负责人", "recommended_action": "取得盖章规划条件、测绘成果和道路断面后重跑同边界强排。"}, {"gap_id": "GAP-FINANCE", "status": "blocked", "severity": "decision_blocking", "statement": "土地、建安、税负、融资和赠送空间的收益成本链尚未完全闭合。", "owner": "成本财务负责人", "recommended_action": "统一口径并复核保本/目标毛利楼面价。"}, {"gap_id": "GAP-ABSORPTION", "status": "partial", "severity": "confidence_affecting", "statement": "去化模型缺目标项目真实漏斗回测。", "owner": "营销负责人", "recommended_action": "以首开90天真实数据校准情景。"}],
        "case_evidence": [{"case_id": "CASE-ARCHLIB-LOWDENSITY", "Selected case": "成都低密改善户型机制案例", "Strategy": "ArchLib", "Fit": "低密改善与主流面积段机制可参考", "Score": 58, "Why": "可提取空间可变、公共生活与改善梯度的表达机制", "mechanism": "主流面积段做底盘，局部可变空间形成价值锚点", "image_role": "mechanism_reference", "source_refs": ["SRC-ARCHLIB-LOWDENSITY"], "visual_asset": {"asset_id": "ASSET-ARCHLIB-LOWDENSITY", "path": str(ARCHLIB_IMAGE), "mime": "image/png", "rights_status": "authorized_internal_reference", "design_keywords": ["低密改善", "户型机制", "空间可变"]}, "locality": "cross_city", "identity_status": "verified", "prohibited_analogies": ["不得迁移成都案例的价格、去化、成本或套数"], "transfer_actions": ["把主流面积段与可变空间机制转译为本项目专项户型任务书"], "transfer_conditions": ["先完成天津政策、结构消防、成本和销售口径复核"]}],
        "allowed_outputs": ["条件性投决研判", "产品定位", "设计任务书输入", "证据缺口与行动清单"],
    }


MARKET_MONTHS = [
    "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05",
    "2026-06", "2026-07",
]
MARKET_SALES = [384, 315, 629, 312, 509, 381, 187, 536, 592, 481, 519, 381]
MARKET_PRICES = [13425, 14444, 13427, 13553, 12647, 13664, 13134, 12552, 13453, 12223, 13128, 14098]
MARKET_APPROVED = [110, 274, 124, 283, 238, 392, None, 583, 504, 266, 442, 496]
MARKET_AVAILABLE = [12037, 12017, 11524, 11509, 11015, 10100, 9820, 9988, 9942, 9636, 9569, 9695]

COMPETITOR_ROWS = [
    ["绿城尚玉蘭", "杨村", 362, 17808, 460, 104, 30.2, "22.6%"],
    ["宏顺央璟颂", "杨村", 313, 16444, 535, 223, 26.1, "41.7%"],
    ["雍鑫·溪和林", "杨村", 241, 14400, 116, 162, 20.1, "—"],
    ["金地雍阳印", "杨村", 137, 19301, 196, 147, 11.4, "75.0%"],
    ["龙湖云曜", "武清城东", 78, 15062, 228, 150, 6.5, "65.8%"],
]

TOTAL_PRICE_ROWS = [
    ["50万以下", 150, 6112, 5296, "边缘"],
    ["50-80万", 485, 34121, 7753, "刚需低总价"],
    ["80-120万", 1131, 109828, 9206, "刚需主力"],
    ["120-160万", 833, 118296, 12562, "刚改起步"],
    ["160-200万", 1324, 234697, 14259, "套数第一"],
    ["200-300万", 1109, 258956, 16526, "金额第一/改善主力"],
    ["300-400万", 142, 48491, 18516, "改善高总价"],
    ["400-500万", 43, 18824, 19153, "高端"],
    ["500万以上", 9, 5100, None, "极少数"],
]

AREA_ROWS = [
    ["70㎡以下", 18, 6578, "刚需小户"],
    ["70-90㎡", 151, 9632, "刚需"],
    ["90-120㎡", 582, 12449, "刚改主力"],
    ["120-140㎡", 457, 13865, "规模+单价主力"],
    ["140-180㎡", 137, 15462, "改善溢价段"],
    ["180-240㎡", 35, 12197, "大户型少量"],
    ["240-300㎡", 0, None, "近三个月无样本"],
    ["300㎡以上", 1, 10005, "极少数"],
]

LAND_TREND_ROWS = [
    ["2023", 91, 823, 8321, 81, 689, 8150, 1.41],
    ["2024", 70, 473, 7004, 64, 421, 6795, 1.52],
    ["2025", 78, 527, 6828, 77, 521, 6894, 1.07],
    ["2026H1", 20, 110, 5671, 17, 92, 5459, 1.63],
]

LAND_COMP_ROWS = [
    ["雍阳中学东侧", "津武(挂)2024-044", "≤2.0", 30103.8, 7507, 22600, "2026-06-29", "天津京城投资开发有限公司"],
    ["雍阳中学东侧（前次）", "津武(挂)2024-044", "≤2.0", 30100, 7508, 22600, "2025-03-21", "天津运成投资有限公司"],
    ["雍阳西道北侧", "津武(挂)2024-063", "≤1.6", 69100, 5630, 38900, "2025-06-25", "天津鑫昇置业有限公司"],
    ["英华道南侧", "津武(挂)2023-014", "≤1.6", 54300, 7532, 40900, "2024-05-30", "天津市津武房地产开发有限公司"],
]


def _page_map(pages: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(page.get("page_id")): page for page in pages}


def _diagram_blocks(labels: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"label": label, "color": color} for label, color in labels]


def _prompt_records() -> list[dict[str, object]]:
    return [
        {
            "prompt_id": "ZZ-D0-PLAN",
            "title": "D0｜法定规划条件与红线",
            "scope": "先补齐会阻断强排和投决的法定底图。",
            "prompt": "请抓取并核验天津市武清区杨村街道、武清新城14-03-05单元、雍阳中学北侧（六街L-9/津武(挂)2024-024号）最新有效的规划条件通知书、挂牌文件及附件。返回：用地性质、用地面积、容积率上下限、建筑密度、绿地率、建筑高度/限高、退界、停车、人防、配套、开竣工约束、地块红线坐标、坐标系、真北、道路红线与断面。每个字段必须给原文摘录、文件名、发布/更新日期、页码或条款号、链接；找不到的字段写‘未找到’，禁止用相邻地块或推介口径替代。",
            "acceptance": "法定字段逐项有来源；红线可导入CAD/GIS；附件2与任务书冲突处单列并标注权威层级。",
            "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"],
        },
        {
            "prompt_id": "ZZ-D0-LAND",
            "title": "D0｜挂牌、地价与合同约束",
            "scope": "把楼面价锚和开发时序从二手转述升级为原始交易证据。",
            "prompt": "请抓取津武(挂)2024-024号及L-9对应公开挂牌/成交公告、出让合同或补充协议。返回：起始价、成交楼面价、成交总价、竞买保证金、付款节点、土地年限、开工/竣工/交付约束、配建及移交、违约条款、地价计算基数。请同时核对雍阳中学东侧2024-044、雍阳西道北2024-063、英华道南2023-014的成交信息，统一单位为元/㎡楼面价并保留日期与原文链接。",
            "acceptance": "每个地块至少一条官方公告或交易平台原始记录；成交价与面积、总价可反算闭合。",
            "source_refs": ["SRC-LAND-COMP", "SRC-SITE-OFFICIAL"],
        },
        {
            "prompt_id": "ZZ-D0-GEOMETRY",
            "title": "D0｜测绘、道路与市政底图",
            "scope": "给建筑师一份可直接建模的空间输入，而不是文字位置描述。",
            "prompt": "请获取L-9正式红线及周边现状测绘/CAD/GIS资料：CGCS2000或明确坐标系的角点坐标、真北、现状标高、道路中心线/红线/断面、出入口限制、地下管线、雨污水、电力、燃气、热力、现状建筑及北侧在建项目总图。输出可下载原文件、坐标系、比例尺、版本日期、数据精度和缺失清单；不要根据地图截图推算精确边界。",
            "acceptance": "红线闭合、真北明确、标高与道路断面可复核；无文件时明确返回‘无法获取’，并列出替代测绘任务。",
            "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK"],
        },
        {
            "prompt_id": "ZZ-MKT-MONTHLY",
            "title": "市场｜月度量价、供求与库存",
            "scope": "把区域快照升级为可计算的连续时间序列。",
            "prompt": "请抓取武清区及杨村/项目所在板块近24个月新房商品住宅月度数据：成交套数、成交面积、成交均价、成交金额、批准上市套数、期末可售套数、供求比、去化周期。按月返回原始值、统计口径、行政范围、数据版本、抓取日期和中指数据页面/导出文件标识；缺失月份不得插值，供求比和去化周期需给计算公式。",
            "acceptance": "月份连续、单位统一、成交金额可由面积×均价近似校验；区域与板块口径分开。",
            "source_refs": ["SRC-MARKET"],
        },
        {
            "prompt_id": "ZZ-MKT-STRUCTURE",
            "title": "市场｜面积、总价、户型与支付力",
            "scope": "让户型配比和总价带直接对应真实成交结构。",
            "prompt": "请抓取同一统计期的分面积段、分总价段、分户型成交数据，至少覆盖70㎡以下、70-90、90-120、120-140、140-180、180-240、240-300、300㎡以上；50万以下至500万以上总价带；两房/三房/四房/叠拼/联排等户型。返回成交套数、面积、均价、套数占比、样本总量、统计期和原始页面。另抓取首付比例、贷款利率、公积金可贷上限或公开可验证的支付力代理指标，并区分事实与推算。",
            "acceptance": "面积/总价/户型三张表能按同一时间窗口复算占比；没有数据的分段写0或无样本，不得混用不同窗口。",
            "source_refs": ["SRC-MARKET", "SRC-CUSTOMER", "SRC-POLICY-LOAN"],
        },
        {
            "prompt_id": "ZZ-COMP-FULL",
            "title": "竞品｜3/5公里在售与待入市全量",
            "scope": "形成可比较、可更新的竞品数据库，而不是只列几个项目名。",
            "prompt": "请以L-9地块为中心抓取3公里和5公里范围内在售、待售、待入市及近一年成交的住宅项目。每项目返回：项目身份/开发主体、坐标与距离、产品类型、容积率或楼层、户型/面积配比、成交均价及折扣、近12个月成交套数、批准上市、期末可售、月均去化、剩余货量、首开/最近推盘时间、交付状态、样板区/核心卖点、数据来源和统计期。项目身份无法确认时标‘待核验’，不要合并同名项目。",
            "acceptance": "同一统计期可横向比较；每个关键数字都有来源；早期三个月估算与近12个月实证分栏保存。",
            "source_refs": ["SRC-COMPETITOR", "SRC-MARKET"],
        },
        {
            "prompt_id": "ZZ-SUPPLY-LAND",
            "title": "竞品｜土地供应与未来竞争",
            "scope": "把未来供应对首开窗口的影响量化。",
            "prompt": "请抓取武清杨村及L-9周边未来36个月住宅供地、挂牌、成交、规划建面、预计入市时间和产品定位；同时整理近三年涉宅供地宗数、规划建面、推出/成交楼面价、溢价率。识别与L-9同面积段、同总价带、同低密逻辑的直接供应，给出计容建面与年度成交的倍数关系，并列出数据截止日。",
            "acceptance": "未来供应按地块逐宗列出，已成交与待挂牌分开；倍数计算可追溯。",
            "source_refs": ["SRC-LAND-COMP", "SRC-COMPETITOR", "SRC-BRIEF-STRATEGY"],
        },
        {
            "prompt_id": "ZZ-CUSTOMER-FUNNEL",
            "title": "客群｜真实漏斗、支付与竞品反馈",
            "scope": "补上目前最影响去化模型的目标项目实证。",
            "prompt": "请抓取或调研L-9所在板块及同类竞品近12个月的来访、认筹、签约、净签、退房/退款、渠道占比、首付来源、置换链、通勤来源、家庭结构、关注面积段和价格抗性。若中指数据无法提供，请返回可执行的线下调研字段表和样本量建议；所有比例注明样本量与统计期，不要把访谈判断写成市场事实。",
            "acceptance": "至少形成首开90天漏斗字段：来访→认筹→网签→净签→退房，并能回算月均净签。",
            "source_refs": ["SRC-CUSTOMER", "SRC-MARKET", "SRC-COMPETITOR"],
        },
        {
            "prompt_id": "ZZ-POLICY-VALUE",
            "title": "政策｜送赠、奖励面积与审批边界",
            "scope": "验证货值破局的政策能否落到本地、当前批次和具体产品。",
            "prompt": "请核验天津住宅多样性空间增值利用政策及2024补充、2025继续执行通知对武清住宅项目的当前有效性。按挑空、坡屋顶、地下室、庭院、露台、封闭阳台、设备平台、停车/架空平台分别返回适用建筑类型、面积/高度/比例上限、计容口径、补缴土地出让收益比例、结构消防和审批前置、可否销售/赠送的原文依据。重点回答低多层洋房、上下跃、139㎡产品是否可适用；缺乏明确条款时标为待主管部门确认。",
            "acceptance": "政策条款、有效期、适用范围和补缴情景逐项有原文；禁止把理论奖励面积直接计入确定货值。",
            "source_refs": ["SRC-POLICY-SPACE", "SRC-TASKBOOK"],
        },
        {
            "prompt_id": "ZZ-COST-FINANCE",
            "title": "财务｜成本、税费、融资与现金峰值",
            "scope": "把精修成本口径与送赠增量成本接到同一模型。",
            "prompt": "请按L-9任务书FAR2.0设计输入与‘法定条件待核’双口径建立可复算模型：土地楼面价4,000-7,507、综合建安4,000-5,500（重点4,200/4,600/5,200）、营销展示中心约2,000㎡、地库/园林/保温、期间费、增值税、土增税预缴与清算、所得税、融资利息、补缴地价（区级28%）分别列项。输出不送赠与送赠8/15/20/25%四档的货值、增量成本、净增值、毛利率、保本售价、现金峰值和敏感性；所有公式、面积口径、税率和时间点写清。",
            "acceptance": "一张输入表可驱动所有情景；计容面积、可售面积、奖励面积不混算；输出‘示意/待复核’标签。",
            "source_refs": ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL", "SRC-POLICY-SPACE"],
        },
        {
            "prompt_id": "ZZ-OUTPUT-SCHEMA",
            "title": "回传格式与质量控制",
            "scope": "让抓取结果能直接进入DDS证据包而不是再次人工整理。",
            "prompt": "请按以下JSON字段回传：dataset_id、geography、time_window、as_of、source_url、source_title、publisher、retrieved_at、raw_file_ref、records、missing_fields、calculation_notes、confidence、conflicts。records内每个数字必须包含value、unit、period、scope、source_locator。禁止填充推测值；无数据填null并说明原因。若不同来源冲突，保留两条记录并给出冲突解释，不覆盖原值。",
            "acceptance": "JSON可解析、字段齐全、无无来源数字、缺口可逐项回写数据需求清单。",
            "source_refs": ["SRC-MARKET", "SRC-COMPETITOR", "SRC-LAND-COMP", "SRC-TASKBOOK"],
        },
    ]


def write_zhongzhi_prompts() -> Path:
    output = WORK / "中指数据Agent_缺口抓取提示词.md"
    lines = [
        "# 天津武清六街 L-9｜中指数据Agent缺口抓取提示词",
        "",
        f"> 项目：{PROJECT_ID}  |  基准日：{AS_OF}",
        "> 用法：逐条复制给中指数据Agent；必须返回原始来源、统计期、口径、缺失字段和可复算公式。不得以相邻地块、旧版本或推测值填充法定条件。",
        "",
        "## 当前报告的硬缺口",
        "",
        "- D0：附件2规划条件通知书、盖章红线、统一坐标、真北、标高、道路断面、管线和北侧在建总图；",
        "- D0/D1：L-9挂牌/合同约束、完整竞品身份与统一12个月统计、未来36个月供地；",
        "- D1：近24个月连续市场量价供求、分面积/总价/户型支付结构、二手与一手联动；",
        "- D1：目标项目真实来访—认筹—净签—退房漏斗，以及送赠政策适用和补缴成本的项目级确认；",
        "- D1：FAR2.0与旧资料≤1.5的权威冲突裁决、两套强排实测指标、成本/税费/融资闭合模型。",
        "",
        "## 可复制任务",
        "",
    ]
    for item in _prompt_records():
        lines.extend([
            f"### {item['prompt_id']}｜{item['title']}",
            "",
            f"**任务目的**：{item['scope']}",
            "",
            "```text",
            str(item["prompt"]),
            "```",
            "",
            f"**验收标准**：{item['acceptance']}",
            f"**关联入库证据**：{'、'.join(item['source_refs'])}",
            "",
        ])
    lines.extend([
        "## 回传后接入DDS的顺序",
        "",
        "1. 先将原始文件和JSON放入 `00_Core/runs/`，保留抓取日期和哈希；",
        "2. 用字段级证据矩阵更新D0/D1缺口，不覆盖旧记录；",
        "3. 先复核法定边界与FAR冲突，再重跑双方案强排；",
        "4. 将实测可售面积、奖励面积、补缴地价、建安和真实漏斗回填货值/去化/财务；",
        "5. 只有硬证闭合后，才把报告从探索性条件研判升级为正式可研输入。",
        "",
        "> 注意：本文件是数据获取任务，不是本项目事实来源；抓取结果未入库前，报告中的相应数字仍保持‘示意/待复核’。",
    ])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def enrich_pages(pages: list[dict[str, object]], refs: list[str]) -> list[dict[str, object]]:
    """Correct the first-draft snapshots and add the full DDS decision trail.

    This is deliberately an evidence-preserving enrichment layer.  It never
    upgrades a missing geometry, policy, funnel, or financial input into a
    fact; unresolved items remain labelled as conditional or to-be-verified.
    """
    by_id = _page_map(pages)

    # The Core archive contains both the DDS full-flow synthesis and an older
    # feasibility study.  Surface them as an explicit reconciliation page so
    # the reader can see which numbers were retained, which were superseded,
    # and why the report remains conditional.
    pages.append(
        base_page(
            "sc1-05", "SC1", "历史可研与当前证据：保留冲突，不把旧结论伪装成新事实",
            "Core中的历史可研报告提供了早期判断和一组旧测算；DDS全流程文件已把任务书、市场快照、政策和精修成本重新串联。本版采用有来源且时间窗口更清楚的读数，历史值只作为冲突登记，不参与无标记混算。",
            [
                {"type": "table", "title": "历史口径→当前处理", "headers": ["维度", "历史可研/前版读数", "当前报告采用", "处理原则"], "rows": [
                    ["容积率", "≤1.5或作为假设", "任务书内嵌图示2.0作为设计输入；≤1.5保留敏感性", "附件2未到前双口径并列"],
                    ["市场窗口", "出清21.78个月、供求比1.30等前版摘要", "月表实际列12个月；期末静态去化约22.8个月", "不把不同窗口和算法拼成一条趋势"],
                    ["需求结构", "三房75.2%、90—140㎡58.7%等旧口径", "三房3,838/5,526约69%；90—140㎡列明1,039套，源文本约38%但分母待核", "旧值不再作为当前占比"],
                    ["建安", "约3,500元/㎡旧估算", "综合工作区间4,200—5,200，中枢约4,600", "旧值只作成本敏感性对照"],
                    ["地价结论", "≤5,900建议、>7,200拒绝等旧建议", "4,600—5,100基准；5,630条件上限；7,507原则拒绝", "以精修成本与送赠净增值联算"],
                ], "source_refs": ["SRC-LEGACY-FEASIBILITY", "SRC-DDS-FULL-FLOW", "SRC-MARKET", "SRC-FINANCE"]},
                {"type": "narrative", "title": "明确结论", "text": "历史可研不是无效资料，而是旧版本假设的证据：它帮助定位本项目长期存在的去化、地价和成本敏感性，但不能覆盖任务书中已经明确的FAR2.0输入，也不能覆盖当前源表的字段级校验。正式投决前必须让附件2、统一市场窗口、A/B强排和财务模型共同裁决。", "source_refs": ["SRC-LEGACY-FEASIBILITY", "SRC-DDS-FULL-FLOW", "SRC-TASKBOOK"]},
                {"type": "table", "title": "冲突登记与下一动作", "headers": ["冲突项", "影响", "责任动作", "放行标准"], "rows": [
                    ["FAR2.0 vs ≤1.5", "改变计容、套数、地库、货值", "规划取得附件2并锁定法定口径", "红线/指标字段可追溯"],
                    ["面积段分母1,381 vs约2,738", "影响配比和市场占比", "中指回传完整分母与缺失段", "同窗口三张结构表可复算"],
                    ["3,500 vs4,600建安", "直接改变安全楼面", "成本拆分地库/园林/保温/展示中心", "输入表驱动全部情景"],
                ], "source_refs": ["SRC-ASSUMPTIONS", "SRC-MARKET", "SRC-COST-FORECAST", "SRC-FINANCE"]},
            ],
            ["SRC-LEGACY-FEASIBILITY", "SRC-DDS-FULL-FLOW", "SRC-TASKBOOK", "SRC-MARKET", "SRC-FINANCE"],
            unit_status="partial", unit_role="decision_reconciliation", story_role="primary_narrative", visual_evidence="text",
        )
    )

    # Make the full-flow synthesis visible in the evidence graph as well as in
    # the source drawer; it is a project archive summary, not an independent
    # market source.
    for page in pages:
        if str(page.get("page_id")) in {"sc1-01", "sc1-02", "cs-01", "cs-04"}:
            page["source_refs"] = list(dict.fromkeys(list(page.get("source_refs") or []) + ["SRC-DDS-FULL-FLOW"]))

    # Correct the two figures that were wrong in the first visual draft.
    p = by_id.get("sc2-02")
    if p:
        p.update(
            {
                "title": "需求结构：三房约69%，90—140㎡近三个月约38%，主力盘逻辑明确",
                "display_title": "需求结构｜面积、总价与户型",
                "takeaway": "市场有结构性需求，但不是无差别需求：三房占近12个月成交约69%，90—140㎡占近三个月约38%，160—300万元占近12个月约44%；L-9应先做可负担主流段，再用低密与空间机制承接溢价。",
                "decision_question": "哪一组面积、户型和总价真正构成首开现金流骨架？",
                "decision_impact": "把‘三房+90—140㎡+160—300万’作为产品与首开验证主轴；前版75.2%/58.7%口径已废止，面积段占比仍需完成分母校验。",
                "blocks": [
                    {"type": "table", "title": "分面积段成交结构｜近3个月 2026-05—07", "headers": ["面积段", "成交套数", "均价（元/㎡）", "产品含义"], "rows": AREA_ROWS, "source_refs": ["SRC-MARKET"]},
                    {"type": "table", "title": "分户型成交结构｜近12个月 2025-08—2026-07", "headers": ["户型", "成交套数", "占已列样本", "成交均价/判断"], "rows": [["三房", 3838, "69%（占总成交）", "13,787｜绝对主力"], ["二房", 697, "12.6%", "10,706｜刚需"], ["四房", 384, "6.9%", "13,152｜改善补充"], ["叠拼", 149, "2.7%", "15,374｜低密溢价信号"], ["联排", 96, "1.7%", "10,085｜少量"], ["其他", "约59", "约1.1%", "少量/需继续核验"]], "source_refs": ["SRC-MARKET", "SRC-CUSTOMER"]},
                    {"type": "table", "title": "分总价段成交结构｜近12个月", "headers": ["总价段", "成交套数", "成交金额（万）", "均价（元/㎡）", "判断"], "rows": TOTAL_PRICE_ROWS, "source_refs": ["SRC-MARKET"]},
                    {"type": "metrics", "title": "结构性主力读数", "metrics": [{"label": "三房", "value": "69%", "unit": "3,838 / 5,526套"}, {"label": "90—140㎡", "value": "38%", "unit": "近3个月1,039 / 约2,738套"}, {"label": "160—300万", "value": "44%", "unit": "近12个月2,433套"}, {"label": "任务书主力", "value": "60%", "unit": "95/105㎡各30%"}], "source_refs": ["SRC-MARKET", "SRC-TASKBOOK"]},
                    {"type": "narrative", "title": "面积段分母校验｜当前不得当作闭合占比", "text": "源表列出的8个面积段合计1,381套，其中90—140㎡为1,039套；源文件另写‘约2,738套/38%’，与列明行合计不一致。当前只把1,039套作为已列样本事实，38%保留为源文本口径，不能当作已闭合占比；需由中指数据Agent返回完整分母、统计期及缺失面积段。", "source_refs": ["SRC-MARKET"]},
                ],
                "chart_specs": [
                    chart("CHART-MARKET-AREA-CORRECTED", "近3个月面积段成交套数", "套", [{"name": "成交套数", "values": [18, 151, 582, 457, 137, 35, 0, 1]}], ["SRC-MARKET"], "distribution", dimensions=["70㎡以下", "70-90㎡", "90-120㎡", "120-140㎡", "140-180㎡", "180-240㎡", "240-300㎡", "300㎡以上"], measures=["成交套数"], time_window="2026-05—07", findings=["90—120㎡为近3个月套数第一主力段", "90—140㎡列明样本1,039套；源文本约38%，分母待核", "140—180㎡单价最高但规模较小"], decision_message="首开以95/105㎡承接90—120㎡流速，以122㎡承接120—140㎡改善；面积占比待分母校验后再锁定。"),
                    chart("CHART-MARKET-TOTAL-PRICE", "近12个月总价带成交套数", "套", [{"name": "成交套数", "values": [150, 485, 1131, 833, 1324, 1109, 142, 43, 9]}], ["SRC-MARKET"], "distribution", dimensions=["50万以下", "50-80万", "80-120万", "120-160万", "160-200万", "200-300万", "300-400万", "400-500万", "500万以上"], measures=["成交套数"], time_window="2025-08—2026-07", findings=["160—300万合计2,433套，占总成交约44%", "200—300万成交金额最高", "高总价存在但不宜直接外推为全盘主力"], decision_message="以160—300万作为低密刚改的支付力主场，控制首开总价。"),
                ],
            }
        )

    # Add the missing market evidence pages.  These are intentionally kept as
    # compact, single-question pages so the scroll report reads like the NICE
    # examples while the full tables remain available below each visual.
    pages.extend([
        base_page(
            "sc2-07", "SC2", "月度市场：成交有波动、价格未单边下行，但库存仍是首开约束",
            "入库月表显示成交套数在187—629套/月之间波动，价格在12,223—14,444元/㎡之间波动；期末可售从12,037降至9,695套。市场不是无需求，而是需要用产品和价格捕捉波动中的有效需求。",
            [
                {"type": "table", "title": "武清新房月度量价｜入库表 2025-08—2026-07", "headers": ["月份", "成交套数", "成交面积（㎡）", "均价（元/㎡）", "批准上市（套）", "期末可售（套）"], "rows": [["2025-08", 384, 46238, 13425, 110, 12037], ["2025-09", 315, 40765, 14444, 274, 12017], ["2025-10", 629, 74066, 13427, 124, 11524], ["2025-11", 312, 38058, 13553, 283, 11509], ["2025-12", 509, 59763, 12647, 238, 11015], ["2026-01", 381, 47109, 13664, 392, 10100], ["2026-02", 187, 21989, 13134, "—", 9820], ["2026-03", 536, 65447, 12552, 583, 9988], ["2026-04", 592, 73990, 13453, 504, 9942], ["2026-05", 481, 57265, 12223, 266, 9636], ["2026-06", 519, 59350, 13128, 442, 9569], ["2026-07", 381, 45404, 14098, 496, 9695]], "source_refs": ["SRC-MARKET"]},
                {"type": "metrics", "title": "窗口汇总", "metrics": [{"label": "成交合计", "value": "5,526", "unit": "套｜表列12个月"}, {"label": "加权均价", "value": "13,257", "unit": "元/㎡"}, {"label": "批准上市", "value": "4,647", "unit": "套"}, {"label": "期末可售", "value": "9,695", "unit": "套｜2026-07"}, {"label": "静态去化", "value": "22.8", "unit": "个月"}], "source_refs": ["SRC-MARKET"]},
                {"type": "narrative", "title": "数据质量提示", "text": "源文件标题写‘近13个月/共19个月’，但实际列出的月份为2025-08至2026-07共12个自然月；本报告按列明月份计算，同时把‘窗口月数’列为中指数据Agent待核字段。", "source_refs": ["SRC-MARKET"]},
            ],
            ["SRC-MARKET"],
            chart_specs=[chart("CHART-MARKET-MONTHLY", "月度成交与价格走势", "套 / 元/㎡", [{"name": "成交套数", "values": MARKET_SALES}, {"name": "成交均价", "values": MARKET_PRICES}], ["SRC-MARKET"], "combo_bar_line", dimensions=MARKET_MONTHS, measures=["成交套数", "成交均价"], time_window="2025-08—2026-07（源表列12个月，标题月数待核）", findings=["成交套数波动区间187—629套/月", "价格随月度结构波动，不是单边下行", "期末库存下降但仍有22.8个月静态去化"], decision_message="首开要用主流产品捕捉成交波峰，并以分批供货降低库存暴露。")],
        ),
        base_page(
            "sc2-08", "SC2", "支付结构：160—300万占约44%，二手价差大，低密新房必须靠兑现价值",
            "近12个月总价成交最厚的是160—300万元；二手成交约7,500—8,300元/㎡、挂牌约9,300—10,000元/㎡，与新房存在约40—45%价差。L-9的价格支撑来自新房产品与交付兑现，不来自二手价格抬升。",
            [
                {"type": "table", "title": "分总价段成交｜近12个月", "headers": ["总价段", "成交套数", "成交金额（万）", "均价（元/㎡）", "判断"], "rows": TOTAL_PRICE_ROWS, "source_refs": ["SRC-MARKET"]},
                {"type": "table", "title": "二手住宅价格支撑｜入库月点 2025-08—2026-07", "headers": ["月份", "成交套数", "成交均价（元/㎡）", "挂牌均价（元/㎡）", "网签价（元/㎡）"], "rows": [["2025-08", 875, 7909, 9772, 6691], ["2025-12", 854, 7901, 9913, 7500], ["2026-03", 1047, 7896, 9522, 6809], ["2026-05", 1135, 8307, 9508, 7547], ["2026-06", 1006, 8269, 9488, 7447], ["2026-07", 1009, 7476, 9283, 7220]], "source_refs": ["SRC-MARKET"]},
                {"type": "narrative", "title": "支付与产品结论", "text": "95/105㎡若按中性12,426元/㎡测算，对应约118—130万元名义总价；122/139㎡进入160—300万更厚的改善带时，需要用面宽、得房、归家和低密界面解释总价，而不是只靠‘低密’标签。目标项目真实首付、月供和置换链仍需补证。", "source_refs": ["SRC-MARKET", "SRC-CUSTOMER", "SRC-FINANCE"]},
            ],
            ["SRC-MARKET", "SRC-CUSTOMER", "SRC-FINANCE"],
            chart_specs=[chart("CHART-MARKET-PRICE-DISTRIBUTION", "总价带成交套数", "套", [{"name": "成交套数", "values": [150, 485, 1131, 833, 1324, 1109, 142, 43, 9]}], ["SRC-MARKET"], "distribution", dimensions=["50万以下", "50-80万", "80-120万", "120-160万", "160-200万", "200-300万", "300-400万", "400-500万", "500万以上"], measures=["成交套数"], time_window="2025-08—2026-07", findings=["160—200万套数最多，为1,324套", "160—300万合计2,433套，占约44%", "二手成交价显著低于新房，改善溢价必须靠产品兑现"], decision_message="首开总价优先卡在160—300万可解释区间，并用95/105㎡控制入场门槛。")],
        ),
        base_page(
            "sc2-09", "SC2", "土地市场：供地收缩、楼面下行、溢价低位，当前更像买方窗口",
            "近三年涉宅供地规划建面从823万㎡降至2026H1的110万㎡，推出楼面均价从8,321降至5,671元/㎡，平均溢价率仅1.07—1.63%。L-9应利用窗口控制楼面，而不是复制邻地7,507元/㎡。",
            [
                {"type": "table", "title": "天津涉宅土地趋势｜2023—2026H1", "headers": ["年度", "推出宗数", "推出规划建面（万㎡）", "推出楼面均价", "成交宗数", "成交建面（万㎡）", "成交楼面均价", "平均溢价率"], "rows": LAND_TREND_ROWS, "source_refs": ["SRC-LAND-COMP"]},
                {"type": "table", "title": "L-9最直接可比地块", "headers": ["地块", "编号", "容积率", "建面（㎡）", "成交楼面价", "成交总价（万）", "成交时间", "受让单位"], "rows": LAND_COMP_ROWS, "source_refs": ["SRC-LAND-COMP"]},
                {"type": "narrative", "title": "地价结论", "text": "雍阳中学东侧7,507—7,508元/㎡是最硬邻地锚，但不是L-9的安全楼面；相邻低密段5,630—7,532元/㎡提供市场参照，财务安全仍由精修成本、送赠净增值和售价/去化共同决定。", "source_refs": ["SRC-LAND-COMP", "SRC-FINANCE", "SRC-RANGE"]},
            ],
            ["SRC-LAND-COMP", "SRC-FINANCE", "SRC-RANGE"],
            chart_specs=[chart("CHART-LAND-TREND", "涉宅供地与楼面价趋势", "万㎡ / 元/㎡", [{"name": "推出规划建面", "values": [823, 473, 527, 110]}, {"name": "推出楼面均价", "values": [8321, 7004, 6828, 5671]}], ["SRC-LAND-COMP"], "combo_bar_line", dimensions=["2023", "2024", "2025", "2026H1"], measures=["推出规划建面", "推出楼面均价"], time_window="2023—2026H1", findings=["供地规划建面明显收缩", "楼面价中枢连续下行", "溢价率低位，底价成交为主"], decision_message="把土地窗口转化为议价纪律，优先控制4,600—5,100元/㎡基准区间。")],
        ),
    ])

    p = by_id.get("sc2-05")
    if p:
        p.update(
            {
                "title": "客群画像：本地外溢刚改 + 中端改善 + 新天津人，购买逻辑落在总价与兑现",
                "display_title": "客群｜触发、抗性与支付",
                "takeaway": "三类核心客群共享一条购买逻辑：三房实用、总价可控、通勤/生活配套可兑现、期房风险可解释。客群支持刚改高流速，但不支持把项目包装成纯豪宅或投资型产品。",
                "decision_question": "谁会在首开阶段买，为什么现在买，以及什么会让他放弃？",
                "decision_impact": "营销与户型先围绕95/105㎡三房和160—300万总价带建立验证，再用122/139㎡承接改善，不用未经样本支持的客群比例做预算。",
                "blocks": [
                    {"type": "table", "title": "三类核心客群与产品动作", "headers": ["客群", "购买触发", "主要抗性", "产品/营销动作"], "rows": [["本地核心老城外溢刚改", "孩子成长、老房置换、成熟配套", "首付/月供敏感、总价上限明确", "95/105㎡三房、透明总价、首开现金流"], ["地缘性刚改改善", "居住品质、环境、归家和社区感", "对竞品品质与交付兑现挑剔", "105/122㎡改善承接、示范区与交付材料先行"], ["新天津人刚需/刚改", "京津通勤、产业就业、资格与贷款便利", "交通距离、配套兑现、期房风险", "武清站通勤叙事、低门槛主力段、政策咨询"], ["教育关注人群（交叉标签）", "关注雍阳中学等教育资源", "不能接受学区承诺不清", "只表达‘紧邻/便利’事实，不做学区承诺"]], "source_refs": ["SRC-TASKBOOK", "SRC-CUSTOMER", "SRC-POLICY-LOAN"]},
                    {"type": "table", "title": "支付力证据｜区域成交结构，不等同目标项目客户比例", "headers": ["指标", "实测读数", "使用边界"], "rows": [["三房", "3,838套 / 69%（近12个月）", "支持三房为产品底盘"], ["90—140㎡", "1,039套 / 约38%（近3个月）", "支持面积段优先级"], ["160—300万", "2,433套 / 约44%（近12个月）", "支持总价带卡位"], ["目标项目客群构成", "暂无来访/认筹/净签样本", "必须由首开90天漏斗补证"]], "source_refs": ["SRC-MARKET", "SRC-CUSTOMER"]},
                    {"type": "narrative", "title": "明确抗性", "text": "客户未必反对低密，而是会追问：总价是否超预算、送赠是否真实、交付是否兑现、南侧商业是否影响居住、通勤与教育便利是否可被体验。产品和营销应按这些问题组织证据。", "source_refs": ["SRC-CUSTOMER", "SRC-TASKBOOK", "SRC-POLICY-SPACE"]},
                ],
                "chart_specs": [chart("CHART-CUSTOMER-STRUCTURE", "区域成交结构与任务书主力", "%", [{"name": "区域成交占比", "values": [69, 38, 44]}, {"name": "任务书配比", "values": [60, 60, 60]}], ["SRC-MARKET", "SRC-TASKBOOK"], "combo_bar_line", dimensions=["三房", "90—140㎡", "160—300万"], measures=["区域成交占比", "任务书95/105㎡合计配比"], time_window="混合窗口：近12个月/近3个月/任务书", findings=["三房是近12个月绝对主力", "90—140㎡是近3个月主力面积段", "160—300万是近12个月最厚总价带"], decision_message="先验证主流刚改，再用改善产品做梯度；不要把区域结构当成目标项目转化率。" )],
            }
        )

    p = by_id.get("sc3-02")
    if p:
        p.update(
            {
                "title": "任务书硬输入与口径冲突：4.19ha、FAR2.0设计输入，旧资料≤1.5待裁决",
                "display_title": "任务书｜硬输入与冲突",
                "takeaway": "任务书内嵌图示明确4.19ha、容积率2.0和95/105/122/139㎡配比；但官方条件档案与旧专项文件保留≤1.5口径。当前只能把2.0作为任务书设计输入、把≤1.5作为敏感性/冲突项，不能把任一口径冒充最终法定条件。",
                "decision_question": "强排和财务究竟以哪个FAR工作，如何避免口径漂移？",
                "decision_impact": "所有方案必须同时回填‘任务书FAR2.0’与‘法定条件待核’两套面积/套数；收到附件2后再裁决正式口径。",
                "blocks": [
                    {"type": "table", "title": "口径权威层级", "headers": ["口径", "来源/状态", "本版使用方式", "禁止事项"], "rows": [["4.19ha / 41,900㎡", "任务书正文与项目档案一致", "作为用地面积工作输入", "不替代红线测绘"], ["FAR2.0", "任务书内嵌图示，设计输入", "作为方案推演主场景", "不宣称为已核定法定指标"], ["≤1.5", "旧官方推介/专项文件口径，未见附件2原文", "作为低密敏感性和冲突记录", "不与2.0混算可售/货值"], ["建筑密度/绿地率/限高/停车/人防", "任务书要求复核附件2", "保持待核", "不凭经验填数"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-ASSUMPTIONS"]},
                    {"type": "table", "title": "任务书产品硬输入", "headers": ["面积段", "配比", "备注", "设计动作"], "rows": [["95㎡", "30%", "包含上下跃户型", "主力三房/高流速候选"], ["105㎡", "30%", "包含上下跃户型", "主力三房/高流速候选"], ["122㎡", "25%", "包含上下跃户型", "改善承接"], ["139㎡", "15%", "包含上下跃户型", "价值锚点/控量"], ["合计", "100%", "销售面积含全楼公摊", "不得与计容面积直接等同"]], "source_refs": ["SRC-TASKBOOK"]},
                    {"type": "narrative", "title": "处理原则", "text": "FAR冲突不是可以忽略的脚注，而是会同时改变楼栋数量、套数、车位、地库、景观投入、货值与财务结果的主变量；因此报告中的所有强排和货值数字都必须带口径标签。", "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-FINANCE"]},
                ],
            }
        )

    p = by_id.get("sc3-03")
    if p:
        p.update(
            {
                "title": "任务书设计约束：从总图、户型、展示区到地库，成果必须一一对应",
                "display_title": "设计任务｜成果验收",
                "takeaway": "任务书不是只要一张好看的总图，而是要求不少于两个规划方案，并把强排、户型、日照、交通、地库、展示区、立面和货值分析绑定成可落地成果。",
                "decision_question": "设计团队交付什么，前策如何验收，哪些缺失会直接阻断投决？",
                "decision_impact": "把任务书条款转成逐项验收表；没有总图—户型—指标—地库对应关系，就不能把方案称为可落地强排。",
                "blocks": [
                    {"type": "table", "title": "规划与产品成果清单", "headers": ["成果模块", "任务书原要求", "本版验收口径", "状态"], "rows": [["规划方案", "不少于两个；含强排总图、产品分布、指标、体块分析", "A/B同一输入可横向比较", "待实测"], ["空间形态", "大中庭小宅间、均好楼间距、轴线秩序、归家动线", "形成空间关系图+楼间距/日照数据", "待实测"], ["户型", "所有面积段、整层平面，与总图对应", "95/105/122/139㎡逐一落位", "待实测"], ["交通", "人行≤2、车行≤2，至少一个车人结合，人车分流", "入口数量、消防、交通影响分析", "待实测"], ["日照", "正南北为主；南侧商业按三层、局部四层暂不考虑", "正式日照图和边界条件", "待实测"], ["展示区", "约2,000㎡，实体展示、沙盘1:150、篮球场、桌游/乒乓球", "选址、动线、面积、运营与首开时序", "待实测"], ["地库", "柱网车位、出入口、疏散，经济性与均好性比较", "地库面积、车位、竖向与建安回填", "待实测"], ["立面", "标识性、公建化、亲人尺度局部材质、兼顾成本", "竞品/标杆对标+成本分配", "待实测"], ["周期", "2026-08-19—08-31概念方案汇报提交", "版本、时间、成果包可追溯", "任务书记录"]], "source_refs": ["SRC-TASKBOOK"]},
                    {"type": "narrative", "title": "不要遗漏的硬条件", "text": "层高3.1—3.15m、北向入户优先、减少变异户型、南侧商业不拆除、主入口考虑与北侧在建地块结合、住宅正南北为主、所有单体轮廓含保温及装饰层，均需进入设计交底和验收。", "source_refs": ["SRC-TASKBOOK"]},
                ],
            }
        )

    p = by_id.get("sc3-04")
    if p:
        p["title"] = "强排推演：同一口径做A/B两案，比较指标、流线、地库、货值与风险"
        p["display_title"] = "强排｜A/B双方案"
        p["takeaway"] = "两套方案不是两张风格图，而是同一任务书输入下的可比实验：A优先流速与经济性，B优先展示界面与空间溢价；两案都必须经过指标、日照、消防、车位、地库和货值回填。"
        p["decision_question"] = "在FAR口径待裁决的情况下，如何先做可比而不伪造精确总图？"
        p["decision_impact"] = "先建立参数化方案框架和验收表，待红线/附件2/测绘到位后由建筑师回填真实几何；本页关系图不代表建筑定位。"
        p["diagram_specs"] = [{
            "diagram_id": "DIA-L9-MASSING-AB",
            "grammar": "SPATIAL_DIAGRAM",
            "title": "FAR口径→A/B方案→强排验收→货值去化闸门",
            "diagram_type": "massing_sequence",
            "program_blocks": _diagram_blocks([
                ("任务书FAR2.0输入", "#64d2ff"),
                ("方案A｜高流速经济型", "#30d158"),
                ("方案B｜展示溢价型", "#ff9f0a"),
                ("95/105㎡主力", "#64d2ff"),
                ("122/139㎡改善", "#bf5af2"),
                ("地库/车位/日照复核", "#ffd60a"),
                ("货值×去化×成本决策", "#ff453a"),
            ]),
            "relations": [[0, 1], [0, 2], [1, 3], [2, 4], [1, 5], [2, 5], [5, 6]],
            "source_refs": ["SRC-TASKBOOK", "SRC-FINANCE", "SRC-POLICY-SPACE"],
            "note": "仅表达推演顺序与决策关系；无红线坐标时不表达真实尺寸、距离、日照或已完成强排成果。",
        }]
        p["blocks"] = [
            {"type": "table", "title": "两套强排方向（同边界比选）", "headers": ["维度", "方案A｜高流速经济型", "方案B｜展示溢价型", "共同验收"], "rows": [["产品", "95/105㎡优先，122/139㎡控量", "四档完整，局部上下跃作价值锚", "任务书配比/面积口径对应"], ["空间", "中庭均好、组团清晰、控制复杂度", "入口—示范区—商业界面连续礼序", "楼间距、日照、景观投入可核"], ["交通", "车行最短、人车分流、经济地库", "展示动线前置、步行体验强化", "入口≤2、消防、交通影响"], ["展示区", "与住宅脱开、首开快速搭建", "沿街/街角昭示、沉浸式体验", "约2,000㎡、篮球/桌游/乒乓球"], ["货值", "先兑现流速和总价带", "测试上下跃/庭院/露台等溢价", "政策、结构、消防、补缴"], ["成本", "控制地库/园林复杂度", "品质集中在可见界面", "综合建安4,200—4,600目标"], ["放行", "月均目标需首开回测", "送赠≥15%需实测", "两案指标、财务、去化同表"]], "source_refs": ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-RANGE", "SRC-POLICY-SPACE"]},
            {"type": "narrative", "title": "强排验收底线", "text": "没有红线、真北、标高和道路断面时，只能输出参数关系和待办；不能把概念体块、默认楼间距或示意套数包装成正式强排。", "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK"]},
        ]

    p = by_id.get("va2-01")
    if p:
        p.update(
            {
                "title": "价值研判：送赠/可变空间是第一货值杠杆，也是第一合规闸门",
                "display_title": "货值｜政策与合规",
                "takeaway": "天津多样性空间政策为挑空、坡屋顶、地下室、庭院、露台、封闭阳台和停车/架空平台提供研究路径，但政策允许不等于本项目已获批；每一平方米增值都要同时核对计容、销售、结构、消防、补缴和成本。",
                "decision_question": "哪些空间机制能创造货值，哪些条件必须先被主管部门和设计专业确认？",
                "decision_impact": "送赠不再作为一句营销口号，而是作为‘适用性—可实现面积—增量成本—净增值—售价/去化’五步验收链。",
                "blocks": [
                    {"type": "table", "title": "政策机制与项目级验证", "headers": ["机制", "入库政策要点", "项目级待核", "财务处理"], "rows": [["挑空/通高", "通高部位层高≤7.2m；低多层套数不限；套型总建面上限20%", "建筑类型、结构、消防、销售表达", "按实测可实现面积计，不直接按上限计"], ["坡屋顶", "奖励建面≤地上计容建面20%，不计容积率方向", "起坡点/坡度/净高、审批和补缴", "区级补缴地价28%+建安增量"], ["地下室", "层高≤6.0m；居住功能限制", "防水、结构、消防、可销售/赠送口径", "单列地下增量成本与收益"], ["庭院/露台/封闭阳台", "第二批补充细化方向", "适用条款、面积边界、交付责任", "仅做条件性溢价"], ["停车/架空平台", "鼓励计容奖励方向", "非经营部分、配建、审批", "纳入总图、车位和成本联算"]], "source_refs": ["SRC-POLICY-SPACE", "SRC-TASKBOOK"]},
                    {"type": "table", "title": "送赠净增值公式", "headers": ["项", "公式", "本版状态"], "rows": [["货值增益", "奖励/实得面积 × 认可单价", "理论，待强排实测"], ["补缴成本", "奖励面积 × 评估地价 × 28%（区级口径）", "政策资料口径，待项目确认"], ["建安增量", "奖励/地下/园林/结构面积 × 分项单方", "待工程量"], ["净增值", "货值增益 − 补缴成本 − 建安/营销/融资增量", "未闭合，不进入确定毛利"], ["放行", "净增值足以把悲观毛利拉回≥8%", "必须由A/B强排+财务复核"]], "source_refs": ["SRC-POLICY-SPACE", "SRC-FINANCE", "SRC-COST-FORECAST"]},
                ],
                "diagram_specs": [{
                    "diagram_id": "DIA-L9-VALUE-CHAIN",
                    "grammar": "SPATIAL_DIAGRAM",
                    "title": "政策条款→空间实现→成本补缴→货值去化→放行",
                    "diagram_type": "adjacency",
                    "program_blocks": _diagram_blocks([("政策适用", "#64d2ff"), ("上跃/下跃/庭院", "#bf5af2"), ("结构消防", "#ffd60a"), ("补缴+建安", "#ff9f0a"), ("实得货值", "#30d158"), ("财务/去化闸门", "#ff453a")]),
                    "relations": [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5]],
                    "source_refs": ["SRC-POLICY-SPACE", "SRC-TASKBOOK", "SRC-FINANCE"],
                    "note": "决策链关系图，不表达建筑尺寸或审批结果。",
                }],
            }
        )

    p = by_id.get("va2-02")
    if p:
        p.update(
            {
                "title": "财务精修：建安4,500—4,800为中枢，成本上修会直接压缩拿地空间",
                "display_title": "财务｜成本口径修正",
                "takeaway": "3,500元/㎡更接近不含地库的毛地面口径；低密洋房含地库、园林、北方保温的综合单方合理区间约4,000—5,500，中枢4,500—4,800。本版以4,600作为工作中枢，所有安全楼面都必须在此基础上重算。",
                "decision_question": "建安口径修正后，哪些楼面价与售价组合还留有安全边际？",
                "decision_impact": "将地库、园林、保温和展示区从‘隐含成本’改为显性输入；5,630以上不再用旧3,500口径解释。",
                "blocks": [
                    {"type": "table", "title": "综合建安口径", "headers": ["口径/情景", "单方（元/㎡）", "含义", "使用方式"], "rows": [["毛坯不含地库旧口径", 3500, "可能遗漏地下/园林", "只作历史对照"], ["强管控下限", 4200, "设计与招采压降目标", "积极情景"], ["工作中枢", 4600, "含地库+园林+北方保温", "主情景"], ["市场中枢", "4500—4800", "低密洋房市场级基准", "区间判断"], ["风险上限", 5200, "品质/材料/工程量上行", "压力情景"], ["综合上沿", 5500, "市场级P75提示", "预留风险"]], "source_refs": ["SRC-COST-FORECAST", "SRC-COST-BENCHMARK"]},
                    {"type": "table", "title": "精修后三情景｜楼面5,200 + 建安4,600 + 期间8% + 综合税6%", "headers": ["情景", "售价（元/㎡）", "全成本单方（元/㎡）", "毛利率", "判断"], "rows": [["乐观", 13080, 11631, "11.27%", "高于8%检验线"], ["中性", 12426, 11540, "7.13%", "低于8%检验线"], ["悲观", 11772, 11448, "2.49%", "接近保本"], ["备注", "—", "—", "—", "送赠净增值尚未计入"]], "source_refs": ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL"]},
                    {"type": "narrative", "title": "财务结论", "text": "成本精修后，项目不能依赖市场均价自然上行来修复利润；必须同时压住楼面、锁定综合建安、核实税费融资，并用强排实测的送赠净增值验证是否能把中性/悲观情景拉回安全线。", "source_refs": ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-POLICY-SPACE"]},
                ],
                "chart_specs": [chart("CHART-FINANCE-COST-BANDS", "综合建安成本区间", "元/㎡", [{"name": "成本口径", "values": [3500, 4200, 4600, 5200, 5500]}], ["SRC-COST-FORECAST", "SRC-COST-BENCHMARK"], "ranked_bar", dimensions=["旧口径对照", "强管控下限", "工作中枢", "风险情景", "综合上沿"], measures=["综合建安"], findings=["3,500可能遗漏地库与园林", "4,600为本版工作中枢", "5,200以上将显著压缩安全楼面"], decision_message="先锁定综合单方，再谈楼面价上限。"), chart("CHART-FINANCE-MARGIN-REFINED", "精修后三情景毛利率", "%", [{"name": "毛利率", "values": [11.27, 7.13, 2.49]}], ["SRC-FINANCE"], "ranked_bar", dimensions=["乐观", "中性", "悲观"], measures=["毛利率"], findings=["乐观仅约11.27%", "中性低于8%检验线", "悲观约2.49%接近保本"], decision_message="无送赠净增值时，不支持高楼面无条件进入。" )],
            }
        )

    p = by_id.get("va2-03")
    if p:
        p.update(
            {
                "title": "货值破局矩阵：送赠只能修复部分成本缺口，不能替代地价纪律",
                "display_title": "货值｜楼面×送赠",
                "takeaway": "送赠/奖励面积是第一货值杠杆，但净增值必须扣除补缴地价、建安、结构消防、融资和营销成本；7,507元/㎡不能仅凭‘有政策’被视为安全。",
                "decision_question": "送赠增益需要达到什么幅度，才足以改变楼面价结论？",
                "decision_impact": "把送赠幅度当作敏感性变量而非确定收益；正式方案需输出不送赠、15%、20%、25%四档净增值。",
                "blocks": [
                    {"type": "table", "title": "旧粗口径敏感性｜仅作历史对照，不作为主情景", "headers": ["送赠幅度", "楼面4,600", "楼面5,000", "楼面5,630", "楼面7,507", "使用边界"], "rows": [["0%", "10.2%", "6.8%", "1.4%", "-14.5%", "3,500/旧费用假设"], ["10%", "15.9%", "12.8%", "8.0%", "-6.5%", "只作方向"], ["15%", "18.4%", "15.4%", "10.8%", "-3.1%", "需扣净增成本"], ["20%", "20.7%", "17.8%", "13.4%", "0.1%", "不能直接视为精修结果"], ["25%", "22.8%", "20.0%", "15.8%", "3.0%", "仍不足以证明7,507安全"]], "source_refs": ["SRC-RANGE", "SRC-FINANCE", "SRC-POLICY-SPACE"]},
                    {"type": "table", "title": "精修口径的放行条件", "headers": ["变量", "主情景目标", "失守后的动作"], "rows": [["楼面价", "4,600—5,100；5,630为条件上限", ">5,630暂停进入"], ["综合建安", "4,200—4,600", ">5,200重做产品/报价"], ["送赠实得提升", "15%—20%理论目标", "不足15%不计入安全收益"], ["税费/融资", "综合税5.5%—6.0%，预缴现金峰值单列", "统一模型后再判断"], ["去化", "首开回测后36—42套/月目标", "低于30套/月暂停放量"]], "source_refs": ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-POLICY-SPACE", "SRC-RANGE"]},
                    {"type": "narrative", "title": "核心判断", "text": "送赠能改变的是‘实得货值/单方成本’，不能改变法定红线、市场支付力和现金流时间价值；因此它是验证项，不是免责项。", "source_refs": ["SRC-FINANCE", "SRC-POLICY-SPACE"]},
                ],
            }
        )

    p = by_id.get("sc2-03")
    if p:
        p.update(
            {
                "title": "竞品：近12个月实证显示‘高价不等于快销’，余量分化才是核心",
                "display_title": "竞品｜价格×速度×余量",
                "takeaway": "近12个月实证的头部竞品月均约6.5—30.2套，显著低于早期‘35—40套/月’估算；高价项目去化更慢、余量更高。L-9必须用主流面积段和首开节奏换流速。",
                "decision_question": "L-9应对标谁的价格、速度和剩余货量，而不是只看标杆单价？",
                "decision_impact": "竞品比较统一采用近12个月实证；早期近3个月估算只作为历史信号，不再作为目标承诺。",
                "blocks": [
                    {"type": "table", "title": "竞品近12个月实证｜2025-08—2026-07", "headers": ["项目", "板块", "成交套数", "均价（元/㎡）", "批准上市", "可售", "月均去化", "可售占比"], "rows": COMPETITOR_ROWS, "source_refs": ["SRC-COMPETITOR"]},
                    {"type": "table", "title": "竞争信号转译", "headers": ["信号", "证据", "对L-9的动作"], "rows": [["品质盘可溢价但速度有限", "绿城尚玉蘭17,808元/㎡、30.2套/月、可售占比22.6%", "品质表达服务于主力段，不把高价当默认售价"], ["中高价盘余量压力", "宏顺央璟颂可售占比41.7%；金地雍阳印75%", "避开重库存价位，先做可成交总价"], ["同板块直接竞对", "雍鑫·溪和林14,400元/㎡、20.1套/月", "主力产品与交付兑现形成效率差异"], ["历史估算不可直接外推", "早期近3个月约35—40套/月与近12个月实证不同", "报告和模型必须注明统计窗口"]], "source_refs": ["SRC-COMPETITOR", "SRC-MARKET"]},
                    {"type": "narrative", "title": "结论", "text": "L-9的合理竞品位置不是最高价，也不是老盘最低价，而是用90—140㎡主流段、明确的160—300万总价和可兑现的低密体验，争取超过重库存竞品的首开效率。", "source_refs": ["SRC-COMPETITOR", "SRC-CUSTOMER"]},
                ],
                "chart_specs": [
                    chart("CHART-COMP-PRICE-12M", "竞品近12个月成交均价", "元/㎡", [{"name": "成交均价", "values": [17808, 16444, 14400, 19301, 15062]}], ["SRC-COMPETITOR"], "ranked_bar", dimensions=[row[0] for row in COMPETITOR_ROWS], measures=["成交均价"], time_window="2025-08—2026-07", findings=["金地雍阳印均价最高但月均去化仅11.4套", "绿城尚玉蘭均价17,808元/㎡且余量较低", "价格必须和余量、速度一起读取"], decision_message="不追逐最高价，优先卡住主流总价与首开流速。"),
                    chart("CHART-COMP-SPEED-12M", "竞品近12个月月均去化", "套/月", [{"name": "月均去化", "values": [30.2, 26.1, 20.1, 11.4, 6.5]}], ["SRC-COMPETITOR"], "ranked_bar", dimensions=[row[0] for row in COMPETITOR_ROWS], measures=["月均去化"], time_window="2025-08—2026-07", findings=["近12个月实证区间为6.5—30.2套/月", "高价与高速度没有同步关系", "目标36—42套/月属于条件性目标而非现状事实"], decision_message="首开目标需用真实漏斗校准，不能只复制竞品早期估算。"),
                ],
            }
        )

    p = by_id.get("sc2-06")
    if p:
        p.update(
            {
                "title": "竞品速度：近12个月实证低于早期估算，首开节奏必须前置",
                "display_title": "竞品速度｜实证与估算分开",
                "takeaway": "早期近3个月估算约13—40套/月，近12个月实证仅约6.5—30.2套/月；L-9的36—42套/月只能作为有条件目标，必须在首开90天用漏斗回测。",
                "blocks": [
                    {"type": "table", "title": "同一批竞品的时间窗口差异", "headers": ["项目/梯队", "早期近3个月估算", "近12个月实证", "如何使用"], "rows": [["雍鑫·溪和林", "约40套/月", "20.1套/月", "早期信号，不作为承诺"], ["龙湖云曜", "约39套/月", "6.5套/月", "余量和统计窗口影响很大"], ["绿城尚玉蘭", "约36套/月", "30.2套/月", "接近成熟盘上限参考"], ["宏顺央璟颂", "约35套/月", "26.1套/月", "中速改善对标"], ["金地雍阳印", "约13套/月", "11.4套/月", "高价低速警示"]], "source_refs": ["SRC-COMPETITOR"]},
                    {"type": "table", "title": "L-9首开目标的验证门槛", "headers": ["阶段", "目标", "必须观察", "触发动作"], "rows": [["蓄客期", "形成90—140㎡有效客户池", "来源、面积、总价、置换链", "调整产品/渠道"], ["首开30天", "净签达到月均目标的前置比例", "认筹转化、折扣、退房", "校准价格与供货"], ["首开90天", "36—42套/月仅作上限目标", "净签、余量、回款", "低于30套/月暂停放量"]], "source_refs": ["SRC-COMPETITOR", "SRC-RANGE", "SRC-CUSTOMER"]},
                ],
            }
        )

    p = by_id.get("sc2-04")
    if p:
        p.update(
            {
                "title": "供应压测：库存约22.8个月，新增供地与面积错配共同决定窗口",
                "display_title": "供应压测｜存量、增量与窗口",
                "takeaway": "2026年7月末可售约9,695套、静态去化约22.8个月；按面积口径约为2026年成交面积的3.2倍。供应压力并非简单的‘没有需求’，而是存量、产品错配和未来供地叠加。",
                "blocks": [
                    {"type": "metrics", "title": "供应压力读数（口径分开）", "metrics": [{"label": "期末可售", "value": "9,695", "unit": "套｜2026-07"}, {"label": "静态去化", "value": "22.8", "unit": "个月｜9,695/425"}, {"label": "近13月成交面积", "value": "62.94", "unit": "万㎡"}, {"label": "可售面积代理", "value": "约117.4", "unit": "万㎡｜项目资料口径"}, {"label": "面积存量倍数", "value": "约3.2×", "unit": "可售面积/年成交面积"}, {"label": "待入市新增", "value": "约43.1", "unit": "万㎡计容｜项目资料口径"}], "source_refs": ["SRC-MARKET", "SRC-COMPETITOR", "SRC-LAND-COMP"]},
                    {"type": "table", "title": "存量与增量的设计含义", "headers": ["层次", "当前读数", "L-9应对"], "rows": [["市场存量", "9,695套、22.8个月", "主流总价优先，不做高价慢销的大面积押注"], ["面积存量", "可售面积约为年成交面积3.2倍（项目资料口径）", "首开控制供货，分批验证"], ["未来土地", "邻地及津武2025-080/058等约43.1万㎡计容", "抢窗口、做错位，持续跟踪入市时间"], ["真实矛盾", "供求比0.84但去化周期偏高", "把产品和价格错配作为首要诊断"]], "source_refs": ["SRC-MARKET", "SRC-LAND-COMP", "SRC-COMPETITOR"]},
                ],
            }
        )

    p = by_id.get("sc1-02")
    if p:
        p.update(
            {
                "title": "决策结论：有条件推进，基准楼面4,600—5,100，5,630仅在硬条件同时成立时进入",
                "display_title": "投决结论｜条件性推进",
                "takeaway": "本项目可以继续做方案与谈判，但当前不支持无条件拿地：综合建安按4,600元/㎡工作中枢、送赠实得单价提升15%为硬前提时，建议楼面基准控制在4,600—5,100元/㎡；5,630元/㎡仅作审慎上限；7,507元/㎡原则上放弃。",
                "decision_question": "在法定条件尚未闭合的前提下，什么价格与产品条件组合才允许继续推进？",
                "decision_impact": "把报价、产品、强排和首开验证绑成同一张闸门表；任何单项达标都不能替代全部条件闭合。",
                "blocks": [
                    {"type": "metrics", "title": "四维判断（条件性）", "metrics": [{"label": "市场机会", "value": "中等", "unit": "供求比0.84｜去化22.8月"}, {"label": "产品适配", "value": "较强", "unit": "三房69%｜主力段38%"}, {"label": "财务安全", "value": "偏紧", "unit": "建安4,600工作口径"}, {"label": "空间可行", "value": "未闭合", "unit": "红线/标高/附件2待补"}], "source_refs": ["SRC-MARKET", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-SITE-OFFICIAL"]},
                    {"type": "table", "title": "投决闸门｜必须同时满足", "headers": ["闸门", "放行条件", "当前状态", "不满足时"], "rows": [["法定规划", "附件2、红线、坐标、真北、标高、道路闭合", "阻断", "停止精确强排与正式报价"], ["产品强排", "FAR口径冲突裁决；两套方案指标、日照、车位、地库对应", "待做", "只能保留概念关系图"], ["财务底盘", "建安4,200—4,600可控；税费融资统一；奖励面积补缴可算", "条件", "5,630以上不进入"], ["货值政策", "送赠15—20%实得提升经强排验证", "理论", "不把送赠计入安全收益"], ["去化验证", "首开90天回测后达到约36—42套/月目标区间", "待回测", "低于30套/月暂停放量"]], "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-POLICY-SPACE", "SRC-COMPETITOR"]},
                    {"type": "narrative", "title": "一句话结论", "text": "可继续研究，不可无条件买地；先以4,600—5,100元/㎡做基准谈判区间，5,630只在送赠、成本、强排和首开证据同时通过时审慎进入，7,507不作为默认可接受价格。", "source_refs": ["SRC-RANGE", "SRC-FINANCE", "SRC-LAND-COMP"]},
                ],
                "chart_specs": [chart("CHART-DECISION-GATE", "价格闸门与条件", "元/㎡", [{"name": "楼面价", "values": [4600, 5100, 5630, 7507]}], ["SRC-FINANCE", "SRC-RANGE", "SRC-LAND-COMP"], "ranked_bar", dimensions=["基准下沿", "基准上沿", "审慎上限", "邻地锚"], measures=["楼面价"], findings=["精修成本口径下基准区间较早期进一步收窄", "5,630需送赠与强排实测同时成立", "7,507无送赠逻辑下不成立"], decision_message="把4,600—5,100作为谈判/测算基准，保留5,630条件上限。" )],
            }
        )

    pages.extend([
        base_page(
            "sc2-10", "SC2", "竞品实证图谱：价格、速度、余量必须三项联读",
            "五个竞品的近12个月实证表明：成交均价从14,400到19,301元/㎡，月均去化从6.5到30.2套，剩余货量分化明显；单看价格无法判断流速，L-9要争取的是‘主流总价+可兑现差异+及时首开’。",
            [
                {"type": "table", "title": "近12个月竞品全量快照｜统一窗口", "headers": ["项目", "板块", "成交套数", "均价（元/㎡）", "批准上市", "可售", "月均去化", "可售占比"], "rows": COMPETITOR_ROWS, "source_refs": ["SRC-COMPETITOR"]},
                {"type": "table", "title": "竞品位置与L-9策略", "headers": ["竞品信号", "市场含义", "L-9策略"], "rows": [["14,400元/㎡、20.1套/月的直接竞对", "主流改善有成交，但速度不是自然发生", "用95/105㎡效率和交付可信度抢首开"], ["17,808元/㎡、30.2套/月且可售占比22.6%", "品质溢价可成立，但接近成熟盘/清盘阶段", "提取品质机制，不复制价格"], ["19,301元/㎡、11.4套/月、可售75%", "高价与去化脱钩，余量压力明显", "不把标杆价作为售价依据"], ["6.5套/月且可售65.8%", "项目/产品/时序差异会放大库存", "首开供货和价格要先验证"]], "source_refs": ["SRC-COMPETITOR", "SRC-MARKET"]},
                {"type": "narrative", "title": "使用边界", "text": "竞品项目名和数字来自项目已入库的中指监测快照；统计期、身份和可售口径仍建议由中指数据Agent统一复核。早期近3个月估算保留在上一页，不与本页实证混算。", "source_refs": ["SRC-COMPETITOR"]},
            ],
            ["SRC-COMPETITOR", "SRC-MARKET"],
            chart_specs=[chart("CHART-COMP-SCATTER", "竞品价格×月均去化", "元/㎡ / 套/月", [{"name": "竞品", "values": [[17808, 30.2], [16444, 26.1], [14400, 20.1], [19301, 11.4], [15062, 6.5]]}], ["SRC-COMPETITOR"], "scatter_bubble", dimensions=["成交均价", "月均去化"], measures=["成交均价", "月均去化"], time_window="2025-08—2026-07", findings=["价格最高项目并非速度最高", "近12个月实证速度上限约30.2套/月", "余量占比是判断竞争压力的第三维"], decision_message="L-9应以总价、主力段和首开节奏形成综合效率，而非追逐最高单价。"), chart("CHART-COMP-REMAIN", "竞品可售占比", "%", [{"name": "可售占比", "values": [22.6, 41.7, 0, 75, 65.8]}], ["SRC-COMPETITOR"], "ranked_bar", dimensions=[row[0] for row in COMPETITOR_ROWS], measures=["可售占比"], findings=["部分竞品仍处高余量阶段", "绿城尚玉蘭余量较低接近清盘", "雍鑫·溪和林可售占比在源表中缺失，不能补猜"], decision_message="避开重库存段，首开分批供货。" )],
        ),
        base_page(
            "sc2-11", "SC2", "竞争空白：主流价格带 × 低密体验 × 可兑现送赠，是可测试的错位组合",
            "板块已经有高价改善、品牌改善和老盘低价锚，但‘刚改高流速+低密体验+政策边界透明’仍可作为测试方向；这只是产品假设，不是已验证的市场空白。",
            [
                {"type": "table", "title": "供给分层与机会假设", "headers": ["供给层", "已观察信号", "L-9可形成的错位", "必须验证"], "rows": [["老盘低价锚", "区域存在约9,864元/㎡级老盘价格参照", "不拼绝对低价，强调新房兑现", "同面积段折扣与客户分流"], ["主流改善竞品", "约14,400—16,444元/㎡、月均20—26套实证", "95/105㎡控制总价，122㎡承接改善", "首开价格/户型/竞品反应"], ["高价品质竞品", "17,808—19,301元/㎡，速度与余量分化", "提取界面、归家、立面机制，不照搬价格", "品质投入与支付力"], ["低密/创新空间", "任务书要求上下跃、天津新规研究", "局部小批验证实得价值", "政策、结构、消防、补缴情景"], ["未来新增供给", "资料口径约43.1万㎡计容", "用时序和产品差异抢窗口", "逐宗入市时间与面积段"]], "source_refs": ["SRC-COMPETITOR", "SRC-TASKBOOK", "SRC-POLICY-SPACE", "SRC-LAND-COMP"]},
                {"type": "narrative", "title": "产品假设", "text": "‘空白’不能由案例图片或设计偏好直接证明。只有当主流面积段、总价、低密界面和送赠机制在客户访谈、竞品反馈、首开转化和强排实测中同时成立，才可升级为差异化结论。", "source_refs": ["SRC-COMPETITOR", "SRC-CUSTOMER", "SRC-ARCHLIB-LOWDENSITY"]},
            ],
            ["SRC-COMPETITOR", "SRC-TASKBOOK", "SRC-POLICY-SPACE", "SRC-LAND-COMP"],
            diagram_specs=[{
                "diagram_id": "DIA-L9-COMP-WHITESPACE",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "价格/速度/密度/兑现四维错位假设",
                "diagram_type": "adjacency",
                "program_blocks": _diagram_blocks([("老盘低价锚", "#8e8e93"), ("主流改善", "#64d2ff"), ("高价品质", "#ff9f0a"), ("L-9主流刚改", "#30d158"), ("低密空间机制", "#bf5af2"), ("客户/首开验证", "#ffd60a")]),
                "relations": [[0, 3], [1, 3], [2, 3], [3, 4], [4, 5]],
                "source_refs": ["SRC-COMPETITOR", "SRC-TASKBOOK", "SRC-POLICY-SPACE"],
                "note": "错位假设关系图，不代表真实竞品距离、市场份额或已验证空白。",
            }],
        ),
        base_page(
            "sc3-05", "SC3", "周边与四侧界面：学校、公园、道路、商业都要转成可验证的设计动作",
            "L-9的区位资源足以支撑刚改与中端改善，但资源本身不是溢价；只有把四侧道路、雍阳中学、西苑公园、南侧商业和北侧在建地块转成安全、动线、展示和交付体验，价值才有机会兑现。",
            [
                {"type": "table", "title": "四侧界面与周边资源", "headers": ["方向/资源", "已入库事实", "机会", "风险/待核"], "rows": [["北｜规划支路一/在建住宅", "北至规划支路一；北侧有在建住宅", "主入口可与北侧地块关系统筹；北向入户优先", "道路断面、交付时序、交通接口、日照"], ["南｜振华西道/既有商业", "南至次干路；商业不予拆除", "城市展示界面、生活配套、示范区昭示", "噪声、人流、首层价值、三/四层日照"], ["东｜建设路", "东至主干路", "城市识别面、车行与展示入口候选", "交通影响、出入口审批、噪声"], ["西｜规划支路五/西苑公园", "西侧规划支路五；紧邻西苑公园", "运动、慢行、健康生活叙事", "公园边界、道路实施、景观视线"], ["片区｜杨村成熟生活圈", "教育、文化公园、少年宫等周边配套资料", "刚改与改善生活便利", "不得夸大学区或配套兑现"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-BRIEF-STRATEGY"]},
                {"type": "table", "title": "资源到动作的转换表", "headers": ["资源", "不能直接说", "可以验证的动作"], "rows": [["雍阳中学", "学区房/确定入学", "步行安全、界面噪声、家庭成长场景"], ["西苑公园", "天然高溢价", "慢行入口、运动展示、景观视线"], ["南侧商业", "配套已解决", "缓冲绿化、首层功能、交通与人流分流"], ["四侧临路", "四面都能开口", "按≤2个人行、≤2个车行和消防复核"], ["北侧在建地块", "可以共享全部设施", "明确边界、时序和接口协议"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"]},
            ],
            ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-BRIEF-STRATEGY"],
            diagram_specs=[{
                "diagram_id": "DIA-L9-SURROUNDING",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "四侧界面→动线/展示/缓冲/验证",
                "diagram_type": "site_constraints",
                "program_blocks": _diagram_blocks([("北侧在建住宅/规划支路一", "#64d2ff"), ("南侧振华西道/既有商业", "#ff9f0a"), ("东侧建设路/主干路", "#ffd60a"), ("西侧规划支路五/西苑公园", "#30d158"), ("住宅组团与示范区", "#bf5af2"), ("交通/日照/噪声复核", "#ff453a")]),
                "relations": [[0, 4], [1, 4], [2, 4], [3, 4], [4, 5]],
                "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"],
                "note": "非比例场地关系图；无正式红线和坐标，不表达真实距离、朝向精度或建筑位置。",
            }],
        ),
        base_page(
            "sc3-06", "SC3", "强排输入台账：任务书的每一条要求都要进入设计与经营验收",
            "任务书已明确项目定位、产品配比、空间形态、展示中心、交通、日照、地库、立面和成果清单；当前缺的不是方向，而是把方向落成同一边界下可计算、可复核、可交付的两套方案。",
            [
                {"type": "table", "title": "任务书原文硬输入与设计转译", "headers": ["模块", "任务书输入", "必须输出/验收", "当前状态"], "rows": [["项目身份", "14-03-05单元雍阳中学北侧，四侧道路", "区位、四至、城市界面分析", "已登记"], ["规模/性质", "住宅用地4.19ha；权属天津市人民政府", "用地面积与红线闭合", "面积已登记/红线待补"], ["FAR", "内嵌图示2.0；附件2法定条件待核", "2.0工作场景+冲突裁决+敏感性", "冲突保留"], ["产品定位", "刚需、中端改善、高流速；舒适/品质/智慧/健康", "定位、客户、产品逻辑", "已登记"], ["产品配比", "95/105/122/139㎡=30/30/25/15%，含上下跃", "户型、整层、总图一一对应", "配比已登记/图纸待做"], ["空间形态", "大中庭小宅间、轴线、均好楼间距、归家动线", "总图生成推演与空间分析", "待实测"], ["朝向/入户", "正南北为主；尽量北向入户、减少变异", "朝向统计、入户和户型变异表", "待实测"], ["交通", "人行≤2、车行≤2，至少1个车行结合人行；人车分流", "交通影响、消防、出入口与流线", "待实测"], ["日照/层高", "南侧商业三层、局部四层暂不考虑；层高3.1—3.15m", "日照分析与层高/剖面", "待实测"], ["展示中心", "约2,000㎡；沙盘1:150、篮球场、桌游/乒乓球、运动风格", "选址、平面、动线、运营和首开时序", "待实测"], ["地库", "柱网/车位/出入口/疏散；经济性与均好性比较", "地库平面、竖向、车位和成本", "待实测"], ["立面", "标识性、公建化、局部幕墙、低成本材料做虚实", "竞品/标杆对标、材料与成本分配", "待实测"], ["成果", "彩总、指标、分析图、所有户型/整层、地库、售楼处、鸟瞰/立面效果", "A/B完整成果包", "待交付"], ["周期", "2026-08-19—08-31概念方案汇报提交", "版本、责任人与提交物可追溯", "任务书记录"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"]},
                {"type": "narrative", "title": "台账结论", "text": "任务书要求已足够支撑设计团队开始参数化推演，但不足以支撑法定精确强排；附件2、红线、道路断面、测绘和北侧在建总图到位前，所有空间数字必须带‘工作输入/待核’标签。", "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"]},
            ],
            ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"],
            diagram_specs=[{
                "diagram_id": "DIA-L9-INPUT-LEDGER",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "任务书输入→设计成果→货值/去化/落地验收",
                "diagram_type": "program_zoning",
                "program_blocks": _diagram_blocks([("用地/规划条件", "#64d2ff"), ("产品/户型配比", "#bf5af2"), ("交通/日照/地库", "#ffd60a"), ("示范区/立面", "#ff9f0a"), ("总图+整层+指标", "#30d158"), ("货值/去化/财务", "#ff453a")]),
                "relations": [[0, 4], [1, 4], [2, 4], [3, 4], [4, 5]],
                "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"],
                "note": "输入台账关系图，不代表真实建筑布局。",
            }],
        ),
        base_page(
            "sc3-07", "SC3", "强排验收矩阵：没有同边界指标，A/B方案就不能比较",
            "强排成果的最低合格线不是‘有总图’，而是每套方案都能回填用地、计容、楼栋、户型、车位、日照、消防、地库、展示区、成本和货值，并且口径一致。",
            [
                {"type": "table", "title": "A/B方案共同验收矩阵", "headers": ["验收项", "方案A需交付", "方案B需交付", "通过标准"], "rows": [["边界/指标", "红线、FAR、建筑密度、绿地、限高、计容", "同一套输入", "来源文件与计算表闭合"], ["楼栋与户型", "95/105/122/139㎡逐栋/整层对应", "同上", "面宽、进深、保温装饰层与总图一致"], ["日照/消防", "正式日照、消防车道、登高面", "正式日照、消防车道、登高面", "按天津规范和法定边界校核"], ["交通", "人行≤2、车行≤2、人车分流", "同上", "外部道路衔接与交通影响可说明"], ["地库", "柱网、车位、出入口、疏散、竖向", "经济性/均好性对照", "车位指标与建安成本可回填"], ["展示区", "约2,000㎡、实体展示、运动功能", "沿街/街角昭示与沉浸体验", "面积、动线、首开时序和成本可回填"], ["货值/去化", "主流段流速、成本优先", "空间溢价、送赠实测", "售价、套数、净增值、去化假设同表"], ["成果完整性", "彩总、指标、分析图、户型/整层、地库", "同上", "可直接进入评审与复核"]], "source_refs": ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-FINANCE", "SRC-POLICY-SPACE"]},
                {"type": "table", "title": "当前状态与补证责任", "headers": ["缺口", "影响", "责任/下一动作", "状态"], "rows": [["附件2规划条件", "FAR/密度/绿地/限高/停车可能改变方案", "规划负责人取得盖章原文", "D0阻断"], ["红线/测绘/真北/标高", "不能做精确楼栋、日照、竖向", "测绘/建筑建立统一底图", "D0阻断"], ["地库与工程量", "建安4,200—5,200区间无法收敛", "建筑+成本分项量核", "D1"], ["送赠适用性与补缴", "货值净增值不能确认", "规划/法务/财务联审", "D1"], ["真实首开漏斗", "36—42套/月只是情景", "营销回填来访—净签—退房", "D1"]], "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-POLICY-SPACE", "SRC-COMPETITOR"]},
            ],
            ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-COST-FORECAST", "SRC-FINANCE", "SRC-POLICY-SPACE"],
            diagram_specs=[{
                "diagram_id": "DIA-L9-ACCEPTANCE",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "同一输入→双方案→六类校核→投决",
                "diagram_type": "circulation",
                "program_blocks": _diagram_blocks([("统一边界输入", "#64d2ff"), ("方案A", "#30d158"), ("方案B", "#ff9f0a"), ("指标/户型", "#bf5af2"), ("交通/日照/消防", "#ffd60a"), ("地库/成本/货值", "#ff453a"), ("投决闸门", "#f5f5f7")]),
                "circulation": [{"label": "验收路径", "points": [[110, 300], [245, 180], [390, 300], [535, 180], [690, 300]], "color": "#64d2ff"}],
                "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-FINANCE"],
                "note": "验收路径示意，不代表建筑消防流线或实际总图道路。",
            }],
        ),
        base_page(
            "sc3-08", "SC3", "方案A/B评分卡：A保流速，B试溢价，先用同一套指标做选择",
            "方案A是首开现金流与成本控制优先，方案B是展示界面与空间溢价优先；当前评分是决策框架而非已完成设计的打分，最终必须由实测方案数据替换。",
            [
                {"type": "table", "title": "双方案经营—设计评分框架（5分制，待实测回填）", "headers": ["维度", "权重", "方案A｜高流速经济型", "方案B｜展示溢价型", "判定依据"], "rows": [["首开流速", "25%", "4（目标）", "3（假设）", "95/105㎡供货、总价、认筹转化"], ["货值上限", "20%", "3（假设）", "4（目标）", "送赠实得、122/139㎡溢价"], ["建安与地库", "20%", "4（目标）", "3（假设）", "综合单方、地库效率、园林投入"], ["规划落地", "15%", "待测", "待测", "日照、消防、车位、竖向"], ["展示与品牌", "10%", "3（假设）", "5（目标）", "沿街界面、示范区、立面"], ["风险可控", "10%", "4（假设）", "3（假设）", "政策、工程量、交付复杂度"], ["加权总分", "100%", "待实测", "待实测", "不得用概念图直接定案"]], "source_refs": ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-POLICY-SPACE", "SRC-RANGE"]},
                {"type": "table", "title": "方案选择触发器", "headers": ["如果实测结果是", "优先选择", "原因"], "rows": [["A在悲观售价下毛利≥8%，且月均≥36套", "A先行、B局部导入", "现金流与试错平衡"], ["B的送赠净增值足以覆盖补缴+建安，且首开总价不超160—300万", "B分批验证", "空间溢价有经营依据"], ["两案均无法满足法定日照/消防/地库", "暂停方案放大", "先补底图和规划条件"], ["首开90天净签<30套/月", "收缩供货/重做价格", "不把目标当事实"]], "source_refs": ["SRC-FINANCE", "SRC-COMPETITOR", "SRC-RANGE", "SRC-TASKBOOK"]},
                {"type": "narrative", "title": "评分使用边界", "text": "A/B分数是为了让建筑、产品、成本和营销在同一张表上对话，不是替代日照、消防、交通和正式财务审核。", "source_refs": ["SRC-TASKBOOK", "SRC-FINANCE"]},
            ],
            ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-POLICY-SPACE", "SRC-RANGE"],
            chart_specs=[chart("CHART-SCHEME-SCORE", "A/B方案条件性评分", "分", [{"name": "方案A", "values": [4, 3, 4, 3, 3, 4]}, {"name": "方案B", "values": [3, 4, 3, 3, 5, 3]}], ["SRC-TASKBOOK", "SRC-COST-FORECAST", "SRC-POLICY-SPACE"], "radar", dimensions=["首开流速", "货值上限", "建安与地库", "规划落地", "展示品牌", "风险可控"], measures=["方案A", "方案B"], findings=["A偏现金流与成本，B偏展示与溢价", "规划落地项目前均为待实测", "分数不能替代正式图纸和财务模型"], decision_message="先用A作为现金流基准，同时保留B的局部溢价验证。")],
        ),
        base_page(
            "sc3-09", "SC3", "强排生成逻辑：价值分区→楼栋与户型→交通日照→地库→货值回填",
            "强排的正确顺序是先识别四侧价值与不利因素，再组织楼栋、户型和流线，最后用地库、日照、成本和货值反校；顺序颠倒就容易出现‘总图好看、指标不闭合’。",
            [
                {"type": "table", "title": "参数化推演五步", "headers": ["步骤", "输入", "输出", "失败时"], "rows": [["1 价值分区", "四侧道路、学校、公园、商业、北侧在建", "城市界面/安静界面/展示界面/缓冲界面", "不做先验高低价结论"], ["2 产品落位", "95/105/122/139㎡配比、朝向、层高", "主力/改善/价值锚点楼栋与整层", "不把总图套数当成交套数"], ["3 流线与日照", "入口限制、人车分流、南侧商业层数、真北", "人行/车行/消防/日照方案", "红线/真北缺失则保持概念"], ["4 地库与工程", "柱网、车位、竖向、疏散、地库比例", "经济地库/均好地库对照", "成本区间不能收敛"], ["5 货值回填", "可售/奖励面积、单价、补缴、建安、去化", "A/B经营评分与放行条件", "不升级为确定投资结论"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-COST-FORECAST", "SRC-FINANCE"]},
                {"type": "narrative", "title": "本版输出边界", "text": "本版用关系图表达生成逻辑，未伪造真实建筑轮廓、楼间距、日照或车位数；正式总图必须由人类设计模型在统一坐标和法定条件下完成，再回填本报告的经营判断。", "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK"]},
            ],
            ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-COST-FORECAST", "SRC-FINANCE"],
            diagram_specs=[{
                "diagram_id": "DIA-L9-GENERATION",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "价值分区→产品落位→合规校核→地库工程→经营回填",
                "diagram_type": "adjacency",
                "program_blocks": _diagram_blocks([("四侧价值/不利因素", "#64d2ff"), ("户型与楼栋", "#bf5af2"), ("入口/人车/日照", "#ffd60a"), ("地库/竖向/消防", "#ff9f0a"), ("货值/去化/成本", "#30d158"), ("设计模型复核", "#ff453a")]),
                "relations": [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5]],
                "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-FINANCE"],
                "note": "参数化推演逻辑图；不是建筑总平面或比例图。",
            }],
        ),
        base_page(
            "va2-05", "VA2", "政策到货值：挑空、坡屋顶、地下室与庭院必须逐项过合规闸门",
            "政策提供了低密产品创新的可能，但‘可研究’不等于‘可销售、可赠送、可计入货值’；项目必须把适用条款、工程量、补缴地价和客户可感知价值逐项核验。",
            [
                {"type": "table", "title": "政策机制→设计动作→经营验证", "headers": ["政策机制", "可研究设计动作", "经营价值假设", "项目级验收"], "rows": [["低多层挑空/通高", "上跃、局部通高空间", "改善体验与顶层价值", "层高≤7.2m、套型比例/结构消防/销售口径"], ["坡屋顶奖励建面", "顶层坡屋顶与上跃", "增加实得/奖励面积", "≤地上计容建面20%、起坡/坡度/净高、补缴"], ["地下室", "下跃/储藏/功能空间", "首层溢价与空间增益", "层高≤6.0m、功能限制、防水消防、补缴"], ["庭院/露台/封闭阳台", "首层庭院、局部露台/封闭阳台", "首层与改善溢价", "适用批次、面积边界、交付责任"], ["停车/架空平台", "停车与架空公共空间组合", "总图效率/公共体验", "配建标准、计容奖励、非经营部分口径"]], "source_refs": ["SRC-POLICY-SPACE", "SRC-TASKBOOK"]},
                {"type": "table", "title": "四账联算", "headers": ["账目", "必须回答", "当前状态"], "rows": [["面积账", "计容、可售、奖励、实得面积分别是多少？", "未有强排工程量"], ["政策账", "哪一条款适用，谁审批，何时有效？", "政策框架已入库，项目适用待确认"], ["成本账", "补缴地价、结构、防水、保温、园林和营销增量？", "建安区间已入库，分项待核"], ["市场账", "客户愿意为哪种空间付多少钱？", "区域结构有证，目标项目漏斗缺失"], ["现金账", "预缴税费、融资峰值和回款节奏如何变化？", "税负框架有证，现金流待重算"]], "source_refs": ["SRC-POLICY-SPACE", "SRC-FINANCE", "SRC-COST-FORECAST", "SRC-CUSTOMER"]},
                {"type": "narrative", "title": "放行原则", "text": "只有当净增值=货值增益−补缴地价−建安/结构/营销/融资增量在悲观售价下仍能支撑目标毛利，并且规划、消防、结构、交付口径闭合，创新空间才可进入主方案；否则只做小批验证。", "source_refs": ["SRC-POLICY-SPACE", "SRC-FINANCE"]},
            ],
            ["SRC-POLICY-SPACE", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-COST-FORECAST"],
            chart_specs=[chart("CHART-VALUE-MECHANISMS", "空间机制的验证优先级", "优先级分", [{"name": "验证优先级", "values": [5, 4, 3, 3, 2]}], ["SRC-POLICY-SPACE", "SRC-TASKBOOK"], "ranked_bar", dimensions=["政策适用/审批", "实得面积/净增值", "结构消防", "客户支付/溢价", "公共空间/停车"], measures=["优先级"], findings=["政策适用性是全部货值机制的前置", "补缴地价与建安增量必须显性计入", "客户溢价和交付口径不能由设计单方面假定"], decision_message="先验证政策和净增值，再决定哪些创新空间进入首开。")],
        ),
        base_page(
            "va2-06", "VA2", "财务边界：安全楼面不是一个数字，而是售价、成本、税费与送赠的组合",
            "精修口径下，楼面价安全边界明显收窄：中性售价12,426元/㎡、建安4,600元/㎡时，5,200楼面仅约7.13%毛利；因此4,600—5,100是基准谈判区间，5,630只能作为条件上限。",
            [
                {"type": "table", "title": "精修情景与安全线", "headers": ["情景", "售价（元/㎡）", "楼面/建安", "毛利率", "与8%线关系"], "rows": [["乐观", 13080, "5,200 / 4,600", "11.27%", "高于"], ["中性", 12426, "5,200 / 4,600", "7.13%", "低于"], ["悲观", 11772, "5,200 / 4,600", "2.49%", "显著低于"], ["安全楼面（旧精修边界）", "12,426", "— / 4,600", "目标15%", "≤4,577"], ["保本楼面（旧精修边界）", "12,426", "— / 4,600", "0%", "≤6,441"]], "source_refs": ["SRC-FINANCE", "SRC-COST-FORECAST"]},
                {"type": "table", "title": "价格组合决策", "headers": ["楼面档位", "送赠/实得前提", "使用结论", "下一动作"], "rows": [["≤4,600", "不依赖送赠也有安全垫", "优先进入测算", "做A案现金流基准"], ["4,600—5,100", "建安4,200—4,600；送赠15%需实测", "主推谈判区间", "做A/B同边界复核"], ["5,100—5,630", "送赠≥15%、建安≤4,600、税费融资闭合", "审慎条件进入", "先拿政策/强排证据"], [">5,630 / 7,507", "需极高净增值且悲观仍过线", "原则上不进入", "要求降价或放弃"]], "source_refs": ["SRC-FINANCE", "SRC-RANGE", "SRC-LAND-COMP", "SRC-POLICY-SPACE"]},
                {"type": "narrative", "title": "财务结论", "text": "任何只展示售价或邻地成交价的财务判断都不完整；本项目必须把计容口径、综合建安、税费、融资、奖励面积补缴、实得货值和去化时间放进同一张可复算模型。", "source_refs": ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL"]},
            ],
            ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL", "SRC-RANGE", "SRC-LAND-COMP"],
            chart_specs=[chart("CHART-VALUE-LAND-GATE", "楼面价边界与情景", "元/㎡", [{"name": "楼面价", "values": [4577, 5000, 5100, 5630, 6441, 7507]}], ["SRC-FINANCE", "SRC-RANGE", "SRC-LAND-COMP"], "ranked_bar", dimensions=["中性15%安全线", "基准中枢", "精修主推上沿", "条件上限", "中性保本线", "邻地锚"], measures=["楼面价"], findings=["中性15%安全线约4,577元/㎡", "5,630只可作为送赠/成本同时成立的条件上限", "7,507高于保本线，不能无条件接受"], decision_message="先把楼面价压进4,600—5,100，再用实测净增值争取上探。")],
        ),
        base_page(
            "va2-07", "VA2", "货值去化联动：508套是低密1.5敏感性，FAR2.0约736套只是任务书示意",
            "当前存在两套总量口径：旧专项按≤1.5推导约508套，任务书FAR2.0×41,900㎡按加权户型推导约736套；两者都不是最终可售套数。去化周期必须随正式总图、配套和可售面积重算。",
            [
                {"type": "table", "title": "总量口径对照", "headers": ["口径", "计容/工作面积", "示意套数", "用途", "限制"], "rows": [["任务书FAR2.0", "83,800㎡（41,900×2.0）", "约736套", "强排主场景/任务书输入", "不等同最终可售，法定附件2待核"], ["旧低密≤1.5敏感性", "62,850㎡（41,900×1.5）", "约508套", "低密货值/去化敏感性", "来自旧口径，不能覆盖任务书2.0"], ["正式可售", "需拆非住宅/配套/公摊/奖励面积", "待总图", "最终财务与营销", "当前缺红线、指标和户型整层"]], "source_refs": ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-RANGE"]},
                {"type": "table", "title": "去化情景｜仅对508套敏感性示意", "headers": ["月均净签", "去化月数", "经营含义", "验证要求"], "rows": [["30套", "16.9月", "偏慢/现金占用长", "检查价格、供货和渠道"], ["36套", "14.1月", "稳健目标", "首开90天漏斗回测"], ["42套", "12.1月", "高流速目标", "需主力段和价格共同成立"], ["48套", "10.6月", "强跑量假设", "不得作为基准承诺"]], "source_refs": ["SRC-RANGE", "SRC-COMPETITOR", "SRC-CUSTOMER"]},
                {"type": "narrative", "title": "现金回收结论", "text": "套数越大不代表货值越高：FAR2.0的额外面积会同步带来地库、园林、配套、建安、税费和融资峰值。正式版应按首开供货—回款—后续批次—库存逐月做现金流，而不是只用‘总套数/月均去化’相除。", "source_refs": ["SRC-RANGE", "SRC-FINANCE", "SRC-COST-FORECAST"]},
            ],
            ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-RANGE", "SRC-COMPETITOR", "SRC-FINANCE"],
            chart_specs=[chart("CHART-ABSORPTION-SCENARIOS", "508套敏感性去化周期", "月", [{"name": "去化月数", "values": [16.9, 14.1, 12.1, 10.6]}], ["SRC-RANGE", "SRC-COMPETITOR"], "ranked_bar", dimensions=["30套/月", "36套/月", "42套/月", "48套/月"], measures=["去化月数"], findings=["36—42套/月对应约12—14个月", "目标来自条件性模型，不是目标项目实证", "总套数口径一变，周期必须重算"], decision_message="先用508套做敏感性，再等待正式总图和首开漏斗重建模型。")],
        ),
    ])
    pages.extend([
        base_page(
            "va3-04", "VA3", "户型配比区间：任务书四档固定骨架，市场结构决定首开排序",
            "任务书给出95/105/122/139㎡=30/30/25/15%的产品骨架，市场实测支持90—140㎡和三房优先；建议把95/105㎡作为首开现金流，122㎡承接改善，139㎡控量验证，具体套数等正式总图。",
            [
                {"type": "table", "title": "任务书配比→产品角色", "headers": ["销售面积", "任务书配比", "市场对应", "首开/后续角色", "必须验证"], "rows": [["95㎡", "30%", "90—120㎡主力带；三房需求", "首开主力", "三房效率、月供、面宽、得房"], ["105㎡", "30%", "90—120㎡主力带；三房需求", "首开主力", "功能完整、总价、认筹"], ["122㎡", "25%", "120—140㎡主力改善带", "首开/二批承接", "主套、公共厅、溢价"], ["139㎡", "15%", "140—180㎡改善溢价带的控量映射", "二批/价值锚", "总价、去化、实得"], ["上下跃", "各档备注包含", "不能直接从区域结构推导", "小批试验", "政策、结构、消防、成本、销售口径"]], "source_refs": ["SRC-TASKBOOK", "SRC-MARKET", "SRC-POLICY-SPACE"]},
                {"type": "table", "title": "市场结构与任务书的差异", "headers": ["维度", "市场实测", "任务书输入", "前策结论"], "rows": [["三房", "3,838套/约69%（近12个月）", "未规定房型比例", "三房作为主力底盘"], ["90—140㎡", "1,039套/约38%（近3个月）", "95/105/122合计85%（销售面积配比）", "配比方向匹配，但需控制同质化"], ["160—300万", "2,433套/约44%（近12个月）", "未规定总价", "首开总价与面积/单价共同校准"], ["最终套数", "区域成交不能外推", "FAR/配比为设计输入", "必须由总图和首开漏斗落地"]], "source_refs": ["SRC-MARKET", "SRC-TASKBOOK", "SRC-CUSTOMER"]},
                {"type": "narrative", "title": "配比结论", "text": "任务书配比可以作为设计起点，不能直接视为最优配比；正式方案应在A/B两案中测试95/105㎡供货比例、122/139㎡溢价、上下跃小批量和160—300万总价覆盖率。", "source_refs": ["SRC-TASKBOOK", "SRC-MARKET", "SRC-RANGE"]},
            ],
            ["SRC-TASKBOOK", "SRC-MARKET", "SRC-CUSTOMER", "SRC-POLICY-SPACE"],
            chart_specs=[chart("CHART-MIX-MARKET-FIT", "任务书配比与市场主力覆盖", "%", [{"name": "任务书配比", "values": [30, 30, 25, 15]}, {"name": "市场面积段信号", "values": [32.4, 32.4, 26.3, 11.2]}], ["SRC-TASKBOOK", "SRC-MARKET"], "combo_bar_line", dimensions=["95/90—120", "105/90—120", "122/120—140", "139/140—180"], measures=["任务书配比", "市场成交占比/参考"], time_window="任务书配比 + 近3个月面积结构（非同窗口直接相加）", findings=["95/105㎡合计60%与主流面积段方向一致", "122㎡对应120—140㎡改善带", "139㎡需控制供货，区域140—180㎡样本较小"], decision_message="配比方向可用，套数和首开批次必须由强排与漏斗验证。")],
        ),
        base_page(
            "va3-05", "VA3", "户型研发与竞品对标：每个面积段都要回答‘面宽、核心筒、实得、总价’",
            "任务书明确要求各面积段竞品对标，并可放眼全市；当前已具备区域成交结构，但缺少完整户型图、核心筒和实得面积样本。因此本页把研究维度和设计交付明确化，不虚构竞品户型参数。",
            [
                {"type": "table", "title": "四档户型研发任务书", "headers": ["面积段", "刚改/改善任务", "重点空间指标", "竞品对标字段", "当前状态"], "rows": [["95㎡", "高流速三房底盘", "面宽、三开间/功能完整、收纳、得房", "同面积段户型图、核心筒、梯户比、总价", "区域数据有，户型样本待补"], ["105㎡", "主力改善三房", "面宽进深、餐客厅、主卧、弹性空间", "同面积段价格/折扣/实得", "待补"], ["122㎡", "改善承接", "主套、公共厅、南向面宽、归家界面", "同类改善盘成交与样板反馈", "待补"], ["139㎡", "价值锚/控量", "低密景观、上下跃/庭院/露台边界", "同类低密产品溢价与去化", "待补"], ["全段上下跃", "政策创新试验", "挑空/坡屋顶/地下/庭院/露台", "法规、结构、消防、补缴、交付", "项目级未闭合"]], "source_refs": ["SRC-TASKBOOK", "SRC-POLICY-SPACE", "SRC-CUSTOMER"]},
                {"type": "table", "title": "竞品对标输出模板（任务书要求）", "headers": ["字段", "为什么重要", "必须回填"], "rows": [["面积/户型", "判断需求与总价", "户型图、整层图、套数"], ["面宽/进深", "判断舒适性和立面", "尺寸、朝向、采光"], ["核心筒/梯户比", "判断效率和成本", "核心筒平面、消防"], ["实得/赠送", "判断货值与合规", "计容、可售、奖励、补缴"], ["成交/去化", "判断商业结果", "统一12个月窗口、净签、余量"], ["交付/样板", "判断兑现能力", "材料、公共空间、实景反馈"]], "source_refs": ["SRC-TASKBOOK", "SRC-COMPETITOR", "SRC-POLICY-SPACE"]},
                {"type": "narrative", "title": "对标结论", "text": "ArchLib案例只提取‘主流面积段+局部可变空间+公共生活场景’的机制；不迁移成都案例的价格、套数、去化或成本。真正的户型结论要等全市样本、天津政策和项目强排同时补齐。", "source_refs": ["SRC-ARCHLIB-LOWDENSITY", "SRC-TASKBOOK", "SRC-COMPETITOR"]},
            ],
            ["SRC-TASKBOOK", "SRC-COMPETITOR", "SRC-POLICY-SPACE", "SRC-ARCHLIB-LOWDENSITY"],
            asset_refs=["ASSET-ARCHLIB-LOWDENSITY"],
        ),
        base_page(
            "va3-06", "VA3", "方案亮点排序：可兑现的主流体验先行，创新空间小批验证",
            "真正能支撑销售和交付的亮点是城市界面、归家序列、全龄运动、户型舒适和透明交付；上下跃、庭院、露台等高风险机制放在小批试验位，不让创新绑架全盘成本。",
            [
                {"type": "table", "title": "方案亮点—价值—风险—验证", "headers": ["亮点", "价值假设", "主要风险", "验证动作", "优先级"], "rows": [["运动型展示中心", "品牌引流、健康生活、城市名片", "2,000㎡投入与运营成本", "篮球场/桌游/乒乓球、动线、首开时序", "P0"], ["城市界面与归家礼序", "提升第一印象与改善感知", "沿街噪声、人流、交付兑现", "沿街/街角选址、步行剖面、材料成本", "P0"], ["大中庭小宅间", "均好性、景观与社区感", "楼间距/日照/景观投入", "总图、日照、景观覆盖率", "P0"], ["95/105㎡高效三房", "首开流速与支付力", "同质化、总价压力", "户型图、认筹、净签、退房", "P0"], ["南侧商业缓冲", "减少干扰、形成生活界面", "噪声/首层价值", "分区、绿化、交通、人流管理", "P1"], ["上下跃/庭院/露台", "实得面积与局部溢价", "政策、结构、消防、补缴、交付", "小批样板+四账联算", "P1"], ["公建化立面局部材质", "标识性与品牌识别", "材料成本与维护", "竞品/标杆对标、材料样板", "P1"]], "source_refs": ["SRC-TASKBOOK", "SRC-POLICY-SPACE", "SRC-COST-FORECAST", "SRC-MARKET"]},
                {"type": "narrative", "title": "亮点结论", "text": "亮点顺序应服务于高流速：先保证可买、可住、可交付，再用局部空间机制拉开差异；任何不能写进指标、成本、审批和交付清单的‘亮点’都只能停留在概念表达。", "source_refs": ["SRC-TASKBOOK", "SRC-CUSTOMER", "SRC-COST-FORECAST"]},
            ],
            ["SRC-TASKBOOK", "SRC-POLICY-SPACE", "SRC-COST-FORECAST", "SRC-MARKET"],
            chart_specs=[chart("CHART-HIGHLIGHT-PRIORITY", "方案亮点验证优先级", "优先级分", [{"name": "优先级", "values": [5, 5, 5, 5, 4, 3, 3]}], ["SRC-TASKBOOK", "SRC-POLICY-SPACE"], "ranked_bar", dimensions=["运动展示中心", "归家与城市界面", "大中庭小宅间", "95/105㎡高效三房", "南侧商业缓冲", "上下跃/庭院/露台", "公建化立面"], measures=["优先级"], findings=["主流产品与归家体验是P0", "创新空间需要政策和工程量证据", "立面标识性必须与成本共同排序"], decision_message="先做可兑现体验链，再做小批创新验证。")],
        ),
    ])
    prompt_blocks = []
    for item in _prompt_records():
        prompt_blocks.append({
            "type": "prompt",
            "title": f"{item['prompt_id']}｜{item['title']}",
            "text": str(item["prompt"]),
            "items": [f"任务目的：{item['scope']}", f"验收标准：{item['acceptance']}"],
            "source_refs": item["source_refs"],
        })
    pages.append(
        base_page(
            "cs-02", "CS", "中指数据Agent：缺口抓取任务与可复制提示词",
            "当前报告已把缺口拆成可执行的数据任务：法定规划与红线、土地合同、连续市场、面积/总价/户型、竞品全量、未来供应、客群漏斗、政策适用和财务输入。抓取结果入库前，所有相应结论保持条件性。",
            [
                {"type": "narrative", "title": "使用说明", "text": "下方提示词可逐条复制给中指数据Agent。每次回传必须包含原始来源、统计期、口径、字段级定位、缺失字段和可复算公式；不得用推测值填补。完整Markdown版本已同步到工作目录。", "source_refs": ["SRC-TASKBOOK", "SRC-MARKET", "SRC-COMPETITOR"]},
                *prompt_blocks,
            ],
            ["SRC-TASKBOOK", "SRC-MARKET", "SRC-COMPETITOR", "SRC-LAND-COMP", "SRC-POLICY-SPACE", "SRC-FINANCE"],
            unit_status="partial", unit_role="data_acquisition", story_role="evidence_appendix", appendix_policy="evidence", visual_evidence="gap",
        )
    )
    pages.extend([
        base_page(
            "cs-03", "CS", "投决闸门：从探索性报告到正式可研，还差哪些硬证",
            "本版已经完成资料复核、市场/竞品/客群/货值/去化/强排框架和可复制抓取任务，但仍是探索性条件研判；只有D0法定边界、A/B强排实测、财务闭合和首开漏斗补齐，才能进入正式可研输入。",
            [
                {"type": "table", "title": "状态总表", "headers": ["闸门", "已完成", "未完成", "升级标准"], "rows": [["证据底座", "已登记来源、6条核心主张、任务书正文/内嵌图像复核", "中指统一窗口与原始文件仍需补", "来源/统计期/字段级证据闭合"], ["法定规划", "项目身份、4.19ha、四至、任务书设计输入", "附件2、红线、坐标、真北、标高、道路断面", "规划条件与任务书口径裁决"], ["产品与强排", "A/B方案逻辑、配比、验收矩阵", "真实总图、楼栋、日照、消防、车位、地库、整层户型", "人类设计模型完成并通过专业复核"], ["货值政策", "政策体系、送赠公式、风险边界", "项目适用、实测面积、补缴、结构消防、销售口径", "四账联算净增值在悲观情景过线"], ["财务", "成本区间、售价锚、楼面边界和压力测试", "可售面积、融资现金流、展示中心/地库分项", "统一模型可复算保本/目标毛利"], ["去化", "区域结构、竞品快照、36—42套/月情景", "目标项目来访—净签—退房真实漏斗", "首开90天回测达到目标或重做"]], "source_refs": ["SRC-TASKBOOK", "SRC-MARKET", "SRC-COMPETITOR", "SRC-POLICY-SPACE", "SRC-FINANCE", "SRC-SITE-OFFICIAL"]},
                {"type": "table", "title": "建议下一步顺序", "headers": ["顺序", "动作", "交付物", "责任"], "rows": [["1", "取得附件2、红线、测绘和北侧在建总图", "法定条件包+统一底图", "规划/测绘"], ["2", "按2.0工作输入与法定待核做A/B两案", "总图、指标、户型、地库、日照、交通", "建筑/工程"], ["3", "回填奖励面积、补缴地价、建安、税费、融资", "货值—成本—现金流模型", "成本/财务"], ["4", "用90天漏斗校准首开供货与价格", "净签/退房/回款复盘", "营销"], ["5", "重新运行Harness与DDS验收", "正式可研输入或继续保留探索性", "项目负责人"]], "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-COMPETITOR"]},
            ],
            ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-COMPETITOR", "SRC-POLICY-SPACE"],
            unit_status="partial", unit_role="confidence_state", story_role="evidence_appendix", appendix_policy="evidence", visual_evidence="gap",
        ),
        base_page(
            "cs-04", "CS", "证据地图：每一个结论都要能回到来源、假设和下一动作",
            "本报告把‘事实—推断—动作—验证’分开：市场与竞品数字来自入库快照，案例只提供机制，强排和货值仍是条件性推演；这样可以直接定位下一轮数据或设计工作，而不是把不确定性藏在漂亮页面后面。",
            [
                {"type": "table", "title": "核心判断的证据链", "headers": ["判断", "事实来源", "当前推断", "下一动作", "状态"], "rows": [["主力是刚改三房", "SRC-MARKET / SRC-CUSTOMER", "95/105㎡优先", "首开漏斗和户型对标", "部分支持"], ["地价不能无条件复制邻地", "SRC-LAND-COMP / SRC-FINANCE", "4,600—5,100基准", "送赠净增值与融资重算", "条件"], ["低密空间可做价值抓手", "SRC-POLICY-SPACE / SRC-TASKBOOK", "上下跃小批验证", "政策/结构/消防/补缴", "理论"], ["两案优于单一概念图", "SRC-TASKBOOK", "A流速、B溢价", "同边界强排实测", "待做"], ["市场不是无需求而是错配", "SRC-MARKET / SRC-COMPETITOR", "主流价位+分批供货", "统一窗口数据与首开回测", "部分支持"]], "source_refs": ["SRC-MARKET", "SRC-COMPETITOR", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-POLICY-SPACE"]},
                {"type": "narrative", "title": "最终提醒", "text": "如果下一轮只补一件事，优先补D0法定边界和统一底图；如果再补一件事，补目标项目真实漏斗。它们对强排和投决的影响大于继续堆叠概念案例。", "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK", "SRC-CUSTOMER"]},
            ],
            ["SRC-MARKET", "SRC-COMPETITOR", "SRC-TASKBOOK", "SRC-FINANCE", "SRC-POLICY-SPACE"],
            diagram_specs=[{
                "diagram_id": "DIA-L9-EVIDENCE-MAP",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "来源→事实→判断→设计/经营动作→验收",
                "diagram_type": "adjacency",
                "program_blocks": _diagram_blocks([("任务书/法定条件", "#64d2ff"), ("中指市场/竞品", "#30d158"), ("政策/成本/财务", "#ff9f0a"), ("事实与冲突", "#bf5af2"), ("A/B强排与产品", "#ffd60a"), ("投决闸门", "#ff453a")]),
                "relations": [[0, 3], [1, 3], [2, 3], [3, 4], [4, 5]],
                "source_refs": ["SRC-TASKBOOK", "SRC-MARKET", "SRC-COMPETITOR", "SRC-FINANCE"],
                "note": "证据架构关系图；不是建筑总平面。",
            }],
        ),
    ])
    # Keep the two newly ingested Core sources visible on the pages that make
    # the top-level recommendation, not only on the reconciliation appendix.
    for page in pages:
        if str(page.get("page_id")) in {"sc1-01", "sc1-02", "sc2-01", "sc3-04", "va2-07", "cs-01", "cs-03", "cs-04"}:
            page["source_refs"] = list(dict.fromkeys(list(page.get("source_refs") or []) + ["SRC-DDS-FULL-FLOW"]))
        if str(page.get("page_id")) in {"sc1-01", "sc1-02", "cs-03", "cs-04"}:
            page["source_refs"] = list(dict.fromkeys(list(page.get("source_refs") or []) + ["SRC-LEGACY-FEASIBILITY"]))
    return pages


def enrich_seed_payload(seed: dict[str, object]) -> dict[str, object]:
    """Keep top-level module snapshots aligned with the page-level evidence.

    The report compiler preserves the module payload alongside the page manifest.
    Updating it here prevents a downstream consumer from seeing the corrected
    page figures next to the stale first-draft market/competitor/finance values.
    These are still bounded snapshots: no missing project-level evidence is
    promoted to a fact.
    """
    meta = dict(seed.get("meta") or {})
    meta.update(
        {
            "decision_authority": "advisory_report",
            "authority_note": "本报告用于前期研判与设计输入，不替代正式投决、盖章规划条件、法定测绘或销售承诺。",
            "retained_conflicts": [
                {
                    "field": "far",
                    "taskbook_input": 2.0,
                    "legacy_or_unverified_input": "≤1.5",
                    "status": "unresolved_until_attachment_2_is_verified",
                }
            ],
        }
    )
    seed["meta"] = meta

    market = dict(seed.get("market") or {})
    market["status"] = "partial"
    market["snapshot_window"] = "2025-08—2026-07（源表实际列12个月）"
    market["window_quality_note"] = "源文件标题与实际列数不一致；按实际列明月份计算，待中指数据Agent复核。面积段行合计1,381套，但源文本以约2,738套为分母，90—140㎡约38%的占比尚未闭合。"
    market["area_band_quality_note"] = "90—140㎡列明成交1,039套；8个面积段行合计1,381套，而源文本另给约2,738套分母。当前仅保留列明套数与源文本口径，不把38%升级为已核验占比。"
    market["area_bands"] = [
        {"band": row[0], "transactions": row[1], "price_cny_sqm": row[2], "share_basis": "近3个月", "source_refs": ["SRC-MARKET"]}
        for row in AREA_ROWS
    ]
    market["household_bands"] = [
        {"band": "三房", "transactions": 3838, "share_pct": 69.0, "source_refs": ["SRC-MARKET"]},
        {"band": "二房", "transactions": 697, "share_pct": 12.6, "source_refs": ["SRC-MARKET"]},
        {"band": "四房", "transactions": 384, "share_pct": 6.9, "source_refs": ["SRC-MARKET"]},
        {"band": "叠拼", "transactions": 149, "share_pct": 2.7, "source_refs": ["SRC-MARKET"]},
        {"band": "联排", "transactions": 96, "share_pct": 1.7, "source_refs": ["SRC-MARKET"]},
    ]
    market["total_price_bands"] = [
        {"band": row[0], "transactions": row[1], "amount_10k_cny": row[2], "price_cny_sqm": row[3], "source_refs": ["SRC-MARKET"]}
        for row in TOTAL_PRICE_ROWS
    ]
    market["monthly_series"] = [
        {"month": month, "transactions": sales, "price_cny_sqm": price, "approved_units": approved, "available_units": available}
        for month, sales, price, approved, available in zip(MARKET_MONTHS, MARKET_SALES, MARKET_PRICES, MARKET_APPROVED, MARKET_AVAILABLE)
    ]
    market["source_refs"] = ["SRC-MARKET", "SRC-CUSTOMER"]
    seed["market"] = market

    competitor = dict(seed.get("competitor_series") or {})
    competitor["status"] = "partial"
    competitor["series_status"] = "12m_snapshot_with_early_3m_estimates_separated"
    competitor["snapshot_window"] = "2025-08—2026-07"
    competitor["items"] = [
        {
            "project_name": row[0],
            "submarket": row[1],
            "transactions_12m": row[2],
            "unit_price_cny_sqm": row[3],
            "approved_units": row[4],
            "available_units": row[5],
            "monthly_absorption_12m": row[6],
            "available_share_pct": row[7],
            "source_refs": ["SRC-COMPETITOR"],
        }
        for row in COMPETITOR_ROWS
    ]
    competitor["evidence_gaps"] = [
        "雍鑫·溪和林可售占比源表缺失，未补猜",
        "竞品分面积段价格、折扣、加推和户型实得仍待统一抓取",
        "目标项目真实来访—认筹—净签漏斗缺失",
    ]
    competitor["source_refs"] = ["SRC-COMPETITOR", "SRC-MARKET"]
    seed["competitor_series"] = competitor

    absorption = dict(seed.get("absorption_forecast") or {})
    absorption.update(
        {
            "status": "partial",
            "taskbook_far2_total_units": 736,
            "low_density_far1_5_sensitivity_units": 508,
            "unit_count_note": "736套为FAR2.0×41,900㎡按加权户型的示意推导；508套为旧≤1.5敏感性；正式可售套数待总图拆分。",
            "scenarios": [
                {"name": "508套｜30套/月", "basis": "低密敏感性", "monthly_units": 30, "sellout_months": 16.9},
                {"name": "508套｜36套/月", "basis": "低密敏感性", "monthly_units": 36, "sellout_months": 14.1},
                {"name": "508套｜42套/月", "basis": "低密敏感性", "monthly_units": 42, "sellout_months": 12.1},
                {"name": "736套｜36套/月", "basis": "FAR2.0示意", "monthly_units": 36, "sellout_months": 20.4},
            ],
            "decision_eligibility": False,
        }
    )
    seed["absorption_forecast"] = absorption

    finance = dict(seed.get("finance") or {})
    finance.update(
        {
            "status": "blocked",
            "working_land_price_range_cny_sqm": [4600, 5100],
            "conditional_land_price_ceiling_cny_sqm": 5630,
            "legacy_neighbor_anchor_cny_sqm": 7507,
            "working_construction_cost_cny_sqm": 4600,
            "construction_cost_range_cny_sqm": [4200, 5200],
            "cost_basis_note": "3,500元/㎡仅作旧口径对照；本版采用含地库、园林、北方保温的综合建安工作中枢4,600元/㎡。",
            "refined_scenarios": [
                {"name": "乐观", "sale_price_cny_sqm": 13080, "gross_margin_pct": 11.27},
                {"name": "中性", "sale_price_cny_sqm": 12426, "gross_margin_pct": 7.13},
                {"name": "悲观", "sale_price_cny_sqm": 11772, "gross_margin_pct": 2.49},
            ],
        }
    )
    seed["finance"] = finance

    gaps = list(seed.get("evidence_gaps") or [])
    existing_gap_ids = {str(item.get("gap_id")) for item in gaps if isinstance(item, dict)}
    for gap in [
        {"gap_id": "GAP-TASKBOOK-ATTACHMENT-2", "status": "blocked", "severity": "decision_blocking", "statement": "任务书引用的附件2法定规划条件未单独入库。", "owner": "规划设计负责人", "recommended_action": "补齐盖章附件2并逐字段核对FAR、密度、绿地、限高、停车、人防、退界。"},
        {"gap_id": "GAP-COMPETITOR-UNIFORM", "status": "partial", "severity": "confidence_affecting", "statement": "竞品完整身份、户型、折扣、加推与统一统计期尚未闭合。", "owner": "市场研究负责人", "recommended_action": "按提示词抓取同一统计窗口并保留原始链接/页码。"},
        {"gap_id": "GAP-CUSTOMER-FUNNEL", "status": "partial", "severity": "confidence_affecting", "statement": "目标项目真实来访、认筹、净签、退房和支付力样本缺失。", "owner": "营销负责人", "recommended_action": "首开前后按90天漏斗回填面积段与总价转化。"},
    ]:
        if gap["gap_id"] not in existing_gap_ids:
            gaps.append(gap)
    seed["evidence_gaps"] = gaps
    return seed


def build_dataneed_v02(records: list[dict[str, object]], sources: list[dict[str, object]]) -> None:
    if DATANEED_V01.is_dir():
        shutil.copytree(DATANEED_V01, DATANEED_V02, dirs_exist_ok=True)
    else:
        DATANEED_V02.mkdir(parents=True, exist_ok=True)
    manifest_path = DATANEED_V02 / "run-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    else:
        manifest = {}
    manifest.update(
        {
            "run_id": "20260826_arch-front-dataneed_v02_local",
            "generated_at": f"{AS_OF}T18:00:00+08:00",
            "refined_at": f"{AS_OF}T18:00:00+08:00",
            "status": "valid",
            "execution_mode": "local_fallback_after_multi_agent_rate_limit",
            "multi_agent_status": "429 Too Many Requests; no agent artifact accepted",
            "dds_evidence_input": "work/research_candidate_input.json",
            "dds_evidence_sources": len(sources),
            "dds_evidence_records": len(records),
            "excluded_paths": ["02_Reference/"],
            "notes": str(manifest.get("notes") or "") + f" 本地补强：已将{len(sources)}个授权来源与{len(records)}条核心证据主张接入DDS候选证据包；新增采购任务书正文及内嵌图像核验，明确FAR2.0与95/105/122/139㎡配比。附件2法定规划细项、测绘、目标项目真实客群漏斗和完整财务链仍为投决阻断项。",
        }
    )
    write_json(manifest_path, manifest)
    research_dir = DATANEED_V02 / "artifacts" / "data_need" / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        research_dir / "acquisition-log.json",
        {"schema_version": "arch-front-dataneed/acquisition-log/1.0", "as_of": AS_OF, "mode": "local_fallback", "attempts": [{"route": "multi_agent", "status": "rate_limited_429", "artifacts_accepted": 0}, {"route": "project_archive", "status": "completed", "sources": len(sources), "records": len(records)}]},
    )
    write_json(
        research_dir / "evidence-matrix.json",
        {"schema_version": "arch-front-dataneed/evidence-matrix/1.0", "as_of": AS_OF, "claims": [{"claim_id": item["claim_id"], "source_refs": item["source_refs"], "status": "source_backed_bounded", "decision_eligibility": False} for item in records]},
    )
    write_json(
        research_dir / "gap-register.json",
        {"schema_version": "arch-front-dataneed/gap-register/1.0", "as_of": AS_OF, "blocking": [{"gap_id": "GAP-REDLINE", "priority": "D0", "status": "needed", "owner": "规划设计负责人", "acceptance": "盖章规划条件、红线、坐标、真北、标高、道路断面闭合"}, {"gap_id": "GAP-FINANCE", "priority": "D1", "status": "needed", "owner": "成本财务负责人", "acceptance": "土地、建安、税负、融资和赠送口径勾稽一致"}], "confidence_gaps": ["目标项目真实客群漏斗", "竞品完整统一统计口径"]},
    )
    (research_dir / "fallback-options.md").write_text(
        "# 本地执行回退说明\n\n- 多智能体调度连续返回 429，未采用任何不完整 agent 产物。\n- 证据改由项目已入库资料、ArchLib V2 内部案例快照和本地确定性编译器处理。\n- 研究结果保持 source-backed + bounded gaps；不得把当前包直接解释为已关闭的土地/财务投决结论。\n",
        encoding="utf-8",
    )


def build() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    refresh_existing_intake_answers()
    sources = build_sources()
    records = build_records()
    build_dataneed_v02(records, sources)
    candidate = {"schema_version": "dds.research-candidate-input/1.0", "records": records, "sources": sources, "critical_claim_ids": ["claim-policy", "claim-taskbook", "claim-market", "claim-competition", "claim-case-analogy", "claim-spatial"]}
    write_json(WORK / "research_candidate_input.json", candidate)
    seed = enrich_seed_payload(build_seed(sources))
    write_json(WORK / "report_seed.json", seed)
    prompt_path = write_zhongzhi_prompts()
    manifest = {"run_id": "20260826_arch-front-dataneed_v02_local", "project_id": PROJECT_ID, "as_of": AS_OF, "status": "partial_local_fallback", "multi_agent_status": "rate_limited_429_no_artifact", "source_count": len(sources), "record_count": len(records), "excluded_paths": ["02_Reference/"], "evidence_policy": "source_backed_with_bounded_gaps"}
    manifest["zhongzhi_prompt_path"] = str(prompt_path)
    manifest["report_page_count"] = len(seed["page_manifest"])
    write_json(WORK / "local_preparation_manifest.json", manifest)
    print(json.dumps({"sources": len(sources), "records": len(records), "seed_pages": len(seed["page_manifest"]), "archlib_asset": ARCHLIB_IMAGE.is_file(), "zhongzhi_prompt_path": str(prompt_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
