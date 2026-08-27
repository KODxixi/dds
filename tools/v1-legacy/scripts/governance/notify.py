# -*- coding: utf-8 -*-
"""
统一数据治理通知告警模块
=========================
功能：为 DDS 所有治理模块提供统一的告警推送通道，支持三种通道按优先级自动降级。

通道体系
--------
| 优先级 | 通道           | 实现方式           | 配置来源                     |
|--------|----------------|--------------------|-----------------------------|
| 1      | 飞书 Webhook   | urllib HTTP POST   | DDS_FEISHU_WEBHOOK_URL      |
| 2      | 邮件 SMTP      | smtplib            | DDS_SMTP_HOST/PORT/USER/PASS|
| 3      | 本地文件       | JSON 写入          | 始终可用（兜底）            |

设计原则
--------
- **纯标准库**：smtplib + urllib + json，无第三方依赖
- **静默降级**：环境变量未配置时自动跳过，不报错
- **单例模式**：全局复用同一 Notifier 实例
- **超时保护**：飞书 5s，邮件 10s，避免阻塞治理管道

与现有模块集成
--------------
- drift_monitor.py: check_drift() 返回非 green 时调用
- quality_gates.py: P0/P1/P2 门禁 FAIL 时调用
- fc_governance.py: TOS 告警写入后调用（双保险）

CLI
---
    python scripts/governance/notify.py --selftest
    python scripts/governance/notify.py --send --module drift_monitor --level orange --title "测试"
    python scripts/governance/notify.py --send --module quality_gates --level red --title "P0门禁失败" --summary "Schema列数不足" --detail '{"checks":[...]}'
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import socket
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

# ── 模块级常量 ───────────────────────────────────────────────────────
_ALERT_DIR = "data_out/governance/alerts"
_INDEX_FILE = "_alert_index.jsonl"
_FEISHU_TIMEOUT = 5          # 秒
_SMTP_TIMEOUT = 10           # 秒
_FEISHU_MAX_BYTES = 20 * 1024  # 飞书单条消息上限 20KB

# 告警级别 → 飞书颜色
_LEVEL_COLOR_MAP = {
    "red": "red",
    "orange": "orange",
    "yellow": "yellow",
    "green": "green",
}

# ── 集成钩子点位（各模块调用后在此记录） ────────────────────────────────
# 格式：(module, level, title, summary, detail_dict_or_None, report_path_or_None)
_INTEGRATION_HOOKS: list[tuple[str, str, str, str, dict | None, str | None]] = []


# ═══════════════════════════════════════════════════════════════════════
#  Notifier 单例
# ═══════════════════════════════════════════════════════════════════════

class Notifier:
    """统一告警通知器（单例模式，纯标准库实现）。

    使用方式::

        from scripts.governance.notify import get_notifier
        notifier = get_notifier()
        notifier.send(module="drift_monitor", level="red", title="...", summary="...")
    """

    _instance: Notifier | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        # 飞书
        self._feishu_url = os.environ.get("DDS_FEISHU_WEBHOOK_URL", "").strip()
        self._feishu_available = bool(self._feishu_url)

        # 邮件
        self._smtp_host = os.environ.get("DDS_SMTP_HOST", "").strip()
        self._smtp_port = int(os.environ.get("DDS_SMTP_PORT", "587"))
        self._smtp_user = os.environ.get("DDS_SMTP_USER", "").strip()
        self._smtp_pass = os.environ.get("DDS_SMTP_PASS", "").strip()
        self._alert_emails = os.environ.get("DDS_ALERT_EMAIL_TO", "").strip()
        self._smtp_available = all([self._smtp_host, self._smtp_user, self._smtp_pass, self._alert_emails])

        # 本地文件（始终可用，兜底）
        self._alert_dir = Path(_ALERT_DIR)
        self._alert_dir_abs: Path | None = None  # 惰性解析

    @classmethod
    def get_instance(cls) -> Notifier:
        """获取单例实例（线程安全）。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 属性 ──────────────────────────────────────────────────────────

    @property
    def feishu_available(self) -> bool:
        return self._feishu_available

    @property
    def smtp_available(self) -> bool:
        return self._smtp_available

    @property
    def local_available(self) -> bool:
        return True  # 始终可用

    def status(self) -> dict[str, bool]:
        """返回三个通道的可用性状态。"""
        return {
            "feishu": self._feishu_available,
            "smtp": self._smtp_available,
            "local": True,
        }

    # ── 统一发送入口 ──────────────────────────────────────────────────

    def send(
        self,
        module: str,
        level: str,
        title: str,
        summary: str,
        detail: dict[str, Any] | None = None,
        report_path: str | None = None,
    ) -> dict[str, Any]:
        """发送告警，按优先级尝试通道，失败自动降级。

        参数:
            module: 来源模块名（如 "drift_monitor"）
            level: 告警级别 red/orange/yellow/green
            title: 告警标题
            summary: 告警摘要（单行文字）
            detail: 详细信息（JSON 可序列化字典）
            report_path: 完整报告文件路径（可选）

        返回:
            发送结果字典，包含 channel / success / message
        """
        if level not in _LEVEL_COLOR_MAP:
            level = "green"

        result = {"module": module, "level": level, "title": title, "timestamp": datetime.now(timezone.utc).isoformat()}

        # 1) 飞书优先
        if self._feishu_available:
            ok, msg = self._send_feishu(module, level, title, summary, detail, report_path)
            if ok:
                result.update({"channel": "feishu", "success": True, "message": msg})
                return result
            # 降级：飞书失败，继续尝试邮件
            result["feishu_error"] = msg

        # 2) 邮件
        if self._smtp_available:
            ok, msg = self._send_email(module, level, title, summary, detail, report_path)
            if ok:
                result.update({"channel": "smtp", "success": True, "message": msg})
                return result
            result["smtp_error"] = msg

        # 3) 本地文件（兜底，始终可用）
        ok, msg = self._send_local(module, level, title, summary, detail, report_path)
        result.update({"channel": "local", "success": ok, "message": msg})
        return result

    # ── 批量通知 ──────────────────────────────────────────────────────

    def send_batch(self, alerts: list[dict[str, Any]]) -> dict[str, Any]:
        """合并多条告警为一条摘要消息，避免刷屏。

        参数:
            alerts: 告警列表，每个元素同 send() 参数格式

        返回:
            发送结果
        """
        if not alerts:
            return {"channel": "none", "success": True, "message": "empty batch"}

        if len(alerts) == 1:
            a = alerts[0]
            return self.send(
                module=a.get("module", "batch"),
                level=a.get("level", "green"),
                title=a.get("title", ""),
                summary=a.get("summary", ""),
                detail=a.get("detail"),
                report_path=a.get("report_path"),
            )

        # 多告警合并：按模块分组，同模块同级别合并
        groups: dict[str, dict[str, list[dict]]] = {}
        for a in alerts:
            mod = a.get("module", "unknown")
            lvl = a.get("level", "green")
            groups.setdefault(mod, {}).setdefault(lvl, []).append(a)

        # 构建合并摘要
        highest_level = "green"
        level_order = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
        lines = []
        all_details = {}

        for mod, lvl_groups in sorted(groups.items()):
            for lvl, alist in sorted(lvl_groups.items(), key=lambda x: level_order.get(x[0], 99)):
                if level_order.get(lvl, 99) < level_order.get(highest_level, 99):
                    highest_level = lvl
                count = len(alist)
                titles = [a.get("title", "") for a in alist]
                lines.append(f"[{mod}] {lvl.upper()} x{count}: {', '.join(titles)}")
                all_details.setdefault(mod, {})[lvl] = [a.get("summary", "") for a in alist]

        merged_summary = "; ".join(lines)
        merged_title = f"DDS 治理批量告警（{len(alerts)} 条）"

        return self.send(
            module="batch",
            level=highest_level,
            title=merged_title,
            summary=merged_summary,
            detail={"groups": all_details, "total": len(alerts)},
        )

    # ── 飞书 Webhook 通道 ──────────────────────────────────────────────

    def _send_feishu(
        self,
        module: str,
        level: str,
        title: str,
        summary: str,
        detail: dict | None,
        report_path: str | None,
    ) -> tuple[bool, str]:
        """通过飞书 Webhook 发送富文本消息。"""
        color = _LEVEL_COLOR_MAP.get(level, "green")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 构建 Markdown 内容
        content_lines = [
            f"**DDS 治理告警 — [{module}]**",
            f"级别：<font color='{color}'>{level.upper()}</font>",
            f"时间：{timestamp}",
            f"",
            f"**{title}**",
            f"{summary}",
        ]

        if detail:
            detail_str = json.dumps(detail, ensure_ascii=False, indent=2)
            content_lines.append(f"")
            content_lines.append(f"详情：")
            content_lines.append(f"```json")
            content_lines.append(detail_str)
            content_lines.append(f"```")

        if report_path:
            content_lines.append(f"")
            content_lines.append(f"完整报告：{report_path}")

        full_content = "\n".join(content_lines)

        # 超长时截断 + 提示
        content_bytes = full_content.encode("utf-8")
        if len(content_bytes) > _FEISHU_MAX_BYTES:
            # 截断到 19KB，留 1KB 给 JSON 结构
            truncated = content_bytes[:_FEISHU_MAX_BYTES - 2048].decode("utf-8", errors="replace")
            truncated += "\n\n> ⚠ 消息过长已截断"
            if report_path:
                truncated += f"，完整内容见报告：{report_path}"
            full_content = truncated

        # 飞书富文本消息体
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"[{level.upper()}] {title}"},
                    "template": color,
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": full_content,
                    }
                ],
            },
        }

        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self._feishu_url,
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=_FEISHU_TIMEOUT) as resp:
                resp_body = resp.read().decode("utf-8")
                resp_json = json.loads(resp_body)
                if resp_json.get("code") == 0:
                    return True, f"飞书发送成功 (StatusCode={resp_json.get('StatusCode')})"
                return False, f"飞书返回错误: {resp_json}"
        except urllib.error.URLError as e:
            return False, f"飞书网络错误: {e}"
        except socket.timeout:
            return False, "飞书连接超时"
        except Exception as e:
            return False, f"飞书发送异常: {e}"

    # ── 邮件 SMTP 通道 ─────────────────────────────────────────────────

    def _send_email(
        self,
        module: str,
        level: str,
        title: str,
        summary: str,
        detail: dict | None,
        report_path: str | None,
    ) -> tuple[bool, str]:
        """通过 SMTP 发送纯文本 + HTML 双格式邮件。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 纯文本版本
        text_lines = [
            f"DDS 治理告警 — [{module}]",
            f"级别：{level.upper()}",
            f"时间：{timestamp}",
            f"",
            f"标题：{title}",
            f"摘要：{summary}",
        ]
        if detail:
            text_lines.append(f"")
            text_lines.append(f"详情：")
            text_lines.append(json.dumps(detail, ensure_ascii=False, indent=2))
        if report_path:
            text_lines.append(f"")
            text_lines.append(f"完整报告：{report_path}")
        text_body = "\n".join(text_lines)

        # HTML 版本
        color = _LEVEL_COLOR_MAP.get(level, "green")
        html_lines = [
            "<html><body style='font-family: monospace;'>",
            f"<h2 style='color:{color};'>DDS 治理告警 — [{module}]</h2>",
            "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>",
            f"<tr><td><b>级别</b></td><td style='color:{color};font-weight:bold;'>{level.upper()}</td></tr>",
            f"<tr><td><b>时间</b></td><td>{timestamp}</td></tr>",
            f"<tr><td><b>标题</b></td><td>{title}</td></tr>",
            f"<tr><td><b>摘要</b></td><td>{summary}</td></tr>",
            "</table>",
        ]
        if detail:
            html_lines.append("<h4>详情</h4>")
            html_lines.append(f"<pre>{json.dumps(detail, ensure_ascii=False, indent=2)}</pre>")
        if report_path:
            html_lines.append(f"<p><b>完整报告：</b>{report_path}</p>")
        html_lines.append("</body></html>")
        html_body = "\n".join(html_lines)

        # 收件人列表
        recipients = [addr.strip() for addr in self._alert_emails.split(",") if addr.strip()]

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[DDS {level.upper()}] [{module}] {title}"
            msg["From"] = self._smtp_user
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=_SMTP_TIMEOUT) as server:
                server.starttls()
                server.login(self._smtp_user, self._smtp_pass)
                server.send_message(msg)

            return True, f"邮件发送成功 (收件人: {len(recipients)} 人)"
        except smtplib.SMTPAuthenticationError:
            return False, "SMTP 认证失败，请检查 DDS_SMTP_USER/DDS_SMTP_PASS"
        except smtplib.SMTPConnectError as e:
            return False, f"SMTP 连接失败: {e}"
        except socket.timeout:
            return False, "SMTP 连接超时"
        except Exception as e:
            return False, f"邮件发送异常: {e}"

    # ── 本地文件通道（兜底） ───────────────────────────────────────────

    def _send_local(
        self,
        module: str,
        level: str,
        title: str,
        summary: str,
        detail: dict | None,
        report_path: str | None,
    ) -> tuple[bool, str]:
        """写入本地 JSON 文件 + 索引（始终可用）。"""
        try:
            alert_dir = self._resolve_alert_dir()
            alert_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now()
            ts_str = timestamp.strftime("%Y%m%d_%H%M%S")
            filename = f"{module}_{level}_{ts_str}.json"
            filepath = alert_dir / filename

            # 告警 JSON
            alert_data = {
                "module": module,
                "level": level,
                "title": title,
                "summary": summary,
                "detail": detail,
                "report_path": report_path,
                "timestamp": timestamp.isoformat(),
                "created_at": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(alert_data, f, ensure_ascii=False, indent=2)

            # 索引 JSONL
            index_path = alert_dir / _INDEX_FILE
            index_entry = {
                "file": filename,
                "module": module,
                "level": level,
                "title": title,
                "summary": summary,
                "timestamp": timestamp.isoformat(),
            }
            with open(index_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(index_entry, ensure_ascii=False) + "\n")

            return True, f"本地文件写入成功: {filepath}"
        except Exception as e:
            return False, f"本地文件写入失败: {e}"

    def _resolve_alert_dir(self) -> Path:
        """惰性解析告警目录为绝对路径。"""
        if self._alert_dir_abs is None:
            # 相对于 DDS 项目根目录
            script_dir = Path(__file__).resolve().parent
            project_root = script_dir.parent.parent
            self._alert_dir_abs = project_root / _ALERT_DIR
        return self._alert_dir_abs


# ═══════════════════════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════════════════════

def get_notifier() -> Notifier:
    """获取全局 Notifier 单例。"""
    return Notifier.get_instance()


def send_alert(
    module: str,
    level: str,
    title: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    report_path: str | None = None,
) -> dict[str, Any]:
    """便捷函数：发送一条告警。"""
    return get_notifier().send(module, level, title, summary, detail, report_path)


def send_batch_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """便捷函数：批量发送告警。"""
    return get_notifier().send_batch(alerts)


# ═══════════════════════════════════════════════════════════════════════
#  与现有模块集成钩子
# ═══════════════════════════════════════════════════════════════════════

def notify_drift_check(alert: dict[str, Any], report_path: str | None = None) -> dict[str, Any]:
    """集成 drift_monitor.py: check_drift() 返回结果后调用。

    当 alert['alert_level'] != 'green' 时发送通知。

    参数:
        alert: check_drift() 返回的 drift_alert 字典
        report_path: 漂移报告文件路径（可选）

    返回:
        通知发送结果
    """
    level = alert.get("alert_level", "green")
    if level == "green":
        return {"channel": "none", "success": True, "message": "无漂移，跳过通知"}

    # 构建摘要
    results = alert.get("results", {})
    alerts_list = []
    for dim, result in results.items():
        if result.get("level") != "green":
            alerts_list.append(f"{dim}: {result.get('detail', '')}")

    summary = "；".join(alerts_list) if alerts_list else "数据漂移检测异常"
    title = f"数据漂移告警 ({alert.get('alerts', 0)} 维异常)"

    return get_notifier().send(
        module="drift_monitor",
        level=level,
        title=title,
        summary=summary,
        detail=alert,
        report_path=report_path,
    )


def notify_gate_failure(gate_result: dict[str, Any]) -> dict[str, Any]:
    """集成 quality_gates.py: run_gate() 返回 FAIL 时调用。

    参数:
        gate_result: run_gate() 返回的门禁结果字典

    返回:
        通知发送结果
    """
    if gate_result.get("passed", True):
        return {"channel": "none", "success": True, "message": "门禁通过，跳过通知"}

    level = gate_result.get("level", "P0")
    action = gate_result.get("action", "block")
    failed_checks = [c for c in gate_result.get("checks", []) if not c.get("passed", True)]

    # 级别映射：P0 → red, P1 → orange, P2 → yellow
    level_map = {"P0": "red", "P1": "orange", "P2": "yellow"}
    alert_level = level_map.get(level, "orange")

    summary_parts = []
    for c in failed_checks:
        summary_parts.append(f"{c.get('check', '?')}: {c.get('detail', '')}")
    summary = "；".join(summary_parts)

    title = f"{level} 门禁失败（{action}）"

    return get_notifier().send(
        module="quality_gates",
        level=alert_level,
        title=title,
        summary=summary,
        detail=gate_result,
    )


def notify_fc_governance(
    city: str,
    level: str,
    title: str,
    summary: str,
    detail: dict[str, Any] | None = None,
    tos_key: str | None = None,
) -> dict[str, Any]:
    """集成 fc_governance.py: TOS 告警写入后调用（双保险）。

    参数:
        city: 城市名
        level: 告警级别
        title: 标题
        summary: 摘要
        detail: 详细信息
        tos_key: TOS 对象 key（可选）

    返回:
        通知发送结果
    """
    return get_notifier().send(
        module="fc_governance",
        level=level,
        title=f"[{city}] {title}",
        summary=summary,
        detail=detail,
        report_path=f"TOS: {tos_key}" if tos_key else None,
    )


# ═══════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """自测三种通道（本地文件必定成功，飞书/邮件按配置）。"""
    print("=" * 60)
    print("  DDS 通知模块自测")
    print("=" * 60)

    notifier = get_notifier()

    # 通道状态
    status = notifier.status()
    print(f"\n通道状态：")
    print(f"  飞书 Webhook: {'已配置' if status['feishu'] else '未配置（将跳过）'}")
    print(f"  邮件 SMTP:    {'已配置' if status['smtp'] else '未配置（将跳过）'}")
    print(f"  本地文件:     {'始终可用' if status['local'] else '不可用'}")

    # 单条告警
    print(f"\n── 测试 1：单条告警 ──")
    test_detail = {
        "metric": "price_ks",
        "ks_statistic": 0.15,
        "threshold": 0.10,
        "reference_mean": 28500,
        "current_mean": 31200,
    }
    result = notifier.send(
        module="selftest",
        level="orange",
        title="自测告警：价格分布漂移",
        summary="KS 统计量 0.15 > 阈值 0.10，价格分布发生中等漂移",
        detail=test_detail,
    )
    print(f"  通道: {result.get('channel')}")
    print(f"  成功: {result.get('success')}")
    print(f"  消息: {result.get('message')}")

    # 批量告警
    print(f"\n── 测试 2：批量告警 ──")
    batch = [
        {"module": "drift_monitor", "level": "orange", "title": "KS 漂移", "summary": "KS=0.15"},
        {"module": "drift_monitor", "level": "yellow", "title": "填充率下降", "summary": "3列填充率下降 >5%"},
        {"module": "quality_gates", "level": "red", "title": "P0 门禁失败", "summary": "Schema 列数 45 < 50"},
        {"module": "quality_gates", "level": "red", "title": "P0 坐标有效率", "summary": "坐标有效率 35% < 50%"},
    ]
    result = notifier.send_batch(batch)
    print(f"  通道: {result.get('channel')}")
    print(f"  成功: {result.get('success')}")
    print(f"  消息: {result.get('message')}")

    # 集成钩子测试
    print(f"\n── 测试 3：集成钩子 ──")
    # 模拟 drift_monitor 输出
    mock_drift = {
        "alert_level": "orange",
        "alerts": 2,
        "results": {
            "price_distribution": {"level": "orange", "detail": "KS=0.15 > 0.10"},
            "fill_rate": {"level": "yellow", "detail": "3 列填充率下降 >5%"},
            "city_coverage": {"level": "green", "detail": "城市覆盖无变化"},
            "coord_offset": {"level": "green", "detail": "坐标偏移 0.3km"},
            "schema": {"level": "green", "detail": "Schema 无变化"},
            "volume": {"level": "green", "detail": "数据量变化 +2.1%"},
        },
    }
    r = notify_drift_check(mock_drift)
    print(f"  drift_monitor 钩子: channel={r.get('channel')}, success={r.get('success')}")

    # 模拟 quality_gates 输出
    mock_gate = {
        "level": "P0",
        "passed": False,
        "action": "block",
        "total_checks": 5,
        "passed_checks": 3,
        "failed_checks": 2,
        "checks": [
            {"check": "schema_columns", "passed": False, "detail": "列数 45 < 50"},
            {"check": "required_fields", "passed": True, "detail": "必填字段完整"},
            {"check": "price_range", "passed": True, "detail": "价格范围正常"},
            {"check": "coord_valid_rate", "passed": False, "detail": "坐标有效率 35% < 50%"},
            {"check": "duplicate_rate", "passed": True, "detail": "重复率 5% < 30%"},
        ],
    }
    r = notify_gate_failure(mock_gate)
    print(f"  quality_gates 钩子: channel={r.get('channel')}, success={r.get('success')}")

    # 模拟 fc_governance 输出
    r = notify_fc_governance(
        city="三亚",
        level="red",
        title="P0 门禁失败",
        summary="Schema 列数不足，坐标有效率不达标",
        detail={"failed_checks": ["schema_columns", "coord_valid_rate"]},
        tos_key="governance/_alerts/三亚_20260710_p0_fail.json",
    )
    print(f"  fc_governance 钩子: channel={r.get('channel')}, success={r.get('success')}")

    # 本地文件验证
    print(f"\n── 测试 4：本地文件验证 ──")
    alert_dir = notifier._resolve_alert_dir()
    print(f"  告警目录: {alert_dir}")
    if alert_dir.exists():
        json_files = sorted(alert_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        print(f"  告警文件数: {len(json_files)}")
        for f in json_files[:3]:
            print(f"    - {f.name}")
        index_path = alert_dir / _INDEX_FILE
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            print(f"  索引条目数: {len(lines)}")
    else:
        print(f"  (目录尚未创建)")

    print(f"\n{'=' * 60}")
    print(f"  自测完成")
    print(f"{'=' * 60}")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DDS 统一数据治理通知告警模块",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/governance/notify.py --selftest
  python scripts/governance/notify.py --send --module drift_monitor --level orange --title "价格漂移" --summary "KS=0.15"
  python scripts/governance/notify.py --send --module quality_gates --level red --title "P0失败" --summary "列数不足" --detail '{"checks":[]}'
        """,
    )

    parser.add_argument("--selftest", action="store_true", help="运行内置自测（三种通道）")
    parser.add_argument("--send", action="store_true", help="发送告警")
    parser.add_argument("--status", action="store_true", help="查看通道可用性状态")
    parser.add_argument("--module", type=str, default="cli", help="来源模块名")
    parser.add_argument("--level", type=str, default="green", choices=["red", "orange", "yellow", "green"], help="告警级别")
    parser.add_argument("--title", type=str, help="告警标题")
    parser.add_argument("--summary", type=str, help="告警摘要")
    parser.add_argument("--detail", type=str, default=None, help="详细信息（JSON 字符串）")
    parser.add_argument("--report-path", type=str, default=None, help="完整报告路径")

    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    if args.status:
        notifier = get_notifier()
        status = notifier.status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return

    if args.send:
        if not args.title:
            print("错误：--send 需要 --title 参数")
            return

        detail = None
        if args.detail:
            try:
                detail = json.loads(args.detail)
            except json.JSONDecodeError as e:
                print(f"错误：--detail JSON 解析失败: {e}")
                return

        result = send_alert(
            module=args.module,
            level=args.level,
            title=args.title,
            summary=args.summary or "",
            detail=detail,
            report_path=args.report_path,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    parser.print_help()


if __name__ == "__main__":
    main()