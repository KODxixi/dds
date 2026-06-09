"""DDS 核心三角 MVP — 冒烟测试（Flask test client）"""
import json
import sys
import pytest

sys.path.insert(0, "scripts")
from app import app, validate_input


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_validation_no_input():
    v, e = validate_input({"city": "三亚"})
    assert v is None
    assert e is not None


def test_validation_bad_city():
    v, e = validate_input({"city": "北京", "address": "test"})
    assert v is None
    assert "三亚" in e


def test_validation_bad_price():
    v, e = validate_input({"city": "三亚", "address": "test", "expected_price": 99999})
    assert v is not None


def test_report_endpoint(client):
    resp = client.post("/api/report", json={
        "city": "三亚", "address": "海棠区南田路16号", "expected_price": 35000, "radius_km": 5,
    })
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["status"] == "ok"
    assert "report_json" in data
    r = data["report_json"]
    assert "parcel" in r
    assert "market" in r
    assert "decision" in r
    assert "amenities" in r
    assert "meta" in r


def test_build_chat_prompt_limits_scope():
    from app import build_chat_prompt

    report = {
        "parcel": {"address": "三亚海棠区南田路16号", "lng": 109.7261, "lat": 18.3960},
        "market": {
            "sample_size": 30,
            "avg_price": 35712,
            "competitors": [
                {"project_name": "开创·白玉海棠", "unit_price_cny": 33000, "distance_km": 0.39, "room_types": "三室,四室"}
            ],
        },
        "decision": {"summary": "建议有条件进入", "top_personas": [{"name": "度假改善", "score": 58}]},
        "amenities": {"school": {"label": "学校", "items": [{"name": "人才基地幼儿园", "distance_m": 791}]}},
    }

    prompt = build_chat_prompt("这个地块最大风险是什么？", report)

    assert "这个地块最大风险是什么？" in prompt
    assert "三亚海棠区南田路16号" in prompt
    assert "开创·白玉海棠" in prompt
    assert "不执行命令" in prompt
    assert "不要编造" in prompt


def test_chat_endpoint_requires_message(client):
    resp = client.post("/api/chat", json={"report_json": {}})
    data = resp.get_json()

    assert resp.status_code == 400
    assert data["code"] == "INVALID_INPUT"


def test_chat_endpoint_requires_report(client):
    resp = client.post("/api/chat", json={"message": "分析风险"})
    data = resp.get_json()

    assert resp.status_code == 400
    assert data["code"] == "INVALID_INPUT"


def test_chat_panel_markup_exists():
    html = open("index.html", encoding="utf-8").read()

    assert "ddsChatPanel" in html
    assert "chatMessages" in html
    assert "chatInput" in html
    assert "sendChatMessage" in html
    assert "POST', headers: { 'Content-Type': 'application/json' }" in html
    assert "/api/chat" in html


def test_map_clean_3d_defaults():
    html = open("index.html", encoding="utf-8").read()
    assert 'pitch: 42' in html
    assert 'rotation: 0' in html
    assert 'showLabel: false' in html
    assert 'data-layer="zmarker"><span class="dot"' in html
    assert 'data-layer="scatter"><span class="dot"' in html
    assert 'data-layer="pulse"><span class="dot"' in html
    assert '<label><input type="checkbox" checked data-layer="zmarker"' not in html
    assert '<label><input type="checkbox" checked data-layer="scatter"' not in html


def test_validation_no_input_endpoint(client):
    resp = client.post("/api/report", json={"city": "三亚"})
    data = resp.get_json()
    assert resp.status_code == 400
    assert data["code"] == "INVALID_INPUT"


def test_validation_bad_city_endpoint(client):
    resp = client.post("/api/report", json={"city": "北京", "address": "test"})
    data = resp.get_json()
    assert resp.status_code == 400
    assert data["code"] == "INVALID_INPUT"


def test_chat_endpoint_success(client):
    resp = client.post("/api/chat", json={
        "message": "在这个地块建别墅，定位有什么风险？",
        "report_json": {
            "parcel": {"address": "三亚海棠区南田路16号", "lng": 109.7261, "lat": 18.3960},
            "market": {
                "sample_size": 30,
                "avg_price": 35000,
                "competitors": [{"project_name": "竞品A", "unit_price_cny": 33000, "distance_km": 0.5}]
            },
            "decision": {"summary": "测试用简要决策"}
        }
    })
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["status"] == "ok"
    assert "answer" in data
    assert data["llm_used"] is False  # 测试环境没有 token，应优雅降级


def test_ceo_presets_endpoint(client):
    resp = client.get("/api/ceo_presets")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["status"] == "ok"
    assert "presets" in data
    assert "invest" in data["presets"]


def test_ceo_reweight_endpoint(client):
    resp = client.post("/api/ceo_reweight", json={
        "decision": {
            "abm_market_agent": {
                "top_personas": [{"name": "度假投资", "score": 80}]
            },
            "blueprint_logic": {
                "land_value_sensitivity": {"balanced_land_value": 15000}
            },
            "migration_agent": {
                "yearly_distribution": [{"year": 5, "exit_cumulative": 0.1}]
            },
            "unit_mix_agent": {
                "unit_mix": [{"recommended_count": 50}]
            }
        },
        "preset": "design"
    })
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["status"] == "ok"
    assert "ceo" in data
    assert "total_score" in data["ceo"]


def test_supplement_endpoint_missing_key(client):
    resp = client.post("/api/supplement", json={"lng": 109.7, "lat": 18.4})
    data = resp.get_json()
    assert resp.status_code == 200
    assert "status" in data


def test_chat_stream_endpoint(client):
    resp = client.post("/api/chat_stream", json={
        "message": "在这个地块建别墅，定位有什么风险？",
        "report_json": {
            "parcel": {"address": "三亚海棠区南田路16号", "lng": 109.7261, "lat": 18.3960},
            "market": {
                "sample_size": 30,
                "avg_price": 35000,
                "competitors": [{"project_name": "竞品A", "unit_price_cny": 33000, "distance_km": 0.5}]
            },
            "decision": {"summary": "测试用简要决策"}
        }
    })
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["Content-Type"]
    
    # 物理验证流式块输出
    chunks = resp.data.decode("utf-8")
    assert "data: " in chunks
    assert "chunk" in chunks
    assert "[DONE]" in chunks

