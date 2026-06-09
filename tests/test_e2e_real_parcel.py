"""端到端真实地块验证：三亚海棠湾·佳兆业海棠伴山 → ABM + 决策报告"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.dds_decision_engine import run_decision_engine, write_decision_outputs


def load_sanya_competitors(target_district: str = "海棠区", top_n: int = 12) -> list[dict]:
    """从 Vault CSV 直接读真实三亚在售楼盘，提取 nearby_competitors 结构"""
    csv_path = ROOT / "Vault" / "2026新楼盘" / "新楼盘-三亚.csv"
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("销售状态") not in ("在售", "尾盘"):
                continue
            if target_district and target_district not in (r.get("区域名称") or ""):
                continue
            price = _safe_int(r.get("最新价格"))
            if not price:
                continue
            rows.append({
                "project_name": r.get("楼盘名称"),
                "unit_price_cny": price,
                "area_range": r.get("面积范围"),
                "room_types": r.get("户型文本描述"),
                "district": r.get("区域名称"),
                "lng": _safe_float(r.get("百度地图经度")),
                "lat": _safe_float(r.get("百度地图纬度")),
                "status": r.get("销售状态"),
            })
    rows.sort(key=lambda x: x["unit_price_cny"], reverse=True)
    return rows[:top_n]


def _safe_int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def _safe_float(s):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def build_real_parcel_report() -> dict:
    competitors = load_sanya_competitors("海棠区", top_n=12)
    prices = [c["unit_price_cny"] for c in competitors if c["unit_price_cny"]]
    avg = sum(prices) / len(prices) if prices else 35000
    return {
        "meta": {
            "generated_at": "2026-05-27T12:00:00",
            "version": "real-parcel-test",
            "data_scope": "Vault/2026新楼盘/三亚 · 海棠区",
        },
        "input": {
            "city": "三亚",
            "district": "海棠区",
            "address": "海棠区南田路16号",
            "lng": 109.71899,
            "lat": 18.41040,
        },
        "location": {"city": "三亚", "district": "海棠区",
                     "lng": 109.71899, "lat": 18.41040},
        "nearby_competitors": competitors,
        "market_summary": {
            "price": {"avg": int(avg), "min": min(prices), "max": max(prices)},
            "sample_count": len(competitors),
            "room_types": {},
        },
        "amenities": {},
    }


def run():
    parcel = build_real_parcel_report()
    print(f"[parcel] 加载 {len(parcel['nearby_competitors'])} 条真实海棠区竞品")
    print(f"[parcel] 均价 {parcel['market_summary']['price']['avg']} 元/㎡")

    goal = {
        "product_type": "低密商墅",
        "floor_area_ratio": 1.2,
        "benchmark": parcel["nearby_competitors"][0]["project_name"],
    }
    result = run_decision_engine(parcel, goal)

    abm = result["abm_market_agent"]
    summary = result["decision_summary"]
    print("\n=== ABM 输出 ===")
    print(f"方法: {abm['method']}")
    print(f"样本: N={abm['n_personas']}, 置信度={abm['confidence']['level']}")
    print(f"平均购买概率: {abm['avg_buy_probability']}")
    print("Top 3 客群:")
    for p in abm["top_personas"]:
        print(f"  - {p['name']}: share={p['share']*100:.1f}%, "
              f"score={p['score']}, WTP-p50={p['wtp_p50']:.0f} 元/㎡")
    print(f"\nWTP 总览: {abm['wtp_summary']}")
    print(f"12 个月累积去化: {abm['sellthrough_curve'][11]}")
    print(f"24 个月累积去化: {abm['sellthrough_curve'][23]}")
    print("\n价格敏感性:")
    for s in abm["price_sensitivity"]:
        print(f"  {s['price_delta']}: 平均买概率 {s['avg_buy_prob']} ({s['vs_base_pct']:+.1f}%)")

    print("\n=== 决策摘要 ===")
    print(f"标题: {summary['headline']}")
    print(f"拿地价区间: {summary['pricing_posture']}")
    print(f"核心机会: {summary['top_opportunity']}")
    print(f"硬阻断: {summary['hard_stop'] or '暂无'}")

    md, j, h = write_decision_outputs(result)
    print(f"\n[done] 报告已写入:\n  {md}\n  {j}\n  {h}")
    return result


if __name__ == "__main__":
    run()
