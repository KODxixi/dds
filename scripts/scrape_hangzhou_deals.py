"""
杭州市网签成交数据抓取
目标：杭州市住建局商品房网签备案 — 一房一价成交记录
平台：https://zjj.hangzhou.gov.cn/

字段目标：楼盘名、楼栋/单元/房号、建筑面积、成交总价、成交单价、签约日期、户型、楼层

结构：checkpoint + 每页落盘 + GCS
用法：python scrape_hangzhou_deals.py [--days 365] [--no-upload]
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

SOURCE   = "hangzhou_deals"
CATEGORY = "residential_transaction"
TZ       = timezone(timedelta(hours=8))
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# T7: 从环境变量读 URL，未配置时优雅降级
BASE_URL = os.getenv("HZ_DEALS_URL", "").rstrip("/") if os.getenv("HZ_DEALS_URL") else None
if not BASE_URL:
    print("[WARN] HZ_DEALS_URL not configured in environment")
    print("[WARN] Set HZ_DEALS_URL in .env and retry, or see README for manual setup")
    sys.exit(0)  # 优雅退出

# 备选：杭州市公共数据平台或安居客公开接口
ALT_URL   = "https://hz.anjuke.com"

# 主接口路径（需实际抓包确认，此处为推断值）
API_PATHS = [
    "/col/col1229122/index.html",        # 网签公示列表页
    "/icity/api/house/deal/list",        # JSON 接口备选1
    "/api/v1/transaction/list",          # JSON 接口备选2
]


# ── HTTP ────────────────────────────────────────────────────────────────────

def http_get(url: str, retries: int = 3) -> str:
    req = request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*",
                                         "Referer": BASE_URL})
    for i in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=25) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
        except Exception as e:
            if i == retries: raise RuntimeError(f"GET {url}: {e}")
            time.sleep(i * 4 + random.random() * 2)
    raise RuntimeError("unreachable")


def post_form(url: str, payload: dict, retries: int = 3) -> dict:
    data = urlencode(payload).encode()
    req  = request.Request(url, data=data, method="POST",
                           headers={"User-Agent": UA,
                                    "Content-Type": "application/x-www-form-urlencoded",
                                    "Referer": BASE_URL})
    for i in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace"))
        except Exception as e:
            if i == retries: raise RuntimeError(f"POST {url}: {e}")
            time.sleep(i * 4 + random.random() * 2)
    raise RuntimeError("unreachable")


# ── HTML 解析（住建局公示页格式）────────────────────────────────────────────

def parse_deal_html(html: str) -> list[dict]:
    """
    解析住建局网签公示 HTML 表格
    常见格式：<tr><td>楼盘</td><td>房号</td><td>面积</td><td>总价</td><td>日期</td></tr>
    """
    items = []
    # 提取所有 <tr> 行
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>\s*(.*?)\s*</td>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]  # 去 HTML 标签
        if len(cells) >= 5 and re.search(r'\d{4}', cells[0] if cells else ""):
            # 跳过表头行
            continue
        if len(cells) >= 5:
            items.append({
                "project_name": cells[0] if len(cells) > 0 else None,
                "unit_id":      cells[1] if len(cells) > 1 else None,
                "area_sqm":     _to_float(cells[2]) if len(cells) > 2 else None,
                "total_price":  _to_float(cells[3]) if len(cells) > 3 else None,
                "deal_date":    cells[4] if len(cells) > 4 else None,
                "unit_price":   _to_float(cells[5]) if len(cells) > 5 else None,
                "floor":        cells[6] if len(cells) > 6 else None,
                "room_type":    cells[7] if len(cells) > 7 else None,
            })
    return [it for it in items if it.get("project_name")]


def _to_float(s: str):
    try: return float(re.sub(r"[^\d.]", "", s or ""))
    except: return None


# ── 归一化 ──────────────────────────────────────────────────────────────────

def normalize(item: dict, scraped_at: str) -> dict:
    return {
        "source":        SOURCE,
        "category":      CATEGORY,
        "scraped_at":    scraped_at,
        "city":          "杭州",
        "province":      "浙江省",
        "deal_date":     item.get("deal_date") or item.get("signDate") or item.get("cjrq"),
        "project_name":  item.get("project_name") or item.get("projectName") or item.get("lpmc"),
        "unit_id":       item.get("unit_id") or item.get("houseCode") or item.get("fwbh"),
        "area_sqm":      item.get("area_sqm") or _to_float(str(item.get("jzmj") or "")),
        "total_price":   item.get("total_price") or _to_float(str(item.get("cjzj") or "")),
        "unit_price":    item.get("unit_price") or _to_float(str(item.get("cjdj") or "")),
        "floor":         item.get("floor") or item.get("lc"),
        "room_type":     item.get("room_type") or item.get("hx"),
        "district":      item.get("district") or item.get("qx"),
        "confidence":    "high",   # 住建局网签属于一级数据
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
           else {"last_page": 0, "items_saved": 0}


def save_checkpoint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="杭州网签成交数据抓取")
    parser.add_argument("--bucket",    default="gs://dds-data-lake")
    parser.add_argument("--days",      type=int,   default=365)
    parser.add_argument("--page-size", type=int,   default=20)
    parser.add_argument("--sleep-min", type=float, default=4.0)
    parser.add_argument("--sleep-max", type=float, default=10.0)
    parser.add_argument("--out",       default="data_out/hangzhou_deals")
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

    # ── 探测接口 ──────────────────────────────────────────────────────────
    use_json = False
    total_pages = 50   # HTML 模式默认上限，遇空页停止
    total = 0

    try:
        payload = {
            "pageNo":    1,
            "pageSize":  args.page_size,
            "startDate": f"{start_date:%Y-%m-%d}",
            "endDate":   f"{end_date:%Y-%m-%d}",
        }
        for api_path in API_PATHS[1:]:   # 跳过 HTML 路径
            try:
                resp = post_form(BASE_URL + api_path, payload)
                if isinstance(resp, dict) and any(k in resp for k in ("data","list","rows","records")):
                    use_json    = True
                    data        = resp.get("data") or resp.get("rows") or resp.get("list") or []
                    total       = int(resp.get("total") or resp.get("count") or len(data))
                    total_pages = max(1, -(-total // args.page_size))
                    print(f"[init] JSON 接口可用 {api_path}，total={total}")
                    break
            except Exception:
                continue
    except Exception:
        pass

    if not use_json:
        print(f"[init] HTML 模式，上限 {total_pages} 页（遇空页自动停）")

    errors = []
    items_this_run = 0
    consecutive_failures = 0
    start_page = max(1, ckpt["last_page"] + 1)

    for page in range(start_page, total_pages + 1):
        try:
            if use_json:
                payload  = {"pageNo": page, "pageSize": args.page_size,
                            "startDate": f"{start_date:%Y-%m-%d}", "endDate": f"{end_date:%Y-%m-%d}"}
                resp     = post_form(BASE_URL + API_PATHS[1], payload)
                raw_items = resp.get("data") or resp.get("rows") or resp.get("list") or []
            else:
                url      = f"{BASE_URL}{API_PATHS[0]}?pageNo={page}&startDate={start_date}&endDate={end_date}"
                html     = http_get(url)
                raw_items = parse_deal_html(html)
                if not raw_items:
                    print(f"[stop] page {page} 为空，HTML 模式判定末页")
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
            gcs_base = f"{args.bucket}/real_estate_data/provincial/hangzhou"
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
        save_checkpoint(ckpt_path, {"last_page": 0, "items_saved": 0})
        print("[done] 完成，checkpoint 已重置")
    else:
        print(f"[pause] 至 page {ckpt['last_page']}，重新运行可续抓")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
