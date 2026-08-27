from __future__ import annotations

from dds.engines.benchmark import (
    dimension_frontier,
    match_benchmarks,
    phenom_score,
    signature_dims,
    trend_analysis,
    validate,
)

DIMS = [
    "demo_zone", "floorplan", "podium_lift", "display_zone", "sunken_club",
    "policy_leverage", "open_floor", "landscape", "facade", "narrative",
]


def _scores(**overrides: float) -> dict[str, float]:
    base = {dim: 7.0 for dim in DIMS}
    base.update(overrides)
    return base


def make_case(
    cid: str,
    name: str,
    *,
    category: str = "现象级产品",
    year: int = 2023,
    city: str = "武汉",
    **score_overrides: float,
) -> dict:
    return {
        "id": cid,
        "name": name,
        "category": category,
        "city": city,
        "city_tier": 1.5,
        "year": year,
        "product_type": "改善",
        "positioning_tags": [],
        "headline": f"{name} 样板",
        "confidence": 0.8,
        "data_quality": "A",
        "scores": _scores(**score_overrides),
        "sources": [{"url": "https://example.com/case"}],
        "key_tactics": ["示范区即实景"],
    }


def make_lib(*cases: dict) -> dict:
    return {
        "dimensions": {dim: {"cn": dim} for dim in DIMS},
        "dimension_layers": {},
        "meta": {"schema_version": "v3", "case_count": len(cases)},
        "cases": list(cases),
    }


def test_phenom_score_rewards_spike_over_flat():
    spike = make_case("s", "尖峰盘", narrative=9.8, facade=9.5)
    flat = make_case("f", "均衡盘", narrative=7.2, facade=7.2)
    assert phenom_score(spike) > phenom_score(flat)


def test_signature_dims_derives_peaks():
    case = make_case("s", "尖峰盘", narrative=9.8, facade=9.6)
    sig = signature_dims(case, threshold=9.5)
    assert "narrative" in sig
    assert "facade" in sig


def test_match_benchmarks_respects_top_k_and_mode():
    lib = make_lib(
        make_case("c1", "案例一", year=2024),
        make_case("c2", "案例二", year=2022),
        make_case("c3", "案例三", year=2021),
    )
    result = match_benchmarks("武汉", None, "改善", top_k=2, lib=lib, mode="peer")
    assert result["status"] == "ok"
    assert result["mode"] == "peer"
    assert result["count"] == 2
    assert len(result["items"]) == 2
    assert {item["name"] for item in result["items"]} <= {"案例一", "案例二", "案例三"}


def test_dimension_frontier_finds_best_case_per_dim():
    lib = make_lib(
        make_case("low", "低分盘", demo_zone=6.0),
        make_case("high", "高分盘", demo_zone=9.5),
    )
    frontier = dimension_frontier(lib)
    assert frontier["demo_zone"]["case"] == "高分盘"
    assert frontier["demo_zone"]["score"] == 9.5


def test_trend_analysis_has_momentum_tag():
    lib = make_lib(
        make_case("old", "旧盘", year=2021, facade=5.0),
        make_case("new", "新盘", year=2025, facade=9.0),
    )
    trend = trend_analysis(lib)
    assert trend["facade"]["slope"] > 0
    assert trend["facade"]["momentum"] in ("上升", "温和上升", "成熟/平稳")


def test_validate_reports_missing_sources():
    good = make_case("g", "好案例")
    bad = make_case("b", "坏案例")
    bad["sources"] = []
    lib = make_lib(good, bad)
    result = validate(lib)
    assert result["ok"] is False
    assert any("坏案例" in issue or "b:" in issue for issue in result["issues"])
