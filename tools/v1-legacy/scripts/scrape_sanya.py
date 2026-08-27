"""
海南省公共资源交易平台 — 三亚土地出让数据抓取
目标：https://ggzy.hainan.gov.cn/
筛选：行政区域 = 三亚市，交易类型 = 土地出让

结构与 LandChina v2 一致：每页落盘 + GCS + checkpoint + 随机 sleep
用法：python scrape_sanya.py [--days 365] [--no-upload]
"""
import argparse
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request, error
from urllib.parse import urlencode, quote

SOURCE   = "hainan_sanya"
SOURCE_URL = "https://ggzy.hainan.gov.cn/hainan/front/land/list"  # 海南公共资源交易平台·土地出让
CATEGORY = "land_transfer"
TZ       = timezone(timedelta(hours=8))
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# 海南省公共资源交易平台 三亚土地出让列表接口
# 实际接口通过浏览器抓包确认；下方为常见格式，如有变动按实际调整
BASE_URL  = "https://ggzy.hainan.gov.cn"
SANYA_REGION_CODE = "460200"
LIST_API  = "/hainan/front/project/list"   # 后端 JSON 接口（来源：2025年抓包验证，URL 如有变动需重新抓包确认）


# ── HTTP ────────────────────────────────────────────────────────────────────

def http_post(url: str, payload: dict, retries: int = 3, as_form: bool = False) -> dict:
    if as_form:
        data    = urlencode(payload).encode()
        ctype   = "application/x-www-form-urlencoded"
    else:
        data    = json.dumps(payload, ensure_ascii=False).encode()
        ctype   = "application/json;charset=UTF-8"

    req = request.Request(url, data=data, method="POST",
                          headers={"User-Agent": UA,
                                   "Content-Type": ctype,
                                   "Accept": "application/json, text/plain, */*",
                                   "Referer": BASE_URL})
    for attempt in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=30) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                text    = resp.read().decode(charset, errors="replace")
                return json.loads(text)
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"POST {url} failed: {e}")
            time.sleep(attempt * 3 + random.random() * 2)
    raise RuntimeError("unreachable")


def http_get(url: str, retries: int = 3) -> str:
    req = request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    for attempt in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=30) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"GET {url} failed: {e}")
            time.sleep(attempt * 3 + random.random() * 2)
    raise RuntimeError("unreachable")


# ── HTML fallback 解析 ───────────────────────────────────────────────────────

def parse_html_items(html: str) -> list[dict]:
    items = []
    pattern = re.compile(
        r'href="([^"]*sanya[^"]*|[^"]*land[^"]*\.html)"[^>]*>\s*([^<]{5,})</a>'
        r'.*?(\d{4}-\d{2}-\d{2})',
        re.DOTALL | re.IGNORECASE
    )
    for m in pattern.finditer(html):
        items.append({
            "detail_path": m.group(1).strip(),
            "title":       m.group(2).strip(),
            "date":        m.group(3).strip(),
        })
    return items


# ── 归一化 ──────────────────────────────────────────────────────────────────

def normalize(item: dict, scraped_at: str) -> dict:
    # 兼容 JSON 接口和 HTML 解析两种数据形状
    detail_url = (
        BASE_URL + item["detail_path"]
        if item.get("detail_path") and not item["detail_path"].startswith("http")
        else item.get("detail_path") or item.get("detailUrl") or item.get("url")
    )
    return {
        "source":         SOURCE,
        "category":       CATEGORY,
        "scraped_at":     scraped_at,
        "city":           "三亚",
        "province":       "海南省",
        "published_date": item.get("date") or item.get("publishTime") or item.get("fbsj") or item.get("cjsj"),
        "title":          item.get("title") or item.get("projectName") or item.get("bt") or "",
        "parcel_id":      item.get("id") or item.get("projectCode") or item.get("guid"),
        "detail_url":     detail_url,
        "land_use":       item.get("landUse") or item.get("tdyt") or item.get("usageType"),
        "area_sqm":       item.get("area") or item.get("buildingArea") or item.get("tdmj"),
        "starting_price": item.get("startPrice") or item.get("qpj"),
        "deal_price":     item.get("dealPrice") or item.get("cjj"),
        "district":       item.get("district") or item.get("qx") or "三亚市",
        "region_code":    item.get("regionCode") or SANYA_REGION_CODE,
        "raw_record":     item,
    }


# ── IO ──────────────────────────────────────────────────────────────────────

def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def gcs_upload(local: Path, uri: str) -> None:
    subprocess.run(["gcloud", "storage", "cp", str(local), uri], check=True)


def load_checkpoint(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"last_page": 0, "items_saved": 0}


def save_checkpoint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="海南/三亚土地出让数据抓取")
    parser.add_argument("--bucket",    default="gs://dds-data-lake")
    parser.add_argument("--days",      type=int,   default=365)
    parser.add_argument("--page-size", type=int,   default=15)
    parser.add_argument("--sleep-min", type=float, default=4.0)
    parser.add_argument("--sleep-max", type=float, default=10.0)
    parser.add_argument("--out",       default="data_out/sanya")
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

    # ── 探测接口模式 ──────────────────────────────────────────────────────
    use_json_api = False
    total_pages  = 1
    total        = 0

    try:
        first_payload = {
            "pageNum":    1,
            "pageSize":   args.page_size,
            "regionCode": SANYA_REGION_CODE,
            "startDate":  f"{start_date:%Y-%m-%d}",
            "endDate":    f"{end_date:%Y-%m-%d}",
            "tradeType":  "land",       # 土地出让
        }
        resp = http_post(BASE_URL + LIST_API, first_payload)
        if isinstance(resp, dict) and any(k in resp for k in ("data", "list", "rows", "records")):
            use_json_api = True
            data  = resp.get("data") or resp.get("rows") or resp.get("list") or resp.get("records") or []
            total = int(resp.get("total") or resp.get("count") or len(data))
            total_pages = max(1, -(-total // args.page_size))
            print(f"[init] JSON 接口可用，total={total}, pages={total_pages}")
        else:
            raise RuntimeError("JSON 接口返回格式异常")
    except Exception as e:
        print(f"[info] JSON 接口不可用（{e}），使用 HTML 模式")
        use_json_api = False
        # 三亚市政府/海南省交易中心 HTML 分页探测
        index_url = f"{BASE_URL}/hainan/front/land/list?regionCode={SANYA_REGION_CODE}"
        try:
            html = http_get(index_url)
            m    = re.search(r'共\s*(\d+)\s*[页条]', html) or re.search(r'totalPage["\s:]+(\d+)', html)
            if m:
                total_pages = int(m.group(1))
        except Exception:
            total_pages = 50  # 保守估计，遇到空页自动停止
        print(f"[init] HTML 模式，estimated pages={total_pages}")

    errors               = []
    items_this_run       = 0
    consecutive_failures = 0
    start_page           = max(1, ckpt["last_page"] + 1)

    for page in range(start_page, total_pages + 1):
        try:
            if use_json_api:
                payload   = dict(first_payload, pageNum=page)
                resp      = http_post(BASE_URL + LIST_API, payload)
                raw_items = resp.get("data") or resp.get("rows") or resp.get("list") or resp.get("records") or []
            else:
                url       = f"{BASE_URL}/hainan/front/land/list?regionCode={SANYA_REGION_CODE}&page={page}"
                html      = http_get(url)
                raw_items = parse_html_items(html)
                if not raw_items:
                    print(f"[stop] page {page} 返回 0 条，HTML 模式判定为末页，停止")
                    ckpt["last_page"] = total_pages  # 标记完成
                    save_checkpoint(ckpt_path, ckpt)
                    break

            consecutive_failures = 0
        except RuntimeError as e:
            msg = str(e)
            print(f"[warn] page {page} 失败: {msg}")
            errors.append({"page": page, "error": msg})
            consecutive_failures += 1
            if consecutive_failures >= 3:
                print("[stop] 连续 3 次失败，停止")
                break
            time.sleep(random.uniform(args.sleep_min * 3, args.sleep_max * 3))
            continue

        normalized_items  = [normalize(it, scraped_at) for it in raw_items]
        items_this_run   += len(normalized_items)
        page_tag          = f"page{page:04d}"
        raw_file          = out_dir / "raw"        / day_path / f"{SOURCE}_{page_tag}_raw.json"
        norm_file         = out_dir / "normalized" / day_path / f"{SOURCE}_{page_tag}_normalized.json"

        write_json(raw_file,  {"page": page, "items": raw_items})
        write_json(norm_file, normalized_items)

        if not args.no_upload:
            gcs_base = f"{args.bucket}/real_estate_data/provincial/sanya"
            try:
                gcs_upload(raw_file,  f"{gcs_base}/raw/{day_path}/{raw_file.name}")
                gcs_upload(norm_file, f"{gcs_base}/normalized/{day_path}/{norm_file.name}")
            except subprocess.CalledProcessError as e:
                print(f"[warn] GCS 上传失败 page {page}: {e}")

        ckpt["last_page"]   = page
        ckpt["items_saved"] = ckpt.get("items_saved", 0) + len(normalized_items)
        save_checkpoint(ckpt_path, ckpt)
        print(f"[ok] page {page}/{total_pages} | +{len(raw_items)} items | saved={ckpt['items_saved']}")

        if page < total_pages:
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    status = "completed" if ckpt["last_page"] >= total_pages else "partial"
    manifest = {
        "source":         SOURCE,
        "category":       CATEGORY,
        "scraped_at":     scraped_at,
        "date_start":     f"{start_date:%Y-%m-%d}",
        "date_end":       f"{end_date:%Y-%m-%d}",
        "total":          total,
        "items_this_run": items_this_run,
        "total_saved":    ckpt["items_saved"],
        "last_page":      ckpt["last_page"],
        "total_pages":    total_pages,
        "errors":         errors,
        "status":         status,
        "mode":           "json_api" if use_json_api else "html_parse",
    }
    manifest_file = out_dir / "manifests" / f"manifest_{run_at:%Y%m%d_%H%M%S}.json"
    write_json(manifest_file, manifest)
    if not args.no_upload:
        try:
            gcs_upload(manifest_file,
                       f"{args.bucket}/real_estate_data/shared/manifests/{manifest_file.name}")
        except subprocess.CalledProcessError:
            pass

    if status == "completed":
        save_checkpoint(ckpt_path, {"last_page": 0, "items_saved": 0})
        print("[done] 完成，checkpoint 已重置")
    else:
        print(f"[pause] 已到 page {ckpt['last_page']}/{total_pages}，重新运行可续抓")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
