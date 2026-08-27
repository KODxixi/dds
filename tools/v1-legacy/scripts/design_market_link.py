"""
设计×市场联动引擎 (Design × Market Linkage) — DDS 护城河层

把"设计动作"映射到"市场结果"。这是开发商爬不到、纯数据商也做不出的一层:
他们有市场数据没有设计语言,DDS 两边都有。

双锚:
  - 本地锚: 本地竞品中设计特征 ↔ 价/去化 的相关(样本不足时退化)
  - 标杆锚: 现象级 benchmark 三层 + decode(设计动作 → 已达成的市场结果)

正向:溢价分解(每个设计动作贡献多少溢价)。
逆向:目标 → 设计任务书(要达到目标价/去化,按 ROI 排序该做哪些设计动作)。
另出:设计杠杆器数据(交互式"拨动设计选择看市场变化")。

诚实边界:因果为相关 + 推演,非市场实测;每项带 来源 / 置信 / 可迁移条件。

================  输出契约 design_market_link  ================
{
  "status": "ok" | "degraded" | "skipped" | "error",
  "anchor": "both" | "benchmark_only" | "local_only",
  "base_price": float,                              # 区位基准价(周边均价, 元/㎡)
  "design_premium_pct": float,                      # 设计可归因溢价(相对基准, 0-1)
  "premium_decomposition": {                        # 正向:溢价拆解
    "components": [
      {"dimension": str, "contribution_pct": float, "contribution_cny": float,
       "outcome": str, "evidence": "benchmark"|"local",
       "benchmark_case": str|None, "confidence": float, "transfer": str}
    ],
    "projected_price": {"low": float, "mid": float, "high": float},
    "note": str
  },
  "reverse_brief": {                                # 逆向:目标 → 设计任务书
    "target": {"price": float|None, "note": str},
    "moves": [
      {"dimension": str, "action": str, "expected_gain": str,
       "roi_rank": int, "benchmark_to_study": str, "confidence": float}
    ]
  },
  "levers": [                                       # 设计杠杆器(前端交互)
    {"dimension": str, "current": float, "frontier": float,
     "steps": [{"level": float, "delta_price_pct": float, "delta_price_cny": float}]}
  ],
  "disclaimer": str
}
==============================================================
"""
from __future__ import annotations

LOCAL_MIN_SAMPLE = 15          # 本地锚最低样本量,不足退化为纯标杆锚
DESIGN_PREMIUM_CAP = 0.25      # 设计可归因溢价的合理上限(推演兜底)
DEFAULT_DESIGN_PREMIUM = 0.10  # 无标杆溢价信号时的保守假设

# 设计维度(中文名) → 主要市场结果(可读定性映射;标杆锚的解释层)
DIM_OUTCOME = {
    "示范区设计": "首入印象 → 转化/去化",
    "展示区设计": "交付即所见 → 信任/去化",
    "外立面风格": "公建化立面 → 单价溢价/品牌",
    "下沉会所": "酒店化会所 → 客群升级/溢价",
    "景观风格": "景观体系 → 溢价/去化",
    "架空层": "全龄架空 → 居住体验/去化",
    "抬板策略": "抬板礼序 → 尊贵感/溢价",
    "户型设计": "户型创新 → 去化/客群匹配",
    "政策导向": "政策红利 → 户型代差/单价",
    "在地人文叙事": "在地叙事 → 心智/溢价",
    "IP/内容叙事": "内容运营 → 心智/复购",
    "社群运营": "社群运营 → 黏性/口碑去化",
    "自持商业": "自持配套 → 持续溢价",
    "客群心智占位": "品类第一心智 → 保值/溢价",
    "媒体声量/打卡量": "声量 → 蓄客/去化提速",
}


def _outcome(cn: str) -> str:
    return DIM_OUTCOME.get(cn, "综合 → 溢价/去化")


def _round(x, n=2):
    try:
        return round(float(x), n)
    except Exception:
        return x


def analyze(parcel_report: dict, client_goal: dict,
            high_end_benchmarks: dict | None = None,
            competitors: list | None = None,
            base_price: float | None = None,
            benchmark_premium_ratio: float | None = None) -> dict:
    """生成 design_market_link(见模块顶部契约)。任何缺失都优雅降级,不抛错。"""
    hb = high_end_benchmarks or {}
    competitors = competitors or []
    items = hb.get("items") or []

    # 无标杆 → 跳过(前端据此不渲染该段)
    if hb.get("status") not in ("ok", None) or not items:
        return {"status": "skipped", "anchor": "benchmark_only",
                "disclaimer": "对标层未就绪,设计×市场联动暂不可用。"}

    # 基准价
    if base_price is None:
        base_price = (parcel_report.get("market") or {}).get("avg_price") or 0
    base_price = float(base_price or 0)

    # 锚:本地样本是否够 → 决定 anchor / status
    sample = len(competitors)
    local_ok = sample >= LOCAL_MIN_SAMPLE
    anchor = "both" if local_ok else "benchmark_only"
    status = "ok" if local_ok else "degraded"

    # 设计可归因溢价包络(标杆锚):用对标溢价倍数推演,封顶
    if benchmark_premium_ratio and benchmark_premium_ratio > 1.0:
        env = min(DESIGN_PREMIUM_CAP, benchmark_premium_ratio - 1.0)
    else:
        env = DEFAULT_DESIGN_PREMIUM

    # 取头号标杆的尖峰维度作为设计驱动项
    top = items[0]
    drivers = top.get("signature") or top.get("strong_dims") or []
    drivers = drivers[:4] or ["设计综合"]
    conf = float(top.get("confidence", 0.7) or 0.7)
    decode = top.get("decode") or {}
    transfer = decode.get("transfer", "需评估用地条件/容积率让渡与造价投入。")

    per = env / len(drivers)
    components = [{
        "dimension": d,
        "contribution_pct": _round(per, 4),
        "contribution_cny": _round(base_price * per, 0),
        "outcome": _outcome(d),
        "evidence": "benchmark",
        "benchmark_case": top.get("name"),
        "confidence": _round(conf, 2),
        "transfer": transfer,
    } for d in drivers]

    projected = {
        "low": _round(base_price * (1 + env * 0.5), 0),
        "mid": _round(base_price * (1 + env), 0),
        "high": _round(base_price * (1 + env * 1.3), 0),
    }
    note = ("本地样本 %d 条 ≥ 阈值,本地锚可用(回归待数据管线补齐);标杆锚已生效。" % sample
            if local_ok else
            "本地样本 %d 条 < %d,暂用纯标杆锚;本地相关待数据补齐。" % (sample, LOCAL_MIN_SAMPLE))

    # 逆向任务书:复用 next_gen.priority_moves(已按 ROI 排序)
    ng = hb.get("next_gen") or {}
    moves = []
    for i, m in enumerate((ng.get("priority_moves") or [])[:5], 1):
        tgt = m.get("target_score")
        gap = m.get("gap_to_frontier")
        moves.append({
            "dimension": m.get("dimension"),
            "action": (m.get("frontier_tactic") or "")[:60],
            "expected_gain": "目标分 %s(距天花板 %s)· %s" % (tgt, gap, _outcome(m.get("dimension", ""))),
            "roi_rank": i,
            "benchmark_to_study": m.get("benchmark_to_study"),
            "confidence": _round(conf, 2),
        })
    tprice = client_goal.get("expected_price") or client_goal.get("price")
    reverse_brief = {
        "target": {"price": tprice, "note": "目标价/去化驱动的设计动作清单,按投入产出排序。"},
        "moves": moves,
    }

    # 设计杠杆器:每个驱动维度 当前→天花板 的价差(推演,供前端交互拨动)
    levers = []
    pm_by_dim = {m.get("dimension"): m for m in (ng.get("priority_moves") or [])}
    for d in drivers:
        m = pm_by_dim.get(d, {})
        frontier = float(m.get("target_score", 9.0) or 9.0)
        current = max(0.0, frontier - float(m.get("gap_to_frontier", 3.0) or 3.0))
        step_pct = per  # 该维度拉满≈贡献 per 的溢价
        levers.append({
            "dimension": d,
            "current": _round(current, 1),
            "frontier": _round(frontier, 1),
            "steps": [{
                "level": _round(frontier, 1),
                "delta_price_pct": _round(step_pct, 4),
                "delta_price_cny": _round(base_price * step_pct, 0),
            }],
        })

    return {
        "status": status,
        "anchor": anchor,
        "base_price": _round(base_price, 0),
        "design_premium_pct": _round(env, 4),
        "premium_decomposition": {
            "components": components,
            "projected_price": projected,
            "note": note,
        },
        "reverse_brief": reverse_brief,
        "levers": levers,
        "disclaimer": "设计→市场为相关 + 推演,非市场实测;溢价分解为按标杆强度的分配估计,"
                      "每项的可迁移性需结合用地条件/造价单独评估。",
    }


if __name__ == "__main__":
    import json
    demo_hb = {
        "status": "ok",
        "items": [{
            "name": "融创·苏州桃花源", "confidence": 0.85,
            "signature": ["景观风格", "在地人文叙事", "外立面风格"],
            "decode": {"transfer": "需让渡约 X% 地面强度 + 香山帮工艺投入。"},
        }],
        "next_gen": {"priority_moves": [
            {"dimension": "外立面风格", "target_score": 9.3, "gap_to_frontier": 1.5,
             "benchmark_to_study": "融创·外滩壹号院", "frontier_tactic": "城市渐变艺术立面"},
            {"dimension": "在地人文叙事", "target_score": 9.2, "gap_to_frontier": 1.2,
             "benchmark_to_study": "苏州桃花源", "frontier_tactic": "香山帮国匠营造"},
        ]},
    }
    out = analyze({"market": {"avg_price": 60000}}, {"expected_price": 75000},
                  demo_hb, competitors=[], base_price=60000, benchmark_premium_ratio=1.22)
    print(json.dumps(out, ensure_ascii=False, indent=2))
