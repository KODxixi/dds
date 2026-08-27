#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 云端->本地数据同步。

从 TOS 数据湖下载指定数据到本地:
- vault/ 层  -> 本地 Vault/
- governance/ 层 -> 本地 data_out/
- _meta/ 层  -> 本地缓存 (~/.dds_cache/)
- 指定发布版本的数据

用法:
    python sync_tos_data.py --vault 三亚      # 同步指定城市数据
    python sync_tos_data.py --governance       # 同步治理报告
    python sync_tos_data.py --meta             # 同步元数据
    python sync_tos_data.py --release <id>     # 同步指定发布
    python sync_tos_data.py --all              # 同步全部
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import tos_client

# 本地缓存目录：优先 DDS_TOS_CACHE_DIR，回退 ~/.dds_cache/
CACHE_DIR = Path(os.environ.get("DDS_TOS_CACHE_DIR", str(Path.home() / ".dds_cache")))


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


def _list_tos_keys(prefix: str) -> list[str]:
    """列出 TOS 上指定前缀的所有对象 key。"""
    client, bucket = _get_client_and_bucket()
    keys: list[str] = []
    marker = ""
    while True:
        resp = client.list_objects(bucket, prefix=prefix, marker=marker, max_keys=200)
        contents = getattr(resp, "contents", None) or []
        for obj in contents:
            keys.append(getattr(obj, "key", ""))
        is_truncated = getattr(resp, "is_truncated", False)
        marker = getattr(resp, "next_marker", "") or ""
        if not is_truncated or not marker:
            break
    return keys


def _download_object(object_key: str, local_path: Path) -> bool:
    """下载单个 TOS 对象到本地路径。"""
    _, bucket = _get_client_and_bucket()
    return tos_client.download_one(bucket, object_key, local_path)


def sync_vault(city: str) -> dict[str, Any]:
    """从 TOS vault/ 层下载指定城市的数据到本地 Vault。

    匹配规则: TOS key 包含 -{city}. 或 -{city}/ 的文件，
    覆盖新楼盘、二手房小区、历史年份等。
    本地路径映射: vault/xxx -> Vault/xxx
    """
    all_vault_keys = _list_tos_keys("vault/")

    # 匹配属于该城市的文件
    city_keys: list[str] = []
    suffixes = [f"-{city}.", f"-{city}/"]
    for key in all_vault_keys:
        if any(s in key for s in suffixes):
            city_keys.append(key)

    downloaded = 0
    skipped = 0
    failed = 0

    for key in city_keys:
        # 路径映射: vault/2026新楼盘/新楼盘-三亚.csv -> Vault/2026新楼盘/新楼盘-三亚.csv
        local_path = PROJECT_ROOT / key.replace("vault/", "Vault/", 1)

        if local_path.exists():
            skipped += 1
            continue

        ok = _download_object(key, local_path)
        if ok:
            downloaded += 1
            rel = local_path.relative_to(PROJECT_ROOT)
            print(f"  DOWNLOADED {key} -> {rel}")
        else:
            failed += 1
            print(f"  FAILED     {key}", file=sys.stderr)

    result: dict[str, Any] = {
        "city": city,
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "total": len(city_keys),
    }
    print(f"\nVault sync [{city}]: {downloaded} downloaded, {skipped} skipped, {failed} failed")
    return result


def sync_governance() -> dict[str, Any]:
    """从 TOS governance/ 层下载治理报告到本地 data_out/。

    路径映射: governance/T1_xxx/report.md -> data_out/governance/T1_xxx/report.md
    """
    keys = _list_tos_keys("governance/")

    downloaded = 0
    skipped = 0
    failed = 0

    for key in keys:
        local_path = PROJECT_ROOT / "data_out" / key

        if local_path.exists():
            skipped += 1
            continue

        ok = _download_object(key, local_path)
        if ok:
            downloaded += 1
            print(f"  DOWNLOADED {key}")
        else:
            failed += 1
            print(f"  FAILED     {key}", file=sys.stderr)

    result: dict[str, Any] = {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "total": len(keys),
    }
    print(f"\nGovernance sync: {downloaded} downloaded, {skipped} skipped, {failed} failed")
    return result


def sync_meta() -> dict[str, Any]:
    """从 TOS _meta/ 层下载元数据到本地缓存目录。

    路径映射: _meta/schema_version.json -> {CACHE_DIR}/_meta/schema_version.json
    """
    keys = _list_tos_keys("_meta/")

    downloaded = 0
    skipped = 0
    failed = 0

    for key in keys:
        local_path = CACHE_DIR / key

        if local_path.exists():
            skipped += 1
            continue

        ok = _download_object(key, local_path)
        if ok:
            downloaded += 1
            print(f"  DOWNLOADED {key} -> {local_path}")
        else:
            failed += 1
            print(f"  FAILED     {key}", file=sys.stderr)

    result: dict[str, Any] = {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "total": len(keys),
        "cache_dir": str(CACHE_DIR),
    }
    print(f"\nMeta sync: {downloaded} downloaded, {skipped} skipped, {failed} failed")
    print(f"  -> {CACHE_DIR}")
    return result


def sync_release(release_id: str) -> dict[str, Any]:
    """同步指定发布版本的数据到本地缓存目录。

    读取发布元数据，下载除 dds/releases/ 和 logs/ 外的全部数据湖对象。
    """
    # 读取发布元数据
    release_key = f"dds/releases/{release_id}/release_meta.json"
    client, bucket = _get_client_and_bucket()
    try:
        resp = client.get_object(bucket, release_key)
        meta = json.loads(resp.read().decode("utf-8"))
    except Exception:
        print(f"ERROR: 未找到发布 '{release_id}'", file=sys.stderr)
        return {"error": "release_not_found", "release_id": release_id}

    # 列出所有数据湖对象
    all_keys = _list_tos_keys("")
    # 排除 releases/ 和 logs/ 目录
    data_keys = [
        k for k in all_keys
        if not k.startswith("dds/releases/") and not k.startswith("logs/")
    ]

    local_base = CACHE_DIR / "releases" / release_id
    downloaded = 0
    skipped = 0
    failed = 0

    for key in data_keys:
        local_path = local_base / key

        if local_path.exists():
            skipped += 1
            continue

        ok = _download_object(key, local_path)
        if ok:
            downloaded += 1
        else:
            failed += 1

    result: dict[str, Any] = {
        "release_id": release_id,
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "total": len(data_keys),
        "local_dir": str(local_base),
        "meta_file_count": meta.get("file_count", 0),
    }
    print(f"\nRelease sync [{release_id}]: {downloaded} downloaded, {skipped} skipped, {failed} failed")
    print(f"  -> {local_base}")
    return result


def sync_all() -> dict[str, Any]:
    """同步全部: meta + governance + 所有城市 vault。"""
    results: dict[str, Any] = {}

    # 1. 元数据
    print("=" * 60)
    print("Syncing _meta/ ...")
    print("=" * 60)
    results["meta"] = sync_meta()

    # 2. 治理报告
    print("\n" + "=" * 60)
    print("Syncing governance/ ...")
    print("=" * 60)
    results["governance"] = sync_governance()

    # 3. 所有城市的 vault 数据
    print("\n" + "=" * 60)
    print("Syncing vault/ (all cities) ...")
    print("=" * 60)
    vault_keys = _list_tos_keys("vault/")
    cities: set[str] = set()
    for key in vault_keys:
        # vault/2026新楼盘/新楼盘-三亚.csv -> 三亚
        parts = key.split("/")
        if len(parts) >= 3:
            filename = parts[-1]
            if "-" in filename:
                city = filename.rsplit("-", 1)[-1].rsplit(".", 1)[0]
                cities.add(city)

    vault_results: dict[str, Any] = {}
    for city in sorted(cities):
        print(f"\n--- City: {city} ---")
        vault_results[city] = sync_vault(city)
    results["vault"] = vault_results

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS 云端->本地数据同步 — 从 TOS 数据湖下载到本地"
    )
    parser.add_argument("--vault", help="同步指定城市的 vault 数据")
    parser.add_argument("--governance", action="store_true", help="同步治理报告")
    parser.add_argument("--meta", action="store_true", help="同步元数据")
    parser.add_argument("--release", help="同步指定发布版本的数据")
    parser.add_argument("--all", action="store_true", help="同步全部（meta + governance + 所有城市 vault）")
    args = parser.parse_args()

    if args.all:
        results = sync_all()
        print("\n" + "=" * 60)
        print("ALL SYNC COMPLETE")
        print("=" * 60)
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.vault:
        result = sync_vault(args.vault)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.governance:
        result = sync_governance()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.meta:
        result = sync_meta()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.release:
        result = sync_release(args.release)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # 默认：打印帮助
    parser.print_help()
    print(f"\n本地缓存目录: {CACHE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
