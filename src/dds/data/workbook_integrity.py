"""Fail-closed structural integrity checks for uploaded XLSX workbooks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Mapping
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_QUOTED_SHEET_REF = re.compile(r"'((?:[^']|'')+)'!")
_UNQUOTED_SHEET_REF = re.compile(
    r"(?<![\]'A-Za-z0-9_])([A-Za-z_\u3400-\u9fff][A-Za-z0-9_.\u3400-\u9fff]*)!"
)
_EXTERNAL_REF = re.compile(r"\[[^\]]+\]")


@dataclass(frozen=True, slots=True)
class WorkbookIssue:
    kind: str
    message: str
    sheet: str = ""
    cell: str = ""
    formula: str = ""


@dataclass(frozen=True, slots=True)
class WorkbookIntegrityReport:
    status: str
    issues: tuple[WorkbookIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "issues": [asdict(issue) for issue in self.issues]}


class WorkbookIntegrityScanner:
    """Inspect XLSX package structure without evaluating formulas."""

    def scan(
        self,
        path: Path,
        required_cells: Mapping[str, tuple[str, ...]] | None = None,
    ) -> WorkbookIntegrityReport:
        try:
            with ZipFile(path) as archive:
                return self._scan_archive(archive, required_cells or {})
        except (BadZipFile, KeyError, ElementTree.ParseError, OSError, ValueError) as exc:
            issue = WorkbookIssue("invalid_workbook", f"无法解析 XLSX：{exc}")
            return WorkbookIntegrityReport("blocked", (issue,))

    def _scan_archive(
        self,
        archive: ZipFile,
        required_cells: Mapping[str, tuple[str, ...]],
    ) -> WorkbookIntegrityReport:
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall(f"{{{_PKG_REL_NS}}}Relationship")
        }
        sheets: dict[str, str] = {}
        for sheet in workbook.findall(f".//{{{_MAIN_NS}}}sheet"):
            target = targets[sheet.attrib[f"{{{_DOC_REL_NS}}}id"]].replace("\\", "/")
            sheets[sheet.attrib["name"]] = (
                f"xl/{target.lstrip('/')}" if not target.startswith("xl/") else target
            )

        issues: list[WorkbookIssue] = []
        if any(name.startswith("xl/externalLinks/") for name in archive.namelist()):
            issues.append(WorkbookIssue("external_link", "工作簿包含外部链接关系"))

        known_sheets = set(sheets)
        for sheet_name, member in sheets.items():
            root = ElementTree.fromstring(archive.read(member))
            cells = {cell.attrib.get("r", ""): cell for cell in root.findall(f".//{{{_MAIN_NS}}}c")}
            for address, cell in cells.items():
                formula_node = cell.find(f"{{{_MAIN_NS}}}f")
                value_node = cell.find(f"{{{_MAIN_NS}}}v")
                formula = formula_node.text or "" if formula_node is not None else ""
                value = value_node.text or "" if value_node is not None else ""
                if "#REF!" in formula or value == "#REF!":
                    issues.append(
                        WorkbookIssue(
                            "broken_reference",
                            "公式或缓存值包含 #REF!",
                            sheet_name,
                            address,
                            formula,
                        )
                    )
                if _EXTERNAL_REF.search(formula):
                    issues.append(
                        WorkbookIssue(
                            "external_link", "公式引用外部工作簿", sheet_name, address, formula
                        )
                    )
                referenced = {match.replace("''", "'") for match in _QUOTED_SHEET_REF.findall(formula)}
                referenced.update(_UNQUOTED_SHEET_REF.findall(formula))
                for referenced_sheet in sorted(referenced - known_sheets - {"REF"}):
                    issues.append(
                        WorkbookIssue(
                            "missing_sheet_reference",
                            f"公式引用不存在的工作表：{referenced_sheet}",
                            sheet_name,
                            address,
                            formula,
                        )
                    )

            for address in required_cells.get(sheet_name, ()):
                cell = cells.get(address)
                value = cell.find(f"{{{_MAIN_NS}}}v") if cell is not None else None
                inline = cell.find(f".//{{{_MAIN_NS}}}t") if cell is not None else None
                formula = cell.find(f"{{{_MAIN_NS}}}f") if cell is not None else None
                has_content = any(
                    node is not None and (node.text or "").strip()
                    for node in (value, inline, formula)
                )
                if cell is None or not has_content:
                    issues.append(
                        WorkbookIssue(
                            "required_cell_empty", "必填单元格为空", sheet_name, address
                        )
                    )

        for required_sheet in set(required_cells) - known_sheets:
            issues.append(
                WorkbookIssue(
                    "missing_sheet_reference",
                    f"必填工作表不存在：{required_sheet}",
                    required_sheet,
                )
            )

        unique = tuple(dict.fromkeys(issues))
        return WorkbookIntegrityReport("blocked" if unique else "valid", unique)
