"""
DDS 推理过程可视化 — AIPM 认知层核心模块
将决策链（合规→价值→ABM→任务书）从黑盒转为白盒 trace

提供：
  1. DecisionTrace — 结构化决策链路记录
  2. trace_context — 上下文管理器，自动捕获各 agent 输入/输出
  3. render_trace — 生成人类可读的推理链摘要
  4. render_trace_html — 生成 HTML 可视化

用法：
  from decision_trace import DecisionTrace, trace_context
  trace = DecisionTrace()
  with trace_context(trace, "compliance_agent"):
      result = run_compliance(...)
  print(trace.render())
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any, Optional
import json
import time


@dataclass
class TraceStep:
    """单个决策步骤"""
    agent: str          # agent 名称
    step: int           # 步骤序号
    input_summary: str  # 输入摘要
    output_summary: str # 输出摘要
    reasoning: str      # 推理逻辑（为什么得出这个结论）
    confidence: Optional[dict] = None  # 置信度（来自 confidence_engine）
    duration_ms: float = 0.0
    status: str = "ok"  # ok / warning / error / skipped
    caveats: list[str] = field(default_factory=list)
    timestamp: str = ""


@dataclass
class DecisionTrace:
    """完整决策链路追踪"""
    trace_id: str = ""
    persona: str = "developer"
    city: str = ""
    address: str = ""
    steps: list[TraceStep] = field(default_factory=list)
    total_duration_ms: float = 0.0
    overall_confidence: Optional[dict] = None
    created_at: str = ""

    def add_step(self, agent: str, input_summary: str, output_summary: str,
                 reasoning: str, confidence: Optional[dict] = None,
                 duration_ms: float = 0.0, status: str = "ok",
                 caveats: Optional[list[str]] = None) -> TraceStep:
        step = TraceStep(
            agent=agent,
            step=len(self.steps) + 1,
            input_summary=input_summary,
            output_summary=output_summary,
            reasoning=reasoning,
            confidence=confidence,
            duration_ms=round(duration_ms, 1),
            status=status,
            caveats=caveats or [],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.steps.append(step)
        return step

    def render(self) -> str:
        """生成人类可读的推理链文本"""
        lines = [
            f"## DDS 决策推理链 `{self.trace_id}`",
            f"**角色**: {self.persona} | **城市**: {self.city} | **地址**: {self.address}",
            f"**总耗时**: {self.total_duration_ms:.0f}ms | **整体置信度**: {self.overall_confidence.get('grade', 'N/A') if self.overall_confidence else 'N/A'}",
            "",
        ]
        for s in self.steps:
            conf_str = f" [置信度: {s.confidence.get('grade', '?')}/{s.confidence.get('score', '?')}]" if s.confidence else ""
            lines.append(f"### 步骤 {s.step}: {s.agent}{conf_str}")
            lines.append(f"> **输入**: {s.input_summary}")
            lines.append(f"> **推理**: {s.reasoning}")
            lines.append(f"> **输出**: {s.output_summary}")
            if s.caveats:
                for c in s.caveats:
                    lines.append(f"> ⚠ {c}")
            if s.status == "error":
                lines.append(f"> 🔴 此步骤执行异常")
            elif s.status == "skipped":
                lines.append(f"> ⏭ 此步骤已跳过")
            lines.append("")
        return "\n".join(lines)

    def render_html(self) -> str:
        """生成 HTML 可视化推理链"""
        steps_html = []
        for s in self.steps:
            conf_badge = ""
            if s.confidence:
                grade = s.confidence.get("grade", "?")
                score = s.confidence.get("score", 0)
                color = "#22c55e" if grade in ("A+", "A") else ("#f59e0b" if grade == "B" else "#ef4444")
                conf_badge = f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;margin-left:8px">{grade} {score}</span>'

            caveats_html = ""
            if s.caveats:
                caveats_html = "<div style='margin-top:8px'>" + "".join(
                    f"<div style='color:#f59e0b;font-size:13px;margin:2px 0'>⚠ {c}</div>"
                    for c in s.caveats
                ) + "</div>"

            status_icon = {"ok": "✓", "warning": "⚠", "error": "✗", "skipped": "→"}[s.status]
            status_color = {"ok": "#22c55e", "warning": "#f59e0b", "error": "#ef4444", "skipped": "#8b8fa3"}[s.status]

            steps_html.append(f"""
            <div style="background:#1a1d27;border:1px solid #2d3143;border-radius:8px;padding:16px;margin:12px 0">
              <div style="display:flex;align-items:center;margin-bottom:8px">
                <span style="color:{status_color};font-weight:bold;font-size:18px;margin-right:8px">{status_icon}</span>
                <strong style="color:#e8eaed;font-size:15px">步骤 {s.step}: {s.agent}</strong>
                {conf_badge}
                <span style="color:#8b8fa3;font-size:12px;margin-left:auto">{s.duration_ms}ms</span>
              </div>
              <div style="color:#a0a4b8;font-size:13px;margin:4px 0">
                <span style="color:#4da6ff">输入:</span> {s.input_summary}
              </div>
              <div style="color:#a0a4b8;font-size:13px;margin:4px 0">
                <span style="color:#f59e0b">推理:</span> {s.reasoning}
              </div>
              <div style="color:#a0a4b8;font-size:13px;margin:4px 0">
                <span style="color:#22c55e">输出:</span> {s.output_summary}
              </div>
              {caveats_html}
            </div>""")

        overall_conf = ""
        if self.overall_confidence:
            oc = self.overall_confidence
            grade = oc.get("grade", "?")
            score = oc.get("score", 0)
            actionable = "✅ 可据此决策" if oc.get("actionable") else "⚠ 仅供参考，需交叉验证"
            overall_conf = f"""
            <div style="background:#0d1117;border:2px solid #4da6ff;border-radius:8px;padding:16px;margin:16px 0">
              <strong style="color:#4da6ff;font-size:16px">整体置信度: {grade} ({score})</strong>
              <span style="color:#a0a4b8;margin-left:12px">{actionable}</span>
              <div style="color:#8b8fa3;font-size:13px;margin-top:4px">{oc.get('recommendation', '')}</div>
              <div style="color:#4da6ff;font-size:12px;margin-top:4px">📈 提升路径: {oc.get('target_note', '')}</div>
            </div>"""

        return f"""
        <div style="font-family:system-ui,sans-serif;max-width:800px;margin:0 auto;color:#e8eaed;background:#0f1117;padding:24px;border-radius:12px">
          <h2 style="color:#4da6ff;margin:0 0 4px">DDS 决策推理链</h2>
          <div style="color:#8b8fa3;font-size:13px;margin-bottom:16px">
            {self.trace_id} | {self.persona} | {self.city} | 总耗时 {self.total_duration_ms:.0f}ms
          </div>
          {overall_conf}
          {"".join(steps_html)}
        </div>"""

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "persona": self.persona,
            "city": self.city,
            "address": self.address,
            "total_duration_ms": self.total_duration_ms,
            "overall_confidence": self.overall_confidence,
            "steps": [
                {
                    "step": s.step,
                    "agent": s.agent,
                    "input_summary": s.input_summary,
                    "output_summary": s.output_summary,
                    "reasoning": s.reasoning,
                    "confidence": s.confidence,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "caveats": s.caveats,
                }
                for s in self.steps
            ],
            "created_at": self.created_at,
        }


@contextmanager
def trace_context(trace: DecisionTrace, agent: str):
    """自动追踪单个 agent 执行的上下文管理器"""
    t0 = time.perf_counter()
    step = trace.add_step(agent, "", "", "", duration_ms=0, status="running")
    result_holder = {"result": None, "error": None}
    try:
        yield result_holder
        duration_ms = (time.perf_counter() - t0) * 1000
        step.duration_ms = round(duration_ms, 1)
    except Exception as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        step.duration_ms = round(duration_ms, 1)
        step.status = "error"
        step.caveats.append(f"执行异常: {str(e)[:120]}")
        result_holder["error"] = e
        raise


def generate_trace_id() -> str:
    return datetime.now(timezone.utc).strftime("trace-%Y%m%dT%H%M%SZ")


# ── 不确定性标注（P0-3）──

@dataclass
class UncertaintyAnnotation:
    """AI 不确定时主动标注（对标 CRIC 深度智联）"""
    field: str           # 不确定的字段/结论
    reason: str          # 不确定原因
    severity: str        # "高" / "中" / "低"
    suggestion: str      # 如何获取更确定的信息
    fallback_value: Any = None  # 当前使用的降级值


def annotate_uncertainty(result: dict, agent: str) -> list[UncertaintyAnnotation]:
    """扫描 agent 输出，标注所有不确定项"""
    annotations = []

    # 合规 Agent
    if agent == "compliance_agent":
        if result.get("hard_constraints_missing"):
            annotations.append(UncertaintyAnnotation(
                field="一级法定硬约束",
                reason="未获取到完整的规划条件/用地性质/限高/容积率上限等官方文件",
                severity="高",
                suggestion="从当地自然资源和规划局官网获取出让条件文件",
                fallback_value="基于周边项目反推（不准确）",
            ))
        if result.get("verdict") == "conditional":
            annotations.append(UncertaintyAnnotation(
                field="合规结论",
                reason="存在条件性通过项，未完全确认可售分割/产权年限/商办住宅监管口径",
                severity="中",
                suggestion="咨询当地住建局确认商办/住宅监管政策",
            ))

    # 价值 Agent
    if agent == "value_agent":
        ref = result.get("product_reference") or {}
        areas = ref.get("observed_area_ranges") or []
        if len(areas) < 3:
            annotations.append(UncertaintyAnnotation(
                field="产品面积段参考",
                reason=f"仅 {len(areas)} 个对标项目，样本量不足",
                severity="中",
                suggestion="扩大竞品搜索范围或补充人工调研",
                fallback_value=areas,
            ))
        if not result.get("benchmark_premium_ratio"):
            annotations.append(UncertaintyAnnotation(
                field="对标溢价率",
                reason="无匹配的对标标杆项目",
                severity="中",
                suggestion="从 benchmark_library.json 中手动匹配相似案例",
            ))

    # ABM Agent
    if agent == "abm_agent":
        conf = result.get("confidence") or {}
        if conf.get("score", 0) < 0.5:
            annotations.append(UncertaintyAnnotation(
                field="ABM 模拟结果",
                reason="合成人口分布，非真实客群数据；样本覆盖不足",
                severity="高",
                suggestion="接入 L1 真实成交数据可提升置信度至 0.9+",
                fallback_value="基于通用客群池的蒙特卡洛估计",
            ))

    # 溢价引擎
    if agent == "premium_engine":
        for d in result.get("drivers") or result.get("premium_decomposition", {}).get("components", []):
            if d.get("status") == "待接入":
                annotations.append(UncertaintyAnnotation(
                    field=f"溢价驱动: {d.get('cn', d.get('key', '?'))}",
                    reason="该维度数据源未接入，使用默认值",
                    severity="中",
                    suggestion=f"接入 {d.get('evidence', '对应数据源')}",
                ))

    return annotations


def render_uncertainty_html(annotations: list[UncertaintyAnnotation]) -> str:
    """生成不确定标注的 HTML"""
    if not annotations:
        return ""

    sev_colors = {"高": "#ef4444", "中": "#f59e0b", "低": "#8b8fa3"}
    items = []
    for a in annotations:
        items.append(f"""
        <div style="background:#1a1d27;border-left:3px solid {sev_colors.get(a.severity, '#8b8fa3')};padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0">
          <div style="display:flex;align-items:center">
            <strong style="color:#e8eaed;font-size:14px">{a.field}</strong>
            <span style="background:{sev_colors.get(a.severity, '#8b8fa3')};color:#fff;padding:1px 6px;border-radius:3px;font-size:11px;margin-left:8px">{a.severity}</span>
          </div>
          <div style="color:#f59e0b;font-size:13px;margin-top:4px">⚠ {a.reason}</div>
          <div style="color:#4da6ff;font-size:12px;margin-top:2px">→ {a.suggestion}</div>
          {f'<div style="color:#8b8fa3;font-size:11px;margin-top:2px">当前降级值: {a.fallback_value}</div>' if a.fallback_value else ''}
        </div>""")

    return f"""
    <div style="background:#0d1117;border:1px solid #f59e0b;border-radius:8px;padding:16px;margin:16px 0">
      <h3 style="color:#f59e0b;margin:0 0 8px">⚠ AI 不确定标注</h3>
      <div style="color:#8b8fa3;font-size:13px;margin-bottom:8px">以下结论存在不确定性，建议交叉验证</div>
      {"".join(items)}
    </div>"""