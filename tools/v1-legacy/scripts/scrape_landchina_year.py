"""
LandChina 断点续抓脚本 v2
特性：每页落盘 + 每页上传 GCS + checkpoint + 随机 sleep + 单线程
用法：python scrape_landchina_year.py [--days 365] [--sleep-min 5] [--sleep-max 12] [--no-upload]
"""
import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request, error

BASE_API = "https://api.landchina.com"
ENDPOINT = "/tGygg/transfer/list"
SOURCE   = "landchina"
CATEGORY = "transfer_announcement"
UA       = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
TZ       = timezone(timedelta(hours=8))


# ── 工具函数 ────────────────────────────────────────────────────────────────

def make_hash(endpoint_name: str) -> str:
    raw = f"{UA}{datetime.now(TZ).day}{endpoint_name}"
    return hashlib.md5(raw.encode()).hexdigest()


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def post_json(path: str, payload: dict, retries: int = 3) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = request.Request(
        BASE_API + path, data=data, method="POST",
        headers={
            "User-Agent":   UA,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept":       "application/json, text/plain, */*",
            "Origin":       "https://www.landchina.com",
            "Referer":      "https://www.landchina.com/",
            "Hash":         make_hash(path.rstrip("/").split("/")[-1]),
        },
    )
    opener = request.build_opener(NoRedirect)
    for attempt in range(1, retries + 1):
        try:
            with opener.open(req, timeout=30) as resp:
                text = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
                return json.loads(text)
        except error.HTTPError as e:
            if e.code in (301, 302):
                raise RuntimeError(f"WAF redirect HTTP {e.code} → {e.headers.get('Location')}")
            if attempt == retries:
                raise RuntimeError(f"HTTP {e.code} after {retries} attempts")
            time.sleep(attempt * 4 + random.random() * 2)
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"request error: {e}")
            time.sleep(attempt * 4 + random.random() * 2)
    raise RuntimeError("unreachable")


def normalize(item: dict, scraped_at: str) -> dict:
    """最小归一化：提取关键字段，保留 raw_record 供后续扩展"""
    region = item.get("xzqFullName") or ""
    province = None
    for suffix in ("省", "自治区", "市"):
        if suffix in region:
            province = region.split(suffix)[0] + suffix
            break
    return {
        "source":            SOURCE,
        "category":          CATEGORY,
        "scraped_at":        scraped_at,
        "published_date":    item.get("fbSj"),
        "province":          province,
        "region_code":       item.get("xzqDm"),
        "region_full_name":  item.get("xzqFullName"),
        "title":             item.get("gyggBt") or "",
        "parcel_id":         item.get("gyggGuid"),
        "announcement_type": item.get("ggLx"),
        "detail_url":        (
            f"https://www.landchina.com/#/landSupplyDetail?id={item.get('gyggGuid')}&type=出让公告&path=0"
            if item.get("gyggGuid") else None
        ),
        "raw_record": item,
    }


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def gcs_upload(local: Path, uri: str) -> None:
    subprocess.run(["gcloud", "storage", "cp", str(local), uri], check=True)


# ── Checkpoint ─────────────────────────────────────────────────────────────

def load_checkpoint(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"last_page": 0, "items_saved": 0}


def save_checkpoint(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 主流程 ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="LandChina 断点续抓 v2")
    parser.add_argument("--bucket",    default="gs://dds-data-lake")
    parser.add_argument("--days",      type=int,   default=365)
    parser.add_argument("--page-size", type=int,   default=10)    # 保守值，勿改大
    parser.add_argument("--sleep-min", type=float, default=5.0)   # 随机 sleep 下界（秒）
    parser.add_argument("--sleep-max", type=float, default=12.0)  # 随机 sleep 上界（秒）
    parser.add_argument("--out",       default="data_out/landchina")
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

    base_payload = {
        "pageNum":   1,
        "pageSize":  args.page_size,
        "startDate": f"{start_date:%Y-%m-%d} 00:00:00",
        "endDate":   f"{end_date:%Y-%m-%d} 23:59:59",
    }

    # ── 第一页：获取总量 ──────────────────────────────────────────────────
    print(f"[init] 请求第 1 页，获取总量…")
    first = post_json(ENDPOINT, base_payload)
    if first.get("code") != 200:
        print(f"[error] API 非 200: {first}", file=sys.stderr)
        return 1

    d           = first.get("data") or {}
    total       = int(d.get("total") or 0)
    total_pages = math.ceil(total / args.page_size) if total else 1
    print(f"[init] total={total}, pages={total_pages}, 从 page {ckpt['last_page'] + 1} 续抓")

    errors         = []
    items_this_run = 0
    consecutive_failures = 0

    # ── 逐页抓取（从 checkpoint 恢复）────────────────────────────────────
    start_page = max(1, ckpt["last_page"] + 1)
    for page in range(start_page, total_pages + 1):
        payload = dict(base_payload, pageNum=page)
        try:
            resp = post_json(ENDPOINT, payload)
            consecutive_failures = 0  # 成功后重置
        except RuntimeError as e:
            msg = str(e)
            print(f"[warn] page {page} 失败: {msg}")
            errors.append({"page": page, "error": msg})
            consecutive_failures += 1
            # 连续失败 3 次判定为 WAF，停止等冷却
            if consecutive_failures >= 3:
                print("[stop] 连续 3 次失败，停止。等待 WAF 冷却后重新运行即可续抓。")
                break
            sleep_t = random.uniform(args.sleep_min * 3, args.sleep_max * 3)
            print(f"[sleep] 错误后等待 {sleep_t:.1f}s …")
            time.sleep(sleep_t)
            continue

        if resp.get("code") != 200:
            errors.append({"page": page, "error": f"code={resp.get('code')}"})
            continue

        items            = (resp.get("data") or {}).get("list") or []
        normalized_items = [normalize(it, scraped_at) for it in items]
        items_this_run  += len(normalized_items)

        # 每页独立文件，按 page 编号命名
        page_tag  = f"page{page:04d}"
        raw_file  = out_dir / "raw"        / day_path / f"{SOURCE}_{page_tag}_raw.json"
        norm_file = out_dir / "normalized" / day_path / f"{SOURCE}_{page_tag}_normalized.json"

        write_json(raw_file,  {"request": payload, "response": resp})
        write_json(norm_file, normalized_items)

        # 立即上传 GCS
        if not args.no_upload:
            gcs_base = f"{args.bucket}/real_estate_data/landchina"
            try:
                gcs_upload(raw_file,  f"{gcs_base}/raw/{day_path}/{raw_file.name}")
                gcs_upload(norm_file, f"{gcs_base}/normalized/{day_path}/{norm_file.name}")
            except subprocess.CalledProcessError as e:
                print(f"[warn] GCS 上传失败 page {page}: {e}")

        # 更新 checkpoint（每页写入，保证断点精度）
        ckpt["last_page"]   = page
        ckpt["items_saved"] = ckpt.get("items_saved", 0) + len(normalized_items)
        save_checkpoint(ckpt_path, ckpt)

        print(f"[ok] page {page}/{total_pages} | +{len(items)} items | saved={ckpt['items_saved']}")

        if page < total_pages:
            sleep_t = random.uniform(args.sleep_min, args.sleep_max)
            time.sleep(sleep_t)

    # ── Manifest ──────────────────────────────────────────────────────────
    status = "completed" if ckpt["last_page"] >= total_pages else "partial"
    manifest = {
        "source":          SOURCE,
        "category":        CATEGORY,
        "scraped_at":      scraped_at,
        "date_start":      f"{start_date:%Y-%m-%d}",
        "date_end":        f"{end_date:%Y-%m-%d}",
        "api_total":       total,
        "items_this_run":  items_this_run,
        "total_saved":     ckpt["items_saved"],
        "last_page":       ckpt["last_page"],
        "total_pages":     total_pages,
        "errors":          errors,
        "status":          status,
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
        # 完成后重置 checkpoint，下次开新一轮
        save_checkpoint(ckpt_path, {"last_page": 0, "items_saved": 0})
        print("[done] 全量完成，checkpoint 已重置")
    else:
        print(f"[pause] 已到 page {ckpt['last_page']}/{total_pages}，直接重新运行脚本即可续抓")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
