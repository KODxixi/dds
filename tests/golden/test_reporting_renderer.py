from __future__ import annotations

import hashlib


from dds.reporting.renderer import render_cinematic_deck_html
from dds.reporting.report_document import build_report_document
from test_reporting_document import report_seed


def test_renderer_hash_is_stable_and_output_is_self_contained() -> None:
    document = build_report_document(report_seed())

    rendered = render_cinematic_deck_html({"report_document": document})
    repeated = render_cinematic_deck_html({"report_document": document})

    assert rendered == repeated
    assert hashlib.sha256(rendered.encode("utf-8")).hexdigest() == (
        "37534e6cc0ddcc04a2d847981573b03c3ea742df7baed294995a691db38e6c82"
    )
    assert 'src="https://' not in rendered
    assert 'src="http://' not in rendered
    assert 'href="https://' not in rendered
    assert 'href="http://' not in rendered
    assert 'id="dds-report-payload"' in rendered
    assert "window.DDSReportRuntime" in rendered
    assert "window.DDSReportModes" in rendered
    assert "preparePrint" in rendered
    assert 'stage.dataset.runtimeReady = "true"' in rendered
    assert 'root.setAttribute("data-section-nav", "")' in rendered






