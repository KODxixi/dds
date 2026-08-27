# -*- coding: utf-8 -*-
"""
坐标回填（DDS 数据池维护 · 在 DDS 环境运行，用高德 Web Key）
给 新楼盘-{city}.csv 里「有地址但缺百度经纬度」的项目调用高德地理编码补坐标，
高德返回 GCJ-02 → 转 BD-09（与池内既有百度坐标口径一致）写入「百度地图经度/纬度」。
就地填充、天然断点续跑（再运行只处理仍缺坐标的行）、重建 parquet。

Agent/CLI 调用：
  python scripts/backfill_coords.py --city 济南
  python scripts/backfill_coords.py --city 济南 --limit 200   # 单轮限量
依赖：.env 里的 AMAP_KEY；scripts/gis_amap.py 的 geocode()。
"""
from __future__ import annotations
import argparse, csv, math, os, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
csv.field_size_limit(10**7)


def gcj02_to_bd09(lng, lat):
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="济南")
    ap.add_argument("--limit", type=int, default=0, help="单轮最多补 N 条(0=全部)")
    ap.add_argument("--delay", type=float, default=0.25)
    a = ap.parse_args()

    try:
        from gis_amap import geocode, _load_env_once
        _load_env_once()
    except Exception as e:
        print("[error] 无法导入 gis_amap.geocode：%s" % e); return 1
    key = os.environ.get("AMAP_KEY")
    if not key or key.startswith("YOUR_"):
        print("[error] 未配置 AMAP_KEY（.env）"); return 1

    p = ROOT / "Vault" / "2026新楼盘" / ("新楼盘-%s.csv" % a.city)
    if not p.exists():
        print("[error] 找不到 %s" % p); return 1
    rows = list(csv.reader(open(p, encoding="utf-8-sig")))
    h = rows[0]; idx = {n: i for i, n in enumerate(h)}
    LAT, LNG, ADDR, NAME = idx["百度地图纬度"], idx["百度地图经度"], idx["地址"], idx["楼盘名称"]
    todo = [r for r in rows[1:] if not r[LAT].strip() and r[ADDR].strip()]
    print("[%s] 缺坐标待补：%d（总 %d）" % (a.city, len(todo), len(rows) - 1))
    n = 0
    for r in (todo[: a.limit] if a.limit else todo):
        try:
            g = geocode(r[ADDR], a.city, key)
            if g:
                blng, blat = gcj02_to_bd09(g["lng"], g["lat"])
                r[LNG] = "%.6f" % blng; r[LAT] = "%.6f" % blat
                n += 1
        except Exception:
            pass
        if n and n % 50 == 0:
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerows(rows)
            print("  已补 %d ..." % n)
        time.sleep(a.delay)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    try:
        import pandas as pd
        pd.read_csv(p, dtype=str, keep_default_na=False).to_parquet(str(p).replace(".csv", ".parquet"), index=False)
    except Exception as e:
        print("  [warn] parquet 跳过(%s)" % e)
    print("[%s] 本轮补坐标 %d 条；仍缺 %d（再运行可续）" % (a.city, n, len(todo) - n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
