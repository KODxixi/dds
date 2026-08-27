"""
升值/溢价引擎 (Premium / Appreciation Engine) — DDS 北极星层

溢价/升值是开发商、设计院、购房者唯一的共同目标:资产升值。
本引擎把它从"一个数字"升为"贯穿全系统的脊柱"——不新造孤岛,而是把已有各 agent
(价值/时间穿越/客群迁移/蓝图风险 + 设计×市场联动)拧成一条"升值推演链":

  入市溢价(六维驱动横截面分解) → 持有期升值曲线(纵向推演,带置信带) → 二手保值

【铁律 · 不造数】各驱动仅在有真实信号处量化;缺采维度标 status="待接入";总溢价锚定
已有的对标溢价倍数(分析师估计),不另臆造;曲线用真实板块价 CAGR 推演,全程标"推演 + 置信"。

================  输出契约 premium_engine  ================
{
  "status": "ok"|"degraded"|"skipped",
  "north_star": "资产升值",
  "base_price": float,
  "entry_premium": {                 # 入市溢价(六维驱动)
    "total_pct": float|None,         # 入市可归因溢价 %(锚定对标溢价倍数)
    "drivers": [ {"key","cn","pct","trend","evidence","confidence","status"} ],
    "note": str
  },
  "holding_curve": {                 # 持有期升值推演(index 以入市=100)
    "horizon_years": int, "cagr_pct": float|None,
    "points": [ {"year","index","low","high"} ],   # low/high = 置信带
    "basis": str, "confidence": float, "status": str
  },
  "resale_retention": {"y3_pct","y5_pct","status","source"},   # 二手保值
  "persona_view": {"developer","designer","buyer"},            # 一链三视图
  "risk_to_appreciation": [ {"risk","level","trigger"} ],      # 升值下行风险
  "disclaimer": str
}
==========================================================
"""
from __future__ import annotations

PREMIUM_CAP = 0.25     # 入市可归因溢价合理上限(锚定对标溢价倍数时封顶)
HORIZON_YEARS = 10     # 升值曲线推演年限


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def run_premium_engine(parcel_report: dict, client_goal: dict,
                       value: dict | None = None, abm: dict | None = None,
                       migration: dict | None = None, time_travel: dict | None = None,
                       blueprint: dict | None = None,
                       multimodal: bool = False,
                       multimodal_weights: tuple = (0.5, 0.25, 0.25),
                       archlib_projects: list | None = None,
                       vlm_evidence: dict | None = None) -> dict:
    value = value or {}
    abm = abm or {}
    migration = migration or {}
    time_travel = time_travel or {}
    blueprint = blueprint or {}
    market = parcel_report.get("market") or {}
    base_price = _f(market.get("avg_price") or value.get("market_avg_price_cny") or 0)

    # ── 六维驱动:有真实信号才量化,余者待接入 ──
    drivers = []

    # 1 区位升值 ← 时间穿越·板块价趋势
    loc_cagr = (time_travel.get("price_trend") or {}).get("cagr_pct")
    drivers.append({
        "key": "location", "cn": "区位升值",
        "pct": None, "trend": (f"年化 {loc_cagr}%" if loc_cagr is not None else None),
        "evidence": "时间穿越·板块价趋势", "confidence": 0.6 if loc_cagr is not None else 0.0,
        "status": "ok" if loc_cagr is not None else "待接入",
    })

    # 2 设计溢价 ← 设计×市场联动(已量化)
    dml = value.get("design_market_link") or {}
    dpp = dml.get("design_premium_pct")
    comps = (dml.get("premium_decomposition") or {}).get("components") or []
    dconf = (comps[0].get("confidence") if comps else 0.6) or 0.6
    drivers.append({
        "key": "design", "cn": "设计溢价",
        "pct": round(dpp * 100, 1) if dpp else None,
        "trend": "设计动作驱动" if dpp else None,
        "evidence": "设计×市场联动", "confidence": dconf if dpp else 0.0,
        "status": "ok" if dpp else "待接入",
    })

    # 3 政策代差(第四代/好房子)— Schema v2.0 有 is_4th_gen/绿色建筑等级/装配率 字段；
    #    管道六(政策与金融)将接入 LPR/限购/第四代住宅政策红利信号
    parcel_data = parcel_report.get("parcel_data") or {}
    is_4th = parcel_data.get("是否四代住宅") or parcel_data.get("is_4th_gen")
    green_grade = parcel_data.get("绿色建筑等级") or parcel_data.get("green_building_grade")
    assembly = parcel_data.get("装配率_pct") or parcel_data.get("assembly_rate")
    policy_signals = [s for s in [is_4th, green_grade, assembly] if s]
    if policy_signals:
        drivers.append({"key": "policy", "cn": "政策代差", "pct": None,
                        "trend": "第四代/好房子红利",
                        "evidence": f"产品字段: 四代宅={is_4th}, 绿建={green_grade}, 装配率={assembly}",
                        "confidence": 0.4, "status": "ok"})
    else:
        drivers.append({"key": "policy", "cn": "政策代差", "pct": None,
                        "trend": "第四代/好房子红利",
                        "evidence": "待管道六(政策金融)接入;Schema v2.0 已有字段 is_4th_gen/green_building_grade/assembly_rate",
                        "confidence": 0.0, "status": "待接入"})

    # 4 品牌运营 ← Schema v2.0「十五·品牌与信用」22 列 + 管道四(开发商信用)；
    #    信用评级/交付率/延期率/投诉次数 将提供真实信号
    dev_credit = parcel_data.get("开发商信用评级") or parcel_data.get("developer_credit_rating")
    dev_delivery = parcel_data.get("开发商交付率_pct") or parcel_data.get("developer_delivery_rate")
    if dev_credit or dev_delivery:
        drivers.append({"key": "brand", "cn": "品牌运营", "pct": None,
                        "trend": "开发商信用 + 运营层",
                        "evidence": f"信用评级={dev_credit}, 交付率={dev_delivery}%",
                        "confidence": 0.5, "status": "ok"})
    else:
        drivers.append({"key": "brand", "cn": "品牌运营", "pct": None,
                        "trend": "开发商信用 + 运营层",
                        "evidence": "待管道四(开发商信用)接入;Schema v2.0 已有 22 列品牌信用字段",
                        "confidence": 0.0, "status": "待接入"})

    # 5 稀缺资源(低密/学区/景观)— Schema v2.0「十六·产品力与稀缺」23 列；
    #    容积率已有(部分), 学区深度(对应小学/初中/排名), 景观资源待管道补采
    far = parcel_data.get("容积率") or parcel_data.get("plot_ratio")
    is_low = parcel_data.get("是否低密住宅") or parcel_data.get("is_low_density")
    is_school = parcel_data.get("是否学区房") or parcel_data.get("is_school_district")
    scenic = parcel_data.get("景观资源") or parcel_data.get("scenic_view")
    scarcity_signals = [s for s in [far, is_low, is_school, scenic] if s]
    if scarcity_signals:
        evidence_parts = []
        if far: evidence_parts.append(f"容积率={far}")
        if is_low: evidence_parts.append("低密")
        if is_school: evidence_parts.append("学区")
        if scenic: evidence_parts.append(f"景观={scenic}")
        drivers.append({"key": "scarcity", "cn": "稀缺资源", "pct": None,
                        "trend": "低密/学区/景观",
                        "evidence": " / ".join(evidence_parts),
                        "confidence": 0.4, "status": "ok"})
    else:
        drivers.append({"key": "scarcity", "cn": "稀缺资源", "pct": None,
                        "trend": "低密/学区/景观",
                        "evidence": "待管道补采;Schema v2.0 已有 23 列含 is_low_density/is_school_district/scenic_view/学区深度",
                        "confidence": 0.0, "status": "待接入"})

    # 6 供需迁移 ← ABM·5年客群迁移(需求侧)
    shifts = migration.get("key_shifts") or []
    up = any((_f(s.get("delta")) > 0) and any(k in (s.get("archetype") or "")
             for k in ("改善", "高净值", "康养")) for s in shifts)
    drivers.append({
        "key": "demand", "cn": "供需迁移",
        "pct": None, "trend": ("客群升级·需求上行" if up else ("客群结构平稳" if shifts else None)),
        "evidence": "ABM·5年客群迁移", "confidence": 0.5 if shifts else 0.0,
        "status": "ok" if shifts else "待接入",
    })

    # 入市溢价总:锚定对标溢价倍数(已有·分析师估计),不另造数
    bpr = value.get("benchmark_premium_ratio")
    if bpr and bpr > 1:
        total_pct = round(min(PREMIUM_CAP, bpr - 1) * 100, 1)
    elif dpp:
        total_pct = round(dpp * 100, 1)
    else:
        total_pct = None
    evidenced = sum(1 for d in drivers if d["status"] == "ok")
    entry_premium = {
        "total_pct": total_pct, "drivers": drivers,
        "note": "总溢价锚定对标溢价倍数(分析师估计);各驱动仅在有真实信号处量化,"
                f"现 {evidenced}/6 维有据,余者标待接入,不造数。",
    }

    # ── 持有期升值曲线:用真实板块 CAGR 推演 + 置信带 ──
    points, curve_status, basis = [], "ok", "板块价 CAGR(时间穿越)"
    cagr = loc_cagr
    if cagr is None:
        curve_status, basis = "待接入", "缺板块价趋势,升值曲线待接入"
    else:
        g = _f(cagr) / 100.0
        for y in range(0, HORIZON_YEARS + 1):
            idx = round(100 * ((1 + g) ** y), 1)
            spread = round(0.4 * abs(g) * y * 100 + (2 if y else 0), 1)  # 置信带随时间张开
            points.append({"year": y, "index": idx,
                           "low": round(idx - spread, 1), "high": round(idx + spread, 1)})
    holding_curve = {"horizon_years": HORIZON_YEARS, "cagr_pct": cagr, "points": points,
                     "basis": basis, "confidence": 0.5,
                     "confidence_target": "0.9+ (管道一 L1 成交数据接入后)",
                     "status": curve_status}

    # 二手保值: Schema v2.0「二十二·二手房市场」6 列 + 管道一二手房子管道扩展至 50 城；
    #   接入后可用真实二手成交/挂牌价计算 y3/y5 保值率
    resale_avg = parcel_data.get("二手房成交均价_元每㎡") or parcel_data.get("resale_transaction_price") if parcel_data else None
    if resale_avg and base_price:
        y3_est = round((resale_avg / base_price - 1) * 100, 1) if base_price > 0 else None
        resale_retention = {"y3_pct": y3_est, "y5_pct": None, "status": "ok",
                            "source": f"二手成交均价={resale_avg}元/㎡(管道一二手房子管道);y5待更多时间序列数据"}
    else:
        resale_retention = {"y3_pct": None, "y5_pct": None, "status": "待接入",
                            "source": "待管道一二手房子管道接入;Schema v2.0 已有 6 列二手房字段(目前仅济南);"
                                     "现以入市溢价 + 板块趋势近似"}

    persona_view = {
        "developer": "升值=区位趋势 + 设计溢价 + 品牌沉淀;盯入市溢价兑现速度(去化)与未来地货比。",
        "designer": "设计动作→溢价贡献是你能直接拨动的升值杠杆(见设计×市场联动逆向任务书)。",
        "buyer": "持有升值看区位趋势 + 产品代差 + 二手保值,下行风险见风险段;本工具为信息参考,不构成投资建议。",
    }

    risk_to_appreciation = [{"risk": r.get("risk"), "level": r.get("level"), "trigger": r.get("trigger")}
                            for r in (blueprint.get("risk_curve") or [])]

    status = "ok" if (total_pct is not None and curve_status == "ok") else "degraded"
    result = {
        "status": status, "north_star": "资产升值", "base_price": round(base_price, 0),
        "entry_premium": entry_premium, "holding_curve": holding_curve,
        "resale_retention": resale_retention, "persona_view": persona_view,
        "risk_to_appreciation": risk_to_appreciation,
        "disclaimer": "升值为多维推演,非市场实测或承诺;总溢价与曲线基于现有信号的分析估计,"
                      "缺采维度标待接入,不臆造;6 条数据管道(网签成交/土地出让/建安成本/开发商信用/"
                      "租赁市场/政策金融)接入后,待接入维度将逐步转为 ok,置信度从 0.5 提升至 0.9+;"
                      "购房者视角不构成投资建议。",
    }

    # ── 多模态增强（可选）──
    if multimodal:
        try:
            from multimodal_valuation import multimodal_enhance
            mm = multimodal_enhance(
                parcel_report=parcel_report,
                client_goal=client_goal,
                archlib_projects=archlib_projects,
                vlm_evidence=vlm_evidence,
                weights=multimodal_weights,
            )
            result["multimodal"] = mm
            result["status"] = "ok" if mm["multimodal_valuation"]["delta_pct"] != 0 else result["status"]
        except ImportError:
            result["multimodal"] = {
                "status": "unavailable",
                "note": "multimodal_valuation.py 模块不可用，请确认 scripts/ 路径正确。",
            }

    return result


if __name__ == "__main__":
    import json
    demo = run_premium_engine(
        {"market": {"avg_price": 60000}}, {"expected_price": 75000, "ceo_preset": "design"},
        value={"benchmark_premium_ratio": 1.18,
               "design_market_link": {"status": "degraded", "design_premium_pct": 0.10,
                                      "premium_decomposition": {"components": [{"confidence": 0.85}]}}},
        migration={"key_shifts": [{"archetype": "高净值改善", "delta": 0.06}]},
        time_travel={"price_trend": {"cagr_pct": 4.2}},
        blueprint={"risk_curve": [{"risk": "政策风险", "level": "中", "trigger": "限价/限购变化"}]},
    )
    print(json.dumps(demo, ensure_ascii=False, indent=2))
