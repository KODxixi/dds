# -*- coding: utf-8 -*-
"""
用真实开盘年重建历史楼盘桶（替换合成克隆假数据）。
逻辑：当前池 新楼盘-{city}.csv 的项目带真实「开盘日期」。一个 Yo 年开盘的项目，
按典型在售周期视为在 Yo、Yo+1、Yo+2 年"在售"，写入对应年份桶 Vault/{Y}年/{city}.csv。
=> 历史桶=真实项目按真实年份的"在售视图"。近年(2015-2026)密、远年稀疏、pre-2010 留空(无真实源)。
合成旧数据(csv+parquet)归档至 Vault/_synthetic_history_archive/；ABM 客群文件原样保留。
"""
import csv, re, sys
from pathlib import Path
from collections import defaultdict
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from schema_dds import FULL_HEADERS
csv.field_size_limit(10**7)

CITIES = ["三亚", "上海", "杭州", "青岛"]   # 有合成历史的4城
WINDOW = 2          # 开盘 Yo -> 在售于 Yo..Yo+WINDOW
REBUILD_YEARS = range(2010, 2026)   # 重建 2010-2025；2026保持当前;pre-2010无真实源留空
ARCH = ROOT / "Vault" / "_synthetic_history_archive"
VA = ROOT / "Vault"


def yr(s):
    m = re.search(r'(19|20)\d{2}', str(s)); return int(m.group(0)) if m else None


def main():
    ARCH.mkdir(parents=True, exist_ok=True)
    oi = FULL_HEADERS.index("开盘日期"); ev = FULL_HEADERS.index("业内评价")
    # 1) 读当前池, 按开盘年滚动分桶
    buckets = defaultdict(list)   # (year, city) -> rows
    for city in CITIES:
        p = VA / "2026新楼盘" / f"新楼盘-{city}.csv"
        rows = list(csv.reader(open(p, encoding="utf-8-sig")))[1:]
        for r in rows:
            if len(r) < len(FULL_HEADERS):
                r = r + [""] * (len(FULL_HEADERS) - len(r))
            y0 = yr(r[oi])
            if not y0:
                continue
            for Y in range(y0, y0 + WINDOW + 1):
                if Y in REBUILD_YEARS:
                    rr = list(r)
                    rr[ev] = f"来源:购买库按真实开盘年({y0})回填{Y}年在售视图"
                    buckets[(Y, city)].append(rr)
    # 2) 归档旧合成 + 写真实
    import pandas as pd
    moved = wrote = 0
    for Y in range(1995, 2026):
        ydir = VA / f"{Y}年"
        for city in CITIES:
            for ext in ("csv", "parquet"):
                old = ydir / f"{city}.{ext}"
                if old.exists():
                    dest = ARCH / f"{Y}年_{city}.{ext}"
                    if not dest.exists():
                        old.replace(dest); moved += 1
                    else:
                        old.unlink()
            if Y in REBUILD_YEARS:
                rows = buckets.get((Y, city), [])
                tgt = ydir / f"{city}.csv"
                ydir.mkdir(parents=True, exist_ok=True)
                with open(tgt, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f); w.writerow(FULL_HEADERS); w.writerows(rows)
                try:
                    pd.read_csv(tgt, dtype=str, keep_default_na=False).to_parquet(str(tgt).replace(".csv", ".parquet"), index=False)
                except Exception:
                    pass
                wrote += 1
    print(f"归档旧合成文件: {moved} | 写真实历史桶: {wrote}")
    # 3) 抽样核对
    for Y in (2015, 2018, 2021, 2024):
        line = []
        for city in CITIES:
            line.append(f"{city}:{len(buckets.get((Y,city),[]))}")
        print(f"  {Y}年在售盘数  " + "  ".join(line))


if __name__ == "__main__":
    main()
