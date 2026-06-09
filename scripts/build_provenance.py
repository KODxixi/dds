"""
T3 数据溯源 manifest 生成器
输出 Vault/_manifest.csv 与 _manifest.json，标注每份数据的来源、信任级、合成标记
"""
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

VAULT = Path(__file__).resolve().parent.parent / "Vault"
CURRENT_YEAR = "2026"

# 信任级定义
TRUST_L1 = "L1"
TRUST_L2 = "L2"
TRUST_L3 = "L3"

# 来源标签
SOURCE_ANJUKE_REAL = "anjuke_real"
SOURCE_CLONED_SYNTHETIC = "cloned_synthetic"
SOURCE_LLM_GENERATED = "llm_generated"


def get_usable_price_rate(csv_path: Path, city: str) -> tuple[int, int]:
    """
    计算可用单价行数 / 总行数
    口径：参考价格 LIKE '%元/㎡%' 且 最新价格 可转 double
    """
    try:
        csv_str = str(csv_path).replace("\\", "/")
        result = duckdb.query(f"""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN "参考价格" LIKE '%元/㎡%'
                         AND TRY_CAST("最新价格" AS DOUBLE) IS NOT NULL
                    THEN 1 ELSE 0 END) as usable
            FROM read_csv_auto('{csv_str}', header=true)
        """).fetchall()
        if result and len(result) > 0:
            total, usable = result[0]
            return (usable or 0, total or 1)
        return (0, 1)
    except Exception as e:
        print(f"[WARN] get_usable_price_rate error: {csv_path}: {e}")
        return (0, 1)


def get_row_count(csv_path: Path) -> int:
    """获取 CSV 行数"""
    try:
        csv_str = str(csv_path).replace("\\", "/")
        result = duckdb.query(f"SELECT COUNT(*) as cnt FROM read_csv_auto('{csv_str}', header=true)").fetchall()
        return result[0][0] if result else 0
    except Exception:
        return 0


def build_manifest() -> list[dict]:
    """
    枚举 Vault 目录，为每个数据文件生成 manifest 行
    按规则自动打标 (source, trust, is_synthetic)
    """
    records = []

    # ── 1. 历史数据 1995–2025年 ──────────────────────────────────────────
    for year_dir in VAULT.glob("????年"):
        year_name = year_dir.name.replace("年", "")
        if not year_name.isdigit() or int(year_name) > 2025 or int(year_name) < 1995:
            continue
        year = year_name

        for csv_file in year_dir.glob("*.csv"):
            # 识别城市
            stem = csv_file.stem
            city = None
            for candidate in ["三亚", "杭州", "上海", "青岛"]:
                if candidate in stem:
                    city = candidate
                    break

            if not city:
                continue

            # 识别数据类型
            if "客群样本" in stem:
                dtype = "客群样本"
                source = SOURCE_LLM_GENERATED
                trust = TRUST_L3
                is_synthetic = True
            elif "客群画像" in stem:
                dtype = "客群画像"
                source = SOURCE_LLM_GENERATED
                trust = TRUST_L3
                is_synthetic = True
            else:
                dtype = "楼盘"
                source = SOURCE_CLONED_SYNTHETIC
                trust = TRUST_L3
                is_synthetic = True

            # 计算指标
            row_count = get_row_count(csv_file)
            usable_price_rows, total_rows = get_usable_price_rate(csv_file, city) if dtype == "楼盘" else (0, row_count)
            usable_price_rate = usable_price_rows / total_rows if total_rows > 0 else 0
            # geo_valid_rate 待 T1 完成后从 geo_clean 填充，现暂为 N/A
            geo_valid_rate = None

            records.append({
                "year": year,
                "city": city,
                "dtype": dtype,
                "path": str(csv_file.relative_to(VAULT)),
                "rows": row_count,
                "usable_price_rows": usable_price_rows if dtype == "楼盘" else None,
                "usable_price_rate": round(usable_price_rate, 3) if dtype == "楼盘" else None,
                "geo_valid_rate": geo_valid_rate,
                "source": source,
                "trust": trust,
                "is_synthetic": is_synthetic,
            })

    # ── 2. 当年数据 2026年 ────────────────────────────────────────────────
    year_2026_dir = VAULT / "2026年"
    if year_2026_dir.exists():
        for csv_file in year_2026_dir.glob("*.csv"):
            stem = csv_file.stem
            city = None
            for candidate in ["三亚", "杭州", "上海", "青岛"]:
                if candidate in stem:
                    city = candidate
                    break

            if not city:
                continue

            if "客群样本" in stem:
                dtype = "客群样本"
                source = SOURCE_LLM_GENERATED
                trust = TRUST_L3
                is_synthetic = True
            elif "客群画像" in stem:
                dtype = "客群画像"
                source = SOURCE_LLM_GENERATED
                trust = TRUST_L3
                is_synthetic = True
            else:
                dtype = "楼盘"
                source = SOURCE_ANJUKE_REAL
                trust = TRUST_L2
                is_synthetic = False

            row_count = get_row_count(csv_file)
            usable_price_rows, total_rows = get_usable_price_rate(csv_file, city) if dtype == "楼盘" else (0, row_count)
            usable_price_rate = usable_price_rows / total_rows if total_rows > 0 else 0
            geo_valid_rate = None

            records.append({
                "year": "2026",
                "city": city,
                "dtype": dtype,
                "path": str(csv_file.relative_to(VAULT)),
                "rows": row_count,
                "usable_price_rows": usable_price_rows if dtype == "楼盘" else None,
                "usable_price_rate": round(usable_price_rate, 3) if dtype == "楼盘" else None,
                "geo_valid_rate": geo_valid_rate,
                "source": source,
                "trust": trust,
                "is_synthetic": is_synthetic,
            })

    # ── 3. 全量新楼盘 2026新楼盘 ───────────────────────────────────────────
    new_loupan_dir = VAULT / "2026新楼盘"
    if new_loupan_dir.exists():
        for csv_file in new_loupan_dir.glob("*.csv"):
            stem = csv_file.stem
            city = None
            for candidate in ["三亚", "杭州", "上海", "青岛"]:
                if candidate in stem:
                    city = candidate
                    break

            if not city:
                continue

            dtype = "楼盘"
            source = SOURCE_ANJUKE_REAL
            trust = TRUST_L2
            is_synthetic = False

            row_count = get_row_count(csv_file)
            usable_price_rows, total_rows = get_usable_price_rate(csv_file, city)
            usable_price_rate = usable_price_rows / total_rows if total_rows > 0 else 0
            geo_valid_rate = None

            records.append({
                "year": "2026",
                "city": city,
                "dtype": dtype,
                "path": str(csv_file.relative_to(VAULT)),
                "rows": row_count,
                "usable_price_rows": usable_price_rows,
                "usable_price_rate": round(usable_price_rate, 3),
                "geo_valid_rate": geo_valid_rate,
                "source": source,
                "trust": trust,
                "is_synthetic": is_synthetic,
            })

    return records


def main():
    print("[...] Scanning Vault directory...")
    records = build_manifest()
    if not records:
        print("[ERR] No manifest records generated")
        return

    # 排序：year, city, dtype
    records = sorted(records, key=lambda r: (r["year"], r["city"], r["dtype"]))

    # 输出 CSV
    df = pd.DataFrame(records)
    csv_path = VAULT / "_manifest.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[OK] Manifest saved: {csv_path}")
    print(f"[OK] Total records: {len(records)}")

    # 输出 JSON
    json_path = VAULT / "_manifest.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"[OK] JSON saved: {json_path}")

    # 统计
    print()
    print("Summary:")
    print(f"  Total files: {len(records)}")
    print(f"  Synthetic: {sum(1 for r in records if r['is_synthetic'])}")
    print(f"  Real: {sum(1 for r in records if not r['is_synthetic'])}")
    print(f"  Trust L1: {sum(1 for r in records if r['trust'] == TRUST_L1)}")
    print(f"  Trust L2: {sum(1 for r in records if r['trust'] == TRUST_L2)}")
    print(f"  Trust L3: {sum(1 for r in records if r['trust'] == TRUST_L3)}")


if __name__ == "__main__":
    main()
