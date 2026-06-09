"""
向量证据查询：优先 chromadb，fallback 到 Vault CSV 价格相似度匹配

让 value_agent 拿到"类似地块的历史成交 / 同档次产品"作为新证据维度。
"""
from __future__ import annotations

import os

# ⚠ 必须在导入 transformers 之前设置离线模式
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CHROMA_PATH = str(ROOT / "vectordb")
MODEL_CACHE = str(ROOT / "models" / "bge-m3")

# 模块级模型缓存，只加载一次
_embed_model = None
_embed_tokenizer = None


def _get_embed_fn():
    """懒加载 BGE-M3（AutoModel + mean pooling，避免 SentenceTransformer 的 SIGSEGV）"""
    global _embed_model, _embed_tokenizer
    if _embed_model is None:
        from transformers import AutoTokenizer, AutoModel
        import torch
        _embed_tokenizer = AutoTokenizer.from_pretrained(
            "BAAI/bge-m3", cache_dir=MODEL_CACHE
        )
        _embed_model = AutoModel.from_pretrained(
            "BAAI/bge-m3", cache_dir=MODEL_CACHE
        )
        _embed_model.eval()

    def encode(texts):
        tokens = _embed_tokenizer(
            list(texts), padding=True, truncation=True,
            max_length=512, return_tensors="pt"
        )
        with torch.no_grad():
            out = _embed_model(**tokens)
        attn = tokens["attention_mask"].unsqueeze(-1).float()
        emb = (out.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
        return torch.nn.functional.normalize(emb, p=2, dim=1)

    return encode


def find_similar_parcels(
    city: str,
    avg_price: float | None,
    product_type: str | None = None,
    top_k: int = 5,
    years_back: int = 5,
) -> dict:
    """查找语义/价格相似的历史地块作为决策证据。

    优先 chromadb，不可用时降级 Vault CSV 价格相似度匹配。
    """
    if not city:
        return {"status": "skipped", "reason": "no_city", "items": []}

    # 1) 试 chromadb
    try:
        evidence = _try_chromadb(city, avg_price, product_type, top_k)
        if evidence and evidence.get("items"):
            return evidence
    except Exception as e:
        pass  # 静默降级

    # 2) Vault CSV 价格相似度兜底
    return _fallback_vault_similarity(city, avg_price, product_type, top_k, years_back)


def _try_chromadb(city, avg_price, product_type, top_k):
    """尝试连 chromadb，用 BGE-M3 (AutoModel + mean pooling) 做真语义检索。
    若依赖/数据缺失则抛异常被外层 catch。
    """
    import chromadb

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    encode = _get_embed_fn()

    query_text = f"{city} {product_type or ''} 均价 {int(avg_price or 0)} 元/㎡"

    # 优先 competitor_projects（Vault 楼盘数据），其次 transactions（成交数据）
    results = []
    for coll_name in ("competitor_projects", "transactions"):
        try:
            coll = client.get_collection(coll_name)
            if coll.count() == 0:
                continue
            query_emb = encode([query_text]).tolist()
            res = coll.query(query_embeddings=query_emb, n_results=top_k)
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            distances = (res.get("distances") or [[]])[0]
            for idx, (d, m) in enumerate(zip(docs, metas)):
                results.append({
                    "text": d[:300] if d else "",
                    "metadata": m or {},
                    "distance": round(distances[idx], 4) if idx < len(distances) else None,
                    "collection": coll_name,
                })
            if results:
                break  # 主力集合有数据就不往下查
        except Exception:
            continue

    if not results:
        return None

    # 按 distance 升序（余弦距离越小越相似），截 top_k
    results.sort(key=lambda x: x.get("distance", 1.0))
    results = results[:top_k]

    return {
        "status": "ok",
        "source": "chromadb",
        "collection": results[0]["collection"] if results else "unknown",
        "items": results,
    }


def _fallback_vault_similarity(city, avg_price, product_type, top_k, years_back):
    """从 Vault 近 N 年 CSV 找单价接近且产品类型可能相关的楼盘"""
    if not VAULT.exists():
        return {"status": "not_configured",
                "reason": "vectordb 未配置且 Vault 不存在",
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
        "note": "chromadb 未配置或无数据，使用 Vault 价格相似度兜底",
    }


def _extract_price(s: str) -> int | None:
    if not s:
        return None
    m = re.search(r"(\d{3,})", s)  # 至少 3 位数（防止匹配到年份后缀）
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
