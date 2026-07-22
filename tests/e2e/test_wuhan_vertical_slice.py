from __future__ import annotations

import pytest

from dds.config import Settings
from dds.data.catalog import DatasetCatalog, DatasetNotFoundError
from dds.data.repository import CompetitorQuery, DatasetRepository
from dds.domain import ProjectContext
from dds.engines.market import MarketEngine, MarketReadiness
from dds.engines.premium import PremiumEngine, PremiumScope, PremiumStatus
from dds.engines.product import ProductEngine
from dds.reporting import (
    compile_frozen_package,
    compute_report_document_hash,
    render_frozen_package,
)
from dds.services import ReportService, ResearchService
from dds.services.report_compiler_adapter import ReportCompilerAdapter
from dds.data.adapters import ListingEvidenceAdapter


@pytest.mark.integration
def test_real_wuhan_sc2_ad3_va1_freeze_compile_work_report():
    settings = Settings.from_env()
    catalog = DatasetCatalog(settings)
    try:
        catalog.resolve_new_home_listings("武汉")
    except DatasetNotFoundError:
        pytest.skip("configured V1 Wuhan listing snapshot is unavailable")

    research = ResearchService(
        adapter=ListingEvidenceAdapter(DatasetRepository(catalog)),
        market_engine=MarketEngine(
            settings.minimum_competitors, settings.degraded_competitors
        ),
    ).research_market(CompetitorQuery(city="武汉", limit=20))
    assert research.analysis.readiness is MarketReadiness.READY
    assert research.analysis.effective_sample_size >= 5
    assert set(research.analysis.evidence_refs) == {
        item.evidence_id for item in research.evidence
    }

    product = ProductEngine().propose_concept_schemes(
        market_evidence_refs=research.analysis.evidence_refs,
        preferred_direction_id="B",
        statutory_inputs_complete=False,
    )
    premium = PremiumEngine().assess(
        scope=PremiumScope.SELLING_PRICE,
        baseline_unit_price_cny_m2=research.analysis.price_median,
        saleable_area_m2=None,
        baseline_evidence_refs=research.analysis.evidence_refs,
        drivers=(),
    )
    assert premium.status is PremiumStatus.NOT_ASSESSABLE
    assert premium.scenarios == ()

    run = ReportService().assemble(
        run_id="wuhan-real-slice",
        project_context=ProjectContext(
            project_id="WH-REAL-SLICE",
            project_name="武汉真实数据纵向切片",
            city="武汉",
            project_type="住宅",
            decision_question="验证真实证据闭环，不形成投决定价承诺",
            evidence_boundary="V1 只读新房挂牌快照；不含成交、成本与法定条件",
            base_date="2026-07-22",
        ),
        evidence=research.evidence,
        market=research.analysis,
        product=product,
        premium=premium,
    )
    package = ReportCompilerAdapter().build_frozen_compiler_package(
        run, required_units=("SC2", "AD3", "VA1")
    )
    first = compile_frozen_package(package)
    second = compile_frozen_package(package)
    assert compute_report_document_hash(first) == compute_report_document_hash(second)
    assert first["page_manifest"] == second["page_manifest"]
    section_order = list(
        dict.fromkeys(page["section_id"] for page in first["page_manifest"])
    )
    assert section_order == ["SC2", "AD3", "VA1"]
    assert {page["section_id"] for page in first["page_manifest"]} == {
        "SC2",
        "AD3",
        "VA1",
    }
    assert first["qa"]["delivery_ready"] is False
    assert first["qa"]["unit_readiness"]["VA1"] == "missing"
    html = render_frozen_package(package)
    assert "D:\\Vault-assets" not in html
    assert '<script src="http' not in html.lower()
    assert '<link href="http' not in html.lower()
