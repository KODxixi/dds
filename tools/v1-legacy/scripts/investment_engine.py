"""Auditable, fail-closed real-estate investment modelling.

The engine deliberately has no project defaults.  A decision case is either
fully specified by evidence supplied by the caller, or it is blocked.  All
cash flows are unlevered project cash flows and use CNY 10,000 ("wan") as the
currency unit.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Iterable, Mapping, Sequence


EVIDENCE_TYPE = "model_simulation"

REQUIRED_FIELDS = (
    "saleable_area_sqm",
    "gfa_sqm",
    "sale_price_cny_sqm",
    "land_cost_wan",
    "construction_cost_cny_sqm",
    "construction_months",
    "sales_start_month",
    "sales_months",
    "tax_rate",
    "other_cost_rate",
    "target_profit_margin",
)

SCENARIO_FACTORS: dict[str, dict[str, float]] = {
    "conservative": {
        "price_factor": 0.90,
        "absorption_factor": 0.80,
        "construction_cost_factor": 1.08,
    },
    "base": {
        "price_factor": 1.00,
        "absorption_factor": 1.00,
        "construction_cost_factor": 1.00,
    },
    "optimistic": {
        "price_factor": 1.08,
        "absorption_factor": 1.20,
        "construction_cost_factor": 0.97,
    },
}

_PROHIBITED_FIELD_TOKENS = (
    "fengshui",
    "traditional",
    "wuxing",
    "bagua",
    "xuankong",
    "bazi",
    "风水",
    "五行",
    "八卦",
    "玄空",
    "飞星",
    "八宅",
    "命卦",
    "择日",
    "吉凶",
    "龙脉",
    "财位",
    "煞气",
)

_SOCIAL_APPENDIX_FIELDS = (
    "market_perception_risk",
    "social_intelligence",
    "social_observations",
    "persona_evidence_profiles",
)


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _find_prohibited_fields(value: Any, path: str = "inputs") -> list[str]:
    """Return paths whose *field names* contain traditional-culture tokens."""

    matches: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            folded = key.casefold()
            if any(token.casefold() in folded for token in _PROHIBITED_FIELD_TOKENS):
                matches.append(child_path)
            matches.extend(_find_prohibited_fields(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            matches.extend(_find_prohibited_fields(child, f"{path}[{index}]"))
    return matches


def _validation_gaps(inputs: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in inputs or inputs[field] is None:
            gaps.append(field)
            errors.append(f"missing required evidence: {field}")

    if gaps:
        return gaps, errors

    positive_fields = (
        "saleable_area_sqm",
        "gfa_sqm",
        "sale_price_cny_sqm",
        "construction_cost_cny_sqm",
    )
    for field in positive_fields:
        value = inputs[field]
        if not _is_finite_number(value) or float(value) <= 0:
            gaps.append(field)
            errors.append(f"{field} must be a finite number greater than zero")

    land_cost = inputs["land_cost_wan"]
    if not _is_finite_number(land_cost) or float(land_cost) < 0:
        gaps.append("land_cost_wan")
        errors.append("land_cost_wan must be a finite non-negative number")

    for field in ("construction_months", "sales_months"):
        value = inputs[field]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            gaps.append(field)
            errors.append(f"{field} must be a positive integer")

    sales_start = inputs["sales_start_month"]
    if (
        not isinstance(sales_start, int)
        or isinstance(sales_start, bool)
        or sales_start < 0
    ):
        gaps.append("sales_start_month")
        errors.append("sales_start_month must be a non-negative integer")

    for field in ("tax_rate", "other_cost_rate", "target_profit_margin"):
        value = inputs[field]
        if not _is_finite_number(value) or not 0 <= float(value) < 1:
            gaps.append(field)
            errors.append(f"{field} must be a finite ratio in [0, 1)")

    if "tax_rate" not in gaps and "other_cost_rate" not in gaps:
        if float(inputs["tax_rate"]) + float(inputs["other_cost_rate"]) >= 1:
            gaps.extend(("tax_rate", "other_cost_rate"))
            errors.append("tax_rate + other_cost_rate must be less than 1")

    # Preserve the required-field order and avoid duplicates from cross-field checks.
    ordered_gaps = [field for field in REQUIRED_FIELDS if field in set(gaps)]
    return ordered_gaps, errors


def _blocked_result(
    evidence_gaps: Sequence[str],
    validation_errors: Sequence[str],
    source_refs: Sequence[Any],
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "evidence_type": EVIDENCE_TYPE,
        "evidence_gaps": list(evidence_gaps),
        "validation_errors": list(validation_errors),
        "assumptions": {},
        "source_refs": deepcopy(list(source_refs)),
        "evidence_confidence": 0.0,
        "simulation_stability": 0.0,
        "limitations": [
            "Decision metrics are intentionally suppressed until every required input is evidenced."
        ],
        "scenarios": {},
        "roi": None,
        "unlevered_project_irr": None,
        "land_bid_boundaries": None,
        "market_perception_risk": None,
        "non_financial_appendix": {},
    }


def _normalise_source_refs(source_refs: Iterable[Any] | None) -> list[Any]:
    if source_refs is None:
        return []
    if isinstance(source_refs, (str, bytes)):
        return [source_refs]
    try:
        return deepcopy(list(source_refs))
    except TypeError:
        return [deepcopy(source_refs)]


def _npv(rate: float, cash_flows: Sequence[float]) -> float:
    if rate <= -1:
        return math.nan
    divisor = 1.0 + rate
    value = float(cash_flows[-1])
    for cash_flow in reversed(cash_flows[:-1]):
        # Horner evaluation is more stable than independently exponentiating each
        # period.  Near -100%, an overflow has the sign of the accumulated tail.
        if divisor < 1 and abs(value) > 1e307 * divisor:
            return math.copysign(math.inf, value)
        value = float(cash_flow) + value / divisor
    return value


def _sign_changes(cash_flows: Sequence[float]) -> int:
    signs: list[int] = []
    for value in cash_flows:
        if abs(value) <= 1e-12:
            continue
        signs.append(1 if value > 0 else -1)
    return sum(left != right for left, right in zip(signs, signs[1:]))


def solve_unlevered_irr(cash_flows: Sequence[float]) -> dict[str, Any]:
    """Solve monthly IRR by bracketing and bisection, never by Newton iteration.

    Non-conventional cash flows are not assigned an arbitrary root.  Multiple
    sign changes, the absence of a sign change, a missing numerical bracket, and
    annualized results over 100% are all explicitly routed to review.
    """

    method = "bracketed_bisection"
    try:
        values = [float(value) for value in cash_flows]
    except (TypeError, ValueError):
        values = []

    if len(values) < 2 or not all(math.isfinite(value) for value in values):
        return {
            "status": "review",
            "reason": "invalid_cash_flow_series",
            "monthly_irr": None,
            "annualized_irr": None,
            "sign_change_count": None,
            "method": method,
        }

    change_count = _sign_changes(values)
    if change_count == 0:
        return {
            "status": "review",
            "reason": "no_cash_flow_sign_change",
            "monthly_irr": None,
            "annualized_irr": None,
            "sign_change_count": change_count,
            "method": method,
        }
    if change_count > 1:
        return {
            "status": "review",
            "reason": "multiple_cash_flow_sign_changes",
            "monthly_irr": None,
            "annualized_irr": None,
            "sign_change_count": change_count,
            "method": method,
        }

    # The grid deliberately spans almost -100% monthly to extremely high
    # positive rates.  Adjacent sign changes provide a genuine numerical bracket.
    search_grid = (
        -0.999999,
        -0.999,
        -0.99,
        -0.95,
        -0.90,
        -0.75,
        -0.50,
        -0.25,
        -0.10,
        -0.05,
        -0.01,
        0.0,
        0.01,
        0.025,
        0.05,
        0.10,
        0.20,
        0.50,
        1.0,
        2.0,
        5.0,
        10.0,
        20.0,
        50.0,
        100.0,
        200.0,
        500.0,
        1_000.0,
    )
    bracket: tuple[float, float] | None = None
    previous_rate = search_grid[0]
    previous_value = _npv(previous_rate, values)
    if previous_value == 0:
        bracket = (previous_rate, previous_rate)
    else:
        for rate in search_grid[1:]:
            current_value = _npv(rate, values)
            if current_value == 0:
                bracket = (rate, rate)
                break
            if (
                not math.isnan(previous_value)
                and not math.isnan(current_value)
                and (previous_value < 0 < current_value or current_value < 0 < previous_value)
            ):
                bracket = (previous_rate, rate)
                break
            previous_rate = rate
            previous_value = current_value

    if bracket is None:
        return {
            "status": "review",
            "reason": "no_numerical_bracket",
            "monthly_irr": None,
            "annualized_irr": None,
            "sign_change_count": change_count,
            "method": method,
        }

    low, high = bracket
    if low == high:
        monthly_irr = low
    else:
        low_value = _npv(low, values)
        for _ in range(200):
            midpoint = (low + high) / 2.0
            midpoint_value = _npv(midpoint, values)
            if abs(midpoint_value) <= 1e-10 or high - low <= 1e-12:
                low = high = midpoint
                break
            if (low_value < 0 < midpoint_value) or (midpoint_value < 0 < low_value):
                high = midpoint
            else:
                low = midpoint
                low_value = midpoint_value
        monthly_irr = (low + high) / 2.0

    annualized_irr = (1.0 + monthly_irr) ** 12 - 1.0
    if not math.isfinite(annualized_irr):
        return {
            "status": "review",
            "reason": "non_finite_annualized_irr",
            "monthly_irr": monthly_irr,
            "annualized_irr": None,
            "sign_change_count": change_count,
            "method": method,
        }

    status = "ok"
    reason = None
    if annualized_irr > 1.0:
        status = "review"
        reason = "annualized_irr_above_100_percent"

    return {
        "status": status,
        "reason": reason,
        "monthly_irr": monthly_irr,
        "annualized_irr": annualized_irr,
        "sign_change_count": change_count,
        "method": method,
        "bracket": [bracket[0], bracket[1]],
    }


def _monthly_cash_flow(
    *,
    land_cost_wan: float,
    construction_cost_wan: float,
    construction_months: int,
    sales_start_month: int,
    sales_duration_months: int,
    revenue_wan: float,
    tax_rate: float,
    other_cost_rate: float,
) -> list[dict[str, Any]]:
    last_month = max(
        construction_months - 1,
        sales_start_month + sales_duration_months - 1,
    )
    monthly_construction = construction_cost_wan / construction_months
    monthly_revenue = revenue_wan / sales_duration_months
    rows: list[dict[str, Any]] = []
    cumulative = 0.0

    for month in range(last_month + 1):
        revenue = (
            monthly_revenue
            if sales_start_month <= month < sales_start_month + sales_duration_months
            else 0.0
        )
        land = land_cost_wan if month == 0 else 0.0
        construction = monthly_construction if month < construction_months else 0.0
        tax = revenue * tax_rate
        other = revenue * other_cost_rate
        net = revenue - land - construction - tax - other
        cumulative += net
        rows.append(
            {
                "month": month,
                "sales_revenue_wan": revenue,
                "land_cost_wan": land,
                "construction_cost_wan": construction,
                "tax_wan": tax,
                "other_cost_wan": other,
                "net_cash_flow_wan": net,
                "cumulative_cash_flow_wan": cumulative,
            }
        )
    return rows


def _payback_month(rows: Sequence[Mapping[str, float]]) -> int | None:
    has_been_negative = False
    for row in rows:
        cumulative = float(row["cumulative_cash_flow_wan"])
        if cumulative < 0:
            has_been_negative = True
        elif has_been_negative:
            return int(row["month"])
    return None


def _build_scenario(
    name: str,
    factors: Mapping[str, float],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    price = float(inputs["sale_price_cny_sqm"]) * factors["price_factor"]
    sales_duration = max(
        1,
        math.ceil(float(inputs["sales_months"]) / factors["absorption_factor"]),
    )
    revenue = float(inputs["saleable_area_sqm"]) * price / 10_000.0
    construction_cost = (
        float(inputs["gfa_sqm"])
        * float(inputs["construction_cost_cny_sqm"])
        * factors["construction_cost_factor"]
        / 10_000.0
    )
    land_cost = float(inputs["land_cost_wan"])
    tax = revenue * float(inputs["tax_rate"])
    other_cost = revenue * float(inputs["other_cost_rate"])
    total_cost = land_cost + construction_cost + tax + other_cost
    profit = revenue - total_cost
    margin = profit / revenue
    roi = profit / total_cost if total_cost else None
    project_multiple = revenue / total_cost if total_cost else None

    cash_flow_rows = _monthly_cash_flow(
        land_cost_wan=land_cost,
        construction_cost_wan=construction_cost,
        construction_months=int(inputs["construction_months"]),
        sales_start_month=int(inputs["sales_start_month"]),
        sales_duration_months=sales_duration,
        revenue_wan=revenue,
        tax_rate=float(inputs["tax_rate"]),
        other_cost_rate=float(inputs["other_cost_rate"]),
    )
    irr = solve_unlevered_irr([row["net_cash_flow_wan"] for row in cash_flow_rows])
    metrics = {
        "evidence_type": EVIDENCE_TYPE,
        "revenue_wan": revenue,
        "land_cost_wan": land_cost,
        "construction_cost_wan": construction_cost,
        "tax_wan": tax,
        "other_cost_wan": other_cost,
        "total_cost_wan": total_cost,
        "profit_wan": profit,
        "profit_margin": margin,
        "roi": roi,
        "project_multiple": project_multiple,
        "payback_period_months": _payback_month(cash_flow_rows),
        "monthly_unlevered_project_irr": irr["monthly_irr"],
        "unlevered_project_irr": irr["annualized_irr"],
        "irr_status": irr["status"],
        "irr_review_reason": irr["reason"],
        "irr_method": irr["method"],
        "cash_flow_sign_change_count": irr["sign_change_count"],
    }
    return {
        "scenario": name,
        "evidence_type": EVIDENCE_TYPE,
        "factors": dict(factors),
        "scenario_sale_price_cny_sqm": price,
        "sales_duration_months": sales_duration,
        "metrics": metrics,
        "monthly_cash_flow": cash_flow_rows,
    }


def _land_bid_boundaries(inputs: Mapping[str, Any]) -> dict[str, Any]:
    revenue = (
        float(inputs["saleable_area_sqm"])
        * float(inputs["sale_price_cny_sqm"])
        / 10_000.0
    )
    construction_cost = (
        float(inputs["gfa_sqm"])
        * float(inputs["construction_cost_cny_sqm"])
        / 10_000.0
    )
    revenue_cost_rate = float(inputs["tax_rate"]) + float(inputs["other_cost_rate"])
    residual_before_land = revenue * (1.0 - revenue_cost_rate)
    break_even_land = residual_before_land - construction_cost
    target_land = (
        revenue
        * (
            1.0
            - revenue_cost_rate
            - float(inputs["target_profit_margin"])
        )
        - construction_cost
    )
    break_even_revenue = (
        float(inputs["land_cost_wan"]) + construction_cost
    ) / (1.0 - revenue_cost_rate)
    gfa = float(inputs["gfa_sqm"])
    saleable_area = float(inputs["saleable_area_sqm"])
    return {
        "evidence_type": EVIDENCE_TYPE,
        "formula_basis": {
            "tax_and_other_cost_basis": "gross_sales_revenue",
            "construction_cost_basis": "GFA",
            "land_floor_price_basis": "GFA",
            "target_profit_margin_basis": "gross_sales_revenue",
        },
        "break_even_sale_price_cny_sqm": break_even_revenue * 10_000.0 / saleable_area,
        "break_even_land_cost_wan": break_even_land,
        "break_even_land_floor_price_cny_sqm": break_even_land * 10_000.0 / gfa,
        "target_profit_margin": float(inputs["target_profit_margin"]),
        "target_margin_max_land_cost_wan": target_land,
        "target_margin_max_land_floor_price_cny_sqm": target_land * 10_000.0 / gfa,
        "break_even_land_is_non_negative": break_even_land >= 0,
        "target_margin_land_is_non_negative": target_land >= 0,
    }


def _evidence_confidence(source_refs: Sequence[Any]) -> float:
    # Source count is only a conservative ceiling; the caller must still supply
    # provenance quality in the referenced evidence records.
    if not source_refs:
        return 0.20
    if len(source_refs) == 1:
        return 0.35
    if len(source_refs) == 2:
        return 0.50
    return 0.65


def build_investment_case(
    inputs: Mapping[str, Any],
    source_refs: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Build three auditable, unlevered investment scenarios.

    Social evidence is copied into a non-financial appendix only.  Traditional
    culture fields fail closed because they are prohibited from pricing, IRR,
    ROI, and land-bid formulas.
    """

    sources = _normalise_source_refs(source_refs)
    if not isinstance(inputs, Mapping):
        return _blocked_result(
            REQUIRED_FIELDS,
            ["inputs must be a mapping"],
            sources,
        )

    prohibited_paths = _find_prohibited_fields(inputs)
    if prohibited_paths:
        gaps = [f"prohibited_input:{path}" for path in prohibited_paths]
        return _blocked_result(
            gaps,
            [
                "Traditional-culture fields are prohibited from financial decision inputs: "
                + ", ".join(prohibited_paths)
            ],
            sources,
        )

    gaps, validation_errors = _validation_gaps(inputs)
    if gaps:
        return _blocked_result(gaps, validation_errors, sources)

    scenarios = {
        name: _build_scenario(name, factors, inputs)
        for name, factors in SCENARIO_FACTORS.items()
    }
    boundaries = _land_bid_boundaries(inputs)
    any_irr_review = any(
        scenario["metrics"]["irr_status"] == "review"
        for scenario in scenarios.values()
    )

    appendix = {
        field: deepcopy(inputs[field])
        for field in _SOCIAL_APPENDIX_FIELDS
        if field in inputs
    }
    limitations = [
        "Unlevered project model: financing, leverage, interest, and equity waterfalls are excluded.",
        "Construction spending and sales absorption are distributed evenly within their stated windows.",
        "Taxes and other costs are modelled as ratios of gross sales revenue.",
        "Scenario results are deterministic simulations, not observed future outcomes.",
        "Social evidence is appendix-only and does not alter price, WTP, IRR, ROI, or land boundaries.",
    ]
    if not sources:
        limitations.append(
            "Source refs are missing; evidence confidence is capped at a low level."
        )
    if any_irr_review:
        limitations.append(
            "At least one scenario has an IRR result that requires human review."
        )

    base_metrics = scenarios["base"]["metrics"]
    confidence = _evidence_confidence(sources)
    stability = 0.70 if any_irr_review else 0.90
    assumptions = {field: deepcopy(inputs[field]) for field in REQUIRED_FIELDS}
    assumptions.update(
        {
            "currency_unit": "CNY 10,000",
            "cash_flow_frequency": "monthly",
            "irr_basis": "unlevered_project_cash_flow",
            "irr_annualization": "(1 + monthly_irr) ^ 12 - 1",
            "tax_and_other_cost_basis": "gross_sales_revenue",
            "construction_cost_basis": "GFA",
            "sales_recognition": "only within the scenario sales window; no unsold value is accelerated",
            "scenario_factors": deepcopy(SCENARIO_FACTORS),
        }
    )

    return {
        "status": "review" if any_irr_review else "ready",
        "evidence_type": EVIDENCE_TYPE,
        "evidence_gaps": [],
        "validation_errors": [],
        "assumptions": assumptions,
        "source_refs": sources,
        "evidence_confidence": confidence,
        "simulation_stability": stability,
        "limitations": limitations,
        "scenarios": scenarios,
        "roi": base_metrics["roi"],
        "unlevered_project_irr": base_metrics["unlevered_project_irr"],
        "land_bid_boundaries": boundaries,
        "market_perception_risk": deepcopy(inputs.get("market_perception_risk")),
        "non_financial_appendix": appendix,
    }


__all__ = ["build_investment_case", "solve_unlevered_irr"]
