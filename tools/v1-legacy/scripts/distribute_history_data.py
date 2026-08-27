# -*- coding: utf-8 -*-
"""
DDS 历史楼盘数据本地重映射与归档模块
利用本地已有的 2026 楼盘源数据，将其中的 2010 - 2025 年历史楼盘进行高精无损重分类，
按照规范建立年份直名归档，即：Vault/{年份}年/{城市}.csv。
"""
import argparse
import csv
import os
import re
import sys
from pathlib import Path
import pandas as pd

VAULT = Path(__file__).resolve().parent.parent / "Vault"
DEPRECATED_OPT_IN_FLAG = "--allow-deprecated-synthetic-history"
DEPRECATED_WARNING = (
    "DANGER: DEPRECATED derived-history entry point. "
    "This script can create year directories and append derived records under Vault."
)

# DDS 对齐的 56 个核心字段
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

CITIES = ["三亚", "上海", "杭州", "青岛"]

def main():
    parser = argparse.ArgumentParser(description="DDS 历史数据本地分桶提取归档器")
    parser.add_argument("--dry-run", action="store_true", help="空跑预览模式")
    parser.add_argument(
        DEPRECATED_OPT_IN_FLAG,
        action="store_true",
        help="显式确认运行已弃用且可能追加 Vault 派生历史数据的旧入口",
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
    print(" [*] DDS 历史楼盘本地无损重分类与分桶归档启动")
    if args.dry_run:
        print(" [-] 【空跑模式开启】— 仅进行解析与分桶预览，不实际写入磁盘")
    print("="*64 + "\n")

    total_records = 0
    written_records = 0
    skipped_records = 0
    city_stats = {}

    for city in CITIES:
        source_file = VAULT / "2026新楼盘" / f"新楼盘-{city}.csv"
        if not source_file.exists():
            print(f" [warn] 找不到城市 {city} 的原始数据源 {source_file}，跳过该城市")
            continue

        print(f"正在处理城市: {city} ...")
        
        try:
            # 读取原始 CSV 数据
            df = pd.read_csv(source_file, dtype=str)
            total_records += len(df)
            
            city_buckets = {}
            
            for idx, row in df.iterrows():
                # 1. 兼容获取“交房时间”或“开盘时间”以解析年代年份
                date_str = str(row.get("交房时间", "")) or str(row.get("开盘时间", "")) or ""
                year_match = re.search(r"(\d{4})", date_str)
                
                if not year_match:
                    skipped_records += 1
                    continue
                
                year = int(year_match.group(1))
                
                # 2. 筛选符合 2010 - 2025 区间的历史楼盘
                if not (2010 <= year <= 2025):
                    skipped_records += 1
                    continue
                
                # 3. 准备写入分桶
                year_dir = VAULT / f"{year}年"
                target_file = year_dir / f"{city}.csv"
                
                # 构建 56 字段对齐的单行行数据，保持原汁原味
                row_data = [row.get(h, "") for h in CSV_HEADERS]
                
                if not args.dry_run:
                    # 确保物理目录存在
                    year_dir.mkdir(parents=True, exist_ok=True)
                    file_exists = target_file.exists()
                    
                    with open(target_file, mode="a", encoding="utf-8", newline="") as f:
                        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                        if not file_exists:
                            writer.writerow(CSV_HEADERS)
                        writer.writerow(row_data)
                
                # 统计数据
                city_buckets[year] = city_buckets.get(year, 0) + 1
                written_records += 1
            
            city_stats[city] = city_buckets
            print(f"  [OK] {city} 解析完成！命中历史年代楼盘数: {sum(city_buckets.values())} 条")
            
        except Exception as e:
            print(f" [error] 处理城市 {city} 失败: {e}")

    print("\n" + "="*64)
    print(" [OK] DDS 历史数据归档统计报告")
    print(f"  [-] 原始分析总行数: {total_records} 行")
    print(f"  [-] 历史过滤归档数: {written_records} 行")
    print(f"  [-] 超出范围或空值: {skipped_records} 行")
    print("-" * 64)
    for city, buckets in sorted(city_stats.items()):
        print(f"  城市: {city}")
        for yr, cnt in sorted(buckets.items()):
            print(f"    [-] Vault/{yr}年/{city}.csv | 新增 {cnt} 条")
    print("="*64 + "\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())
