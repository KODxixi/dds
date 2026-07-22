from __future__ import annotations

from decimal import Decimal

from dds.engines.premium import (
    PremiumDriver,
    PremiumEngine,
    PremiumScope,
    PremiumStatus,
)
from dds.engines.product import ProductEngine, ProductStatus, UnitMixRow


def test_product_audit_reconciles_units_area_share_and_value():
    rows = [
        UnitMixRow("刚需", 90, 60, 20_000, 0.6, ("scheme:E1",)),
        UnitMixRow("改善", 130, 40, 24_000, 0.4, ("scheme:E2",)),
    ]
    result = ProductEngine().audit_existing_scheme(
        rows,
        declared_total_units=100,
        declared_saleable_area_m2=10_600,
        declared_saleable_value_cny=232_800_000,
        statutory_inputs_complete=True,
    )
    assert result.status is ProductStatus.READY
    assert result.audit is not None and result.audit.reconciled
    assert result.audit.computed_shares == {"刚需": 0.6, "改善": 0.4}


def test_product_audit_above_half_percent_fails_reconciliation():
    result = ProductEngine().audit_existing_scheme(
        [UnitMixRow("改善", 100, 100, 20_000, evidence_refs=("E1",))],
        declared_total_units=101,
        declared_saleable_area_m2=10_000,
        declared_saleable_value_cny=200_000_000,
        statutory_inputs_complete=True,
    )
    assert result.status is ProductStatus.CONCEPT_ONLY
    assert result.audit is not None and not result.audit.reconciled
    assert result.audit.reconciliation_errors["total_units"] > 0.005


def test_concept_mode_does_not_invent_recommendation_without_market_evidence():
    result = ProductEngine().propose_concept_schemes(
        market_evidence_refs=[], preferred_direction_id="B"
    )
    assert result.status is ProductStatus.BLOCKED
    assert result.recommended_direction_id is None
    assert len(result.concepts) == 3


def test_premium_missing_cost_is_not_assessable_and_has_no_numbers():
    result = PremiumEngine().assess(
        scope=PremiumScope.SELLING_PRICE,
        baseline_unit_price_cny_m2=20_000,
        saleable_area_m2=10_000,
        baseline_evidence_refs=("SC2:E1",),
        drivers=(
            PremiumDriver(
                "facade",
                "立面升级",
                "到达感知",
                0.01,
                0.02,
                0.03,
                None,
                ("VA1:E1",),
                "matched comparison",
            ),
        ),
    )
    assert result.status is PremiumStatus.NOT_ASSESSABLE
    assert result.scenarios == ()


def test_premium_range_is_formula_backed_and_deterministic():
    driver = PremiumDriver(
        "landscape",
        "景观升级",
        "停留体验",
        "0.01",
        "0.02",
        "0.03",
        "1000000",
        ("driver:E1",),
        "versioned counterfactual model",
    )
    kwargs = dict(
        scope=PremiumScope.SELLING_PRICE,
        baseline_unit_price_cny_m2="20000",
        saleable_area_m2="10000",
        baseline_evidence_refs=("baseline:E1",),
        drivers=(driver,),
    )
    first = PremiumEngine().assess(**kwargs)
    second = PremiumEngine().assess(**kwargs)
    assert first == second
    assert first.status is PremiumStatus.ASSESSABLE
    assert first.scenarios[1].combined_rate == Decimal("0.020000")
    assert first.scenarios[1].net_incremental_value_cny == Decimal("3000000.00")
