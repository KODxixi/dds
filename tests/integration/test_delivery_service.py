from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from dds.reporting import (
    build_frozen_package,
    compute_package_hash,
    require_report_delivery_validation,
)
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
                "decision_question": "Can this evidence support the current decision?",
                "takeaway": "Evidence-bound",
                "decision_impact": "Use only within the stated evidence boundary.",
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


def _qualified_source(raw_hash: str, snapshot_hash: str) -> dict:
    return {
        "source_id": "SRC-1",
        "title": "Qualified fixture",
        "publisher": "Project owner",
        "author_type": "provided_input",
        "source_type": "provided_input",
        "published_at": "2026-07-21",
        "captured_at": "2026-07-22T00:00:00Z",
        "geography": {"city": "Test City"},
        "time_window": {"start": "2026-07-21", "end": "2026-07-21"},
        "rights_status": "internal_project_use",
        "duplicate_cluster": "fixture:delivery:1",
        "canonical_ref": "inbox/evidence.txt",
        "snapshot_ref": "source_snapshots/evidence.bin",
        "snapshot_hash": snapshot_hash,
        "raw_hash": raw_hash,
        "qualification_status": "qualified",
        "limitations": ["Only supports this delivery-gate fixture."],
    }


def _closed_claim(*, status: str = "qualified") -> dict:
    return {
        "claim_id": "claim-delivery-1",
        "statement": "The frozen source supports the fixture conclusion.",
        "status": status,
        "source_refs": ["SRC-1"],
        "counter_source_refs": [],
        "counter_evidence_status": "searched_none_found",
        "counter_evidence_note": "No contradictory fixture source was found.",
        "limitations": ["Not valid outside this delivery-gate fixture."],
        "decision_eligibility": False,
    }


def qualified_package(
    tmp_path: Path,
    *,
    qualification_status: str | None = "qualified",
    claim_status: str = "qualified",
) -> dict:
    evidence_root = tmp_path / "evidence-project"
    inbox = evidence_root / "inbox"
    snapshots = evidence_root / "work" / "source_snapshots"
    inbox.mkdir(parents=True)
    snapshots.mkdir(parents=True)
    raw = b"qualified delivery evidence"
    snapshot = b'{"source":"qualified delivery evidence"}'
    (inbox / "evidence.txt").write_bytes(raw)
    (snapshots / "evidence.bin").write_bytes(snapshot)

    seed = report_seed()
    source = _qualified_source(
        sha256(raw).hexdigest(),
        sha256(snapshot).hexdigest(),
    )
    if qualification_status is None:
        source.pop("qualification_status")
    else:
        source["qualification_status"] = qualification_status
    seed["source_registry"] = [source]
    seed["claims"] = [_closed_claim(status=claim_status)]
    package = build_frozen_package(
        seed,
        project_id="delivery-1",
        as_of="2026-07-22",
    )
    package["report_delivery_validation"] = require_report_delivery_validation(
        package,
        project_dir=evidence_root,
    )
    package["package_hash"] = compute_package_hash(package)
    return package


def layout_probe() -> dict:
    return {
        "exists": True,
        "overflow_x_px": 0,
        "overflow_y_px": 0,
        "hidden_overflow": [],
        "out_of_bounds": [],
        "passed": True,
    }


def browser_report(
    html_hash: str,
    report_document_hash: str = "1" * 64,
    *,
    runner: str = "playwright-python-sync-api",
) -> dict:
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
                "evidence": {
                    "page_checks": [
                        {
                            "passed": True,
                            "frame_within_viewport": True,
                            "body_horizontal_overflow_px": 0,
                            "html_horizontal_overflow_px": 0,
                            "frame_horizontal_overflow_px": 0,
                            "article_horizontal_overflow_px": 0,
                            "primary_visual": layout_probe(),
                            "decision_card": layout_probe(),
                            "rendered_images": [
                                {
                                    "complete": True,
                                    "natural_width": 1,
                                    "natural_height": 1,
                                    "decoded": True,
                                }
                            ],
                        }
                    ]
                },
                "screenshot": f"output/browser-qa-{width}x{height}.png",
            }
        )
    return {
        "schema_version": "dds.browser-qa/1.0",
        "runner": runner,
        "passed": True,
        "report_document_hash": "sha256:" + report_document_hash,
        "html_hash": "sha256:" + html_hash,
        "checks": dict(_BROWSER_CHECKS),
        "viewports": rows,
        "metrics": {"dependency_request_count": 0, "max_mounted_pages": 5},
        "dependency_requests": [],
        "artifact_directory": "output",
        "evidence": {
            "expected_page_count": 1,
            "geometry_tolerance_px": 1.5,
            "clipped_overflow_tolerance_px": 0,
        },
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
        "evidence": {
            "expected_page_count": 1,
            "prepared_page_count": 1,
            "geometry_tolerance_px": 1.5,
            "clipped_overflow_tolerance_px": 0,
            "layout": {
                "expected_page_count": 1,
                "dom_page_count": 1,
                "pages": [
                    {
                        "passed": True,
                        "article": layout_probe(),
                        "primary_visual": layout_probe(),
                        "decision_card": layout_probe(),
                    }
                ],
            },
        },
    }


def test_delivery_requires_official_qa_for_exact_html_and_writes_atomically(tmp_path):
    package = qualified_package(tmp_path)
    service = DeliveryService(tmp_path)
    html, html_hash, document_hash = service.render(package)
    browser_path = tmp_path / "browser_qa.json"
    print_path = tmp_path / "print_qa.json"
    browser_path.write_text(
        json.dumps(browser_report(html_hash, document_hash)),
        encoding="utf-8",
    )
    print_path.write_text(json.dumps(print_report(html_hash)), encoding="utf-8")

    qa = BrowserQAResult.from_reports(browser_path, print_path)

    assert qa.passed
    assert qa.html_hash == html_hash
    assert qa.report_document_hash == document_hash
    artifact = service.deliver(
        package, relative_path="delivery/report.html", browser_qa=qa
    )
    assert artifact.path.read_text(encoding="utf-8") == html
    assert artifact.html_hash == html_hash


def test_preview_render_remains_available_but_formal_delivery_requires_attestation(
    tmp_path,
):
    package = build_frozen_package(
        report_seed(), project_id="delivery-1", as_of="2026-07-22"
    )
    service = DeliveryService(tmp_path)
    html, html_hash, document_hash = service.render(package)
    qa = BrowserQAResult.from_reports(
        browser_report(html_hash, document_hash),
        print_report(html_hash),
    )

    assert html.startswith("<!doctype html>")
    with pytest.raises(DeliveryGateError, match="evidence_attestation_missing"):
        service.deliver(package, relative_path="delivery/report.html", browser_qa=qa)
    assert not (tmp_path / "delivery" / "report.html").exists()


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("qualification_missing", "source_qualification_missing:SRC-1"),
        ("source_hash_missing", "source_hash_missing:SRC-1"),
        ("claim_not_closed", "claim_not_closed:claim-delivery-1"),
    ],
)
def test_formal_delivery_rejects_unqualified_or_unclosed_evidence(
    tmp_path,
    case,
    expected_reason,
):
    package = qualified_package(
        tmp_path,
        qualification_status=None if case == "qualification_missing" else "qualified",
        claim_status="partial" if case == "claim_not_closed" else "qualified",
    )
    if case == "source_hash_missing":
        for collection in ("sources", "source_registry"):
            for field in (
                "raw_hash",
                "source_hash",
                "sha256",
                "content_hash",
                "snapshot_hash",
            ):
                package[collection][0].pop(field, None)
        package["package_hash"] = compute_package_hash(package)

    service = DeliveryService(tmp_path)
    _, html_hash, document_hash = service.render(package)
    qa = BrowserQAResult.from_reports(
        browser_report(html_hash, document_hash),
        print_report(html_hash),
    )

    with pytest.raises(DeliveryGateError, match=expected_reason):
        service.deliver(package, relative_path="delivery/report.html", browser_qa=qa)
    assert not (tmp_path / "delivery" / "report.html").exists()


def test_node_system_chrome_runner_is_allowed_but_unknown_runner_is_rejected():
    accepted = BrowserQAResult.from_reports(
        browser_report(
            "a" * 64,
            "b" * 64,
            runner="playwright-node-system-chrome",
        ),
        print_report("a" * 64),
    )
    rejected = BrowserQAResult.from_reports(
        browser_report("a" * 64, "b" * 64, runner="unregistered-runner"),
        print_report("a" * 64),
    )

    assert accepted.passed
    assert accepted.report_document_hash == "b" * 64
    assert not rejected.passed
    assert any("official Playwright runner" in error for error in rejected.validation_errors)


def test_legacy_custom_qa_schema_is_not_treated_as_canonical() -> None:
    browser = browser_report("a" * 64, "b" * 64)
    printed = print_report("a" * 64)
    browser["schema_version"] = "dds.liquid_glass.browser_qa/1.0"
    printed["schema_version"] = "dds.liquid_glass.print_qa/1.0"

    result = BrowserQAResult.from_reports(browser, printed)

    assert not result.passed
    assert sum(
        "schema_version is unsupported" in error
        for error in result.validation_errors
    ) == 2


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


def test_report_document_hash_mismatch_cannot_overwrite_old_report(tmp_path):
    package = build_frozen_package(
        report_seed(), project_id="delivery-1", as_of="2026-07-22"
    )
    service = DeliveryService(tmp_path)
    _, html_hash, _ = service.render(package)
    target = tmp_path / "stable.html"
    target.write_text("OLD", encoding="utf-8")
    qa = BrowserQAResult.from_reports(
        browser_report(html_hash, "0" * 64),
        print_report(html_hash),
    )

    with pytest.raises(DeliveryGateError, match="different ReportDocument"):
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
        "print_one_pixel_overflow",
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
    elif case == "print_one_pixel_overflow":
        printed["metrics"]["horizontal_overflow_px"] = 1
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


@pytest.mark.parametrize("surface", ["primary_visual", "decision_card"])
def test_from_reports_rejects_one_pixel_clipped_overflow_in_browser_evidence(surface):
    browser = browser_report("d" * 64)
    probe = browser["viewports"][0]["evidence"]["page_checks"][0][surface]
    probe["overflow_x_px"] = 1
    probe["hidden_overflow"] = [
        {"node": "div", "overflow_x_px": 1, "overflow_y_px": 0}
    ]

    result = BrowserQAResult.from_reports(browser, print_report("d" * 64))

    assert not result.passed
    assert any("clipped overflow" in error for error in result.validation_errors)


def test_from_reports_rejects_one_pixel_clipped_overflow_in_print_evidence():
    printed = print_report("e" * 64)
    probe = printed["evidence"]["layout"]["pages"][0]["primary_visual"]
    probe["overflow_y_px"] = 1
    probe["hidden_overflow"] = [
        {"node": "div", "overflow_x_px": 0, "overflow_y_px": 1}
    ]

    result = BrowserQAResult.from_reports(browser_report("e" * 64), printed)

    assert not result.passed
    assert any("clipped overflow" in error for error in result.validation_errors)


def test_partial_package_is_rejected_before_output(tmp_path):
    seed = deepcopy(report_seed())
    seed["project_panorama"] = {"required_units": ["SC1", "SC2"]}
    package = build_frozen_package(seed, project_id="partial", as_of="2026-07-22")
    with pytest.raises(DeliveryGateError, match="not delivery ready"):
        DeliveryService(tmp_path).render(package)
