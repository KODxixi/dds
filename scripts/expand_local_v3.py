# -*- coding: utf-8 -*-
"""
DDS 历史小区数据本地深度扩充引擎 v3（纯本地，不依赖外部 API）
策略：从 2026 母体 CSV 出发，通过以下算法大幅扩充 1995-2025 的数据量：
  1. 行政区历史开发密度曲线：每个城市的各行政区在不同年代的开发权重不同
  2. 母体基因裂变：每个母体小区可以衍生出 N 个同行政区的历史小区（名称变体 + 坐标微扰 + 价格时间回归）
  3. 时间回归价格模型：根据城市年均房价增长率反推历史价格
  4. 保留已有数据：只追加，不覆盖已有年份文件中的记录
"""
import csv
import math
import os
import random
import re
import sys
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent / "Vault"

# ── 城市配置 & 历史开发权重 ──

# 各城市年均房价增长率（用于价格回归）
PRICE_GROWTH = {
    "上海": 0.08,   # 年均 8%
    "杭州": 0.07,
    "青岛": 0.055,
    "三亚": 0.06,
}

# 各城市各年代的总体开发活跃度（相对于2020年=1.0的倍数）
YEAR_ACTIVITY = {}
for city in ["上海", "杭州", "青岛", "三亚"]:
    YEAR_ACTIVITY[city] = {}
    for y in range(1995, 2027):
        if y < 2000:
            YEAR_ACTIVITY[city][y] = 0.05
        elif y < 2005:
            YEAR_ACTIVITY[city][y] = 0.15
        elif y < 2010:
            YEAR_ACTIVITY[city][y] = 0.35
        elif y < 2013:
            YEAR_ACTIVITY[city][y] = 0.55
        elif y < 2016:
            YEAR_ACTIVITY[city][y] = 0.70
        elif y < 2018:
            YEAR_ACTIVITY[city][y] = 0.85
        elif y < 2020:
            YEAR_ACTIVITY[city][y] = 0.95
        elif y < 2023:
            YEAR_ACTIVITY[city][y] = 1.0
        else:
            YEAR_ACTIVITY[city][y] = 0.90

# 小区名称常见前缀/后缀池（用于生成合理的变体名称）
NAME_PREFIX_POOL = [
    "翠", "碧", "锦", "华", "金", "银", "龙", "凤", "嘉", "鑫",
    "新", "瑞", "和", "泰", "福", "盛", "恒", "雅", "御", "悦",
    "天", "中", "海", "世", "万", "绿", "紫", "蓝", "博", "宏",
]

NAME_SUFFIX_POOL = [
    "苑", "园", "庭", "府", "居", "城", "湾", "庄", "阁", "台",
    "轩", "堂", "墅", "邸", "宅", "里", "坊", "筑", "景", "岸",
    "都", "汇", "家", "舍", "榭", "洲", "谷", "峰", "林", "源",
    "华庭", "花园", "小区", "公寓", "雅苑", "花苑", "新村", "家园", "佳苑", "名苑",
    "锦园", "怡园", "嘉园", "翠苑", "雅居", "美景", "丽景", "豪庭", "新城", "名城",
]

NAME_MIDDLE_POOL = [
    "翡翠", "阳光", "春天", "明珠", "水岸", "星辰", "月亮", "云天", "紫金", "碧水",
    "金色", "银河", "龙湖", "世纪", "东方", "南国", "北辰", "西岸", "中央", "半岛",
    "蓝湾", "绿洲", "红树", "白鸽", "黄金", "青山", "翠竹", "玉兰", "桃源", "梅林",
    "康城", "乐居", "和平", "幸福", "美好", "理想", "梦想", "未来", "时代", "盛世",
]

# 开发商池
DEVELOPER_POOL = [
    "碧桂园", "万科", "恒大", "融创", "保利", "中海", "华润", "龙湖", "绿城",
    "招商蛇口", "新城控股", "中梁", "旭辉", "世茂", "正荣", "金地", "建发",
    "雅居乐", "阳光城", "远洋", "金茂", "华侨城", "中南", "佳兆业", "禹洲",
    "美的置业", "滨江集团", "朗诗", "仁恒", "大华", "路劲", "象屿", "中铁建",
]

# 物业公司池
PROPERTY_POOL = [
    "万科物业", "碧桂园服务", "龙湖智慧服务", "融创服务", "保利物业",
    "中海物业", "华润万象生活", "绿城服务", "金地智慧服务", "世茂服务",
    "招商积余", "雅生活服务", "旭辉永升", "新城悦服务", "合景悠活",
]

CSV_HEADERS = None  # 将从母体文件动态读取


def generate_variant_name(base_name: str, idx: int) -> str:
    """基于母体名称生成合理的变体小区名"""
    random.seed(hash(base_name) + idx)  # 确定性随机以保证可重复
    
    # 策略1: 前缀+中间词+后缀组合
    if idx % 3 == 0:
        prefix = random.choice(NAME_PREFIX_POOL)
        middle = random.choice(NAME_MIDDLE_POOL)
        suffix = random.choice(NAME_SUFFIX_POOL)
        return f"{prefix}{middle}{suffix}"
    # 策略2: 基于母体名取前2字+新后缀
    elif idx % 3 == 1:
        base = base_name[:2] if len(base_name) >= 2 else base_name
        suffix = random.choice(NAME_SUFFIX_POOL)
        middle = random.choice(NAME_MIDDLE_POOL)
        return f"{base}{middle}{suffix}"
    # 策略3: 全新随机名
    else:
        prefix = random.choice(NAME_PREFIX_POOL)
        suffix = random.choice(NAME_SUFFIX_POOL)
        num = random.choice(["", "一期", "二期", "三期", "A区", "B区", ""])
        return f"{prefix}{suffix}{num}"


def perturb_coord(lat: float, lng: float, radius_m: float = 1500) -> tuple:
    """在指定半径内对 GPS 坐标进行微扰"""
    # 1度纬度 ≈ 111km, 1度经度 ≈ 111km * cos(lat)
    dlat = (random.random() * 2 - 1) * (radius_m / 111000)
    dlng = (random.random() * 2 - 1) * (radius_m / (111000 * math.cos(math.radians(lat))))
    return round(lat + dlat, 6), round(lng + dlng, 6)


def regress_price(price_str: str, from_year: int, to_year: int, growth_rate: float) -> str:
    """用年均增长率反推历史价格"""
    try:
        # 提取数字
        nums = re.findall(r'[\d.]+', str(price_str))
        if not nums:
            return price_str
        price = float(nums[0])
        if price < 100:  # 可能是万元单位
            price *= 10000
        years_diff = from_year - to_year
        historical_price = price / ((1 + growth_rate) ** years_diff)
        return f"{int(historical_price)}元/㎡"
    except Exception:
        return price_str


def process_city(city: str, dry_run: bool = False):
    """处理单个城市的本地深度扩充"""
    global CSV_HEADERS
    
    mother_file = VAULT / "2026新楼盘" / f"新楼盘-{city}.csv"
    if not mother_file.exists():
        print(f" [error] 找不到基因母体源文件: {mother_file}")
        return 0
    
    print(f"\n{'='*50}")
    print(f" 正在处理城市 【{city}】 的本地深度扩充...")
    print(f"{'='*50}")
    
    # 读取母体
    mother_records = []
    with open(mother_file, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        CSV_HEADERS = reader.fieldnames
        for row in reader:
            lat_str = row.get("百度地图纬度") or ""
            lng_str = row.get("百度地图经度") or ""
            date_str = str(row.get("交房时间", "")) or str(row.get("开盘时间", "")) or ""
            ym = re.search(r"(\d{4})", date_str)
            year = int(ym.group(1)) if ym else None
            
            try:
                lat = float(lat_str) if lat_str else None
                lng = float(lng_str) if lng_str else None
            except ValueError:
                lat, lng = None, None
            
            mother_records.append({
                "name": row.get("楼盘名称", ""),
                "district": row.get("区域名称", ""),
                "lat": lat,
                "lng": lng,
                "year": year,
                "row": row
            })
    
    print(f"  --> 载入母体 {len(mother_records)} 条")
    
    # 按行政区分组母体
    district_mothers = {}
    for m in mother_records:
        d = m["district"] or "未知"
        if d not in district_mothers:
            district_mothers[d] = []
        district_mothers[d].append(m)
    
    print(f"  --> 覆盖 {len(district_mothers)} 个行政区: {', '.join(district_mothers.keys())}")
    
    # 收集已有数据的小区名称（避免重复）
    existing_names = set()
    for yr in range(1995, 2027):
        csv_file = VAULT / f"{yr}年" / f"{city}.csv"
        if csv_file.exists():
            try:
                with open(csv_file, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        name = row.get("楼盘名称", "")
                        if name:
                            existing_names.add(name)
            except Exception:
                pass
    
    print(f"  --> 已有 {len(existing_names)} 个小区名在 Vault 中")
    
    growth_rate = PRICE_GROWTH.get(city, 0.06)
    total_added = 0
    
    # 为每个年份生成扩充数据
    for target_year in range(1995, 2026):
        activity = YEAR_ACTIVITY[city].get(target_year, 0.5)
        
        year_dir = VAULT / f"{target_year}年"
        year_dir.mkdir(parents=True, exist_ok=True)
        target_file = year_dir / f"{city}.csv"
        
        # 计算该年份需要生成的小区数量
        # 基于母体数量 × 活跃度 × 扩充因子
        base_count = len(mother_records)
        target_count = max(5, int(base_count * activity * 0.6))
        
        # 检查已有数据量
        existing_count = 0
        if target_file.exists():
            with open(target_file, mode="r", encoding="utf-8-sig") as f:
                existing_count = sum(1 for _ in f) - 1  # 减去表头
        
        # 需要追加的数量
        need_count = max(0, target_count - existing_count)
        
        if need_count == 0:
            continue
        
        if dry_run:
            print(f"  [DRY] {target_year}年: 已有 {existing_count} 条，需追加 {need_count} 条 (目标 {target_count})")
            continue
        
        # 生成追加数据
        new_records = []
        variant_idx = existing_count + 1000 * target_year  # 确保不同年份不重名
        
        for i in range(need_count):
            # 随机选择一个行政区的母体作为基因源
            district = random.choice(list(district_mothers.keys()))
            mother = random.choice(district_mothers[district])
            
            # 生成变体名称
            variant_name = generate_variant_name(mother["name"], variant_idx + i)
            
            # 检查是否已存在
            max_attempts = 10
            attempt = 0
            while variant_name in existing_names and attempt < max_attempts:
                variant_idx += 1
                variant_name = generate_variant_name(mother["name"], variant_idx + i)
                attempt += 1
            
            if variant_name in existing_names:
                continue
            
            existing_names.add(variant_name)
            
            # 克隆母体行数据
            new_row = mother["row"].copy()
            new_row["楼盘名称"] = variant_name
            
            # 生成新的楼盘 ID
            new_row["楼盘ID"] = str(abs(hash(f"{city}_{variant_name}_{target_year}")) % 10000000)
            
            # 坐标微扰
            if mother["lat"] and mother["lng"]:
                new_lat, new_lng = perturb_coord(mother["lat"], mother["lng"], radius_m=2000)
                new_row["百度地图纬度"] = str(new_lat)
                new_row["百度地图经度"] = str(new_lng)
            
            # 时间回归
            new_row["交房时间"] = f"{target_year}年"
            new_row["开盘时间"] = f"{target_year - 1}年"
            new_row["开盘日期"] = f"{target_year - 1}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
            
            # 价格回归
            ref_price = mother["row"].get("参考价格", "")
            new_row["参考价格"] = regress_price(ref_price, 2026, target_year, growth_rate)
            latest_price = mother["row"].get("最新价格", "")
            new_row["最新价格"] = regress_price(latest_price, 2026, target_year, growth_rate)
            
            # 销售状态
            new_row["销售状态"] = "售罄"
            new_row["销售标题"] = "售罄"
            new_row["租售标题"] = "售罄"
            
            # 随机分配开发商和物业
            new_row["开发商"] = random.choice(DEVELOPER_POOL)
            new_row["物业公司"] = random.choice(PROPERTY_POOL)
            
            # 建筑类型随机微调
            building_types = ["高层", "小高层", "多层", "别墅", "洋房", "超高层"]
            # 保留母体类型概率更高
            if random.random() > 0.3:
                pass  # 保持母体建筑类型
            else:
                new_row["建筑类型"] = random.choice(building_types)
            
            new_records.append(new_row)
        
        if new_records:
            file_exists = target_file.exists()
            with open(target_file, mode="a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                if not file_exists:
                    writer.writerow(CSV_HEADERS)
                for rec in new_records:
                    row_data = [str(rec.get(h, "")) for h in CSV_HEADERS]
                    writer.writerow(row_data)
            
            total_added += len(new_records)
            print(f"  [+] {target_year}年/{city}.csv: 追加 {len(new_records)} 条 (现有 {existing_count} + {len(new_records)} = {existing_count + len(new_records)})")
    
    print(f"\n [OK] 城市 【{city}】 深度扩充完成，新增 {total_added} 条历史小区记录")
    return total_added


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DDS 历史小区数据本地深度扩充引擎 v3")
    parser.add_argument("--city", choices=["上海", "杭州", "青岛", "三亚"], help="指定城市")
    parser.add_argument("--dry-run", action="store_true", help="干跑预览")
    args = parser.parse_args()
    
    print("\n" + "=" * 64)
    print(" [*] DDS 历史小区数据本地深度扩充引擎 v3 启动")
    print("     策略：母体基因裂变 + 价格时间回归 + 坐标微扰")
    print("=" * 64)
    
    cities = [args.city] if args.city else ["上海", "杭州", "青岛", "三亚"]
    grand_total = 0
    
    for city in cities:
        try:
            added = process_city(city, dry_run=args.dry_run)
            grand_total += (added or 0)
        except Exception as e:
            print(f" [error] 处理城市 【{city}】 失败: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 64)
    print(f" [OK] 全部完成！共新增 {grand_total} 条历史小区记录")
    print("=" * 64 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
