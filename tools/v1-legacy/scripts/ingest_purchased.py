# -*- coding: utf-8 -*-
"""
把购买的楼盘数据(inbox)按列名映射到 DDS 105 列规范 → 入库 Vault。
File1 全国新楼盘(58列): 几乎与 legacy 同名(identity), 高德坐标忽略, 价格归一, 可按城市过滤。
File2 济南二手房小区(50列): 多数列名即 taxonomy 中文名(identity) + 少量 alias。
流式读取(防 213MB 爆内存)。查不到留空，不编造。
"""
from __future__ import annotations
import argparse, csv, re, sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from schema_dds import FULL_HEADERS, LEGACY_HEADERS  # noqa
csv.field_size_limit(10**7)
TODAY = date.today().isoformat()

# File1 新盘：基本 identity；这些源列无目标 → 跳过
SKIP1 = {"高德地图经度", "高德地图纬度"}

# ── 数据来源标注 ──
# 购买数据供应商信息（待补充合同编号/购买日期）
PURCHASE_SUPPLIER = "购买数据（供应商待记录）"
PURCHASE_DATE = "2026-06"  # 入库月份
NEWHOUSE_SOURCE = f"来源:购买({PURCHASE_SUPPLIER}) 全国新楼盘库 ~128K行 632城 入库{PURCHASE_DATE}"
COMMUNITY_SOURCE = f"来源:购买({PURCHASE_SUPPLIER}) 济南二手房小区库 ~8.5K行 入库{PURCHASE_DATE}"
# File2 小区：源列名 → 105 列物理名（其余 identity）
ALIAS_COMM = {
    "小区编号": "楼盘ID", "小区名称": "楼盘名称", "区县名称": "区域名称", "区县ID": "区域ID",
    "商圈名称": "子区域名称", "商圈ID": "子区域ID", "小区地址": "地址",
    "经度": "百度地图经度", "纬度": "百度地图纬度", "小区标签": "标签列表",
    "总户数": "规划户数", "物业费": "物业管理费", "房价": "最新价格",
}
SKIP2 = {"高德经度", "高德纬度"}


def build_map(headers, alias, skip):
    m = {}
    for c in headers:
        cs = c.strip()
        if cs in skip:
            continue
        if cs in FULL_HEADERS:
            m[c] = cs
        elif cs in alias:
            m[c] = alias[cs]
    return m


def norm_price(r):
    if not r["最新价格"].strip():
        m = re.search(r'(\d+(?:\.\d+)?)', r["参考价格"])
        if m:
            r["最新价格"] = m.group(1)
    # 参考价格保证带单位口径(query_local 靠它判断)
    if r["最新价格"].strip() and "元/㎡" not in r["参考价格"] and "万元/套" not in r["参考价格"]:
        r["参考价格"] = "元/㎡"
    return r


def convert(infile, alias, skip, provenance, city_filter=None):
    rows = []
    with open(infile, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        h = next(rd)
        cmap = build_map(h, alias, skip)
        ci = h.index("城市名称") if "城市名称" in h else -1
        for src in rd:
            if city_filter and ci >= 0 and (len(src) <= ci or src[ci].strip() != city_filter):
                continue
            r = {col: "" for col in FULL_HEADERS}
            for j, c in enumerate(h):
                t = cmap.get(c)
                if t and j < len(src) and src[j].strip() and not r[t]:
                    r[t] = src[j].strip()
            norm_price(r)
            if city_filter:
                r["城市名称"] = city_filter
            elif not r["城市名称"]:
                r["城市名称"] = "济南"
            if not r["业内评价"]:
                r["业内评价"] = provenance
            rows.append([r[col] for col in FULL_HEADERS])
    return rows


def merge_into(targets, new_rows, tag):
    def read(p):
        rr = list(csv.reader(open(p, encoding="utf-8-sig"))); return rr[1:] if rr else []
    ni = LEGACY_HEADERS.index("楼盘名称")
    def norm(s): return re.sub(r"[\s&·・·\-—丨|（）()]", "", str(s)).strip().lower()
    tot = 0
    for tgt in targets:
        p = ROOT / tgt
        existing = read(p) if p.exists() else []
        best, order = {}, []
        for row in new_rows + existing:   # 购买数据优先做基底
            k = norm(row[ni]) or norm(row[0])
            if not k:
                continue
            fill = sum(1 for v in row if str(v).strip())
            if k not in best:
                best[k] = row; order.append(k)
            elif fill > sum(1 for v in best[k] if str(v).strip()):
                best[k] = row
        merged = [best[k] for k in order]
        tot = len(merged)
        if p.exists():
            bak = ROOT / "Vault" / "_backup_premigration" / (tgt.replace("/", "_") + "." + tag)
            bak.parent.mkdir(parents=True, exist_ok=True)
            if not bak.exists(): bak.write_bytes(p.read_bytes())
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f); w.writerow(FULL_HEADERS); w.writerows(merged)
        try:
            import pandas as pd
            pd.read_csv(p, dtype=str, keep_default_na=False).to_parquet(str(p).replace(".csv", ".parquet"), index=False)
        except Exception as e:
            print("  [warn] parquet 跳过(%s)" % e)
        print("  [入库] %s : %d 行" % (tgt, len(merged)))
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--newhouse", help="全国新楼盘 CSV")
    ap.add_argument("--community", help="二手房小区 CSV")
    ap.add_argument("--city", default="济南")
    a = ap.parse_args()
    if a.newhouse:
        print("[新盘] 过滤城市=%s ..." % a.city)
        rows = convert(a.newhouse, {}, SKIP1, NEWHOUSE_SOURCE + " 归档%s" % TODAY, city_filter=a.city)
        print("  抽出 %s 新盘 %d 个" % (a.city, len(rows)))
        merge_into(["Vault/2026新楼盘/新楼盘-%s.csv" % a.city, "Vault/2026年/%s.csv" % a.city], rows, "prepurchase")
    if a.community:
        print("[二手房小区] ...")
        rows = convert(a.community, ALIAS_COMM, SKIP2, COMMUNITY_SOURCE + " 归档%s" % TODAY, city_filter=None)
        print("  小区 %d 个" % len(rows))
        merge_into(["Vault/2026新楼盘/二手房小区-%s.csv" % a.city], rows, "comm")
    print("\n✓ 完成。")


if __name__ == "__main__":
    main()
