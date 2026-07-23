"""Input 2 project inventory, sales conversion, and monthly cash flow."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence


_MONEY = Decimal("0.01")
_UNITS = Decimal("0.0001")


def _d(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _units(value: Decimal) -> Decimal:
    return value.quantize(_UNITS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class ProductBatch:
    batch_id: str
    product_id: str
    release_month: int
    units: Decimal | float | int | str
    unit_area_m2: Decimal | float | str
    unit_price_cny_m2: Decimal | float | str
    demand_weight: Decimal | float | str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.batch_id.strip() or not self.product_id.strip():
            raise ValueError("batch_id and product_id are required")
        if self.release_month < 1:
            raise ValueError("release_month must be positive")
        if _d(self.units) <= 0 or _d(self.unit_area_m2) <= 0:
            raise ValueError("batch units and unit area must be positive")
        if _d(self.unit_price_cny_m2) <= 0:
            raise ValueError("unit price must be positive")
        if _d(self.demand_weight) <= 0:
            raise ValueError("demand_weight must be positive")
        if not self.evidence_refs:
            raise ValueError("batch inputs require evidence refs")

    @property
    def unit_value_cny(self) -> Decimal:
        return _money(_d(self.unit_area_m2) * _d(self.unit_price_cny_m2))


@dataclass(frozen=True, slots=True)
class ProjectDemandParameters:
    version: str
    initial_customer_pool: Decimal | float | str
    monthly_new_leads: Decimal | float | str
    visit_to_subscription_rate: Decimal | float | str
    subscription_to_contract_rate: Decimal | float | str
    monthly_pool_decay_rate: Decimal | float | str
    monthly_channel_capacity: Decimal | float | str
    seasonality: tuple[Decimal | float | str, ...]
    tail_decay_rate: Decimal | float | str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.evidence_refs:
            raise ValueError("demand version and evidence refs are required")
        if _d(self.initial_customer_pool) < 0 or _d(self.monthly_new_leads) < 0:
            raise ValueError("customer pool inputs must be non-negative")
        for name, value in (
            ("visit_to_subscription_rate", self.visit_to_subscription_rate),
            ("subscription_to_contract_rate", self.subscription_to_contract_rate),
            ("monthly_pool_decay_rate", self.monthly_pool_decay_rate),
            ("tail_decay_rate", self.tail_decay_rate),
        ):
            if not Decimal("0") <= _d(value) <= Decimal("1"):
                raise ValueError(f"{name} must be within [0, 1]")
        if _d(self.monthly_channel_capacity) <= 0:
            raise ValueError("monthly_channel_capacity must be positive")
        if len(self.seasonality) != 12 or any(_d(item) <= 0 for item in self.seasonality):
            raise ValueError("seasonality must contain 12 positive multipliers")


@dataclass(frozen=True, slots=True)
class ProjectCostParameters:
    land_cost_cny: Decimal | float | str
    construction_cost_cny_m2: Decimal | float | str
    design_incremental_cost_cny: Decimal | float | str
    monthly_marketing_cost_cny: Decimal | float | str
    sales_tax_rate: Decimal | float | str
    monthly_financing_rate: Decimal | float | str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        values = (
            self.land_cost_cny,
            self.construction_cost_cny_m2,
            self.design_incremental_cost_cny,
            self.monthly_marketing_cost_cny,
            self.sales_tax_rate,
            self.monthly_financing_rate,
        )
        if any(_d(item) < 0 for item in values):
            raise ValueError("cost and rate inputs must be non-negative")
        if _d(self.sales_tax_rate) >= 1:
            raise ValueError("sales_tax_rate must be below 1")
        if not self.evidence_refs:
            raise ValueError("cost inputs require evidence refs")


@dataclass(frozen=True, slots=True)
class ProjectSimulationAssumptions:
    horizon_months: int = 36
    signing_lag_months: int = 1
    collection_lag_months: int = 2
    revenue_recognition_lag_months: int = 3
    monthly_discount_rate: Decimal | float | str = Decimal("0.006")
    model_version: str = "dds.project-cashflow/1.0"

    def __post_init__(self) -> None:
        if self.horizon_months < 12:
            raise ValueError("horizon_months must be at least 12")
        if min(
            self.signing_lag_months,
            self.collection_lag_months,
            self.revenue_recognition_lag_months,
        ) < 0:
            raise ValueError("lag months must be non-negative")
        if _d(self.monthly_discount_rate) < 0:
            raise ValueError("monthly_discount_rate must be non-negative")
        if not self.model_version.strip():
            raise ValueError("model_version is required")


@dataclass(frozen=True, slots=True)
class MonthlyProductResult:
    product_id: str
    opening_available_inventory: Decimal
    released_units: Decimal
    subscriptions: Decimal
    cancellations: Decimal
    contracts: Decimal
    closing_available_inventory: Decimal
    closing_reserved_inventory: Decimal
    cumulative_contracts: Decimal


@dataclass(frozen=True, slots=True)
class ProjectMonthlyResult:
    month: int
    customer_pool_opening: Decimal
    new_leads: Decimal
    customer_pool_closing: Decimal
    subscriptions: Decimal
    cancellations: Decimal
    contracts: Decimal
    contracted_sales_value_cny: Decimal
    cash_collections_cny: Decimal
    accounting_revenue_cny: Decimal
    land_cost_cny: Decimal
    construction_cost_cny: Decimal
    design_incremental_cost_cny: Decimal
    marketing_cost_cny: Decimal
    sales_tax_cny: Decimal
    financing_cost_cny: Decimal
    net_cash_flow_cny: Decimal
    cumulative_net_cash_cny: Decimal
    discounted_net_cash_cny: Decimal
    products: tuple[MonthlyProductResult, ...]


@dataclass(frozen=True, slots=True)
class ProjectCashFlowResult:
    parameter_version: str
    model_version: str
    months: tuple[ProjectMonthlyResult, ...]
    total_released_units: Decimal
    total_contracts: Decimal
    ending_available_inventory: Decimal
    ending_reserved_inventory: Decimal
    contracted_sales_value_cny: Decimal
    cash_collections_cny: Decimal
    accounting_revenue_cny: Decimal
    net_cash_npv_cny: Decimal
    peak_funding_requirement_cny: Decimal
    evidence_refs: tuple[str, ...]
    formula: str
    evidence_type: str = "model_simulation"


@dataclass(slots=True)
class _ProductState:
    available: Decimal = Decimal("0")
    released: Decimal = Decimal("0")
    contracted: Decimal = Decimal("0")
    reserved_cohorts: list[tuple[int, Decimal]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.reserved_cohorts is None:
            self.reserved_cohorts = []

    @property
    def reserved(self) -> Decimal:
        return _units(sum((item[1] for item in self.reserved_cohorts), Decimal("0")))


class ProjectCashFlowEngine:
    """Simulate sourced project batches; never scale the standard-100 result."""

    def simulate(
        self,
        batches: Sequence[ProductBatch],
        demand: ProjectDemandParameters,
        costs: ProjectCostParameters,
        *,
        assumptions: ProjectSimulationAssumptions | None = None,
    ) -> ProjectCashFlowResult:
        if not batches:
            raise ValueError("at least one product batch is required")
        if len({item.batch_id for item in batches}) != len(batches):
            raise ValueError("batch_id values must be unique")
        config = assumptions or ProjectSimulationAssumptions()
        batches_by_month: dict[int, list[ProductBatch]] = {}
        product_batches: dict[str, list[ProductBatch]] = {}
        for batch in batches:
            batches_by_month.setdefault(batch.release_month, []).append(batch)
            product_batches.setdefault(batch.product_id, []).append(batch)
        states = {product_id: _ProductState() for product_id in product_batches}
        contract_value_queue: dict[int, Decimal] = {}
        accounting_queue: dict[int, Decimal] = {}
        customer_pool = _d(demand.initial_customer_pool)
        cumulative_cash = Decimal("0")
        cumulative_discounted = Decimal("0")
        peak_funding = Decimal("0")
        monthly_results: list[ProjectMonthlyResult] = []
        design_cost_recorded = False

        for month in range(1, config.horizon_months + 1):
            opening_pool = customer_pool
            seasonal = _d(demand.seasonality[(month - 1) % 12])
            new_leads = _units(_d(demand.monthly_new_leads) * seasonal)
            customer_pool = _units(
                customer_pool
                * (Decimal("1") - _d(demand.monthly_pool_decay_rate))
                + new_leads
            )
            released_by_product: dict[str, Decimal] = {}
            construction_cost = Decimal("0")
            for batch in batches_by_month.get(month, []):
                released = _units(_d(batch.units))
                state = states[batch.product_id]
                state.available = _units(state.available + released)
                state.released = _units(state.released + released)
                released_by_product[batch.product_id] = _units(
                    released_by_product.get(batch.product_id, Decimal("0"))
                    + released
                )
                construction_cost += (
                    released
                    * _d(batch.unit_area_m2)
                    * _d(costs.construction_cost_cny_m2)
                )

            product_rows: list[MonthlyProductResult] = []
            total_subscriptions = Decimal("0")
            total_cancellations = Decimal("0")
            total_contracts = Decimal("0")
            contracted_value = Decimal("0")
            available_products = [
                product_id
                for product_id, state in states.items()
                if state.available > 0
            ]
            available_weight = sum(
                (
                    _d(product_batches[product_id][0].demand_weight)
                    for product_id in available_products
                ),
                Decimal("0"),
            )
            pool_demand = (
                customer_pool
                * _d(demand.visit_to_subscription_rate)
            )
            channel_capacity = _d(demand.monthly_channel_capacity)

            for product_id, state in states.items():
                opening_available = state.available - released_by_product.get(
                    product_id, Decimal("0")
                )
                cancellations = Decimal("0")
                contracts = Decimal("0")
                pending: list[tuple[int, Decimal]] = []
                for due_month, reserved_units in state.reserved_cohorts:
                    if due_month > month:
                        pending.append((due_month, reserved_units))
                        continue
                    contracted = _units(
                        reserved_units
                        * _d(demand.subscription_to_contract_rate)
                    )
                    cancelled = _units(reserved_units - contracted)
                    contracts += contracted
                    cancellations += cancelled
                    state.available = _units(state.available + cancelled)
                    state.contracted = _units(state.contracted + contracted)
                    batch = product_batches[product_id][0]
                    value = contracted * batch.unit_value_cny
                    contracted_value += value
                    contract_value_queue[
                        month + config.collection_lag_months
                    ] = contract_value_queue.get(
                        month + config.collection_lag_months,
                        Decimal("0"),
                    ) + value
                    accounting_queue[
                        month + config.revenue_recognition_lag_months
                    ] = accounting_queue.get(
                        month + config.revenue_recognition_lag_months,
                        Decimal("0"),
                    ) + value
                state.reserved_cohorts = pending

                subscriptions = Decimal("0")
                if state.available > 0 and available_weight > 0:
                    weight = (
                        _d(product_batches[product_id][0].demand_weight)
                        / available_weight
                    )
                    sold_fraction = (
                        state.contracted / state.released
                        if state.released > 0
                        else Decimal("0")
                    )
                    tail_factor = max(
                        Decimal("0.2"),
                        Decimal("1")
                        - _d(demand.tail_decay_rate) * sold_fraction,
                    )
                    subscriptions = _units(
                        min(
                            state.available,
                            pool_demand * weight * tail_factor,
                            channel_capacity * weight,
                        )
                    )
                    state.available = _units(state.available - subscriptions)
                    due = month + config.signing_lag_months
                    state.reserved_cohorts.append((due, subscriptions))

                total_subscriptions += subscriptions
                total_cancellations += cancellations
                total_contracts += contracts
                product_rows.append(
                    MonthlyProductResult(
                        product_id=product_id,
                        opening_available_inventory=_units(opening_available),
                        released_units=released_by_product.get(
                            product_id, Decimal("0")
                        ),
                        subscriptions=subscriptions,
                        cancellations=_units(cancellations),
                        contracts=_units(contracts),
                        closing_available_inventory=state.available,
                        closing_reserved_inventory=state.reserved,
                        cumulative_contracts=state.contracted,
                    )
                )

            customer_pool = _units(
                max(Decimal("0"), customer_pool - total_subscriptions)
            )
            cash_collections = _money(contract_value_queue.get(month, Decimal("0")))
            accounting_revenue = _money(accounting_queue.get(month, Decimal("0")))
            land_cost = _money(_d(costs.land_cost_cny) if month == 1 else Decimal("0"))
            design_cost = _money(
                _d(costs.design_incremental_cost_cny)
                if not design_cost_recorded and released_by_product
                else Decimal("0")
            )
            if design_cost:
                design_cost_recorded = True
            construction_cost = _money(construction_cost)
            marketing_cost = _money(_d(costs.monthly_marketing_cost_cny))
            sales_tax = _money(
                cash_collections * _d(costs.sales_tax_rate)
            )
            pre_finance_cash = (
                cash_collections
                - land_cost
                - construction_cost
                - design_cost
                - marketing_cost
                - sales_tax
            )
            financing_cost = _money(
                max(Decimal("0"), -cumulative_cash)
                * _d(costs.monthly_financing_rate)
            )
            net_cash = _money(pre_finance_cash - financing_cost)
            cumulative_cash = _money(cumulative_cash + net_cash)
            discounted = _money(
                net_cash
                / (
                    (Decimal("1") + _d(config.monthly_discount_rate))
                    ** month
                )
            )
            cumulative_discounted = _money(
                cumulative_discounted + discounted
            )
            peak_funding = max(peak_funding, -cumulative_cash)
            monthly_results.append(
                ProjectMonthlyResult(
                    month=month,
                    customer_pool_opening=_units(opening_pool),
                    new_leads=new_leads,
                    customer_pool_closing=customer_pool,
                    subscriptions=_units(total_subscriptions),
                    cancellations=_units(total_cancellations),
                    contracts=_units(total_contracts),
                    contracted_sales_value_cny=_money(contracted_value),
                    cash_collections_cny=cash_collections,
                    accounting_revenue_cny=accounting_revenue,
                    land_cost_cny=land_cost,
                    construction_cost_cny=construction_cost,
                    design_incremental_cost_cny=design_cost,
                    marketing_cost_cny=marketing_cost,
                    sales_tax_cny=sales_tax,
                    financing_cost_cny=financing_cost,
                    net_cash_flow_cny=net_cash,
                    cumulative_net_cash_cny=cumulative_cash,
                    discounted_net_cash_cny=discounted,
                    products=tuple(product_rows),
                )
            )

        total_released = _units(
            sum((state.released for state in states.values()), Decimal("0"))
        )
        total_contracts = _units(
            sum((state.contracted for state in states.values()), Decimal("0"))
        )
        ending_available = _units(
            sum((state.available for state in states.values()), Decimal("0"))
        )
        ending_reserved = _units(
            sum((state.reserved for state in states.values()), Decimal("0"))
        )
        all_refs = tuple(
            dict.fromkeys(
                [
                    *demand.evidence_refs,
                    *costs.evidence_refs,
                    *(
                        ref
                        for batch in batches
                        for ref in batch.evidence_refs
                    ),
                ]
            )
        )
        return ProjectCashFlowResult(
            parameter_version=demand.version,
            model_version=config.model_version,
            months=tuple(monthly_results),
            total_released_units=total_released,
            total_contracts=total_contracts,
            ending_available_inventory=ending_available,
            ending_reserved_inventory=ending_reserved,
            contracted_sales_value_cny=_money(
                sum(
                    (item.contracted_sales_value_cny for item in monthly_results),
                    Decimal("0"),
                )
            ),
            cash_collections_cny=_money(
                sum(
                    (item.cash_collections_cny for item in monthly_results),
                    Decimal("0"),
                )
            ),
            accounting_revenue_cny=_money(
                sum(
                    (item.accounting_revenue_cny for item in monthly_results),
                    Decimal("0"),
                )
            ),
            net_cash_npv_cny=cumulative_discounted,
            peak_funding_requirement_cny=_money(peak_funding),
            evidence_refs=all_refs,
            formula=(
                "released=available+reserved+contracted; "
                "subscriptions=min(inventory, customer_pool_conversion, "
                "channel_capacity)*seasonality*tail_effect; "
                "contracts=lagged_subscriptions*conversion; "
                "cash and accounting revenue use separate lags; "
                "net_cash=cash-costs-tax-financing"
            ),
        )


__all__ = [
    "MonthlyProductResult",
    "ProductBatch",
    "ProjectCashFlowEngine",
    "ProjectCashFlowResult",
    "ProjectCostParameters",
    "ProjectDemandParameters",
    "ProjectMonthlyResult",
    "ProjectSimulationAssumptions",
]
