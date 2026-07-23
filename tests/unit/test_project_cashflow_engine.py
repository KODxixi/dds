from __future__ import annotations

from decimal import Decimal

from dds.engines.project_cashflow import (
    ProductBatch,
    ProjectCashFlowEngine,
    ProjectCostParameters,
    ProjectDemandParameters,
    ProjectSimulationAssumptions,
)


def batches() -> tuple[ProductBatch, ...]:
    return (
        ProductBatch(
            "phase-1-main",
            "main",
            1,
            120,
            100,
            20_000,
            0.65,
            ("scheme:E1",),
        ),
        ProductBatch(
            "phase-2-value",
            "value",
            7,
            80,
            135,
            24_000,
            0.35,
            ("scheme:E2",),
        ),
    )


def demand() -> ProjectDemandParameters:
    return ProjectDemandParameters(
        version="project-demand/1.0",
        initial_customer_pool=180,
        monthly_new_leads=45,
        visit_to_subscription_rate=0.18,
        subscription_to_contract_rate=0.86,
        monthly_pool_decay_rate=0.08,
        monthly_channel_capacity=18,
        seasonality=(0.8, 0.9, 1, 1.05, 1.1, 0.95, 0.85, 0.9, 1, 1.1, 1.2, 0.9),
        tail_decay_rate=0.45,
        evidence_refs=("demand:E1",),
    )


def costs() -> ProjectCostParameters:
    return ProjectCostParameters(
        land_cost_cny=80_000_000,
        construction_cost_cny_m2=5_000,
        design_incremental_cost_cny=3_000_000,
        monthly_marketing_cost_cny=800_000,
        sales_tax_rate=0.05,
        monthly_financing_rate=0.006,
        evidence_refs=("cost:E1",),
    )


def test_project_inventory_and_monthly_flows_reconcile() -> None:
    result = ProjectCashFlowEngine().simulate(
        batches(),
        demand(),
        costs(),
        assumptions=ProjectSimulationAssumptions(horizon_months=30),
    )

    assert result.total_released_units == 200
    assert (
        result.total_contracts
        + result.ending_available_inventory
        + result.ending_reserved_inventory
        == result.total_released_units
    )
    for month in result.months:
        assert month.subscriptions >= 0
        assert month.cancellations >= 0
        assert month.contracts >= 0
        assert month.cash_collections_cny >= 0
        assert month.accounting_revenue_cny >= 0
        for product in month.products:
            assert (
                product.opening_available_inventory
                + product.released_units
                + product.cancellations
                - product.subscriptions
                == product.closing_available_inventory
            )


def test_sales_cash_and_accounting_revenue_are_separate_lagged_series() -> None:
    result = ProjectCashFlowEngine().simulate(
        batches(),
        demand(),
        costs(),
        assumptions=ProjectSimulationAssumptions(
            horizon_months=24,
            signing_lag_months=1,
            collection_lag_months=2,
            revenue_recognition_lag_months=4,
        ),
    )

    first_contract_month = next(
        item.month for item in result.months if item.contracts > 0
    )
    first_cash_month = next(
        item.month for item in result.months if item.cash_collections_cny > 0
    )
    first_revenue_month = next(
        item.month for item in result.months if item.accounting_revenue_cny > 0
    )
    assert first_contract_month < first_cash_month < first_revenue_month
    assert result.contracted_sales_value_cny != result.cash_collections_cny
    assert result.cash_collections_cny != result.accounting_revenue_cny


def test_design_incremental_cost_is_charged_exactly_once() -> None:
    result = ProjectCashFlowEngine().simulate(
        batches(),
        demand(),
        costs(),
    )
    recorded = sum(
        (item.design_incremental_cost_cny for item in result.months),
        Decimal("0"),
    )
    assert recorded == Decimal("3000000.00")


def test_project_model_is_not_linear_standard_100_scaling() -> None:
    result = ProjectCashFlowEngine().simulate(
        batches(),
        demand(),
        costs(),
        assumptions=ProjectSimulationAssumptions(horizon_months=12),
    )
    monthly_contracts = [item.contracts for item in result.months]

    assert max(monthly_contracts) <= Decimal("18")
    assert result.total_contracts < Decimal("200")
    assert "customer_pool_conversion" in result.formula
    assert "channel_capacity" in result.formula
    assert "tail_effect" in result.formula
