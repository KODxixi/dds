from __future__ import annotations

import hashlib
import json


from dds.reporting.renderer import render_cinematic_deck_html
from dds.reporting.report_document import build_report_document
from test_reporting_document import report_seed


def test_renderer_hash_is_stable_and_output_is_self_contained() -> None:
    document = build_report_document(report_seed())

    rendered = render_cinematic_deck_html({"report_document": document})
    repeated = render_cinematic_deck_html({"report_document": document})

    assert rendered == repeated
    assert hashlib.sha256(rendered.encode("utf-8")).hexdigest() == (
        "f9d511011ba5f5fb12c1585d6f954dc5b15c2eec92b1499b584284a642ea2042"
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
    assert 'sectionNav.setAttribute("data-section-nav","")' in rendered
    assert '"id":"gary-ui"' in rendered
    assert '"recipe":"decision-report"' in rendered
    assert '"integration_mode":"approved-template-origin"' in rendered
    assert '"compiled_template_hash":"5ae75f40d52f6d82aa6a124c41a16dd67e89fba17fb7558f6173405341c6ca39"' in rendered
    assert '"projection_hash":"d87cb7a893de229d7da460ab6b30df37e53e06a8168093314f1f8b240dc62f65"' in rendered
    assert "C:\\AI" not in rendered


def test_renderer_html_is_stable_after_document_json_round_trip() -> None:
    document = build_report_document(report_seed())
    persisted = json.loads(
        json.dumps(document, ensure_ascii=False, sort_keys=True)
    )

    assert render_cinematic_deck_html({"report_document": document}) == (
        render_cinematic_deck_html({"report_document": persisted})
    )






