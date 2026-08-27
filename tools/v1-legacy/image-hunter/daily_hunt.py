#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Image Hunter — Daily Auto Hunt  (每日自动猎取)
================================================
在不改动原 index.py 的前提下，新增「自动发现 → 智能打分筛选 → 去重 →
复用原下载/分类/归档管线」的每日批处理层。

来源:
  - ArchDaily 中国   (www.archdaily.cn)      requests 直连
  - 谷德 gooood       (www.gooood.cn)         服务端渲染最新项目
  - 有方 archiposition(www.archiposition.com) 服务端渲染项目流
  - mooool 木藕       (mooool.com/feed)       标准 RSS
  - _inbox.txt        小红书 / 微信公众号链接队列 → 逐条调用 index.py

发现层只抓文章 URL（稳健），标题与原图统一从详情页取（<title> + 图床正则）。

用法:
    python daily_hunt.py            # 跑全部启用来源
    python daily_hunt.py --dry-run  # 只发现+打分，不下载
    python daily_hunt.py --source gooood
"""

import sys, os, re, json, time, html, subprocess, hashlib, shutil
from pathlib import Path
from datetime import datetime
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:
    print("错误: 需要 requests → pip install requests"); sys.exit(1)

# 复用原 skill 的全部下载/分类/归档逻辑（保证“之前的逻辑”一致）
SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))
import index as ih   # noqa: E402

# ───────────────────────── 配置 ─────────────────────────
def load_config():
    cfg_path = SKILL_DIR / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cfg.setdefault("daily_max_total", 15)
    cfg.setdefault("daily_max_per_source", 5)
    cfg.setdefault("discover_cap_per_source", 10)
    cfg.setdefault("min_score", 3.5)
    cfg.setdefault("request_timeout", 30)
    cfg.setdefault("polite_delay_sec", 1.5)
    cfg.setdefault("interest_weights", {})
    cfg.setdefault("noise_keywords", [])
    cfg.setdefault("notable_firm_bonus", 2)
    cfg.setdefault("sources", {})
    cfg.setdefault("llm", {"enabled": False})
    cfg.setdefault("write_daily_digest", True)
    return cfg

CFG = load_config()
TIMEOUT = CFG["request_timeout"]

STATE_DIR = SKILL_DIR / "state"; STATE_DIR.mkdir(exist_ok=True)
LOG_DIR   = SKILL_DIR / "logs";  LOG_DIR.mkdir(exist_ok=True)
SEEN_PATH = STATE_DIR / "seen.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

REFERERS = {
    "archdaily_cn": "https://www.archdaily.cn/",
    "gooood":       "https://www.gooood.cn/",
    "archiposition":"https://www.archiposition.com/",
    "mooool":       "https://mooool.com/",
}

JUNK_IMG = re.compile(r"(placeholder|logo|avatar|gravatar|qrcode|weixin|icon|sprite|"
                      r"banner|/ad[-_]|/ads/|footer|header[-_])", re.I)

# ───────────────────────── 工具 ─────────────────────────
def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line)
    with open(LOG_DIR / f"run-{datetime.now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")

def http_get(url, source=None, timeout=None):
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if source in REFERERS:
        h["Referer"] = REFERERS[source]
    r = requests.get(url, headers=h, timeout=timeout or TIMEOUT)
    r.encoding = r.apparent_encoding or "utf-8"
    return r

def img_headers(source):
    h = {"User-Agent": UA}
    if source in REFERERS:
        h["Referer"] = REFERERS[source]
    return h

def load_seen():
    if SEEN_PATH.exists():
        try: return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
        except Exception: return set()
    return set()

def _atomic_write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(path))

def save_seen(seen):
    _atomic_write_json(SEEN_PATH, sorted(seen))

def canon(url):
    return re.sub(r"[#?].*$", "", url.strip()).rstrip("/")

def dedupe_keep_order(items, keyfn):
    seen, out = set(), []
    for it in items:
        k = keyfn(it)
        if k and k not in seen:
            seen.add(k); out.append(it)
    return out

SITE_SUFFIX = re.compile(r"\s*[|｜\-–—]\s*(ArchDaily|有方|gooood|谷德设计网|谷德|mooool|木藕设计网|木藕).*$", re.I)

def extract_title(html_text):
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html_text, re.I)
    if not m:
        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.S)
    t = html.unescape(m.group(1)).strip() if m else ""
    return SITE_SUFFIX.sub("", t).strip()

def enrich(cand):
    """给候选补标题：抓详情页 <title>，并缓存 HTML 供后续下载复用。"""
    if cand.get("title"):
        return
    try:
        html_text = http_get(cand["url"], cand["source"]).text
        cand["_html"] = html_text
        cand["title"] = extract_title(html_text) or cand["url"].rstrip("/").rsplit("/", 1)[-1]
    except Exception as e:
        log(f"  ⚠️ 取标题失败 {cand['url']}: {e}")
        cand["title"] = ""

# ───────────────────────── 打分（启发式 + 可选 LLM）─────────────────────────
def score_entry(title, summary, source):
    text = f"{title} {summary}"
    score = 1.0
    for kw, w in CFG["interest_weights"].items():
        if kw.startswith("_"): continue
        if kw in text:
            score += w
    if ih.find_designer(text):
        score += CFG["notable_firm_bonus"]
    for nk in CFG["noise_keywords"]:
        if nk in text:
            score -= 4
    score += {"gooood": 0.8, "archdaily_cn": 0.6, "archiposition": 0.6, "mooool": 0.3}.get(source, 0)
    return round(score, 2)

def llm_rerank(candidates):
    """可选：用 OpenAI 兼容接口让 LLM 按质量/话题度复筛，返回保留的 url 集合。"""
    lc = CFG.get("llm", {})
    if not lc.get("enabled"): return None
    try:
        listing = "\n".join(f"{i}. [{c['source']}] {c['title']}" for i, c in enumerate(candidates))
        prompt = ("你是资深建筑策展人。下面是今日各站最新项目标题，请按【设计质量 + 话题度 + "
                  "是否贴合商业综合体/文体/酒店题材】挑选最值得收藏的，"
                  f"最多 {lc.get('select_top',12)} 个。只返回编号，逗号分隔。\n\n{listing}")
        r = requests.post(
            f"{lc['base_url'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {lc.get('api_key','')}",
                     "Content-Type": "application/json"},
            json={"model": lc["model"], "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60)
        txt = r.json()["choices"][0]["message"]["content"]
        idxs = {int(x) for x in re.findall(r"\d+", txt)}
        keep = {candidates[i]["url"] for i in idxs if 0 <= i < len(candidates)}
        log(f"🤖 LLM 复筛保留 {len(keep)} 个")
        return keep
    except Exception as e:
        log(f"⚠️ LLM 复筛失败，回退启发式: {e}")
        return None

# ───────────────────────── 标题 → 项目名/设计方 ─────────────────────────
def parse_proj_des(title):
    t = html.unescape(title or "").strip()
    t = re.sub(r"\s*[\|｜]\s*[A-Za-z].*$", "", t)          # 去掉 “中文｜English” 的英文段
    des = ih.find_designer(t)
    proj = t
    m = re.split(r"\s+by\s+|\s+/\s+|\s+\|\s+|｜|·\s*设计[:：]?", t, maxsplit=1, flags=re.I)
    if len(m) == 2:
        proj = m[0].strip()
        if not des:
            des = m[1].strip()
    proj = ih.clean_name(proj, 25)
    des  = ih.clean_name(des, 15) if des else ""
    return proj or t[:20], des

# ───────────────────────── 详情页取原图 ─────────────────────────
def normalize_gooood(u):
    return re.sub(r"-\d{2,4}x\d{2,4}(\.\w+)$", r"\1", u)

def normalize_wp(u):
    return re.sub(r"-\d{2,4}x\d{2,4}(\.\w+)$", r"\1", u)

def upsize_adsttc(u):
    # .../media/images/HEX/.../{SIZE}/{name}.jpg → 把倒数第二段尺寸换成 large_jpg
    parts = u.split("/")
    if len(parts) >= 2:
        parts[-2] = "large_jpg"
    return "/".join(parts)

def extract_images(html_text, source):
    raw = []
    if source == "gooood":
        raw = [normalize_gooood(u) for u in re.findall(
            r'https://oss\.gooood\.cn/uploads/[^\s"\'<>)]+?\.(?:jpg|jpeg|png)', html_text, re.I)]
    elif source == "archdaily_cn":
        found = re.findall(r'https://images\.adsttc\.com/media/images/[^\s"\'<>)]+?\.(?:jpg|jpeg|png)',
                           html_text, re.I)
        raw = [upsize_adsttc(u) for u in found]
    elif source == "archiposition":
        raw = re.findall(r'https://image\.archiposition\.com/[^\s"\'<>)]+?\.(?:jpg|jpeg|png)',
                         html_text, re.I)
    elif source == "mooool":
        raw = re.findall(r'https://i\.mooool\.com/img/[^\s"\'<>)?]+?\.(?:jpg|jpeg|png)', html_text, re.I)
    out, seen = [], set()
    for u in raw:
        if JUNK_IMG.search(u): continue
        if u in seen: continue
        seen.add(u); out.append(u)
    return out[:80]

# ───────────────────────── 各来源发现（只抓 URL，标题走详情页）─────────────────────────
def discover_gooood(list_url):
    html_text = http_get(list_url, "gooood").text
    urls = re.findall(r'https://www\.gooood\.cn/[a-z0-9][a-z0-9\-]+\.htm', html_text, re.I)
    bad = ("submission", "ad-article", "ad-text", "job-service", "find-designer",
           "get-project-designer", "official-application", "work-for-gooood",
           "aboutus", "copyright", "/talks", "ad-text-and-images")
    cand = [{"source": "gooood", "url": canon(u), "title": "", "cover": ""}
            for u in urls if not any(b in u for b in bad)]
    return dedupe_keep_order(cand, lambda c: c["url"])

def discover_archdaily(list_url):
    html_text = http_get(list_url, "archdaily_cn").text
    urls = re.findall(r'https://www\.archdaily\.cn/cn/\d{5,}/[a-z0-9\-]+', html_text)
    cand = [{"source": "archdaily_cn", "url": canon(u), "title": "", "cover": ""} for u in urls]
    return dedupe_keep_order(cand, lambda c: c["url"])

def discover_archiposition(list_url):
    # 现代文章 ID 为 10 位 hex（/items/43fb153a83）；排除服务页(competition)与14位日期ID
    html_text = http_get(list_url, "archiposition").text
    urls = re.findall(r'https://www\.archiposition\.com/items/[0-9a-f]{10}(?![0-9a-f])', html_text)
    cand = [{"source": "archiposition", "url": canon(u), "title": "", "cover": ""} for u in urls]
    return dedupe_keep_order(cand, lambda c: c["url"])

def discover_mooool(list_url):
    xml_text = http_get(list_url, "mooool").text
    cand = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            link = (item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "")
            cats = " ".join(e.text or "" for e in item.findall("category"))
            if link:
                cand.append({"source": "mooool", "url": canon(link),
                             "title": title, "cover": "", "summary": cats + " " + desc[:120]})
    except ET.ParseError:
        for link, title in re.findall(r"<link>(https://mooool\.com/[^<]+)</link>.*?<title>([^<]+)</title>",
                                      xml_text, re.S):
            cand.append({"source": "mooool", "url": canon(link), "title": title, "cover": ""})
    return dedupe_keep_order(cand, lambda c: c["url"])

DISCOVERERS = {
    "archdaily_cn":  discover_archdaily,
    "gooood":        discover_gooood,
    "archiposition": discover_archiposition,
    "mooool":        discover_mooool,
}

# ───────────────────────── 归档单个项目 ─────────────────────────
def archive_project(entry, seen, digest):
    url, source = entry["url"], entry["source"]
    html_text = entry.get("_html")   # 发现阶段补标题时已缓存，避免二次抓取
    if not html_text:
        try:
            html_text = http_get(url, source).text
        except Exception as e:
            log(f"  ❌ 详情获取失败 {url}: {e}"); return False
    title = entry.get("title") or extract_title(html_text) or url.rstrip("/").rsplit("/", 1)[-1]
    entry["title"] = title

    imgs = extract_images(html_text, source)
    if not imgs:
        log(f"  ⏭️  无原图，跳过: {title}"); return False

    done, tmp = ih.batch_download(imgs, img_headers(source))
    if not done:
        log(f"  ⏭️  无有效图: {title}")
        shutil.rmtree(tmp, ignore_errors=True); return False

    proj, des = parse_proj_des(title)
    loc = ih.find_location(title)
    dest = ih.do_archive(done, proj, loc, des, "", f"{title} {entry.get('summary','')}")
    shutil.rmtree(tmp, ignore_errors=True)

    seen.add(url); save_seen(seen)
    cat = dest.parent.name
    digest.append({"source": source, "title": title, "category": cat,
                   "images": len(done), "url": url, "dest": str(dest)})
    log(f"  ✅ {title}  [{cat}]  {len(done)}图 → {dest.name}")
    return True

# ───────────────────────── 小红书/公众号 收件箱 ─────────────────────────
def process_inbox(digest):
    inbox = SKILL_DIR / "_inbox.txt"
    if not inbox.exists(): return
    lines = [l.strip() for l in inbox.read_text(encoding="utf-8").splitlines()]
    urls = [l for l in lines if l and not l.startswith("#") and l.startswith("http")]
    if not urls:
        return
    log(f"📥 收件箱: {len(urls)} 条小红书/公众号链接")
    done_log = SKILL_DIR / "_inbox_done.txt"
    remaining = [l for l in lines if l not in urls]   # 保留注释/空行
    for u in urls:
        try:
            r = subprocess.run([sys.executable, str(SKILL_DIR / "index.py"), u],
                               capture_output=True, text=True, encoding="utf-8", timeout=300)
            ok = "归档完成" in (r.stdout or "")
            log(f"  {'✅' if ok else '⚠️'} inbox {u}")
            with open(done_log, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M}\t{'OK' if ok else 'FAIL'}\t{u}\n")
            if ok:
                digest.append({"source": "inbox", "title": u, "category": "(见 index.py 输出)",
                               "images": "-", "url": u, "dest": ""})
        except Exception as e:
            log(f"  ❌ inbox 处理异常 {u}: {e}")
            remaining.append(u)
    inbox.write_text("\n".join(remaining) + "\n", encoding="utf-8")

# ───────────────────────── 日报 ─────────────────────────
def write_digest(digest):
    if not CFG.get("write_daily_digest") or not digest:
        return None
    root = Path(CFG.get("archive_root", str(ih.ARCHIG_ROOT)))
    out_dir = (root if root.exists() else ih.OBSIDIAN_FALLBACK) / "_daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{datetime.now():%Y-%m-%d}.md"
    n_proj = len([d for d in digest if d["source"] != "inbox"])
    lines = [f"# 每日猎取 · {datetime.now():%Y-%m-%d}", "",
             f"共归档 **{n_proj}** 个项目。", ""]
    for d in digest:
        lines.append(f"- **[{d['source']}]** {d['title']} · `{d['category']}` · {d['images']}图  \n  <{d['url']}>")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p

# ───────────────────────── 主流程 ─────────────────────────
def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    backfill = "--backfill" in args
    only = None
    if "--source" in args:
        only = args[args.index("--source") + 1]

    log("═" * 48)
    log(f"🏹 Image Hunter 每日猎取启动  dry_run={dry}")
    seen = load_seen()
    digest = []

    # 1) 发现（每源取最新 N 条，补标题）
    all_cand = []
    cap = CFG.get("discover_cap_per_source", 10)
    for name, sconf in CFG["sources"].items():
        if name == "inbox": continue
        if not sconf.get("enabled"): continue
        if only and name != only: continue
        if name not in DISCOVERERS: continue
        try:
            found = DISCOVERERS[name](sconf["list_url"])
            fresh = [c for c in found if c["url"] not in seen][:cap]
            for c in fresh:
                if not c.get("title"):
                    enrich(c); time.sleep(0.5)
            got = sum(1 for c in fresh if c.get("title"))
            log(f"🔎 {name}: 发现 {len(found)}，取新 {len(fresh)}，得标题 {got}")
            all_cand += fresh
        except Exception as e:
            log(f"⚠️ {name} 发现失败: {e}")

    # 2) 打分
    for c in all_cand:
        c["score"] = score_entry(c.get("title", ""), c.get("summary", ""), c["source"])
    all_cand.sort(key=lambda c: c["score"], reverse=True)

    # 2b) 可选 LLM 复筛
    keep = llm_rerank(all_cand)
    if keep is not None:
        all_cand = [c for c in all_cand if c["url"] in keep]

    # 3) 配额内归档
    per_cap = CFG["daily_max_per_source"]; total_cap = CFG["daily_max_total"]
    per_count, total = {}, 0
    log(f"📋 候选 {len(all_cand)} 个，配额 单站<={per_cap} 总<={total_cap} 阈值>={CFG['min_score']}")
    for c in all_cand:
        if total >= total_cap:
            break
        if c["score"] < CFG["min_score"]:
            continue
        if per_count.get(c["source"], 0) >= per_cap:
            continue
        tag = f"[{c['score']}] [{c['source']}] {c.get('title') or c['url']}"
        if dry:
            log(f"  - {tag}")
            per_count[c["source"]] = per_count.get(c["source"], 0) + 1
            total += 1
            continue
        if archive_project(c, seen, digest):
            per_count[c["source"]] = per_count.get(c["source"], 0) + 1
            total += 1
            time.sleep(CFG["polite_delay_sec"])

    # 3.5) mooool 住宅展示全量（独立结构 → ArchLib）
    if not dry and CFG.get("mooool_residential", {}).get("enabled"):
        hunt_mooool_residential(backfill, digest)

    # 4) 收件箱（小红书/公众号）
    if not dry and CFG["sources"].get("inbox", {}).get("enabled"):
        process_inbox(digest)

    # 5) 日报
    dp = write_digest(digest)
    n_proj = len([d for d in digest if d["source"] != "inbox"])
    log(f"🏁 完成：本次归档 {n_proj} 项" + (f"，日报 {dp}" if dp else ""))
    log("=" * 48)


# ───────────────────────── mooool 住宅展示（全量 → ArchLib 独立结构）─────────────────────────
def discover_mooool_space(space_slug, pages):
    out = []
    excl = ("join-us", "recruitment", "zhaopin", "collection-of", "happy-chinese",
            "-forum", "/category/", "/tag/", "/space/", "/designer/", "/photographer/",
            "/developer/", "/position/", "/groups/", "/member/", "/en/", "/videos",
            "about-us", "copyright", "/page/", "wp-login", "wp-content")
    for pg in range(1, pages + 1):
        url = f"https://mooool.com/space/{space_slug}" + ("" if pg == 1 else f"/page/{pg}")
        try:
            r = http_get(url, "mooool"); h = r.text
        except Exception as e:
            log(f"  ⚠️ {space_slug} 第{pg}页请求异常: {e}"); break
        links = re.findall(r"https://mooool\.com/[a-z0-9%][a-z0-9%\-]+\.html", h, re.I)
        links = [canon(u) for u in links if not any(b in u for b in excl)]
        links = list(dict.fromkeys(links))
        if not links:
            log(f"  ⚠️ {space_slug} 第{pg}页 HTTP {r.status_code}，0 项目链接（限流/改版/翻到底? 停止翻页）")
            break
        out += links
        time.sleep(0.8)
    return list(dict.fromkeys(out))

def _archive_to(done_files, root, cat, proj, loc, des):
    base = Path(root) if Path(root).exists() else ih.OBSIDIAN_FALLBACK
    dest = base / Path(cat) / f"{loc}_{proj}_{des}"
    dest.mkdir(parents=True, exist_ok=True)
    for i, p in done_files:
        ih.save_as_png(p, dest / f"{proj}_{i:02d}")
    return dest

def hunt_mooool_residential(backfill, digest):
    rc = CFG.get("mooool_residential", {})
    root = rc.get("archive_root", "D:\\ArchLib")
    pages = rc.get("backfill_pages", 12) if backfill else rc.get("pages", 2)
    cap = rc.get("max_per_run", 40)
    targets = rc.get("targets", [{"space": "apartments", "cat": "10_居住/11_公寓住宅"},
                                 {"space": "sales-center", "cat": "30_营销空间/31_售楼中心展示区"}])
    seen_path = STATE_DIR / "seen_mooool_res.json"
    seen = set()
    if seen_path.exists():
        try:
            seen = set(json.loads(seen_path.read_text(encoding="utf-8")))
        except Exception:
            log("  ⚠️ seen_mooool_res.json 损坏，已重置"); seen = set()
    grabbed = 0
    log(f"🏠 mooool住宅展示 → {root}  pages={pages} cap={cap} backfill={backfill}")
    for t in targets:
        if grabbed >= cap:
            break
        links = discover_mooool_space(t["space"], pages)
        fresh = [u for u in links if u not in seen]
        log(f"  🔎 {t['cat']}({t['space']}): 列出 {len(links)}，新 {len(fresh)}")
        for url in fresh:
            if grabbed >= cap:
                break
            try:
                h = http_get(url, "mooool").text
            except Exception as e:
                log(f"    ❌ {url}: {e}"); continue
            title = extract_title(h)
            imgs = extract_images(h, "mooool")
            if not imgs:
                log(f"    ⏭️ 无原图: {title}"); seen.add(url); continue
            done, tmp = ih.batch_download(imgs, img_headers("mooool"))
            if not done:
                shutil.rmtree(tmp, ignore_errors=True); seen.add(url); continue
            proj, des = parse_proj_des(title)
            loc = ih.find_location(title) or "未知"
            proj = ih.clean_name(proj, 25) or "未知"
            des = ih.clean_name(des, 15) or "未知"
            dest = _archive_to(done, root, t["cat"], proj, loc, des)
            shutil.rmtree(tmp, ignore_errors=True)
            seen.add(url)
            _atomic_write_json(seen_path, sorted(seen))
            grabbed += 1
            digest.append({"source": "mooool住宅", "title": title, "category": t["cat"],
                           "images": len(done), "url": url, "dest": str(dest)})
            log(f"    ✅ {title}  [{t['cat']}]  {len(done)}图")
            time.sleep(CFG.get("polite_delay_sec", 1.0))
    log(f"🏠 mooool住宅展示完成：本次 {grabbed} 个")

if __name__ == "__main__":
    main()
