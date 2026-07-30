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
from dds.reporting import (
    ReportEdition,
    compile_frozen_package,
    compute_report_document_hash,
)
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
    panorama = first["module_inputs"]["report_seed"]["project_panorama"]
    assert panorama["required_units"] == ["SC2", "AD3", "VA1"]
    assert panorama["included_units"] == ["SC2", "AD3"]
    assert document["qa"]["missing_required_units"] == ["VA1"]
    assert document["qa"]["delivery_ready"] is False
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


def test_compiler_routes_ad_title_question_and_gap_by_selected_mode() -> None:
    expected_by_mode = {
        1: {
            "title": "定位与概念路线",
            "question": "哪些市场、客群与场地机会应转化为可验证的产品定位和概念路线？",
            "gap": "尚未把机会、客群和场地判断转成可验证的定位与概念路线。",
        },
        2: {
            "title": "定位、容量与可建包络",
            "question": "哪些市场、客群与场地输入有资格进入设计，完整容量和可建包络如何建立？",
            "gap": "尚未把已确认的市场、客群、功能指标与法定边界转成定位、容量和可建包络。",
        },
        3: {
            "title": "方案1／2／3强排比选",
            "question": "至少三个方向性方案在同一口径下分别获得和牺牲什么？",
            "gap": "尚未形成方案1／2／3的同口径容量、空间、成本、运营与风险比选。",
        },
    }

    for mode, expected in expected_by_mode.items():
        input_profile: dict[str, object] = {"address": "测试城测试路 1 号"}
        if mode == 2:
            input_profile["constraints"] = [{"constraint_id": "height-limit"}]
        if mode == 3:
            input_profile["schemes"] = [{"scheme_id": "A"}, {"scheme_id": "B"}]
        run = ReportService().assemble(
            run_id=f"route-input-{mode}",
            project_context=ProjectContext(
                project_id=f"route-{mode}",
                project_name=f"Route Input {mode}",
                city="测试城",
                base_date="2026-07-30",
            ),
            evidence=[],
            analysis_profile=resolve_intervention_profile(
                input_profile,
                selected_mode=mode,
                confirmed=True,
            ),
        )

        seed = ReportCompilerAdapter().build_report_seed(
            run,
            edition=ReportEdition.EVIDENCE_WORKBOOK,
        )
        page = next(
            item for item in seed["page_manifest"] if item["section_id"] == "AD1"
        )

        assert page["title"] == expected["title"]
        assert page["decision_question"] == expected["question"]
        assert page["takeaway"] == expected["gap"]
        assert page["blocks"] == [
            {
                "type": "gap",
                "text": expected["gap"],
                "source_refs": [],
            }
        ]
        assert next(
            item["gap"]
            for item in seed["evidence_gaps"]
            if item["section_id"] == "AD1"
        ) == expected["gap"]
