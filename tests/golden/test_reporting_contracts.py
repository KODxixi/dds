# ruff: noqa: E402
from __future__ import annotations

import hashlib
import json
from typing import Any


from dds.reporting.contracts.evidence_contract import compute_evidence_confidence
from dds.reporting.contracts.report_chart_contract import normalize_chart_spec
from dds.reporting.contracts.report_diagram_contract import normalize_diagram_spec
from dds.reporting.contracts.report_structure_contract import (
    apply_section_metadata,
    compile_adaptive_manifest,
    framework_manifest,
)


def _hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_v1_contract_outputs_remain_byte_stable() -> None:
    confidence = compute_evidence_confidence(
        {
            "source": 1,
            "coverage": 0.8,
            "freshness": 0.6,
            "independent_cross": 0.4,
            "geographic_relevance": 0.9,
            "method_fit": 0.7,
            "stability": 0.5,
        }
    )
    chart = normalize_chart_spec(
        {
            "type": "bar",
            "title": "Price",
            "series": [{"name": "A", "value": 1}],
            "source_refs": ["S1", "S1"],
        },
        grammar="decision_narrative",
        page_id="p1",
        chart_index=0,
    )
    diagram = normalize_diagram_spec(
        {
            "diagram_type": "site_constraints",
            "site_geometry": {"boundary": [[0, 0], [1, 0], [0, 1]]},
            "source_refs": ["S1"],
        },
        page_id="p1",
        diagram_index=0,
    )

    assert _hash(confidence) == "6ee393260bf48bada384612ce47e102dca4a3ee31708e667534b42f313fd14b7"
    assert _hash(chart) == "8c9c497c60f2840a17f50ae557b888b883226f2704330907f9da8df7e003aba3"
    assert _hash(diagram) == "29bb2a17ddb7fb16f5ceb78e60bd8be897786f17b15b4027d09f27735f81eaa2"
    assert (
        _hash(framework_manifest())
        == "7a01dc0057e29e3c85907859e053d5d6875da5b63f73936c124bb4e50a2fd00b"
    )


def test_input_2_framework_uses_constraint_collaboration_ad_semantics() -> None:
    input_2 = framework_manifest(selected_mode=2)
    input_3 = framework_manifest(selected_mode=3)
    input_2_units = {item["unit_id"]: item for item in input_2["units"]}
    input_3_units = {item["unit_id"]: item for item in input_3["units"]}

    assert input_2_units["AD1"]["title"] == "定位、容量与可建包络"
    assert input_2_units["AD2"]["title"] == "约束驱动策略与评价基线"
    assert input_2_units["AD5"]["title"] == "设计任务书与阶段边界"
    assert "主推" not in input_2_units["AD2"]["question"]
    assert (
        next(group["label"] for group in input_2["groups"] if group["group_id"] == "AD")
        == "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1"
    )
    assert input_3_units["AD1"]["title"] == "方案1／2／3强排比选"
    assert input_3_units["AD2"]["title"] == "主推方案与决策闸门"
    assert (
        next(group["label"] for group in input_3["groups"] if group["group_id"] == "AD")
        == "\u4ea7\u54c1\u4e0e\u5efa\u7b51\u65b9\u6848"
    )
    assert input_3 == framework_manifest()


def test_input_1_framework_uses_independent_direction_ad_semantics() -> None:
    input_1 = framework_manifest(selected_mode=1)
    input_1_units = {item["unit_id"]: item for item in input_1["units"]}

    assert input_1_units["AD1"] == {
        "section_id": "AD1",
        "group_id": "AD",
        "unit_id": "AD1",
        "title": "定位与概念路线",
        "question": "哪些市场、客群与场地机会应转化为可验证的产品定位和概念路线？",
        "gap": "尚未把机会、客群和场地判断转成可验证的定位与概念路线。",
    }
    assert input_1_units["AD2"]["title"] == "方向选择与验证闸门"
    assert "主推方案" not in input_1_units["AD2"]["question"]
    assert (
        next(group["label"] for group in input_1["groups"] if group["group_id"] == "AD")
        == "产品方向与概念路线"
    )


def test_explicit_page_section_title_survives_canonical_metadata() -> None:
    page = apply_section_metadata(
        {
            "page_id": "ad2-input2",
            "section_id": "AD2",
            "section_title": "约束驱动策略与评价基线",
            "section_group_label": "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1",
        }
    )

    assert page["section_title"] == "约束驱动策略与评价基线"
    assert page["section_id"] == "AD2"
    assert page["section_group_label"] == "\u4ea7\u54c1\u5b9a\u4f4d\u4e0e\u8bbe\u8ba1\u4efb\u52a1"


def test_adaptive_manifest_keeps_missing_required_units_as_internal_gate() -> None:
    compiled = compile_adaptive_manifest(
        [
            {
                "page_id": "sc1-primary",
                "section_id": "SC1",
                "unit_id": "SC1",
                "title": "项目身份已冻结",
                "takeaway": "本轮以已确认项目身份为准。",
                "blocks": [{"type": "narrative", "text": "项目身份与时点已冻结。"}],
            }
        ],
        required_units=("SC1", "VA2"),
        included_units=("SC1",),
    )

    assert compiled["required_units"] == ["SC1", "VA2"]
    assert compiled["included_units"] == ["SC1"]
    assert compiled["missing_required_units"] == ["VA2"]
    assert compiled["delivery_ready"] is False
