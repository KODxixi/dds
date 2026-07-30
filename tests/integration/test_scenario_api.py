from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from dds.api.app import create_app
from dds.product.service import ProductSettings


def input_1_payload() -> dict:
    return {
        "market_parameters": {
            "city": "Test City",
            "version": "market/1.0",
            "base_monthly_demand_per_100": 6,
            "tail_decay_rate": 0.35,
            "supporting_evidence_refs": ["market:E1"],
            "calibration_method": "observed comparable absorption",
        },
        "strategies": [
            {
                "strategy_id": "fast",
                "positioning": "fast",
                "demand_multiplier": 1.3,
                "list_price_index": 94,
                "marketing_cost_index": 1.5,
                "value_investment_index": 0.5,
                "switch_triggers": ["slow launch"],
            },
            {
                "strategy_id": "balanced",
                "positioning": "balanced",
                "demand_multiplier": 1,
                "list_price_index": 104,
                "marketing_cost_index": 1,
                "value_investment_index": 1,
                "switch_triggers": ["weak conversion"],
            },
            {
                "strategy_id": "premium",
                "positioning": "premium",
                "demand_multiplier": 0.78,
                "list_price_index": 122,
                "marketing_cost_index": 1.2,
                "value_investment_index": 2,
                "switch_triggers": ["weak visits"],
            },
        ],
        "scenarios": [
            {
                "scenario_id": "conservative",
                "demand_multiplier": 0.72,
                "price_realization_rate": 0.96,
                "cancellation_rate": 0.08,
            },
            {
                "scenario_id": "base",
                "demand_multiplier": 1,
                "price_realization_rate": 1,
                "cancellation_rate": 0.04,
            },
            {
                "scenario_id": "optimistic",
                "demand_multiplier": 1.25,
                "price_realization_rate": 1.02,
                "cancellation_rate": 0.02,
            },
        ],
    }


def input_2_payload() -> dict:
    return {
        "batches": [
            {
                "batch_id": "phase-1",
                "product_id": "main",
                "release_month": 1,
                "units": 120,
                "unit_area_m2": 100,
                "unit_price_cny_m2": 20_000,
                "demand_weight": 0.65,
                "evidence_refs": ["scheme:E1"],
            },
            {
                "batch_id": "phase-2",
                "product_id": "value",
                "release_month": 7,
                "units": 80,
                "unit_area_m2": 135,
                "unit_price_cny_m2": 24_000,
                "demand_weight": 0.35,
                "evidence_refs": ["scheme:E2"],
            },
        ],
        "demand": {
            "version": "demand/1.0",
            "initial_customer_pool": 180,
            "monthly_new_leads": 45,
            "visit_to_subscription_rate": 0.18,
            "subscription_to_contract_rate": 0.86,
            "monthly_pool_decay_rate": 0.08,
            "monthly_channel_capacity": 18,
            "seasonality": [1] * 12,
            "tail_decay_rate": 0.45,
            "evidence_refs": ["demand:E1"],
        },
        "costs": {
            "land_cost_cny": 80_000_000,
            "construction_cost_cny_m2": 5_000,
            "design_incremental_cost_cny": 3_000_000,
            "monthly_marketing_cost_cny": 800_000,
            "sales_tax_rate": 0.05,
            "monthly_financing_rate": 0.006,
            "evidence_refs": ["cost:E1"],
        },
        "assumptions": {"horizon_months": 30},
    }


def scenario(
    scenario_id: str,
    probability: float,
    value: int,
    npv: int,
) -> dict:
    return {
        "scenario_id": scenario_id,
        "probability": probability,
        "absorption_12m": 0.65,
        "realized_value_cny": value,
        "cash_npv_cny": npv,
        "peak_funding_cny": 85_000_000,
        "tail_inventory_rate": 0.2,
        "evidence_refs": [f"outcome:{scenario_id}"],
        "source_model_version": "cashflow/1.0",
    }


def input_3_payload() -> dict:
    return {
        "schemes": [
            {
                "scheme_id": scheme_id,
                "planning_efficiency": 0.85,
                "product_market_fit": 0.82,
                "implementation_complexity": 0.45,
                "hard_constraints_passed": True,
                "hard_constraint_failures": [],
                "outcomes": [
                    scenario(
                        "conservative",
                        0.25,
                        180_000_000 + shift,
                        20_000_000 + shift,
                    ),
                    scenario(
                        "base",
                        0.5,
                        220_000_000 + shift,
                        45_000_000 + shift,
                    ),
                    scenario(
                        "optimistic",
                        0.25,
                        250_000_000 + shift,
                        70_000_000 + shift,
                    ),
                ],
                "evidence_refs": [f"scheme:{scheme_id}"],
            }
            for scheme_id, shift in (("A", 8_000_000), ("B", 0))
        ],
        "policy": {
            "cvar_alpha": 0.25,
            "risk_aversion_lambda": 0.5,
        },
    }


def confirmed_job(
    client: TestClient,
    *,
    level: int,
    profile: dict,
) -> dict:
    created = client.post(
        "/api/research-jobs",
        json={
            "project_name": f"Input {level} simulation",
            "city": "Test City",
            **profile,
        },
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["job_id"]
    confirmed = client.post(
        f"/api/research-jobs/{job_id}/intervention",
        json={
            "selected_mode": level,
            "user_goal": f"Run confirmed Input {level} simulation",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def test_simulation_api_rejects_self_reported_mode_without_frozen_job(tmp_path):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )

    response = client.post(
        "/api/analysis/simulate",
        json={
            "selected_mode": 1,
            "mode_confirmed": True,
            "input_profile": {"address": "Test City Road 1"},
            "payload": input_1_payload(),
        },
    )

    assert response.status_code == 422


def test_simulation_api_runs_only_with_job_bound_frozen_brief_hash(tmp_path):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )
    job = confirmed_job(
        client,
        level=1,
        profile={"address": "Test City Road 1"},
    )

    response = client.post(
        "/api/analysis/simulate",
        json={
            "job_id": job["job_id"],
            "intervention_brief_hash": job["intervention_brief_hash"],
            "payload": input_1_payload(),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["analysis_profile"]["selected_mode"] == 1

    rejected = client.post(
        "/api/analysis/simulate",
        json={
            "job_id": job["job_id"],
            "intervention_brief_hash": "0" * 64,
            "payload": input_1_payload(),
        },
    )
    assert rejected.status_code == 400
    assert "does not match" in rejected.json()["detail"]


@pytest.mark.parametrize(
    ("level", "profile", "payload", "kind"),
    [
        (
            1,
            {"address": "Test City Road 1"},
            input_1_payload(),
            "standard_100_operating_lab",
        ),
        (
            2,
            {
                "address": "Test City Road 2",
                "constraint_sources": [
                    {"source_ref": "client://planning-v1"}
                ],
            },
            input_2_payload(),
            "project_inventory_cashflow",
        ),
        (
            3,
            {
                "address": "Test City Road 3",
                "constraint_sources": [
                    {"source_ref": "client://planning-v1"}
                ],
                "core_development_boundaries_ready": True,
                "schemes": [{"scheme_id": "A"}, {"scheme_id": "B"}],
            },
            input_3_payload(),
            "scheme_risk_pareto",
        ),
    ],
)
def test_simulation_api_runs_each_input_level(
    tmp_path,
    level,
    profile,
    payload,
    kind,
):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )
    job = confirmed_job(client, level=level, profile=profile)
    response = client.post(
        "/api/analysis/simulate",
        json={
            "job_id": job["job_id"],
            "intervention_brief_hash": job["intervention_brief_hash"],
            "payload": payload,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_profile"]["selected_mode"] == level
    assert body["analysis_profile"]["mode_status"] == "confirmed"
    assert body["simulation_kind"] == kind
    assert body["evidence_type"] == "model_simulation"
    assert body["delivery_ready"] is False
    assert body["result"]


def test_simulation_api_keeps_input_3_blocked_when_schemes_are_missing(tmp_path):
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )
    job = confirmed_job(
        client,
        level=3,
        profile={"address": "Test City Road 1"},
    )
    response = client.post(
        "/api/analysis/simulate",
        json={
            "job_id": job["job_id"],
            "intervention_brief_hash": job["intervention_brief_hash"],
            "payload": input_3_payload(),
        },
    )

    assert response.status_code == 400
    assert "two_comparable_schemes" in response.json()["detail"]


def test_simulation_api_accepts_only_valid_aggregate_customer_bundle(tmp_path):
    customer = {
        "bundle_id": "test-city-c1",
        "city": "Test City",
        "as_of": "2026-07-23",
        "version": "customer/1.0",
        "evidence_level": "c1",
        "local_population": {
            "city": "Test City",
            "as_of": "2026-07-23",
            "version": "prior/1.0",
            "household_marginals": {
                "household_stage": {"first_home": 1.0}
            },
            "evidence_refs": ["e-population"],
            "source_hashes": ["sha256:population"],
            "rights_status": "authorized_aggregate",
        },
        "segments": [
            {
                "segment_id": "first-home",
                "label": "First-home commuters",
                "weight": 1.0,
                "household_stage": "new_family",
                "income_band": "20-30",
                "asset_band": "80-150",
                "current_housing": "rent",
                "purchase_stage": "active_search",
                "primary_needs": ["commute"],
                "purchase_barriers": ["down_payment"],
                "evidence_refs": ["e-segment"],
            }
        ],
        "evidence_refs": ["e-population", "e-segment"],
        "artifact_hashes": ["sha256:bundle"],
        "rights_status": "authorized_aggregate",
        "allowed_uses": ["customer_hypothesis"],
        "prohibited_uses": [
            "direct_monthly_sales_from_choice_share",
            "raw_personal_data_export",
        ],
    }
    client = TestClient(
        create_app(ProductSettings(root=tmp_path), research_sources=())
    )
    job = confirmed_job(
        client,
        level=1,
        profile={
            "city": "Test City",
            "address": "Test City Road 1",
        },
    )
    response = client.post(
        "/api/analysis/simulate",
        json={
            "job_id": job["job_id"],
            "intervention_brief_hash": job["intervention_brief_hash"],
            "payload": input_1_payload(),
            "customer_intelligence": customer,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["customer_intelligence"]["evidence_level"] == 1
    assert body["customer_intelligence"]["segments"][0]["segment_id"] == "first-home"

    customer["segments"][0]["phone"] = "13800000000"
    rejected = client.post(
        "/api/analysis/simulate",
        json={
            "job_id": job["job_id"],
            "intervention_brief_hash": job["intervention_brief_hash"],
            "payload": input_1_payload(),
            "customer_intelligence": customer,
        },
    )
    assert rejected.status_code == 400
    assert "forbidden" in rejected.json()["detail"]
