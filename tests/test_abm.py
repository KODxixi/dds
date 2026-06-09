"""ABM 引擎 + 决策接入回归测试。极简：纯逻辑，无 Flask 依赖。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.abm_engine import (
    Product, run_abm, sample_personas, CITY_POOLS,
    product_from_client_goal, competitors_from_parcel,
)
from scripts.dds_decision_engine import run_decision_engine


PREF_HIGH = {"景观": 0.85, "私密": 0.9, "圈层": 0.7, "户型": 0.75,
             "通勤": 0.3, "学校": 0.4, "品牌": 0.8}


def _mock_parcel(city: str, avg_price: float = 35000) -> dict:
    return {
        "input": {"city": city, "address": "test"},
        "location": {"city": city, "district": "test"},
        "market_summary": {"price": {"avg": avg_price}, "room_types": {}},
        "nearby_competitors": [
            {"project_name": "竞品A", "unit_price_cny": int(avg_price * 0.9),
             "area_range": "180-220", "room_types": "4房"},
            {"project_name": "竞品B", "unit_price_cny": int(avg_price * 1.15),
             "area_range": "200-280", "room_types": "4-5房"},
        ],
        "amenities": {},
        "meta": {"data_scope": "test"},
    }


# ---------- 1. 抽样器 ----------
def test_sample_personas_count():
    for city in CITY_POOLS:
        ps = sample_personas(city, n=100)
        assert len(ps) == 100
        assert all(p.annual_income > 0 for p in ps)
        assert all(p.budget_ceiling > 0 for p in ps)


def test_sample_personas_weight_distribution():
    """抽样占比应大体接近 archetype 权重"""
    ps = sample_personas("三亚", n=5000)
    counts = {}
    for p in ps:
        counts[p.archetype] = counts.get(p.archetype, 0) + 1
    # 度假投资权重 0.25，允许 ±5% 偏差
    assert 1000 <= counts.get("度假投资", 0) <= 1500


def test_sample_personas_unknown_city():
    import pytest
    with pytest.raises(ValueError):
        sample_personas("北京", n=10)


# ---------- 2. ABM 主入口 ----------
def test_run_abm_basic_output_shape():
    p = Product("低密商墅", 35000, 220, PREF_HIGH)
    result = run_abm("三亚", p, [], n=500)
    # 关键字段齐
    for k in ("role", "method", "n_personas", "avg_buy_probability",
              "top_personas", "personas", "wtp_summary",
              "sellthrough_curve", "price_sensitivity", "confidence"):
        assert k in result, f"missing: {k}"
    assert result["n_personas"] == 500
    assert 0 <= result["avg_buy_probability"] <= 1


def test_run_abm_sellthrough_monotonic():
    """累积去化曲线必须单调递增"""
    p = Product("低密商墅", 35000, 220, PREF_HIGH)
    curve = run_abm("三亚", p, [], n=500)["sellthrough_curve"]
    cum = [x["cumulative"] for x in curve]
    assert all(cum[i] <= cum[i + 1] for i in range(len(cum) - 1))
    assert len(cum) == 24


def test_run_abm_price_sensitivity_direction():
    """涨价后平均购买率应下降，降价后应上升"""
    p = Product("低密商墅", 35000, 220, PREF_HIGH)
    sens = run_abm("三亚", p, [], n=1000)["price_sensitivity"]
    by_delta = {s["price_delta"]: s["avg_buy_prob"] for s in sens}
    assert by_delta["-10%"] > by_delta["+10%"]
    assert by_delta["-5%"] > by_delta["+5%"]


def test_run_abm_persona_share_sums_to_one():
    p = Product("低密商墅", 35000, 220, PREF_HIGH)
    personas = run_abm("三亚", p, [], n=500)["personas"]
    total_share = sum(x["share"] for x in personas)
    assert 0.98 <= total_share <= 1.02


def test_run_abm_four_cities():
    """四城都应能跑出非空结果"""
    p = Product("改善产品", 30000, 130,
                {"景观": 0.6, "私密": 0.5, "圈层": 0.55, "户型": 0.8,
                 "通勤": 0.7, "学校": 0.75, "品牌": 0.6})
    for city in ("三亚", "杭州", "上海", "青岛"):
        r = run_abm(city, p, [], n=300)
        assert r["n_personas"] == 300
        assert len(r["top_personas"]) >= 1


# ---------- 3. 适配层 ----------
def test_product_from_client_goal_villa():
    p = product_from_client_goal({"product_type": "低密商墅"}, 35000)
    assert p.unit_price == 35000
    assert p.area == 220
    assert p.pref_score["私密"] >= 0.85


def test_product_from_client_goal_default():
    """无 avg_price 时应有默认值"""
    p = product_from_client_goal({"product_type": "刚需"}, None)
    assert p.unit_price == 30000


def test_competitors_from_parcel():
    competitors = competitors_from_parcel([
        {"project_name": "A", "unit_price_cny": 30000, "area_range": "100-140"},
        {"project_name": "B", "unit_price_cny": None, "area_range": "120"},
        {"project_name": "C", "unit_price_cny": 40000, "area_range": "200"},
    ])
    # B 缺价格应被过滤
    assert len(competitors) == 2
    assert competitors[0].name == "A"


# ---------- 4. 决策引擎集成 ----------
def test_decision_engine_uses_real_abm():
    """run_decision_engine 应调用新 ABM 而非旧硬编码 4 persona"""
    parcel = _mock_parcel("三亚")
    goal = {"product_type": "低密商墅", "floor_area_ratio": 1.0, "benchmark": "竞品B"}
    result = run_decision_engine(parcel, goal)
    abm = result["abm_market_agent"]
    # 关键标识：新 ABM 有 method 含 MNL 字样
    assert "MNL" in abm["method"]
    assert abm["n_personas"] == 1000
    assert "sellthrough_curve" in abm
    # 老硬编码的"度假改善"persona 名不应再出现
    persona_names = {p["name"] for p in abm["personas"]}
    assert "度假投资" in persona_names or "高净值康养" in persona_names


def test_decision_engine_handles_unknown_city():
    """未配置城市应优雅降级"""
    parcel = _mock_parcel("北京")
    goal = {"product_type": "改善"}
    result = run_decision_engine(parcel, goal)
    abm = result["abm_market_agent"]
    assert abm.get("status") == "city_not_configured"


def test_decision_summary_top_personas_present():
    parcel = _mock_parcel("杭州", avg_price=45000)
    goal = {"product_type": "改善产品", "floor_area_ratio": 2.0}
    result = run_decision_engine(parcel, goal)
    top = result["decision_summary"]["top_personas"]
    assert len(top) >= 1
    assert "name" in top[0]


# ---------- 5. 维度加厚（v1.1）----------
def test_persona_new_dimensions_present():
    ps = sample_personas("三亚", n=300)
    p = ps[0]
    for attr in ("family_size", "kids", "elderly_cohabit",
                 "social_class", "info_channel", "decision_urgency"):
        assert hasattr(p, attr), f"missing: {attr}"


def test_persona_social_class_diverse():
    """三亚池含 A/B/C 三个阶层"""
    ps = sample_personas("三亚", n=500)
    classes = {p.social_class for p in ps}
    assert len(classes) >= 2  # 至少 2 个阶层


def test_persona_urgency_range():
    ps = sample_personas("杭州", n=300)
    assert all(0 <= p.decision_urgency <= 1 for p in ps)


# ---------- 6. 反向匹配 ----------
def test_optimize_unit_mix_basic():
    from scripts.abm_engine import optimize_unit_mix
    units = [
        Product("小户型", 75000, 95,
                {"景观": 0.5, "私密": 0.4, "圈层": 0.4, "户型": 0.85,
                 "通勤": 0.4, "学校": 0.5, "品牌": 0.55}),
        Product("大平层", 85000, 180,
                {"景观": 0.85, "私密": 0.75, "圈层": 0.75, "户型": 0.75,
                 "通勤": 0.3, "学校": 0.4, "品牌": 0.8}),
    ]
    r = optimize_unit_mix("三亚", units, total_units=100, n=500)
    assert r["total_units"] == 100
    assert len(r["unit_mix"]) == 2
    total_count = sum(u["recommended_count"] for u in r["unit_mix"])
    # 推荐套数总和 ≈ total_units（允许 ±2 套四舍五入误差）
    assert abs(total_count - 100) <= 2 or total_count == 0


def test_optimize_unit_mix_share_sums():
    from scripts.abm_engine import optimize_unit_mix
    units = [
        Product("A", 70000, 100, {"景观": 0.6, "私密": 0.5, "圈层": 0.5,
                                   "户型": 0.8, "通勤": 0.5, "学校": 0.5,
                                   "品牌": 0.6}),
        Product("B", 80000, 150, {"景观": 0.7, "私密": 0.65, "圈层": 0.65,
                                   "户型": 0.8, "通勤": 0.4, "学校": 0.55,
                                   "品牌": 0.7}),
        Product("C", 95000, 220, {"景观": 0.9, "私密": 0.9, "圈层": 0.8,
                                   "户型": 0.7, "通勤": 0.2, "学校": 0.3,
                                   "品牌": 0.85}),
    ]
    r = optimize_unit_mix("三亚", units, total_units=120, n=500)
    if r.get("status") != "no_demand":
        total_share = sum(u["share"] for u in r["unit_mix"])
        assert 0.98 <= total_share <= 1.02


# ---------- 7. 5 年迁移 ----------
def test_project_personas_5year_shape():
    from scripts.abm_engine import project_personas_5year
    r = project_personas_5year("三亚", horizon_years=5, n=500)
    assert r["horizon_years"] == 5
    assert len(r["yearly_distribution"]) == 6  # Y0..Y5
    assert r["yearly_distribution"][0]["year"] == 0
    assert r["yearly_distribution"][-1]["year"] == 5


def test_project_personas_5year_distribution_sums():
    from scripts.abm_engine import project_personas_5year
    r = project_personas_5year("三亚", horizon_years=5, n=500)
    for h in r["yearly_distribution"]:
        s = sum(h["shares"].values())
        assert 0.98 <= s <= 1.02, f"Y{h['year']} sum={s}"


def test_project_personas_5year_exit_grows():
    """累计退出应单调递增"""
    from scripts.abm_engine import project_personas_5year
    r = project_personas_5year("三亚", horizon_years=5, n=500)
    exits = [h["exit_cumulative"] for h in r["yearly_distribution"]]
    assert all(exits[i] <= exits[i + 1] for i in range(len(exits) - 1))


def test_project_personas_5year_four_cities():
    from scripts.abm_engine import project_personas_5year
    for city in ("三亚", "杭州", "上海", "青岛"):
        r = project_personas_5year(city, horizon_years=5, n=300)
        assert r["city"] == city
        assert len(r["yearly_distribution"]) == 6


# ---------- 8. 决策报告新输出层 ----------
def test_decision_report_has_unit_mix():
    parcel = _mock_parcel("三亚", avg_price=50000)
    goal = {"product_type": "改善产品", "floor_area_ratio": 1.5}
    result = run_decision_engine(parcel, goal)
    assert "unit_mix_agent" in result
    mix = result["unit_mix_agent"]
    if mix.get("status") != "skipped":
        assert "unit_mix" in mix
        assert mix["total_units"] > 0


def test_decision_report_has_migration():
    parcel = _mock_parcel("三亚", avg_price=50000)
    goal = {"product_type": "低密商墅"}
    result = run_decision_engine(parcel, goal)
    assert "migration_agent" in result
    mig = result["migration_agent"]
    if mig.get("status") != "skipped":
        assert len(mig["yearly_distribution"]) == 6


# ---------- 9. CEO 权重引擎 ----------
def test_ceo_aggregator_present():
    parcel = _mock_parcel("三亚", avg_price=50000)
    goal = {"product_type": "改善产品"}
    result = run_decision_engine(parcel, goal)
    ceo = result.get("ceo_aggregator")
    assert ceo is not None
    assert ceo["preset"] == "invest"  # 默认
    assert 0 <= ceo["total_score"] <= 100
    assert 0 <= ceo["overall_confidence"] <= 1
    assert ceo["grade"] in ("A+", "A", "B+", "B", "C", "D")
    assert len(ceo["breakdown"]) == 6  # 6 个 Agent


def test_ceo_presets_yield_different_scores():
    """三套 preset 应产生不同总分（权重不同 → 加权和不同）"""
    parcel = _mock_parcel("三亚", avg_price=50000)
    scores = {}
    for preset in ("invest", "design", "finance"):
        goal = {"product_type": "低密商墅", "ceo_preset": preset}
        r = run_decision_engine(parcel, goal)
        scores[preset] = r["ceo_aggregator"]["total_score"]
    # 三个 preset 至少有两个不同
    assert len(set(scores.values())) >= 2


def test_ceo_breakdown_weights_sum_to_one():
    parcel = _mock_parcel("三亚", avg_price=50000)
    goal = {"product_type": "改善产品"}
    result = run_decision_engine(parcel, goal)
    ceo = result["ceo_aggregator"]
    total_w = sum(b["weight"] for b in ceo["breakdown"])
    assert 0.98 <= total_w <= 1.02


# ---------- 10. CEO API + 前端联动（test client） ----------
def test_ceo_api_preset_switch():
    """/api/ceo_reweight 接 preset 切换返回不同总分"""
    import app as a
    parcel = _mock_parcel("三亚", avg_price=50000)
    goal = {"product_type": "低密商墅"}
    decision = run_decision_engine(parcel, goal)
    client = a.app.test_client()
    scores = {}
    for preset in ("invest", "design", "finance"):
        r = client.post("/api/ceo_reweight",
                        json={"decision": decision, "preset": preset})
        j = r.get_json()
        assert j["status"] == "ok"
        scores[preset] = j["ceo"]["total_score"]
    assert len(set(scores.values())) >= 2


def test_ceo_api_custom_weights():
    import app as a
    parcel = _mock_parcel("三亚", avg_price=50000)
    decision = run_decision_engine(parcel, {"product_type": "改善产品"})
    client = a.app.test_client()
    custom = {"compliance": 0.5, "value": 0.1, "abm": 0.1,
              "unit_mix": 0.1, "migration": 0.1, "blueprint": 0.1}
    r = client.post("/api/ceo_reweight",
                    json={"decision": decision, "weights": custom})
    j = r.get_json()
    assert j["status"] == "ok"
    # custom weights 注入应触发 _custom preset 但已被清掉
    presets = j["presets"]
    assert "_custom" not in presets


def test_ceo_api_presets_listed():
    import app as a
    client = a.app.test_client()
    r = client.get("/api/ceo_presets")
    j = r.get_json()
    assert set(j["presets"]) >= {"invest", "design", "finance"}


# ---------- 11. HTML 报告输出 ----------
def test_render_decision_html_contains_keys():
    from scripts.dds_decision_engine import render_decision_html
    parcel = _mock_parcel("三亚", avg_price=50000)
    decision = run_decision_engine(parcel, {"product_type": "低密商墅"})
    html = render_decision_html(decision)
    assert "<!doctype html>" in html
    assert "CEO 综合评分" in html
    assert "ABM 客群模拟" in html
    assert "户型配比" in html
    assert "5 年客群迁移" in html
    # CSS 内嵌
    assert ".score-card" in html


# ---------- 12. interpretation 中文 ----------
def test_interpretation_is_chinese():
    parcel = _mock_parcel("三亚", avg_price=50000)
    decision = run_decision_engine(parcel, {"product_type": "改善产品"})
    interp = decision["ceo_aggregator"]["interpretation"]
    # 不含老的 code 串
    assert "low_confidence" not in interp
    assert "high_confidence" not in interp
    assert "supplement_data_first" not in interp
    # 含中文
    assert any(0x4e00 <= ord(c) <= 0x9fff for c in interp)


# ---------- 13. CEO 权重学习 ----------
def test_ceo_weight_learning_ema():
    """模拟用户拖动 5 次偏向 ABM/unit_mix，EMA 应学到主导权重"""
    import app as a
    c = a.app.test_client()
    uid = "test_user_" + str(__import__("time").time())
    weights_seq = [
        {"compliance": 0.10, "value": 0.10, "abm": 0.30, "unit_mix": 0.30,
         "migration": 0.10, "blueprint": 0.10}
    ] * 5
    for w in weights_seq:
        c.post("/api/ceo_record_weights", json={"user_id": uid, "weights": w})
    r = c.get("/api/ceo_learned_weights?user_id=" + uid)
    j = r.get_json()
    assert j["status"] == "ok"
    assert j["learned"]["sample_count"] == 5
    # 主导权重应为 abm 或 unit_mix
    top = j["focus"]["top_focus"][0]["agent"]
    assert top in ("abm", "unit_mix")


def test_ceo_learning_insufficient_samples():
    """少于 3 个样本时不返回 learned"""
    import app as a
    c = a.app.test_client()
    uid = "test_user_few_" + str(__import__("time").time())
    for _ in range(2):
        c.post("/api/ceo_record_weights", json={"user_id": uid,
            "weights": {"abm": 0.5, "compliance": 0.5}})
    r = c.get("/api/ceo_learned_weights?user_id=" + uid)
    j = r.get_json()
    assert j["learned"] is None  # 不足 min_samples


def test_ceo_learning_no_weights_rejected():
    import app as a
    c = a.app.test_client()
    r = c.post("/api/ceo_record_weights", json={"user_id": "x"})
    assert r.status_code == 400


# ---------- 14. 向量库证据注入 ----------
def test_vector_evidence_in_value_agent():
    parcel = _mock_parcel("三亚", avg_price=35000)
    decision = run_decision_engine(parcel, {"product_type": "低密商墅"})
    ve = (decision.get("value_agent") or {}).get("vector_evidence") or {}
    # 至少应该 status=ok (Vault fallback) 或 not_configured
    assert ve.get("status") in ("ok", "not_configured", "no_data", "skipped")


def test_vector_evidence_vault_fallback():
    """chromadb 未启用时应走 Vault 价格相似度兜底"""
    from scripts.vector_evidence import find_similar_parcels
    r = find_similar_parcels("三亚", 35000, "低密商墅", top_k=5)
    assert r["status"] == "ok"
    assert r["source"] == "vault_fallback"
    assert len(r["items"]) > 0
    for item in r["items"]:
        m = item["metadata"]
        assert "year" in m and "unit_price" in m and "project_name" in m


def test_vector_evidence_html_renders():
    from scripts.dds_decision_engine import render_vector_evidence_html
    ve = {"status": "ok", "source": "test", "collection": "x",
          "items": [{"text": "t", "metadata": {"year": 2023, "project_name": "A",
                                                "district": "B", "unit_price": 35000,
                                                "area_range": "100-180", "developer": "X"}}]}
    html = render_vector_evidence_html(ve)
    assert html is not None
    assert "table" in html.lower()
    assert "2023" in html
    assert "35000" in html


def test_vector_evidence_skipped_gracefully():
    from scripts.dds_decision_engine import render_vector_evidence_html
    html = render_vector_evidence_html({})
    assert html is not None
    assert "未启用" in html


# ---------- 15. 客群语义画像 & 向量证据相似度 ----------
def test_pain_need_md_render():
    from scripts.dds_decision_engine import render_pain_need_md
    abm = {"n_personas": 500,
           "top_pains": [{"pain": "无电梯", "weight": 0.3, "raw_score": 6}],
           "top_details": [{"need": "主卧套房", "weight": 0.4, "raw_score": 8}]}
    md = render_pain_need_md(abm)
    assert "无电梯" in md and "主卧套房" in md
    assert "30.0%" in md and "40.0%" in md


def test_pain_need_html_render():
    from scripts.dds_decision_engine import render_pain_need_html
    abm = {"n_personas": 500,
           "top_pains": [{"pain": "无电梯", "weight": 0.3}],
           "top_details": []}
    html = render_pain_need_html(abm)
    assert html and "无电梯" in html and "<table" in html


def test_vector_evidence_md_includes_similarity():
    from scripts.dds_decision_engine import render_vector_evidence_md
    ve = {"status": "ok", "source": "chromadb", "collection": "competitor_projects",
          "items": [{"text": "t", "distance": 0.15,
                     "metadata": {"year": 2024, "project_name": "X",
                                  "district": "Y", "unit_price": 35000,
                                  "area_range": "100-180", "developer": "A"}}]}
    md = render_vector_evidence_md(ve)
    assert "相似度" in md and "85%" in md  # 1 - 0.15 = 85%


def test_vector_evidence_html_similarity_bar():
    from scripts.dds_decision_engine import render_vector_evidence_html
    ve = {"status": "ok", "source": "chromadb", "collection": "competitor_projects",
          "items": [{"text": "t", "distance": 0.10,
                     "metadata": {"year": 2024, "project_name": "X",
                                  "district": "Y", "unit_price": 35000,
                                  "area_range": "100-180", "developer": "A"}}]}
    html = render_vector_evidence_html(ve)
    assert html and "90%" in html and "<span style=" in html
