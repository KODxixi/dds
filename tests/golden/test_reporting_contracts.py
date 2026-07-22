# ruff: noqa: E402
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from dds.reporting.contracts.evidence_contract import compute_evidence_confidence
from dds.reporting.contracts.report_chart_contract import normalize_chart_spec
from dds.reporting.contracts.report_diagram_contract import normalize_diagram_spec
from dds.reporting.contracts.report_structure_contract import framework_manifest


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

    assert (
        _hash(confidence)
        == "6ee393260bf48bada384612ce47e102dca4a3ee31708e667534b42f313fd14b7"
    )
    assert (
        _hash(chart)
        == "8c9c497c60f2840a17f50ae557b888b883226f2704330907f9da8df7e003aba3"
    )
    assert (
        _hash(diagram)
        == "29bb2a17ddb7fb16f5ceb78e60bd8be897786f17b15b4027d09f27735f81eaa2"
    )
    assert (
        _hash(framework_manifest())
        == "cac819edf385dbf55db90fb768c3a01567e78e6cb2f85304c30917eb6daf5398"
    )
