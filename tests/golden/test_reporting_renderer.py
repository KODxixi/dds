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
        "3f98ffc7c50decae82b7be628dbef3048aa3f15291e42af4db3bc5c7672b2689"
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






