from __future__ import annotations

from pathlib import Path

from dds.data.adapters import ListingEvidenceAdapter
from dds.data.catalog import DatasetAsset, DatasetKind
from dds.data.repository import CompetitorQuery, QueryResult
from dds.domain import ProjectContext
from dds.engines.market import MarketEngine, MarketReadiness
from dds.engines.premium import PremiumEngine, PremiumScope, PremiumStatus
from dds.engines.product import ProductEngine, ProductStatus
from dds.services import ReportService, ResearchService


class FixtureRepository:
    def query_competitors(self, query):
        asset = DatasetAsset(
            kind=DatasetKind.NEW_HOME_LISTINGS,
            path=Path(__file__).resolve(),
            city=query.city,
            format="fixture",
            size_bytes=1,
            modified_at="2026-06-25T00:00:00+00:00",
            source_hash="a" * 64,
            authority="fixture",
        )
        rows = tuple(
            {
                "project_id": str(index),
                "project_name": f"P{index}",
                "city": query.city,
                "district": "核心区",
                "subdistrict": "样本板块",
                "sales_status": "在售",
                "property_type": "住宅",
                "price": 20_000 + index * 500,
                "area_min": 90,
                "area_max": 130,
                "distance_km": index,
                "source_ref": asset.source_ref,
                "source_hash": asset.source_hash,
            }
            for index in range(6)
        )
        return QueryResult(
            records=rows,
            dataset=asset,
            query_signature="fixture-query",
            filters={"city": query.city, "districts": ["核心区"]},
        )


def test_sc2_ad3_va1_assemble_without_silent_fill():
    research = ResearchService(
        adapter=ListingEvidenceAdapter(FixtureRepository()),
        market_engine=MarketEngine(),
    )
    market = research.research_market(CompetitorQuery(city="测试城"))
    assert market.analysis.readiness is MarketReadiness.READY
    assert len(market.evidence) == len(market.analysis.evidence_refs) == 6

    product = ProductEngine().propose_concept_schemes(
        market_evidence_refs=market.analysis.evidence_refs,
        preferred_direction_id="B",
        statutory_inputs_complete=False,
    )
    assert product.status is ProductStatus.CONCEPT_ONLY

    premium = PremiumEngine().assess(
        scope=PremiumScope.SELLING_PRICE,
        baseline_unit_price_cny_m2=market.analysis.price_median,
        saleable_area_m2=None,
        baseline_evidence_refs=market.analysis.evidence_refs,
        drivers=(),
    )
    assert premium.status is PremiumStatus.NOT_ASSESSABLE
    assert premium.scenarios == ()

    run = ReportService().assemble(
        run_id="fixture-run",
        project_context=ProjectContext(
            project_id="fixture-project",
            city="测试城",
            decision_question="纵向切片验证",
            evidence_boundary="仅新房挂牌快照",
            base_date="2026-06-25",
        ),
        evidence=market.evidence,
        market=market.analysis,
        product=product,
        premium=premium,
    )
    assert len(run.sections) == 12
    assert all(key == section.section_id for key, section in run.sections.items())
    assert run.sections["SC2"].data["competitors"].value
    assert run.sections["AD3"].data["sales_rhythm"].value is None
    assert run.sections["VA1"].data["premium_factors"].value is None
    assert run.sections["VA1"].gaps
