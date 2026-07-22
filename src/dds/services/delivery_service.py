"""Atomic, QA-bound delivery of self-contained V4 HTML reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from dds.reporting import (
    compile_frozen_package,
    compute_report_document_hash,
    evaluate_delivery_status,
    render_frozen_package,
)


class DeliveryGateError(RuntimeError):
    pass


_BROWSER_QA_SCHEMA = "dds.browser-qa/1.0"
_PRINT_QA_SCHEMA = "dds.print-browser-qa/1.0"
_BROWSER_QA_RUNNER = "playwright-python-sync-api"
_REQUIRED_VIEWPORTS = ((1280, 720), (1440, 900), (1920, 1080))
_BROWSER_CHECKS = (
    "console_error_free",
    "first_to_last_scroll",
    "mounted_pages_bounded",
    "native_scrollbar_hidden",
    "offline_runtime",
    "presentation_navigation",
    "right_black_rail_absent",
    "source_drawer_union",
    "top_navigation",
    "visuals_readable",
)
_PRINT_CHECKS = (
    "console_error_free",
    "horizontal_overflow_absent",
    "images_exist_and_decode",
    "offline_runtime",
    "pages_exist",
    "print_media_active",
)
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _load_report(
    value: Mapping[str, Any] | str | Path,
    *,
    label: str,
    errors: list[str],
) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        loaded = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"{label} could not be loaded: {exc}")
        return {}
    if not isinstance(loaded, Mapping):
        errors.append(f"{label} must contain a JSON object")
        return {}
    return loaded


def _mapping_field(
    payload: Mapping[str, Any], key: str, *, label: str, errors: list[str]
) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        errors.append(f"{label}.{key} must be an object")
        return {}
    return value


def _list_field(
    payload: Mapping[str, Any], key: str, *, label: str, errors: list[str]
) -> tuple[str, ...]:
    if key not in payload:
        errors.append(f"{label}.{key} is required")
        return ()
    value = payload[key]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        errors.append(f"{label}.{key} must be an array")
        return ()
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{label}.{key}[{index}] must be a string")
            result.append(repr(item))
        else:
            result.append(item)
    return tuple(result)


def _normalized_hash(value: Any, *, label: str, errors: list[str]) -> str:
    if not isinstance(value, str):
        errors.append(f"{label} must be a SHA-256 string")
        return ""
    raw = value[7:] if value.startswith("sha256:") else value
    if not _SHA256.fullmatch(raw):
        errors.append(f"{label} must be sha256:<64 hex> or 64 hex")
        return ""
    return raw.lower()


def _require_true_checks(
    payload: Mapping[str, Any],
    names: Sequence[str],
    *,
    label: str,
    errors: list[str],
) -> None:
    checks = _mapping_field(payload, "checks", label=label, errors=errors)
    for name in names:
        if checks.get(name) is not True:
            errors.append(f"{label}.checks.{name} must be true")


def _bounded_overflow(
    metrics: Mapping[str, Any], *, label: str, errors: list[str]
) -> bool:
    value = metrics.get("horizontal_overflow_px")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{label}.horizontal_overflow_px must be numeric")
        return False
    if not math.isfinite(float(value)) or float(value) != 0:
        errors.append(f"{label}.horizontal_overflow_px must be exactly 0px")
        return False
    return True


def _nonnegative_int(
    metrics: Mapping[str, Any], key: str, *, label: str, errors: list[str]
) -> int | None:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{label}.{key} must be a non-negative integer")
        return None
    return value


@dataclass(frozen=True, slots=True)
class BrowserQAResult:
    html_hash: str
    viewports_checked: tuple[str, ...]
    console_errors: tuple[str, ...] = ()
    page_errors: tuple[str, ...] = ()
    dependency_requests: tuple[str, ...] = ()
    horizontal_overflow_viewports: tuple[str, ...] = ()
    missing_images: tuple[str, ...] = ()
    print_layout_passed: bool = False
    validation_errors: tuple[str, ...] = (
        "browser and print QA reports have not been validated",
    )

    @classmethod
    def from_reports(
        cls,
        browser_report: Mapping[str, Any] | str | Path,
        print_report: Mapping[str, Any] | str | Path,
    ) -> BrowserQAResult:
        """Strictly validate official arch-front browser and print reports."""

        validation_errors: list[str] = []
        browser = _load_report(
            browser_report, label="browser_report", errors=validation_errors
        )
        printed = _load_report(
            print_report, label="print_report", errors=validation_errors
        )

        if browser.get("schema_version") != _BROWSER_QA_SCHEMA:
            validation_errors.append("browser_report.schema_version is unsupported")
        if browser.get("runner") != _BROWSER_QA_RUNNER:
            validation_errors.append(
                "browser_report.runner is not the official Playwright runner"
            )
        if browser.get("passed") is not True:
            validation_errors.append("browser_report.passed must be true")
        browser_hash = _normalized_hash(
            browser.get("html_hash"),
            label="browser_report.html_hash",
            errors=validation_errors,
        )
        _normalized_hash(
            browser.get("report_document_hash"),
            label="browser_report.report_document_hash",
            errors=validation_errors,
        )
        _require_true_checks(
            browser,
            _BROWSER_CHECKS,
            label="browser_report",
            errors=validation_errors,
        )
        browser_dependencies = _list_field(
            browser,
            "dependency_requests",
            label="browser_report",
            errors=validation_errors,
        )
        if browser_dependencies:
            validation_errors.append(
                "browser_report contains external dependency requests"
            )
        browser_metrics = _mapping_field(
            browser, "metrics", label="browser_report", errors=validation_errors
        )
        dependency_count = _nonnegative_int(
            browser_metrics,
            "dependency_request_count",
            label="browser_report.metrics",
            errors=validation_errors,
        )
        if dependency_count != 0:
            validation_errors.append(
                "browser_report.metrics.dependency_request_count must be zero"
            )

        console_errors: list[str] = []
        page_errors: list[str] = []
        dependency_requests: list[str] = list(browser_dependencies)
        overflow_viewports: list[str] = []
        checked: list[tuple[int, int]] = []
        rows = browser.get("viewports")
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            validation_errors.append("browser_report.viewports must be an array")
            rows = ()
        for index, row_value in enumerate(rows):
            label = f"browser_report.viewports[{index}]"
            if not isinstance(row_value, Mapping):
                validation_errors.append(f"{label} must be an object")
                continue
            row = row_value
            width = row.get("width")
            height = row.get("height")
            if (
                isinstance(width, bool)
                or isinstance(height, bool)
                or not isinstance(width, int)
                or not isinstance(height, int)
            ):
                validation_errors.append(f"{label} width and height must be integers")
                viewport_name = f"invalid-{index}"
            else:
                checked.append((width, height))
                viewport_name = f"{width}x{height}"
            if row.get("passed") is not True:
                validation_errors.append(f"{label}.passed must be true")
            _require_true_checks(
                row, _BROWSER_CHECKS, label=label, errors=validation_errors
            )
            row_console = _list_field(
                row, "console_errors", label=label, errors=validation_errors
            )
            row_page = _list_field(
                row, "page_errors", label=label, errors=validation_errors
            )
            row_dependencies = _list_field(
                row, "dependency_requests", label=label, errors=validation_errors
            )
            console_errors.extend(f"{viewport_name}: {item}" for item in row_console)
            page_errors.extend(f"{viewport_name}: {item}" for item in row_page)
            dependency_requests.extend(row_dependencies)
            if row_console or row_page:
                validation_errors.append(f"{label} contains browser errors")
            if row_dependencies:
                validation_errors.append(
                    f"{label} contains external dependency requests"
                )
            row_metrics = _mapping_field(
                row, "metrics", label=label, errors=validation_errors
            )
            if not _bounded_overflow(
                row_metrics,
                label=f"{label}.metrics",
                errors=validation_errors,
            ):
                overflow_viewports.append(viewport_name)
            if row_metrics.get("runtime_ready") is not True:
                validation_errors.append(f"{label}.metrics.runtime_ready must be true")
            if row_metrics.get("runtime_error_visible") is not False:
                validation_errors.append(
                    f"{label}.metrics.runtime_error_visible must be false"
                )

        if len(rows) != len(_REQUIRED_VIEWPORTS) or set(checked) != set(
            _REQUIRED_VIEWPORTS
        ):
            validation_errors.append(
                "browser_report must contain exactly 1280x720, 1440x900, and 1920x1080"
            )
        if len(checked) != len(set(checked)):
            validation_errors.append("browser_report contains duplicate viewports")

        if printed.get("schema_version") != _PRINT_QA_SCHEMA:
            validation_errors.append("print_report.schema_version is unsupported")
        if printed.get("passed") is not True:
            validation_errors.append("print_report.passed must be true")
        print_hash = _normalized_hash(
            printed.get("html_hash"),
            label="print_report.html_hash",
            errors=validation_errors,
        )
        if not browser_hash or not print_hash or browser_hash != print_hash:
            validation_errors.append("browser and print HTML hashes do not match")
        _require_true_checks(
            printed,
            _PRINT_CHECKS,
            label="print_report",
            errors=validation_errors,
        )
        print_console = _list_field(
            printed, "console_errors", label="print_report", errors=validation_errors
        )
        print_page = _list_field(
            printed, "page_errors", label="print_report", errors=validation_errors
        )
        print_dependencies = _list_field(
            printed,
            "dependency_requests",
            label="print_report",
            errors=validation_errors,
        )
        console_errors.extend(f"print: {item}" for item in print_console)
        page_errors.extend(f"print: {item}" for item in print_page)
        dependency_requests.extend(print_dependencies)
        if print_console or print_page:
            validation_errors.append("print_report contains browser errors")
        if print_dependencies:
            validation_errors.append(
                "print_report contains external dependency requests"
            )

        print_metrics = _mapping_field(
            printed, "metrics", label="print_report", errors=validation_errors
        )
        if not _bounded_overflow(
            print_metrics,
            label="print_report.metrics",
            errors=validation_errors,
        ):
            overflow_viewports.append("print")
        if print_metrics.get("media_print") is not True:
            validation_errors.append("print_report.metrics.media_print must be true")
        images = _nonnegative_int(
            print_metrics,
            "images",
            label="print_report.metrics",
            errors=validation_errors,
        )
        decoded_images = _nonnegative_int(
            print_metrics,
            "decoded_images",
            label="print_report.metrics",
            errors=validation_errors,
        )
        missing_images: tuple[str, ...] = ()
        if images is None or decoded_images is None or decoded_images != images:
            validation_errors.append(
                "print_report image decode count does not match image count"
            )
            missing_images = ("print image decode mismatch",)
        for metric in ("prepared_pages", "surfaces", "visible_pages"):
            count = _nonnegative_int(
                print_metrics,
                metric,
                label="print_report.metrics",
                errors=validation_errors,
            )
            if count is not None and count <= 0:
                validation_errors.append(
                    f"print_report.metrics.{metric} must be positive"
                )
        print_viewport = _mapping_field(
            printed, "viewport", label="print_report", errors=validation_errors
        )
        if print_viewport.get("width") != 1920 or print_viewport.get("height") != 1080:
            validation_errors.append("print_report.viewport must be exactly 1920x1080")

        ordered_viewports = tuple(
            f"{width}x{height}"
            for width, height in _REQUIRED_VIEWPORTS
            if (width, height) in checked
        )
        return cls(
            html_hash=browser_hash,
            viewports_checked=ordered_viewports,
            console_errors=tuple(console_errors),
            page_errors=tuple(page_errors),
            dependency_requests=tuple(dict.fromkeys(dependency_requests)),
            horizontal_overflow_viewports=tuple(overflow_viewports),
            missing_images=missing_images,
            print_layout_passed=printed.get("passed") is True,
            validation_errors=tuple(validation_errors),
        )

    @property
    def passed(self) -> bool:
        return (
            self.viewports_checked
            == tuple(f"{width}x{height}" for width, height in _REQUIRED_VIEWPORTS)
            and not self.console_errors
            and not self.page_errors
            and not self.dependency_requests
            and not self.horizontal_overflow_viewports
            and not self.missing_images
            and self.print_layout_passed
            and not self.validation_errors
        )


@dataclass(frozen=True, slots=True)
class DeliveryArtifact:
    path: Path
    html_hash: str
    report_document_hash: str
    byte_size: int


_EXTERNAL_RUNTIME = re.compile(
    r"<(?:script|img)\b[^>]*\bsrc\s*=\s*['\"]https?://|"
    r"<link\b[^>]*\bhref\s*=\s*['\"]https?://",
    re.IGNORECASE,
)


class DeliveryService:
    """Compile offline, bind browser QA to bytes, then atomically publish."""

    def __init__(self, exports_root: Path) -> None:
        self.exports_root = exports_root.resolve()

    @staticmethod
    def render(package: Mapping[str, Any]) -> tuple[str, str, str]:
        document = compile_frozen_package(package)
        gate = evaluate_delivery_status(document)
        if not gate["ready"]:
            raise DeliveryGateError(
                "ReportDocument is not delivery ready: "
                + ", ".join(gate["reason_codes"])
            )
        html = render_frozen_package(package)
        if _EXTERNAL_RUNTIME.search(html):
            raise DeliveryGateError(
                "rendered HTML contains an external runtime dependency"
            )
        html_hash = sha256(html.encode("utf-8")).hexdigest()
        return html, html_hash, compute_report_document_hash(document)

    def deliver(
        self,
        package: Mapping[str, Any],
        *,
        relative_path: str | Path,
        browser_qa: BrowserQAResult,
    ) -> DeliveryArtifact:
        html, html_hash, document_hash = self.render(package)
        if not browser_qa.passed:
            raise DeliveryGateError(
                "exact three-viewport and print browser QA has not passed"
            )
        if browser_qa.html_hash != html_hash:
            raise DeliveryGateError(
                "browser QA belongs to different rendered HTML bytes"
            )

        relative = Path(relative_path)
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise DeliveryGateError("delivery path must remain under exports_root")
        if relative.suffix.lower() != ".html":
            raise DeliveryGateError(
                "first delivery format must be a single HTML file"
            )
        target = (self.exports_root / relative).resolve()
        try:
            target.relative_to(self.exports_root)
        except ValueError as exc:
            raise DeliveryGateError("delivery path escapes exports_root") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = html.encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return DeliveryArtifact(
            path=target,
            html_hash=html_hash,
            report_document_hash=document_hash,
            byte_size=len(payload),
        )


__all__ = [
    "BrowserQAResult",
    "DeliveryArtifact",
    "DeliveryGateError",
    "DeliveryService",
]


