"""
DDS report price-band backtest -- leave-one-out spatial cross validation.

Tests the report's core output (area price estimate). For every real listing,
estimate avg/median price from OTHER real listings within R km (same logic as
summarize_market + haversine in report_parcel), then compare to that listing's
own real unit price. Ground truth = local real listing price (Vault).

Metrics: MAPE / median APE of the estimate, and price-band coverage
(median +/-15%, and comp min-max). Pure stdlib + duckdb. ASCII console output.

Caveat: ground truth is listing/reference price (挂牌/参考价), not 网签成交.
"""
import argparse
import json
import math
import os
import statistics
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CITY_FILES = {"三亚": "新楼盘-三亚.csv", "杭州": "新楼盘-杭州.csv",
              "上海": "新楼盘-上海.csv", "青岛": "新楼盘-青岛.csv",
              "济南": "新楼盘-济南.csv"}


def _src(city: str):
    f = VAULT / "2026新楼盘" / CITY_FILES[city]
    pq = f.with_suffix(".parquet")
    if pq.exists():
        return f"read_parquet('{pq.as_posix()}')"
    if f.exists():
        return f"read_csv_auto('{f.as_posix()}', header=true, all_varchar=true)"
    return None


def load_city(city: str):
    """Return list of (price, lat, lng) for real listings with 元/㎡ price + coords."""
    src = _src(city)
    if not src:
        return []
    rows = duckdb.query(f"""
        SELECT TRY_CAST(最新价格 AS DOUBLE) AS p,
               TRY_CAST(百度地图纬度 AS DOUBLE) AS lat,
               TRY_CAST(百度地图经度 AS DOUBLE) AS lng
        FROM {src}
        WHERE 参考价格 LIKE '%元/㎡%'
          AND TRY_CAST(最新价格 AS DOUBLE) BETWEEN 3000 AND 300000
          AND TRY_CAST(百度地图纬度 AS DOUBLE) BETWEEN 3 AND 54
          AND TRY_CAST(百度地图经度 AS DOUBLE) BETWEEN 73 AND 135
    """).fetchall()
    return [(p, lat, lng) for p, lat, lng in rows if p and lat and lng]


def haversine(lng1, lat1, lng2, lat2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def backtest(pts, radius_km=5.0, min_comps=5, band=0.15):
    """LOO: for each point, estimate from comps within radius, compare to its own price."""
    lat_win = radius_km / 111.0  # 经纬度包围盒预筛，避免 O(n^2) 全量 haversine
    res = []
    n = len(pts)
    for i in range(n):
        p_i, lat_i, lng_i = pts[i]
        lng_win = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat_i))))
        comps = []
        for j in range(n):
            if j == i:
                continue
            p_j, lat_j, lng_j = pts[j]
            if abs(lat_j - lat_i) > lat_win or abs(lng_j - lng_i) > lng_win:
                continue
            if haversine(lng_i, lat_i, lng_j, lat_j) <= radius_km:
                comps.append(p_j)
        if len(comps) < min_comps:
            continue
        est_med = statistics.median(comps)
        est_avg = sum(comps) / len(comps)
        res.append({
            "ape_med": abs(est_med - p_i) / p_i,
            "ape_avg": abs(est_avg - p_i) / p_i,
            "cov_band": est_med * (1 - band) <= p_i <= est_med * (1 + band),
            "cov_mm": min(comps) <= p_i <= max(comps),
            "ncomp": len(comps),
        })
    return res


def agg(res):
    if not res:
        return None
    n = len(res)
    apes = sorted(r["ape_med"] for r in res)

    def pctl(q):
        return apes[min(n - 1, int(q * n))]

    return {
        "n": n,
        "mape_med": round(sum(r["ape_med"] for r in res) / n * 100, 1),
        "mape_avg": round(sum(r["ape_avg"] for r in res) / n * 100, 1),
        "medape": round(statistics.median(r["ape_med"] for r in res) * 100, 1),
        "cov_band": round(sum(r["cov_band"] for r in res) / n * 100, 1),
        "cov_mm": round(sum(r["cov_mm"] for r in res) / n * 100, 1),
        "comps_med": round(statistics.median(r["ncomp"] for r in res)),
        # 覆盖 80%/90% 真实价所需的 ±价格带宽 = APE 的 p80/p90
        "band80": round(pctl(0.8) * 100, 1),
        "band90": round(pctl(0.9) * 100, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radii", default="3,5", help="逗号分隔的半径 km")
    ap.add_argument("--min-comps", type=int, default=5)
    ap.add_argument("--band", type=float, default=0.15)
    ap.add_argument("--out", default=None, help="markdown 报告输出路径")
    args = ap.parse_args()
    radii = [float(x) for x in args.radii.split(",")]

    data = {c: load_city(c) for c in CITY_FILES}
    lines = []  # markdown rows
    print("=" * 78)
    print(" DDS price-band backtest (leave-one-out, ground truth = local listing price)")
    print("=" * 78)
    print(f"{'city':<8}{'R_km':<6}{'N':<7}{'MAPE_med%':<11}{'MedAPE%':<10}{'cov_band15%':<13}{'cov_minmax%':<12}{'comps':<6}")

    md = ["# DDS 报告价格带回测", "",
          f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
          "",
          "**方法**：留出法(leave-one-out 空间交叉验证)。对每个真实楼盘，用其半径 R km 内其它真实楼盘"
          "的单价估均价/中位(复刻报告 summarize_market + haversine 逻辑)，与该楼盘自身真实单价比对。",
          "",
          f"**真值**：本地 Vault 2026 真实楼盘单价(挂牌/参考价，非网签成交)。`min_comps={args.min_comps}`，"
          f"价格带=中位±{int(args.band*100)}%。",
          "",
          "**指标**：MAPE_med=中位估计的平均绝对百分误差；MedAPE=误差中位数；"
          "cov_band15=真实价落在中位±15%的比例；cov_minmax=落在竞品价区间的比例。",
          "",
          "| 城市 | R(km) | 样本N | MAPE_med% | MedAPE% | 覆盖±15%% | 覆盖min-max% | 竞品中位 |",
          "|---|---|---|---|---|---|---|---|"]

    # 每个 (城市,半径) 只回测一次，结果复用
    runs = {(city, r): backtest(data[city], r, args.min_comps, args.band)
            for city in CITY_FILES for r in radii}

    for city in CITY_FILES:
        for r in radii:
            a = agg(runs[(city, r)])
            if not a:
                print(f"{city:<8}{r:<6}-- insufficient samples --")
                md.append(f"| {city} | {r} | 0 | - | - | - | - | - |")
                continue
            print(f"{city:<8}{r:<6}{a['n']:<7}{a['mape_med']:<11}{a['medape']:<10}{a['cov_band']:<13}{a['cov_mm']:<12}{a['comps_med']:<6}")
            md.append(f"| {city} | {r} | {a['n']} | {a['mape_med']} | {a['medape']} | {a['cov_band']} | {a['cov_mm']} | {a['comps_med']} |")

    print("-" * 78)
    md.append("")
    md.append("| 全城合计 | R(km) | 样本N | MAPE_med% | MedAPE% | 覆盖±15%% | 覆盖min-max% | 竞品中位 |")
    md.append("|---|---|---|---|---|---|---|---|")
    for r in radii:
        allres = []
        for city in CITY_FILES:
            allres.extend(runs[(city, r)])
        a = agg(allres)
        if a:
            print(f"{'ALL':<8}{r:<6}{a['n']:<7}{a['mape_med']:<11}{a['medape']:<10}{a['cov_band']:<13}{a['cov_mm']:<12}{a['comps_med']:<6}")
            md.append(f"| 全部 | {r} | {a['n']} | {a['mape_med']} | {a['medape']} | {a['cov_band']} | {a['cov_mm']} | {a['comps_med']} |")

    # 价格带校准：覆盖 80%/90% 真实价所需的 ±带宽
    print("-" * 78)
    print("band calibration (+/- width to cover 80% / 90% of real prices):")
    md.append("")
    md.append("## 价格带校准（覆盖目标真实价所需 ±带宽）")
    md.append("")
    md.append("| 范围 | R(km) | 覆盖80% ±% | 覆盖90% ±% |")
    md.append("|---|---|---|---|")
    for r in radii:
        allres = []
        for city in CITY_FILES:
            allres.extend(runs[(city, r)])
        a = agg(allres)
        if a:
            print(f"  ALL  R={r}km   80%: +/-{a['band80']}%   90%: +/-{a['band90']}%")
            md.append(f"| 全部 | {r} | {a['band80']} | {a['band90']} |")
    for city in CITY_FILES:
        a = agg(runs[(city, radii[0])])
        if a:
            md.append(f"| {city} | {radii[0]} | {a['band80']} | {a['band90']} |")

    md += ["", "**口径与局限**：真值为挂牌/参考价非网签成交；同板块/同盘多期会拉高覆盖率；"
           "样本为 2026 现状横截面(非时序预测)；坐标为百度坐标系，自洽。"]

    out = args.out or (ROOT / "data_out" / "reports" / "backtest" /
                       f"price_backtest_{datetime.now():%Y%m%d_%H%M%S}.md")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"\n[OK] markdown report -> {out}")

    # 机器可读校准（city -> band80），供 app.py / buyer_data.py 读取，去硬编码
    r0 = radii[0]
    calib = {city: round(a["band80"] / 100, 2)
             for city in CITY_FILES
             if (a := agg(runs[(city, r0)]))}
    calib_path = ROOT / "data" / "price_band_calibration.json"
    calib_path.parent.mkdir(parents=True, exist_ok=True)
    calib_path.write_text(json.dumps({
        "bands": calib,
        "meta": {"generated_at": datetime.now().isoformat(timespec="seconds"),
                 "method": "leave-one-out spatial CV", "radius_km": r0,
                 "coverage_target": 0.8,
                 "ground_truth": "local listing price (挂牌/参考价)",
                 "source": "scripts/backtest_price.py"},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] calibration -> {calib_path}")


if __name__ == "__main__":
    main()
