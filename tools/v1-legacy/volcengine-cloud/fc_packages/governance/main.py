#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DDS 云端治理 FC 函数 — TOS 事件触发 → T7 清洗 → T8 质量评估 → quality_gates 门禁。

部署到火山引擎函数计算（FC），配置 TOS 事件触发器：
  - 触发源：dds-data-lake bucket，vault/ 前缀，ObjectCreated 事件
  - 运行时：Python 3.9+
  - 超时：300s（可通过 DDS_FC_TIMEOUT 配置）
  - 内存：512MB+

设计原则：
  - 复用现有 governance/ 脚本（纯标准库），不重写逻辑
  - 从 TOS 下载 CSV → 本地 /tmp 处理 → 结果写回 TOS governance/
  - 门禁失败不阻断 FC 执行，写告警标记到 governance/
  - 所有步骤写 audit log 到 TOS governance/_audit/ 便于追溯

用法（本地测试）：
    python fc_governance.py --object-key "vault/2026新楼盘/新楼盘-三亚.csv"
    python fc_governance.py --csv /tmp/test.csv  # 跳过 TOS 下载，直接本地文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 路径设置 ──────────────────────────────────────────────────────
_FC_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FC_DIR.parents[1]  # DDS 根目录
sys.path.insert(0, str(_FC_DIR))           # tos_client
sys.path.insert(0, str(_PROJECT_ROOT / "scripts" / "governance"))  # T7/T8/gates

import tos_client  # noqa: E402


# ── 配置 ──────────────────────────────────────────────────────────
GOVERNANCE_PREFIX = "governance/"
AUDIT_PREFIX = "governance/_audit/"
FC_TIMEOUT = int(os.environ.get("DDS_FC_TIMEOUT", "300"))


# ═══════════════════════════════════════════════════════════════════
#  TOS 事件解析
# ═══════════════════════════════════════════════════════════════════

def parse_tos_event(event: dict) -> dict | None:
    """解析火山引擎 TOS 事件触发器传来的 event。

    返回 {"bucket": str, "object_key": str}，解析失败返回 None。

    支持的 event 格式：
      1. 标准 TOS 事件：{"Records": [{"tos": {"bucket": {"name": "..."}, "object": {"key": "..."}}}]}
      2. 简化格式：{"bucket": "...", "object_key": "..."}（本地测试用）
    """
    # 标准 TOS 事件格式
    records = event.get("Records", [])
    if records:
        rec = records[0]
        tos_info = rec.get("tos", {})
        bucket_info = tos_info.get("bucket", {})
        obj_info = tos_info.get("object", {})
        bucket_name = bucket_info.get("name", "")
        obj_key = obj_info.get("key", "")
        if bucket_name and obj_key:
            return {"bucket": bucket_name, "object_key": obj_key}

    # 简化格式（本地测试）
    bucket = event.get("bucket", "")
    obj_key = event.get("object_key", "")
    if bucket and obj_key:
        return {"bucket": bucket, "object_key": obj_key}

    return None


# ═══════════════════════════════════════════════════════════════════
#  TOS I/O 辅助
# ═══════════════════════════════════════════════════════════════════

def _ensure_tos_client() -> str:
    """仅从 veFaaS 运行时环境变量/角色配置初始化 TOS 客户端。"""
    tos_client.disable_proxy()
    cfg = tos_client.get_tos_config()
    bucket = cfg["bucket"]
    if not bucket:
        raise RuntimeError("TOS bucket 未配置（DDS_TOS_BUCKET）")
    return bucket


def _download_csv(bucket: str, object_key: str) -> Path:
    """从 TOS 下载 CSV 到临时文件，返回本地路径。"""
    tmp = Path(tempfile.gettempdir()) / "dds_gov_input.csv"
    print(f"[fc_gov] 从 TOS 下载: {bucket}/{object_key} → {tmp}")
    ok = tos_client.download_one(bucket, object_key, tmp)
    if not ok:
        raise RuntimeError(f"TOS 下载失败: {bucket}/{object_key}")
    file_size = tmp.stat().st_size
    print(f"[fc_gov] 下载完成: {file_size:,} bytes")
    return tmp


def _upload_text(bucket: str, object_key: str, content: str) -> None:
    """上传文本内容到 TOS。"""
    import tos
    client = tos_client.get_thread_local_client()
    client.put_object(bucket, object_key, content=content.encode("utf-8"))
    print(f"[fc_gov] 上传: {bucket}/{object_key}")


def _upload_json(bucket: str, object_key: str, data: Any) -> None:
    """上传 JSON 到 TOS。"""
    _upload_text(bucket, object_key, json.dumps(data, ensure_ascii=False, indent=2))


def _upload_file(bucket: str, object_key: str, local_path: Path) -> None:
    """上传本地文件到 TOS。"""
    import tos
    client = tos_client.get_thread_local_client()
    client.put_object_from_file(bucket, object_key, str(local_path))
    print(f"[fc_gov] 上传: {bucket}/{object_key}")


# ═══════════════════════════════════════════════════════════════════
#  治理流水线
# ═══════════════════════════════════════════════════════════════════

def _extract_city_from_key(object_key: str) -> str:
    """从 TOS object key 提取城市名。

    vault/2026新楼盘/新楼盘-三亚.csv → 三亚
    vault/2026新楼盘/新楼盘-杭州.csv → 杭州
    """
    import re
    stem = Path(object_key).stem  # 新楼盘-三亚
    m = re.search(r"新楼盘-(.+)$", stem)
    if m:
        return m.group(1)
    m = re.search(r"二手房小区-(.+)$", stem)
    if m:
        return m.group(1)
    return "unknown"


def run_governance_pipeline(csv_path: Path, city: str, bucket: str,
                            no_upload: bool = False) -> dict[str, Any]:
    """执行完整的治理流水线：T7 → T8 → quality_gates。

    返回 {"status": "ok"|"failed", "t7": {...}, "t8": {...}, "gate": {...}, "audit_key": str}

    参数:
        no_upload: 仅执行治理，不上传结果到 TOS（本地测试用）
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_log: dict[str, Any] = {
        "pipeline": "governance",
        "city": city,
        "csv_path": str(csv_path),
        "started": ts,
        "steps": {},
    }

    # ── T7 清洗 ───────────────────────────────────────────────────
    print(f"\n[fc_gov] === T7 清洗管道 ===")
    try:
        from t7_clean_pipeline import read_csv_rows, clean_pipeline  # noqa: E402
        rows = read_csv_rows(str(csv_path))
        print(f"[fc_gov] T7 读取 {len(rows)} 行")
        cleaned_rows, t7_report = clean_pipeline(rows)
        audit_log["steps"]["t7"] = {
            "status": "ok",
            "input_rows": t7_report["summary"]["input_rows"],
            "clean_pass": t7_report["summary"]["clean_pass_rows"],
            "duplicate": t7_report["summary"]["duplicate_rows"],
            "anomaly": t7_report["summary"]["anomaly_rows"],
        }
        print(f"[fc_gov] T7 完成: 通过 {t7_report['summary']['clean_pass_rows']} / "
              f"重复 {t7_report['summary']['duplicate_rows']} / "
              f"异常 {t7_report['summary']['anomaly_rows']}")

        # 上传 T7 报告到 TOS
        t7_key = f"{GOVERNANCE_PREFIX}t7_clean/{city}/{ts}_clean_report.json"
        if not no_upload:
            _upload_json(bucket, t7_key, t7_report)
        else:
            print(f"[fc_gov] (skip upload) {t7_key}")
    except Exception as exc:
        audit_log["steps"]["t7"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        print(f"[fc_gov] T7 失败: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        # T7 失败不阻断后续，但标记

    # ── T8 质量评估 ───────────────────────────────────────────────
    print(f"\n[fc_gov] === T8 质量评估 ===")
    try:
        from t8_quality_assess import read_csv_rows as t8_read, assess_quality  # noqa: E402
        rows = t8_read(str(csv_path))
        t8_result = assess_quality(rows)
        audit_log["steps"]["t8"] = {
            "status": "ok",
            "overall_score": t8_result["overall_score"],
            "grade": t8_result["grade"],
            "dimensions": {d: info["score"] for d, info in t8_result["dimensions"].items()},
        }
        print(f"[fc_gov] T8 完成: {t8_result['overall_score']} ({t8_result['grade']})")

        # 上传 T8 工件到 TOS
        artifacts = t8_result["artifacts"]
        t8_keys = [
            (f"{GOVERNANCE_PREFIX}t8_quality/{city}/{ts}_quality_report.md",
             "text", artifacts["quality_report_md"]),
            (f"{GOVERNANCE_PREFIX}t8_quality/{city}/{ts}_data_card.md",
             "text", artifacts["data_card_md"]),
            (f"{GOVERNANCE_PREFIX}t8_quality/{city}/{ts}_data_feasibility.md",
             "text", artifacts["data_feasibility_md"]),
        ]
        scores = {d: info["score"] for d, info in t8_result["dimensions"].items()}
        scores["overall"] = t8_result["overall_score"]
        scores["grade"] = t8_result["grade"]
        t8_keys.append(
            (f"{GOVERNANCE_PREFIX}t8_quality/{city}/{ts}_quality_scores.json", "json", scores))
        for key, kind, payload in t8_keys:
            if not no_upload:
                if kind == "text":
                    _upload_text(bucket, key, payload)
                else:
                    _upload_json(bucket, key, payload)
            else:
                print(f"[fc_gov] (skip upload) {key}")
    except Exception as exc:
        audit_log["steps"]["t8"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        print(f"[fc_gov] T8 失败: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ── P0 质量门禁 ───────────────────────────────────────────────
    print(f"\n[fc_gov] === P0 质量门禁 ===")
    gate_result = None
    try:
        from quality_gates import read_csv_rows as gate_read, run_gate  # noqa: E402
        rows = gate_read(str(csv_path))
        gate_result = run_gate(rows, level="P0")
        audit_log["steps"]["gate"] = {
            "status": "ok",
            "passed": gate_result["passed"],
            "level": gate_result["level"],
            "action": gate_result["action"],
            "summary": gate_result["summary"],
            "checks": gate_result["checks"],
        }
        status_icon = "[OK]" if gate_result["passed"] else "[!!]"
        print(f"[fc_gov] P0 门禁: {status_icon} {gate_result['summary']}")

        # 上传门禁报告
        gate_key = f"{GOVERNANCE_PREFIX}gates/{city}/{ts}_p0_gate.json"
        if not no_upload:
            _upload_json(bucket, gate_key, gate_result)
        else:
            print(f"[fc_gov] (skip upload) {gate_key}")

        # 门禁失败时写告警标记
        if not gate_result["passed"]:
            alert = {
                "type": "P0_GATE_FAILED",
                "city": city,
                "timestamp": ts,
                "summary": gate_result["summary"],
                "failed_checks": [c for c in gate_result["checks"] if not c["passed"]],
            }
            alert_key = f"{GOVERNANCE_PREFIX}_alerts/{city}_{ts}_p0_fail.json"
            if not no_upload:
                _upload_json(bucket, alert_key, alert)
            else:
                print(f"[fc_gov] (skip upload) {alert_key}")
            print(f"[fc_gov] [!!] P0 门禁未通过，已写告警")
    except Exception as exc:
        audit_log["steps"]["gate"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        print(f"[fc_gov] 门禁执行失败: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ── 写审计日志 ────────────────────────────────────────────────
    audit_log["finished"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    overall = "ok" if all(
        s.get("status") == "ok" for s in audit_log["steps"].values()
    ) else "partial"
    audit_log["overall_status"] = overall
    audit_key = f"{AUDIT_PREFIX}{city}_{ts}_governance.json"
    if not no_upload:
        _upload_json(bucket, audit_key, audit_log)
        print(f"\n[fc_gov] 审计日志: {bucket}/{audit_key}")
    else:
        print(f"[fc_gov] (skip upload) {audit_key}")

    return {"status": overall, "audit_key": audit_key, "steps": audit_log["steps"]}


# ═══════════════════════════════════════════════════════════════════
#  FC Handler（火山引擎 FC 入口）
# ═══════════════════════════════════════════════════════════════════

def handler(event: dict, context: Any = None) -> dict[str, Any]:
    """火山引擎 FC 函数入口。

    由 TOS 事件触发，解析 event 中的 object key，下载 CSV 后执行治理流水线。

    参数:
        event: TOS 事件 dict（火山引擎 FC 传入）
        context: FC 运行时上下文（未使用）

    返回:
        {"statusCode": 200, "body": {...}}
    """
    print(f"[fc_gov] FC 触发，event: {json.dumps(event, ensure_ascii=False, default=str)[:500]}")

    try:
        parsed = parse_tos_event(event)
        if not parsed:
            msg = f"无法解析 TOS 事件: {json.dumps(event, ensure_ascii=False)[:200]}"
            print(f"[fc_gov] {msg}")
            return {"statusCode": 400, "body": json.dumps({"error": msg})}

        bucket = parsed["bucket"]
        object_key = parsed["object_key"]
        print(f"[fc_gov] 解析成功: bucket={bucket}, key={object_key}")

        # 仅处理 CSV 文件
        if not object_key.endswith(".csv"):
            print(f"[fc_gov] 跳过非 CSV 文件: {object_key}")
            return {"statusCode": 200, "body": json.dumps({"skipped": True, "reason": "not_csv"})}

        # 仅处理 vault/ 前缀（新楼盘 或 二手房小区）
        if not object_key.startswith("vault/"):
            print(f"[fc_gov] 跳过非 vault 文件: {object_key}")
            return {"statusCode": 200, "body": json.dumps({"skipped": True, "reason": "not_vault"})}

        city = _extract_city_from_key(object_key)
        print(f"[fc_gov] 城市: {city}")

        # 下载 CSV
        csv_path = _download_csv(bucket, object_key)

        # 执行治理流水线
        result = run_governance_pipeline(csv_path, city, bucket)

        # 清理临时文件
        try:
            csv_path.unlink()
        except Exception:
            pass

        return {
            "statusCode": 200,
            "body": json.dumps({"status": result["status"], "audit_key": result["audit_key"]},
                               ensure_ascii=False),
        }

    except Exception as exc:
        print(f"[fc_gov] 致命错误: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False),
        }


# ═══════════════════════════════════════════════════════════════════
#  CLI（本地测试）
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DDS 云端治理 FC 函数 — 本地测试入口"
    )
    parser.add_argument("--object-key", type=str,
                        help="TOS object key（模拟 TOS 事件触发）")
    parser.add_argument("--csv", type=str,
                        help="本地 CSV 路径（跳过 TOS 下载，直接治理）")
    parser.add_argument("--city", type=str, default=None,
                        help="城市名（默认从 object_key 或 CSV 路径提取）")
    parser.add_argument("--no-upload", action="store_true",
                        help="仅执行治理，不上传结果到 TOS（本地测试用）")
    args = parser.parse_args()

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"错误：文件不存在 {csv_path}")
            sys.exit(1)
        city = args.city or _extract_city_from_key(csv_path.name)
        if not args.no_upload:
            bucket = _ensure_tos_client()
        else:
            bucket = "local-test"
        print(f"[fc_gov] 本地模式: {csv_path} (city={city}, no_upload={args.no_upload})")
        result = run_governance_pipeline(csv_path, city, bucket, no_upload=args.no_upload)
        print(f"\n[fc_gov] 完成: {result['status']}")
        if not args.no_upload:
            print(f"[fc_gov] 审计日志: {result['audit_key']}")
        sys.exit(0 if result["status"] == "ok" else 1)

    if args.object_key:
        # 模拟 TOS 事件
        bucket = _ensure_tos_client()
        event = {
            "Records": [{
                "tos": {
                    "bucket": {"name": bucket},
                    "object": {"key": args.object_key},
                },
                "eventName": "tos:ObjectCreated:Put",
            }],
        }
        result = handler(event)
        print(f"\n[fc_gov] Handler 返回: {json.dumps(result, ensure_ascii=False, indent=2)}")
        sys.exit(0 if result.get("statusCode") == 200 else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
