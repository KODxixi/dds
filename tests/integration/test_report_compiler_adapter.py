from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from dds.data.adapters import ListingEvidenceAdapter
from dds.analysis_profile import resolve_intervention_profile
from dds.data.catalog import DatasetAsset, DatasetKind
from dds.data.repository import CompetitorQuery, QueryResult
from dds.domain import ProjectContext, ResolvedStatus
from dds.engines.market import MarketEngine
from dds.engines.product import ProductEngine
from dds.reporting import compile_frozen_package, compute_report_document_hash
from dds.services import ReportService, ResearchService
from dds.services.report_compiler_adapter import ReportCompilerAdapter


class FixtureRepository:
    def query_competitors(self, query):
        asset = DatasetAsset(
            kind=DatasetKind.NEW_HOME_LISTINGS,
            path=Path(__file__).resolve(),
            city=query.city,
            format="fixture",
            size_bytes=1,
            modified_at="2026-07-22T00:00:00Z",
            source_hash="b" * 64,
            authority="fixture",
        )
        return QueryResult(
            records=tuple(
                {
                    "project_id": f"P{index}",
                    "project_name": f"Project {index}",
                    "city": query.city,
                    "district": "D1",
                    "sales_status": "在售",
                    "property_type": "住宅",
                    "price": 20_000 + index * 1_000,
                    "area_min": 90,
                    "area_max": 130,
                    "distance_km": index,
                    "source_ref": asset.source_ref,
                    "source_hash": asset.source_hash,
                }
                for index in range(5)
            ),
            dataset=asset,
            query_signature="fixture",
            filters={"city": query.city, "districts": ["D1"]},
        )


def test_report_run_to_compiler_package_is_deterministic_and_portable():
    research = ResearchService(
        adapter=ListingEvidenceAdapter(FixtureRepository()),
        market_engine=MarketEngine(),
    ).research_market(CompetitorQuery(city="测试城"))
    product = ProductEngine().propose_concept_schemes(
        market_evidence_refs=research.analysis.evidence_refs,
        preferred_direction_id="B",
    )
    run = ReportService().assemble(
        run_id="request-fixture",
        project_context=ProjectContext(
            project_id="P-001",
            project_name="Fixture",
            city="测试城",
            base_date="2026-07-22",
        ),
        evidence=research.evidence,
        market=research.analysis,
        product=product,
    )
    raw_evidence_ids: list[str] = []
    remapped_ids: dict[str, str] = {}
    for index, evidence in enumerate(run.evidence_records):
        old_id = evidence.evidence_id
        raw_id = rf"D:\\private\\evidence-{index}.json"
        evidence.evidence_id = raw_id
        raw_evidence_ids.append(raw_id)
        remapped_ids[old_id] = raw_id
    for section in run.sections.values():
        section.evidence_refs = [
            remapped_ids.get(source_ref, source_ref)
            for source_ref in section.evidence_refs
        ]

    adapter = ReportCompilerAdapter()
    first = adapter.build_frozen_compiler_package(
        run, required_units=("SC2", "AD3", "VA1")
    )
    second = adapter.build_frozen_compiler_package(
        run, required_units=("SC2", "AD3", "VA1")
    )
    assert first == second
    document = compile_frozen_package(first)
    repeated = compile_frozen_package(second)
    assert compute_report_document_hash(document) == compute_report_document_hash(repeated)
    assert [page["section_id"] for page in document["page_manifest"]] == [
        "SC2",
        "AD3",
    ]
    registry = first["source_registry"]
    registry_ids = {item["source_id"] for item in registry}
    expected_ids = {
        f"evidence-{sha256(raw_id.encode('utf-8')).hexdigest()[:24]}"
        for raw_id in raw_evidence_ids
    }
    assert registry_ids == expected_ids
    assert {
        item["evidence_id_sha256"] for item in registry
    } == {
        sha256(raw_id.encode("utf-8")).hexdigest()
        for raw_id in raw_evidence_ids
    }
    assert all(
        item["canonical_ref"] == f"dds:evidence/{item['source_id']}"
        for item in registry
    )

    frozen_seed = first["module_inputs"]["report_seed"]
    assert {
        item["source_id"] for item in frozen_seed["source_registry"]
    } == registry_ids
    all_refs: list[str] = []
    for pages in (frozen_seed["page_manifest"], document["page_manifest"]):
        for page in pages:
            all_refs.extend(page.get("source_refs", []))
            for block in page.get("blocks", []):
                all_refs.extend(block.get("source_refs", []))
    assert all_refs
    assert sum(ref in registry_ids for ref in all_refs) / len(all_refs) == 1.0

    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True)
    assert "D:\\" not in serialized
    assert "[local reference removed]" not in serialized
    assert all(raw_id not in serialized for raw_id in raw_evidence_ids)
    assert first["package_hash"]

    run.metadata["analysis_profile"] = resolve_intervention_profile(
        {"address": "?????? 1 ?"},
        selected_mode=1,
        confirmed=True,
    )
    run.metadata["decision_scope"] = "independent_opportunity_research"
    run.sections["VA1"].status = ResolvedStatus.PARTIAL
    run.sections["VA1"].conclusions = ["?????????????????"]
    adaptive = adapter.build_frozen_compiler_package(run)
    adaptive_document = compile_frozen_package(adaptive)
    panorama = adaptive["module_inputs"]["report_seed"]["project_panorama"]
    assert "VA1" not in panorama["intervention_required_units"]
    assert "VA1" not in panorama["required_units"]
    assert "VA1" not in panorama["included_units"]
    assert "VA1" not in {
        page["section_id"] for page in adaptive_document["page_manifest"]
    }
