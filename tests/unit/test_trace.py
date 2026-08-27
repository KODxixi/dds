from __future__ import annotations

import pytest

from dds.trace import DecisionTrace, trace_context


def test_add_step_increments_step_number():
    trace = DecisionTrace(trace_id="t1")
    trace.add_step("market_agent", "in", "out", "reason")
    trace.add_step("premium_agent", "in2", "out2", "reason2")
    assert [step.step for step in trace.steps] == [1, 2]
    assert trace.steps[0].agent == "market_agent"


def test_render_contains_steps_and_context():
    trace = DecisionTrace(trace_id="t1", city="武汉")
    trace.add_step("market_agent", "input", "output", "because evidence")
    text = trace.render()
    assert "DDS 决策推理链" in text
    assert "market_agent" in text
    assert "武汉" in text


def test_render_html_contains_agent_and_status():
    trace = DecisionTrace(trace_id="t1")
    trace.add_step("market_agent", "i", "o", "r")
    html = trace.render_html()
    assert "market_agent" in html
    assert "DDS 决策推理链" in html


def test_trace_context_marks_error_and_propagates():
    trace = DecisionTrace(trace_id="t1")
    with pytest.raises(ValueError, match="boom"):
        with trace_context(trace, "boom_agent"):
            raise ValueError("boom")
    step = trace.steps[0]
    assert step.status == "error"
    assert any("boom" in caveat for caveat in step.caveats)


def test_trace_context_marks_ok_on_success():
    trace = DecisionTrace(trace_id="t1")
    with trace_context(trace, "ok_agent") as holder:
        holder["result"] = 42
    assert trace.steps[0].status == "ok"
    assert trace.steps[0].duration_ms >= 0.0


def test_to_dict_round_trips_confidence():
    trace = DecisionTrace(trace_id="t1")
    trace.add_step("a", "i", "o", "r", confidence={"grade": "A", "score": 0.9})
    data = trace.to_dict()
    assert data["trace_id"] == "t1"
    assert data["steps"][0]["agent"] == "a"
    assert data["steps"][0]["confidence"]["grade"] == "A"
