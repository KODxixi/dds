# -*- coding: utf-8 -*-
"""
T8 数据质量评估 — 八维质量指标 + 标准工件输出
================================================
功能：对楼盘数据集执行八维质量评估，输出三个标准工件：
  - ``quality_report.md`` — 八维质量报告（含评分/证据/建议）
  - ``data_card.md`` — 数据卡片（元信息摘要）
  - ``data_feasibility.md`` — 数据可行性评估（可用于哪些场景/风险提示）

八维质量指标
------------
1. **完整性** (Completeness)    — 非空值占比
2. **一致性** (Consistency)      — 跨字段逻辑一致性（价格 vs 参考价格等）
3. **准确性** (Accuracy)         — 值在合理范围内占比
4. **时效性** (Timeliness)       — 数据新鲜度（最近开盘日期距今天数）
5. **唯一性** (Uniqueness)       — 非重复行占比
6. **代表性** (Representativeness)— 城市/区域覆盖广度
7. **偏差** (Bias)              — 价格/面积分布偏度
8. **隐私** (Privacy)            — 敏感信息暴露风险（电话/身份证等）

设计原则
--------
- 纯标准库（json / csv / re / math），不依赖 pandas
- 每维度 0-100 分，综合分 = 加权平均
- 工件输出 Markdown 格式，人类可读

CLI
---
    python scripts/governance/t8_quality_assess.py --city 三亚 --outdir data_out/governance/t8_quality/
    python scripts/governance/t8_quality_assess.py --csv data.csv --outdir reports/
    python scripts/governance/t8_quality_assess.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 复用 T4/T5 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from t4_dedup import dedup as _t4_dedup  # noqa: E402
from t5_missing_profile import is_missing, profile_missing  # noqa: E402


# ── 权重配置 ──────────────────────────────────────────────────────────
DIMENSION_WEIGHTS = {
    "completeness": 0.20,
    "consistency": 0.10,
    "accuracy": 0.15,
    "timeliness": 0.10,
    "uniqueness": 0.15,
    "representativeness": 0.10,
    "bias": 0.10,
    "privacy": 0.10,
}

# 关键列定义
KEY_COLUMNS = ["楼盘名称", "城市名称", "最新价格", "地址"]
# 价格合理范围
PRICE_MIN, PRICE_MAX = 500, 500000
# 敏感信息正则
SENSITIVE_PATTERNS = {
    "phone_400": re.compile(r"400\d{7}"),
    "mobile": re.compile(r"1[3-9]\d{9}"),
    "id_card": re.compile(r"\d{17}[\dXx]"),
}


# ═══════════════════════════════════════════════════════════════════════
#  八维质量评估
# ═══════════════════════════════════════════════════════════════════════

def assess_completeness(rows: list[dict]) -> dict[str, Any]:
    """维度1：完整性 — 非空值占比（所有单元格）。"""
    if not rows:
        return {"score": 0, "detail": "无数据"}
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    total_cells = len(rows) * len(all_keys)
    missing_cells = 0
    for r in rows:
        for k in all_keys:
            if is_missing(r.get(k)):
                missing_cells += 1
    fill_rate = 1 - missing_cells / total_cells if total_cells else 0
    score = round(fill_rate * 100)
    return {
        "score": score,
        "fill_rate": round(fill_rate, 4),
        "missing_cells": missing_cells,
        "total_cells": total_cells,
        "detail": f"填充率 {fill_rate:.1%}（{total_cells - missing_cells}/{total_cells}）",
    }


def assess_consistency(rows: list[dict]) -> dict[str, Any]:
    """维度2：一致性 — 跨字段逻辑一致性。"""
    if not rows:
        return {"score": 0, "detail": "无数据"}
    checks = 0
    consistent = 0
    for r in rows:
        # 检查1：最新价格 vs 参考价格 是否同量级（差异 <50%）
        price = _to_float(r.get("最新价格"))
        ref = r.get("参考价格", "")
        if price is not None and ref:
            ref_num = _extract_price_from_ref(ref)
            if ref_num is not None and ref_num > 0:
                checks += 1
                ratio = abs(price - ref_num) / max(price, ref_num)
                if ratio < 0.5:
                    consistent += 1
        # 检查2：城市名称非空
        checks += 1
        if not is_missing(r.get("城市名称")):
            consistent += 1
    rate = consistent / checks if checks else 1.0
    return {
        "score": round(rate * 100),
        "consistent_checks": consistent,
        "total_checks": checks,
        "detail": f"一致性通过率 {rate:.1%}（{consistent}/{checks}）",
    }


def assess_accuracy(rows: list[dict]) -> dict[str, Any]:
    """维度3：准确性 — 值在合理范围内占比。"""
    if not rows:
        return {"score": 0, "detail": "无数据"}
    valid = 0
    total = 0
    for r in rows:
        price = _to_float(r.get("最新价格"))
        if price is not None:
            total += 1
            if PRICE_MIN <= price <= PRICE_MAX:
                valid += 1
    rate = valid / total if total else 1.0
    return {
        "score": round(rate * 100),
        "valid_values": valid,
        "total_checked": total,
        "detail": f"价格范围准确率 {rate:.1%}（{valid}/{total}）",
    }


def assess_timeliness(rows: list[dict]) -> dict[str, Any]:
    """维度4：时效性 — 数据新鲜度。"""
    if not rows:
        return {"score": 0, "detail": "无数据"}
    now = datetime.now()
    recent_count = 0
    total_dates = 0
    for r in rows:
        date_str = r.get("开盘日期", "") or r.get("open_date", "")
        if is_missing(date_str):
            continue
        parsed = _parse_date(str(date_str))
        if parsed is None:
            continue
        total_dates += 1
        days_old = (now - parsed).days
        if days_old <= 730:  # 2 年内
            recent_count += 1
    rate = recent_count / total_dates if total_dates else 0.0
    # 如果没有日期数据，给中等分
    if total_dates == 0:
        return {"score": 50, "detail": "无开盘日期数据，时效性无法评估"}
    return {
        "score": round(rate * 100),
        "recent_records": recent_count,
        "total_dated_records": total_dates,
        "detail": f"2年内数据占比 {rate:.1%}（{recent_count}/{total_dates}）",
    }


def assess_uniqueness(rows: list[dict]) -> dict[str, Any]:
    """维度5：唯一性 — 非重复行占比。"""
    if not rows:
        return {"score": 0, "detail": "无数据"}
    marked = _t4_dedup(rows)
    dup_rows = sum(1 for r in marked if r.get("dup_group_id", -1) >= 0)
    unique_rows = len(rows) - dup_rows
    rate = unique_rows / len(rows) if rows else 0
    return {
        "score": round(rate * 100),
        "unique_rows": unique_rows,
        "duplicate_rows": dup_rows,
        "total_rows": len(rows),
        "detail": f"唯一率 {rate:.1%}（{unique_rows}/{len(rows)}，重复 {dup_rows}）",
    }


def assess_representativeness(rows: list[dict]) -> dict[str, Any]:
    """维度6：代表性 — 城市/区域覆盖广度。"""
    if not rows:
        return {"score": 0, "detail": "无数据"}
    cities = set()
    districts = set()
    for r in rows:
        city = r.get("城市名称", "")
        if not is_missing(city):
            cities.add(str(city).strip())
        district = r.get("区域名称", "")
        if not is_missing(district):
            districts.add(str(district).strip())
    city_count = len(cities)
    district_count = len(districts)
    # 评分：城市数 ×区域数 归一化到 100
    score = min(100, city_count * 20 + district_count * 5)
    return {
        "score": score,
        "city_count": city_count,
        "district_count": district_count,
        "cities": sorted(cities),
        "detail": f"覆盖 {city_count} 城市、{district_count} 区域",
    }


def assess_bias(rows: list[dict]) -> dict[str, Any]:
    """维度7：偏差 — 价格分布偏度。"""
    prices = []
    for r in rows:
        p = _to_float(r.get("最新价格"))
        if p is not None and p > 0:
            prices.append(p)
    if len(prices) < 3:
        return {"score": 70, "detail": "样本不足，偏差无法评估"}
    # 计算偏度 (Skewness)
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return {"score": 100, "detail": "价格无方差"}
    skewness = sum((p - mean) ** 3 for p in prices) / (len(prices) * std ** 3)
    # 偏度绝对值越小越好（|skew| < 1 接近正态）
    bias_score = max(0, 100 - abs(skewness) * 30)
    return {
        "score": round(bias_score),
        "skewness": round(skewness, 4),
        "mean_price": round(mean, 2),
        "std_price": round(std, 2),
        "detail": f"偏度 {skewness:.2f}（{'右偏' if skewness > 0 else '左偏'}）",
    }


def assess_privacy(rows: list[dict]) -> dict[str, Any]:
    """维度8：隐私 — 敏感信息暴露风险。"""
    if not rows:
        return {"score": 0, "detail": "无数据"}
    sensitive_count = 0
    sensitive_types: dict[str, int] = {}
    for r in rows:
        row_text = json.dumps(r, ensure_ascii=False)
        found = False
        for name, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(row_text):
                sensitive_types[name] = sensitive_types.get(name, 0) + 1
                found = True
        if found:
            sensitive_count += 1
    # 暴露风险越低分越高
    exposure_rate = sensitive_count / len(rows) if rows else 0
    score = round((1 - min(1.0, exposure_rate)) * 100)
    # 如果是 400 电话（公开商业电话），不算严重风险
    if "phone_400" in sensitive_types and len(sensitive_types) == 1:
        score = max(score, 80)  # 400 电话是公开的
    return {
        "score": score,
        "sensitive_rows": sensitive_count,
        "sensitive_types": sensitive_types,
        "exposure_rate": round(exposure_rate, 4),
        "detail": f"敏感信息暴露率 {exposure_rate:.1%}（{sensitive_count}/{len(rows)}）",
    }


# ═══════════════════════════════════════════════════════════════════════
#  综合评估
# ═══════════════════════════════════════════════════════════════════════

def assess_quality(rows: list[dict]) -> dict[str, Any]:
    """
    执行八维质量评估。

    参数:
        rows: 行列表

    返回:
        包含八维分数 + 综合分 + 标准工件文本的字典：
        ``{
            "dimensions": {...},
            "overall_score": float,
            "grade": str,
            "artifacts": {"quality_report_md": str, "data_card_md": str, "data_feasibility_md": str}
        }``
    """
    dimensions = {
        "completeness": assess_completeness(rows),
        "consistency": assess_consistency(rows),
        "accuracy": assess_accuracy(rows),
        "timeliness": assess_timeliness(rows),
        "uniqueness": assess_uniqueness(rows),
        "representativeness": assess_representativeness(rows),
        "bias": assess_bias(rows),
        "privacy": assess_privacy(rows),
    }
    # 加权综合分
    overall = sum(dimensions[d]["score"] * w for d, w in DIMENSION_WEIGHTS.items())
    overall = round(overall, 1)
    grade = _score_to_grade(overall)

    return {
        "dimensions": dimensions,
        "overall_score": overall,
        "grade": grade,
        "weights": DIMENSION_WEIGHTS,
        "total_rows": len(rows),
        "assessed_at": datetime.now().isoformat(),
        "artifacts": {
            "quality_report_md": _gen_quality_report_md(dimensions, overall, grade, len(rows)),
            "data_card_md": _gen_data_card_md(dimensions, overall, grade, rows),
            "data_feasibility_md": _gen_data_feasibility_md(dimensions, overall, grade, rows),
        },
    }


def _score_to_grade(score: float) -> str:
    """分数转等级。"""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"


# ═══════════════════════════════════════════════════════════════════════
#  工件生成
# ═══════════════════════════════════════════════════════════════════════

def _gen_quality_report_md(dims: dict, overall: float, grade: str, n: int) -> str:
    """生成 quality_report.md。"""
    lines = [
        "# 数据质量评估报告",
        "",
        f"- **评估时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- **数据行数**: {n}",
        f"- **综合得分**: {overall} / 100",
        f"- **质量等级**: {grade}",
        "",
        "## 八维质量指标",
        "",
        "| 维度 | 分数 | 详情 |",
        "|------|------|------|",
    ]
    dim_names = {
        "completeness": "完整性", "consistency": "一致性", "accuracy": "准确性",
        "timeliness": "时效性", "uniqueness": "唯一性",
        "representativeness": "代表性", "bias": "偏差", "privacy": "隐私",
    }
    for d in ["completeness", "consistency", "accuracy", "timeliness",
              "uniqueness", "representativeness", "bias", "privacy"]:
        info = dims[d]
        lines.append(f"| {dim_names[d]} | {info['score']} | {info['detail']} |")
    lines.append("")
    lines.append("## 改进建议")
    lines.append("")
    for d in ["completeness", "consistency", "accuracy", "timeliness",
              "uniqueness", "representativeness", "bias", "privacy"]:
        info = dims[d]
        if info["score"] < 80:
            lines.append(f"- **{dim_names[d]}** ({info['score']}分): {info['detail']} — 建议优先补采")
    return "\n".join(lines)


def _gen_data_card_md(dims: dict, overall: float, grade: str, rows: list[dict]) -> str:
    """生成 data_card.md。"""
    cities = set()
    for r in rows:
        c = r.get("城市名称", "")
        if not is_missing(c):
            cities.add(str(c).strip())
    lines = [
        "# 数据卡片 (Data Card)",
        "",
        f"- **数据集名称**: DDS 楼盘数据库",
        f"- **记录数**: {len(rows)}",
        f"- **城市覆盖**: {len(cities)} 个城市",
        f"- **综合质量分**: {overall} / 100 ({grade})",
        f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## 质量维度摘要",
        "",
        f"- 完整性: {dims['completeness']['score']}",
        f"- 准确性: {dims['accuracy']['score']}",
        f"- 唯一性: {dims['uniqueness']['score']}",
        f"- 代表性: {dims['representativeness']['score']}",
        "",
        "## 使用须知",
        "",
        "- 本数据集用于 DDS 地产决策支持，非交易级数据",
        "- 价格为挂牌/参考价，非实际成交价",
        "- 坐标为 BD-09 体系，使用前需转换",
    ]
    return "\n".join(lines)


def _gen_data_feasibility_md(dims: dict, overall: float, grade: str, rows: list[dict]) -> str:
    """生成 data_feasibility.md。"""
    feasible = overall >= 70
    lines = [
        "# 数据可行性评估",
        "",
        f"- **综合质量分**: {overall} / 100 ({grade})",
        f"- **可行性判定**: {'可用' if feasible else '需改进后使用'}",
        "",
        "## 适用场景",
        "",
    ]
    if dims["completeness"]["score"] >= 70:
        lines.append("- [x] 竞品分析（价格带对比）")
    else:
        lines.append("- [ ] 竞品分析（完整性不足）")
    if dims["accuracy"]["score"] >= 70:
        lines.append("- [x] 价格估值参考")
    else:
        lines.append("- [ ] 价格估值参考（准确性不足）")
    if dims["representativeness"]["score"] >= 60:
        lines.append("- [x] 区域市场概况")
    else:
        lines.append("- [ ] 区域市场概况（代表性不足）")
    lines += [
        "",
        "## 风险提示",
        "",
        "- 价格为挂牌价，实际成交价可能有偏差",
        "- 历史数据可能存在合成/克隆数据，需甄别来源标记",
        "- 学区/物业等动态信息需交叉验证",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _to_float(value: Any) -> float | None:
    """安全转 float。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("元/㎡", "").replace("元/平米", "")
    if s in ("", "nan", "none", "null", "无"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _extract_price_from_ref(ref: str) -> float | None:
    """从参考价格字段中提取数字。"""
    if not ref:
        return None
    match = re.search(r"[\d.]+", str(ref))
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def _parse_date(s: str) -> datetime | None:
    """解析日期字符串。"""
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y年%m月%d日", "%Y-%m"]:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


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
    """内置自测：验证八维分数计算正确。"""
    test_rows = [
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "区域名称": "海棠区", "最新价格": "35000", "参考价格": "34000元/㎡", "地址": "南田路16号", "开盘日期": "2025-06-01", "百度地图纬度": "18.41", "百度地图经度": "109.71", "400电话": "4001234567"},
        {"楼盘名称": "亚龙湾翡翠谷", "城市名称": "三亚", "区域名称": "吉阳区", "最新价格": "28000", "参考价格": "29000元/㎡", "地址": "亚龙湾路88号", "开盘日期": "2024-12-15", "百度地图纬度": "18.20", "百度地图经度": "109.50", "400电话": ""},
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "区域名称": "海棠区", "最新价格": "35500", "参考价格": "35000元/㎡", "地址": "南田路16号", "开盘日期": "2025-06-01", "百度地图纬度": "18.41", "百度地图经度": "109.71", "400电话": "4001234567"},  # 重复
        {"楼盘名称": "清水湾度假村", "城市名称": "三亚", "区域名称": "陵水县", "最新价格": "22000", "参考价格": "", "地址": "清水湾大道1号", "开盘日期": "2023-03-01", "百度地图纬度": "18.50", "百度地图经度": "110.00", "400电话": ""},
        {"楼盘名称": "西湖一号", "城市名称": "杭州", "区域名称": "西湖区", "最新价格": "45000", "参考价格": "46000元/㎡", "地址": "天目山路1号", "开盘日期": "2025-09-01", "百度地图纬度": "30.26", "百度地图经度": "120.15", "400电话": ""},
        {"楼盘名称": "钱江新城", "城市名称": "杭州", "区域名称": "江干区", "最新价格": "38000", "参考价格": "39000元/㎡", "地址": "四季大道2号", "开盘日期": "2025-01-20", "百度地图纬度": "30.30", "百度地图经度": "120.20", "400电话": ""},
    ]

    result = assess_quality(test_rows)
    dims = result["dimensions"]

    # 断言 1：八维全部有分数
    assert len(dims) == 8, f"应有 8 个维度，实际 {len(dims)}"
    for d_name, d_info in dims.items():
        assert "score" in d_info, f"维度 {d_name} 应有 score"
        assert 0 <= d_info["score"] <= 100, f"维度 {d_name} 分数应在 0-100，实际 {d_info['score']}"
    print(f"  [OK] 八维全部有分数（0-100）")

    # 断言 2：综合分在合理范围
    overall = result["overall_score"]
    assert 0 <= overall <= 100, f"综合分应在 0-100，实际 {overall}"
    print(f"  [OK] 综合分：{overall} ({result['grade']})")

    # 断言 3：完整性 > 0（有数据）
    assert dims["completeness"]["score"] > 0, "完整性应 > 0"
    print(f"  [OK] 完整性：{dims['completeness']['score']} — {dims['completeness']['detail']}")

    # 断言 4：唯一性 < 100（有重复行）
    assert dims["uniqueness"]["score"] < 100, f"有重复行，唯一性应 < 100，实际 {dims['uniqueness']['score']}"
    assert dims["uniqueness"]["duplicate_rows"] >= 1, "应检测到重复行"
    print(f"  [OK] 唯一性：{dims['uniqueness']['score']} — {dims['uniqueness']['detail']}")

    # 断言 5：代表性检测到 2 个城市
    assert dims["representativeness"]["city_count"] == 2, \
        f"应检测到 2 个城市，实际 {dims['representativeness']['city_count']}"
    print(f"  [OK] 代表性：{dims['representativeness']['score']} — {dims['representativeness']['detail']}")

    # 断言 6：偏差有偏度值
    assert "skewness" in dims["bias"], "偏差维度应有偏度值"
    print(f"  [OK] 偏差：{dims['bias']['score']} — {dims['bias']['detail']}")

    # 断言 7：隐私检测到 400 电话
    assert dims["privacy"]["sensitive_rows"] >= 1, "应检测到敏感信息（400电话）"
    print(f"  [OK] 隐私：{dims['privacy']['score']} — {dims['privacy']['detail']}")

    # 断言 8：三个工件全部生成
    artifacts = result["artifacts"]
    assert "quality_report_md" in artifacts
    assert "data_card_md" in artifacts
    assert "data_feasibility_md" in artifacts
    assert "数据质量评估报告" in artifacts["quality_report_md"]
    assert "数据卡片" in artifacts["data_card_md"]
    assert "可行性" in artifacts["data_feasibility_md"]
    print(f"  [OK] 三个工件全部生成")

    print("\n  === T8 数据质量评估 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="T8 数据质量评估 — 八维指标 + 标准工件"
    )
    parser.add_argument("--csv", type=str, help="输入 CSV 文件路径")
    parser.add_argument("--city", type=str, default="三亚", help="城市名（用于查找默认 CSV）")
    parser.add_argument("--outdir", type=str, default="data_out/governance/t8_quality/",
                        help="输出目录")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    # 确定输入 CSV
    csv_path = args.csv
    if not csv_path:
        vault = Path(__file__).resolve().parent.parent.parent / "Vault"
        csv_path = str(vault / "2026新楼盘" / f"新楼盘-{args.city}.csv")

    print(f"[T8] 读取数据：{csv_path}")
    rows = read_csv_rows(csv_path)
    print(f"[T8] 总行数：{len(rows)}")

    result = assess_quality(rows)

    # 写工件
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # quality_report.md
    (outdir / "quality_report.md").write_text(
        result["artifacts"]["quality_report_md"], encoding="utf-8"
    )
    # data_card.md
    (outdir / "data_card.md").write_text(
        result["artifacts"]["data_card_md"], encoding="utf-8"
    )
    # data_feasibility.md
    (outdir / "data_feasibility.md").write_text(
        result["artifacts"]["data_feasibility_md"], encoding="utf-8"
    )
    # quality_scores.json
    scores = {d: info["score"] for d, info in result["dimensions"].items()}
    scores["overall"] = result["overall_score"]
    scores["grade"] = result["grade"]
    with open(outdir / "quality_scores.json", "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)

    print(f"[T8] 工件已写入：{outdir}")
    print(f"[T8] 综合分：{result['overall_score']} ({result['grade']})")
    for d, info in result["dimensions"].items():
        print(f"      {d:25s}: {info['score']:3d} — {info['detail']}")


if __name__ == "__main__":
    main()
