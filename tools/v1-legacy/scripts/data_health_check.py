"""
T6 数据体检表生成器
Vault 全量扫描 -> data_out/reports/vault_health_YYYYMMDD.xlsx
每个 sheet 均带数据来源溯源（来源/信任级/来源URL/文件），逐项可追溯。
"""
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

VAULT = Path(__file__).resolve().parent.parent / "Vault"

# 来源元数据：source 代码 -> (可读来源, 信任级, 来源URL/依据)
# anjuke_real 实采自安居客新房（每行"默认图片"为真实 *.ajkimg.com 链接佐证）；
# 合成/LLM 数据无真实外部来源，如实标注生成脚本，绝不伪造 URL。
SOURCE_META = {
    "anjuke_real":      ("安居客新房(实采)",   "L2", "https://www.anjuke.com/"),
    "purchased_newhouse_pool": (
        "购买库-全国新楼盘",
        "L2",
        "internal:Vault/_purchased/全国新楼盘数据.csv",
    ),
    "mixed_real_sources": (
        "真实数据混合来源",
        "L2",
        "row-level:业内评价",
    ),
    "unmarked_real": ("真实数据-来源标记缺失", "L2", "gap:业内评价为空"),
    "purchased_opening_year_backfill": (
        "购买库-真实开盘年在售回填",
        "L2",
        "internal:Vault/_purchased/",
    ),
    "cloned_synthetic": ("本地合成-最近邻克隆", "L3", "no-source:generate_cloned_history.py"),
    "llm_generated":    ("LLM合成-客群",       "L3", "no-source:abm_engine.py"),
}


def src_meta(source: str):
    return SOURCE_META.get(source, ("未知", "NA", "unknown"))


def optional_bool(value):
    """规范 manifest 中可能来自 CSV 的布尔字符串。"""
    if pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes", "是"}:
        return True
    if normalized in {"false", "0", "no", "否"}:
        return False
    return None


def discover_loupan_files(vault: Path = VAULT) -> pd.DataFrame:
    """发现实际楼盘文件，排除 2026 年镜像和派生副本。"""
    vault = Path(vault)
    records = []

    for year in range(1995, 2026):
        year_dir = vault / f"{year}年"
        if not year_dir.is_dir():
            continue
        for csv_path in sorted(year_dir.glob("*.csv"), key=lambda p: p.name):
            city = csv_path.stem
            if city.startswith("_") or city.startswith("客群"):
                continue
            records.append({
                "path": csv_path.relative_to(vault).as_posix(),
                "year": str(year),
                "city": city,
            })

    current_dir = vault / "2026新楼盘"
    if current_dir.is_dir():
        for csv_path in sorted(current_dir.glob("新楼盘-*.csv"), key=lambda p: p.name):
            city = csv_path.stem.removeprefix("新楼盘-")
            # 当前池中的下划线后缀文件是插补/调试等派生物，不是规范城市文件。
            if not city or "_" in city:
                continue
            records.append({
                "path": csv_path.relative_to(vault).as_posix(),
                "year": "2026",
                "city": city,
            })

    return pd.DataFrame(records, columns=["path", "year", "city"])


def scan_vault_metadata(vault: Path = VAULT) -> pd.DataFrame:
    """读取 _manifest.csv 元数据汇总"""
    manifest_path = Path(vault) / "_manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return pd.read_csv(manifest_path, dtype={"year": str})


def scan_files(files: pd.DataFrame, vault: Path = VAULT) -> pd.DataFrame:
    """逐个读取发现到的 CSV，所有质量指标均来自同一次实时扫描。"""
    from geo_clean import clean_coords

    metric_columns = [
        "rows", "missing_price", "missing", "zero", "outside_china",
        "outside_expected_scope", "centroid_outlier", "valid", "geo_valid",
        "dup_ids",
    ]
    columns = [
        "path", "year", "city", "source", "trust", "is_synthetic",
        *metric_columns, "scan_error",
    ]
    out = []
    for _, row in files.iterrows():
        csv_path = Path(vault) / Path(str(row["path"]))
        rec = {
            "path": str(row["path"]),
            "year": str(row["year"]),
            "city": str(row["city"]),
            "source": row.get("source", None),
            "trust": row.get("trust", None),
            "is_synthetic": row.get("is_synthetic", None),
            **{name: None for name in metric_columns},
            "scan_error": "",
        }
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
            rec["rows"] = len(df)
            if "最新价格" in df.columns:
                price = pd.to_numeric(df["最新价格"], errors="coerce")
                rec["missing_price"] = int((price.isna() | price.le(0)).sum())
            else:
                rec["missing_price"] = len(df)

            if "百度地图纬度" in df.columns and "百度地图经度" in df.columns:
                g = df.rename(columns={"百度地图纬度": "lat", "百度地图经度": "lng"})
                classified = clean_coords(g, str(row["city"]))
                counts = classified["geo_status"].value_counts()
                for status in [
                    "missing", "zero", "outside_china",
                    "outside_expected_scope", "centroid_outlier", "valid",
                ]:
                    rec[status] = int(counts.get(status, 0))
                rec["geo_valid"] = rec["valid"]
            else:
                rec.update({
                    "missing": len(df),
                    "zero": 0,
                    "outside_china": 0,
                    "outside_expected_scope": 0,
                    "centroid_outlier": 0,
                    "valid": 0,
                    "geo_valid": 0,
                })
            rec["dup_ids"] = int(df["楼盘ID"].duplicated().sum()) if "楼盘ID" in df.columns else 0
        except Exception as e:
            rec["scan_error"] = f"{type(e).__name__}: {e}"
            print(f"[WARN] scan {row['path']}: {rec['scan_error']}")
        out.append(rec)
    return pd.DataFrame(out, columns=columns)


def build_overview_sheet(scan: pd.DataFrame) -> pd.DataFrame:
    """Sheet1 概览：分子、分母全部聚合自同一份实时扫描结果。"""
    columns = [
        "年份", "城市", "文件数", "总行数", "可用价行数", "可用价率%",
        "坐标有效行数", "坐标有效率%", "信任级", "是否合成", "来源",
        "来源URL", "扫描错误",
    ]
    rows = []
    if scan.empty:
        return pd.DataFrame(columns=columns)

    for (year, city), sub in scan.groupby(["year", "city"], sort=True, dropna=False):
        errors = [str(value) for value in sub["scan_error"] if value]
        source_values = sub["source"].dropna().tolist()
        trust_values = sub["trust"].dropna().tolist()
        synth_values = [
            parsed
            for parsed in (optional_bool(value) for value in sub["is_synthetic"])
            if parsed is not None
        ]
        source = source_values[0] if source_values else None
        label, _, url = src_meta(source)

        if errors:
            total = usable = geo_valid = None
            price_rate = geo_rate = None
        else:
            total = int(sub["rows"].sum())
            missing_price = int(sub["missing_price"].sum())
            usable = total - missing_price
            geo_valid = int(sub["geo_valid"].sum())
            price_rate = round(usable / total * 100, 1) if total else 0.0
            geo_rate = round(geo_valid / total * 100, 1) if total else 0.0

        rows.append({
            "年份": year,
            "城市": city,
            "文件数": len(sub),
            "总行数": total,
            "可用价行数": usable,
            "可用价率%": price_rate,
            "坐标有效行数": geo_valid,
            "坐标有效率%": geo_rate,
            "信任级": trust_values[0] if trust_values else "NA",
            "是否合成": (
                "是" if synth_values and any(synth_values)
                else "否" if synth_values else "未知"
            ),
            "来源": label,
            "来源URL": url,
            "扫描错误": " | ".join(errors),
        })
    return pd.DataFrame(rows, columns=columns)


def build_anomaly_sheet(scan: pd.DataFrame) -> pd.DataFrame:
    """Sheet2 异常清单：保留每种异常原因，读取失败显式展示。"""
    cols = [
        "年份", "城市", "总行数", "来源", "信任级", "缺价行数(空或0)",
        "坐标缺失行数", "零坐标行数", "中国范围外行数", "预期范围外行数",
        "中心点离群行数", "坐标有效行数", "重复ID行数", "扫描错误",
        "来源URL", "文件",
    ]
    out = []
    for _, r in scan.iterrows():
        anomaly_fields = [
            "missing_price", "missing", "zero", "outside_china",
            "outside_expected_scope", "centroid_outlier", "dup_ids",
        ]
        has_anomaly = any(pd.notna(r[name]) and r[name] > 0 for name in anomaly_fields)
        if has_anomaly or bool(r["scan_error"]):
            label, _, url = src_meta(r.get("source"))
            out.append({
                "年份": r["year"], "城市": r["city"], "总行数": r["rows"],
                "来源": label,
                "信任级": r["trust"] if pd.notna(r.get("trust")) else "NA",
                "缺价行数(空或0)": r["missing_price"],
                "坐标缺失行数": r["missing"],
                "零坐标行数": r["zero"],
                "中国范围外行数": r["outside_china"],
                "预期范围外行数": r["outside_expected_scope"],
                "中心点离群行数": r["centroid_outlier"],
                "坐标有效行数": r["geo_valid"],
                "重复ID行数": r["dup_ids"],
                "扫描错误": r["scan_error"],
                "来源URL": url, "文件": r["path"],
            })
    return pd.DataFrame(out, columns=cols)


def build_price_trend_sheet(files: pd.DataFrame, vault: Path = VAULT) -> pd.DataFrame:
    """Sheet3 价格趋势：各 CSV 中位单价 + 来源溯源。"""
    out = []
    for _, row in files.iterrows():
        csv_path = str(Path(vault) / Path(str(row["path"]))).replace("\\", "/")
        median_price, n = None, 0
        try:
            res = duckdb.query(f"""
                SELECT TRY_CAST("最新价格" AS DOUBLE) p
                FROM read_csv_auto('{csv_path}', header=true)
                WHERE "参考价格" LIKE '%元/㎡%' AND TRY_CAST("最新价格" AS DOUBLE) IS NOT NULL
            """).fetchall()
            prices = [r[0] for r in res if r[0] is not None]
            n = len(prices)
            if prices:
                median_price = sorted(prices)[len(prices) // 2]
        except Exception as e:
            print(f"[WARN] price {row['path']}: {e}")
        source = row.get("source", None)
        trust = row.get("trust", None)
        label, _, url = src_meta(source)
        out.append({
            "年份": row["year"], "城市": row["city"],
            "中位单价": median_price, "样本数": n,
            "来源": label,
            "信任级": trust if pd.notna(trust) else "NA",
            "来源URL": url,
            "文件": row["path"],
        })
    return pd.DataFrame(out)


def build_manifest_summary(scan: pd.DataFrame) -> pd.DataFrame:
    """Sheet4 来源透视：文件数和行数同样来自实时扫描。"""
    columns = ["信任级", "合成标记", "文件数", "总行数", "来源", "来源URL"]
    if scan.empty:
        return pd.DataFrame(columns=columns)

    work = scan.copy()
    work["trust"] = work["trust"].fillna("NA")
    work["is_synthetic"] = work["is_synthetic"].map(optional_bool).map(
        lambda value: "未知" if value is None else ("合成" if value else "真实")
    )
    out = []
    for (trust, synth), sub in work.groupby(["trust", "is_synthetic"], sort=True):
        sources = sorted(str(value) for value in sub["source"].dropna().unique())
        urls = sorted({src_meta(source)[2] for source in sources})
        total = sub["rows"].sum(min_count=1)
        out.append({
            "信任级": trust,
            "合成标记": synth,
            "文件数": len(sub),
            "总行数": int(total) if pd.notna(total) else None,
            "来源": " / ".join(sources) if sources else "未知",
            "来源URL": " / ".join(urls) if urls else "unknown",
        })
    return pd.DataFrame(out, columns=columns)


def build_source_legend() -> pd.DataFrame:
    """Sheet5 来源说明：数据来源口径与依据（溯源图例）。"""
    return pd.DataFrame([
        {"source代码": "anjuke_real", "来源": "安居客新房(实采)", "信任级": "L2", "是否合成": "否",
         "来源URL/依据": "https://www.anjuke.com/ （每行'默认图片'为真实 https://*.ajkimg.com 链接佐证）",
         "采集/生成": "scrape_fang.py / anjuke_detail.py"},
        {"source代码": "purchased_newhouse_pool", "来源": "购买库-全国新楼盘",
         "信任级": "L2", "是否合成": "否",
         "来源URL/依据": "internal:Vault/_purchased/全国新楼盘数据.csv；行内业内评价=来源:购买数据",
         "采集/生成": "ingest_purchased.py / split_national_to_pool.py"},
        {"source代码": "mixed_real_sources", "来源": "真实数据混合来源",
         "信任级": "L2", "是否合成": "否",
         "来源URL/依据": "row-level:业内评价；文件内包含购买/采集/未标记等多类来源",
         "采集/生成": "多入口合并，按行追溯"},
        {"source代码": "unmarked_real", "来源": "真实数据-来源标记缺失",
         "信任级": "L2", "是否合成": "否",
         "来源URL/依据": "gap:业内评价为空；须补来源标记",
         "采集/生成": "未知真实入口"},
        {"source代码": "purchased_opening_year_backfill", "来源": "购买库-真实开盘年在售回填",
         "信任级": "L2", "是否合成": "否",
         "来源URL/依据": "internal:Vault/_purchased/；行内业内评价带真实开盘年回填标记",
         "采集/生成": "rebuild_history_real.py / 济南真实历史回填流程"},
        {"source代码": "cloned_synthetic", "来源": "本地合成-最近邻克隆", "信任级": "L3", "是否合成": "是",
         "来源URL/依据": "无真实外部来源（合成数据，价格按时间回归生成）",
         "采集/生成": "generate_cloned_history.py / expand_local_v3.py"},
        {"source代码": "llm_generated", "来源": "LLM合成-客群样本/画像", "信任级": "L3", "是否合成": "是",
         "来源URL/依据": "无真实外部来源（LLM 生成）",
         "采集/生成": "abm_engine.py / generate_customer_data.py"},
        {"source代码": "L1(未接入)", "来源": "政府成交/备案价", "信任级": "L1", "是否合成": "否",
         "来源URL/依据": ".env 占位 HZ_PRICE_URL/SANYA_PRICE_URL/HZ_DEALS_URL/SANYA_DEALS_URL，待真实政府域名",
         "采集/生成": "scrape_*_price.py / scrape_*_deals.py（T7 降级中）"},
    ])


def main():
    print("[...] Discovering canonical project files...")
    files = discover_loupan_files(VAULT)

    # Manifest 只补充来源元数据，不参与任何实时质量指标的分子或分母。
    try:
        manifest = scan_vault_metadata(VAULT)
    except FileNotFoundError as error:
        print(f"[WARN] {error}")
        manifest = pd.DataFrame()
    if not manifest.empty and "path" in manifest.columns:
        metadata_columns = [
            name for name in ["path", "source", "trust", "is_synthetic"]
            if name in manifest.columns
        ]
        metadata = manifest[metadata_columns].copy()
        metadata["path"] = metadata["path"].astype(str).str.replace("\\", "/", regex=False)
        metadata = metadata.drop_duplicates("path", keep="last")
        files = files.merge(metadata, on="path", how="left")

    print("[...] Scanning files (geo/price/dup)...")
    scan = scan_files(files, vault=VAULT)

    print("[...] Building sheets...")
    overview = build_overview_sheet(scan)
    anomalies = build_anomaly_sheet(scan)
    price_trend = build_price_trend_sheet(files, vault=VAULT)
    manifest_summary = build_manifest_summary(scan)
    legend = build_source_legend()

    ts = datetime.now().strftime("%Y%m%d")
    out_path = VAULT / f"../data_out/reports/vault_health_{ts}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[...] Writing Excel: {out_path}")
    with pd.ExcelWriter(out_path, engine="openpyxl") as w:
        overview.to_excel(w, sheet_name="概览", index=False)
        anomalies.to_excel(w, sheet_name="异常清单", index=False)
        price_trend.to_excel(w, sheet_name="价格趋势", index=False)
        manifest_summary.to_excel(w, sheet_name="Manifest汇总", index=False)
        legend.to_excel(w, sheet_name="来源说明", index=False)
        manifest.to_excel(w, sheet_name="Manifest原始", index=False)

    print(f"[OK] Health check saved: {out_path}")
    print(f"  - 概览: {len(overview)} 行（坐标有效率已现算）")
    print(f"  - 异常清单: {len(anomalies)} 行（缺价/坐标分类/重复ID/扫描错误）")
    print(f"  - 价格趋势: {len(price_trend)} 行")
    print(f"  - Manifest汇总: {len(manifest_summary)} 行")
    print(f"  - 来源说明: {len(legend)} 行")


if __name__ == "__main__":
    main()
