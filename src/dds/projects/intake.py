"""Hash-bound, read-only migration of real V1 project materials."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import mimetypes
from pathlib import Path
import re
import shutil
from typing import Any, Iterable
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


SUPPORTED_SUFFIXES = {
    ".csv", ".docx", ".dwg", ".dxf", ".html", ".jpeg", ".jpg", ".json",
    ".md", ".mov", ".mp4", ".pdf", ".png", ".pptx", ".txt", ".webp", ".xlsx",
}


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_nodes(raw: bytes) -> str:
    root = ElementTree.fromstring(raw)
    return " ".join(
        text.strip()
        for node in root.iter()
        for text in [node.text or ""]
        if text.strip()
    )


@dataclass(frozen=True, slots=True)
class ExtractionLocator:
    locator: str
    text: str = ""
    extraction_method: str = ""
    review_status: str = "machine_extracted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceAsset:
    source_id: str
    original_name: str
    relative_source_path: str
    snapshot_ref: str
    sha256: str
    bytes: int
    media_type: str
    source_role: str
    rights_status: str
    extraction_status: str
    locators: tuple[ExtractionLocator, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["locators"] = [item.to_dict() for item in self.locators]
        return payload


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    project_id: str
    project_name: str
    source_root_hash: str
    imported_at: str
    source_assets: tuple[SourceAsset, ...]
    source_system: str = "DDS_V1_READ_ONLY"
    schema_version: str = "dds.project-manifest/2.0"
    limitations: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_assets"] = [item.to_dict() for item in self.source_assets]
        return payload


def _extract_office(path: Path) -> tuple[ExtractionLocator, ...]:
    suffix = path.suffix.lower()
    try:
        with ZipFile(path) as archive:
            if suffix == ".docx":
                return (
                    ExtractionLocator(
                        locator="document:body",
                        text=_text_nodes(archive.read("word/document.xml")),
                        extraction_method="openxml",
                    ),
                )
            if suffix == ".pptx":
                slides = sorted(
                    (
                        name for name in archive.namelist()
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                    ),
                    key=lambda name: int(re.search(r"\d+", name).group()),
                )
                return tuple(
                    ExtractionLocator(
                        locator=f"slide:{index}",
                        text=_text_nodes(archive.read(name)),
                        extraction_method="openxml",
                    )
                    for index, name in enumerate(slides, start=1)
                )
            if suffix == ".xlsx":
                shared: list[str] = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    shared_root = ElementTree.fromstring(
                        archive.read("xl/sharedStrings.xml")
                    )
                    shared = [
                        "".join(node.itertext()).strip()
                        for node in shared_root
                    ]
                sheets = sorted(
                    name for name in archive.namelist()
                    if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
                )
                locators: list[ExtractionLocator] = []
                for sheet_index, name in enumerate(sheets, start=1):
                    root = ElementTree.fromstring(archive.read(name))
                    for cell in root.iter():
                        if not cell.tag.endswith("}c"):
                            continue
                        ref = cell.attrib.get("r", "")
                        value_node = next(
                            (child for child in cell if child.tag.endswith("}v")),
                            None,
                        )
                        formula_node = next(
                            (child for child in cell if child.tag.endswith("}f")),
                            None,
                        )
                        value = value_node.text if value_node is not None else ""
                        if cell.attrib.get("t") == "s" and str(value).isdigit():
                            index = int(str(value))
                            value = shared[index] if index < len(shared) else value
                        text = str(value or "")
                        if formula_node is not None and formula_node.text:
                            text = f"={formula_node.text} → {text}"
                        if text:
                            locators.append(
                                ExtractionLocator(
                                    locator=f"sheet:{sheet_index}!{ref}",
                                    text=text,
                                    extraction_method="openxml",
                                )
                            )
                return tuple(locators)
    except (BadZipFile, KeyError, ElementTree.ParseError):
        return ()
    return ()


def _extract(
    path: Path,
) -> tuple[str, tuple[ExtractionLocator, ...], tuple[str, ...]]:
    suffix = path.suffix.lower()
    if suffix in {".docx", ".pptx", ".xlsx"}:
        locators = _extract_office(path)
        return (
            "extracted" if locators else "needs_review",
            locators,
            () if locators else ("OpenXML extraction failed.",),
        )
    if suffix == ".pdf":
        page_count = max(
            1, len(re.findall(rb"/Type\s*/Page\b", path.read_bytes()))
        )
        return (
            "needs_ocr",
            tuple(
                ExtractionLocator(
                    locator=f"page:{page}",
                    extraction_method="page_inventory",
                    review_status="ocr_required",
                )
                for page in range(1, page_count + 1)
            ),
            ("PDF text and tables require OCR or a dedicated parser.",),
        )
    if suffix in {".md", ".txt", ".csv", ".json", ".html"}:
        return (
            "extracted",
            (
                ExtractionLocator(
                    locator="document:body",
                    text=path.read_text(encoding="utf-8", errors="replace"),
                    extraction_method="utf8_text",
                ),
            ),
            (),
        )
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return (
            "needs_ocr",
            (
                ExtractionLocator(
                    locator="image:1",
                    extraction_method="image_inventory",
                    review_status="ocr_and_visual_review_required",
                ),
            ),
            ("Image facts require OCR and visual review.",),
        )
    if suffix in {".mp4", ".mov"}:
        return (
            "needs_transcription",
            (
                ExtractionLocator(
                    locator="media:timecode_required",
                    extraction_method="media_inventory",
                    review_status="transcription_and_frame_review_required",
                ),
            ),
            ("Meeting claims require timecodes and human review.",),
        )
    if suffix == ".dwg":
        return (
            "registered_only",
            (),
            ("DWG requires a PDF or DXF derivative for auditable geometry.",),
        )
    if suffix == ".dxf":
        return (
            "needs_geometry_parser",
            (),
            ("DXF is registered but precise geometry extraction is pending.",),
        )
    return "unsupported", (), ("Unsupported material format.",)


class V1ProjectImporter:
    """Copy selected V1 materials into a self-contained V2 snapshot."""

    def import_project(
        self,
        source_root: Path,
        target_root: Path,
        *,
        project_id: str,
        project_name: str,
        imported_at: str,
        include_paths: Iterable[Path] | None = None,
    ) -> ProjectManifest:
        source = source_root.resolve()
        if not source.is_dir():
            raise FileNotFoundError(source)
        target = target_root.resolve()
        target.mkdir(parents=True, exist_ok=True)
        candidates = (
            tuple(path.resolve() for path in include_paths)
            if include_paths is not None
            else tuple(path for path in source.rglob("*") if path.is_file())
        )
        assets: list[SourceAsset] = []
        for path in sorted(candidates):
            try:
                relative = path.relative_to(source)
            except ValueError as exc:
                raise ValueError(
                    f"migration path escapes V1 project root: {path}"
                ) from exc
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            digest = _digest(path)
            snapshot_name = f"{digest[:16]}-{path.name}"
            snapshot = target / "assets" / snapshot_name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if snapshot.exists():
                if _digest(snapshot) != digest:
                    raise ValueError(
                        f"snapshot hash conflict: {snapshot_name}"
                    )
            else:
                shutil.copy2(path, snapshot)
            status, locators, limitations = _extract(snapshot)
            assets.append(
                SourceAsset(
                    source_id=f"SRC-{digest[:20].upper()}",
                    original_name=path.name,
                    relative_source_path=relative.as_posix(),
                    snapshot_ref=f"assets/{snapshot_name}",
                    sha256=digest,
                    bytes=path.stat().st_size,
                    media_type=(
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream"
                    ),
                    source_role="client_provided_material",
                    rights_status="internal_project_use",
                    extraction_status=status,
                    locators=locators,
                    limitations=limitations,
                )
            )
        root_hash = sha256(
            "\n".join(
                f"{item.relative_source_path}:{item.sha256}" for item in assets
            ).encode("utf-8")
        ).hexdigest()
        manifest = ProjectManifest(
            project_id=project_id,
            project_name=project_name,
            source_root_hash=root_hash,
            imported_at=imported_at,
            source_assets=tuple(assets),
            limitations=(
                "V1 source directory was read-only; V2 runs use copied snapshots.",
            ),
        )
        (target / "project_manifest.json").write_text(
            json.dumps(
                manifest.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return manifest
