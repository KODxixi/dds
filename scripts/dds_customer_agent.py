"""
DDS/DSS 客服对话 Agent — 项目介绍、数据说明、地块报告、客户线索

用法：
  python dds_customer_agent.py
  python dds_customer_agent.py --once "帮我分析三亚海棠区南田路16号，预期均价35000"
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gis_amap import AMAP_KEY
from query_local import CITY_FILES
from report_parcel import build_report, write_outputs
from dds_decision_engine import run_decision_engine, write_decision_outputs
from dds_online_research import load_evidence

SUPPORTED_CITIES = list(CITY_FILES.keys())
DEFAULT_REPORT_DIR = ROOT / "data_out" / "reports" / "parcel"
DEFAULT_LEADS_FILE = ROOT / "data_out" / "leads" / "dds_customer_leads.jsonl"
DEFAULT_EVIDENCE_DIR = ROOT / "data_out" / "online_evidence"


def find_latest_evidence() -> Path | None:
    """自动发现最新的线上证据文件"""
    if not DEFAULT_EVIDENCE_DIR.exists():
        return None
    files = sorted(DEFAULT_EVIDENCE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


class CustomerAgent:
    def __init__(self, project_doc: Path, llm_mode: str = "stub"):
        self.project_doc = project_doc
        self.llm_mode = llm_mode
        self.project_brief = load_project_brief(project_doc)
        self.session = {"report_slots": {}, "lead": {}, "last_report_result": None, "last_report_slots": None}

    def respond(self, text: str) -> str:
        text = text.strip()
        if not text:
            return "请直接输入你的问题，或输入 help 查看我可以做什么。"
        command = text.lower()
        if command in {"help", "帮助"}:
            return help_text()
        if command in {"about", "介绍"}:
            return answer_about()
        if command in {"scope", "数据", "范围"}:
            return answer_data_scope()
        if command == "report":
            self.session["mode"] = "report"
            return "请提供地块城市 + 地址或经纬度；如有预期均价也一起给我，例如：三亚海棠区南田路16号，预期均价35000。"
        if command == "lead":
            self.session["mode"] = "lead"
            return "请留下姓名、公司、角色、关注城市/地块、联系方式和需求备注。我会保存为客户线索。"

        intent = classify_intent(text, self.session.get("mode"))
        if intent == "development_advice":
            return self.handle_development_advice(text)
        if intent == "report":
            return self.handle_report(text)
        if intent == "lead":
            return self.handle_lead(text)
        if intent == "data_scope":
            return answer_data_scope()
        if intent == "about":
            return answer_about()
        return fallback_answer()

    def handle_report(self, text: str) -> str:
        slots = self.session.setdefault("report_slots", {})
        slots.update({k: v for k, v in extract_report_slots(text).items() if v is not None})
        missing = missing_report_slots(slots)
        if missing:
            return ask_for_missing_slots(missing, slots)
        try:
            result = run_parcel_report(slots)
        except Exception as e:
            return f"报告生成失败：{e}\n请确认城市、地址或经纬度是否准确。"
        self.session["last_report_result"] = result
        self.session["last_report_slots"] = dict(slots)
        self.session["report_slots"] = {}
        self.session["mode"] = None
        return format_report_result(result, extract_development_slots(text))

    def handle_development_advice(self, text: str) -> str:
        dev_slots = extract_development_slots(text)
        report_slots = dict(self.session.get("last_report_slots") or {})
        report_slots.update({k: v for k, v in extract_report_slots(text).items() if v is not None})
        if missing_report_slots(report_slots):
            return (
                "这个问题需要结合具体地块位置才能判断。请补充城市 + 地址或经纬度；"
                "例如：三亚海棠湾三灶村，容积率1.2，做商墅，对标保利棠隐。"
            )
        try:
            result = run_parcel_report(report_slots)
            evidence_path = self.session.get("online_evidence_path") or find_latest_evidence()
            online_evidence = load_evidence(evidence_path)
            decision = run_decision_engine(result["report"], dev_slots, online_evidence)
            decision_md, decision_json, decision_html = write_decision_outputs(decision)
        except Exception as e:
            return f"开发测算前置报告生成失败：{e}\n请把地块位置简化为城市 + 具体地址或经纬度。"
        self.session["last_report_result"] = result
        self.session["last_report_slots"] = dict(report_slots)
        return format_decision_result(result, decision, decision_md, decision_json, decision_html)

    def handle_lead(self, text: str) -> str:
        lead = self.session.setdefault("lead", {})
        lead.update(extract_lead(text))
        lead["raw_message"] = text
        lead["created_at"] = datetime.now().isoformat(timespec="seconds")
        save_lead(lead, DEFAULT_LEADS_FILE)
        self.session["lead"] = {}
        self.session["mode"] = None
        return (
            "已记录客户线索。\n"
            f"- 姓名/称呼：{lead.get('name') or '未提供'}\n"
            f"- 公司：{lead.get('company') or '未提供'}\n"
            f"- 关注城市：{lead.get('city') or '未提供'}\n"
            f"- 联系方式：{lead.get('contact') or '未提供'}\n"
            f"- 保存位置：{DEFAULT_LEADS_FILE}"
        )


def load_project_brief(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    return text[:8000]


def classify_intent(text: str, mode: str | None = None) -> str:
    if mode in {"report", "lead"}:
        return mode
    if any(k in text for k in ["联系", "合作", "留资", "电话", "微信", "公司", "我叫"]):
        return "lead"
    if any(k in text for k in ["拿地", "开发", "容积率", "商墅", "对标", "货值", "楼面价", "地价", "什么价格合适"]):
        return "development_advice"
    if any(k in text for k in ["分析", "地块", "地址", "坐标", "均价", "竞品", "配套", "报告", "楼盘", "价值", "位置"]):
        return "report"
    if any(k in text for k in ["数据", "来源", "范围", "可信", "城市", "样本", "更新", "扩展"]):
        return "data_scope"
    if any(k in text for k in ["是什么", "介绍", "价值", "客户", "DSS", "DDS", "dss", "dds", "能做什么"]):
        return "about"
    return "fallback"


def answer_about() -> str:
    return (
        "DDS/DSS 是一个地产数据决策服务，不是单纯的画图或报表工具。\n"
        "它的核心目标是把地块、竞品、区位、政策、成交和客户偏好放到同一个决策框架里，计算地块价值最大化的路径。\n\n"
        "面向客户的价值：\n"
        "1. 投拓阶段：快速判断地块周边竞品、价格带、区位配套和风险。\n"
        "2. 产品定位：用竞品价格、户型、面积段、销售状态反推产品组合。\n"
        "3. 决策可信：报告会标注数据来源和样本边界，避免黑箱结论。\n"
        "4. 持续扩展：后续可接入政府文件、网签成交、GIS、社媒情绪和企业内部数据。"
    )


def answer_data_scope() -> str:
    return (
        "当前首版数据能力分三层说明：\n"
        "- 一级数据目标：政府出让文件、房产局/网签成交、备案价格等，用作高可信锚点。\n"
        "- 二级数据已接入：GIS/高德 POI，用于学校、医院、交通、商业、公园等区位配套分析。\n"
        "- 三级数据目标：社媒舆情和客户偏好，用于情绪溢价/折价判断，需人工校准。\n\n"
        "当前本地 MVP 可直接分析三亚、杭州、上海、青岛的新盘样本，并可生成地块周边竞品、价格带和配套报告。\n"
        "数据会持续扩展；涉及“全国竞品”时，当前先以本地四城样本做 MVP，并在报告中明确标注边界。"
    )


def help_text() -> str:
    return (
        "我是 DDS/DSS 客服对话 Agent，可以帮你：\n"
        "- about：介绍 DDS/DSS 是什么、适合谁、解决什么问题。\n"
        "- scope：说明当前数据来源、覆盖城市和可信度边界。\n"
        "- report：根据地块地址或经纬度生成竞品 + 区位配套 + 价格带报告。\n"
        "- lead：记录合作线索。\n\n"
        "示例：帮我分析三亚海棠区南田路16号，预期均价35000。\n"
        "退出请输入 exit 或 quit。"
    )


def fallback_answer() -> str:
    return (
        "我可以围绕 DDS/DSS 项目介绍、数据范围、地块竞品分析、区位配套和合作线索来服务。\n"
        "你可以直接说：帮我分析某个地块；或输入 about / scope / report / lead。"
    )


def extract_report_slots(text: str) -> dict:
    slots = {}
    for city in SUPPORTED_CITIES:
        if city in text:
            slots["city"] = city
            break
    district_match = re.search(r"([一-龥A-Za-z0-9]{2,12}(?:区|县|市|湾|镇|街道))", text)
    if district_match:
        slots["district"] = district_match.group(1)

    coord_match = re.search(r"(?:lng|经度)?\s*([0-9]{2,3}\.\d+)\s*[,，\s]+(?:lat|纬度)?\s*([0-9]{1,2}\.\d+)", text, re.I)
    if coord_match:
        first = float(coord_match.group(1))
        second = float(coord_match.group(2))
        if first > second:
            slots["lng"] = first
            slots["lat"] = second
        else:
            slots["lat"] = first
            slots["lng"] = second

    price = extract_expected_price(text)
    if price is not None:
        slots["expected_price"] = price

    # 提取年份
    year_match = re.search(r"\b(199\d|20[0-2]\d)\s*(?:年|年度)?", text)
    if year_match:
        slots["year"] = year_match.group(1)

    if not slots.get("lng") or not slots.get("lat"):
        address = extract_address(text, slots.get("city"))
        if address:
            slots["address"] = address
    return slots


def extract_expected_price(text: str) -> float | None:
    patterns = [
        r"(?:预期|目标)\s*(?:均价|售价|价格)?\s*[:：为是]?\s*(\d+(?:\.\d+)?)\s*(万|元|块)?\s*(?:/㎡|每平|一平|平)?",
        r"(?:均价|售价)\s*[:：为是]?\s*(\d+(?:\.\d+)?)\s*(万|元|块)?\s*(?:/㎡|每平|一平|平)?",
        r"(\d+(?:\.\d+)?)\s*(万|元)\s*(?:/㎡|每平|一平|平)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = float(match.group(1))
        unit = match.group(2)
        if unit == "万" or value < 1000:
            value *= 10000
        return value
    return None


def extract_address(text: str, city: str | None) -> str | None:
    location_patterns = [
        r"((?:三亚|杭州|上海|青岛)[一-龥A-Za-z0-9·（）()]{0,20}(?:村|路|街|湾|镇|区|号))",
        r"((?:海棠湾|三亚湾|亚龙湾|浦沿|滨江区|海棠区)[一-龥A-Za-z0-9·（）()]{0,16}(?:村|路|街|湾|镇|区|号)?)",
    ]
    for pattern in location_patterns:
        matches = re.findall(pattern, text)
        if matches:
            candidate = matches[-1].strip(" ，,。的所在位置价值周边竞品")
            if city and city not in candidate:
                candidate = city + candidate
            return candidate

    cleaned = re.sub(r"预期均价\s*[0-9.]+\s*(?:万|元)?\s*(?:/㎡|每平|一平|平)?", "", text)
    cleaned = re.sub(r"我准备.*?开发|什么价格合适|暂定容积率\s*[0-9.]+|做成[一-龥A-Za-z0-9]+|对标[一-龥A-Za-z0-9·（）()]+", "", cleaned)
    cleaned = re.sub(r"帮我|请|分析|生成|报告|竞品|配套|地块|看一下|想看|所在位置|价值|周边", "", cleaned)
    cleaned = cleaned.strip(" ，,。？")
    if city and city in cleaned:
        return cleaned
    if any(k in cleaned for k in ["区", "路", "街", "湾", "镇", "号", "村"]):
        return f"{city}{cleaned}" if city and city not in cleaned else cleaned
    return None


def extract_development_slots(text: str) -> dict:
    slots = {}
    far_match = re.search(r"容积率\s*([0-9]+(?:\.[0-9]+)?)", text)
    if far_match:
        slots["floor_area_ratio"] = float(far_match.group(1))
    product_match = re.search(r"(?:做成?|产品(?:类型)?[为是]?)\s*([一-龥A-Za-z0-9]{2,12})", text)
    if product_match:
        slots["product_type"] = product_match.group(1)
    benchmark_match = re.search(r"对标\s*([一-龥A-Za-z0-9·（）()]+)", text)
    if benchmark_match:
        slots["benchmark"] = benchmark_match.group(1).strip(" ，,。？")
    if any(k in text for k in ["住宅", "开发", "可研", "货值", "未来推演"]):
        slots["product_type"] = slots.get("product_type") or "住宅开发"
    if any(k in text for k in ["拿地", "地价", "楼面价", "什么价格合适", "货值", "测算", "可研"]):
        slots["asks_land_price"] = True
    
    # 提取年份
    year_match = re.search(r"\b(199\d|20[0-2]\d)\s*(?:年|年度)?", text)
    if year_match:
        slots["year"] = year_match.group(1)
        
    return slots


def missing_report_slots(slots: dict) -> list[str]:
    missing = []
    if not slots.get("city"):
        missing.append("city")
    if not ((slots.get("lng") is not None and slots.get("lat") is not None) or slots.get("address")):
        missing.append("location")
    return missing


def ask_for_missing_slots(missing: list[str], slots: dict) -> str:
    prompts = []
    if "city" in missing:
        prompts.append(f"请补充城市，目前支持：{'、'.join(SUPPORTED_CITIES)}。")
    if "location" in missing:
        prompts.append("请补充地块地址，或提供经纬度，例如：109.71899, 18.41040。")
    known = []
    if slots.get("city"):
        known.append(f"城市={slots['city']}")
    if slots.get("expected_price"):
        known.append(f"预期均价={slots['expected_price']}元/㎡")
    suffix = f"\n已识别：{', '.join(known)}" if known else ""
    return "\n".join(prompts) + suffix


def run_parcel_report(slots: dict) -> dict:
    args = SimpleNamespace(
        city=slots.get("city"),
        district=slots.get("district"),
        address=slots.get("address"),
        lng=slots.get("lng"),
        lat=slots.get("lat"),
        expected_price=slots.get("expected_price"),
        radius_km=slots.get("radius_km", 5.0),
        price_band=slots.get("price_band", 0.15),
        year=slots.get("year"),
        out_dir=str(DEFAULT_REPORT_DIR),
        key=AMAP_KEY,
    )
    report = build_report(args)
    md_path, json_path = write_outputs(report, DEFAULT_REPORT_DIR)
    return {"report": report, "md_path": md_path, "json_path": json_path}


def format_report_result(result: dict, dev_slots: dict | None = None) -> str:
    report = result["report"]
    market = report["market_summary"]
    price = market["price"]
    amenities = summarize_amenities(report["amenities"])
    expected = report["input"].get("expected_price")
    price_judgment = "未输入预期均价，暂不做价格偏离判断。"
    if expected and price.get("avg"):
        diff = price["avg"] - expected
        ratio = diff / expected * 100
        price_judgment = f"周边样本均价较预期均价{'高' if diff > 0 else '低'}约 {abs(ratio):.1f}%。"
    dev_advice = format_development_advice(report, dev_slots or {})
    return (
        "地块画像报告已生成。\n"
        f"- 竞品样本：{market['sample_count']} 条，有效价格样本 {price['valid_count']} 条。\n"
        f"- 周边均价：{price.get('avg') or '—'} 元/㎡，区间：{price.get('min') or '—'} - {price.get('max') or '—'} 元/㎡。\n"
        f"- 价格判断：{price_judgment}\n"
        f"- 销售状态：{market.get('sales_status')}\n"
        f"- 主力户型：{market.get('room_types')}\n"
        f"- 区位配套：{amenities}\n"
        f"{dev_advice}"
        f"- Markdown：{result['md_path']}\n"
        f"- JSON：{result['json_path']}\n"
        "数据边界：当前基于本地四城样本 + 高德 POI，后续可扩展全国数据源。"
    )


def format_development_advice(report: dict, dev_slots: dict) -> str:
    if not dev_slots:
        return ""
    market = report["market_summary"]
    avg_price = market["price"].get("avg")
    far = dev_slots.get("floor_area_ratio")
    product = dev_slots.get("product_type")
    benchmark = dev_slots.get("benchmark")
    lines = ["- 开发初判："]
    if product:
        lines.append(f"  - 产品方向：{product}。周边户型结构可作为面积段和总价控制的第一轮参照。")
    if benchmark:
        bench = find_competitor(report["nearby_competitors"], benchmark)
        if bench:
            lines.append(
                f"  - 对标项目：{benchmark}，样本价格约 {bench.get('unit_price_cny') or '—'} 元/㎡，"
                f"距离约 {bench.get('distance_km') or '—'}km。"
            )
        else:
            lines.append(f"  - 对标项目：{benchmark} 未进入本次周边样本，可在下一轮扩大半径或指定对标库。")
    if far and avg_price:
        conservative = round(avg_price * 0.28 * far, 0)
        aggressive = round(avg_price * 0.38 * far, 0)
        lines.append(
            f"  - 拿地口径：按容积率 {far} 和周边均价 {avg_price} 元/㎡粗算，"
            f"可先把楼面地价敏感区间压在约 {conservative}-{aggressive} 元/㎡土地口径继续测算。"
        )
    lines.append("  - 这不是最终拿地价，下一步需要补充可售面积、建安成本、税费、融资成本、去化周期和目标利润率。")
    return "\n".join(lines) + "\n"


def find_competitor(competitors: list[dict], name: str) -> dict | None:
    for item in competitors:
        project = item.get("project_name") or ""
        if name in project or project in name:
            return item
    return None


def format_decision_result(report_result: dict, decision: dict, decision_md: Path, decision_json: Path, decision_html: Path = None) -> str:
    report = report_result["report"]
    market = report["market_summary"]
    price = market["price"]
    summary = decision["decision_summary"]
    compliance = decision["compliance_agent"]
    value = decision["value_agent"]
    abm = decision["abm_market_agent"]
    briefing = decision["briefing_engine"]
    blueprint = decision["blueprint_logic"]
    top_personas = "；".join(f"{p['name']} {p['score']}分" for p in abm.get("top_personas", []))
    
    html_line = f"- 决策报告 HTML：{decision_html}\n" if decision_html else ""
    
    return (
        "DDS Agent v2 决策推演已生成。\n"
        f"- 决策结论：{summary['headline']}\n"
        f"- 本地数据库：已调用 Vault，本地样本 {market['sample_count']} 条；报告时间 {report.get('meta', {}).get('generated_at')}。\n"
        f"- 线上补充：{decision.get('traceability', {}).get('online_sources', {}).get('status')}，来源数 {decision.get('traceability', {}).get('online_sources', {}).get('count', 0)}。\n"
        f"- 周边样本：{market['sample_count']} 条，均价 {price.get('avg') or '—'} 元/㎡，区间 {price.get('min') or '—'} - {price.get('max') or '—'} 元/㎡。\n"
        f"- 拿地敏感区间：{summary['pricing_posture']}\n"
        f"- 合规红线：{compliance.get('pricing_boundary')} 缺失项：{compliance.get('hard_constraints_missing')}\n"
        f"- 价值机会：{summary['top_opportunity']}\n"
        f"- 对标判断：溢价倍数 {value.get('benchmark_premium_ratio') or '未形成'}；对标样本 {value.get('benchmark') or '未命中'}\n"
        f"- ABM 客群：{top_personas or '暂无'}\n"
        f"- 任务书参数：{briefing.get('performance_brief', [])[:4]}\n"
        f"- 蓝图风险：{blueprint.get('risk_curve', [])}\n"
        f"- 决策报告 Markdown：{decision_md}\n"
        f"- 决策报告 JSON：{decision_json}\n"
        f"{html_line}"
        f"- 地块基础报告 Markdown：{report_result['md_path']}\n"
        f"- 地块基础报告 JSON：{report_result['json_path']}"
    )


def summarize_amenities(amenities: dict) -> str:
    parts = []
    for payload in amenities.values():
        label = payload.get("label")
        items = payload.get("items") or []
        if items:
            first = items[0]
            parts.append(f"{label}最近为{first.get('name')}约{first.get('distance')}m")
    return "；".join(parts[:5]) if parts else "暂无可用 POI 摘要"


def extract_lead(text: str) -> dict:
    lead = {}
    name_match = re.search(r"(?:我叫|我是|姓名[:：]?)([一-龥A-Za-z0-9]{1,12})", text)
    if name_match:
        lead["name"] = name_match.group(1)
    company_match = re.search(r"(?:公司是|来自|公司[:：]?)([一-龥A-Za-z0-9·（）()]{2,30})", text)
    if company_match:
        lead["company"] = company_match.group(1)
    contact_match = re.search(r"(1[3-9]\d{9}|[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9-.]+|微信[:：]?\s*[A-Za-z0-9_-]{4,30})", text)
    if contact_match:
        lead["contact"] = contact_match.group(1)
    for city in SUPPORTED_CITIES:
        if city in text:
            lead["city"] = city
            break
    lead["note"] = text
    return lead


def save_lead(lead: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(lead, ensure_ascii=False) + "\n")


def run_repl(agent: CustomerAgent) -> int:
    print("DDS/DSS 客服 Agent 已启动。输入 help 查看能力，输入 exit 退出。")
    while True:
        try:
            text = input("客户> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0
        if text.lower() in {"exit", "quit", "q", "退出"}:
            print("已退出。")
            return 0
        print("Agent> " + agent.respond(text))


def main() -> int:
    parser = argparse.ArgumentParser(description="DDS/DSS 客服对话 Agent")
    parser.add_argument("--project-doc", default=str(ROOT / "DDS_main.md"))
    parser.add_argument("--llm-mode", choices=["off", "stub"], default="stub")
    parser.add_argument("--once", help="单轮输入，用于自动化测试")
    args = parser.parse_args()

    agent = CustomerAgent(Path(args.project_doc), llm_mode=args.llm_mode)
    if args.once:
        print(agent.respond(args.once))
        return 0
    return run_repl(agent)


if __name__ == "__main__":
    raise SystemExit(main())
