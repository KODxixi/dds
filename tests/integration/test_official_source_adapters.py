from __future__ import annotations


import pytest

from dds.config import Settings
from dds.data.adapters.land import LandAdapter
from dds.data.adapters.macro import MacroAdapter
from dds.data.adapters.transactions import TransactionAdapter
from dds.data.adapters.user_materials import UserMaterialAdapter
from dds.domain import EvidenceType


@pytest.mark.integration
def test_wuhan_transaction_land_macro_sources_are_distinct():
    settings = Settings.from_env()
    if not settings.datasets_root.exists():
        pytest.skip("configured curated datasets are unavailable")
    transactions = TransactionAdapter(settings).collect("武汉", limit=10)
    land = LandAdapter(settings).collect("武汉")
    macro = MacroAdapter(settings).collect("武汉", limit_per_source=10)
    assert transactions.evidence
    assert land.evidence
    assert macro.evidence
    assert {item.metric_id for item in transactions.evidence} == {
        "market.transaction.unit_price"
    }
    assert all("listing" not in item.metric_id for item in transactions.evidence)
    assert all(item.has_traceable_source for item in (*transactions.evidence, *land.evidence, *macro.evidence))


def test_user_text_is_social_observation_not_observed_fact(tmp_path):
    material = tmp_path / "brief.md"
    material.write_text("甲方认为目标客户愿意支付更高价格。", encoding="utf-8")
    evidence = UserMaterialAdapter(tmp_path).collect((material,))
    assert evidence[0].evidence_type is EvidenceType.SOCIAL_OBSERVATION
    assert "not independently verified" in evidence[0].limitations[0]
