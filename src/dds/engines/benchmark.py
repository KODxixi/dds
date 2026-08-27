"""High-end / global benchmark matching engine.

Consumes a benchmark case library (``benchmark_library.json``) and provides:

1. ``match_benchmarks``     — match a parcel/positioning to the most relevant
                              flagship benchmarks (evidence injection for the
                              product engine).
2. ``dimension_frontier``   — the "ceiling" case per dimension.
3. ``trend_analysis``       — per-dimension momentum over time.
4. ``extrapolate_next_gen`` — next-generation product vector, priority moves,
                              and cross-dimension combinations.

Ported from DDS V1 ``scripts/benchmark_engine.py``. Pure standard library; the
scores are analyst estimates and the extrapolated conclusions are analysis
inference (see the library's own ``meta``), never observed market facts.
"""

from __future__ import annotations

import json
import os
from itertools import combinations
from pathlib import Path

# Dimension order (aligned with the library JSON).
DIMS = [
    "demo_zone", "floorplan", "podium_lift", "display_zone", "sunken_club",
    "policy_leverage", "open_floor", "landscape", "facade", "narrative",
]

# L1 hardware (the ten physical dimensions above), L2 content/operation, L3 capital result.
DIMS_HW = DIMS
DIMS_OPS = ["ip_narrative", "community_ops", "retail_holding", "service_system", "lifestyle_brand"]
DIMS_RESULT = ["sellthrough_speed", "premium_ability", "resale_value", "mindshare", "media_volume"]
DIMS_ALL = DIMS_HW + DIMS_OPS + DIMS_RESULT
SIGNATURE_THRESHOLD = 9.5

CITY_TIER = {
    "上海": 1, "北京": 1, "深圳": 1, "广州": 1,
    "杭州": 1.5, "南京": 1.5, "成都": 1.5, "武汉": 1.5, "重庆": 1.5,
    "苏州": 1.5, "长沙": 1.5, "青岛": 1.5, "三亚": 1.5,
}

COMBO_LIBRARY = [
    ({"policy_leverage", "open_floor", "landscape"},
     "第四代不计容阳台 × 架空层全龄会所 × 垂直绿化空中院落 → 公园会客厅产品"),
    ({"facade", "landscape", "narrative"},
     "公建化立面 × 绿先于楼景观 × 在地叙事 → 酒店化都市村落示范区"),
    ({"demo_zone", "display_zone", "podium_lift"},
     "示范区即交付实景 × 抬板礼序 × 现房展示 → 去售楼处化的归家剧场"),
    ({"sunken_club", "narrative"},
     "酒店式会所 × 在地隐士叙事 → 服务即产品的精神会所"),
    ({"policy_leverage", "floorplan"},
     "好房子3.0层高 × 270°转角窗大平层 → 政策红利户型"),
    ({"facade", "open_floor"},
     "Heatherwick 式有机绿丘 × 架空地景连续 → 抬升地景立面"),
]

PARADIGMS = [
    {"name": "内容即资产·运营前置",
     "dims": ["ip_narrative", "community_ops", "retail_holding"],
     "rationale": "把内容/社群/自持商业做成前置资产(阿那亚式),用运营反哺溢价与心智——多数硬件强盘的空白区。"},
    {"name": "好房子新规·政策红利产品",
     "dims": ["policy_leverage", "floorplan", "open_floor"],
     "rationale": "吃透第四代/好房子规则,把不计容阳台×层高×架空层做成户型代差。"},
    {"name": "心智资产化社区",
     "dims": ["mindshare", "lifestyle_brand", "community_ops"],
     "rationale": "以品牌+社群占领品类第一心智,使二手保值与溢价脱离单纯地段。"},
    {"name": "绿先于楼·公园会客厅",
     "dims": ["landscape", "open_floor", "facade"],
     "rationale": "麻布台/垂直森林式,景观与架空地景先行,立面让位于绿,换取稀缺心智。"},
]


def _default_library_path() -> Path:
    """Resolve the benchmark library path from env or the repository data dir."""
    env = os.getenv("DDS_BENCHMARK_LIBRARY")
    if env:
        return Path(env).expanduser().resolve()
    root = Path(__file__).resolve().parents[3]
    return root / "data" / "benchmark" / "benchmark_library.json"


def load_library(path: Path | None = None) -> dict:
    target = Path(path) if path else _default_library_path()
    with open(target, encoding="utf-8") as handle:
        return json.load(handle)


# ── utility functions ──────────────────────────────────────
def _minmax(values: dict) -> dict:
    normalized = list(values.values())
    low, high = min(normalized), max(normalized)
    if high - low < 1e-9:
        return {key: 0.5 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def _slope(points: list[tuple[float, float]]) -> float:
    count = len(points)
    if count < 2:
        return 0.0
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator < 1e-9:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


def _median(numbers: list[float]) -> float:
    ordered = sorted(numbers)
    count = len(ordered)
    if count % 2:
        return ordered[count // 2]
    return (ordered[count // 2 - 1] + ordered[count // 2]) / 2


def _top_dims(scores: dict, k: int = 3) -> list[str]:
    return [dim for dim, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:k]]


def _all_scores(case: dict) -> dict:
    out = dict(case.get("scores", {}) or {})
    for block in ("layer2_ops", "layer3_proof"):
        block_value = case.get(block)
        if isinstance(block_value, dict):
            for key, value in block_value.items():
                if isinstance(value, (int, float)):
                    out[key] = value
    return out


def signature_dims(case: dict, threshold: float = SIGNATURE_THRESHOLD) -> list[str]:
    if case.get("signature_dims"):
        return list(case["signature_dims"])
    all_scores = _all_scores(case)
    return [dim for dim, value in sorted(all_scores.items(), key=lambda kv: -kv[1]) if value >= threshold]


def phenom_score(case: dict) -> float:
    all_scores = _all_scores(case)
    if not all_scores:
        return 0.0
    values = sorted(all_scores.values(), reverse=True)
    spike = values[0]
    baseline = _median(values[1:]) if len(values) > 1 else values[0]
    return round(spike * baseline / 10.0, 2)


def _cn(lib: dict, dim: str) -> str:
    return (lib.get("dimensions", {}).get(dim) or {}).get("cn", dim)


def _layer_of(lib: dict, dim: str) -> str:
    for layer, dims in (lib.get("dimension_layers") or {}).items():
        if isinstance(dims, list) and dim in dims:
            return layer
    return "L1_hardware"


def decode_play(case: dict, lib: dict | None = None) -> dict:
    lib = lib or load_library()
    signature = signature_dims(case) or _top_dims(case.get("scores", {}))
    signature_cn = [_cn(lib, dim) for dim in signature]
    tactics = case.get("key_tactics", [])
    signal = "；".join(tactics[:2]) or (case.get("headline") or "")
    layer = _layer_of(lib, signature[0]) if signature else "L1_hardware"
    mechanism = {
        "L1_hardware": "靠物理产品力建立记忆点与首入印象,溢价来自'看得见的好'。",
        "L2_ops": "靠内容/社群/自持运营持续创造到访与黏性,溢价来自'持续被讨论'。",
        "L3_result": "已转化为市场结果(心智/声量/去化),壁垒来自'先发心智'。",
    }.get(layer, "")
    cost = {
        "L1_hardware": "示范区与立面造价、工艺标准",
        "L2_ops": "运营前置投入与回收周期、内容/社群团队",
        "L3_result": "品牌与时间积累、先发卡位",
    }.get(layer, "投入评估")
    transfer = f"本案要复刻其【{signature_cn[0] if signature_cn else '强项'}】,需评估:用地条件/容积率让渡 + {cost}。"
    return {"signal": signal, "mechanism": mechanism, "transfer": transfer, "signature": signature_cn}


def _proven_combos(lib: dict, min_score: float = 8.5, top: int = 4) -> list[dict]:
    products = [case for case in lib["cases"] if case.get("category") == "现象级产品"]
    counts: dict[tuple[str, ...], int] = {}
    examples: dict[tuple[str, ...], str] = {}
    for case in products:
        hot = sorted(dim for dim, value in _all_scores(case).items() if value >= min_score)
        for combo in combinations(hot, 2):
            counts[combo] = counts.get(combo, 0) + 1
            examples.setdefault(combo, case["name"])
    result = []
    for combo, num in sorted(counts.items(), key=lambda kv: -kv[1]):
        if num < 2:
            continue
        result.append({"dims": [_cn(lib, dim) for dim in combo], "count": num, "example": examples[combo]})
        if len(result) >= top:
            break
    return result


# ── 1) benchmark matching (evidence injection) ─────────────
def _rank(cases, city, product_type, mode="peer", recent_bias=False):
    target_tier = CITY_TIER.get(city or "", 1.5)
    product_type = product_type or ""
    expanded = {product_type}
    if "改善" in product_type:
        expanded.update(["品质改善", "高端改善", "功能改善", "改善产品"])
    if "豪宅" in product_type or "顶豪" in product_type:
        expanded.update(["顶豪", "高端改善", "大平层"])
    ranked = []
    for case in cases:
        phenom = phenom_score(case)
        case_tier = case.get("city_tier", 1.5)
        if mode == "aspire":
            tier_rel = max(0.0, target_tier - case_tier)
        elif mode == "cross":
            tier_rel = 0.0
        else:
            tier_rel = max(0.0, 1.0 - abs(case_tier - target_tier))
        relevance = tier_rel
        case_product_type = case.get("product_type", "")
        case_tags = case.get("positioning_tags", [])
        case_headline = case.get("headline", "")
        product_hit = any(
            term in case_product_type or any(term in tag for tag in case_tags) or term in case_headline
            for term in expanded
        )
        if mode == "cross":
            relevance += 0.3 if product_hit else 0.0
        elif product_hit:
            relevance += 1.5
        transferable = {
            "四代": 0.6, "第四代": 0.6, "空中花园": 0.5, "空中庭院": 0.5,
            "错层露台": 0.5, "退台": 0.4, "台地": 0.4, "垂直绿化": 0.4,
            "立体生态": 0.4, "LDKG": 0.3, "挑高露台": 0.4, "独立入户": 0.3,
            "高赠送": 0.5, "高得房率": 0.5, "不计容": 0.5,
            "四代宅": 0.6, "抬板": 0.4, "层层花园": 0.4,
        }
        strategy_boost = 0.0
        case_text = case_product_type + " " + " ".join(case_tags) + " " + case_headline
        for keyword, boost in transferable.items():
            if keyword in case_text:
                strategy_boost = max(strategy_boost, boost)
        relevance += strategy_boost
        relevance += 0.3 * case.get("confidence", 0.5)
        relevance += 0.25 * phenom
        if recent_bias:
            relevance += 0.2 * max(0, case.get("year", 0) - 2020)
        ranked.append((relevance, phenom, case.get("confidence", 0.5), case))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [(item[0], item[3]) for item in ranked]


def _match_item(case, lib, relevance):
    return {
        "name": case["name"], "category": case.get("category"),
        "developer": case.get("developer"), "designer": case.get("designer"),
        "year": case.get("year"), "headline": case.get("headline"),
        "relevance": round(relevance, 2),
        "phenom": phenom_score(case),
        "strong_dims": [lib["dimensions"][dim]["cn"] for dim in _top_dims(case["scores"])],
        "signature": [_cn(lib, dim) for dim in signature_dims(case)],
        "key_tactics": case.get("key_tactics", []),
        "decode": decode_play(case, lib),
        "data_quality": case.get("data_quality"), "sources": case.get("sources", []),
    }


def match_benchmarks(
    city: str | None = None,
    avg_price: float | None = None,
    product_type: str | None = None,
    top_k: int = 5,
    lib: dict | None = None,
    design_ref_k: int = 3,
    mode: str = "peer",
    recent_bias: bool = False,
) -> dict:
    lib = lib or load_library()
    products = [case for case in lib["cases"] if case.get("category") == "现象级产品"]
    firms = [case for case in lib["cases"] if case.get("category") == "设计机构"]
    items = [_match_item(case, lib, relevance)
             for relevance, case in _rank(products, city, product_type, mode, recent_bias)[:top_k]]
    design_ref = [_match_item(case, lib, relevance)
                  for relevance, case in _rank(firms, city, product_type, mode, recent_bias)[:design_ref_k]]
    return {
        "status": "ok", "source": "benchmark_library", "mode": mode, "recent_bias": recent_bias,
        "note": "对标主体=现象级产品(items);design_ref=设计机构(落地参考)。scores 为分析师估计",
        "count": len(items), "items": items, "design_ref": design_ref,
    }


# ── 2) dimension frontier (ceiling) ────────────────────────
def dimension_frontier(
    lib: dict | None = None,
    recent_year: int | None = None,
    categories=("现象级产品",),
) -> dict:
    lib = lib or load_library()
    cases = [
        case for case in lib["cases"]
        if (categories is None or case.get("category") in categories)
        and (recent_year is None or case.get("year", 0) >= recent_year)
    ]
    result = {}
    for dim in DIMS:
        with_dim = [case for case in cases if dim in case.get("scores", {})]
        if not with_dim:
            continue
        best = max(with_dim, key=lambda case: case["scores"][dim])
        moves = best.get("moves", {})
        tactic = moves.get(dim, "") if isinstance(moves, dict) else ""
        result[dim] = {
            "cn": lib["dimensions"][dim]["cn"], "score": best["scores"][dim],
            "case": best["name"], "tactic": tactic,
        }
    return result


# ── 3) trend momentum ──────────────────────────────────────
def trend_analysis(lib: dict | None = None, categories=("现象级产品",)) -> dict:
    lib = lib or load_library()
    cases = [case for case in lib["cases"] if categories is None or case.get("category") in categories]
    result = {}
    for dim in DIMS:
        with_dim = [case for case in cases if dim in case.get("scores", {})]
        points = [(float(case["year"]), case["scores"][dim]) for case in with_dim]
        slope = _slope(points)
        recent = [case["scores"][dim] for case in with_dim if case.get("year", 0) >= 2023]
        momentum = "上升" if slope > 0.12 else ("温和上升" if slope > 0.02 else "成熟/平稳")
        result[dim] = {
            "cn": lib["dimensions"][dim]["cn"], "slope": round(slope, 3),
            "recent_mean": round(sum(recent) / len(recent), 2) if recent else None,
            "momentum": momentum,
        }
    return result


# ── 4) next-generation extrapolation ───────────────────────
def extrapolate_next_gen(
    city: str | None = None,
    product_type: str | None = None,
    current_scores: dict | None = None,
    horizon: int = 2,
    top_n: int = 5,
    lib: dict | None = None,
) -> dict:
    lib = lib or load_library()
    frontier = dimension_frontier(lib)
    trend = trend_analysis(lib)
    products = [case for case in lib["cases"] if case.get("category") == "现象级产品"]
    all_scores: dict[str, list[float]] = {}
    for dim in DIMS:
        values = [case["scores"][dim] for case in products if dim in case.get("scores", {})]
        if values:
            all_scores[dim] = values

    gap, momentum, target = {}, {}, {}
    for dim in DIMS:
        baseline = (current_scores or {}).get(dim, _median(all_scores[dim]))
        gap[dim] = max(0.0, frontier[dim]["score"] - baseline)
        momentum[dim] = max(0.0, trend[dim]["slope"])
        target[dim] = round(min(10.0, frontier[dim]["score"] + momentum[dim] * horizon), 1)

    norm_gap, norm_momentum = _minmax(gap), _minmax(momentum)
    priority = {dim: 0.6 * norm_gap[dim] + 0.4 * norm_momentum[dim] for dim in DIMS}

    moves = []
    for dim in sorted(DIMS, key=lambda item: -priority[item])[:top_n]:
        moves.append({
            "dimension": lib["dimensions"][dim]["cn"],
            "priority": round(priority[dim], 2),
            "target_score": target[dim],
            "gap_to_frontier": round(gap[dim], 1),
            "momentum": trend[dim]["momentum"],
            "benchmark_to_study": frontier[dim]["case"],
            "frontier_tactic": frontier[dim]["tactic"] or "(见该案例 key_tactics)",
        })

    hot = set(sorted(DIMS, key=lambda item: -momentum[item])[:4])
    combos = [text for dims, text in COMBO_LIBRARY if len(dims & hot) >= 2] or [
        text for _, text in COMBO_LIBRARY[:2]
    ]

    tier0 = [
        {"dim": _cn(lib, dim), "ceiling": frontier[dim]["score"],
         "holder": frontier[dim]["case"], "tactic": frontier[dim]["tactic"] or "(见 key_tactics)"}
        for dim in sorted(DIMS, key=lambda item: -gap[item])[:top_n]
    ]
    tier1 = _proven_combos(lib)
    gap_set = set(sorted(DIMS, key=lambda item: -gap[item])[:5])
    paradigms = sorted(PARADIGMS, key=lambda paradigm: -len(set(paradigm["dims"]) & gap_set))
    tier2 = [
        {"paradigm": paradigm["name"],
         "dims": [_cn(lib, dim) for dim in paradigm["dims"]],
         "rationale": paradigm["rationale"]}
        for paradigm in paradigms[:3]
    ]

    return {
        "status": "ok",
        "scope": {"city": city, "product_type": product_type, "horizon_years": horizon,
                  "baseline": "self_assessment" if current_scores else "library_median"},
        "next_version_vector": {lib["dimensions"][dim]["cn"]: target[dim] for dim in DIMS},
        "priority_moves": moves,
        "emerging_combos": combos[:3],
        "future_ladder": {
            "tier0_frontier": tier0,
            "tier1_proven_combos": tier1,
            "tier2_paradigm": tier2,
            "_note": "Tier0=今天就存在的单维天花板; Tier1=案例库已验证的跨维组合(≥2案例共现); Tier2=白地+命名范式(推演假设)。",
        },
        "disclaimer": "目标向量与组合为基于案例库 scores 的分析推演,非市场实测;scores 系分析师估计。",
    }


# ── validation & stats (support continuous updates) ────────
def validate(lib: dict | None = None) -> dict:
    lib = lib or load_library()
    issues: list[str] = []
    seen_ids: set[str] = set()
    for case in lib["cases"]:
        case_id = case.get("id", "<no-id>")
        if case_id in seen_ids:
            issues.append(f"{case_id}: id 重复")
        seen_ids.add(case_id)
        missing = [dim for dim in DIMS if dim not in case.get("scores", {})]
        if missing:
            issues.append(f"{case_id}: 缺维度 {missing}")
        bad = [dim for dim, value in case.get("scores", {}).items() if not (0 <= value <= 10)]
        if bad:
            issues.append(f"{case_id}: 分数越界 {bad}")
        for block, allowed in (("layer2_ops", DIMS_OPS), ("layer3_proof", DIMS_RESULT)):
            block_value = case.get(block)
            if not isinstance(block_value, dict):
                # list/None blocks carry no numeric scores; nothing to validate.
                continue
            for key, value in block_value.items():
                if key not in allowed:
                    issues.append(f"{case_id}: {block} 未知维度 {key}")
                elif isinstance(value, (int, float)) and not (0 <= value <= 10):
                    issues.append(f"{case_id}: {block}.{key} 分数越界 {value}")
        if case.get("signature_dims") and any(dim not in DIMS_ALL for dim in case["signature_dims"]):
            issues.append(f"{case_id}: signature_dims 含未知维度")
        if not case.get("sources") or not any(source.get("url") for source in case["sources"]):
            issues.append(f"{case_id}: 缺少带 URL 的溯源")
        for field in ("name", "data_quality", "confidence"):
            if not case.get(field):
                issues.append(f"{case_id}: 缺字段 {field}")
    declared = lib.get("meta", {}).get("case_count")
    if declared is not None and declared != len(lib["cases"]):
        issues.append(f"meta.case_count={declared} 与实际 {len(lib['cases'])} 不一致")
    return {"ok": not issues, "case_count": len(lib["cases"]), "issues": issues}


def stats(lib: dict | None = None) -> dict:
    lib = lib or load_library()
    cases = lib["cases"]
    global_count = sum(
        1 for case in cases
        if case.get("city") in ("东京", "新加坡", "米兰", "迪拜") or "安缦" in case["name"]
    )
    data_quality: dict[str, int] = {}
    for case in cases:
        quality = case.get("data_quality", "?")
        data_quality[quality] = data_quality.get(quality, 0) + 1
    years = [case.get("year") for case in cases if case.get("year")]
    return {
        "case_count": len(cases),
        "domestic": len(cases) - global_count, "global": global_count,
        "by_data_quality": data_quality,
        "year_range": [min(years), max(years)] if years else None,
        "avg_confidence": round(sum(case.get("confidence", 0) for case in cases) / len(cases), 2),
        "schema_version": lib.get("meta", {}).get("schema_version"),
    }


__all__ = [
    "COMBO_LIBRARY",
    "DIMS",
    "DIMS_ALL",
    "decode_play",
    "dimension_frontier",
    "extrapolate_next_gen",
    "load_library",
    "match_benchmarks",
    "phenom_score",
    "signature_dims",
    "stats",
    "trend_analysis",
    "validate",
]
