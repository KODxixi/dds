# -*- coding: utf-8 -*-
"""导入真实成交明细 CSV 到 DDS Vault 成交数据层。

输入保持原始中文字段；输出:
  - Vault/_purchased/<原文件名>              原始购买文件保全
  - Vault/成交数据/成交-<城市>.csv(.parquet) 可查询成交层
  - Vault/成交数据/_cities_index.csv        成交层城市索引
  - Vault/成交数据/_manifest.md             成交层数据说明
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
PURCHASED = VAULT / "_purchased"
TRANSACTIONS = VAULT / "成交数据"
TODAY = date.today().isoformat()

REQUIRED_COLUMNS = [
    "小区名称",
    "区域地址",
    "物业类型",
    "经度",
    "纬度",
    "高德经度",
    "高德纬度",
    "户型",
    "面积",
    "成交总额",
    "成交均价",
    "成交时间",
    "城市",
    "商圈",
    "区县",
    "楼层",
    "总楼层",
    "朝向",
]


def _read_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=enc)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"无法读取 CSV: {path}") from last_error


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def _copy_raw(source: Path) -> Path:
    PURCHASED.mkdir(parents=True, exist_ok=True)
    target = PURCHASED / source.name
    if target.exists() and _sha256(target) != _sha256(source):
        target = PURCHASED / f"{source.stem}.{TODAY}{source.suffix}"
    shutil.copy2(source, target)
    return target


def _validate(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("成交 CSV 缺少必要字段: " + ", ".join(missing))


def _write_city_index() -> None:
    rows = []
    for path in sorted(TRANSACTIONS.glob("成交-*.csv")):
        city = path.stem.replace("成交-", "", 1)
        df = _read_csv(path)
        price = _safe_number(df["成交均价"]) if "成交均价" in df else pd.Series(dtype=float)
        dates = pd.to_datetime(df["成交时间"], errors="coerce") if "成交时间" in df else pd.Series(dtype="datetime64[ns]")
        rows.append({
            "城市": city,
            "成交记录数": len(df),
            "小区数": df["小区名称"].nunique() if "小区名称" in df else "",
            "最早成交时间": dates.min().date().isoformat() if dates.notna().any() else "",
            "最新成交时间": dates.max().date().isoformat() if dates.notna().any() else "",
            "成交均价均值_元㎡": round(float(price.mean()), 0) if price.notna().any() else "",
            "成交均价中位数_元㎡": round(float(price.median()), 0) if price.notna().any() else "",
            "有parquet": path.with_suffix(".parquet").exists(),
        })
    with open(TRANSACTIONS / "_cities_index.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "城市", "成交记录数", "小区数", "最早成交时间", "最新成交时间",
            "成交均价均值_元㎡", "成交均价中位数_元㎡", "有parquet",
        ])
        writer.writeheader()
        writer.writerows(rows)


def _write_manifest(raw_target: Path, city: str, df: pd.DataFrame) -> None:
    price = _safe_number(df["成交均价"])
    area = _safe_number(df["面积"])
    dates = pd.to_datetime(df["成交时间"], errors="coerce")
    target_csv = TRANSACTIONS / f"成交-{city}.csv"
    lines = [
        "# 成交数据层 Manifest",
        "",
        f"- **最近导入日期**: {TODAY}",
        "- **用途**: DDS 真实成交/二手交易明细参考层，不并入新盘供给库。",
        "- **目录**: `Vault/成交数据/`",
        "- **读取方式**: 直接读取 `成交-{城市}.csv/.parquet`；后续如需报告自动引用，再接入 `query_local`。",
        "",
        "## 本次导入",
        f"- 原始保全: `Vault/_purchased/{raw_target.name}`",
        f"- 查询层 CSV: `Vault/成交数据/{target_csv.name}`",
        f"- 查询层 Parquet: `Vault/成交数据/{target_csv.with_suffix('.parquet').name}`",
        f"- 城市: {city}",
        f"- 成交记录数: {len(df):,}",
        f"- 小区数: {df['小区名称'].nunique():,}",
        f"- 时间范围: {dates.min().date().isoformat() if dates.notna().any() else '未知'} 至 {dates.max().date().isoformat() if dates.notna().any() else '未知'}",
        f"- 成交均价: 均值 {price.mean():.0f} 元/㎡，中位数 {price.median():.0f} 元/㎡",
        f"- 面积: 均值 {area.mean():.1f} ㎡，中位数 {area.median():.1f} ㎡",
        "",
        "## 字段口径",
        "- `成交总额`: 源数据口径为万元。",
        "- `成交均价`: 元/㎡。",
        "- 坐标同时保留源经纬度与高德经纬度；查询层分别暴露为 `lng/lat` 与 `lng_gcj/lat_gcj`。",
        "- 本层是交易明细，不代表新盘在售供给，不参与 `新楼盘-{城市}` 去重合并。",
        "",
    ]
    (TRANSACTIONS / "_manifest.md").write_text("\n".join(lines), encoding="utf-8")


def ingest(source: Path, city: str | None = None, source_label: str | None = None) -> dict:
    source = source.resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    raw_target = _copy_raw(source)
    df = _read_csv(source)
    _validate(df)

    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()

    actual_city = city or str(df["城市"].mode().iloc[0]).strip()
    df = df[df["城市"].astype(str).str.strip() == actual_city].copy()
    if df.empty:
        raise ValueError(f"未找到城市={actual_city} 的成交记录")

    provenance = source_label or f"来源:购买数据({actual_city}成交数据) 归档{TODAY}"
    if "数据来源" not in df.columns:
        df["数据来源"] = provenance
    if "入库日期" not in df.columns:
        df["入库日期"] = TODAY

    TRANSACTIONS.mkdir(parents=True, exist_ok=True)
    target_csv = TRANSACTIONS / f"成交-{actual_city}.csv"
    df.to_csv(target_csv, index=False, encoding="utf-8-sig")
    df.to_parquet(target_csv.with_suffix(".parquet"), index=False)

    _write_city_index()
    _write_manifest(raw_target, actual_city, df)

    price = _safe_number(df["成交均价"])
    dates = pd.to_datetime(df["成交时间"], errors="coerce")
    return {
        "city": actual_city,
        "rows": int(len(df)),
        "communities": int(df["小区名称"].nunique()),
        "date_min": dates.min().date().isoformat() if dates.notna().any() else "",
        "date_max": dates.max().date().isoformat() if dates.notna().any() else "",
        "avg_unit_price": round(float(price.mean()), 0) if price.notna().any() else None,
        "median_unit_price": round(float(price.median()), 0) if price.notna().any() else None,
        "raw": str(raw_target),
        "csv": str(target_csv),
        "parquet": str(target_csv.with_suffix(".parquet")),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 DDS 真实成交明细 CSV")
    parser.add_argument("--in", dest="input_path", required=True, help="成交 CSV 路径")
    parser.add_argument("--city", default=None, help="城市名；默认取 CSV 城市众数")
    parser.add_argument("--source-label", default=None, help="写入数据来源列的溯源说明")
    args = parser.parse_args()

    summary = ingest(Path(args.input_path), city=args.city, source_label=args.source_label)
    print("[成交入库] 完成")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
