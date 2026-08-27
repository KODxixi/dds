#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 火山引擎 TOS 环境就绪检查。

参考 ArchLib volcengine_env_check.py 模式：
- 检查 .env.volcengine 中必需的环境变量
- 检查 TOS SDK 是否安装
- 检查本地 Vault 数据是否存在
- 报告 SET / MISSING 状态（不泄露密钥值）
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_ENV = [
    "DDS_TOS_BUCKET",
    "DDS_TOS_ENDPOINT",
    "DDS_TOS_REGION",
    "DDS_TOS_ACCESS_KEY_ID",
    "DDS_TOS_SECRET_ACCESS_KEY",
]

OPTIONAL_ENV = [
    "DDS_MODE",
    "DDS_TOS_CACHE_DIR",
    "DDS_RELEASE",
    "DDS_TOS_RELEASE_KEY",
    "DDS_FC_FUNCTION_PREFIX",
    "DDS_FC_ENDPOINT",
    "AMAP_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "DDS_ALLOWED_ORIGINS",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def count_vault_files() -> dict[str, int]:
    """统计 Vault 各层数据文件数。"""
    vault = PROJECT_ROOT / "Vault"
    counts = {
        "2026新楼盘": 0,
        "二手房小区": 0,
        "历史年份": 0,
        "建安成本": 0,
        "开发商信用": 0,
    }
    # 新楼盘
    new_dir = vault / "2026新楼盘"
    if new_dir.exists():
        counts["2026新楼盘"] = len(list(new_dir.glob("新楼盘-*.csv")))
    # 二手房
    second_dir = vault / "2026新楼盘"
    if second_dir.exists():
        counts["二手房小区"] = len(list(second_dir.glob("二手房小区-*.csv")))
    # 历史年份
    hist_count = 0
    for item in vault.iterdir():
        if item.is_dir() and item.name.endswith("年"):
            hist_count += 1
    counts["历史年份"] = hist_count
    # 建安成本
    cost_dir = vault / "建安成本"
    if cost_dir.exists():
        counts["建安成本"] = len(list(cost_dir.glob("*.csv"))) + len(list(cost_dir.glob("*.json")))
    # 开发商信用
    credit_dir = vault / "开发商信用"
    if credit_dir.exists():
        counts["开发商信用"] = len(list(credit_dir.glob("*.csv"))) + len(list(credit_dir.glob("*.json")))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Check DDS Volcengine TOS readiness.")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / "cloud" / "volcengine" / ".env.volcengine"))
    args = parser.parse_args()

    env_file = Path(args.env_file)
    load_dotenv(env_file)

    print(f"Project root: {PROJECT_ROOT}")
    print(f"Env file: {env_file} ({'found' if env_file.exists() else 'not found'})")

    # Python 依赖检查
    print("\nPython dependencies:")
    deps = {
        "tos": bool(importlib.util.find_spec("tos")),
        "dotenv": bool(importlib.util.find_spec("dotenv")),
        "requests": bool(importlib.util.find_spec("requests")),
        "flask": bool(importlib.util.find_spec("flask")),
        "duckdb": bool(importlib.util.find_spec("duckdb")),
        "pandas": bool(importlib.util.find_spec("pandas")),
    }
    for name, ok in deps.items():
        print(f"  {name}: {'OK' if ok else 'MISSING'}")

    # 环境变量检查
    print("\nEnvironment:")
    missing_env = []
    for key in REQUIRED_ENV:
        value = os.environ.get(key)
        print(f"  {key}: {'SET' if value else 'MISSING'}")
        if not value:
            missing_env.append(key)
    for key in OPTIONAL_ENV:
        value = os.environ.get(key)
        print(f"  {key}: {'SET' if value else 'optional'}")

    # Vault 数据检查
    print("\nVault data:")
    vault_counts = count_vault_files()
    for name, count in vault_counts.items():
        print(f"  {name}: {count} files")
    total_vault = sum(vault_counts.values())
    print(f"  total: {total_vault} files")

    # 治理模块检查
    gov_dir = PROJECT_ROOT / "scripts" / "governance"
    gov_modules = list(gov_dir.glob("*.py")) if gov_dir.exists() else []
    print(f"\nGovernance modules: {len(gov_modules)}")
    for mod in sorted(gov_modules, key=lambda p: p.name):
        print(f"  {mod.name}")

    # 判定
    if missing_env:
        print(f"\nNOT READY: missing required env vars: {missing_env}")
        print("Next: fill cloud/volcengine/.env.volcengine after creating TOS bucket and IAM keys.")
        return 1

    if not deps["tos"]:
        print("\nNOT READY: TOS credentials exist, but tos SDK is missing.")
        print("Next: pip install -r cloud/volcengine/requirements.volcengine.txt")
        return 1

    if total_vault == 0:
        print("\nWARNING: Vault data is empty. Upload will have nothing to send.")
        print("Next: run scripts/ingest_purchased.py to populate Vault before upload.")

    dds_mode = os.environ.get("DDS_MODE", "local")
    print(f"\nDDS_MODE: {dds_mode}")

    print("\nREADY: env, TOS credentials, and SDK are sufficient for cloud upload.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
