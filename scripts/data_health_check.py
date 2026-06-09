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
    "cloned_synthetic": ("本地合成-最近邻克隆", "L3", "no-source:generate_cloned_history.py"),
    "llm_generated":    ("LLM合成-客群",       "L3", "no-source:abm_engine.py"),
}


def src_meta(source: str):
    return SOURCE_META.get(source, ("未知", "NA", "unknown"))


def scan_vault_metadata() -> pd.DataFrame:
    """读取 _manifest.csv 元数据汇总"""
    manifest_path = VAULT / "_manifest.csv"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return pd.read_csv(manifest_path, dtype={"year": str})


def scan_files(manifest: pd.DataFrame) -> pd.DataFrame:
    """单次扫描每个楼盘 CSV，产出行级质量统计，供概览+异常复用（避免重复读盘）。"""
    from geo_clean import clean_coords  # T1 坐标清洗
    loupan = manifest[manifest["dtype"] == "楼盘"].copy()
    out = []
    for _, row in loupan.iterrows():
        csv_path = str(VAULT / row["path"]).replace("\\", "/")
        rec = {"path": row["path"], "year": row["year"], "city": row["city"],
               "source": row["source"], "trust": row["trust"], "is_synthetic": row["is_synthetic"],
               "rows": 0, "missing_price": 0, "geo_valid": 0, "out_of_bbox": 0, "dup_ids": 0}
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
            rec["rows"] = len(df)
            # 缺价：最新价格 为空/非数/<=0（修复:稀疏价格是空值，旧逻辑 ==0 永不命中）
            price = pd.to_numeric(df.get("最新价格"), errors="coerce")
            rec["missing_price"] = int((price.isna() | (price <= 0)).sum())
            # 坐标有效率/越界：复用 T1 clean_coords（修复:概览旧列读空的 manifest 字段）
            if "百度地图纬度" in df.columns and "百度地图经度" in df.columns:
                g = df.rename(columns={"百度地图纬度": "lat", "百度地图经度": "lng"})
                g["lat"] = pd.to_numeric(g["lat"], errors="coerce")
                g["lng"] = pd.to_numeric(g["lng"], errors="coerce")
                valid = clean_coords(g, str(row["city"]))["geo_valid"]
                rec["geo_valid"] = int(valid.sum())
                rec["out_of_bbox"] = int((~valid).sum())
            else:
                rec["geo_valid"] = len(df)  # 无坐标列：不计越界
            rec["dup_ids"] = int(df["楼盘ID"].duplicated().sum()) if "楼盘ID" in df.columns else 0
        except Exception as e:
            print(f"[WARN] scan {row['path']}: {e}")
        out.append(rec)
    return pd.DataFrame(out)


def build_overview_sheet(manifest: pd.DataFrame, scan: pd.DataFrame) -> pd.DataFrame:
    """Sheet1 概览：每年每城 行数/可用价率/坐标有效率 + 来源溯源。"""
    loupan = manifest[manifest["dtype"] == "楼盘"].copy()
    rows = []
    for year in sorted(loupan["year"].unique()):
        for city in sorted(loupan["city"].unique()):
            sub = loupan[(loupan["year"] == year) & (loupan["city"] == city)]
            if sub.empty:
                continue
            sc = scan[(scan["year"] == year) & (scan["city"] == city)]
            total = int(sub["rows"].sum())
            usable = int(sub["usable_price_rows"].sum()) if "usable_price_rows" in sub.columns else 0
            geo_rows = int(sc["rows"].sum())
            geo_valid = int(sc["geo_valid"].sum())
            label, _, url = src_meta(sub["source"].iloc[0])
            rows.append({
                "年份": year, "城市": city, "总行数": total,
                "可用价行数": usable,
                "可用价率%": round(usable / total * 100, 1) if total else 0,
                # 坐标有效率现算（geo_clean），不再读空的 manifest 字段
                "坐标有效率%": round(geo_valid / geo_rows * 100, 1) if geo_rows else 0,
                "信任级": sub["trust"].iloc[0],
                "是否合成": "是" if sub["is_synthetic"].iloc[0] else "否",
                "来源": label, "来源URL": url,
            })
    return pd.DataFrame(rows)


def build_anomaly_sheet(scan: pd.DataFrame) -> pd.DataFrame:
    """Sheet2 异常清单：行级 缺价/越界/重复ID + 来源URL。"""
    cols = ["年份", "城市", "来源", "信任级", "缺价行数(空或0)", "坐标越界行数",
            "重复ID行数", "来源URL", "文件"]
    out = []
    for _, r in scan.iterrows():
        if r["missing_price"] > 0 or r["out_of_bbox"] > 0 or r["dup_ids"] > 0:
            label, _, url = src_meta(r["source"])
            out.append({
                "年份": r["year"], "城市": r["city"], "来源": label, "信任级": r["trust"],
                "缺价行数(空或0)": int(r["missing_price"]),
                "坐标越界行数": int(r["out_of_bbox"]),
                "重复ID行数": int(r["dup_ids"]),
                "来源URL": url, "文件": r["path"],
            })
    return pd.DataFrame(out, columns=cols)


def build_price_trend_sheet(manifest: pd.DataFrame) -> pd.DataFrame:
    """Sheet3 价格趋势：各 CSV 中位单价 + 来源溯源。"""
    loupan = manifest[manifest["dtype"] == "楼盘"].copy()
    out = []
    for _, row in loupan.iterrows():
        csv_path = str(VAULT / row["path"]).replace("\\", "/")
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
        label, _, url = src_meta(row["source"])
        out.append({
            "年份": row["year"], "城市": row["city"],
            "中位单价": median_price, "样本数": n,
            "来源": label, "信任级": row["trust"], "来源URL": url, "文件": row["path"],
        })
    return pd.DataFrame(out)


def build_manifest_summary(manifest: pd.DataFrame) -> pd.DataFrame:
    """Sheet4 Manifest 透视：按 trust/is_synthetic 聚合 + 来源URL。"""
    out = []
    for trust in ["L1", "L2", "L3"]:
        for synth in [True, False]:
            sub = manifest[(manifest["trust"] == trust) & (manifest["is_synthetic"] == synth)]
            if sub.empty:
                continue
            urls = sorted({src_meta(s)[2] for s in sub["source"].unique()})
            out.append({
                "信任级": trust, "合成标记": "合成" if synth else "真实",
                "文件数": len(sub), "总行数": int(sub["rows"].sum()),
                "来源": " / ".join(sub["source"].unique()),
                "来源URL": " / ".join(urls),
            })
    return pd.DataFrame(out)


def build_source_legend() -> pd.DataFrame:
    """Sheet5 来源说明：数据来源口径与依据（溯源图例）。"""
    return pd.DataFrame([
        {"source代码": "anjuke_real", "来源": "安居客新房(实采)", "信任级": "L2", "是否合成": "否",
         "来源URL/依据": "https://www.anjuke.com/ （每行'默认图片'为真实 https://*.ajkimg.com 链接佐证）",
         "采集/生成": "scrape_fang.py / anjuke_detail.py"},
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
    print("[...] Scanning Vault metadata...")
    manifest = scan_vault_metadata()
    print("[...] Scanning files (geo/price/dup)...")
    scan = scan_files(manifest)

    print("[...] Building sheets...")
    overview = build_overview_sheet(manifest, scan)
    anomalies = build_anomaly_sheet(scan)
    price_trend = build_price_trend_sheet(manifest)
    manifest_summary = build_manifest_summary(manifest)
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
    print(f"  - 异常清单: {len(anomalies)} 行（缺价/越界/重复ID）")
    print(f"  - 价格趋势: {len(price_trend)} 行")
    print(f"  - Manifest汇总: {len(manifest_summary)} 行")
    print(f"  - 来源说明: {len(legend)} 行")


if __name__ == "__main__":
    main()
