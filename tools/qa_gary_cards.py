"""Visual/readability probe for the local Gary report composition.

This is intentionally separate from the canonical delivery QA: it checks the
long-form evidence cards that the project-local Gary renderer adds, especially
vertical clipping and prompt/table readability at representative pages.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: qa_gary_cards.py REPORT.html [ARTIFACT_DIR]")
    html_path = Path(sys.argv[1]).resolve()
    artifact_dir = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) > 2
        else html_path.parent / "gary_card_qa"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    sample_indices = [0, 6, 7, 11, 14, 21, 26, 33, 43, 44]

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=str(executable) if executable.is_file() else None,
            args=["--allow-file-access-from-files", "--disable-background-networking"],
        )
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=1,
                locale="zh-CN",
                reduced_motion="reduce",
            )
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.goto(html_path.as_uri(), wait_until="load", timeout=20_000)
            page.wait_for_function(
                "() => document.querySelector('[data-page-stage]')?.dataset.runtimeReady === 'true'",
                timeout=10_000,
            )
            page.wait_for_timeout(180)
            total_pages = int(
                page.evaluate(
                    "() => window.DDSReportRuntime?.pages?.length || 0"
                )
            )
            indices = list(range(total_pages))
            rows: list[dict[str, object]] = []
            for index in indices:
                page.evaluate(
                    """(index) => {
                      const modes = window.DDSReportModes;
                      const runtime = window.DDSReportRuntime;
                      modes?.setMode('reading', { restoreScroll: false });
                      runtime?.setActiveIndex(index, { scroll: true, smooth: false, updateHash: false });
                    }""",
                    index,
                )
                page.wait_for_timeout(520)
                row = page.evaluate(
                    """(index) => {
                      const article = document.querySelector(`[data-mounted-page="${index}"]`);
                      if (!article) return { index, mounted: false };
                      const selectors = [
                        '.gary-page-layout', '.gary-page-primary', '.gary-visual-frame',
                        '.gary-evidence-grid', '.gary-decision-panel', '.gary-conclusion-bar',
                        '.gary-evidence-grid > *', '.report-table', '.report-block',
                        '.gary-prompt-block', '.source-index'
                      ];
                      const clipped = [];
                      const inspected = [];
                      for (const selector of selectors) {
                        for (const node of article.querySelectorAll(selector)) {
                          const style = getComputedStyle(node);
                          const rect = node.getBoundingClientRect();
                          const verticalOverflow = node.scrollHeight > node.clientHeight + 2;
                          const hidesOverflow = ['hidden', 'clip'].includes(style.overflowY) ||
                            ['hidden', 'clip'].includes(style.overflow);
                          const item = {
                            selector,
                            height: Math.round(rect.height),
                            scrollHeight: node.scrollHeight,
                            clientHeight: node.clientHeight,
                            overflowY: style.overflowY,
                            overflow: style.overflow,
                          };
                          inspected.push(item);
                          if (verticalOverflow && hidesOverflow) clipped.push(item);
                        }
                      }
                      return {
                        index,
                        mounted: true,
                        pageId: article.getAttribute('data-page-id') || article.dataset.pageId || '',
                        title: article.getAttribute('aria-label') || '',
                        articleHeight: Math.round(article.getBoundingClientRect().height),
                        articleScrollHeight: article.scrollHeight,
                        blockCount: article.querySelectorAll('.gary-evidence-grid > *').length,
                        tableCount: article.querySelectorAll('.report-table').length,
                        promptCount: article.querySelectorAll('.gary-prompt-block').length,
                        chartCount: article.querySelectorAll('.runtime-chart').length,
                        diagramCount: article.querySelectorAll('.runtime-diagram').length,
                        textLength: (article.innerText || '').length,
                        clipped,
                        inspectedCount: inspected.length,
                      };
                    }""",
                    index,
                )
                if index in sample_indices:
                    screenshot = artifact_dir / f"gary-page-{index + 1:02d}.png"
                    locator = page.locator(f'[data-mounted-page="{index}"]').first
                    if locator.count():
                        # Element screenshots capture the full element box even
                        # when its lower portion extends beyond the viewport.
                        locator.screenshot(path=str(screenshot))
                    row["screenshot"] = str(screenshot)
                rows.append(row)
            context.close()
        finally:
            browser.close()
    result = {
        "report": str(html_path),
        "viewport": {"width": 1440, "height": 900},
        "sample_indices": sample_indices,
        "total_pages": len(indices),
        "console_or_page_errors": errors,
        "passed": not errors and all(not row.get("clipped") for row in rows),
        "pages": rows,
    }
    output = artifact_dir / "gary_card_qa.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
