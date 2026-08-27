"""DDS × ArchLib Co-Director Agent Orchestrator.

Automatically calls DDS and ArchLib sub-agents, builds the evidence-driven
Architecture Director packet, and generates a Bauhaus-style interactive HTML report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Import local modules
from app import app
from architecture_director import build_architecture_director_packet
from archlib_visual_roles import search_visual_assets


def run_co_director(
    city: str,
    address: str | None = None,
    lng: float | None = None,
    lat: float | None = None,
    expected_price: float | None = None,
    vision: str | None = None,
    persona: str = "developer",
    out_dir: str | Path | None = None,
) -> dict:
    print(f"[*] Starting Co-Director Agent for {city} - {address or (lng, lat)}...")
    
    # 1. Invoke DDS Report via Flask test client
    app.config["TESTING"] = True
    client = app.test_client()
    payload = {
        "city": city,
        "persona": persona,
    }
    if address:
        payload["address"] = address
    if lng is not None and lat is not None:
        payload["lng"] = lng
        payload["lat"] = lat
    if expected_price is not None:
        payload["expected_price"] = expected_price
    if vision:
        payload["vision"] = vision

    print(f"[*] Dispatching query to DDS API...")
    resp = client.post("/api/report", json=payload)
    if resp.status_code != 200:
        print(f"[!] DDS API failed with status {resp.status_code}: {resp.get_data(as_text=True)}")
        sys.exit(1)
        
    res_data = resp.get_json()
    if res_data.get("status") != "ok":
        print(f"[!] DDS report failed: {res_data.get('message')}")
        sys.exit(1)
        
    report_json = res_data["report_json"]
    print(f"[+] DDS Report retrieved successfully. Competitors found: {len(report_json['market'].get('competitors', []))}")

    # 2. Enrich with Architecture Director Packet
    print(f"[*] Building Architecture Director Packet...")
    director_packet = build_architecture_director_packet(report_json, user_text=vision or address)
    report_json["architecture_director"] = director_packet

    # 3. Dynamic Case Query from ArchLib Visual Index
    print(f"[*] Querying ArchLib Visual Index for cases matching vision...")
    strategy_tags = ["现代", "立面", "会所", "大堂", "玻璃"]
    if vision:
        if "低密" in vision or "别墅" in vision or "洋房" in vision:
            strategy_tags.extend(["低密住宅", "合院", "叠墅"])
        if "山" in vision or "坡" in vision or "台" in vision:
            strategy_tags.extend(["坡地", "台地花园", "山地住宅"])
        if "代" in vision or "露台" in vision:
            strategy_tags.extend(["第四代住宅", "大挑檐"])

    # Perform search
    search_res = search_visual_assets(
        intent=vision or address or "",
        strategy_tags=strategy_tags,
        must_have_roles=["facade_detail", "masterplan", "luxury_aesthetic", "sales_center", "evidence_image"],
        top_k=8
    )
    cases = search_res.get("items", [])
    print(f"[+] ArchLib query returned {len(cases)} matched cases.")

    # 4. Render Bauhaus Interactive HTML
    print(f"[*] Compiling final Bauhaus HTML report...")
    template_path = ROOT / "skills" / "arch-dds" / "assets" / "archdds-template.html"
    if not template_path.exists():
        print(f"[!] HTML Template not found at {template_path}")
        sys.exit(1)
        
    template = template_path.read_text(encoding="utf-8")
    
    # Placeholders formatting
    proj_name = address or f"{city}地块"
    if vision and len(vision) < 30:
        proj_name = f"{proj_name} ({vision})"
        
    hero_image = ""
    # Try to find a matched image for Hero
    if cases:
        hero_image = "file:///" + cases[0]["path"].replace("\\", "/")
        
    city_district = f"{city} · {report_json['parcel'].get('district') or ''}"
    one_line_thesis = director_packet["brief"]["assumptions"][0] if director_packet["brief"]["assumptions"] else report_json["decision"].get("summary", "")
    
    ceo = report_json["decision_full"].get("ceo_aggregator", {}) or {}
    total_score = ceo.get("total_score", 50)
    
    if total_score >= 75:
        verdict = "进行 (Proceed)"
        verdict_note = "各项评估良好，建议拿地并推进概念设计。"
    elif total_score >= 55:
        verdict = "有条件进行 (Conditional)"
        verdict_note = "存在潜在不确定性或学区/配套缺口，建议锁定硬性前提条件。"
    elif total_score >= 35:
        verdict = "建议中止 (Pause)"
        verdict_note = "财务敏感度高，或政策约束大，需前置人审决策。"
    else:
        verdict = "建议放弃 (Abandon)"
        verdict_note = "多项硬性停工点触发，利润空间极窄。"

    land_range = report_json["decision"]["land_price_range"]
    land_boundary = f"{land_range.get('balanced', 0):,.0f} 元/㎡"
    land_note = f"地价占比 {report_json['decision'].get('land_to_price_ratio', '?')}% | 保守: {land_range.get('conservative', 0):,.0f} | 激进: {land_range.get('aggressive', 0):,.0f}"
    
    prod_position = report_json["decision"].get("recommended_area_range", "100-140㎡")
    prod_note = f"定位客群: {', '.join([p['name'] for p in report_json['decision'].get('top_personas', [])[:2]])}"

    # Tables Generation
    # 1. Site Tables
    parcel = report_json["parcel"]
    site_table_rows = f"""
    <thead>
        <tr><th>地块指标</th><th>参数值</th><th>数据口径/来源</th></tr>
    </thead>
    <tbody>
        <tr><td>出让面积</td><td>{parcel.get('land_area_m2', '待接入')} ㎡</td><td>DDS 地块解析</td></tr>
        <tr><td>容积率</td><td>{parcel.get('floor_area_ratio', '待接入')}</td><td>DDS 地块解析</td></tr>
        <tr><td>目标均价</td><td>{expected_price or '自动匹配'} 元/㎡</td><td>用户设定</td></tr>
        <tr><td>中心经度</td><td>{parcel.get('lng', '待接入')}</td><td>AMap GIS 定位</td></tr>
        <tr><td>中心纬度</td><td>{parcel.get('lat', '待接入')}</td><td>AMap GIS 定位</td></tr>
    </tbody>
    """

    # 2. Competitors
    competitors = report_json["market"].get("competitors", [])
    comp_rows = "<thead><tr><th>竞品项目</th><th>售价 (元/㎡)</th><th>距离 (km)</th><th>物业开发商</th><th>数据来源</th></tr></thead><tbody>"
    for c in competitors[:10]:
        comp_rows += f"""
        <tr>
            <td><b>{c.get('project_name', '未知')}</b></td>
            <td>{c.get('unit_price_cny', '待接入')}</td>
            <td>{c.get('distance_km', 0):.2f}</td>
            <td>{c.get('developer') or '未知'}</td>
            <td>{c.get('source') or 'L2-DDS'}</td>
        </tr>
        """
    if not competitors:
        comp_rows += "<tr><td colspan='5'>暂无周边竞品数据</td></tr>"
    comp_rows += "</tbody>"

    # 3. Customer ABM
    personas = report_json["decision"].get("top_personas", [])
    cust_rows = "<thead><tr><th>客群角色</th><th>占比</th><th>综合评分</th><th>支付意愿 (WTP P50)</th><th>支付意愿 (WTP P90)</th></tr></thead><tbody>"
    for p in personas:
        cust_rows += f"""
        <tr>
            <td><b>{p.get('name', '未知')}</b></td>
            <td>{p.get('share', 0)*100:.1f}%</td>
            <td>{p.get('score', 0):.1f}/100</td>
            <td>{p.get('wtp_p50', 0):,.0f} 元/㎡</td>
            <td>{p.get('wtp_p90', 0):,.0f} 元/㎡</td>
        </tr>
        """
    if not personas:
        cust_rows += "<tr><td colspan='5'>暂无客群模拟数据</td></tr>"
    cust_rows += "</tbody>"

    # 4. Strategy Cards
    strategy_cards = ""
    default_strategies = [
        ("地块竖向与到达", "场地具有坡度或朝向资源", "顺应等高线布置台地与架空大堂，缩减挖方并最大化资源界面", "溢价提升与控成本"),
        ("立面公建化 recognizability", "中产客群对传统外立面审美疲劳", "公建化通透立面结合金属铝板和大挑檐，形成鲜明高端质感", "溢价提升 8%-10%"),
        ("都市度假生活馆/会所", "客研痛点首位为配套设施陈旧、绿化匮乏", "设置 2000㎡ 下沉式生活服务中心、恒温泳池与运动健身角", "提升客户去化速率"),
        ("第四代绿色科技住宅", "高溢价代差优势", "奇偶层错层露台全赠送、高得房率结合全屋空气与智能家居控制", "建立绝对产品代差"),
    ]
    for title, trigger, action, val_mech in default_strategies:
        strategy_cards += f"""
        <article class="card">
            <h3>{title}</h3>
            <p class="muted">触发证据: {trigger}</p>
            <p>{action}</p>
            <div class="tag" style="margin-top:10px">{val_mech}</div>
        </article>
        """

    # 5. DDS Investment
    dds_invest_rows = f"""
    <thead>
        <tr><th>财务与决策指标</th><th>数值 / 建议</th><th>意义 / 口径</th></tr>
    </thead>
    <tbody>
        <tr><td>综合评分 (CEO Score)</td><td>{total_score:.1f} / 100</td><td>DDS CEO 整合评估得分</td></tr>
        <tr><td>建议拿地价 (保守)</td><td>{land_range.get('conservative', 0):,.0f} 元/㎡</td><td>可实现预期利润的稳健举牌价</td></tr>
        <tr><td>建议拿地价 (均衡)</td><td>{land_range.get('balanced', 0):,.0f} 元/㎡</td><td>建议举牌上限</td></tr>
        <tr><td>建议拿地价 (激进)</td><td>{land_range.get('aggressive', 0):,.0f} 元/㎡</td><td>超出此界限极易侵蚀基本利润</td></tr>
        <tr><td>地价房价比</td><td>{report_json['decision'].get('land_to_price_ratio', '?')}%</td><td>地价占预期售价比重</td></tr>
    </tbody>
    """

    # 6. Case Cards (ArchLib Visuals)
    case_cards = ""
    for idx, c in enumerate(cases[:6]):
        local_path = c["path"].replace("\\", "/")
        web_path = "file:///" + local_path
        case_cards += f"""
        <article class="case-card">
            <img src="{web_path}" alt="{c.get('project', '案例')}">
            <h3>{c.get('project', '未知案例')}</h3>
            <p class="muted">{c.get('image_role')} | Fit: {c.get('Fit', '强匹配')} (Score: {c.get('asset_score')})</p>
            <p>{c.get('one_liner') or 'ArchLib 标杆案例参考'}</p>
        </article>
        """
    if not cases:
        case_cards += "<p class='muted'>未匹配到有效标杆案例，请在 build 索引后重试。</p>"

    # 7. Risk Table Rows
    gaps = director_packet["knowledge_gaps"]
    risk_rows = "<thead><tr><th>风险标识</th><th>严重等级</th><th>建议应对行动</th></tr></thead><tbody>"
    for g in gaps:
        risk_rows += f"""
        <tr>
            <td><b>{g['gap_id']}</b></td>
            <td><span style="color:red">{g['severity']}</span></td>
            <td>{g['recommended_action']}</td>
        </tr>
        """
    if not gaps:
        risk_rows += "<tr><td colspan='3'>符合数据合规与低风险运营指标</td></tr>"
    risk_rows += "</tbody>"

    # 8. Source Table Rows
    sources = director_packet["source_matrix"]
    source_rows = "<thead><tr><th>标识</th><th>源数据/引擎</th><th>信任级别</th><th>主用途</th></tr></thead><tbody>"
    for s in sources:
        source_rows += f"""
        <tr>
            <td>{s['source_id']}</td>
            <td><b>{s['name']}</b> ({s['path_or_url']})</td>
            <td>{s['tier']}</td>
            <td>{s['use']}</td>
        </tr>
        """
    source_rows += "</tbody>"

    # Replace values in template
    html = template
    html = html.replace("{{PROJECT_NAME}}", proj_name)
    html = html.replace("{{HERO_IMAGE}}", hero_image)
    html = html.replace("{{CITY_DISTRICT}}", city_district)
    html = html.replace("{{ONE_LINE_THESIS}}", one_line_thesis)
    html = html.replace("{{VERDICT}}", verdict)
    html = html.replace("{{VERDICT_NOTE}}", verdict_note)
    html = html.replace("{{LAND_BOUNDARY}}", land_boundary)
    html = html.replace("{{LAND_NOTE}}", land_note)
    html = html.replace("{{PRODUCT_POSITION}}", prod_position)
    html = html.replace("{{PRODUCT_NOTE}}", prod_note)
    html = html.replace("{{SITE_JUDGMENT}}", one_line_thesis)
    html = html.replace("{{SITE_TABLE_ROWS}}", site_table_rows)
    html = html.replace("{{COMPETITOR_TABLE_ROWS}}", comp_rows)
    html = html.replace("{{CUSTOMER_TABLE_ROWS}}", cust_rows)
    html = html.replace("{{STRATEGY_CARDS}}", strategy_cards)
    html = html.replace("{{DDS_INVESTMENT_ROWS}}", dds_invest_rows)
    html = html.replace("{{CASE_CARDS}}", case_cards)
    html = html.replace("{{RISK_TABLE_ROWS}}", risk_rows)
    html = html.replace("{{SOURCE_TABLE_ROWS}}", source_rows)

    # 5. Output file
    out_path = Path(out_dir) if out_dir else ROOT / "data_out" / "reports" / "co_director"
    out_path.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{city}_{address or 'parcel'}").strip("_")
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slug}_co_director.html"
    file_path = out_path / filename
    
    file_path.write_text(html, encoding="utf-8")
    print(f"[+] Co-Director report exported successfully to: {file_path}")
    
    return {
        "status": "ok",
        "html_path": str(file_path),
        "gap_count": len(gaps),
        "total_score": total_score,
        "matched_cases": len(cases),
    }


def main():
    parser = argparse.ArgumentParser(description="DDS × ArchLib Co-Director Orchestrator Agent")
    parser.add_argument("--city", required=True, help="City name (e.g., 济南, 三亚)")
    parser.add_argument("--address", help="Address of the parcel")
    parser.add_argument("--lng", type=float, help="Longitude of the parcel")
    parser.add_argument("--lat", type=float, help="Latitude of the parcel")
    parser.add_argument("--expected-price", type=float, help="Expected selling price in CNY/m²")
    parser.add_argument("--vision", help="NL project design vision or brief")
    parser.add_argument("--persona", default="developer", help="User role persona lens")
    parser.add_argument("--out-dir", help="Output directory for reports")

    args = parser.parse_args()
    
    result = run_co_director(
        city=args.city,
        address=args.address,
        lng=args.lng,
        lat=args.lat,
        expected_price=args.expected_price,
        vision=args.vision,
        persona=args.persona,
        out_dir=args.out_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
