"""
T3 数据溯源 manifest 生成器。

输出 Vault/_manifest.csv、_manifest.json、_manifest.parquet 与
Vault/2026新楼盘/_cities_index.csv。所有输出均由当前 CSV 实时计算，写入时使用
同目录临时文件原子替换；``--check`` 只检查派生目录是否过期。
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

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
SOURCE_PURCHASED_OPENING_YEAR_BACKFILL = "purchased_opening_year_backfill"
SOURCE_PURCHASED_NEWHOUSE_POOL = "purchased_newhouse_pool"
SOURCE_MIXED_REAL_SOURCES = "mixed_real_sources"
SOURCE_UNMARKED_REAL = "unmarked_real"

REAL_OPENING_YEAR_MARKER = "来源:购买库按真实开盘年"
PURCHASED_NEWHOUSE_MARKER = "来源:购买数据"
MANAGED_CITIES = ("三亚", "杭州", "上海", "青岛", "济南")
KNOWN_ANJUKE_EMPTY_CITIES = frozenset({"三亚", "上海", "杭州", "青岛"})
MANIFEST_COLUMNS = [
    "year",
    "city",
    "dtype",
    "path",
    "rows",
    "usable_price_rows",
    "usable_price_rate",
    "geo_valid_rate",
    "source",
    "trust",
    "is_synthetic",
]
CITIES_INDEX_COLUMNS = ["城市", "新盘项目数", "有parquet"]
_ROW_COUNT_CACHE: dict[str, int] = {}
_REAL_SOURCE_CACHE: dict[tuple[str, str], str] = {}


def _duckdb_path(csv_path: Path) -> str:
    """返回可安全放入 DuckDB 字符串字面量的路径。"""
    return str(csv_path).replace("\\", "/").replace("'", "''")


def _row_count_cache_key(csv_path: Path) -> str:
    return str(csv_path.resolve()).casefold()


def _try_get_usable_price_rate(csv_path: Path) -> tuple[int, int] | None:
    """读取标准楼盘价格字段；非楼盘 CSV 返回 ``None``。"""
    try:
        columns = pd.read_csv(csv_path, nrows=0).columns
        if "参考价格" not in columns or "最新价格" not in columns:
            return None
        result = duckdb.query(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN "参考价格" LIKE '%元/㎡%'
                         AND TRY_CAST("最新价格" AS DOUBLE) IS NOT NULL
                    THEN 1 ELSE 0 END) AS usable
            FROM read_csv_auto('{_duckdb_path(csv_path)}', header=true)
        """).fetchall()
        if result:
            total, usable = result[0]
            total = int(total or 0)
            _ROW_COUNT_CACHE[_row_count_cache_key(csv_path)] = total
            return int(usable or 0), total
    except Exception as exc:
        print(f"[WARN] get_usable_price_rate error: {csv_path}: {exc}")
    return None


def get_usable_price_rate(csv_path: Path, city: str = "") -> tuple[int, int]:
    """
    计算可用单价行数 / 总行数
    口径：参考价格 LIKE '%元/㎡%' 且 最新价格 可转 double
    """
    del city  # 保留旧调用签名；计算口径与城市无关。
    return _try_get_usable_price_rate(csv_path) or (0, 0)


def get_row_count(csv_path: Path) -> int:
    """获取 CSV 行数"""
    cache_key = _row_count_cache_key(csv_path)
    if cache_key in _ROW_COUNT_CACHE:
        return _ROW_COUNT_CACHE[cache_key]
    try:
        result = duckdb.query(
            f"SELECT COUNT(*) AS cnt FROM read_csv_auto('{_duckdb_path(csv_path)}', header=true)"
        ).fetchall()
        row_count = int(result[0][0]) if result else 0
        _ROW_COUNT_CACHE[cache_key] = row_count
        return row_count
    except Exception as exc:
        print(f"[WARN] get_row_count error: {csv_path}: {exc}")
        return 0


def _manifest_path(path: Path, vault: Path) -> str:
    return str(path.relative_to(vault))


def _path_key(value: object) -> str:
    return str(value or "").strip().replace("/", "\\").casefold()


def _path_in_vault(value: object, vault: Path) -> Path | None:
    """把 manifest 相对路径解析到 Vault，并拒绝绝对路径和目录穿越。"""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
        return None
    candidate = vault.joinpath(*pure.parts).resolve()
    try:
        candidate.relative_to(vault.resolve())
    except ValueError:
        return None
    return candidate


def _optional_float(value: object) -> float | None:
    if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    return int(number) if number is not None else None


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "是"}


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """统一 CSV/JSON 读入后的标量类型与字段顺序。"""
    return {
        "year": str(record.get("year", "")),
        "city": str(record.get("city", "")),
        "dtype": str(record.get("dtype", "")),
        "path": str(record.get("path", "")).replace("/", "\\"),
        "rows": _optional_int(record.get("rows")) or 0,
        "usable_price_rows": _optional_int(record.get("usable_price_rows")),
        "usable_price_rate": _optional_float(record.get("usable_price_rate")),
        "geo_valid_rate": _optional_float(record.get("geo_valid_rate")),
        "source": str(record.get("source", "")),
        "trust": str(record.get("trust", "")),
        "is_synthetic": _bool_value(record.get("is_synthetic")),
    }


def _load_existing_manifest(vault: Path) -> list[dict[str, Any]]:
    """读取旧 manifest，供保留生成器职责外的专用记录。"""
    csv_path = vault / "_manifest.csv"
    if csv_path.is_file():
        try:
            frame = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
            return frame.to_dict(orient="records")
        except Exception as exc:
            print(f"[WARN] Existing CSV manifest unreadable: {exc}")

    json_path = vault / "_manifest.json"
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, list) else []
        except Exception as exc:
            print(f"[WARN] Existing JSON manifest unreadable: {exc}")
    return []


def _detect_city(stem: str) -> str | None:
    return next((city for city in MANAGED_CITIES if city in stem), None)


def _is_managed_path(value: object) -> bool:
    """判断路径是否属于本生成器职责，避免把派生副本作为专用记录保留。"""
    raw = str(value or "").replace("\\", "/")
    parts = PurePosixPath(raw).parts
    if len(parts) != 2:
        return False
    parent, name = parts
    if not name.casefold().endswith(".csv"):
        return False
    stem = Path(name).stem
    if parent.endswith("年"):
        year_text = parent.removesuffix("年")
        if year_text.isdigit() and 1995 <= int(year_text) <= 2026:
            return _detect_city(stem) is not None
    if parent == "2026新楼盘" and stem.startswith("新楼盘-"):
        city = stem.removeprefix("新楼盘-")
        # 派生副本也属于本生成器的排除职责，不能从旧 manifest 作为专用记录回流。
        return bool(city)
    return False


def _history_is_real_backfill(csv_path: Path, row_count: int) -> bool:
    """空年度视图或每行带真实开盘年回填标记时，判为非合成数据。"""
    if row_count == 0:
        return True
    try:
        reviews = pd.read_csv(
            csv_path,
            usecols=["业内评价"],
            dtype=str,
            keep_default_na=False,
        )["业内评价"]
    except (OSError, UnicodeError, ValueError, KeyError):
        return False
    return len(reviews) == row_count and bool(reviews.str.startswith(REAL_OPENING_YEAR_MARKER).all())


def _classify_real_loupan_source(csv_path: Path, city: str) -> str:
    """按 ``业内评价`` 的文件级标记判定当年真实楼盘来源。"""
    cache_key = (_row_count_cache_key(csv_path), city)
    if cache_key in _REAL_SOURCE_CACHE:
        return _REAL_SOURCE_CACHE[cache_key]

    try:
        reviews = pd.read_csv(
            csv_path,
            usecols=["业内评价"],
            dtype=str,
            keep_default_na=False,
        )["业内评价"].astype(str).str.strip()
    except (OSError, UnicodeError, ValueError, KeyError):
        reviews = pd.Series(dtype=str)

    if reviews.empty or bool(reviews.eq("").all()):
        source = (
            SOURCE_ANJUKE_REAL
            if city in KNOWN_ANJUKE_EMPTY_CITIES
            else SOURCE_UNMARKED_REAL
        )
    elif bool(reviews.str.startswith(PURCHASED_NEWHOUSE_MARKER).all()):
        source = SOURCE_PURCHASED_NEWHOUSE_POOL
    else:
        source = SOURCE_MIXED_REAL_SOURCES

    _REAL_SOURCE_CACHE[cache_key] = source
    return source


def _record_for_file(
    csv_file: Path,
    *,
    vault: Path,
    year: str,
    city: str,
    dtype: str,
    source: str,
    trust: str,
    is_synthetic: bool,
    row_count: int | None = None,
) -> dict[str, Any]:
    if dtype == "楼盘":
        price_metrics = _try_get_usable_price_rate(csv_file)
        if price_metrics is None:
            row_count = get_row_count(csv_file) if row_count is None else row_count
            usable_price_rows, total_rows = 0, row_count
        else:
            usable_price_rows, total_rows = price_metrics
            row_count = total_rows if row_count is None else row_count
        usable_price_rate = usable_price_rows / total_rows if total_rows else 0
    else:
        row_count = get_row_count(csv_file) if row_count is None else row_count
        usable_price_rows = None
        usable_price_rate = None
    return {
        "year": year,
        "city": city,
        "dtype": dtype,
        "path": _manifest_path(csv_file, vault),
        "rows": row_count,
        "usable_price_rows": usable_price_rows,
        "usable_price_rate": round(usable_price_rate, 3) if usable_price_rate is not None else None,
        "geo_valid_rate": None,
        "source": source,
        "trust": trust,
        "is_synthetic": is_synthetic,
    }


def _refresh_preserved_record(record: dict[str, Any], vault: Path) -> dict[str, Any] | None:
    normalized = _normalize_record(record)
    csv_path = _path_in_vault(normalized["path"], vault)
    if csv_path is None or not csv_path.is_file():
        return None
    normalized["path"] = _manifest_path(csv_path, vault)
    normalized["rows"] = get_row_count(csv_path)

    price_metrics = _try_get_usable_price_rate(csv_path)
    if price_metrics is not None:
        usable, total = price_metrics
        normalized["usable_price_rows"] = usable
        normalized["usable_price_rate"] = round(usable / total, 3) if total else 0
    return normalized


def build_manifest(vault: Path | None = None) -> list[dict[str, Any]]:
    """
    枚举 Vault 目录，为每个数据文件生成 manifest 行
    按规则自动打标 (source, trust, is_synthetic)
    """
    vault = Path(vault or VAULT)
    _ROW_COUNT_CACHE.clear()
    _REAL_SOURCE_CACHE.clear()
    records: list[dict[str, Any]] = []

    # ── 1. 历史数据 1995–2025年 ──────────────────────────────────────────
    for year_dir in sorted(vault.glob("????年"), key=lambda path: path.name):
        year_name = year_dir.name.replace("年", "")
        if not year_name.isdigit() or int(year_name) > 2025 or int(year_name) < 1995:
            continue
        year = year_name

        for csv_file in sorted(year_dir.glob("*.csv"), key=lambda path: path.name):
            # 识别城市
            stem = csv_file.stem
            city = _detect_city(stem)

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
                row_count = get_row_count(csv_file)
                is_real_backfill = _history_is_real_backfill(csv_file, row_count)
                source = (
                    SOURCE_PURCHASED_OPENING_YEAR_BACKFILL
                    if is_real_backfill
                    else SOURCE_CLONED_SYNTHETIC
                )
                trust = TRUST_L2 if is_real_backfill else TRUST_L3
                is_synthetic = not is_real_backfill

            records.append(
                _record_for_file(
                    csv_file,
                    vault=vault,
                    year=year,
                    city=city,
                    dtype=dtype,
                    source=source,
                    trust=trust,
                    is_synthetic=is_synthetic,
                    row_count=row_count if dtype == "楼盘" else None,
                )
            )

    # ── 2. 当年数据 2026年 ────────────────────────────────────────────────
    year_2026_dir = vault / "2026年"
    if year_2026_dir.exists():
        for csv_file in sorted(year_2026_dir.glob("*.csv"), key=lambda path: path.name):
            stem = csv_file.stem
            city = _detect_city(stem)

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
                source = _classify_real_loupan_source(csv_file, city)
                trust = TRUST_L2
                is_synthetic = False

            records.append(
                _record_for_file(
                    csv_file,
                    vault=vault,
                    year="2026",
                    city=city,
                    dtype=dtype,
                    source=source,
                    trust=trust,
                    is_synthetic=is_synthetic,
                )
            )

    # ── 3. 全量新楼盘 2026新楼盘 ───────────────────────────────────────────
    new_loupan_dir = vault / "2026新楼盘"
    if new_loupan_dir.exists():
        for csv_file in sorted(new_loupan_dir.glob("新楼盘-*.csv"), key=lambda path: path.name):
            stem = csv_file.stem
            city = stem.removeprefix("新楼盘-")

            if not city or "_" in city:
                continue

            dtype = "楼盘"
            source = _classify_real_loupan_source(csv_file, city)
            trust = TRUST_L2
            is_synthetic = False

            records.append(
                _record_for_file(
                    csv_file,
                    vault=vault,
                    year="2026",
                    city=city,
                    dtype=dtype,
                    source=source,
                    trust=trust,
                    is_synthetic=is_synthetic,
                )
            )

    # ── 4. 保留本生成器职责外、且文件仍存在的专用记录 ─────────────────────
    generated_keys = {_path_key(record["path"]) for record in records}
    for existing in _load_existing_manifest(vault):
        key = _path_key(existing.get("path"))
        if not key or key in generated_keys or _is_managed_path(existing.get("path")):
            continue
        refreshed = _refresh_preserved_record(existing, vault)
        if refreshed is not None:
            records.append(refreshed)
            generated_keys.add(key)

    return sorted(records, key=_manifest_sort_key)


def _manifest_sort_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("year", "")),
        str(record.get("city", "")),
        str(record.get("dtype", "")),
        _path_key(record.get("path")),
    )


def build_cities_index(vault: Path | None = None) -> list[dict[str, Any]]:
    """按实时城市 CSV 构建新楼盘索引，排除 ``_*`` 派生文件。"""
    vault = Path(vault or VAULT)
    pool = vault / "2026新楼盘"
    rows: list[dict[str, Any]] = []
    if not pool.is_dir():
        return rows

    for csv_file in sorted(pool.glob("新楼盘-*.csv"), key=lambda path: path.name):
        city = csv_file.stem.removeprefix("新楼盘-")
        if not city or "_" in city:
            continue
        rows.append(
            {
                "城市": city,
                "新盘项目数": get_row_count(csv_file),
                "有parquet": "是" if csv_file.with_suffix(".parquet").is_file() else "否",
            }
        )
    return sorted(rows, key=lambda row: (-int(row["新盘项目数"]), str(row["城市"])))


def _csv_bytes(records: list[dict[str, Any]], columns: list[str]) -> bytes:
    frame = pd.DataFrame(records, columns=columns)
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def _expected_outputs(vault: Path) -> tuple[list[dict[str, Any]], dict[Path, bytes]]:
    # 缓存仅服务于单次生成内的 manifest/index 复用；每次调用都必须重新观察源文件。
    _ROW_COUNT_CACHE.clear()
    records = build_manifest(vault)
    cities_index = build_cities_index(vault)
    outputs = {
        vault / "_manifest.csv": _csv_bytes(records, MANIFEST_COLUMNS),
        vault / "_manifest.json": (
            json.dumps(records, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
        vault / "2026新楼盘" / "_cities_index.csv": _csv_bytes(
            cities_index, CITIES_INDEX_COLUMNS
        ),
    }
    return records, outputs


def _normalized_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((_normalize_record(record) for record in records), key=_manifest_sort_key)


def _parquet_is_current(path: Path, expected_records: list[dict[str, Any]]) -> bool:
    """语义比较 Parquet，避免 ``--check`` 为比对而创建任何临时文件。"""
    if not path.is_file():
        return False
    try:
        frame = duckdb.query(
            f"SELECT * FROM read_parquet('{_duckdb_path(path)}')"
        ).to_df()
    except Exception:
        return False
    if list(frame.columns) != MANIFEST_COLUMNS:
        return False
    actual = _normalized_records(frame.to_dict(orient="records"))
    expected = _normalized_records(expected_records)
    return actual == expected


def _stage_bytes(path: Path, content: bytes) -> Path:
    """在目标同目录完整暂存字节，尚不替换目标。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary_path
    except Exception:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise


def _stage_parquet(path: Path, records: list[dict[str, Any]]) -> Path:
    """由 DuckDB 在目标同目录完整暂存 Parquet，尚不替换目标。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    connection = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
        # DuckDB 的 COPY 负责创建文件；先移除刚刚用于获取唯一名称的空占位。
        temporary_path.unlink()
        frame = pd.DataFrame(records, columns=MANIFEST_COLUMNS)
        connection = duckdb.connect()
        connection.register("manifest_records", frame)
        connection.execute(
            f"COPY (SELECT * FROM manifest_records) TO '{_duckdb_path(temporary_path)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        connection.close()
        connection = None
        return temporary_path
    except Exception:
        if connection is not None:
            connection.close()
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise


def _replace_staged_outputs(
    outputs: dict[Path, bytes],
    parquet_path: Path,
    records: list[dict[str, Any]],
) -> None:
    """先完整暂存四份资产；全部成功后才逐份原子替换。"""
    staged: list[tuple[Path, Path]] = []
    try:
        for path, content in outputs.items():
            staged.append((_stage_bytes(path, content), path))
        staged.append((_stage_parquet(parquet_path, records), parquet_path))

        for temporary_path, target_path in staged:
            os.replace(temporary_path, target_path)
    finally:
        for temporary_path, _ in staged:
            if temporary_path.exists():
                temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成或检查 DDS 数据溯源派生目录")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查 manifest 和城市索引是否与当前 CSV 一致，不写文件",
    )
    args = parser.parse_args(argv)

    print("[...] Scanning Vault directory...")
    vault = Path(VAULT)
    records, outputs = _expected_outputs(vault)
    if not records:
        print("[ERR] No manifest records generated")
        return 2

    if args.check:
        stale = [
            path
            for path, expected in outputs.items()
            if not path.is_file() or path.read_bytes() != expected
        ]
        parquet_path = vault / "_manifest.parquet"
        if not _parquet_is_current(parquet_path, records):
            stale.append(parquet_path)
        if stale:
            for path in stale:
                print(f"[STALE] {path}")
            return 1
        print("[OK] Manifest and cities index are current")
        return 0

    parquet_path = vault / "_manifest.parquet"
    _replace_staged_outputs(outputs, parquet_path, records)
    for path in outputs:
        print(f"[OK] Saved: {path}")
    print(f"[OK] Saved: {parquet_path}")
    print(f"[OK] Total records: {len(records)}")

    # 统计
    print()
    print("Summary:")
    print(f"  Total files: {len(records)}")
    print(f"  Synthetic: {sum(1 for r in records if r['is_synthetic'])}")
    print(f"  Real: {sum(1 for r in records if not r['is_synthetic'])}")
    print(f"  Trust L1: {sum(1 for r in records if r['trust'] == TRUST_L1)}")
    print(f"  Trust L2: {sum(1 for r in records if r['trust'] == TRUST_L2)}")
    print(f"  Trust L3: {sum(1 for r in records if r['trust'] == TRUST_L3)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
