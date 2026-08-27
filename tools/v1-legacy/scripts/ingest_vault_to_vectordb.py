"""
把 Vault/{year}年/{city}.csv (或 .parquet) 的楼盘数据灌入 Gavis LanceDB dds_competitor_proj。
优先读 parquet（列式快），fallback 到 csv。

用法: python scripts/ingest_vault_to_vectordb.py [--year 2026] [--city 三亚]
"""
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse
import csv
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CITIES = ["三亚", "杭州", "上海", "青岛"]

GAVIS_CORE = Path(os.path.expanduser("~")) / "Gavis" / "gavis-core"
if str(GAVIS_CORE) not in sys.path:
    sys.path.insert(0, str(GAVIS_CORE))

from unified_indexer import get_indexer


def clean_val(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "None", "", "[]", "{}") else s


def _extract_price(s: str) -> float | None:
    if not s:
        return None
    m = re.search(r"(\d{3,})", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def read_rows(year_dir_name, city):
    year_dir = VAULT / year_dir_name
    if not year_dir.exists():
        return []
    f = year_dir / f"{city}.csv"
    if not f.exists():
        pf = year_dir / f"{city}.parquet"
        if pf.exists():
            try:
                import pandas as pd
                df = pd.read_parquet(pf)
                try:
                    year = int(year_dir_name[:4])
                except ValueError:
                    year = 0
                rows = df.to_dict("records")
                for r in rows:
                    r["_year"] = year
                return rows
            except Exception:
                pass
        return []
    try:
        year = int(year_dir_name[:4])
    except ValueError:
        year = 0
    rows = []
    with open(f, encoding="utf-8-sig") as fp:
        for r in csv.DictReader(fp):
            r["_year"] = year
            rows.append(r)
    return rows


def index_rows(idx, rows, batch_size=100):
    indexed = 0
    for i, r in enumerate(rows):
        try:
            city = clean_val(r.get("城市名称", ""))
            district = clean_val(r.get("区域名称", "") or r.get("子区域名称", ""))
            project_name = clean_val(r.get("楼盘名称", ""))
            if not city or not project_name:
                continue

            idx.index_competitor(
                city=city,
                district=district,
                project_name=project_name,
                avg_price=_extract_price(r.get("最新价格", "")),
                area_range=clean_val(r.get("面积范围", "")),
                product_type=clean_val(r.get("物业类型", "")),
                developer=clean_val(r.get("开发商", "") or r.get("开发商品牌", "")),
                year=r.get("_year", 0),
                address=clean_val(r.get("地址", "")),
                room_types=clean_val(r.get("户型文本描述", "")),
                volume_rate=clean_val(r.get("容积率", "")),
                green_rate=clean_val(r.get("绿化率", "")),
                source_file=f"{r.get('_year', '')}/{city}.csv",
            )
            indexed += 1
            if indexed % batch_size == 0:
                print(f"  已入库 {indexed} 行...")
        except Exception as e:
            continue
    return indexed


def main():
    parser = argparse.ArgumentParser(description="DDS Vault → Gavis LanceDB 入库")
    parser.add_argument("--year", type=int)
    parser.add_argument("--city", type=str, choices=CITIES)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()

    idx = get_indexer()
    print(f"[DDS ingest] LanceDB = {idx.persist_dir}")

    year_dirs = sorted([d for d in VAULT.iterdir()
                        if d.is_dir() and re.match(r"\d{4}年", d.name)],
                       key=lambda p: p.name)
    if args.year:
        year_dirs = [d for d in year_dirs if str(args.year) in d.name]

    cities = [args.city] if args.city else CITIES

    total = 0
    t0 = time.time()
    for yd in year_dirs:
        for city in cities:
            rows = read_rows(yd.name, city)
            if not rows:
                continue
            print(f"[DDS ingest] {yd.name}/{city}: {len(rows)} 行")
            n = index_rows(idx, rows, args.batch_size)
            total += n

    elapsed = time.time() - t0
    print(f"\n[DDS ingest] 完成: {total} 条记录, 耗时 {elapsed:.1f}s")
    print(f"[DDS ingest] 表统计: {idx.count('dds_competitor_proj')} 条")


if __name__ == "__main__":
    main()
