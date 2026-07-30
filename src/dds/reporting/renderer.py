"""Manifest-driven, self-contained DDS v4 Liquid Glass report renderer.

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
    from .ui_recipe import load_decision_report_recipe
except ImportError:  # pragma: no cover - top-level V1 compatibility import
    from assets import AssetResolver
    from ui_recipe import load_decision_report_recipe

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
TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "templates"
    / "dds_report_liquid_glass_v4.html"
)


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
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
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
    document.setdefault("template_profile_version", "dds.liquid-glass-v4/1.0.0")
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
    """Compile adaptive report units without imposing a fixed-slide density."""
    manifest = compile_page_manifest(page_source)
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
    recipe = load_decision_report_recipe()
    recipe_profile = recipe["profile"]
    metadata = {
        "schema_version": str(document.get("schema_version") or "dds.report-document/1.0"),
        "template_id": str(document.get("template_id") or "dds-intelligence-report-v1"),
        "template_profile_version": str(
            document.get("template_profile_version")
            or "dds.liquid-glass-v4/1.0.0"
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
        "design_system": {
            "id": "gary-ui",
            "recipe": "decision-report",
            "recipe_version": recipe_profile["version"],
            "integration_mode": recipe["integration_mode"],
            "compiled_template_hash": recipe["compiled_template_hash"],
            "projection_hash": recipe["projection_hash"],
            "source": "gary-ui://patterns/recipes/decision-report",
        },
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
    return rendered


__all__ = [
    "PAGE_CHUNK_SIZE",
    "TEMPLATE_PATH",
    "compile_cinematic_manifest",
    "resolve_cinematic_document",
    "render_cinematic_deck_html",
]
