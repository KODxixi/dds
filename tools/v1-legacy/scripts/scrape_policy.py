#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 管道六·政策与金融 — 央行 LPR + 各城市住建局政策数据导入与归一化。

================================================================================
合规声明 COMPLIANCE DECLARATION
================================================================================
本脚本为"框架就绪 + 手动数据导入"模式，不执行任何自动化网页抓取。
数据来源：
  - 央行 LPR：中国人民银行货币政策司 LPR 公告页，由人工整理
  - 各城市政策：各城市住建局官网政策公告栏，由人工整理
严禁未经授权爬取商业平台数据。所有导入数据需附带来源标注（机构名称 + 具体 URL）。

数据来源（示例，以实际导入时标注为准）：
  - 央行 LPR: https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/
    （中国人民银行货币政策司 — 贷款市场报价利率 LPR）
  - 武汉: https://zjw.wuhan.gov.cn/（武汉市住房和城市更新局 — 政策公告）
  - 杭州: https://fgj.hangzhou.gov.cn/（杭州市住房保障和房产管理局 — 政策公告）
  - 三亚: https://zj.sanya.gov.cn/（三亚市住房和城乡建设局 — 政策公告）
  - 更多城市住建局政策公告栏 URL 可在各城市住建局官网找到

输入格式：

  【LPR CSV 格式】:
    LPR 历史数据 CSV，表头：日期,1年期LPR(%),5年期以上LPR(%),来源URL,备注
    示例: 2026-06-20,3.10,3.60,https://www.pbc.gov.cn/...,2026年6月LPR报价

  【政策 JSON 格式】:
    [{"city": "武汉", "policy_type": "限购", "effective_date": "2026-06-01",
      "title": "关于进一步促进房地产市场平稳健康发展的通知",
      "summary": "调整限购区域...", "source_url": "https://zjw.wuhan.gov.cn/...",
      "note": ""}, ...]

  policy_type 可选值: 限购, 限售, 限贷, 首付比例, 贷款利率, 公积金, 契税, 购房补贴,
                    人才引进, 预售监管, 资金监管, 租赁住房, 保障房, 城市更新, 其他

输出：归一化 JSON 到 data_out/policy/ 目录
  - LPR 数据: data_out/policy/lpr_{YYYYMM}.json
  - 政策数据: data_out/policy/{城市}_policy_{YYYYMM}.json

用法:
  python scrape_policy.py --lpr-csv lpr_history.csv --dry-run
  python scrape_policy.py --lpr-csv lpr_history.csv --execute
  python scrape_policy.py --policy-json '[...]' --dry-run
  python scrape_policy.py --policy-json '[...]' --execute
  python scrape_policy.py --template  # 输出 LPR CSV + 政策 JSON 模板
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

# 央行 LPR
LPR_SOURCE = {
    "institution": "中国人民银行货币政策司",
    "url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/",
    "description": "贷款市场报价利率（LPR）官方公告",
}

# 各城市住建局政策公告
POLICY_SOURCES: dict[str, str] = {
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
    "合规警示：本数据由人工从官方公开渠道整理（央行 LPR 公告、各城市住建局政策公告），"
    "严禁未经授权自动化爬取商业平台数据。"
    "所有导入记录必须附带来源 URL 标注。"
)

# ── 政策类型枚举 ────────────────────────────────────────────────────
POLICY_TYPES = [
    "限购", "限售", "限贷", "首付比例", "贷款利率",
    "公积金", "契税", "购房补贴", "人才引进", "预售监管",
    "资金监管", "租赁住房", "保障房", "城市更新", "其他",
]

# ── LPR CSV 列定义 ──────────────────────────────────────────────────
LPR_CSV_COLUMNS = [
    "日期",              # date (YYYY-MM-DD)
    "1年期LPR(%)",       # lpr_1y
    "5年期以上LPR(%)",   # lpr_5y
    "来源URL",           # source_url
    "备注",              # note
]

# ── 政策 JSON 字段定义 ──────────────────────────────────────────────
POLICY_JSON_FIELDS = [
    "city",              # 城市
    "policy_type",       # 政策类型
    "effective_date",    # 生效日期 (YYYY-MM-DD)
    "title",             # 政策标题
    "summary",           # 政策摘要
    "source_url",        # 来源 URL
    "note",              # 备注
]


def print_template() -> None:
    """打印 LPR CSV 和 政策 JSON 导入模板。"""
    print("=" * 80)
    print("LPR CSV 导入模板（UTF-8 with BOM，Excel 兼容）：")
    print("=" * 80)
    print(",".join(LPR_CSV_COLUMNS))
    print("2026-06-20,3.10,3.60,https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/...,2026年6月LPR报价")
    print("2026-05-20,3.10,3.60,https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/...,2026年5月LPR报价")
    print("2026-04-20,3.10,3.60,https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/...,2026年4月LPR报价")
    print()
    print("LPR 字段说明:")
    print("  日期         - LPR 报价日期，格式 YYYY-MM-DD（通常为每月 20 日）")
    print("  1年期LPR(%)  - 数字，1 年期贷款市场报价利率")
    print("  5年期以上LPR(%) - 数字，5 年期以上贷款市场报价利率（房贷基准）")
    print("  来源URL      - 必填，央行 LPR 公告页的具体 URL")
    print("  备注         - 选填")
    print()
    print("=" * 80)
    print("政策 JSON 导入模板：")
    print("=" * 80)
    policy_template = [
        {
            "city": "武汉",
            "policy_type": "限购",
            "effective_date": "2026-06-01",
            "title": "关于进一步促进房地产市场平稳健康发展的通知",
            "summary": "调整限购区域，部分区域取消限购；优化住房信贷政策，首套房首付比例降至20%",
            "source_url": "https://zjw.wuhan.gov.cn/...",
            "note": "手动整理自住建局公告",
        },
        {
            "city": "杭州",
            "policy_type": "首付比例",
            "effective_date": "2026-05-15",
            "title": "关于优化调整房地产市场调控措施的通知",
            "summary": "首套房最低首付比例调整为20%，二套房调整为30%",
            "source_url": "https://fgj.hangzhou.gov.cn/...",
            "note": "手动整理自住建局公告",
        },
    ]
    print(json.dumps(policy_template, ensure_ascii=False, indent=2))
    print()
    print("政策 JSON 字段说明:")
    print("  city           - 城市名称（如 武汉、杭州、三亚）")
    print("  policy_type    - 政策类型，可选值: " + ", ".join(POLICY_TYPES))
    print("  effective_date - 生效日期，格式 YYYY-MM-DD")
    print("  title          - 政策文件标题")
    print("  summary        - 政策摘要（核心条款，50-200 字）")
    print("  source_url     - 必填，住建局政策公告页的具体 URL")
    print("  note           - 选填")


# ═══════════════════════════════════════════════════════════════════════
#  LPR 数据导入
# ═══════════════════════════════════════════════════════════════════════

def parse_lpr_csv(filepath: str) -> list[dict[str, Any]]:
    """解析 LPR CSV 文件，返回 LPR 记录列表。

    CSV 编码自动检测：UTF-8 → UTF-8-BOM → GBK 降级。
    """
    records: list[dict[str, Any]] = []
    content = None

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
    missing_cols = [c for c in LPR_CSV_COLUMNS[:3] if c not in reader.fieldnames]
    if missing_cols:
        raise ValueError(
            f"LPR CSV 缺少必要列: {missing_cols}\n"
            f"  当前列: {reader.fieldnames}\n"
            f"  必要列: {LPR_CSV_COLUMNS[:3]}\n"
            f"  提示: 使用 --template 查看模板"
        )

    for row_num, row in enumerate(reader, start=2):
        if not any(v.strip() for v in row.values()):
            continue

        try:
            record: dict[str, Any] = {
                "date": row.get("日期", "").strip(),
                "lpr_1y": float(row.get("1年期LPR(%)", "0").strip() or 0),
                "lpr_5y": float(row.get("5年期以上LPR(%)", "0").strip() or 0),
                "source_url": row.get("来源URL", "").strip(),
                "note": row.get("备注", "").strip(),
            }
        except (ValueError, TypeError) as exc:
            print(f"[警告] 第 {row_num} 行数据格式错误，跳过: {exc}")
            continue

        if not record["date"]:
            print(f"[警告] 第 {row_num} 行缺少日期，跳过")
            continue

        try:
            datetime.strptime(record["date"], "%Y-%m-%d")
        except ValueError:
            print(f"[警告] 第 {row_num} 行日期格式错误（需 YYYY-MM-DD）: {record['date']}，跳过")
            continue

        if not record["source_url"]:
            print(f"[警告] 第 {row_num} 行缺少来源 URL，跳过")
            continue

        if record["lpr_1y"] <= 0 and record["lpr_5y"] <= 0:
            print(f"[警告] 第 {row_num} 行 LPR 数值无效，跳过")
            continue

        records.append(record)

    return records


def normalize_lpr(record: dict[str, Any]) -> dict[str, Any]:
    """归一化 LPR 记录。"""
    return {
        "date": record["date"],
        "lpr_1y": record["lpr_1y"],
        "lpr_5y": record["lpr_5y"],
        "source_url": record["source_url"],
        "note": record.get("note", "手动整理自央行 LPR 公告"),
        "data_source": {
            "institution": LPR_SOURCE["institution"],
            "url": LPR_SOURCE["url"],
            "category": "LPR 贷款市场报价利率",
            "collection_method": "manual",
            "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "compliance_note": COMPLIANCE_WARNING,
        },
        "pipeline": "管道六·政策与金融",
        "data_type": "lpr",
        "normalized_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


def run_lpr_import(
    records: list[dict[str, Any]],
    output_dir: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """执行 LPR 数据导入与归一化。"""
    if not records:
        return {"status": "empty", "message": "无有效 LPR 记录可导入", "records": 0}

    out_path = Path(output_dir)
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    normalized: list[dict[str, Any]] = []
    for rec in records:
        norm = normalize_lpr(rec)
        normalized.append(norm)

    # 按月分组
    by_month: dict[str, list[dict[str, Any]]] = {}
    for r in normalized:
        month_key = r["date"][:7]  # YYYY-MM
        if month_key not in by_month:
            by_month[month_key] = []
        by_month[month_key].append(r)

    outputs: list[str] = []
    for month_key, month_records in sorted(by_month.items()):
        year_month = month_key.replace("-", "")
        filename = f"lpr_{year_month}.json"
        filepath = out_path / filename

        if dry_run:
            print(f"  [DRY-RUN] 将写入: {filepath}")
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(month_records, f, ensure_ascii=False, indent=2)
            print(f"  [OK] 已写入: {filepath} ({len(month_records)} 条)")
        outputs.append(str(filepath))

    result = {
        "pipeline": "管道六·政策与金融",
        "data_type": "lpr",
        "source": LPR_SOURCE["institution"],
        "source_url": LPR_SOURCE["url"],
        "collection_method": "manual",
        "compliance": COMPLIANCE_WARNING,
        "mode": "dry-run" if dry_run else "execute",
        "total_records": len(records),
        "normalized_records": len(normalized),
        "output_files": outputs,
        "output_dir": str(out_path),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    print(f"\n{'=' * 60}")
    print(f"管道六·LPR 数据导入 {'[DRY-RUN]' if dry_run else '[EXECUTE]'}")
    print(f"  数据来源: {LPR_SOURCE['institution']}")
    print(f"  来源 URL: {LPR_SOURCE['url']}")
    print(f"  合规声明: {COMPLIANCE_WARNING}")
    print(f"  总记录数: {len(records)}")
    print(f"  输出文件: {len(outputs)} 个")
    print(f"  输出目录: {out_path}")
    for f in outputs:
        print(f"    -> {f}")
    print(f"{'=' * 60}")

    return result


# ═══════════════════════════════════════════════════════════════════════
#  政策数据导入
# ═══════════════════════════════════════════════════════════════════════

def parse_policy_json(json_str: str) -> list[dict[str, Any]]:
    """解析政策 JSON 输入字符串，返回政策记录列表。"""
    data = json.loads(json_str)

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError("政策 JSON 输入需为数组或单个对象")

    records: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            print(f"[警告] 第 {i + 1} 项不是对象，跳过")
            continue

        policy_type = str(item.get("policy_type", "")).strip()
        if policy_type and policy_type not in POLICY_TYPES:
            print(f"[警告] 第 {i + 1} 项 policy_type '{policy_type}' 不在已知类型中，"
                  f"将保留但建议使用: {', '.join(POLICY_TYPES)}")

        record = {
            "city": str(item.get("city", "")).strip(),
            "policy_type": policy_type,
            "effective_date": str(item.get("effective_date", "")).strip(),
            "title": str(item.get("title", "")).strip(),
            "summary": str(item.get("summary", "")).strip(),
            "source_url": str(item.get("source_url", "")).strip(),
            "note": str(item.get("note", "")).strip(),
        }

        if not record["city"] or not record["effective_date"] or not record["title"]:
            print(f"[警告] 第 {i + 1} 项缺少必填字段（city/effective_date/title），跳过")
            continue

        if not record["source_url"]:
            print(f"[警告] 第 {i + 1} 项缺少 source_url，跳过")
            continue

        try:
            datetime.strptime(record["effective_date"], "%Y-%m-%d")
        except ValueError:
            print(f"[警告] 第 {i + 1} 项 effective_date 格式错误: {record['effective_date']}，跳过")
            continue

        records.append(record)

    return records


def normalize_policy(record: dict[str, Any]) -> dict[str, Any]:
    """归一化政策记录。"""
    city = record["city"]
    city_source = POLICY_SOURCES.get(city, f"{city}住建局官网")

    return {
        "city": city,
        "policy_type": record["policy_type"],
        "effective_date": record["effective_date"],
        "title": record["title"],
        "summary": record["summary"],
        "source_url": record["source_url"],
        "note": record.get("note", "手动整理自住建局政策公告"),
        "data_source": {
            "institution": city_source,
            "category": "城市房地产政策",
            "collection_method": "manual",
            "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "compliance_note": COMPLIANCE_WARNING,
        },
        "pipeline": "管道六·政策与金融",
        "data_type": "policy",
        "normalized_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


def run_policy_import(
    records: list[dict[str, Any]],
    output_dir: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """执行政策数据导入与归一化。"""
    if not records:
        return {"status": "empty", "message": "无有效政策记录可导入", "records": 0}

    out_path = Path(output_dir)
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    normalized: list[dict[str, Any]] = []
    by_city_type: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        norm = normalize_policy(rec)
        normalized.append(norm)
        city = norm["city"]
        if city not in by_city_type:
            by_city_type[city] = []
        by_city_type[city].append(norm)

    outputs: list[str] = []
    now_ym = datetime.now().strftime("%Y%m")
    for city, city_records in sorted(by_city_type.items()):
        filename = f"{city}_policy_{now_ym}.json"
        filepath = out_path / filename

        if dry_run:
            print(f"  [DRY-RUN] 将写入: {filepath}")
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(city_records, f, ensure_ascii=False, indent=2)
            print(f"  [OK] 已写入: {filepath} ({len(city_records)} 条)")
        outputs.append(str(filepath))

    # 城市与政策类型统计
    city_stats = {c: len(rs) for c, rs in sorted(by_city_type.items())}
    type_stats: dict[str, int] = {}
    for r in normalized:
        pt = r["policy_type"]
        type_stats[pt] = type_stats.get(pt, 0) + 1

    result = {
        "pipeline": "管道六·政策与金融",
        "data_type": "policy",
        "source": "各城市住建局政策公告",
        "source_urls": {
            "央行 LPR": LPR_SOURCE["url"],
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
        "by_policy_type": type_stats,
        "output_files": outputs,
        "output_dir": str(out_path),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    print(f"\n{'=' * 60}")
    print(f"管道六·政策数据导入 {'[DRY-RUN]' if dry_run else '[EXECUTE]'}")
    print(f"  数据来源: 各城市住建局政策公告（手动整理）")
    print(f"  合规声明: {COMPLIANCE_WARNING}")
    print(f"  总记录数: {len(records)}")
    print(f"  归一化:   {len(normalized)} 条")
    print(f"  城市分布: {city_stats}")
    print(f"  政策类型: {type_stats}")
    print(f"  输出文件: {len(outputs)} 个")
    print(f"  输出目录: {out_path}")
    for f in outputs:
        print(f"    -> {f}")
    print(f"{'=' * 60}")

    return result


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS 管道六·政策与金融 — 央行 LPR + 各城市住建局政策数据导入与归一化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
合规声明:
  {COMPLIANCE_WARNING}

数据来源:
  央行 LPR: https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/
            （中国人民银行货币政策司 — 贷款市场报价利率 LPR）
  武汉:     https://zjw.wuhan.gov.cn/（武汉市住房和城市更新局 — 政策公告）
  杭州:     https://fgj.hangzhou.gov.cn/（杭州市住房保障和房产管理局 — 政策公告）
  三亚:     https://zj.sanya.gov.cn/（三亚市住房和城乡建设局 — 政策公告）
  更多城市: 各城市住建局官网政策公告栏

示例:
  python scrape_policy.py --template
  python scrape_policy.py --lpr-csv lpr_history.csv --dry-run
  python scrape_policy.py --lpr-csv lpr_history.csv --execute
  python scrape_policy.py --policy-json '[{{"city":"武汉","policy_type":"限购","effective_date":"2026-06-01","title":"关于进一步促进房地产市场平稳健康发展的通知","summary":"调整限购区域...","source_url":"https://zjw.wuhan.gov.cn/..."}}]' --dry-run
  python scrape_policy.py --policy-json '[{{...}}]' --execute
        """,
    )
    parser.add_argument("--template", action="store_true",
                        help="输出 LPR CSV + 政策 JSON 导入模板")
    parser.add_argument("--lpr-csv", type=str, help="从 LPR 历史 CSV 文件导入")
    parser.add_argument("--policy-json", type=str, help="从政策 JSON 字符串导入")
    parser.add_argument("--output", type=str,
                        default=str(PROJECT_ROOT / "data_out" / "policy"),
                        help="输出目录（默认 data_out/policy/）")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="模拟运行，不实际写入文件（默认）")
    parser.add_argument("--execute", action="store_true",
                        help="真正执行，写入文件")
    args = parser.parse_args()

    if args.template:
        print_template()
        return 0

    if not args.lpr_csv and not args.policy_json:
        print("错误: 请指定 --lpr-csv 或 --policy-json 参数。使用 --help 查看帮助。")
        print("\n提示: 使用 --template 查看导入模板。")
        return 1

    dry_run = not args.execute
    exit_code = 0

    if args.lpr_csv:
        if not os.path.isfile(args.lpr_csv):
            print(f"错误: LPR CSV 文件不存在: {args.lpr_csv}")
            return 1
        print(f"读取 LPR CSV: {args.lpr_csv}")
        lpr_records = parse_lpr_csv(args.lpr_csv)
        if lpr_records:
            lpr_result = run_lpr_import(lpr_records, args.output, dry_run=dry_run)
            if lpr_result["total_records"] == 0:
                exit_code = 1
        else:
            print("警告: 未解析到任何有效 LPR 记录。")
            exit_code = 1

    if args.policy_json:
        policy_records = parse_policy_json(args.policy_json)
        if policy_records:
            policy_result = run_policy_import(policy_records, args.output, dry_run=dry_run)
            if policy_result["total_records"] == 0:
                exit_code = 1
        else:
            print("警告: 未解析到任何有效政策记录。")
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())