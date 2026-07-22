from __future__ import annotations

from copy import deepcopy
import json

import pytest

from dds.reporting import build_frozen_package
from dds.services.delivery_service import (
    BrowserQAResult,
    DeliveryGateError,
    DeliveryService,
)


_BROWSER_CHECKS = {
    "console_error_free": True,
    "first_to_last_scroll": True,
    "mounted_pages_bounded": True,
    "native_scrollbar_hidden": True,
    "offline_runtime": True,
    "presentation_navigation": True,
    "right_black_rail_absent": True,
    "source_drawer_union": True,
    "top_navigation": True,
    "visuals_readable": True,
}
_PRINT_CHECKS = {
    "console_error_free": True,
    "horizontal_overflow_absent": True,
    "images_exist_and_decode": True,
    "offline_runtime": True,
    "pages_exist": True,
    "print_media_active": True,
}


def report_seed():
    return {
        "meta": {"as_of": "2026-07-22", "compiled_at": "2026-07-22T00:00:00Z"},
        "project": {"project_id": "delivery-1", "name": "Delivery Fixture"},
        "project_panorama": {"required_units": ["SC1"]},
        "page_manifest_authoritative": True,
        "page_manifest": [
            {
                "page_id": "sc1",
                "chapter_id": "decision",
                "section_id": "SC1",
                "unit_id": "SC1",
                "unit_status": "ready",
                "layout": "summary",
                "title": "Decision boundary",
                "takeaway": "Evidence-bound",
                "blocks": [{"type": "narrative", "text": "No silent gaps."}],
                "source_refs": ["SRC-1"],
                "confidence": {"score": 0.8},
                "evidence_type": "analysis_inference",
            }
        ],
        "source_registry": [
            {"source_id": "SRC-1", "title": "Fixture", "kind": "fixture"}
        ],
    }


def browser_report(html_hash: str) -> dict:
    rows = []
    for width, height in ((1280, 720), (1440, 900), (1920, 1080)):
        rows.append(
            {
                "width": width,
                "height": height,
                "passed": True,
                "checks": dict(_BROWSER_CHECKS),
                "console_errors": [],
                "page_errors": [],
                "dependency_requests": [],
                "metrics": {
                    "horizontal_overflow_px": 0,
                    "runtime_ready": True,
                    "runtime_error_visible": False,
                },
                "screenshot": f"output/browser-qa-{width}x{height}.png",
            }
        )
    return {
        "schema_version": "dds.browser-qa/1.0",
        "runner": "playwright-python-sync-api",
        "passed": True,
        "report_document_hash": "sha256:" + "1" * 64,
        "html_hash": "sha256:" + html_hash,
        "checks": dict(_BROWSER_CHECKS),
        "viewports": rows,
        "metrics": {"dependency_request_count": 0, "max_mounted_pages": 5},
        "dependency_requests": [],
        "artifact_directory": "output",
    }


def print_report(html_hash: str) -> dict:
    return {
        "schema_version": "dds.print-browser-qa/1.0",
        "passed": True,
        "html_hash": "sha256:" + html_hash,
        "checks": dict(_PRINT_CHECKS),
        "console_errors": [],
        "page_errors": [],
        "dependency_requests": [],
        "metrics": {
            "decoded_images": 1,
            "horizontal_overflow_px": 0,
            "images": 1,
            "media_print": True,
            "prepared_pages": 1,
            "surfaces": 1,
            "visible_pages": 1,
        },
        "viewport": {"width": 1920, "height": 1080},
        "screenshot": "output/browser-qa-print-1920x1080.png",
    }


def test_delivery_requires_official_qa_for_exact_html_and_writes_atomically(tmp_path):
    package = build_frozen_package(
        report_seed(), project_id="delivery-1", as_of="2026-07-22"
    )
    service = DeliveryService(tmp_path)
    html, html_hash, _ = service.render(package)
    browser_path = tmp_path / "browser_qa.json"
    print_path = tmp_path / "print_qa.json"
    browser_path.write_text(json.dumps(browser_report(html_hash)), encoding="utf-8")
    print_path.write_text(json.dumps(print_report(html_hash)), encoding="utf-8")

    qa = BrowserQAResult.from_reports(browser_path, print_path)

    assert qa.passed
    assert qa.html_hash == html_hash
    artifact = service.deliver(
        package, relative_path="delivery/report.html", browser_qa=qa
    )
    assert artifact.path.read_text(encoding="utf-8") == html
    assert artifact.html_hash == html_hash


def test_stale_or_incomplete_browser_qa_cannot_overwrite_old_report(tmp_path):
    package = build_frozen_package(
        report_seed(), project_id="delivery-1", as_of="2026-07-22"
    )
    service = DeliveryService(tmp_path)
    target = tmp_path / "stable.html"
    target.write_text("OLD", encoding="utf-8")
    qa = BrowserQAResult.from_reports(
        browser_report("0" * 64), print_report("0" * 64)
    )
    with pytest.raises(DeliveryGateError, match="different rendered HTML bytes"):
        service.deliver(package, relative_path="stable.html", browser_qa=qa)
    assert target.read_text(encoding="utf-8") == "OLD"


def test_from_reports_rejects_missing_viewport_even_if_aggregate_passed():
    browser = browser_report("a" * 64)
    browser["viewports"].pop()

    result = BrowserQAResult.from_reports(browser, print_report("a" * 64))

    assert not result.passed
    assert any("exactly 1280x720" in error for error in result.validation_errors)


def test_from_reports_rejects_print_hash_mismatch():
    result = BrowserQAResult.from_reports(
        browser_report("a" * 64), print_report("b" * 64)
    )

    assert not result.passed
    assert any("hashes do not match" in error for error in result.validation_errors)


@pytest.mark.parametrize(
    "case",
    [
        "console_error",
        "page_error",
        "external_request",
        "one_pixel_overflow",
        "image_decode",
        "fake_viewport_passed",
        "missing_error_field",
    ],
)
def test_from_reports_recomputes_passed_from_raw_evidence(case):
    browser = browser_report("c" * 64)
    printed = print_report("c" * 64)
    if case == "console_error":
        browser["viewports"][0]["console_errors"] = ["ReferenceError"]
    elif case == "page_error":
        browser["viewports"][1]["page_errors"] = ["page crashed"]
    elif case == "external_request":
        browser["dependency_requests"] = ["https://cdn.example/runtime.js"]
        browser["metrics"]["dependency_request_count"] = 1
    elif case == "one_pixel_overflow":
        browser["viewports"][2]["metrics"]["horizontal_overflow_px"] = 1
    elif case == "image_decode":
        printed["metrics"]["decoded_images"] = 0
    elif case == "fake_viewport_passed":
        browser["viewports"][0]["passed"] = False
    elif case == "missing_error_field":
        del browser["viewports"][0]["page_errors"]

    result = BrowserQAResult.from_reports(browser, printed)

    assert not result.passed
    assert result.validation_errors


def test_direct_construction_is_not_a_valid_attestation():
    result = BrowserQAResult(
        html_hash="a" * 64,
        viewports_checked=("1280x720", "1440x900", "1920x1080"),
        print_layout_passed=True,
    )

    assert not result.passed


def test_partial_package_is_rejected_before_output(tmp_path):
    seed = deepcopy(report_seed())
    seed["project_panorama"] = {"required_units": ["SC1", "SC2"]}
    package = build_frozen_package(seed, project_id="partial", as_of="2026-07-22")
    with pytest.raises(DeliveryGateError, match="not delivery ready"):
        DeliveryService(tmp_path).render(package)


