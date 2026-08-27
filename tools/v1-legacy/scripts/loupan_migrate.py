# -*- coding: utf-8 -*-
"""
DDS 楼盘数据 -> 完整维度 canonical schema 迁移/清洗工具 (reusable CLI)
====================================================================
将任意旧 schema 楼盘 CSV (56列 或 260列三重重复版) 统一迁移到
schema_dds.FULL_HEADERS (56 legacy + 49 新维度 = 105列), 可合并/去重,
并重建同名 parquet。保留既有 56 列中文名与数据 byte 不变, 仅追加缺失维度空列。

用法:
  python scripts/loupan_migrate.py --in A.csv --out A.csv
  python scripts/loupan_migrate.py --in main.csv --merge extra.csv --out main.csv --dedup
"""
from __future__ import annotations
import argparse, csv, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_dds import LEGACY_HEADERS, FULL_HEADERS, NEW_COLUMNS, TAXONOMY, KEY_TO_LEGACY

N = len(LEGACY_HEADERS)
ALIASES = {"hydropower": ["供水供电", "供电供水"]}
_CN2KEY = {cn: k for _c, k, cn, _s in TAXONOMY if k not in KEY_TO_LEGACY}

def _cands(cn):
    k = _CN2KEY[cn]
    return [cn, cn + "(" + k + ")", k] + ALIASES.get(k, [])

def _demangle(h):
    return re.sub(r"\.\d+$", "", str(h).strip())

def read_rows(p):
    r = list(csv.reader(open(p, encoding="utf-8-sig", newline="")))
    return (r[0], r[1:]) if r else ([], [])

def to_canonical(headers, rows):
    hidx = {}
    for i, h in enumerate(headers):
        hidx.setdefault(str(h).strip(), i)
    legacy_ok = [_demangle(x) for x in headers[:N]] == LEGACY_HEADERS
    out = []
    for row in rows:
        row = list(row) + [""] * (len(headers) - len(row))
        o = []
        if legacy_ok:
            o.extend(row[i] if i < len(row) else "" for i in range(N))
        else:
            for col in LEGACY_HEADERS:
                j = hidx.get(col)
                o.append(row[j] if j is not None and j < len(row) else "")
        for cn in NEW_COLUMNS:
            v = ""
            for c in _cands(cn):
                j = hidx.get(c)
                if j is not None and j < len(row) and str(row[j]).strip():
                    v = row[j]; break
            o.append(v)
        out.append(o)
    return out

def _norm(s):
    return "".join(str(s).split()).strip().lower()

def dedup(rows):
    ni = LEGACY_HEADERS.index("楼盘名称")
    best, order = {}, []
    for row in rows:
        key = _norm(row[ni]) or _norm(row[0])
        fill = sum(1 for v in row if str(v).strip())
        if key not in best:
            best[key] = row; order.append(key)
        elif fill > sum(1 for v in best[key] if str(v).strip()):
            best[key] = row
    return [best[k] for k in order]

def write_canonical(path, rows, make_parquet=True):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(FULL_HEADERS); w.writerows(rows)
    print("  [CSV] %s : %d rows x %d cols" % (path.name, len(rows), len(FULL_HEADERS)))
    if make_parquet:
        try:
            import pandas as pd
            pd.read_csv(path, dtype=str, keep_default_na=False).to_parquet(path.with_suffix(".parquet"), index=False)
            print("  [Pq ] %s" % path.with_suffix(".parquet").name)
        except Exception as e:
            print("  [warn] parquet skipped: %s" % e)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--merge", action="append", default=[])
    ap.add_argument("--out", required=True)
    ap.add_argument("--dedup", action="store_true")
    ap.add_argument("--no-parquet", action="store_true")
    a = ap.parse_args()
    rows = []
    for p in [a.inp] + a.merge:
        p = Path(p)
        if not p.exists():
            print("  [skip] %s" % p); continue
        h, rr = read_rows(p); rows.extend(to_canonical(h, rr))
        print("  read %s : %d rows (%d cols)" % (p.name, len(rr), len(h)))
    if a.dedup:
        b = len(rows); rows = dedup(rows); print("  dedup: %d -> %d" % (b, len(rows)))
    write_canonical(Path(a.out), rows, make_parquet=not a.no_parquet)

if __name__ == "__main__":
    main()
