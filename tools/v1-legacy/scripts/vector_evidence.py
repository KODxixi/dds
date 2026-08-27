"""
向量证据查询：查找类似地块作为决策证据。

优先向量后端（云端钩子 _try_vector_backend，当前未接入恒返 None），
不可用时降级 Vault CSV 价格相似度匹配。

让 value_agent 拿到"类似地块的历史成交 / 同档次产品"作为新证据维度。

历史说明：曾依赖 Gavis 统一索引中枢 (LanceDB)，已按用户决定移除；
向量能力改由 _try_vector_backend 预留给后续本地/云端向量后端（见 handoff）。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"


def _try_vector_backend(city, avg_price, product_type, top_k):
    """向量后端钩子：预留给后续本地(LocalLanceStore)/云端(VikingDB)向量检索。

    当前未接入，恒返回 None → 调用方走 CSV 价格相似度兜底。
    接入时在此调用 VectorStore.search 并返回同 _fallback 结构的 dict。
    """
    return None


def find_similar_parcels(
    city: str,
    avg_price: float | None,
    product_type: str | None = None,
    top_k: int = 5,
    years_back: int = 5,
) -> dict:
    """查找语义/价格相似的历史地块作为决策证据。

    优先向量后端（当前未接入），不可用时降级 Vault CSV 价格相似度匹配。
    """
    if not city:
        return {"status": "skipped", "reason": "no_city", "items": []}

    # 1) 试向量后端（云端钩子，当前恒 None）
    try:
        evidence = _try_vector_backend(city, avg_price, product_type, top_k)
        if evidence and evidence.get("items"):
            return evidence
    except Exception:
        pass  # 静默降级

    # 2) Vault CSV 价格相似度兜底
    return _fallback_vault_similarity(city, avg_price, product_type, top_k, years_back)


def _fallback_vault_similarity(city, avg_price, product_type, top_k, years_back):
    """从 Vault 近 N 年 CSV 找单价接近且产品类型可能相关的楼盘"""
    if not VAULT.exists():
        return {"status": "not_configured",
                "reason": "LanceDB 未配置且 Vault 不存在",
                "items": []}
    # 找最近 years_back 个有数据的年份
    year_dirs = sorted([d for d in VAULT.iterdir() if d.is_dir()
                        and re.match(r"\d{4}年", d.name)],
                       key=lambda p: p.name, reverse=True)[:years_back + 1]
    candidates = []
    for yd in year_dirs:
        f = yd / f"{city}.csv"
        if not f.exists():
            continue
        try:
            year = int(yd.name[:4])
        except ValueError:
            continue
        with open(f, encoding="utf-8-sig") as fp:
            for r in csv.DictReader(fp):
                price_text = (r.get("最新价格") or "").strip()
                price = _extract_price(price_text)
                if not price:
                    continue
                area_range = (r.get("面积范围") or "").strip()
                product_match = _product_match(r.get("户型文本描述", ""), product_type)
                candidates.append({
                    "year": year,
                    "project_name": r.get("楼盘名称"),
                    "district": r.get("区域名称") or r.get("子区域名称"),
                    "address": r.get("地址"),
                    "unit_price": price,
                    "area_range": area_range,
                    "room_types": r.get("户型文本描述"),
                    "developer": r.get("开发商品牌") or r.get("开发商"),
                    "product_match": product_match,
                })
    if not candidates:
        return {"status": "no_data", "items": []}
    # 按 (产品匹配优先, 价格距离, 年份近) 排序
    if avg_price:
        for c in candidates:
            c["_dist"] = abs(c["unit_price"] - avg_price) / max(avg_price, 1)
    else:
        for c in candidates:
            c["_dist"] = 0
    candidates.sort(key=lambda c: (not c["product_match"], c["_dist"], -c["year"]))
    items = []
    for c in candidates[:top_k]:
        items.append({
            "text": f"{c['year']}年 · {c['project_name']} ({c['district']}) "
                    f"· {c['unit_price']} 元/㎡ · {c['area_range']} · "
                    f"{c['developer'] or '-'}",
            "metadata": {
                "year": c["year"],
                "project_name": c["project_name"],
                "district": c["district"],
                "address": c["address"],
                "unit_price": c["unit_price"],
                "area_range": c["area_range"],
                "developer": c["developer"],
                "price_distance": round(c["_dist"], 3),
                "product_match": c["product_match"],
            },
        })
    return {
        "status": "ok",
        "source": "vault_fallback",
        "collection": f"Vault CSV (近 {years_back} 年)",
        "items": items,
        "note": "LanceDB 未配置或无数据，使用 Vault 价格相似度兜底",
    }


def _extract_price(s: str) -> int | None:
    if not s:
        return None
    m = re.search(r"(\d{3,})", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _product_match(room_text: str, product_type: str | None) -> bool:
    if not product_type or not room_text:
        return False
    if "商墅" in product_type or "别墅" in product_type:
        return "别墅" in room_text or "商墅" in room_text
    if "豪宅" in product_type or "高端" in product_type:
        return "大平层" in room_text or "5室" in room_text or "四室" in room_text
    if "改善" in product_type:
        return "三室" in room_text or "四室" in room_text
    return True
