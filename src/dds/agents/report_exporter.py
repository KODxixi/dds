"""Legacy work-report exporter retained only for V1 API compatibility.

This exporter never performs formal V4 delivery.  Every file and return value is
permanently marked ``legacy_work_report`` and ``delivery_ready=False``.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.config import Settings
from dds.contracts import REPORT_GROUPS, SECTION_ORDER, UNIT_BY_ID, SectionData, confidence_level


LEGACY_ARTIFACT_KIND = "legacy_work_report"


def _safe_filename_token(value: object) -> str:
    token = re.sub(r"[^0-9A-Za-z._\-\u3400-\u9fff]+", "-", str(value).strip())
    return token.strip(".-") or "report"


class ReportExporterAgent(BaseAgent):
    """Write a self-contained compatibility work report, never a delivery."""

    @property
    def agent_id(self) -> str:
        return "report_exporter"

    @property
    def agent_name(self) -> str:
        return "兼容工作稿导出 Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        sections = task.parameters.get("sections", {})
        context = task.parameters.get("project_context", {}) or {}
        validation = task.parameters.get("validation", {}) or {}
        if not sections:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=["没有可导出的 sections"],
            )

        explicit_output = task.parameters.get("output_dir")
        configured_output = Settings.from_env().exports_root
        output_dir = (
            Path(explicit_output) if explicit_output else configured_output
        ).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        report_id = str(
            task.parameters.get(
                "report_id", f"RPT-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            )
        )
        filename = (
            f"DDS_Work_Report_{_safe_filename_token(report_id)}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )
        output_path = output_dir / filename
        output_path.write_text(
            self._build_html(report_id, sections, context, validation), encoding="utf-8"
        )

        metadata = {
            "artifact_kind": LEGACY_ARTIFACT_KIND,
            "delivery_ready": False,
            "formal_delivery_pipeline": "frozen_v4_compiler_and_hash_bound_browser_qa",
        }
        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "output_path": str(output_path),
                "filename": filename,
                "report_id": report_id,
                "output_dir": str(output_dir),
                "artifact_kind": LEGACY_ARTIFACT_KIND,
                "delivery_ready": False,
                "metadata": metadata,
            },
        )

    def _build_html(
        self,
        report_id: str,
        sections: dict[str, Any],
        context: dict[str, Any],
        validation: dict[str, Any],
    ) -> str:
        confidence = float(validation.get("overall_confidence", 0.0) or 0.0)
        level = confidence_level(confidence)
        group_markup = []
        for group in REPORT_GROUPS:
            cards = "".join(
                self._render_section(section_id, sections.get(section_id))
                for section_id in group["units"]
            )
            group_markup.append(
                '<section class="group">'
                f'<h2>{html.escape(group["label"])}'
                f'<span class="group-id">{html.escape(group["group_id"])}</span></h2>'
                f"{cards}</section>"
            )

        confidence_markup = []
        for section_id, raw_score in sorted(
            (validation.get("section_confidences", {}) or {}).items(),
            key=lambda item: SECTION_ORDER.get(item[0], 99),
        ):
            score = float(raw_score or 0.0)
            width = max(2, min(100, int(score * 100)))
            confidence_markup.append(
                '<div class="conf-row">'
                f'<span>{html.escape(str(section_id))}</span>'
                f'<i><b style="width:{width}%"></b></i><em>{score:.2f}</em></div>'
            )

        warnings = "".join(
            f"<li>⚠ {html.escape(str(item))}</li>"
            for item in validation.get("warnings", [])
        )
        errors = "".join(
            f"<li>✕ {html.escape(str(item))}</li>"
            for item in validation.get("errors", [])
        )
        project_name = html.escape(str(context.get("project_name", report_id)))
        city = html.escape(str(context.get("city", "目标城市")))
        project_type = html.escape(str(context.get("project_type", "地产")))
        base_date = html.escape(
            str(context.get("base_date", datetime.now().strftime("%Y-%m-%d")))
        )
        validation_note = (
            f'<ul class="messages">{warnings}{errors}</ul>'
            if warnings or errors
            else '<p class="muted">没有登记警告；不代表正式交付门已通过。</p>'
        )
        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="dds-artifact-kind" content="{LEGACY_ARTIFACT_KIND}">
<meta name="dds-delivery-ready" content="false">
<title>DDS Work Report - {project_name}</title><style>{self._CSS}</style></head>
<body><div class="ribbon">WORK REPORT · 非正式交付</div><main>
<header class="cover"><div class="badge">DDS v2 · legacy_work_report 兼容工作稿</div>
<h1>{city} · {project_type}项目分析工作稿</h1>
<p class="warning">delivery_ready=false：本文件未经过 V4 冻结编译与哈希绑定浏览器 QA，不得作为正式交付物。</p>
<div class="meta"><span>项目：{project_name}</span><span>基准日：{base_date}</span>
<span>工作稿编号：{html.escape(report_id)}</span><span>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</span></div>
<div class="overall"><small>旧链路参考置信度</small>
<strong class="conf-{level}">{confidence:.2f}</strong><small>{self._conf_label(confidence)}</small></div></header>
<section class="panel"><h2>置信度分布</h2><div class="conf-list">{''.join(confidence_markup)}</div></section>
{''.join(group_markup)}
<section class="panel"><h2>数据缺口清单</h2>{self._render_gap_list(sections)}</section>
<section class="panel"><h2>旧链路校验信息</h2>{validation_note}</section>
<footer>DDS v2 兼容工作稿 · delivery_ready=false · 正式交付必须使用 Frozen EvidencePackage → V4 ReportDocument → hash-bound browser QA。</footer>
</main></body></html>"""

    def _render_section(self, section_id: str, section: Any) -> str:
        unit = UNIT_BY_ID.get(section_id, {})
        if isinstance(section, SectionData):
            data = section.data
            confidence = section.confidence
        else:
            data = section if isinstance(section, dict) else {}
            confidence = {}
        score = (
            float(confidence.get("score", 0.0) or 0.0)
            if isinstance(confidence, dict)
            else 0.0
        )
        question = html.escape(str(unit.get("question", "")))
        question_markup = (
            f'<p class="question">{question}</p>' if question else ""
        )
        narrative = self._markdown_to_html(str(data.get("narrative", "")))
        return (
            f'<article class="section" id="sec-{html.escape(section_id)}">'
            '<div class="section-head">'
            f'<span class="section-id">{html.escape(section_id)}</span>'
            f'<h3>{html.escape(str(unit.get("title", section_id)))}</h3>'
            f'<span class="score conf-{confidence_level(score)}">{score:.2f}</span></div>'
            f"{question_markup}<div class=\"content\">{narrative}</div></article>"
        )

    def _render_gap_list(self, sections: dict[str, Any]) -> str:
        cs = sections.get("CS")
        if isinstance(cs, SectionData):
            gaps = cs.data.get("data_gaps", [])
        elif isinstance(cs, dict):
            gaps = cs.get("data_gaps", [])
        else:
            gaps = []
        if not gaps:
            return '<p class="muted">未登记数据缺口；这不等于证据有效或正式交付就绪。</p>'
        rows = []
        for gap in gaps:
            if isinstance(gap, dict):
                rows.append(
                    "<tr>"
                    f'<td>{html.escape(str(gap.get("section", "?")))}</td>'
                    f'<td>{html.escape(str(gap.get("field", "?")))}</td>'
                    f'<td>{html.escape(str(gap.get("strategy", "unknown")))}</td></tr>'
                )
        return (
            "<table><thead><tr><th>章节</th><th>缺失字段</th><th>状态</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

    @staticmethod
    def _conf_label(score: float) -> str:
        labels = {"high": "高", "medium": "中", "low": "低", "undecidable": "不可判定"}
        return labels[confidence_level(score)]

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        if not text:
            return '<p class="muted">本节尚无叙事内容。</p>'
        return "".join(
            f"<p>{html.escape(line.strip())}</p>"
            for line in text.splitlines()
            if line.strip()
        )

    _CSS = """
    :root{--bg:#080d18;--panel:rgba(21,29,48,.78);--border:rgba(255,255,255,.1);
    --text:#e8edf8;--muted:#8d98ad;--accent:#6ea8ff;--high:#2dd4bf;--mid:#fbbf24;
    --low:#f87171}*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,
    "PingFang SC","Microsoft YaHei",sans-serif;background:radial-gradient(ellipse at top,#1a2648,
    var(--bg) 62%);color:var(--text);line-height:1.7;padding:32px 16px 60px}main{max-width:1080px;
    margin:auto}.ribbon{position:fixed;z-index:10;top:18px;right:-48px;transform:rotate(35deg);
    width:230px;text-align:center;padding:7px;color:#fff;background:#b4232c;font-size:11px;
    font-weight:800;letter-spacing:1px;box-shadow:0 5px 20px #0007}.cover,.panel,.group{
    background:var(--panel);border:1px solid var(--border);border-radius:22px;padding:28px;
    margin-bottom:24px;backdrop-filter:blur(16px)}.cover{text-align:center;padding:60px 28px;
    background:linear-gradient(135deg,#6ea8ff24,#b85dff14)}.badge{display:inline-block;padding:5px 15px;
    margin-bottom:16px;border-radius:20px;color:var(--accent);background:#6ea8ff24;font-size:12px}
    h1{font-size:32px;margin-bottom:15px}.warning{max-width:760px;margin:0 auto 22px;padding:12px 16px;
    border:1px solid #f8717159;border-radius:12px;color:#fecaca;background:#f8717114;font-size:13px}
    .meta{display:flex;justify-content:center;gap:22px;flex-wrap:wrap;color:var(--muted);font-size:13px;
    margin-bottom:25px}.overall{display:inline-flex;flex-direction:column;padding:14px 32px;border-radius:16px;
    background:#ffffff0a;border:1px solid var(--border)}.overall strong{font-size:40px;line-height:1.2}
    .overall small{color:var(--muted)}h2{display:flex;gap:10px;align-items:center;font-size:20px;
    margin-bottom:18px}.group-id,.section-id{color:var(--accent);background:#6ea8ff1c;border-radius:8px;
    padding:2px 9px;font:600 12px Consolas,monospace}.section{margin-bottom:16px;padding:20px 24px;
    background:#ffffff06;border:1px solid var(--border);border-left:3px solid var(--accent);
    border-radius:13px}.section-head{display:flex;align-items:center;gap:12px;margin-bottom:10px}.section-head h3{
    flex:1;font-size:16px}.score{padding:2px 10px;border-radius:7px;font:600 13px Consolas,monospace}
    .question{color:var(--muted);font-size:13px;font-style:italic;border-left:2px solid var(--border);
    padding-left:10px;margin-bottom:12px}.content{font-size:14px}.content p{margin:7px 0}.conf-high{
    color:var(--high)!important;background:#2dd4bf1c}.conf-medium{color:var(--mid)!important;
    background:#fbbf241c}.conf-low{color:var(--low)!important;background:#f871711c}.conf-undecidable{
    color:#9ca3af!important;background:#9ca3af1c}.conf-list{display:flex;flex-direction:column;gap:8px}
    .conf-row{display:flex;align-items:center;gap:12px;font:13px Consolas,monospace}.conf-row span{width:48px;
    color:var(--muted)}.conf-row i{flex:1;height:8px;overflow:hidden;border-radius:5px;background:#ffffff0f}
    .conf-row b{display:block;height:100%;background:var(--accent)}.conf-row em{width:48px;text-align:right;
    color:var(--muted);font-style:normal}table{width:100%;border-collapse:collapse;font-size:13px}th,td{
    padding:10px 12px;text-align:left;border-bottom:1px solid var(--border)}th{color:var(--muted)}
    .muted{color:var(--muted);font-size:13px}.messages{list-style:none}.messages li{padding:5px 0}
    footer{text-align:center;color:var(--muted);padding:28px 14px;font-size:12px}@media(max-width:720px){
    body{padding:18px 10px 40px}.cover{padding:45px 18px}h1{font-size:24px}.panel,.group{padding:18px}
    .section{padding:17px}}@media print{body{background:#fff;color:#111;padding:0}.ribbon{display:none}
    .cover,.panel,.group,.section{color:#111;background:#fff;border-color:#bbb;break-inside:avoid}.warning{
    color:#8b0000;border-color:#8b0000}}
    """


__all__ = ["LEGACY_ARTIFACT_KIND", "ReportExporterAgent"]
