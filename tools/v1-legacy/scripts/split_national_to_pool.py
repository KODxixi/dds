# -*- coding: utf-8 -*-
"""把 _purchased/全国新楼盘数据.csv 按城市拆成 Vault/2026新楼盘/新楼盘-{city}.csv(105列)。
保留现有 5 城不覆盖；其余城市新建。流式读取+内存分组。CSV-only(query_local 无 parquet 时读 CSV)。"""
import csv, re, sys
from collections import defaultdict
from pathlib import Path
from datetime import date
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from schema_dds import FULL_HEADERS
csv.field_size_limit(10**7)
TODAY = date.today().isoformat()
SKIP = {"高德地图经度", "高德地图纬度"}
EXISTING = {"三亚", "杭州", "上海", "青岛", "济南"}
SRC = ROOT / "Vault" / "_purchased" / "全国新楼盘数据.csv"
OUTDIR = ROOT / "Vault" / "2026新楼盘"
PROV = "来源:购买数据(全国新楼盘库) 归档%s" % TODAY


def safe(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def main():
    groups = defaultdict(list)
    with open(SRC, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f); h = next(rd)
        ci = h.index("城市名称")
        cmap = {}
        for c in h:
            cs = c.strip()
            if cs in SKIP: continue
            if cs in FULL_HEADERS: cmap[c] = cs
        for src in rd:
            if len(src) <= ci: continue
            city = src[ci].strip()
            if not city or city in EXISTING: continue
            r = {col: "" for col in FULL_HEADERS}
            for j, c in enumerate(h):
                t = cmap.get(c)
                if t and j < len(src) and src[j].strip() and not r[t]:
                    r[t] = src[j].strip()
            if not r["最新价格"].strip():
                m = re.search(r'(\d+(?:\.\d+)?)', r["参考价格"])
                if m: r["最新价格"] = m.group(1)
            if r["最新价格"].strip() and "元/㎡" not in r["参考价格"] and "万元/套" not in r["参考价格"]:
                r["参考价格"] = "元/㎡"
            r["城市名称"] = city
            if not r["业内评价"]: r["业内评价"] = PROV
            groups[city].append([r[col] for col in FULL_HEADERS])
    total = 0
    for city, rows in groups.items():
        p = OUTDIR / ("新楼盘-%s.csv" % safe(city))
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f); w.writerow(FULL_HEADERS); w.writerows(rows)
        total += len(rows)
    print("新增城市数:", len(groups), "| 总行:", total)
    top = sorted(groups.items(), key=lambda x: -len(x[1]))[:12]
    print("Top:", [(c, len(r)) for c, r in top])


if __name__ == "__main__":
    main()
