"""
DDS 详情页增强脚本
读取已抓的 normalized JSON → 补抓详情字段 → 写 enriched JSON → 上传 GCS

支持三个数据源：landchina / hangzhou_ctc / hainan_sanya
用法：
  python enrich_detail.py --source landchina --date 2026/05/11
  python enrich_detail.py --source hangzhou_ctc --input data_out/hangzhou/normalized/...
"""
import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request, error

TZ = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


# ── HTTP ────────────────────────────────────────────────────────────────────

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


def http_get(url: str, retries: int = 3) -> str:
    req = request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    for i in range(1, retries + 1):
        try:
            with request.urlopen(req, timeout=20) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
        except Exception as e:
            if i == retries: raise RuntimeError(f"GET {url}: {e}")
            time.sleep(i * 3 + random.random())
    raise RuntimeError("unreachable")


def post_json(url: str, payload: dict, extra_headers: dict = None, retries: int = 3) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"User-Agent": UA, "Content-Type": "application/json;charset=UTF-8",
                "Accept": "application/json", **(extra_headers or {})}
    req = request.Request(url, data=data, method="POST", headers=headers)
    opener = request.build_opener(NoRedirect)
    for i in range(1, retries + 1):
        try:
            with opener.open(req, timeout=20) as r:
                return json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace"))
        except Exception as e:
            if i == retries: raise RuntimeError(f"POST {url}: {e}")
            time.sleep(i * 3 + random.random())
    raise RuntimeError("unreachable")


# ── LandChina 详情 ──────────────────────────────────────────────────────────

LC_BASE    = "https://api.landchina.com"
LC_DETAIL  = "/tGygg/transfer/detail"   # GET ?id={guid} 或 POST {id}
LC_DETAIL2 = "/tGygg/transfer/getById"  # 备选接口

def _lc_hash(endpoint: str) -> str:
    raw = f"{UA}{datetime.now(TZ).day}{endpoint}"
    return hashlib.md5(raw.encode()).hexdigest()


def fetch_landchina_detail(parcel_id: str) -> dict:
    """尝试多个接口形式获取 LandChina 详情"""
    endpoint = LC_DETAIL.rstrip("/").split("/")[-1]
    headers  = {
        "Origin":  "https://www.landchina.com",
        "Referer": "https://www.landchina.com/",
        "Hash":    _lc_hash(endpoint),
    }
    # 方式1: GET
    try:
        url  = f"{LC_BASE}{LC_DETAIL}?id={parcel_id}"
        resp = json.loads(http_get(url))  # 直接 GET
        if resp.get("code") == 200:
            return resp.get("data") or {}
    except Exception:
        pass

    # 方式2: POST {id}
    try:
        resp = post_json(f"{LC_BASE}{LC_DETAIL}", {"id": parcel_id}, extra_headers=headers)
        if resp.get("code") == 200:
            return resp.get("data") or {}
    except Exception:
        pass

    # 方式3: POST {gyggGuid}
    try:
        resp = post_json(f"{LC_BASE}{LC_DETAIL2}", {"gyggGuid": parcel_id}, extra_headers=headers)
        if resp.get("code") == 200:
            return resp.get("data") or {}
    except Exception:
        pass

    return {}


def parse_landchina_detail(raw: dict) -> dict:
    """从详情 API 响应提取关键字段"""
    def num(v):
        try: return float(v)
        except: return None
    return {
        "land_use":          raw.get("tdyt")   or raw.get("landUse"),
        "area_sqm":          num(raw.get("tdmj")  or raw.get("area")),
        "starting_price":    num(raw.get("qpj")   or raw.get("startPrice")),
        "floor_area_ratio":  num(raw.get("rjl")   or raw.get("floorAreaRatio")),
        "coverage_ratio":    num(raw.get("jzmj")  or raw.get("coverageRatio")),
        "green_ratio":       num(raw.get("ldl")   or raw.get("greenRatio")),
        "building_height":   num(raw.get("jzxg")  or raw.get("buildingHeight")),
        "plot_ratio":        num(raw.get("rjl")),
        "supply_period":     raw.get("gytq")   or raw.get("supplyPeriod"),
        "listing_end_date":  raw.get("jzbmsj") or raw.get("listingEndDate"),
        "lng":               num(raw.get("jd")    or raw.get("longitude")),
        "lat":               num(raw.get("wd")    or raw.get("latitude")),
        "detail_raw":        raw,
    }


# ── 杭州 详情 ───────────────────────────────────────────────────────────────

def fetch_hangzhou_detail(detail_url: str) -> dict:
    if not detail_url: return {}
    try:
        html = http_get(detail_url)
        return parse_html_detail(html)
    except Exception:
        return {}


# ── 三亚 详情 ───────────────────────────────────────────────────────────────

def fetch_sanya_detail(detail_url: str) -> dict:
    if not detail_url: return {}
    try:
        html = http_get(detail_url)
        return parse_html_detail(html)
    except Exception:
        return {}


# ── 通用 HTML 详情解析 ───────────────────────────────────────────────────────

_LABEL_MAP = {
    # 中文标签 → 标准字段
    "土地用途": "land_use",
    "用地面积": "area_sqm",
    "起始价":   "starting_price",
    "容积率":   "floor_area_ratio",
    "建筑密度": "coverage_ratio",
    "绿地率":   "green_ratio",
    "限高":     "building_height",
    "出让年限": "supply_period",
    "竞买保证金": "deposit",
    "成交价":   "deal_price",
    "挂牌起始日": "listing_start",
    "截止日期": "listing_end",
}

def parse_html_detail(html: str) -> dict:
    result = {}
    # 常见模式：<td>标签</td><td>值</td>
    td_pairs = re.findall(r'<td[^>]*>\s*([^<]{2,20})\s*</td>\s*<td[^>]*>\s*([^<]*)\s*</td>', html)
    for label, value in td_pairs:
        label = label.strip().rstrip("：:")
        value = value.strip()
        if label in _LABEL_MAP and value:
            field = _LABEL_MAP[label]
            # 数值字段转 float
            if field in ("area_sqm", "starting_price", "floor_area_ratio",
                         "coverage_ratio", "green_ratio", "building_height", "deal_price"):
                try: result[field] = float(re.sub(r"[^\d.]", "", value))
                except: result[field] = value
            else:
                result[field] = value
    # 经纬度（常见 JS 变量）
    m = re.search(r'longitude["\s:]+([0-9.]+)', html)
    if m: result["lng"] = float(m.group(1))
    m = re.search(r'latitude["\s:]+([0-9.]+)', html)
    if m: result["lat"] = float(m.group(1))
    return result


# ── 增强主逻辑 ───────────────────────────────────────────────────────────────

FETCHERS = {
    "landchina":    lambda item: parse_landchina_detail(fetch_landchina_detail(item.get("parcel_id", ""))),
    "hangzhou_ctc": lambda item: fetch_hangzhou_detail(item.get("detail_url")),
    "hainan_sanya": lambda item: fetch_sanya_detail(item.get("detail_url")),
}


def enrich_file(norm_file: Path, source: str, sleep_min: float, sleep_max: float,
                bucket: str, no_upload: bool, dry_run: bool) -> dict:
    items = json.loads(norm_file.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        items = [items]

    fetcher   = FETCHERS[source]
    enriched  = []
    ok = fail = skip = 0

    for item in items:
        if item.get("detail_fetched"):   # 已增强过，跳过
            enriched.append(item)
            skip += 1
            continue
        if dry_run:
            enriched.append(item)
            continue

        try:
            detail = fetcher(item)
            item.update({k: v for k, v in detail.items() if v is not None})
            item["detail_fetched"]  = True
            item["detail_fetched_at"] = datetime.now(TZ).isoformat()
            ok += 1
        except Exception as e:
            item["detail_error"] = str(e)
            fail += 1

        enriched.append(item)
        time.sleep(random.uniform(sleep_min, sleep_max))

    # 写回 enriched 文件（同目录，_enriched 后缀）
    out_path = norm_file.parent / norm_file.name.replace("_normalized", "_enriched")
    out_path.write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")

    if not no_upload and bucket:
        # GCS 路径：normalized → enriched 子目录
        rel    = norm_file.name.replace("_normalized", "_enriched")
        day    = "/".join(norm_file.parts[-4:-1])   # YYYY/MM/DD
        source_path = {"landchina": "landchina", "hangzhou_ctc": "provincial/hangzhou",
                       "hainan_sanya": "provincial/sanya"}.get(source, source)
        uri = f"{bucket}/real_estate_data/{source_path}/enriched/{day}/{rel}"
        try:
            subprocess.run(["gcloud", "storage", "cp", str(out_path), uri], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[warn] GCS 上传失败: {e}")

    return {"file": norm_file.name, "ok": ok, "fail": fail, "skip": skip}


# ── 主入口 ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="DDS 详情页增强")
    parser.add_argument("--source",    required=True, choices=["landchina", "hangzhou_ctc", "hainan_sanya"])
    parser.add_argument("--input",     help="指定单个 normalized JSON 文件路径")
    parser.add_argument("--date",      help="按日期扫描，格式 YYYY/MM/DD，如 2026/05/11")
    parser.add_argument("--bucket",    default="gs://dds-data-lake")
    parser.add_argument("--sleep-min", type=float, default=3.0)
    parser.add_argument("--sleep-max", type=float, default=8.0)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--dry-run",   action="store_true", help="只扫描文件，不实际请求")
    args = parser.parse_args()

    base_dirs = {
        "landchina":    Path("data_out/landchina/normalized"),
        "hangzhou_ctc": Path("data_out/hangzhou/normalized"),
        "hainan_sanya": Path("data_out/sanya/normalized"),
    }

    if args.input:
        files = [Path(args.input)]
    elif args.date:
        files = list((base_dirs[args.source] / args.date).glob("*_normalized.json"))
    else:
        files = list(base_dirs[args.source].rglob("*_normalized.json"))

    if not files:
        print(f"[warn] 没有找到 normalized 文件，请先运行 scrape_*.py")
        return 1

    print(f"[init] source={args.source}, 找到 {len(files)} 个文件")
    total = {"ok": 0, "fail": 0, "skip": 0}

    for f in files:
        result = enrich_file(f, args.source, args.sleep_min, args.sleep_max,
                             args.bucket, args.no_upload, args.dry_run)
        for k in total: total[k] += result[k]
        print(f"[done] {result['file']} ok={result['ok']} fail={result['fail']} skip={result['skip']}")

    print(f"\n[summary] ok={total['ok']} fail={total['fail']} skip={total['skip']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
