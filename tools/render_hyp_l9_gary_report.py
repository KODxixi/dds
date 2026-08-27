#!/usr/bin/env python
"""Render the L-9 ReportDocument with a project-local Gary-UI composition.

The canonical DDS document and runtime are unchanged.  This adapter creates a
temporary copy of the read-only report shell, applies the local page renderer
and Gary token projection, then emits one self-contained HTML file.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import tempfile
from pathlib import Path


SKILL_DIR = Path(r"C:\Users\shiguanyu\.agents\skills\arch-front-html")
TEMPLATE_DIR = SKILL_DIR / "templates"
RENDER_SCRIPT = SKILL_DIR / "scripts" / "render_report_html.py"
GARY_TOKENS = Path(r"C:\AI\UI\gary-ui\tokens\base.css")
PATCH_JS = Path(__file__).with_name("gary_report_page_patch.js")
GARY_CSS = Path(__file__).with_name("gary_l9_report.css")


def load_renderer():
    spec = importlib.util.spec_from_file_location("_hyp_l9_report_renderer", RENDER_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载报告渲染器：{RENDER_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patch_app(source: str) -> str:
    snippet = PATCH_JS.read_text(encoding="utf-8")
    marker = "  function renderBlock(block) {"
    if "function renderGaryPage(page, index)" not in source:
        if marker not in source:
            raise RuntimeError("报告 app.js 缺少 renderBlock 注入点")
        source = source.replace(marker, snippet + "\n" + marker, 1)
    source = source.replace(
        marker + "\n",
        marker + "\n    if (block && block.type === \"prompt\") return renderPromptBlock(block);\n",
        1,
    )
    old_page = "  function renderPage(page, index) {\n"
    if old_page not in source:
        raise RuntimeError("报告 app.js 缺少 renderPage 注入点")
    if "return renderGaryPage(page, index);" not in source:
        source = source.replace(old_page, old_page + "    return renderGaryPage(page, index);\n", 1)
    if "attachPromptCopy();" not in source:
        source = source.replace("      buildGlobalIndex();\n", "      buildGlobalIndex();\n      attachPromptCopy();\n", 1)
    # Prompt text is part of the page evidence and should be available to the
    # runtime token/a11y checks as well as to the human reader.
    source = source.replace(
        "[page.takeaway, page.table, page.tables, page.decision_gate, page.owner, page.case_mechanism, page.market_outcome_case]",
        "[page.takeaway, page.blocks, page.table, page.tables, page.decision_gate, page.owner, page.case_mechanism, page.market_outcome_case]",
        1,
    )
    return source


def prepare_template(root: Path) -> None:
    target = root / "templates"
    shutil.copytree(TEMPLATE_DIR, target)
    app_path = target / "app.js"
    app_path.write_text(patch_app(app_path.read_text(encoding="utf-8")), encoding="utf-8")

    css_path = target / "vendor" / "gary-l9.css"
    css_path.write_text(GARY_CSS.read_text(encoding="utf-8"), encoding="utf-8")

    token_path = target / "vendor" / "gary-tokens.css"
    tokens = GARY_TOKENS.read_text(encoding="utf-8")
    # The canonical token entry imports the component projection.  The report
    # already carries its own shell components; remove that import and the
    # optional scene image URL so the final file remains fully offline.
    tokens = re.sub(r"^\s*@import[^;]+;\s*", "", tokens, count=1, flags=re.MULTILINE)
    tokens = tokens.replace('url("../assets/backgrounds/gary-default-scene.png")', "none")
    token_path.write_text(tokens, encoding="utf-8")

    shell_path = target / "report_shell.html"
    shell = shell_path.read_text(encoding="utf-8")
    shell = shell.replace(
        '<link rel="stylesheet" href="report.css">',
        '<link rel="stylesheet" href="vendor/gary-tokens.css" data-gary-source="tokens/base.css">\n  <link rel="stylesheet" href="report.css">',
        1,
    )
    shell = shell.replace(
        '<link rel="stylesheet" href="report-modes.css">',
        '<link rel="stylesheet" href="report-modes.css">\n  <link rel="stylesheet" href="vendor/gary-l9.css" data-gary-composition="decision-report">',
        1,
    )
    shell = shell.replace(
        '<body class="dark architect-edition report-mode-reading" data-dds-report data-report-mode="reading">',
        '<body class="dark architect-edition report-mode-reading" data-dds-report data-report-mode="reading" data-gary-ui data-gary-theme="dark" data-gary-material="regular" data-gary-density="balanced" data-gary-application-mode="scroll-report" data-gary-page-mode="data-page">',
        1,
    )
    shell_path.write_text(shell, encoding="utf-8")


def render(data: Path, output: Path) -> Path:
    renderer = load_renderer()
    with tempfile.TemporaryDirectory(prefix="hyp-l9-gary-template-") as temp:
        temp_root = Path(temp)
        prepare_template(temp_root)
        renderer.TEMPLATE_DIR = temp_root / "templates"
        renderer.load_shell = lambda: (renderer.TEMPLATE_DIR / "report_shell.html").read_text(encoding="utf-8")
        document = renderer._read_document(data)
        result = renderer.render_document(document, output=output)
    return Path(result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = render(args.data, args.output)
    print(f"Rendered Gary L-9 report: {output} ({output.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
