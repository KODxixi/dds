#!/usr/bin/env python3
"""
DDS 火山云 HMAC v3 联调验证脚本
==================================
从 GarchOS 运行环境执行，验证与 DDS 的 HMAC 验签通路。

用法：
  python3 verify_dds_hmac.py --secret <DDS_API_SECRET> --base-url <DDS_BASE_URL>

DDS_BASE_URL 示例：
  - 火山云 ECS 内网 IP: http://10.x.x.x
  - 火山云 CLB 私网: http://internal-clb-xxx.cn-shanghai.volces.com
  - Docker Compose 同网络: http://dds-web

环境变量：
  DDS_API_SECRET    - HMAC 共享密钥（也可通过 --secret 传入）
  DDS_BASE_URL      - DDS 私网地址（也可通过 --base-url 传入）
  DDS_API_KEY_ID    - 密钥对标识（默认 garchos-web-v1）
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
import urllib.request
from urllib import parse as urlparse
from urllib.error import HTTPError, URLError


def sign_request(method: str, path: str, query_params: list[tuple[str, str]],
                 user_id: str, role: str, body_bytes: bytes,
                 secret: bytes, key_id: str) -> dict[str, str]:
    """按 v3 契约生成 6 个 HMAC 请求头。"""
    ts = int(time.time())
    request_id = str(uuid.uuid4())
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()

    qsl = sorted(query_params, key=lambda p: p[0])
    qs = urlparse.urlencode(qsl, doseq=True, safe="")
    cpq = f"{path}?{qs}" if qs else path

    canonical = "\n".join((
        "v1", key_id, str(ts), method.upper(), cpq,
        user_id, role, request_id, body_sha256,
    ))
    sig = hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    return {
        "X-DDS-Key-ID": key_id,
        "X-DDS-Request-ID": request_id,
        "X-DDS-Content-SHA256": body_sha256,
        "X-DDS-Signature": f"t={ts},v1={sig}",
        "X-GarchOS-User-ID": user_id,
        "X-GarchOS-User-Role": role,
    }


def request_json(method: str, url: str, headers: dict | None = None,
                 body: bytes | None = None, timeout: int = 30) -> tuple[int, dict]:
    """发送 HTTP 请求并返回 (status_code, json_body)。"""
    req = urllib.request.Request(url, data=body, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}
    except URLError as e:
        return 0, {"error": f"URLError: {e.reason}"}


def main():
    parser = argparse.ArgumentParser(description="DDS HMAC v3 联调验证")
    parser.add_argument("--secret", help="DDS_API_SECRET")
    parser.add_argument("--base-url", help="DDS 私网地址")
    parser.add_argument("--key-id", default="garchos-web-v1", help="密钥对标识")
    args = parser.parse_args()

    secret_raw = args.secret or os.environ.get("DDS_API_SECRET", "")
    base_url = (args.base_url or os.environ.get("DDS_BASE_URL", "")).rstrip("/")
    key_id = args.key_id or os.environ.get("DDS_API_KEY_ID", "garchos-web-v1")

    if not secret_raw:
        print("FATAL: DDS_API_SECRET 未设置。请通过 --secret 或环境变量传入。")
        print("  Secret 不要打印到日志中。")
        sys.exit(1)
    if not base_url:
        print("FATAL: DDS_BASE_URL 未设置。请通过 --base-url 或环境变量传入。")
        sys.exit(1)

    secret = secret_raw.encode("utf-8")

    print(f"DDS_BASE_URL = {base_url}")
    print(f"DDS_API_KEY_ID = {key_id}")
    print(f"Secret length = {len(secret)} bytes")
    print()

    passed = 0
    failed = 0

    # ── [1] GET /api/health 无签名 → 200 ──
    url = f"{base_url}/api/health"
    status, body = request_json("GET", url)
    if status == 200 and body.get("ok"):
        print(f"[1] PASS  GET /api/health (no auth) → {status} ok={body['ok']}")
        passed += 1
    else:
        print(f"[1] FAIL  GET /api/health (no auth) → {status} {body}")
        failed += 1

    # ── [2] GET /dds/ 无签名 → 401 ──
    url = f"{base_url}/dds/"
    status, body = request_json("GET", url)
    if status == 401 and body.get("code") == "UNAUTHORIZED":
        print(f"[2] PASS  GET /dds/ (no auth) → {status} {body.get('code')}")
        passed += 1
    else:
        print(f"[2] FAIL  GET /dds/ (no auth) → {status} {body}")
        failed += 1

    # ── [3] GET /dds/ 合法签名 → 200 ──
    url = f"{base_url}/dds/"
    headers = sign_request("GET", "/dds/", [], "garchos-test-user", "member", b"", secret, key_id)
    status, body = request_json("GET", url, headers=headers)
    if status == 200:
        print(f"[3] PASS  GET /dds/ (signed member) → {status}")
        passed += 1
    else:
        print(f"[3] FAIL  GET /dds/ (signed member) → {status} {body}")
        failed += 1

    # ── [4] GET /dds/ 错误签名 → 401 ──
    headers = sign_request("GET", "/dds/", [], "garchos-test-user", "member", b"", secret, key_id)
    headers["X-DDS-Signature"] = headers["X-DDS-Signature"].replace("v1=", "v1=deadbeef00000000000000000000000000000000000000000000000000000000")
    status, body = request_json("GET", url, headers=headers)
    if status == 401:
        print(f"[4] PASS  GET /dds/ (bad sig) → {status} {body.get('code')}")
        passed += 1
    else:
        print(f"[4] FAIL  GET /dds/ (bad sig) → {status} {body}")
        failed += 1

    # ── [5] GET /dds/ 过期签名 → 401 ──
    old_ts = int(time.time()) - 400
    old_rid = str(uuid.uuid4())
    old_body_sha256 = hashlib.sha256(b"").hexdigest()
    old_canonical = "\n".join((
        "v1", key_id, str(old_ts), "GET", "/dds/",
        "garchos-test-user", "member", old_rid, old_body_sha256,
    ))
    old_sig = hmac.new(secret, old_canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    old_headers = {
        "X-DDS-Key-ID": key_id,
        "X-DDS-Request-ID": old_rid,
        "X-DDS-Content-SHA256": old_body_sha256,
        "X-DDS-Signature": f"t={old_ts},v1={old_sig}",
        "X-GarchOS-User-ID": "garchos-test-user",
        "X-GarchOS-User-Role": "member",
    }
    status, body = request_json("GET", url, headers=old_headers)
    if status == 401:
        print(f"[5] PASS  GET /dds/ (expired sig) → {status} {body.get('code')}")
        passed += 1
    else:
        print(f"[5] FAIL  GET /dds/ (expired sig) → {status} {body}")
        failed += 1

    print()
    print("=" * 55)
    print(f"Results: {passed}/{passed + failed} passed")
    if failed:
        print("SOME TESTS FAILED — 请检查 DDS 部署状态")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED — GarchOS ↔ DDS HMAC 验签通路正常")
        sys.exit(0)


if __name__ == "__main__":
    main()