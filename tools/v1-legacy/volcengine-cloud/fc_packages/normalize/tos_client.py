#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS TOS 客户端封装：线程安全 + thread-local 连接复用。

参考 ArchLib upload_tos_seed.py 的 thread-local 模式，
避免每个文件上传都重建 TCP+TLS 连接。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_write_lock = threading.Lock()
_thread_local = threading.local()


def load_dotenv(path: Path) -> None:
    """加载 .env 文件到 os.environ（不覆盖已有值）。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_tos_config() -> dict[str, str]:
    """从环境变量获取 TOS 配置。"""
    return {
        "bucket": os.environ.get("DDS_TOS_BUCKET", ""),
        "endpoint": os.environ.get("DDS_TOS_ENDPOINT", ""),
        "region": os.environ.get("DDS_TOS_REGION", "cn-shanghai"),
        "ak": os.environ.get("DDS_TOS_ACCESS_KEY_ID", ""),
        "sk": os.environ.get("DDS_TOS_SECRET_ACCESS_KEY", ""),
        "security_token": os.environ.get("DDS_TOS_SECURITY_TOKEN", ""),
        "role_name": os.environ.get("DDS_TOS_ROLE_NAME", ""),
    }


def disable_proxy() -> None:
    """禁用代理（TOS SDK 直连）。"""
    if os.environ.get("DDS_TOS_DISABLE_PROXY", "1") != "0":
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                     "http_proxy", "https_proxy", "all_proxy"):
            os.environ.pop(key, None)
        os.environ.setdefault("NO_PROXY", "*")


def get_thread_local_client():
    """获取线程本地 TOS 客户端（首次调用时创建）。"""
    import tos
    client = getattr(_thread_local, "client", None)
    if client is None:
        cfg = get_tos_config()
        if cfg["role_name"]:
            credentials_provider = tos.EcsCredentialsProvider(cfg["role_name"])
        else:
            if not cfg["ak"] or not cfg["sk"]:
                raise RuntimeError(
                    "TOS runtime identity is not configured; set a role or complete runtime credentials"
                )
            credentials_provider = tos.StaticCredentialsProvider(
                cfg["ak"], cfg["sk"], cfg["security_token"] or None
            )
        client = tos.TosClientV2(
            endpoint=cfg["endpoint"],
            region=cfg["region"],
            credentials_provider=credentials_provider,
        )
        # 超时：(connect=30s, read=120s)
        if hasattr(client, "http_client"):
            client.http_client.timeout = (30, 120)
        _thread_local.client = client
    return client


def write_status(path: Path, row: dict[str, Any]) -> None:
    """线程安全地追加 JSONL 状态记录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now().isoformat(timespec="seconds"), **row}
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_done_keys(path: Path) -> set[str]:
    """加载已完成的 object_key 集合（用于断点续传）。"""
    if not path.exists():
        return set()
    done: set[str] = set()
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("status") in {"uploaded", "exists_remote"} and row.get("object_key"):
                done.add(str(row["object_key"]))
    return done


def upload_one(
    bucket: str,
    row: dict,
    global_idx: int,
    total_rows: int,
    status_file: Path,
    done_keys: set,
    max_retries: int = 3,
    head_before_put: bool = False,
) -> dict[str, Any]:
    """上传单个文件到 TOS。支持断点续传和重试。"""
    client = get_thread_local_client()

    local_path = Path(row.get("local_path") or "")
    object_key = row.get("object_key", "")

    if not local_path.exists() or not object_key:
        result = {"status": "missing_local", "object_key": object_key,
                   "path": str(local_path)}
        write_status(status_file, result)
        print(f"SKIP MISSING {global_idx}/{total_rows} {object_key or local_path}", flush=True)
        return result

    if object_key in done_keys:
        return {"status": "skipped_status", "object_key": object_key}

    if head_before_put:
        try:
            client.head_object(bucket, object_key)
            result = {"status": "exists_remote", "object_key": object_key,
                       "byte_size": row.get("byte_size")}
            write_status(status_file, result)
            done_keys.add(object_key)
            print(f"SKIP REMOTE {global_idx}/{total_rows} {object_key}", flush=True)
            return result
        except Exception:
            pass

    import time
    ok = False
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            file_size = local_path.stat().st_size
            # 大文件用分片上传，小文件直接 put
            if file_size > 20 * 1024 * 1024:
                client.upload_file(bucket, object_key, str(local_path),
                                   task_num=4, part_size=20 * 1024 * 1024,
                                   enable_checkpoint=True)
            else:
                client.put_object_from_file(bucket, object_key, str(local_path))
            ok = True
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= max_retries:
                result = {"status": "failed", "object_key": object_key,
                           "error": last_error[:500]}
                write_status(status_file, result)
                print(f"FAILED {global_idx}/{total_rows} {object_key}: {last_error}", flush=True)
                return result
            print(f"RETRY {attempt}/{max_retries} {global_idx}/{total_rows} {object_key}: {type(exc).__name__}", flush=True)
            time.sleep(min(5, attempt * 2))

    if ok:
        result = {"status": "uploaded", "object_key": object_key,
                   "byte_size": row.get("byte_size")}
        write_status(status_file, result)
        print(f"UPLOADED {global_idx}/{total_rows} {object_key}", flush=True)
        return result

    return {"status": "unknown_error", "object_key": object_key}


def head_verify_one(
    bucket: str,
    object_key: str,
    global_idx: int,
    total_rows: int,
    status_file: Path,
) -> dict[str, Any]:
    """HEAD 验证 TOS 对象是否存在。"""
    client = get_thread_local_client()
    try:
        resp = client.head_object(bucket, object_key)
        content_length = 0
        if hasattr(resp, "content_length"):
            content_length = resp.content_length or 0
        result = {"status": "verified", "object_key": object_key,
                   "byte_size": content_length}
        write_status(status_file, result)
        print(f"VERIFIED {global_idx}/{total_rows} {object_key}", flush=True)
        return result
    except Exception as exc:
        result = {"status": "missing", "object_key": object_key,
                   "error": f"{type(exc).__name__}: {exc}"[:500]}
        write_status(status_file, result)
        print(f"MISSING {global_idx}/{total_rows} {object_key}: {type(exc).__name__}", flush=True)
        return result


def download_one(
    bucket: str,
    object_key: str,
    local_path: Path,
    max_retries: int = 3,
) -> bool:
    """从 TOS 下载单个文件到本地。用于 query_local.py 的 TOS fallback。"""
    import time
    client = get_thread_local_client()
    local_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_retries + 1):
        try:
            client.get_object_to_file(bucket, object_key, str(local_path))
            return True
        except Exception as exc:
            print(f"DOWNLOAD RETRY {attempt}/{max_retries} {object_key}: {type(exc).__name__}: {exc}", flush=True)
            if attempt >= max_retries:
                return False
            time.sleep(min(5, attempt * 2))
    return False
