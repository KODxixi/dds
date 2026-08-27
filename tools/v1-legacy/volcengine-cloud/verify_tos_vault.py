#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HEAD 验证已上传到 TOS 的 DDS Vault 对象是否存在。

读取 upload_status.jsonl 获取已上传的 object_key 列表，
对每个 key 执行 HEAD 请求验证存在性，结果记录到 verify_status.jsonl。
支持断点续传：已标记 verified 的 key 自动跳过。
"""
from __future__ import annotations

import argparse
import json
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
DEFAULT_ENV_FILE = PROJECT_ROOT / "cloud" / "volcengine" / ".env.volcengine"
DEFAULT_UPLOAD_STATUS = PROJECT_ROOT / "cloud" / "volcengine" / "status" / "vault_upload_status.jsonl"
DEFAULT_VERIFY_STATUS = PROJECT_ROOT / "cloud" / "volcengine" / "status" / "vault_verify_status.jsonl"


def iter_jsonl(path: Path):
    """逐行读取 JSONL 文件。"""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def latest_uploaded_keys(path: Path) -> list[str]:
    """从 upload_status.jsonl 中提取已上传的 object_key 列表（去重、保序）。

    状态为 uploaded 或 exists_remote 的记录视为已上传。
    """
    if not path.exists():
        return []
    seen: set[str] = set()
    keys: list[str] = []
    for row in iter_jsonl(path):
        if row.get("status") in {"uploaded", "exists_remote"} and row.get("object_key"):
            key = str(row["object_key"])
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def load_verified_keys(path: Path) -> set[str]:
    """从 verify_status.jsonl 中提取已验证的 object_key 集合（断点续传用）。"""
    if not path.exists():
        return set()
    done: set[str] = set()
    for row in iter_jsonl(path):
        if row.get("status") == "verified" and row.get("object_key"):
            done.add(str(row["object_key"]))
    return done


def verify_worker(
    bucket: str,
    object_key: str,
    global_idx: int,
    total: int,
    status_file: Path,
) -> dict[str, Any]:
    """单个 HEAD 验证工作函数（委托给 tos_client.head_verify_one）。"""
    return tos_client.head_verify_one(bucket, object_key, global_idx, total, status_file)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HEAD 验证已上传到 TOS 的 DDS Vault 对象。",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="环境变量文件路径")
    parser.add_argument("--upload-status", default=str(DEFAULT_UPLOAD_STATUS),
                        help="上传状态 JSONL 文件路径（读取已上传 object_key）")
    parser.add_argument("--verify-status", default=str(DEFAULT_VERIFY_STATUS),
                        help="验证状态 JSONL 文件路径（记录验证结果）")
    parser.add_argument("--start", type=int, default=0,
                        help="零起始偏移量，跳过前 N 个 key")
    parser.add_argument("--limit", type=int, default=0,
                        help="最多验证 N 个 key，0 表示全部")
    parser.add_argument("--workers", type=int, default=20,
                        help="并发 HEAD 请求线程数")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划，不实际发送 HEAD 请求")
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

    upload_status = Path(args.upload_status)
    if not upload_status.is_absolute():
        upload_status = PROJECT_ROOT / upload_status
    verify_status = Path(args.verify_status)
    if not verify_status.is_absolute():
        verify_status = PROJECT_ROOT / verify_status

    # 获取已上传的 object_key 列表
    all_keys = latest_uploaded_keys(upload_status)
    total_keys = len(all_keys)

    if args.start:
        all_keys = all_keys[args.start:]
    if args.limit > 0:
        all_keys = all_keys[:args.limit]

    # 获取已验证的 key 集合
    verified_keys = load_verified_keys(verify_status)

    print(f"上传状态文件: {upload_status}")
    print(f"已上传 object_key 总数: {total_keys}")
    print(f"本次选取: {len(all_keys)} (start={args.start}, limit={args.limit})")
    print(f"验证状态文件: {verify_status}")
    print(f"已验证（跳过）: {len(verified_keys)}")
    print(f"并发线程数: {args.workers}")

    if args.dry_run:
        print("\n--- DRY RUN ---")
        for key in all_keys[:10]:
            print(f"  HEAD tos://{bucket}/{key}")
        if len(all_keys) > 10:
            print(f"  ... 还有 {len(all_keys) - 10} 个 key")
        print("Dry run 模式，未发送任何 HEAD 请求。")
        return 0

    # 预过滤：跳过已验证的 key
    to_verify: list[tuple[str, int]] = []
    for local_idx, key in enumerate(all_keys, 1):
        global_idx = args.start + local_idx
        if key in verified_keys:
            continue
        to_verify.append((key, global_idx))

    print(f"待验证: {len(to_verify)}（跳过 {len(all_keys) - len(to_verify)} 已验证）")

    verified = 0
    missing = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for key, gidx in to_verify:
            fut = pool.submit(
                verify_worker,
                bucket, key, gidx, total_keys, verify_status,
            )
            futures[fut] = (key, gidx)

        for fut in as_completed(futures):
            result = fut.result()
            status = result.get("status", "")
            if status == "verified":
                verified += 1
                verified_keys.add(result["object_key"])
            elif status == "missing":
                missing += 1

    print(
        f"完成。verified={verified}, missing={missing}, "
        f"total_checked={len(to_verify)}"
    )
    # 有缺失对象视为失败
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
