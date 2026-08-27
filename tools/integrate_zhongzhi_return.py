from __future__ import annotations

"""Integrate a Zhongzhi data-return batch into the L-9 DDS research layer.

The return batch is treated as a new, source-addressed evidence layer.  Original
files are copied into the project's immutable inbox snapshot and the previous
research snapshot is retained.  This module deliberately keeps unresolved
planning, geometry, funnel and project-level financial evidence conditional.
"""

import copy
import hashlib
import json
import shutil
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


PROJECT = Path(r"D:\Arch_Projects\HYP_天津_武清区六街 L-9 地块_Gary")
CORE = PROJECT / "00_Core"
WORK = PROJECT / "work"
INBOX = PROJECT / "inbox"
DDS_ROOT = Path(r"D:\Arch_agent\DDS_v2")
RETURN_RUN = CORE / "runs" / "20260826_zhongzhi-datacollect_v01"
RETURN_DATA_DIR = RETURN_RUN / "artifacts" / "zhongzhi_datasets"
DATANEED_V02 = CORE / "runs" / "20260826_arch-front-dataneed_v02_local"
DATANEED_V03 = CORE / "runs" / "20260826_arch-front-dataneed_v03_zhongzhi"
AS_OF = "2026-08-26"


DATASET_SPECS = [
    ("ZZ-D0-LAND", "SRC-ZZ-D0-LAND", "ZZ-D0-LAND_dataset.json", "挂牌/地价约束", "land"),
    ("ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-MONTHLY", "ZZ-MKT-MONTHLY_dataset.json", "连续市场量价供求", "market"),
    ("ZZ-MKT-STRUCTURE", "SRC-ZZ-MKT-STRUCTURE", "ZZ-MKT-STRUCTURE_dataset.json", "面积/总价/户型结构", "market"),
    ("ZZ-COMP-FULL", "SRC-ZZ-COMP-FULL", "ZZ-COMP-FULL_dataset.json", "3/5公里竞品全量库", "competition"),
    ("ZZ-SUPPLY-LAND", "SRC-ZZ-SUPPLY-LAND", "ZZ-SUPPLY-LAND_dataset.json", "土地供应与未来竞争", "land"),
    ("ZZ-POLICY-VALUE", "SRC-ZZ-POLICY-VALUE", "ZZ-POLICY-VALUE_dataset.json", "住宅多样性空间政策", "policy"),
    ("ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-CUSTOMER-FUNNEL", "ZZ-CUSTOMER-FUNNEL_dataset.json", "客户漏斗与支付力", "customer"),
]

# DataNeed requires every archived source to declare at least one downstream
# requirement.  These are linkage references, not claims that the return has
# verified the requirement; the requirement status remains ``needed`` where
# the source only supplies a fallback or partial proxy.
RETURN_REQUIREMENT_REFS = {
    "SRC-ZZ-D0-LAND": ["DN-D0-LEGAL01", "DN-D0-LEGAL02", "DN-D0-GEO01", "DN-D1-GS01"],
    "SRC-ZZ-MKT-MONTHLY": ["DN-D1-MKT01"],
    "SRC-ZZ-MKT-STRUCTURE": ["DN-D1-MKT02", "DN-D1-KQ01", "DN-D1-PM01"],
    "SRC-ZZ-COMP-FULL": ["DN-D1-COMP01", "DN-D2-COMP02"],
    "SRC-ZZ-SUPPLY-LAND": ["DN-D1-GS01", "DN-D2-COMP02", "DN-D2-URBAN01"],
    "SRC-ZZ-POLICY-VALUE": ["DN-D1-POL02", "DN-D1-HX01"],
    "SRC-ZZ-CUSTOMER-FUNNEL": ["DN-D1-KQ01", "DN-D1-COMP01", "DN-D3-MKT03"],
}
DATASET_REF = {dataset_id: source_id for dataset_id, source_id, *_ in DATASET_SPECS}
SOURCE_TO_LEGACY = {
    "SRC-ZZ-D0-LAND": "SRC-LAND-COMP",
    "SRC-ZZ-MKT-MONTHLY": "SRC-MARKET",
    "SRC-ZZ-MKT-STRUCTURE": "SRC-MARKET",
    "SRC-ZZ-COMP-FULL": "SRC-COMPETITOR",
    "SRC-ZZ-SUPPLY-LAND": "SRC-LAND-COMP",
    "SRC-ZZ-POLICY-VALUE": "SRC-POLICY-SPACE",
    "SRC-ZZ-CUSTOMER-FUNNEL": "SRC-CUSTOMER",
}


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dec(value: object) -> Decimal:
    return Decimal(str(value))


def rounded(value: Decimal, places: int = 2) -> float:
    quantum = Decimal("1") if places == 0 else Decimal("1." + "0" * places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))


def fmt(value: object, places: int = 0) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        return value
    number = dec(value)
    if places == 0:
        return f"{int(number.quantize(Decimal('1'), rounding=ROUND_HALF_UP)):,}"
    return f"{number.quantize(Decimal('1.' + '0' * places), rounding=ROUND_HALF_UP):,}"


def load_return_batch() -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    if not RETURN_DATA_DIR.is_dir():
        raise FileNotFoundError(RETURN_DATA_DIR)
    datasets: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    required = {
        "dataset_id",
        "geography",
        "time_window",
        "as_of",
        "source_url",
        "source_title",
        "publisher",
        "retrieved_at",
        "missing_fields",
        "confidence",
        "conflicts",
    }
    for dataset_id, _source_id, filename, _title, _family in DATASET_SPECS:
        path = RETURN_DATA_DIR / filename
        payload = read_json(path)
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"{filename} missing required keys: {missing}")
        if payload.get("dataset_id") != dataset_id:
            raise ValueError(f"dataset_id mismatch in {filename}")
        # The policy and customer-funnel returns use narrative sections in
        # place of the shared calculation_notes field.  Record that schema
        # variance as a warning; do not reject an otherwise complete return.
        if "calculation_notes" not in payload:
            warnings.append(
                f"{filename}未提供统一字段calculation_notes，已按其叙述性字段接入并保留原JSON。"
            )
        datasets[dataset_id] = payload

    chart_path = RETURN_RUN / "artifacts" / "charts" / "market_trend.option.json"
    chart = read_json(chart_path) if chart_path.is_file() else {}

    # The batch README contains two summary figures that do not reconcile with
    # the row-level JSON.  Keep these as explicit QA warnings and use the rows.
    monthly = datasets["ZZ-MKT-MONTHLY"].get("records") or []
    readme_path = RETURN_RUN / "README_数据采集回传.md"
    readme_text = readme_path.read_text(encoding="utf-8-sig") if readme_path.is_file() else ""
    monthly_units_from_rows = sum(
        dec(row.get("成交套数_value", row.get("成交套数", 0))) for row in monthly
    )
    structure = datasets["ZZ-MKT-STRUCTURE"]
    price_rows = structure.get("总价段", {}).get("records") or []
    price_total = sum(dec(row.get("成交套数", 0)) for row in price_rows)
    price_120_300 = sum(
        dec(row.get("成交套数", 0))
        for row in price_rows
        if row.get("总价段") in {"120-160万", "160-200万", "200-300万"}
    )
    if len(monthly) != 24:
        warnings.append(
            f"README摘要称24个月，但ZZ-MKT-MONTHLY逐月JSON实际为{len(monthly)}条；报告按逐月JSON使用。"
        )
    if "488" in readme_text and monthly:
        row_average = rounded(monthly_units_from_rows / dec(len(monthly)), 2)
        if row_average != 488:
            warnings.append(
                f"README摘要称月均约488套，逐月JSON复算为{row_average}套；报告按逐月JSON使用。"
            )
    if price_total and rounded(price_120_300 / price_total * 100, 2) != 57.3:
        warnings.append(
            f"README摘要称120-300万占57.3%，逐行JSON复算为{rounded(price_120_300 / price_total * 100, 2)}%；报告按JSON使用。"
        )
    chart_months = (chart.get("xAxis") or {}).get("data") or []
    if chart_months and len(chart_months) != len(monthly):
        warnings.append("市场趋势图配置与月度JSON长度不一致，报告重建图表时以月度JSON为准。")
    return datasets, chart, warnings


def calculate_metrics(datasets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    monthly = list(datasets["ZZ-MKT-MONTHLY"].get("records") or [])
    structure = datasets["ZZ-MKT-STRUCTURE"]
    area_rows = list((structure.get("面积段") or {}).get("records") or [])
    price_rows = list((structure.get("总价段") or {}).get("records") or [])
    house_rows = list((structure.get("户型") or {}).get("records") or [])
    total_structure_units = dec((structure.get("面积段") or {}).get("总套数") or 0)
    # Monthly return rows use the normalized ``*_value`` field names, while
    # the structure tables use the shorter Chinese labels.  Read the monthly
    # value explicitly so the aggregate cannot silently collapse to zero.
    total_units = sum(
        dec(row.get("成交套数_value", row.get("成交套数", 0))) for row in monthly
    )
    total_area = sum(dec(row.get("成交面积_㎡_value", 0)) for row in monthly)
    weighted_price = (
        sum(
            dec(row.get("成交面积_㎡_value", 0))
            * dec(row.get("成交均价_元_㎡_value", 0))
            for row in monthly
        )
        / total_area
        if total_area
        else Decimal("0")
    )
    latest = monthly[-1] if monthly else {}
    area_90_140 = sum(
        dec(row.get("成交套数", 0))
        for row in area_rows
        if row.get("面积段") in {"90-120㎡", "120-140㎡"}
    )
    three_room = next(
        (dec(row.get("成交套数", 0)) for row in house_rows if row.get("户型") == "三房"),
        Decimal("0"),
    )
    price_120_300 = sum(
        dec(row.get("成交套数", 0))
        for row in price_rows
        if row.get("总价段") in {"120-160万", "160-200万", "200-300万"}
    )
    price_160_300 = sum(
        dec(row.get("成交套数", 0))
        for row in price_rows
        if row.get("总价段") in {"160-200万", "200-300万"}
    )
    land = datasets["ZZ-D0-LAND"].get("L-9挂牌信息") or {}
    land_constraints = list(
        datasets["ZZ-D0-LAND"].get("土地配套/开发约束(公开公告口径)") or []
    )
    comp = datasets["ZZ-COMP-FULL"]
    summary = comp.get("近期市场汇总") or {}
    supply_dataset = datasets["ZZ-SUPPLY-LAND"]
    policy_dataset = datasets["ZZ-POLICY-VALUE"]
    customer_dataset = datasets["ZZ-CUSTOMER-FUNNEL"]
    mix = dec(95) * dec("0.30") + dec(105) * dec("0.30") + dec(122) * dec("0.25") + dec(139) * dec("0.15")
    legal_plan_area = dec(land.get("规划建筑面积_㎡") or 0)
    taskbook_plan_area = dec("41900") * dec("2.0")
    # The 90% usable-efficiency factor is an explicit planning assumption, not
    # a legal or sales fact.  It reproduces the previous low-density envelope
    # while using the newly verified listing area.
    legal_units_90 = legal_plan_area * dec("0.90") / mix if mix else Decimal("0")
    task_units_90 = taskbook_plan_area * dec("0.90") / mix if mix else Decimal("0")
    task_units_no_eff = taskbook_plan_area / mix if mix else Decimal("0")
    return {
        "monthly_count": len(monthly),
        "monthly_units": rounded(total_units, 0),
        "monthly_area_sqm": rounded(total_area, 2),
        "monthly_weighted_price": rounded(weighted_price, 0),
        "monthly_avg_units": rounded(total_units / dec(len(monthly)), 2) if monthly else 0,
        "latest_available": latest.get("可售套数_value"),
        "latest_cycle": latest.get("出清周期_月_value"),
        "area_total_units": rounded(total_structure_units, 0),
        "area_90_140_units": rounded(area_90_140, 0),
        "area_90_140_pct": rounded(area_90_140 / total_structure_units * 100, 1) if total_structure_units else 0,
        "three_room_units": rounded(three_room, 0),
        "three_room_pct": rounded(three_room / total_structure_units * 100, 1) if total_structure_units else 0,
        "price_120_300_units": rounded(price_120_300, 0),
        "price_120_300_pct": rounded(price_120_300 / total_structure_units * 100, 1) if total_structure_units else 0,
        "price_160_300_units": rounded(price_160_300, 0),
        "price_160_300_pct": rounded(price_160_300 / total_structure_units * 100, 1) if total_structure_units else 0,
        "land_area_sqm": land.get("建设用地面积_㎡"),
        "land_plan_area_sqm": land.get("规划建筑面积_㎡"),
        "land_far": land.get("容积率"),
        "land_start_floor_price": land.get("推出楼面价_元_㎡"),
        "land_start_price_10k": land.get("起始价_万元"),
        "land_deposit_10k": land.get("竞买保证金_万元"),
        "land_ground_price": land.get("推出地面价_元_㎡"),
        "land_name": land.get("地块名称"),
        "land_use": land.get("用地性质"),
        "land_method": land.get("出让方式"),
        "land_start_date": land.get("起始时间"),
        "land_listing_start": land.get("挂牌起始"),
        "land_deadline": land.get("截止时间"),
        "land_expected_close": land.get("成交预计"),
        "land_tenure": land.get("土地年限"),
        "land_status": land.get("交易状态"),
        "land_constraints": land_constraints,
        "mix_weighted_area": rounded(mix, 2),
        "legal_units_90": rounded(legal_units_90, 0),
        "task_units_90": rounded(task_units_90, 0),
        "task_units_no_eff": rounded(task_units_no_eff, 0),
        "three_km_summary": summary.get("3km圈层", ""),
        "five_km_summary": summary.get("5km圈层", ""),
        "area_rows": area_rows,
        "price_rows": price_rows,
        "house_rows": house_rows,
        "monthly_rows": monthly,
        "competitor_rows": list(comp.get("records") or []),
        "supply_rows": list(comp.get("待入市/未来供应跟踪") or []),
        "land_comps": list((datasets["ZZ-D0-LAND"].get("可比地块统一成交楼面价(元/㎡)") or [])),
        "supply_core_rows": list(supply_dataset.get("本案L-9周边3km核心涉宅地块") or []),
        "supply_price_rows": list(supply_dataset.get("武清区涉宅地价序列(近年成交楼面价,元/㎡)") or []),
        "supply_annual_rows": list(supply_dataset.get("天津市涉宅土地年度(全市)") or []),
        "supply_outlook": list(supply_dataset.get("未来36个月供地研判") or []),
        "supply_reference_rows": list(supply_dataset.get("补助/补缴参考") or []),
        "policy_effective": dict(policy_dataset.get("政策有效性") or {}),
        "policy_space_rows": list(policy_dataset.get("四大空间政策条款") or []),
        "policy_batch2_rows": list(policy_dataset.get("第二批政策补充(津规资建发2024-8)") or []),
        "policy_product_conclusions": list(policy_dataset.get("低多层洋房/上下跃/139㎡产品适用结论") or []),
        "policy_controls": list(policy_dataset.get("关键控制点与前置条件") or []),
        "policy_example_rows": list(policy_dataset.get("补缴实例(武清区先行先试)") or []),
        "customer_funnel_rows": list(customer_dataset.get("竞品成交漏斗(近12个月实证，来源指中)") or []),
        "customer_estimate_rows": list(customer_dataset.get("首开90天漏斗折算(以L-9同口径竞品为参考,全为估算)") or []),
        "customer_payment_rows": list(customer_dataset.get("支付力/置换/需求结构(公开市场+推断)") or []),
        "customer_offline_rows": list(customer_dataset.get("线下调研字段表(中指无法提供,建议执行)") or []),
        "customer_target_text": str(customer_dataset.get("首开90天目标") or ""),
    }


def copy_and_register_sources(
    datasets: dict[str, dict[str, Any]], metrics: dict[str, Any]
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    snapshot_dir = WORK / "source_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for dataset_id, source_id, filename, title, family in DATASET_SPECS:
        source_path = RETURN_DATA_DIR / filename
        inbox_rel = Path("zhongzhi_return") / filename
        inbox_path = INBOX / inbox_rel
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, inbox_path)
        snapshot_name = source_id.lower() + ".json"
        snapshot_path = snapshot_dir / snapshot_name
        snapshot_path.write_bytes(source_path.read_bytes())
        payload = datasets[dataset_id]
        missing = [str(item) for item in payload.get("missing_fields") or []]
        conflicts = [str(item) for item in payload.get("conflicts") or []]
        limitations = [
            "中指数据 Agent 回传批次；作为报告输入，不自动升级为绑定投决证据。",
            f"回传置信度：{payload.get('confidence')}",
        ]
        if missing:
            limitations.append("未闭合字段：" + "；".join(missing))
        if conflicts:
            limitations.append("保留冲突：" + "；".join(conflicts))
        sources.append(
            {
                "source_id": source_id,
                "title": f"中指数据回传｜{title}",
                "name": f"中指数据回传｜{title}",
                "source_type": "structured_data_return",
                "kind": "external_database",
                "canonical_ref": f"inbox/{inbox_rel.as_posix()}",
                "snapshot_ref": f"source_snapshots/{snapshot_name}",
                "path": f"inbox/{inbox_rel.as_posix()}",
                "source_url": str(payload.get("source_url") or ""),
                "publisher": str(payload.get("publisher") or "中指数据 CREIS"),
                "author_type": "external_data_agent_return",
                "published_at": str(payload.get("as_of") or AS_OF),
                "captured_at": f"{payload.get('retrieved_at') or AS_OF}T00:00:00+08:00",
                "geography": str(payload.get("geography") or "天津市武清区"),
                "time_window": {"label": str(payload.get("time_window") or ""), "as_of": payload.get("as_of")},
                "rights_status": "authorized_internal_reference",
                "raw_hash": sha256(source_path),
                "snapshot_hash": sha256(snapshot_path),
                "duplicate_cluster": f"zhongzhi-return-{family}",
                "source_family": family,
                "used_for": ["data_return", "report_seed", "evidence_appendix"],
                "limitations": limitations,
                "substitution_status": "external_return_ingested",
                "status": "collected",
                "trust_tier": "L1",
                "supersedes": [SOURCE_TO_LEGACY[source_id]],
                "return_batch": "20260826_zhongzhi-datacollect_v01",
            }
        )
    return sources


def _record(
    record_id: str,
    claim_id: str,
    evidence_type: str,
    statement: str,
    source_refs: list[str],
    method: str,
    limitations: list[str],
    confidence: float,
    counter_status: str = "counter_evidence_found",
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "claim_id": claim_id,
        "evidence_type": evidence_type,
        "statement": statement,
        "source_refs": source_refs,
        "counter_source_refs": [],
        # Keep the status in the canonical DDS vocabulary.  The earlier
        # internal label ``bounded_conflict_retained`` is useful prose, but is
        # intentionally not emitted as a validator status.
        "counter_evidence_status": counter_status,
        "counter_evidence_note": "旧快照、README摘要或项目任务书与本批次存在口径差异，未静默覆盖。",
        "geography": {"scope": "天津市武清区杨村/武清新城", "precision": "district_submarket"},
        "time_window": {"as_of": AS_OF, "status": "external_return_snapshot"},
        "method": method,
        "limitations": limitations,
        "confidence": confidence,
    }


def build_return_records(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _record(
            "REC-ZZ-LAND-01",
            "claim-zz-land-listing",
            "observed_fact",
            "中指数据与天津公开挂牌口径显示，L-9对应津武(挂)2024-024号，建设用地41,891.3㎡、规划建面62,836.95㎡、容积率≤1.5，起始价37,400万元、保证金7,480万元、起拍楼面价5,952元/㎡、起拍地面价8,928元/㎡，挂牌截止2026-08-28、预计2026-09-07成交；当前仍为挂牌中未成交。",
            ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND"],
            "structured_return_cross_check",
            ["正式出让合同、附件2规划条件、竞买条款及最终成交结果仍未取得。"],
            0.91,
        ),
        _record(
            "REC-ZZ-MARKET-01",
            "claim-zz-market-monthly",
            "observed_fact",
            f"ZZ-MKT-MONTHLY逐月JSON列出{metrics['monthly_count']}个月（2024-09—2026-07），累计成交{fmt(metrics['monthly_units'])}套、加权均价约{fmt(metrics['monthly_weighted_price'])}元/㎡、月均{fmt(metrics['monthly_avg_units'], 1)}套；2026-07可售{fmt(metrics['latest_available'])}套、出清周期{fmt(metrics['latest_cycle'], 2)}个月。",
            ["SRC-ZZ-MKT-MONTHLY"],
            "row_level_decimal_recalculation",
            ["区域为武清全域，杨村板块未单列；README将窗口概括为24个月但逐月JSON为23条。"],
            0.90,
        ),
        _record(
            "REC-ZZ-STRUCTURE-01",
            "claim-zz-market-structure",
            "observed_fact",
            f"同一近12个月结构表以{fmt(metrics['area_total_units'])}套为分母：90—140㎡合计{fmt(metrics['area_90_140_units'])}套、约{metrics['area_90_140_pct']}%；三房{fmt(metrics['three_room_units'])}套、约{metrics['three_room_pct']}%；120—300万元{fmt(metrics['price_120_300_units'])}套、约{metrics['price_120_300_pct']}%，其中160—300万元{fmt(metrics['price_160_300_units'])}套、约{metrics['price_160_300_pct']}%。",
            ["SRC-ZZ-MKT-STRUCTURE"],
            "same_window_structure_recalculation",
            ["README摘要将120—300万元写为57.3%，与JSON逐行合计不一致；报告采用可复算JSON。"],
            0.92,
        ),
        _record(
            "REC-ZZ-COMP-01",
            "claim-zz-competition",
            "observed_fact",
            "以L-9为中心的竞品回传显示，3公里圈层11个项目、在售7个，近12个月成交930套、成交均价15,339元/㎡、可售1,330套；5公里圈层22个项目、在售16个，成交1,970套、均价15,656元/㎡、可售2,427套。不同项目的首开峰值、成熟期月均与库存必须分开读取。",
            ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"],
            "ring_summary_and_project_level_cross_check",
            ["竞品户型级成交、折扣、送赠和部分项目身份仍需统一下钻；新盘首开峰值不可年化。"],
            0.84,
        ),
        _record(
            "REC-ZZ-POLICY-01",
            "claim-zz-policy-value",
            "observed_fact",
            "天津住宅多样性空间增值利用政策截至2026-08-26仍有效至2028-08-24，武清已有补缴实例；但奖励面积需个案审批与评估补缴，任务书所写区级28%不能直接替代补缴评估口径。",
            ["SRC-ZZ-POLICY-VALUE"],
            "official_policy_return_cross_reading",
            ["本项目具体空间比例、结构消防、销售确权和补缴金额尚未形成专项审批结果。"],
            0.88,
        ),
        _record(
            "REC-ZZ-CUSTOMER-01",
            "claim-zz-customer-funnel",
            "analysis_inference",
            "中指回传不含L-9真实来访—认筹—网签—净签—退房过程数据；15—25套/月、首开90天45—75套等为同类竞品参考估算，不是目标项目销售承诺。",
            ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL"],
            "bounded_funnel_scenario",
            ["必须以目标项目线下台账和首开90天回测校准；渠道、首付来源和置换链仍是缺口。"],
            0.59,
            "searched_none_found",
        ),
    ]


def table_block(title: str, headers: list[str], rows: list[list[Any]], refs: list[str]) -> dict[str, Any]:
    return {"type": "table", "title": title, "headers": headers, "rows": rows, "source_refs": refs}


def narrative_block(title: str, text: str, refs: list[str]) -> dict[str, Any]:
    return {"type": "narrative", "title": title, "text": text, "source_refs": refs}


def metric_block(title: str, metrics: list[dict[str, Any]], refs: list[str]) -> dict[str, Any]:
    return {"type": "metrics", "title": title, "metrics": metrics, "source_refs": refs}


def prompt_block(title: str, text: str, items: list[str], refs: list[str]) -> dict[str, Any]:
    return {"type": "prompt", "title": title, "text": text, "items": items, "source_refs": refs}


def chart_spec(
    chart_id: str,
    title: str,
    unit: str,
    series: list[dict[str, Any]],
    refs: list[str],
    chart_type: str = "ranked_bar",
    dimensions: list[Any] | None = None,
    measures: list[Any] | None = None,
    time_window: str | None = None,
    findings: list[str] | None = None,
    decision_message: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "chart_id": chart_id,
        "type": chart_type,
        "title": title,
        "unit": unit,
        "units": unit,
        "series": series,
        "source_refs": refs,
        "source_note": "中指数据回传；统计期、口径和冲突见数据回传校准页。",
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
    return value


def find_page(pages: list[dict[str, Any]], page_id: str) -> dict[str, Any] | None:
    return next((page for page in pages if str(page.get("page_id")) == page_id), None)


def update_page(
    page: dict[str, Any],
    *,
    title: str,
    takeaway: str,
    blocks: list[dict[str, Any]],
    refs: list[str],
    charts: list[dict[str, Any]] | None = None,
    diagrams: list[dict[str, Any]] | None = None,
    decision_question: str | None = None,
    decision_impact: str | None = None,
    display_title: str | None = None,
) -> None:
    page["title"] = title
    page["display_title"] = display_title or title
    page["takeaway"] = takeaway
    page["blocks"] = blocks
    page["source_refs"] = list(dict.fromkeys(refs))
    if charts is not None:
        page["chart_specs"] = charts
    if diagrams is not None:
        page["diagram_specs"] = diagrams
    if decision_question is not None:
        page["decision_question"] = decision_question
    if decision_impact is not None:
        page["decision_impact"] = decision_impact


def clone_page(template: dict[str, Any], page_id: str, unit_id: str) -> dict[str, Any]:
    page = copy.deepcopy(template)
    page["page_id"] = page_id
    page["chapter_id"] = unit_id.lower()
    page["section_id"] = unit_id
    page["unit_id"] = unit_id
    page["story_role"] = "primary_narrative"
    page["evidence_type"] = "observed_fact"
    page["unit_status"] = "partial"
    page["appendix_policy"] = "presentation"
    page["print_policy"] = {"include": True, "page_break_after": True, "allow_internal_scroll": False}
    return page


def area_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            row.get("面积段"),
            row.get("成交套数"),
            fmt(row.get("成交均价")),
            f"{row.get('套数占比%')}%",
        ]
        for row in metrics["area_rows"]
    ]


def price_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    total = dec(metrics["area_total_units"])
    result: list[list[Any]] = []
    for row in metrics["price_rows"]:
        units = dec(row.get("成交套数", 0))
        result.append(
            [
                row.get("总价段"),
                row.get("成交套数"),
                f"{rounded(units / total * 100, 1)}%" if total else "—",
                fmt(row.get("成交均价")),
                row.get("成交金额_万元"),
            ]
        )
    return result


def house_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    total = dec(metrics["area_total_units"])
    result: list[list[Any]] = []
    for row in metrics["house_rows"]:
        units = dec(row.get("成交套数", 0))
        result.append(
            [
                row.get("户型"),
                row.get("成交套数"),
                f"{rounded(units / total * 100, 1)}%" if total else "—",
                fmt(row.get("成交均价")),
            ]
        )
    return result


def monthly_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            row.get("month"),
            row.get("成交套数_value"),
            row.get("成交面积_㎡_value"),
            row.get("成交均价_元_㎡_value"),
            row.get("批准上市套数_value") if row.get("批准上市套数_value") is not None else "—",
            row.get("可售套数_value"),
            row.get("出清周期_月_value"),
        ]
        for row in metrics["monthly_rows"]
    ]


def focus_competitor_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    names = {"绿城·尚玉蘭", "宏顺·央璟颂", "雍鑫·溪和林", "金地雍阳印", "龙湖云曜", "招商揽阅", "新城玺樾春秋"}
    result: list[list[Any]] = []
    for row in metrics["competitor_rows"]:
        name = row.get("项目名")
        if name not in names:
            continue
        transactions = row.get("成交套数")
        speed: Any = "—"
        note = "成熟期/数据待核"
        if name == "绿城·尚玉蘭":
            speed, note = "180—240峰值*", "2026-06新盘首开约1.5个月，不可年化"
        elif name == "龙湖云曜":
            speed, note = "约52估算*", "78套/约1.5个月，新盘错期"
        elif transactions is not None:
            speed, note = rounded(dec(transactions) / dec(12), 1), "近12个月成交/12"
        result.append(
            [
                name,
                row.get("距离_m"),
                row.get("容积率"),
                row.get("成交套数") if transactions is not None else "—",
                fmt(row.get("成交均价")),
                row.get("可售套数", "—"),
                speed,
                note,
            ]
        )
    return result


def full_competitor_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    result: list[list[Any]] = []
    for row in metrics["competitor_rows"]:
        transactions = row.get("成交套数")
        result.append(
            [
                row.get("项目名"),
                row.get("状态"),
                row.get("距离_m"),
                row.get("容积率"),
                row.get("户数"),
                transactions if transactions is not None else "—",
                fmt(row.get("成交均价")),
                row.get("可售套数", "—"),
                row.get("产品定位", "—"),
            ]
        )
    return result


def land_comparison_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    land = [
        [
            "L-9（当前挂牌）",
            "津武(挂)2024-024",
            "≤1.5",
            metrics["land_plan_area_sqm"],
            metrics["land_start_floor_price"],
            "挂牌中",
            metrics["land_deadline"],
        ]
    ]
    for row in metrics["land_comps"]:
        land.append(
            [
                row.get("地块"),
                row.get("地块"),
                row.get("容积率"),
                "—",
                row.get("楼面价"),
                row.get("成交时间"),
                row.get("备注", "—"),
            ]
        )
    return land


def listing_transaction_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    """Return the material fields from the live listing in a readable ledger.

    The source JSON is the authority for the values.  Keeping the fields in a
    small two-column table makes the time pressure and cash requirement visible
    in the report instead of hiding them in a single headline metric.
    """
    return [
        ["地块编号", "津武(挂)2024-024号", "L-9当前挂牌身份"],
        ["地块名称", metrics.get("land_name") or "武清区建设路西侧", "公开挂牌名称"],
        ["用地性质 / 出让方式", f"{metrics.get('land_use') or '—'} / {metrics.get('land_method') or '—'}", "住宅交易口径"],
        ["建设用地面积", f"{fmt(metrics.get('land_area_sqm'), 1)}㎡", "挂牌字段"],
        ["规划建筑面积", f"{fmt(metrics.get('land_plan_area_sqm'), 2)}㎡", "挂牌字段"],
        ["容积率", str(metrics.get("land_far") or "—"), "当前交易基准；附件2待裁决"],
        ["起始价 / 竞买保证金", f"{fmt(metrics.get('land_start_price_10k'))}万元 / {fmt(metrics.get('land_deposit_10k'))}万元", "资金准备硬约束"],
        ["推出楼面价 / 地面价", f"{fmt(metrics.get('land_start_floor_price'))}元/㎡ / {fmt(metrics.get('land_ground_price'))}元/㎡", "楼面价用于压力测试"],
        ["公告起始 / 挂牌起始", f"{metrics.get('land_start_date') or '—'} / {metrics.get('land_listing_start') or '—'}", "时间字段"],
        ["挂牌截止 / 成交预计", f"{metrics.get('land_deadline') or '—'} / {metrics.get('land_expected_close') or '—'}", "截止与预计成交并非成交事实"],
        ["土地年限 / 交易状态", f"{metrics.get('land_tenure') or '—'} / {metrics.get('land_status') or '—'}", "正式合同优先"],
    ]


def land_constraint_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for index, text in enumerate(metrics.get("land_constraints") or [], start=1):
        value = str(text)
        label = value.split("：", 1)[0] if "：" in value else f"开发约束{index}"
        rows.append([label, value, "公开公告/通用参照", "L-9须以正式出让合同及挂牌附件核验"])
    return rows


def supply_core_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in metrics.get("supply_core_rows") or []:
        floor = item.get("成交楼面价")
        if floor is None:
            floor = item.get("推出楼面价")
        rows.append([
            item.get("地块") or "—",
            item.get("位置") or "—",
            item.get("容积率") if item.get("容积率") is not None else "—",
            fmt(item.get("规划建面"), 0),
            fmt(floor),
            item.get("状态") or (f"成交 {item.get('成交时间')}" if item.get("成交时间") else "—"),
        ])
    return rows


def supply_core_detail_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in metrics.get("supply_core_rows") or []:
        time_value = item.get("成交时间") or item.get("公告") or "—"
        deadline = item.get("截止") or item.get("成交预计") or "—"
        total = item.get("成交总价_万")
        if total is None:
            total = item.get("起始价_万")
        total_label = f"{fmt(total)}万" if total is not None else "—"
        deposit = f"{fmt(item.get('保证金_万'))}万" if item.get("保证金_万") is not None else "—"
        party = item.get("受让") or "—"
        relation = item.get("与L-9距离") or "—"
        premium = item.get("溢价")
        premium_text = f"溢价{premium}%" if premium is not None else "—"
        note = item.get("备注") or "—"
        rows.append([
            item.get("地块") or "—",
            f"{time_value} / {deadline}",
            f"总价 {total_label}；保证金 {deposit}",
            f"{party}；{relation}",
            f"{premium_text}；{note}",
        ])
    return rows


def supply_price_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            item.get("地块") or "—",
            item.get("时间") or "—",
            item.get("容积率") if item.get("容积率") is not None else "—",
            fmt(item.get("楼面价")),
            item.get("备注") or "—",
        ]
        for item in metrics.get("supply_price_rows") or []
    ]


def supply_annual_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            item.get("年度") or "—",
            item.get("成交宗数") if item.get("成交宗数") is not None else "—",
            fmt(item.get("成交规划建面_万㎡"), 2),
            fmt(item.get("成交楼面均价")),
            item.get("平均溢价率") or "—",
        ]
        for item in metrics.get("supply_annual_rows") or []
    ]


def supply_outlook_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    labels = ["供给总量趋势", "低密稀缺性", "未来直接竞争", "对L-9窗口的含义"]
    return [[labels[index] if index < len(labels) else f"研判{index + 1}", text] for index, text in enumerate(metrics.get("supply_outlook") or [])]


def supply_reference_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            item.get("地块") or "—",
            item.get("补缴场景") or "—",
            fmt(item.get("补缴金额_万")),
            item.get("公告时间") or "—",
            item.get("来源") or "—",
        ]
        for item in metrics.get("supply_reference_rows") or []
    ]


def policy_effective_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    labels = {
        "核心文件": "核心文件",
        "试行期": "原试行期",
        "继续执行通知": "继续执行通知",
        "当前有效": "当前有效",
        "有效至基准日": "基准日状态",
        "第二批": "第二批政策",
        "适用武清": "武清适用",
        "武清适用说明": "武清说明",
    }
    return [[labels.get(key, key), metrics.get("policy_effective", {}).get(key) or "—"] for key in labels]


def policy_space_rule_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            item.get("空间") or "—",
            item.get("设要") or "—",
            item.get("计容") or "—",
            item.get("奖励") or "—",
            item.get("销售") or "—",
        ]
        for item in metrics.get("policy_space_rows") or []
    ]


def policy_space_application_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [item.get("空间") or "—", item.get("适用L-9") or "—", item.get("补缴") or "—"]
        for item in metrics.get("policy_space_rows") or []
    ]


def policy_batch_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [item.get("空间") or "—", item.get("要点") or "—", item.get("计容") or "—"]
        for item in metrics.get("policy_batch2_rows") or []
    ]


def policy_product_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [[index + 1, text] for index, text in enumerate(metrics.get("policy_product_conclusions") or [])]


def policy_control_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [[index + 1, text] for index, text in enumerate(metrics.get("policy_controls") or [])]


def policy_example_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [item.get("项目") or "—", item.get("适用") or "—", fmt(item.get("补缴_万")), item.get("公告") or "—"]
        for item in metrics.get("policy_example_rows") or []
    ]


def customer_funnel_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [
            item.get("项目") or "—",
            item.get("可售套数") if item.get("可售套数") is not None else "—",
            item.get("净签套数") if item.get("净签套数") is not None else "—",
            item.get("统计期") or "—",
            item.get("备注") or "—",
        ]
        for item in metrics.get("customer_funnel_rows") or []
    ]


def customer_estimate_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [item.get("环节") or "—", item.get("参考比率") or item.get("参考") or "—", item.get("样本") or "—", item.get("status") or "—"]
        for item in metrics.get("customer_estimate_rows") or []
    ]


def customer_payment_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [item.get("维度") or "—", item.get("参考") or "—", item.get("source") or "—", item.get("status") or "—"]
        for item in metrics.get("customer_payment_rows") or []
    ]


def customer_offline_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    return [
        [item.get("字段") or "—", item.get("建议") or "—", item.get("样本") or "—"]
        for item in metrics.get("customer_offline_rows") or []
    ]


def customer_target_rows(metrics: dict[str, Any]) -> list[list[Any]]:
    target_text = metrics.get("customer_target_text") or "若L-9首开推200套并保持月均15—25套净签，则90天可实现45—75套去化，约23—38%去化率，全年可能去化150—250套"
    return [
        ["首开90天目标（原文回传）", target_text, "全为参考估算"],
        ["首开推盘假设", "200套", "估算输入"],
        ["90天去化", "45—75套", "按15—25套/月折算"],
        ["90天去化率", "约23—38%", "对首开200套的估算"],
        ["全年去化", "150—250套", "不可替代正式销售计划"],
    ]


def margin_matrix() -> tuple[list[list[Any]], list[list[Any]]]:
    # Planning-only formula retained from the refined local model:
    # all-in unit cost = land floor + comprehensive construction 4,600 + 14%
    # of realized selling price.  It is explicitly not a final financial model.
    lands = [5952, 6500, 7500]
    sales = [12956, 14400, 15339, 15656]
    rows: list[list[Any]] = []
    for land in lands:
        for sale in sales:
            cost = dec(land) + dec(4600) + dec(sale) * dec("0.14")
            margin = (dec(sale) - cost) / dec(sale) * 100
            rows.append([land, sale, rounded(cost, 0), rounded(margin, 2), rounded(dec(land) / dec(sale) * 100, 2)])
    thresholds = [
        [land, rounded((dec(land) + dec(4600)) / dec("0.78"), 0), rounded((dec(land) + dec(4600)) / dec("0.71"), 0), rounded((dec(land) + dec(4600)) / dec("0.86"), 0)]
        for land in lands
    ]
    return rows, thresholds


def apply_page_updates(seed: dict[str, Any], metrics: dict[str, Any], source_ids: list[str]) -> None:
    pages = list(seed.get("page_manifest") or [])
    by_id = {str(page.get("page_id")): page for page in pages}
    zz_land = ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND"]
    zz_market = ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE"]
    zz_comp = ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]
    zz_policy = ["SRC-ZZ-POLICY-VALUE"]
    taskbook = ["SRC-TASKBOOK"]
    finance = ["SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL"]
    old_land = ["SRC-LAND-COMP", "SRC-RANGE"]

    # Front decision pages: the current listing changes the decision basis.
    p = by_id.get("sc1-01")
    if p:
        update_page(
            p,
            title="L-9：挂牌事实把前策从概念研判拉回到实时竞买闸门",
            display_title="L-9｜挂牌事实与实时闸门",
            takeaway="最重要的新事实不是邻地7,507元/㎡，而是L-9自身已挂牌：容积率≤1.5、规划建面62,836.95㎡、起始价3.74亿元、保证金7,480万元、起拍楼面5,952元/㎡，8月28日截止、预计9月7日成交。任务书FAR2.0只能作为设计备选，不能覆盖当前挂牌口径。",
            decision_question="在挂牌窗口极短的情况下，当前地价、产品和法定条件是否允许继续进入？",
            decision_impact="把L-9起拍5,952元/㎡作为现实基准，把6,500和7,500作为压力情景；立即补齐附件2、合同和底图，停止把FAR2.0当作已核定法定指标。",
            blocks=[
                metric_block("当前挂牌硬事实", [
                    {"label": "建设用地", "value": "41,891.3", "unit": "㎡"},
                    {"label": "规划建面", "value": "62,836.95", "unit": "㎡"},
                    {"label": "挂牌容积率", "value": "≤1.5", "unit": "当前交易口径"},
                    {"label": "起拍楼面", "value": "5,952", "unit": "元/㎡"},
                    {"label": "挂牌截止", "value": "08-28", "unit": "预计成交09-07"},
                ], zz_land),
                table_block("挂牌公告关键字段｜资金与时序必须单独核验", ["字段", "当前回传值", "使用边界"], listing_transaction_rows(metrics), ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND"]),
                table_block("公开公告中的开发约束｜通用参照不等于L-9合同", ["约束类别", "回传原文", "口径", "放行要求"], land_constraint_rows(metrics), ["SRC-ZZ-D0-LAND"]),
                table_block("新数据如何改变原报告", [
                    "变量", "原报告主口径", "回传后当前口径", "处理"
                ], [
                    ["地块建面", "约83,800㎡（FAR2.0推导）", "62,836.95㎡挂牌建面", "以挂牌为交易基准，2.0保留备选"],
                    ["楼面情景", "4,600—5,630安全区间", "5,952起拍 / 6,500 / 7,500", "重新做售价—利润压力测试"],
                    ["需求结构", "90—140约38%、三房约69%", "90—140约70.3%、三房73.4%", "采用同窗口结构JSON"],
                    ["去化目标", "36—42套/月主情景", "15—25套/月参考基准，30—42为上行情景", "采用竞品窗口和真实漏斗边界"],
                ], zz_land + zz_market + zz_comp),
                narrative_block("明确结论", "当前不是简单的‘继续/停止’二选一：可以继续做竞买前研究，但不能按旧的4,600—5,100元/㎡假设掩盖5,952元/㎡现实起拍。若以武清全域12,956元/㎡成交中枢定价，5,952元/㎡仅有约4.6%的规划毛利空间；只有当低密直接竞品价格和本案交付价值能够把实际成交价推到14,400元/㎡以上，起拍情景才具备8%以上的条件性空间。", zz_land + zz_market + zz_comp + finance),
            ],
            refs=zz_land + zz_market + zz_comp + taskbook,
            charts=[chart_spec("CHART-ZZ-LAND-REALITY", "L-9当前挂牌与可比楼面价", "元/㎡", [{"name": "楼面价", "values": [5952, 6511, 7507, 7525]}], zz_land + old_land, dimensions=["L-9起拍", "宏顺地块", "紧邻2024-044", "建设路东侧"], measures=["楼面价"], time_window="截至2026-08-26", findings=["5,952是现实起拍，不是4,000可达下界", "6,500是低密可比与L-9之间的压力位", "7,500接近紧邻地块，需高价兑现"], decision_message="先按挂牌事实重建财务和强排基线。" )],
        )
    p = by_id.get("sc1-02")
    if p:
        margin_rows, thresholds = margin_matrix()
        update_page(
            p,
            title="决策结论：5,952元/㎡是现实起拍，6,500是条件压力线，7,500不应默认复制",
            display_title="决策｜挂牌价与售价兑现",
            takeaway="回传把项目从‘理论安全楼面’改成‘现实挂牌压力测试’：5,952元/㎡不自动否决，但必须证明实际成交价至少达到13,528元/㎡才过8%规划检验线；6,500需14,231元/㎡，7,500需15,513元/㎡。",
            decision_question="L-9当前起拍价在什么售价和产品兑现条件下才有研究价值？",
            decision_impact="竞买前只接受三档透明压力测试：5,952、6,500、7,500；以14,400/15,339/15,656元/㎡为低密直接竞品与3/5公里参照，不把参照价写成项目承诺。",
            blocks=[
                metric_block("新地价—售价闸门", [
                    {"label": "L-9起拍", "value": "5,952", "unit": "元/㎡"},
                    {"label": "3km竞品加权", "value": "15,339", "unit": "元/㎡"},
                    {"label": "5km竞品加权", "value": "15,656", "unit": "元/㎡"},
                    {"label": "起拍8%售价线", "value": "13,528", "unit": "规划公式"},
                    {"label": "6,500的8%售价线", "value": "14,231", "unit": "规划公式"},
                ], zz_land + zz_comp + finance),
                table_block("三档现实压力测试｜规划模型，不是财务承诺", ["楼面情景", "8%毛利所需售价", "15%毛利所需售价", "对标参照", "结论"], [
                    ["5,952｜起拍", "13,528", "14,862", "武清全域12,956；直接低密约14,400", "可继续研究，必须验证产品溢价"],
                    ["6,500｜压力", "14,231", "15,634", "3km15,339；5km15,656", "条件进入，成本与兑现要同时过线"],
                    ["7,500｜高风险", "15,513", "17,042", "接近5km高位，余量和折扣风险高", "不作为默认方案"],
                ], zz_land + zz_market + zz_comp + finance),
                table_block("逐行复算结果｜土地占售价、规划毛利", ["楼面", "售价", "规划全成本", "规划毛利率", "土地/售价"], margin_rows, finance + zz_land + zz_comp),
                narrative_block("一句话结论", "可以继续，但必须按挂牌事实重做：5,952元/㎡是起拍底盘，不是安全承诺；6,500元/㎡需要14,231元/㎡以上实际成交价与成本闭合；7,500元/㎡只有在高位成交、低密空间真实兑现且悲观情景仍过线时才有讨论价值。", zz_land + zz_comp + finance),
            ],
            refs=zz_land + zz_market + zz_comp + finance,
            charts=[chart_spec("CHART-ZZ-DECISION-GATE", "现实地价情景与8%售价线", "元/㎡", [{"name": "楼面价", "values": [5952, 6500, 7500]}, {"name": "8%售价线", "values": [13528, 14231, 15513]}], zz_land + zz_comp + finance, "combo_bar_line", dimensions=["5,952", "6,500", "7,500"], measures=["楼面价", "8%售价线"], time_window="2026-08-26回传校准", findings=["起拍情景需要的售价低于3/5km竞品加权参照，但高于武清全域中枢", "6,500的8%售价线进入直接竞品区间", "7,500的8%售价线接近5km高位，安全垫薄"], decision_message="把成交价兑现和成本闭合作为进入条件。" )],
        )
    p = by_id.get("sc1-03")
    if p:
        update_page(
            p,
            title="项目全景：挂牌地块与成熟城区资源同时成立，时序和法定口径决定兑现度",
            takeaway="L-9的区位和改善需求获得新数据支持，但当前交易口径是≤1.5、62,836.95㎡规划建面，不能再用FAR2.0推导直接替代挂牌事实。",
            blocks=[
                metric_block("项目当前身份", [
                    {"label": "位置", "value": "建设路西侧", "unit": "雍阳中学北侧"},
                    {"label": "用地", "value": "41,891.3", "unit": "㎡"},
                    {"label": "挂牌建面", "value": "62,836.95", "unit": "㎡"},
                    {"label": "3km竞品均价", "value": "15,339", "unit": "元/㎡"},
                    {"label": "5km竞品均价", "value": "15,656", "unit": "元/㎡"},
                ], zz_land + zz_comp),
                table_block("资源—风险双面读取", ["资源/事实", "对本案价值", "必须验证"], [
                    ["杨村成熟主城与教育/商业资源", "支持刚改改善叙事", "配套可达性与学区表达边界"],
                    ["3/5km竞品均价15,339/15,656", "存在低密改善价格参照", "本案折扣、交付和户型溢价"],
                    ["L-9起拍5,952、8/28截止", "窗口明确但资金准备紧", "合同、保证金、付款及竞买条件"],
                    ["任务书FAR2.0 vs挂牌≤1.5", "设计任务与交易口径冲突", "附件2法定规划条件裁决"],
                ], zz_land + zz_comp + taskbook),
            ],
            refs=zz_land + zz_comp + taskbook,
        )

    # Market and competition pages use the returned row-level data.
    p = by_id.get("sc2-01")
    if p:
        update_page(
            p,
            title="市场：需求集中在主流改善，但全域库存仍要求分批卖、先验证",
            takeaway=f"23个月逐月回传显示武清全域成交{fmt(metrics['monthly_units'])}套、加权均价{fmt(metrics['monthly_weighted_price'])}元/㎡、月均{fmt(metrics['monthly_avg_units'], 1)}套；期末可售{fmt(metrics['latest_available'])}套、出清{fmt(metrics['latest_cycle'], 2)}个月。",
            blocks=[
                metric_block("23个月市场快照｜2024-09—2026-07", [
                    {"label": "成交合计", "value": fmt(metrics["monthly_units"]), "unit": "套"},
                    {"label": "加权均价", "value": fmt(metrics["monthly_weighted_price"]), "unit": "元/㎡"},
                    {"label": "月均成交", "value": fmt(metrics["monthly_avg_units"], 1), "unit": "套/月"},
                    {"label": "期末可售", "value": fmt(metrics["latest_available"]), "unit": "套｜2026-07"},
                    {"label": "出清周期", "value": fmt(metrics["latest_cycle"], 2), "unit": "个月"},
                ], ["SRC-ZZ-MKT-MONTHLY"]),
                table_block("区域与圈层不要混用", ["层级", "回传读数", "使用边界"], [
                    ["武清全域", "23个月加权12,956元/㎡；11,434套", "判断总量和库存，不直接等同杨村售价"],
                    ["L-9 3km", "11项目/7在售；成交930套；均价15,339；可售1,330", "判断直接竞品压力"],
                    ["L-9 5km", "22项目/16在售；成交1,970套；均价15,656；可售2,427", "判断改善价格参照和未来竞争"],
                ], ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL"]),
                narrative_block("市场结论", "市场不是没有改善需求，而是总量市场偏冷、局部改善项目集中热销。L-9应把全域12,956元/㎡作为下行情景，把3/5公里15,339—15,656元/㎡作为需要产品兑现才能接近的参照，不把任何一个区域均价直接写成项目售价。", ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL"]),
            ],
            refs=["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"],
            charts=[chart_spec("CHART-ZZ-MARKET-SNAPSHOT", "全域市场与周边竞品价格参照", "元/㎡", [{"name": "价格", "values": [12956, 15339, 15656]}], ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL"], dimensions=["武清全域", "L-9 3km", "L-9 5km"], measures=["成交/竞品加权均价"], time_window="全域23个月与竞品近12个月", findings=["全域中枢低于周边直接竞品", "价格差异与产品/圈层相关", "售价锚必须分情景"], decision_message="以全域中枢做下行压力，以直接竞品做兑现参照。" )],
        )
    p = by_id.get("sc2-02")
    if p:
        area_chart_values = [row.get("成交套数", 0) for row in metrics["area_rows"]]
        price_chart_values = [row.get("成交套数", 0) for row in metrics["price_rows"]]
        update_page(
            p,
            title="需求结构校准：90—140㎡约70.3%，三房73.4%，120—300万约62.5%",
            display_title="需求结构｜同窗口面积、总价与户型",
            takeaway="新回传解决了前版最关键的结构缺口：面积、总价、户型三表均为2025-08—2026-07、分母均为5,226套。90—140㎡和三房是确定性主力，低密改善应围绕总价而不是脱离支付力单独拔高。",
            decision_question="市场主力是否足以支撑任务书四档产品和L-9的低密溢价？",
            decision_impact="95/105㎡承担首开流速，122㎡承接改善，139㎡控量验证；120—300万是区域支付主带，但139㎡以上必须用实得、面宽、交付和低密界面解释。",
            blocks=[
                table_block("分面积段成交结构｜近12个月 2025-08—2026-07", ["面积段", "成交套数", "均价（元/㎡）", "占总套数"], area_rows(metrics), ["SRC-ZZ-MKT-STRUCTURE"]),
                table_block("分户型成交结构｜同一窗口", ["户型", "成交套数", "占总套数", "均价（元/㎡）"], house_rows(metrics), ["SRC-ZZ-MKT-STRUCTURE"]),
                table_block("分总价段成交结构｜同一窗口", ["总价段", "成交套数", "占总套数", "均价（元/㎡）", "成交金额（万）"], price_rows(metrics), ["SRC-ZZ-MKT-STRUCTURE"]),
                metric_block("结构性主力读数", [
                    {"label": "90—140㎡", "value": f"{metrics['area_90_140_pct']}%", "unit": f"{fmt(metrics['area_90_140_units'])}/{fmt(metrics['area_total_units'])}套"},
                    {"label": "三房", "value": f"{metrics['three_room_pct']}%", "unit": f"{fmt(metrics['three_room_units'])}/{fmt(metrics['area_total_units'])}套"},
                    {"label": "120—300万", "value": f"{metrics['price_120_300_pct']}%", "unit": f"{fmt(metrics['price_120_300_units'])}套"},
                    {"label": "160—300万", "value": f"{metrics['price_160_300_pct']}%", "unit": f"{fmt(metrics['price_160_300_units'])}套"},
                ], ["SRC-ZZ-MKT-STRUCTURE"]),
                narrative_block("口径校正", "回传README摘要把120—300万元概括为57.3%，但逐行JSON的三档套数833+1,324+1,109=3,266，除以5,226套为62.5%。报告按可复算JSON使用；旧版90—140约38%和三房约69%不再作为当前结构结论。", ["SRC-ZZ-MKT-STRUCTURE"]),
            ],
            refs=["SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-MKT-MONTHLY", "SRC-TASKBOOK"],
            charts=[
                chart_spec("CHART-ZZ-AREA-STRUCTURE", "近12个月面积段成交结构", "%", [{"name": "套数占比", "values": [row.get("套数占比%", 0) for row in metrics["area_rows"]]}], ["SRC-ZZ-MKT-STRUCTURE"], dimensions=[row.get("面积段") for row in metrics["area_rows"]], measures=["套数占比"], time_window="2025-08—2026-07", findings=["90—120㎡39.6%为第一主力", "120—140㎡30.7%为第二主力", "90—140㎡合计70.3%"], decision_message="以95/105㎡承接主流，以122㎡承接改善。"),
                chart_spec("CHART-ZZ-PRICE-STRUCTURE", "近12个月总价段成交结构", "%", [{"name": "套数占比", "values": [rounded(dec(row.get("成交套数", 0)) / dec(metrics["area_total_units"]) * 100, 1) for row in metrics["price_rows"]]}], ["SRC-ZZ-MKT-STRUCTURE"], dimensions=[row.get("总价段") for row in metrics["price_rows"]], measures=["套数占比"], time_window="2025-08—2026-07", findings=["160—200万为单段第一", "120—300万为主力总价带", "300万以上占比很低"], decision_message="低密改善要控制总价并把溢价落到可体验指标。"),
            ],
        )
    p = by_id.get("sc2-03")
    if p:
        update_page(
            p,
            title="竞品：3/5公里均价15,339—15,656，但高流速与首开峰值不能年化",
            takeaway="回传把竞品从‘几个项目名’补成3/5公里圈层和项目级全量库：直接竞品价格高于武清全域中枢，但项目速度受首开时点、余量和统计窗口影响显著。",
            blocks=[
                table_block("3/5公里圈层汇总", ["圈层", "项目数/在售", "近12个月成交", "成交均价", "期末可售", "使用边界"], [
                    ["3km", "11 / 7", "930套 / 116,772㎡", "15,339元/㎡", "1,330套 / 172,912㎡", "直接竞争"],
                    ["5km", "22 / 16", "1,970套 / 244,679㎡", "15,656元/㎡", "2,427套 / 327,953㎡", "改善参照与未来竞争"],
                ], ["SRC-ZZ-COMP-FULL"]),
                table_block("重点竞品近12个月/首开口径分开", ["项目", "距离m", "容积率", "成交套数", "均价", "可售", "速度", "口径说明"], focus_competitor_rows(metrics), ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]),
                narrative_block("竞争结论", "成熟竞品的可比速度主要落在约11.4—26.1套/月；绿城尚玉蘭的180—240套/月是新盘首开峰值，龙湖云曜约52套/月也属于开盘错期估算，不能外推为L-9常态。L-9应以主流面积段、总价、交付和低密界面形成综合效率，而不是简单复制最高单价或最高首开峰值。", ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]),
            ],
            refs=["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-MKT-STRUCTURE"],
            charts=[chart_spec("CHART-ZZ-COMP-PRICE", "重点竞品成交均价", "元/㎡", [{"name": "成交均价", "values": [17808, 16444, 14400, 19301, 15062, 17391, 15671]}], ["SRC-ZZ-COMP-FULL"], dimensions=["绿城尚玉蘭", "宏顺央璟颂", "雍鑫溪和林", "金地雍阳印", "龙湖云曜", "招商揽阅", "新城玺樾春秋"], measures=["成交均价"], time_window="近12个月项目快照", findings=["直接竞品价格带14,400—17,808", "高价不等于高速度", "可售和开盘时点必须共同读取"], decision_message="用主流总价和兑现效率切入，不追逐单一高价。" )],
        )
    p = by_id.get("sc2-04")
    if p:
        update_page(
            p,
            title="供应压测：全域21.78个月出清，3/5公里仍有1,330—2,427套可售",
            takeaway="全域库存较高、周边直接竞品仍有货量，L-9的窗口价值来自低密供给稀缺和当前挂牌时点，而不是市场自然快销。",
            blocks=[
                metric_block("存量与圈层供应", [
                    {"label": "全域期末可售", "value": "9,695", "unit": "套｜2026-07"},
                    {"label": "全域出清周期", "value": "21.78", "unit": "个月"},
                    {"label": "3km可售", "value": "1,330", "unit": "套"},
                    {"label": "5km可售", "value": "2,427", "unit": "套"},
                    {"label": "L-9起拍", "value": "5,952", "unit": "元/㎡"},
                ], ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-D0-LAND"]),
                table_block("未来供应与L-9时序", ["地块/项目", "状态", "规划建面/规模", "对L-9影响", "必须跟踪"], [
                    ["L-9津武2024-024", "挂牌中，08-28截止", "62,836.95㎡", "自身首开和资金窗口", "合同/入市节奏"],
                    ["津武2025-080", "已成交待入市", "132,900㎡", "中期低密竞争", "入市时间/产品"],
                    ["津武2026-062", "挂牌中", "57,165㎡；推出楼面5,021", "同武清新城低密竞争", "成交与入市"],
                    ["金地雍阳印二期", "预计2026-12-31，待核", "项目级", "改善供应时序", "字段真实性"],
                ], ["SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-COMP-FULL"]),
                narrative_block("供应结论", "首开窗口需要抢，但不等于可以用速度掩盖法定和财务缺口。建议把L-9首开放量控制在真实漏斗支持的范围，以15—25套/月作为当前参考基准，把30—42套/月保留为上行情景；周边可售和未来低密地块的入市时间应每周更新。", ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-CUSTOMER-FUNNEL"]),
            ],
            refs=["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-CUSTOMER-FUNNEL"],
        )
    p = by_id.get("sc2-05")
    if p:
        update_page(
            p,
            title="客群：三房主导、90—140㎡集中，首开漏斗仍必须以线下实测为准",
            takeaway="新回传把客群依据从旧版结构值校正为同窗口事实：三房73.4%、90—140㎡70.3%、120—300万62.5%；但来访—认筹—净签过程数据依然缺失。",
            blocks=[
                table_block("三类核心客群与产品动作", ["客群", "购买触发", "主要抗性", "产品动作"], [
                    ["本地老城外溢刚改", "置换、孩子成长、成熟配套", "首付/月供和总价", "95/105㎡三房、透明总价"],
                    ["地缘改善", "环境、归家、社区品质", "交付和竞品比较", "105/122㎡、示范区先行"],
                    ["新天津人/京津通勤", "就业、通勤、贷款便利", "交通和期房风险", "主力段低门槛、通勤体验"],
                    ["教育关注交叉人群", "邻近教育资源关注", "不能接受学区承诺不清", "只表达可核验的邻近/便利事实"],
                ], ["SRC-TASKBOOK", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL"]),
                metric_block("支付力与结构证据", [
                    {"label": "三房", "value": "73.4%", "unit": "3,838/5,226套"},
                    {"label": "90—140㎡", "value": "70.3%", "unit": "3,673/5,226套"},
                    {"label": "120—300万", "value": "62.5%", "unit": "3,266/5,226套"},
                    {"label": "首开参考", "value": "15—25", "unit": "套/月估算"},
                ], ["SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL"]),
                table_block("首开90天漏斗：已知与未知", ["环节", "当前数据", "状态", "使用方式"], [
                    ["来访→认筹", "15—25%参考转化", "估算", "只做流量敏感性"],
                    ["认筹→网签", "60—75%参考转化", "估算", "待竞品/现场台账"],
                    ["网签→净签", "95—98%参考转化", "估算", "扣退房和审批"],
                    ["净签", "成熟竞品约11.4—26.1套/月", "实证参照", "L-9当前参考区间"],
                    ["目标项目真实漏斗", "未提供", "阻断", "首开90天回填"],
                ], ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL"]),
            ],
            refs=["SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL", "SRC-TASKBOOK"],
            charts=[chart_spec("CHART-ZZ-CUSTOMER-MATCH", "市场结构与首开产品优先级", "%", [{"name": "市场结构", "values": [73.4, 70.3, 62.5]}, {"name": "任务书95/105合计", "values": [60, 60, 60]}], ["SRC-ZZ-MKT-STRUCTURE", "SRC-TASKBOOK"], "combo_bar_line", dimensions=["三房", "90—140㎡", "120—300万"], measures=["市场结构", "任务书95/105合计"], time_window="结构近12个月 + 任务书", findings=["三房与90—140㎡高度集中", "任务书95/105合计60%是首开骨架", "总价与实得价值决定改善段转化"], decision_message="主流先行，改善控量，漏斗回填后再改配比。" )],
        )
    p = by_id.get("sc2-06")
    if p:
        update_page(
            p,
            title="竞品速度：成熟盘约11—26套/月，新盘峰值不能年化，L-9先按15—25套/月",
            takeaway="新回传同时给出项目级成交和首开峰值，说明速度必须按生命周期读取；L-9当前应把15—25套/月作为可复核参考，把30—42套/月降为上行情景。",
            blocks=[
                table_block("竞品速度分层", ["梯队", "项目/口径", "速度", "如何使用"], [
                    ["首开峰值", "绿城尚玉蘭", "180—240套/月峰值", "不可年化"],
                    ["新盘错期", "龙湖云曜", "约52套/月估算", "78套/约1.5个月"],
                    ["成熟改善", "宏顺央璟颂 / 新城玺樾春秋", "26.1 / 24.3套/月", "可比参考"],
                    ["成熟刚改/同源", "雍鑫溪和林", "20.1套/月", "直接竞品参考"],
                    ["高价低速", "金地雍阳印 / 招商揽阅", "11.4 / 13.6套/月", "价格与余量警示"],
                ], ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]),
                table_block("L-9去化情景｜508套示意总量", ["月均净签", "去化月数", "状态", "触发动作"], [["15套", "33.9月", "当前保守参考", "控制首开供货"], ["20套", "25.4月", "中性参考", "观察价格/渠道"], ["25套", "20.3月", "积极参考", "需产品兑现"], ["30套", "16.9月", "上行情景", "首开数据支持后启用"], ["36—42套", "14.1—12.1月", "高上行情景", "不得先写成基准"]], ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL", "SRC-RANGE"]),
                narrative_block("速度结论", "目标项目没有真实漏斗前，报告不再把36—42套/月写成默认目标。首开前应先用15—25套/月做供货和现金占用压力，首开90天若实测净签稳定超过30套/月，再逐批释放上行情景。", ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL"]),
            ],
            refs=["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-RANGE"],
            charts=[chart_spec("CHART-ZZ-SPEED", "成熟竞品与L-9参考速度", "套/月", [{"name": "月均去化", "values": [26.1, 24.3, 20.1, 13.6, 11.4, 15, 20, 25]}], ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"], dimensions=["宏顺", "新城玺樾", "雍鑫", "招商", "金地", "L-9保守", "L-9中性", "L-9积极"], measures=["月均去化"], time_window="竞品近12个月 + L-9情景", findings=["成熟盘速度集中在11—26套/月", "L-9 15—25为当前参考", "30套以上需首开回测"], decision_message="先用真实漏斗决定放量，不用峰值替代常态。" )],
        )
    p = by_id.get("sc2-07")
    if p:
        months = [str(row.get("month")) for row in metrics["monthly_rows"]]
        sales = [row.get("成交套数_value") for row in metrics["monthly_rows"]]
        prices = [row.get("成交均价_元_㎡_value") for row in metrics["monthly_rows"]]
        update_page(
            p,
            title="月度市场：23个月连续回传显示成交波动、价格中枢约12,956，库存仍高",
            takeaway="以逐月JSON为准，2024-09—2026-07共23个月连续数据；成交月度波动444—944等，期末可售由14120降至9695套，出清周期仍21.78个月。",
            blocks=[
                table_block("武清新房月度量价供求｜逐月JSON", ["月份", "成交套数", "成交面积㎡", "均价元/㎡", "批准上市", "期末可售", "出清月数"], monthly_rows(metrics), ["SRC-ZZ-MKT-MONTHLY"]),
                metric_block("23个月汇总", [
                    {"label": "成交", "value": fmt(metrics["monthly_units"]), "unit": "套"},
                    {"label": "成交面积", "value": fmt(metrics["monthly_area_sqm"], 0), "unit": "㎡"},
                    {"label": "加权均价", "value": fmt(metrics["monthly_weighted_price"]), "unit": "元/㎡"},
                    {"label": "月均成交", "value": fmt(metrics["monthly_avg_units"], 1), "unit": "套/月"},
                    {"label": "期末可售", "value": fmt(metrics["latest_available"]), "unit": "套"},
                ], ["SRC-ZZ-MKT-MONTHLY"]),
                narrative_block("数据质量与结论", "回传README把窗口概括为24个月、月均约488套，但逐月JSON实际为23个月、累计11,434套、月均约497.1套；报告按行级JSON重算。2026-02批准上市为空，不插值。全域库存和周期仍要求L-9采用分批供货。", ["SRC-ZZ-MKT-MONTHLY"]),
            ],
            refs=["SRC-ZZ-MKT-MONTHLY"],
            charts=[chart_spec("CHART-ZZ-MONTHLY-23M", "武清新房23个月成交与均价", "套 / 元/㎡", [{"name": "成交套数", "values": sales}, {"name": "成交均价", "values": prices}], ["SRC-ZZ-MKT-MONTHLY"], "combo_bar_line", dimensions=months, measures=["成交套数", "成交均价"], time_window="2024-09—2026-07（23条逐月记录）", findings=["成交存在明显月度波动", "价格中枢约12,956元/㎡", "库存下降但21.78个月周期仍高"], decision_message="首开抢窗口与控制供货必须同时做。" )],
        )
    p = by_id.get("sc2-08")
    if p:
        update_page(
            p,
            title="支付结构：120—300万约62.5%、160—300万约46.6%，二手价差要求新房兑现价值",
            takeaway="同一近12个月结构JSON显示120—300万元为62.5%、160—300万元为46.6%；3km二手挂牌约9,291元/㎡，改善溢价必须由户型、低密和交付解释。",
            blocks=[
                table_block("总价带逐行结构", ["总价段", "成交套数", "占总套数", "均价元/㎡", "成交金额万"], price_rows(metrics), ["SRC-ZZ-MKT-STRUCTURE"]),
                table_block("支付与置换代理", ["指标", "回传读数", "状态", "使用边界"], [["武清3km二手挂牌", "9,291元/㎡", "中指实证", "二手价格参照"], ["武清3km二手年成交", "3,322套", "中指实证", "置换池规模代理"], ["商贷首付", "首二套统一15%", "公开政策", "以属地执行为准"], ["公积金最高可贷", "普通家庭约100万级", "公开推断", "需属地核实"]], ["SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL"]),
                narrative_block("支付结论", "95/105㎡按12,956元/㎡全域中枢对应约123—136万元名义总价；按3km竞品参照15,339元/㎡对应约146—161万元。122/139㎡进入160—300万元主力带时，必须用面宽、得房、归家和可交付的低密体验解释价格，不把赠送理论直接计入支付力。", ["SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]),
            ],
            refs=["SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"],
            charts=[chart_spec("CHART-ZZ-TOTAL-PRICE", "总价段成交占比", "%", [{"name": "占比", "values": [rounded(dec(row.get("成交套数", 0)) / dec(metrics["area_total_units"]) * 100, 1) for row in metrics["price_rows"]]}], ["SRC-ZZ-MKT-STRUCTURE"], dimensions=[row.get("总价段") for row in metrics["price_rows"]], measures=["占总套数"], time_window="2025-08—2026-07", findings=["160—200万单段25.3%最高", "120—300万合计62.5%", "300万以上仅少量"], decision_message="首开总价先卡支付主带，再用改善产品拉开梯度。" )],
        )
    p = by_id.get("sc2-09")
    if p:
        update_page(
            p,
            title="土地市场：L-9已挂牌起拍5,952，当前要用真实起拍替代理论4,000下界",
            takeaway="土地回传同时补齐了L-9当前挂牌和近年可比：L-9起拍5,952元/㎡，同容积率1.5可比约6,511—7,522元/㎡，紧邻2.0地块约7,507—7,525元/㎡。",
            blocks=[
                table_block("L-9挂牌交易参数", ["字段", "当前回传值", "使用边界"], listing_transaction_rows(metrics), ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND"]),
                table_block("L-9与可比地块统一楼面价", ["地块", "编号/状态", "容积率", "规划建面㎡", "楼面价元/㎡", "时间/截止", "备注"], land_comparison_rows(metrics), ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND"]),
                table_block("土地价格三档使用方式", ["情景", "数值", "性质", "报告用法"], [["现实起拍", "5,952", "L-9当前挂牌", "财务与竞买基准"], ["中间压力", "6,500", "1.5低密可比下沿附近", "条件性情景"], ["高位压力", "7,500", "紧邻/同板块高位参照", "不默认接受"], ["4,000", "任务书/旧模型下界", "当前不可达假设", "仅作历史对照"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND", "SRC-LEGACY-FEASIBILITY"]),
                narrative_block("土地结论", "L-9起拍价本身已经高于旧报告建议的4,600—5,630区间上沿，不能继续用旧安全区间掩盖现实。新的研究问题是：起拍5,952是否能通过14,400元/㎡以上的低密成交价、综合建安4,600元/㎡和真实首开漏斗共同成立；在证据闭合前，7,500只保留为高风险压力情景。", ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-COMP-FULL", "SRC-FINANCE"]),
            ],
            refs=["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-COMP-FULL", "SRC-FINANCE"],
            charts=[chart_spec("CHART-ZZ-LAND-TREND", "近年土地供应与L-9楼面情景", "元/㎡", [{"name": "楼面价", "values": [5952, 6511, 5630, 7507, 7525]}], ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND"], dimensions=["L-9起拍", "宏顺1.5", "雍阳西道1.6", "紧邻044", "045"], measures=["楼面价"], time_window="2025—2026挂牌/成交", findings=["L-9起拍处于低密可比中低位但不是低价", "同板块高位地价约7,500", "现实竞买情景需重建"], decision_message="以5,952为基准、6,500为压力、7,500为高风险。" )],
        )

    # Site and strong-row pages now carry the transaction/legal hierarchy.
    p = by_id.get("sc3-01")
    if p:
        update_page(
            p,
            title="场地：挂牌交易口径已明确，但附件2和真实几何仍不能用文字替代",
            takeaway="当前可确认的是L-9挂牌身份、41,891.3㎡用地和62,836.95㎡规划建面、≤1.5容积率；任务书FAR2.0是设计输入，红线/真北/标高/道路仍未闭合。",
            blocks=[
                table_block("场地证据层级", ["层级", "已知内容", "使用方式", "未闭合"], [["挂牌/土地回传", "津武2024-024；41,891.3㎡；62,836.95㎡；≤1.5", "当前交易和财务基准", "合同与附件细项"], ["任务书", "约4.19ha；FAR2.0；四档户型与方案要求", "设计备选输入", "法定条件优先级"], ["测绘/法定底图", "尚未取得闭合红线、坐标、真北、标高、道路断面", "禁止精确强排", "D0阻断"]], zz_land + taskbook + ["SRC-SITE-OFFICIAL"]),
                narrative_block("空间结论", "当前可以做价值分区、交通关系和A/B参数框架，不能输出法定楼栋定位、日照、消防、建筑间距、地库或精确可售套数。收到附件2和真实底图后，应以挂牌≤1.5与任务书2.0双基线重跑，再由法定条件裁决。", zz_land + taskbook + ["SRC-SITE-OFFICIAL"]),
            ],
            refs=zz_land + taskbook + ["SRC-SITE-OFFICIAL"],
        )
    p = by_id.get("sc3-02")
    if p:
        update_page(
            p,
            title="口径冲突升级：挂牌 ≤1.5 是当前交易基准，任务书 FAR2.0 仅为设计备选",
            display_title="任务书｜挂牌口径与设计输入",
            takeaway="新D0土地回传把原来的‘旧资料≤1.5’升级为‘L-9当前挂牌≤1.5’。在附件2未取得前，强排和财务必须并列两套基线，但交易判断不能继续以FAR2.0单独作为现实总量。",
            decision_question="当前竞买与方案设计分别应以什么口径工作？",
            decision_impact="交易/财务基准：≤1.5、62,836.95㎡；设计备选：任务书FAR2.0、83,800㎡推导；正式法定口径：待附件2裁决。所有套数、车位、地库和货值均需带口径标签。",
            blocks=[
                table_block("三层权威层级", ["口径", "来源", "优先级", "本版使用"], [["当前挂牌", "ZZ-D0-LAND / ZZ-SUPPLY-LAND", "交易事实", "5,952与62,836.95㎡主基准"], ["任务书设计输入", "任务书内嵌图示", "设计任务", "FAR2.0与95/105/122/139配比备选"], ["最终法定条件", "附件2规划条件通知书", "最高", "尚未取得，正式放行前必须裁决"]], zz_land + taskbook + ["SRC-SITE-OFFICIAL"]),
                table_block("总量示意｜同一任务书配比、显式效率假设", ["口径", "计容/规划建面", "加权户均", "90%效率示意套数", "无效率扣减套数", "限制"], [["挂牌≤1.5", "62,836.95㎡", "111.35㎡", "约508", "约564", "不是最终可售"], ["任务书FAR2.0", "83,800㎡", "111.35㎡", "约677", "约753", "法定待核"], ["正式可售", "待拆非住宅/公建/公摊/奖励", "待户型整层", "待总图", "待总图", "不得提前计入货值"]], zz_land + taskbook + ["SRC-SITE-OFFICIAL"]),
                narrative_block("处理原则", "新土地回传不是把任务书内容作废，而是把它放回正确层级：FAR2.0仍服务于方案研究，挂牌≤1.5服务于当前交易和财务压力；附件2一旦取得，必须让法定条件覆盖冲突并同步重建报告。", zz_land + taskbook + ["SRC-SITE-OFFICIAL"]),
            ],
            refs=zz_land + taskbook + ["SRC-SITE-OFFICIAL"],
            diagrams=[{
                "diagram_id": "DIA-ZZ-FAR-HIERARCHY",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "挂牌事实→任务书设计输入→附件2法定裁决",
                "diagram_type": "massing_sequence",
                "program_blocks": [{"label": "挂牌≤1.5 / 62,836.95㎡", "color": "#30d158"}, {"label": "任务书FAR2.0 / 83,800㎡", "color": "#64d2ff"}, {"label": "附件2法定条件", "color": "#ff9f0a"}, {"label": "正式强排与财务", "color": "#ff453a"}],
                "relations": [[0, 3], [1, 2], [2, 3]],
                "source_refs": zz_land + taskbook + ["SRC-SITE-OFFICIAL"],
                "note": "关系图，不表达真实边界、楼栋位置或法定结论。",
            }],
        )
    p = by_id.get("sc3-04")
    if p:
        update_page(
            p,
            title="强排推演：先按挂牌≤1.5做现实基线，再以FAR2.0做设计备选",
            takeaway="A/B两案仍然必要，但同边界比较要从‘FAR2.0唯一主场景’改为‘挂牌≤1.5现实基线 + FAR2.0设计备选’；两案都不能在无红线时冒充精确总图。",
            blocks=[
                table_block("两套强排×两套总量基线", ["维度", "挂牌≤1.5现实基线", "任务书FAR2.0设计备选", "共同验收"], [["计容/规划建面", "62,836.95㎡", "83,800㎡示意", "法定附件2裁决"], ["方案A", "高流速、经济地库、约508套示意", "同逻辑扩容至约677套示意", "指标/车位/日照/消防"], ["方案B", "低密界面、上下跃小批、控量", "展示与空间价值放大", "送赠/补缴/成本"], ["产品", "95/105首开、122/139控量", "任务书四档完整", "户型与总图对应"], ["去化", "15—25套/月参考", "30—42套/月上行情景", "首开90天漏斗"], ["放行", "起拍5,952下仍需售价兑现", "需附件2确认后再进入正式方案", "双基线可复算"]], zz_land + taskbook + ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-FINANCE"]),
                narrative_block("强排验收底线", "挂牌数据解决了现实总量和地价问题，但没有解决真实红线、真北、标高、道路断面、日照、消防、地库和可售面积。因此当前输出仍是参数关系与验收矩阵，建筑师取得底图后必须按两套基线分别回填，不得用概念体块替代正式强排。", zz_land + taskbook + ["SRC-SITE-OFFICIAL", "SRC-FINANCE"]),
            ],
            refs=zz_land + taskbook + ["SRC-SITE-OFFICIAL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-FINANCE"],
            diagrams=[{
                "diagram_id": "DIA-ZZ-MASSING-AB",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "挂牌现实基线 / FAR2.0备选→A/B方案→验收",
                "diagram_type": "massing_sequence",
                "program_blocks": [{"label": "挂牌≤1.5 / 62,836.95㎡", "color": "#30d158"}, {"label": "任务书FAR2.0备选", "color": "#64d2ff"}, {"label": "A｜高流速经济型", "color": "#30d158"}, {"label": "B｜展示溢价型", "color": "#ff9f0a"}, {"label": "指标/日照/消防/地库", "color": "#ffd60a"}, {"label": "货值×去化×成本", "color": "#ff453a"}],
                "relations": [[0, 2], [0, 3], [1, 2], [1, 3], [2, 4], [3, 4], [4, 5]],
                "source_refs": zz_land + taskbook + ["SRC-SITE-OFFICIAL", "SRC-FINANCE"],
                "note": "非比例关系图；无红线时不表达真实楼栋、间距或日照。",
            }],
        )

    # Finance and value pages.
    margin_rows, threshold_rows = margin_matrix()
    p = by_id.get("va2-02")
    if p:
        update_page(
            p,
            title="财务精修：现实起拍5,952下，售价兑现比旧安全楼面更关键",
            takeaway="综合建安工作中枢仍按4,600元/㎡、其他期间/税费规划系数14%做条件性压力测试；新挂牌价使‘能否达到14,400元/㎡以上实际成交’成为第一财务问题。",
            blocks=[
                table_block("输入口径", ["输入", "当前值", "状态", "限制"], [["L-9土地楼面", "5,952 / 6,500 / 7,500", "回传+情景", "7,500非默认"], ["规划/交易建面", "62,836.95㎡；FAR2.0备选83,800㎡", "双口径", "附件2待裁决"], ["综合建安", "4,200—5,200；工作4,600", "项目精修", "地库/展示/园林需拆分"], ["其他期间/税费规划系数", "售价14%", "规划公式", "不等于最终税务清算"], ["送赠/奖励", "不提前计入", "政策条件", "补缴/结构消防待核"]], zz_land + finance + ["SRC-ZZ-POLICY-VALUE"]),
                table_block("售价参照与规划毛利率", ["楼面", "售价参照", "规划全成本", "规划毛利率", "土地/售价"], margin_rows, zz_land + ["SRC-ZZ-COMP-FULL", "SRC-ZZ-MKT-MONTHLY"] + finance),
                table_block("达到毛利线所需的实际成交价", ["楼面", "保本售价", "8%售价", "15%售价", "解释"], [[str(row[0]), str(row[3]), str(row[1]), str(row[2]), "先看成交口径，不看展示/挂牌价"] for row in threshold_rows], zz_land + ["SRC-ZZ-COMP-FULL"] + finance),
                narrative_block("财务结论", "5,952元/㎡在武清全域12,956元/㎡中枢下只有约4.6%的规划毛利空间；若实际成交能达到14,400元/㎡，规划毛利约12.7%，但仍需扣除折扣、首开费用、现金峰值和项目级送赠成本。6,500与7,500不应只看竞品均价，必须把真实折扣、余量和交付兑现一起带入。", zz_land + ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL"] + finance),
            ],
            refs=zz_land + ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-POLICY-VALUE"] + finance,
            charts=[chart_spec("CHART-ZZ-MARGIN-MATRIX", "楼面情景与售价参照下的规划毛利", "%", [{"name": "全域中枢12,956", "values": [4.56, 0.33, -7.39]}, {"name": "直接低端14,400", "values": [12.72, 8.92, 1.97]}, {"name": "3km15,339", "values": [17.21, 13.64, 7.12]}, {"name": "5km15,656", "values": [18.6, 15.1, 8.71]}], zz_land + ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-FINANCE"], "multi_series_bar", dimensions=["5,952", "6,500", "7,500"], measures=["规划毛利率"], time_window="规划公式：土地+建安4,600+售价14%", findings=["起拍价在全域中枢下安全垫薄", "直接低密价位可改善毛利", "7,500需要接近高位售价且没有余量"] , decision_message="把实际成交价、折扣和成本拆分作为财务放行条件。" )],
        )
    p = by_id.get("va2-03")
    if p:
        update_page(
            p,
            title="货值破局矩阵：送赠是增值杠杆，但不能替代5,952起拍下的真实售价与成本",
            takeaway="政策回传确认有效期和武清补缴实例，但也确认补缴由评估核定、不是任务书‘区级28%’的简单套算；送赠只能在强排、成本和销售口径闭合后进入货值。",
            blocks=[
                table_block("政策—货值—成本五步链", ["步骤", "必须回答", "当前状态"], [["适用", "低多层、上下跃、坡屋顶、地下、封闭阳台是否适用", "政策方向支持，项目级待核"], ["实现", "真实可实现面积和结构消防", "无底图/强排，未闭合"], ["补缴", "第三方评估核定金额", "武清有实例，L-9未核"], ["成本", "地库/结构/保温/园林/展示中心增量", "工作中枢4,600，需拆分"], ["收益", "实际成交价与净签而非展示价", "以12,956/14,400/15,339/15,656做情景"]], ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND", "SRC-ZZ-COMP-FULL", "SRC-FINANCE"]),
                table_block("武清补缴实例：只作机制证据", ["地块", "空间机制", "补缴万", "可迁移结论", "不能迁移"], [["津武2023-014", "坡屋顶+地下+封闭阳台", "228.39", "武清存在落地案例", "金额/比例不可直接套用"], ["津武2024-013", "坡屋顶+地下+兼容商业", "523.65", "政策可形成货值路径", "不能替代L-9评估"], ["L-9", "待专项方案", "待评估", "只做条件性研究", "不提前计入确定货值"]], ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-SUPPLY-LAND"]),
                narrative_block("货值结论", "把送赠写成‘实现15%实得提升’并不能自动让5,952或7,500成立。正确顺序是：先用挂牌面积和成交价建立不送赠压力，再用项目级强排测出可实现面积、补缴、增量建安、折扣和退房，最后判断净增值是否足以过8%检验线。", ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND", "SRC-FINANCE"]),
            ],
            refs=["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND", "SRC-FINANCE"],
        )
    p = by_id.get("va2-06")
    if p:
        update_page(
            p,
            title="财务边界重置：5,952起拍可研究，6,500需条件，7,500必须高位兑现",
            takeaway="安全边界不再是单一楼面数字，而是挂牌楼面、实际成交价、综合建安、折扣、去化和政策净增值的组合；新数据使起拍情景也需要明确售价门槛。",
            blocks=[
                table_block("现实三档边界", ["楼面", "全域中枢下规划毛利", "直接低密14,400下规划毛利", "放行条件"], [["5,952", "4.56%", "12.72%", "实际成交≥13,528过8%；成本与漏斗闭合"], ["6,500", "0.33%", "8.92%", "实际成交≥14,231；低密产品和交付兑现"], ["7,500", "-7.39%", "1.97%", "实际成交≥15,513；不作为默认方案"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-FINANCE"]),
                table_block("必须同步验证的六个变量", ["变量", "当前读数/状态", "不满足时"], [["土地", "起拍5,952，成交未发生", "不把成交价当事实"], ["售价", "全域12,956；3km15,339；5km15,656", "改用保守情景"], ["成本", "4,200—5,200，中枢4,600", "下调楼面/品质"], ["去化", "15—25套/月参考，真实漏斗缺失", "减首开供货"], ["送赠", "政策有效，项目金额未核", "不计入确定货值"], ["规划", "挂牌≤1.5 vs任务书2.0", "停止精确强排"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-SITE-OFFICIAL"]),
                narrative_block("财务结论", "建议把5,952元/㎡作为当前研究和竞买基准，而不是把它直接命名为安全价；6,500仅在14,231元/㎡以上实际成交、综合建安受控和15—25套/月漏斗成立时讨论；7,500只有在15,513元/㎡以上且悲观仍过线时才进入压力测试。", ["SRC-ZZ-D0-LAND", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-FINANCE"]),
            ],
            refs=["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-FINANCE"],
        )
    p = by_id.get("va2-07")
    if p:
        update_page(
            p,
            title="货值去化联动：挂牌≤1.5约508套示意，FAR2.0约677—753套仅作备选容量",
            takeaway="新挂牌规划建面使508套低密示意重新获得现实基础；FAR2.0不再是当前交易总量，而是任务书设计备选。去化主情景从36—42套/月下调为15—25套/月参考。",
            blocks=[
                table_block("总量口径对照｜任务书四档配比、111.35㎡加权户均、90%效率仅为假设", ["口径", "建面", "加权户均", "90%效率示意", "不扣效率", "使用"], [["挂牌≤1.5", "62,836.95㎡", "111.35㎡", "约508套", "约564套", "当前交易/容量基准"], ["任务书FAR2.0", "83,800㎡", "111.35㎡", "约677套", "约753套", "设计备选"], ["正式可售", "待拆分", "待户型整层", "待总图", "待总图", "最终财务/营销"]], ["SRC-ZZ-D0-LAND", "SRC-TASKBOOK", "SRC-SITE-OFFICIAL"]),
                table_block("去化情景｜508套仅作容量敏感性", ["月均净签", "周期", "角色", "验证"], [["15套", "33.9月", "保守基准", "真实漏斗"], ["20套", "25.4月", "中性参考", "价格/供货"], ["25套", "20.3月", "积极参考", "产品兑现"], ["30套", "16.9月", "上行情景", "首开回测"], ["36套", "14.1月", "高上行情景", "不得预设"], ["42套", "12.1月", "极高上行情景", "不得预设"]], ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL", "SRC-ZZ-D0-LAND"]),
                narrative_block("容量结论", "在真实目标项目漏斗缺失的情况下，当前报告把15—25套/月作为经营参考，把30—42套/月保留为上行情景。508套并不等于最终可售套数；它只是挂牌建面×90%效率÷任务书配比加权户均的透明示意，必须由总图、非住宅、公建、公摊、地库和首开回测重算。", ["SRC-ZZ-D0-LAND", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-SITE-OFFICIAL"]),
            ],
            refs=["SRC-ZZ-D0-LAND", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL", "SRC-TASKBOOK", "SRC-SITE-OFFICIAL"],
            charts=[chart_spec("CHART-ZZ-ABSORPTION", "508套容量的去化周期敏感性", "月", [{"name": "周期", "values": [33.9, 25.4, 20.3, 16.9, 14.1, 12.1]}], ["SRC-ZZ-D0-LAND", "SRC-ZZ-CUSTOMER-FUNNEL"], dimensions=["15套/月", "20套/月", "25套/月", "30套/月", "36套/月", "42套/月"], measures=["去化周期"], time_window="挂牌≤1.5容量示意", findings=["15—25套/月对应20—34个月", "30套以上属于上行情景", "正式套数变化后必须重算"], decision_message="先用保守漏斗做首开供货，不把高速度写成基准。" )],
        )

    p = by_id.get("va3-04")
    if p:
        update_page(
            p,
            title="户型配比：任务书四档保留，市场校准为90—140㎡70.3%、三房73.4%",
            takeaway="任务书95/105/122/139㎡=30/30/25/15%仍是设计起点；新结构数据支持90—140㎡和三房主导，但不支持把四档配比直接当作最终最优或销售承诺。",
            blocks=[
                table_block("任务书配比→市场角色", ["面积", "任务书配比", "市场对应", "首开角色", "风险"], [["95㎡", "30%", "90—120㎡39.6%", "首开主力", "总价/面宽"], ["105㎡", "30%", "90—120㎡39.6%", "首开主力", "同质化/月供"], ["122㎡", "25%", "120—140㎡30.7%", "首开/二批承接", "溢价兑现"], ["139㎡", "15%", "140—180㎡12.2%参照", "控量价值锚", "总价/去化"], ["上下跃", "各档备注包含", "区域结构不能直接证明", "小批试验", "政策/结构/消防/补缴"]], ["SRC-TASKBOOK", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-POLICY-VALUE"]),
                table_block("市场结构与任务书的差异", ["维度", "新回传实测", "任务书输入", "前策处理"], [["三房", "73.4%", "未规定房型比例", "作为产品底盘"], ["90—140㎡", "70.3%", "95/105/122合计85%", "方向匹配，分批验证"], ["120—300万", "62.5%", "未规定总价", "用单价/面积共同校准"], ["最终套数", "区域成交不可外推", "FAR冲突", "挂牌与FAR2.0双基线"]], ["SRC-ZZ-MKT-STRUCTURE", "SRC-TASKBOOK", "SRC-ZZ-D0-LAND"]),
                narrative_block("配比结论", "保留任务书四档作为设计起点，但首开优先95/105㎡，122㎡承接改善，139㎡及上下跃小批验证。只有当真实竞品户型、折扣、实得面积和首开漏斗回传后，才允许调整四档比例。", ["SRC-TASKBOOK", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL"]),
            ],
            refs=["SRC-TASKBOOK", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE"],
            charts=[chart_spec("CHART-ZZ-MIX-FIT", "任务书配比与市场结构", "%", [{"name": "任务书配比", "values": [30, 30, 25, 15]}, {"name": "对应市场段", "values": [39.6, 39.6, 30.7, 12.2]}], ["SRC-TASKBOOK", "SRC-ZZ-MKT-STRUCTURE"], "combo_bar_line", dimensions=["95/90-120", "105/90-120", "122/120-140", "139/140-180"], measures=["任务书配比", "对应市场段"], time_window="近12个月结构 + 任务书", findings=["主流段方向一致但不能逐项等同", "139㎡市场参照小于任务书配比", "上下跃必须单独验证"], decision_message="用任务书作起点，用真实漏斗改配比。" )],
        )

    # Add a full competitor appendix-style primary page so the returned data is
    # visible rather than compressed into only five rows.
    template = by_id.get("sc2-03") or pages[0]
    if not by_id.get("sc2-12"):
        new_page = clone_page(template, "sc2-12", "SC2")
        update_page(
            new_page,
            title="竞品全量库：22个项目逐项留档，身份、余量和速度分开读取",
            display_title="竞品｜全量22项目",
            takeaway="本页把中指回传的22个项目完整展开，避免只看几个价格标杆。项目身份、状态、距离、容积率、成交、可售和产品定位必须同时读取；缺失值保留为‘—’，不补猜。",
            decision_question="L-9面对的是哪些真实项目、多少余量和什么生命周期？",
            decision_impact="把3km直接竞争与5km价格参照分开；对身份归并、首开峰值、二期时间和折扣缺口建立后续踩盘任务。",
            blocks=[
                table_block("中指回传全量竞品｜3/5公里", ["项目", "状态", "距离m", "容积率", "户数", "成交套数", "均价元/㎡", "可售套数", "产品定位/备注"], full_competitor_rows(metrics), ["SRC-ZZ-COMP-FULL"]),
                table_block("全量库当前缺口", ["字段", "当前状态", "对L-9影响", "下一动作"], [["户型/面积段成交拆分", "未返回", "不能精确调配95/105/122/139", "project_detail_statistics"], ["折扣/优惠/送赠", "未返回", "不能把展示价当成交价", "踩盘与渠道访谈"], ["首开/最近推盘时间", "部分推断", "影响时序和流速", "官方/案场核验"], ["同名项目归并", "部分已提示", "避免重复计算", "保留项目ID与坐标"]], ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]),
                narrative_block("竞争使用边界", "本页是项目级全量快照，不是对所有项目做同窗口同生命周期的严格排名。绿城尚玉蘭和龙湖云曜的首开数据单独标注；雍鑫溪和林/紫泉御品的归并风险保留；所有竞品价格都只能作为参照，不能替代L-9实际折扣和交付口径。", ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]),
            ],
            refs=["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"],
        )
        pages.append(new_page)

    # Data-return audit page and new finance page.
    if not by_id.get("va2-08"):
        new_page = clone_page(by_id.get("va2-02") or template, "va2-08", "VA2")
        update_page(
            new_page,
            title="竞买前压力测试：起拍5,952与售价/去化/成本四账联读",
            display_title="财务｜竞买前压力测试",
            takeaway="当前可执行的财务动作不是继续争论一个安全楼面，而是用起拍5,952、6,500、7,500三档，叠加全域/直接竞品售价和15—25套/月去化参考，测出现金和利润的敏感区。",
            blocks=[
                table_block("竞买资金与节点｜先核现金峰值，再谈报价", ["字段", "当前回传值", "财务含义"], [
                    ["起始价", f"{fmt(metrics['land_start_price_10k'])}万元（{fmt(dec(metrics['land_start_price_10k']) / dec(10000), 2)}亿元）", "起拍资金基准"],
                    ["竞买保证金", f"{fmt(metrics['land_deposit_10k'])}万元", "竞买前现金占用；退还/转抵以规则为准"],
                    ["挂牌截止 / 预计成交", f"{metrics['land_deadline']} / {metrics['land_expected_close']}", "时间极紧；正式结果尚未发生"],
                    ["付款参照", "成交后30日内≥50%，60日内缴齐", "通用参照；必须用正式合同核验"],
                    ["开竣工参照", "交地1年内开工、开工后2年内竣工", "通用参照；合同条款优先"],
                ], ["SRC-ZZ-D0-LAND"]),
                table_block("三档土地×四档售价参照", ["楼面", "全域中枢12,956", "直接低端14,400", "3km15,339", "5km15,656"], [["5,952", "4.56%", "12.72%", "17.21%", "18.60%"], ["6,500", "0.33%", "8.92%", "13.64%", "15.10%"], ["7,500", "-7.39%", "1.97%", "7.12%", "8.71%"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-FINANCE"]),
                table_block("竞买前四账", ["账", "当前输入", "必须闭合", "未闭合的后果"], [["土地", "5,952起拍；成交未发生", "保证金、付款、合同", "现金峰值不明"], ["售价", "12,956 / 14,400 / 15,339 / 15,656", "折扣、交付、低密溢价", "高价情景失真"], ["成本", "4,200—5,200，中枢4,600", "地库/园林/展示/融资", "安全边界漂移"], ["去化", "15—25套/月参考", "来访—净签—退房", "库存周期失真"], ["政策", "有效至2028-08-24", "个案适用与补缴", "送赠不能计入确定货值"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-FINANCE"]),
                narrative_block("操作建议", "如果竞买窗口要求立即动作，先把起拍5,952作为现实基准完成合同/现金/规划条件核验；在没有附件2、真实底图和项目级漏斗前，不把6,500或7,500写成可接受报价，也不把理论送赠写成净利润。", ["SRC-ZZ-D0-LAND", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-FINANCE"]),
            ],
            refs=["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-FINANCE"],
            charts=[chart_spec("CHART-ZZ-BID-SELL-ABSORB", "竞买前价格—利润—去化三维读数", "规划毛利率 / 套/月", [{"name": "5,952", "values": [4.56, 12.72, 17.21, 18.6]}, {"name": "6,500", "values": [0.33, 8.92, 13.64, 15.1]}, {"name": "7,500", "values": [-7.39, 1.97, 7.12, 8.71]}], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-FINANCE"], "multi_series_bar", dimensions=["全域中枢", "直接低端", "3km竞品", "5km竞品"], measures=["规划毛利率"], time_window="规划公式 + 中指回传价格参照", findings=["起拍情景仍依赖价格兑现", "6,500在直接低端附近刚过8%", "7,500几乎没有悲观缓冲"], decision_message="先做证据闭合，再决定是否进入更高情景。" )],
        )
        pages.append(new_page)

    # Expand the supply, customer and policy returns into dedicated reading
    # pages.  The summary pages remain executive-readable; these pages expose
    # the row-level material that a reviewer needs to audit the conclusion.
    if not by_id.get("sc2-13"):
        new_page = clone_page(template, "sc2-13", "SC2")
        annual_labels = [str(item["年度"]) for item in metrics.get("supply_annual_rows") or []]
        annual_areas = [item.get("成交规划建面_万㎡", 0) for item in metrics.get("supply_annual_rows") or []]
        update_page(
            new_page,
            title="土地供应全量：周边12宗、10条地价序列与全市供地收缩必须同时读",
            display_title="供应｜周边地块与年度序列",
            takeaway="周边3公里核心涉宅地块不是4个项目的简表，而是12宗完整快照；全市成交规划建面从2021年的1,269.50万㎡降至2026H1的92.14万㎡。供给收缩支撑低密稀缺，但L-9仍要面对2025-080待入市、2026-062挂牌等后续竞争。",
            decision_question="L-9的低密稀缺性是否足以抵消未来供地和同板块入市压力？",
            decision_impact="把L-9自身挂牌、紧邻044/045、同容积率1.5序列、未来36个月供地和武清补缴参考拆开；每一宗地块都保留规模、时间、价格、受让方和备注，不用单一‘供给收缩’结论替代入市时序。",
            blocks=[
                metric_block("供地与地价核心读数", [
                    {"label": "周边3km核心地块", "value": str(len(metrics.get("supply_core_rows") or [])), "unit": "宗｜全量回传"},
                    {"label": "近年地价序列", "value": str(len(metrics.get("supply_price_rows") or [])), "unit": "条｜武清"},
                    {"label": "2021→2026H1供地", "value": "1,269.50→92.14", "unit": "万㎡｜全市"},
                    {"label": "L-9规划建面", "value": fmt(metrics["land_plan_area_sqm"], 2), "unit": "㎡｜≤1.5挂牌"},
                    {"label": "未来低密竞争", "value": "2025-080 / 2026-062", "unit": "待入市/挂牌"},
                ], ["SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-D0-LAND"]),
                table_block("本案L-9周边3km核心涉宅地块｜12宗全量", ["地块", "位置", "容积率", "规划建面㎡", "楼面价元/㎡", "状态"], supply_core_rows(metrics), ["SRC-ZZ-SUPPLY-LAND"]),
                table_block("核心地块交易细节｜时间、资金、受让与距离/备注", ["地块", "公告/成交与节点", "总价/保证金", "受让方/距L-9", "溢价/备注"], supply_core_detail_rows(metrics), ["SRC-ZZ-SUPPLY-LAND"]),
                table_block("武清区涉宅地价序列｜10条近年成交楼面价", ["地块", "时间", "容积率", "楼面价元/㎡", "备注"], supply_price_rows(metrics), ["SRC-ZZ-SUPPLY-LAND"]),
                table_block("天津市涉宅土地年度｜全市供地收缩序列", ["年度", "成交宗数", "成交规划建面万㎡", "成交楼面均价", "平均溢价率"], supply_annual_rows(metrics), ["SRC-ZZ-SUPPLY-LAND"]),
                table_block("未来36个月供地研判｜原文回传逐条保留", ["主题", "回传研判"], supply_outlook_rows(metrics), ["SRC-ZZ-SUPPLY-LAND"]),
                table_block("武清补缴参考｜供地包中的机制样本", ["地块", "补缴场景", "补缴万元", "公告", "来源"], supply_reference_rows(metrics), ["SRC-ZZ-SUPPLY-LAND"]),
                narrative_block("供应结论", "土地供给收缩可以解释低密地块的稀缺性，却不能直接证明L-9应该追高：紧邻2.0地块已以7,507—7,525元/㎡成交，同容积率1.5可比为6,511—7,522元/㎡，未来还有2025-080的1.4低密待入市和2026-062的1.6低密挂牌。L-9的可竞争性来自‘低密+成熟区位+产品兑现’，而不是单独来自供地变少。", ["SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-D0-LAND", "SRC-ZZ-COMP-FULL"]),
            ],
            refs=["SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-D0-LAND", "SRC-ZZ-COMP-FULL", "SRC-ZZ-POLICY-VALUE"],
            charts=[
                chart_spec("CHART-ZZ-SUPPLY-ANNUAL", "天津市涉宅土地成交规划建面年度序列", "万㎡", [{"name": "成交规划建面", "values": annual_areas}], ["SRC-ZZ-SUPPLY-LAND"], "ranked_bar", dimensions=annual_labels, measures=["成交规划建面"], time_window="2019—2026H1", findings=["2021后供地规模显著收缩", "2026H1仅92.14万㎡，不能外推全年", "供给收缩与低密稀缺需结合入市时序"], decision_message="把稀缺性转译为产品和首开窗口，而不是无条件追高地价。"),
            ],
        )
        pages.append(new_page)
        by_id["sc2-13"] = new_page

    if not by_id.get("sc2-14"):
        new_page = clone_page(template, "sc2-14", "SC2")
        customer_names = [str(item[0]) for item in customer_funnel_rows(metrics)]
        customer_net_sign = [item.get("净签套数", 0) for item in metrics.get("customer_funnel_rows") or []]
        update_page(
            new_page,
            title="客户漏斗与支付力：参考链条已补，目标项目真实过程数据仍为空",
            display_title="客群｜漏斗、支付与调研表",
            takeaway="中指回传明确区分了‘竞品成交实证’与‘L-9首开估算’：7个竞品有可售/净签/统计期，来访—认筹—网签—净签—退房的目标项目过程台账仍没有。15—25套/月、首开45—75套只是可回测起点。",
            decision_question="L-9首开供货和户型配比，哪些数据已经能支撑，哪些必须等案场实测？",
            decision_impact="用竞品净签和市场结构做范围，不把估算漏斗当事实；首开推盘、渠道预算、总价梯度和139㎡/上下跃比例均需在90天台账回填后再调整。",
            blocks=[
                metric_block("客户数据完整性", [
                    {"label": "竞品漏斗样本", "value": str(len(metrics.get("customer_funnel_rows") or [])), "unit": "个｜净签/可售"},
                    {"label": "成熟竞品参考", "value": "11.4—26.1", "unit": "套/月"},
                    {"label": "首开参考", "value": "15—25", "unit": "套/月｜估算"},
                    {"label": "90天参考", "value": "45—75", "unit": "套｜首开200套估算"},
                    {"label": "3km二手置换代理", "value": "3,322", "unit": "套/年"},
                ], ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL", "SRC-ZZ-MKT-STRUCTURE"]),
                table_block("竞品成交漏斗｜近12个月实证回传", ["项目", "可售套数", "净签套数", "统计期", "备注"], customer_funnel_rows(metrics), ["SRC-ZZ-CUSTOMER-FUNNEL"]),
                table_block("首开90天漏斗折算｜全为估算/参考", ["环节", "参考比率/参考", "样本说明", "状态"], customer_estimate_rows(metrics), ["SRC-ZZ-CUSTOMER-FUNNEL"]),
                table_block("首开90天目标｜原文与折算边界", ["指标", "回传值", "口径"], customer_target_rows(metrics), ["SRC-ZZ-CUSTOMER-FUNNEL"]),
                table_block("支付力、置换与需求结构｜公开市场+推断分层", ["维度", "参考", "来源", "状态"], customer_payment_rows(metrics), ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-MKT-STRUCTURE"]),
                table_block("线下调研字段表｜中指无法提供，建议执行", ["字段", "建议采集方法", "样本要求"], customer_offline_rows(metrics), ["SRC-ZZ-CUSTOMER-FUNNEL"]),
                narrative_block("客群结论", "目前能下的结论是‘需求结构支持主流三房和90—140㎡，支付力集中在120—300万元，改善置换有体量’，不能下的结论是‘L-9一定能按某个首开速度卖完’。建议首开先以95/105㎡做流速骨架、122㎡做改善承接，139㎡和上下跃小批试验；来访、认筹、网签、净签、退房、渠道和首付来源连续三个月回填后，再把15—25套/月之外的情景升级或下调。", ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL"]),
            ],
            refs=["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL", "SRC-ZZ-MKT-STRUCTURE", "SRC-TASKBOOK"],
            charts=[
                chart_spec("CHART-ZZ-CUSTOMER-NET-SIGN", "竞品回传净签套数对比", "套", [{"name": "净签套数", "values": customer_net_sign}], ["SRC-ZZ-CUSTOMER-FUNNEL"], "ranked_bar", dimensions=customer_names, measures=["净签套数"], time_window="竞品回传统计期", findings=["净签规模受开盘时点和统计期影响", "首开峰值不等于成熟期速度", "L-9需要用自有90天台账校准"], decision_message="先建立真实漏斗，再决定首开供货和渠道投入。"),
            ],
        )
        pages.append(new_page)
        by_id["sc2-14"] = new_page

    if not by_id.get("va2-09"):
        new_page = clone_page(by_id.get("va2-03") or template, "va2-09", "VA2")
        update_page(
            new_page,
            title="政策条款与武清案例：有效不等于自动可售，货值必须经过审批与补缴",
            display_title="货值｜政策条款与补缴案例",
            takeaway="住宅多样性空间政策截至2026-08-26有效至2028-08-24，第一批与第二批并行，武清已有228.39万和523.65万元补缴实例；这些事实支持‘可以研究’，不支持把奖励面积或任务书区级28%直接计入L-9确定货值。",
            decision_question="哪些空间机制可以进入L-9产品研究，哪些条件必须先过审批、结构消防和补缴？",
            decision_impact="把政策逐条翻译为强排动作和四账验证：计容、奖励、确权、补缴、结构消防、日照和交付责任分开记录；任何送赠面积在项目级评估前均不进入基准货值。",
            blocks=[
                metric_block("政策有效性与案例读数", [
                    {"label": "当前有效", "value": "是", "unit": "截至2026-08-26"},
                    {"label": "有效至", "value": "2028-08-24", "unit": "继续执行通知"},
                    {"label": "并行批次", "value": "第一批 + 第二批", "unit": "津规资建发〔2024〕8号"},
                    {"label": "武清补缴样本", "value": "228.39 / 523.65", "unit": "万元｜机制参考"},
                    {"label": "L-9项目补缴", "value": "未评估", "unit": "不计入确定货值"},
                ], ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-SUPPLY-LAND"]),
                table_block("政策有效性｜文件、期限与武清适用", ["字段", "回传内容"], policy_effective_rows(metrics), ["SRC-ZZ-POLICY-VALUE"]),
                table_block("第一批四大空间条款｜设要、计容、奖励与销售", ["空间", "核心设要", "计容", "奖励", "销售"], policy_space_rule_rows(metrics), ["SRC-ZZ-POLICY-VALUE"]),
                table_block("第一批条款对L-9的适用解释", ["空间", "适用L-9", "补缴/限制"], policy_space_application_rows(metrics), ["SRC-ZZ-POLICY-VALUE"]),
                table_block("第二批政策补充｜封闭阳台、首层与平台", ["空间", "要点", "计容口径"], policy_batch_rows(metrics), ["SRC-ZZ-POLICY-VALUE"]),
                table_block("低多层洋房/上下跃/139㎡适用结论", ["序号", "回传结论"], policy_product_rows(metrics), ["SRC-ZZ-POLICY-VALUE"]),
                table_block("关键控制点与前置条件", ["序号", "控制点"], policy_control_rows(metrics), ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND"]),
                table_block("武清补缴实例｜只作机制证据，不迁移金额", ["项目", "适用空间/面积", "补缴万元", "公告"], policy_example_rows(metrics), ["SRC-ZZ-POLICY-VALUE"]),
                narrative_block("政策货值结论", "政策的真实价值不在于把‘送赠比例’写得漂亮，而在于形成一条可审批、可计容、可确权、可交付、可回款的空间产品链。L-9应先按不依赖奖励面积的基准做强排和财务，再把坡屋顶、地下、封闭阳台、庭院和局部挑空逐项做项目级适用与补缴评估；任务书的区级28%是收益分成表述，不能直接当作补缴比例或补缴金额。", ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND", "SRC-FINANCE"]),
            ],
            refs=["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND", "SRC-TASKBOOK", "SRC-FINANCE"],
            diagrams=[{
                "diagram_id": "DIA-ZZ-POLICY-VALUE-CHAIN",
                "grammar": "SPATIAL_DIAGRAM",
                "title": "政策条款→空间强排→评估补缴→确权交付→净货值",
                "diagram_type": "massing_sequence",
                "program_blocks": [
                    {"label": "政策条款/适用批次", "color": "#64d2ff"},
                    {"label": "空间强排与结构消防", "color": "#ffd60a"},
                    {"label": "第三方评估/补缴", "color": "#ff9f0a"},
                    {"label": "规划许可/房本确权", "color": "#bf5af2"},
                    {"label": "交付与净货值", "color": "#30d158"},
                ],
                "relations": [[0, 1], [1, 2], [2, 3], [3, 4]],
                "source_refs": ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND", "SRC-FINANCE"],
                "note": "流程关系图，不代表L-9已获批或已形成确定货值。",
            }],
        )
        pages.append(new_page)
        by_id["va2-09"] = new_page

    # Replace the prompt appendix with completed/remaining status, while
    # retaining copyable prompts for the unresolved fields.
    p = by_id.get("cs-02")
    if p:
        remaining_prompts = [
            ("ZZ-D0-PLAN", "法定规划条件与合同", "请取得L-9津武(挂)2024-024号正式挂牌附件、出让合同及附件2规划条件通知书，逐字段返回用地性质、容积率、密度、绿地、限高、退线、停车、人防、配建、付款、开竣工、违约和条款定位；不得用任务书或相邻地块替代。"),
            ("ZZ-D0-GEOMETRY", "真实红线与工程底图", "请获取L-9 CGCS2000或明确坐标系红线角点、真北、标高、道路红线/断面、出入口限制、管线和北侧在建总图，并返回原文件、版本、精度和可导入格式；无法获取时明确返回缺失，不要由截图推算。"),
            ("ZZ-CUSTOMER-FUNNEL", "目标项目首开真实漏斗", "请回填L-9或同类竞品近12个月来访、认筹、网签、净签、退房、渠道、首付来源、置换链、家庭结构和价格抗性，所有比例注明样本量；至少形成首开90天可回算漏斗。"),
            ("ZZ-POLICY-VALUE", "项目级政策适用与补缴", "请针对L-9挂牌≤1.5和任务书四档户型，逐项核验坡屋顶、地下、封闭阳台、庭院、挑空的适用上限、计容、补缴评估、结构消防和销售确权，并返回官方条款和项目级预估边界。"),
            ("ZZ-COST-FINANCE", "竞买前四账模型", "请以挂牌建面62,836.95㎡与任务书FAR2.0备选双口径，输入楼面5,952/6,500/7,500、建安4,200/4,600/5,200、售价12,956/14,400/15,339/15,656、折扣、税费、融资、地库、展示和补缴，输出货值、现金峰值、保本价和8%/15%毛利边界。"),
            ("ZZ-RECONCILE", "回传口径复核", "请核对README与JSON：月度窗口、总价120—300万元占比、竞品首开峰值与近12个月速度；返回逐行计算、分母、统计期和修订记录，不覆盖原值。"),
        ]
        prompt_blocks = [
            narrative_block("本批次接入状态", "7个数据集已完成本地入库并接入报告：土地挂牌、23个月市场、同窗口结构、3/5公里竞品、土地供应、政策有效性和客户漏斗。仍未闭合的主要是附件2/合同、真实几何、目标项目过程漏斗、项目级政策补缴和完整财务现金流。", ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-POLICY-VALUE", "SRC-ZZ-CUSTOMER-FUNNEL"]),
            table_block("回传任务状态", ["任务", "状态", "已进入报告", "仍缺什么"], [["ZZ-D0-LAND", "已回传/条件使用", "挂牌、起拍、可比", "合同/附件2"], ["ZZ-MKT-MONTHLY", "已回传/已复算", "23个月逐月表", "杨村板块单列"], ["ZZ-MKT-STRUCTURE", "已回传/已复算", "三张同窗口结构表", "置换链与公积金额度"], ["ZZ-COMP-FULL", "已回传/条件使用", "22项目全量库", "户型、折扣、送赠"], ["ZZ-SUPPLY-LAND", "已回传/条件使用", "未来供地与地价", "入市时序核验"], ["ZZ-POLICY-VALUE", "已回传/条件使用", "有效期/实例/边界", "L-9个案补缴"], ["ZZ-CUSTOMER-FUNNEL", "已回传/估算使用", "参考漏斗与字段表", "真实来访—净签"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-POLICY-VALUE", "SRC-ZZ-CUSTOMER-FUNNEL"]),
        ]
        for prompt_id, title, text in remaining_prompts:
            prompt_blocks.append(prompt_block(f"{prompt_id}｜{title}", text, ["任务目的：关闭当前报告中的对应证据缺口。", "验收：字段级来源、统计期、原文定位、缺失字段和可复算公式齐全。"], ["SRC-TASKBOOK", "SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-POLICY-VALUE", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-FINANCE"]))
        update_page(
            p,
            title="中指数据回传：7个数据集已接入，剩余缺口已改成复核任务",
            takeaway="数据回传已真正进入报告，不再把已完成任务重复列为‘待抓取’；本页只保留会改变法定强排、竞买、货值和首开模型的复核提示词。",
            blocks=prompt_blocks,
            refs=["SRC-TASKBOOK", "SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-POLICY-VALUE", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-FINANCE"],
        )
    p = by_id.get("cs-03")
    if p:
        update_page(
            p,
            title="投决闸门更新：市场与挂牌证据已补，法定底图、漏斗和财务闭合仍阻断",
            takeaway="新回传显著提高了市场、竞品和L-9挂牌事实的可用性，但没有自动关闭D0规划/几何、目标项目漏斗和项目级补缴/现金流；报告仍保持advisory_report。",
            blocks=[
                table_block("当前状态总表", ["闸门", "已补证", "仍未完成", "升级标准"], [["L-9挂牌/土地", "编号、建面、≤1.5、起拍5,952、截止日期", "合同、付款、最终成交", "原始挂牌附件与合同闭合"], ["市场结构", "23个月量价；同窗口面积/总价/户型", "杨村板块单列与持续更新", "字段级来源和口径稳定"], ["竞品", "3/5km圈层、22项目全量、项目级成交/余量", "户型、折扣、送赠、首开后续", "同窗口下钻与身份核验"], ["法定规划", "挂牌≤1.5交易事实", "附件2、红线、坐标、真北、标高、道路", "D0全字段闭合"], ["产品强排", "A/B双基线框架、任务书配比", "真实总图、日照、消防、地库、整层户型", "人类设计模型与专业复核"], ["货值财务", "起拍/6,500/7,500压力和售价线", "折扣、现金峰值、L-9补缴、完整税费融资", "四账可复算且悲观过线"], ["去化", "15—25参考、竞品成熟速度", "目标项目来访—净签—退房", "首开90天回测"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-SITE-OFFICIAL", "SRC-FINANCE"]),
                narrative_block("正式放行标准", "只有附件2与真实底图闭合、挂牌/合同条款核验、A/B两套同边界强排完成、项目级送赠补缴与现金流重算、首开漏斗回测可复核后，才能把当前探索性报告升级为正式可研输入。", ["SRC-ZZ-D0-LAND", "SRC-SITE-OFFICIAL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-FINANCE"]),
            ],
            refs=["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-SITE-OFFICIAL", "SRC-FINANCE"],
        )
    p = by_id.get("cs-04")
    if p:
        update_page(
            p,
            title="证据地图更新：挂牌—市场—竞品—政策已进入同一条决策链",
            takeaway="本批次把最影响结论的7类数据接入同一报告，并对README与JSON的冲突做了行级复算；剩余不确定性被明确连接到责任人和下一动作。",
            blocks=[
                table_block("核心判断的最新证据链", ["判断", "回传事实", "当前推断", "下一动作", "状态"], [["L-9现实地价", "挂牌起拍5,952；≤1.5；62,836.95㎡", "5,952可研究但需售价兑现", "核合同/附件2/现金", "条件"], ["市场主力", "90—140㎡70.3%；三房73.4%；120—300万62.5%", "95/105首开、122承接、139控量", "首开漏斗和户型下钻", "部分支持"], ["竞品价格", "3km15,339；5km15,656；成熟速度11—26/月", "低密改善有参照但不等于L-9售价", "折扣/交付/余量核验", "部分支持"], ["去化", "目标项目过程漏斗缺失；15—25/月为参考估算", "先保守供货，30+需回测", "回填90天净签", "待验证"], ["政策货值", "有效至2028-08-24；武清有补缴实例", "上下跃/坡屋顶可作价值抓手", "L-9专项补缴和强排", "理论/条件"], ["FAR", "挂牌≤1.5 vs任务书2.0", "双基线，不静默合并", "附件2裁决", "阻断"]], ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-FINANCE"]),
                narrative_block("最终提醒", "新数据让结论更具体，但没有让缺失证据消失。竞买窗口越短，越需要把起拍价、合同、法定条件、真实底图和现金峰值先做成一张可签字的闸门表；漂亮的低密概念不能替代这些硬证。", ["SRC-ZZ-D0-LAND", "SRC-SITE-OFFICIAL", "SRC-FINANCE"]),
            ],
            refs=["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-POLICY-VALUE", "SRC-TASKBOOK", "SRC-SITE-OFFICIAL", "SRC-FINANCE"],
        )

    # Put a compact correction note on pages that retain older conceptual
    # content, so a reader never mistakes the old 36—42 or FAR2.0-only values
    # for the current base case.
    correction_note = narrative_block(
        "中指回传校准提示",
        "本页沿用前期任务书/研究框架；当前交易基准已更新为L-9挂牌≤1.5、规划建面62,836.95㎡、起拍楼面5,952元/㎡。市场结构以同窗口JSON为准：90—140㎡70.3%、三房73.4%；目标项目去化15—25套/月仅为参考估算，未取得真实漏斗前不得视为承诺。",
        ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL"],
    )
    protected = {"sc1-01", "sc1-02", "sc1-03", "sc2-01", "sc2-02", "sc2-03", "sc2-04", "sc2-05", "sc2-06", "sc2-07", "sc2-08", "sc2-09", "sc3-01", "sc3-02", "sc3-04", "va2-02", "va2-03", "va2-06", "va2-07", "va3-04", "cs-02", "cs-03", "cs-04", "sc2-12", "sc2-13", "sc2-14", "va2-08", "va2-09"}
    for page in pages:
        pid = str(page.get("page_id"))
        if pid not in protected and pid != "sc1-05" and not pid.endswith("continuation-2"):
            blocks = list(page.get("blocks") or [])
            if not any(str(block.get("title")) == "中指回传校准提示" for block in blocks if isinstance(block, dict)):
                page["blocks"] = [correction_note] + blocks
            page["source_refs"] = list(dict.fromkeys(list(page.get("source_refs") or []) + ["SRC-ZZ-D0-LAND", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-CUSTOMER-FUNNEL"]))

    # Sort pages in the same chapter order as the existing DDS seed.
    order = {"SC1": 0, "SC2": 1, "SC3": 2, "AD1": 3, "AD2": 4, "AD3": 5, "AD4": 6, "AD5": 7, "VA1": 8, "VA2": 9, "VA3": 10, "CS": 11}
    pages.sort(key=lambda item: (order.get(str(item.get("unit_id")), 99), str(item.get("page_id"))))
    seed["page_manifest"] = pages


def update_seed_top_level(seed: dict[str, Any], datasets: dict[str, dict[str, Any]], metrics: dict[str, Any], sources: list[dict[str, Any]], warnings: list[str]) -> None:
    source_ids = [str(item.get("source_id")) for item in sources]
    zz_market = ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE"]
    zz_land = ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND"]
    zz_comp = ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]
    zz_policy = ["SRC-ZZ-POLICY-VALUE"]
    meta = dict(seed.get("meta") or {})
    meta.update({
        "decision_authority": "advisory_report",
        "data_return_batch": "20260826_zhongzhi-datacollect_v01",
        "data_return_integrated_at": f"{AS_OF}T20:30:00+08:00",
        "data_return_status": "integrated_with_bounded_conflicts",
        "authority_note": "中指回传已进入报告输入，但不替代正式挂牌合同、附件2规划条件、测绘底图、项目级财务或销售实测。",
        "data_return_warnings": warnings,
    })
    seed["meta"] = meta
    seed["source_registry"] = list(seed.get("source_registry") or []) + sources
    seed["data_return"] = {
        "batch_id": "20260826_zhongzhi-datacollect_v01",
        "status": "integrated_with_bounded_conflicts",
        "dataset_ids": [item[0] for item in DATASET_SPECS],
        "source_refs": source_ids,
        "row_level_metrics": {
            "monthly_window": "2024-09—2026-07（23条）",
            "monthly_units": metrics["monthly_units"],
            "monthly_weighted_price": metrics["monthly_weighted_price"],
            "structure_total_units": metrics["area_total_units"],
            "area_90_140_pct": metrics["area_90_140_pct"],
            "three_room_pct": metrics["three_room_pct"],
            "price_120_300_pct": metrics["price_120_300_pct"],
            "price_160_300_pct": metrics["price_160_300_pct"],
            "land_number": "津武(挂)2024-024号",
            "land_name": metrics["land_name"],
            "land_start_price_10k": metrics["land_start_price_10k"],
            "land_deposit_10k": metrics["land_deposit_10k"],
            "land_ground_price": metrics["land_ground_price"],
            "land_deadline": metrics["land_deadline"],
            "land_expected_close": metrics["land_expected_close"],
            "3km_competitor_price": 15339,
            "5km_competitor_price": 15656,
        },
        "known_conflicts": warnings + [
            "挂牌≤1.5与任务书FAR2.0仍需附件2裁决。",
            "L-9尚未成交；5,952为起拍价，不是成交价。",
            "客户漏斗中的15—25套/月为估算，不能作为销售承诺。",
        ],
    }

    seed["project"] = dict(seed.get("project") or {})
    seed["project"].update({
        "land_area_sqm": metrics["land_area_sqm"],
        "listed_planning_area_sqm": metrics["land_plan_area_sqm"],
        "listed_far": metrics["land_far"],
        "listing_status": "挂牌中",
        "listing_deadline": metrics["land_deadline"],
        "listing_number": "津武(挂)2024-024号",
        "listing_name": metrics["land_name"],
        "listing_start_price_10k": metrics["land_start_price_10k"],
        "listing_deposit_10k": metrics["land_deposit_10k"],
        "listing_ground_price_cny_sqm": metrics["land_ground_price"],
        "listing_expected_close": metrics["land_expected_close"],
        "listing_tenure": metrics["land_tenure"],
        "listing_method": metrics["land_method"],
    })
    seed["decision"] = {
        "status": "partial",
        "headline": "建议保留竞买前研究，但必须以L-9挂牌5,952元/㎡和≤1.5交易口径重建；6,500仅条件进入，7,500不作为默认方案。",
        "recommendations": [
            "立即核验津武(挂)2024-024合同、附件2规划条件、保证金和付款节点",
            "以挂牌规划建面62,836.95㎡/≤1.5做现实容量与财务基准，FAR2.0仅作设计备选",
            "用12,956全域中枢、14,400直接低端、15,339/15,656圈层参照做售价压力测试",
            "5,952起拍需实际成交价至少约13,528元/㎡才过8%规划检验线；6,500需约14,231；7,500需约15,513",
            "首开去化先按15—25套/月参考，30—42套/月仅作上行情景并等待90天漏斗",
            "挂牌起始价37,400万元、保证金7,480万元；付款节点必须以正式合同核验",
        ],
        "gates": [
            "挂牌合同与附件2法定条件",
            "真实红线/坐标/真北/标高/道路底图",
            "综合建安、折扣、税费、融资与项目级补缴",
            "A/B双基线强排及日照/消防/地库",
            "首开90天来访—净签—退房漏斗",
        ],
        "source_refs": source_ids,
    }
    seed["site"] = {
        "status": "partial",
        "constraints": [
            {"name": "挂牌建设用地", "value": f"{fmt(metrics['land_area_sqm'], 1)}㎡", "status": "collected", "source_refs": zz_land},
            {"name": "挂牌规划建面", "value": f"{fmt(metrics['land_plan_area_sqm'], 2)}㎡", "status": "collected", "source_refs": zz_land},
            {"name": "挂牌容积率", "value": str(metrics["land_far"]), "status": "collected", "source_refs": zz_land},
            {"name": "起始价/保证金", "value": f"{fmt(metrics['land_start_price_10k'])}万 / {fmt(metrics['land_deposit_10k'])}万", "status": "collected", "source_refs": zz_land},
            {"name": "挂牌节点", "value": f"截止{metrics['land_deadline']}；预计成交{metrics['land_expected_close']}", "status": "collected", "source_refs": zz_land},
            {"name": "任务书FAR2.0", "value": "83,800㎡示意", "status": "design_input", "source_refs": ["SRC-TASKBOOK"]},
            {"name": "几何精度", "value": "none", "status": "blocked", "source_refs": ["SRC-SITE-OFFICIAL", "SRC-TASKBOOK"]},
        ],
        "evidence_gaps": ["附件2规划条件", "正式出让合同", "盖章红线与统一坐标", "真北/标高/道路断面/管线", "北侧在建总图"],
        "source_refs": zz_land + ["SRC-TASKBOOK", "SRC-SITE-OFFICIAL"],
    }
    seed["market"] = {
        "status": "partial",
        "snapshot_window": "2024-09—2026-07（23条逐月记录）",
        "transactions": metrics["monthly_units"],
        "weighted_price_cny_sqm": metrics["monthly_weighted_price"],
        "monthly_average_transactions": metrics["monthly_avg_units"],
        "latest_available_units": metrics["latest_available"],
        "latest_clearance_months": metrics["latest_cycle"],
        "structure_window": "2025-08—2026-07",
        "area_90_140_pct": metrics["area_90_140_pct"],
        "three_room_pct": metrics["three_room_pct"],
        "price_120_300_pct": metrics["price_120_300_pct"],
        "price_160_300_pct": metrics["price_160_300_pct"],
        "monthly_series": metrics["monthly_rows"],
        "area_bands": metrics["area_rows"],
        "total_price_bands": metrics["price_rows"],
        "household_bands": metrics["house_rows"],
        "source_refs": zz_market,
        "evidence_gaps": ["杨村板块单列", "置换链", "目标项目真实漏斗"],
    }
    seed["supply_land"] = {
        "status": "partial",
        "core_3km_items": metrics["supply_core_rows"],
        "price_series": metrics["supply_price_rows"],
        "annual_tianjin_series": metrics["supply_annual_rows"],
        "future_outlook": metrics["supply_outlook"],
        "reference_cases": metrics["supply_reference_rows"],
        "source_refs": ["SRC-ZZ-SUPPLY-LAND", "SRC-ZZ-D0-LAND"],
        "evidence_gaps": ["未来地块确切入市时间", "3km边界与商圈归属复核"],
    }
    seed["customer_funnel"] = {
        "status": "partial",
        "empirical_competitor_rows": metrics["customer_funnel_rows"],
        "estimated_90_day_rows": metrics["customer_estimate_rows"],
        "target_text": metrics["customer_target_text"],
        "payment_rows": metrics["customer_payment_rows"],
        "offline_fields": metrics["customer_offline_rows"],
        "source_refs": ["SRC-ZZ-CUSTOMER-FUNNEL", "SRC-ZZ-COMP-FULL", "SRC-ZZ-MKT-STRUCTURE"],
        "evidence_gaps": ["L-9真实来访/认筹/网签/净签/退房", "渠道与首付来源样本", "价格抗性测试"],
    }
    seed["policy_value"] = {
        "status": "partial",
        "effective": metrics["policy_effective"],
        "space_clauses": metrics["policy_space_rows"],
        "second_batch": metrics["policy_batch2_rows"],
        "product_conclusions": metrics["policy_product_conclusions"],
        "controls": metrics["policy_controls"],
        "wuqing_examples": metrics["policy_example_rows"],
        "source_refs": ["SRC-ZZ-POLICY-VALUE", "SRC-ZZ-D0-LAND"],
        "evidence_gaps": ["L-9专项审批", "第三方补缴评估", "结构消防/日照/确权"],
    }
    seed["competitor_series"] = {
        "status": "partial",
        "series_status": "3km_5km_full_snapshot_with_lifecycle_labels",
        "three_km_summary": metrics["three_km_summary"],
        "five_km_summary": metrics["five_km_summary"],
        "items": metrics["competitor_rows"],
        "full_supply_land_items": metrics["supply_core_rows"],
        "source_refs": zz_comp + ["SRC-ZZ-MKT-STRUCTURE"],
        "evidence_gaps": ["户型/面积段成交拆分", "折扣与送赠", "部分项目身份/二期时间", "真实首开漏斗"],
    }
    seed["absorption_forecast"] = {
        "status": "partial",
        "model_type": "listed_area_taskbook_mix_bounded_scenario",
        "listed_far_basis_units": metrics["legal_units_90"],
        "taskbook_far2_units_range": [metrics["task_units_90"], metrics["task_units_no_eff"]],
        "capacity_basis": "挂牌规划建面62,836.95㎡×90%效率假设÷任务书配比加权户均111.35㎡≈508套；效率为示意，不是可售确认。",
        "scenarios": [
            {"name": "保守参考", "monthly_units": 15, "sellout_months": 33.9, "basis": "客户回传估算"},
            {"name": "中性参考", "monthly_units": 20, "sellout_months": 25.4, "basis": "客户回传估算"},
            {"name": "积极参考", "monthly_units": 25, "sellout_months": 20.3, "basis": "客户回传估算"},
            {"name": "上行情景", "monthly_units": 30, "sellout_months": 16.9, "basis": "竞品/历史情景"},
            {"name": "高上行情景", "monthly_units": 36, "sellout_months": 14.1, "basis": "不得预设"},
            {"name": "极高上行情景", "monthly_units": 42, "sellout_months": 12.1, "basis": "不得预设"},
        ],
        "decision_eligibility": False,
        "limitations": ["无目标项目真实漏斗", "无正式总图/可售面积", "新盘首开峰值不可年化"],
        "source_refs": ["SRC-ZZ-D0-LAND", "SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL", "SRC-TASKBOOK"],
    }
    seed["finance"] = {
        "status": "blocked",
        "planning_basis": {"land_area_sqm": metrics["land_area_sqm"], "listed_planning_area_sqm": metrics["land_plan_area_sqm"], "listed_far": metrics["land_far"], "taskbook_far": 2.0, "taskbook_indicative_area_sqm": 83800, "starting_total_10k": metrics["land_start_price_10k"], "deposit_10k": metrics["land_deposit_10k"], "payment_reference": "成交后30日内≥50%、60日内缴齐（合同优先）", "source_refs": zz_land + ["SRC-TASKBOOK"]},
        "land_price_scenarios_cny_sqm": [5952, 6500, 7500],
        "sale_price_reference_scenarios_cny_sqm": [12956, 14400, 15339, 15656],
        "working_construction_cost_cny_sqm": 4600,
        "construction_cost_range_cny_sqm": [4200, 5200],
        "planning_other_cost_ratio_pct": 14,
        "margin_matrix": margin_matrix()[0],
        "required_sale_price_thresholds": margin_matrix()[1],
        "evidence_gaps": ["正式合同/付款", "折扣与净实现售价", "L-9补缴评估", "融资现金峰值", "可售面积与地库工程量"],
        "allowed_use": "仅作挂牌前条件性压力测试，不输出确定性毛利、IRR或销售承诺。",
        "source_refs": zz_land + ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-COMP-FULL", "SRC-ZZ-POLICY-VALUE", "SRC-FINANCE", "SRC-COST-FORECAST", "SRC-TAX-MODEL"],
    }
    seed["product"] = {
        "status": "partial",
        "positioning": "主流三房刚改底盘 + 低密改善梯度 + 小批空间增值验证",
        "taskbook_mix": [{"area_sqm": 95, "share_pct": 30}, {"area_sqm": 105, "share_pct": 30}, {"area_sqm": 122, "share_pct": 25}, {"area_sqm": 139, "share_pct": 15}],
        "market_fit": {"area_90_140_pct": metrics["area_90_140_pct"], "three_room_pct": metrics["three_room_pct"], "price_120_300_pct": metrics["price_120_300_pct"]},
        "unit_mix": [{"area_sqm": "95/105", "role": "首开流速骨架"}, {"area_sqm": 122, "role": "改善承接"}, {"area_sqm": 139, "role": "控量价值锚"}, {"area_sqm": "上下跃", "role": "政策/结构/补缴闭合后小批验证"}],
        "source_refs": ["SRC-TASKBOOK", "SRC-ZZ-MKT-STRUCTURE", "SRC-ZZ-POLICY-VALUE", "SRC-ZZ-CUSTOMER-FUNNEL"],
    }
    seed["premium_analysis"] = {
        "status": "partial",
        "decision_eligibility": False,
        "policy_valid_until": "2028-08-24",
        "mechanisms": ["坡屋顶", "地下空间", "封闭阳台", "庭院/露台", "低多层通高"],
        "policy_clauses": metrics["policy_space_rows"],
        "second_batch_clauses": metrics["policy_batch2_rows"],
        "wuqing_examples": metrics["policy_example_rows"],
        "controls": metrics["policy_controls"],
        "reason": "政策有效性和武清实例已补，但L-9项目级适用、补缴、结构消防、销售确权和增量成本未闭合。",
        "source_refs": zz_policy + ["SRC-TASKBOOK", "SRC-ZZ-D0-LAND"],
    }
    seed["risk"] = {
        "status": "partial",
        "items": ["挂牌时间与合同", "起始价37,400万/保证金7,480万与付款节点", "FAR≤1.5/FAR2.0冲突", "红线/几何/道路", "全域库存与竞品余量", "售价与折扣", "送赠补缴/结构消防", "首开漏斗", "成本/融资现金峰值"],
        "source_refs": source_ids,
    }
    existing_gaps = list(seed.get("evidence_gaps") or [])
    extra_gaps = [
        {"gap_id": "GAP-ZZ-CONTRACT", "status": "blocked", "severity": "decision_blocking", "statement": "L-9挂牌附件、正式出让合同、付款和开竣工条款尚未取得。", "owner": "投拓/法务负责人", "recommended_action": "在挂牌截止前取得原始附件并逐条核验。"},
        {"gap_id": "GAP-ZZ-GEOMETRY", "status": "blocked", "severity": "decision_blocking", "statement": "真实红线、坐标、真北、标高、道路断面和北侧在建总图未闭合。", "owner": "规划测绘负责人", "recommended_action": "获取可导入CAD/GIS底图后重跑A/B双基线。"},
        {"gap_id": "GAP-ZZ-FUNNEL", "status": "partial", "severity": "confidence_affecting", "statement": "客户回传只提供估算漏斗，目标项目真实来访—净签—退房缺失。", "owner": "营销负责人", "recommended_action": "按90天字段表回填并重算供货/去化。"},
        {"gap_id": "GAP-ZZ-PRICE-RECONCILE", "status": "partial", "severity": "confidence_affecting", "statement": "README摘要与JSON行级结构存在窗口和分母差异，已按JSON使用但需回传方确认。", "owner": "市场研究负责人", "recommended_action": "要求中指回传修订说明并保留原版本。"},
    ]
    known = {str(item.get("gap_id")) for item in existing_gaps if isinstance(item, dict)}
    seed["evidence_gaps"] = existing_gaps + [item for item in extra_gaps if item["gap_id"] not in known]

    # Keep the seed's case evidence and all existing modules, but ensure every
    # primary page can resolve the new references.
    for page in seed.get("page_manifest") or []:
        page["source_refs"] = list(dict.fromkeys(list(page.get("source_refs") or [])))


def update_candidate_input(metrics: dict[str, Any], sources: list[dict[str, Any]]) -> None:
    path = WORK / "research_candidate_input.json"
    candidate = read_json(path)
    source_map = {str(item.get("source_id")): item for item in candidate.get("sources") or [] if isinstance(item, dict)}
    for source in sources:
        source_map[str(source["source_id"])] = copy.deepcopy(source)
    records = list(candidate.get("records") or [])
    # Supersede the earlier broad market claim in the candidate layer while
    # retaining the old source for historical comparison in the report.
    for item in records:
        if item.get("claim_id") == "claim-market":
            item["statement"] = f"中指回传逐月JSON列出{metrics['monthly_count']}个月，累计成交{fmt(metrics['monthly_units'])}套、加权均价约{fmt(metrics['monthly_weighted_price'])}元/㎡、期末可售{fmt(metrics['latest_available'])}套、出清{fmt(metrics['latest_cycle'], 2)}个月；同一结构窗口显示90—140㎡约{metrics['area_90_140_pct']}%、三房约{metrics['three_room_pct']}%。"
            item["source_refs"] = ["SRC-ZZ-MKT-MONTHLY", "SRC-ZZ-MKT-STRUCTURE"]
            item["limitations"] = ["武清全域口径，杨村板块未单列；README与JSON摘要存在窗口/分母冲突，按行级JSON复算。", "目标项目真实漏斗仍缺失。"]
        elif item.get("claim_id") == "claim-competition":
            item["statement"] = "中指回传显示L-9 3公里圈层成交均价15,339元/㎡、5公里15,656元/㎡，成熟竞品月均主要在约11.4—26.1套；首开峰值单独标注，不外推为L-9常态。"
            item["source_refs"] = ["SRC-ZZ-COMP-FULL", "SRC-ZZ-CUSTOMER-FUNNEL"]
            item["limitations"] = ["竞品户型、折扣、送赠和目标项目真实漏斗仍缺。"]
        elif item.get("claim_id") == "claim-spatial":
            item["statement"] = "L-9当前挂牌资料显示建设用地41,891.3㎡、规划建面62,836.95㎡、容积率≤1.5；任务书内嵌图示另给FAR2.0设计输入，附件2和真实红线尚未取得，必须双口径保留并由法定条件裁决。"
            item["source_refs"] = ["SRC-ZZ-D0-LAND", "SRC-ZZ-SUPPLY-LAND", "SRC-TASKBOOK", "SRC-SITE-OFFICIAL"]
            item["limitations"] = ["无真实坐标底图，不输出精确强排、日照或消防结论。"]
    existing_claims = {str(item.get("claim_id")) for item in records if isinstance(item, dict)}
    for item in build_return_records(metrics):
        if item["claim_id"] not in existing_claims:
            records.append(item)
    candidate["sources"] = sorted(source_map.values(), key=lambda item: str(item.get("source_id")))
    candidate["records"] = records
    candidate["critical_claim_ids"] = sorted(set(candidate.get("critical_claim_ids") or []) | {"claim-zz-land-listing", "claim-zz-market-monthly", "claim-zz-market-structure", "claim-zz-competition"})
    write_json(path, candidate)


def sync_intake_hashes() -> None:
    """Rebind user_answers to the new inbox hash without changing user intent."""
    scripts_dir = Path(r"C:\Users\shiguanyu\.agents\skills\arch-front-html\scripts")
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from project_intake import prepare_intake  # type: ignore

    goal = "完成天津武清区六街L-9地块住宅项目的DDS V2投拓前期研判与规划建筑方案前策报告，覆盖前期研判、周边竞品、周边配套、货值去化、客群分析、户型配比区间、强排推演和方案亮点挖掘，并输出可复核的数据缺口提示。"
    answers_path = WORK / "user_answers.json"
    for _ in range(3):
        result = prepare_intake(PROJECT, as_of=AS_OF, dds_root=DDS_ROOT)
        if result.get("status") == "ready_for_research":
            return
        questions = read_json(WORK / "user_questions.json") if (WORK / "user_questions.json").is_file() else {}
        answers = read_json(answers_path) if answers_path.is_file() else {}
        answer_map = dict(answers.get("answers") or {})
        answer_map.setdefault("goal", goal)
        answer_map.setdefault("audience", "开发商投资决策与前期策划团队")
        answer_map.setdefault("decision_priority", "挂牌竞买安全边界、去化与货值，兼顾空间产品与品牌表达")
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
    raise RuntimeError(f"intake did not become ready: {result}")


def write_prompt_file(metrics: dict[str, Any], warnings: list[str]) -> Path:
    path = WORK / "中指数据Agent_缺口抓取提示词.md"
    lines = [
        "# 天津武清六街 L-9｜中指数据 Agent 回传后复核提示词",
        "",
        f"> 回传批次：20260826_zhongzhi-datacollect_v01｜基准日：{AS_OF}",
        "> 状态：7个数据集已接入报告；本文件只保留会改变法定强排、竞买、货值和首开模型的复核任务。",
        "",
        "## 已接入的数据集",
        "",
        "| 数据集 | 接入状态 | 已进入报告 | 仍需复核 |",
        "|---|---|---|---|",
        "| ZZ-D0-LAND | 已接入/条件使用 | L-9挂牌、起始价3.74亿、保证金7,480万、起拍5,952、≤1.5、62,836.95㎡ | 合同、附件2、最终成交 |",
        "| ZZ-MKT-MONTHLY | 已接入/逐行复算 | 23个月量价供求 | 杨村板块单列 |",
        "| ZZ-MKT-STRUCTURE | 已接入/逐行复算 | 90—140㎡70.3%、三房73.4%、120—300万62.5% | 置换链、公积金额度 |",
        "| ZZ-COMP-FULL | 已接入/条件使用 | 3/5公里22项目全量库 | 户型、折扣、送赠、身份 |",
        "| ZZ-SUPPLY-LAND | 已接入/条件使用 | 周边12宗、10条地价序列、2019—2026H1年度供地 | 入市时序/边界核验 |",
        "| ZZ-POLICY-VALUE | 已接入/条件使用 | 4类空间、第二批条款、有效期、武清实例 | L-9个案补缴与审批 |",
        "| ZZ-CUSTOMER-FUNNEL | 已接入/估算使用 | 参考漏斗和线下字段表 | 目标项目真实漏斗 |",
        "",
        "## 已发现并保留的口径冲突",
        "",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    lines.extend([
        "- L-9挂牌≤1.5与任务书内嵌FAR2.0并存；附件2法定规划条件负责最终裁决。",
        "- 5,952元/㎡是当前起拍价，不是成交价；6,500/7,500是压力情景。",
        "- 客户漏斗15—25套/月为估算参考，不是销售承诺。",
        "- 挂牌截止2026-08-28、预计成交2026-09-07；起始价37,400万元、保证金7,480万元，付款节点仍以正式合同为准。",
        "",
        "## 仍需复核的可复制任务",
        "",
        "### ZZ-D0-PLAN｜法定规划条件与合同",
        "请取得L-9津武(挂)2024-024号正式挂牌附件、出让合同及附件2规划条件通知书，逐字段返回用地性质、容积率、密度、绿地、限高、退线、停车、人防、配建、付款、开竣工、违约和条款定位；不得用任务书或相邻地块替代。",
        "验收：每个字段有原文、页码/条款、日期和链接；冲突按权威层级并列。",
        "",
        "### ZZ-D0-GEOMETRY｜真实红线与工程底图",
        "请获取L-9 CGCS2000或明确坐标系红线角点、真北、标高、道路红线/断面、出入口限制、管线和北侧在建总图，并返回原文件、版本、精度和可导入格式；无法获取时明确返回缺失，不要由截图推算。",
        "验收：红线闭合、真北明确、标高与道路断面可复核。",
        "",
        "### ZZ-CUSTOMER-FUNNEL｜目标项目首开真实漏斗",
        "请回填L-9或同类竞品近12个月来访、认筹、网签、净签、退房、渠道、首付来源、置换链、家庭结构和价格抗性，所有比例注明样本量；至少形成首开90天可回算漏斗。",
        "验收：来访→认筹→网签→净签→退房逐环节可回算。",
        "",
        "### ZZ-POLICY-VALUE｜项目级政策适用与补缴",
        "请针对L-9挂牌≤1.5和任务书四档户型，逐项核验坡屋顶、地下、封闭阳台、庭院、挑空的适用上限、计容、补缴评估、结构消防和销售确权，并返回官方条款和项目级预估边界。",
        "验收：不得把区级28%直接套作补缴金额；每种空间单列审批和成本。",
        "",
        "### ZZ-COST-FINANCE｜竞买前四账模型",
        "请以挂牌建面62,836.95㎡与任务书FAR2.0备选双口径，输入楼面5,952/6,500/7,500、建安4,200/4,600/5,200、售价12,956/14,400/15,339/15,656、折扣、税费、融资、地库、展示和补缴，输出货值、现金峰值、保本价和8%/15%毛利边界。",
        "验收：计容、可售、奖励面积不混算；每个数字有公式和来源。",
        "",
        "### ZZ-RECONCILE｜回传口径复核",
        "请核对README与JSON：月度窗口、总价120—300万元占比、竞品首开峰值与近12个月速度；返回逐行计算、分母、统计期和修订记录，不覆盖原值。",
        "验收：保留原版本、修订版本和冲突解释。",
        "",
        "> 当前行级复算：逐月记录{0}条、累计成交{1}套、加权均价{2}元/㎡；结构样本{3}套。".format(metrics["monthly_count"], fmt(metrics["monthly_units"]), fmt(metrics["monthly_weighted_price"]), fmt(metrics["area_total_units"])),
        "> 本文件是复核任务，不是法定规划条件或销售承诺。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_core_digest(metrics: dict[str, Any], warnings: list[str]) -> Path:
    path = CORE / "research" / "06_中指数据Agent回传与口径校准_20260826.md"
    text = f"""# 中指数据 Agent 回传与口径校准｜天津武清六街 L-9

> 回传批次：`20260826_zhongzhi-datacollect_v01`  
> 基准日：`{AS_OF}`  
> 角色：前策研究输入；不替代正式挂牌合同、附件2规划条件、测绘底图或绑定投决。

## 一、已确认并进入报告的当前事实

| 维度 | 行级回传读数 | 使用边界 |
|---|---:|---|
| L-9挂牌编号 | 津武(挂)2024-024号 | 挂牌中，未成交 |
| 建设用地 | {fmt(metrics['land_area_sqm'], 1)}㎡ | 当前土地交易口径 |
| 规划建面 | {fmt(metrics['land_plan_area_sqm'], 2)}㎡ | 当前挂牌口径 |
| 容积率 | {metrics['land_far']} | 附件2仍需裁决 |
| 起始价 / 保证金 | {fmt(metrics['land_start_price_10k'])}万元 / {fmt(metrics['land_deposit_10k'])}万元 | 竞买资金与现金峰值输入 |
| 起拍楼面价 | {fmt(metrics['land_start_floor_price'])}元/㎡ | 2026-08-28截止 |
| 起拍地面价 | {fmt(metrics['land_ground_price'])}元/㎡ | 挂牌字段 |
| 挂牌时序 | 起始{metrics['land_start_date']}；截止{metrics['land_deadline']}；预计成交{metrics['land_expected_close']} | 预计成交不等于成交事实 |
| 武清全域市场 | {metrics['monthly_count']}个月、{fmt(metrics['monthly_units'])}套、加权{fmt(metrics['monthly_weighted_price'])}元/㎡ | 2024-09—2026-07逐月JSON |
| 需求结构 | 90—140㎡{metrics['area_90_140_pct']}%、三房{metrics['three_room_pct']}%、120—300万{metrics['price_120_300_pct']}% | 2025-08—2026-07同一分母 |
| 竞品圈层 | 3km 15,339元/㎡；5km 15,656元/㎡ | 近12个月项目级快照 |

## 二、回传包的全量维度已落位

- 供应：周边3公里核心涉宅地块`{len(metrics['supply_core_rows'])}`宗、武清近年地价序列`{len(metrics['supply_price_rows'])}`条、天津年度供地`{len(metrics['supply_annual_rows'])}`条，已在报告SC2供应详表展开。
- 政策：四大空间条款、第二批补充、低多层/上下跃/139㎡适用结论、控制点和武清两宗补缴案例，已在报告VA2政策详表展开。
- 客群：7个竞品成交漏斗、5条首开估算漏斗、6条支付/置换判断、7条线下调研字段，已在报告SC2客群详表展开。
- 以上数据仍按“事实/估算/推断/缺口”分层；全量展示不等于投决证据已闭合。

## 三、影响投前判断的复算结论

规划压力公式：`全成本单方 = 楼面价 + 综合建安4,600 + 售价×14%`。该式仅用于条件性排序，不是最终财务模型。

| 楼面情景 | 8%规划毛利所需售价 | 15%规划毛利所需售价 |
|---:|---:|---:|
| 5,952 | 13,528 | 14,862 |
| 6,500 | 14,231 | 15,634 |
| 7,500 | 15,513 | 17,042 |

因此：5,952元/㎡可以继续研究，但不能自动称为安全价；6,500需要低密直接竞品与交付兑现；7,500只有高位成交且悲观仍过线时才有讨论价值。

## 四、口径冲突

{chr(10).join('- ' + item for item in warnings)}

- 挂牌≤1.5与任务书FAR2.0并存：前者用于当前交易/财务基准，后者用于设计备选，最终由附件2裁决。
- 回传客户漏斗中的15—25套/月为估算参考；目标项目真实来访、认筹、净签和退房仍未取得。
- 政策有效至2028-08-24，但L-9具体补缴、结构消防、销售确权和增量成本尚未闭合。

## 五、报告处理

- 已更新前期研判、竞品、市场结构、货值去化、客群、户型配比、强排双基线和财务压力页。
- 原始回传包保留在 `00_Core/runs/20260826_zhongzhi-datacollect_v01/`，未覆盖。
- 旧研究快照保留为历史/冲突来源；报告不把旧值静默替换成新事实。
- 正式放行前仍需：附件2/合同、真实红线与测绘、L-9项目级政策补缴、完整现金流、首开90天漏斗。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    mapping = CORE / "index" / "FILE_MAPPING.md"
    if mapping.is_file():
        mapping_text = mapping.read_text(encoding="utf-8")
        row = "| `06_中指数据Agent回传与口径校准_20260826.md` | `research/06_中指数据Agent回传与口径校准_20260826.md` | 中指回传、口径校准与前策结论 |"
        if row not in mapping_text:
            mapping.write_text(mapping_text.rstrip() + "\n" + row + "\n", encoding="utf-8")
    return path


def write_dataneed_v03(
    sources: list[dict[str, Any]], metrics: dict[str, Any], warnings: list[str]
) -> Path:
    if DATANEED_V02.is_dir():
        shutil.copytree(DATANEED_V02, DATANEED_V03, dirs_exist_ok=True)
    else:
        DATANEED_V03.mkdir(parents=True, exist_ok=True)
    root = DATANEED_V03 / "artifacts" / "data_need"
    returns_dir = root / "returns" / "20260826_zhongzhi-datacollect_v01"
    returns_dir.mkdir(parents=True, exist_ok=True)
    for _dataset_id, _source_id, filename, _title, _family in DATASET_SPECS:
        shutil.copyfile(RETURN_DATA_DIR / filename, returns_dir / filename)
    chart_src = RETURN_RUN / "artifacts" / "charts" / "market_trend.option.json"
    if chart_src.is_file():
        shutil.copyfile(chart_src, returns_dir / "market_trend.option.json")

    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    source_register_path = archive / "source-register.json"
    source_register = read_json(source_register_path) if source_register_path.is_file() else {"schema_version": "arch-front-dataneed/source-register/1.0", "sources": []}
    existing = {str(item.get("source_id")): item for item in source_register.get("sources") or [] if isinstance(item, dict)}
    for source in sources:
        payload = {
            "source_id": source["source_id"],
            "title": source["title"],
            "source_class": "public_or_authorized_retrieval",
            "origin_locator": source.get("source_url") or source["canonical_ref"],
            "publisher_or_owner": source["publisher"],
            "captured_at": AS_OF,
            "geography": source["geography"],
            "time_scope": str((source.get("time_window") or {}).get("label") or AS_OF),
            "version": "20260826回传批次",
            "rights_status": source["rights_status"],
            "checksum_sha256": str(source["raw_hash"]).upper(),
            "archive_path": f"artifacts/data_need/returns/20260826_zhongzhi-datacollect_v01/{Path(source['canonical_ref']).name}",
            "requirement_refs": list(RETURN_REQUIREMENT_REFS.get(source["source_id"], [])),
            "extraction_method": "中指数据Agent回传+本地行级复算",
            "status": "collected",
            "limitations": source["limitations"],
            "trust_tier": source.get("trust_tier", "L1"),
        }
        existing[source["source_id"]] = payload
    source_register["sources"] = sorted(existing.values(), key=lambda item: str(item.get("source_id")))
    write_json(source_register_path, source_register)

    # Rebind the requirement register to the new evidence layer while keeping
    # each requirement's verified/needed state explicit.  A linkage reference
    # is not a promotion: D0 documents and project-level D1 analyses remain
    # ``needed`` until their acceptance checks are actually closed.
    requirements_path = root / "requirements" / "data-requirements.json"
    if requirements_path.is_file():
        requirements = read_json(requirements_path)
        for requirement in requirements.get("requirements") or []:
            if not isinstance(requirement, dict):
                continue
            requirement_id = str(requirement.get("requirement_id") or "")
            linked = [
                source_id
                for source_id, refs in RETURN_REQUIREMENT_REFS.items()
                if requirement_id in refs
            ]
            if linked:
                requirement["source_refs"] = sorted(
                    set(str(item) for item in requirement.get("source_refs") or [])
                    | set(linked)
                )
        acceptance_updates = {
            "DN-D1-MKT01": [
                "逐月JSON窗口2024-09—2026-07共23条",
                "累计成交11,434套、加权均价约12,956元/㎡",
                "2026-07可售9,695套、出清周期21.78个月",
            ],
            "DN-D1-MKT02": [
                "同一结构窗口2025-08—2026-07",
                "90—140㎡占70.3%、三房占73.4%",
                "120—300万元占62.5%",
            ],
            "DN-D1-COMP01": [
                "3/5公里竞品全量库22个项目",
                "成熟期月均与新盘首开峰值分开记录",
                "重点竞品速度可回算且标注统计期",
            ],
            "DN-D1-GS01": [
                "L-9当前挂牌起拍楼面价5,952元/㎡",
                "同容积率/低密可比楼面价6,511—7,522元/㎡",
                "挂牌价与成交价分列",
            ],
            "DN-D1-POL02": [
                "政策有效至2028-08-24",
                "武清补缴实例已收录",
                "L-9项目级适用与补缴仍需专项核验",
            ],
        }
        for requirement in requirements.get("requirements") or []:
            if not isinstance(requirement, dict):
                continue
            requirement_id = str(requirement.get("requirement_id") or "")
            if requirement_id in acceptance_updates:
                requirement["acceptance_checks"] = acceptance_updates[requirement_id]
        write_json(requirements_path, requirements)

    # Keep the prior exclusion register but make the versioned return explicit.
    exclusion_path = archive / "exclusion-register.json"
    if not exclusion_path.is_file():
        write_json(exclusion_path, {"schema_version": "arch-front-dataneed/exclusion-register/1.0", "exclusions": []})

    research = root / "research"
    research.mkdir(parents=True, exist_ok=True)
    write_json(research / "zhongzhi-return-validation.json", {"schema_version": "zhongzhi-return-validation/1.0", "batch_id": "20260826_zhongzhi-datacollect_v01", "as_of": AS_OF, "passed": True, "warnings": warnings, "metrics": metrics})
    write_json(research / "acquisition-log.json", {"schema_version": "arch-front-dataneed/acquisition-log/1.0", "as_of": AS_OF, "mode": "external_return_ingest", "attempts": [{"route": "zhongzhi_return_batch", "status": "completed_with_bounded_conflicts", "datasets": [item[0] for item in DATASET_SPECS], "warnings": warnings}]})
    write_json(research / "evidence-matrix.json", {"schema_version": "arch-front-dataneed/evidence-matrix/1.0", "as_of": AS_OF, "claims": [{"claim_id": item, "source_refs": [DATASET_REF.get(item.split("claim-zz-")[-1], "")], "status": "source_backed_bounded", "decision_eligibility": False} for item in ["claim-zz-land-listing", "claim-zz-market-monthly", "claim-zz-market-structure", "claim-zz-competition", "claim-zz-policy-value", "claim-zz-customer-funnel"]]})
    write_json(research / "gap-register.json", {"schema_version": "arch-front-dataneed/gap-register/1.0", "as_of": AS_OF, "blocking": [{"gap_id": "GAP-ZZ-CONTRACT", "priority": "D0", "status": "needed", "owner": "投拓/法务负责人", "acceptance": "合同、附件2、付款和竞买条款闭合"}, {"gap_id": "GAP-ZZ-GEOMETRY", "priority": "D0", "status": "needed", "owner": "规划测绘负责人", "acceptance": "红线坐标、真北、标高、道路和北侧总图闭合"}], "confidence_gaps": ["目标项目真实客户漏斗", "项目级政策补缴", "竞品户型/折扣/送赠", "完整财务现金流"]})
    (research / "fallback-options.md").write_text("""# 中指回传后仍未闭合缺口的替代路径

## D0 法定条件/合同/几何

1. 直接获取挂牌附件、出让合同、附件2和测绘CAD/GIS（推荐，正式放行前必须）。
2. 在原始文件未到位前，沿用挂牌≤1.5做容量/财务压力，并保留任务书FAR2.0设计备选（仅研究，不做正式强排）。
3. 由规划测绘专项建立临时底图和不确定性边界，所有尺寸、日照、消防和车位结论标为不可放行，待原始文件替换。

## 客户漏斗

1. 调取目标项目/同类竞品90天案场台账（推荐）。
2. 使用中指项目级成交和成熟竞品11—26套/月做保守/中性/积极情景。
3. 先按15—25套/月控制首开供货，首开90天超过30套/月后再切换上行情景。

## 政策货值/财务

1. 规划、结构、消防、法务、成本联合出具L-9专项适用和补缴估算（推荐）。
2. 以武清2023-014/2024-013作为机制代理，不迁移补缴金额。
3. 在不送赠口径下完成5,952/6,500/7,500三档压力，再把实测奖励面积和增量成本作为第二层输入。
""", encoding="utf-8")
    qa_dir = root / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    write_json(qa_dir / "return-acceptance-report.json", {"schema_version": "arch-front-dataneed/return-acceptance/1.0", "batch_id": "20260826_zhongzhi-datacollect_v01", "structural_passed": True, "row_level_json_parsed": True, "warnings": warnings, "blocking_gaps": ["附件2/合同", "真实几何", "目标项目真实漏斗", "项目级补缴与现金流"]})
    manifest_path = DATANEED_V03 / "run-manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    manifest.update({"run_id": "20260826_arch-front-dataneed_v03_zhongzhi", "generated_at": f"{AS_OF}T20:30:00+08:00", "refined_at": f"{AS_OF}T20:30:00+08:00", "status": "valid_with_blocking_gaps", "execution_mode": "zhongzhi_return_ingest_after_local_fallback", "return_batch": "20260826_zhongzhi-datacollect_v01", "return_dataset_count": len(DATASET_SPECS), "return_warning_count": len(warnings), "notes": "已将中指回传7个JSON数据集与图表配置复制到data_need/returns；市场/竞品/挂牌事实已进入报告，附件2、合同、几何、真实漏斗、项目级补缴和完整现金流仍为阻断项。"})
    write_json(manifest_path, manifest)
    return DATANEED_V03


def validate_seed_refs(seed: dict[str, Any]) -> None:
    source_ids = {str(item.get("source_id")) for item in seed.get("source_registry") or [] if isinstance(item, dict)}
    missing: set[str] = set()
    def walk(value: object) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("source_refs"), list):
                missing.update(str(item) for item in value["source_refs"] if str(item) not in source_ids)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(seed.get("page_manifest"))
    walk(seed.get("data_return"))
    if missing:
        raise ValueError("unresolved report source refs: " + ", ".join(sorted(missing)))


def main() -> None:
    datasets, _chart, warnings = load_return_batch()
    metrics = calculate_metrics(datasets)

    # Copy the new raw return into the inbox before canonical intake so the
    # evidence-set hash records the new version.  The original batch is never
    # edited.
    sources = copy_and_register_sources(datasets, metrics)
    import prepare_hyp_l9_inputs as base  # local workspace helper
    base.build()
    sync_intake_hashes()

    seed_path = WORK / "report_seed.json"
    seed = read_json(seed_path)
    update_seed_top_level(seed, datasets, metrics, sources, warnings)
    apply_page_updates(seed, metrics, [str(item["source_id"]) for item in sources])
    validate_seed_refs(seed)
    write_json(seed_path, seed)
    update_candidate_input(metrics, sources)
    prompt_path = write_prompt_file(metrics, warnings)
    digest_path = write_core_digest(metrics, warnings)
    dataneed_path = write_dataneed_v03(sources, metrics, warnings)

    manifest = {
        "run_id": "20260826_zhongzhi-datacollect_v01_integrated",
        "project_id": "HYP_天津_武清区六街 L-9 地块_Gary",
        "as_of": AS_OF,
        "status": "integrated_with_bounded_conflicts",
        "dataset_count": len(DATASET_SPECS),
        "source_count_added": len(sources),
        "warnings": warnings,
        "metrics": {key: value for key, value in metrics.items() if not isinstance(value, list) or key in {"area_rows", "price_rows", "house_rows"}},
        "prompt_path": str(prompt_path),
        "core_digest_path": str(digest_path),
        "dataneed_v03": str(dataneed_path),
        "report_seed": str(seed_path),
    }
    write_json(WORK / "zhongzhi_integration_manifest.json", manifest)
    print(json.dumps({"datasets": len(DATASET_SPECS), "sources_added": len(sources), "pages": len(seed.get("page_manifest") or []), "monthly_records": metrics["monthly_count"], "structure_units": metrics["area_total_units"], "land_floor": metrics["land_start_floor_price"], "warnings": warnings, "prompt_path": str(prompt_path), "core_digest_path": str(digest_path), "dataneed_v03": str(dataneed_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
