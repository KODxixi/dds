"""
三亚市网签成交数据抓取
目标：海南省住建厅 / 三亚市住建局商品房网签备案 — 一房一价成交记录
平台：https://zjt.hainan.gov.cn/ 或 https://zjj.sanya.gov.cn/

字段目标：楼盘名、房号、建筑面积、成交总价、成交单价、签约日期、户型、楼层

结构：checkpoint + 每页落盘 + GCS
用法：python scrape_sanya_deals.py [--days 365] [--no-upload]
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request, error
from urllib.parse import urlencode

SOURCE   = "sanya_deals"
CATEGORY = "residential_transaction"
TZ       = timezone(timedelta(hours=8))
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# T7: 从环境变量读 URL，未配置时优雅降级
_SANYA_DEALS_URL = os.getenv("SANYA_DEALS_URL", "").rstrip("/") if os.getenv("SANYA_DEALS_URL") else None
if not _SANYA_DEALS_URL:
    print("[WARN] SANYA_DEALS_URL not configured in environment")
    print("[WARN] Set SANYA_DEALS_URL in .env and retry, or see README for manual setup")
    sys.exit(0)  # 优雅退出

# 三亚/海南住建局接口（按优先级尝试，优先使用环变量）
ENDPOINTS = [
    {"base": _SANYA_DEALS_URL, "path": "",                                "method": "post_json"},
    {"base": "https://zjt.hainan.gov.cn",  "path": "/api/transaction/query",      "method": "post_json"},
    {"base": "https://zjj.sanya.gov.cn",   "path": "/col/houseinfo/index.html",   "method": "get_html"},
    {"base": "https://ggzy.hainan.gov.cn", "path": "/hainan/front/house/deal",    "method": "post_json"},
]
SANYA_REGION = "469000"


# ── HTTP ────────────────────────────────────────────────────────────────────

def http_get(url: str, retries: int = 3) -> str:
    req = request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    for i in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=25) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
        except Exception as e:
            if i == retries: raise RuntimeError(f"GET {url}: {e}")
            time.sleep(i * 4 + random.random() * 2)
    raise RuntimeError("unreachable")


def post_json_req(url: str, payload: dict, retries: int = 3) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req  = request.Request(url, data=data, method="POST",
                           headers={"User-Agent": UA,
                                    "Content-Type": "application/json;charset=UTF-8",
                                    "Accept": "application/json"})
    for i in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace"))
        except Exception as e:
            if i == retries: raise RuntimeError(f"POST {url}: {e}")
            time.sleep(i * 4 + random.random() * 2)
    raise RuntimeError("unreachable")


# ── HTML 解析 ────────────────────────────────────────────────────────────────

def _to_float(s: str):
    try: return float(re.sub(r"[^\d.]", "", s or ""))
    except: return None


def parse_deal_html(html: str) -> list[dict]:
    items = []
    rows  = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>\s*(.*?)\s*</td>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(cells) >= 5 and not re.search(r'楼盘|项目|备案', cells[0] if cells else ""):
            items.append({
                "project_name": cells[0] if len(cells) > 0 else None,
                "unit_id":      cells[1] if len(cells) > 1 else None,
                "area_sqm":     _to_float(cells[2]) if len(cells) > 2 else None,
                "total_price":  _to_float(cells[3]) if len(cells) > 3 else None,
                "deal_date":    cells[4] if len(cells) > 4 else None,
                "unit_price":   _to_float(cells[5]) if len(cells) > 5 else None,
            })
    return [it for it in items if it.get("project_name")]


# ── 归一化 ──────────────────────────────────────────────────────────────────

def normalize(item: dict, scraped_at: str) -> dict:
    return {
        "source":        SOURCE,
        "category":      CATEGORY,
        "scraped_at":    scraped_at,
        "city":          "三亚",
        "province":      "海南省",
        "region_code":   SANYA_REGION,
        "deal_date":     item.get("deal_date") or item.get("signDate") or item.get("备案日期"),
        "project_name":  item.get("project_name") or item.get("projectName") or item.get("楼盘名称"),
        "unit_id":       item.get("unit_id") or item.get("houseCode") or item.get("房号"),
        "area_sqm":      item.get("area_sqm") or _to_float(str(item.get("建筑面积") or "")),
        "total_price":   item.get("total_price") or _to_float(str(item.get("备案价格") or item.get("成交总价") or "")),
        "unit_price":    item.get("unit_price") or _to_float(str(item.get("单价") or "")),
        "floor":         item.get("floor") or item.get("楼层"),
        "room_type":     item.get("room_type") or item.get("户型"),
        "district":      item.get("district") or "三亚市",
        "confidence":    "high",
        "raw_record":    item,
    }


# ── IO / GCS ────────────────────────────────────────────────────────────────

def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def gcs_upload(local: Path, uri: str) -> None:
    subprocess.run(["gcloud", "storage", "cp", str(local), uri], check=True)


def load_checkpoint(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() \
           else {"last_page": 0, "items_saved": 0, "endpoint_idx": 0}


def save_checkpoint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="三亚网签成交数据抓取")
    parser.add_argument("--bucket",    default="gs://dds-data-lake")
    parser.add_argument("--days",      type=int,   default=365)
    parser.add_argument("--page-size", type=int,   default=20)
    parser.add_argument("--sleep-min", type=float, default=4.0)
    parser.add_argument("--sleep-max", type=float, default=10.0)
    parser.add_argument("--out",       default="data_out/sanya_deals")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    run_at     = datetime.now(TZ)
    end_date   = run_at.date()
    start_date = end_date - timedelta(days=args.days)
    day_path   = f"{run_at:%Y/%m/%d}"
    scraped_at = run_at.isoformat()

    out_dir   = Path(args.out)
    ckpt_path = out_dir / "checkpoint.json"
    ckpt      = load_checkpoint(ckpt_path)

    # ── 探测可用接口 ──────────────────────────────────────────────────────
    active_ep    = None
    use_json_api = False
    total_pages  = 50
    total        = 0

    for ep in ENDPOINTS:
        try:
            if ep["method"] == "post_json":
                payload = {"pageNum": 1, "pageSize": args.page_size,
                           "regionCode": SANYA_REGION,
                           "startDate": f"{start_date:%Y-%m-%d}",
                           "endDate":   f"{end_date:%Y-%m-%d}"}
                resp = post_json_req(ep["base"] + ep["path"], payload)
                if isinstance(resp, dict) and any(k in resp for k in ("data","list","rows","records")):
                    active_ep    = ep
                    use_json_api = True
                    data         = resp.get("data") or resp.get("rows") or resp.get("list") or []
                    total        = int(resp.get("total") or resp.get("count") or len(data))
                    total_pages  = max(1, -(-total // args.page_size))
                    print(f"[init] JSON 接口可用: {ep['base']}{ep['path']}, total={total}")
                    break
            else:
                url  = f"{ep['base']}{ep['path']}"
                html = http_get(url)
                if parse_deal_html(html):
                    active_ep    = ep
                    use_json_api = False
                    print(f"[init] HTML 接口可用: {url}")
                    break
        except Exception as e:
            print(f"[skip] {ep['base']}{ep['path']}: {e}")
            continue

    if not active_ep:
        print("[error] 所有接口均不可用，请手动抓包确认接口地址")
        return 1

    errors = []
    items_this_run = 0
    consecutive_failures = 0
    start_page = max(1, ckpt["last_page"] + 1)

    for page in range(start_page, total_pages + 1):
        try:
            if use_json_api:
                payload   = {"pageNum": page, "pageSize": args.page_size,
                             "regionCode": SANYA_REGION,
                             "startDate": f"{start_date:%Y-%m-%d}",
                             "endDate":   f"{end_date:%Y-%m-%d}"}
                resp      = post_json_req(active_ep["base"] + active_ep["path"], payload)
                raw_items = resp.get("data") or resp.get("rows") or resp.get("list") or []
            else:
                url       = f"{active_ep['base']}{active_ep['path']}?page={page}"
                html      = http_get(url)
                raw_items = parse_deal_html(html)
                if not raw_items:
                    print(f"[stop] page {page} 为空，HTML 模式末页")
                    ckpt["last_page"] = total_pages
                    save_checkpoint(ckpt_path, ckpt)
                    break

            consecutive_failures = 0
        except RuntimeError as e:
            errors.append({"page": page, "error": str(e)})
            consecutive_failures += 1
            print(f"[warn] page {page}: {e}")
            if consecutive_failures >= 3:
                print("[stop] 连续 3 次失败")
                break
            time.sleep(random.uniform(args.sleep_min * 3, args.sleep_max * 3))
            continue

        norm_items     = [normalize(it, scraped_at) for it in raw_items]
        items_this_run += len(norm_items)
        page_tag        = f"page{page:04d}"
        raw_file        = out_dir / "raw"        / day_path / f"{SOURCE}_{page_tag}_raw.json"
        norm_file       = out_dir / "normalized" / day_path / f"{SOURCE}_{page_tag}_normalized.json"

        write_json(raw_file,  {"page": page, "items": raw_items})
        write_json(norm_file, norm_items)

        if not args.no_upload:
            gcs_base = f"{args.bucket}/real_estate_data/provincial/sanya"
            try:
                gcs_upload(raw_file,  f"{gcs_base}/deals/raw/{day_path}/{raw_file.name}")
                gcs_upload(norm_file, f"{gcs_base}/deals/normalized/{day_path}/{norm_file.name}")
            except subprocess.CalledProcessError as e:
                print(f"[warn] GCS 上传 page {page}: {e}")

        ckpt["last_page"]   = page
        ckpt["items_saved"] = ckpt.get("items_saved", 0) + len(norm_items)
        save_checkpoint(ckpt_path, ckpt)
        print(f"[ok] page {page}/{total_pages} | +{len(raw_items)} | saved={ckpt['items_saved']}")

        if page < total_pages:
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    status = "completed" if ckpt["last_page"] >= total_pages else "partial"
    manifest = {
        "source": SOURCE, "category": CATEGORY, "scraped_at": scraped_at,
        "date_start": f"{start_date:%Y-%m-%d}", "date_end": f"{end_date:%Y-%m-%d}",
        "items_this_run": items_this_run, "total_saved": ckpt["items_saved"],
        "errors": errors, "status": status,
    }
    mf = out_dir / "manifests" / f"manifest_{run_at:%Y%m%d_%H%M%S}.json"
    write_json(mf, manifest)
    if not args.no_upload:
        try: gcs_upload(mf, f"{args.bucket}/real_estate_data/shared/manifests/{mf.name}")
        except: pass

    if status == "completed":
        save_checkpoint(ckpt_path, {"last_page": 0, "items_saved": 0, "endpoint_idx": 0})
        print("[done] 完成，checkpoint 已重置")
    else:
        print(f"[pause] 至 page {ckpt['last_page']}，重新运行可续抓")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
