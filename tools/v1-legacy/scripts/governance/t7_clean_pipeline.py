# -*- coding: utf-8 -*-
"""
T7 清洗管道 — DeepSeek 三阶段数据预处理
=========================================
功能：借鉴 DeepSeek 三阶段方法论，对楼盘数据执行：
  1. **去重**（Dedup）：MinHash 近似匹配，标记 dup_group_id
  2. **过滤**（Filter）：规则引擎检测异常价格/面积/坐标，标记 anomaly_flag
  3. **混洗**（Shuffle）：分块随机 + 跨城市均衡，打乱行序避免训练偏置

三阶段均为非破坏式：标记不删行，保留 ``clean_status`` 列追踪每行处理状态。

设计原则
--------
- 纯标准库（random / csv / json / re），不依赖 pandas
- 复用 ``t4_dedup`` 的 MinHash 去重逻辑
- 每阶段输出可独立检查的中间结果

CLI
---
    python scripts/governance/t7_clean_pipeline.py --city 三亚 --out clean_report.json
    python scripts/governance/t7_clean_pipeline.py --csv data.csv --out clean_report.json
    python scripts/governance/t7_clean_pipeline.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

# 复用 T4 去重模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from t4_dedup import dedup as _t4_dedup, dedup_report as _t4_dedup_report  # noqa: E402


# ── 过滤规则参数 ──────────────────────────────────────────────────────
PRICE_MIN = 500          # 最低合理单价（元/㎡）
PRICE_MAX = 500000       # 最高合理单价（元/㎡）
AREA_MIN = 10            # 最低合理面积（㎡）
AREA_MAX = 1000000       # 最高合理面积（㎡）
CHINA_BBOX = (3.0, 54.0, 73.0, 135.0)  # 中国经纬度边界 (lat_lo, lat_hi, lng_lo, lng_hi)

CITY_BBOXES = {
    "三亚": (18.0, 19.0, 108.5, 110.5),
    "杭州": (29.0, 31.0, 118.0, 121.0),
    "上海": (30.5, 32.0, 120.5, 122.5),
    "青岛": (35.0, 37.5, 119.0, 121.5),
    "济南": (35.8, 37.8, 116.0, 118.2),
    "襄阳": (31.0, 33.0, 110.0, 113.5),
}


# 清洗状态标记
CLEAN_PASS = "pass"          # 通过所有检查
CLEAN_DUP = "duplicate"      # 被标记为重复
CLEAN_ANOMALY = "anomaly"    # 检测到异常
CLEAN_DUP_ANOMALY = "dup+anomaly"  # 重复且异常


# ═══════════════════════════════════════════════════════════════════════
#  阶段一：去重
# ═══════════════════════════════════════════════════════════════════════

def stage_dedup(rows: list[dict], keys: list[str] | None = None) -> tuple[list[dict], dict[str, Any]]:
    """
    阶段一：MinHash 近似去重（非破坏式标记）。

    参数:
        rows: 行列表
        keys: 去重键列名

    返回:
        (marked_rows, dedup_report) — 标记后的行 + 去重报告
    """
    marked = _t4_dedup(rows, keys=keys)
    report = _t4_dedup_report(rows, keys=keys)
    return marked, report


# ═══════════════════════════════════════════════════════════════════════
#  阶段二：过滤（规则引擎）
# ═══════════════════════════════════════════════════════════════════════

def _to_float(value: Any) -> float | None:
    """安全转换字符串为 float，失败返回 None。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("元/㎡", "").replace("元/平米", "")
    if s in ("", "nan", "none", "null", "无"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def check_price_anomaly(row: dict) -> str | None:
    """检查价格异常：超出合理范围或为空。返回异常描述或 None。"""
    price = _to_float(row.get("最新价格"))
    if price is None:
        return None  # 缺价格不视为异常（另行标记）
    if price < PRICE_MIN:
        return f"价格过低: {price} < {PRICE_MIN}"
    if price > PRICE_MAX:
        return f"价格过高: {price} > {PRICE_MAX}"
    return None


def check_area_anomaly(row: dict) -> str | None:
    """检查面积异常：超出合理范围。"""
    # 占地面积 / 建筑面积
    for col in ["占地面积", "建筑面积"]:
        area = _to_float(row.get(col))
        if area is None:
            continue
        if area < AREA_MIN:
            return f"{col}过小: {area} < {AREA_MIN}"
        if area > AREA_MAX:
            return f"{col}过大: {area} > {AREA_MAX}"
    return None


def check_coord_anomaly(row: dict) -> str | None:
    """检查坐标异常：越界或为零。"""
    # 兼容两种列名体系
    lat = _to_float(row.get("lat") or row.get("百度地图纬度"))
    lng = _to_float(row.get("lng") or row.get("百度地图经度"))
    city = row.get("城市名称") or row.get("city")
    if lat is None or lng is None:
        return None  # 缺坐标不视为异常
    if lat == 0 or lng == 0:
        return "坐标为零"
        
    # Resolve bounding box based on city
    bbox = CHINA_BBOX
    if city:
        for key, box in CITY_BBOXES.items():
            if key in city or city in key:
                bbox = box
                break
    lat_lo, lat_hi, lng_lo, lng_hi = bbox
    
    if not (lat_lo <= lat <= lat_hi and lng_lo <= lng <= lng_hi):
        return f"坐标越界: ({lat}, {lng})"
    return None


def detect_anomalies(row: dict) -> list[str]:
    """
    对单行执行全部异常检测规则。

    返回:
        异常描述列表（空列表 = 无异常）
    """
    anomalies = []
    for checker in [check_price_anomaly, check_area_anomaly, check_coord_anomaly]:
        result = checker(row)
        if result:
            anomalies.append(result)
    return anomalies


def stage_filter(rows: list[dict]) -> tuple[list[dict], dict[str, Any]]:
    """
    阶段二：规则引擎过滤（非破坏式标记 anomaly_flag + anomaly_detail）。

    参数:
        rows: 行列表

    返回:
        (marked_rows, filter_report) — 标记后的行 + 过滤报告
    """
    marked = [dict(row) for row in rows]
    anomaly_count = 0
    anomaly_types: dict[str, int] = {}

    for row in marked:
        anomalies = detect_anomalies(row)
        if anomalies:
            row["anomaly_flag"] = True
            row["anomaly_detail"] = "; ".join(anomalies)
            anomaly_count += 1
            for a in anomalies:
                key = a.split(":")[0].strip()
                anomaly_types[key] = anomaly_types.get(key, 0) + 1
        else:
            row["anomaly_flag"] = False
            row["anomaly_detail"] = ""

    report = {
        "total_rows": len(rows),
        "anomaly_rows": anomaly_count,
        "clean_rows": len(rows) - anomaly_count,
        "anomaly_rate": round(anomaly_count / len(rows), 4) if rows else 0.0,
        "anomaly_types": anomaly_types,
    }
    return marked, report


# ═══════════════════════════════════════════════════════════════════════
#  阶段三：混洗（分块随机 + 跨城市均衡）
# ═══════════════════════════════════════════════════════════════════════

def stage_shuffle(
    rows: list[dict],
    chunk_size: int = 1000,
    city_col: str = "城市名称",
    seed: int = 42,
) -> tuple[list[dict], dict[str, Any]]:
    """
    阶段三：分块随机 + 跨城市均衡混洗。

    算法:
        1. 按城市分组
        2. 轮转抽取（Round-Robin）各城市行，使相邻行尽量来自不同城市
        3. 每 chunk_size 行做一次局部随机打乱（种子可复现）

    参数:
        rows: 行列表
        chunk_size: 局部打乱块大小
        city_col: 城市列名
        seed: 随机种子

    返回:
        (shuffled_rows, shuffle_report) — 混洗后的行 + 混洗报告
    """
    rng = random.Random(seed)
    n = len(rows)

    # 按城市分组
    city_groups: dict[str, list[dict]] = {}
    for row in rows:
        city = str(row.get(city_col, "未知")).strip() or "未知"
        city_groups.setdefault(city, []).append(row)

    # 各城市组内随机
    for city in city_groups:
        rng.shuffle(city_groups[city])

    # 轮转抽取（Round-Robin）
    cities = sorted(city_groups.keys())
    indices = {c: 0 for c in cities}
    result = []
    while len(result) < n:
        progressed = False
        for city in cities:
            idx = indices[city]
            if idx < len(city_groups[city]):
                result.append(city_groups[city][idx])
                indices[city] += 1
                progressed = True
        if not progressed:
            break

    # 分块局部打乱
    for i in range(0, len(result), chunk_size):
        chunk = result[i : i + chunk_size]
        rng.shuffle(chunk)
        result[i : i + chunk_size] = chunk

    # 统计混洗后城市分布
    city_dist: dict[str, int] = {}
    for row in result:
        city = str(row.get(city_col, "未知")).strip() or "未知"
        city_dist[city] = city_dist.get(city, 0) + 1

    report = {
        "total_rows": n,
        "city_count": len(cities),
        "city_distribution": city_dist,
        "chunk_size": chunk_size,
        "seed": seed,
        "adjacent_same_city_rate": _calc_adjacent_same_city_rate(result, city_col),
    }
    return result, report


def _calc_adjacent_same_city_rate(rows: list[dict], city_col: str) -> float:
    """计算相邻行来自同一城市的比例（越低越好）。"""
    if len(rows) < 2:
        return 0.0
    same = 0
    for i in range(len(rows) - 1):
        c1 = str(rows[i].get(city_col, ""))
        c2 = str(rows[i + 1].get(city_col, ""))
        if c1 == c2:
            same += 1
    return round(same / (len(rows) - 1), 4)


# ═══════════════════════════════════════════════════════════════════════
#  完整清洗管道
# ═══════════════════════════════════════════════════════════════════════

def clean_pipeline(
    rows: list[dict],
    keys: list[str] | None = None,
    chunk_size: int = 1000,
    city_col: str = "城市名称",
    seed: int = 42,
) -> tuple[list[dict], dict[str, Any]]:
    """
    执行完整三阶段清洗管道。

    阶段:
        1. 去重 → 标记 dup_group_id / dup_rank
        2. 过滤 → 标记 anomaly_flag / anomaly_detail
        3. 混洗 → 分块随机 + 跨城市均衡

    参数:
        rows: 行列表
        keys: 去重键列名
        chunk_size: 混洗块大小
        city_col: 城市列名
        seed: 随机种子

    返回:
        (cleaned_rows, clean_report) — 清洗后行 + 三阶段报告
    """
    if keys is None:
        keys = ["楼盘名称", "城市名称", "地址"]

    # 阶段一：去重
    deduped_rows, dedup_report = stage_dedup(rows, keys=keys)

    # 阶段二：过滤
    filtered_rows, filter_report = stage_filter(deduped_rows)

    # 阶段三：混洗
    shuffled_rows, shuffle_report = stage_shuffle(
        filtered_rows, chunk_size=chunk_size, city_col=city_col, seed=seed
    )

    # 综合 clean_status
    for row in shuffled_rows:
        is_dup = row.get("dup_group_id", -1) >= 0
        is_anomaly = row.get("anomaly_flag", False)
        if is_dup and is_anomaly:
            row["clean_status"] = CLEAN_DUP_ANOMALY
        elif is_dup:
            row["clean_status"] = CLEAN_DUP
        elif is_anomaly:
            row["clean_status"] = CLEAN_ANOMALY
        else:
            row["clean_status"] = CLEAN_PASS

    clean_report = {
        "stage_1_dedup": dedup_report,
        "stage_2_filter": filter_report,
        "stage_3_shuffle": shuffle_report,
        "summary": {
            "input_rows": len(rows),
            "output_rows": len(shuffled_rows),
            "duplicate_rows": dedup_report["dup_rows"],
            "anomaly_rows": filter_report["anomaly_rows"],
            "clean_pass_rows": sum(1 for r in shuffled_rows if r["clean_status"] == CLEAN_PASS),
            "stages": ["dedup", "filter", "shuffle"],
        },
    }
    return shuffled_rows, clean_report


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
    """内置自测：10 条测试数据（含异常价格/越界坐标/重复行），验证三阶段正确执行。"""
    test_rows = [
        # 行 0-1: 正常数据
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "地址": "南田路16号", "最新价格": "35000", "百度地图纬度": "18.41", "百度地图经度": "109.71", "占地面积": "50000"},
        {"楼盘名称": "亚龙湾翡翠谷", "城市名称": "三亚", "地址": "亚龙湾路88号", "最新价格": "28000", "百度地图纬度": "18.20", "百度地图经度": "109.50", "占地面积": "80000"},
        # 行 2: 重复行（与行 0 近似）
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "地址": "南田路16号", "最新价格": "35500", "百度地图纬度": "18.41", "百度地图经度": "109.71", "占地面积": "50000"},
        # 行 3: 异常价格（过低）
        {"楼盘名称": "清水湾度假村", "城市名称": "三亚", "地址": "清水湾大道1号", "最新价格": "100", "百度地图纬度": "18.50", "百度地图经度": "110.00", "占地面积": "60000"},
        # 行 4: 异常价格（过高）
        {"楼盘名称": "香水湾一号", "城市名称": "三亚", "地址": "香水湾路1号", "最新价格": "999999", "百度地图纬度": "18.30", "百度地图经度": "109.80", "占地面积": "40000"},
        # 行 5: 越界坐标
        {"楼盘名称": "南山花园", "城市名称": "三亚", "地址": "南山路5号", "最新价格": "20000", "百度地图纬度": "0", "百度地图经度": "0", "占地面积": "30000"},
        # 行 6: 异常面积
        {"楼盘名称": "天涯海景", "城市名称": "三亚", "地址": "天涯路10号", "最新价格": "25000", "百度地图纬度": "18.25", "百度地图经度": "109.30", "占地面积": "1"},
        # 行 7-8: 来自另一个城市的正常数据
        {"楼盘名称": "西湖一号", "城市名称": "杭州", "地址": "西湖区天目山路1号", "最新价格": "45000", "百度地图纬度": "30.26", "百度地图经度": "120.15", "占地面积": "35000"},
        {"楼盘名称": "钱江新城", "城市名称": "杭州", "地址": "江干区四季大道2号", "最新价格": "38000", "百度地图纬度": "30.30", "百度地图经度": "120.20", "占地面积": "45000"},
        # 行 9: 重复行（与行 7 近似）
        {"楼盘名称": "西湖一号", "城市名称": "杭州", "地址": "西湖区天目山路1号", "最新价格": "45000", "百度地图纬度": "30.26", "百度地图经度": "120.15", "占地面积": "35000"},
    ]

    cleaned, report = clean_pipeline(test_rows, chunk_size=3)

    # ── 阶段一：去重 ──
    dedup_r = report["stage_1_dedup"]
    assert dedup_r["total_rows"] == 10, f"去重输入应为 10 行"
    assert dedup_r["dup_groups"] >= 1, f"应检测到重复组"
    assert dedup_r["dup_rows"] >= 2, f"应检测到重复行（≥2）"
    print(f"  [OK] 阶段一去重：{dedup_r['dup_groups']} 组，{dedup_r['dup_rows']} 重复行")

    # ── 阶段二：过滤 ──
    filter_r = report["stage_2_filter"]
    assert filter_r["total_rows"] == 10
    assert filter_r["anomaly_rows"] >= 4, f"应检测到 ≥4 条异常（价格低/高/坐标零/面积小），实际 {filter_r['anomaly_rows']}"
    print(f"  [OK] 阶段二过滤：{filter_r['anomaly_rows']} 条异常，类型：{filter_r['anomaly_types']}")

    # 验证异常标记
    anomalies = [r for r in cleaned if r.get("anomaly_flag")]
    assert len(anomalies) >= 4, f"异常行数应 ≥4"
    # 检查价格过低被标记
    low_price_rows = [r for r in cleaned if "价格过低" in r.get("anomaly_detail", "")]
    assert len(low_price_rows) >= 1, "应检测到价格过低"
    # 检查价格过高被标记
    high_price_rows = [r for r in cleaned if "价格过高" in r.get("anomaly_detail", "")]
    assert len(high_price_rows) >= 1, "应检测到价格过高"
    # 检查坐标异常被标记
    coord_rows = [r for r in cleaned if "坐标" in r.get("anomaly_detail", "")]
    assert len(coord_rows) >= 1, "应检测到坐标异常"
    print(f"  [OK] 异常标记正确：价格过低 {len(low_price_rows)} / 价格过高 {len(high_price_rows)} / 坐标异常 {len(coord_rows)}")

    # ── 阶段三：混洗 ──
    shuffle_r = report["stage_3_shuffle"]
    assert shuffle_r["total_rows"] == 10
    assert shuffle_r["city_count"] == 2, f"应包含 2 个城市，实际 {shuffle_r['city_count']}"
    # 相邻同城市率应较低（跨城市均衡）
    assert shuffle_r["adjacent_same_city_rate"] < 0.8, \
        f"相邻同城市率应较低（混洗效果），实际 {shuffle_r['adjacent_same_city_rate']}"
    print(f"  [OK] 阶段三混洗：{shuffle_r['city_count']} 城市，相邻同城市率 {shuffle_r['adjacent_same_city_rate']:.1%}")

    # ── 综合 clean_status ──
    summary = report["summary"]
    assert summary["input_rows"] == 10
    assert summary["output_rows"] == 10  # 非破坏式
    pass_rows = summary["clean_pass_rows"]
    assert pass_rows > 0, "应有通过清洗的行"
    print(f"  [OK] clean_status：通过 {pass_rows} / 重复 {summary['duplicate_rows']} / 异常 {summary['anomaly_rows']}")

    # 验证非破坏式
    assert len(cleaned) == 10, f"非破坏式：行数应保持 10，实际 {len(cleaned)}"
    print(f"  [OK] 非破坏式：输出行数 = {len(cleaned)}")

    print("\n  === T7 清洗管道 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="T7 清洗管道 — DeepSeek 三阶段（去重→过滤→混洗）"
    )
    parser.add_argument("--csv", type=str, help="输入 CSV 文件路径")
    parser.add_argument("--city", type=str, default="三亚", help="城市名（用于查找默认 CSV）")
    parser.add_argument("--out", type=str, default="clean_report.json", help="输出清洗报告 JSON 路径")
    parser.add_argument("--keys", type=str, default=None, help="去重键列名（逗号分隔）")
    parser.add_argument("--chunk-size", type=int, default=1000, help="混洗块大小")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
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

    keys = args.keys.split(",") if args.keys else None

    print(f"[T7] 读取数据：{csv_path}")
    rows = read_csv_rows(csv_path)
    print(f"[T7] 总行数：{len(rows)}")

    cleaned, report = clean_pipeline(
        rows, keys=keys, chunk_size=args.chunk_size, seed=args.seed
    )

    # 写报告
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[T7] 清洗报告已写入：{out_path}")

    s = report["summary"]
    print(f"[T7] 汇总：输入 {s['input_rows']} 行 → 输出 {s['output_rows']} 行")
    print(f"      重复 {s['duplicate_rows']} / 异常 {s['anomaly_rows']} / 通过 {s['clean_pass_rows']}")


if __name__ == "__main__":
    main()
