"""Manifest-driven, self-contained Apple Dark 16:9 DDS report renderer.

The Python side only serializes bounded page chunks.  Page DOM is created by
the embedded runtime on demand, so a 500-page report does not create 500 page
elements at export or first paint.
"""

from __future__ import annotations

import html
import hashlib
import json
import re
import base64
import io
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from .assets import AssetResolver
except ImportError:  # pragma: no cover - top-level V1 compatibility import
    from assets import AssetResolver

try:
    from .runtime_compat import inject_browser_qa_runtime
except ImportError:  # pragma: no cover - top-level V1 compatibility import
    from runtime_compat import inject_browser_qa_runtime

try:  # Existing app.py imports modules from ``scripts`` as top-level modules.
    from .report_document import (
        build_report_document,
        compile_page_manifest,
        framework_manifest,
    )
except ImportError:  # pragma: no cover - exercised by the Flask import style
    from report_document import (
        build_report_document,
        compile_page_manifest,
        framework_manifest,
    )


PAGE_CHUNK_SIZE = 24
# The manifest may reference many deduplicated visuals while the Web runtime
# still decodes at most 12 at once.  This is an export limit, not a live-DOM
# limit, so the sendable single-file report can safely carry the complete case
# evidence set.
# Portable reports must not silently drop the 33rd referenced image.  Runtime
# virtualization limits decoded media; it does not limit embedded asset count.
ASSET_EMBED_LIMIT: int | None = None
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "dds_report_apple_16x9.html"


_BROWSER_QA_LAYOUT_CSS = r"""
    /* DDS_BROWSER_QA_LAYOUT_V2
       Browser-verified desktop composition for compact summary charts,
       spatial pages and page-level frozen evidence visuals. */
    @media screen {
      .page-surface.is-summary .chart-block {
        min-width:0;
        overflow:hidden;
        grid-template-columns:minmax(0,1fr);
        grid-template-rows:auto minmax(0,1fr);
        align-items:stretch;
        gap:8px;
      }
      .page-surface.is-summary .chart-intro {
        min-width:0;
        align-self:auto;
        display:grid;
        grid-template-columns:minmax(0,1fr) auto;
        align-items:end;
        gap:4px 8px;
      }
      .page-surface.is-summary .chart-intro p { display:none; }
      .page-surface.is-summary .chart-unit { white-space:nowrap; }
      .page-surface.is-summary .chart-rows {
        min-width:0;
        align-content:center;
      }
      .page-surface.is-summary .chart-row {
        min-width:0;
        grid-template-columns:minmax(56px,.72fr) minmax(0,1.7fr) minmax(36px,.34fr);
        gap:7px;
      }
      .page-surface.is-summary .chart-note { display:none; }

      .page-content > .media-block {
        min-width:0;
        min-height:0;
        align-self:stretch;
        padding:0;
        overflow:hidden;
        border:1px solid rgba(255,255,255,.075);
        border-radius:16px;
        background:#0b0d0e;
        box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 18px 46px rgba(0,0,0,.22);
      }
      .page-content > .media-block .embedded-media {
        width:100%;
        height:100%;
        min-height:0;
        object-fit:contain;
        object-position:center;
        border-radius:15px;
        background:#0b0d0e;
      }

      .page-surface.is-summary:has(.page-content > .media-block) .page-content {
        grid-template-rows:minmax(0,1.08fr) minmax(0,.92fr);
        overflow:hidden;
      }
      .page-surface.is-summary:has(.page-content > .media-block) .summary-judgment {
        grid-column:1 / span 8;
        grid-row:1 / span 2;
      }
      .page-surface.is-summary:has(.page-content > .media-block) .page-content > .media-block {
        grid-column:9 / -1;
        grid-row:1;
      }
      .page-surface.is-summary.has-chart:has(.page-content > .media-block) .page-content > .chart-block,
      .page-surface.is-summary.has-chart:has(.page-content > .media-block) .page-content > .svg-chart-block {
        grid-column:9 / -1;
        grid-row:2;
      }
      .page-surface.is-summary:not(.has-chart):has(.page-content > .media-block) .page-content > .media-block {
        grid-row:1 / span 2;
      }
      .page-surface.is-summary.has-chart:has(.page-content > .media-block) .chart-intro p,
      .page-surface.is-summary.has-chart:has(.page-content > .media-block) .chart-unit {
        display:none;
      }

      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .page-content {
        grid-template-columns:minmax(250px,.42fr) minmax(0,.58fr);
        grid-template-rows:minmax(0,.58fr) minmax(100px,.42fr);
        align-items:stretch;
        overflow:hidden;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .page-content > .chart-block,
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .page-content > .svg-chart-block {
        grid-column:1;
        grid-row:1;
        min-width:0;
        min-height:0;
        overflow:hidden;
        grid-template-columns:minmax(0,1fr);
        grid-template-rows:auto minmax(0,1fr);
        align-items:stretch;
        gap:6px;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .page-content > .narrative-block {
        grid-column:1;
        grid-row:2;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .page-content > .media-block {
        grid-column:2;
        grid-row:1 / span 2;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .chart-intro {
        min-width:0;
        align-self:auto;
        display:grid;
        grid-template-columns:minmax(0,1fr) auto;
        align-items:end;
        gap:4px 8px;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .chart-intro p,
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .chart-unit {
        display:none;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .chart-rows,
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .spatial-chart-rows {
        min-width:0;
        align-content:center;
        padding:2px 0 0;
        gap:4px;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .chart-row {
        min-width:0;
        grid-template-columns:minmax(56px,.72fr) minmax(0,1.7fr) minmax(36px,.34fr);
        gap:7px;
      }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .chart-note { display:none; }
      .page-surface.has-chart:not(.is-summary):has(.page-content > .media-block) .spatial-chart-row {
        grid-template-columns:24px minmax(64px,.66fr) minmax(0,1fr) 34px;
        gap:5px;
      }

      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .page-content {
        grid-template-columns:minmax(250px,.38fr) minmax(0,.62fr);
        grid-template-rows:minmax(0,.58fr) minmax(100px,.42fr);
        align-items:stretch;
        overflow:hidden;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .page-content > .chart-spatial-diagram {
        grid-column:1;
        grid-row:1;
        min-width:0;
        min-height:0;
        overflow:hidden;
        grid-template-columns:minmax(0,1fr);
        grid-template-rows:auto minmax(0,1fr);
        align-items:stretch;
        gap:6px;
        padding:11px 12px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .page-content > .narrative-block {
        grid-column:1;
        grid-row:2;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .page-content > .spatial-diagram-block {
        grid-column:2;
        grid-row:1 / span 2;
        min-width:0;
        min-height:0;
        height:auto;
        overflow:hidden;
        grid-template-columns:minmax(0,1.7fr) minmax(180px,.6fr);
        gap:10px;
        padding:9px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .chart-intro {
        min-width:0;
        align-self:auto;
        display:grid;
        grid-template-columns:minmax(0,1fr) auto;
        align-items:end;
        gap:4px 8px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .chart-intro p,
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .chart-unit {
        display:none;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-chart-rows {
        min-width:0;
        align-content:center;
        padding:2px 0 0;
        gap:4px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-chart-row {
        grid-template-columns:24px minmax(64px,.66fr) minmax(0,1fr) 34px;
        gap:5px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-section-canvas {
        min-height:0;
        height:100%;
        align-content:center;
        gap:2px;
        padding:6px 8px;
        overflow:hidden;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .section-layer {
        min-height:0;
        grid-template-columns:92px 70px minmax(0,1fr);
        gap:6px;
        padding:5px 8px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-callout-rail {
        min-height:0;
        height:auto;
        overflow:hidden;
        gap:5px;
        padding:0;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-callout-rail header {
        padding:0 2px 5px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-callout {
        grid-template-columns:24px minmax(0,1fr);
        gap:6px;
        padding:7px;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-callout span {
        margin-top:1px;
        line-height:1.25;
        display:-webkit-box;
        -webkit-box-orient:vertical;
        -webkit-line-clamp:2;
        overflow:hidden;
      }
      .page-surface[data-visual-language="spatial_diagram"].has-chart:not(:has(.page-content > .media-block)) .spatial-boundary {
        padding:6px 7px;
        line-height:1.25;
      }
    }
"""

_RAW_PAGE_MARKER = re.compile(r"(?:raw[\s_-]*json|json[\s_-]*dump|原始\s*json)", re.I)
_SENSITIVE_KEY = re.compile(
    r"(?:secret|password|passwd|token|credential|security.?code|api.?key|amap.?js.?key)",
    re.I,
)
_OPAQUE_KEY = re.compile(r"^(?:raw|payload|personas_raw|records|raw_json)$", re.I)


def _safe_json(value: Any) -> str:
    """Serialize JSON safely inside an HTML ``script`` data block."""
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return (
        serialized.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _sanitized(value: Any, *, depth: int = 0) -> Any:
    """Keep audit metadata while dropping credentials and opaque raw dumps."""
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text) or _OPAQUE_KEY.search(key_text):
                continue
            clean[key_text] = _sanitized(item, depth=depth + 1)
        return clean
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitized(item, depth=depth + 1) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return deepcopy(value)
    return str(value)


def _contains_redaction_marker(value: Any, *, depth: int = 0) -> bool:
    if depth > 8:
        return False
    if value == "[redacted]":
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_redaction_marker(item, depth=depth + 1)
            for item in value.values()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(
            _contains_redaction_marker(item, depth=depth + 1)
            for item in value
        )
    return False


def _looks_raw_page(page: Mapping[str, Any]) -> bool:
    marker = " ".join(
        str(page.get(key) or "")
        for key in ("page_id", "chapter_id", "layout", "title")
    )
    if _RAW_PAGE_MARKER.search(marker):
        return True
    blocks = page.get("blocks")
    if isinstance(blocks, Sequence) and not isinstance(blocks, (str, bytes, bytearray)):
        block_types = [
            str(block.get("type") or block.get("layout") or "")
            for block in blocks
            if isinstance(block, Mapping)
        ]
        return bool(block_types) and all(_RAW_PAGE_MARKER.search(kind) for kind in block_types)
    return False


def _materialize_page_asset_blocks(
    manifest: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Turn otherwise invisible page asset references into main visuals.

    ``asset_refs`` are an evidence contract, not merely a packaging hint.  A
    page that cites a frozen image but has no block using it receives an
    idempotent media block.  The block is prepended so the runtime's six-block
    density cap cannot silently discard the evidence visual.
    """

    materialized: list[dict[str, Any]] = []
    for source_page in manifest:
        page = deepcopy(dict(source_page))
        blocks = [
            deepcopy(dict(block))
            for block in page.get("blocks") or []
            if isinstance(block, Mapping)
        ]
        explicit_refs: set[str] = set()
        for block in blocks:
            asset_ref = str(block.get("asset_ref") or "")
            if asset_ref:
                explicit_refs.add(asset_ref)
            nested_refs = block.get("asset_refs")
            if isinstance(nested_refs, Sequence) and not isinstance(
                nested_refs, (str, bytes, bytearray)
            ):
                explicit_refs.update(str(item) for item in nested_refs if str(item))
        missing_refs = [
            str(asset_id)
            for asset_id in page.get("asset_refs") or []
            if str(asset_id) and str(asset_id) not in explicit_refs
        ]
        implicit_media = [
            {
                "type": "media",
                "asset_ref": asset_id,
                "alt": f"{page.get('title') or '本页'} · 冻结证据图",
                "presentation_role": "page_asset_visual",
            }
            for asset_id in missing_refs
        ]
        page["blocks"] = implicit_media + blocks
        materialized.append(page)
    return materialized


def _fit_dense_mixed_pages(
    manifest: Sequence[Mapping[str, Any]], *, table_only_rows: int = 6,
) -> list[dict[str, Any]]:
    """Split tables to a 1280x720-safe density without local scrollbars."""
    fitted: list[dict[str, Any]] = []
    for source_page in manifest:
        page = deepcopy(dict(source_page))
        blocks = page.get("blocks")
        if not isinstance(blocks, Sequence) or isinstance(
            blocks, (str, bytes, bytearray)
        ):
            fitted.append(page)
            continue
        tables = [
            block
            for block in blocks
            if isinstance(block, Mapping) and str(block.get("type") or "") == "table"
        ]
        other_blocks = [
            block
            for block in blocks
            if not (isinstance(block, Mapping) and str(block.get("type") or "") == "table")
        ]
        max_rows = max(
            (
                len(block.get("rows") or [])
                for block in tables
                if isinstance(block.get("rows") or [], Sequence)
                and not isinstance(block.get("rows") or [], (str, bytes, bytearray))
            ),
            default=0,
        )
        if tables and other_blocks:
            narrative_page = deepcopy(page)
            narrative_page["blocks"] = deepcopy(other_blocks)
            fitted.append(narrative_page)
            offset = 0
            sequence = 1
            while offset < max_rows:
                split_page = deepcopy(page)
                split_page["blocks"] = []
                for table in tables:
                    table_copy = deepcopy(dict(table))
                    table_copy["rows"] = list(
                        table.get("rows") or []
                    )[offset : offset + table_only_rows]
                    if table_copy["rows"]:
                        split_page["blocks"].append(table_copy)
                split_page["page_id"] = (
                    f"{page.get('page_id')}-table-continuation-{sequence + 1}"
                )
                split_page["title"] = (
                    f"{page.get('title') or '证据矩阵'}（表续 {sequence + 1}）"
                )
                split_page["load_priority"] = "deferred"
                if split_page["blocks"]:
                    fitted.append(split_page)
                offset += table_only_rows
                sequence += 1
            continue
        first_limit = table_only_rows
        if max_rows <= first_limit:
            fitted.append(page)
            continue

        offset = 0
        sequence = 0
        while offset < max_rows:
            limit = first_limit if sequence == 0 else table_only_rows
            split_page = deepcopy(page)
            split_page["blocks"] = deepcopy(other_blocks) if sequence == 0 else []
            for table in tables:
                table_copy = deepcopy(dict(table))
                table_copy["rows"] = list(table.get("rows") or [])[offset : offset + limit]
                if table_copy["rows"] or (sequence == 0 and not table.get("rows")):
                    split_page["blocks"].append(table_copy)
            if sequence:
                split_page["page_id"] = (
                    f"{page.get('page_id')}-table-continuation-{sequence + 1}"
                )
                split_page["title"] = (
                    f"{page.get('title') or '证据矩阵'}（表续 {sequence + 1}）"
                )
                split_page["load_priority"] = "deferred"
            if split_page["blocks"]:
                fitted.append(split_page)
            offset += limit
            sequence += 1
    return fitted


def _project_from_legacy(report: Mapping[str, Any]) -> dict[str, Any]:
    parcel = report.get("parcel") if isinstance(report.get("parcel"), Mapping) else {}
    meta = report.get("meta") if isinstance(report.get("meta"), Mapping) else {}
    return {
        key: _sanitized(parcel.get(key, meta.get(key)))
        for key in ("city", "district", "address", "lng", "lat", "vision")
        if parcel.get(key, meta.get(key)) not in (None, "")
    }


def _embedded_image_data(path: Path) -> tuple[str, int | None, int | None]:
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1600, 1000), Image.Resampling.LANCZOS)
            width, height = image.size
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=76, method=4)
            if output.tell() > 1_500_000:
                output = io.BytesIO()
                image.save(output, format="WEBP", quality=64, method=4)
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            return f"data:image/webp;base64,{encoded}", width, height
    except Exception:
        return "", None, None


def _resolved_asset_registry(
    document: Mapping[str, Any],
    *,
    asset_resolver: AssetResolver | None = None,
) -> list[dict[str, Any]]:
    supplied = document.get("asset_registry")
    if not isinstance(supplied, Sequence) or isinstance(
        supplied, (str, bytes, bytearray)
    ):
        return []
    resolver = asset_resolver or AssetResolver()
    referenced: list[str] = []
    for page in document.get("page_manifest") or []:
        if not isinstance(page, Mapping):
            continue
        for asset_id in page.get("asset_refs") or []:
            marker = str(asset_id or "")
            if marker and marker not in referenced:
                referenced.append(marker)
    source_by_id = {
        str(entry.get("asset_id")): entry
        for entry in supplied
        if isinstance(entry, Mapping) and entry.get("asset_id")
    }
    ordered_ids = referenced + [
        asset_id for asset_id in source_by_id if asset_id not in referenced
    ]
    result: list[dict[str, Any]] = []
    embedded_count = 0
    payload_owner: dict[str, str] = {}
    for asset_id in ordered_ids:
        entry = source_by_id.get(asset_id) or {}
        data_uri = str(entry.get("data_uri") or "")
        width = entry.get("width")
        height = entry.get("height")
        existing_alias = str(entry.get("alias_of") or "")
        existing_hash = str(entry.get("content_hash") or "")
        if (
            not data_uri
            and existing_alias
            and existing_hash
            and str(entry.get("embed_status") or "") == "embedded"
        ):
            result.append(
                {
                    "asset_id": asset_id,
                    "mime": str(entry.get("mime") or ""),
                    "width": width,
                    "height": height,
                    "rights_status": str(
                        entry.get("rights_status") or "internal_reference"
                    ),
                    "embed_status": "embedded",
                    "content_hash": existing_hash,
                    "alias_of": existing_alias,
                    "data_uri": "",
                }
            )
            continue
        status = "embedded" if data_uri.startswith("data:image/") else "missing"
        if not data_uri and (
            ASSET_EMBED_LIMIT is None or embedded_count < ASSET_EMBED_LIMIT
        ):
            locator = next(
                (
                    str(entry.get(field) or "").strip()
                    for field in (
                        "data_or_object_ref",
                        "snapshot_ref",
                        "source_path",
                        "local_path",
                        "path",
                    )
                    if str(entry.get(field) or "").strip()
                ),
                "",
            )
            candidate = resolver.resolve(locator) if locator else None
            if candidate is not None:
                data_uri, width, height = _embedded_image_data(candidate)
                status = "embedded" if data_uri else status
        alias_of = ""
        content_hash = ""
        if data_uri:
            content_hash = hashlib.sha256(data_uri.encode("utf-8")).hexdigest()
            alias_of = payload_owner.get(content_hash, "")
            if alias_of:
                data_uri = ""
                status = "embedded"
            else:
                payload_owner[content_hash] = asset_id
                embedded_count += 1
        result.append(
            {
                "asset_id": asset_id,
                "mime": "image/webp" if data_uri.startswith("data:image/webp") else str(entry.get("mime") or ""),
                "width": width,
                "height": height,
                "rights_status": str(entry.get("rights_status") or "internal_reference"),
                "embed_status": status,
                "content_hash": content_hash,
                "alias_of": alias_of,
                "data_uri": data_uri,
            }
        )
    return result


def _resolve_document(report_json: Mapping[str, Any]) -> dict[str, Any]:
    supplied = report_json.get("report_document")
    if isinstance(supplied, Mapping):
        document = deepcopy(dict(supplied))
    elif isinstance(report_json.get("page_manifest"), Sequence) and not isinstance(
        report_json.get("page_manifest"), (str, bytes, bytearray)
    ):
        document = {
            "schema_version": report_json.get("schema_version")
            or "dds.report-document/1.0",
            "template_id": report_json.get("template_id")
            or "dds-intelligence-report-v1",
            "meta": _sanitized(report_json.get("meta") or {}),
            "project": _sanitized(
                report_json.get("project") or _project_from_legacy(report_json)
            ),
            "page_manifest": deepcopy(list(report_json["page_manifest"])),
            "source_registry": _sanitized(report_json.get("source_registry") or []),
            "asset_registry": _sanitized(report_json.get("asset_registry") or []),
            "qa": _sanitized(report_json.get("qa") or {}),
        }
    else:
        document = build_report_document(report_json)

    page_source = document.get("page_manifest") or []
    if isinstance(page_source, Mapping):
        page_source = [page_source]
    if not isinstance(page_source, Sequence) or isinstance(
        page_source, (str, bytes, bytearray)
    ):
        page_source = []
    page_source = [
        page
        for page in page_source
        if isinstance(page, Mapping) and not _looks_raw_page(page)
    ]
    manifest = _materialize_page_asset_blocks(
        compile_cinematic_manifest(page_source)
    )
    manifest = [page for page in manifest if not _looks_raw_page(page)]
    if not manifest:
        manifest = compile_page_manifest(
            [
                {
                    "page_id": "report-gap",
                    "chapter_id": "report",
                    "layout": "gap",
                    "title": "报告资料缺口",
                    "takeaway": "没有可编译页面；请补齐 ReportDocument 或基础报告数据。",
                    "blocks": [
                        {
                            "type": "gap",
                            "status": "missing",
                            "text": "PageManifest 为空。",
                            "required_action": "补齐证据模块后重新生成。",
                        }
                    ],
                }
            ]
        )

    document["page_manifest"] = manifest
    document.setdefault("schema_version", "dds.report-document/1.0")
    document.setdefault("template_id", "dds-intelligence-report-v1")
    document.setdefault("template_profile_version", "dds.apple-dark-16x9/1.1.0")
    document.setdefault("meta", {})
    document.setdefault("project", _project_from_legacy(report_json))
    document.setdefault("report_framework", framework_manifest())
    document.setdefault("source_registry", [])
    document.setdefault("asset_registry", [])
    document.setdefault("qa", {})
    return document


def compile_cinematic_manifest(
    page_source: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compile the exact desktop 16:9 page density budget, idempotently."""
    manifest = compile_page_manifest(page_source)
    manifest = compile_page_manifest(_fit_dense_mixed_pages(manifest))
    return [page for page in manifest if not _looks_raw_page(page)]


def resolve_cinematic_document(
    report_json: Mapping[str, Any],
    *,
    asset_resolver: AssetResolver | None = None,
) -> dict[str, Any]:
    """Return the exact, asset-materialized document consumed by the runtime."""
    if not isinstance(report_json, Mapping):
        raise TypeError("report_json must be a mapping")
    document = _resolve_document(report_json)
    document["asset_registry"] = _resolved_asset_registry(
        document, asset_resolver=asset_resolver
    )
    manifest = list(document.get("page_manifest") or [])
    qa = deepcopy(document.get("qa")) if isinstance(document.get("qa"), Mapping) else {}
    appendix_positions = [
        index
        for index, page in enumerate(manifest)
        if page.get("story_role") == "evidence_appendix"
    ]
    first_appendix = min(appendix_positions) if appendix_positions else len(manifest)
    table_rows = [
        len(block.get("rows") or [])
        for page in manifest
        for block in page.get("blocks") or []
        if isinstance(block, Mapping) and block.get("type") == "table"
    ]
    qa["page_count"] = len(manifest)
    qa["presentation_page_count"] = first_appendix
    qa["evidence_appendix_page_count"] = len(appendix_positions)
    qa["max_table_rows"] = max(table_rows, default=0)
    checks = dict(qa.get("checks") or {})
    page_ids = [str(page.get("page_id") or "") for page in manifest]
    referenced_assets = sorted(
        {
            str(asset_id)
            for page in manifest
            if isinstance(page, Mapping)
            for asset_id in page.get("asset_refs") or []
            if str(asset_id)
        }
    )
    resolved_assets = {
        str(entry.get("asset_id")): entry
        for entry in document.get("asset_registry") or []
        if isinstance(entry, Mapping) and entry.get("asset_id")
    }
    missing_assets = [
        asset_id
        for asset_id in referenced_assets
        if str((resolved_assets.get(asset_id) or {}).get("embed_status") or "")
        != "embedded"
    ]
    qa["referenced_asset_count"] = len(referenced_assets)
    qa["missing_asset_ids"] = missing_assets
    checks["page_ids_unique"] = len(page_ids) == len(set(page_ids))
    checks["page_count_matches_manifest"] = qa["page_count"] == len(manifest)
    checks["referenced_assets_resolved"] = not missing_assets
    checks["portable_assets_embedded"] = not missing_assets
    qa["checks"] = checks
    qa["passed"] = all(checks.values()) if checks else True
    document["qa"] = qa
    return document


def _legacy_headline(report_json: Mapping[str, Any], document: Mapping[str, Any]) -> str:
    decision = report_json.get("decision")
    if isinstance(decision, Mapping):
        for key in ("summary", "headline", "recommendation"):
            value = decision.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:360]
    project = document.get("project")
    if isinstance(project, Mapping):
        for key in ("headline", "brief", "vision"):
            value = project.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:360]
    manifest = document.get("page_manifest") or []
    if manifest and isinstance(manifest[0], Mapping):
        return str(manifest[0].get("takeaway") or "")[:360]
    return "先核验证据边界，再进入投资决策。"


def _page_index(manifest: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for offset, page in enumerate(manifest):
        confidence = page.get("confidence")
        score = confidence.get("score") if isinstance(confidence, Mapping) else confidence
        index.append(
            {
                "index": offset,
                "page_id": str(page.get("page_id") or f"page-{offset + 1}"),
                "chapter_id": str(page.get("chapter_id") or "report"),
                "source_chapter_id": str(
                    page.get("source_chapter_id") or page.get("chapter_id") or "report"
                ),
                "unit_id": str(page.get("unit_id") or page.get("section_id") or "CS"),
                "group_id": str(page.get("group_id") or page.get("section_group") or "CS"),
                "section_id": str(page.get("section_id") or "CS"),
                "section_group": str(page.get("section_group") or "CS"),
                "section_group_title": str(
                    page.get("section_group_title") or "Confidence State"
                ),
                "section_group_label": str(
                    page.get("section_group_label") or "证据与置信状态"
                ),
                "section_title": str(page.get("section_title") or "来源、方法与置信状态"),
                "display_code": str(
                    page.get("display_code") or page.get("page_code") or f"CS·{offset + 1:02d}"
                ),
                "page_code": str(
                    page.get("page_code") or page.get("display_code") or f"CS·{offset + 1:02d}"
                ),
                "title": str(page.get("title") or f"报告页 {offset + 1}"),
                "takeaway": str(page.get("takeaway") or ""),
                "source_refs": _sanitized(page.get("source_refs") or []),
                "evidence_type": str(page.get("evidence_type") or "analysis_inference"),
                "confidence": score,
                "story_role": str(page.get("story_role") or "primary_narrative"),
                "appendix_policy": str(page.get("appendix_policy") or "presentation"),
                "visual_evidence": str(page.get("visual_evidence") or "text"),
                "visual_language": str(
                    page.get("visual_language") or "decision_narrative"
                ),
                "chunk": offset // PAGE_CHUNK_SIZE,
                "offset": offset % PAGE_CHUNK_SIZE,
            }
        )
    return index


def _chapter_index(page_index: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for page in page_index:
        chapter_id = str(page["chapter_id"])
        position = positions.get(chapter_id)
        if position is not None:
            chapters[position]["count"] += 1
            continue
        positions[chapter_id] = len(chapters)
        chapters.append(
            {
                "chapter_id": chapter_id,
                "title": str(page["title"]),
                "start_index": int(page["index"]),
                "count": 1,
            }
        )
    return chapters


def _section_index(page_index: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build the visible SC / AD / VA / CS directory units."""
    sections: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for page in page_index:
        section_id = str(page.get("unit_id") or page.get("section_id") or "CS")
        position = positions.get(section_id)
        if position is None:
            positions[section_id] = len(sections)
            sections.append(
                {
                    "section_id": section_id,
                    "unit_id": section_id,
                    "group_id": str(page.get("group_id") or page.get("section_group") or "CS"),
                    "section_group": str(page.get("section_group") or "CS"),
                    "section_group_title": str(
                        page.get("section_group_title") or "Confidence State"
                    ),
                    "section_group_label": str(
                        page.get("section_group_label") or "证据与置信状态"
                    ),
                    "title": str(page.get("section_title") or page.get("title") or section_id),
                    "start_index": int(page["index"]),
                    "count": 1,
                }
            )
            continue
        sections[position]["count"] += 1
    return sections


def _legacy_external_media_markers(report_json: Mapping[str, Any]) -> str:
    """Remote thumbnails are not evidence and are never emitted."""
    return ""


def _title(document: Mapping[str, Any]) -> str:
    project = document.get("project")
    if not isinstance(project, Mapping):
        return "DDS 全域研判报告"
    city = str(project.get("city") or "DDS")
    address = str(project.get("address") or project.get("name") or "全域研判报告")
    return f"{city} · {address}"


def render_cinematic_deck_html(
    report_json: dict,
    *,
    asset_resolver: AssetResolver | None = None,
) -> str:
    """Render a dependency-free HTML shell around a virtual PageManifest."""
    if not isinstance(report_json, Mapping):
        raise TypeError("report_json must be a mapping")
    document = resolve_cinematic_document(
        report_json, asset_resolver=asset_resolver
    )
    manifest = list(document["page_manifest"])
    index = _page_index(manifest)
    chapters = _chapter_index(index)
    sections = _section_index(index)
    metadata = {
        "schema_version": str(document.get("schema_version") or "dds.report-document/1.0"),
        "template_id": str(document.get("template_id") or "dds-intelligence-report-v1"),
        "template_profile_version": str(
            document.get("template_profile_version")
            or "dds.apple-dark-16x9/1.1.0"
        ),
        "meta": _sanitized(document.get("meta") or {}),
        "project": _sanitized(document.get("project") or {}),
        "qa": _sanitized(document.get("qa") or {}),
        "page_count": len(manifest),
        "page_index": index,
        "chapter_index": chapters,
        "section_index": sections,
        "report_framework": _sanitized(
            document.get("report_framework") or framework_manifest()
        ),
        "source_registry": _sanitized(document.get("source_registry") or []),
        "asset_registry": _resolved_asset_registry(
            document, asset_resolver=asset_resolver
        ),
        "headline": _legacy_headline(report_json, document),
    }
    if _contains_redaction_marker(report_json):
        metadata["meta"] = dict(metadata["meta"])
        metadata["meta"]["redaction_status"] = "[redacted]"
    chunks: list[str] = []
    for chunk_index, start in enumerate(range(0, len(manifest), PAGE_CHUNK_SIZE)):
        chunk = manifest[start : start + PAGE_CHUNK_SIZE]
        chunks.append(
            '<script type="application/json" '
            f'data-page-chunk="{chunk_index}" data-start="{start}" '
            f'data-end="{start + len(chunk) - 1}">{_safe_json(chunk)}</script>'
        )

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        "__DOCUMENT_TITLE__": html.escape(_title(document), quote=True),
        "__REPORT_METADATA__": _safe_json(metadata),
        "__PAGE_CHUNKS__": "\n".join(chunks),
        "__PAGE_CHUNK_SIZE__": str(PAGE_CHUNK_SIZE),
        "__LEGACY_MEDIA_MARKERS__": _legacy_external_media_markers(report_json),
    }
    rendered = template
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    rendered = rendered.replace(
        "</style>", f"{_BROWSER_QA_LAYOUT_CSS}\n  </style>", 1
    )
    return inject_browser_qa_runtime(rendered)


__all__ = [
    "PAGE_CHUNK_SIZE",
    "TEMPLATE_PATH",
    "compile_cinematic_manifest",
    "resolve_cinematic_document",
    "render_cinematic_deck_html",
]
