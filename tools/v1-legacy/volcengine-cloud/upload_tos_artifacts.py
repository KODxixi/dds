#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上传 DDS 报告产物和治理校准数据到火山引擎 TOS。

扫描以下目录：
- data_out/reports/      地块报告 + 决策报告 + 回测报告 + 交互报告
- data_out/online_evidence/  线上补充证据
- data_out/leads/        客户线索 JSONL
- data/                  静态校准数据（benchmark_library.json 等）

object_key 格式：
- reports/parcel/xxx.md、reports/decision/xxx.html、reports/backtest/xxx.md
- governance/quality_report.md
- online_evidence/xxx.json
- leads/xxx.jsonl
- data/xxx.json
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
DEFAULT_ENV_FILE = PROJECT_ROOT / "cloud" / "volcengine" / ".env.volcengine"
DEFAULT_STATUS_FILE = PROJECT_ROOT / "cloud" / "volcengine" / "status" / "artifact_upload_status.jsonl"

# 报告产物目录
REPORTS_DIR = PROJECT_ROOT / "data_out" / "reports"
ONLINE_EVIDENCE_DIR = PROJECT_ROOT / "data_out" / "online_evidence"
LEADS_DIR = PROJECT_ROOT / "data_out" / "leads"
GOVERNANCE_DIR = PROJECT_ROOT / "scripts" / "governance"
DATA_DIR = PROJECT_ROOT / "data"

# 报告产物扩展名
ARTIFACT_EXTENSIONS = {".md", ".json", ".html", ".jsonl", ".csv"}

# data/ 下需上传的静态校准文件白名单
STATIC_DATA_FILES = [
    "benchmark_library.json",
    "price_band_calibration.json",
    "loupan_schema.json",
    "archetype_real_calibration.json",
    "architecture_director_contract.json",
    "listing_dimensions.json",
]


def collect_artifact_files(
    include_reports: bool = True,
    include_evidence: bool = True,
    include_leads: bool = True,
    include_governance: bool = True,
    include_static_data: bool = True,
) -> list[tuple[Path, str]]:
    """收集所有需要上传的产物文件，返回 (local_path, object_key) 列表。

    object_key 格式：保留相对 data_out/ 或 data/ 的路径结构。
    """
    results: list[tuple[Path, str]] = []

    # ── 报告目录：data_out/reports/* ──
    if include_reports and REPORTS_DIR.exists():
        for path in sorted(REPORTS_DIR.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ARTIFACT_EXTENSIONS:
                continue
            if "__pycache__" in path.parts:
                continue
            # data_out/reports/decision/xxx.md -> reports/decision/xxx.md
            rel = path.relative_to(REPORTS_DIR).as_posix()
            object_key = f"reports/{rel}"
            results.append((path, object_key))

    # ── 线上证据：data_out/online_evidence/* ──
    if include_evidence and ONLINE_EVIDENCE_DIR.exists():
        for path in sorted(ONLINE_EVIDENCE_DIR.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ARTIFACT_EXTENSIONS:
                continue
            rel = path.relative_to(ONLINE_EVIDENCE_DIR).as_posix()
            object_key = f"online_evidence/{rel}"
            results.append((path, object_key))

    # ── 客户线索：data_out/leads/* ──
    if include_leads and LEADS_DIR.exists():
        for path in sorted(LEADS_DIR.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ARTIFACT_EXTENSIONS:
                continue
            rel = path.relative_to(LEADS_DIR).as_posix()
            object_key = f"leads/{rel}"
            results.append((path, object_key))

    # ── 治理模块源码产物：scripts/governance/*.py ──
    # 治理标准工件（quality_report.md 等）在 data_out/ 下生成，
    # 这里上传治理脚本本身作为 governance/ 参考代码
    if include_governance and GOVERNANCE_DIR.exists():
        for path in sorted(GOVERNANCE_DIR.glob("*.py")):
            if path.name.startswith("__"):
                continue
            object_key = f"governance/{path.name}"
            results.append((path, object_key))

    # ── 静态校准数据：data/*.json ──
    if include_static_data and DATA_DIR.exists():
        for name in STATIC_DATA_FILES:
            path = DATA_DIR / name
            if path.exists():
                object_key = f"data/{name}"
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
        description="上传 DDS 报告产物和治理校准数据到火山引擎 TOS（支持断点续传）。",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE),
                        help="环境变量文件路径")
    parser.add_argument("--status-file", default=str(DEFAULT_STATUS_FILE),
                        help="JSONL 状态文件路径（断点续传用）")
    parser.add_argument("--start", type=int, default=0,
                        help="零起始偏移量，跳过前 N 个文件")
    parser.add_argument("--limit", type=int, default=0,
                        help="最多处理 N 个文件，0 表示全部")
    parser.add_argument("--workers", type=int, default=10,
                        help="并发上传线程数（产物文件较小，默认 10）")
    parser.add_argument("--retries", type=int, default=3,
                        help="单个文件最大重试次数")
    parser.add_argument("--head-before-put", action="store_true",
                        help="上传前先 HEAD 远端，已存在则跳过")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划，不实际上传")
    parser.add_argument("--skip-reports", action="store_true",
                        help="跳过报告目录")
    parser.add_argument("--skip-evidence", action="store_true",
                        help="跳过线上证据目录")
    parser.add_argument("--skip-leads", action="store_true",
                        help="跳过客户线索目录")
    parser.add_argument("--skip-governance", action="store_true",
                        help="跳过治理模块")
    parser.add_argument("--skip-static-data", action="store_true",
                        help="跳过静态校准数据")
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

    # 收集文件
    files = collect_artifact_files(
        include_reports=not args.skip_reports,
        include_evidence=not args.skip_evidence,
        include_leads=not args.skip_leads,
        include_governance=not args.skip_governance,
        include_static_data=not args.skip_static_data,
    )
    total_files = len(files)
    if args.start:
        files = files[args.start:]
    if args.limit > 0:
        files = files[:args.limit]

    status_file = Path(args.status_file)
    if not status_file.is_absolute():
        status_file = PROJECT_ROOT / status_file

    done_keys = tos_client.load_done_keys(status_file)

    print(f"产物文件总数: {total_files}")
    print(f"本次选取: {len(files)} (start={args.start}, limit={args.limit})")
    print(f"并发线程数: {args.workers}")
    print(f"状态文件: {status_file}")
    print(f"已完成（跳过）: {len(done_keys)}")

    rows = build_rows(files)

    if args.dry_run:
        print("\n--- DRY RUN ---")
        for row in rows[:15]:
            print(f"  {row['local_path']} -> tos://{bucket}/{row['object_key']}")
        if len(rows) > 15:
            print(f"  ... 还有 {len(rows) - 15} 个文件")
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
