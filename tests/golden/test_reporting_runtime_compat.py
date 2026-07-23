from __future__ import annotations

import hashlib

import pytest


from dds.reporting.renderer import TEMPLATE_PATH  # noqa: E402
from dds.reporting.runtime_compat import inject_browser_qa_runtime  # noqa: E402


V2_TEMPLATE_SHA256 = "3409c3735bd36fed651e785769d5b4db8bdfcdff321524dc6b57f6da41d0a224"


def test_runtime_bridge_is_deterministic_without_mutating_v1_template() -> None:
    template_bytes = TEMPLATE_PATH.read_bytes()
    template = template_bytes.decode("utf-8")

    first = inject_browser_qa_runtime(template)
    repeated = inject_browser_qa_runtime(template)

    assert hashlib.sha256(template_bytes).hexdigest() == V2_TEMPLATE_SHA256
    assert first == repeated
    assert "window.DDSReportRuntime" in first
    assert "window.DDSReportModes" in first
    assert "preparePrint" in first
    assert 'addEventListener("beforeprint", preparePrint)' in first
    assert "sequence.forEach(index =>" in first
    assert "PRINT_BATCH_LIMIT" not in first
    assert 'stage.dataset.runtimeReady = "true"' in first
    assert 'failedStage.dataset.runtimeReady = "error"' in first
    assert 'notice.setAttribute("data-runtime-error", "")' in first
    assert 'root.setAttribute("data-section-nav", "")' in first


def test_runtime_bridge_fails_closed_on_template_drift_or_double_injection() -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    injected = inject_browser_qa_runtime(template)

    with pytest.raises(RuntimeError, match="closing marker"):
        inject_browser_qa_runtime(injected)

    with pytest.raises(RuntimeError, match="opening marker"):
        inject_browser_qa_runtime(template.replace('    "use strict";\n', "", 1))


def test_empty_manifest_sets_machine_readable_runtime_error() -> None:
    injected = inject_browser_qa_runtime(TEMPLATE_PATH.read_text(encoding="utf-8"))

    assert 'stage.dataset.runtimeReady = "error"' in injected
    assert 'stage.dataset.runtimeDiagnostic = bootstrapIssue || "empty_manifest"' in injected




