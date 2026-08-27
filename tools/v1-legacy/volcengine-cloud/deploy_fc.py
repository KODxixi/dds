#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 火山引擎 FC (veFaaS) 函数部署脚本。

完成以下操作：
  Step 1: 上传 FC 函数包到 TOS dds-data-lake bucket 的 fc_packages/ 前缀下
  Step 2: 创建两个 FC 函数（如果已存在则先删除再创建）
  Step 3: 配置 TOS 事件触发器（vault/ + .csv → FC 函数）
  Step 4: 验证部署结果（列出函数 + 获取详情 + 列出通知配置）

用法：
  cd C:\\Users\\shiguanyu\\DDS
  "C:\\Program Files\\Python313\\python.exe" deploy_fc.py
"""
from __future__ import annotations

import os
import sys
import json
import re
import time
import traceback
import zipfile
from pathlib import Path
from datetime import datetime

# ── 路径常量 ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(r"D:\Vault-assets\AI_Projects\DDS")
ENV_FILE = PROJECT_ROOT / "cloud" / "volcengine" / ".env.volcengine"
FC_PKG_DIR = PROJECT_ROOT / "cloud" / "volcengine" / "fc_packages"

# FC 函数定义
FUNCTIONS = [
    {
        "name": "dds-pipeline-governance",
        "zip_file": FC_PKG_DIR / "dds-fc-governance.zip",
        "tos_key": "fc_packages/dds-fc-governance.zip",
        "runtime": "python3.9/v1",
        "command": "main.handler",
        "memory_mb": 512,
        "request_timeout": 300,
        "description": "DDS 云端治理管道：T7 清洗 → T8 质量评估 → P0 门禁",
    },
    {
        "name": "dds-pipeline-normalize",
        "zip_file": FC_PKG_DIR / "dds-fc-normalize.zip",
        "tos_key": "fc_packages/dds-fc-normalize.zip",
        "runtime": "python3.9/v1",
        "command": "main.handler",
        "memory_mb": 1024,
        "request_timeout": 300,
        "description": "DDS 云端归一化：CSV → DuckDB → Parquet (zstd)",
    },
]

# TOS 事件触发器配置
TRIGGERS = [
    {
        "rule_id": "dds-governance-trigger",
        "function_name": "dds-pipeline-governance",
        "prefix": "vault/",
        "suffix": ".csv",
        "events": ["tos:ObjectCreated:Put", "tos:ObjectCreated:Post"],
    },
    {
        "rule_id": "dds-normalize-trigger",
        "function_name": "dds-pipeline-normalize",
        "prefix": "vault/",
        "suffix": ".csv",
        "events": ["tos:ObjectCreated:Put", "tos:ObjectCreated:Post"],
    },
]

# ── 颜色输出 ──────────────────────────────────────────────────────
def _ts():
    return datetime.now().strftime("%H:%M:%S")

def info(msg):
    print(f"[{_ts()}] [INFO]  {msg}")

def ok(msg):
    print(f"[{_ts()}] [OK]    {msg}")

def warn(msg):
    print(f"[{_ts()}] [WARN]  {msg}")

def err(msg):
    print(f"[{_ts()}] [ERROR] {msg}")

def step(num, msg):
    print(f"\n{'='*60}")
    print(f"[{_ts()}] STEP {num}: {msg}")
    print(f"{'='*60}\n")


# ── 环境变量加载 ──────────────────────────────────────────────────
def load_env(path: Path) -> dict[str, str]:
    """加载 .env 文件，返回 key-value 字典。"""
    env = {}
    if not path.exists():
        err(f"环境文件不存在: {path}")
        sys.exit(1)
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def disable_proxy():
    """禁用代理设置，确保 TOS/FC 直连。"""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    os.environ.setdefault("NO_PROXY", "*")


# ── 结果追踪 ──────────────────────────────────────────────────────
results: list[dict] = []


def record(step_name: str, status: str, detail: str = ""):
    results.append({"step": step_name, "status": status, "detail": detail})


class UnsafeFunctionPackageError(RuntimeError):
    """FC package failed closed without disclosing matched secret material."""

    def __init__(self, zip_path: Path, reason: str):
        self.reason = reason
        super().__init__(
            f"FC package security validation failed: archive={zip_path.name}, reason={reason}"
        )


_SENSITIVE_FILE_NAMES = frozenset({
    "credentials",
    "credentials.json",
    "credentials.ini",
    "credentials.yaml",
    "credentials.yml",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "private.key",
    "private_key",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "service-account.json",
    "service_account.json",
})
_SENSITIVE_FILE_SUFFIXES = (
    ".credentials",
    ".jks",
    ".key",
    ".keystore",
    ".kdbx",
    ".p12",
    ".pfx",
)
_PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)
_CREDENTIAL_PATTERNS = (
    re.compile(
        rb"(?im)^\s*(?:export\s+)?(?:DDS_TOS_SECRET_ACCESS_KEY|"
        rb"VOLC_SECRETKEY|VOLCENGINE_SECRET_KEY|AWS_SECRET_ACCESS_KEY|DDS_TOS_SECURITY_TOKEN|"
        rb"ANTHROPIC_AUTH_TOKEN|OPENAI_API_KEY|ARK_API_KEY|API_TOKEN)"
        rb"\s*[:=]\s*[\"']?[^\s#\"']{12,}"
    ),
    re.compile(
        rb"(?i)[\"'](?:secret_access_key|access_key_secret|api[_-]?token|"
        rb"auth[_-]?token|private_key)[\"']\s*:\s*[\"'][^\"'\r\n]{12,}[\"']"
    ),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
)
_SCAN_CHUNK_SIZE = 64 * 1024
_SCAN_OVERLAP = 4096


def _sensitive_filename_reason(entry_name: str) -> str | None:
    parts = [
        part.casefold()
        for part in entry_name.replace("\\", "/").split("/")
        if part not in {"", ".", ".."}
    ]
    if not parts:
        return None
    if any(part.startswith(".env") for part in parts):
        return "dotenv-file"

    basename = parts[-1]
    if basename in _SENSITIVE_FILE_NAMES:
        return "credential-file"
    if basename.endswith(_SENSITIVE_FILE_SUFFIXES):
        return "credential-file"
    if basename.endswith(("-credentials.json", "_credentials.json")):
        return "credential-file"
    return None


def _sensitive_content_reason(stream) -> str | None:
    tail = b""
    while True:
        chunk = stream.read(_SCAN_CHUNK_SIZE)
        if not chunk:
            return None
        window = tail + chunk
        if any(marker in window for marker in _PRIVATE_KEY_MARKERS):
            return "private-key-material"
        if any(pattern.search(window) for pattern in _CREDENTIAL_PATTERNS):
            return "credential-material"
        tail = window[-_SCAN_OVERLAP:]


def validate_fc_package(zip_path: Path) -> None:
    """Reject sensitive package entries without logging names or matched values."""
    zip_path = Path(zip_path)
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            for entry in archive.infolist():
                filename_reason = _sensitive_filename_reason(entry.filename)
                if filename_reason:
                    raise UnsafeFunctionPackageError(zip_path, filename_reason)
                if entry.is_dir():
                    continue
                if entry.flag_bits & 0x1:
                    raise UnsafeFunctionPackageError(zip_path, "unreadable-encrypted-entry")
                with archive.open(entry, "r") as stream:
                    content_reason = _sensitive_content_reason(stream)
                if content_reason:
                    raise UnsafeFunctionPackageError(zip_path, content_reason)
    except UnsafeFunctionPackageError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise UnsafeFunctionPackageError(zip_path, "unreadable-archive") from exc


# ═══════════════════════════════════════════════════════════════════
#  Step 0: 重建 FC 函数包（main.py → index.py）
# ═══════════════════════════════════════════════════════════════════

def step0_rebuild_packages():
    """重建 FC 函数包，将 main.py 重命名为 index.py。

    veFaaS python3.9/v1 运行时要求入口文件为 index.py（默认 handler 函数）。
    原始函数包使用 main.py 作为入口，需要重命名后重新打包。
    """
    step(0, "重建 FC 函数包（main.py → index.py）")

    import shutil

    safe_functions = []
    for func in FUNCTIONS:
        zip_path = func["zip_file"]
        name = func["name"]

        if not zip_path.exists():
            err(f"函数包不存在: {zip_path}")
            record(f"重建 {name}", "FAILED", f"文件不存在: {zip_path}")
            continue

        validate_fc_package(zip_path)
        safe_functions.append(func)

    for func in safe_functions:
        zip_path = func["zip_file"]
        name = func["name"]

        # 读取原始 zip 内容
        with zipfile.ZipFile(zip_path, 'r') as zf_orig:
            file_list = zf_orig.namelist()

        has_main = 'main.py' in file_list
        has_index = 'index.py' in file_list

        if has_index and not has_main:
            ok(f"已有 index.py，无需重建: {zip_path.name}")
            record(f"重建 {name}", "SKIPPED", "已有 index.py")
            continue

        if not has_main:
            warn(f"未找到 main.py，跳过: {zip_path.name}")
            record(f"重建 {name}", "SKIPPED", "无 main.py")
            continue

        # 重建：main.py → index.py
        tmp_path = zip_path.with_suffix('.tmp.zip')
        info(f"重建 {zip_path.name}: main.py → index.py")

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf_orig:
                with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf_new:
                    for item in zf_orig.infolist():
                        data = zf_orig.read(item.filename)
                        # 重命名 main.py → index.py
                        new_name = 'index.py' if item.filename == 'main.py' else item.filename
                        zf_new.writestr(new_name, data)

            # 在替换原包前再次验证新包，失败时保留原包不变。
            validate_fc_package(tmp_path)
            shutil.move(str(tmp_path), str(zip_path))
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

        # 验证新 zip
        with zipfile.ZipFile(zip_path, 'r') as zf_check:
            new_files = zf_check.namelist()
            if 'index.py' in new_files:
                size_kb = zip_path.stat().st_size / 1024
                ok(f"重建成功: {zip_path.name} ({size_kb:.1f} KB), 入口文件: index.py")
                record(f"重建 {name}", "OK", f"index.py ({size_kb:.1f} KB)")
            else:
                err(f"重建失败: index.py 未找到")
                record(f"重建 {name}", "FAILED", "index.py 未找到")


# ═══════════════════════════════════════════════════════════════════
#  Step 1: 上传 FC 函数包到 TOS
# ═══════════════════════════════════════════════════════════════════

def step1_upload_packages(env: dict) -> dict[str, str]:
    """上传两个 zip 到 TOS fc_packages/ 前缀，返回 {函数名: TOS source 字符串}。"""
    step(1, "上传 FC 函数包到 TOS")

    safe_functions = []
    for func in FUNCTIONS:
        zip_path = func["zip_file"]
        name = func["name"]
        if not zip_path.exists():
            err(f"函数包不存在: {zip_path}")
            record(f"上传 {name}", "FAILED", f"文件不存在: {zip_path}")
            continue
        validate_fc_package(zip_path)
        safe_functions.append(func)

    if not safe_functions:
        return {}

    import tos

    bucket = env["DDS_TOS_BUCKET"]
    endpoint = env["DDS_TOS_ENDPOINT"]
    region = env["DDS_TOS_REGION"]
    ak = env["DDS_TOS_ACCESS_KEY_ID"]
    sk = env["DDS_TOS_SECRET_ACCESS_KEY"]

    client = tos.TosClientV2(ak, sk, endpoint, region)

    sources: dict[str, str] = {}

    for func in safe_functions:
        name = func["name"]
        zip_path = func["zip_file"]
        tos_key = func["tos_key"]

        # 防止安全扫描和云写入之间包内容被替换。
        validate_fc_package(zip_path)

        file_size = zip_path.stat().st_size
        size_kb = file_size / 1024
        info(f"上传 {zip_path.name} ({size_kb:.1f} KB) → tos://{bucket}/{tos_key}")

        try:
            # 强制重新上传（先删除旧对象）
            try:
                client.delete_object(bucket, tos_key)
                info(f"删除旧对象: {tos_key}")
            except Exception:
                pass  # 对象不存在时忽略

            client.put_object_from_file(bucket, tos_key, str(zip_path))
            ok(f"上传成功: {tos_key}")
            record(f"上传 {name}", "OK", f"{tos_key} ({size_kb:.1f} KB)")

            # source 格式: bucket_name:object_key
            sources[name] = f"{bucket}:{tos_key}"

        except Exception as exc:
            err(f"上传失败 {tos_key}: {type(exc).__name__}: {exc}")
            record(f"上传 {name}", "FAILED", f"{type(exc).__name__}: {exc}")

    return sources


# ═══════════════════════════════════════════════════════════════════
#  Step 2: 创建 FC 函数
# ═══════════════════════════════════════════════════════════════════

def _build_envs(env: dict) -> list:
    """构建 veFaaS 运行时环境变量；凭证永不写入函数 ZIP。"""
    import volcenginesdkvefaas

    env_keys = [
        "DDS_TOS_BUCKET",
        "DDS_TOS_ENDPOINT",
        "DDS_TOS_REGION",
        "DDS_TOS_DISABLE_PROXY",
        "DDS_MODE",
        "DDS_FLASK_HOST",
        "DDS_FLASK_PORT",
        "DDS_TOS_CACHE_DIR",
        "DDS_RELEASE",
        "DDS_TOS_RELEASE_KEY",
    ]
    if env.get("DDS_TOS_ROLE_NAME"):
        env_keys.append("DDS_TOS_ROLE_NAME")
    else:
        env_keys.extend([
            "DDS_TOS_ACCESS_KEY_ID",
            "DDS_TOS_SECRET_ACCESS_KEY",
            "DDS_TOS_SECURITY_TOKEN",
        ])

    envs = []
    for key in env_keys:
        if key in env and env[key]:
            envs.append(volcenginesdkvefaas.EnvForCreateFunctionInput(
                key=key,
                value=env[key],
            ))

    # 额外添加 DDS_FC_TIMEOUT
    envs.append(volcenginesdkvefaas.EnvForCreateFunctionInput(
        key="DDS_FC_TIMEOUT",
        value="300",
    ))

    return envs


def _get_fc_client(env: dict):
    """创建 veFaaS API 客户端。"""
    import volcenginesdkcore
    import volcenginesdkvefaas

    region = env.get("DDS_TOS_REGION", "cn-shanghai")
    ak = env["DDS_TOS_ACCESS_KEY_ID"]
    sk = env["DDS_TOS_SECRET_ACCESS_KEY"]

    configuration = volcenginesdkcore.Configuration()
    configuration.ak = ak
    configuration.sk = sk
    configuration.region = region
    configuration.client_side_validation = True
    volcenginesdkcore.Configuration.set_default(configuration)

    client = volcenginesdkvefaas.VEFAASApi(volcenginesdkcore.ApiClient(configuration))
    return client


def _find_function_id(fc_client, name: str) -> str | None:
    """通过函数名查找函数 ID。"""
    import volcenginesdkvefaas

    try:
        resp = fc_client.list_functions(
            volcenginesdkvefaas.ListFunctionsRequest(
                page_number=1,
                page_size=100,
            )
        )
        for item in resp.items or []:
            if item.name == name:
                return item.id
    except Exception as exc:
        warn(f"列出函数失败: {type(exc).__name__}: {exc}")
    return None


def _delete_function(fc_client, func_id: str, name: str) -> bool:
    """删除已有函数。"""
    import volcenginesdkvefaas

    try:
        info(f"删除已有函数: {name} (id={func_id})")
        fc_client.delete_function(
            volcenginesdkvefaas.DeleteFunctionRequest(id=func_id)
        )
        ok(f"删除成功: {name}")
        # 等待删除完成
        info("等待 5 秒让删除生效...")
        time.sleep(5)
        return True
    except Exception as exc:
        err(f"删除失败 {name}: {type(exc).__name__}: {exc}")
        return False


def _create_function(fc_client, func: dict, source: str, envs: list) -> dict | None:
    """创建 FC 函数。"""
    import volcenginesdkvefaas

    try:
        request = volcenginesdkvefaas.CreateFunctionRequest(
            name=func["name"],
            description=func["description"],
            runtime=func["runtime"],
            source_type="tos",
            source=source,
            memory_mb=func["memory_mb"],
            request_timeout=func["request_timeout"],
            envs=envs,
        )

        info(f"创建函数: {func['name']}")
        info(f"  runtime={func['runtime']}, memory={func['memory_mb']}MB, timeout={func['request_timeout']}s")
        info(f"  source={source}")
        info(f"  envs={len(envs)} 个环境变量")

        resp = fc_client.create_function(request)
        func_id = resp.id
        ok(f"创建成功: {func['name']} (id={func_id})")
        record(f"创建 {func['name']}", "OK", f"id={func_id}")

        return {"name": func["name"], "id": func_id, "resp": resp}

    except Exception as exc:
        err(f"创建失败 {func['name']}: {type(exc).__name__}: {exc}")
        record(f"创建 {func['name']}", "FAILED", f"{type(exc).__name__}: {exc}")
        return None


def step2_create_functions(env: dict, sources: dict[str, str]) -> dict[str, str]:
    """创建两个 FC 函数，返回 {函数名: 函数ID}。"""
    step(2, "创建 FC 函数")

    from volcenginesdkcore.rest import ApiException

    fc_client = _get_fc_client(env)
    envs = _build_envs(env)
    info(f"环境变量: {[e.key for e in envs]}")

    func_ids: dict[str, str] = {}

    for func in FUNCTIONS:
        name = func["name"]
        source = sources.get(name)
        if not source:
            err(f"跳过 {name}: 缺少 TOS source")
            continue

        # 检查是否已存在
        existing_id = _find_function_id(fc_client, name)
        if existing_id:
            warn(f"函数已存在: {name} (id={existing_id})，先删除再创建")
            _delete_function(fc_client, existing_id, name)

        # 创建
        result = _create_function(fc_client, func, source, envs)
        if result:
            func_ids[name] = result["id"]

        # 创建间隔
        time.sleep(2)

    # ── 发布函数（TOS 触发器要求函数已发布） ──────────────────────
    _release_functions(fc_client, func_ids)

    return func_ids


def _release_functions(fc_client, func_ids: dict[str, str]):
    """发布 FC 函数。TOS 事件触发器要求函数必须先发布（release）。"""
    step("2b", "发布 FC 函数（TOS 触发器前置条件）")

    import volcenginesdkvefaas
    from volcenginesdkcore.rest import ApiException

    for name, func_id in func_ids.items():
        info(f"发布函数: {name} (id={func_id})")
        try:
            # 先获取最新 revision 号
            revision_number = 0  # 默认值，新创建函数的 revision 从 0 开始
            try:
                revisions = fc_client.list_revisions(
                    volcenginesdkvefaas.ListRevisionsRequest(
                        function_id=func_id,
                        page_number=1,
                        page_size=10,
                    )
                )
                if revisions.items:
                    # 取最新的 revision
                    revision_number = revisions.items[0].revision_number
                    info(f"  最新 revision: {revision_number}")
                else:
                    info(f"  无 revision 记录，使用默认值: {revision_number}")
            except Exception as exc:
                warn(f"  获取 revision 列表失败: {type(exc).__name__}: {exc}")
                warn(f"  使用默认 revision_number={revision_number}")

            # 发起发布（revision_number 是必填字段，SDK 在构造时即校验）
            release_req = volcenginesdkvefaas.ReleaseRequest(
                function_id=func_id,
                revision_number=revision_number,
                description=f"DDS 部署自动发布 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            )

            resp = fc_client.release(release_req)
            info(f"  发布状态: {resp.status} - {resp.status_message}")
            ok(f"函数已发布: {name}")

            # 等待发布完成
            release_id = resp.release_record_id
            for wait in range(30):
                time.sleep(3)
                try:
                    status_resp = fc_client.get_release_status(
                        volcenginesdkvefaas.GetReleaseStatusRequest(
                            function_id=func_id,
                        )
                    )
                    status = status_resp.status
                    info(f"  发布进度 [{wait+1}/30]: {status} - {status_resp.status_message}")

                    # 状态判断（不区分大小写）
                    status_lower = status.lower() if status else ""
                    if status_lower in ["finished", "finishedwitherrors", "failed", "done"]:
                        if status_lower in ["finished", "done"]:
                            ok(f"发布完成: {name}")
                            record(f"发布 {name}", "OK", f"status={status}")
                        else:
                            err(f"发布异常: {name} status={status} msg={status_resp.status_message}")
                            record(f"发布 {name}", "FAILED", f"status={status}")
                        break
                except Exception as exc:
                    warn(f"  查询发布状态失败: {type(exc).__name__}: {exc}")
            else:
                warn(f"发布超时（30 次轮询），继续后续步骤: {name}")
                record(f"发布 {name}", "TIMEOUT", "30 次轮询未完成")

        except ApiException as exc:
            err(f"发布失败 {name}: ApiException status={exc.status} reason={exc.reason}")
            if exc.body:
                err(f"  body: {exc.body}")
            record(f"发布 {name}", "FAILED", f"ApiException: {exc.status} {exc.reason}")
        except Exception as exc:
            err(f"发布失败 {name}: {type(exc).__name__}: {exc}")
            record(f"发布 {name}", "FAILED", f"{type(exc).__name__}: {exc}")

        time.sleep(2)


# ═══════════════════════════════════════════════════════════════════
#  Step 3: 配置 TOS 事件触发器
# ═══════════════════════════════════════════════════════════════════

def _make_cloud_function_config(trigger: dict, func_id: str, prefix: str = None):
    """构建单个 CloudFunctionConfiguration 对象。"""
    from tos.models2 import (
        CloudFunctionConfiguration,
        Filter,
        FilterKey,
        FilterRule,
    )

    return CloudFunctionConfiguration(
        id=trigger["rule_id"],
        events=trigger["events"],
        cloud_function=func_id,
        filter=Filter(
            key=FilterKey(
                rules=[
                    FilterRule(name="prefix", value=prefix or trigger["prefix"]),
                    FilterRule(name="suffix", value=trigger["suffix"]),
                ]
            )
        ),
    )


def step3_configure_triggers(env: dict, func_ids: dict[str, str]):
    """配置 TOS 事件通知触发器。

    TOS 平台限制：同一 bucket 的通知规则不能 overlap（即两个规则不能有相同的
    prefix+suffix 组合）。当两个触发器的过滤条件完全相同时会报 "rule overlap" 错误。

    本函数策略：
      1. 先尝试同时配置两个触发器（相同 filter）
      2. 如果报 "rule overlap"，改用不同 prefix 策略：
         - governance: vault/2026新楼盘/  (新楼盘 CSV)
         - normalize:  vault/二手房小区/   (二手房 CSV)
      3. 如果仍然失败，只配置 governance 触发器（主管道）
    """
    step(3, "配置 TOS 事件触发器")

    import tos
    from tos.models2 import CloudFunctionConfiguration

    bucket = env["DDS_TOS_BUCKET"]
    endpoint = env["DDS_TOS_ENDPOINT"]
    region = env["DDS_TOS_REGION"]
    ak = env["DDS_TOS_ACCESS_KEY_ID"]
    sk = env["DDS_TOS_SECRET_ACCESS_KEY"]

    client = tos.TosClientV2(ak, sk, endpoint, region)

    # 构建所有 CloudFunctionConfiguration（尝试 1: 相同 filter）
    configs = []
    for trigger in TRIGGERS:
        func_name = trigger["function_name"]
        func_id = func_ids.get(func_name)

        if not func_id:
            err(f"跳过触发器 {trigger['rule_id']}: 函数 {func_name} 未创建")
            record(f"触发器 {trigger['rule_id']}", "SKIPPED", f"函数 {func_name} 不存在")
            continue

        config = _make_cloud_function_config(trigger, func_id)
        configs.append(config)

        info(f"触发器配置: {trigger['rule_id']}")
        info(f"  前缀={trigger['prefix']}, 后缀={trigger['suffix']}")
        info(f"  事件={trigger['events']}")
        info(f"  目标函数={func_name} (id={func_id})")

    if not configs:
        err("没有可配置的触发器")
        return

    # 尝试 1: 同时配置两个触发器（相同 filter）
    try:
        info(f"尝试 1: 同时配置 {len(configs)} 个触发器 (相同 filter)")
        info(f"设置 TOS 事件通知: bucket={bucket}")
        client.put_bucket_notification(
            bucket=bucket,
            cloud_function_configurations=configs,
        )
        ok("TOS 事件通知配置成功（两个触发器同时配置）")
        record("TOS 事件通知", "OK", f"{len(configs)} 个触发器 (相同 filter)")
        return
    except Exception as exc:
        exc_str = str(exc)
        if "rule overlap" not in exc_str and "overlap" not in exc_str.lower():
            err(f"TOS 事件通知配置失败: {type(exc).__name__}: {exc}")
            record("TOS 事件通知", "FAILED", f"{type(exc).__name__}: {exc}")
            return

        warn(f"尝试 1 失败 (rule overlap): TOS 不允许相同 filter 的两个规则")
        info("切换到尝试 2: 使用不同 prefix 避免规则重叠")

    # 尝试 2: 使用不同 prefix（vault/2026新楼盘/ 和 vault/二手房小区/）
    configs_v2 = []
    alt_prefixes = {
        "dds-pipeline-governance": "vault/2026新楼盘/",
        "dds-pipeline-normalize": "vault/二手房小区/",
    }

    for trigger in TRIGGERS:
        func_name = trigger["function_name"]
        func_id = func_ids.get(func_name)
        if not func_id:
            continue
        alt_prefix = alt_prefixes.get(func_name, trigger["prefix"])
        config = _make_cloud_function_config(trigger, func_id, prefix=alt_prefix)
        configs_v2.append(config)

        info(f"触发器配置 (备选): {trigger['rule_id']}")
        info(f"  前缀={alt_prefix}, 后缀={trigger['suffix']}")
        info(f"  目标函数={func_name} (id={func_id})")

    try:
        info(f"尝试 2: 配置 {len(configs_v2)} 个触发器 (不同 prefix)")
        client.put_bucket_notification(
            bucket=bucket,
            cloud_function_configurations=configs_v2,
        )
        ok("TOS 事件通知配置成功（不同 prefix 避免重叠）")
        record("TOS 事件通知", "OK",
               f"{len(configs_v2)} 个触发器 (不同 prefix: 2026新楼盘/ + 二手房小区/)")
        return
    except Exception as exc:
        exc_str = str(exc)
        if "rule overlap" not in exc_str and "overlap" not in exc_str.lower():
            err(f"尝试 2 失败: {type(exc).__name__}: {exc}")
            record("TOS 事件通知", "FAILED", f"尝试 2: {type(exc).__name__}: {exc}")
            return

        warn(f"尝试 2 失败 (rule overlap): 二手房小区/ 前缀可能仍与新楼盘/ 重叠")
        info("切换到尝试 3: 仅配置 governance 触发器（主管道）")

    # 尝试 3: 仅配置 governance 触发器
    gov_trigger = TRIGGERS[0]
    gov_func_id = func_ids.get(gov_trigger["function_name"])
    if not gov_func_id:
        err("governance 函数不存在，无法配置触发器")
        record("TOS 事件通知", "FAILED", "无 governance 函数 ID")
        return

    gov_config = _make_cloud_function_config(gov_trigger, gov_func_id)

    try:
        info(f"尝试 3: 仅配置 governance 触发器")
        info(f"  前缀={gov_trigger['prefix']}, 后缀={gov_trigger['suffix']}")
        info(f"  目标函数={gov_trigger['function_name']} (id={gov_func_id})")
        client.put_bucket_notification(
            bucket=bucket,
            cloud_function_configurations=[gov_config],
        )
        ok("TOS 事件通知配置成功（仅 governance 触发器）")
        warn("注意: normalize 函数未配置 TOS 触发器")
        warn("  normalize 需通过以下方式触发:")
        warn("  1. pipeline_scheduler.py --mode fc --run normalize --city <城市> --execute")
        warn("  2. 或在 governance 函数中添加 normalize 调用（函数链式调用）")
        record("TOS 事件通知", "OK",
               "仅 governance 触发器 (normalize 需手动/链式触发)")
    except Exception as exc:
        err(f"尝试 3 失败: {type(exc).__name__}: {exc}")
        record("TOS 事件通知", "FAILED", f"尝试 3: {type(exc).__name__}: {exc}")


# ═══════════════════════════════════════════════════════════════════
#  Step 4: 验证
# ═══════════════════════════════════════════════════════════════════

def step4_verify(env: dict, func_ids: dict[str, str]):
    """验证部署结果。"""
    step(4, "验证部署结果")

    import volcenginesdkvefaas
    import tos

    # ── 4a: 列出已创建的函数 ──────────────────────────────────────
    info("--- 4a: 列出 FC 函数 ---")
    fc_client = _get_fc_client(env)

    try:
        resp = fc_client.list_functions(
            volcenginesdkvefaas.ListFunctionsRequest(
                page_number=1,
                page_size=100,
            )
        )
        dds_functions = [item for item in (resp.items or []) if item.name in [f["name"] for f in FUNCTIONS]]

        if dds_functions:
            ok(f"找到 {len(dds_functions)} 个 DDS FC 函数:")
            for item in dds_functions:
                print(f"    名称:   {item.name}")
                print(f"    ID:     {item.id}")
                print(f"    运行时: {item.runtime}")
                print(f"    内存:   {item.memory_mb} MB")
                print(f"    超时:   {item.request_timeout}s")
                print(f"    来源:   {item.source_type}")
                print(f"    描述:   {item.description}")
                print(f"    创建:   {item.creation_time}")
                print()
            record("列出函数", "OK", f"{len(dds_functions)} 个函数")
        else:
            warn("未找到 DDS FC 函数")
            record("列出函数", "WARN", "未找到函数")

    except Exception as exc:
        err(f"列出函数失败: {type(exc).__name__}: {exc}")
        record("列出函数", "FAILED", f"{type(exc).__name__}: {exc}")

    # ── 4b: 获取函数详情 ──────────────────────────────────────────
    info("--- 4b: 获取函数详情 ---")
    for func in FUNCTIONS:
        name = func["name"]
        func_id = func_ids.get(name)
        if not func_id:
            warn(f"跳过 {name}: 无函数 ID")
            continue

        try:
            detail = fc_client.get_function(
                volcenginesdkvefaas.GetFunctionRequest(id=func_id)
            )
            ok(f"函数详情: {name}")
            print(f"    ID:           {detail.id}")
            print(f"    名称:         {detail.name}")
            print(f"    运行时:       {detail.runtime}")
            print(f"    内存:         {detail.memory_mb} MB")
            print(f"    超时:         {detail.request_timeout}s")
            print(f"    代码大小:     {detail.code_size} bytes")
            print(f"    来源类型:     {detail.source_type}")
            print(f"    来源:         {detail.source}")
            print(f"    来源地址:     {detail.source_location}")
            print(f"    环境变量数:   {len(detail.envs) if detail.envs else 0}")
            print(f"    触发器数:     {detail.triggers_count}")
            print(f"    创建时间:     {detail.creation_time}")
            print(f"    更新时间:     {detail.last_update_time}")
            print()
            record(f"详情 {name}", "OK", f"id={detail.id}, triggers={detail.triggers_count}")

        except Exception as exc:
            err(f"获取详情失败 {name}: {type(exc).__name__}: {exc}")
            record(f"详情 {name}", "FAILED", f"{type(exc).__name__}: {exc}")

    # ── 4c: 列出 TOS 事件通知配置 ──────────────────────────────────
    info("--- 4c: 列出 TOS 事件通知配置 ---")
    bucket = env["DDS_TOS_BUCKET"]
    endpoint = env["DDS_TOS_ENDPOINT"]
    region = env["DDS_TOS_REGION"]
    ak = env["DDS_TOS_ACCESS_KEY_ID"]
    sk = env["DDS_TOS_SECRET_ACCESS_KEY"]

    tos_client = tos.TosClientV2(ak, sk, endpoint, region)

    try:
        notif = tos_client.get_bucket_notification(bucket=bucket)

        cloud_configs = notif.cloud_function_configurations or []
        ok(f"TOS 事件通知配置: {len(cloud_configs)} 个 CloudFunction 配置")

        for cfg in cloud_configs:
            print(f"    规则 ID:       {cfg.id}")
            print(f"    事件类型:      {cfg.events}")
            print(f"    目标函数:      {cfg.cloud_function}")
            if cfg.filter and cfg.filter.key and cfg.filter.key.rules:
                for rule in cfg.filter.key.rules:
                    print(f"    过滤规则:      {rule.name}={rule.value}")
            print()

        if not cloud_configs:
            warn("TOS 事件通知配置为空")
        record("TOS 通知配置", "OK", f"{len(cloud_configs)} 个配置")

    except Exception as exc:
        err(f"获取 TOS 通知配置失败: {type(exc).__name__}: {exc}")
        record("TOS 通知配置", "FAILED", f"{type(exc).__name__}: {exc}")


# ═══════════════════════════════════════════════════════════════════
#  汇总表
# ═══════════════════════════════════════════════════════════════════

def print_summary():
    """打印部署结果汇总表。"""
    print(f"\n{'='*60}")
    print(f"  部署结果汇总")
    print(f"{'='*60}")
    print(f"{'步骤':<30} {'状态':<10} {'详情'}")
    print(f"{'-'*30} {'-'*10} {'-'*30}")

    ok_count = 0
    fail_count = 0
    skip_count = 0

    for r in results:
        status = r["status"]
        if status == "OK":
            ok_count += 1
        elif status == "FAILED":
            fail_count += 1
        elif status == "SKIPPED":
            skip_count += 1

        print(f"{r['step']:<30} {status:<10} {r['detail']}")

    print(f"{'-'*30} {'-'*10} {'-'*30}")
    print(f"  成功: {ok_count}  失败: {fail_count}  跳过: {skip_count}")
    print(f"{'='*60}\n")


# ═══════════════════════════════════════════════════════════════════
#  主函数
# ═══════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'#'*60}")
    print(f"  DDS 火山引擎 FC 函数部署脚本")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Python: {sys.executable}")
    print(f"  工作目录: {os.getcwd()}")
    print(f"{'#'*60}\n")

    # 加载环境变量
    info(f"加载环境变量: {ENV_FILE}")
    env = load_env(ENV_FILE)
    disable_proxy()

    # 验证必要的环境变量
    required_keys = [
        "DDS_TOS_BUCKET",
        "DDS_TOS_ENDPOINT",
        "DDS_TOS_REGION",
        "DDS_TOS_ACCESS_KEY_ID",
        "DDS_TOS_SECRET_ACCESS_KEY",
    ]
    missing = [k for k in required_keys if not env.get(k)]
    if missing:
        err(f"缺少必要的环境变量: {missing}")
        sys.exit(1)

    ok(f"环境变量加载完成: bucket={env['DDS_TOS_BUCKET']}, region={env['DDS_TOS_REGION']}")

    # 验证函数包存在
    for func in FUNCTIONS:
        if not func["zip_file"].exists():
            err(f"函数包不存在: {func['zip_file']}")
            sys.exit(1)
        size_kb = func["zip_file"].stat().st_size / 1024
        ok(f"函数包就绪: {func['zip_file'].name} ({size_kb:.1f} KB)")

    # Step 0: 重建函数包（main.py → index.py）
    step0_rebuild_packages()

    # Step 1: 上传函数包到 TOS
    sources = step1_upload_packages(env)

    # Step 2: 创建 FC 函数
    func_ids = step2_create_functions(env, sources)

    # Step 3: 配置 TOS 事件触发器
    step3_configure_triggers(env, func_ids)

    # Step 4: 验证
    step4_verify(env, func_ids)

    # 汇总
    print_summary()

    # 退出码
    fail_count = sum(1 for r in results if r["status"] == "FAILED")
    if fail_count > 0:
        warn(f"部署完成，但有 {fail_count} 个步骤失败")
        sys.exit(1)
    else:
        ok("部署全部完成!")
        sys.exit(0)


if __name__ == "__main__":
    main()
