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
                "layout": "summary",
                "title": "Decision Summary",
                "takeaway": "Conditional entry",
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
        "bd410699a0e2ecb9870feadae8916a5394db2d10b7c6ce8517f3ba0a0caa28d1"
    )
    assert document["schema_version"] == "dds.report-document/1.2"
    assert [page["page_id"] for page in document["page_manifest"]] == [
        "decision-summary"
    ]
