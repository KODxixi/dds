"""Deterministic decision-trace recording and rendering.

Captures the reasoning chain behind a decision (which agent ran, what it saw,
why it concluded what it did, and its confidence) and renders it as markdown or
HTML. This is the white-box counterpart to the numeric engines: the numbers come
from :mod:`dds.engines`, and this module records *how* they were reached so a
reviewer can audit the reasoning instead of trusting a black box.

Ported from DDS V1 ``scripts/decision_trace.py``. The original ``trace_context``
left a step in the non-final ``"running"`` state on success (which its own HTML
renderer could not map to a status icon); here a successful step is finalised as
``"ok"``.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

_STATUS_ICONS = {"ok": "✓", "warning": "⚠", "error": "✗", "skipped": "→"}
_STATUS_COLORS = {"ok": "#22c55e", "warning": "#f59e0b", "error": "#ef4444", "skipped": "#8b8fa3"}


@dataclass
class TraceStep:
    """One decision step within a :class:`DecisionTrace`."""

    agent: str
    step: int
    input_summary: str
    output_summary: str
    reasoning: str
    confidence: Optional[dict] = None
    duration_ms: float = 0.0
    status: str = "ok"
    caveats: list[str] = field(default_factory=list)
    timestamp: str = ""


@dataclass
class DecisionTrace:
    """A complete, structured decision-reasoning chain."""

    trace_id: str = ""
    persona: str = "developer"
    city: str = ""
    address: str = ""
    steps: list[TraceStep] = field(default_factory=list)
    total_duration_ms: float = 0.0
    overall_confidence: Optional[dict] = None
    created_at: str = ""

    def add_step(
        self,
        agent: str,
        input_summary: str,
        output_summary: str,
        reasoning: str,
        confidence: Optional[dict] = None,
        duration_ms: float = 0.0,
        status: str = "ok",
        caveats: Optional[list[str]] = None,
    ) -> TraceStep:
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
        """Render the reasoning chain as human-readable markdown."""
        overall_grade = (
            self.overall_confidence.get("grade", "N/A")
            if self.overall_confidence
            else "N/A"
        )
        lines = [
            f"## DDS 决策推理链 `{self.trace_id}`",
            f"**角色**: {self.persona} | **城市**: {self.city} | **地址**: {self.address}",
            f"**总耗时**: {self.total_duration_ms:.0f}ms | **整体置信度**: {overall_grade}",
            "",
        ]
        for step in self.steps:
            conf_str = ""
            if step.confidence:
                grade = step.confidence.get("grade", "?")
                score = step.confidence.get("score", "?")
                conf_str = f" [置信度: {grade}/{score}]"
            lines.append(f"### 步骤 {step.step}: {step.agent}{conf_str}")
            lines.append(f"> **输入**: {step.input_summary}")
            lines.append(f"> **推理**: {step.reasoning}")
            lines.append(f"> **输出**: {step.output_summary}")
            for caveat in step.caveats:
                lines.append(f"> ⚠ {caveat}")
            if step.status == "error":
                lines.append("> 🔴 此步骤执行异常")
            elif step.status == "skipped":
                lines.append("> ⏭ 此步骤已跳过")
            lines.append("")
        return "\n".join(lines)

    def render_html(self) -> str:
        """Render the reasoning chain as a self-contained HTML fragment."""
        steps_html = []
        for step in self.steps:
            conf_badge = ""
            if step.confidence:
                grade = step.confidence.get("grade", "?")
                score = step.confidence.get("score", 0)
                color = (
                    "#22c55e"
                    if grade in ("A+", "A")
                    else ("#f59e0b" if grade == "B" else "#ef4444")
                )
                conf_badge = (
                    f'<span style="background:{color};color:#fff;padding:2px 8px;'
                    f'border-radius:4px;font-size:12px;margin-left:8px">{grade} {score}</span>'
                )

            caveats_html = ""
            if step.caveats:
                caveats_html = "<div style='margin-top:8px'>" + "".join(
                    f"<div style='color:#f59e0b;font-size:13px;margin:2px 0'>⚠ {caveat}</div>"
                    for caveat in step.caveats
                ) + "</div>"

            status_icon = _STATUS_ICONS[step.status]
            status_color = _STATUS_COLORS[step.status]

            steps_html.append(
                f"""
            <div style="background:#1a1d27;border:1px solid #2d3143;border-radius:8px;padding:16px;margin:12px 0">
              <div style="display:flex;align-items:center;margin-bottom:8px">
                <span style="color:{status_color};font-weight:bold;font-size:18px;margin-right:8px">{status_icon}</span>
                <strong style="color:#e8eaed;font-size:15px">步骤 {step.step}: {step.agent}</strong>
                {conf_badge}
                <span style="color:#8b8fa3;font-size:12px;margin-left:auto">{step.duration_ms}ms</span>
              </div>
              <div style="color:#a0a4b8;font-size:13px;margin:4px 0">
                <span style="color:#4da6ff">输入:</span> {step.input_summary}
              </div>
              <div style="color:#a0a4b8;font-size:13px;margin:4px 0">
                <span style="color:#f59e0b">推理:</span> {step.reasoning}
              </div>
              <div style="color:#a0a4b8;font-size:13px;margin:4px 0">
                <span style="color:#22c55e">输出:</span> {step.output_summary}
              </div>
              {caveats_html}
            </div>"""
            )

        overall_conf = ""
        if self.overall_confidence:
            oc = self.overall_confidence
            grade = oc.get("grade", "?")
            score = oc.get("score", 0)
            actionable = "✅ 可据此决策" if oc.get("actionable") else "⚠ 仅供参考，需交叉验证"
            overall_conf = (
                f"""
            <div style="background:#0d1117;border:2px solid #4da6ff;border-radius:8px;padding:16px;margin:16px 0">
              <strong style="color:#4da6ff;font-size:16px">整体置信度: {grade} ({score})</strong>
              <span style="color:#a0a4b8;margin-left:12px">{actionable}</span>
              <div style="color:#8b8fa3;font-size:13px;margin-top:4px">{oc.get('recommendation', '')}</div>
              <div style="color:#4da6ff;font-size:12px;margin-top:4px">📈 提升路径: {oc.get('target_note', '')}</div>
            </div>"""
            )

        return f"""
        <div style="font-family:system-ui,sans-serif;max-width:800px;margin:0 auto;color:#e8eaed;background:#0f1117;padding:24px;border-radius:12px">
          <h2 style="color:#4da6ff;margin:0 0 4px">DDS 决策推理链</h2>
          <div style="color:#8b8fa3;font-size:13px;margin-bottom:16px">
            {self.trace_id} | {self.persona} | {self.city} | 总耗时 {self.total_duration_ms:.0f}ms
          </div>
          {overall_conf}
          {"".join(steps_html)}
        </div>"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "persona": self.persona,
            "city": self.city,
            "address": self.address,
            "total_duration_ms": self.total_duration_ms,
            "overall_confidence": self.overall_confidence,
            "steps": [
                {
                    "step": step.step,
                    "agent": step.agent,
                    "input_summary": step.input_summary,
                    "output_summary": step.output_summary,
                    "reasoning": step.reasoning,
                    "confidence": step.confidence,
                    "duration_ms": step.duration_ms,
                    "status": step.status,
                    "caveats": step.caveats,
                }
                for step in self.steps
            ],
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@contextmanager
def trace_context(trace: DecisionTrace, agent: str) -> Iterator[dict[str, Any]]:
    """Record a single agent execution: input, timing, output, or failure.

    The yielded dict is a holder the caller may populate (e.g. ``holder["result"]``);
    exceptions are recorded on the step and re-raised so the caller's control flow
    is unchanged.
    """
    start = time.perf_counter()
    step = trace.add_step(agent, "", "", "", duration_ms=0.0, status="ok")
    holder: dict[str, Any] = {"result": None, "error": None}
    try:
        yield holder
        step.duration_ms = round((time.perf_counter() - start) * 1000, 1)
    except Exception as error:  # noqa: BLE001 - trace the failure and propagate
        step.duration_ms = round((time.perf_counter() - start) * 1000, 1)
        step.status = "error"
        step.caveats.append(f"执行异常: {str(error)[:120]}")
        holder["error"] = error
        raise


def generate_trace_id() -> str:
    return datetime.now(timezone.utc).strftime("trace-%Y%m%dT%H%M%SZ")


__all__ = [
    "DecisionTrace",
    "TraceStep",
    "generate_trace_id",
    "trace_context",
]
