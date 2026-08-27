# -*- coding: utf-8 -*-
"""
漂移监控模块 — reference vs current 六维对比 + 三级告警
=====================================================
功能：对比参考画像（reference profile）与当前数据，检测六维漂移：
  1. **价格分布** — KS 检验（Kolmogorov-Smirnov）
  2. **填充率差异** — 各列填充率变化
  3. **城市覆盖** — 城市集合变化
  4. **坐标偏移** — 坐标中心偏移距离
  5. **Schema 版本** — 列结构变化
  6. **数据量变化** — 行数增减比例

告警级别
--------
| 级别  | 颜色 | 行动           |
|-------|------|----------------|
| red   | 红   | 阻断，立即处理 |
| orange| 橙   | 调查原因       |
| yellow| 黄   | 持续监控       |
| green | 绿   | 无漂移         |

CLI
---
    python scripts/governance/drift_monitor.py --init --vault Vault/
    python scripts/governance/drift_monitor.py --check --reference governance/drift/reference_profile.json --csv current.csv
    python scripts/governance/drift_monitor.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 复用 T5 缺失值判定
sys.path.insert(0, str(Path(__file__).resolve().parent))
from t5_missing_profile import is_missing  # noqa: E402


# ── 告警阈值 ──────────────────────────────────────────────────────────
THRESHOLDS = {
    "price_ks": {"yellow": 0.10, "orange": 0.20, "red": 0.30},
    "fill_rate_diff": {"yellow": 0.05, "orange": 0.15, "red": 0.30},
    "city_coverage_change": {"yellow": 0.05, "orange": 0.15, "red": 0.30},
    "coord_offset_km": {"yellow": 1.0, "orange": 5.0, "red": 20.0},
    "schema_change": {"yellow": 1, "orange": 5, "red": 10},
    "volume_change_pct": {"yellow": 0.10, "orange": 0.30, "red": 0.50},
}

ALERT_COLORS = {"red": "#e74c3c", "orange": "#e67e22", "yellow": "#f1c40f", "green": "#2ecc71"}


# ═══════════════════════════════════════════════════════════════════════
#  数据画像构建
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


def build_profile(rows: list[dict]) -> dict[str, Any]:
    """
    构建数据画像（reference profile）。

    参数:
        rows: 行列表

    返回:
        包含六维统计信息的画像字典
    """
    if not rows:
        return {"total_rows": 0, "created_at": datetime.now().isoformat()}

    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())

    # 1. 价格分布
    prices = [_to_float(r.get("最新价格")) for r in rows]
    prices = [p for p in prices if p is not None and p > 0]
    price_profile = {
        "count": len(prices),
        "mean": round(sum(prices) / len(prices), 2) if prices else 0,
        "min": min(prices) if prices else 0,
        "max": max(prices) if prices else 0,
        "median": _median(prices) if prices else 0,
        "p25": _percentile(prices, 25) if prices else 0,
        "p75": _percentile(prices, 75) if prices else 0,
        "sorted_values": sorted(prices) if len(prices) <= 1000 else sorted(prices)[:1000],  # 限制大小
    }

    # 2. 填充率
    fill_rates = {}
    for k in all_keys:
        filled = sum(1 for r in rows if not is_missing(r.get(k)))
        fill_rates[k] = round(filled / len(rows), 4)

    # 3. 城市覆盖
    cities = set()
    for r in rows:
        c = r.get("城市名称", "")
        if not is_missing(c):
            cities.add(str(c).strip())

    # 4. 坐标中心
    lats = [_to_float(r.get("lat") or r.get("百度地图纬度")) for r in rows]
    lngs = [_to_float(r.get("lng") or r.get("百度地图经度")) for r in rows]
    lats = [x for x in lats if x is not None and x != 0]
    lngs = [x for x in lngs if x is not None and x != 0]
    coord_center = {
        "lat_median": _median(lats) if lats else None,
        "lng_median": _median(lngs) if lngs else None,
        "valid_count": len(lats),
    }

    # 5. Schema 版本
    schema = {
        "column_count": len(all_keys),
        "columns": sorted(all_keys),
    }

    return {
        "total_rows": len(rows),
        "created_at": datetime.now().isoformat(),
        "price_profile": price_profile,
        "fill_rates": fill_rates,
        "cities": sorted(cities),
        "city_count": len(cities),
        "coord_center": coord_center,
        "schema": schema,
    }


# ═══════════════════════════════════════════════════════════════════════
#  六维漂移检测
# ═══════════════════════════════════════════════════════════════════════

def _ks_statistic(sorted_a: list[float], sorted_b: list[float]) -> float:
    """
    计算 Kolmogorov-Smirnov 统计量（两样本 KS 检验）。

    KS = max|CDF_a(x) - CDF_b(x)|

    参数:
        sorted_a: 排序后的样本 A
        sorted_b: 排序后的样本 B

    返回:
        KS 统计量（0~1）
    """
    if not sorted_a or not sorted_b:
        return 0.0
    na, nb = len(sorted_a), len(sorted_b)
    all_values = sorted(set(sorted_a + sorted_b))
    max_diff = 0.0
    for v in all_values:
        cdf_a = sum(1 for x in sorted_a if x <= v) / na
        cdf_b = sum(1 for x in sorted_b if x <= v) / nb
        diff = abs(cdf_a - cdf_b)
        max_diff = max(max_diff, diff)
    return round(max_diff, 4)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine 距离（km）。"""
    radius = 6371.0088
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return radius * 2 * math.asin(math.sqrt(a))


def check_drift(
    reference: dict[str, Any],
    current_rows: list[dict],
) -> dict[str, Any]:
    """
    对比参考画像与当前数据，检测六维漂移。

    参数:
        reference: 参考画像（``build_profile()`` 的返回值）
        current_rows: 当前数据行列表

    返回:
        ``drift_alert`` 字典，包含六维漂移结果 + 综合告警级别
    """
    current = build_profile(current_rows)

    results = {}

    # 1. 价格分布 KS 检验
    ref_prices = reference.get("price_profile", {}).get("sorted_values", [])
    cur_prices = current.get("price_profile", {}).get("sorted_values", [])
    ks_stat = _ks_statistic(ref_prices, cur_prices) if ref_prices and cur_prices else 0.0
    price_level = _level_from_threshold(ks_stat, THRESHOLDS["price_ks"])
    results["price_ks"] = {
        "statistic": ks_stat,
        "level": price_level,
        "ref_mean": reference.get("price_profile", {}).get("mean", 0),
        "cur_mean": current.get("price_profile", {}).get("mean", 0),
        "detail": f"KS 统计量 {ks_stat:.4f}（参考均值 {reference.get('price_profile', {}).get('mean', 0)} → 当前均值 {current.get('price_profile', {}).get('mean', 0)}）",
    }

    # 2. 填充率差异
    ref_fill = reference.get("fill_rates", {})
    cur_fill = current.get("fill_rates", {})
    all_cols = set(ref_fill.keys()) | set(cur_fill.keys())
    max_fill_diff = 0.0
    fill_diffs = {}
    for col in all_cols:
        r = ref_fill.get(col, 0)
        c = cur_fill.get(col, 0)
        diff = abs(c - r)
        fill_diffs[col] = {"ref": r, "cur": c, "diff": round(diff, 4)}
        max_fill_diff = max(max_fill_diff, diff)
    fill_level = _level_from_threshold(max_fill_diff, THRESHOLDS["fill_rate_diff"])
    results["fill_rate_diff"] = {
        "max_diff": round(max_fill_diff, 4),
        "level": fill_level,
        "top_diffs": sorted(fill_diffs.items(), key=lambda x: x[1]["diff"], reverse=True)[:5],
        "detail": f"最大填充率差异 {max_fill_diff:.1%}",
    }

    # 3. 城市覆盖变化
    ref_cities = set(reference.get("cities", []))
    cur_cities = set(current.get("cities", []))
    added = cur_cities - ref_cities
    removed = ref_cities - cur_cities
    coverage_change = len(added) + len(removed)
    ref_count = len(ref_cities) if ref_cities else 1
    coverage_pct = coverage_change / ref_count if ref_count else 0
    city_level = _level_from_threshold(coverage_pct, THRESHOLDS["city_coverage_change"])
    results["city_coverage"] = {
        "added": sorted(added),
        "removed": sorted(removed),
        "change_count": coverage_change,
        "change_pct": round(coverage_pct, 4),
        "level": city_level,
        "detail": f"新增 {len(added)} 城市，移除 {len(removed)} 城市",
    }

    # 4. 坐标偏移
    ref_coord = reference.get("coord_center", {})
    cur_coord = current.get("coord_center", {})
    ref_lat = ref_coord.get("lat_median")
    ref_lng = ref_coord.get("lng_median")
    cur_lat = cur_coord.get("lat_median")
    cur_lng = cur_coord.get("lng_median")
    if all(v is not None for v in [ref_lat, ref_lng, cur_lat, cur_lng]):
        offset = _haversine_km(ref_lat, ref_lng, cur_lat, cur_lng)
    else:
        offset = 0.0
    coord_level = _level_from_threshold(offset, THRESHOLDS["coord_offset_km"])
    results["coord_offset"] = {
        "offset_km": round(offset, 2),
        "ref_center": {"lat": ref_lat, "lng": ref_lng},
        "cur_center": {"lat": cur_lat, "lng": cur_lng},
        "level": coord_level,
        "detail": f"坐标中心偏移 {offset:.2f} km",
    }

    # 5. Schema 变化
    ref_cols = set(reference.get("schema", {}).get("columns", []))
    cur_cols = set(current.get("schema", {}).get("columns", []))
    cols_added = cur_cols - ref_cols
    cols_removed = ref_cols - cur_cols
    schema_change = len(cols_added) + len(cols_removed)
    schema_level = _level_from_threshold(schema_change, THRESHOLDS["schema_change"])
    results["schema_change"] = {
        "columns_added": sorted(cols_added),
        "columns_removed": sorted(cols_removed),
        "change_count": schema_change,
        "level": schema_level,
        "detail": f"新增 {len(cols_added)} 列，移除 {len(cols_removed)} 列",
    }

    # 6. 数据量变化
    ref_rows = reference.get("total_rows", 0)
    cur_rows = current.get("total_rows", 0)
    if ref_rows > 0:
        volume_change = abs(cur_rows - ref_rows) / ref_rows
    else:
        volume_change = 1.0 if cur_rows > 0 else 0.0
    vol_level = _level_from_threshold(volume_change, THRESHOLDS["volume_change_pct"])
    results["volume_change"] = {
        "ref_rows": ref_rows,
        "cur_rows": cur_rows,
        "change_pct": round(volume_change, 4),
        "direction": "increase" if cur_rows > ref_rows else "decrease",
        "level": vol_level,
        "detail": f"行数 {ref_rows} → {cur_rows}（变化 {volume_change:.1%}）",
    }

    # 综合告警级别（取最高）
    levels = [r["level"] for r in results.values()]
    overall_level = _max_level(levels)

    alert_dict = {
        "alert_level": overall_level,
        "checked_at": datetime.now().isoformat(),
        "reference_created_at": reference.get("created_at", ""),
        "results": results,
        "summary": {
            "ref_rows": ref_rows,
            "cur_rows": cur_rows,
            "dimensions_checked": 6,
            "alerts": sum(1 for l in levels if l != "green"),
        },
    }

    # ── 通知告警（尽力而为，不影响主流程） ──────────────────────────────
    if overall_level in ("red", "orange"):
        try:
            from scripts.governance.notify import get_notifier  # noqa: E402
            notifier = get_notifier()
            triggered_dims = [
                f"{dim}({r['level']})" for dim, r in results.items()
                if r["level"] in ("red", "orange", "yellow")
            ]
            notifier.send(
                module="drift_monitor",
                level=overall_level,
                title=f"数据漂移告警：{overall_level}",
                summary=f"漂移维度数：{alert_dict['summary']['alerts']}，"
                        f"最高告警级别：{overall_level}，"
                        f"触发检查项：{', '.join(triggered_dims)}",
                detail=alert_dict,
            )
        except Exception:
            pass  # 通知失败不影响漂移检测主流程

    return alert_dict


def _level_from_threshold(value: float, thresholds: dict[str, float]) -> str:
    """根据阈值判定告警级别。"""
    if value >= thresholds["red"]:
        return "red"
    elif value >= thresholds["orange"]:
        return "orange"
    elif value >= thresholds["yellow"]:
        return "yellow"
    else:
        return "green"


def _max_level(levels: list[str]) -> str:
    """取最高告警级别。"""
    priority = {"red": 4, "orange": 3, "yellow": 2, "green": 1}
    return max(levels, key=lambda l: priority.get(l, 0)) if levels else "green"


def _median(values: list[float]) -> float:
    """计算中位数。"""
    if not values:
        return 0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 0:
        return round((sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2, 6)
    return round(sorted_vals[n // 2], 6)


def _percentile(values: list[float], p: float) -> float:
    """计算百分位数。"""
    if not values:
        return 0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return round(sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f]), 6)


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
    """内置自测：reference + current（含已知漂移），验证告警正确触发。"""
    # 参考数据（基准画像）
    reference_rows = [
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "最新价格": "35000", "lat": "18.41", "lng": "109.71", "区域名称": "海棠区"},
        {"楼盘名称": "亚龙湾翡翠谷", "城市名称": "三亚", "最新价格": "28000", "lat": "18.20", "lng": "109.50", "区域名称": "吉阳区"},
        {"楼盘名称": "清水湾度假村", "城市名称": "三亚", "最新价格": "22000", "lat": "18.50", "lng": "110.00", "区域名称": "陵水县"},
        {"楼盘名称": "西湖一号", "城市名称": "杭州", "最新价格": "45000", "lat": "30.26", "lng": "120.15", "区域名称": "西湖区"},
        {"楼盘名称": "钱江新城", "城市名称": "杭州", "最新价格": "38000", "lat": "30.30", "lng": "120.20", "区域名称": "江干区"},
    ]

    # 当前数据（含已知漂移）
    current_rows = [
        # 价格大幅上涨（漂移）
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "最新价格": "55000", "lat": "18.41", "lng": "109.71", "区域名称": "海棠区"},
        {"楼盘名称": "亚龙湾翡翠谷", "城市名称": "三亚", "最新价格": "48000", "lat": "18.20", "lng": "109.50", "区域名称": "吉阳区"},
        {"楼盘名称": "清水湾度假村", "城市名称": "三亚", "最新价格": "42000", "lat": "18.50", "lng": "110.00", "区域名称": "陵水县"},
        # 城市覆盖变化：杭州消失，新增济南
        {"楼盘名称": "西湖一号", "城市名称": "济南", "最新价格": "45000", "lat": "30.26", "lng": "120.15", "区域名称": "西湖区"},
        {"楼盘名称": "钱江新城", "城市名称": "济南", "最新价格": "38000", "lat": "30.30", "lng": "120.20", "区域名称": "江干区"},
        # 新增数据
        {"楼盘名称": "大明湖畔", "城市名称": "济南", "最新价格": "30000", "lat": "36.65", "lng": "117.00", "区域名称": "历下区"},
    ]

    # 构建参考画像
    reference = build_profile(reference_rows)
    assert reference["total_rows"] == 5, f"参考画像行数应为 5，实际 {reference['total_rows']}"
    assert reference["city_count"] == 2, f"参考城市数应为 2，实际 {reference['city_count']}"
    print(f"  [OK] 参考画像构建：{reference['total_rows']} 行，{reference['city_count']} 城市")

    # 检测漂移
    alert = check_drift(reference, current_rows)
    results = alert["results"]

    # 断言 1：六维全部检测
    assert len(results) == 6, f"应有 6 个维度，实际 {len(results)}"
    print(f"  [OK] 六维漂移检测完成")

    # 断言 2：价格分布漂移（KS > 0）
    ks = results["price_ks"]
    assert ks["statistic"] > 0, f"KS 统计量应 > 0（有价格漂移），实际 {ks['statistic']}"
    assert ks["level"] != "green", f"价格漂移应触发告警，实际 {ks['level']}"
    print(f"  [OK] 价格分布漂移：KS={ks['statistic']:.4f}，级别={ks['level']}")

    # 断言 3：城市覆盖变化（杭州→济南）
    city = results["city_coverage"]
    assert "济南" in city["added"], f"应新增济南城市，实际 {city['added']}"
    assert "杭州" in city["removed"], f"应移除杭州城市，实际 {city['removed']}"
    assert city["level"] != "green", f"城市覆盖变化应触发告警"
    print(f"  [OK] 城市覆盖变化：新增 {city['added']}，移除 {city['removed']}，级别={city['level']}")

    # 断言 4：数据量变化（5→6，增长 20%）
    vol = results["volume_change"]
    assert vol["cur_rows"] == 6, f"当前行数应为 6，实际 {vol['cur_rows']}"
    assert vol["change_pct"] > 0, f"应有数据量增长"
    print(f"  [OK] 数据量变化：{vol['ref_rows']} → {vol['cur_rows']}（{vol['change_pct']:.1%}），级别={vol['level']}")

    # 断言 5：综合告警级别不为 green（至少有漂移）
    assert alert["alert_level"] != "green", \
        f"综合告警级别不应为 green（有漂移），实际 {alert['alert_level']}"
    print(f"  [OK] 综合告警级别：{alert['alert_level']}")

    # 断言 6：填充率检测正常
    fill = results["fill_rate_diff"]
    assert "max_diff" in fill
    print(f"  [OK] 填充率差异：{fill['max_diff']:.1%}，级别={fill['level']}")

    # 断言 7：Schema 变化检测
    schema = results["schema_change"]
    assert "columns_added" in schema
    print(f"  [OK] Schema 变化：新增 {len(schema['columns_added'])} 列，移除 {len(schema['columns_removed'])} 列")

    # 断言 8：坐标偏移检测
    coord = results["coord_offset"]
    assert "offset_km" in coord
    print(f"  [OK] 坐标偏移：{coord['offset_km']:.2f} km，级别={coord['level']}")

    # 断言 9：无漂移场景
    no_drift_alert = check_drift(reference, reference_rows)
    assert no_drift_alert["alert_level"] == "green", \
        f"相同数据应无漂移（green），实际 {no_drift_alert['alert_level']}"
    print(f"  [OK] 无漂移场景：相同数据告警级别 = green")

    print("\n  === 漂移监控 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="漂移监控 — reference vs current 六维对比 + 三级告警"
    )
    parser.add_argument("--init", action="store_true", help="初始化参考画像")
    parser.add_argument("--check", action="store_true", help="执行漂移检测")
    parser.add_argument("--vault", type=str, default="Vault/", help="Vault 根目录（--init 模式）")
    parser.add_argument("--csv", type=str, help="当前数据 CSV 路径（--check 模式）")
    parser.add_argument("--reference", type=str,
                        default="data_out/governance/drift/reference_profile.json",
                        help="参考画像 JSON 路径")
    parser.add_argument("--out", type=str, default=None, help="漂移报告输出路径")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    if args.init:
        # 初始化参考画像
        vault_path = Path(args.vault)
        if not vault_path.is_absolute():
            vault_path = Path(__file__).resolve().parent.parent.parent / args.vault

        print(f"[drift] 扫描 Vault 构建参考画像：{vault_path}")
        # 扫描所有 CSV
        all_rows = []
        for csv_file in vault_path.rglob("*.csv"):
            if csv_file.name.startswith("_"):
                continue
            try:
                rows = read_csv_rows(csv_file)
                all_rows.extend(rows[:100])  # 每文件采样 100 行
            except Exception:
                continue

        profile = build_profile(all_rows)
        ref_path = Path(args.reference)
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ref_path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        print(f"[drift] 参考画像已写入：{ref_path}")
        print(f"[drift] 行数：{profile['total_rows']}，城市：{profile['city_count']}")

    elif args.check:
        # 执行漂移检测
        ref_path = Path(args.reference)
        if not ref_path.exists():
            print(f"[drift] 错误：参考画像不存在：{ref_path}")
            print(f"[drift] 请先运行 --init 创建参考画像")
            return

        with open(ref_path, "r", encoding="utf-8") as f:
            reference = json.load(f)

        if not args.csv:
            print("[drift] 错误：--check 模式需要 --csv 参数指定当前数据")
            return

        print(f"[drift] 读取当前数据：{args.csv}")
        current_rows = read_csv_rows(args.csv)
        print(f"[drift] 当前行数：{len(current_rows)}")

        alert = check_drift(reference, current_rows)

        # 输出报告
        out_path = Path(args.out) if args.out else Path("drift_alert.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(alert, f, ensure_ascii=False, indent=2)
        print(f"[drift] 漂移报告已写入：{out_path}")

        print(f"\n[drift] 综合告警级别：{alert['alert_level'].upper()}")
        for dim, result in alert["results"].items():
            level = result["level"]
            marker = {"red": "[!]", "orange": "[*]", "yellow": "[~]", "green": "[ok]"}[level]
            print(f"  {marker} {dim:25s}: {result['detail']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
