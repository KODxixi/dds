"""报告导出 Agent (ReportExporterAgent) - 生成自包含 HTML 报告。

核心设计：
- 输出为单文件 HTML，内嵌 CSS，无外部依赖，方便在任意浏览器直接打开
- 视觉风格：Apple Liquid Glass 暗色基调，适合演示
- 内容结构：封面 → SC/AD/VA/CS 分组 → 置信度仪表盘 → 数据缺口清单
- 对降级字段使用明确的颜色标记，不掩盖数据质量问题
"""

from __future__ import annotations

import html
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dds.agents.base import AgentResult, AgentStatus, AgentTask, BaseAgent
from dds.contracts import (
    GROUP_BY_ID,
    REPORT_GROUPS,
    SECTION_ORDER,
    UNIT_BY_ID,
    SectionData,
    confidence_level,
)

# 默认输出目录
DEFAULT_OUTPUT_DIR = Path("d:/Vault-assets/AI_Projects/DDS_v2/output")


class ReportExporterAgent(BaseAgent):
    """报告导出 Agent。"""

    @property
    def agent_id(self) -> str:
        return "report_exporter"

    @property
    def agent_name(self) -> str:
        return "报告导出 Agent"

    async def execute(self, task: AgentTask) -> AgentResult:
        sections = task.parameters.get("sections", {})
        ctx = task.parameters.get("project_context", {}) or {}
        validation = task.parameters.get("validation", {}) or {}
        output_dir = task.parameters.get("output_dir")

        if not sections:
            return AgentResult(
                task_id=task.task_id,
                success=False,
                agent_id=self.agent_id,
                status=AgentStatus.FAILED,
                errors=["没有可导出的 sections"],
            )

        out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        report_id = task.parameters.get("report_id", f"RPT-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        filename = f"DDS_Report_{report_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        out_path = out_dir / filename

        html_content = self._build_html(report_id, sections, ctx, validation)
        out_path.write_text(html_content, encoding="utf-8")

        return AgentResult(
            task_id=task.task_id,
            success=True,
            agent_id=self.agent_id,
            status=AgentStatus.COMPLETED,
            data={
                "output_path": str(out_path),
                "filename": filename,
                "report_id": report_id,
            },
        )

    # ─────────────────────────────────────────────────────────
    # HTML 构建
    # ─────────────────────────────────────────────────────────

    def _build_html(
        self,
        report_id: str,
        sections: dict[str, Any],
        ctx: dict[str, Any],
        validation: dict[str, Any],
    ) -> str:
        city = html.escape(str(ctx.get("city", "目标城市")))
        ptype = html.escape(str(ctx.get("project_type", "地产")))
        project_name = html.escape(str(ctx.get("project_name", report_id)))
        base_date = html.escape(str(ctx.get("base_date", datetime.now().strftime("%Y-%m-%d"))))
        overall_conf = validation.get("overall_confidence", 0.0)
        conf_level_label = self._conf_label(overall_conf)

        # 按分组渲染章节
        group_html_parts: list[str] = []
        for group in REPORT_GROUPS:
            gid = group["group_id"]
            glabel = html.escape(group["label"])
            section_parts = []
            for sid in group["units"]:
                sd = sections.get(sid)
                section_parts.append(self._render_section(sid, sd))
            group_html_parts.append(
                f"""<section class="group">
                    <h2 class="group-title">{glabel}<span class="group-id">{gid}</span></h2>
                    {''.join(section_parts)}
                </section>"""
            )

        # 置信度仪表盘数据
        section_confidences = validation.get("section_confidences", {})
        conf_bars = []
        for sid, score in sorted(section_confidences.items(), key=lambda x: SECTION_ORDER.get(x[0], 99)):
            label = html.escape(sid)
            bar_width = max(2, int(score * 100))
            level_cls = confidence_level(score)
            conf_bars.append(
                f'<div class="conf-row"><span class="conf-sid">{label}</span>'
                f'<div class="conf-bar-bg"><div class="conf-bar conf-{level_cls}" style="width:{bar_width}%"></div></div>'
                f'<span class="conf-val">{score:.2f}</span></div>'
            )

        # 数据缺口清单
        gaps_html = self._render_gap_list(sections)

        warnings = validation.get("warnings", [])
        errors = validation.get("errors", [])
        warnings_html = "".join(
            f'<li class="warn-item">⚠️ {html.escape(str(w))}</li>' for w in warnings
        )
        errors_html = "".join(
            f'<li class="err-item">❌ {html.escape(str(e))}</li>' for e in errors
        )

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DDS 决策报告 - {project_name}</title>
<style>
{self._CSS}
</style>
</head>
<body>
<div class="container">
  <header class="cover">
    <div class="cover-badge">DDS v2 · 数据驱动地产决策报告</div>
    <h1 class="cover-title">{city} · {ptype}项目决策分析</h1>
    <div class="cover-meta">
      <span>项目：{project_name}</span>
      <span>基准日：{base_date}</span>
      <span>报告编号：{html.escape(report_id)}</span>
      <span>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
    </div>
    <div class="overall-conf">
      <div class="overall-label">整体置信度</div>
      <div class="overall-value conf-{confidence_level(overall_conf)}">{overall_conf:.2f}</div>
      <div class="overall-level">{conf_level_label}</div>
    </div>
  </header>

  <section class="panel">
    <h2>置信度分布</h2>
    <div class="conf-list">{''.join(conf_bars)}</div>
  </section>

  {''.join(group_html_parts)}

  <section class="panel">
    <h2>数据缺口清单</h2>
    {gaps_html}
  </section>

  <section class="panel">
    <h2>校验信息</h2>
    {f'<ul class="warn-list">{warnings_html}</ul>' if warnings else '<p class="muted">无警告。</p>'}
    {f'<ul class="err-list">{errors_html}</ul>' if errors else ''}
  </section>

  <footer class="footer">
    本报告由 DDS v2 多Agent 架构自动生成。所有降级数据均已标注来源，建议结合人工复核后用于决策。
  </footer>
</div>
</body>
</html>"""

    def _render_section(self, sid: str, sd: Any) -> str:
        unit_meta = UNIT_BY_ID.get(sid, {})
        title = html.escape(unit_meta.get("title", sid))
        question = html.escape(unit_meta.get("question", ""))

        if isinstance(sd, SectionData):
            data_dict = sd.data
            confidence = sd.confidence
            narrative = data_dict.get("narrative", "")
        else:
            data_dict = sd if isinstance(sd, dict) else {}
            confidence = {}
            narrative = ""

        score = confidence.get("score", 0.0) if isinstance(confidence, dict) else 0.0
        level = confidence_level(score)

        # 将 Markdown-like 的 narrative 做简单 HTML 转换
        narrative_html = self._markdown_to_html(narrative)

        return f"""<article class="section section-{level}" id="sec-{html.escape(sid)}">
            <header class="section-header">
              <span class="section-id">{html.escape(sid)}</span>
              <h3 class="section-title">{title}</h3>
              <span class="section-conf conf-{level}">{score:.2f}</span>
            </header>
            {f'<p class="section-question">{question}</p>' if question else ''}
            <div class="section-body">{narrative_html}</div>
        </article>"""

    def _render_gap_list(self, sections: dict[str, Any]) -> str:
        # 尝试从 CS 章节的 data_gaps 读取
        cs = sections.get("CS")
        if isinstance(cs, SectionData):
            gaps = cs.data.get("data_gaps", [])
        elif isinstance(cs, dict):
            gaps = cs.get("data_gaps", [])
        else:
            gaps = []

        if not gaps:
            return '<p class="muted">无数据缺口，所有字段均为真实数据。</p>'

        rows = []
        for gap in gaps:
            sec = html.escape(str(gap.get("section", "?")))
            field = html.escape(str(gap.get("field", "?")))
            strategy = html.escape(str(gap.get("strategy", "?")))
            rows.append(
                f'<tr><td>{sec}</td><td>{field}</td><td><span class="badge badge-{strategy}">{strategy}</span></td></tr>'
            )
        return f"""<table class="gap-table">
            <thead><tr><th>章节</th><th>缺失字段</th><th>降级策略</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>"""

    @staticmethod
    def _conf_label(score: float) -> str:
        return {"high": "高", "medium": "中", "low": "低", "undecidable": "不可判定"}[confidence_level(score)]

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """极简 Markdown → HTML：处理 ### 标题、**加粗**、*斜体*、列表项、段落。"""
        if not text:
            return ""
        lines = text.split("\n")
        html_parts: list[str] = []
        in_list = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                continue
            if stripped.startswith("### "):
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                html_parts.append(f"<h4>{html.escape(stripped[4:])}</h4>")
            elif stripped.startswith("- "):
                if not in_list:
                    html_parts.append("<ul>")
                    in_list = True
                item = stripped[2:]
                item = item.replace("**", "<strong>", 1).replace("**", "</strong>", 1) if "**" in item else item
                html_parts.append(f"<li>{html.escape(item).replace('&lt;strong&gt;', '<strong>').replace('&lt;/strong&gt;', '</strong>')}</li>")
            else:
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                # 处理 **bold** 和 *italic*
                esc = html.escape(stripped)
                esc = esc.replace("**", "<strong>", 1).replace("**", "</strong>", 1) if esc.count("**") >= 2 else esc
                html_parts.append(f"<p>{esc}</p>")
        if in_list:
            html_parts.append("</ul>")
        return "\n".join(html_parts)

    _CSS = """
    :root {
      --bg: #0a0e1a;
      --bg-panel: rgba(22, 28, 46, 0.72);
      --border: rgba(255,255,255,0.08);
      --text: #e6e9f2;
      --text-muted: #8a93a8;
      --accent: #6ea8ff;
      --conf-high: #2dd4bf;
      --conf-medium: #fbbf24;
      --conf-low: #f87171;
      --conf-undecidable: #9ca3af;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
      background: radial-gradient(ellipse at top, #1a2142 0%, var(--bg) 60%);
      color: var(--text);
      line-height: 1.7;
      padding: 32px 16px;
    }
    .container { max-width: 1080px; margin: 0 auto; }
    .cover {
      text-align: center;
      padding: 64px 24px;
      background: linear-gradient(135deg, rgba(110,168,255,0.12), rgba(167,139,250,0.08));
      border: 1px solid var(--border);
      border-radius: 24px;
      margin-bottom: 32px;
      backdrop-filter: blur(20px);
    }
    .cover-badge {
      display: inline-block;
      padding: 4px 14px;
      background: rgba(110,168,255,0.15);
      color: var(--accent);
      border-radius: 20px;
      font-size: 12px;
      letter-spacing: 1px;
      margin-bottom: 16px;
    }
    .cover-title { font-size: 32px; font-weight: 600; margin-bottom: 20px; letter-spacing: 1px; }
    .cover-meta { display: flex; justify-content: center; gap: 24px; flex-wrap: wrap; color: var(--text-muted); font-size: 13px; margin-bottom: 28px; }
    .overall-conf { display: inline-block; padding: 16px 32px; border-radius: 16px; background: rgba(255,255,255,0.04); border: 1px solid var(--border); }
    .overall-label { font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }
    .overall-value { font-size: 42px; font-weight: 700; line-height: 1; }
    .overall-level { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
    .panel, .group {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 28px;
      margin-bottom: 24px;
      backdrop-filter: blur(12px);
    }
    .group-title {
      font-size: 20px;
      font-weight: 600;
      margin-bottom: 20px;
      padding-bottom: 12px;
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 10px;
    }
    .group-id { font-size: 12px; color: var(--accent); background: rgba(110,168,255,0.12); padding: 2px 10px; border-radius: 10px; }
    .section {
      background: rgba(255,255,255,0.02);
      border: 1px solid var(--border);
      border-left: 3px solid var(--accent);
      border-radius: 12px;
      padding: 20px 24px;
      margin-bottom: 16px;
    }
    .section-header { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
    .section-id { font-family: "SF Mono", Consolas, monospace; font-size: 12px; font-weight: 600; color: var(--accent); background: rgba(110,168,255,0.1); padding: 2px 8px; border-radius: 6px; }
    .section-title { font-size: 16px; font-weight: 600; flex: 1; }
    .section-conf { font-family: "SF Mono", Consolas, monospace; font-size: 13px; font-weight: 600; padding: 2px 10px; border-radius: 6px; }
    .section-question { color: var(--text-muted); font-size: 13px; font-style: italic; margin-bottom: 12px; padding-left: 10px; border-left: 2px solid var(--border); }
    .section-body { font-size: 14px; color: var(--text); }
    .section-body p { margin-bottom: 8px; }
    .section-body ul { margin: 8px 0 8px 20px; }
    .section-body li { margin-bottom: 4px; font-size: 13px; }
    .section-body h4 { font-size: 14px; color: var(--accent); margin: 12px 0 8px; }
    .section-body strong { color: #fff; }
    .conf-high { color: var(--conf-high) !important; background: rgba(45,212,191,0.12); }
    .conf-medium { color: var(--conf-medium) !important; background: rgba(251,191,36,0.12); }
    .conf-low { color: var(--conf-low) !important; background: rgba(248,113,113,0.12); }
    .conf-undecidable { color: var(--conf-undecidable) !important; background: rgba(156,163,175,0.12); }
    .conf-list { display: flex; flex-direction: column; gap: 8px; }
    .conf-row { display: flex; align-items: center; gap: 12px; font-size: 13px; }
    .conf-sid { width: 48px; font-family: "SF Mono", Consolas, monospace; color: var(--text-muted); }
    .conf-bar-bg { flex: 1; height: 8px; background: rgba(255,255,255,0.06); border-radius: 4px; overflow: hidden; }
    .conf-bar { height: 100%; border-radius: 4px; transition: width 0.6s ease; }
    .conf-bar.conf-high { background: var(--conf-high); }
    .conf-bar.conf-medium { background: var(--conf-medium); }
    .conf-bar.conf-low { background: var(--conf-low); }
    .conf-bar.conf-undecidable { background: var(--conf-undecidable); }
    .conf-val { width: 48px; text-align: right; font-family: "SF Mono", Consolas, monospace; color: var(--text-muted); }
    .gap-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 12px; }
    .gap-table th, .gap-table td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }
    .gap-table th { color: var(--text-muted); font-weight: 500; font-size: 12px; }
    .badge { padding: 2px 8px; border-radius: 6px; font-size: 11px; font-family: "SF Mono", Consolas, monospace; }
    .badge-city_benchmark { background: rgba(110,168,255,0.15); color: var(--accent); }
    .badge-project_type_average { background: rgba(167,139,250,0.15); color: #a78bfa; }
    .badge-national_benchmark { background: rgba(251,191,36,0.15); color: var(--conf-medium); }
    .badge-human_input_required { background: rgba(248,113,113,0.15); color: var(--conf-low); }
    .muted { color: var(--text-muted); font-size: 13px; }
    .warn-list, .err-list { list-style: none; margin-top: 8px; }
    .warn-item, .err-item { padding: 6px 0; font-size: 13px; }
    .footer { text-align: center; color: var(--text-muted); font-size: 12px; padding: 32px 0; }
    """
