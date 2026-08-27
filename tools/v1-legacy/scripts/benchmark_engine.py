"""
高端/全球化对标引擎 (Benchmark Engine)

读取 data/benchmark_library.json,提供:
1) match_benchmarks  —— 给地块/定位匹配最相关的旗舰标杆(DDS 证据注入,供 value_agent 调用)
2) dimension_frontier —— 十大维度的"标杆前沿"(谁是天花板)
3) trend_analysis    —— 各维度随时间的动量(市场往哪走)
4) extrapolate_next_gen —— 下一代产品线推演(目标向量 + 优先动作 + 跨维组合)

纯标准库实现,无第三方依赖。scores 为分析师量化估计,推演结论属分析推演(见 JSON meta)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB_PATH = ROOT / "data" / "benchmark_library.json"

# 维度顺序(与 JSON 对齐)
DIMS = ["demo_zone", "floorplan", "podium_lift", "display_zone", "sunken_club",
        "policy_leverage", "open_floor", "landscape", "facade", "narrative"]

# ── 维度分层 (schema v3) ─────────────────────────────────────
# L1 产品硬件力 = 原十维(对标主体物理产品力; frontier/trend/extrapolate 默认仍按此层,向后兼容)
DIMS_HW = DIMS
# L2 内容运营力(新增·可选): "为何有人来"——现象级真正引擎(阿那亚/天目里靠这层)
DIMS_OPS = ["ip_narrative", "community_ops", "retail_holding", "service_system", "lifestyle_brand"]
# L3 资本结果力(新增·可选): 市场用脚投票; 公开度不一,缺采可填字符串"待接入"(不计分)
DIMS_RESULT = ["sellthrough_speed", "premium_ability", "resale_value", "mindshare", "media_volume"]
DIMS_ALL = DIMS_HW + DIMS_OPS + DIMS_RESULT
SIGNATURE_THRESHOLD = 9.5  # 单维 ≥ 此值视为"杀手锏"尖峰

# 城市 → 能级(仅用于相关性匹配,缺省 1.5)
CITY_TIER = {
    "上海": 1, "北京": 1, "深圳": 1, "广州": 1,
    "杭州": 1.5, "南京": 1.5, "成都": 1.5, "武汉": 1.5, "重庆": 1.5,
    "苏州": 1.5, "长沙": 1.5, "青岛": 1.5, "三亚": 1.5,
}

# 跨维组合知识(可读命名;键为参与维度集合)——下一代产品假设的"翻译器"
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

# 命名范式向量(未来三阶梯 Tier2; 含 L2/L3 维度, 标注为推演假设)
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


def load_library(path: Path | None = None) -> dict:
    p = path or LIB_PATH
    with open(p, encoding="utf-8") as f:   # 强制 utf-8,避免 GBK 乱码
        return json.load(f)


# ── 工具函数 ──────────────────────────────────────────────
def _minmax(values: dict) -> dict:
    """把一组 dim->值 归一化到 0-1;全相等时返回 0.5"""
    vs = list(values.values())
    lo, hi = min(vs), max(vs)
    if hi - lo < 1e-9:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def _slope(points: list[tuple[float, float]]) -> float:
    """最小二乘斜率(x=年份, y=分);点不足返回 0"""
    n = len(points)
    if n < 2:
        return 0.0
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    den = sum((x - mx) ** 2 for x, _ in points)
    if den < 1e-9:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in points) / den


def _median(nums: list[float]) -> float:
    s = sorted(nums)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _top_dims(scores: dict, k: int = 3) -> list[str]:
    return [d for d, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:k]]


def _all_scores(case: dict) -> dict:
    """合并 L1 scores + 可选 L2 layer2_ops + L3 layer3_proof(仅数值项)为单一 dim->score。"""
    out = dict(case.get("scores", {}) or {})
    for blk in ("layer2_ops", "layer3_proof"):
        blk_val = case.get(blk)
        if isinstance(blk_val, dict):
            for k, v in blk_val.items():
                if isinstance(v, (int, float)):
                    out[k] = v
        # list/None → skip, cannot extract numeric scores
    return out


def signature_dims(case: dict, threshold: float = SIGNATURE_THRESHOLD) -> list[str]:
    """案例的"尖峰/杀手锏"维度: 跨全部已评维度(L1+L2+L3) ≥ 阈值者。
    优先用案例自带 signature_dims 字段(人工标注),否则从分数推导。"""
    if case.get("signature_dims"):
        return list(case["signature_dims"])
    allsc = _all_scores(case)
    return [d for d, v in sorted(allsc.items(), key=lambda kv: -kv[1]) if v >= threshold]


def phenom_score(case: dict) -> float:
    """现象级度 = 尖峰(最高维) × 完成度基线(其余维中位数)/10。
    奖励"一招9.9"而非"样样7分"; 缺 L2/L3 不惩罚(只按已评维度)。"""
    allsc = _all_scores(case)
    if not allsc:
        return 0.0
    vals = sorted(allsc.values(), reverse=True)
    spike = vals[0]
    base = _median(vals[1:]) if len(vals) > 1 else vals[0]
    return round(spike * base / 10.0, 2)


def _cn(lib: dict, d: str) -> str:
    return (lib.get("dimensions", {}).get(d) or {}).get("cn", d)


def _layer_of(lib: dict, d: str) -> str:
    for L, ds in (lib.get("dimension_layers") or {}).items():
        if isinstance(ds, list) and d in ds:
            return L
    return "L1_hardware"


def decode_play(case: dict, lib: dict | None = None) -> dict:
    """把标杆拆成 信号(做了什么)→机制(为何有效)→可迁移性(本案需付出什么)。
    规则化简版: 基于 key_tactics + 尖峰维度所在层。"""
    lib = lib or load_library()
    sig = signature_dims(case) or _top_dims(case.get("scores", {}))
    sig_cn = [_cn(lib, d) for d in sig]
    tactics = case.get("key_tactics", [])
    signal = "；".join(tactics[:2]) or (case.get("headline") or "")
    L = _layer_of(lib, sig[0]) if sig else "L1_hardware"
    mech = {
        "L1_hardware": "靠物理产品力建立记忆点与首入印象,溢价来自'看得见的好'。",
        "L2_ops": "靠内容/社群/自持运营持续创造到访与黏性,溢价来自'持续被讨论'。",
        "L3_result": "已转化为市场结果(心智/声量/去化),壁垒来自'先发心智'。",
    }.get(L, "")
    cost = {"L1_hardware": "示范区与立面造价、工艺标准",
            "L2_ops": "运营前置投入与回收周期、内容/社群团队",
            "L3_result": "品牌与时间积累、先发卡位"}.get(L, "投入评估")
    transfer = f"本案要复刻其【{sig_cn[0] if sig_cn else '强项'}】,需评估:用地条件/容积率让渡 + {cost}。"
    return {"signal": signal, "mechanism": mech, "transfer": transfer, "signature": sig_cn}


def _proven_combos(lib: dict, min_score: float = 8.5, top: int = 4) -> list[dict]:
    """从现象级产品里挖'高分共现'的维度组合(数据驱动的已验证打法)。"""
    from itertools import combinations
    prods = [c for c in lib["cases"] if c.get("category") == "现象级产品"]
    cnt, eg = {}, {}
    for c in prods:
        hot = sorted(d for d, v in _all_scores(c).items() if v >= min_score)
        for combo in combinations(hot, 2):
            cnt[combo] = cnt.get(combo, 0) + 1
            eg.setdefault(combo, c["name"])
    out = []
    for combo, num in sorted(cnt.items(), key=lambda kv: -kv[1]):
        if num < 2:
            continue
        out.append({"dims": [_cn(lib, d) for d in combo], "count": num, "example": eg[combo]})
        if len(out) >= top:
            break
    return out


# ── 1) 标杆匹配(证据注入)─────────────────────────────────
def _rank(cases, city, product_type, mode="peer", recent_bias=False):
    """按 现象级度(phenom) + 能级关系(随 mode) + 产品类型 + 可信度 + 可迁移设计策略 排序,返回 [(rel, case), ...]。
    mode: peer 同侪近似 / aspire 主动越级(拉高能级志向) / cross 跨界(看现象级度,弱化品类)。"""
    target_tier = CITY_TIER.get(city or "", 1.5)
    pt = product_type or ""
    # 产品类型模糊匹配扩展: 改善 → 品质改善/高端改善/功能改善
    pt_expanded = {pt}
    if "改善" in pt:
        pt_expanded.update(["品质改善", "高端改善", "功能改善", "改善产品"])
    if "豪宅" in pt or "顶豪" in pt:
        pt_expanded.update(["顶豪", "高端改善", "大平层"])
    ranked = []
    for c in cases:
        ph = phenom_score(c)
        ctier = c.get("city_tier", 1.5)
        if mode == "aspire":
            tier_rel = max(0.0, target_tier - ctier)             # 比目标更高线 → 加分
        elif mode == "cross":
            tier_rel = 0.0                                        # 不看能级
        else:
            tier_rel = max(0.0, 1.0 - abs(ctier - target_tier))  # 同侪近似
        rel = tier_rel
        # 产品类型匹配(模糊扩展)
        c_pt = c.get("product_type", "")
        c_tags = c.get("positioning_tags", [])
        c_headline = c.get("headline", "")
        pt_hit = False
        for pt_term in pt_expanded:
            if pt_term in c_pt or any(pt_term in t for t in c_tags) or pt_term in c_headline:
                pt_hit = True
                break
        if mode == "cross":
            rel += 0.3 if pt_hit else 0.0                        # 跨界:同类只轻微加分,鼓励异类
        elif pt_hit:
            rel += 1.5
        # 可迁移设计策略加成: 跨城市通用的产品创新应获得额外相关性
        # 四代宅/空中花园/退台/台地/得房率策略 → 不受城市匹配限制
        transferable_strategies = {
            "四代": 0.6, "第四代": 0.6, "空中花园": 0.5, "空中庭院": 0.5,
            "错层露台": 0.5, "退台": 0.4, "台地": 0.4, "垂直绿化": 0.4,
            "立体生态": 0.4, "LDKG": 0.3, "挑高露台": 0.4, "独立入户": 0.3,
            "高赠送": 0.5, "高得房率": 0.5, "不计容": 0.5,
            "四代宅": 0.6, "抬板": 0.4, "层层花园": 0.4,
        }
        strategy_boost = 0.0
        c_text = c_pt + " " + " ".join(c_tags) + " " + c_headline
        for kw, boost in transferable_strategies.items():
            if kw in c_text:
                strategy_boost = max(strategy_boost, boost)  # 取最高单项加成
        rel += strategy_boost
        rel += 0.3 * c.get("confidence", 0.5)
        rel += 0.25 * ph                                         # 现象级度并入相关性(尖峰盘上浮)
        if recent_bias:
            rel += 0.2 * max(0, c.get("year", 0) - 2020)         # 设计师视角:越近年(2021+)越靠前
        ranked.append((rel, ph, c.get("confidence", 0.5), c))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [(t[0], t[3]) for t in ranked]


def _match_item(c, lib, rel):
    return {
        "name": c["name"], "category": c.get("category"),
        "developer": c.get("developer"), "designer": c.get("designer"),
        "year": c.get("year"), "headline": c.get("headline"),
        "relevance": round(rel, 2),
        "phenom": phenom_score(c),
        "strong_dims": [lib["dimensions"][d]["cn"] for d in _top_dims(c["scores"])],
        "signature": [_cn(lib, d) for d in signature_dims(c)],
        "key_tactics": c.get("key_tactics", []),
        "decode": decode_play(c, lib),
        "data_quality": c.get("data_quality"), "sources": c.get("sources", []),
    }


def match_benchmarks(city: str | None = None, avg_price: float | None = None,
                     product_type: str | None = None, top_k: int = 5,
                     lib: dict | None = None, design_ref_k: int = 3,
                     mode: str = "peer", recent_bias: bool = False) -> dict:
    """匹配最相关标杆。对标主体=现象级产品(items);另附 design_ref=设计机构(落地参考)。
    mode: peer 同侪 / aspire 越级(拉高能级志向) / cross 跨界借招。"""
    lib = lib or load_library()
    products = [c for c in lib["cases"] if c.get("category") == "现象级产品"]
    firms = [c for c in lib["cases"] if c.get("category") == "设计机构"]
    items = [_match_item(c, lib, rel) for rel, c in _rank(products, city, product_type, mode, recent_bias)[:top_k]]
    design_ref = [_match_item(c, lib, rel) for rel, c in _rank(firms, city, product_type, mode, recent_bias)[:design_ref_k]]
    return {"status": "ok", "source": "benchmark_library", "mode": mode, "recent_bias": recent_bias,
            "note": "对标主体=现象级产品(items);design_ref=设计机构(落地参考)。scores 为分析师估计",
            "count": len(items), "items": items, "design_ref": design_ref}


# ── 2) 维度前沿(天花板)──────────────────────────────────
def dimension_frontier(lib: dict | None = None, recent_year: int | None = None,
                       categories=("现象级产品",)) -> dict:
    """每个维度的最高分案例(默认只看现象级产品=对标主体;categories=None 看全部)。"""
    lib = lib or load_library()
    cases = [c for c in lib["cases"]
             if (categories is None or c.get("category") in categories)
             and (recent_year is None or c.get("year", 0) >= recent_year)]
    out = {}
    for d in DIMS:
        # 跳过该维度缺失的案例(向后兼容,避免新增案例缺维导致崩溃)
        with_dim = [c for c in cases if d in c.get("scores", {})]
        if not with_dim:
            continue
        best = max(with_dim, key=lambda c: c["scores"][d])
        moves = best.get("moves", {})
        tactic = moves.get(d, "") if isinstance(moves, dict) else ""
        out[d] = {"cn": lib["dimensions"][d]["cn"], "score": best["scores"][d],
                  "case": best["name"], "tactic": tactic}
    return out


# ── 3) 趋势动量(市场往哪走)──────────────────────────────
def trend_analysis(lib: dict | None = None, categories=("现象级产品",)) -> dict:
    """各维度随年份的斜率 + 近期均值,判断动量(默认只看现象级产品)。"""
    lib = lib or load_library()
    cases = [c for c in lib["cases"] if categories is None or c.get("category") in categories]
    out = {}
    for d in DIMS:
        with_dim = [c for c in cases if d in c.get("scores", {})]
        pts = [(float(c["year"]), c["scores"][d]) for c in with_dim]
        slope = _slope(pts)
        recent = [c["scores"][d] for c in with_dim if c.get("year", 0) >= 2023]
        tag = "上升" if slope > 0.12 else ("温和上升" if slope > 0.02 else "成熟/平稳")
        out[d] = {"cn": lib["dimensions"][d]["cn"], "slope": round(slope, 3),
                  "recent_mean": round(sum(recent) / len(recent), 2) if recent else None,
                  "momentum": tag}
    return out


# ── 4) 下一代产品线推演 ───────────────────────────────────
def extrapolate_next_gen(city: str | None = None, product_type: str | None = None,
                         current_scores: dict | None = None, horizon: int = 2,
                         top_n: int = 5, lib: dict | None = None) -> dict:
    """推演下一版本(未来)产品向量 + 优先动作 + 跨维组合。

    current_scores: 自家现有产品的维度自评(可选);缺省用案例库中位数做基线。
    horizon: 外推年数。
    """
    lib = lib or load_library()
    frontier = dimension_frontier(lib)
    trend = trend_analysis(lib)
    # 基线中位数也只看现象级产品(对标主体)
    prods = [c for c in lib["cases"] if c.get("category") == "现象级产品"]
    all_scores = {}
    for d in DIMS:
        vals = [c["scores"][d] for c in prods if d in c.get("scores", {})]
        if vals:
            all_scores[d] = vals

    gap, momentum, target = {}, {}, {}
    for d in DIMS:
        base = (current_scores or {}).get(d, _median(all_scores[d]))  # 基线
        gap[d] = max(0.0, frontier[d]["score"] - base)               # 与天花板的差距
        momentum[d] = max(0.0, trend[d]["slope"])                    # 上升动量
        target[d] = round(min(10.0, frontier[d]["score"] + momentum[d] * horizon), 1)  # 下一版本目标

    ng, nm = _minmax(gap), _minmax(momentum)
    priority = {d: 0.6 * ng[d] + 0.4 * nm[d] for d in DIMS}          # 差距优先 + 顺势而为

    moves = []
    for d in sorted(DIMS, key=lambda x: -priority[x])[:top_n]:
        moves.append({
            "dimension": lib["dimensions"][d]["cn"],
            "priority": round(priority[d], 2),
            "target_score": target[d],
            "gap_to_frontier": round(gap[d], 1),
            "momentum": trend[d]["momentum"],
            "benchmark_to_study": frontier[d]["case"],
            "frontier_tactic": frontier[d]["tactic"] or "(见该案例 key_tactics)",
        })

    # 取动量最高的维度,匹配可读的跨维组合(向后兼容键)
    hot = set(sorted(DIMS, key=lambda x: -momentum[x])[:4])
    combos = [txt for dims, txt in COMBO_LIBRARY if len(dims & hot) >= 2] or \
             [txt for _, txt in COMBO_LIBRARY[:2]]

    # ── 未来三阶梯(P1):天花板 → 已验证组合 → 未来范式 ──
    tier0 = [{"dim": _cn(lib, d), "ceiling": frontier[d]["score"],
              "holder": frontier[d]["case"], "tactic": frontier[d]["tactic"] or "(见 key_tactics)"}
             for d in sorted(DIMS, key=lambda x: -gap[x])[:top_n]]
    tier1 = _proven_combos(lib)
    gapset = set(sorted(DIMS, key=lambda x: -gap[x])[:5])
    para = sorted(PARADIGMS, key=lambda p: -len(set(p["dims"]) & gapset))
    tier2 = [{"paradigm": p["name"], "dims": [_cn(lib, x) for x in p["dims"]],
              "rationale": p["rationale"]} for p in para[:3]]

    return {
        "status": "ok",
        "scope": {"city": city, "product_type": product_type, "horizon_years": horizon,
                  "baseline": "self_assessment" if current_scores else "library_median"},
        "next_version_vector": {lib["dimensions"][d]["cn"]: target[d] for d in DIMS},
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


# ── 校验 & 统计(支撑持续更新)──────────────────────────────
def validate(lib: dict | None = None) -> dict:
    """校验案例库完整性:十维分数齐全且在0-10、必有带URL的溯源、必填字段。"""
    lib = lib or load_library()
    issues, ids = [], set()
    for c in lib["cases"]:
        cid = c.get("id", "<no-id>")
        if cid in ids:
            issues.append(f"{cid}: id 重复")
        ids.add(cid)
        miss = [d for d in DIMS if d not in c.get("scores", {})]
        if miss:
            issues.append(f"{cid}: 缺维度 {miss}")
        bad = [d for d, v in c.get("scores", {}).items() if not (0 <= v <= 10)]
        if bad:
            issues.append(f"{cid}: 分数越界 {bad}")
        # 可选 L2/L3 层(schema v3): 存在则校验键合法 + 数值项 0-10(允许字符串如"待接入")
        for blk, allowed in (("layer2_ops", DIMS_OPS), ("layer3_proof", DIMS_RESULT)):
            for k, v in (c.get(blk) or {}).items():
                if k not in allowed:
                    issues.append(f"{cid}: {blk} 未知维度 {k}")
                elif isinstance(v, (int, float)) and not (0 <= v <= 10):
                    issues.append(f"{cid}: {blk}.{k} 分数越界 {v}")
        if c.get("signature_dims") and any(d not in DIMS_ALL for d in c["signature_dims"]):
            issues.append(f"{cid}: signature_dims 含未知维度")
        if not c.get("sources") or not any(s.get("url") for s in c["sources"]):
            issues.append(f"{cid}: 缺少带 URL 的溯源")
        for f in ("name", "data_quality", "confidence"):
            if not c.get(f):
                issues.append(f"{cid}: 缺字段 {f}")
    declared = lib.get("meta", {}).get("case_count")
    if declared is not None and declared != len(lib["cases"]):
        issues.append(f"meta.case_count={declared} 与实际 {len(lib['cases'])} 不一致")
    return {"ok": not issues, "case_count": len(lib["cases"]), "issues": issues}


def stats(lib: dict | None = None) -> dict:
    """库统计:数量、国内/全球、数据质量分布、年份区间、平均可信度。"""
    lib = lib or load_library()
    cases = lib["cases"]
    glob = sum(1 for c in cases if c.get("city") in ("东京", "新加坡", "米兰", "迪拜")
               or "安缦" in c["name"])
    dq = {}
    for c in cases:
        dq[c.get("data_quality", "?")] = dq.get(c.get("data_quality", "?"), 0) + 1
    years = [c.get("year") for c in cases if c.get("year")]
    return {
        "case_count": len(cases),
        "domestic": len(cases) - glob, "global": glob,
        "by_data_quality": dq,
        "year_range": [min(years), max(years)] if years else None,
        "avg_confidence": round(sum(c.get("confidence", 0) for c in cases) / len(cases), 2),
        "schema_version": lib.get("meta", {}).get("schema_version"),
    }


# ── CLI ───────────────────────────────────────────────────
def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # 中文控制台防乱码
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(description="DDS 高端/全球化对标引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出全部标杆案例")
    sub.add_parser("frontier", help="十大维度天花板")
    sub.add_parser("trend", help="维度动量分析")
    sub.add_parser("validate", help="校验案例库完整性(更新后必跑)")
    sub.add_parser("stats", help="案例库统计")

    m = sub.add_parser("match", help="匹配最相关标杆")
    m.add_argument("--city")
    m.add_argument("--product-type")
    m.add_argument("--top", type=int, default=5)
    m.add_argument("--mode", choices=["peer", "aspire", "cross"], default="peer",
                   help="peer 同侪 / aspire 越级 / cross 跨界")
    m.add_argument("--recent", action="store_true", help="近期偏好(设计师视角:偏向2021+现象级)")

    e = sub.add_parser("extrapolate", help="下一代产品线推演")
    e.add_argument("--city")
    e.add_argument("--product-type")
    e.add_argument("--horizon", type=int, default=2)
    e.add_argument("--current", help="自评:demo_zone=7,facade=6 ...逗号分隔", default="")

    s = sub.add_parser("show", help="查看单个案例")
    s.add_argument("--id", required=True)

    a = ap.parse_args(argv)
    lib = load_library()

    if a.cmd == "list":
        for c in lib["cases"]:
            print(f"[{c['id']}] {c['name']} | {c.get('year')} | {c.get('headline')} "
                  f"| 强项: {'/'.join(lib['dimensions'][d]['cn'] for d in _top_dims(c['scores']))}")
    elif a.cmd == "frontier":
        _print(dimension_frontier(lib))
    elif a.cmd == "trend":
        _print(trend_analysis(lib))
    elif a.cmd == "validate":
        _print(validate(lib))
    elif a.cmd == "stats":
        _print(stats(lib))
    elif a.cmd == "match":
        _print(match_benchmarks(a.city, None, a.product_type, a.top, lib, mode=a.mode, recent_bias=a.recent))
    elif a.cmd == "extrapolate":
        cur = {}
        for kv in [x for x in a.current.split(",") if "=" in x]:
            k, v = kv.split("=", 1)
            cur[k.strip()] = float(v)
        _print(extrapolate_next_gen(a.city, a.product_type, cur or None, a.horizon, lib=lib))
    elif a.cmd == "show":
        c = next((x for x in lib["cases"] if x["id"] == a.id), None)
        _print(c or {"error": f"未找到 id={a.id}"})


if __name__ == "__main__":
    main()
