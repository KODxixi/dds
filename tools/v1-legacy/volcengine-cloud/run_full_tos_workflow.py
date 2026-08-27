#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS TOS 全流程一键编排脚本。

参考 ArchLib run_full_tos_workflow.py 模式：
- 编排 vault upload → artifact upload → vault verify 流程
- 默认 dry-run，只打印命令不执行
- 实际执行需要 --execute --confirm-external-upload dds-data-lake 确认令牌
- 支持 --skip-assets / --skip-artifacts / --skip-verify 跳过阶段
- 支持 --batch-size / --max-batches / --start-batch 分批执行
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ── 定位项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / "cloud" / "volcengine" / ".env.volcengine"
CONFIRM_TOKEN = "dds-data-lake"

# ── 数据文件扩展名（用于估算 Vault 总数）──
DATA_EXTENSIONS = {".csv", ".parquet", ".json", ".jsonl"}


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


def run(cmd: list[str], dry_run: bool) -> int:
    """执行命令（dry-run 模式只打印不执行）。"""
    printable = " ".join(cmd)
    print(f"RUN: {printable}", flush=True)
    if dry_run:
        return 0
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), text=True)
    return proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS TOS 全流程编排：Vault 上传 → 产物上传 → Vault 验证。",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="环境变量文件路径")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="每批次上传文件数")
    parser.add_argument("--max-batches", type=int, default=0,
                        help="最多执行 N 个批次，0 表示全部")
    parser.add_argument("--start-batch", type=int, default=0,
                        help="从第 N 批开始（零起始）")
    parser.add_argument("--workers", type=int, default=20,
                        help="并发线程数")
    parser.add_argument("--retries", type=int, default=3,
                        help="单文件最大重试次数")
    parser.add_argument("--head-before-put", action="store_true",
                        help="上传前先 HEAD 远端，已存在则跳过")
    # 阶段跳过
    parser.add_argument("--skip-assets", action="store_true",
                        help="跳过 Vault 数据上传阶段")
    parser.add_argument("--skip-artifacts", action="store_true",
                        help="跳过产物上传阶段")
    parser.add_argument("--skip-verify", action="store_true",
                        help="跳过验证阶段")
    # 执行控制
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="只打印命令不执行（默认）")
    parser.add_argument("--execute", action="store_true",
                        help="实际执行外部 TOS 上传/HEAD 请求")
    parser.add_argument("--confirm-external-upload", default="",
                        help="确认令牌，必须为 dds-data-lake 才能实际执行")
    args = parser.parse_args()

    # 参数校验
    if args.batch_size <= 0 or args.start_batch < 0 or args.max_batches < 0:
        print("ERROR: batch-size 必须为正，start-batch/max-batches 不能为负。")
        return 2

    dry_run = not args.execute
    if not dry_run and args.confirm_external_upload != CONFIRM_TOKEN:
        print(f"ERROR: 实际上传需要 --confirm-external-upload {CONFIRM_TOKEN}")
        return 2

    # 估算 Vault 文件总数
    vault_dir = PROJECT_ROOT / "Vault"
    total_assets = count_vault_files(vault_dir)
    total_batches = (total_assets + args.batch_size - 1) // args.batch_size
    end_batch = total_batches if args.max_batches == 0 else min(
        total_batches, args.start_batch + args.max_batches
    )

    print("=" * 60)
    print("DDS TOS 全流程编排")
    print("=" * 60)
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"模式: {'DRY-RUN（只打印）' if dry_run else 'EXECUTE（实际执行）'}")
    print(f"Vault 数据文件总数: {total_assets}")
    print(f"批次大小: {args.batch_size}")
    print(f"批次范围: {args.start_batch}..{end_batch - 1} / {total_batches}")
    print(f"环境文件: {args.env_file}")
    print(f"阶段跳过: "
          f"assets={'是' if args.skip_assets else '否'}, "
          f"artifacts={'是' if args.skip_artifacts else '否'}, "
          f"verify={'是' if args.skip_verify else '否'}")
    print()

    py = sys.executable
    script_dir = PROJECT_ROOT / "cloud" / "volcengine"
    failures: list[tuple[str, int]] = []

    # ── 阶段一：Vault 数据上传 ──
    if not args.skip_assets:
        print(">>> 阶段一：Vault 数据上传")
        for batch in range(args.start_batch, end_batch):
            start = batch * args.batch_size
            limit = min(args.batch_size, max(0, total_assets - start))
            if limit <= 0:
                break
            cmd = [
                py, "-B",
                str(script_dir / "upload_tos_vault.py"),
                "--env-file", args.env_file,
                "--start", str(start),
                "--limit", str(limit),
                "--workers", str(args.workers),
                "--retries", str(args.retries),
            ]
            if args.head_before_put:
                cmd.append("--head-before-put")
            if dry_run:
                cmd.append("--dry-run")
            rc = run(cmd, dry_run)
            if rc:
                failures.append((f"vault_batch_{batch}", rc))
                break
        if not failures:
            print("  阶段一完成。" if not dry_run else "  阶段一计划完成（dry-run）。")
        print()

    # ── 阶段二：产物上传 ──
    if not failures and not args.skip_artifacts:
        print(">>> 阶段二：产物上传")
        cmd = [
            py, "-B",
            str(script_dir / "upload_tos_artifacts.py"),
            "--env-file", args.env_file,
            "--workers", str(min(args.workers, 10)),
            "--retries", str(args.retries),
        ]
        if args.head_before_put:
            cmd.append("--head-before-put")
        if dry_run:
            cmd.append("--dry-run")
        rc = run(cmd, dry_run)
        if rc:
            failures.append(("artifacts", rc))
        if not failures:
            print("  阶段二完成。" if not dry_run else "  阶段二计划完成（dry-run）。")
        print()

    # ── 阶段三：Vault 验证 ──
    if not failures and not args.skip_verify:
        print(">>> 阶段三：Vault HEAD 验证")
        verify_total = total_assets
        verify_batches = (verify_total + args.batch_size - 1) // args.batch_size
        if args.max_batches:
            verify_batches = min(verify_batches, args.max_batches)
        for batch in range(args.start_batch, min(total_batches, args.start_batch + verify_batches)):
            start = batch * args.batch_size
            limit = min(args.batch_size, max(0, verify_total - start))
            if limit <= 0:
                break
            cmd = [
                py, "-B",
                str(script_dir / "verify_tos_vault.py"),
                "--env-file", args.env_file,
                "--start", str(start),
                "--limit", str(limit),
                "--workers", str(args.workers),
            ]
            if dry_run:
                cmd.append("--dry-run")
            rc = run(cmd, dry_run)
            if rc:
                failures.append((f"verify_batch_{batch}", rc))
                break
        if not failures:
            print("  阶段三完成。" if not dry_run else "  阶段三计划完成（dry-run）。")
        print()

    # ── 结果汇总 ──
    if failures:
        print("FAILED:")
        for name, rc in failures:
            print(f"  {name}: exit={rc}")
        return 1

    if dry_run:
        print("工作流计划完成（dry-run 模式）。")
        print(f"实际执行请添加: --execute --confirm-external-upload {CONFIRM_TOKEN}")
    else:
        print("工作流执行完成。")
        print("建议运行 cloud_upload_status.py 查看完整状态汇总。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
