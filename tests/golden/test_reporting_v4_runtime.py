from __future__ import annotations

import hashlib
import json

from dds.reporting import PROFILE_PATH
from dds.reporting.renderer import TEMPLATE_PATH


V4_TEMPLATE_SHA256 = "5ae75f40d52f6d82aa6a124c41a16dd67e89fba17fb7558f6173405341c6ca39"


def test_v4_profile_declares_every_formal_qa_viewport() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    assert profile["viewport_policy"]["acceptance_viewports"] == [
        "1280x720",
        "1440x900",
        "1920x1080",
    ]


def test_v4_template_contains_the_embedded_runtime_contract() -> None:
    template_bytes = TEMPLATE_PATH.read_bytes()
    template = template_bytes.decode("utf-8")

    assert hashlib.sha256(template_bytes).hexdigest() == V4_TEMPLATE_SHA256
    assert "window.DDSReportRuntime" in template
    assert "window.DDSReportModes" in template
    assert "preparePrint" in template
    assert 'addEventListener("beforeprint",preparePrint)' in template
    assert "max(0,center-3)" in template
    assert 'stage.dataset.runtimeReady = "true"' in template
    assert 'stage.dataset.runtimeReady = "error"' in template
    assert 'data-runtime-error' in template
    assert 'sectionNav.setAttribute("data-section-nav","")' in template
    assert 'data-mode="reading"' in template
    assert 'data-mode="presentation"' in template
    assert "dds_report_apple_16x9" not in template


def test_v4_template_uses_continuous_reading_and_one_presentation_surface() -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert template.count('class="presentation-root"') == 1
    assert "scrollIntoView" in template
    assert "IntersectionObserver" in template
    assert "--report-width: 1480px" in template
    assert "--primary-width: 840px" in template
    assert "--decision-width: 460px" in template
    assert 'body[data-mode="reading"] { overflow-y: auto' not in template


def test_empty_manifest_sets_machine_readable_runtime_error() -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'renderFallback("empty_manifest","报告没有可展示页面")' in template
    assert 'stage.dataset.runtimeDiagnostic = code' in template


def test_source_drawer_localizes_internal_source_type_enums() -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "const sourceTypeLabel = value => ({" in template
    for label in (
        "政府公开信息",
        "甲方项目资料",
        "媒体线索",
        "内部案例库",
        "企业官方信息",
        "来源资料",
    ):
        assert label in template
    assert "const sourceType = sourceTypeLabel(source.source_type || source.type);" in template
    assert (
        "source.locator || source.url || source.source_type || source.type"
        not in template
    )
