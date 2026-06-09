"""
脚本: query_vectordb.py
功能: 提供命令行检索接口，支持跨 Collection 查询和元数据筛选
"""
import os
import argparse
import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from sentence_transformers import SentenceTransformer

# 配置常量
CHROMA_PATH = r"C:\Users\shiguanyu\DDS\vectordb"
MODEL_DIR = r"C:\Users\shiguanyu\DDS\models\bge-m3"
MODEL_NAME = "BAAI/bge-m3"

ALL_COLLECTIONS = [
    "land_parcels",
    "transactions",
    "competitor_projects",
    "pdf_extracts",
    "gis_context"
]

# 单例模式加载器
_model_instance = None

def get_model():
    global _model_instance
    if _model_instance is None:
        print(f"[*] 正在静默加载 Embedding 模型...")
        _model_instance = SentenceTransformer(MODEL_NAME, cache_folder=MODEL_DIR)
        _model_instance.max_seq_length = 512
    return _model_instance

class BgeM3EmbeddingFunction(EmbeddingFunction):
    """查询专用的 Embedding 适配器"""
    def __init__(self):
        self.model = get_model()
    def __call__(self, input: Documents) -> Embeddings:
        return self.model.encode(input, normalize_embeddings=True, show_progress_bar=False).tolist()

def perform_query(client, ef_fn, collection_name, query_text, n_results, filters):
    """在指定 Collection 中执行查询"""
    try:
        coll = client.get_collection(name=collection_name, embedding_function=ef_fn)
    except Exception:
        # 如果 Collection 不存在，静默跳过或打印提示
        return []

    # 构建 metadata where 过滤器
    where_clause = {}
    for k, v in filters.items():
        if v is not None:
            where_clause[k] = v
            
    # 如果为空，传 None 给 chroma
    if not where_clause:
        where_clause = None

    try:
        res = coll.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where_clause
        )
        
        # 解析结果结构
        results = []
        if not res["ids"] or len(res["ids"][0]) == 0:
            return []
            
        for i in range(len(res["ids"][0])):
            dist = res["distances"][0][i] if "distances" in res and res["distances"] else 1.0
            # 将 Cosine 距离转换近似相关性得分 (1.0 - dist)
            score = 1.0 - dist 
            
            results.append({
                "coll": collection_name,
                "id": res["ids"][0][i],
                "document": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "score": score
            })
        return results
    except Exception as e:
        print(f"[!] Collection {collection_name} 查询出错: {e}")
        return []

def print_results(hits):
    if not hits:
        print("\n[-] 未检索到符合条件的记录。")
        return

    print(f"\n=== 检索到 {len(hits)} 条相关记录 ===")
    for idx, item in enumerate(hits, start=1):
        score = item["score"]
        doc = item["document"]
        meta = item["metadata"]
        
        # 按 Prompt 要求的输出格式拼接头行
        src = meta.get("source", "未知")
        city = meta.get("city", "未知")
        date_val = meta.get("date", "") or meta.get("scraped_at", "")[:10]
        
        header = f"[{idx}] score={score:.4f} | source={src} | city={city} | date={date_val}"
        
        # 如果是跨库搜索，展示库名
        coll_tag = f" | coll={item['coll']}" if item.get('is_multi') else ""
        
        print(header + coll_tag)
        # 打印文本片段，加缩进
        clean_doc = doc.replace("\n", " ").strip()
        if len(clean_doc) > 120:
            clean_doc = clean_doc[:117] + "..."
        print(f"  {clean_doc}")
        
        # 打印 GCS 链接
        gcs_uri = meta.get("gcs_uri", "无")
        print(f"  gcs: {gcs_uri}")
        print("-" * 40)

def main():
    parser = argparse.ArgumentParser(description="DDS 向量语义检索引擎")
    
    # 核心参数
    parser.add_argument("--q", type=str, required=True, help="语义查询文本")
    parser.add_argument("--collection", type=str, choices=ALL_COLLECTIONS, help="指定检索的 Collection")
    parser.add_argument("--all", action="store_true", help="跨所有 Collection 执行全局检索")
    parser.add_argument("--n", type=int, default=5, help="返回结果的最大条数")
    
    # 元数据过滤器参数
    parser.add_argument("--city", type=str, help="过滤城市")
    parser.add_argument("--year", type=int, help="过滤年份")
    parser.add_argument("--source", type=str, help="过滤数据源")
    
    args = parser.parse_args()
    
    # 校验模式
    if not args.collection and not args.all:
        parser.error("请至少指定 --collection <名称> 或使用 --all 进行全局搜索")

    if not os.path.exists(CHROMA_PATH):
        print(f"[!] 向量库目录未找到: {CHROMA_PATH}。请先运行 setup_vectordb.py")
        return

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef_fn = BgeM3EmbeddingFunction()
    
    # 汇集元数据过滤器
    filters = {}
    if args.city: filters["city"] = args.city
    if args.year: filters["year"] = args.year
    if args.source: filters["source"] = args.source

    target_colls = []
    if args.all:
        target_colls = ALL_COLLECTIONS
        is_multi = True
    else:
        target_colls = [args.collection]
        is_multi = False

    all_hits = []
    print(f"[*] 正在执行语义检索，目标库: {' | '.join(target_colls)}...")
    
    for cname in target_colls:
        hits = perform_query(client, ef_fn, cname, args.q, args.n, filters)
        for h in hits:
            h["is_multi"] = is_multi
        all_hits.extend(hits)

    # 按相似度分数倒序排列
    all_hits.sort(key=lambda x: x["score"], reverse=True)
    
    # 截取最终的前 N 条
    final_hits = all_hits[:args.n]
    
    print_results(final_hits)

if __name__ == "__main__":
    main()
