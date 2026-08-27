# -*- coding: utf-8 -*-
"""
把 TRAE/豆包等采集的济南楼盘 CSV 映射到当前 DDS 逻辑 Schema，清洗后去重合并入库 Vault。
鲁棒：utf-8/gbk 解码、跳过 ### 标记、忽略重复表头、按表头名映射、引号字段、查不到留空(不编造)。
清洗：地址去重复区县前缀、产权归一(70年)、装修去“住宅:”前缀、户数取数字、车位个数→车位数、车位比保留 1:x。
"""
from __future__ import annotations
import argparse, csv, io, re, sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from schema_dds import FULL_HEADERS, LEGACY_HEADERS  # noqa

TODAY = date.today().isoformat()
PTYPE = ["住宅", "公寓", "别墅", "洋房", "叠拼", "联排", "商业", "写字楼", "商铺"]
MAP = {
    "楼盘名称": "楼盘名称", "区域": "区域名称", "商圈": "子区域名称", "地址": "地址",
    "物业类型": "物业类型", "销售状态": "销售状态", "最新价格": "最新价格", "面积范围": "面积范围",
    "容积率": "容积率", "绿化率": "绿化率", "规划户数": "规划户数", "开发商": "开发商",
    "物业公司": "物业公司", "物业费": "物业管理费", "产权年限": "产权年限", "装修情况": "装修情况",
    "开盘时间": "开盘日期", "交房时间": "交房时间", "车位比": "车位比", "标签": "标签列表",
    "占地面积": "占地面积", "总建筑面积": "建筑面积", "总户数": "规划户数", "车位数": "车位数",
    "投资商": "投资商", "建筑设计方": "建筑设计方", "景观设计方": "景观设计方",
    "预售证号": "预售证号", "售楼处地址": "售楼处地址", "400电话": "400电话",
}


def decode(p):
    b = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try: return b.decode(enc)
        except Exception: continue
    return b.decode("utf-8", errors="replace")


def clean_addr(addr, region):
    a = addr.strip()
    if region and a.startswith(region + region):   # "历下历下区.." -> "历下区.."
        a = a[len(region):]
    return a


def clean_tenure(s):
    m = re.search(r'(\d{2,3})\s*年', s); return (m.group(1) + "年") if m else s.strip()


def clean_decor(s):
    s = s.strip()
    if "：" in s or ":" in s:
        s = re.split(r'[：:]', s)[-1]
    return s.split(",")[0].split("，")[0].strip()


def digits(s):
    m = re.search(r'(\d+)', str(s)); return m.group(1) if m else ""


def normalize_price(raw_value: str, raw_unit: str) -> tuple[str, str]:
    """保持 ``最新价格`` 为纯数字；区间等非标价格只保留在参考口径。"""
    raw_value = str(raw_value or "").strip()
    if not raw_value:
        return "", ""

    unit = str(raw_unit or "").strip()
    if not unit.startswith(("元", "万")):
        unit = "元/㎡"

    numeric = raw_value.replace(",", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", numeric):
        return numeric, numeric + unit
    return "", raw_value + unit


def parse(text):
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("###")]
    if not lines: return []
    reader = list(csv.reader(io.StringIO("\n".join(lines))))
    hi = next((i for i, r in enumerate(reader) if r and any("楼盘名称" in c for c in r)), None)
    if hi is None: return []
    header = [c.strip() for c in reader[hi]]
    col = {n: j for j, n in enumerate(header)}
    out = []
    for row in reader[hi + 1:]:
        if not row or all(not c.strip() for c in row): continue
        if any("楼盘名称" in c for c in row): continue
        def gv(cn):
            j = col.get(cn); return row[j].strip() if (j is not None and j < len(row)) else ""
        name = gv("楼盘名称")
        if not name: continue
        r = {h: "" for h in FULL_HEADERS}
        for dc, lg in MAP.items():
            r[lg] = gv(dc)
        region = r["区域名称"]
        r["地址"] = clean_addr(r["地址"], region)
        if r["产权年限"]: r["产权年限"] = clean_tenure(r["产权年限"])
        if r["装修情况"]: r["装修情况"] = clean_decor(r["装修情况"])
        if r["规划户数"]: r["规划户数"] = digits(r["规划户数"])
        # 车位：个数→车位数；1:x→车位比
        pk = gv("车位比")
        if pk:
            if "个" in pk or (pk.replace(",", "").isdigit() and int(digits(pk) or 0) > 20):
                r["车位数"] = r["车位数"] or digits(pk); r["车位比"] = ""
            else:
                r["车位比"] = pk
        r["最新价格"], r["参考价格"] = normalize_price(
            r["最新价格"], gv("价格单位")
        )
        rooms = gv("主力户型")
        if rooms:
            r["全部户型"] = rooms; r["户型文本描述"] = rooms
        if not r["物业类型"]:
            r["物业类型"] = next((p for p in PTYPE if p in (rooms + name)), "")
        r["城市名称"] = "济南"
        r["业内评价"] = ("来源:采集(TRAE) " + gv("来源链接") + " " + TODAY).strip()
        r["楼盘ID"] = "DB" + str(abs(hash(name)) % 10**8)
        out.append([r[h] for h in FULL_HEADERS])
    return out


def merge(new_rows):
    def rd(p):
        rr = list(csv.reader(open(p, encoding="utf-8-sig"))); return rr[0], rr[1:]
    ni = LEGACY_HEADERS.index("楼盘名称")
    def norm(s): return re.sub(r"[\s&·・·\-—丨|（）()]", "", str(s)).strip().lower()
    tot = 0
    for tgt in ["Vault/2026新楼盘/新楼盘-济南.csv", "Vault/2026年/济南.csv"]:
        p = ROOT / tgt
        existing = rd(p)[1] if p.exists() else []
        best, order = {}, []
        for row in existing + new_rows:
            k = norm(row[ni]) or norm(row[0]); fill = sum(1 for v in row if str(v).strip())
            if k not in best: best[k] = row; order.append(k)
            elif fill > sum(1 for v in best[k] if str(v).strip()): best[k] = row
        merged = [best[k] for k in order]; tot = len(merged)
        if p.exists():
            bak = ROOT / "Vault" / "_backup_premigration" / (tgt.replace("/", "_") + ".predoubao")
            bak.parent.mkdir(parents=True, exist_ok=True)
            if not bak.exists(): bak.write_bytes(p.read_bytes())
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f); w.writerow(FULL_HEADERS); w.writerows(merged)
        try:
            import pandas as pd
            pd.read_csv(p, dtype=str, keep_default_na=False).to_parquet(str(p).replace(".csv", ".parquet"), index=False)
        except Exception as e: print("  [warn] parquet 跳过(%s)" % e)
        print("  [入库] %s : %d 行" % (tgt, len(merged)))
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", action="append", default=[])
    ap.add_argument("--dir", dest="dir", default=None, help="批量导入该文件夹下所有 *.csv")
    a = ap.parse_args()
    files = list(a.inp)
    if a.dir:
        d = Path(a.dir); d = d if d.is_absolute() else ROOT / a.dir
        files += [str(x) for x in sorted(d.glob("*.csv"))]
    if not files:
        print("请用 --in <文件> 或 --dir <文件夹>"); return
    allr = []
    for f in files:
        p = Path(f); p = p if p.is_absolute() else ROOT / f
        if not p.exists(): print("  [skip] 找不到 %s" % p); continue
        rs = parse(decode(p)); print("  解析 %s : %d 条" % (p.name, len(rs))); allr += rs
    if not allr: print("没解析到数据。"); return
    print("合并入库 ..."); tot = merge(allr); print("\n✓ 完成。济南现有 %d 个项目。" % tot)


if __name__ == "__main__":
    main()
