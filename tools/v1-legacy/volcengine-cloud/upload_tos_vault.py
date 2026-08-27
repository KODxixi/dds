#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传 DDS Vault 数据到火山引擎 TOS 对象存储。

参考 ArchLib upload_tos_seed.py 模式：
- 扫描 Vault/ 目录下所有数据文件（CSV / parquet / json）
- 为每个文件生成 object_key，保留目录结构（如 vault/2026新楼盘/新楼盘-三亚.csv）
- 使用 ThreadPoolExecutor 并发上传，thread-local TOS 客户端复用连接
- 断点续传：已上传的 object_key 记录在状态 JSONL 中，后续运行自动跳过
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ── 定位项目根目录 + 引入同目录 tos_client ──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.path.dirname(__file__))
import tos_client  # noqa: E402

# ── 常量 ──
VAULT_DIR = PROJECT_ROOT / "Vault"
DEFAULT_ENV_FILE = PROJECT_ROOT / "cloud" / "volcengine" / ".env.volcengine"
DEFAULT_STATUS_FILE = PROJECT_ROOT / "cloud" / "volcengine" / "status" / "vault_upload_status.jsonl"

# 需要上传的数据文件扩展名
DATA_EXTENSIONS = {".csv", ".parquet", ".json", ".jsonl"}


def scan_vault_files(vault_dir: Path) -> list[tuple[Path, str]]:
    """扫描 Vault 目录下所有数据文件，返回 (local_path, object_key) 列表。

    object_key 格式：vault/<相对路径>，保留原始目录结构。
    例：Vault/2026新楼盘/新楼盘-三亚.csv → vault/2026新楼盘/新楼盘-三亚.csv
    """
    if not vault_dir.exists():
        return []
    results: list[tuple[Path, str]] = []
    for path in sorted(vault_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in DATA_EXTENSIONS:
            continue
        # 跳过 __pycache__ 等非数据目录
        if "__pycache__" in path.parts:
            continue
        # 跳过隐藏文件（.gitkeep 等）
        if path.name.startswith("."):
            continue
        rel = path.relative_to(vault_dir).as_posix()
        object_key = f"vault/{rel}"
        results.append((path, object_key))
    return results


def build_rows(files: list[tuple[Path, str]]) -> list[dict[str, Any]]:
    """构建上传行记录列表。"""
    rows: list[dict[str, Any]] = []
    for local_path, object_key in files:
        try:
            byte_size = local_path.stat().st_size
        except OSError:
            byte_size = 0
        rows.append({
            "local_path": str(local_path),
            "object_key": object_key,
            "byte_size": byte_size,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="上传 DDS Vault 数据到火山引擎 TOS（支持断点续传）。",
    )
    parser.add_argument("--vault-dir", default=str(VAULT_DIR),
                        help="Vault 数据根目录，默认为项目根下 Vault/")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="环境变量文件路径")
    parser.add_argument("--status-file", default=str(DEFAULT_STATUS_FILE),
                        help="JSONL 状态文件路径（断点续传用）")
    parser.add_argument("--start", type=int, default=0,
                        help="零起始偏移量，跳过前 N 个文件")
    parser.add_argument("--limit", type=int, default=0,
                        help="最多处理 N 个文件，0 表示全部")
    parser.add_argument("--workers", type=int, default=20,
                        help="并发上传线程数")
    parser.add_argument("--retries", type=int, default=3,
                        help="单个文件最大重试次数")
    parser.add_argument("--head-before-put", action="store_true",
                        help="上传前先 HEAD 远端，已存在则跳过")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划，不实际上传")
    args = parser.parse_args()

    if args.start < 0 or args.limit < 0 or args.workers <= 0:
        print("ERROR: --start/--limit 不能为负，--workers 必须为正。")
        return 2

    # 加载环境变量
    tos_client.load_dotenv(Path(args.env_file))
    tos_client.disable_proxy()

    cfg = tos_client.get_tos_config()
    bucket = cfg["bucket"]
    if not bucket or not cfg["endpoint"] or not cfg["ak"] or not cfg["sk"]:
        print("ERROR: TOS 环境变量不完整，请检查 .env.volcengine")
        return 2

    vault_dir = Path(args.vault_dir)
    if not vault_dir.is_absolute():
        vault_dir = PROJECT_ROOT / vault_dir
    if not vault_dir.exists():
        print(f"ERROR: Vault 目录不存在: {vault_dir}")
        return 2

    # 扫描文件
    files = scan_vault_files(vault_dir)
    total_files = len(files)
    if args.start:
        files = files[args.start:]
    if args.limit > 0:
        files = files[:args.limit]

    status_file = Path(args.status_file)
    if not status_file.is_absolute():
        status_file = PROJECT_ROOT / status_file

    done_keys = tos_client.load_done_keys(status_file)

    print(f"Vault 目录: {vault_dir}")
    print(f"扫描到数据文件总数: {total_files}")
    print(f"本次选取: {len(files)} (start={args.start}, limit={args.limit})")
    print(f"并发线程数: {args.workers}")
    print(f"状态文件: {status_file}")
    print(f"已完成（跳过）: {len(done_keys)}")

    rows = build_rows(files)

    if args.dry_run:
        print("\n--- DRY RUN ---")
        for row in rows[:10]:
            print(f"  {row['local_path']} -> tos://{bucket}/{row['object_key']}")
        if len(rows) > 10:
            print(f"  ... 还有 {len(rows) - 10} 个文件")
        print("Dry run 模式，未上传任何对象。")
        return 0

    # 预过滤：跳过已完成和缺失文件
    to_upload: list[tuple[dict, int]] = []
    for local_idx, row in enumerate(rows, 1):
        global_idx = args.start + local_idx
        local_path = Path(row.get("local_path") or "")
        object_key = row.get("object_key", "")
        if not local_path.exists() or not object_key:
            result = {"status": "missing_local", "object_key": object_key,
                       "path": str(local_path)}
            tos_client.write_status(status_file, result)
            print(f"SKIP MISSING {global_idx}/{total_files} {object_key or local_path}", flush=True)
            continue
        if object_key in done_keys:
            continue
        to_upload.append((row, global_idx))

    print(f"待上传: {len(to_upload)}（跳过 {len(rows) - len(to_upload)} 已完成/缺失）")

    uploaded = 0
    skipped_remote = 0
    failed_count = 0
    max_retries = max(1, args.retries)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for row, gidx in to_upload:
            fut = pool.submit(
                tos_client.upload_one,
                bucket, row, gidx, total_files,
                status_file, done_keys,
                max_retries, args.head_before_put,
            )
            futures[fut] = (row, gidx)

        for fut in as_completed(futures):
            result = fut.result()
            if result.get("status") == "uploaded":
                uploaded += 1
                done_keys.add(result["object_key"])
            elif result.get("status") == "exists_remote":
                skipped_remote += 1
                done_keys.add(result["object_key"])
            elif result.get("status") == "failed":
                failed_count += 1

    print(
        f"完成。uploaded={uploaded}, skipped_remote={skipped_remote}, "
        f"failed={failed_count}, total_selected={len(rows)}"
    )
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
