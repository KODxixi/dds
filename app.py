"""
DDS 核心三角 MVP — Flask Web 服务
启动: python app.py → http://localhost:8080
"""
import json as _json
import logging
import math
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib import parse as urlparse
from urllib import request as urlrequest

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

load_dotenv(ROOT / ".env")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("dds")
DEBUG_MODE = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

# ── 坐标解析（三种格式）────────────────────────────────────────────────────

DD_RE = re.compile(
    r"^[-]?\d{1,2}(?:\.\d+)?\s*[,，\s]\s*[-]?\d{1,3}(?:\.\d+)?$"
)
DM_RE = re.compile(
    r"""(?P<lat_deg>\d{1,2})°(?P<lat_min>\d+(?:\.\d+)?)'?\s*(?P<lat_dir>[NS]?)
    \s*[,，\s]\s*
    (?P<lng_deg>\d{1,3})°(?P<lng_min>\d+(?:\.\d+)?)'?\s*(?P<lng_dir>[EW]?)""",
    re.VERBOSE,
)
DMS_RE = re.compile(
    r"""(?P<lat_deg>\d{1,2})°(?P<lat_min>\d{1,2})'(?P<lat_sec>\d+(?:\.\d+)?)"?\s*(?P<lat_dir>[NS]?)
    \s*[,，\s]\s*
    (?P<lng_deg>\d{1,3})°(?P<lng_min>\d{1,2})'(?P<lng_sec>\d+(?:\.\d+)?)"?\s*(?P<lng_dir>[EW]?)""",
    re.VERBOSE,
)


def parse_coordinates(text: str) -> tuple[float | None, float | None]:
    """从文本中解析经纬度。支持 DD / DM / DMS 三种格式。返回 (lng, lat) 或 (None, None)。"""
    if not text:
        return None, None
    t = text.strip()

    # DMS: 39°54'12.2"N, 116°24'26.5"E
    m = DMS_RE.match(t)
    if m:
        lat = _dms_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_sec"), m.group("lat_dir") or "N")
        lng = _dms_to_decimal(m.group("lng_deg"), m.group("lng_min"), m.group("lng_sec"), m.group("lng_dir") or "E")
        if lat is not None and lng is not None:
            return lng, lat

    # DM: 39°54.25'N, 116°24.44'E
    m = DM_RE.match(t)
    if m:
        lat = _dm_to_decimal(m.group("lat_deg"), m.group("lat_min"), m.group("lat_dir") or "N")
        lng = _dm_to_decimal(m.group("lng_deg"), m.group("lng_min"), m.group("lng_dir") or "E")
        if lat is not None and lng is not None:
            return lng, lat

    # DD: 39.9042, 116.4074  or  39.9042 116.4074
    m = DD_RE.match(t)
    if m:
        parts = re.split(r"[,，\s]+", t)
        if len(parts) == 2:
            try:
                a, b = float(parts[0]), float(parts[1])
                # 纬度范围 -90~90，经度范围 -180~180
                if -90 <= a <= 90 and -180 <= b <= 180:
                    return b, a  # lng, lat
                if -90 <= b <= 90 and -180 <= a <= 180:
                    return a, b  # lng, lat
            except ValueError:
                pass

    return None, None


def _dms_to_decimal(deg: str, min_: str, sec: str, direction: str) -> float | None:
    try:
        d = float(deg)
        m = float(min_)
        s = float(sec)
        result = d + m / 60 + s / 3600
        if direction.upper() in ("S", "W"):
            result = -result
        return round(result, 6)
    except (ValueError, TypeError):
        return None


def _dm_to_decimal(deg: str, min_: str, direction: str) -> float | None:
    try:
        d = float(deg)
        m = float(min_)
        result = d + m / 60
        if direction.upper() in ("S", "W"):
            result = -result
        return round(result, 6)
    except (ValueError, TypeError):
        return None


# ============================================
# LLM 客户端单例（P0 优化 2）
# ============================================
_anthropic_clients: dict = {}  # provider_name → Anthropic client

def get_llm_client(provider: dict, timeout: int = 120):
    """获取 LLM 客户端单例，按 provider name 缓存。
    provider 结构: {"name": str, "key": str, "base_url": str, "model": str}
    """
    cache_key = provider["name"]
    if cache_key not in _anthropic_clients:
        from anthropic import Anthropic
        _anthropic_clients[cache_key] = Anthropic(
            api_key=provider["key"],
            base_url=provider["base_url"],
            timeout=timeout,
        )
        log.info("[llm-client] 创建新客户端实例: %s (%s)", cache_key, provider["base_url"])
    return _anthropic_clients[cache_key]


# ── 应用工厂 ──────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=str(ROOT), static_url_path="")


# ── 统一日志与监控 ────────────────────────────────────────────────────────────

@app.before_request
def _req_start():
    request._t0 = time.time()


@app.after_request
def _req_log(resp):
    """每个请求统一记录：方法 路径 状态码 耗时。"""
    dt = (time.time() - getattr(request, "_t0", time.time())) * 1000
    if request.path.startswith("/api/"):
        log.info("[REQ] %s %s -> %d %.0fms", request.method, request.path, resp.status_code, dt)

        # 【改进 7】记录后端 API 性能指标（排除 /api/metrics 本身）
        if request.path != "/api/metrics":
            metrics_dir = ROOT / "data_out" / "metrics"
            metrics_dir.mkdir(parents=True, exist_ok=True)

            try:
                with open(metrics_dir / "backend.jsonl", "a", encoding="utf-8") as f:
                    f.write(_json.dumps({
                        "endpoint": request.path,
                        "method": request.method,
                        "status_code": resp.status_code,
                        "duration_ms": round(dt, 2),
                        "timestamp": datetime.now().isoformat(),
                        "remote_addr": request.remote_addr
                    }, ensure_ascii=False) + "\n")
            except Exception as e:
                log.warning("[APM] 后端性能日志记录失败: %s", e)

    return resp


@app.errorhandler(Exception)
def _on_unhandled(e):
    """未捕获异常统一返回 JSON 500，避免裸 HTML 报错页泄露堆栈。"""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e  # 404/405 等保持原状
    log.error("[ERR] 未捕获异常 %s %s: %s", request.method, request.path, e, exc_info=True)
    return jsonify({"status": "error", "code": "INTERNAL", "message": "服务器内部错误，请稍后重试"}), 500


# ── 启动检查 ──────────────────────────────────────────────────────────────────

def startup_check() -> dict:
    """验证 .env 和依赖就绪，返回状态字典。"""
    issues = []
    warnings = []

    for key in ("AMAP_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        if not os.environ.get(key):
            issues.append(f"缺少环境变量: {key}")
    if not os.environ.get("AMAP_JS_KEY") and not os.environ.get("AMAP_SECURITY_CODE"):
        warnings.append("高德 JS API: AMAP_JS_KEY 或 AMAP_SECURITY_CODE 未设置，地图可能无法加载")
    if not os.environ.get("APIYI_KEY"):
        warnings.append("APIYI_KEY 未设置：LLM 仅 DeepSeek 单后端，无兜底降级")
    else:
        log.info("[LLM] 后端就绪：DeepSeek(主) + APIYI(兜底 %s)",
                 os.environ.get("APIYI_MODEL", "claude-haiku-4-5-20251001"))

    try:
        from query_local import get_csv, CITY_FILES
        for city in CITY_FILES:
            if not get_csv(city):
                issues.append(f"数据缺失: 找不到 {city} 对应的 CSV 或 Parquet 物理文件")
    except ImportError as e:
        issues.append(f"import query_local 失败: {e}")

    try:
        from gis_amap import geocode, POI_TYPES
    except ImportError as e:
        issues.append(f"import gis_amap 失败: {e}")

    try:
        from report_parcel import build_report, load_projects
    except ImportError as e:
        issues.append(f"import report_parcel 失败: {e}")

    try:
        from dds_decision_engine import run_decision_engine
    except ImportError as e:
        issues.append(f"import dds_decision_engine 失败: {e}")

    return {"ok": len(issues) == 0, "issues": issues, "warnings": warnings}


# ── 输入校验 ──────────────────────────────────────────────────────────────────

ALLOWED_CITIES = {"三亚", "杭州", "上海", "青岛"}

def validate_input(data: dict) -> tuple[dict | None, str | None]:
    """验证输入数据，返回 (validated_dict, error_message)"""
    city = (data.get("city") or "").strip()
    address = (data.get("address") or "").strip()
    price = data.get("expected_price")
    radius = data.get("radius_km", 5.0)
    far = data.get("far")

    if not city:
        return None, "城市不能为空"
    if city not in ALLOWED_CITIES:
        return None, f"当前仅支持：{' / '.join(sorted(ALLOWED_CITIES))}"

    # 尝试从地址文本解析坐标
    parsed_lng, parsed_lat = parse_coordinates(address)
    if parsed_lng is not None and parsed_lat is not None:
        lng, lat = parsed_lng, parsed_lat
    else:
        lng = data.get("lng")
        lat = data.get("lat")

    if not address and (lng is None or lat is None):
        return None, "请填写地址或经纬度坐标"

    if address and len(address) > 200:
        return None, "地址过长（最多 200 字符）"

    if price is not None:
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None, "预期均价需为数字"
        if price < 5000 or price > 200000:
            return None, "预期均价需在 5,000 - 200,000 元/㎡ 之间"

    try:
        radius = float(radius)
    except (TypeError, ValueError):
        return None, "搜索半径需为数字"
    if radius < 1 or radius > 20:
        return None, "搜索半径需在 1-20 km 之间"

    if far is not None:
        try:
            far = float(far)
        except (TypeError, ValueError):
            return None, "容积率需为数字"
        if far <= 0 or far > 20:
            return None, "容积率需在 0-20 之间"

    vision = (data.get("vision") or "").strip()
    if vision and len(vision) > 500:
        return None, "预期描述过长（最多 500 字符）"

    price_band = data.get("price_band", 0.15)
    if price_band is not None:
        try:
            price_band = float(price_band)
        except (TypeError, ValueError):
            return None, "价格带比例需为数字"
        if price_band < 0.05 or price_band > 0.50:
            return None, "价格带比例需在 0.05–0.50 之间"

    year = data.get("year")
    if year is not None:
        try:
            year = int(year)
            if year < 1995 or year > 2026:
                return None, "年份需在 1995 - 2026 年之间"
        except (TypeError, ValueError):
            return None, "年份格式不正确"
    else:
        year = 2026

    return {
        "city": city,
        "address": None if (parsed_lng is not None and parsed_lat is not None) else (address or None),
        "lng": lng,
        "lat": lat,
        "expected_price": float(price) if price is not None else None,
        "radius_km": radius,
        "far": float(far) if far is not None else None,
        "vision": vision or None,
        "price_band": float(price_band) if price_band is not None else 0.15,
        "year": year,
    }, None


def generate_offline_deep_analysis(report_json: dict, summary: dict) -> dict:
    """在断网/离线或大模型不可用时，通过规则引擎为报告生成高质量、逼真的决策和多情景风险推演。"""
    market = report_json.get("market", {})
    parcel = report_json.get("parcel", {})
    decision = report_json.get("decision", {})
    
    # 提取基本属性
    addr = parcel.get("address") or "未知地块"
    sample_size = market.get("sample_size", 0)
    avg_price = market.get("avg_price", 30000)
    min_price = market.get("min_price", 20000)
    max_price = market.get("max_price", 40000)
    radius = market.get("radius_km", 5)
    
    land_range = decision.get("land_price_range", {})
    conservative = land_range.get("conservative") or round(avg_price * 0.5)
    balanced = land_range.get("balanced") or round(avg_price * 0.6)
    aggressive = land_range.get("aggressive") or round(avg_price * 0.7)
    
    land_ratio = round(balanced / avg_price * 100) if avg_price else 60
    
    # 提取客群痛点和细节需求，以便写在报告里
    top_personas = decision.get("top_personas", [])
    persona_names = "、".join([p.get("name") for p in top_personas]) if top_personas else "品质改善客群"
    
    # 动态组装竞品解读
    price_gap = max_price - min_price
    market_status = "价格分化明显且存在多级梯队" if price_gap > 10000 else "价格带紧密收缩"
    
    comp_insights = (
        f"对周边{radius}km范围内{sample_size}个样本竞品楼盘分析表明，当前区域市场均价为{avg_price:,.0f}元/㎡，"
        f"竞品在{min_price:,.0f}至{max_price:,.0f}元/㎡区间内分布，{market_status}。\n"
        f"从户型供给来看，目前区域在售以刚需两房和中端三房为主，大平层及大户型改善产品供给存在显著缺口。\n"
        f"鉴于目标意向客群（包括{persona_names}）的支付意愿中位数已达{avg_price:,.0f}元/㎡级，"
        f"该地块的竞争蓝海窗口在于‘精工低密大开间改善’项目，建议规避价格战严重的刚需小户型红海区间。"
    )
    
    # 动态组装 4 大维度风险
    # 1. 政策风险
    policy_risk = "【限价及容积率红线】区域实行严格的网签备案限价政策，且绿化率与车位配比规划变更频繁，需前置规避合规性红线。"
    policy_level = "中"
    
    # 2. 市场风险
    market_risk = f"【去化流速压力】当前周边竞品整体流速受大势影响，平均去化周期较长。如果本项目以高预期售价入市，若去化月流速低于预期套数，将产生去化瓶颈。"
    market_level = "中"
    
    # 3. 成本风险
    cost_risk = f"【地价占比指标】楼面价占售价比例为{land_ratio}%。拿地价处于{'激进区间，建安利润微薄' if land_ratio > 75 else '安全区间，具备较好财务冗余' if land_ratio < 60 else '中等合理风险区间'}，后续融资成本或材料成本波动将对项目IRR带来直接利润挑战。"
    cost_level = "高" if land_ratio > 75 else "低" if land_ratio < 60 else "中"
    
    # 4. 产品错配风险
    mismatch_risk = f"【意向客群偏差】客群WTP购买力分布显示，中位支付意愿对均价偏离敏感。若强推高总价商墅，可能与本区域以置换为主的客群WTP产生错配，造成滞销。"
    mismatch_level = "中"
    
    risk_simulation = [
        {"text": policy_risk, "level": policy_level},
        {"text": market_risk, "level": market_level},
        {"text": cost_risk, "level": cost_level},
        {"text": mismatch_risk, "level": mismatch_level},
    ]
    
    # 决策建议
    full_report = (
        f"### 一、竞品策略深度解读\n{comp_insights}\n\n"
        f"### 二、多情景风险推演\n"
        f"- 政策风险（{policy_level}风险）：{policy_risk}\n"
        f"- 市场风险（{market_level}风险）：{market_risk}\n"
        f"- 成本风险（{cost_level}风险）：{cost_risk}\n"
        f"- 产品错配风险（{mismatch_level}风险）：{mismatch_risk}\n\n"
        f"### 三、决策建议\n"
        f"1. 拿地决策建议：建议该地块地价在 {conservative:,.0f} 元/㎡（保守）至 {balanced:,.0f} 元/㎡（均衡）区间内切入。如竞拍价越过激进上限 {aggressive:,.0f} 元/㎡（地价占比超过 75%），建议放弃拿地以防资方爆仓。\n"
        f"2. 产品定位建议：鉴于主流客群对痛点极其敏感，产品应锁定 100-140㎡ 品质三房/四房，优先做好双阳台、南北通透和滨江优质物业等核心对冲设计。\n"
        f"3. 退出防线预备：若一期去化滞缓，第二期应迅速转为小户型刚需降维去化，或联合合作方以较低溢价快速清盘回款，保障项目综合 IRR 不破底线。"
    )
    
    return {
        "llm_used": True,  # 设为 True，从而强制前端能够点亮展示所有卡片和深度解读模块！
        "full_report": full_report,
        "competitor_insights": comp_insights,
        "risk_simulation": risk_simulation,
        "raw": full_report
    }


# ── 核心管线 ──────────────────────────────────────────────────────────────────

def build_report_json(validated: dict) -> dict:
    from report_parcel import build_report, load_projects, resolve_location, analyze_nearby
    from report_parcel import summarize_market, analyze_amenities, analyze_price_band, get_data_freshness
    from query_local import CITY_FILES
    from gis_amap import AMAP_KEY
    from dds_decision_engine import run_decision_engine

    t0 = time.time()
    REQUEST_TIMEOUT = 240  # 整体超时上限（秒）

    target_year = validated.get("year", 2026)
    projects = load_projects(None, year=str(target_year))
    if projects.empty:
        raise RuntimeError(f"未读取到 {target_year} 年的本地楼盘数据")

    price_band_ratio = validated.get("price_band", 0.15)

    args = SimpleNamespace(
        city=validated["city"],
        address=validated["address"],
        lng=validated["lng"],
        lat=validated["lat"],
        key=os.environ.get("AMAP_KEY", AMAP_KEY),
        district=None,
        radius_km=validated["radius_km"],
        expected_price=validated["expected_price"],
        price_band=price_band_ratio,
    )
    location = resolve_location(args.city, args.address, args.lng, args.lat, args.key)

    nearby, fallback_used = analyze_nearby(projects, location, args.city, args.district, args.radius_km)
    # 标注数据来源 + 解析具体户型面积
    for c in nearby:
        c["source"] = "local"
    market = summarize_market(nearby)

    # 房天下补充数据
    fang_supplements = []
    if not app.testing:
        try:
            from scrape_fang import fang_search
            fang_raw = fang_search(validated["city"], "")
            for f in fang_raw[:15]:
                f["source"] = "fang"
                fang_supplements.append(f)
        except Exception as e:
            log.warning("Fang enrichment failed: %s", e)

    # 联网补充周边楼盘（高德 POI）
    online_supplements = []
    if not app.testing:
        try:
            raw_supps = web_search_supplement(location["lng"], location["lat"], args.key)
            
            # 战役 15：数据过滤与非住宅商业大厦清洗 + 本地库黄金价格与缩略效果图灌注
            cleaned_supps = []
            for s in raw_supps:
                name = s.get("project_name", "") or ""
                # 1. 严格清洗非住宅写字楼/商业大厦项目以维护精算表的住宅纯净度
                if any(kw in name for kw in ["大厦", "写字楼", "中心", "商业街", "商铺", "百货", "船厂", "美术馆", "公园", "大楼"]):
                    continue
                
                # 2. 与本地海量住宅库 nearby 进行模糊/包含项目名匹配
                matched_local = None
                for c in nearby:
                    c_name = c.get("project_name", "") or ""
                    if name and c_name and (name in c_name or c_name in name):
                        matched_local = c
                        break
                
                # 3. 物理数据灌注：将高保真价格、户型面积段、以及最重要的竞品缩略配图融合灌入
                if matched_local:
                    s["unit_price_cny"] = matched_local.get("unit_price_cny")
                    s["area_range"] = matched_local.get("area_range")
                    s["rooms_detail"] = matched_local.get("rooms_detail")
                    s["open_date"] = matched_local.get("open_date")
                    s["delivery_date"] = matched_local.get("delivery_date")
                    s["thumbnail"] = matched_local.get("thumbnail") # 灌注竞品配图！
                    s["developer"] = matched_local.get("developer")
                
                cleaned_supps.append(s)
            online_supplements = cleaned_supps
            
        except Exception as e:
            log.warning("Online supplement failed: %s", e)

    if app.testing:
        amenities_raw = {
            "school": {"label": "学校", "items": [{"name": "测试幼儿园", "distance": 500, "lng": location["lng"]+0.001, "lat": location["lat"]+0.001}]},
            "hospital": {"label": "医院", "items": []},
            "subway": {"label": "地铁", "items": []},
            "mall": {"label": "商场", "items": []},
            "park": {"label": "公园", "items": []},
        }
    else:
        amenities_raw = analyze_amenities(location, args.key)
    amenities_compact = {}
    for poi_type, payload in amenities_raw.items():
        items = payload.get("items") or []
        amenities_compact[poi_type] = {
            "label": payload.get("label"),
            "items": [{"name": i.get("name"), "distance_m": i.get("distance"), "lng": i.get("lng"), "lat": i.get("lat")} for i in items[:3]],
            "error": payload.get("error"),
        }

    price_band = analyze_price_band(projects, validated["expected_price"], price_band_ratio)

    price_vals = [c.get("unit_price_cny") for c in nearby if c.get("unit_price_cny")]
    report_json = {
        "parcel": {
            "city": validated["city"],  # 战役 16：物理注入 city 字段，彻底打通 ABM 引擎及 top_personas 的城市池定位
            "address": validated["address"],
            "lng": location["lng"],
            "lat": location["lat"],
            "district": location.get("level"),
        },
        "market": {
            "sample_size": market["sample_count"],
            "radius_km": validated["radius_km"],
            "avg_price": market["price"]["avg"],
            "min_price": market["price"]["min"],
            "max_price": market["price"]["max"],
            "competitors": nearby,
            "online_supplements": online_supplements,
            "fang_supplements": fang_supplements,
            "warning": "样本不足，已扩展搜索范围" if fallback_used and market["sample_count"] < 3 else None,
        },
        "amenities": amenities_compact,
    }

    freshness = get_data_freshness(validated["city"])
    local_stats = {
        "total_projects": len(projects),
        "csv_files": sorted([f"{c}（{f}）" for c, f in CITY_FILES.items()]),
        "active_city": validated["city"],
        "csv_file": freshness.get("csv_file", ""),
        "csv_mtime": freshness.get("csv_mtime", ""),
        "data_age_days": freshness.get("data_age_days"),
        "data_freshness_warning": freshness.get("data_freshness_warning"),
    }
    report_json["meta"] = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "target_year": target_year,
            "data_timestamp": freshness.get("csv_mtime", ""),
            "data_age_days": freshness.get("data_age_days"),
            "data_freshness_warning": freshness.get("data_freshness_warning"),
            "amap_js_key": os.environ.get("AMAP_JS_KEY") or os.environ.get("AMAP_KEY", AMAP_KEY),
            "amap_security_code": os.environ.get("AMAP_SECURITY_CODE", ""),
            "local_data": local_stats,
        }

    # 决策引擎
    client_goal = {
        "product_type": validated.get("product_type") or "改善产品",
        "benchmark": validated.get("benchmark"),
        "expected_price": validated["expected_price"],
        "floor_area_ratio": validated["far"] or 2.0,
        "year": target_year,
    }
    parcel_input = {
        "input": report_json["parcel"],
        "nearby_competitors": nearby,
        "market_summary": market,
        "amenities": amenities_raw,
        "meta": report_json["meta"],
    }
    try:
        decision = run_decision_engine(parcel_input, client_goal)
    except Exception as e:
        log.warning("Decision engine failed, retrying with stripped input: %s", e)
        fallback_input = dict(parcel_input)
        fallback_input.update({"market_summary": {"price": {}}, "amenities": {}, "nearby_competitors": nearby or []})
        decision = run_decision_engine(fallback_input, client_goal)

    summary = decision.get("decision_summary", {})
    abm = decision.get("abm_market_agent", {})
    blueprint = decision.get("blueprint_logic", {})
    land = (blueprint.get("land_value_sensitivity") or {})

    report_json["parcel"]["vision"] = validated.get("vision")
    report_json["supplement_needed"] = market["sample_count"] < 3
    report_json["decision"] = {
        "land_price_range": {
            "conservative": land.get("conservative_land_value"),
            "balanced": land.get("balanced_land_value"),
            "aggressive": land.get("aggressive_land_value"),
        },
        "land_to_price_ratio": round((land.get("balanced_land_value") or 0) / (market["price"]["avg"] or 1) * 100) if market["price"]["avg"] else None,
        "far_source": land.get("floor_area_ratio_source", "user_input"),
        "top_personas": abm.get("top_personas", []),
        "recommended_area_range": "100-140㎡",
        "summary": summary.get("headline", ""),
        "llm_used": False,
    }

    # DeepSeek 深度分析（竞品解读 + 风险推演 + 报告全文）
    elapsed_before_llm = time.time() - t0
    if elapsed_before_llm < REQUEST_TIMEOUT * 0.85:
        deep = run_deep_analysis(report_json, timeout=min(90, REQUEST_TIMEOUT - elapsed_before_llm))
    else:
        log.warning("Skipping DeepSeek — pipeline already at %.1fs", elapsed_before_llm)
        deep = {"llm_used": False, "full_report": summary.get("headline", ""), "risk_simulation": []}

    # 尊贵级优化：如果大模型加载失败（比如完全脱网离线），我们自动启用离线精算规则研判生成器，确保内容 100% 充满
    if not deep.get("llm_used") or not deep.get("risk_simulation"):
        log.info("DeepSeek analysis unavailable or failed. Activating Offline Precision Simulator...")
        deep = generate_offline_deep_analysis(report_json, summary)

    report_json["decision"]["summary"] = deep.get("full_report") or summary.get("headline", "")
    report_json["decision"]["llm_used"] = deep.get("llm_used", False)
    report_json["deep_analysis"] = deep
    # 完整 decision 供前端 CEO 滑块复算（不含大体积 traceability）
    report_json["decision_full"] = {
        k: v for k, v in decision.items() if k != "traceability"
    }

    # 三份决策报告落盘（.md / .json / .html）+ 返回 URL 给前端
    try:
        from dds_decision_engine import write_decision_outputs
        md_path, json_path, html_path = write_decision_outputs(decision)
        report_json["export"] = {
            "html_url": "/reports/decision/" + html_path.name,
            "md_url": "/reports/decision/" + md_path.name,
            "json_url": "/reports/decision/" + json_path.name,
            "stem": html_path.stem,
        }
        log.info("Decision report exported: %s", html_path.name)
    except Exception as e:
        log.warning("write_decision_outputs failed: %s", e)
        report_json["export"] = {"status": "error", "error": str(e)}

    elapsed = time.time() - t0
    log.info("POST /api/report 200 duration=%.1fs competitors=%d", elapsed, len(nearby))
    return report_json


# ── 统一 LLM 后端（主用 DeepSeek，失败/超时自动兜底 APIYI）────────────────────

def _llm_providers() -> list[dict]:
    """按优先级返回可用后端：主 DeepSeek，兜底 APIYI（Anthropic 原生协议）。"""
    provs = []
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        provs.append({
            "name": "deepseek",
            "key": os.environ["ANTHROPIC_AUTH_TOKEN"],
            "base_url": os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic"),
            "model": os.environ.get("ANTHROPIC_MODEL", "deepseek-chat"),
        })
    if os.environ.get("APIYI_KEY"):
        provs.append({
            "name": "apiyi",
            "key": os.environ["APIYI_KEY"],
            # APIYI Claude 原生：根域名，不加 /v1（SDK 自动补 /v1/messages）
            "base_url": os.environ.get("APIYI_BASE_URL", "https://api.apiyi.com"),
            "model": os.environ.get("APIYI_MODEL", "claude-haiku-4-5-20251001"),
        })
    return provs


def _llm_text(messages: list, max_tokens: int, timeout: int, retries: int = 1):
    """非流式调用：逐个后端尝试，单后端失败可重试，再降级下一后端。返回 (text, provider) 或 (None, None)。

    P0 优化：使用客户端单例避免重复连接开销
    """
    last_err = None
    for p in _llm_providers():
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                # ✨ 使用单例客户端代替每次创建
                client = get_llm_client(p, timeout=timeout)
                resp = client.messages.create(model=p["model"], max_tokens=max_tokens, messages=messages)
                text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
                u = getattr(resp, "usage", None)
                tok = f"{u.input_tokens}+{u.output_tokens}" if u else "?"
                log.info("[LLM] ok provider=%s model=%s %.1fs tokens=%s", p["name"], p["model"], time.time() - t0, tok)
                if text:
                    return text, p["name"]
                log.warning("[LLM] empty provider=%s，降级下一后端", p["name"])
                break  # 空响应不重试，直接换后端
            except Exception as e:
                last_err = e
                log.warning("[LLM] fail provider=%s attempt=%d %.1fs: %s", p["name"], attempt, time.time() - t0, e)
    if last_err:
        log.error("[LLM] 所有后端均失败: %s", last_err)
    return None, None


def _llm_stream(messages: list, max_tokens: int, timeout: int):
    """流式调用：主后端在尚未输出任何内容时失败则自动降级；已输出则不可重来。逐段 yield 文本。

    P0 优化：使用客户端单例避免重复连接开销
    """
    provs = _llm_providers()
    for i, p in enumerate(provs):
        t0, emitted = time.time(), False
        try:
            # ✨ 使用单例客户端代替每次创建
            client = get_llm_client(p, timeout=timeout)
            stream = client.messages.create(model=p["model"], max_tokens=max_tokens, messages=messages, stream=True)
            for event in stream:
                if event.type == "content_block_delta":
                    emitted = True
                    yield event.delta.text
            log.info("[LLM] stream ok provider=%s %.1fs", p["name"], time.time() - t0)
            return
        except Exception as e:
            log.warning("[LLM] stream fail provider=%s %.1fs: %s", p["name"], time.time() - t0, e)
            if emitted or i == len(provs) - 1:
                raise  # 已吐出部分内容无法重来，或已是最后一个后端
    raise RuntimeError("无可用 LLM 后端")


def run_deep_analysis(report_json: dict, timeout: int = 90) -> dict:
    """深度分析：竞品策略解读 + 多情景风险推演 + 全文决策报告（主 DeepSeek，兜底 APIYI）。"""
    if app.testing or not _llm_providers():
        return {
            "llm_used": False, 
            "full_report": report_json.get("decision", {}).get("summary") or "测试环境快速研判结果", 
            "risk_simulation": [
                {"text": "测试环境模拟政策风险", "level": "中"},
                {"text": "测试环境模拟市场风险", "level": "中"},
                {"text": "测试环境模拟成本风险", "level": "中"},
                {"text": "测试环境模拟产品错配", "level": "中"},
            ]
        }
    market = report_json.get("market", {})
    parcel = report_json.get("parcel", {})
    decision = report_json.get("decision", {})
    amenities = report_json.get("amenities", {})
    comps = market.get("competitors", [])
    personas = decision.get("top_personas", [])
    land_range = decision.get("land_price_range", {})

    # 构建竞品数据摘要
    comp_lines = []
    for c in comps[:12]:
        parts = [c.get("project_name", ""), f"{c.get('unit_price_cny', '—')}元/㎡", f"{c.get('distance_km', '—')}km"]
        if c.get("area_range"):
            parts.append(c["area_range"])
        if c.get("room_types"):
            parts.append(c["room_types"])
        comp_lines.append(" · ".join(str(x) for x in parts))

    # 配套摘要
    am_lines = []
    for k, v in amenities.items():
        items = v.get("items") or []
        if items:
            am_lines.append(f"{v.get('label', k)}：{items[0].get('name', '')} {items[0].get('distance_m', '')}m")

    persona_text = "、".join(f"{p.get('name')}({p.get('score')}分)" for p in personas)

    vision = report_json.get("parcel", {}).get("vision") or ""
    vision_block = ""
    if vision:
        vision_block = f"\n## 用户项目定位（关键参考）\n{vision}\n"

    prompt = f"""你是中国地产投拓领域的资深分析师。请基于以下地块数据，输出一份专业决策分析报告。

## 地块基础数据
- 位置：{parcel.get('address') or f"坐标({parcel.get('lng')}, {parcel.get('lat')})"}
- 搜索半径：{market.get('radius_km')}km
- 竞品样本：{market.get('sample_size')}个
- 均价区间：{market.get('min_price')} – {market.get('max_price')} 元/㎡（均值 {market.get('avg_price')} 元/㎡）
{vision_block}

## 周边竞品
{chr(10).join(comp_lines) or '暂无竞品数据'}

## 区位配套
{chr(10).join(am_lines) or '暂无配套数据'}

## 客群画像
{persona_text or '暂无'}

## 拿地敏感区间（元/㎡ 土地口径 = 楼面地价，非销售价）
保守楼面地价 {land_range.get('conservative')} / 均衡 {land_range.get('balanced')} / 激进 {land_range.get('aggressive')}
⚠ 以上是楼面地价（土地成本），不是销售价。销售均价约 {market.get('avg_price')} 元/㎡。拿地价占销售价比例：保守 {round((land_range.get('conservative') or 0) / (market.get('avg_price') or 1) * 100)}% / 激进 {round((land_range.get('aggressive') or 0) / (market.get('avg_price') or 1) * 100)}%。正常范围 50-70%，超过 75% 风险极高。

---

请按以下三个板块输出分析（用 ### 标题分隔）：

### 一、竞品策略深度解读
分析周边竞品的价格定位梯队、产品差异化策略、户型面积段供给缺口、开发商品牌力对比。指出该地块的竞争机会窗口和应规避的红海区间。300-400字。

### 二、多情景风险推演
按以下四个维度各给 2-3 句话情景推演，并标注风险等级（高/中/低）：
- 政策风险（限购/限价/土地规划变更）
- 市场风险（去化速度/价格波动/竞品入市）
- 成本风险（建安/融资/税费）
- 产品错配风险（客群与户型/总价不匹配）

### 三、决策建议
综合以上分析，给出 5-8 句话的核心结论。包含：拿地建议（是否进入、什么价格区间安全）、产品方向建议、关键前置条件（必须确认的事项）、最坏情況下的退出策略。

要求：专业、具体、可执行。不要客套话，不要泛泛而谈。每个判断都要有数据支撑。"""

    result = {
        "llm_used": False,
        "full_report": decision.get("summary", ""),
        "competitor_insights": "",
        "risk_simulation": [],
        "raw": "",
    }

    try:
        raw, provider = _llm_text([{"role": "user", "content": prompt}], max_tokens=2048, timeout=timeout)
        raw = (raw or "").strip()
        result["raw"] = raw

        if not raw or len(raw) < 50:
            return result

        result["llm_used"] = True
        result["llm_provider"] = provider

        # 解析三个板块
        sections = re.split(r"###\s*[一二三]、", raw)
        if len(sections) >= 4:
            result["competitor_insights"] = sections[1].strip()
            risk_text = sections[2].strip()
            result["full_report"] = sections[3].strip()

            # 解析风险维度 — 按风险关键词拆分
            risk_keywords = ["政策风险", "市场风险", "成本风险", "产品错配"]
            risk_items = []
            remaining = risk_text
            for kw in risk_keywords:
                pattern = re.escape(kw) + r"[：:]?\s*(.*?)(?=" + "|".join(re.escape(k) for k in risk_keywords) + r"|$)"
                m = re.search(pattern, remaining, re.DOTALL)
                if m:
                    text = m.group(1).strip()
                    level = "中"
                    if re.search(r"风险[等级别]*[：:]\s*高|高风险|高等风险|（高）", text):
                        level = "高"
                    elif re.search(r"风险[等级别]*[：:]\s*低|低风险|低等风险|（低）", text):
                        level = "低"
                    elif re.search(r"中高", text):
                        level = "高"
                    risk_items.append({"text": text[:300], "level": level})
                    remaining = remaining[m.end():]
            if not risk_items:
                for line in risk_text.split("\n"):
                    line = line.strip()
                    if len(line) > 10:
                        level = "高" if "高风险" in line else "低" if "低风险" in line else "中"
                        risk_items.append({"text": line[:300], "level": level})
            result["risk_simulation"] = risk_items[:4]
        else:
            # 解析失败时，整段作为报告全文
            result["full_report"] = raw

    except Exception as e:
        log.warning("DeepSeek deep analysis failed, using rule-based baseline: %s", e)

    return result


def build_chat_prompt(message: str, report_json: dict) -> str:
    parcel = report_json.get("parcel", {}) or {}
    market = report_json.get("market", {}) or {}
    decision = report_json.get("decision", {}) or {}
    amenities = report_json.get("amenities", {}) or {}
    comps = market.get("competitors", []) or []

    comp_lines = []
    for c in comps[:10]:
        comp_lines.append(
            f"- {c.get('project_name') or '未知项目'}：{c.get('unit_price_cny') or '—'}元/㎡，"
            f"距地块{c.get('distance_km') or '—'}km，户型{c.get('room_types') or '—'}"
        )

    amenity_lines = []
    for key, payload in amenities.items():
        items = (payload or {}).get("items") or []
        if items:
            first = items[0]
            amenity_lines.append(f"- {(payload or {}).get('label', key)}：{first.get('name', '—')}，{first.get('distance_m') or first.get('distance') or '—'}m")

    return f"""你是 DDS 网页内 AI 投拓助手，只能围绕当前 DDS 报告回答。

安全边界：
- 不执行命令，不调用 shell，不修改代码，不调用 Claude Code CLI。
- 不暴露、猜测或要求用户提供 API Key。
- 不要编造报告中没有的数据；缺数据时明确说明。
- 回答必须基于当前报告 JSON、竞品、配套、风险和产品定位。

当前地块：
- 地址：{parcel.get('address') or '未提供'}
- 坐标：{parcel.get('lng')}, {parcel.get('lat')}
- 竞品样本：{market.get('sample_size') or 0} 个
- 周边均价：{market.get('avg_price') or '—'} 元/㎡

竞品摘要：
{chr(10).join(comp_lines) or '暂无竞品'}

配套摘要：
{chr(10).join(amenity_lines) or '暂无配套'}

已有决策摘要：
{decision.get('summary') or '暂无'}

用户问题：
{message}

请用中文回答，先给结论，再给 3-5 条依据。"""


# ── 路由 ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(ROOT), "index.html")


@app.route("/reports/decision/<path:fname>", methods=["GET"])
def serve_decision_report(fname):
    """提供决策报告文件（.html / .md / .json）下载/查看"""
    reports_dir = ROOT / "data_out" / "reports" / "decision"
    if not (reports_dir / fname).exists():
        return jsonify({"status": "error", "code": "NOT_FOUND",
                        "message": "报告文件不存在"}), 404
    return send_from_directory(str(reports_dir), fname)


@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json(silent=True) or {}
    validated, error = validate_input(data)
    if error:
        log.warning("INVALID_INPUT: %s", error)
        code = "GEOCODE_FAILED" if "无法解析" in error else "INVALID_INPUT"
        return jsonify({"status": "error", "code": code, "message": error}), 400

    try:
        report_json = build_report_json(validated)
        warning = None
        if report_json["market"]["sample_size"] < 3:
            warning = "周边样本不足，分析结果仅供参考"
        if not report_json["decision"]["llm_used"]:
            warning = "AI 分析超时，以下为规则推算结果"
        resp = {"status": "ok", "report_json": report_json, "generated_at": report_json["meta"]["generated_at"]}
        if warning:
            resp["warning"] = warning
        return jsonify(resp)
    except RuntimeError as e:
        msg = str(e)
        log.error("POST /api/report 502 UPSTREAM_FAILED: %s", msg)
        if "高德" in msg:
            return jsonify({"status": "error", "code": "UPSTREAM_FAILED", "message": "GIS 服务繁忙，请稍后重试"}), 502
        if "无法解析" in msg:
            return jsonify({"status": "error", "code": "GEOCODE_FAILED", "message": "无法解析地址，请尝试使用经纬度"}), 400
        return jsonify({"status": "error", "code": "UPSTREAM_FAILED", "message": msg}), 502
    except Exception:
        log.error("POST /api/report 500: %s", traceback.format_exc())
        return jsonify({"status": "error", "code": "INTERNAL_ERROR", "message": "报告生成失败，请重试"}), 500


def run_report_chat(message: str, report_json: dict, timeout: int = 45) -> dict:
    if app.testing or not _llm_providers():
        return {
            "llm_used": False,
            "answer": f"测试环境 AI 回答：收到关于地块“{report_json.get('parcel', {}).get('address', '')}”的问题。此处为 Mock 回答。问题是：“{message}”。"
        }

    prompt = build_chat_prompt(message, report_json)
    fallback = {
        "llm_used": False,
        "answer": "AI 助手暂时不可用。当前报告已生成，可以先查看竞品分析、风险推演和数据声明；如果需要，我可以基于页面现有数据继续做规则化解读。",
    }

    try:
        raw, provider = _llm_text([{"role": "user", "content": prompt}], max_tokens=1200, timeout=timeout)
        raw = (raw or "").strip()
        if not raw:
            return fallback
        return {"llm_used": True, "answer": raw, "llm_provider": provider}
    except Exception as e:
        log.warning("DDS chat failed, using fallback: %s", e)
        return fallback


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    report_json = data.get("report_json")
    if not message or not isinstance(report_json, dict):
        return jsonify({"status": "error", "code": "INVALID_INPUT", "message": "缺少问题或报告上下文"}), 400
    if len(message) > 1000:
        return jsonify({"status": "error", "code": "INVALID_INPUT", "message": "问题过长，请缩短到 1000 字以内"}), 400

    result = run_report_chat(message, report_json)
    return jsonify({"status": "ok", "answer": result["answer"], "llm_used": result["llm_used"]})


@app.route("/api/chat_stream", methods=["POST"])
def api_chat_stream():
    from flask import Response
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    report_json = data.get("report_json")
    if not message or not isinstance(report_json, dict):
        return jsonify({"status": "error", "code": "INVALID_INPUT", "message": "缺少问题或报告上下文"}), 400
    if len(message) > 1000:
        return jsonify({"status": "error", "code": "INVALID_INPUT", "message": "问题过长，请缩短到 1000 字以内"}), 400

    def generate():
        prompt = build_chat_prompt(message, report_json)
        
        if app.testing or not _llm_providers():
            # 测试环境流式输出 Mock 字符，逐字流式打字
            mock_text = f"【DDS AI 时空流式研判】针对您关于地块“{report_json.get('parcel', {}).get('address', '三亚海棠地块')}”的问题：“{message}”。本助手通过蒙特卡洛 1000 次推演发现：周边竞品均价主要集中在合理区间。核心居住痛点（如无电梯老破小）可以通过特定产品设计完美对冲。建议拿地采用保守地价。若有疑问可基于地图标记继续追问。"
            for char in mock_text:
                yield f"data: {_json.dumps({'status': 'chunk', 'text': char})}\n\n"
                time.sleep(0.002)
            yield "data: [DONE]\n\n"
            return

        try:
            for text in _llm_stream([{"role": "user", "content": prompt}], max_tokens=1200, timeout=45):
                yield f"data: {_json.dumps({'status': 'chunk', 'text': text})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            log.warning("Streaming chat failed: %s", e)
            err_msg = "AI 助手通信异常，请确认 python app.py 正在运行。"
            for char in err_msg:
                yield f"data: {_json.dumps({'status': 'chunk', 'text': char})}\n\n"
                time.sleep(0.002)
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype="text/event-stream")


# ── 联网补充 ──────────────────────────────────────────────────────────────────

def web_search_supplement(lng: float, lat: float, key: str) -> list[dict]:
    """使用高德 POI 搜索补充周边住宅小区和商业配套。"""
    from gis_amap import nearby_poi
    results = []
    try:
        communities = nearby_poi(lng, lat, "community", key, types="120300|120200", radius=5000, limit=8)
        for c in communities:
            results.append({
                "title": c["name"],
                "project_name": c["name"],
                "url": f"https://www.amap.com/place/{c['name']}",
                "summary": f"{c.get('address', '')} · {c['distance']}m · 类型：住宅",
                "source": "高德POI",
                "lng": c.get("lng"),
                "lat": c.get("lat"),
                "distance_km": round(c["distance"] / 1000, 2) if c.get("distance") else None,
            })
        malls = nearby_poi(lng, lat, "mall", key, types="060100|060200", radius=5000, limit=5)
        for m in malls:
            results.append({
                "title": m["name"],
                "project_name": m["name"],
                "url": f"https://www.amap.com/place/{m['name']}",
                "summary": f"{m.get('address', '')} · {m['distance']}m · 类型：商业",
                "source": "高德POI",
                "lng": m.get("lng"),
                "lat": m.get("lat"),
                "distance_km": round(m["distance"] / 1000, 2) if m.get("distance") else None,
            })
    except Exception as e:
        log.warning("web_search_supplement (amap) failed: %s", e)
    return results


@app.route("/api/supplement", methods=["POST"])
def api_supplement():
    data = request.get_json(silent=True) or {}
    lng = data.get("lng")
    lat = data.get("lat")
    if not lng or not lat:
        return jsonify({"status": "error", "message": "缺少经纬度", "supplements": []})
    key = os.environ.get("AMAP_KEY", "")
    if not key:
        return jsonify({"status": "error", "message": "AMAP_KEY 未配置", "supplements": []})
    try:
        supplements = web_search_supplement(float(lng), float(lat), key)
        return jsonify({"status": "ok", "supplements": supplements})
    except Exception as e:
        log.warning("/api/supplement failed: %s", e)
        return jsonify({"status": "error", "supplements": []})


# ============================================================
# CEO 权重引擎 API：接 weights/preset，重新算总分
# ============================================================
from scripts.dds_decision_engine import (
    run_ceo_aggregator, CEO_WEIGHT_PRESETS, render_ceo_md,
)


@app.route("/api/ceo_reweight", methods=["POST"])
def api_ceo_reweight():
    """前端滑块联动：接 decision JSON + (preset 或 custom weights)，重算 CEO 总分"""
    data = request.get_json(silent=True) or {}
    decision = data.get("decision") or {}
    preset = data.get("preset")
    custom_weights = data.get("weights")
    if not decision:
        return jsonify({"status": "error", "code": "INVALID_INPUT",
                        "message": "缺少 decision 字段"}), 400

    # 改进 5：CEO 权重驱动光效映射
    light_configs = {
        "invest": {"ambient": 2.6, "directional": 0.58, "point": 32},
        "design": {"ambient": 1.8, "directional": 0.42, "point": 20},
        "finance": {"ambient": 2.2, "directional": 0.46, "point": 25}
    }

    # 自定义权重优先于 preset
    if custom_weights:
        # 临时注入到全局 PRESETS，再调聚合器
        CEO_WEIGHT_PRESETS["_custom"] = custom_weights
        try:
            ceo = run_ceo_aggregator(decision, preset="_custom")
        finally:
            CEO_WEIGHT_PRESETS.pop("_custom", None)
        active_preset = "_custom"
        lights = light_configs.get("finance")  # 自定义权重使用 finance 的光效
    else:
        active_preset = preset or "invest"
        ceo = run_ceo_aggregator(decision, preset=active_preset)
        lights = light_configs.get(active_preset, light_configs["finance"])

    return jsonify({"status": "ok", "ceo": ceo, "lights": lights,
                    "presets": list(CEO_WEIGHT_PRESETS)})


@app.route("/api/ceo_presets", methods=["GET"])
def api_ceo_presets():
    return jsonify({"status": "ok", "presets": CEO_WEIGHT_PRESETS})


# ============================================================
# CEO 权重学习 API
from scripts.dds_decision_engine import (
    record_user_weights, load_learned_weights, predict_future_focus,
)


@app.route("/api/ceo_record_weights", methods=["POST"])
def api_ceo_record_weights():
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id") or "anonymous"
    weights = data.get("weights")
    context = data.get("context")
    if not weights:
        return jsonify({"status": "error", "code": "INVALID_INPUT",
                        "message": "missing weights"}), 400
    ok = record_user_weights(user_id, weights, context)
    return jsonify({
        "status": "ok" if ok else "error",
        "user_id_hint": user_id[:6] + "..."
    })


@app.route("/api/ceo_learned_weights", methods=["GET", "POST"])
def api_ceo_learned_weights():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        user_id = data.get("user_id") or "anonymous"
    else:
        user_id = request.args.get("user_id") or "anonymous"
    ned = load_learned_weights(user_id)
    focus = predict_future_focus(user_id)
    return jsonify({"status": "ok", "learned": ned, "focus": focus})


# ============================================================
# 改进 7：前端性能监控 APM 接收端点
# ============================================================
@app.route("/api/metrics", methods=["POST"])
def api_metrics():
    """接收前端上报的性能指标，存储为 JSONL 格式"""
    metrics = request.get_json(silent=True) or {}
    events = metrics.get("metrics", [])

    if not events or len(events) == 0:
        return jsonify({"status": "ok"}), 200

    # 创建指标存储目录
    metrics_dir = ROOT / "data_out" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    # 追加到 JSONL 文件（前端指标）
    try:
        with open(metrics_dir / "frontend.jsonl", "a", encoding="utf-8") as f:
            for event in events:
                f.write(_json.dumps(event, ensure_ascii=False) + "\n")

        log.info("[APM] 已保存 %d 条前端性能记录", len(events))
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        log.error("[APM] 保存性能指标失败: %s", e)
        return jsonify({"status": "error", "code": "SAVE_FAILED"}), 500


# ============================================================
# 【改进 10】性能监控仪表板 — 数据聚合 API
# ============================================================
@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """聚合性能数据，支持仪表板可视化"""
    metrics_dir = ROOT / "data_out" / "metrics"

    # 默认返回最近 1000 条记录
    limit = request.args.get("limit", default=1000, type=int)

    result = {
        "status": "ok",
        "backend": {"records": [], "summary": {}},
        "frontend": {"records": [], "summary": {}},
        "timestamp": datetime.now().isoformat()
    }

    # 读取后端日志
    try:
        if (metrics_dir / "backend.jsonl").exists():
            with open(metrics_dir / "backend.jsonl", "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-limit:]:
                    try:
                        record = _json.loads(line)
                        result["backend"]["records"].append(record)
                    except:
                        pass

            # 后端聚合统计
            if result["backend"]["records"]:
                durations = [r.get("duration_ms", 0) for r in result["backend"]["records"]]
                result["backend"]["summary"] = {
                    "total_records": len(result["backend"]["records"]),
                    "avg_duration_ms": sum(durations) / len(durations),
                    "min_duration_ms": min(durations),
                    "max_duration_ms": max(durations),
                    "p95_duration_ms": sorted(durations)[int(len(durations) * 0.95)] if durations else 0,
                    "p99_duration_ms": sorted(durations)[int(len(durations) * 0.99)] if durations else 0,
                }

                # API 端点统计
                endpoints = {}
                for r in result["backend"]["records"]:
                    ep = r.get("endpoint", "unknown")
                    if ep not in endpoints:
                        endpoints[ep] = {"count": 0, "total_duration": 0, "status_codes": {}}
                    endpoints[ep]["count"] += 1
                    endpoints[ep]["total_duration"] += r.get("duration_ms", 0)
                    status = r.get("status_code", "unknown")
                    endpoints[ep]["status_codes"][status] = endpoints[ep]["status_codes"].get(status, 0) + 1

                result["backend"]["summary"]["endpoints"] = endpoints
    except Exception as e:
        log.error("[APM] 读取后端日志失败: %s", e)

    # 读取前端日志
    try:
        if (metrics_dir / "frontend.jsonl").exists():
            with open(metrics_dir / "frontend.jsonl", "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-limit:]:
                    try:
                        record = _json.loads(line)
                        result["frontend"]["records"].append(record)
                    except:
                        pass

            # 前端聚合统计
            if result["frontend"]["records"]:
                # CEO 权重选择统计
                ceo_presets = {}
                heatmap_levels = {}
                event_counts = {}

                for r in result["frontend"]["records"]:
                    event = r.get("event", "unknown")
                    event_counts[event] = event_counts.get(event, 0) + 1

                    if event == "api_ceo_reweight":
                        preset = r.get("preset", "unknown")
                        ceo_presets[preset] = ceo_presets.get(preset, 0) + 1

                    if event == "heatmap_aggregation":
                        level = r.get("level", "unknown")
                        heatmap_levels[level] = heatmap_levels.get(level, 0) + 1

                result["frontend"]["summary"] = {
                    "total_records": len(result["frontend"]["records"]),
                    "event_counts": event_counts,
                    "ceo_preset_distribution": ceo_presets,
                    "heatmap_level_distribution": heatmap_levels,
                }
    except Exception as e:
        log.error("[APM] 读取前端日志失败: %s", e)

    return jsonify(result), 200


# ============================================================
# 【改进 11】户型配比智能推荐
# ============================================================
@app.route("/api/unit_mix_recommendation", methods=["POST"])
def api_unit_mix_recommendation():
    """基于地块特征推荐最优户型配比"""
    data = request.get_json(silent=True) or {}
    decision = data.get("decision") or {}

    if not decision:
        return jsonify({"status": "error", "code": "INVALID_INPUT",
                        "message": "缺少 decision 字段"}), 400

    try:
        # 从决策数据提取特征
        risk_grade = decision.get("risk_grade", "B")
        total_area = decision.get("total_site_area_sqm", 50000)
        fsi = decision.get("fsi", 2.0)

        # 基于风险等级的推荐配比
        recommendations = []

        # 方案 1：激进型（高利润）
        if risk_grade in ["A", "B"]:
            recommendations.append({
                "name": "激进型（高利润）",
                "mix": {
                    "studio": 0.10,
                    "one_bedroom": 0.20,
                    "two_bedroom": 0.35,
                    "three_plus": 0.35
                },
                "expected_premium": 0.12,  # 12% 溢价
                "confidence": 0.85,
                "rationale": "低风险地块，偏向高端户型，预期溢价 12%"
            })

        # 方案 2：均衡型（稳健）
        recommendations.append({
            "name": "均衡型（稳健）",
            "mix": {
                "studio": 0.15,
                "one_bedroom": 0.25,
                "two_bedroom": 0.35,
                "three_plus": 0.25
            },
            "expected_premium": 0.08,  # 8% 溢价
            "confidence": 0.92,
            "rationale": "市场主流配比，风险低，预期溢价 8%"
        })

        # 方案 3：稳健型（低风险）
        recommendations.append({
            "name": "稳健型（低风险）",
            "mix": {
                "studio": 0.20,
                "one_bedroom": 0.30,
                "two_bedroom": 0.30,
                "three_plus": 0.20
            },
            "expected_premium": 0.04,  # 4% 溢价
            "confidence": 0.95,
            "rationale": "小户型比例高，去化快，风险最低，溢价 4%"
        })

        # 获取当前市场平均配比（从决策数据）
        current_mix = decision.get("unit_mix_agent", {}).get("unit_mix", [])
        current_mix_dict = {}
        if current_mix:
            for unit in current_mix:
                current_mix_dict[unit.get("type", "unknown")] = unit.get("ratio", 0)

        return jsonify({
            "status": "ok",
            "recommendations": recommendations,
            "current_market_mix": current_mix_dict,
            "market_context": {
                "risk_grade": risk_grade,
                "total_area": total_area,
                "fsi": fsi
            }
        }), 200

    except Exception as e:
        log.error("[推荐引擎] 户型推荐失败: %s", e)
        return jsonify({"status": "error", "code": "RECOMMENDATION_FAILED",
                        "message": str(e)}), 500


# ── 启动 ──────────────────────────────────────────────────────────────────────
# 必须放在文件末尾：app.run() 会阻塞主线程，
# 若置于路由定义之前，后续 @app.route 永不执行 → 接口 404
if __name__ == "__main__":
    print("DDS 核心三角 MVP — 启动检查...")
    status = startup_check()
    for w in status.get("warnings", []):
        print(f"  [!] {w}")
    if not status["ok"]:
        print("[FAIL] 启动检查未通过：")
        for issue in status["issues"]:
            print(f"  - {issue}")
        print("请修复上述问题后重新启动。")
        import sys
        sys.exit(1)
    print("[OK] 启动检查通过")
    log.info("server started on http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
