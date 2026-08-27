#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 容器健康检查脚本。

调用 DDS_HEALTHCHECK_URL（默认 http://127.0.0.1:8080/api/health），
检查返回 JSON 的 ok 字段和数据池城市数量。

用法（Dockerfile HEALTHCHECK 自动调用）：
    python -B /app/cloud/volcengine/service/healthcheck.py

退出码：
    0 = 健康
    1 = 不健康（Flask 未启动 / ok=false / 城市数不足）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def int_env(name: str, default: int) -> int:
    """安全读取整数型环境变量，解析失败时返回默认值。"""
    try:
        return int(os.environ.get(name, str(default)))
    except (ValueError, TypeError):
        return default


def main() -> int:
    # 健康检查端点 URL（默认指向容器内部 Flask）
    url = os.environ.get(
        "DDS_HEALTHCHECK_URL",
        "http://127.0.0.1:8080/api/health",
    )
    # HTTP 请求超时（秒）
    timeout = int_env("DDS_HEALTHCHECK_TIMEOUT", 10)
    # 数据池至少要有 N 个城市的数据才算健康
    min_cities = int_env("DDS_HEALTHCHECK_MIN_CITIES", 1)

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        print(f"unhealthy: 无法连接 Flask 服务 — {type(exc).__name__}: {exc}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"unhealthy: 健康端点返回非 JSON — {exc}")
        return 1

    # 检查 ok 标志位
    if not payload.get("ok"):
        issues = payload.get("issues") or []
        print(f"unhealthy: ok 标志为 false — issues: {issues}")
        return 1

    # 检查数据池城市数量
    cities = int(payload.get("cities") or 0)
    if cities < min_cities:
        print(f"unhealthy: 数据池城市数 {cities} < 最低要求 {min_cities}")
        return 1

    mode = payload.get("mode", "unknown")
    print(f"healthy: cities={cities} mode={mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
