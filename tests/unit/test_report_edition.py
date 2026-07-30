from __future__ import annotations

from dds.reporting import decision_pages, page_value_errors


def _page(**overrides):
    page = {
        "page_id": "sc2-primary",
        "section_id": "SC2",
        "title": "市场机会",
        "decision_question": "真实成交支持哪个价格带？",
        "takeaway": "真实成交显示核心价格带集中在 8–10 万元/㎡。",
        "decision_impact": "据此调整产品总价和补证任务。",
        "blocks": [{"type": "table", "rows": [["A", "8.5"]]}],
        "source_refs": ["src-trade"],
        "unit_status": "ready",
    }
    page.update(overrides)
    return page


def test_page_value_gate_requires_question_conclusion_evidence_and_action():
    assert page_value_errors(_page()) == []
    assert page_value_errors(
        _page(takeaway="市场机会", decision_impact="", source_refs=[])
    ) == [
        "decision_takeaway_missing",
        "source_refs_missing",
        "decision_impact_or_action_missing",
    ]


def test_decision_report_omits_empty_gaps_but_keeps_explicit_gate():
    missing = _page(
        page_id="ad1-gap",
        unit_status="missing",
        takeaway="尚缺第二个方案。",
    )
    assert decision_pages([_page(), missing]) == [
        {
            **_page(),
            "value_gate": {"status": "passed", "errors": []},
        }
    ]
    kept = decision_pages([missing], blocking_page_ids=("ad1-gap",))
    assert kept[0]["layout"] == "gap"


def test_future_customer_page_requires_complete_decision_chain():
    incomplete = _page(
        page_role="future_customer_outlook",
        future_customer_chain={
            "event": ["企鹅岛一期投入运营"],
            "customer": ["家庭化核心骨干"],
            "behavior": ["园区公寓后进入改善置业"],
            "product_action": [],
        },
    )
    assert page_value_errors(incomplete) == [
        "future_customer_product_action_missing"
    ]

    complete = _page(
        page_role="future_customer_outlook",
        future_customer_chain={
            "event": ["企鹅岛一期投入运营"],
            "customer": ["家庭化核心骨干"],
            "behavior": ["园区公寓后进入改善置业"],
            "product_action": ["验证总价、双人办公与门到门通勤"],
        },
    )
    assert page_value_errors(complete) == []
