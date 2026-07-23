from __future__ import annotations

from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from dds.projects import V1ProjectImporter


def _pptx(path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:t>方案 A</a:t></p:sld>',
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:t>方案 B</a:t></p:sld>',
        )


def test_v1_importer_copies_hashes_and_extracts_auditable_locators(tmp_path):
    source = tmp_path / "v1"
    source.mkdir()
    (source / "事实卡.md").write_text("限高 80m", encoding="utf-8")
    _pptx(source / "初始方案.pptx")
    (source / "总图.dwg").write_bytes(b"dwg-placeholder")

    target = tmp_path / "v2"
    manifest = V1ProjectImporter().import_project(
        source,
        target,
        project_id="P-001",
        project_name="真实项目",
        imported_at="2026-07-23T12:00:00+08:00",
    )

    assert len(manifest.source_assets) == 3
    assert all(len(item.sha256) == 64 for item in manifest.source_assets)
    assert all((target / item.snapshot_ref).is_file() for item in manifest.source_assets)
    pptx = next(item for item in manifest.source_assets if item.original_name.endswith(".pptx"))
    assert [item.locator for item in pptx.locators] == ["slide:1", "slide:2"]
    dwg = next(item for item in manifest.source_assets if item.original_name.endswith(".dwg"))
    assert dwg.extraction_status == "registered_only"
    assert "PDF or DXF" in dwg.limitations[0]
    repeated = V1ProjectImporter().import_project(
        source,
        target,
        project_id="P-001",
        project_name="真实项目",
        imported_at="2026-07-23T12:00:00+08:00",
    )
    assert repeated.source_root_hash == manifest.source_root_hash


def test_v1_importer_rejects_paths_outside_project_root(tmp_path):
    source = tmp_path / "v1"
    source.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes V1 project root"):
        V1ProjectImporter().import_project(
            source,
            tmp_path / "v2",
            project_id="P-001",
            project_name="真实项目",
            imported_at="2026-07-23T12:00:00+08:00",
            include_paths=(outside,),
        )
