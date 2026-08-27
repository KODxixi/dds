#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 云端归一化 FC 函数 — TOS vault CSV → normalized Parquet + 数据溯源。

部署到火山引擎函数计算（FC），配置 TOS 事件触发器：
  - 触发源：dds-data-lake bucket，vault/ 前缀，ObjectCreated 事件
  - 运行时：Python 3.9+
  - 超时：300s
  - 内存：512MB+（DuckDB 需要足够内存做 CSV→Parquet 转换）

设计原则：
  - 读取 vault/ CSV → DuckDB 类型推断 → 写入 normalized/ Parquet
  - 同时更新 _meta/data_lineage.json 溯源记录
  - 处理空文件/损坏 CSV 优雅降级，不阻断 FC
  - 城市名从文件名提取，城市分区写入

用法（本地测试）：
    python fc_normalize.py --object-key "vault/2026新楼盘/新楼盘-三亚.csv"
    python fc_normalize.py --csv /tmp/test.csv --city 三亚  # 跳过 TOS 下载
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 路径设置 ──────────────────────────────────────────────────────
_FC_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FC_DIR.parents[1]
sys.path.insert(0, str(_FC_DIR))

import tos_client  # noqa: E402


# ── 配置 ──────────────────────────────────────────────────────────
NORMALIZED_PREFIX = "normalized/"
META_LINEAGE_KEY = "_meta/data_lineage.json"
FC_TIMEOUT = int(os.environ.get("DDS_FC_TIMEOUT", "300"))


# ═══════════════════════════════════════════════════════════════════
#  TOS 事件解析（与 fc_governance.py 共享逻辑）
# ═══════════════════════════════════════════════════════════════════

def parse_tos_event(event: dict) -> dict | None:
    """解析火山引擎 TOS 事件触发器传来的 event。"""
    records = event.get("Records", [])
    if records:
        rec = records[0]
        tos_info = rec.get("tos", {})
        bucket_info = tos_info.get("bucket", {})
        obj_info = tos_info.get("object", {})
        bucket_name = bucket_info.get("name", "")
        obj_key = obj_info.get("key", "")
        if bucket_name and obj_key:
            return {"bucket": bucket_name, "object_key": obj_key}

    bucket = event.get("bucket", "")
    obj_key = event.get("object_key", "")
    if bucket and obj_key:
        return {"bucket": bucket, "object_key": obj_key}

    return None


# ═══════════════════════════════════════════════════════════════════
#  TOS I/O 辅助
# ═══════════════════════════════════════════════════════════════════

def _ensure_tos_client() -> str:
    """仅从 veFaaS 运行时环境变量/角色配置初始化 TOS 客户端。"""
    tos_client.disable_proxy()
    cfg = tos_client.get_tos_config()
    bucket = cfg["bucket"]
    if not bucket:
        raise RuntimeError("TOS bucket 未配置（DDS_TOS_BUCKET）")
    return bucket


def _download_csv(bucket: str, object_key: str) -> Path:
    """从 TOS 下载 CSV 到临时文件。"""
    tmp = Path(tempfile.gettempdir()) / "dds_norm_input.csv"
    print(f"[fc_norm] 从 TOS 下载: {bucket}/{object_key} → {tmp}")
    ok = tos_client.download_one(bucket, object_key, tmp)
    if not ok:
        raise RuntimeError(f"TOS 下载失败: {bucket}/{object_key}")
    print(f"[fc_norm] 下载完成: {tmp.stat().st_size:,} bytes")
    return tmp


def _upload_file(bucket: str, object_key: str, local_path: Path) -> None:
    """上传本地文件到 TOS。"""
    import tos
    client = tos_client.get_thread_local_client()
    client.put_object_from_file(bucket, object_key, str(local_path))
    print(f"[fc_norm] 上传: {bucket}/{object_key}")


def _download_json(bucket: str, object_key: str) -> dict | None:
    """从 TOS 下载 JSON 文件。"""
    import tos
    try:
        client = tos_client.get_thread_local_client()
        resp = client.get_object(bucket, object_key)
        content = resp.read().decode("utf-8")
        return json.loads(content)
    except Exception:
        return None


def _upload_json(bucket: str, object_key: str, data: Any) -> None:
    """上传 JSON 到 TOS。"""
    import tos
    client = tos_client.get_thread_local_client()
    client.put_object(bucket, object_key,
                      content=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"[fc_norm] 上传: {bucket}/{object_key}")


def _extract_city_from_key(object_key: str) -> str:
    """从 TOS object key 提取城市名。"""
    import re
    stem = Path(object_key).stem
    m = re.search(r"新楼盘-(.+)$", stem)
    if m:
        return m.group(1)
    m = re.search(r"二手房小区-(.+)$", stem)
    if m:
        return m.group(1)
    return "unknown"


# ═══════════════════════════════════════════════════════════════════
#  CSV → Parquet 归一化
# ═══════════════════════════════════════════════════════════════════

def csv_to_parquet(csv_path: Path, pq_path: Path) -> dict[str, Any]:
    """使用 DuckDB 将 CSV 转换为 Parquet。

    自动类型推断，处理中文列名、混合类型列、大文件分块。

    返回 {"row_count": int, "col_count": int, "file_size_mb": float, "columns": [...]}
    """
    try:
        import duckdb
    except ImportError:
        raise RuntimeError(
            "DuckDB 未安装。FC 运行时需在 requirements.volcengine.txt 中添加 duckdb。"
            "\n  pip install duckdb"
        )

    # 确保输出目录存在
    pq_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[fc_norm] CSV → Parquet: {csv_path} → {pq_path}")
    print(f"[fc_norm] CSV 大小: {csv_path.stat().st_size / 1024 / 1024:.1f} MB")

    con = duckdb.connect()
    try:
        # 读取 CSV（sniff_csv 自动检测类型）
        csv_str = str(csv_path).replace("\\", "/")
        pq_str = str(pq_path).replace("\\", "/")

        # 先获取列信息
        col_info = con.execute(f"""
            SELECT column_name, column_type
            FROM (DESCRIBE SELECT * FROM read_csv_auto('{csv_str}', ALL_VARCHAR=0))
        """).fetchall()

        columns = [{"name": c[0], "type": c[1]} for c in col_info]
        col_count = len(columns)

        # 执行转换
        row_count = con.execute(f"""
            COPY (
                SELECT * FROM read_csv_auto('{csv_str}', ALL_VARCHAR=0)
            ) TO '{pq_str}' (FORMAT 'parquet', COMPRESSION 'zstd')
        """)

        # 获取实际行数
        row_count = con.execute(f"SELECT COUNT(*) FROM '{pq_str}'").fetchone()[0]

        file_size_mb = pq_path.stat().st_size / 1024 / 1024

        result = {
            "row_count": row_count,
            "col_count": col_count,
            "file_size_mb": round(file_size_mb, 2),
            "columns": columns,
        }
        print(f"[fc_norm] 转换完成: {row_count} 行 x {col_count} 列 → {file_size_mb:.1f} MB Parquet")
        return result

    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════════
#  数据溯源
# ═══════════════════════════════════════════════════════════════════

def update_lineage(bucket: str, city: str, object_key: str,
                   norm_key: str, norm_info: dict) -> None:
    """更新 _meta/data_lineage.json 溯源记录。

    追加一条记录，保留历史全部记录。
    """
    lineage = _download_json(bucket, META_LINEAGE_KEY) or {"entries": []}

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": {
            "bucket": bucket,
            "key": object_key,
            "format": "csv",
        },
        "data_origin": {
            "pipeline": "unknown",  # 由 CSV 文件名推断，或由上游注入
            "original_source": "unknown",  # 如 "landchina" / "fang.com" / "purchased" / "doubao_trae"
            "note": "fc_normalize 无法自动推断数据原始来源，需上游在 CSV 首行注释或 TOS metadata 中标注",
        },
        "target": {
            "bucket": bucket,
            "key": norm_key,
            "format": "parquet",
        },
        "city": city,
        "stats": {
            "row_count": norm_info["row_count"],
            "col_count": norm_info["col_count"],
            "file_size_mb": norm_info["file_size_mb"],
        },
        "schema_version": "2.0",
    }

    lineage["entries"].append(entry)
    # 限制最多保留 1000 条记录
    if len(lineage["entries"]) > 1000:
        lineage["entries"] = lineage["entries"][-1000:]

    _upload_json(bucket, META_LINEAGE_KEY, lineage)
    print(f"[fc_norm] 溯源已更新: {META_LINEAGE_KEY} ({len(lineage['entries'])} 条记录)")


# ═══════════════════════════════════════════════════════════════════
#  FC Handler（火山引擎 FC 入口）
# ═══════════════════════════════════════════════════════════════════

def handler(event: dict, context: Any = None) -> dict[str, Any]:
    """火山引擎 FC 函数入口。

    由 TOS 事件触发，解析 event 中的 object key，
    下载 CSV → DuckDB 转 Parquet → 上传到 normalized/ → 更新溯源。

    返回:
        {"statusCode": 200, "body": {...}}
    """
    print(f"[fc_norm] FC 触发，event: {json.dumps(event, ensure_ascii=False, default=str)[:500]}")

    try:
        parsed = parse_tos_event(event)
        if not parsed:
            msg = f"无法解析 TOS 事件: {json.dumps(event, ensure_ascii=False)[:200]}"
            print(f"[fc_norm] {msg}")
            return {"statusCode": 400, "body": json.dumps({"error": msg})}

        bucket = parsed["bucket"]
        object_key = parsed["object_key"]
        print(f"[fc_norm] 解析成功: bucket={bucket}, key={object_key}")

        # 仅处理 CSV 文件
        if not object_key.endswith(".csv"):
            print(f"[fc_norm] 跳过非 CSV 文件: {object_key}")
            return {"statusCode": 200, "body": json.dumps({"skipped": True, "reason": "not_csv"})}

        # 仅处理 vault/ 前缀
        if not object_key.startswith("vault/"):
            print(f"[fc_norm] 跳过非 vault 文件: {object_key}")
            return {"statusCode": 200, "body": json.dumps({"skipped": True, "reason": "not_vault"})}

        city = _extract_city_from_key(object_key)
        print(f"[fc_norm] 城市: {city}")

        # 下载 CSV
        csv_path = _download_csv(bucket, object_key)

        # 生成归一化输出路径
        # vault/2026新楼盘/新楼盘-三亚.csv → normalized/2026新楼盘/新楼盘-三亚.parquet
        stem = Path(object_key).stem
        # 使用 posix 路径分隔符，确保跨平台一致（FC 运行在 Linux，本地测试在 Windows）
        parent = Path(object_key).parent.as_posix()  # vault/2026新楼盘
        norm_parent = parent.replace("vault/", f"{NORMALIZED_PREFIX}", 1)
        norm_key = f"{norm_parent}/{stem}.parquet"

        # 本地临时 Parquet 路径
        tmp_pq = Path(tempfile.gettempdir()) / f"dds_norm_{stem}.parquet"

        # CSV → Parquet
        try:
            norm_info = csv_to_parquet(csv_path, tmp_pq)
        except Exception as exc:
            print(f"[fc_norm] 归一化失败: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            return {
                "statusCode": 500,
                "body": json.dumps({"error": f"Normalization failed: {type(exc).__name__}: {exc}"},
                                   ensure_ascii=False),
            }

        # 上传 Parquet 到 TOS
        _upload_file(bucket, norm_key, tmp_pq)

        # 更新溯源
        try:
            update_lineage(bucket, city, object_key, norm_key, norm_info)
        except Exception as exc:
            print(f"[fc_norm] 溯源更新失败（非致命）: {type(exc).__name__}: {exc}")

        # 清理临时文件
        for tmp in [csv_path, tmp_pq]:
            try:
                tmp.unlink()
            except Exception:
                pass

        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "ok",
                "city": city,
                "source_key": object_key,
                "target_key": norm_key,
                "stats": norm_info,
            }, ensure_ascii=False),
        }

    except Exception as exc:
        print(f"[fc_norm] 致命错误: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
        }


# ═══════════════════════════════════════════════════════════════════
#  CLI（本地测试）
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DDS 云端归一化 FC 函数 — 本地测试入口"
    )
    parser.add_argument("--object-key", type=str,
                        help="TOS object key（模拟 TOS 事件触发）")
    parser.add_argument("--csv", type=str,
                        help="本地 CSV 路径（跳过 TOS 下载，直接归一化）")
    parser.add_argument("--city", type=str, default=None,
                        help="城市名（默认从文件名提取）")
    parser.add_argument("--out", type=str, default=None,
                        help="输出 Parquet 路径（本地模式，默认写入临时目录）")
    args = parser.parse_args()

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"错误：文件不存在 {csv_path}")
            sys.exit(1)
        city = args.city or _extract_city_from_key(csv_path.name)
        pq_path = Path(args.out) if args.out else \
            Path(tempfile.gettempdir()) / f"{csv_path.stem}.parquet"
        print(f"[fc_norm] 本地模式: {csv_path} → {pq_path} (city={city})")
        try:
            info = csv_to_parquet(csv_path, pq_path)
            print(f"\n[fc_norm] 完成: {info['row_count']} 行 x {info['col_count']} 列")
            print(f"[fc_norm] 输出: {pq_path} ({info['file_size_mb']} MB)")
            if args.out is None:
                # 上传到 TOS（如果配置了）
                try:
                    bucket = _ensure_tos_client()
                    norm_key = f"{NORMALIZED_PREFIX}test/{csv_path.stem}.parquet"
                    _upload_file(bucket, norm_key, pq_path)
                    print(f"[fc_norm] 已上传到 TOS: {bucket}/{norm_key}")
                except Exception as exc:
                    print(f"[fc_norm] TOS 上传跳过: {type(exc).__name__}: {exc}")
        except Exception as exc:
            print(f"错误: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            sys.exit(1)
        return

    if args.object_key:
        bucket = _ensure_tos_client()
        event = {
            "Records": [{
                "tos": {
                    "bucket": {"name": bucket},
                    "object": {"key": args.object_key},
                },
                "eventName": "tos:ObjectCreated:Put",
            }],
        }
        result = handler(event)
        print(f"\n[fc_norm] Handler 返回: {json.dumps(result, ensure_ascii=False, indent=2)}")
        sys.exit(0 if result.get("statusCode") == 200 else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
