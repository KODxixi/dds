"""
脚本: query_vectordb.py (已切换至 Gavis LanceDB)
功能: 提供命令行检索接口，支持跨 Collection 查询和元数据筛选
使用 Gavis 统一索引中枢 (LanceDB)。
"""
import os
import sys
import argparse
from pathlib import Path

# 指向 Gavis 统一索引器
GAVIS_CORE = Path(os.path.expanduser("~")) / "Gavis" / "gavis-core"
if str(GAVIS_CORE) not in sys.path:
    sys.path.insert(0, str(GAVIS_CORE))

from unified_indexer import get_indexer, ALL_COLLECTIONS as ALL_SPECS

# DDS 相关的 collection 名
DDS_COLLECTIONS = [
    "dds_competitor_proj",
    "dds_land_parcels",
    "dds_transactions",
    "dds_pdf_extracts",
    "dds_gis_context",
]

# 兼容旧名到新名的映射
NAME_MAP = {
    "land_parcels": "dds_land_parcels",
    "transactions": "dds_transactions",
    "competitor_projects": "dds_competitor_proj",
    "pdf_extracts": "dds_pdf_extracts",
    "gis_context": "dds_gis_context",
}


def perform_query(idx, collection_name, query_text, n_results, filters):
    """在指定表中执行查询"""
    real_name = NAME_MAP.get(collection_name, collection_name)
    if real_name not in ALL_SPECS:
        print(f"[!] 未知 collection: {collection_name}")
        return []

    spec = ALL_SPECS[real_name]

    try:
        tbl = idx._get_table(spec)
    except Exception:
        print(f"[-] 表 {real_name} 不存在")
        return []

    query_vec = idx.embed_query_text(query_text, dim=spec.embedding_dim)

    # 构建 WHERE
    wheres = []
    for k, v in filters.items():
        if v is not None:
            if isinstance(v, str):
                wheres.append(f"{k} = '{v}'")
            else:
                wheres.append(f"{k} = {v}")
    filter_expr = " AND ".join(wheres) if wheres else None

    try:
        search = tbl.search(query_vec, vector_column_name="vector") \
            .metric("cosine") \
            .limit(n_results)
        if filter_expr:
            search = search.where(filter_expr, prefilter=True)
        results = search.to_list()
    except Exception as e:
        print(f"[!] 查询 {real_name} 出错: {e}")
        return []

    hits = []
    for r in results:
        dist = r.get("_distance", 1.0)
        score = 1.0 - dist
        hits.append({
            "coll": collection_name,
            "id": r.get("id", ""),
            "document": r.get("text", ""),
            "metadata": {
                k: r.get(k)
                for k in spec.schema_fields
                if r.get(k) is not None
            },
            "score": score,
        })
    return hits


def print_results(hits):
    if not hits:
        print("\n[-] 未检索到符合条件的记录。")
        return

    print(f"\n=== 检索到 {len(hits)} 条相关记录 ===")
    for idx, item in enumerate(hits, start=1):
        score = item["score"]
        doc = item["document"]
        meta = item["metadata"]

        city = meta.get("city", "未知")
        date_val = meta.get("indexed_at", "")[:10]

        header = f"[{idx}] score={score:.4f} | coll={item['coll']} | city={city} | date={date_val}"
        print(header)

        clean_doc = doc.replace("\n", " ").strip() if doc else ""
        if len(clean_doc) > 120:
            clean_doc = clean_doc[:117] + "..."
        print(f"  {clean_doc}")
        print("-" * 40)


def main():
    parser = argparse.ArgumentParser(description="DDS 向量语义检索引擎 (LanceDB)")
    parser.add_argument("--q", type=str, required=True, help="语义查询文本")
    parser.add_argument("--collection", type=str, choices=DDS_COLLECTIONS + list(NAME_MAP.keys()),
                        help="指定检索的 Collection")
    parser.add_argument("--all", action="store_true", help="跨所有 Collection 执行全局检索")
    parser.add_argument("--n", type=int, default=5, help="返回结果的最大条数")
    parser.add_argument("--city", type=str, help="过滤城市")
    parser.add_argument("--year", type=int, help="过滤年份")
    parser.add_argument("--source", type=str, help="过滤数据源")
    args = parser.parse_args()

    if not args.collection and not args.all:
        parser.error("请至少指定 --collection <名称> 或使用 --all 进行全局搜索")

    idx = get_indexer()
    print(f"[*] LanceDB = {idx.persist_dir}")

    filters = {}
    if args.city:
        filters["city"] = args.city
    if args.year:
        filters["year"] = args.year

    target_colls = DDS_COLLECTIONS if args.all else [args.collection]

    all_hits = []
    for cname in target_colls:
        hits = perform_query(idx, cname, args.q, args.n, filters)
        all_hits.extend(hits)

    all_hits.sort(key=lambda x: x["score"], reverse=True)
    final_hits = all_hits[:args.n]
    print_results(final_hits)


if __name__ == "__main__":
    main()
