#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 云端架构验证（纯本地检查，不上传不下载）。

检查 cloud/volcengine/ 目录结构完整性、Python 脚本语法正确性、
环境变量模板完整性、服务文件存在性、tos_client 可导入性。
输出 JSON 报告: {"architecture_ready": bool, "checks": [...], "missing": [...]}

用法:
    python validate_cloud_architecture.py
    python validate_cloud_architecture.py --out report.json
"""
from __future__ import annotations

import argparse
import json
import py_compile
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLOUD_DIR = Path(__file__).resolve().parent  # cloud/volcengine/

# 预期 cloud/volcengine/ 目录下的文件
EXPECTED_SCRIPTS = [
    "tos_client.py",
    "tos_data_lake.py",
    "pipeline_scheduler.py",
    "publish_tos_release.py",
    "sync_tos_data.py",
    "validate_cloud_architecture.py",
    "volcengine_env_check.py",
    ".env.volcengine.example",
    "requirements.volcengine.txt",
]

# .env.volcengine.example 中必需的环境变量
REQUIRED_ENV_VARS = [
    "DDS_TOS_BUCKET",
    "DDS_TOS_ENDPOINT",
    "DDS_TOS_REGION",
    "DDS_TOS_ACCESS_KEY_ID",
    "DDS_TOS_SECRET_ACCESS_KEY",
]

# 预期 service/ 目录下的文件
EXPECTED_SERVICE_FILES = [
    "Dockerfile",
    "docker-compose.yml",
]


def check_directory_structure() -> dict[str, Any]:
    """检查 cloud/volcengine/ 目录结构完整性。"""
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    for filename in EXPECTED_SCRIPTS:
        filepath = CLOUD_DIR / filename
        exists = filepath.exists()
        checks.append({
            "check": f"file_exists: {filename}",
            "status": "pass" if exists else "fail",
        })
        if not exists:
            missing.append(str(filepath))

    return {"checks": checks, "missing": missing}


def check_python_syntax() -> dict[str, Any]:
    """检查所有 Python 脚本语法正确性（py_compile）。"""
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    py_files = sorted(CLOUD_DIR.glob("*.py"))
    for py_file in py_files:
        try:
            py_compile.compile(str(py_file), doraise=True)
            checks.append({
                "check": f"py_compile: {py_file.name}",
                "status": "pass",
            })
        except py_compile.PyCompileError as exc:
            checks.append({
                "check": f"py_compile: {py_file.name}",
                "status": "fail",
                "error": str(exc),
            })
            missing.append(str(py_file))

    return {"checks": checks, "missing": missing}


def check_env_template() -> dict[str, Any]:
    """检查 .env.volcengine.example 存在且包含所有必需变量。"""
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    env_file = CLOUD_DIR / ".env.volcengine.example"
    if not env_file.exists():
        checks.append({
            "check": "env_template_exists",
            "status": "fail",
        })
        missing.append(str(env_file))
        return {"checks": checks, "missing": missing}

    checks.append({
        "check": "env_template_exists",
        "status": "pass",
    })

    content = env_file.read_text(encoding="utf-8-sig")
    for var in REQUIRED_ENV_VARS:
        if var in content:
            checks.append({
                "check": f"env_var_defined: {var}",
                "status": "pass",
            })
        else:
            checks.append({
                "check": f"env_var_defined: {var}",
                "status": "fail",
            })
            missing.append(var)

    return {"checks": checks, "missing": missing}


def check_service_files() -> dict[str, Any]:
    """检查 service/ 目录下 Dockerfile 等文件存在。"""
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    service_dir = CLOUD_DIR / "service"
    if not service_dir.exists():
        for f in EXPECTED_SERVICE_FILES:
            checks.append({
                "check": f"service_file: {f}",
                "status": "fail",
                "note": "service/ directory does not exist",
            })
            missing.append(str(service_dir / f))
        return {"checks": checks, "missing": missing}

    for f in EXPECTED_SERVICE_FILES:
        filepath = service_dir / f
        exists = filepath.exists()
        checks.append({
            "check": f"service_file: {f}",
            "status": "pass" if exists else "fail",
        })
        if not exists:
            missing.append(str(filepath))

    return {"checks": checks, "missing": missing}


def check_tos_client_import() -> dict[str, Any]:
    """检查 tos_client.py 可被 import 且包含所有必需函数。"""
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    # 临时添加 cloud/volcengine/ 到 sys.path
    original_path = sys.path[:]
    sys.path.insert(0, str(CLOUD_DIR))
    try:
        import tos_client

        # 检查关键函数是否存在
        required_functions = [
            "load_dotenv",
            "get_tos_config",
            "disable_proxy",
            "get_thread_local_client",
            "write_status",
            "load_done_keys",
            "upload_one",
            "head_verify_one",
            "download_one",
        ]
        for func_name in required_functions:
            if hasattr(tos_client, func_name):
                checks.append({
                    "check": f"tos_client.function: {func_name}",
                    "status": "pass",
                })
            else:
                checks.append({
                    "check": f"tos_client.function: {func_name}",
                    "status": "fail",
                })
                missing.append(f"tos_client.{func_name}")

    except Exception as exc:
        checks.append({
            "check": "tos_client_import",
            "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
        })
        missing.append("tos_client (import failed)")
    finally:
        sys.path = original_path

    return {"checks": checks, "missing": missing}


def validate() -> dict[str, Any]:
    """执行全部架构验证检查，返回 JSON 报告。"""
    all_checks: list[dict[str, Any]] = []
    all_missing: list[str] = []

    # 1. 目录结构完整性
    result = check_directory_structure()
    all_checks.extend(result["checks"])
    all_missing.extend(result["missing"])

    # 2. Python 脚本语法
    result = check_python_syntax()
    all_checks.extend(result["checks"])
    all_missing.extend(result["missing"])

    # 3. 环境变量模板
    result = check_env_template()
    all_checks.extend(result["checks"])
    all_missing.extend(result["missing"])

    # 4. 服务文件
    result = check_service_files()
    all_checks.extend(result["checks"])
    all_missing.extend(result["missing"])

    # 5. tos_client 可导入
    result = check_tos_client_import()
    all_checks.extend(result["checks"])
    all_missing.extend(result["missing"])

    architecture_ready = len(all_missing) == 0

    passed = sum(1 for c in all_checks if c["status"] == "pass")
    failed = sum(1 for c in all_checks if c["status"] == "fail")

    return {
        "architecture_ready": architecture_ready,
        "checks": all_checks,
        "missing": all_missing,
        "summary": {
            "total_checks": len(all_checks),
            "passed": passed,
            "failed": failed,
            "missing_count": len(all_missing),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DDS 云端架构验证 — 纯本地检查，不上传不下载"
    )
    parser.add_argument("--out", help="写报告到指定 JSON 文件路径")
    args = parser.parse_args()

    report = validate()

    # 输出 JSON
    output = json.dumps(report, ensure_ascii=False, indent=2)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"报告已写入: {out_path}")

    # 打印摘要
    summary = report["summary"]
    print(f"\nDDS 云端架构验证")
    print(f"{'=' * 60}")
    ready_str = "YES" if report["architecture_ready"] else "NO"
    print(f"  architecture_ready: {ready_str}")
    print(f"  total checks:       {summary['total_checks']}")
    print(f"  passed:             {summary['passed']}")
    print(f"  failed:             {summary['failed']}")
    print(f"  missing:            {summary['missing_count']}")

    if report["missing"]:
        print(f"\n缺失项:")
        for m in report["missing"]:
            print(f"  - {m}")

    # 详细检查结果
    if args.out:
        print(f"\n详细报告: {args.out}")
    else:
        print(f"\n详细检查:")
        for c in report["checks"]:
            status = "[PASS]" if c["status"] == "pass" else "[FAIL]"
            line = f"  {status} {c['check']}"
            if "error" in c:
                line += f"  -- {c['error']}"
            if "note" in c:
                line += f"  -- {c['note']}"
            print(line)

    return 0 if report["architecture_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
