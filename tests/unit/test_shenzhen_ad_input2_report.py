from __future__ import annotations

import ast
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any

import pytest

from dds.reporting.renderer import render_cinematic_deck_html
from dds.reporting.report_document import build_report_document


ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "tools/dev/build_shenzhen_ad_report.py"
EXPECTED_PAGE_IDS = [
    "ad1-01",
    "ad1-02",
    "ad1-03",
    "ad1-04",
    "ad2-01",
    "ad2-02",
    "ad3-01",
    "ad3-02",
    "ad3-03",
    "ad4-01",
    "ad4-02",
    "ad5-01",
]


def _load_build_module() -> ModuleType:
    assert BUILD_SCRIPT.is_file(), "Shenzhen AD Input 2 build script is missing"
    spec = spec_from_file_location("dds_shenzhen_ad_report_test", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.path.insert(0, str(BUILD_SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


@pytest.fixture
def ad_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    module = _load_build_module()
    assert hasattr(module, "build_ad_pages")

    def fake_find(root: Path, name: str) -> Path:
        return root / name

    def fake_source_id(path: Path) -> str:
        return f"SRC-{path.name}"

    monkeypatch.setattr(module, "_find", fake_find)
    monkeypatch.setattr(module, "_source_id", fake_source_id)
    if hasattr(module, "_render_pdf_page"):
        monkeypatch.setattr(
            module,
            "_render_pdf_page",
            lambda *args, **kwargs: "data:image/jpeg;base64,ZmFrZQ==",
        )
    if hasattr(module, "_pptx_media_data_uri"):
        monkeypatch.setattr(
            module,
            "_pptx_media_data_uri",
            lambda *args, **kwargs: "data:image/png;base64,ZmFrZQ==",
        )
    pages = module.build_ad_pages(
        tmp_path / "v1",
        tmp_path / "pdftoppm.exe",
        tmp_path / "rendered",
    )
    assert isinstance(pages, list)
    return pages


def _has_primary_visual(page: dict[str, Any]) -> bool:
    has_image = any(
        isinstance(block, dict) and block.get("type") == "image"
        for block in page.get("blocks") or []
    )
    return bool(has_image or page.get("chart_specs") or page.get("diagram_specs"))


def _numeric_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.fullmatch(r"\s*([\d,.]+)\s*(?:㎡|m²)?\s*", str(value))
    if not match:
        return None
    return int(float(match.group(1).replace(",", "")))


def _collect_labeled_values(
    node: Any,
    result: dict[str, set[int]],
) -> None:
    if isinstance(node, dict):
        label = next(
            (str(node[key]) for key in ("label", "name", "category") if node.get(key)),
            "",
        )
        value = _numeric_value(node.get("value"))
        if label and value is not None:
            result.setdefault(label, set()).add(value)
        for key, value_node in node.items():
            if key == "rows" and isinstance(value_node, list):
                for row in value_node:
                    if isinstance(row, list) and len(row) >= 2:
                        row_value = _numeric_value(row[1])
                        if row_value is not None:
                            result.setdefault(str(row[0]), set()).add(row_value)
            _collect_labeled_values(value_node, result)
    elif isinstance(node, list):
        for item in node:
            _collect_labeled_values(item, result)


def _assert_area_value(
    values: dict[str, set[int]],
    label_fragment: str,
    expected: int,
) -> None:
    matching_values = {
        value
        for label, candidates in values.items()
        if label_fragment in label
        for value in candidates
    }
    assert expected in matching_values


def test_ad_input_2_has_exactly_twelve_ordered_pages(
    ad_pages: list[dict[str, Any]],
) -> None:
    assert [page["page_id"] for page in ad_pages] == EXPECTED_PAGE_IDS
    assert len(ad_pages) == 12
    assert {page["section_id"] for page in ad_pages} == {
        "AD1",
        "AD2",
        "AD3",
        "AD4",
        "AD5",
    }


def test_ad_input_2_pages_all_pass_the_reader_value_contract(
    ad_pages: list[dict[str, Any]],
) -> None:
    for page in ad_pages:
        assert str(page.get("decision_question") or "").strip(), page["page_id"]
        assert str(page.get("takeaway") or "").strip(), page["page_id"]
        assert str(page.get("decision_impact") or "").strip(), page["page_id"]
        assert page.get("source_refs"), page["page_id"]
        assert _has_primary_visual(page), page["page_id"]


def test_ad_input_2_does_not_reintroduce_v1_scheme_selection(
    ad_pages: list[dict[str, Any]],
) -> None:
    decision_text = json.dumps(
        [
            {
                "title": page.get("title"),
                "takeaway": page.get("takeaway"),
                "decision_impact": page.get("decision_impact"),
            }
            for page in ad_pages
        ],
        ensure_ascii=False,
    )
    for forbidden in (
        "方案A",
        "方案 A",
        "方案B",
        "方案 B",
        "方案C",
        "方案 C",
        "C+",
        "主推'方案",
        "主推“方案",
        "主推方案 C",
        "推荐方案",
        "淘汰理由",
        "切换方案",
    ):
        assert forbidden not in decision_text


def test_ad_program_ledger_closes_to_119535_and_cites_original_inputs(
    ad_pages: list[dict[str, Any]],
) -> None:
    area_page = next(
        page
        for page in ad_pages
        if all(
            token in json.dumps(page, ensure_ascii=False) for token in ("104600", "10095", "4100")
        )
    )
    values: dict[str, set[int]] = {}
    _collect_labeled_values(area_page, values)
    _assert_area_value(values, "住宅", 104600)
    _assert_area_value(values, "商业", 10095)
    _assert_area_value(values, "幼儿园", 4100)

    has_combined_support = any(
        "托育" in label and "物业" in label and 740 in candidates
        for label, candidates in values.items()
    )
    has_separate_support = any(
        "托育" in label and 500 in candidates for label, candidates in values.items()
    ) and any("物业" in label and 240 in candidates for label, candidates in values.items())
    assert has_combined_support or has_separate_support
    assert 104600 + 10095 + 4100 + 740 == 119535

    area_sources = set(area_page["source_refs"])
    assert "SRC-规划设计要点.pdf" in area_sources
    assert area_sources & {
        "SRC-宝安中心区DY02-01地块规划设计条件研究.pdf",
        "SRC-宝中方案招标任务书0710.docx",
        "SRC-宝中DY02-01审查要点.docx",
    }


def test_ad3_translates_penguin_island_customers_without_conversion_rates(
    ad_pages: list[dict[str, Any]],
) -> None:
    ad3_text = json.dumps(
        [page for page in ad_pages if page["section_id"] == "AD3"],
        ensure_ascii=False,
    )
    assert "企鹅岛" in ad3_text
    assert "高管" in ad3_text
    assert "核心骨干" in ad3_text
    assert "生态链" in ad3_text
    assert "转化率" not in ad3_text
    assert re.search(r"员工.{0,12}\d+(?:\.\d+)?%", ad3_text) is None


def test_ad_build_is_frozen_to_confirmed_input_2() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected_modes = [
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "selected_mode"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, int)
    ]
    assert selected_modes == [2]
    assert '"confirmed_at"' in source
    assert "datetime.now" not in source
    assert 'FROZEN_AT = "2026-07-24T16:40:21+08:00"' in source
    temporary_directories = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TemporaryDirectory"
    ]
    assert len(temporary_directories) == 1
    directory_keyword = next(
        (keyword.value for keyword in temporary_directories[0].keywords if keyword.arg == "dir"),
        None,
    )
    assert isinstance(directory_keyword, ast.Attribute)
    assert isinstance(directory_keyword.value, ast.Name)
    assert directory_keyword.value.id == "output"
    assert directory_keyword.attr == "parent"


def test_input_2_ad_titles_survive_document_and_renderer_indexes(
    ad_pages: list[dict[str, Any]],
) -> None:
    unit_ids = ["AD1", "AD2", "AD3", "AD4", "AD5"]
    source_ids = sorted(
        {str(source_ref) for page in ad_pages for source_ref in page.get("source_refs") or []}
    )
    document = build_report_document(
        {
            "page_manifest_authoritative": True,
            "project": {"project_id": "shenzhen-input2-ad", "name": "深圳 Input 2 AD"},
            "meta": {"analysis_profile": {"selected_mode": 2}},
            "project_panorama": {
                "required_units": unit_ids,
                "included_units": unit_ids,
            },
            "page_manifest": ad_pages,
            "source_registry": [
                {"source_id": source_id, "title": source_id} for source_id in source_ids
            ],
        }
    )

    framework_units = {item["unit_id"]: item for item in document["report_framework"]["units"]}
    unit_data = {item["unit_id"]: item for item in document["unit_data"]}
    assert framework_units["AD1"]["title"] == "定位、容量与可建包络"
    assert framework_units["AD2"]["title"] == "约束驱动策略与评价基线"
    assert unit_data["AD1"]["unit_title"] == "定位、容量与可建包络"
    assert unit_data["AD2"]["unit_title"] == "约束驱动策略与评价基线"
    assert (
        next(page for page in document["page_manifest"] if page["section_id"] == "AD1")[
            "section_title"
        ]
        == "定位、容量与可建包络"
    )
    assert (
        next(page for page in document["page_manifest"] if page["section_id"] == "AD1")[
            "section_group_label"
        ]
        == "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1"
    )

    rendered = render_cinematic_deck_html({"report_document": document})
    payload_match = re.search(
        (
            r'<script id="dds-report-payload" type="application/json">'
            r"(.*?)</script>"
        ),
        rendered,
        re.DOTALL,
    )
    assert payload_match is not None
    metadata = json.loads(payload_match.group(1))
    page_index = {item["section_id"]: item for item in metadata["page_index"]}
    section_index = {item["section_id"]: item for item in metadata["section_index"]}
    assert page_index["AD1"]["section_title"] == "定位、容量与可建包络"
    assert page_index["AD2"]["section_title"] == "约束驱动策略与评价基线"
    assert (
        page_index["AD1"]["section_group_label"]
        == "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1"
    )
    assert (
        page_index["AD2"]["section_group_label"]
        == "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1"
    )
    assert section_index["AD1"]["title"] == "定位、容量与可建包络"
    assert section_index["AD2"]["title"] == "约束驱动策略与评价基线"
    assert (
        section_index["AD1"]["section_group_label"]
        == "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1"
    )
    assert (
        section_index["AD2"]["section_group_label"]
        == "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1"
    )
