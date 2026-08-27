# -*- coding: utf-8 -*-
"""
DDS 济南楼盘全量抓取器 v2（房天下 · 本机/住宅 IP 运行）
================================================================
v2 修复：v1 翻页 +0 的问题 —— 房天下需 session cookie 才认翻页参数(b9N)，
无 cookie 时每页都返回第1页。本版：
  1. 用 requests.Session() 先访问首页拿 cookie，再翻页(b9N)；
  2. 同时遍历首页里抓到的「区县 + 商圈」筛选入口 URL（各自是独立入口，
     不依赖翻页就能返回不同楼盘），最大化覆盖；
  3. 翻页连续 +0 自动停止，改靠筛选入口兜底。
抓 列表ID + 详情字段(容积率/绿化率/户数/物业/开发商/开盘交房/价格/地址) →
105 列 schema → 去重合并 Vault + parquet。断点续抓、礼貌延时。
"""
from __future__ import annotations
import argparse, csv, json, random, re, subprocess, sys, time
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from schema_dds import FULL_HEADERS, LEGACY_HEADERS  # noqa: E402

OUT = ROOT / "data_out" / "jinan_fang"; OUT.mkdir(parents=True, exist_ok=True)
CKPT = OUT / "checkpoint.json"; STAGE = OUT / "scraped_105.csv"
HOST = "https://jn.newhouse.fang.com"
BASE = HOST + "/house/s/"
TODAY = date.today().isoformat()
UAS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"]
HEADERS = {"User-Agent": random.choice(UAS),
           "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
           "Accept-Language": "zh-CN,zh;q=0.9", "Referer": "https://www.fang.com/"}

try:
    import requests
    try:
        import urllib3; urllib3.disable_warnings()
    except Exception: pass
    SESSION = requests.Session(); SESSION.headers.update(HEADERS)
except Exception:
    requests = None; SESSION = None

_seeded = False
def seed():
    global _seeded
    if SESSION is not None and not _seeded:
        try: SESSION.get(BASE, timeout=20, verify=False); _seeded = True
        except Exception: pass

def is_block(t):
    return (not t) or ("请完成下列验证" in t) or ("check.3g.fang.com" in t)

def get_html(url, retries=4):
    seed()
    for attempt in range(1, retries + 1):
        if SESSION is not None:
            try:
                r = SESSION.get(url, timeout=20, verify=False)
                r.encoding = "gbk" if ("gbk" in r.text[:600].lower() or "gb2312" in r.text[:600].lower()) else "utf-8"
                if r.status_code == 200 and not is_block(r.text):
                    return r.text
            except Exception: pass
        try:
            cmd = ["curl", "-s", "-k", "-L", "--max-time", "20",
                   "-H", "User-Agent: " + HEADERS["User-Agent"],
                   "-H", "Accept-Language: zh-CN,zh;q=0.9",
                   "-H", "Referer: https://www.fang.com/", url]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="gbk", errors="replace")
            if res.returncode == 0 and not is_block(res.stdout):
                return res.stdout
        except Exception: pass
        time.sleep(attempt * 2 + random.random())
    return ""

def parse_list(html):
    out, seen = [], set()
    for m in re.finditer(r'loupan/(\d+)\.htm["\']?[^>]*>\s*([^<>\n]{2,40}?)\s*<', html):
        pid, name = m.group(1), re.sub(r'\s+', '', m.group(2))
        if pid in seen: continue
        if name and not name.startswith("http") and "img" not in name:
            seen.add(pid); out.append((pid, name))
    for pid in re.findall(r'loupan/(\d+)\.htm', html):
        if pid not in seen: seen.add(pid); out.append((pid, ""))
    return out

def harvest_filters(html):
    """从首页抓「区县 + 商圈」筛选入口（首段≥4字母,排除 b9/c2 等参数筛选）。"""
    slugs = set()
    for m in re.finditer(r'/house/s/([a-z]{4,}[a-z0-9]*(?:/[a-z][a-z0-9]*)?)/', html):
        slugs.add(m.group(1))
    return [BASE + s + "/" for s in sorted(slugs)]

PTYPE = ["住宅", "公寓", "别墅", "洋房", "叠拼", "联排", "商业", "写字楼", "商铺"]
def fetch_details(pid):
    d = {k: "" for k in ("name","district","bizcircle","address","price","status","far","green",
                         "units","property_company","property_fee","developer","build_type","open_date","delivery_date","room_types","area_range")}
    html = get_html(HOST + "/loupan/%s/housedetail.htm" % pid)
    if not html: return d
    t = html
    for tag in ['</div>','</li>','</p>','</td>','</tr>','<br>','<br/>']: t = t.replace(tag, '\n')
    t = re.sub(r'<[^>]+>', '', t)
    def g(p):
        mm = re.search(p, t); return mm.group(1).strip() if mm else ""
    h1 = re.search(r'<h1>([^<]+)</h1>', html)
    d["name"] = h1.group(1).strip() if h1 else g(r'楼盘名称[：:]\s*([^\n|]+)')
    d["status"] = g(r'销售状态[：:]\s*([^\s\n|]+)')
    d["price"] = g(r'(?:单价|均价|参考价格)[：:]\s*(\d{3,})')
    d["address"] = g(r'楼盘地址[：:]\s*([^\n|]+)')
    d["district"] = g(r'所属区域[：:]\s*([^\s\n|]+)')
    d["open_date"] = g(r'开盘时间[：:]\s*([^\n|]+)')
    d["delivery_date"] = g(r'交房时间[：:]\s*([^\n|]+)')
    d["far"] = g(r'容积率[：:]\s*([\d.]+)')
    gm = re.search(r'绿化率[：:]\s*([\d.]+)%', t); d["green"] = gm.group(1) + "%" if gm else ""
    d["units"] = g(r'(?:规划户数|总户数)[：:]\s*(\d+)')
    d["property_company"] = g(r'物业公司[：:]\s*([^\n|]+)')
    d["property_fee"] = g(r'物业费[：:]\s*([^\n|]+)')
    d["developer"] = g(r'开发商[：:]\s*([^\n|]+)')
    d["build_type"] = g(r'建筑类别[：:]\s*([^\n|]+)')
    d["room_types"] = g(r'主力户型[：:]\s*([^\n|]+)')
    d["area_range"] = g(r'建筑面积[：:]\s*([^\n|]+)')
    return d

def to_row(pid, name, d):
    r = {h: "" for h in FULL_HEADERS}
    r["楼盘ID"]="FANG"+pid; r["楼盘名称"]=d.get("name") or name; r["城市名称"]="济南"
    r["区域名称"]=d.get("district",""); r["子区域名称"]=d.get("bizcircle",""); r["地址"]=d.get("address","")
    r["最新价格"]=d.get("price",""); r["参考价格"]=(d["price"]+"元/㎡") if d.get("price") else ""
    r["建筑面积"]=d.get("area_range",""); r["容积率"]=d.get("far",""); r["绿化率"]=d.get("green","")
    r["规划户数"]=d.get("units",""); r["物业公司"]=d.get("property_company",""); r["物业管理费"]=d.get("property_fee","")
    r["开发商"]=d.get("developer",""); bt=d.get("build_type",""); r["建筑类型"]=bt
    r["物业类型"]=next((p for p in PTYPE if p in bt),"") or next((p for p in PTYPE if p in (d.get("room_types","")+name)),"")
    r["全部户型"]=d.get("room_types",""); r["户型文本描述"]=d.get("room_types","")
    r["开盘日期"]=d.get("open_date",""); r["交房时间"]=d.get("delivery_date",""); r["销售状态"]=d.get("status","")
    r["业内评价"]="来源:房天下 %s/loupan/%s.htm 采集%s" % (HOST, pid, TODAY)
    return [r[h] for h in FULL_HEADERS]

def load_ckpt():
    if CKPT.exists():
        try: return json.loads(CKPT.read_text(encoding="utf-8"))
        except Exception: pass
    return {"ids": [], "names": {}, "done": [], "phase1_done": False}
def save_ckpt(c): CKPT.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
def save_stage(rows):
    with open(STAGE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(FULL_HEADERS); w.writerows(rows)

def add_ids(c, seen, items):
    new = 0
    for pid, name in items:
        if pid not in seen:
            seen.add(pid); c["ids"].append(pid)
            if name: c["names"][pid] = name
            new += 1
    return new

def merge_into_vault():
    def read(p):
        rr = list(csv.reader(open(p, encoding="utf-8-sig"))); return rr[0], rr[1:]
    ni = LEGACY_HEADERS.index("楼盘名称")
    def norm(s): return re.sub(r"[\s&·・·\-—丨|（）()]", "", str(s)).strip().lower()
    for tgt in ["Vault/2026新楼盘/新楼盘-济南.csv", "Vault/2026年/济南.csv"]:
        p = ROOT / tgt; existing = []
        if p.exists(): _, existing = read(p)
        _, staged = read(STAGE)
        best, order = {}, []
        for row in existing + staged:
            k = norm(row[ni]) or norm(row[0]); fill = sum(1 for v in row if str(v).strip())
            if k not in best: best[k] = row; order.append(k)
            elif fill > sum(1 for v in best[k] if str(v).strip()): best[k] = row
        merged = [best[k] for k in order]
        if p.exists():
            bak = ROOT / "Vault" / "_backup_premigration" / (tgt.replace("/", "_") + ".prefang")
            bak.parent.mkdir(parents=True, exist_ok=True)
            if not bak.exists(): bak.write_bytes(p.read_bytes())
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f); w.writerow(FULL_HEADERS); w.writerows(merged)
        try:
            import pandas as pd
            pd.read_csv(p, dtype=str, keep_default_na=False).to_parquet(str(p).replace(".csv", ".parquet"), index=False)
        except Exception as e: print("  [warn] parquet 跳过(%s)" % e)
        print("  [入库] %s : %d 行" % (tgt, len(merged)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=71)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--no-details", action="store_true")
    ap.add_argument("--no-merge", action="store_true")
    a = ap.parse_args()
    c = load_ckpt()

    if not c.get("phase1_done"):
        print("[阶段1] 收集楼盘 id（首页+翻页+区县/商圈筛选入口）...")
        seen = set(c["ids"])
        # 1a. 首页（拿 cookie + 抓筛选入口）
        base_html = get_html(BASE)
        if base_html:
            n = add_ids(c, seen, parse_list(base_html)); print("  首页: +%d (累计 %d)" % (n, len(c["ids"])))
        filters = harvest_filters(base_html) if base_html else []
        print("  发现 %d 个 区县/商圈 筛选入口" % len(filters))
        # 1b. 翻页（带 cookie）；连续 +0 三次则停
        zero = 0
        for pg in range(2, a.pages + 1):
            html = get_html(BASE + "b9%d/" % pg)
            if not html: print("  翻页%d: 被验证/失败,跳过" % pg); continue
            n = add_ids(c, seen, parse_list(html))
            print("  翻页%d/%d: +%d (累计 %d)" % (pg, a.pages, n, len(c["ids"])))
            save_ckpt(c)
            zero = zero + 1 if n == 0 else 0
            if zero >= 3:
                print("  翻页连续 +0，停止翻页，改用筛选入口兜底"); break
            time.sleep(a.delay * 0.5 + random.random())
        # 1c. 区县/商圈 筛选入口（各自独立返回不同楼盘）
        for i, fu in enumerate(filters, 1):
            html = get_html(fu)
            if not html: continue
            n = add_ids(c, seen, parse_list(html))
            if n: print("  筛选[%d/%d] +%d (累计 %d) %s" % (i, len(filters), n, len(c["ids"]), fu.split('/house/s/')[1]))
            save_ckpt(c)
            time.sleep(a.delay * 0.5 + random.random())
        c["phase1_done"] = True; save_ckpt(c)
        print("[阶段1完成] 共 %d 个楼盘" % len(c["ids"]))

    rows_by_id = {}
    if STAGE.exists() and STAGE.stat().st_size > 0:
        rr = list(csv.reader(open(STAGE, encoding="utf-8-sig")))
        for row in rr[1:]:
            if row: rows_by_id[row[0]] = row
    ids = c["ids"][: a.limit] if a.limit else c["ids"]
    if a.no_details:
        for pid in ids:
            if "FANG"+pid not in rows_by_id: rows_by_id["FANG"+pid] = to_row(pid, c["names"].get(pid, ""), {})
    else:
        print("[阶段2] 抓详情(%d 个; 断点续抓)..." % len(ids))
        done = set(c["done"])
        for i, pid in enumerate(ids, 1):
            if pid in done and "FANG"+pid in rows_by_id: continue
            d = fetch_details(pid)
            rows_by_id["FANG"+pid] = to_row(pid, c["names"].get(pid, ""), d)
            c["done"].append(pid)
            if i % 20 == 0 or i == len(ids):
                save_stage(list(rows_by_id.values())); save_ckpt(c)
                print("  %d/%d  最新: %s" % (i, len(ids), (d.get("name") or c["names"].get(pid, pid))))
            time.sleep(a.delay + random.random())
    save_stage(list(rows_by_id.values()))
    print("[阶段2完成] staging %d 行" % len(rows_by_id))
    if not a.no_merge:
        print("[阶段3] 合并入库 Vault ..."); merge_into_vault()
    print("\n✓ 完成。DDS 里可查询济南了。")

if __name__ == "__main__":
    main()
