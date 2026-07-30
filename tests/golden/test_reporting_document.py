from __future__ import annotations



from dds.reporting.compiler import compute_report_document_hash
from dds.reporting.report_document import build_report_document


def report_seed() -> dict:
    return {
        "meta": {
            "as_of": "2026-07-22",
            "compiled_at": "2026-07-22T00:00:00+00:00",
        },
        "project": {
            "project_id": "golden-001",
            "name": "Golden Parcel",
            "city": "Jinan",
            "address": "1 Test Road",
        },
        "project_panorama": {"required_units": ["SC1"]},
        "page_manifest_authoritative": True,
        "page_manifest": [
            {
                "page_id": "decision-summary",
                "chapter_id": "decision",
                "section_id": "SC1",
                "unit_status": "ready",
                "layout": "summary",
                "title": "Decision Summary",
                "decision_question": "Should the project enter the next stage?",
                "takeaway": "Conditional entry",
                "decision_impact": "Proceed only while the cited entry condition holds.",
                "blocks": [
                    {
                        "type": "metric",
                        "label": "Target price",
                        "value": 18000,
                        "unit": "CNY/m2",
                    }
                ],
                "source_refs": ["SRC-1"],
                "confidence": {"score": 0.72},
                "evidence_type": "analysis_inference",
            }
        ],
        "source_registry": [
            {"source_id": "SRC-1", "title": "Offline sample", "kind": "fixture"}
        ],
    }


def test_report_document_matches_v1_golden_hash() -> None:
    document = build_report_document(report_seed())

    assert compute_report_document_hash(document) == (
        "456a3043cb34e8a64fa3320cb65a9490edd533cdc66e284dc740a4c9763b839b"
    )
    assert document["schema_version"] == "dds.report-document/1.2"
    assert [page["page_id"] for page in document["page_manifest"]] == [
        "decision-summary"
    ]


def test_core_qa_rejects_authoritative_page_that_fails_page_value_gate() -> None:
    seed = report_seed()
    page = seed["page_manifest"][0]
    page["decision_question"] = "当前证据是否支持进入下一阶段？"
    page["decision_impact"] = ""
    page["source_refs"] = []

    document = build_report_document(seed)

    assert document["page_manifest"][0]["value_gate"] == {
        "status": "failed",
        "errors": [
            "source_refs_missing",
            "decision_impact_or_action_missing",
        ],
    }
    assert document["qa"]["page_value_failures"] == [
        {
            "page_id": "decision-summary",
            "unit_id": "SC1",
            "errors": [
                "source_refs_missing",
                "decision_impact_or_action_missing",
            ],
        }
    ]
    assert document["qa"]["checks"]["page_value_gate_passed"] is False
    assert document["qa"]["delivery_ready"] is False
