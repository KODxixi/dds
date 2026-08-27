"""
DDS 购房者数据层 — 为"购房者"视角派生本地真实证据。

全部基于 Vault 本地楼盘库（DuckDB，parquet 优先），每块输出都带 source/口径标注。
无法本地派生的（学区划片）如实标 "待接入" + 目标来源，绝不编造。
口径: 本库为新房归集数据，"同板块参考价"是新房在售价，非二手网签。
"""
import json
import os
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CURRENT_YEAR = "2026"

CITY_FILES = {"三亚": "新楼盘-三亚.csv", "杭州": "新楼盘-杭州.csv",
              "上海": "新楼盘-上海.csv", "青岛": "新楼盘-青岛.csv"}

SOURCE = "Vault 本地新房库(安居客归集)"


def _csv(city: str) -> str | None:
    """定位城市数据文件；parquet 优先（与 query_local 一致）。"""
    for key, fname in CITY_FILES.items():
        if city in key or key in city:
            p = VAULT / "2026新楼盘" / fname
            if p.exists():
                return str(p)
    yp = VAULT / f"{CURRENT_YEAR}年" / f"{city}.csv"
    return str(yp) if yp.exists() else None


def _src_expr(city: str) -> str | None:
    f = _csv(city)
    if not f:
        return None
    pq = f.rsplit(".", 1)[0] + ".parquet"
    return f"read_parquet('{pq}')" if os.path.exists(pq) else f"read_csv_auto('{f}', header=true, all_varchar=true)"


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return None


def list_districts(city: str) -> list[str]:
    """城市下真实区域列表（按楼盘数降序）— 供购房者区域下拉。"""
    src = _src_expr(city)
    if not src:
        return []
    try:
        rows = duckdb.query(f"""
            SELECT 区域名称, COUNT(*) n FROM {src}
            WHERE 区域名称 IS NOT NULL AND 区域名称 <> ''
            GROUP BY 区域名称 ORDER BY n DESC
        """).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _q(s) -> str:
    """转义单引号，防止本地 SQL 字符串拼接被用户输入破坏。"""
    return str(s).replace("'", "''")


def _band80(city: str) -> float:
    """城市价格带（回测校准 JSON，与 app.py 同源；缺失则回退默认 0.30）。"""
    bands = {"上海": 0.17, "杭州": 0.30, "三亚": 0.33, "青岛": 0.37}
    try:
        p = ROOT / "data" / "price_band_calibration.json"
        if p.exists():
            loaded = (json.loads(p.read_text(encoding="utf-8")) or {}).get("bands") or {}
            bands.update({k: float(v) for k, v in loaded.items() if v})
    except Exception:
        pass
    return float(bands.get(city, 0.30))


# ── 1. 同板块价格参考（真实，L2）──────────────────────────────────────────────
def resale_reference(city: str, district: str = None, intended_price: float = None) -> dict:
    """同板块在售新房单价分布 → 分位数；可对比用户意向单价判断'值不值'（描述性，不给买卖结论）。"""
    src = _src_expr(city)
    out = {"available": False, "source": SOURCE,
           "caliber": "同板块新房在售参考价（非二手网签价）"}
    if not src:
        return out
    where = "WHERE 参考价格 LIKE '%元/㎡%' AND TRY_CAST(最新价格 AS DOUBLE) > 0"
    if district:
        where += f" AND 区域名称 = '{_q(district)}'"
    try:
        rows = duckdb.query(f"""
            SELECT TRY_CAST(最新价格 AS DOUBLE) AS p
            FROM {src} {where}
        """).fetchall()
    except Exception:
        return out
    prices = sorted(p[0] for p in rows if p[0])
    if not prices:
        # 区域无样本 → 退一档到全市
        if district:
            return resale_reference(city, None, intended_price)
        return out
    n = len(prices)

    def pct(q):
        return prices[min(n - 1, int(q * n))]

    out.update({
        "available": True, "scope": district or city, "sample_size": n,
        "p25": round(pct(0.25)), "median": round(pct(0.5)), "p75": round(pct(0.75)),
        "low": round(prices[0]), "high": round(prices[-1]),
    })
    # 区域价格区间（回测校准，覆盖~80%；与 app.py 同源 JSON）
    b = _band80(city)
    out["range80"] = {"band_pct": round(b * 100),
                      "low": round(out["median"] * (1 - b)),
                      "high": round(out["median"] * (1 + b)),
                      "basis": "回测校准(覆盖~80%)"}
    # 值不值：意向价 vs 板块中位（纯描述）+ 是否落在校准区间
    if intended_price:
        med = out["median"]
        delta = (intended_price - med) / med * 100
        below = sum(1 for p in prices if p <= intended_price)
        out["value_for_money"] = {
            "intended_price": round(intended_price),
            "delta_vs_median_pct": round(delta, 1),
            "percentile": round(below / n * 100),
            "in_range80": out["range80"]["low"] <= intended_price <= out["range80"]["high"],
            "note": (f"意向单价 ¥{intended_price:,.0f} "
                     + ("高于" if delta >= 0 else "低于")
                     + f"同板块中位价 ¥{med:,.0f} 约 {abs(delta):.0f}%，"
                     + f"在 {n} 个在售样本中处于第 {round(below/n*100)} 百分位。"),
        }
    return out


# ── 2. 开发商交付信用 / 防烂尾（真实派生，指示性）─────────────────────────────
def developer_credit(developer: str, city: str = None) -> dict:
    """从该开发商库内项目的 销售状态/工程进度/交房时间 派生指示性交付信号。非官方信用评级。"""
    out = {"available": False, "developer": developer, "source": SOURCE + " 派生",
           "caliber": "基于库内项目销售状态/工程进度/交房时间的指示性信号，非官方信用评级"}
    if not developer:
        return out
    cities = [city] if city else list(CITY_FILES)
    projects = []
    for c in cities:
        src = _src_expr(c)
        if not src:
            continue
        try:
            rows = duckdb.query(f"""
                SELECT 楼盘名称, 销售状态, 工程进度, 交房时间, 城市名称
                FROM {src}
                WHERE 开发商 LIKE '%{_q(developer)}%' OR 开发商品牌 LIKE '%{_q(developer)}%'
            """).fetchall()
        except Exception:
            continue
        for r in rows:
            projects.append({"name": r[0], "status": r[1], "progress": r[2],
                             "delivery": r[3], "city": r[4]})
    if not projects:
        return out
    n = len(projects)
    sold_out = sum(1 for p in projects if p["status"] in ("售罄", "尾盘"))
    on_sale = sum(1 for p in projects if p["status"] == "在售")
    # 记录广度分级（库内可见项目数）
    if n >= 5:
        track = "充足"
    elif n >= 2:
        track = "有限"
    else:
        track = "单一"
    out.update({
        "available": True, "project_count": n,
        "sold_out": sold_out, "on_sale": on_sale, "track_record": track,
        "projects": projects[:8],
        "signal": (f"该开发商在库内可见 {n} 个项目（记录{track}）："
                   f"已售罄/尾盘 {sold_out} 个、在售 {on_sale} 个。"
                   "可作为品牌与去化能力的参考，交付风险请另查官方预售资金监管与施工进度公示。"),
    })
    return out


# ── 3. 物业参考（真实字段）────────────────────────────────────────────────────
def property_quality(city: str, project_name: str = None, district: str = None) -> dict:
    """物业公司/管理费/特色 — 真实字段，字段缺失则降级。"""
    out = {"available": False, "source": SOURCE}
    src = _src_expr(city)
    if not src:
        return out
    where = ""
    if project_name:
        where = f"WHERE 楼盘名称 LIKE '%{_q(project_name)}%'"
    elif district:
        where = f"WHERE 区域名称 = '{_q(district)}'"
    try:
        rows = duckdb.query(f"""
            SELECT 楼盘名称, 物业公司, 物业管理费, 物业特色
            FROM {src} {where} LIMIT 12
        """).fetchall()
    except Exception:
        # 字段不存在 → 该城数据无物业字段
        out["note"] = "本城数据暂无物业字段"
        return out
    items = [{"project": r[0], "company": r[1], "fee": r[2], "feature": r[3]}
             for r in rows if r[1]]
    if not items:
        return out
    out.update({"available": True, "items": items})
    return out


# ── 4. 周边学校（高德 POI 真实 L2）+ 学区划片（待接入）────────────────────────
def school_proximity(amenities: dict) -> dict:
    """复用已抓取的高德学校 POI（L2 真实）。学区'划片'本地无数据，如实标待接入。"""
    schools = []
    if amenities:
        for cat in ("学校", "教育", "中小学", "school", "education"):
            v = amenities.get(cat)
            if isinstance(v, list):
                schools.extend(v)
    return {
        "nearby_schools": schools[:10],
        "nearby_count": len(schools),
        "source": "高德地图 POI（已抓取）" if schools else "高德地图 POI",
        "school_district": {
            "status": "待接入",
            "reason": "学区'划片'归属为政策数据，本地库无；周边学校 POI 不等于学区归属。",
            "intended_source": "各区教育局学区划片年度公告",
        },
    }


def _first_num(x):
    """从脏字段取首个数字（如『住宅：1.2 别墅：1.2』→1.2，『40%』→40）。"""
    if x is None:
        return None
    m = re.search(r"\d+\.?\d*", str(x))
    return float(m.group()) if m else None


def density_green(city: str, district: str = None) -> dict:
    """板块低密/绿化真实信号：库内 容积率/绿化率均值 → 低密分(容积率越低越高)+绿化分。"""
    out = {"available": False, "source": SOURCE, "caliber": "板块容积率/绿化率均值（库内楼盘）"}
    src = _src_expr(city)
    if not src:
        return out
    where = f"WHERE 区域名称 = '{_q(district)}'" if district else ""
    try:
        rows = duckdb.query(f"SELECT 容积率, 绿化率 FROM {src} {where}").fetchall()
    except Exception:
        return out
    fars = [v for v in (_first_num(r[0]) for r in rows) if v and 0.3 <= v <= 20]
    grns = [v for v in (_first_num(r[1]) for r in rows) if v and 0 < v <= 100]
    far_avg = round(sum(fars) / len(fars), 2) if fars else None
    green_avg = round(sum(grns) / len(grns), 1) if grns else None
    out.update({
        "available": bool(fars or grns), "far_avg": far_avg, "green_avg": green_avg,
        # 低密分：容积率 1.0→100，每 +1.0 扣 25；绿化分：绿化率×2 封顶
        "density_score": max(0, min(100, round(100 - (far_avg - 1.0) * 25))) if far_avg else None,
        "green_score": max(0, min(100, round(green_avg * 2))) if green_avg else None,
        "sample": len(fars),
    })
    return out


# ── 购房动机多维评分（透明规则，每分都有真实依据）────────────────────────────
# 城市定位系数：声明式启发权重(非数据读数)，仅用于度假/康养/通勤这类城市级属性
CITY_TRAIT = {
    "三亚": {"resort": 1.0, "wellness": 1.0, "commute": 0.4},
    "青岛": {"resort": 0.6, "wellness": 0.6, "commute": 0.7},
    "杭州": {"resort": 0.35, "wellness": 0.4, "commute": 0.9},
    "上海": {"resort": 0.3, "wellness": 0.35, "commute": 1.0},
}


def _poi(amenities: dict, key: str):
    """返回某类 POI 的 (数量, 最近距离m)。"""
    v = (amenities or {}).get(key) or {}
    items = v.get("items") or []
    dists = [i.get("distance_m") or i.get("distance") for i in items
             if (i.get("distance_m") or i.get("distance"))]
    return len(items), (min(dists) if dists else None)


def _poi_score(count: int, nearest) -> int:
    """POI 强度 0-100：数量分(每个+18,封顶70) + 就近分(<=500m+30/<=1km+20/<=2km+10)。"""
    if not count:
        return 0
    s = min(70, count * 18)
    if nearest is not None:
        s += 30 if nearest <= 500 else 20 if nearest <= 1000 else 10 if nearest <= 2000 else 0
    return min(100, s)


def _blend(parts) -> int:
    """加权平均，parts=[(权重,值)...]。"""
    tw = sum(w for w, _ in parts) or 1
    return round(sum(w * v for w, v in parts) / tw)


def motivation_fit(city: str, amenities: dict = None, resale: dict = None,
                   developer_credit: dict = None, market: dict = None,
                   density: dict = None) -> dict:
    """围绕多种购房出发点依次打分（学区/自住/投资/度假/刚需/养老/康养）。每分都附 drivers 依据。"""
    S = {k: _poi_score(*_poi(amenities, k))
         for k in ("school", "hospital", "subway", "bus", "mall", "park", "supermarket")}
    c = {k: _poi(amenities, k)[0] for k in S}  # 各类数量(用于 drivers 文案)
    trait = CITY_TRAIT.get(city, {"resort": 0.4, "wellness": 0.4, "commute": 0.8})
    pct = ((resale or {}).get("value_for_money") or {}).get("percentile")  # 意向价分位
    sample = ((market or {}).get("sample_size") or (market or {}).get("sample_count")
              or (resale or {}).get("sample_size") or 0)
    liq = min(100, sample * 3)  # 在售样本活跃度 ~ 流动性近似
    dev_t = {"充足": 100, "有限": 60, "单一": 30}.get((developer_credit or {}).get("track_record"), 0)
    aff = (100 - pct) if pct is not None else None  # 价格可负担度(分位越低越可负担)
    # 低密/绿化真实信号（缺失则中性 50，不臆造）
    dg = density or {}
    den, grn = dg.get("density_score"), dg.get("green_score")
    far_avg, green_avg = dg.get("far_avg"), dg.get("green_avg")
    denv = den if den is not None else 50
    grnv = grn if grn is not None else 50
    eco = (f"·容积率 {far_avg}(低密{den})" if far_avg is not None else "") + (f"·绿化 {green_avg}%" if green_avg is not None else "")

    items = [
        {"key": "school", "label": "学区", "score": S["school"],
         "drivers": [f"周边学校 {c['school']} 处"],
         "caveat": "按周边学校 POI 估算，学区划片待接入"},
        {"key": "live", "label": "自住", "score": _blend(
            [(2, S["mall"]), (2, S["supermarket"]), (2, S["subway"]), (1, S["hospital"]), (1, S["school"]), (1, S["park"]), (1, grnv)]),
         "drivers": [f"商场 {c['mall']}·超市 {c['supermarket']}·地铁 {c['subway']}·医院 {c['hospital']}" + (f"·绿化 {green_avg}%" if green_avg is not None else "")]},
        {"key": "invest", "label": "投资", "score": _blend(
            [(2, S["subway"]), (2, liq), (1, dev_t), (1, S["mall"])]),
         "drivers": [f"地铁 {c['subway']} 处·在售样本 {sample}·开发商记录{(developer_credit or {}).get('track_record', '—')}"],
         "caveat": "信息参考，非投资建议"},
        {"key": "vacation", "label": "第二居所/度假", "score": _blend(
            [(2, round(trait["resort"] * 100)), (2, S["park"]), (2, denv), (1, 100 - S["subway"])]),
         "drivers": [f"城市度假属性 {int(trait['resort'] * 100)}·公园 {c['park']} 处{eco}"]},
        {"key": "essential", "label": "刚需", "score": _blend(
            [(2, aff if aff is not None else 50), (2, S["subway"]), (1, S["bus"]), (1, S["supermarket"])]),
         "drivers": [(f"价格可负担度 {aff}" if aff is not None else "价格分位未知(填意向价更准)") + f"·地铁 {c['subway']}·公交 {c['bus']}"]},
        {"key": "commute", "label": "通勤/职住", "score": _blend(
            [(3, S["subway"]), (2, round(trait.get("commute", 0.6) * 100)), (1, S["bus"])]),
         "drivers": [f"地铁 {c['subway']} 处·公交 {c['bus']} 处·城市通勤属性 {int(trait.get('commute', 0.6) * 100)}"]},
        {"key": "retire", "label": "养老", "score": _blend(
            [(3, S["hospital"]), (2, S["park"]), (2, denv), (1, grnv), (1, round(trait["wellness"] * 100))]),
         "drivers": [f"医院 {c['hospital']} 处·公园 {c['park']} 处{eco}"]},
        {"key": "wellness", "label": "康养", "score": _blend(
            [(2, round(trait["wellness"] * 100)), (2, S["park"]), (2, grnv), (2, denv), (1, S["hospital"])]),
         "drivers": [f"城市康养属性 {int(trait['wellness'] * 100)}·公园 {c['park']}·医院 {c['hospital']}{eco}"]},
    ]
    for it in items:
        it["tier"] = "强" if it["score"] >= 70 else "中" if it["score"] >= 40 else "弱"
    items.sort(key=lambda x: -x["score"])  # 适配度高的动机排前
    return {
        "items": items,
        "basis": "高德POI(L2) + 同板块价分位 + 开发商记录 + 容积率/绿化率 + 城市定位系数(规则)",
        "note": "多动机适配度为透明规则估算，依据见各项 drivers；城市定位系数为声明式启发权重，非数据读数。",
    }


# ── 汇总 ──────────────────────────────────────────────────────────────────────
def buyer_profile(city: str, district: str = None, intended_price: float = None,
                  developer: str = None, project_name: str = None,
                  amenities: dict = None, market: dict = None) -> dict:
    """购房者证据汇总：动机评分 + 四块证据；统一口径与免责。persona=buyer 时调用。"""
    resale = resale_reference(city, district, intended_price)
    dc = developer_credit(developer, city)
    dg = density_green(city, district)
    return {
        "motivation_fit": motivation_fit(city, amenities, resale, dc, market, dg),
        "density_green": dg,
        "resale_reference": resale,
        "value_for_money": resale.get("value_for_money"),
        "developer_credit": dc,
        "property": property_quality(city, project_name, district),
        "school": school_proximity(amenities or {}),
        "disclaimer": "本视图为信息参考工具，不构成投资或购房建议；"
                      "数据有口径与时效限制，请以官方备案、预售资金监管与实地核验为准。",
        "sources_note": "价格/开发商/物业派生自 " + SOURCE + "；周边学校来自高德 POI；学区划片待接入。",
    }


if __name__ == "__main__":
    import json
    import sys
    city = sys.argv[1] if len(sys.argv) > 1 else "三亚"
    dist = sys.argv[2] if len(sys.argv) > 2 else None
    prof = buyer_profile(city, dist, intended_price=35000, developer="碧桂园")
    # 兼容 Windows GBK 终端，避免 ¥ / ㎡ 等符号 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(prof, ensure_ascii=False, indent=2))
