"""
DDS 地块报告自动化 — 地址/坐标 → 竞品分析 + 区位配套 + 价格带参照

用法：
  python report_parcel.py --city 三亚 --address "三亚海棠区南田路16号" --expected-price 35000
  python report_parcel.py --city 三亚 --lng 109.71899 --lat 18.41040 --district 海棠区
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gis_amap import AMAP_KEY, POI_TYPES, geocode, nearby_poi
from query_local import CITY_FILES, VAULT, CURRENT_YEAR
from geo_clean import clean_coords

SALE_STATUSES = ("在售", "尾盘")

# ============================================
# DataFrame 缓存系统（P0 优化 1）
# ============================================
class DataFrameCache:
    """监听文件 mtime，自动失效过期缓存"""

    def __init__(self):
        self._cache = {}  # (城市元组, 年份) → (df, mtime 字典)

    def get(self, cities: tuple, years: tuple) -> Optional[pd.DataFrame]:
        """获取缓存的 DataFrame，校验文件 mtime"""
        key = (cities, years)
        if key not in self._cache:
            return None

        df, file_mtimes = self._cache[key]

        # 检查源文件是否被修改
        for city in cities:
            for year in years:
                path = csv_path_for_city(city, year)
                if not path:
                    continue
                if not path.exists():
                    # 源文件被删除，缓存失效
                    if key in self._cache:
                        del self._cache[key]
                    return None
                current_mtime = path.stat().st_mtime
                if file_mtimes.get(str(path)) != current_mtime:
                    # 源文件被修改，缓存失效
                    if key in self._cache:
                        del self._cache[key]
                    return None

        return df

    def set(self, cities: tuple, years: tuple, df: pd.DataFrame):
        """缓存 DataFrame，同时记录源文件 mtime"""
        key = (cities, years)
        file_mtimes = {}

        for city in cities:
            for year in years:
                path = csv_path_for_city(city, year)
                if path and path.exists():
                    file_mtimes[str(path)] = path.stat().st_mtime

        self._cache[key] = (df, file_mtimes)
        print(f"[cache] 已缓存数据 ({len(cities)} 城, {len(years)} 年)", file=sys.stderr)

# 全局缓存实例（模块级单例）
_df_cache = DataFrameCache()


def csv_path_for_city(city: str, year: str = None) -> Path | None:
    target_year = year or CURRENT_YEAR

    # T2: 当 target_year == CURRENT_YEAR(2026) 时，优先 2026新楼盘（全量 479 优于 51 子集）
    if target_year == CURRENT_YEAR:
        for key, fname in CITY_FILES.items():
            if city in key or key in city:
                p_new_loupan = VAULT / "2026新楼盘" / fname
                if p_new_loupan.exists():
                    return p_new_loupan

    # 1. 优先尝试：Vault/{year}年/{city}.csv
    p_new = VAULT / f"{target_year}年" / f"{city}.csv"
    if p_new.exists():
        return p_new

    # 2. 兼容旧规范：原 Vault/2026新楼盘/新楼盘-{city}.csv 或 Vault/新楼盘-{city}.csv
    for key, fname in CITY_FILES.items():
        if city in key or key in city:
            p_old_dir = VAULT / "2026新楼盘" / fname
            if p_old_dir.exists():
                return p_old_dir
            p_root = VAULT / fname
            if p_root.exists():
                return p_root

    # 3. 模糊匹配该年份下的所有 CSV 文件
    year_dir = VAULT / f"{target_year}年"
    if year_dir.exists():
        for p in year_dir.glob("*.csv"):
            c_name = p.stem
            if city in c_name or c_name in city:
                return p

    return None


def get_data_freshness(city: str) -> dict:
    path = csv_path_for_city(city)
    if not path:
        return {"csv_mtime": None, "data_age_days": None, "data_freshness_warning": None}
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    age_days = (datetime.now() - mtime).days
    return {
        "csv_file": path.name,
        "csv_mtime": mtime.isoformat(timespec="seconds"),
        "data_age_days": age_days,
        "data_freshness_warning": f"⚠ 数据已过期 {age_days} 天，建议更新后再做决策" if age_days > 7 else None,
    }


def load_projects(cities: list[str] | None = None, year: str = None,
                  multi_year: bool = True, years_back: int = 4) -> pd.DataFrame:
    """加载城市楼盘。multi_year=True 时合并最近 N 年的 Vault 数据，让样本量从~200 变 ~5000+。

    P0 优化：使用缓存避免重复磁盘 I/O（预期提升 50-70%）
    """
    selected = cities or list(CITY_FILES.keys())

    # 转为元组用作缓存键
    cities_tuple = tuple(sorted(selected))

    # 多年合并：合并最近 years_back 年 + 主年份
    if multi_year:
        base_year = int(year) if (year and str(year).isdigit()) else 2026
        target_years = tuple(str(y) for y in range(base_year - years_back, base_year + 1))
    else:
        target_years = (year or "2026",)

    # ✨ 尝试从缓存获取
    cached_df = _df_cache.get(cities_tuple, target_years)
    if cached_df is not None:
        print(f"[cache-hit] 从缓存加载数据 ({len(cities_tuple)} 城, {len(target_years)} 年)", file=sys.stderr)
        return cached_df.copy()  # 返回副本，防止外部修改原缓存

    print(f"[cache-miss] 从磁盘加载数据 ({len(cities_tuple)} 城, {len(target_years)} 年)", file=sys.stderr)

    frames = []
    for city in selected:
        for ty in target_years:
            path = csv_path_for_city(city, year=ty)
            if not path:
                continue
            csv_path = str(path).replace("\\", "/")

            import os
            parquet_path = csv_path.rsplit('.', 1)[0] + '.parquet'
            if os.path.exists(parquet_path):
                read_source = f"read_parquet('{parquet_path}')"
            else:
                read_source = f"read_csv_auto('{csv_path}', header=true)"

            df = duckdb.query(f"""
                SELECT
                    "楼盘ID" AS loupan_id,
                    "楼盘名称" AS project_name,
                    "城市名称" AS city,
                    "区域名称" AS district,
                    "子区域名称" AS sub_district,
                    "地址" AS address,
                    "最新价格" AS price_raw,
                    "参考价格" AS price_label,
                    CASE WHEN "参考价格" LIKE '%元/㎡%'
                         THEN TRY_CAST("最新价格" AS DOUBLE)
                         ELSE NULL END AS unit_price_cny,
                    CASE WHEN "参考价格" LIKE '%万元/套%'
                         THEN TRY_CAST("最新价格" AS DOUBLE)
                         ELSE NULL END AS total_price_wan,
                    "面积范围" AS area_range,
                    "全部户型" AS room_types,
                    "房间面积信息" AS room_area_json,
                    "户型文本描述" AS room_type_text,
                    "容积率" AS floor_area_ratio,
                    "绿化率" AS green_ratio,
                    "销售状态" AS sales_status,
                    "开发商" AS developer,
                    TRY_CAST("百度地图纬度" AS DOUBLE) AS lat,
                    TRY_CAST("百度地图经度" AS DOUBLE) AS lng,
                    "规交信息" AS subway_json,
                    "物业类型" AS property_type,
                    "标签列表" AS tags,
                    "开盘时间" AS open_date,
                    "交房时间" AS delivery_date,
                    "发证时间" AS cert_dates,
                    "默认图片" AS thumbnail,
                    "占地面积" AS land_area_sqm,
                    "建筑面积" AS building_area_sqm,
                    "规划户数" AS planned_units,
                    "车位数" AS parking_spaces,
                    "车位比" AS parking_ratio,
                    "物业公司" AS property_company,
                    "物业管理费" AS property_fee,
                    "装修情况" AS decoration,
                    "建筑类型" AS building_type,
                    "产权年限" AS tenure_years,
                    "预售证号" AS presale_permit,
                    "开发商品牌" AS developer_brand,
                    "投资商" AS investor,
                    "工程进度" AS construction_progress
                FROM {read_source}
            """).df()
            # T4: 标注 is_synthetic（历史合成 vs 当年真实）
            df["is_synthetic"] = int(ty) < 2026
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    # 去重：同楼盘 + 同城市保留状态最优的一条（在售 > 尾盘 > 售罄）
    _status_rank = {"在售": 0, "尾盘": 1}
    df["_sr"] = df["sales_status"].map(_status_rank).fillna(2)
    df = df.sort_values("_sr").drop_duplicates(subset=["project_name", "city"], keep="first")
    df = df.drop(columns=["_sr"])

    # ✨ 缓存结果
    _df_cache.set(cities_tuple, target_years, df)

    return df.copy()


def resolve_location(city: str, address: str | None, lng: float | None, lat: float | None, key: str) -> dict:
    # 智能解析：如果输入的 address 是 "lng,lat" 坐标串，则自动识别并解构为经纬度，防止调用高德地理编码报错
    if address:
        try:
            parts = [p.strip() for p in address.replace("，", ",").split(",")]
            if len(parts) == 2:
                p_lng = float(parts[0])
                p_lat = float(parts[1])
                if -180.0 <= p_lng <= 180.0 and -90.0 <= p_lat <= 90.0:
                    lng = p_lng
                    lat = p_lat
        except ValueError:
            pass

    if lng is not None and lat is not None:
        return {
            "lng": lng,
            "lat": lat,
            "source": "coordinates",
            "formatted_addr": address if address else f"{lng:.6f},{lat:.6f}",
            "level": None,
            "adcode": None,
        }
    if not address:
        raise ValueError("必须提供 --address，或同时提供 --lng 与 --lat")
    
    # 1. 尝试使用高德在线地理编码
    try:
        geo = geocode(address, city, key)
        if geo:
            return {
                "lng": geo["lng"],
                "lat": geo["lat"],
                "source": "amap_geocode",
                "formatted_addr": geo.get("formatted_addr"),
                "level": geo.get("level"),
                "adcode": geo.get("adcode"),
            }
    except Exception as e:
        print(f"[warn] 高德在线地理编码异常: {e}，将启用免网离线降级匹配！")

    # 2. 【免网离线降级！】模糊检索本地已有的楼盘经纬度作为虚拟坐标
    try:
        projects = load_projects([city], year="2026") # 兜底加载本地2026年数据
        if not projects.empty:
            # 2.1 尝试在地址或名称中进行包含模糊匹配
            matched = projects[
                projects["address"].astype(str).str.contains(address, na=False) | 
                projects["project_name"].astype(str).str.contains(address, na=False)
            ]
            
            # 2.2 尝试提取行政区匹配
            if matched.empty:
                import re
                dist_match = re.search(r"([一-龥A-Za-z0-9]+区)", address)
                if dist_match:
                    dist_name = dist_match.group(1)
                    matched = projects[projects["district"].astype(str).str.contains(dist_name, na=False)]
            
            # 2.3 仍未匹配到，直接取该城市已知项目的首条数据作为中心代理点
            if matched.empty:
                matched = projects
                
            matched = matched.dropna(subset=["lng", "lat"])
            if not matched.empty:
                ref_row = matched.iloc[0]
                return {
                    "lng": float(ref_row["lng"]),
                    "lat": float(ref_row["lat"]),
                    "source": "offline_proxy_match",
                    "formatted_addr": f"离线代理匹配地块: {ref_row['project_name']} ({ref_row['address']})",
                    "level": "proxy",
                    "adcode": None,
                }
    except Exception as inner_e:
        print(f"[warn] 离线匹配代理地址失败: {inner_e}")

    raise RuntimeError(f"高德离线且本地无法模糊匹配到任何代理已知坐标点：{address}")



def haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    radius = 6371.0088
    lng1, lat1, lng2, lat2 = map(math.radians, [lng1, lat1, lng2, lat2])
    dlng = lng2 - lng1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return radius * 2 * math.asin(math.sqrt(a))


def parse_room_detail(room_area_json: str | None) -> list[dict]:
    """解析房间面积信息 JSON，返回 [{name: '三室', area: 120, area_str: '120㎡'}, ...]"""
    if not room_area_json or not isinstance(room_area_json, str):
        return []
    try:
        raw = json.loads(room_area_json)
        if not isinstance(raw, list):
            return []
        # 去重并按面积排序
        seen = set()
        result = []
        for r in raw:
            alias = (r.get("room_alias") or "").strip()
            area = r.get("area")
            if not alias or area is None:
                continue
            key = f"{alias}_{area}"
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "name": alias,
                "area": float(area),
                "area_str": f"{int(area)}㎡" if area == int(area) else f"{area}㎡",
            })
        result.sort(key=lambda x: x["area"])
        return result
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def clean_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def row_dict(row, fields: list[str]) -> dict:
    return {field: clean_value(row.get(field)) for field in fields}


def analyze_nearby(projects: pd.DataFrame, location: dict, city: str, district: str | None, radius_km: float) -> tuple[list[dict], bool]:
    city_mask = projects["city"].astype(str).str.contains(city, na=False)
    if city_mask.any():
        candidates = projects[city_mask].copy()
    else:
        candidates = projects.copy()
    candidates = candidates.dropna(subset=["lng", "lat"])
    if candidates.empty:
        return [], False

    # T1: 坐标清洗 - 剔除越界脏坐标
    candidates = clean_coords(candidates, city)
    # 竞品仅取 geo_valid==True，保留 < 8 条回退到 district 兜底逻辑
    candidates_valid = candidates[candidates["geo_valid"] == True].copy()

    candidates_valid["distance_km"] = candidates_valid.apply(
        lambda r: haversine_km(location["lng"], location["lat"], r["lng"], r["lat"]), axis=1
    )
    nearby = candidates_valid[candidates_valid["distance_km"] <= radius_km].copy()
    fallback_used = False
    # 如 5km 内 < 8 条，扩展到 district 范围（含历史）
    if len(nearby) < 8:
        fallback_used = True
        if district:
            district_pool = candidates[candidates["district"].astype(str).str.contains(district, na=False)].copy()
            if len(district_pool) > len(nearby):
                nearby = district_pool
        if len(nearby) < 8:
            # 回退时也应用坐标清洗后的候选集
            nearby = candidates_valid.copy() if not candidates_valid.empty else candidates.copy()

    nearby["status_rank"] = nearby["sales_status"].map({"在售": 0, "尾盘": 1}).fillna(2)
    nearby["has_price_rank"] = nearby["unit_price_cny"].notna().map({True: 0, False: 1})
    # 排序：在售优先 + 价格优先 + 距离近，扩到 60 条让前端有充分样本
    nearby = nearby.sort_values(["status_rank", "has_price_rank", "distance_km"]).head(60)

    fields = [
        "loupan_id", "project_name", "city", "district", "sub_district", "sales_status", "unit_price_cny",
        "total_price_wan", "area_range", "room_types", "room_area_json", "room_type_text",
        "floor_area_ratio", "green_ratio",
        "developer", "address", "distance_km", "lng", "lat", "property_type", "tags",
        "open_date", "delivery_date", "cert_dates", "thumbnail",
        "land_area_sqm", "building_area_sqm", "planned_units", "parking_spaces",
        "parking_ratio", "property_company", "property_fee", "decoration",
        "building_type", "tenure_years", "presale_permit", "developer_brand", "investor",
        "construction_progress", "is_synthetic",  # T4: 合成数据标记
    ]
    rows = []
    for _, r in nearby.iterrows():
        item = row_dict(r, fields)
        if item.get("distance_km") is not None:
            item["distance_km"] = round(float(item["distance_km"]), 2)
        item["rooms_detail"] = parse_room_detail(item.pop("room_area_json", None))
        item.pop("room_type_text", None)
        # 来源溯源：仅真实数据给真实 URL；合成数据 id/坐标为生成值，绝不伪造 URL
        lp_id = item.get("loupan_id")
        if not item.get("is_synthetic", False):
            item["source"] = "安居客新房(实采)"
            item["source_url"] = "https://www.anjuke.com/"
            if lp_id:
                item["url_anjuke"] = (f"https://hangzhou.fang.anjuke.com/loupan/{lp_id}.html"
                                      if item.get("city") and "杭" in item["city"]
                                      else f"https://m.anjuke.com/loupan/{lp_id}")
            if item.get("lng") and item.get("lat"):
                item["url_amap"] = f"https://uri.amap.com/marker?position={item['lng']},{item['lat']}&name={item.get('project_name','')}"
        else:
            item["source"] = "本地合成-最近邻克隆"
            item["source_url"] = "no-source:generate_cloned_history.py"
        rows.append(item)
    return rows, fallback_used


def summarize_market(competitors: list[dict], include_synthetic: bool = False) -> dict:
    """
    市场概览统计。默认（include_synthetic=False）仅用真实数据做价格统计；
    合成数据仅用于样本密度参考。
    """
    # T4: 默认过滤合成数据（is_synthetic==False）
    if not include_synthetic:
        real_competitors = [c for c in competitors if not c.get("is_synthetic", False)]
        synthetic_count = len(competitors) - len(real_competitors)
    else:
        real_competitors = competitors
        synthetic_count = 0

    # 仅用真实数据计算价格
    prices = [c["unit_price_cny"] for c in real_competitors if c.get("unit_price_cny")]
    statuses = {}
    room_counter = {}
    districts = {}
    for c in real_competitors:
        status = c.get("sales_status") or "未知"
        statuses[status] = statuses.get(status, 0) + 1
        district = c.get("district") or "未知"
        districts[district] = districts.get(district, 0) + 1
        for room in str(c.get("room_types") or "").split(","):
            room = room.strip()
            if room and room.lower() != "nan":
                room_counter[room] = room_counter.get(room, 0) + 1

    result = {
        "sample_count": len(real_competitors),
        "synthetic_count": synthetic_count,
        "price": {
            "avg": round(sum(prices) / len(prices), 0) if prices else None,
            "min": round(min(prices), 0) if prices else None,
            "max": round(max(prices), 0) if prices else None,
            "valid_count": len(prices),
        },
        "sales_status": dict(sorted(statuses.items(), key=lambda x: -x[1])),
        "districts": dict(sorted(districts.items(), key=lambda x: -x[1])),
        "room_types": dict(sorted(room_counter.items(), key=lambda x: -x[1])[:10]),
    }

    # 添加数据质量标注
    if synthetic_count > 0 and not include_synthetic:
        result["data_note"] = f"[信息] 价格统计基于 {len(real_competitors)} 条真实数据，另有 {synthetic_count} 条合成数据用于样本密度参考"

    return result


def analyze_amenities(location: dict, key: str) -> dict:
    result = {}
    for poi_type, cfg in POI_TYPES.items():
        try:
            result[poi_type] = {
                "label": cfg["label"],
                "items": nearby_poi(location["lng"], location["lat"], poi_type, key),
            }
            time.sleep(0.25)
        except Exception as e:
            result[poi_type] = {"label": cfg["label"], "error": str(e), "items": []}
    return result


def analyze_price_band(projects: pd.DataFrame, expected_price: float | None, price_band: float) -> dict | None:
    if expected_price is None:
        return None
    low = expected_price * (1 - price_band)
    high = expected_price * (1 + price_band)
    df = projects.dropna(subset=["unit_price_cny"]).copy()
    df = df[(df["unit_price_cny"] >= low) & (df["unit_price_cny"] <= high)]
    if df.empty:
        return {"expected_price": expected_price, "low": round(low, 0), "high": round(high, 0), "items": [], "city_distribution": {}}
    df["price_delta_abs"] = (df["unit_price_cny"] - expected_price).abs()
    df = df.sort_values(["price_delta_abs", "city", "district"]).head(30)
    fields = [
        "project_name", "city", "district", "sub_district", "sales_status", "unit_price_cny",
        "area_range", "room_types", "developer", "address", "property_type", "tags", "price_delta_abs",
        "open_date", "delivery_date", "thumbnail",
    ]
    items = []
    for _, r in df.iterrows():
        item = row_dict(r, fields)
        item["price_delta_abs"] = round(float(item["price_delta_abs"]), 0)
        items.append(item)
    return {
        "expected_price": expected_price,
        "low": round(low, 0),
        "high": round(high, 0),
        "price_band": price_band,
        "sample_scope": "本地四城样本：三亚、杭州、上海、青岛",
        "city_distribution": df["city"].value_counts().to_dict(),
        "district_distribution": df["district"].value_counts().head(10).to_dict(),
        "items": items,
    }


def markdown_table(rows: list[dict], headers: list[tuple[str, str]], limit: int = 20) -> str:
    if not rows:
        return "暂无数据\n"
    lines = []
    lines.append("| " + " | ".join(title for title, _ in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows[:limit]:
        values = []
        for _, key in headers:
            value = row.get(key)
            if isinstance(value, float):
                value = round(value, 1)
            values.append(str(value if value is not None else "—").replace("\n", " "))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    market = report["market_summary"]
    loc = report["location"]
    price = market["price"]
    lines = [
        f"# DDS 地块画像报告 — {report['input'].get('city')}",
        "",
        "## 数据可信度 [T5]",
        f"- **可用单价样本**: {price['valid_count']} 条",
        f"- **样本来源**: {market['sample_count']} 条真实竞品" +
        (f"（+{market.get('synthetic_count', 0)} 条合成历史用于密度参考）" if market.get('synthetic_count', 0) > 0 else ""),
        f"- **数据快照**: {report['meta'].get('target_year', '2026')} 年",
        f"- **数据备注**: {market.get('data_note', '基于当年真实楼盘数据')}",
        "- **真实数据来源**: 安居客新房 https://www.anjuke.com/ ｜ 配套 POI: 高德 https://lbs.amap.com/",
        "",
        "## 输入摘要",
        f"- 城市：{report['input'].get('city')}",
        f"- 区域：{report['input'].get('district') or '未指定'}",
        f"- 地址：{report['input'].get('address') or '未指定'}",
        f"- 坐标：{loc['lng']}, {loc['lat']}（来源：{loc['source']}）",
        f"- 预期均价：{report['input'].get('expected_price') or '未输入'} 元/㎡",
        "",
        "## 逻辑流程图初稿",
        "```mermaid",
        "flowchart TD",
        "  A[输入地块地址或经纬度] --> B{是否已有坐标}",
        "  B -- 是 --> C[使用坐标定位]",
        "  B -- 否 --> D[高德地理编码]",
        "  C --> E[读取本地四城楼盘库]",
        "  D --> E",
        "  E --> F[计算周边楼盘距离]",
        "  F --> G[筛选半径内竞品]",
        "  G --> H[生成价格/户型/状态摘要]",
        "  C --> I[高德 POI 配套检索]",
        "  D --> I",
        "  I --> J[生成区位配套摘要]",
        "  K{是否输入预期均价} -- 是 --> L[跨城市价格带竞品筛选]",
        "  K -- 否 --> M[跳过价格带分析]",
        "  H --> N[输出 Markdown + JSON 报告]",
        "  J --> N",
        "  L --> N",
        "  M --> N",
        "```",
        "",
        "## 周边竞品分析",
        f"- 样本数：{market['sample_count']}",
        f"- 有效价格样本：{price['valid_count']}",
        f"- 均价：{price['avg'] or '—'} 元/㎡",
        f"- 价格区间：{price['min'] or '—'} - {price['max'] or '—'} 元/㎡",
    ]
    if report.get("nearby_fallback_used"):
        lines.append("- 注意：半径内样本不足，已补充同区或同城样本。")
    lines.extend([
        "",
        "### 竞品列表",
        markdown_table(report["nearby_competitors"], [
            ("楼盘", "project_name"), ("城市", "city"), ("区域", "district"), ("状态", "sales_status"),
            ("均价元/㎡", "unit_price_cny"), ("距离km", "distance_km"), ("面积段", "area_range"),
            ("户型", "room_types"), ("开发商", "developer"), ("来源", "source"),
        ]),
        "## 区位配套分析",
    ])
    for _, payload in report["amenities"].items():
        label = payload.get("label")
        items = payload.get("items") or []
        if payload.get("error"):
            lines.append(f"- {label}：未获取（{payload['error']}）")
        elif items:
            first = items[0]
            lines.append(f"- {label}：最近 {first.get('name')}，约 {first.get('distance')}m")
        else:
            lines.append(f"- {label}：暂无数据")
    if report.get("price_band_competitors"):
        band = report["price_band_competitors"]
        lines.extend([
            "",
            "## 预期均价竞品参照",
            f"当前全国分析基于{band['sample_scope']}，后续可扩展全国 CSV/在线抓取数据源。",
            f"- 预期均价：{band['expected_price']} 元/㎡",
            f"- 筛选价格带：{band['low']} - {band['high']} 元/㎡",
            f"- 城市分布：{band['city_distribution'] or '暂无'}",
            "",
            markdown_table(band["items"], [
                ("楼盘", "project_name"), ("城市", "city"), ("区域", "district"), ("状态", "sales_status"),
                ("均价元/㎡", "unit_price_cny"), ("价差", "price_delta_abs"), ("面积段", "area_range"),
                ("户型", "room_types"), ("开发商", "developer"),
            ], limit=30),
        ])
    real_n = market.get("sample_count", 0)
    synth_n = market.get("synthetic_count", 0)
    lines.extend([
        "",
        "## 数据来源溯源",
        "| 数据块 | 来源 | 信任级 | 来源/URL |",
        "| --- | --- | --- | --- |",
        f"| 竞品/价格（真实 {real_n} 条） | 安居客新房·本地库 | L2 | https://www.anjuke.com/ |",
        "| 区位配套 POI | 高德地图 Web 服务 | L2 | https://lbs.amap.com/ |",
        f"| 坐标/地理编码 | {loc.get('source', '高德')} | L2 | https://lbs.amap.com/ |",
        f"| 合成历史（{synth_n} 条·仅密度参考） | 本地合成·最近邻克隆 | L3 | 无外部来源 generate_cloned_history.py |",
        "| 政府成交/备案价 | 未接入 | L1 | .env 占位待真实政府域名 |",
        "- 注：合成数据 id/坐标为生成值，不提供外部 URL；真实竞品逐条 URL 见 JSON 的 url_anjuke / url_amap 字段。",
        "",
        "## 数据边界与下一步",
        "- 当前竞品库覆盖本地四城：三亚、杭州、上海、青岛。",
        "- 周边配套来自高德 Web 服务实时 POI。",
        "- 下一步可接入全国楼盘 CSV 或在线抓取任务，将价格带分析从本地 MVP 扩展为真实全国范围。",
    ])
    return "\n".join(lines) + "\n"


def safe_slug(text: str) -> str:
    keep = []
    for ch in text:
        keep.append(ch if ch.isalnum() or ch in "-_" else "_")
    return "".join(keep).strip("_")[:60] or "parcel"


def write_outputs(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_seed = report["input"].get("address") or f"{report['input'].get('city')}_{report['location']['lng']}_{report['location']['lat']}"
    stem = f"{ts}_{safe_slug(name_seed)}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return md_path, json_path


def build_report(args) -> dict:
    year = getattr(args, "year", None)
    if year is None and isinstance(args, dict):
        year = args.get("year")
    
    projects = load_projects(None, year=year)
    if projects.empty:
        raise RuntimeError(f"未读取到本地楼盘数据，年份: {year or '默认'}")
    location = resolve_location(args.city, args.address, args.lng, args.lat, args.key)
    nearby, fallback_used = analyze_nearby(projects, location, args.city, args.district, args.radius_km)
    amenities = analyze_amenities(location, args.key)
    price_band = analyze_price_band(projects, args.expected_price, args.price_band)
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "version": "parcel-report-mvp-1",
            "data_scope": f"Vault/{year or '2026'}年 本地四城样本",
            "target_year": year or "2026",
        },
        "input": {
            "city": args.city,
            "district": args.district,
            "address": args.address,
            "lng": args.lng,
            "lat": args.lat,
            "radius_km": args.radius_km,
            "expected_price": args.expected_price,
            "price_band": args.price_band,
            "year": year,
        },
        "location": location,
        "nearby_fallback_used": fallback_used,
        "nearby_competitors": nearby,
        "market_summary": summarize_market(nearby, include_synthetic=args.include_synthetic),
        "amenities": amenities,
        "price_band_competitors": price_band,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DDS 地块画像报告自动生成")
    parser.add_argument("--city", required=True, help="城市，例如 三亚/杭州")
    parser.add_argument("--district", help="区域，例如 海棠区/滨江区")
    parser.add_argument("--address", help="地块地址；未提供坐标时必填")
    parser.add_argument("--lng", type=float, help="地块经度")
    parser.add_argument("--lat", type=float, help="地块纬度")
    parser.add_argument("--expected-price", type=float, help="预期均价，单位元/㎡")
    parser.add_argument("--radius-km", type=float, default=5.0, help="周边竞品半径，默认 5km")
    parser.add_argument("--price-band", type=float, default=0.15, help="价格带上下浮动比例，默认 0.15")
    parser.add_argument("--year", default="2026", help="目标年份，支持 1995-2026")
    parser.add_argument("--include-synthetic", action="store_true", help="价格统计包含合成历史数据（默认仅真实）")
    parser.add_argument("--out-dir", default=str(ROOT / "data_out" / "reports" / "parcel"))
    parser.add_argument("--key", default=AMAP_KEY, help="高德 Web 服务 Key")
    ALLOWED_CITIES = {"三亚", "杭州", "上海", "青岛"}
    args = parser.parse_args()
    if args.city not in ALLOWED_CITIES:
        print(f"[error] city not supported: {args.city}")
        return 1
    try:
        report = build_report(args)
        md_path, json_path = write_outputs(report, Path(args.out_dir))
    except Exception as e:
        print(f"[error] {e}")
        return 1
    print("[done] report generated")
    print(f"MD: {md_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
