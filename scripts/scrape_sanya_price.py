"""
三亚市/海南省商品房预售价格备案公示（一房一价）抓取脚本

【URL 探测结果记录】
- https://www.hnfdc.gov.cn/ -> ReadTimeout
- https://zjj.sanya.gov.cn/ -> ReadTimeout
- https://zjt.hainan.gov.cn/ -> ReadTimeout
- https://ggzy.hainan.gov.cn/hainan/front/house/deal -> ReadTimeout

由于官方 URL 当前无法直接通过脚本访问（可能存在 WAF/Geoblock 或网络限制），
此脚本处于"结构完整但待填入真实 URL"的状态。

【下一步操作指南】
1. 使用本地浏览器手动打开海南省房地产综合监管平台等官方网站。
2. 找到"一房一价"或"商品房价格备案"栏目。
3. 打开浏览器开发者工具 (F12) -> Network 面板，点击查询或翻页。
4. 找到返回楼盘列表及具体房源价格的 XHR/Fetch JSON 请求。
5. 将抓到的真实 API 接口填入下方的 `BASE_URL` 和 `LIST_API_PATH` / `DETAIL_API_PATH`。
6. 根据实际 JSON 结构，调整下方网络请求和 `normalize` 函数的字段映射。
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request
from urllib.parse import urlencode

SOURCE = "sanya_price"
CATEGORY = "price_registration"
TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# T7: 从环境变量读 URL，未配置时优雅降级
BASE_URL = os.getenv("SANYA_PRICE_URL", "").rstrip("/") if os.getenv("SANYA_PRICE_URL") else None
if not BASE_URL:
    print(f"[WARN] SANYA_PRICE_URL not configured in environment")
    print("[WARN] Set {} in .env and retry, or see README for manual setup".format("SANYA_PRICE_URL"))
    sys.exit(0)  # 优雅退出
LIST_API_PATH = "/api/v1/projects"    # TODO: 替换为楼盘列表接口
DETAIL_API_PATH = "/api/v1/rooms"     # TODO: 替换为房源明细(一房一价)接口


# ── HTTP ────────────────────────────────────────────────────────────────────

def get_json(url: str, retries: int = 3) -> dict:
    req = request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=15) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return json.loads(resp.read().decode(charset, errors="replace"))
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"GET {url} failed: {e}")
            time.sleep(attempt * 3 + random.random())
    raise RuntimeError("unreachable")

def post_json(url: str, payload: dict, retries: int = 3) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST",
                          headers={"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
    for attempt in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=15) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return json.loads(resp.read().decode(charset, errors="replace"))
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"POST {url} failed: {e}")
            time.sleep(attempt * 3 + random.random())
    raise RuntimeError("unreachable")


# ── 归一化 ──────────────────────────────────────────────────────────────────

def normalize(item: dict, scraped_at: str) -> dict:
    """
    TODO: 根据真实抓取到的 JSON 字典 item 结构，映射对应的值
    """
    return {
        "source": SOURCE,
        "category": CATEGORY,
        "scraped_at": scraped_at,
        "city": "三亚",
        "province": "海南省",
        "district": item.get("district", ""),          # TODO: 替换真实键名
        "project_name": item.get("projectName", ""),   # TODO: 替换真实键名
        "developer": item.get("developer", ""),        # TODO: 替换真实键名
        "address": item.get("address", ""),            # TODO: 替换真实键名
        "presale_permit": item.get("permitNo", ""),    # TODO: 替换真实键名
        "building_no": item.get("buildingNo", ""),     # TODO: 替换真实键名
        "unit_no": item.get("unitNo", ""),             # TODO: 替换真实键名
        "floor": item.get("floor", ""),                # TODO: 替换真实键名
        "room_type": item.get("roomType", ""),         # TODO: 替换真实键名
        "area_sqm": float(item.get("area", 0)),        # TODO: 替换真实键名
        "total_price_cny": float(item.get("totalPrice", 0)), # TODO: 替换真实键名
        "unit_price_cny": float(item.get("unitPrice", 0)),   # TODO: 替换真实键名
        "registration_date": item.get("regDate", ""),  # TODO: 替换真实键名
        "status": item.get("status", "在售"),          # TODO: 替换真实键名
        "confidence": "high",
        "source_url": BASE_URL,
        "raw_record": item,
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
    parser = argparse.ArgumentParser(description="三亚商品房预售价格备案抓取")
    parser.add_argument("--bucket",    default="gs://dds-data-lake")
    parser.add_argument("--page-size", type=int,   default=20)
    parser.add_argument("--max-pages", type=int,   default=None)
    parser.add_argument("--sleep-min", type=float, default=4.0)
    parser.add_argument("--sleep-max", type=float, default=10.0)
    parser.add_argument("--out",       default=r"C:\Users\shiguanyu\DDS\data_out\sanya_price")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    run_at = datetime.now(TZ)
    day_path = f"{run_at:%Y/%m/%d}"
    scraped_at = run_at.isoformat()

    out_dir = Path(args.out)
    ckpt_path = out_dir / "checkpoint.json"
    ckpt = load_checkpoint(ckpt_path)

    # 模拟探测总页数
    total_pages = 1
    total = 0
    try:
        if "TODO" not in BASE_URL:
            # TODO: 替换为实际探测首页的逻辑
            first_payload = {"page": 1, "size": args.page_size}
            resp = post_json(BASE_URL + LIST_API_PATH, first_payload)
            total = resp.get("total", 0)
            total_pages = max(1, -(-total // args.page_size))
        else:
            print("[init] ⚠️ 警告：当前未配置真实 API URL，将以 demo 模式运行抓取一页数据用于测试")
            total_pages = 1
            total = 10
    except Exception as e:
        print(f"[error] 初始化失败: {e}")
        return 1

    errors = []
    items_this_run = 0
    consecutive_failures = 0
    start_page = max(1, ckpt["last_page"] + 1)

    if args.max_pages:
        total_pages = min(total_pages, start_page + args.max_pages - 1)

    for page in range(start_page, total_pages + 1):
        try:
            if "TODO" not in BASE_URL:
                # TODO: 实际的请求抓取逻辑（先抓楼盘列表，再抓房源明细）
                payload = {"page": page, "size": args.page_size}
                resp = post_json(BASE_URL + LIST_API_PATH, payload)
                raw_items = resp.get("data", [])
            else:
                # Demo 数据，用于验证架构和文件落地
                raw_items = [{
                    "district": "吉阳区", "projectName": "测试楼盘B", "developer": "三亚测试房产",
                    "address": "迎宾路", "permitNo": "三亚预售字(2023)第00002号",
                    "buildingNo": "2幢", "unitNo": f"{i}02室", "floor": str(i),
                    "roomType": "两室一厅", "area": 89.5, "totalPrice": 2350000,
                    "unitPrice": 26256, "regDate": "2023-11-01", "status": "在售"
                } for i in range(1, 11)]

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

        normalized_items = [normalize(it, scraped_at) for it in raw_items]
        items_this_run += len(normalized_items)
        page_tag = f"page{page:04d}"
        
        raw_file = out_dir / "raw" / day_path / f"{SOURCE}_{page_tag}_raw.json"
        norm_file = out_dir / "normalized" / day_path / f"{SOURCE}_{page_tag}_normalized.json"

        write_json(raw_file, {"page": page, "items": raw_items})
        write_json(norm_file, normalized_items)

        if not args.no_upload:
            gcs_base = f"{args.bucket}/real_estate_data/provincial/sanya/{CATEGORY}"
            try:
                gcs_upload(raw_file, f"{gcs_base}/raw/{day_path}/{raw_file.name}")
                gcs_upload(norm_file, f"{gcs_base}/normalized/{day_path}/{norm_file.name}")
            except subprocess.CalledProcessError as e:
                print(f"[warn] GCS 上传失败 page {page}: {e}")

        ckpt["last_page"] = page
        ckpt["items_saved"] = ckpt.get("items_saved", 0) + len(normalized_items)
        save_checkpoint(ckpt_path, ckpt)
        print(f"[ok] page {page}/{total_pages} | +{len(raw_items)} items | saved={ckpt['items_saved']}")

        if page < total_pages:
            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    status = "completed" if ckpt["last_page"] >= total_pages else "partial"
    if status == "completed":
        save_checkpoint(ckpt_path, {"last_page": 0, "items_saved": 0})
        print("[done] 完成，checkpoint 已重置")
    else:
        print(f"[pause] 已到 page {ckpt['last_page']}/{total_pages}，重新运行可续抓")

    return 0

if __name__ == "__main__":
    sys.exit(main())
