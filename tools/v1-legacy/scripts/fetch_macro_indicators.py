# -*- coding: utf-8 -*-
"""Automated macro indicator fetcher for DDS.

Fetches and updates:
1. 70-city housing price monthly data (国家统计局)
2. National real estate investment/sales data (国家统计局)
3. LPR / mortgage rate data (央行)
4. Rental yield data (中指研究院)

Usage:
    python scripts/fetch_macro_indicators.py              # full update
    python scripts/fetch_macro_indicators.py --check      # dry-run, report what's new
    python scripts/fetch_macro_indicators.py --schedule    # print cron schedule

Designed to be called by DDS data pipeline (管道六·政策与金融) or cron.
Schedule: monthly, 3 days after NBS publishes (typically 18th of each month).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
MACRO = VAULT / "宏观数据"
TODAY = date.today().isoformat()

# ─── Schema ───────────────────────────────────────────────────────────────────
COLS = [
    "城市", "统计年份", "指标组", "指标", "数值", "单位",
    "同比增长_pct", "占比_pct", "前年差值", "前年差单位",
    "DDS维度类目", "备注", "来源标题", "数据来源", "来源URL",
    "发布日期", "入库日期", "来源摘录位置",
]

# ─── Data source registry ────────────────────────────────────────────────────
NBS_BASE = "https://www.stats.gov.cn/"
PBC_BASE = "https://www.pbc.gov.cn/"
CIH_BASE = "https://www.cih-index.com/"

SOURCES = {
    "NBS_70城": {
        "来源标题_tpl": "{year}年{month}月份70个大中城市商品住宅销售价格变动情况",
        "数据来源": "国家统计局",
        "来源URL": NBS_BASE,
    },
    "NBS_投资销售": {
        "来源标题_tpl": "{year}年1-{month}月份全国房地产开发投资和销售情况",
        "数据来源": "国家统计局",
        "来源URL": NBS_BASE,
    },
    "PBC_LPR": {
        "来源标题": "贷款市场报价利率(LPR)",
        "数据来源": "中国人民银行",
        "来源URL": PBC_BASE,
    },
    "CIH_租售比": {
        "来源标题_tpl": "{year}年{month}月50城住宅租金房价比监测",
        "数据来源": "中指研究院",
        "来源URL": CIH_BASE,
    },
}


def row(city: str, period: str, group: str, metric: str,
        value: Any, unit: str, source_key: str, **kw) -> dict:
    """Build one indicator row following the DDS macro schema."""
    src = SOURCES[source_key]
    title = kw.get("title", src.get("来源标题", ""))
    if not title and "来源标题_tpl" in src:
        # parse period for template
        parts = period.split("-")
        if len(parts) == 2:
            title = src["来源标题_tpl"].format(year=parts[0], month=str(int(parts[1])))
        else:
            title = src["来源标题_tpl"].format(year=period, month="")
    return {
        "城市": city,
        "统计年份": period,
        "指标组": group,
        "指标": metric,
        "数值": value,
        "单位": unit,
        "同比增长_pct": kw.get("yoy"),
        "占比_pct": kw.get("share"),
        "前年差值": kw.get("diff"),
        "前年差单位": kw.get("diff_unit", ""),
        "DDS维度类目": kw.get("dds", "市场环境"),
        "备注": kw.get("note", ""),
        "来源标题": title,
        "数据来源": src["数据来源"],
        "来源URL": kw.get("url", src["来源URL"]),
        "发布日期": kw.get("pub_date", ""),
        "入库日期": TODAY,
        "来源摘录位置": kw.get("loc", ""),
    }


# ─── Fetch functions ─────────────────────────────────────────────────────────

def fetch_nbs_70city(year: int, month: int) -> list[dict]:
    """Fetch 70-city price data from NBS.

    In production, this would scrape stats.gov.cn or use the NBS API.
    For now, we provide a stub that returns empty and logs the URL to fetch.
    """
    period = f"{year}-{month:02d}"
    url = f"{NBS_BASE}sj/zs/fwspdj/"
    print(f"[fetch_nbs_70city] Would fetch {period} data from {url}")
    print(f"[fetch_nbs_70city] Manual: visit {url} and update 70城-新房价格环比月度.csv")

    # Return empty - in production, parse the NBS HTML table
    # and return list of row() dicts
    return []


def fetch_nbs_investment(year: int, month: int) -> list[dict]:
    """Fetch national real estate investment/sales data from NBS.

    In production, this would scrape the NBS monthly release.
    """
    period = f"{year}年1-{month}月"
    url = f"{NBS_BASE}sj/zxfb/"
    print(f"[fetch_nbs_investment] Would fetch {period} from {url}")
    return []


def fetch_lpr() -> list[dict]:
    """Fetch latest LPR rates from PBC."""
    url = f"{PBC_BASE}zhengcehuobisi/125207/125213/125440/"
    print(f"[fetch_lpr] Would fetch latest LPR from {url}")
    return []


def fetch_rental_yield(year: int, month: int) -> list[dict]:
    """Fetch rental yield data from CIH."""
    print(f"[fetch_rental_yield] Would fetch {year}-{month:02d} from {CIH_BASE}")
    return []


# ─── Merge & Write ───────────────────────────────────────────────────────────

def load_existing(path: Path) -> pd.DataFrame:
    """Load existing CSV, return empty DataFrame if not found."""
    if path.exists():
        return pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    return pd.DataFrame(columns=COLS)


def merge_and_write(new_records: list[dict], csv_path: Path) -> pd.DataFrame:
    """Merge new records into existing CSV, dedup by (城市, 统计年份, 指标)."""
    existing = load_existing(csv_path)
    if not new_records:
        print(f"[merge] No new records to merge into {csv_path.name}")
        return existing

    new_df = pd.DataFrame(new_records, columns=COLS)
    combined = pd.concat([existing, new_df], ignore_index=True)

    # Dedup: keep last (newest) for same city+period+indicator
    dedup_keys = ["城市", "统计年份", "指标"]
    combined = combined.drop_duplicates(subset=dedup_keys, keep="last")
    combined = combined.sort_values(["城市", "统计年份", "指标组", "指标"])

    # Write CSV + Parquet
    combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
    parquet_path = csv_path.with_suffix(".parquet")
    # Convert numeric columns for parquet
    numeric_cols = ["数值", "同比增长_pct", "占比_pct", "前年差值"]
    for col in numeric_cols:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors="coerce")
    combined.to_parquet(parquet_path, index=False)

    print(f"[merge] Wrote {len(combined)} rows to {csv_path.name} (+{len(new_records)} new, {len(existing)} existing)")
    return combined


def update_manifest():
    """Update _manifest.md to reflect current state of all files."""
    manifest_path = MACRO / "_manifest.md"
    if not manifest_path.exists():
        return

    existing = manifest_path.read_text(encoding="utf-8")

    # Check if new files are already listed
    new_entries = []
    national_csv = MACRO / "全国-房地产市场月度研判.csv"
    city70_csv = MACRO / "70城-新房价格环比月度.csv"

    if national_csv.exists() and "全国-房地产市场月度研判" not in existing:
        df = pd.read_csv(national_csv, encoding="utf-8-sig")
        new_entries.append(
            f"| `全国-房地产市场月度研判.csv/.parquet` | {len(df)} "
            f"| 2026年2-5月(月度)、2026年1-5月(累计)、2026年7月(最新) "
            f"| 国家统计局 / 中国人民银行 / 中指研究院 |"
        )

    if city70_csv.exists() and "70城-新房价格环比月度" not in existing:
        df = pd.read_csv(city70_csv, encoding="utf-8-sig")
        cities = df["城市"].nunique() if "城市" in df.columns else 0
        new_entries.append(
            f"| `70城-新房价格环比月度.csv/.parquet` | {len(df)} "
            f"| 2026年3-5月(逐月) "
            f"| 国家统计局70城房价月报 |"
        )

    if new_entries:
        # Insert before "## 字段口径"
        insert_point = existing.find("## 字段口径")
        if insert_point > 0:
            new_text = "\n".join(new_entries) + "\n\n"
            existing = existing[:insert_point] + new_text + existing[insert_point:]

        # Update date
        existing = re.sub(
            r"\*\*最近导入日期\*\*:\s*\d{4}-\d{2}-\d{2}",
            f"**最近导入日期**: {TODAY}",
            existing,
        )

        manifest_path.write_text(existing, encoding="utf-8")
        print(f"[manifest] Updated _manifest.md with {len(new_entries)} new entries")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def cmd_check(args):
    """Dry-run: report what data is available for update."""
    now = datetime.now()
    # NBS publishes around 16th of each month for previous month
    latest_available_month = now.month - 1 if now.day >= 16 else now.month - 2
    if latest_available_month <= 0:
        latest_available_month += 12

    print(f"[check] Current date: {now.date()}")
    print(f"[check] Latest NBS 70-city data likely available: {now.year}-{latest_available_month:02d}")

    # Check what we already have
    national_csv = MACRO / "全国-房地产市场月度研判.csv"
    city70_csv = MACRO / "70城-新房价格环比月度.csv"

    for p in [national_csv, city70_csv]:
        if p.exists():
            df = pd.read_csv(p, encoding="utf-8-sig")
            periods = df["统计年份"].unique() if "统计年份" in df.columns else []
            print(f"[check] {p.name}: {len(df)} rows, periods: {sorted(periods)}")
        else:
            print(f"[check] {p.name}: NOT FOUND — needs initial creation")


def cmd_update(args):
    """Full update: fetch all available data and merge."""
    now = datetime.now()

    # 1. Try fetch new data
    all_national = []
    all_city = []

    # Fetch latest month available
    latest_month = now.month - 1 if now.day >= 16 else now.month - 2
    if latest_month <= 0:
        latest_month += 12

    all_national.extend(fetch_nbs_70city(now.year, latest_month))
    all_national.extend(fetch_nbs_investment(now.year, latest_month))
    all_national.extend(fetch_lpr())
    all_national.extend(fetch_rental_yield(now.year, latest_month))

    # 2. Merge into files
    national_csv = MACRO / "全国-房地产市场月度研判.csv"
    city70_csv = MACRO / "70城-新房价格环比月度.csv"

    if all_national:
        merge_and_write(all_national, national_csv)
    else:
        print("[update] No new national data fetched (stub mode).")
        print("[update] To add data manually, edit the CSV files directly or run:")
        print(f"         update_official_city_indicators.py (for city annual data)")

    if all_city:
        merge_and_write(all_city, city70_csv)

    # 3. Update manifest
    update_manifest()

    # 4. Rebuild parquet for any existing CSVs
    for csv_file in [national_csv, city70_csv]:
        if csv_file.exists():
            _rebuild_parquet(csv_file)


def _rebuild_parquet(csv_path: Path):
    """Rebuild parquet from CSV."""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    numeric_cols = ["数值", "同比增长_pct", "占比_pct", "前年差值"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    parquet_path = csv_path.with_suffix(".parquet")
    df.to_parquet(parquet_path, index=False)
    print(f"[parquet] Rebuilt {parquet_path.name}: {len(df)} rows")


def cmd_schedule(args):
    """Print recommended cron schedule."""
    print("# DDS 宏观研判自动抓取 推荐调度计划")
    print("# ─────────────────────────────────────────")
    print("# 每月18日 09:00 — NBS 70城房价月报 + 全国投资销售数据")
    print("0 9 18 * *  cd /d c:\\Users\\shiguanyu\\DDS && python scripts/fetch_macro_indicators.py --update")
    print("")
    print("# 每月21日 09:00 — LPR 公告 (央行每月20日发布)")
    print("0 9 21 * *  cd /d c:\\Users\\shiguanyu\\DDS && python scripts/fetch_macro_indicators.py --update")
    print("")
    print("# PowerShell Scheduled Task (Windows):")
    print("# schtasks /create /tn \"DDS_MacroFetch\" /tr \"powershell -ExecutionPolicy Bypass -Command 'cd c:\\Users\\shiguanyu\\DDS; python scripts/fetch_macro_indicators.py --update'\" /sc monthly /d 18 /st 09:00")


def cmd_rebuild(args):
    """Rebuild parquet files from existing CSVs."""
    for csv_file in MACRO.glob("*.csv"):
        if csv_file.name.startswith("_"):
            continue
        _rebuild_parquet(csv_file)


def main():
    parser = argparse.ArgumentParser(description="DDS 宏观研判数据自动抓取管道")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="检查可更新数据（dry-run）")
    sub.add_parser("update", help="全量更新（抓取+合并+写入）")
    sub.add_parser("schedule", help="打印推荐的自动调度计划")
    sub.add_parser("rebuild", help="从CSV重建所有parquet文件")

    args = parser.parse_args()

    if args.command == "check":
        cmd_check(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "schedule":
        cmd_schedule(args)
    elif args.command == "rebuild":
        cmd_rebuild(args)
    else:
        # Default: check
        cmd_check(args)


if __name__ == "__main__":
    main()
