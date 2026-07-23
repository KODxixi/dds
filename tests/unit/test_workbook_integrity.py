from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

from dds.data.workbook_integrity import WorkbookIntegrityScanner


def _write_workbook(path, formula="1+1", value="2", external=False):
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Summary" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData><row r="1"><c r="A1"><f>{formula}</f><v>{value}</v></c></row></sheetData>'
            '</worksheet>',
        )
        if external:
            archive.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")


def test_scan_blocks_structural_formula_failures(tmp_path):
    path = tmp_path / "broken.xlsx"
    _write_workbook(
        path,
        formula="#REF!+'Missing Sheet'!B2+[external.xlsx]Sheet1!C3",
        value="#REF!",
        external=True,
    )

    report = WorkbookIntegrityScanner().scan(path, {"Summary": ("A1", "B2")})

    assert report.status == "blocked"
    assert {issue.kind for issue in report.issues} == {
        "broken_reference",
        "external_link",
        "missing_sheet_reference",
        "required_cell_empty",
    }


def test_scan_accepts_structurally_valid_workbook(tmp_path):
    path = tmp_path / "valid.xlsx"
    _write_workbook(path)
    assert WorkbookIntegrityScanner().scan(path, {"Summary": ("A1",)}).status == "valid"


def test_scan_fails_closed_for_invalid_archive(tmp_path):
    path = tmp_path / "invalid.xlsx"
    path.write_bytes(b"not an xlsx")
    report = WorkbookIntegrityScanner().scan(path)
    assert report.status == "blocked"
    assert report.issues[0].kind == "invalid_workbook"
