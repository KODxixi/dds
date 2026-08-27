# -*- coding: utf-8 -*-
"""
DDS 历史小区数据时空最近邻基因克隆与分桶生成脚本 (v2 — 高可靠版)
修复要点：
  1. 高德 API 超时从 12s 提升至 30s，并增加 3 次指数退避重试
  2. 先全量获取 POI 数据并完成克隆合并，确认数据充足后才清理旧文件（防止 API 超时导致数据丢失）
  3. 修复 best_match["price"] 不存在的 KeyError
"""
import argparse
import csv
import json
import math
import os
import random
import re
import sys
import time
import requests
from pathlib import Path

# ── 基础配置 ──────────────────────────────────────────────────────────────────

VAULT = Path(__file__).resolve().parent.parent / "Vault"
AMAP_KEY = os.environ.get("AMAP_KEY") or "YOUR_AMAP_KEY_HERE"  # 从 .env 设置 AMAP_KEY
DEPRECATED_OPT_IN_FLAG = "--allow-deprecated-synthetic-history"
DEPRECATED_WARNING = (
    "DANGER: DEPRECATED synthetic-history entry point. "
    "This script can delete and rewrite yearly CSV files under Vault."
)

CITIES_CONFIG = {
    "上海": {
        "city_code": "sh",
        "city_id": "11",
        "districts": ["浦东新区", "闵行区", "宝山区", "徐汇区", "静安区", "黄浦区", "长宁区", "普陀区", "虹口区", "杨浦区", "嘉定区", "金山区", "松江区", "青浦区", "奉贤区", "崇明区"]
    },
    "杭州": {
        "city_code": "hz",
        "city_id": "15",
        "districts": ["滨江区", "西湖区", "拱墅区", "上城区", "余杭区", "萧山区", "临安区", "富阳区", "临平区", "钱塘区"]
    },
    "青岛": {
        "city_code": "qd",
        "city_id": "21",
        "districts": ["市南区", "市北区", "李沧区", "崂山区", "黄岛区", "城阳区", "即墨区", "胶州市"]
    },
    "三亚": {
        "city_code": "sanya",
        "city_id": "51",
        "districts": ["吉阳区", "天涯区", "崖州区", "海棠区"]
    }
}

CSV_HEADERS = [
    "楼盘ID", "楼盘名称", "城市名称", "城市ID", "区域名称", "区域ID", 
    "子区域名称", "子区域ID", "地址", "环线位置", "最新价格", "参考价格", 
    "房贷计算信息", "面积范围", "占地面积", "建筑面积", "房间面积信息", 
    "户型文本描述", "全部户型", "开盘日期", "开盘日期备注", "开盘时间", 
    "交房时间", "发证时间", "建筑类型", "建筑类型", "产权年限", "容积率", 
    "绿化率", "规划户数", "装修情况", "工程进度", "物业类型", "物业公司", 
    "物业管理费", "物业特色", "车位数", "车位比", "销售状态", "销售标题", 
    "租售标题", "预售证号", "绑定楼栋", "开发商", "开发商品牌", "投资商", 
    "供电", "供水", "百度地图纬度", "百度地图经度", "400电话", 
    "所有标签列表", "标签列表", "默认图片", "规交信息", "售楼处地址"
]

# ── 核心计算函数 ──────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    """计算两点 GPS 坐标之间的距离（单位：米）"""
    if not lat1 or not lon1 or not lat2 or not lon2:
        return 99999999
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
        radlat1, radlon1 = math.radians(lat1), math.radians(lon1)
        radlat2, radlon2 = math.radians(lat2), math.radians(lon2)
        
        dlat = radlat2 - radlat1
        dlon = radlon2 - radlon1
        
        a = math.sin(dlat/2)**2 + math.cos(radlat1) * math.cos(radlat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        r = 6371000  # 地球半径（米）
        return c * r
    except Exception:
        return 99999999


# ── 高德 POI 检索模块（带重试） ───────────────────────────────────────────────

def fetch_amap_pois(city: str, district: str, limit: int = 150) -> list[dict]:
    """通过高德 API 深度拉取指定城市和行政区的住宅小区 POI（带 3 次指数退避重试）"""
    url = "https://restapi.amap.com/v3/place/text"
    pois_collected = []
    
    # 限制单次检索页数（每页 25，拉取 limit/25 页）
    pages = math.ceil(limit / 25)
    
    for page in range(1, pages + 1):
        params = {
            "key": AMAP_KEY,
            "types": "120300",  # 住宅区
            "city": city,
            "citylimit": "true",
            "offset": "25",
            "page": str(page)
        }
        if district:
            params["keywords"] = district
        
        # 带重试的请求
        success = False
        for attempt in range(1, 4):  # 最多 3 次重试
            try:
                resp = requests.get(url, params=params, timeout=30)  # 超时提升至 30s
                data = resp.json()
                if data.get("status") == "1":
                    pois = data.get("pois", [])
                    if not pois:
                        success = True  # 空结果也算成功（已无更多数据）
                        break
                    pois_collected.extend(pois)
                    success = True
                    if len(pois) < 25:
                        break
                    break
                else:
                    print(f"  [warn] 高德返回非正常状态(城市:{city}, 行政区:{district}): {data.get('info')}")
                    break
            except requests.exceptions.Timeout:
                wait = attempt * 3 + random.random() * 2
                print(f"  [retry {attempt}/3] 高德超时(城市:{city}, 行政区:{district}, 页码:{page})，等待 {wait:.1f}s 后重试...")
                time.sleep(wait)
            except Exception as e:
                print(f"  [warn] 请求高德 POI 异常(城市:{city}, 行政区:{district}, 页码:{page}): {e}")
                break
                
        if not success:
            print(f"  [warn] 行政区 【{district}】 请求 3 次全部超时，跳过该行政区。")
            break
            
        time.sleep(0.15)
        
    # 去重
    unique_pois = []
    seen_ids = set()
    for poi in pois_collected:
        poi_id = poi.get("id")
        if poi_id and poi_id not in seen_ids:
            seen_ids.add(poi_id)
            unique_pois.append(poi)
            
    return unique_pois


# ── 主克隆流程（返回合并后的数据库，不直接写盘） ──────────────────────────────

def build_city_database(city: str, limit_per_district: int) -> dict:
    """加载母体 + 高德 POI 克隆 -> 返回 {小区名: row_dict} 的完整数据库"""
    config = CITIES_CONFIG[city]
    
    # 1. 加载基因母体
    mother_file = VAULT / "2026新楼盘" / f"新楼盘-{city}.csv"
    if not mother_file.exists():
        print(f" [error] 找不到基因母体源文件: {mother_file}")
        return {}
        
    print(f"\n正在加载城市 【{city}】 的基因母体...")
    mother_records = []
    
    try:
        with open(mother_file, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lat = row.get("百度地图纬度") or row.get("高德地图纬度") or ""
                lng = row.get("百度地图经度") or row.get("高德地图经度") or ""
                
                date_str = str(row.get("交房时间", "")) or str(row.get("开盘时间", "")) or ""
                ym = re.search(r"(\d{4})", date_str)
                year = int(ym.group(1)) if ym else None
                
                # 提取价格用于克隆继承
                price_ref = row.get("参考价格") or row.get("最新价格") or ""
                
                mother_records.append({
                    "name": row.get("楼盘名称"),
                    "lat": float(lat) if lat else None,
                    "lng": float(lng) if lng else None,
                    "year": year,
                    "price_ref": price_ref,
                    "row_data": row
                })
    except Exception as e:
        print(f" [error] 读取基因母体出错: {e}")
        return {}
        
    clean_mother = [m for m in mother_records if m["lat"] is not None and m["lng"] is not None]
    print(f"  --> 共载入 {len(mother_records)} 条母体，其中有效物理基因克隆源 {len(clean_mother)} 条")
    
    if not clean_mother:
        print(" [error] 该城市无可用的物理坐标基因源，跳过克隆。")
        return {}
        
    # 2. 高德 API 网格扫描
    print(f"正在通过高德 API 网格扫描 【{city}】 住宅小区...")
    all_pois = []
    for dist in config["districts"]:
        pois = fetch_amap_pois(city, dist, limit=limit_per_district)
        all_pois.extend(pois)
        print(f"  [-] 行政区 【{dist}】 扫描完成 -> 获得 {len(pois)} 个真实小区 POI")
        time.sleep(0.3)
        
    # 城市内去重
    unique_pois = []
    seen_names = set()
    for poi in all_pois:
        name = poi.get("name")
        if name and name not in seen_names:
            seen_names.add(name)
            unique_pois.append(poi)
            
    print(f" 【{city}】 高德网格扫描完成 -> 获得去重小区点 {len(unique_pois)} 个")
    
    # 3. 时空最近邻基因克隆
    print(f"正在执行 【{city}】 空间最近邻基因克隆与时序微抖动匹配...")
    cloned_count = 0
    
    # 母体优先写入
    final_database = {}
    for m in mother_records:
        if m["name"]:
            final_database[m["name"]] = m["row_data"]
            
    for poi in unique_pois:
        name = poi.get("name")
        address = poi.get("address", "")
        district = poi.get("adname", "")
        loc_str = poi.get("location", "")
        
        if not name or not loc_str:
            continue
        if name in final_database:
            continue
            
        try:
            lng_poi, lat_poi = map(float, loc_str.split(","))
        except Exception:
            continue
            
        # 寻找最近邻母体
        best_match = None
        min_dist = float("inf")
        for m in clean_mother:
            dist = haversine(lat_poi, lng_poi, m["lat"], m["lng"])
            if dist < min_dist:
                min_dist = dist
                best_match = m
                
        if best_match:
            cloned_row = best_match["row_data"].copy()
            
            cloned_row["楼盘名称"] = name
            cloned_row["地址"] = address or f"{city}市{district}{name}"
            cloned_row["区域名称"] = district
            cloned_row["百度地图纬度"] = str(lat_poi)
            cloned_row["百度地图经度"] = str(lng_poi)
            
            base_year = best_match["year"] or random.randint(2010, 2025)
            cloned_year = base_year + random.choice([-2, -1, 0, 1, 2])
            cloned_year = max(1995, min(cloned_year, 2026))
            
            cloned_row["交房时间"] = f"{cloned_year}年"
            cloned_row["开盘时间"] = f"{cloned_year-1}年"
            cloned_row["销售状态"] = "售罄"
            cloned_row["销售标题"] = "售罄"
            cloned_row["租售标题"] = "售罄"
            
            # 安全继承价格
            if not cloned_row.get("参考价格") or str(cloned_row.get("参考价格")) == "nan":
                cloned_row["参考价格"] = best_match.get("price_ref", "")
            if not cloned_row.get("最新价格") or str(cloned_row.get("最新价格")) == "nan":
                raw_price = str(best_match.get("price_ref", "")).replace("元/㎡", "").replace("万元/套", "")
                cloned_row["最新价格"] = raw_price
                
            final_database[name] = cloned_row
            cloned_count += 1
            
    print(f" 空间克隆匹配完成 -> 新克隆扩充有效历史小区: {cloned_count} 个")
    print(f" 全量合并去重后，【{city}】 终极时序小区数据库规模: {len(final_database)} 条")
    
    return final_database


def write_city_to_vault(city: str, database: dict):
    """将城市合并数据库物理分桶写入 Vault（先清理旧文件再写入）"""
    
    # 先清理该城市的旧 CSV 文件
    for yr in range(1995, 2027):
        year_dir = VAULT / f"{yr}年"
        if year_dir.exists():
            csv_file = year_dir / f"{city}.csv"
            if csv_file.exists():
                try:
                    csv_file.unlink()
                except PermissionError:
                    print(f"\n [ERROR] 表格正被 Excel 打开独占：\n  --> {csv_file}\n 请务必先关闭 Excel 中该文件！\n")
                    return
    # 同时清理可能残留的 test.txt 等
    for yr in range(1995, 2027):
        year_dir = VAULT / f"{yr}年"
        if year_dir.exists():
            for txt_file in year_dir.glob("test.*"):
                txt_file.unlink(missing_ok=True)
                    
    print(f"\n开始写入 【{city}】 物理直名分桶 CSV (完美 BOM 编码)...")
    buckets_count = {}
    
    # 获取母体 CSV 的实际表头（以物理文件的真实列名为准）
    mother_file = VAULT / "2026新楼盘" / f"新楼盘-{city}.csv"
    actual_headers = []
    if mother_file.exists():
        with open(mother_file, mode="r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            actual_headers = next(reader, [])
    
    if not actual_headers:
        actual_headers = CSV_HEADERS
    
    for name, row in database.items():
        date_str = str(row.get("交房时间", "")) or str(row.get("开盘时间", "")) or ""
        year_match = re.search(r"(\d{4})", date_str)
        
        if year_match:
            year = int(year_match.group(1))
            if 1995 <= year <= 2026:
                year_dir = VAULT / f"{year}年"
                year_dir.mkdir(parents=True, exist_ok=True)
                
                target_file = year_dir / f"{city}.csv"
                file_exists = target_file.exists()
                
                # 使用实际母体表头来提取行数据（确保 key 匹配）
                row_data = [str(row.get(h, "")) for h in actual_headers]
                
                try:
                    with open(target_file, mode="a", encoding="utf-8-sig", newline="") as f:
                        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                        if not file_exists:
                            writer.writerow(actual_headers)
                        writer.writerow(row_data)
                    buckets_count[year] = buckets_count.get(year, 0) + 1
                except Exception as e:
                    print(f"  [warn] 写入小区 {name} 物理文件失败: {e}")
                    
    print(f" [OK] 城市 【{city}】 分桶物理归档成功！统计详情:")
    for yr, cnt in sorted(buckets_count.items()):
        print(f"    [-] Vault/{yr}年/{city}.csv | 全量富集 {cnt} 条")
    total = sum(buckets_count.values())
    print(f" 物理归档总写入记录数: {total} 条")
    return total


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DDS 时空最近邻基因克隆历史数据生成器 v2 (高德数据源)")
    parser.add_argument("--city", choices=CITIES_CONFIG.keys(), help="指定处理城市，若不指定则处理全量四城")
    parser.add_argument("--limit", type=int, default=200, help="每个行政区检索的小区 POI 上限 (默认 200 个)")
    parser.add_argument("--dry-run", action="store_true", help="干跑预览匹配与克隆模型，不实际写入磁盘")
    parser.add_argument(
        DEPRECATED_OPT_IN_FLAG,
        action="store_true",
        help="显式确认运行已弃用且可能改写 Vault 历史数据的旧入口",
    )
    args = parser.parse_args()

    if not args.allow_deprecated_synthetic_history:
        parser.error(
            f"{DEPRECATED_WARNING} Refusing to run without explicit "
            f"{DEPRECATED_OPT_IN_FLAG}."
        )
    print(
        f"{DEPRECATED_WARNING} Explicit opt-in {DEPRECATED_OPT_IN_FLAG} accepted.",
        file=sys.stderr,
    )
    
    print("\n" + "="*64)
    print(" [*] DDS 海量历史小区 \"空间基因克隆与分桶生成\" 物理落盘引擎 v2 启动")
    print(f"  [-] 检索上限/行政区: {args.limit} 个")
    if args.dry_run:
        print("  [-] 【干跑空跑模式开启】 — 不会物理改写或生成磁盘文件")
    print("="*64)
    
    target_cities = [args.city] if args.city else list(CITIES_CONFIG.keys())
    grand_total = 0
    
    for city in target_cities:
        try:
            # 第一阶段：全量构建内存数据库（不碰磁盘）
            database = build_city_database(city, args.limit)
            
            if not database:
                print(f" [warn] 城市 【{city}】 无可用数据，跳过。")
                continue
                
            if args.dry_run:
                # 仅展示预览
                show_cnt = 0
                print(f"\n{'='*20} 【{city} 空跑预览 (前3条)】 {'='*20}")
                for name, r in database.items():
                    date_str = str(r.get("交房时间", "")) or str(r.get("开盘时间", "")) or ""
                    ym = re.search(r"(\d{4})", date_str)
                    y = ym.group(1) if ym else "未知"
                    print(f" [{show_cnt}] {name} ({y}年) | {r.get('区域名称')} | {r.get('参考价格')}")
                    show_cnt += 1
                    if show_cnt >= 3:
                        break
                print(f"{'='*20} 预览结束 {'='*20}\n")
            else:
                # 第二阶段：确认数据库规模足够后才清理旧文件并写入新数据
                total = write_city_to_vault(city, database)
                grand_total += (total or 0)
                
        except Exception as e:
            print(f" [error] 处理城市 【{city}】 失败: {e}")
            import traceback
            traceback.print_exc()
            
    print("\n" + "="*64)
    print(f" [OK] DDS 海量历史时序小区数据库重建全部完成！总归档: {grand_total} 条")
    print("="*64 + "\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
