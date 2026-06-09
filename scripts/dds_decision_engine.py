"""
DDS Agent v2 决策算法编排器

把地块画像报告升级为：数据锚点、合规红线、价值机会、ABM 客群、任务书、蓝图风险。
"""
import json
import statistics
from datetime import datetime
from pathlib import Path

try:
    from abm_engine import (
        run_abm,
        product_from_client_goal,
        competitors_from_parcel,
        optimize_unit_mix,
        project_personas_5year,
        temporal_consistency,
        Product,
    )
except ImportError:
    from scripts.abm_engine import (
        run_abm,
        product_from_client_goal,
        competitors_from_parcel,
        optimize_unit_mix,
        project_personas_5year,
        temporal_consistency,
        Product,
    )


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DECISION_DIR = ROOT / "data_out" / "reports" / "decision"


def run_decision_engine(parcel_report: dict, client_goal: dict, online_evidence: list[dict] | None = None) -> dict:
    # 物理直读 L3 舆情爆料 (战役 4)
    if not online_evidence:
        online_evidence = []
        evidence_dir = ROOT / "data_out" / "online_evidence"
        if not evidence_dir.exists():
            evidence_dir = Path(__file__).resolve().parent.parent / "data_out" / "online_evidence"
            
        if evidence_dir.exists():
            for p in evidence_dir.glob("*.json"):
                try:
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                        if "evidence" in data and isinstance(data["evidence"], list):
                            online_evidence.extend(data["evidence"])
                except Exception as e:
                    print(f"[warn] 读取舆情文件 {p.name} 失败: {e}")

    # 进行高维关联度交叉校验 (Semantic Cross Check)
    city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city") or ""
    product_type = client_goal.get("product_type") or ""
    
    verified_evidence = []
    for ev in online_evidence:
        score = 0.0
        matches = []
        summary_text = ev.get("summary", "") + ev.get("title", "")
        if city and city in summary_text:
            score += 0.4
            matches.append(f"城市匹配({city})")
        if product_type and product_type in summary_text:
            score += 0.4
            matches.append(f"产品类型匹配({product_type})")
        if "康养" in summary_text or "文旅" in summary_text or "高端" in summary_text:
            score += 0.2
            matches.append("概念匹配")
            
        ev["cross_check_score"] = round(score, 2)
        ev["cross_check_matches"] = matches
        ev["is_highly_relevant"] = score >= 0.4
        verified_evidence.append(ev)

    online_evidence = verified_evidence

    data_foundation = build_data_foundation(parcel_report, client_goal, online_evidence)
    compliance = run_compliance_agent(parcel_report, client_goal, data_foundation)
    value = run_value_agent(parcel_report, client_goal, online_evidence)
    abm = run_abm_market_agent(parcel_report, client_goal, value)
    unit_mix = run_unit_mix_agent(parcel_report, client_goal, abm)
    migration = run_migration_agent(parcel_report, abm)
    time_travel = run_time_travel_agent(parcel_report, client_goal) if client_goal.get("skip_time_travel") is not True else None
    briefing = run_briefing_engine(parcel_report, client_goal, value, abm, compliance)
    blueprint = run_blueprint_logic(parcel_report, client_goal, value, compliance, abm)
    summary = build_decision_summary(parcel_report, client_goal, compliance, value, abm, briefing, blueprint)
    traceability = build_traceability(parcel_report, data_foundation, online_evidence)
    _partial = {
        "compliance_agent": compliance, "value_agent": value,
        "abm_market_agent": abm, "unit_mix_agent": unit_mix,
        "migration_agent": migration, "blueprint_logic": blueprint,
    }
    ceo = run_ceo_aggregator(_partial, preset=client_goal.get("ceo_preset") or "invest")
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "version": "dds-agent-v2-mvp-1",
        },
        "input": {
            "parcel": parcel_report.get("input", {}),
            "client_goal": client_goal,
        },
        "data_foundation": data_foundation,
        "compliance_agent": compliance,
        "value_agent": value,
        "abm_market_agent": abm,
        "unit_mix_agent": unit_mix,
        "migration_agent": migration,
        "time_travel_agent": time_travel,
        "briefing_engine": briefing,
        "blueprint_logic": blueprint,
        "decision_summary": summary,
        "ceo_aggregator": ceo,
        "traceability": traceability,
    }


def build_data_foundation(parcel_report: dict, client_goal: dict, online_evidence: list[dict] | None = None) -> dict:
    online_evidence = online_evidence or []
    amenities = parcel_report.get("amenities", {})
    competitors = parcel_report.get("nearby_competitors", [])
    nearest = {}
    for key, payload in amenities.items():
        items = payload.get("items") or []
        if items:
            nearest[key] = {
                "label": payload.get("label"),
                "name": items[0].get("name"),
                "distance_m": items[0].get("distance"),
            }
    return {
        "level_1_legal_constraints": {
            "status": "missing_level_1_constraints",
            "trust_weight": 1.0,
            "available": False,
            "required_before_final_pricing": ["容积率上限", "用地性质", "限高", "退线", "绿地率", "车位指标", "商业比例", "产权/分割销售口径"],
        },
        "level_2_gis_physical": {
            "status": "available",
            "trust_weight": 0.7,
            "nearest_poi": nearest,
            "competitor_sample_count": len(competitors),
        },
        "level_3_market_sentiment": {
            "status": "online_supplement_available" if online_evidence else "sentiment_not_connected",
            "trust_weight": 0.35,
            "proxy_signals": build_proxy_signals(parcel_report, client_goal),
            "online_evidence": online_evidence,
        },
    }


def build_proxy_signals(parcel_report: dict, client_goal: dict) -> dict:
    room_types = parcel_report.get("market_summary", {}).get("room_types", {})
    product = client_goal.get("product_type")
    benchmark = client_goal.get("benchmark")
    return {
        "product_type": product,
        "benchmark": benchmark,
        "room_type_supply": room_types,
        "note": "当前用竞品标签、户型结构、对标项目价格作为三级情绪/非标溢价的弱代理。",
    }


def run_compliance_agent(parcel_report: dict, client_goal: dict, data_foundation: dict) -> dict:
    warnings = []
    blockers = []
    far = client_goal.get("floor_area_ratio")
    product = client_goal.get("product_type") or "未指定"
    if far:
        warnings.append(f"容积率 {far} 是客户假设，尚未由出让合同或控规文件确认。")
    else:
        blockers.append("缺少容积率假设，无法形成楼面口径敏感区间。")
    if "商墅" in product or "别墅" in product:
        blockers.extend(["需确认用地性质是否允许商墅/低密产品", "需确认可售分割、产权年限和商办/住宅监管口径"])
    if data_foundation["level_1_legal_constraints"]["status"] == "missing_level_1_constraints":
        warnings.append("缺少一级法定硬约束，当前只能输出投拓初判，不能作为最终拿地价。")
    return {
        "role": "合规 Agent",
        "verdict": "conditional" if warnings or blockers else "clear",
        "hard_constraints_missing": data_foundation["level_1_legal_constraints"]["required_before_final_pricing"],
        "warnings": warnings,
        "blockers": blockers,
        "pricing_boundary": "只能给楼面地价敏感区间，最终价格需等一级数据校准。",
    }


def run_value_agent(parcel_report: dict, client_goal: dict, online_evidence: list[dict] | None = None) -> dict:
    online_evidence = online_evidence or []
    competitors = parcel_report.get("nearby_competitors", [])
    market_price = parcel_report.get("market_summary", {}).get("price", {})
    avg_price = market_price.get("avg")
    benchmark_name = client_goal.get("benchmark")
    benchmark = find_competitor(competitors, benchmark_name) if benchmark_name else None
    benchmark_price = benchmark.get("unit_price_cny") if benchmark else None
    premium_ratio = round(benchmark_price / avg_price, 2) if benchmark_price and avg_price else None
    opportunity = []
    if premium_ratio and premium_ratio >= 1.5:
        opportunity.append("对标项目显著高于周边均价，说明存在稀缺资源或强产品力溢价，但需要验证成交真实性和去化速度。")
    if client_goal.get("product_type"):
        opportunity.append(f"客户目标产品为{client_goal['product_type']}，应优先验证低密、私密性、到达仪式感和景观资源是否能支撑溢价。")
    # 向量库证据注入（chromadb 优先，Vault 兜底）
    vector_evidence = {"status": "skipped", "items": []}
    try:
        from scripts.vector_evidence import find_similar_parcels
        city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city")
        vector_evidence = find_similar_parcels(city, avg_price, client_goal.get("product_type"), top_k=5)
    except Exception as e:
        vector_evidence = {"status": "error", "error": str(e), "items": []}
    return {
        "role": "价值 Agent",
        "market_avg_price_cny": avg_price,
        "benchmark": benchmark,
        "benchmark_premium_ratio": premium_ratio,
        "opportunities": opportunity,
        "product_reference": build_product_reference(competitors),
        "vector_evidence": vector_evidence,
        "online_cross_check": {
            "status": "supplemental_only" if online_evidence else "not_run",
            "source_count": len(online_evidence),
            "sources": online_evidence,
        },
    }


def build_product_reference(competitors: list[dict]) -> dict:
    areas = [c.get("area_range") for c in competitors if c.get("area_range")]
    room_types = [c.get("room_types") for c in competitors if c.get("room_types")]
    high_price = sorted([c for c in competitors if c.get("unit_price_cny")], key=lambda x: x["unit_price_cny"], reverse=True)[:5]
    return {
        "observed_area_ranges": areas[:12],
        "observed_room_types": room_types[:12],
        "top_price_projects": [
            {"project_name": c.get("project_name"), "price": c.get("unit_price_cny"), "area_range": c.get("area_range"), "room_types": c.get("room_types")}
            for c in high_price
        ],
    }


def run_abm_market_agent(parcel_report: dict, client_goal: dict, value: dict) -> dict:
    """ABM v1：从规则化打分升级为 蒙特卡洛 + MNL 抽样。"""
    city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city")
    avg_price = value.get("market_avg_price_cny")
    product = product_from_client_goal(client_goal, avg_price)
    competitors = competitors_from_parcel(parcel_report.get("nearby_competitors", []))
    
    # 提取年份
    target_year = client_goal.get("year")
    if not target_year and parcel_report.get("meta"):
        target_year = parcel_report["meta"].get("target_year")
    try:
        target_year_int = int(target_year) if target_year else None
    except (ValueError, TypeError):
        target_year_int = None

    try:
        result = run_abm(city, product, competitors, n=1000, target_year=target_year_int)
    except ValueError as e:
        # 城市未配置时降级回兜底
        return {"role": "ABM 市场模拟 Agent", "status": "city_not_configured",
                "error": str(e), "personas": [], "top_personas": []}
    # 时空一致性回测：客户未禁用时默认开启
    if client_goal.get("skip_temporal_check") is not True:
        try:
            result["temporal_consistency"] = temporal_consistency(
                city, product, ref_years=(2015, 2020, 2024), n_per_year=400,
            )
        except Exception as e:
            result["temporal_consistency"] = {"status": "error", "error": str(e)}
    return result


def run_briefing_engine(parcel_report: dict, client_goal: dict, value: dict, abm: dict, compliance: dict) -> dict:
    product = client_goal.get("product_type") or "待定产品"
    avg_price = value.get("market_avg_price_cny")
    benchmark_price = (value.get("benchmark") or {}).get("unit_price_cny")
    target_band = build_target_price_band(avg_price, benchmark_price)
    
    # 动态组装痛点对冲产品设计指南 (战役1)
    pains = abm.get("top_pains", [])
    details = abm.get("top_details", [])
    
    pain_briefs = []
    if pains:
        pain_briefs.append("【核心居住痛点对冲设计】")
        for idx, item in enumerate(pains[:3]):
            pain_text = item["pain"]
            pct = round(item["weight"] * 100, 1)
            pain_briefs.append(f"  - 痛点 {idx+1}：{pain_text} (加权占比 {pct}%) ➔ 设计方案：提供专项户型治理/配套对冲策略。")
            
    if details:
        pain_briefs.append("【核心看房细节需求攻坚】")
        for idx, item in enumerate(details[:3]):
            need_text = item["need"]
            pct = round(item["weight"] * 100, 1)
            pain_briefs.append(f"  - 细节 {idx+1}：{need_text} (加权占比 {pct}%) ➔ 设计标准：深化图纸该专项细节，列入核心营销点。")

    performance_brief = [
        "私密性等级：控制公共界面干扰，强化院落/入户/露台的边界感。",
        "视觉通透度：优先把景观面和主力功能空间绑定，避免只做符号化立面。",
        "垂直动线效率：低密产品需减少无效交通面积，保证可售效率。",
        "到达仪式感：车行、人行、会所或公共入口形成清晰等级。",
        "可售弹性：面积段要覆盖主力客群支付能力，避免只堆大面积高总价。",
        "服务配置：康养、度假、物业服务必须能被客户感知，否则不能计入溢价假设。",
    ]
    if pain_briefs:
        performance_brief.extend(pain_briefs)

    return {
        "role": "任务书制定 Agent",
        "product_positioning": f"以{product}为方向，先验证低密/私密/景观/康养服务是否支撑高端溢价。",
        "target_price_band": target_band,
        "performance_brief": performance_brief,
        "premium_hypotheses": [
            "品牌/稀缺资源/低密私密性可支撑溢价。",
            "普通商业配套和表层景观包装不应被高估为核心溢价。",
            "若一级合规数据不支持商墅口径，产品定位需立即切换。",
        ],
        "compliance_dependency": compliance.get("hard_constraints_missing", []),
        "pain_hedging_guide": pain_briefs,
    }


def build_target_price_band(avg_price: float | None, benchmark_price: float | None) -> dict:
    if not avg_price:
        return {"status": "insufficient_price_data"}
    conservative = round(avg_price * 0.85, 0)
    balanced = round(avg_price, 0)
    aggressive = round(min(benchmark_price * 0.85, avg_price * 1.35), 0) if benchmark_price else round(avg_price * 1.2, 0)
    return {"conservative": conservative, "balanced": balanced, "aggressive": aggressive, "unit": "元/㎡"}


def run_blueprint_logic(parcel_report: dict, client_goal: dict, value: dict, compliance: dict, abm: dict = None) -> dict:
    prices = [c.get("unit_price_cny") for c in parcel_report.get("nearby_competitors", []) if c.get("unit_price_cny")]
    avg_price = value.get("market_avg_price_cny") or 30000.0
    median_price = round(statistics.median(prices), 0) if prices else avg_price
    benchmark_price = (value.get("benchmark") or {}).get("unit_price_cny")
    far = client_goal.get("floor_area_ratio")
    far_source = "user_input"
    if not far and client_goal.get("product_type"):
        far = 1.0
        far_source = "dds_default_assumption"
        
    land_band = None
    if far and avg_price:
        land_band = {
            "conservative_land_value": round(avg_price * 0.28 * far, 0),
            "balanced_land_value": round(avg_price * 0.33 * far, 0),
            "aggressive_land_value": round(avg_price * 0.38 * far, 0),
            "unit": "元/㎡土地口径粗算",
            "floor_area_ratio": far,
            "floor_area_ratio_source": far_source,
        }

    # ---- 战役 2：去化模拟与 IRR 回款精算 ----
    total_units = 500  # 项目模拟总户数
    avg_area = 120.0   # 户均面积 120 ㎡
    target_price = client_goal.get("target_price") or avg_price
    unit_value = target_price * avg_area / 10000.0  # 单套货值（万元）
    total_value = total_units * unit_value          # 总货值（万元）

    # 1. 模拟去化流速 (月去化套数)
    wtp_p50 = 30000.0
    avg_dp_ratio = 0.4
    
    if abm and abm.get("wtp_summary"):
        wtp_p50 = float(abm["wtp_summary"].get("p50", 30000))
        top_personas = abm.get("top_personas", [])
        if top_personas:
            try:
                from abm_engine import CITY_POOLS
            except ImportError:
                from scripts.abm_engine import CITY_POOLS
                
            dp_sum = 0.0
            share_sum = 0.0
            for tp in top_personas:
                name = tp.get("name")
                share = tp.get("share", 0.0)
                dp = 0.4
                for city_archs in CITY_POOLS.values():
                    for a in city_archs:
                        if a.name == name:
                            dp = a.dp_ratio
                            break
                dp_sum += dp * share
                share_sum += share
            if share_sum > 0:
                avg_dp_ratio = dp_sum / share_sum

    price_ratio = target_price / wtp_p50
    base_monthly_sales = 25.0  # 基准流速 25 套/月
    monthly_sales = base_monthly_sales * max(0.2, (1.5 - 1.0 * (price_ratio - 1.0)))
    monthly_sales = min(60.0, max(5.0, monthly_sales))  # 截断在 5 到 60 套/月
    sellout_months = round(total_units / monthly_sales, 1)

    # 2. 季度现金流精算
    quarterly_inflows = []   # 回款流入
    quarterly_outflows = []  # 资金流出
    quarterly_ncf = []       # 净现金流

    land_cost = total_value * 0.35  # 土地款占总货值 35%
    construction_cost = total_units * avg_area * 0.45  # 建安 0.45 万/㎡
    tax_and_fee_rate = 0.12        # 三费及税费 12%

    # 逐季度计算去化与回款
    sales_left = total_units
    quarterly_sales_units = []
    for q in range(8):
        if sales_left > 0:
            q_sales = min(sales_left, monthly_sales * 3.0)
            sales_left -= q_sales
        else:
            q_sales = 0.0
        quarterly_sales_units.append(q_sales)

    for q in range(8):
        q_sales = quarterly_sales_units[q]
        inflow_dp = q_sales * unit_value * avg_dp_ratio
        
        inflow_loan = 0.0
        if q > 0:
            prev_sales = quarterly_sales_units[q-1]
            inflow_loan = prev_sales * unit_value * (1.0 - avg_dp_ratio)
        
        # 最后一个季度把未放贷完的按揭全部收回
        if q == 7:
            total_expected_loan = sum(quarterly_sales_units) * unit_value * (1.0 - avg_dp_ratio)
            inflow_loan = max(inflow_loan, total_expected_loan - sum(quarterly_inflows[1:] if len(quarterly_inflows) > 1 else [0]))

        total_inflow = inflow_dp + inflow_loan
        quarterly_inflows.append(round(total_inflow, 1))

        # 流出：
        # Q0：拿地款 100% 支出
        # Q1 - Q4：建安款均匀支出
        # 每季：按回款 12% 支出当期税费与费用，Q0 额外预付 2% 启动费
        out_land = land_cost if q == 0 else 0.0
        out_const = (construction_cost / 4.0) if (1 <= q <= 4) else 0.0
        out_tax = total_inflow * tax_and_fee_rate
        if q == 0:
            out_tax += total_value * 0.02

        total_outflow = out_land + out_const + out_tax
        quarterly_outflows.append(round(total_outflow, 1))

        ncf = total_inflow - total_outflow
        quarterly_ncf.append(round(ncf, 1))

    # 3. 科学计算 IRR 并转化为年化 IRR
    def _calculate_irr(cash_flows: list[float], max_iters: int = 100) -> float | None:
        if all(x >= 0 for x in cash_flows) or all(x <= 0 for x in cash_flows):
            return None
        r = 0.1
        for _ in range(max_iters):
            npv = sum(cf / ((1 + r) ** t) for t, cf in enumerate(cash_flows))
            d_npv = sum(-t * cf / ((1 + r) ** (t + 1)) for t, cf in enumerate(cash_flows))
            if abs(d_npv) < 1e-9:
                break
            r_new = r - npv / d_npv
            if abs(r_new - r) < 1e-6:
                if r_new > -1:
                    return (1 + r_new) ** 4 - 1
                return None
            r = r_new
        
        low, high = -0.5, 1.0
        for _ in range(100):
            mid = (low + high) / 2
            npv = sum(cf / ((1 + mid) ** t) for t, cf in enumerate(cash_flows))
            if abs(npv) < 1e-4:
                return (1 + mid) ** 4 - 1
            if npv > 0:
                low = mid if cash_flows[0] < 0 else low
                high = mid if cash_flows[0] >= 0 else high
            else:
                high = mid if cash_flows[0] < 0 else high
                low = mid if cash_flows[0] >= 0 else low
        npv_final = sum(cf / ((1 + low) ** t) for t, cf in enumerate(cash_flows))
        if abs(npv_final) < 0.1:
            return (1 + low) ** 4 - 1
        return None

    irr_annual = _calculate_irr(quarterly_ncf)

    if irr_annual is not None:
        irr_level = "高" if irr_annual >= 0.15 else ("中" if irr_annual >= 0.08 else "低")
        irr_note = "项目年化模拟 IRR 表现" + ("优异，可强力推进" if irr_annual >= 0.15 else ("合理，建议平衡地价后稳步推进" if irr_annual >= 0.08 else "预警，拿地风险极大，需压降地价"))
        irr_show = f"{round(irr_annual * 100, 2)}%"
    else:
        irr_level = "低"
        irr_note = "现金流无法收敛，项目可能产生实质性亏损，建议暂停测算"
        irr_show = "计算失败/负收益"

    return {
        "role": "蓝图与风险 Agent",
        "scenario_prices": {
            "conservative_sale_price": median_price,
            "base_sale_price": avg_price,
            "benchmark_discount_sale_price": round(benchmark_price * 0.75, 0) if benchmark_price else None,
        },
        "land_value_sensitivity": land_band,
        "risk_curve": [
            {"risk": "政策风险", "level": "高" if compliance.get("hard_constraints_missing") else "中", "trigger": "一级硬约束未补齐"},
            {"risk": "去化风险", "level": "高" if price_ratio > 1.2 else "中", "trigger": f"当前预售价偏离客群WTP上限 {round(price_ratio*100)}%"},
            {"risk": "回款瓶颈", "level": "中", "trigger": f"加权首付比例仅 {round(avg_dp_ratio*100)}%，过度依赖银行按揭放款节奏"},
            {"risk": "项目错配", "level": "中", "trigger": "商墅若总价过高，会压缩家庭旅居客群"},
        ],
        "absorption_simulation": {
            "total_units": total_units,
            "sellout_months": sellout_months,
            "monthly_absorption_rate_units": round(monthly_sales, 1),
            "price_to_wtp_ratio": round(price_ratio, 2),
            "estimated_avg_dp_ratio": round(avg_dp_ratio, 2),
        },
        "quarterly_cash_flow": {
            "inflows_wan": quarterly_inflows,
            "outflows_wan": quarterly_outflows,
            "net_cash_flow_wan": quarterly_ncf,
        },
        "financial_indicator": {
            "simulated_irr_annual": irr_show,
            "irr_level": irr_level,
            "note": irr_note,
        },
        "next_data_required": ["出让合同/控规图则", "可售面积和计容口径", "建安成本", "税费和融资成本", "目标利润率", "竞品真实成交/去化速度"],
    }


def build_decision_summary(parcel_report: dict, client_goal: dict, compliance: dict, value: dict, abm: dict, briefing: dict, blueprint: dict) -> dict:
    land_band = blueprint.get("land_value_sensitivity") or {}
    return {
        "headline": "可进入投拓测算，但需先补齐一级法定硬约束后才能定最终拿地价。",
        "pricing_posture": {
            "conservative": land_band.get("conservative_land_value"),
            "balanced": land_band.get("balanced_land_value"),
            "aggressive": land_band.get("aggressive_land_value"),
            "unit": land_band.get("unit"),
        },
        "top_opportunity": (value.get("opportunities") or ["暂无明确高置信机会"])[0],
        "top_personas": abm.get("top_personas", []),
        "brief_focus": briefing.get("performance_brief", [])[:4],
        "hard_stop": compliance.get("blockers", []),
    }


def build_traceability(parcel_report: dict, data_foundation: dict, online_evidence: list[dict] | None = None) -> dict:
    online_evidence = online_evidence or []
    return {
        "parcel_report_meta": parcel_report.get("meta", {}),
        "source_scope": parcel_report.get("meta", {}).get("data_scope", "本地样本"),
        "location": parcel_report.get("location", {}),
        "evidence_counts": {
            "nearby_competitors": len(parcel_report.get("nearby_competitors", [])),
            "amenity_categories": len(parcel_report.get("amenities", {})),
        },
        "trust_weights": {
            "level_1": data_foundation["level_1_legal_constraints"]["trust_weight"],
            "level_2": data_foundation["level_2_gis_physical"]["trust_weight"],
            "level_3": data_foundation["level_3_market_sentiment"]["trust_weight"],
        },
        "online_sources": {
            "status": "supplemental_available" if online_evidence else "not_run",
            "count": len(online_evidence),
            "items": online_evidence,
        },
    }


def ascii_bar(val: float, max_val: float = 100.0, length: int = 10) -> str:
    """生成第一性原理的纯 ASCII 安全进度条，防止 Windows 控制台 GBK 乱码与崩溃"""
    if max_val <= 0:
        max_val = 1.0
    pct = max(0.0, min(1.0, float(val) / max_val))
    filled = int(round(pct * length))
    return "[" + "=" * filled + "-" * (length - filled) + "]"


def render_source_provenance_md(decision: dict) -> str:
    """数据来源与信任级：逐块溯源。真实数据给 URL，合成数据如实标注无外部来源。"""
    online = (decision.get("traceability", {}) or {}).get("online_sources", {}) or {}
    n_online = len(online.get("items") or [])
    rows = [
        "| 数据块 | 来源 | 信任级 | 来源/URL |",
        "| :--- | :--- | :--- | :--- |",
        "| 地块/竞品/价格 | 安居客新房·本地库 | L2 | https://www.anjuke.com/ |",
        "| 区位配套 POI | 高德地图 Web 服务 | L2 | https://lbs.amap.com/ |",
        "| 坐标/地理编码 | 高德地理编码 | L2 | https://lbs.amap.com/ |",
        "| 类似地块证据 | 向量库/Vault 兜底 | L2-L3 | 见「类似地块证据」段 |",
        "| 合成历史（仅密度参考） | 本地合成·最近邻克隆 | L3 | 无外部来源 generate_cloned_history.py |",
        "| 客群/ABM 模拟 | LLM 合成 + 蒙特卡洛 | L3 | 无外部来源 abm_engine.py |",
        f"| 线上舆情（交叉校验 {n_online} 条） | 各条目附来源/日期 | L3 | 见「线上舆情」表逐条 URL |",
        "| 政府成交/备案价 | 未接入 | L1 | .env 占位待真实政府域名 |",
    ]
    return "\n".join(rows) + "\n"


def render_decision_markdown(decision: dict) -> str:
    summary = decision["decision_summary"]
    lines = [
        "# DDS Agent v2 决策推演报告",
        "",
        "## 输入假设",
        f"- 地块输入：{decision['input']['parcel']}",
        f"- 客户目标：{decision['input']['client_goal']}",
        "",
        "## 决策逻辑图",
        "```mermaid",
        "flowchart TD",
        "  A[多维信任锚点] --> B[合规 Agent]",
        "  A --> C[价值 Agent]",
        "  C --> D[ABM 客群模拟]",
        "  B --> E[任务书制定]",
        "  D --> E",
        "  E --> F[蓝图与风险演练]",
        "  F --> G[决策摘要 + 溯源边界]",
        "```",
        "",
        "## CEO 权重引擎 / 综合评分",
        render_ceo_md(decision.get("ceo_aggregator") or {}),
        "## 决策摘要",
        f"- 结论：{summary['headline']}",
        f"- 拿地敏感区间：{summary['pricing_posture']}",
        f"- 核心机会：{summary['top_opportunity']}",
        f"- 硬性阻断：{summary['hard_stop'] or '暂无，但一级硬约束仍需补齐'}",
        "",
        "## 数据来源与信任级",
        render_source_provenance_md(decision),
        "## L3级线上舆情校验与交叉比对",
        render_online_evidence_md(decision.get("traceability", {}).get("online_sources", {})),
        "",
        "## Data Foundation",
        json_block(decision["data_foundation"]),
        "## Compliance Agent",
        json_block(decision["compliance_agent"]),
        "## Value Agent",
        json_block(decision["value_agent"]),
        "## 类似地块证据（向量库/Vault 兜底）",
        render_vector_evidence_md((decision.get("value_agent") or {}).get("vector_evidence") or {}),
        "## ABM Market Agent",
        json_block(decision["abm_market_agent"]),
        "## 客群语义画像（痛点 + 细节需求）",
        render_pain_need_md(decision.get("abm_market_agent") or {}),
        "## 户型配比 Agent（反向匹配）",
        render_unit_mix_md(decision.get("unit_mix_agent") or {}),
        "## 5 年客群迁移 Agent",
        render_migration_md(decision.get("migration_agent") or {}),
        "## 时间穿越 Agent（历史年份对比）",
        render_time_travel_md(decision.get("time_travel_agent") or {}),
        "## Briefing Engine",
        json_block(decision["briefing_engine"]),
        "## Blueprint Logic",
        json_block(decision["blueprint_logic"]),
        "## Traceability / 数据边界",
        json_block(decision["traceability"]),
    ]
    return "\n".join(lines) + "\n"


def render_online_evidence_md(online_sources: dict) -> str:
    items = online_sources.get("items") or []
    if not items:
        return "*(暂无相关的线上舆情爆料参与交叉校验)*\n"
    lines = [
        "| 舆情来源 | 来源链接 | 爆料时间 | 爆料摘要 | 关联校验匹配 | 关联度得分 | 校验结论 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ]
    for it in items:
        source = it.get("title", "未知舆情")
        url = it.get("url") or "—"
        date = it.get("data_date", "未知")
        summary = it.get("summary", "")
        matches = ", ".join(it.get("cross_check_matches") or ["无"])
        score = it.get("cross_check_score", 0.0)
        conclusion = "✅ 高度相关，强力校验" if it.get("is_highly_relevant") else "⚠️ 弱相关，仅供参考"
        lines.append(f"| {source} | {url} | {date} | {summary} | {matches} | {score} | {conclusion} |")
    return "\n".join(lines) + "\n"


def json_block(obj: dict) -> str:
    return "```json\n" + json.dumps(obj, ensure_ascii=False, indent=2) + "\n```\n"


def write_decision_outputs(decision: dict, out_dir: Path = DEFAULT_DECISION_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    city = decision.get("input", {}).get("parcel", {}).get("city", "DDS")
    address = decision.get("input", {}).get("parcel", {}).get("address") or "decision"
    stem = f"{ts}_{safe_slug(city + '_' + address)}"
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    html_path = out_dir / f"{stem}.html"
    md_path.write_text(render_decision_markdown(decision), encoding="utf-8")
    json_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_decision_html(decision), encoding="utf-8")
    return md_path, json_path, html_path


def safe_slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text).strip("_")[:70] or "decision"


def find_competitor(competitors: list[dict], name: str | None) -> dict | None:
    if not name:
        return None
    for item in competitors:
        project = item.get("project_name") or ""
        if name in project or project in name:
            return item
    return None


# ============================================================
# 户型配比 Agent（反向匹配）
# ============================================================
# 偏好向量（按面积段差异化）
_UNIT_PREF_SMALL = {"景观": 0.5, "私密": 0.4, "圈层": 0.4, "户型": 0.85,
                    "通勤": 0.55, "学校": 0.6, "品牌": 0.55}
_UNIT_PREF_MID = {"景观": 0.7, "私密": 0.55, "圈层": 0.55, "户型": 0.85,
                  "通勤": 0.45, "学校": 0.6, "品牌": 0.65}
_UNIT_PREF_LARGE = {"景观": 0.82, "私密": 0.72, "圈层": 0.72, "户型": 0.78,
                    "通勤": 0.3, "学校": 0.45, "品牌": 0.78}
_UNIT_PREF_VILLA = {"景观": 0.92, "私密": 0.9, "圈层": 0.82, "户型": 0.72,
                    "通勤": 0.2, "学校": 0.3, "品牌": 0.88}


def _candidate_units_for(product_type: str, avg_price: float) -> list:
    pt = product_type or ""
    avg = avg_price or 30000
    # 三档面积，覆盖核心产品定位 + 上下扩展
    if "商墅" in pt or "别墅" in pt:
        plan = [(150, 1.0, _UNIT_PREF_LARGE), (220, 1.08, _UNIT_PREF_VILLA), (280, 1.15, _UNIT_PREF_VILLA)]
    elif "高端" in pt or "豪宅" in pt:
        plan = [(140, 0.95, _UNIT_PREF_MID), (180, 1.05, _UNIT_PREF_LARGE), (220, 1.12, _UNIT_PREF_VILLA)]
    elif "改善" in pt:
        plan = [(110, 0.95, _UNIT_PREF_MID), (140, 1.0, _UNIT_PREF_MID), (180, 1.08, _UNIT_PREF_LARGE)]
    else:
        plan = [(70, 0.92, _UNIT_PREF_SMALL), (95, 1.0, _UNIT_PREF_SMALL), (130, 1.05, _UNIT_PREF_MID)]
    names = {150: "150㎡四房", 220: "220㎡商墅", 280: "280㎡大宅",
             140: "140㎡三房", 180: "180㎡大平层",
             110: "110㎡三房", 70: "70㎡两房", 95: "95㎡三房", 130: "130㎡三房"}
    return [Product(names.get(area, f"{area}㎡"),
                    round(avg * mult), area, pref)
            for area, mult, pref in plan]


def run_unit_mix_agent(parcel_report: dict, client_goal: dict, abm: dict) -> dict:
    """根据本地 ABM 客群分布 → 反推最优户型配比"""
    city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city")
    avg_price = parcel_report.get("market_summary", {}).get("price", {}).get("avg")
    if not city or abm.get("status") == "city_not_configured":
        return {"role": "户型配比 Agent", "status": "skipped",
                "reason": "城市未配置 ABM"}
    
    # 提取年份
    target_year = client_goal.get("year")
    if not target_year and parcel_report.get("meta"):
        target_year = parcel_report["meta"].get("target_year")
    try:
        target_year_int = int(target_year) if target_year else None
    except (ValueError, TypeError):
        target_year_int = None

    candidates = _candidate_units_for(client_goal.get("product_type") or "", avg_price)
    total_units = int(client_goal.get("total_units") or 120)
    competitors = competitors_from_parcel(parcel_report.get("nearby_competitors", []))
    mix = optimize_unit_mix(city, candidates, total_units=total_units,
                            competitors=competitors, n=1000, target_year=target_year_int)
    mix["role"] = "户型配比 Agent"
    return mix


# ============================================================
# 5 年客群迁移 Agent
# ============================================================
def run_migration_agent(parcel_report: dict, abm: dict) -> dict:
    city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city")
    if not city or abm.get("status") == "city_not_configured":
        return {"role": "5 年客群迁移 Agent", "status": "skipped"}
    
    # 提取年份
    target_year = None
    if parcel_report.get("meta"):
        target_year = parcel_report["meta"].get("target_year")
    try:
        target_year_int = int(target_year) if target_year else None
    except (ValueError, TypeError):
        target_year_int = None

    proj = project_personas_5year(city, horizon_years=5, n=1000, target_year=target_year_int)
    proj["role"] = "5 年客群迁移 Agent"
    return proj


# ============================================================
# Markdown 渲染辅助：户型配比 + 5 年迁移
# ============================================================
def render_unit_mix_md(unit_mix: dict) -> str:
    if not unit_mix or unit_mix.get("status") == "skipped":
        return "_户型配比 Agent 未执行：" + (unit_mix.get("reason", "未知") if unit_mix else "无") + "_\n"
    rows = unit_mix.get("unit_mix") or []
    lines = [
        f"**方法**：{unit_mix.get('method')}  ",
        f"**总户数**：{unit_mix.get('total_units')}  ",
        f"**总预期销售额**：{unit_mix.get('total_expected_revenue_cny', 0) / 1e8:.2f} 亿元  ",
        f"**整体去化预估**：{unit_mix.get('expected_overall_sellthrough_months', '?')} 月  ",
        "",
        "| 户型 | 面积 | 单价 | 推荐套数 | 占比 | 占比图表 | 主力客群 | 预期销售额 |",
        "|---|---:|---:|---:|---:|:---:|---|---:|",
    ]
    for r in rows:
        prim = r.get("primary_customers") or []
        prim_str = ", ".join(f"{p['name']}({p['weight']*100:.0f}%)" for p in prim[:2]) or "—"
        rev = r.get("expected_revenue_cny", 0) / 1e8
        bar_str = ascii_bar(r.get("share", 0), max_val=1.0)
        lines.append(
            f"| {r['name']} | {r['area']}㎡ | {r['unit_price']} | "
            f"{r['recommended_count']} | {r['share']*100:.1f}% | `{bar_str}` | "
            f"{prim_str} | {rev:.2f} 亿 |"
        )
    return "\n".join(lines) + "\n"


def render_migration_md(migration: dict) -> str:
    if not migration or migration.get("status") == "skipped":
        return "_5 年迁移 Agent 未执行_\n"
    lines = [
        f"**方法**：{migration.get('method')}  ",
        f"**累计退出率**：{migration.get('exit_cumulative_pct')}%  ",
        "",
        "### 关键迁移 (Y0 → Y5)",
        "",
        "| 客群 | Y0 | Y5 | 变化 | 趋势 |",
        "|---|---:|---:|---:|---|",
    ]
    h = migration.get("horizon_years", 5)
    yN_key = f"y{h}_share"
    for s in migration.get("key_shifts", [])[:8]:
        row = "| " + s["archetype"] + " | "
        row += f"{s['y0_share']*100:.0f}% | "
        row += f"{s.get(yN_key, 0)*100:.0f}% | "
        row += f"{s['delta']*100:+.0f}pp | "
        row += s["trend"] + " |"
        lines.append(row)
    lines.append("")
    lines.append("### 年度分布演化")
    lines.append("")
    lines.append("| 年份 | Top 3 客群 |")
    lines.append("|---:|---|")
    for h_row in migration.get("yearly_distribution", []):
        top = sorted(h_row["shares"].items(), key=lambda x: x[1], reverse=True)[:3]
        top_str = ", ".join(f"{k} {v*100:.0f}%" for k, v in top)
        lines.append(f"| Y{h_row['year']} | {top_str} |")
    return "\n".join(lines) + "\n"


# ============================================================
# CEO 权重引擎：score + confidence 加权聚合
# ============================================================
# 三套权重 preset
CEO_WEIGHT_PRESETS = {
    "invest": {  # 投拓视角
        "compliance": 0.30, "value": 0.20, "abm": 0.15,
        "unit_mix": 0.15, "migration": 0.10, "blueprint": 0.10,
    },
    "design": {  # 设计师视角
        "compliance": 0.20, "value": 0.10, "abm": 0.25,
        "unit_mix": 0.30, "migration": 0.05, "blueprint": 0.10,
    },
    "finance": {  # 资方视角
        "compliance": 0.20, "value": 0.20, "abm": 0.10,
        "unit_mix": 0.10, "migration": 0.10, "blueprint": 0.30,
    },
}


def _score_compliance(c: dict) -> tuple[float, float]:
    """合规 Agent → (raw_score, confidence)"""
    verdict = c.get("verdict")
    if verdict == "clear":
        raw = 90.0
    elif verdict == "conditional":
        raw = 55.0 - min(15, len(c.get("blockers") or []) * 5)
    else:
        raw = 20.0
    # 缺一级硬约束 → 置信度低
    conf = 0.5 if c.get("hard_constraints_missing") else 0.9
    return max(0, min(100, raw)), conf


def _score_value(v: dict) -> tuple[float, float]:
    """价值 Agent → (raw_score, confidence)"""
    avg = v.get("market_avg_price_cny") or 0
    premium = v.get("benchmark_premium_ratio") or 1.0
    # 价格段映射：3万 → 50；6万 → 70；10万+ → 85
    price_score = min(85, 30 + avg / 1000)
    # 溢价空间加分（对标项目高于均价说明有溢价机会）
    raw = price_score + min(15, (premium - 1) * 20)
    samples = (v.get("product_reference") or {}).get("observed_area_ranges") or []
    conf = min(1.0, 0.5 + len(samples) / 20)
    return max(0, min(100, raw)), conf


def _score_abm(a: dict) -> tuple[float, float]:
    """ABM Agent → (raw_score, confidence)，confidence 融合时空验证"""
    if a.get("status") == "city_not_configured":
        return 30.0, 0.3
    prob = a.get("avg_buy_probability") or 0
    raw = min(100, prob * 1000)  # 0.07 → 70 分
    base_conf = (a.get("confidence") or {}).get("score") or 0.5
    # 时空一致性融合：高一致 +0.15 / 中 +0 / 低 -0.2
    tc = a.get("temporal_consistency") or {}
    if tc.get("status") == "ok":
        score = tc.get("consistency_score", 0)
        if score >= 0.66:
            base_conf = min(1.0, base_conf + 0.15)
        elif score < 0.33:
            base_conf = max(0.1, base_conf - 0.2)
    return max(0, min(100, raw)), base_conf


def _score_unit_mix(u: dict) -> tuple[float, float]:
    """户型配比 Agent → (raw_score, confidence)"""
    if not u or u.get("status") == "skipped":
        return 0.0, 0.2
    # 销售额覆盖度：户型推荐套数应接近总户数
    rows = u.get("unit_mix") or []
    if not rows:
        return 30.0, 0.4
    total = u.get("total_units", 1)
    covered = sum(r.get("recommended_count", 0) for r in rows)
    coverage = min(1.0, covered / total)
    raw = 40 + coverage * 50
    # 户型分散度：多个户型分担说明产品定位健康
    nonzero = sum(1 for r in rows if r.get("recommended_count", 0) > 0)
    if nonzero >= 2:
        raw += 10
    conf = 0.7
    return max(0, min(100, raw)), conf


def _score_migration(m: dict) -> tuple[float, float]:
    """5 年迁移 Agent → (raw_score, confidence)"""
    if not m or m.get("status") == "skipped":
        return 50.0, 0.3
    exit_pct = m.get("exit_cumulative_pct") or 0
    # 退出率 < 20% 优秀；> 40% 减分
    raw = 90 - exit_pct
    shifts = m.get("key_shifts") or []
    # 正向迁移多（高端化）加分
    pos_count = sum(1 for s in shifts if s.get("delta", 0) > 0
                    and s.get("archetype") in ("高净值康养", "本地改善", "金融精英"))
    raw += pos_count * 3
    return max(0, min(100, raw)), 0.65


def _score_blueprint(b: dict) -> tuple[float, float]:
    """蓝图与风险 Agent → (raw_score, confidence)"""
    risks = b.get("risk_curve") or []
    high = sum(1 for r in risks if r.get("level") == "高")
    mid = sum(1 for r in risks if r.get("level") == "中")
    raw = 90 - high * 15 - mid * 5
    return max(0, min(100, raw)), 0.6


def run_ceo_aggregator(decision: dict, preset: str = "invest") -> dict:
    """CEO 权重引擎：聚合各 Agent 的 score+confidence → 总分"""
    weights = CEO_WEIGHT_PRESETS.get(preset, CEO_WEIGHT_PRESETS["invest"])
    scorers = {
        "compliance": (_score_compliance, decision.get("compliance_agent")),
        "value":      (_score_value,      decision.get("value_agent")),
        "abm":        (_score_abm,        decision.get("abm_market_agent")),
        "unit_mix":   (_score_unit_mix,   decision.get("unit_mix_agent")),
        "migration":  (_score_migration,  decision.get("migration_agent")),
        "blueprint":  (_score_blueprint,  decision.get("blueprint_logic")),
    }
    breakdown = []
    weighted_sum = 0.0
    weighted_conf = 0.0
    for key, (fn, payload) in scorers.items():
        try:
            raw, conf = fn(payload or {})
        except Exception as e:
            raw, conf = 50.0, 0.3
        w = weights.get(key, 0)
        contrib = w * raw * conf
        weighted_sum += contrib
        weighted_conf += w * conf
        breakdown.append({
            "agent": key,
            "raw_score": round(raw, 1),
            "confidence": round(conf, 2),
            "weight": w,
            "contribution": round(contrib, 2),
        })
    grade = _grade(weighted_sum)
    return {
        "role": "CEO 权重引擎",
        "preset": preset,
        "weights": weights,
        "total_score": round(weighted_sum, 1),
        "overall_confidence": round(weighted_conf, 2),
        "grade": grade,
        "breakdown": breakdown,
        "interpretation": _interpret(weighted_sum, weighted_conf),
    }


def _grade(score):
    if score >= 75: return "A+"
    if score >= 65: return "A"
    if score >= 55: return "B+"
    if score >= 45: return "B"
    if score >= 35: return "C"
    return "D"


def _interpret(score, conf):
    if conf < 0.5:
        prefix = "⚠ 置信度偏低，结论仅供初步参考，需补一级数据后复核。"
    elif conf < 0.7:
        prefix = "置信度中等，可作为决策辅助，重要环节仍需人工复核。"
    else:
        prefix = "置信度较高，模型输入充分、内部一致。"
    if score >= 65:
        outcome = "综合得分较好，可推进至深度尽调与商务谈判阶段。"
    elif score >= 50:
        outcome = "综合得分中等，建议先补齐一级法定硬约束 + 客群校验后再决策。"
    else:
        outcome = "综合得分偏低，需重新评估产品定位、地块价值或拿地价区间。"
    return prefix + " " + outcome


def render_ceo_md(ceo):
    if not ceo:
        return ""
    parts = []
    parts.append("**preset**: " + str(ceo["preset"]))
    parts.append("**total_score**: " + str(ceo["total_score"]) + " / 100")
    parts.append("**grade**: " + str(ceo["grade"]))
    parts.append("**overall_confidence**: " + str(ceo["overall_confidence"]))
    parts.append("**interpretation**: " + str(ceo["interpretation"]))
    parts.append("")
    parts.append("| Agent | 原始评分 | 置信度 | 权重 | 贡献分 | 评分分布 |")
    parts.append("|---|---:|---:|---:|---:|:---:|")
    for b in ceo["breakdown"]:
        bar_str = ascii_bar(b["raw_score"], max_val=100.0)
        cells = [
            str(b["agent"]), str(b["raw_score"]), str(b["confidence"]),
            str(b["weight"]), str(b["contribution"]), f"`{bar_str}`"
        ]
        parts.append("| " + " | ".join(cells) + " |")
    return "\n".join(parts) + "\n"


# ============================================================
# HTML 决策报告（Bauhaus 风格，自包含单页）
# ============================================================
_HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       background: #f5f3ee; color: #111; padding: 32px; line-height: 1.6; }
.container { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 36px; font-weight: 900; letter-spacing: -1px;
     border-bottom: 4px solid #111; padding-bottom: 12px; margin-bottom: 24px; }
h2 { font-size: 22px; font-weight: 800; margin-top: 36px; margin-bottom: 12px;
     background: #111; color: #f5f3ee; padding: 6px 14px; display: inline-block; }
h3 { font-size: 16px; font-weight: 700; margin: 18px 0 8px; color: #333; }
.score-card { display: flex; gap: 24px; align-items: stretch; margin-bottom: 20px; }
.score-box { background: #111; color: #f5f3ee; padding: 28px 36px; border-radius: 0; }
.score-box .num { font-size: 56px; font-weight: 900; line-height: 1; }
.score-box .lbl { font-size: 12px; letter-spacing: 2px; text-transform: uppercase;
                  opacity: 0.7; margin-top: 6px; }
.grade-A\\+, .grade-A { background: #d9261c; color: #fff5d9; }
.grade-B\\+, .grade-B { background: #f5b800; color: #111; }
.grade-C, .grade-D { background: #666; color: #fff; }
.bar { display: inline-block; height: 18px; background: #d9261c; margin-right: 8px;
       vertical-align: middle; }
.kv { display: flex; flex-wrap: wrap; gap: 8px 24px; margin-bottom: 12px; }
.kv span { color: #555; font-size: 14px; }
.kv b { color: #111; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }
th, td { padding: 8px 12px; border-bottom: 1px solid #ddd; text-align: left; }
th { background: #111; color: #f5f3ee; font-weight: 700; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.warn { background: #fff4e0; border-left: 6px solid #f5b800; padding: 12px 16px;
        margin: 10px 0; font-size: 14px; }
.danger { background: #ffe4e1; border-left: 6px solid #d9261c; padding: 12px 16px;
          margin: 10px 0; font-size: 14px; }
.section { margin-bottom: 24px; }
.muted { color: #888; font-size: 13px; }
.list { padding-left: 22px; }
.list li { margin: 4px 0; font-size: 14px; }
"""


def _esc(s):
    """简易 HTML 转义"""
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _ceo_html(ceo):
    if not ceo:
        return ""
    parts = []
    grade = _esc(ceo["grade"])
    parts.append('<div class="score-card">')
    parts.append(f'<div class="score-box grade-{grade}"><div class="num">{ceo["total_score"]}</div><div class="lbl">综合评分 / 100</div></div>')
    parts.append(f'<div class="score-box"><div class="num">{grade}</div><div class="lbl">等级</div></div>')
    parts.append(f'<div class="score-box"><div class="num">{ceo["overall_confidence"]}</div><div class="lbl">整体置信度</div></div>')
    parts.append(f'<div class="score-box"><div class="num" style="font-size:24px;">{_esc(ceo["preset"])}</div><div class="lbl">视角</div></div>')
    parts.append('</div>')
    # interpretation
    conf = ceo["overall_confidence"]
    cls = "danger" if conf < 0.5 else ("warn" if conf < 0.7 else "")
    if cls:
        parts.append(f'<div class="{cls}">{_esc(ceo["interpretation"])}</div>')
    else:
        parts.append(f'<p>{_esc(ceo["interpretation"])}</p>')
    # 分项条形
    parts.append('<h3>分项明细</h3>')
    parts.append('<table><thead><tr><th>Agent</th><th class="num">原始分</th><th class="num">置信度</th><th class="num">权重</th><th class="num">贡献</th><th>条形</th></tr></thead><tbody>')
    for b in ceo["breakdown"]:
        bar_w = int(b["raw_score"] * 1.5)
        parts.append(
            f'<tr><td>{_esc(b["agent"])}</td>'
            f'<td class="num">{b["raw_score"]}</td>'
            f'<td class="num">{b["confidence"]}</td>'
            f'<td class="num">{b["weight"]}</td>'
            f'<td class="num"><b>{b["contribution"]}</b></td>'
            f'<td><span class="bar" style="width:{bar_w}px;"></span></td></tr>'
        )
    parts.append('</tbody></table>')
    return "\n".join(parts)


def _unit_mix_html(u):
    if not u or u.get("status") == "skipped":
        return '<p class="muted">户型配比 Agent 未执行</p>'
    rows = u.get("unit_mix") or []
    parts = []
    parts.append(f'<div class="kv"><span>方法: <b>{_esc(u.get("method"))}</b></span>'
                 f'<span>总户数: <b>{u.get("total_units")}</b></span>'
                 f'<span>预期销售: <b>{u.get("total_expected_revenue_cny",0)/1e8:.2f} 亿</b></span>'
                 f'<span>去化预估: <b>{u.get("expected_overall_sellthrough_months")} 月</b></span></div>')
    parts.append('<table><thead><tr><th>户型</th><th class="num">面积</th><th class="num">单价</th><th class="num">推荐套数</th><th class="num">占比</th><th>占比图表</th><th>主力客群</th><th class="num">预期销售额</th></tr></thead><tbody>')
    max_w = max((r.get("share", 0) for r in rows), default=0.01)
    for r in rows:
        prim = r.get("primary_customers") or []
        prim_str = ", ".join(f"{_esc(p['name'])} {p['weight']*100:.0f}%" for p in prim[:2]) or "—"
        rev = r.get("expected_revenue_cny", 0) / 1e8
        bar_w = int(r.get("share", 0) / max_w * 120)
        bar = f'<span class="bar" style="width:{bar_w}px;background:#038684;height:12px;display:inline-block;border-radius:2px;"></span>'
        parts.append(
            f'<tr><td><b>{_esc(r["name"])}</b></td>'
            f'<td class="num">{r["area"]}㎡</td>'
            f'<td class="num">{r["unit_price"]}</td>'
            f'<td class="num">{r["recommended_count"]}</td>'
            f'<td class="num"><b>{r["share"]*100:.1f}%</b></td>'
            f'<td>{bar}</td>'
            f'<td>{prim_str}</td>'
            f'<td class="num">{rev:.2f} 亿</td></tr>'
        )
    parts.append('</tbody></table>')
    return "\n".join(parts)


def _migration_html(m):
    if not m or m.get("status") == "skipped":
        return '<p class="muted">5 年迁移 Agent 未执行</p>'
    h = m.get("horizon_years", 5)
    yN_key = f"y{h}_share"
    parts = []
    parts.append(f'<div class="kv"><span>方法: <b>{_esc(m.get("method"))}</b></span>'
                 f'<span>累计退出率: <b>{m.get("exit_cumulative_pct")}%</b></span></div>')
    parts.append('<h3>关键迁移 (Y0 → Y5)</h3>')
    parts.append('<table><thead><tr><th>客群</th><th class="num">Y0</th><th class="num">Y5</th><th class="num">变化</th><th>趋势</th></tr></thead><tbody>')
    for s in (m.get("key_shifts") or [])[:8]:
        trend = s["trend"]
        color = "#d9261c" if trend == "上升" else "#666"
        parts.append(
            f'<tr><td>{_esc(s["archetype"])}</td>'
            f'<td class="num">{s["y0_share"]*100:.0f}%</td>'
            f'<td class="num">{s.get(yN_key,0)*100:.0f}%</td>'
            f'<td class="num" style="color:{color};font-weight:700">{s["delta"]*100:+.0f}pp</td>'
            f'<td>{trend}</td></tr>'
        )
    parts.append('</tbody></table>')
    parts.append('<h3>年度分布演化</h3>')
    parts.append('<table><thead><tr><th>年份</th><th>Top 3 客群</th></tr></thead><tbody>')
    for h_row in (m.get("yearly_distribution") or []):
        top = sorted(h_row["shares"].items(), key=lambda x: x[1], reverse=True)[:3]
        top_str = ", ".join(f"{_esc(k)} {v*100:.0f}%" for k, v in top)
        parts.append(f'<tr><td><b>Y{h_row["year"]}</b></td><td>{top_str}</td></tr>')
    parts.append('</tbody></table>')
    return "\n".join(parts)


def _abm_html(a):
    if not a or a.get("status") == "city_not_configured":
        return '<p class="muted">ABM 未执行（城市未配置）</p>'
    parts = []
    parts.append(f'<div class="kv"><span>方法: <b>{_esc(a.get("method"))}</b></span>'
                 f'<span>样本: <b>N={a.get("n_personas")}</b></span>'
                 f'<span>平均购买率: <b>{a.get("avg_buy_probability")*100:.1f}%</b></span>'
                 f'<span>置信度: <b>{(a.get("confidence") or {}).get("level")}</b></span></div>')
    parts.append('<h3>Top 客群</h3>')
    parts.append('<table><thead><tr><th>客群</th><th class="num">占比</th><th>占比图表</th><th class="num">得分</th><th class="num">WTP p50</th><th class="num">WTP p90</th></tr></thead><tbody>')
    personas = a.get("top_personas") or []
    max_w = max((p.get("share", 0) for p in personas[:5]), default=0.01)
    for p in personas[:5]:
        bar_w = int(p.get("share", 0) / max_w * 120)
        bar = f'<span class="bar" style="width:{bar_w}px;background:#ac8933;height:12px;display:inline-block;border-radius:2px;"></span>'
        parts.append(
            f'<tr><td>{_esc(p["name"])}</td>'
            f'<td class="num"><b>{p["share"]*100:.1f}%</b></td>'
            f'<td>{bar}</td>'
            f'<td class="num">{p["score"]}</td>'
            f'<td class="num">{p.get("wtp_p50","-")}</td>'
            f'<td class="num">{p.get("wtp_p90","-")}</td></tr>'
        )
    parts.append('</tbody></table>')
    if a.get("price_sensitivity"):
        parts.append('<h3>价格敏感性</h3>')
        parts.append('<table><thead><tr><th>价格变化</th><th class="num">平均购买率</th><th class="num">相对基线</th></tr></thead><tbody>')
        for s in a["price_sensitivity"]:
            parts.append(f'<tr><td>{_esc(s["price_delta"])}</td>'
                         f'<td class="num">{s["avg_buy_prob"]}</td>'
                         f'<td class="num">{s["vs_base_pct"]:+.1f}%</td></tr>')
        parts.append('</tbody></table>')
    return "\n".join(parts)


def _summary_html(s, c):
    parts = []
    parts.append(f'<p><b>结论：</b>{_esc(s.get("headline"))}</p>')
    pp = s.get("pricing_posture") or {}
    parts.append(f'<div class="kv"><span>保守: <b>{pp.get("conservative")}</b></span>'
                 f'<span>平衡: <b>{pp.get("balanced")}</b></span>'
                 f'<span>进取: <b>{pp.get("aggressive")}</b></span>'
                 f'<span class="muted">{_esc(pp.get("unit"))}</span></div>')
    parts.append(f'<p><b>核心机会：</b>{_esc(s.get("top_opportunity"))}</p>')
    hs = s.get("hard_stop") or []
    if hs:
        parts.append('<div class="danger"><b>硬性阻断：</b><ul class="list">')
        for h in hs:
            parts.append(f'<li>{_esc(h)}</li>')
        parts.append('</ul></div>')
    if c.get("warnings"):
        parts.append('<div class="warn"><b>合规警示：</b><ul class="list">')
        for w in c["warnings"]:
            parts.append(f'<li>{_esc(w)}</li>')
        parts.append('</ul></div>')
    return "\n".join(parts)


def render_source_provenance_html(decision):
    """数据来源与信任级（HTML 表）。真实给 URL，合成如实标注无外部来源。"""
    online = (decision.get("traceability", {}) or {}).get("online_sources", {}) or {}
    n = len(online.get("items") or [])
    rows = [
        ("地块/竞品/价格", "安居客新房·本地库", "L2", "https://www.anjuke.com/"),
        ("区位配套 POI", "高德地图 Web 服务", "L2", "https://lbs.amap.com/"),
        ("坐标/地理编码", "高德地理编码", "L2", "https://lbs.amap.com/"),
        ("类似地块证据", "向量库/Vault 兜底", "L2-L3", "见「类似地块证据」段"),
        ("合成历史（仅密度参考）", "本地合成·最近邻克隆", "L3", "无外部来源 generate_cloned_history.py"),
        ("客群/ABM 模拟", "LLM 合成 + 蒙特卡洛", "L3", "无外部来源 abm_engine.py"),
        (f"线上舆情（{n} 条）", "各条目附来源/日期", "L3", "见线上舆情逐条 URL"),
        ("政府成交/备案价", "未接入", "L1", ".env 占位待真实政府域名"),
    ]
    body = "".join(
        f"<tr><td>{_esc(a)}</td><td>{_esc(b)}</td><td>{_esc(c)}</td><td>{_esc(d)}</td></tr>"
        for a, b, c, d in rows)
    return ('<table><thead><tr><th>数据块</th><th>来源</th><th>信任级</th>'
            '<th>来源/URL</th></tr></thead><tbody>' + body + '</tbody></table>')


def render_decision_html(decision):
    inp = decision.get("input", {})
    parcel_in = inp.get("parcel", {})
    title = "DDS 决策推演报告"
    addr = parcel_in.get("address") or parcel_in.get("city") or "未知地块"
    html = []
    html.append('<!doctype html>')
    html.append('<html lang="zh"><head><meta charset="utf-8">')
    html.append(f'<title>{_esc(title)} · {_esc(addr)}</title>')
    html.append(f'<style>{_HTML_CSS}</style></head><body><div class="container">')
    html.append(f'<h1>{_esc(title)}</h1>')
    html.append(f'<div class="kv"><span>地块: <b>{_esc(addr)}</b></span>'
                f'<span>城市: <b>{_esc(parcel_in.get("city","-"))}</b></span>'
                f'<span>生成时间: <b>{_esc(decision.get("meta",{}).get("generated_at"))}</b></span>'
                f'<span class="muted">{_esc(decision.get("meta",{}).get("version"))}</span></div>')

    html.append('<h2>CEO 综合评分</h2><div class="section">')
    html.append(_ceo_html(decision.get("ceo_aggregator")))
    html.append('</div>')

    html.append('<h2>决策摘要</h2><div class="section">')
    html.append(_summary_html(decision.get("decision_summary", {}),
                              decision.get("compliance_agent", {})))
    html.append('</div>')

    html.append('<h2>数据来源与信任级</h2><div class="section">')
    html.append(render_source_provenance_html(decision))
    html.append('</div>')

    html.append('<h2>类似地块证据</h2><div class="section">')
    html.append(render_vector_evidence_html((decision.get("value_agent") or {}).get("vector_evidence") or {}))
    html.append('</div>')

    html.append('<h2>客群语义画像</h2><div class="section">')
    html.append(render_pain_need_html(decision.get("abm_market_agent") or {}))
    html.append('</div>')

    html.append('<h2>ABM 客群模拟</h2><div class="section">')
    html.append(_abm_html(decision.get("abm_market_agent")))
    html.append('</div>')

    html.append('<h2>户型配比（反向匹配）</h2><div class="section">')
    html.append(_unit_mix_html(decision.get("unit_mix_agent")))
    html.append('</div>')

    html.append('<h2>5 年客群迁移</h2><div class="section">')
    html.append(_migration_html(decision.get("migration_agent")))
    html.append('</div>')

    html.append('<h2>时间穿越（历史年份对比）</h2><div class="section">')
    html.append(render_time_travel_html(decision.get("time_travel_agent") or {}))
    html.append('</div>')

    html.append('</div></body></html>')
    return "\n".join(html)


# ============================================================
# 时间穿越 Agent：同地块同产品在多个年份的客群+价格演化
# ============================================================
from scripts.abm_engine import load_historical_avg_price


def run_time_travel_agent(parcel_report, client_goal, years=(2010, 2020, 2026)):
    """对同一地块同一产品，跑历史年份 ABM，对比客群构成 + 价格演化"""
    city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city")
    if not city:
        return {"role": "TimeTravel", "status": "skipped", "reason": "no_city"}
    product_type = client_goal.get("product_type") or "改善产品"
    snapshots = []
    for y in years:
        avg = load_historical_avg_price(city, y)
        prod = product_from_client_goal({"product_type": product_type}, avg)
        try:
            result = run_abm(city, prod, [], n=500, target_year=y)
        except ValueError:
            continue
        top3 = []
        for p in (result.get("top_personas") or [])[:3]:
            top3.append({"name": p["name"], "share": p["share"]})
        snapshots.append({
            "year": y,
            "market_avg_price": avg,
            "avg_buy_prob": result.get("avg_buy_probability"),
            "top3": top3,
            "wtp_p50": (result.get("wtp_summary") or {}).get("p50"),
        })
    if not snapshots:
        return {"role": "TimeTravel", "status": "no_data"}
    # 价格趋势
    valid = [s for s in snapshots if s.get("market_avg_price")]
    price_trend = None
    if len(valid) >= 2:
        first = valid[0]
        last = valid[-1]
        ratio = last["market_avg_price"] / max(first["market_avg_price"], 1)
        price_trend = {
            "from_year": first["year"], "to_year": last["year"],
            "from_price": first["market_avg_price"], "to_price": last["market_avg_price"],
            "growth_multiple": round(ratio, 2),
            "cagr_pct": round((ratio ** (1 / max(last["year"] - first["year"], 1)) - 1) * 100, 1),
        }
    # 客群演化：把每个 archetype 在所有快照中的 share 拼接成时间序列
    arch_evolution = {}
    for s in snapshots:
        for p in s["top3"]:
            arch_evolution.setdefault(p["name"], {})[s["year"]] = p["share"]
    return {
        "role": "时间穿越 Agent",
        "city": city,
        "product_type": product_type,
        "years": list(years),
        "snapshots": snapshots,
        "price_trend": price_trend,
        "archetype_evolution": arch_evolution,
    }


def render_time_travel_md(tt):
    if not tt or tt.get("status") in ("skipped", "no_data"):
        return "_时间穿越未执行_\n"
    lines = [
        f"**城市**: {tt.get('city')}  ",
        f"**产品**: {tt.get('product_type')}  ",
    ]
    pt = tt.get("price_trend") or {}
    if pt:
        lines.append(
            f"**价格趋势**: {pt['from_year']} → {pt['to_year']} "
            f"({pt['from_price']} → {pt['to_price']} 元/㎡, "
            f"{pt['growth_multiple']}× 增长，年化 CAGR {pt['cagr_pct']}%)"
        )
    lines.extend([
        "",
        "### 各年快照对比",
        "",
        "| 年份 | 均价 | 平均买率 | WTP-p50 | Top3 客群 |",
        "|---:|---:|---:|---:|---|",
    ])
    for s in tt.get("snapshots", []):
        top_str = " · ".join(f"{p['name']} {p['share']*100:.0f}%"
                             for p in s["top3"])
        lines.append(
            f"| Y{s['year']} | {s.get('market_avg_price','-')} | "
            f"{s.get('avg_buy_prob','-')} | {s.get('wtp_p50','-')} | {top_str} |"
        )
    evo = tt.get("archetype_evolution") or {}
    if evo:
        lines.append("")
        lines.append("### 客群迁徙轨迹")
        lines.append("")
        lines.append("| Archetype | " + " | ".join(f"Y{y}" for y in tt["years"]) + " |")
        lines.append("|---|" + "|".join(["---:"] * len(tt["years"])) + "|")
        for arch, ys in evo.items():
            cells = [f"{ys.get(y, 0)*100:.0f}%" if y in ys else "—"
                     for y in tt["years"]]
            lines.append(f"| {arch} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_time_travel_html(tt):
    if not tt or tt.get("status") in ("skipped", "no_data"):
        return '<p class="muted">时间穿越未执行</p>'
    parts = []
    pt = tt.get("price_trend") or {}
    parts.append(f'<div class="kv"><span>城市: <b>{_esc(tt.get("city"))}</b></span>'
                 f'<span>产品: <b>{_esc(tt.get("product_type"))}</b></span></div>')
    if pt:
        parts.append(f'<div class="kv"><span>价格趋势 <b>{pt["from_year"]} → {pt["to_year"]}</b>: '
                     f'<b>{pt["from_price"]} → {pt["to_price"]}</b> 元/㎡, '
                     f'<b>{pt["growth_multiple"]}×</b> 增长，年化 <b>{pt["cagr_pct"]}%</b></span></div>')
    parts.append('<table><thead><tr><th>年份</th><th class="num">均价</th>'
                 '<th class="num">平均买率</th><th class="num">WTP-p50</th>'
                 '<th>Top3 客群</th></tr></thead><tbody>')
    for s in tt.get("snapshots", []):
        top_str = " · ".join(f"{_esc(p['name'])} {p['share']*100:.0f}%"
                             for p in s["top3"])
        parts.append(
            f'<tr><td><b>Y{s["year"]}</b></td>'
            f'<td class="num">{s.get("market_avg_price","-")}</td>'
            f'<td class="num">{s.get("avg_buy_prob","-")}</td>'
            f'<td class="num">{s.get("wtp_p50","-")}</td>'
            f'<td>{top_str}</td></tr>'
        )
    parts.append("</tbody></table>")
    evo = tt.get("archetype_evolution") or {}
    if evo:
        years = tt["years"]
        parts.append("<h3>客群迁徙轨迹</h3>")
        parts.append('<table><thead><tr><th>Archetype</th>' +
                     ''.join(f'<th class="num">Y{y}</th>' for y in years) +
                     '</tr></thead><tbody>')
        for arch, ys in evo.items():
            cells = ''.join(
                (f'<td class="num">{ys.get(y, 0)*100:.0f}%</td>'
                 if y in ys else '<td class="num">—</td>')
                for y in years
            )
            parts.append(f'<tr><td>{_esc(arch)}</td>{cells}</tr>')
        parts.append("</tbody></table>")
    return "\n".join(parts)



def render_vector_evidence_md(ve):
    if not ve or ve.get("status") in ("skipped", "not_configured"):
        note = ve.get("reason") or "未配置"
        return f"_向量证据未启用：{note}_\n"
    if ve.get("status") == "no_data":
        return "_向量证据库无匹配数据_\n"
    items = ve.get("items") or []
    if not items:
        return "_向量证据为空_\n"
    lines = [f"**来源**: {ve.get('source')} · {ve.get('collection', '')}  "]
    if ve.get("note"):
        lines.append(f"_{ve['note']}_  ")
    lines.append("")
    lines.append("| # | 项目 | 区域 | 年份 | 单价 | 面积段 | 开发商 | 相似度 |")
    lines.append("|---:|---|---|---:|---:|---|---|---:|")
    for i, x in enumerate(items, 1):
        m = x.get("metadata") or {}
        dist = x.get("distance")
        sim = "—" if dist is None else f"{(1 - min(dist, 1.0)) * 100:.0f}%"
        lines.append(
            f"| {i} | {m.get('project_name','-')} | {m.get('district','-')} | "
            f"{m.get('year','-')} | {m.get('unit_price','-')} | "
            f"{m.get('area_range','-')} | {m.get('developer','-')} | {sim} |"
        )
    return "\n".join(lines) + "\n"


def render_vector_evidence_html(ve):
    if not ve or ve.get("status") in ("skipped", "not_configured", "no_data"):
        return '<p class="muted">' + '向量证据未启用：' + (_esc(ve.get("reason", "")) if ve else "") + '</p>'
    items = ve.get("items") or []
    if not items:
        return '<p class="muted">' + '向量证据为空' + '</p>'
    parts = []
    parts.append('<div class="kv"><span>' + '来源' + ': <b>' + _esc(ve.get("source")) + '</b></span>'
                 + '<span>' + _esc(ve.get("collection","")) + '</span></div>')
    if ve.get("note"):
        parts.append('<p class="muted">' + _esc(ve["note"]) + '</p>')
    th = '<table><thead><tr>'
    th += '<th>#</th>'
    th += '<th>' + '项目' + '</th>'
    th += '<th>' + '区域' + '</th>'
    th += '<th class="num">' + '年份' + '</th>'
    th += '<th class="num">' + '单价' + '</th>'
    th += '<th>' + '面积段' + '</th>'
    th += '<th>' + '开发商' + '</th>'
    th += '<th class="num">' + '相似度' + '</th>'
    th += '</tr></thead><tbody>'
    parts.append(th)
    for i, x in enumerate(items, 1):
        m = x.get('metadata') or {}
        dist = x.get('distance')
        if dist is None:
            sim_str = '—'
            bar_w = 0
        else:
            sim_pct = max(0, (1 - min(dist, 1.0)) * 100)
            sim_str = str(int(sim_pct)) + '%'
            bar_w = int(sim_pct * 1.2)
        sim_cell = '<span style="width:' + str(bar_w) + 'px;background:#1040c0;height:10px;display:inline-block;vertical-align:middle;margin-right:6px;"></span>' + sim_str
        row = '<tr><td>' + str(i) + '</td>'
        row += '<td><b>' + _esc(m.get('project_name','-')) + '</b></td>'
        row += '<td>' + _esc(m.get('district','-')) + '</td>'
        row += '<td class="num">' + str(m.get('year','-')) + '</td>'
        row += '<td class="num">' + str(m.get('unit_price','-')) + '</td>'
        row += '<td>' + _esc(m.get('area_range','-')) + '</td>'
        row += '<td>' + _esc(m.get('developer','-')) + '</td>'
        row += '<td class="num">' + sim_cell + '</td></tr>'
        parts.append(row)
    parts.append('</tbody></table>')
    return "\n".join(parts)



def render_pain_need_md(abm):
    if not abm:
        return "_客群语义画像不可用_\n"
    pains = abm.get("top_pains") or []
    needs = abm.get("top_details") or []
    if not pains and not needs:
        return "_客群语义字段尚未接入_\n"
    lines = ["_来自 N=" + str(abm.get("n_personas", 0)) + " 个真实虚拟样本的购房意愿加权聚合_  ", ""]
    if pains:
        lines.append("### 核心居住痛点")
        lines.append("")
        lines.append("| 排名 | 痛点 | 权重 | 影响权重图表 |")
        lines.append("|---:|---|---:|:---:|")
        max_w = max((x.get("weight", 0) for x in pains[:5]), default=1.0)
        for i, x in enumerate(pains[:5], 1):
            bar_str = ascii_bar(x.get("weight", 0), max_val=max_w)
            lines.append(f"| {i} | {x['pain']} | {x['weight']*100:.1f}% | `{bar_str}` |")
        lines.append("")
    if needs:
        lines.append("### 核心细节需求")
        lines.append("")
        lines.append("| 排名 | 需求 | 权重 | 影响权重图表 |")
        lines.append("|---:|---|---:|:---:|")
        max_w = max((x.get("weight", 0) for x in needs[:5]), default=1.0)
        for i, x in enumerate(needs[:5], 1):
            bar_str = ascii_bar(x.get("weight", 0), max_val=max_w)
            lines.append(f"| {i} | {x['need']} | {x['weight']*100:.1f}% | `{bar_str}` |")
    return "\n".join(lines) + "\n"


def render_pain_need_html(abm):
    if not abm:
        return '<p class="muted">客群语义画像不可用</p>'
    pains = abm.get("top_pains") or []
    needs = abm.get("top_details") or []
    if not pains and not needs:
        return '<p class="muted">客群语义字段尚未接入</p>'
    parts = []
    parts.append('<p class="muted">来自 N=' + str(abm.get("n_personas", 0)) +
                 ' 个真实虚拟样本的购房意愿加权聚合</p>')
    if pains:
        parts.append('<h3>' + '核心居住痛点 Top 5' + '</h3>')
        parts.append('<table><thead><tr>'
                     + '<th>#</th><th>' + '痛点' + '</th>'
                     + '<th class="num">' + '权重' + '</th>'
                     + '<th>' + '条形' + '</th>'
                     + '</tr></thead><tbody>')
        max_w = max((x.get('weight', 0) for x in pains[:5]), default=0.01)
        for i, x in enumerate(pains[:5], 1):
            w = x.get('weight', 0)
            bar_w = int(w / max_w * 240)
            bar = '<span style="display:inline-block;height:14px;width:' + str(bar_w) + 'px;background:#d02020;"></span>'
            row = '<tr><td>' + str(i) + '</td>'
            row += '<td>' + _esc(x.get('pain', '')) + '</td>'
            row += '<td class="num"><b>' + str(round(w*100, 1)) + '%</b></td>'
            row += '<td>' + bar + '</td></tr>'
            parts.append(row)
        parts.append('</tbody></table>')
    if needs:
        parts.append('<h3>' + '核心细节需求 Top 5' + '</h3>')
        parts.append('<table><thead><tr>'
                     + '<th>#</th><th>' + '需求' + '</th>'
                     + '<th class="num">' + '权重' + '</th>'
                     + '<th>' + '条形' + '</th>'
                     + '</tr></thead><tbody>')
        max_w = max((x.get('weight', 0) for x in needs[:5]), default=0.01)
        for i, x in enumerate(needs[:5], 1):
            w = x.get('weight', 0)
            bar_w = int(w / max_w * 240)
            bar = '<span style="display:inline-block;height:14px;width:' + str(bar_w) + 'px;background:#1040c0;"></span>'
            row = '<tr><td>' + str(i) + '</td>'
            row += '<td>' + _esc(x.get('need', '')) + '</td>'
            row += '<td class="num"><b>' + str(round(w*100, 1)) + '%</b></td>'
            row += '<td>' + bar + '</td></tr>'
            parts.append(row)
        parts.append('</tbody></table>')
    return "\n".join(parts)



# ============================================================
# CEO 权重学习：从用户拖动行为 EMA 反推个性化权重
# ============================================================
import hashlib
from datetime import datetime as _dt

_CEO_LEARN_DIR = ROOT / "data_out" / "ceo_learning"
_CEO_LEARN_DIR.mkdir(parents=True, exist_ok=True)


def _user_log_path(user_id):
    safe = hashlib.md5(user_id.encode("utf-8")).hexdigest()[:12]
    return _CEO_LEARN_DIR / f"user_{safe}.jsonl"


def record_user_weights(user_id, weights, context=None):
    if not weights or not user_id:
        return False
    log = _user_log_path(user_id)
    entry = {
        "ts": _dt.now().isoformat(timespec="seconds"),
        "weights": weights,
        "context": context or {},
    }
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def load_learned_weights(user_id, alpha=0.3, min_samples=3):
    log = _user_log_path(user_id)
    if not log.exists():
        return None
    entries = []
    with open(log, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if len(entries) < min_samples:
        return None
    keys = set()
    for e in entries:
        keys.update((e.get("weights") or {}).keys())
    ema = {k: 0.0 for k in keys}
    for e in entries:
        w = e.get("weights") or {}
        for k in keys:
            ema[k] = alpha * w.get(k, 0) + (1 - alpha) * ema[k]
    total = sum(ema.values())
    if total > 0:
        ema = {k: round(v / total, 4) for k, v in ema.items()}
    return {
        "weights": ema,
        "sample_count": len(entries),
        "method": "EMA alpha=" + str(alpha),
        "ts_first": entries[0].get("ts"),
        "ts_last": entries[-1].get("ts"),
    }


def predict_future_focus(user_id, top_k=3):
    learned = load_learned_weights(user_id)
    if not learned:
        return None
    ranked = sorted(learned["weights"].items(), key=lambda x: x[1], reverse=True)[:top_k]
    return {
        "user_id_hash": hashlib.md5(user_id.encode("utf-8")).hexdigest()[:12],
        "top_focus": [{"agent": k, "weight": v} for k, v in ranked],
        "sample_count": learned["sample_count"],
        "interpretation": _focus_interpret(ranked),
    }


def _focus_interpret(ranked):
    if not ranked:
        return ""
    primary = ranked[0][0]
    mapping = {
        "compliance": "合规底线优先 — 政策风险敏感",
        "value": "价值挖掘优先 — 关注溢价空间",
        "abm": "ABM",
        "unit_mix": "UM",
        "migration": "MG",
        "blueprint": "BP",
    }
    return mapping.get(primary, primary)
