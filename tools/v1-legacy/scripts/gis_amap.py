"""
高德 GIS 增强模块
功能：地理编码 + 周边 POI（学校/医院/地铁/商业）+ 路线可达性
输出：enriched JSON，追加 gis_* 字段

用法：
  # 单条地址
  python gis_amap.py --address "三亚市海棠区XX路" --city "三亚"

  # 批量增强 normalized JSON
  python gis_amap.py --input data_out/sanya/normalized/2026/05/11/xxx_normalized.json

申请高德 Key：https://lbs.amap.com/dev/key/app
  → 创建应用 → 添加 Key → 服务平台选"Web服务"
  填入下方 AMAP_KEY 或通过 --key 参数传入
"""
import argparse
import json
import sys
import time
import random
import subprocess
from pathlib import Path
from urllib import request
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta

import os

# CLI 入口（report_parcel / dds_customer_agent 等）不经 app.py，需自加载项目根 .env，
# 否则 os.environ 无 AMAP_KEY → 退化成占位符 → 地理编码必失败 → 误走离线代理
def _load_env_once():
    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))  # setdefault 不覆盖已有真实环境变量

_load_env_once()

# ★ 高德 Web 服务 Key（优先环境变量/.env，缺失时为占位符）
AMAP_KEY = os.environ.get("AMAP_KEY") or "YOUR_AMAP_KEY_HERE"

TZ       = timezone(timedelta(hours=8))
BASE_URL = "https://restapi.amap.com/v3"


# ── POI 类型配置（DDS 关心的配套维度）────────────────────────────────────────

POI_TYPES = {
    "school":    {"typecode": "141200|141201|141202", "label": "学校",   "radius": 2000},
    "hospital":  {"typecode": "090000|090100",         "label": "医院",   "radius": 3000},
    "subway":    {"typecode": "150500",                "label": "地铁站", "radius": 2000},
    "bus":       {"typecode": "150700",                "label": "公交站", "radius": 1000},
    "mall":      {"typecode": "060100|060200",         "label": "商场",   "radius": 3000},
    "park":      {"typecode": "110100|110101",         "label": "公园",   "radius": 2000},
    "supermarket":{"typecode": "060100",               "label": "超市",   "radius": 1500},
}

# 通勤目标：到主要商圈的驾车时间（分钟）
COMMUTE_TARGETS = {
    "hangzhou": ["杭州西湖区", "杭州滨江区", "杭州钱江新城"],
    "sanya":    ["三亚海棠湾", "三亚亚龙湾", "三亚市中心"],
    "jinan":    ["济南汉峪金谷", "济南泉城广场", "济南高新区"],
}


# ── API 调用 ─────────────────────────────────────────────────────────────────

def amap_get(path: str, params: dict, key: str) -> dict:
    params["key"] = key
    url = f"{BASE_URL}{path}?{urlencode(params, encoding='utf-8')}"
    with request.urlopen(url, timeout=10) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if resp.get("status") != "1":
        raise RuntimeError(f"高德 API 错误: {resp.get('info')} | {url}")
    return resp


def geocode(address: str, city: str, key: str) -> dict | None:
    """地址 → 经纬度"""
    resp = amap_get("/geocode/geo", {"address": address, "city": city}, key)
    geocodes = resp.get("geocodes") or []
    if not geocodes:
        return None
    g = geocodes[0]
    lng, lat = g.get("location", ",").split(",")
    return {
        "lng":            float(lng),
        "lat":            float(lat),
        "formatted_addr": g.get("formatted_address"),
        "adcode":         g.get("adcode"),
        "level":          g.get("level"),   # 匹配精度
    }


def nearby_poi(lng: float, lat: float, poi_type: str, key: str,
               types: str = None, radius: int = None, limit: int = 3) -> list[dict]:
    """周边 POI 搜索。

    可通过 poi_type 使用 POI_TYPES 配置，也可通过 types/radius 覆盖。
    """
    if types is None:
        cfg = POI_TYPES.get(poi_type, {"typecode": "050000", "radius": 2000})
        types = cfg["typecode"]
        radius = radius or cfg["radius"]
        label = cfg["label"]
    else:
        radius = radius or 2000
        label = poi_type
    resp = amap_get("/place/around", {
        "location":  f"{lng},{lat}",
        "types":     types,
        "radius":    radius,
        "offset":    limit,
        "page":      1,
        "extensions": "base",
    }, key)
    pois = resp.get("pois") or []
    result = []
    for p in pois:
        loc = p.get("location", "")
        lng_str, lat_str = (loc.split(",") + ["", ""])[:2]
        item = {
            "name":     p.get("name"),
            "distance": int(p.get("distance") or 0),
            "address":  p.get("address"),
            "type":     label,
        }
        try:
            item["lng"] = float(lng_str)
            item["lat"] = float(lat_str)
        except (ValueError, TypeError):
            pass
        result.append(item)
    return result


def driving_time(origin_lng: float, origin_lat: float, dest_address: str, key: str) -> int | None:
    """驾车到目标地址的时间（分钟）"""
    # 先对目标地址编码
    try:
        dest = geocode(dest_address, "", key)
        if not dest:
            return None
        resp = amap_get("/direction/driving", {
            "origin":      f"{origin_lng},{origin_lat}",
            "destination": f"{dest['lng']},{dest['lat']}",
            "strategy":    0,
            "extensions":  "base",
        }, key)
        routes = (resp.get("route") or {}).get("paths") or []
        if routes:
            return round(int(routes[0].get("duration") or 0) / 60)
    except Exception:
        pass
    return None


# ── 单条增强 ─────────────────────────────────────────────────────────────────

def enrich_item(item: dict, key: str, city: str) -> dict:
    """给一条 normalized 记录追加 gis_* 字段"""
    # 已有坐标则跳过地理编码
    lng = item.get("lng") or item.get("gis_lng")
    lat = item.get("lat") or item.get("gis_lat")

    if not (lng and lat):
        address = item.get("address") or item.get("title") or ""
        city_   = item.get("city") or city
        if address:
            geo = geocode(address, city_, key)
            if geo:
                lng, lat = geo["lng"], geo["lat"]
                item["gis_geocode"] = geo
            else:
                item["gis_geocode_failed"] = True
                return item

    item["gis_lng"] = lng
    item["gis_lat"] = lat

    # POI 配套
    gis_poi = {}
    for poi_type in POI_TYPES:
        try:
            pois = nearby_poi(lng, lat, poi_type, key)
            gis_poi[poi_type] = pois
            time.sleep(random.uniform(0.2, 0.5))   # 高德 QPS 限制
        except Exception as e:
            gis_poi[poi_type] = {"error": str(e)}
    item["gis_poi"] = gis_poi

    # 最近地铁站距离（关键溢价因子）
    subway_list = gis_poi.get("subway") or []
    if subway_list and isinstance(subway_list, list) and subway_list:
        item["gis_nearest_subway_m"] = subway_list[0].get("distance")
        item["gis_nearest_subway"]   = subway_list[0].get("name")

    # 最近学校
    school_list = gis_poi.get("school") or []
    if school_list and isinstance(school_list, list) and school_list:
        item["gis_nearest_school_m"] = school_list[0].get("distance")
        item["gis_nearest_school"]   = school_list[0].get("name")

    # 通勤时间（根据城市选目标）
    city_raw = item.get("city") or city
    if "三亚" in city_raw:
        city_key = "sanya"
    elif "杭州" in city_raw:
        city_key = "hangzhou"
    elif "济南" in city_raw:
        city_key = "jinan"
    elif "青岛" in city_raw:
        city_key = "qingdao"
    else:
        city_key = "shanghai"
    commute_targets = COMMUTE_TARGETS.get(city_key, [])
    commute = {}
    for target in commute_targets[:2]:   # 只算前 2 个，节省 QPS
        try:
            mins = driving_time(lng, lat, target, key)
            commute[target] = mins
            time.sleep(random.uniform(0.3, 0.6))
        except Exception:
            commute[target] = None
    item["gis_commute_min"] = commute
    item["gis_enriched_at"] = datetime.now(TZ).isoformat()

    return item


# ── 批量增强 ─────────────────────────────────────────────────────────────────

def enrich_file(norm_file: Path, key: str, city: str, bucket: str, no_upload: bool) -> dict:
    items = json.loads(norm_file.read_text(encoding="utf-8"))
    if not isinstance(items, list): items = [items]

    ok = skip = fail = 0
    result = []
    for item in items:
        if item.get("gis_enriched_at"):
            result.append(item); skip += 1; continue
        try:
            item = enrich_item(item, key, city)
            ok += 1
        except Exception as e:
            item["gis_error"] = str(e); fail += 1
        result.append(item)
        time.sleep(random.uniform(0.5, 1.5))

    out = norm_file.parent / norm_file.name.replace("_normalized", "_gis")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not no_upload and bucket:
        day    = "/".join(norm_file.parts[-4:-1])
        source = "landchina"
        if "hangzhou" in str(norm_file): source = "provincial/hangzhou"
        elif "sanya"   in str(norm_file): source = "provincial/sanya"
        uri = f"{bucket}/real_estate_data/{source}/gis/{day}/{out.name}"
        try: subprocess.run(["gcloud", "storage", "cp", str(out), uri], check=True)
        except subprocess.CalledProcessError as e: print(f"[warn] GCS 上传: {e}")

    return {"file": norm_file.name, "ok": ok, "skip": skip, "fail": fail}


# ── 主入口 ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="高德 GIS 增强")
    parser.add_argument("--key",       default=AMAP_KEY, help="高德 Web 服务 Key")
    parser.add_argument("--address",   help="单条地址测试")
    parser.add_argument("--city",      default="", help="城市（用于地理编码消歧）")
    parser.add_argument("--input",     help="normalized JSON 文件路径")
    parser.add_argument("--date",      help="按日期批量处理，格式 YYYY/MM/DD")
    parser.add_argument("--source",    default="sanya", choices=["landchina","hangzhou","sanya"])
    parser.add_argument("--bucket",    default="gs://dds-data-lake")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    key = args.key
    if key == "YOUR_AMAP_KEY_HERE":
        print("[error] 请先填入高德 API Key：--key YOUR_KEY")
        print("申请地址：https://lbs.amap.com/dev/key/app → Web 服务")
        return 1

    # ── 单条地址测试模式 ──────────────────────────────────────────────────
    if args.address:
        result = geocode(args.address, args.city, key)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result:
            for poi_type in list(POI_TYPES.keys())[:3]:
                pois = nearby_poi(result["lng"], result["lat"], poi_type, key)
                print(f"\n{POI_TYPES[poi_type]['label']}：")
                for p in pois:
                    print(f"  {p['name']} — {p['distance']}m")
        return 0

    # ── 批量模式 ──────────────────────────────────────────────────────────
    base_dirs = {
        "landchina": Path("data_out/landchina/normalized"),
        "hangzhou":  Path("data_out/hangzhou/normalized"),
        "sanya":     Path("data_out/sanya/normalized"),
    }
    city_map = {"landchina": "", "hangzhou": "杭州", "sanya": "三亚"}

    if args.input:
        files = [Path(args.input)]
    elif args.date:
        files = list((base_dirs[args.source] / args.date).glob("*_normalized.json"))
    else:
        files = list(base_dirs[args.source].rglob("*_normalized.json"))

    if not files:
        print("[warn] 没有找到 normalized 文件")
        return 1

    city = args.city or city_map[args.source]
    total = {"ok": 0, "skip": 0, "fail": 0}
    for f in files:
        r = enrich_file(f, key, city, args.bucket, args.no_upload)
        for k in total: total[k] += r[k]
        print(f"[done] {r['file']} ok={r['ok']} skip={r['skip']} fail={r['fail']}")

    print(f"\n[summary] ok={total['ok']} skip={total['skip']} fail={total['fail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
