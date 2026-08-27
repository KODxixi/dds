#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 DDS TOS 上传/验证状态并输出续传命令。

参考 ArchLib cloud_upload_status.py 的结构：
- 读取 vault_upload_status.jsonl 和 vault_verify_status.jsonl
- 读取 artifact_upload_status.jsonl
- 统计 uploaded / failed / missing / verified 数量和百分比
- 输出 next_batch 和续传命令
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

# ── 定位项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATUS_DIR = PROJECT_ROOT / "cloud" / "volcengine" / "status"

VAULT_UPLOAD_STATUS = STATUS_DIR / "vault_upload_status.jsonl"
VAULT_VERIFY_STATUS = STATUS_DIR / "vault_verify_status.jsonl"
ARTIFACT_UPLOAD_STATUS = STATUS_DIR / "artifact_upload_status.jsonl"

# TOS 数据湖目录结构中的 Vault 扫描总数（动态计算）
VAULT_DIR = PROJECT_ROOT / "Vault"
DATA_EXTENSIONS = {".csv", ".parquet", ".json", ".jsonl"}


def iter_jsonl(path: Path):
    """逐行读取 JSONL 文件。"""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def count_jsonl(path: Path) -> int:
    """统计 JSONL 文件行数。"""
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig") as f:
        return sum(1 for line in f if line.strip())


def latest_status_by_key(path: Path) -> dict[str, dict[str, Any]]:
    """获取每个 object_key 的最新状态记录（后写入覆盖先写入）。"""
    by_key: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path) or []:
        key = row.get("object_key")
        if key:
            by_key[str(key)] = row
    return by_key


def status_counts(path: Path) -> tuple[Counter, int, int]:
    """返回 (状态计数器, 唯一key数, 总事件数)。"""
    latest = latest_status_by_key(path)
    counts = Counter(str(row.get("status") or "unknown") for row in latest.values())
    return counts, len(latest), count_jsonl(path)


def count_vault_files(vault_dir: Path) -> int:
    """统计 Vault 目录下数据文件总数。"""
    if not vault_dir.exists():
        return 0
    count = 0
    for path in vault_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in DATA_EXTENSIONS:
            continue
        if "__pycache__" in path.parts:
            continue
        if path.name.startswith("."):
            continue
        count += 1
    return count


def done_count(counts: Counter, done_statuses: set[str]) -> int:
    """统计已完成的数量。"""
    return sum(counts.get(status, 0) for status in done_statuses)


def pct(done: int, total: int) -> str:
    """计算百分比字符串。"""
    if total <= 0:
        return "0.0%"
    return f"{done / total * 100:.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="汇总 DDS TOS 上传/验证状态并输出续传命令。",
    )
    parser.add_argument("--batch-size", type=int, default=500,
                        help="每批次文件数，用于计算 next_batch")
    parser.add_argument("--out", default="",
                        help="输出 JSON 摘要文件路径（可选）")
    args = parser.parse_args()

    if args.batch_size <= 0:
        print("ERROR: --batch-size 必须为正数")
        return 2

    # Vault 文件总数（本地扫描）
    vault_total = count_vault_files(VAULT_DIR)

    # 上传状态
    upload_counts, upload_keys, upload_events = status_counts(VAULT_UPLOAD_STATUS)
    # 验证状态
    verify_counts, verify_keys, verify_events = status_counts(VAULT_VERIFY_STATUS)
    # 产物上传状态
    artifact_counts, artifact_keys, artifact_events = status_counts(ARTIFACT_UPLOAD_STATUS)

    vault_uploaded = done_count(upload_counts, {"uploaded", "exists_remote"})
    vault_verified = done_count(verify_counts, {"verified"})
    artifact_uploaded = done_count(artifact_counts, {"uploaded", "exists_remote"})

    # 计算 next_batch
    next_upload_batch = vault_uploaded // args.batch_size
    next_verify_batch = vault_verified // args.batch_size
    total_batches = math.ceil(vault_total / args.batch_size) if vault_total else 0

    # 缺失的 object_key（已上传但验证失败/未找到）
    verify_missing = verify_counts.get("missing", 0)
    # 上传失败的 object_key
    upload_failed = upload_counts.get("failed", 0)

    # 构建续传命令
    py = "python"
    script_dir = "cloud/volcengine"
    resume_commands = {
        "vault_upload_next": (
            f"{py} {script_dir}/upload_tos_vault.py "
            f"--start {min(next_upload_batch * args.batch_size, vault_total)} "
            f"--limit {args.batch_size} --workers 20"
        ),
        "vault_upload_repair": (
            f"{py} {script_dir}/upload_tos_vault.py "
            f"--start 0 --head-before-put --workers 20"
        ),
        "vault_verify_next": (
            f"{py} {script_dir}/verify_tos_vault.py "
            f"--start {min(next_verify_batch * args.batch_size, vault_total)} "
            f"--limit {args.batch_size} --workers 20"
        ),
        "artifact_upload": (
            f"{py} {script_dir}/upload_tos_artifacts.py --workers 10"
        ),
        "full_workflow_dry_run": (
            f"{py} {script_dir}/run_full_tos_workflow.py"
        ),
        "full_workflow_execute": (
            f"{py} {script_dir}/run_full_tos_workflow.py "
            f"--execute --confirm-external-upload dds-data-lake"
        ),
    }

    summary: dict[str, Any] = {
        "project_root": str(PROJECT_ROOT),
        "batch_size": args.batch_size,
        "vault_total_files": vault_total,
        "total_batches": total_batches,
        "vault_upload": {
            "done": vault_uploaded,
            "pct": pct(vault_uploaded, vault_total),
            "failed": upload_failed,
            "unique_keys": upload_keys,
            "events": upload_events,
            "counts": dict(upload_counts),
            "next_batch": min(next_upload_batch, total_batches),
        },
        "vault_verify": {
            "done": vault_verified,
            "pct": pct(vault_verified, vault_total),
            "missing": verify_missing,
            "unique_keys": verify_keys,
            "events": verify_events,
            "counts": dict(verify_counts),
            "next_batch": min(next_verify_batch, total_batches),
        },
        "artifact_upload": {
            "done": artifact_uploaded,
            "unique_keys": artifact_keys,
            "events": artifact_events,
            "counts": dict(artifact_counts),
        },
        "resume_commands": resume_commands,
    }

    # 打印摘要
    print("=" * 60)
    print("DDS TOS 上传状态汇总")
    print("=" * 60)
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Vault 数据文件总数: {vault_total}")
    print(f"批次大小: {args.batch_size}（共 {total_batches} 批）")
    print()

    print(f"--- Vault 上传 ---")
    print(f"  已上传: {vault_uploaded}/{vault_total} ({pct(vault_uploaded, vault_total)})")
    print(f"  失败: {upload_failed}")
    print(f"  唯一 key 数: {upload_keys}")
    print(f"  事件总数: {upload_events}")
    print(f"  状态分布: {dict(upload_counts)}")
    print(f"  下一批次: {min(next_upload_batch, total_batches)} / {total_batches}")
    if upload_failed:
        print(f"  ⚠ 有 {upload_failed} 个上传失败，建议运行 vault_upload_repair 修复")
    print()

    print(f"--- Vault 验证 ---")
    print(f"  已验证: {vault_verified}/{vault_total} ({pct(vault_verified, vault_total)})")
    print(f"  缺失: {verify_missing}")
    print(f"  唯一 key 数: {verify_keys}")
    print(f"  事件总数: {verify_events}")
    print(f"  状态分布: {dict(verify_counts)}")
    print(f"  下一批次: {min(next_verify_batch, total_batches)} / {total_batches}")
    print()

    print(f"--- 产物上传 ---")
    print(f"  已上传: {artifact_uploaded}")
    print(f"  唯一 key 数: {artifact_keys}")
    print(f"  事件总数: {artifact_events}")
    print(f"  状态分布: {dict(artifact_counts)}")
    print()

    print("--- 续传命令 ---")
    for name, cmd in resume_commands.items():
        print(f"  [{name}]")
        print(f"    {cmd}")
    print()

    # 写入 JSON 文件
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = PROJECT_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"摘要已写入: {out_path}")

    # 返回码：有失败或缺失则返回 1
    has_issues = upload_failed > 0 or verify_missing > 0
    return 1 if has_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
