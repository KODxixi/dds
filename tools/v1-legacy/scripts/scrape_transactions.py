#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 管道一·网签成交 — 各城市住建局网签成交数据导入与归一化。

================================================================================
合规声明 COMPLIANCE DECLARATION
================================================================================
本脚本为"框架就绪 + 手动数据导入"模式，不执行任何自动化网页抓取。
数据来源：各城市住房和城乡建设局（住建局）官网公示的网签成交数据，由人工整理。
严禁未经授权爬取商业平台（如贝壳、链家、安居客等）的成交数据。
所有导入数据需附带来源标注（机构名称 + 具体 URL），缺来源标注的数据将被拒绝。

数据来源（示例，以实际导入时标注为准）：
  - 武汉: https://zjw.wuhan.gov.cn/（武汉市住房和城市更新局）
  - 杭州: https://fgj.hangzhou.gov.cn/（杭州市住房保障和房产管理局）
  - 三亚: https://zj.sanya.gov.cn/（三亚市住房和城乡建设局）
  - 更多城市住建局官网可在各城市政府网站"住房和城乡建设"栏目找到

输入格式：
  CSV: 城市,日期,楼盘名称,成交套数,成交均价,成交面积,来源URL,备注
  JSON: [{"city": "武汉", "date": "2026-07-01", "project": "某某楼盘",
          "units_sold": 10, "avg_price": 15000, "area_sqm": 1200,
          "source_url": "https://zjw.wuhan.gov.cn/...", "note": ""}, ...]

输出：归一化 JSON 到 data_out/transactions/{城市}_{日期}.json

用法:
  python scrape_transactions.py --csv 武汉成交202607.csv --dry-run
  python scrape_transactions.py --csv 武汉成交202607.csv --execute
  python scrape_transactions.py --json '[...]' --dry-run
  python scrape_transactions.py --json '[...]' --execute
  python scrape_transactions.py --template  # 输出 CSV 模板
================================================================================
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── 项目根目录 ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ── 数据来源标注（机构名称 + 具体 URL）───────────────────────────────
KNOWN_SOURCES: dict[str, str] = {
    "武汉": "武汉市住房和城市更新局 https://zjw.wuhan.gov.cn/",
    "杭州": "杭州市住房保障和房产管理局 https://fgj.hangzhou.gov.cn/",
    "三亚": "三亚市住房和城乡建设局 https://zj.sanya.gov.cn/",
    "北京": "北京市住房和城乡建设委员会 https://zjw.beijing.gov.cn/",
    "上海": "上海市住房和城乡建设管理委员会 https://zjw.sh.gov.cn/",
    "广州": "广州市住房和城乡建设局 https://zjw.gz.gov.cn/",
    "深圳": "深圳市住房和建设局 https://zjj.sz.gov.cn/",
    "成都": "成都市住房和城乡建设局 https://cdzj.chengdu.gov.cn/",
    "南京": "南京市住房保障和房产局 https://fcj.nanjing.gov.cn/",
    "重庆": "重庆市住房和城乡建设委员会 https://zfcxjw.cq.gov.cn/",
    "苏州": "苏州市住房和城乡建设局 https://zfc.suzhou.gov.cn/",
    "西安": "西安市住房和城乡建设局 https://zjj.xa.gov.cn/",
    "长沙": "长沙市住房和城乡建设局 https://szjw.changsha.gov.cn/",
    "郑州": "郑州市住房保障和房地产管理局 https://fgj.zhengzhou.gov.cn/",
    "济南": "济南市住房和城乡建设局 https://jncc.jinan.gov.cn/",
}

# ── 合规警示文本 ────────────────────────────────────────────────────
COMPLIANCE_WARNING = (
    "合规警示：本数据由人工从各城市住建局官网公示信息整理，"
    "严禁未经授权自动化爬取商业平台（如贝壳、链家、安居客等）的成交数据。"
    "所有导入记录必须附带来源 URL 标注。"
)

# ── 标准 CSV 列定义 ─────────────────────────────────────────────────
CSV_COLUMNS = [
    "城市",       # city
    "日期",       # date (YYYY-MM-DD)
    "楼盘名称",   # project
    "成交套数",   # units_sold
    "成交均价",   # avg_price (元/㎡)
    "成交面积",   # area_sqm (㎡)
    "来源URL",    # source_url
    "备注",       # note
]

# 输出归一化字段映射
OUTPUT_FIELDS = {
    "city": "城市",
    "date": "日期",
    "project": "楼盘名称",
    "units_sold": "成交套数",
    "avg_price": "成交均价",
    "area_sqm": "成交面积",
    "source_url": "来源URL",
    "note": "备注",
}


def print_template() -> None:
    """打印 CSV 导入模板（含表头 + 示例行）。"""
    print("CSV 导入模板（UTF-8 with BOM，Excel 兼容）：")
    print("=" * 80)
    print(",".join(CSV_COLUMNS))
    print("武汉,2026-07-01,某某花园,12,18500,1200,https://zjw.wuhan.gov.cn/...,手动整理自住建局公示")
    print("杭州,2026-07-01,某某府,8,32000,960,https://fgj.hangzhou.gov.cn/...,手动整理自住建局公示")
    print("三亚,2026-07-01,某某湾,5,28000,600,https://zj.sanya.gov.cn/...,手动整理自住建局公示")
    print()
    print("字段说明:")
    print("  城市     - 城市名称（如 武汉、杭州、三亚）")
    print("  日期     - 成交日期，格式 YYYY-MM-DD")
    print("  楼盘名称 - 楼盘/项目名称")
    print("  成交套数 - 整数，当日/当期成交套数")
    print("  成交均价 - 数字，单位元/㎡（不含'元/㎡'后缀）")
    print("  成交面积 - 数字，单位㎡")
    print("  来源URL  - 必填，住建局官网公示页面的具体 URL")
    print("  备注     - 选填，数据来源说明")


def parse_csv(filepath: str) -> list[dict[str, Any]]:
    """解析 CSV 文件，返回记录列表。

    CSV 编码自动检测：UTF-8 → UTF-8-BOM → GBK 降级。
    """
    records: list[dict[str, Any]] = []
    content = None

    # 尝试多种编码
    for encoding in ["utf-8-sig", "utf-8", "gbk", "gb2312"]:
        try:
            with open(filepath, "r", encoding=encoding) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if content is None:
        raise ValueError(f"无法读取 CSV 文件 {filepath}，请确认文件编码为 UTF-8 或 GBK")

    reader = csv.DictReader(content.splitlines())
    if reader.fieldnames is None:
        raise ValueError(f"CSV 文件 {filepath} 无表头行")

    # 检查必要列
    missing_cols = [c for c in CSV_COLUMNS[:6] if c not in reader.fieldnames]
    if missing_cols:
        raise ValueError(
            f"CSV 缺少必要列: {missing_cols}\n"
            f"  当前列: {reader.fieldnames}\n"
            f"  必要列: {CSV_COLUMNS[:6]}\n"
            f"  提示: 使用 --template 查看模板"
        )

    for row_num, row in enumerate(reader, start=2):
        # 跳过空行
        if not any(v.strip() for v in row.values()):
            continue

        try:
            record: dict[str, Any] = {
                "city": row.get("城市", "").strip(),
                "date": row.get("日期", "").strip(),
                "project": row.get("楼盘名称", "").strip(),
                "units_sold": int(row.get("成交套数", "0").strip() or 0),
                "avg_price": float(row.get("成交均价", "0").strip() or 0),
                "area_sqm": float(row.get("成交面积", "0").strip() or 0),
                "source_url": row.get("来源URL", "").strip(),
                "note": row.get("备注", "").strip(),
            }
        except (ValueError, TypeError) as exc:
            print(f"[警告] 第 {row_num} 行数据格式错误，跳过: {exc}")
            continue

        # 验证必填字段
        if not record["city"] or not record["date"] or not record["project"]:
            print(f"[警告] 第 {row_num} 行缺少必填字段（城市/日期/楼盘名称），跳过")
            continue

        # 验证日期格式
        try:
            datetime.strptime(record["date"], "%Y-%m-%d")
        except ValueError:
            print(f"[警告] 第 {row_num} 行日期格式错误（需 YYYY-MM-DD）: {record['date']}，跳过")
            continue

        # 验证来源 URL
        if not record["source_url"]:
            print(f"[警告] 第 {row_num} 行缺少来源 URL，跳过")
            continue

        records.append(record)

    return records


def parse_json_input(json_str: str) -> list[dict[str, Any]]:
    """解析 JSON 输入字符串，返回记录列表。

    JSON 格式：[{...}, {...}] 或单个 {...}
    """
    data = json.loads(json_str)

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError("JSON 输入需为数组或单个对象")

    records: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            print(f"[警告] 第 {i + 1} 项不是对象，跳过")
            continue

        record = {
            "city": str(item.get("city", "")).strip(),
            "date": str(item.get("date", "")).strip(),
            "project": str(item.get("project", "")).strip(),
            "units_sold": int(item.get("units_sold", 0)),
            "avg_price": float(item.get("avg_price", 0)),
            "area_sqm": float(item.get("area_sqm", 0)),
            "source_url": str(item.get("source_url", "")).strip(),
            "note": str(item.get("note", "")).strip(),
        }

        if not record["city"] or not record["date"] or not record["project"]:
            print(f"[警告] 第 {i + 1} 项缺少必填字段（city/date/project），跳过")
            continue

        if not record["source_url"]:
            print(f"[警告] 第 {i + 1} 项缺少 source_url，跳过")
            continue

        try:
            datetime.strptime(record["date"], "%Y-%m-%d")
        except ValueError:
            print(f"[警告] 第 {i + 1} 项日期格式错误: {record['date']}，跳过")
            continue

        records.append(record)

    return records


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """将导入记录归一化为标准输出格式。"""
    city = record["city"]
    city_source = KNOWN_SOURCES.get(city, f"{city}住建局官网")

    return {
        "city": city,
        "date": record["date"],
        "project": record["project"],
        "units_sold": record["units_sold"],
        "avg_price": record["avg_price"],
        "area_sqm": record["area_sqm"],
        "source_url": record["source_url"],
        "note": record.get("note", "手动整理自住建局公示"),
        "data_source": {
            "institution": city_source,
            "category": "网签成交",
            "collection_method": "manual",
            "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "compliance_note": COMPLIANCE_WARNING,
        },
        "pipeline": "管道一·网签成交",
        "normalized_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


def run_import(
    records: list[dict[str, Any]],
    output_dir: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """执行数据导入与归一化。

    Args:
        records: 解析后的记录列表
        output_dir: 输出目录路径
        dry_run: 仅模拟，不实际写入文件

    Returns:
        执行结果摘要
    """
    if not records:
        return {"status": "empty", "message": "无有效记录可导入", "records": 0}

    out_path = Path(output_dir)
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    normalized: list[dict[str, Any]] = []
    by_city: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        norm = normalize_record(rec)
        normalized.append(norm)
        city = norm["city"]
        if city not in by_city:
            by_city[city] = []
        by_city[city].append(norm)

    # 按城市+日期分组输出文件
    outputs: list[str] = []
    for city, city_records in by_city.items():
        # 按日期分组
        by_date: dict[str, list[dict[str, Any]]] = {}
        for r in city_records:
            date_key = r["date"]
            if date_key not in by_date:
                by_date[date_key] = []
            by_date[date_key].append(r)

        for date_key, date_records in by_date.items():
            filename = f"{city}_{date_key}.json"
            filepath = out_path / filename

            if dry_run:
                print(f"  [DRY-RUN] 将写入: {filepath}")
            else:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(date_records, f, ensure_ascii=False, indent=2)
                print(f"  [OK] 已写入: {filepath} ({len(date_records)} 条)")
            outputs.append(str(filepath))

    # 城市汇总统计
    city_stats = {c: len(rs) for c, rs in by_city.items()}

    result = {
        "pipeline": "管道一·网签成交",
        "source": "各城市住建局网签公示",
        "source_urls": {
            "武汉": "https://zjw.wuhan.gov.cn/",
            "杭州": "https://fgj.hangzhou.gov.cn/",
            "三亚": "https://zj.sanya.gov.cn/",
        },
        "collection_method": "manual",
        "compliance": COMPLIANCE_WARNING,
        "mode": "dry-run" if dry_run else "execute",
        "total_records": len(records),
        "normalized_records": len(normalized),
        "by_city": city_stats,
        "output_files": outputs,
        "output_dir": str(out_path),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    print(f"\n{'=' * 60}")
    print(f"管道一·网签成交 导入{'[DRY-RUN]' if dry_run else '[EXECUTE]'}")
    print(f"  数据来源: 各城市住建局网签公示（手动整理）")
    print(f"  合规声明: {COMPLIANCE_WARNING}")
    print(f"  总记录数: {len(records)}")
    print(f"  归一化:   {len(normalized)} 条")
    print(f"  城市分布: {city_stats}")
    print(f"  输出文件: {len(outputs)} 个")
    print(f"  输出目录: {out_path}")
    for f in outputs:
        print(f"    -> {f}")
    print(f"{'=' * 60}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS 管道一·网签成交 — 各城市住建局网签成交数据导入与归一化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
合规声明:
  {COMPLIANCE_WARNING}

数据来源:
  武汉: https://zjw.wuhan.gov.cn/（武汉市住房和城市更新局）
  杭州: https://fgj.hangzhou.gov.cn/（杭州市住房保障和房产管理局）
  三亚: https://zj.sanya.gov.cn/（三亚市住房和城乡建设局）
  更多城市: 各城市住建局官网

示例:
  python scrape_transactions.py --template
  python scrape_transactions.py --csv 武汉成交202607.csv --dry-run
  python scrape_transactions.py --csv 武汉成交202607.csv --execute
  python scrape_transactions.py --json '[{{"city":"武汉","date":"2026-07-01","project":"某某花园","units_sold":12,"avg_price":18500,"area_sqm":1200,"source_url":"https://zjw.wuhan.gov.cn/..."}}]' --dry-run
        """,
    )
    parser.add_argument("--template", action="store_true", help="输出 CSV 导入模板")
    parser.add_argument("--csv", type=str, help="从 CSV 文件导入")
    parser.add_argument("--json", type=str, help="从 JSON 字符串导入")
    parser.add_argument("--output", type=str,
                        default=str(PROJECT_ROOT / "data_out" / "transactions"),
                        help="输出目录（默认 data_out/transactions/）")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="模拟运行，不实际写入文件（默认）")
    parser.add_argument("--execute", action="store_true",
                        help="真正执行，写入文件")
    args = parser.parse_args()

    # 打印模板
    if args.template:
        print_template()
        return 0

    # 确定数据源
    records: list[dict[str, Any]] = []
    if args.csv:
        if not os.path.isfile(args.csv):
            print(f"错误: CSV 文件不存在: {args.csv}")
            return 1
        print(f"读取 CSV: {args.csv}")
        records = parse_csv(args.csv)
    elif args.json:
        records = parse_json_input(args.json)
    else:
        print("错误: 请指定 --csv 或 --json 参数。使用 --help 查看帮助。")
        print("\n提示: 使用 --template 查看 CSV 导入模板。")
        return 1

    if not records:
        print("警告: 未解析到任何有效记录。")
        return 1

    # 执行导入
    dry_run = not args.execute
    result = run_import(records, args.output, dry_run=dry_run)

    return 0 if result["total_records"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())