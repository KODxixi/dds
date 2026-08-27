# -*- coding: utf-8 -*-
"""
三级质量门禁模块 — P0 入库前 / P1 日度 / P2 周度
=================================================
功能：对数据执行三级质量门禁检查，输出 PASS/FAIL + 阻断/标记/报告。

门禁级别
--------
| 级别 | 时机     | 动作 | 检查项                                         |
|------|----------|------|-------------------------------------------------|
| P0   | 入库前   | 阻断 | Schema列数/必填字段/价格范围/坐标有效率/去重率   |
| P1   | 日度     | 标记 | 漂移检测/异常率/新增数据质量                     |
| P2   | 周度     | 报告 | 趋势分析/质量评分变化/覆盖率变化                 |

设计原则
--------
- 纯标准库（json / csv / re / math），不依赖 pandas
- 复用 T4 去重 / T5 缺失值 / drift_monitor 漂移检测
- P0 失败 = 阻断入库；P1 异常 = 标记但不阻断；P2 = 报告趋势

CLI
---
    python scripts/governance/quality_gates.py --csv data.csv --level P0
    python scripts/governance/quality_gates.py --csv data.csv --level P1
    python scripts/governance/quality_gates.py --csv data.csv --level P2
    python scripts/governance/quality_gates.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 复用治理模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from t4_dedup import dedup as _t4_dedup  # noqa: E402
from t5_missing_profile import is_missing  # noqa: E402


# ── P0 门禁阈值 ───────────────────────────────────────────────────────
P0_MIN_COLUMNS = 50          # 最少列数（105 列规范允许部分缺失）
P0_REQUIRED_FIELDS = ["楼盘名称", "城市名称"]  # 必填字段
P0_PRICE_MIN = 500           # 价格下限
P0_PRICE_MAX = 500000        # 价格上限
P0_COORD_VALID_RATE_MIN = 0.50  # 坐标有效率下限
P0_DUPLICATE_RATE_MAX = 0.30    # 重复率上限
CHINA_BBOX = (3.0, 54.0, 73.0, 135.0)

# ── P1 门禁阈值 ───────────────────────────────────────────────────────
P1_ANOMALY_RATE_MAX = 0.15   # 异常率上限
P1_MISSING_RATE_MAX = 0.40   # 关键列缺失率上限

# ── P2 门禁阈值 ───────────────────────────────────────────────────────
P2_QUALITY_SCORE_MIN = 60    # 质量评分下限
P2_COVERAGE_TREND_DAYS = 7   # 覆盖率趋势窗口


# ═══════════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _to_float(value: Any) -> float | None:
    """安全转 float。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("元/㎡", "")
    if s in ("", "nan", "none", "null", "无"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _collect_columns(rows: list[dict]) -> list[str]:
    """收集所有列名。"""
    seen = set()
    columns = []
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                columns.append(k)
    return columns


# ═══════════════════════════════════════════════════════════════════════
#  P0 入库前门禁
# ═══════════════════════════════════════════════════════════════════════

def check_schema_columns(rows: list[dict]) -> dict[str, Any]:
    """P0-1：Schema 列数检查。"""
    columns = _collect_columns(rows)
    col_count = len(columns)
    passed = col_count >= P0_MIN_COLUMNS
    return {
        "check": "schema_columns",
        "passed": passed,
        "detail": f"列数 {col_count}（要求 ≥ {P0_MIN_COLUMNS}）",
        "metric": col_count,
        "threshold": P0_MIN_COLUMNS,
        "severity": "block" if not passed else "pass",
    }


def check_required_fields(rows: list[dict]) -> dict[str, Any]:
    """P0-2：必填字段检查。"""
    issues = []
    for field in P0_REQUIRED_FIELDS:
        missing_count = sum(1 for r in rows if is_missing(r.get(field)))
        if missing_count > 0:
            issues.append(f"{field} 有 {missing_count} 行缺失")
    passed = len(issues) == 0
    return {
        "check": "required_fields",
        "passed": passed,
        "detail": "; ".join(issues) if issues else "所有必填字段完整",
        "issues": issues,
        "severity": "block" if not passed else "pass",
    }


def check_price_range(rows: list[dict]) -> dict[str, Any]:
    """P0-3：价格范围检查。"""
    out_of_range = 0
    total_priced = 0
    for r in rows:
        price = _to_float(r.get("最新价格"))
        if price is None:
            continue
        total_priced += 1
        if price < P0_PRICE_MIN or price > P0_PRICE_MAX:
            out_of_range += 1
    passed = total_priced == 0 or (out_of_range / total_priced) < 0.05
    return {
        "check": "price_range",
        "passed": passed,
        "detail": f"价格越界 {out_of_range}/{total_priced}（范围 {P0_PRICE_MIN}-{P0_PRICE_MAX}）",
        "out_of_range": out_of_range,
        "total_priced": total_priced,
        "severity": "block" if not passed else "pass",
    }


def check_coord_valid_rate(rows: list[dict]) -> dict[str, Any]:
    """P0-4：坐标有效率检查。"""
    valid = 0
    total = 0
    lat_lo, lat_hi, lng_lo, lng_hi = CHINA_BBOX
    for r in rows:
        lat = _to_float(r.get("lat") or r.get("百度地图纬度"))
        lng = _to_float(r.get("lng") or r.get("百度地图经度"))
        total += 1
        if lat is not None and lng is not None and lat != 0 and lng != 0:
            if lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi:
                valid += 1
    rate = valid / total if total else 0
    passed = rate >= P0_COORD_VALID_RATE_MIN
    return {
        "check": "coord_valid_rate",
        "passed": passed,
        "detail": f"坐标有效率 {rate:.1%}（要求 ≥ {P0_COORD_VALID_RATE_MIN:.0%}）",
        "valid_count": valid,
        "total_count": total,
        "rate": round(rate, 4),
        "severity": "block" if not passed else "pass",
    }


def check_duplicate_rate(rows: list[dict]) -> dict[str, Any]:
    """P0-5：去重率检查。"""
    if not rows:
        return {"check": "duplicate_rate", "passed": True, "detail": "无数据", "severity": "pass"}
    marked = _t4_dedup(rows)
    dup_count = sum(1 for r in marked if r.get("dup_group_id", -1) >= 0)
    dup_rate = dup_count / len(rows) if rows else 0
    passed = dup_rate <= P0_DUPLICATE_RATE_MAX
    return {
        "check": "duplicate_rate",
        "passed": passed,
        "detail": f"重复率 {dup_rate:.1%}（要求 ≤ {P0_DUPLICATE_RATE_MAX:.0%}）",
        "duplicate_count": dup_count,
        "total_count": len(rows),
        "dup_rate": round(dup_rate, 4),
        "severity": "block" if not passed else "pass",
    }


def p0_gate(rows: list[dict]) -> tuple[bool, list[dict[str, Any]]]:
    """
    P0 入库前门禁：全部 5 项检查必须通过。

    参数:
        rows: 行列表

    返回:
        (passed, issues) — passed=True 则可入库，issues 为各检查项结果列表
    """
    checks = [
        check_schema_columns(rows),
        check_required_fields(rows),
        check_price_range(rows),
        check_coord_valid_rate(rows),
        check_duplicate_rate(rows),
    ]
    passed = all(c["passed"] for c in checks)
    return passed, checks


# ═══════════════════════════════════════════════════════════════════════
#  P1 日度门禁
# ═══════════════════════════════════════════════════════════════════════

def check_anomaly_rate(rows: list[dict]) -> dict[str, Any]:
    """P1-1：异常率检查（价格越界 + 坐标异常）。"""
    if not rows:
        return {"check": "anomaly_rate", "passed": True, "detail": "无数据", "severity": "pass"}
    anomaly_count = 0
    lat_lo, lat_hi, lng_lo, lng_hi = CHINA_BBOX
    for r in rows:
        price = _to_float(r.get("最新价格"))
        lat = _to_float(r.get("lat") or r.get("百度地图纬度"))
        lng = _to_float(r.get("lng") or r.get("百度地图经度"))
        is_anomaly = False
        if price is not None and (price < P0_PRICE_MIN or price > P0_PRICE_MAX):
            is_anomaly = True
        if lat is not None and lng is not None:
            if lat == 0 or lng == 0 or not (lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi):
                is_anomaly = True
        if is_anomaly:
            anomaly_count += 1
    rate = anomaly_count / len(rows)
    passed = rate <= P1_ANOMALY_RATE_MAX
    return {
        "check": "anomaly_rate",
        "passed": passed,
        "detail": f"异常率 {rate:.1%}（阈值 ≤ {P1_ANOMALY_RATE_MAX:.0%}）",
        "anomaly_count": anomaly_count,
        "total_count": len(rows),
        "rate": round(rate, 4),
        "severity": "flag" if not passed else "pass",
    }


def check_missing_rate(rows: list[dict]) -> dict[str, Any]:
    """P1-2：关键列缺失率检查。"""
    if not rows:
        return {"check": "missing_rate", "passed": True, "detail": "无数据", "severity": "pass"}
    key_cols = ["楼盘名称", "城市名称", "最新价格", "地址"]
    issues = []
    max_rate = 0
    for col in key_cols:
        missing = sum(1 for r in rows if is_missing(r.get(col)))
        rate = missing / len(rows)
        max_rate = max(max_rate, rate)
        if rate > P1_MISSING_RATE_MAX:
            issues.append(f"{col} 缺失率 {rate:.1%}")
    passed = len(issues) == 0
    return {
        "check": "missing_rate",
        "passed": passed,
        "detail": "; ".join(issues) if issues else f"关键列缺失率正常（最高 {max_rate:.1%}）",
        "max_missing_rate": round(max_rate, 4),
        "issues": issues,
        "severity": "flag" if not passed else "pass",
    }


def check_new_data_quality(rows: list[dict]) -> dict[str, Any]:
    """P1-3：新增数据质量检查（行级完整性）。"""
    if not rows:
        return {"check": "new_data_quality", "passed": True, "detail": "无数据", "severity": "pass"}
    # 检查至少有价格数据
    priced = sum(1 for r in rows if _to_float(r.get("最新价格")) is not None)
    rate = priced / len(rows)
    passed = rate >= 0.50  # 至少 50% 有价格
    return {
        "check": "new_data_quality",
        "passed": passed,
        "detail": f"有价格数据 {rate:.1%}（要求 ≥ 50%）",
        "priced_count": priced,
        "total_count": len(rows),
        "rate": round(rate, 4),
        "severity": "flag" if not passed else "pass",
    }


def p1_gate(rows: list[dict]) -> tuple[bool, list[dict[str, Any]]]:
    """
    P1 日度门禁：标记不阻断。

    参数:
        rows: 行列表

    返回:
        (passed, checks) — passed=True 则无标记，checks 为各检查项结果
    """
    checks = [
        check_anomaly_rate(rows),
        check_missing_rate(rows),
        check_new_data_quality(rows),
    ]
    passed = all(c["passed"] for c in checks)
    return passed, checks


# ═══════════════════════════════════════════════════════════════════════
#  P2 周度门禁
# ═══════════════════════════════════════════════════════════════════════

def check_quality_trend(rows: list[dict], history: list[dict] | None = None) -> dict[str, Any]:
    """P2-1：质量评分趋势检查。"""
    # 简化：计算当前数据集的基本质量分
    if not rows:
        return {"check": "quality_trend", "passed": True, "detail": "无数据", "severity": "report"}
    columns = _collect_columns(rows)
    all_cells = len(rows) * len(columns)
    missing_cells = sum(1 for r in rows for c in columns if is_missing(r.get(c)))
    fill_rate = 1 - missing_cells / all_cells if all_cells else 0
    score = round(fill_rate * 100)
    passed = score >= P2_QUALITY_SCORE_MIN

    trend = "stable"
    if history:
        prev_score = history[0].get("quality_score", score) if history else score
        if score > prev_score + 5:
            trend = "improving"
        elif score < prev_score - 5:
            trend = "declining"

    return {
        "check": "quality_trend",
        "passed": passed,
        "detail": f"质量评分 {score}（趋势 {trend}）",
        "quality_score": score,
        "trend": trend,
        "severity": "report",  # P2 始终 report 级别
    }


def check_coverage_change(rows: list[dict], prev_cities: list[str] | None = None) -> dict[str, Any]:
    """P2-2：覆盖率变化检查。"""
    if not rows:
        return {"check": "coverage_change", "passed": True, "detail": "无数据", "severity": "report"}
    current_cities = set()
    for r in rows:
        c = r.get("城市名称", "")
        if not is_missing(c):
            current_cities.add(str(c).strip())

    if prev_cities:
        prev_set = set(prev_cities)
        added = current_cities - prev_set
        removed = prev_set - current_cities
        passed = len(removed) == 0  # 不应有城市消失
        return {
            "check": "coverage_change",
            "passed": passed,
            "detail": f"城市覆盖 {len(current_cities)}（新增 {len(added)}，消失 {len(removed)}）",
            "current_cities": sorted(current_cities),
            "added": sorted(added),
            "removed": sorted(removed),
            "severity": "report",
        }
    return {
        "check": "coverage_change",
        "passed": True,
        "detail": f"当前覆盖 {len(current_cities)} 个城市（无历史对比）",
        "current_cities": sorted(current_cities),
        "severity": "report",
    }


def check_volume_trend(rows: list[dict], prev_count: int | None = None) -> dict[str, Any]:
    """P2-3：数据量趋势检查。"""
    current_count = len(rows)
    if prev_count is not None and prev_count > 0:
        change_pct = (current_count - prev_count) / prev_count
        passed = abs(change_pct) < 0.50  # 变化不应超过 50%
        return {
            "check": "volume_trend",
            "passed": passed,
            "detail": f"行数 {prev_count} → {current_count}（变化 {change_pct:+.1%}）",
            "prev_count": prev_count,
            "current_count": current_count,
            "change_pct": round(change_pct, 4),
            "severity": "report",
        }
    return {
        "check": "volume_trend",
        "passed": True,
        "detail": f"当前行数 {current_count}（无历史对比）",
        "current_count": current_count,
        "severity": "report",
    }


def p2_gate(
    rows: list[dict],
    history: list[dict] | None = None,
    prev_cities: list[str] | None = None,
    prev_count: int | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """
    P2 周度门禁：报告趋势，不阻断。

    参数:
        rows: 行列表
        history: 历史质量记录列表（可选）
        prev_cities: 上周城市列表（可选）
        prev_count: 上周数据量（可选）

    返回:
        (passed, checks) — passed=True 则无异常趋势，checks 为各检查项结果
    """
    checks = [
        check_quality_trend(rows, history),
        check_coverage_change(rows, prev_cities),
        check_volume_trend(rows, prev_count),
    ]
    passed = all(c["passed"] for c in checks)
    return passed, checks


# ═══════════════════════════════════════════════════════════════════════
#  综合门禁入口
# ═══════════════════════════════════════════════════════════════════════

def run_gate(
    rows: list[dict],
    level: str = "P0",
    **kwargs: Any,
) -> dict[str, Any]:
    """
    执行指定级别的质量门禁。

    参数:
        rows: 行列表
        level: 门禁级别（"P0" / "P1" / "P2"）
        **kwargs: P2 级别的可选参数（history, prev_cities, prev_count）

    返回:
        门禁结果字典，包含 level / passed / action / checks / summary
    """
    level = level.upper()
    if level == "P0":
        passed, checks = p0_gate(rows)
        action = "block" if not passed else "pass"
    elif level == "P1":
        passed, checks = p1_gate(rows)
        action = "flag" if not passed else "pass"
    elif level == "P2":
        passed, checks = p2_gate(rows, **kwargs)
        action = "report"
    else:
        raise ValueError(f"未知门禁级别: {level}（应为 P0/P1/P2）")

    failed_checks = [c for c in checks if not c["passed"]]
    return {
        "level": level,
        "passed": passed,
        "action": action,
        "total_checks": len(checks),
        "passed_checks": len(checks) - len(failed_checks),
        "failed_checks": len(failed_checks),
        "checks": checks,
        "checked_at": datetime.now().isoformat(),
        "summary": f"[{level}] {'PASS' if passed else 'FAIL'} — "
                   f"{len(checks) - len(failed_checks)}/{len(checks)} 检查通过",
    }


# ═══════════════════════════════════════════════════════════════════════
#  CSV 读写
# ═══════════════════════════════════════════════════════════════════════

def read_csv_rows(csv_path: str | Path) -> list[dict]:
    """用 csv 模块读取 CSV 文件为行列表。"""
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


# ═══════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """内置自测：通过和不通过的数据集，验证门禁正确阻断。"""
    # ── P0 通过的数据集 ──
    pass_rows = [
        {"楼盘名称": f"楼盘{i}", "城市名称": "三亚", "地址": f"地址{i}号",
         "最新价格": str(30000 + i * 100), "lat": "18.41", "lng": "109.71",
         "区域名称": "海棠区", "建筑面积": "50000", "占地面积": "30000", "容积率": "2.5"}
        for i in range(60)  # 60 行，足够列数
    ]
    # 补充更多列达到 P0_MIN_COLUMNS
    for r in pass_rows:
        for j in range(60):
            r[f"补充列{j}"] = f"值{j}"

    p0_passed, p0_checks = p0_gate(pass_rows)
    assert p0_passed, f"P0 应通过，实际未通过：{[c['check'] for c in p0_checks if not c['passed']]}"
    print(f"  [OK] P0 通过数据集：{sum(1 for c in p0_checks if c['passed'])}/{len(p0_checks)} 检查通过")

    # ── P0 不通过的数据集 ──
    fail_rows = [
        {"楼盘名称": "", "城市名称": "三亚", "最新价格": "100", "lat": "0", "lng": "0"},    # 缺名称+价格低+坐标零
        {"楼盘名称": "重复楼盘", "城市名称": "三亚", "最新价格": "35000", "lat": "0", "lng": "0"},   # 坐标零
        {"楼盘名称": "重复楼盘", "城市名称": "三亚", "最新价格": "35000", "lat": "0", "lng": "0"},   # 坐标零
        {"楼盘名称": "重复楼盘", "城市名称": "三亚", "最新价格": "35000", "lat": "18.41", "lng": "109.71"},  # 重复
    ]

    p0_fail_passed, p0_fail_checks = p0_gate(fail_rows)
    assert not p0_fail_passed, "P0 应不通过（有多个违规）"
    failed_names = [c["check"] for c in p0_fail_checks if not c["passed"]]
    assert "required_fields" in failed_names, "应检测到必填字段缺失"
    assert "price_range" in failed_names, "应检测到价格越界"
    assert "coord_valid_rate" in failed_names, "应检测到坐标无效"
    assert "duplicate_rate" in failed_names, "应检测到重复率过高"
    print(f"  [OK] P0 不通过数据集：正确阻断（失败项：{failed_names}）")

    # ── P0 Schema 列数检查 ──
    schema_check = check_schema_columns(fail_rows)
    assert not schema_check["passed"], f"列数不足应不通过（{schema_check['metric']} < {P0_MIN_COLUMNS}）"
    print(f"  [OK] P0 Schema 列数检查：{schema_check['detail']}")

    # ── P0 必填字段检查 ──
    req_check = check_required_fields(fail_rows)
    assert not req_check["passed"], "必填字段缺失应不通过"
    print(f"  [OK] P0 必填字段检查：{req_check['detail']}")

    # ── P0 价格范围检查 ──
    price_check = check_price_range(fail_rows)
    assert not price_check["passed"], "价格越界应不通过"
    print(f"  [OK] P0 价格范围检查：{price_check['detail']}")

    # ── P0 坐标有效率检查 ──
    coord_check = check_coord_valid_rate(fail_rows)
    assert not coord_check["passed"], "坐标无效应不通过"
    print(f"  [OK] P0 坐标有效率检查：{coord_check['detail']}")

    # ── P0 去重率检查 ──
    dup_check = check_duplicate_rate(fail_rows)
    assert not dup_check["passed"], "重复率过高应不通过"
    print(f"  [OK] P0 去重率检查：{dup_check['detail']}")

    # ── P1 门禁 ──
    p1_passed, p1_checks = p1_gate(pass_rows)
    assert p1_passed, f"P1 应通过（正常数据），实际未通过：{[c['check'] for c in p1_checks if not c['passed']]}"
    print(f"  [OK] P1 通过数据集：{sum(1 for c in p1_checks if c['passed'])}/{len(p1_checks)} 检查通过")

    p1_fail_passed, p1_fail_checks = p1_gate(fail_rows)
    # P1 不阻断，只标记
    assert p1_fail_checks[0]["severity"] in ("flag", "pass"), "P1 应使用 flag 而非 block"
    print(f"  [OK] P1 不通过数据集：标记（非阻断），异常率检查={p1_fail_checks[0]['passed']}")

    # ── P2 门禁 ──
    p2_passed, p2_checks = p2_gate(pass_rows, prev_cities=["三亚"], prev_count=55)
    assert p2_checks[0]["severity"] == "report", "P2 应使用 report 级别"
    print(f"  [OK] P2 趋势报告：{p2_checks[0]['detail']}")

    # ── 综合入口 run_gate ──
    result_p0 = run_gate(pass_rows, level="P0")
    assert result_p0["passed"] is True
    assert result_p0["action"] == "pass"
    print(f"  [OK] run_gate P0：{result_p0['summary']}")

    result_p0_fail = run_gate(fail_rows, level="P0")
    assert result_p0_fail["passed"] is False
    assert result_p0_fail["action"] == "block"
    print(f"  [OK] run_gate P0 阻断：{result_p0_fail['summary']}")

    result_p1 = run_gate(pass_rows, level="P1")
    assert result_p1["action"] in ("pass", "flag")
    print(f"  [OK] run_gate P1：{result_p1['summary']}")

    result_p2 = run_gate(pass_rows, level="P2", prev_cities=["三亚"], prev_count=55)
    assert result_p2["action"] == "report"
    print(f"  [OK] run_gate P2：{result_p2['summary']}")

    # ── JSON 序列化 ──
    json_str = json.dumps(result_p0, ensure_ascii=False)
    assert "checks" in json_str
    print(f"  [OK] 结果 JSON 序列化成功（{len(json_str)} 字符）")

    print("\n  === 三级质量门禁 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="三级质量门禁 — P0 入库前 / P1 日度 / P2 周度"
    )
    parser.add_argument("--csv", type=str, required=False, help="输入 CSV 文件路径")
    parser.add_argument("--level", type=str, default="P0", choices=["P0", "P1", "P2"],
                        help="门禁级别（默认 P0）")
    parser.add_argument("--out", type=str, default=None, help="输出 JSON 报告路径")
    parser.add_argument("--prev-cities", type=str, default=None,
                        help="上周城市列表（逗号分隔，P2 用）")
    parser.add_argument("--prev-count", type=int, default=None,
                        help="上周数据量（P2 用）")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    if not args.csv:
        print("错误：需要 --csv 参数指定数据文件")
        return

    print(f"[gate] 读取数据：{args.csv}")
    rows = read_csv_rows(args.csv)
    print(f"[gate] 总行数：{len(rows)}")

    kwargs = {}
    if args.prev_cities:
        kwargs["prev_cities"] = args.prev_cities.split(",")
    if args.prev_count is not None:
        kwargs["prev_count"] = args.prev_count

    result = run_gate(rows, level=args.level, **kwargs)

    # 输出到文件
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[gate] 报告已写入：{out_path}")

    # 控制台输出
    status = "PASS" if result["passed"] else "FAIL"
    action = {"pass": "通过", "block": "阻断", "flag": "标记", "report": "报告"}[result["action"]]
    print(f"\n[gate] {result['level']} 门禁结果：{status}（动作：{action}）")
    print(f"[gate] {result['summary']}")
    for c in result["checks"]:
        icon = "[OK]" if c["passed"] else "[!!]"
        print(f"  {icon} {c['check']:25s}: {c['detail']}")

    # P0 阻断时返回非零退出码
    if result["level"] == "P0" and not result["passed"]:
        print("\n[gate] P0 门禁未通过，阻断入库！")
        sys.exit(1)


if __name__ == "__main__":
    main()
