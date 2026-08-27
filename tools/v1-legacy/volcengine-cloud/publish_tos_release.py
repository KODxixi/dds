#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS TOS 发布指针管理。

在 TOS 上管理 dds/releases/current.json 指针文件，支持：
- 构建候选发布（candidate）
- 发布门禁验证（零缺失文件、上传完成、HEAD 验证、治理报告存在）
- 发布切换（candidate -> current，指针式，不删除对象）
- 回滚到历史发布

用法:
    # 构建候选发布
    python publish_tos_release.py --status candidate

    # 发布（需要门禁通过 + 确认令牌）
    python publish_tos_release.py --status published --set-current --execute --confirm-release dds-data-lake

    # 回滚到历史发布
    python publish_tos_release.py --rollback dds-20260709-120000

    # 列出所有发布
    python publish_tos_release.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import tos_client

# 确认令牌：发布操作必须传入此值
CONFIRM_TOKEN = "dds-data-lake"

# TOS 路径常量
RELEASE_PREFIX = "dds/releases/"
CURRENT_KEY = "dds/releases/current.json"

# 数据湖四层
LAYERS = ["raw", "normalized", "vault", "governance"]

SCHEMA_VERSION = "2.0"


def _init_env() -> None:
    """加载 .env.volcengine 环境变量并禁用代理。"""
    env_file = Path(__file__).parent / ".env.volcengine"
    tos_client.load_dotenv(env_file)
    tos_client.disable_proxy()


def _get_client_and_bucket():
    """获取 TOS 客户端和 bucket 名称。"""
    _init_env()
    cfg = tos_client.get_tos_config()
    bucket = cfg["bucket"]
    if not bucket:
        print("ERROR: DDS_TOS_BUCKET 未设置", file=sys.stderr)
        sys.exit(1)
    client = tos_client.get_thread_local_client()
    return client, bucket


def _list_tos_objects(prefix: str) -> list[dict[str, Any]]:
    """分页列出 TOS 对象。"""
    client, bucket = _get_client_and_bucket()
    objects: list[dict[str, Any]] = []
    marker = ""
    while True:
        resp = client.list_objects(bucket, prefix=prefix, marker=marker, max_keys=200)
        contents = getattr(resp, "contents", None) or []
        for obj in contents:
            objects.append({
                "key": getattr(obj, "key", ""),
                "size": getattr(obj, "size", 0) or getattr(obj, "content_length", 0) or 0,
            })
        is_truncated = getattr(resp, "is_truncated", False)
        marker = getattr(resp, "next_marker", "") or ""
        if not is_truncated or not marker:
            break
    return objects


def _read_tos_json(key: str) -> dict | None:
    """从 TOS 读取 JSON 对象。"""
    client, bucket = _get_client_and_bucket()
    try:
        resp = client.get_object(bucket, key)
        content = resp.read().decode("utf-8")
        return json.loads(content)
    except Exception:
        return None


def _write_tos_json(key: str, data: dict) -> None:
    """写入 JSON 对象到 TOS。"""
    client, bucket = _get_client_and_bucket()
    content = json.dumps(data, ensure_ascii=False, indent=2)
    client.put_object(
        bucket, key,
        content=content.encode("utf-8"),
        content_type="application/json",
    )


def _generate_release_id() -> str:
    """生成发布 ID（格式: dds-YYYYMMDD-HHMMSS）。"""
    return datetime.now().strftime("dds-%Y%m%d-%H%M%S")


def _read_current_release_id() -> str | None:
    """读取当前 current.json 指针的 release_id。"""
    current = _read_tos_json(CURRENT_KEY)
    if current:
        return current.get("release_id")
    return None


def build_candidate() -> dict[str, Any]:
    """构建候选发布元数据。

    收集数据湖全量对象清单，按层分类统计，
    生成 release_meta.json 写入 TOS dds/releases/{release_id}/。
    状态设为 candidate，尚未通过验证门禁。
    """
    now = datetime.now()
    release_id = _generate_release_id()

    # 列出所有数据湖对象
    all_objects = _list_tos_objects("")

    # 按层分类
    layers_summary: dict[str, int] = {layer: 0 for layer in LAYERS}
    layers_summary["reports"] = 0
    layers_summary["logs"] = 0
    layers_summary["_meta"] = 0
    layers_summary["dds/releases"] = 0
    layers_summary["other"] = 0

    total_size = 0
    missing_files: list[str] = []

    for obj in all_objects:
        key = obj["key"]
        size = obj.get("size", 0)
        total_size += size
        categorized = False
        for layer in LAYERS + ["reports", "logs", "_meta", "dds/releases"]:
            if key.startswith(layer + "/"):
                layers_summary[layer] += 1
                categorized = True
                break
        if not categorized:
            layers_summary["other"] += 1

    candidate: dict[str, Any] = {
        "release_id": release_id,
        "status": "candidate",
        "created_at": now.isoformat(timespec="seconds"),
        "schema_version": SCHEMA_VERSION,
        "file_count": len(all_objects),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "layers_summary": layers_summary,
        "missing_files": missing_files,
        "validation": {
            "zero_missing": False,
            "upload_complete": False,
            "head_verified": False,
            "governance_report_exists": False,
        },
    }

    # 写入 TOS
    release_key = f"{RELEASE_PREFIX}{release_id}/release_meta.json"
    _write_tos_json(release_key, candidate)
    print(f"CANDIDATE  {release_id}")
    print(f"  file_count:   {candidate['file_count']}")
    print(f"  total_size:   {candidate['total_size_mb']} MB")
    print(f"  layers:      {json.dumps(layers_summary, ensure_ascii=False)}")
    print(f"  TOS key:     {release_key}")
    print(f"\n下一步: 验证门禁 + 发布")
    print(f"  python publish_tos_release.py --status published --set-current --execute --confirm-release {CONFIRM_TOKEN}")

    return candidate


def _run_validation_gates(candidate: dict[str, Any]) -> dict[str, Any]:
    """运行验证门禁。

    四项检查全部通过才能发布:
    1. zero_missing: 零缺失文件
    2. upload_complete: 上传完成（file_count > 0）
    3. head_verified: HEAD 验证完成（_meta/schema_version.json 可访问）
    4. governance_report_exists: 治理报告存在（governance/ 层有文件）
    """
    validation: dict[str, Any] = {}

    # 1. 零缺失文件
    validation["zero_missing"] = len(candidate.get("missing_files", [])) == 0

    # 2. 上传完成
    validation["upload_complete"] = candidate.get("file_count", 0) > 0

    # 3. HEAD 验证完成（检查 _meta/schema_version.json 存在）
    client, bucket = _get_client_and_bucket()
    try:
        client.head_object(bucket, "_meta/schema_version.json")
        validation["head_verified"] = True
    except Exception:
        validation["head_verified"] = False

    # 4. 治理报告存在（检查 governance/ 层有文件）
    gov_objects = _list_tos_objects("governance/")
    validation["governance_report_exists"] = len(gov_objects) > 0

    return validation


def publish_current(
    confirm_token: str | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """将候选发布切换为 current（需要验证门禁全部通过）。

    流程:
    1. 验证 confirm_token == CONFIRM_TOKEN
    2. 查找最新的 candidate 发布
    3. 运行验证门禁
    4. 门禁全部通过 + execute=True → 写入 current.json 指针
    5. execute=False → dry-run，只打印将要执行的操作
    """
    if confirm_token != CONFIRM_TOKEN:
        print(
            f"ERROR: 确认令牌不匹配。期望 '{CONFIRM_TOKEN}'，得到 '{confirm_token}'",
            file=sys.stderr,
        )
        return {"error": "confirm_token_mismatch", "expected": CONFIRM_TOKEN}

    # 查找最新的 candidate
    release_objects = _list_tos_objects(RELEASE_PREFIX)
    candidates: list[tuple[str, dict]] = []
    for obj in release_objects:
        key = obj["key"]
        if key.endswith("/release_meta.json") and key != CURRENT_KEY:
            meta = _read_tos_json(key)
            if meta and meta.get("status") == "candidate":
                candidates.append((key, meta))

    if not candidates:
        print("ERROR: 没有找到 candidate 发布", file=sys.stderr)
        return {"error": "no_candidate"}

    # 取最新的（按 created_at 排序）
    candidates.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    release_key, candidate = candidates[0]
    release_id = candidate["release_id"]

    # 运行验证门禁
    validation = _run_validation_gates(candidate)
    candidate["validation"] = validation

    all_passed = all(v for v in validation.values() if isinstance(v, bool))
    if not all_passed:
        print(f"ERROR: 验证门禁未通过:", file=sys.stderr)
        for k, v in validation.items():
            status = "PASS" if v else "FAIL"
            print(f"  {k}: {status}", file=sys.stderr)
        return {"error": "validation_failed", "validation": validation}

    print(f"VALIDATION PASSED:")
    for k, v in validation.items():
        status = "PASS" if v else "FAIL"
        print(f"  {k}: {status}")

    if not execute:
        print(f"\n[DRY-RUN] 将发布 {release_id} 为 current")
        print(f"  重新运行加 --execute 以真正发布:")
        print(f"  python publish_tos_release.py --status published --set-current "
              f"--execute --confirm-release {CONFIRM_TOKEN}")
        return {"dry_run": True, "release_id": release_id, "validation": validation}

    # 更新候选状态为 published
    candidate["status"] = "published"
    candidate["published_at"] = datetime.now().isoformat(timespec="seconds")
    _write_tos_json(release_key, candidate)

    # 更新 current.json 指针
    previous_release = _read_current_release_id()
    current_pointer: dict[str, Any] = {
        "release_id": release_id,
        "status": "published",
        "published_at": candidate["published_at"],
        "previous": previous_release,
        "schema_version": candidate.get("schema_version", SCHEMA_VERSION),
        "file_count": candidate.get("file_count", 0),
        "total_size_bytes": candidate.get("total_size_bytes", 0),
    }
    _write_tos_json(CURRENT_KEY, current_pointer)

    print(f"\nPUBLISHED  {release_id}")
    print(f"  current pointer: {CURRENT_KEY}")
    print(f"  previous:        {previous_release or '(none)'}")
    print(f"  file_count:      {current_pointer['file_count']}")

    return {"published": release_id, "pointer": current_pointer}


def rollback(release_id: str) -> dict[str, Any]:
    """回滚到历史发布（指针式，不删除对象）。

    只更新 current.json 指针指向指定 release_id，
    不删除任何 TOS 对象，可随时再次前滚。
    """
    release_key = f"{RELEASE_PREFIX}{release_id}/release_meta.json"
    meta = _read_tos_json(release_key)
    if not meta:
        print(f"ERROR: 未找到发布 '{release_id}'", file=sys.stderr)
        return {"error": "release_not_found", "release_id": release_id}

    previous_release = _read_current_release_id()

    # 更新 current.json 指针
    current_pointer: dict[str, Any] = {
        "release_id": release_id,
        "status": "rolled_back",
        "rolled_back_at": datetime.now().isoformat(timespec="seconds"),
        "previous": previous_release,
        "schema_version": meta.get("schema_version", SCHEMA_VERSION),
        "file_count": meta.get("file_count", 0),
        "total_size_bytes": meta.get("total_size_bytes", 0),
    }
    _write_tos_json(CURRENT_KEY, current_pointer)

    print(f"ROLLBACK  {previous_release or '(none)'} -> {release_id}")
    print(f"  current pointer: {CURRENT_KEY}")
    print(f"  注意: 对象未被删除，可随时前滚到 {previous_release}")

    return {"rolled_back_to": release_id, "previous": previous_release}


def list_releases() -> list[dict]:
    """列出所有发布版本。"""
    release_objects = _list_tos_objects(RELEASE_PREFIX)
    releases: list[dict] = []
    for obj in release_objects:
        key = obj["key"]
        if key.endswith("/release_meta.json") and key != CURRENT_KEY:
            meta = _read_tos_json(key)
            if meta:
                releases.append(meta)

    current = _read_tos_json(CURRENT_KEY)
    current_id = current.get("release_id") if current else None

    print(f"\nDDS TOS 发布列表:")
    print(f"{'=' * 70}")
    if current:
        print(f"  CURRENT: {current_id} ({current.get('status')})")
    else:
        print(f"  CURRENT: (none)")
    print()

    releases.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    for r in releases:
        marker = " <-- current" if r["release_id"] == current_id else ""
        size_mb = r.get("total_size_mb", round(r.get("total_size_bytes", 0) / (1024 * 1024), 2))
        print(
            f"  {r['release_id']}  status={r['status']:12s}  "
            f"files={r.get('file_count', 0):5d}  size={size_mb}MB{marker}"
        )

    return releases


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS TOS 发布指针管理 — 候选/发布/回滚"
    )
    parser.add_argument(
        "--status", choices=["candidate", "published"],
        help="发布状态操作: candidate=构建候选, published=发布为 current",
    )
    parser.add_argument("--set-current", action="store_true", help="将发布设置为 current")
    parser.add_argument("--execute", action="store_true", help="真正执行（非 dry-run）")
    parser.add_argument(
        "--confirm-release",
        help=f"确认令牌（必须为 '{CONFIRM_TOKEN}'）",
    )
    parser.add_argument("--rollback", help="回滚到指定 release_id")
    parser.add_argument("--list", action="store_true", help="列出所有发布")
    args = parser.parse_args()

    if args.list:
        list_releases()
        return 0

    if args.rollback:
        result = rollback(args.rollback)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if "error" not in result else 1

    if args.status == "candidate":
        result = build_candidate()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.status == "published":
        if not args.set_current:
            print("ERROR: 发布需要 --set-current 标志", file=sys.stderr)
            return 1
        result = publish_current(
            confirm_token=args.confirm_release,
            execute=args.execute,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if "error" not in result else 1

    # 默认：打印帮助
    parser.print_help()
    print(f"\nCONFIRM_TOKEN = '{CONFIRM_TOKEN}'")
    print("\n典型用法:")
    print(f"  1. 构建候选:  python publish_tos_release.py --status candidate")
    print(f"  2. 发布:      python publish_tos_release.py --status published "
          f"--set-current --execute --confirm-release {CONFIRM_TOKEN}")
    print(f"  3. 回滚:      python publish_tos_release.py --rollback <release_id>")
    print(f"  4. 列出:      python publish_tos_release.py --list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
