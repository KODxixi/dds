"""
DDS Agent v2 决策算法编排器

把地块画像报告升级为：数据锚点、合规红线、价值机会、ABM 客群、任务书、蓝图风险。
"""
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

# ── AIPM 认知层：动态置信度 + 推理可视化 + 不确定标注 ──
try:
    from scripts.confidence_engine import (
        compute_confidence, ConfidenceInput, ConfidenceOutput, to_dict as conf_to_dict,
        confidence_from_abm, confidence_from_premium, confidence_from_compliance,
        confidence_from_value, trust_level, TRUST_LEVELS, compute_freshness,
    )
    from scripts.decision_trace import (
        DecisionTrace, generate_trace_id, annotate_uncertainty, render_uncertainty_html,
    )
    _AIPM_COGNITIVE_ENABLED = True
except ImportError:
    _AIPM_COGNITIVE_ENABLED = False

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

# ── 血缘追踪（T10+）──
try:
    from scripts.governance.lineage_tracker import get_tracker
    _LINEAGE_ENABLED = True
except ImportError:
    _LINEAGE_ENABLED = False


def run_decision_engine(parcel_report: dict, client_goal: dict, online_evidence: list[dict] | None = None) -> dict:
    # ── 血缘追踪上下文 ──
    city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city") or ""
    district = parcel_report.get("input", {}).get("district") or parcel_report.get("location", {}).get("district") or ""

    tracker = None
    if _LINEAGE_ENABLED:
        try:
            tracker = get_tracker()
            tracker.push_context(type("RunContext", (), {
                "run_id": f"decision_{datetime.now().strftime('%Y%m%dT%H%M%S')}",
                "pipeline_name": "决策引擎",
                "city": city,
                "params": {"district": district, "persona": client_goal.get("persona", "")},
                "graph": None,
            })())
            # 初始化 graph
            from scripts.governance.lineage_tracker import LineageGraph
            tracker.active_context.graph = LineageGraph(name="决策引擎")
            # 记录输入数据读取
            tracker.track_read(
                f"Vault/2026新楼盘/新楼盘-{city}.csv",
                name=f"新楼盘-{city}",
                source="安居客购买数据",
                source_url="",
                source_note="全国新盘结构化数据，Schema v2.0 253 列",
                transform_name="决策引擎",
            )
            tracker.track_read(
                f"Vault/2026新楼盘/二手房小区-{city}.csv" if city else "",
                name=f"二手房小区-{city}",
                source="安居客购买数据",
                transform_name="决策引擎",
            )
            tracker.track_transform(
                name="决策引擎",
                function_name="run_decision_engine",
                args={"city": city, "district": district, "persona": client_goal.get("persona", "")},
            )
        except Exception:
            pass

    try:
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
    except Exception as e:
        print(f"[warn] 读取在线证据目录失败: {e}")

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
    unit_premium = run_unit_premium_agent(client_goal, briefing, value, abm)
    blueprint = run_blueprint_logic(parcel_report, client_goal, value, compliance, abm)

    # 升值/溢价引擎(北极星层):消费 价值/时间穿越/客群迁移/蓝图 输出,拧成升值推演链
    try:
        from scripts.premium_engine import run_premium_engine
        premium = run_premium_engine(parcel_report, client_goal, value, abm, migration, time_travel, blueprint)
    except Exception as e:
        premium = {"status": "error", "error": str(e)}

    # ── AIPM 认知层：推理链追踪 + 动态置信度 ──
    trace = None
    uncertainty_annotations = []
    if _AIPM_COGNITIVE_ENABLED:
        t0 = time.perf_counter()
        trace = DecisionTrace(
            trace_id=generate_trace_id(),
            persona=client_goal.get("persona", "developer"),
            city=city,
            address=parcel_report.get("input", {}).get("address", ""),
            created_at=datetime.now().isoformat(),
        )

        # 数据基础层
        trace.add_step(
            "data_foundation", f"城市={city}, 产品类型={product_type}",
            f"在线证据 {len(data_foundation.get('online_evidence', []))} 条, 高相关 {sum(1 for e in data_foundation.get('online_evidence', []) if e.get('is_highly_relevant'))} 条",
            "从 data_out/online_evidence/ 读取 JSONL 并做高维关联度交叉校验（城市+产品类型+概念匹配）",
            confidence=conf_to_dict(ConfidenceOutput(
                score=0.6, level="中", grade="B",
                breakdown={}, caveats=["在线证据来源为 L3 辅助决策层，仅作交叉验证"],
                actionable=True, recommendation="在线证据仅供参考", target_score=0.8,
                target_note="接入 L1 真实成交数据可提升置信度",
            )),
        )

        # 合规 Agent
        verdict = compliance.get("verdict", "unknown")
        blockers = compliance.get("blockers", [])
        trace.add_step(
            "compliance_agent", f"地块={city}, 产品类型={product_type}",
            f"结论: {verdict}, 硬阻断: {len(blockers)} 项",
            f"检查规划条件/用地性质/限高/容积率等一级法定硬约束 → {'放行' if verdict == 'clear' else '条件性通过' if verdict == 'conditional' else '阻断'}",
            confidence=conf_to_dict(confidence_from_compliance(
                verdict, compliance.get("hard_constraints_missing", False), len(blockers)
            )),
        )
        uncertainty_annotations.extend(annotate_uncertainty(compliance, "compliance_agent"))

        # 价值 Agent
        num_competitors = len(value.get("competitors", []))
        ref_price = value.get("product_reference", {}).get("avg_price", 0)
        trace.add_step(
            "value_agent", f"城市={city}, 预期均价={client_goal.get('expected_price', 'N/A')}",
            f"竞品: {num_competitors} 个, 参考均价: {ref_price:.0f} 元/㎡" if ref_price else f"竞品: {num_competitors} 个",
            f"从 DuckDB 本地楼盘库 Haversine 距离筛选竞品 → 价格带分析 → 产品面积段参考",
            confidence=conf_to_dict(confidence_from_value(
                num_competitors, value.get("benchmark_premium_ratio", 0) or 0, ref_price or 0
            )),
        )
        uncertainty_annotations.extend(annotate_uncertainty(value, "value_agent"))

        # ABM Agent
        abm_conf = abm.get("confidence", {})
        trace.add_step(
            "abm_market_agent", f"城市={city}, 客群池={len(abm.get('archetype_mix', {}))} 类",
            f"平均购买概率: {abm.get('avg_buy_probability', 0):.1%}, 价格敏感度: {len(abm.get('price_sensitivity', []))} 档",
            f"MNL 随机效用模型 + 蒙特卡洛 1000 样本 → 去化曲线 → 价格敏感性 → 户型偏好",
            confidence=conf_to_dict(confidence_from_abm(
                abm.get("archetype_stats", {}), abm.get("sample_count", 1000),
                use_real_data=abm_conf.get("use_real_data", False),
            )),
        )
        uncertainty_annotations.extend(annotate_uncertainty(abm, "abm_agent"))

        # 溢价引擎
        trace.add_step(
            "premium_engine", f"城市={city}, 产品类型={product_type}",
            f"入市溢价: {premium.get('market_entry_premium', {}).get('total', 'N/A')}, 持有期曲线: {len(premium.get('holding_curve', []))} 年",
            "六维驱动（区位/设计/政策/品牌/稀缺/供需）→ 入市溢价分解 → 持有期曲线 → 二手保值",
        )
        uncertainty_annotations.extend(annotate_uncertainty(premium, "premium_engine"))

        # 蓝图逻辑
        trace.add_step(
            "blueprint_logic", f"容积率={parcel_report.get('land', {}).get('plot_ratio', 'N/A')}",
            f"推荐户型配比: {len(blueprint.get('unit_mix', []))} 种, 风险项: {len(blueprint.get('risks', []))}",
            "合规红线 → 价值机会 → ABM 客群 → 最优户型配比 → 风险预警",
        )

        trace.total_duration_ms = (time.perf_counter() - t0) * 1000
    summary = build_decision_summary(parcel_report, client_goal, compliance, value, abm, briefing, blueprint)
    traceability = build_traceability(parcel_report, data_foundation, online_evidence)
    _partial = {
        "compliance_agent": compliance, "value_agent": value,
        "abm_market_agent": abm, "unit_mix_agent": unit_mix,
        "migration_agent": migration, "blueprint_logic": blueprint,
    }
    ceo = run_ceo_aggregator(_partial, preset=client_goal.get("ceo_preset") or "invest")
    result = {
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
        "unit_premium_agent": unit_premium,
        "blueprint_logic": blueprint,
        "premium_engine": premium,
        "decision_summary": summary,
        "ceo_aggregator": ceo,
        "traceability": traceability,
        # ── AIPM 认知层输出 ──
        "decision_trace": trace.to_dict() if trace else None,
        "uncertainty_annotations": [
            {"field": a.field, "reason": a.reason, "severity": a.severity,
             "suggestion": a.suggestion, "fallback_value": a.fallback_value}
            for a in uncertainty_annotations
        ] if uncertainty_annotations else [],
        "cognitive_meta": {
            "aipm_enabled": _AIPM_COGNITIVE_ENABLED,
            "confidence_version": "1.0-dynamic",
            "trace_version": "1.0-whitebox",
            "uncertainty_version": "1.0-annotate",
        } if _AIPM_COGNITIVE_ENABLED else None,
    }
    # ── 血缘追踪：记录输出并持久化 ──
    if tracker and tracker.active_context:
        try:
            tracker.track_write(
                f"data_out/reports/decision/{city}_{district or 'default'}_decision.json",
                name=f"决策报告-{city}",
                report_type="decision",
                source="DDS 决策引擎",
                source_url="",
                source_note="多智能体协作输出：合规红线+价值机会+ABM客群+最优户型配比+风险预警",
                transform_name="决策引擎",
            )
            tracker.active_context.mark_complete()
            tracker._persist_run(tracker.active_context)
            tracker.pop_context()
        except Exception:
            pass
    return result

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
    # 高端/全球化对标注入（旗舰标杆匹配 + 下一代产品线推演）
    high_end_benchmarks = {"status": "skipped", "items": []}
    design_market_link = {"status": "skipped"}
    archlib_refs = {"status": "skipped"}
    try:
        from scripts.benchmark_engine import match_benchmarks, extrapolate_next_gen
        city = parcel_report.get("input", {}).get("city") or parcel_report.get("location", {}).get("city")
        ptype = client_goal.get("product_type")
        # 设计师视角更侧重近几年(2021+)的现象级产品 → 开启近期偏好
        _recent = (client_goal.get("ceo_preset") == "design")
        high_end_benchmarks = match_benchmarks(city, avg_price, ptype, top_k=4, recent_bias=_recent)
        high_end_benchmarks["next_gen"] = extrapolate_next_gen(city, ptype)  # 下一代推演
        # 设计×市场联动(护城河层):溢价分解 + 逆向设计任务书 + 设计杠杆器
        from scripts.design_market_link import analyze as _dml_analyze
        design_market_link = _dml_analyze(parcel_report, client_goal, high_end_benchmarks,
                                          competitors=competitors, base_price=avg_price,
                                          benchmark_premium_ratio=premium_ratio)
        # ArchLib 视觉参考板(设计师):按产品类型 + 标杆关键动作检索已打标案例
        from scripts.archlib_index import search_archlib
        _akw = (high_end_benchmarks.get("items") or [{}])[0].get("key_tactics") or []
        archlib_refs = search_archlib(building_type=ptype, keywords=_akw[:4], top_k=6)
    except Exception as e:
        high_end_benchmarks = {"status": "error", "error": str(e), "items": []}
        design_market_link = {"status": "error", "error": str(e)}
        archlib_refs = {"status": "error", "error": str(e)}
    return {
        "role": "价值 Agent",
        "market_avg_price_cny": avg_price,
        "benchmark": benchmark,
        "benchmark_premium_ratio": premium_ratio,
        "opportunities": opportunity,
        "product_reference": build_product_reference(competitors),
        "vector_evidence": vector_evidence,
        "high_end_benchmarks": high_end_benchmarks,
        "design_market_link": design_market_link,
        "archlib_refs": archlib_refs,
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

    # 提取地块约束条件用于任务书
    parcel_lng = parcel_report.get("lng")
    parcel_lat = parcel_report.get("lat")
    district = parcel_report.get("district") or ""
    far = client_goal.get("far") or parcel_report.get("vision", {}).get("far")
    height_limit = client_goal.get("height_limit") or parcel_report.get("vision", {}).get("height_limit_m")

    # 基于实际约束生成详细设计任务书
    briefing_detail = {
        "强排与总平": {
            "核心约束": [
                f"容积率 {far}" if far else "容积率待确认（需挂牌文件/控规图则）",
                f"限高 {height_limit}m" if height_limit else "限高待确认（需挂牌文件）",
                "退距要求：南侧≥18m（建筑间距一半），东西北侧≥3m",
                "日照：大寒日≥2h（济南地区），不满户数控制在总户数5%以内",
            ],
            "推荐策略": [
                "东低西高布局：东侧洋房(7-10F)最大化长岭山景观，西侧中高(17F)用足容积率",
                "南偏东15°朝向：同时满足山景视野最大化 + 降低凤山路交通噪声",
                "沿等高线设置台地：东西向4级台地，减少土方开挖（白泉泉域≤6m限制）",
                "主入口临凤山路设60m展示面，230m中心景观主轴贯穿全区",
                "楼栋均匀分布：避免兵营式排布，各组团享有独立景观绿地",
            ],
        },
        "流线与动线": {
            "车行": [
                "主入口沿凤山路，次入口沿凤山北路（规划未建），人车分流",
                "地库入口靠近主次入口，避免深入小区内部",
                "访客车位结合主入口附近地面布置，减少对小区内部干扰",
            ],
            "人行": [
                "主入口→中心花园→各组团大堂，形成三级归家序列",
                "下沉庭院结合会所，打造「入园-下庭-入户」垂直归家体验",
                "无障碍通道贯穿全区，台地高差处以景观台阶+无障碍坡道组合解决",
            ],
            "后勤": [
                "垃圾收集点沿凤山路/凤山北路外围布置，避开主景观轴",
                "快递/外卖设置小区入口智能柜+物业代收，不进入居住组团",
            ],
        },
        "大门与展示区": {
            "社区大门": [
                "临凤山路主入口：面宽≥60m，结合地形高差做台地式入口",
                "大门高度控制：与凤山路形成>=2m高差，营造'登堂入室'仪式感",
                "材质建议：石材+金属格栅+玻璃，日间通透/夜间灯光勾勒轮廓",
                "门卫/快递/访客登记等功能整合于大门两侧，主通道≥6m净宽",
            ],
            "示范区": [
                "售楼部约600㎡（面客区450+后勤150），利用下沉庭院自然采光",
                "园林展示区约7,000㎡，含主入口-中心花园-样板房完整归家动线",
                "样板房：3套（临时1套于售楼部旁+实体2套于首批开盘楼栋）",
                "样板房选型：130㎡三房+140㎡四房+160㎡四代宅洋房（覆盖全产品线）",
                "示范区开放时同步展示会所核心功能区（健身房/泳池/书吧）",
            ],
        },
        "抬板与竖向措施": {
            "工程约束": "白泉泉域保护区，地下开挖≤6m，必须采用抬板方式解决竖向",
            "实施方案": [
                "沿等高线设4级东西向台地，每级高差约5m，总高差覆盖20m",
                "台地挡墙以景观化处理：毛石砌筑+爬藤绿化，非简单混凝土挡墙",
                "台地间以景观台阶+无障碍坡道+景观电梯（局部）连接",
                "抬板下空间利用：非开挖区域可做架空层/半地下车库（≤6m覆土）",
                "排水：台地间设截水沟+盲沟，防止雨水汇集冲刷挡墙",
            ],
            "成本控制": "抬板增加土方约15-20%成本，需在精装/立面中通过溢价回收",
        },
        "设计风格与立面": {
            "整体风格": "现代东方·都市度假——以济南'一城山色'为底色，融合高端度假酒店质感",
            "立面策略": [
                "中高(17F)：现代简约框筒，浅灰+木色金属饰面，横向线条强调",
                "小高(11F)：石材基座+金属中层+玻璃顶层，三段式经典比例",
                "洋房(7-10F)：石材+仿铜金属线条，坡屋顶/平坡结合，呼应山地语境",
                "四代宅洋房：错层露台绿化+玻璃栏板，立体绿化融入长岭山背景",
            ],
            "材料控制": "石材(首层及基座)/真石漆(标准层)/金属饰面板(局部强调)/Low-E玻璃",
        },
        "户型选型参考": {
            "说明": "以下为甲方预立项可研V4确定的户型方案，建议整合入DDS户配推荐",
            "130三房两卫(中高17F)": {
                "计容面积": "120㎡", "户数": "408(37.8%)",
                "核心参数": "面宽12.4m，横厅6m餐客一体，得房率83.5%（含赠送）",
                "对标": "海信君安128（主次卧开间全面超越）",
            },
            "140四房两卫(中高/小高)": {
                "计容面积": "131.6㎡", "户数": "508(47.0%)",
                "核心参数": "面宽13.8m，四房两卫+独立餐厅，得房率84.2%（含赠送）",
                "对标": "云上璟誉150+海信君安143（同等功能面积更小）",
            },
            "140四房两卫(四代宅洋房)": {
                "计容面积": "131.6㎡", "户数": "136(12.6%)",
                "核心参数": "开敞阳台错层，露台全赠送，得房率99-101%",
                "对标": "业态碾压中高产品，赠送率远超传统洋房",
            },
            "160四房三卫(四代宅洋房)": {
                "计容面积": "131.6㎡计容", "户数": "28(2.6%)",
                "核心参数": "面宽13.8m+，双套间+独立电梯厅，得房率111%",
                "对标": "银丰玖玺城173+凤凰路壹号院177（面积少13+㎡）",
            },
        },
    }

    return {
        "role": "任务书制定 Agent",
        "product_positioning": f"以改善产品为方向，聚焦功能/品质改善+高端改善双核，打造'都市度假感的宜人社区'。",
        "target_price_band": target_band,
        "briefing_detail": briefing_detail,
        "performance_brief": [
            "私密性等级：控制公共界面干扰，强化院落/入户/露台的边界感。东侧洋房区独立成团，西侧中高区围合大花园。",
            "视觉通透度：优先把景观面（东侧长岭山/南向远山天际线）和主力功能空间绑定，避免只做符号化立面。",
            "垂直动线效率：低密产品需减少无效交通面积。洋房独立电梯入户，中高优化核心筒保证76%+使用率。",
            "到达仪式感：车行→大门(60m面宽台地入口)→中心花园(230m主轴)→下沉庭院会所→组团大堂→入户，五级归家。",
            "可售弹性：面积段覆盖130-160㎡，主力130+140占85%，总价锚定290-420万，覆盖64%客群购买力。",
            "服务配置：2,000㎡会所(健身房+泳池+书吧+私宴厅+儿童区+康养角)可感知且可持续运营。",
        ],
        "premium_hypotheses": [
            "四代宅洋房（7-10F，得房率99-111%）建立产品代差，可支撑10-15%溢价",
            "台地景观+下沉会所+230m中心花园构成'看得见的好'，不应被低估为普通景观包装",
            "2.0低密容积率+长岭山一线山景=稀缺资源溢价，可对标凤凰路壹号院3万+价格锚点",
        ],
        "compliance_dependency": compliance.get("hard_constraints_missing", []) or [
            "容积率上限", "用地性质", "限高", "退线", "绿地率", "车位指标", "商业比例", "产权/分割销售口径"
        ],
        "pain_hedging_guide": pain_briefs,
    }


def build_target_price_band(avg_price: float | None, benchmark_price: float | None) -> dict:
    if not avg_price:
        return {"status": "insufficient_price_data"}
    conservative = round(avg_price * 0.85, 0)
    balanced = round(avg_price, 0)
    aggressive = round(min(benchmark_price * 0.85, avg_price * 1.35), 0) if benchmark_price else round(avg_price * 1.2, 0)
    return {"conservative": conservative, "balanced": balanced, "aggressive": aggressive, "unit": "元/㎡"}


# ── 户型得房率溢价专篇 ──────────────────────────────────────────────────────

# 四代宅赠送政策参考（各城市口径不同，需根据当地规划条件确认）
FOURTH_GEN_POLICY = {
    "全国通用": {
        "空中花园/露台": "不计容或半计容（需满足挑高≥2层、开敞率≥50%）",
        "设备平台": "不计容（需满足面积比例限制）",
        "飘窗": "不计容（出挑≤0.6m，窗台高≥0.45m）",
        "架空层": "不计容（净高≥3.6m，仅作公共空间）",
    },
    "济南": {
        "空中花园": "按地方技术规定，开敞式空中花园满足挑高≥2层且开敞率≥50%可申请不计容",
        "封闭阳台": "地块规划条件要求封闭阳台 → 四代宅需申请开敞阳台豁免或错层设计合规",
        "地下空间": "白泉泉域≤6m限深 → 抬板利用地上高度替代地下开挖",
    },
}

def run_unit_premium_agent(client_goal: dict, briefing: dict, value: dict, abm: dict) -> dict:
    """户型得房率溢价专篇：基于甲方户型设计 + 四代宅赠送政策 + benchmark案例，
    计算得房率提升带来的等效降价/溢价空间。"""
    avg_price = value.get("market_avg_price_cny") or 25000
    target_band = (value.get("target_price_band") or
                   briefing.get("target_price_band") or
                   {"balanced": avg_price, "unit": "元/㎡"})
    balanced_price = target_band.get("balanced", avg_price)

    # 从 task book 取户型设计（甲方预立项数据）
    bd = briefing.get("briefing_detail", {})
    unit_ref = (bd.get("户型选型参考") if isinstance(bd, dict) else {}) or {}

    # 户型得房率对比表（硬数据来自甲方预立项PPT，不可脑补）
    # 中高17F: 130㎡ 使用率76.51%→含赠送83.5%, 140㎡ 使用率76.80%→含赠送84.2%
    # 小高11F: 同中高17F
    # 四代宅洋房(7-10F): 使用率80.65%→含赠送99.3-100.5%(140), 使用率81.60%→含赠送111.3%(160)
    import re
    unit_comparison = []
    for ut_name, ut_info in unit_ref.items():
        if not isinstance(ut_info, dict):
            continue
        eff_str = ut_info.get("核心参数", "")
        area_str = ut_info.get("计容面积", "")
        try:
            area_val = float(area_str.replace("㎡", "").replace("计容", "").replace("计容", "").strip() or 0)
        except (ValueError, TypeError):
            area_val = 0

        # 解析得房率: 提取最后一个/最高的%值（含赠送得房率通常在最后）
        # "得房率99.3-100.5%" → 取平均值; "得房率83.5%" → 83.5
        eff_matches = re.findall(r'(\d+\.?\d*)\s*%', eff_str)
        if eff_matches:
            # 取所有匹配中的最大值作为含赠送得房率
            eff_values = [float(v) for v in eff_matches]
            far_eff = max(eff_values)  # 含赠送得房率=最高值
            standard_eff = min(eff_values) if len(eff_values) >= 2 else None  # 基础使用率=最低值
        else:
            far_eff = 0
            standard_eff = None

        # 如果只有一个值，且是四代宅，基础得房率取已知值
        is_4th = "四代" in ut_name or "四代" in eff_str
        if is_4th and standard_eff is None and far_eff > 90:
            # 四代宅洋房基础使用率: 140=80.65%, 160=81.60%
            if "160" in ut_name:
                standard_eff = 81.6
            else:
                standard_eff = 80.65

        # 赠送面积估算
        if far_eff and standard_eff and far_eff > standard_eff:
            bonus_rate = far_eff - standard_eff
            bonus_area = area_val * bonus_rate / 100
            bonus_value = bonus_area * balanced_price / 10000
            effective_discount = bonus_rate / far_eff * 100
        else:
            bonus_rate = 0
            bonus_area = 0
            bonus_value = 0
            effective_discount = 0

        unit_comparison.append({
            "name": ut_name,
            "计容面积": area_val,
            "基础得房率(不含赠送)": round(standard_eff or 0, 1),
            "含赠送得房率": round(far_eff or 0, 1),
            "得房率提升(pct)": round(bonus_rate, 1),
            "赠送面积(m²)": round(bonus_area, 1),
            "赠送面积价值(万元)": round(bonus_value, 1),
            "等效价格折扣": f"{effective_discount:.1f}%",
            "四代宅": is_4th,
            "对标竞品": ut_info.get("对标", ""),
        })

    # 四代宅溢价逻辑
    fourth_gen_units = [u for u in unit_comparison if u["四代宅"]]
    standard_units = [u for u in unit_comparison if not u["四代宅"]]
    avg_4th_eff = sum(u["含赠送得房率"] for u in fourth_gen_units) / len(fourth_gen_units) if fourth_gen_units else 0
    avg_std_eff = sum(u["含赠送得房率"] for u in standard_units) / len(standard_units) if standard_units else 0
    eff_gap = avg_4th_eff - avg_std_eff

    # 溢价计算
    # 得房率提升 = 每花1元买到更多使用面积 → 等效降价 = 得房率差距/四代得房率
    if avg_4th_eff > 0 and eff_gap > 0:
        equivalent_discount_pct = round(eff_gap / avg_std_eff * 100, 1)
        # 假设开发商能捕获50%的等效折扣作为溢价
        capturable_premium_pct = round(equivalent_discount_pct * 0.5, 1)
        capturable_premium_cny = round(balanced_price * capturable_premium_pct / 100, 0)
    else:
        equivalent_discount_pct = 0
        capturable_premium_pct = 0
        capturable_premium_cny = 0

    # 提取四代宅 benchmark 案例
    benchmarks = (value.get("high_end_benchmarks") or {}).get("items", [])
    fourth_gen_benchmarks = []
    for bm in benchmarks:
        tags = bm.get("key_tactics", []) + bm.get("signature", [])
        if any("四代" in t or "空中花园" in t or "错层露台" in t or "高赠送" in t for t in tags):
            fourth_gen_benchmarks.append({
                "name": bm.get("name", ""),
                "headline": bm.get("headline", ""),
                "key_tactics": bm.get("key_tactics", []),
                "relevance": bm.get("relevance", 0),
            })

    return {
        "role": "户型得房率溢价 Agent",
        "policy_basis": FOURTH_GEN_POLICY,
        "unit_comparison": unit_comparison,
        "summary": {
            "标准得房率均值": f"{avg_std_eff:.1f}%" if avg_std_eff else "N/A",
            "四代宅得房率均值": f"{avg_4th_eff:.1f}%" if avg_4th_eff else "N/A",
            "得房率差距": f"{eff_gap:.1f} pct",
            "等效价格折扣": f"{equivalent_discount_pct:.1f}%",
            "可捕获溢价率": f"{capturable_premium_pct:.1f}%",
            "可捕获溢价(元/㎡)": capturable_premium_cny,
            "溢价逻辑": (
                f"得房率从{avg_std_eff:.0f}%提升至{avg_4th_eff:.0f}%（+{eff_gap:.0f}pct），"
                f"等效于每平方米售价降低{equivalent_discount_pct:.1f}%。"
                f"假设开发商捕获50%等效折扣作为溢价，"
                f"可实现{capturable_premium_pct:.1f}%（约{capturable_premium_cny}元/㎡）溢价。"
                "核心驱动：四代宅开敞阳台+露台全赠送 + 奇偶错层不计容政策红利。"
            ),
        },
        "fourth_gen_benchmarks": fourth_gen_benchmarks,
        "recommendation": (
            "主力130/140㎡中高户型得房率83-84%已优于竞品（竞品多为76-80%），"
            "四代宅140/160㎡洋房得房率99-111%形成降维打击。"
            "建议：① 在售楼处设「得房率对比体验区」，量化展示得房率差异；"
            "② 将赠送面积价值写入合同附件，增强购买信心；"
            "③ 四代宅定价上浮8-12%，覆盖抬板/露台/绿化增加的成本并产生额外溢价。"
        ),
    }


def run_blueprint_logic(parcel_report: dict, client_goal: dict, value: dict, compliance: dict, abm: dict = None) -> dict:
    prices = [c.get("unit_price_cny") for c in parcel_report.get("nearby_competitors", []) if c.get("unit_price_cny")]
    avg_price = value.get("market_avg_price_cny") or 30000.0
    median_price = round(statistics.median(prices), 0) if prices else avg_price
    benchmark_price = (value.get("benchmark") or {}).get("unit_price_cny")
    far = client_goal.get("floor_area_ratio")

    try:
        from investment_engine import build_investment_case
    except ImportError:
        from scripts.investment_engine import build_investment_case
    investment_inputs = client_goal.get("investment_inputs")
    investment_case = build_investment_case(
        investment_inputs if isinstance(investment_inputs, dict) else {},
        source_refs=client_goal.get("investment_source_refs") or [],
    )

    # 拿地报价只有在售价、面积、地价、建安、税费和节奏全部有证据时才输出。
    # 旧版“售价 × 固定比例 × FAR”不是投资回报模型，不能伪装成拿地边界。
    land_band = None
    boundaries = investment_case.get("land_bid_boundaries") or {}
    target_floor = boundaries.get("target_margin_max_land_floor_price_cny_sqm")
    break_even_floor = boundaries.get("break_even_land_floor_price_cny_sqm")
    if target_floor is not None and break_even_floor is not None:
        land_band = {
            "conservative_land_value": round(float(target_floor) * 0.90, 0),
            "balanced_land_value": round(float(target_floor), 0),
            "aggressive_land_value": round(float(break_even_floor), 0),
            "unit": "元/㎡计容建筑面积",
            "floor_area_ratio": far,
            "floor_area_ratio_source": "user_input" if far is not None else None,
            "basis": "target-profit residual land value / break-even residual land value",
            "source_refs": list(investment_case.get("source_refs") or []),
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

    # 3b. IRR 单变量 ±10% 敏感性（龙卷风图数据；复用同一现金流模型真实重算，非脑补）
    def _ncf_for(sale_mult=1.0, land_mult=1.0, const_mult=1.0, tax_mult=1.0, absorb_mult=1.0):
        uv = unit_value * sale_mult
        lc = land_cost * land_mult
        cc = construction_cost * const_mult
        tr = tax_and_fee_rate * tax_mult
        ms = min(60.0, max(5.0, monthly_sales * absorb_mult))
        sl = total_units
        qsu = []
        for q in range(8):
            qs = min(sl, ms * 3.0) if sl > 0 else 0.0
            sl -= qs
            qsu.append(qs)
        infl = []
        ncf = []
        for q in range(8):
            qs = qsu[q]
            idp = qs * unit_value * sale_mult * avg_dp_ratio
            iloan = 0.0
            if q > 0:
                iloan = qsu[q - 1] * uv * (1.0 - avg_dp_ratio)
            if q == 7:
                tel = sum(qsu) * uv * (1.0 - avg_dp_ratio)
                iloan = max(iloan, tel - sum(infl[1:] if len(infl) > 1 else [0]))
            ti = idp + iloan
            infl.append(ti)
            ol = lc if q == 0 else 0.0
            oc = (cc / 4.0) if (1 <= q <= 4) else 0.0
            ot = ti * tr
            if q == 0:
                ot += total_value * 0.02
            ncf.append(round(ti - (ol + oc + ot), 1))
        return ncf

    irr_sensitivity = []
    if irr_annual is not None:
        _levers = [
            ("售价", "sale_mult"),
            ("拿地楼面价", "land_mult"),
            ("建安成本", "const_mult"),
            ("税费及资金成本", "tax_mult"),
            ("去化速度", "absorb_mult"),
        ]
        for _label, _key in _levers:
            _lo = _calculate_irr(_ncf_for(**{_key: 0.9}))
            _hi = _calculate_irr(_ncf_for(**{_key: 1.1}))
            if _lo is None or _hi is None:
                continue
            _a, _b = sorted([round(_lo * 100, 2), round(_hi * 100, 2)])
            irr_sensitivity.append({
                "lever": _label,
                "delta_pct": 10,
                "irr_low": _a,
                "irr_high": _b,
                "swing_pct": round(_b - _a, 2),
            })
        irr_sensitivity.sort(key=lambda x: -x["swing_pct"])

    if investment_case.get("status") in {"ready", "review"}:
        base_case = (investment_case.get("scenarios") or {}).get("base") or {}
        safe_metrics = base_case.get("metrics") or {}
        safe_irr = safe_metrics.get("unlevered_project_irr")
        financial_indicator = {
            "status": investment_case.get("status"),
            "method": "auditable unlevered monthly cash flow",
            "simulated_irr_annual": (
                f"{round(float(safe_irr) * 100, 2)}%"
                if safe_irr is not None
                else None
            ),
            "irr_level": (
                "需复核"
                if safe_metrics.get("irr_status") == "review"
                else ("高" if safe_irr is not None and safe_irr >= 0.15 else ("中" if safe_irr is not None and safe_irr >= 0.08 else "低"))
            ),
            "note": "未加杠杆项目 IRR；仅在完整财务输入和来源可审计时输出。",
            "irr_base_pct": round(float(safe_irr) * 100, 2) if safe_irr is not None else None,
            "irr_sensitivity": [
                {
                    "scenario": name,
                    "irr_pct": (
                        round(float((scenario.get("metrics") or {}).get("unlevered_project_irr")) * 100, 2)
                        if (scenario.get("metrics") or {}).get("unlevered_project_irr") is not None
                        else None
                    ),
                    "review_reason": (scenario.get("metrics") or {}).get("irr_review_reason"),
                }
                for name, scenario in (investment_case.get("scenarios") or {}).items()
            ],
            "evidence_confidence": investment_case.get("evidence_confidence"),
            "simulation_stability": investment_case.get("simulation_stability"),
        }
        safe_cash_flow = {
            "status": investment_case.get("status"),
            "frequency": "monthly",
            "unit": "万元",
            "rows": base_case.get("monthly_cash_flow") or [],
        }
    else:
        financial_indicator = {
            "status": "blocked",
            "method": "auditable unlevered monthly cash flow",
            "simulated_irr_annual": None,
            "irr_level": None,
            "note": "财务输入不完整，禁止用固定默认值生成 IRR、ROI 或拿地报价。",
            "irr_base_pct": None,
            "irr_sensitivity": [],
            "evidence_gaps": list(investment_case.get("evidence_gaps") or []),
        }
        safe_cash_flow = {
            "status": "blocked",
            "frequency": "monthly",
            "unit": "万元",
            "rows": [],
            "evidence_gaps": list(investment_case.get("evidence_gaps") or []),
        }

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
        "quarterly_cash_flow": safe_cash_flow,
        "financial_indicator": financial_indicator,
        "investment_case": investment_case,
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
        "## 高端/全球化对标与下一代产品推演",
        render_benchmark_md((decision.get("value_agent") or {}).get("high_end_benchmarks") or {}),
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
# 四套权重 preset（六维：compliance/value/abm/unit_mix/migration/blueprint）
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
    "buyer": {  # 购房者视角：保值(value) + 防风险/防烂尾(blueprint+compliance) + 板块趋势(migration)
        "compliance": 0.20, "value": 0.30, "abm": 0.05,
        "unit_mix": 0.05, "migration": 0.15, "blueprint": 0.25,
    },
}

# ============================================================
# PERSONA 镜头层（一核三视图）：同一套事实只算一次，按角色切换 权重/强调板块/话术/决策落点。
# emphasize/deemphasize 用前端 section id；endpoint 是该角色的核心决策问题。
PERSONAS = {
    "developer": {  # 开发商 / 投拓
        "label": "开发商",
        "ceo_preset": "invest",
        "subtitle": "输入地块坐标，输出投拓决策书：拿地价区间、货值、IRR、竞品格局与风险。",
        "cta": "生成投拓决策书",
        "endpoint": "拿不拿地 / 拿地价区间 / 货值与回报 / go-no-go",
        "emphasize": ["competitors", "financial-accounting", "unit-mix", "risk", "ceo-panel"],
        "deemphasize": [],
        "price_label": "预期售价 元/㎡（你打算卖多少）",
        "disclaimer": None,
    },
    "designer": {  # 设计师 / 产品定位
        "label": "设计师",
        "ceo_preset": "design",
        "subtitle": "输入地块坐标，输出产品定位书：目标客群、户型配比、面积段缺口与高端标杆对标。",
        "cta": "生成产品定位书",
        "endpoint": "做什么档次 / 户型配比 / 面积段 / 对标谁",
        "emphasize": ["personas", "unit-mix", "benchmark", "competitors"],
        "deemphasize": ["financial-accounting"],
        "price_label": "项目定位均价 元/㎡（可选）",
        "disclaimer": None,
    },
    "buyer": {  # 购房者
        "label": "购房者",
        "ceo_preset": "buyer",
        "subtitle": "输入楼盘地址，输出购房参考：值不值、同板块对比、开发商交付信用、周边配套与风险清单。",
        "cta": "生成购房参考",
        "endpoint": "值不值这个价 / 同类怎么选 / 有哪些风险（不给买卖结论）",
        "emphasize": ["value-for-money", "amenities", "developer-credit", "risk"],
        "deemphasize": ["financial-accounting", "unit-mix", "ceo-panel"],
        "price_label": "在售 / 意向单价 元/㎡（你考虑买的价）",
        # 购房者面向 C 端，强制信息工具口径，规避投资建议责任
        "disclaimer": "本报告为信息参考工具，不构成投资或购房建议；数据有口径与时效限制，请以官方备案及实地核验为准。",
    },
}


def resolve_persona(name: str | None) -> dict:
    """角色名 → persona 配置；非法值回退开发商。"""
    p = dict(PERSONAS.get((name or "").lower(), PERSONAS["developer"]))
    p["key"] = (name or "developer").lower() if (name or "").lower() in PERSONAS else "developer"
    return p


def _score_compliance(c: dict) -> tuple[float, float]:
    """合规 Agent → (raw_score, confidence) — AIPM 动态置信度"""
    verdict = c.get("verdict")
    if verdict == "clear":
        raw = 90.0
    elif verdict == "conditional":
        raw = 55.0 - min(15, len(c.get("blockers") or []) * 5)
    else:
        raw = 20.0
    if _AIPM_COGNITIVE_ENABLED:
        co = confidence_from_compliance(verdict, c.get("hard_constraints_missing", False),
                                         len(c.get("blockers") or []))
        conf = co.score
    else:
        conf = 0.5 if c.get("hard_constraints_missing") else 0.9
    return max(0, min(100, raw)), conf


def _score_value(v: dict) -> tuple[float, float]:
    """价值 Agent → (raw_score, confidence) — AIPM 动态置信度"""
    avg = v.get("market_avg_price_cny") or 0
    premium = v.get("benchmark_premium_ratio") or 1.0
    price_score = min(85, 30 + avg / 1000)
    raw = price_score + min(15, (premium - 1) * 20)
    samples = (v.get("product_reference") or {}).get("observed_area_ranges") or []
    if _AIPM_COGNITIVE_ENABLED:
        co = confidence_from_value(len(samples), premium, avg)
        conf = co.score
    else:
        conf = min(1.0, 0.5 + len(samples) / 20)
    return max(0, min(100, raw)), conf


def _score_abm(a: dict) -> tuple[float, float]:
    """ABM Agent → (raw_score, evidence confidence)。

    ``simulation_stability`` 与 ``temporal_consistency`` 只描述模型稳定度，
    不得叠加到 CEO 的真实证据置信度。
    """
    if a.get("status") == "city_not_configured":
        return 30.0, 0.3
    prob = a.get("avg_buy_probability") or 0
    raw = min(100, prob * 1000)
    evidence_conf = a.get("evidence_confidence") or a.get("confidence") or {}
    if isinstance(evidence_conf, dict) and evidence_conf.get("score") is not None:
        base_conf = float(evidence_conf.get("score"))
    elif _AIPM_COGNITIVE_ENABLED:
        co = confidence_from_abm(
            a, a.get("n_personas") or a.get("sample_count", 1000),
            use_real_data=(a.get("confidence") or {}).get("use_real_data", False),
        )
        base_conf = co.score
    else:
        base_conf = (a.get("confidence") or {}).get("score") or 0.5
    return max(0, min(100, raw)), max(0.0, min(1.0, base_conf))


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


def render_benchmark_md(bm):
    """高端/全球化对标 + 下一代推演 的 Markdown 渲染"""
    if not bm or bm.get("status") in ("skipped", "not_configured"):
        return "_高端对标未启用_\n"
    if bm.get("status") == "error":
        return f"_高端对标加载失败：{bm.get('error','')}_\n"
    items = bm.get("items") or []
    lines = [f"_{bm.get('note','')}_  ", ""]
    # 对标模式标注（同侪/越级/跨界），老数据缺 mode 时不输出
    _mode_label = {"peer": "同侪对标", "aspire": "越级对标", "cross": "跨界对标"}
    _mode = bm.get("mode")
    if _mode:
        _src = bm.get("source")
        _src_str = f"（对标主体：{_src}）" if _src else ""
        lines.append(f"**对标模式**：{_mode_label.get(_mode, _mode)}{_src_str}  ")
        lines.append("")
    if items:
        lines.append("| # | 标杆 | 开发商 / 设计 | 年份 | 现象级度 | 尖峰维度 | 强项维度 | 关键打法 | 溯源 |")
        lines.append("|---:|---|---|---:|---:|---|---|---|---|")
        for i, x in enumerate(items, 1):
            src = x.get("sources") or [{}]
            link = f"[源]({src[0].get('url','')})" if src and src[0].get("url") else "—"
            phenom = x.get("phenom")
            phenom_str = f"{phenom:.2f}" if isinstance(phenom, (int, float)) else "—"
            signature = x.get("signature") or []
            sig_str = "·".join(signature) if signature else "—"
            lines.append(
                f"| {i} | {x.get('name','-')} | {x.get('developer','-')} / {x.get('designer','-')} | "
                f"{x.get('year','-')} | {phenom_str} | {sig_str} | {'·'.join(x.get('strong_dims',[]))} | "
                f"{'；'.join(x.get('key_tactics',[])[:3])} | {link} |"
            )
        lines.append("")
        # 现象级解码：前 2-3 个 item 的 信号→机制→可迁移性（老数据无 decode 时整段跳过）
        decode_blocks = []
        for x in items[:3]:
            dec = x.get("decode") or {}
            if not dec:
                continue
            blk = [f"**◆ {x.get('name','-')}** 现象级解码  "]
            if dec.get("signal"):
                blk.append(f"- 信号（做了什么）：{dec['signal']}")
            if dec.get("mechanism"):
                blk.append(f"- 机制（为何有效）：{dec['mechanism']}")
            if dec.get("transfer"):
                blk.append(f"- 可迁移性（本案需付出）：{dec['transfer']}")
            _dsig = dec.get("signature") or []
            if _dsig:
                blk.append(f"- 杀手锏维度：{'·'.join(_dsig)}")
            if len(blk) > 1:
                decode_blocks.append("\n".join(blk))
        if decode_blocks:
            lines.append("#### 现象级解码（信号 → 机制 → 可迁移性）")
            lines.append("")
            lines.append("\n\n".join(decode_blocks))
            lines.append("")
    refs = bm.get("design_ref") or []
    if refs:
        names = "；".join(f"{x.get('name','-')}（{'·'.join(x.get('strong_dims',[])[:2])}）" for x in refs)
        lines.append(f"**落地设计参考（设计机构）**：{names}")
        lines.append("")
    ng = bm.get("next_gen") or {}
    moves = ng.get("priority_moves") or []
    if moves:
        lines.append("**下一代产品线·优先动作**（基于案例库 scores 的分析推演）  ")
        lines.append("")
        lines.append("| 维度 | 优先级 | 目标分 | 距天花板 | 动量 | 对标学习 |")
        lines.append("|---|---:|---:|---:|---|---|")
        for m in moves:
            lines.append(
                f"| {m.get('dimension')} | {m.get('priority')} | {m.get('target_score')} | "
                f"{m.get('gap_to_frontier')} | {m.get('momentum')} | {m.get('benchmark_to_study')} |"
            )
        lines.append("")
    # 下一代版本向量（目标分），老数据缺 next_version_vector 时跳过
    nvv = ng.get("next_version_vector") or {}
    if nvv:
        nvv_str = "；".join(f"{d}→{s}" for d, s in nvv.items())
        lines.append(f"**下一代版本向量（目标分）**：{nvv_str}  ")
        lines.append("")
    for combo in (ng.get("emerging_combos") or []):
        lines.append(f"- 🧬 **下一代组合**：{combo}")
    # 未来对标三阶梯：Tier0 当前天花板 / Tier1 已验证组合 / Tier2 未来范式
    ladder = ng.get("future_ladder") or {}
    if ladder:
        lines.append("")
        lines.append("### 未来对标三阶梯")
        lines.append("")
        t0 = ladder.get("tier0_frontier") or []
        if t0:
            lines.append("**Tier 0 · 当前天花板（单维·谁持有）**  ")
            lines.append("")
            lines.append("| 维度 | 天花板 | 持有者 | 打法 |")
            lines.append("|---|---:|---|---|")
            for f in t0:
                lines.append(
                    f"| {f.get('dim','-')} | {f.get('ceiling','-')} | "
                    f"{f.get('holder','-')} | {f.get('tactic','-')} |"
                )
            lines.append("")
        t1 = ladder.get("tier1_proven_combos") or []
        if t1:
            lines.append("**Tier 1 · 案例库已验证共现组合**  ")
            lines.append("")
            lines.append("| 维度组合 | 共现案例数 | 代表案例 |")
            lines.append("|---|---:|---|")
            for c in t1:
                dims = "·".join(c.get("dims") or [])
                lines.append(f"| {dims} | {c.get('count','-')} | {c.get('example','-')} |")
            lines.append("")
        t2 = ladder.get("tier2_paradigm") or []
        if t2:
            lines.append("**Tier 2 · 未来范式（推演）**  ")
            lines.append("")
            for p in t2:
                dims = "·".join(p.get("dims") or [])
                lines.append(
                    f"- 🚀 **{p.get('paradigm','-')}**（{dims}）：{p.get('rationale','')}"
                )
            lines.append("")
        if ladder.get("_note"):
            lines.append(f"_{ladder['_note']}_")
            lines.append("")
    if ng.get("disclaimer"):
        lines.append("")
        lines.append(f"_{ng['disclaimer']}_")
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


# end of module (persona lens + buyer preset added)
