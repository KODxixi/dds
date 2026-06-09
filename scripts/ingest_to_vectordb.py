"""
脚本: ingest_to_vectordb.py
功能: 读取本地 normalized JSON 文件，转换文本模板并生成 Embedding 写入 ChromaDB
使用方式: python ingest_to_vectordb.py [--source landchina|hangzhou|...]
"""
import os
import sys
import json
import glob
import datetime
import argparse
from collections import defaultdict

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from sentence_transformers import SentenceTransformer

# --- 基础配置 ---
CHROMA_PATH = r"C:\Users\shiguanyu\DDS\vectordb"
MODEL_DIR = r"C:\Users\shiguanyu\DDS\models\bge-m3"
MODEL_NAME = "BAAI/bge-m3"
LOG_FILE = r"C:\Users\shiguanyu\DDS\vectordb\ingest_log.jsonl"
BASE_DATA_DIR = r"C:\Users\shiguanyu\DDS\data_out"

# --- 数据源到 Collection 和匹配模式的配置映射 ---
# data_source_key -> { "coll": target_collection_name, "path_sub": 目录子路径, "file_glob": 匹配模式 }
SOURCE_CONFIG = {
    "landchina":      {"coll": "land_parcels", "sub": "landchina",      "glob": "normalized*_normalized.json"},
    "hangzhou":       {"coll": "land_parcels", "sub": "hangzhou",       "glob": "normalized*_normalized.json"},
    "sanya":          {"coll": "land_parcels", "sub": "sanya",          "glob": "normalized*_normalized.json"},
    "hangzhou_deals": {"coll": "transactions", "sub": "hangzhou_deals", "glob": "normalized*_normalized.json"},
    "sanya_deals":    {"coll": "transactions", "sub": "sanya_deals",    "glob": "normalized*_normalized.json"},
    "pdfs":           {"coll": "pdf_extracts", "sub": "pdfs",           "glob": "parsed*_parsed.json"}
}

# --- 向量化文本模板 (按 Collection) ---
TEXT_TEMPLATES = {
    "land_parcels": "{city}{district}土地出让，用途{land_use}，面积{area_sqm}㎡，容积率{floor_area_ratio}，起拍价{starting_price_cny}元，发布日期{published_date}，{announcement_type}",
    
    "transactions": "{city}{district}{project_name}，{room_type}户型，建筑面积{area_sqm}㎡，成交单价{unit_price_cny}元/㎡，成交总价{total_price_cny}元，楼层{floor}，签约日期{deal_date}",
    
    "competitor_projects": "{city}{district}{project_name}，开发商{developer}，{room_type}户型面积{area_sqm}㎡，备案单价{unit_price_cny}元/㎡，总价{total_price_cny}元，备案日期{registration_date}，状态{status}",
    
    "pdf_extracts": "{city}{district}地块规划条件，容积率{floor_area_ratio}，限高{building_height_m}米，绿地率{green_ratio}%，起拍价{starting_price_cny}元，出让年限{supply_years}年，政策标签：{policy_tags}，约束：{constraints}",
    
    "gis_context": "{city}{district}地块GIS画像，最近地铁{gis_nearest_subway}距{gis_nearest_subway_m}米，最近学校{gis_nearest_school}距{gis_nearest_school_m}米，通勤{gis_commute_summary}"
}

# --- Embedding 模型单例与 Chroma 适配器 ---
_model_instance = None

def get_model():
    global _model_instance
    if _model_instance is None:
        print(f"[*] 正在加载进程级单例 Embedding 模型: {MODEL_NAME}")
        # sentence-transformers 会根据 cache_folder 自动利用本地缓存
        _model_instance = SentenceTransformer(MODEL_NAME, cache_folder=MODEL_DIR)
        _model_instance.max_seq_length = 512 # 按 Prompt 要求限制为 512 tokens 截断
        print("[+] 模型单例加载完成")
    return _model_instance

class BgeM3EmbeddingFunction(EmbeddingFunction):
    """ChromaDB 定制 Embedding 适配器，将 BGE-M3 模型注册给 ChromaDB 托管推理"""
    def __init__(self):
        self.model = get_model()
    
    def __call__(self, input: Documents) -> Embeddings:
        # 批量处理编码，normalize_embeddings 提升相似度搜索质量
        embeddings = self.model.encode(input, normalize_embeddings=True, show_progress_bar=False)
        return embeddings.tolist()

# --- 核心工具函数 ---

def log_progress(msg_dict):
    """写入进度记录到 jsonl"""
    msg_dict["@timestamp"] = datetime.datetime.now().isoformat()
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(msg_dict, ensure_ascii=False) + "\n")

def clean_and_format_text(template, item):
    """按照 prompt 要求：None 转为空字符串拼接文本"""
    # 构建 safe dict，默认值为空字符串
    data = defaultdict(str)
    for key, val in item.items():
        # 过滤 None 转换为 ""
        if val is None:
            data[key] = ""
        else:
            data[key] = str(val)
            
    # 使用 format_map 防止模板里的字段在 JSON 中缺失导致崩溃
    formatted_text = template.format_map(data)
    return formatted_text

def build_metadata(item, source_key):
    """提炼元数据并确保符合 Chroma 数据类型规范 (str, int, float, bool)"""
    # 规范字段列表
    meta_spec = ["source", "category", "data_level", "confidence", "city", 
                 "province", "district", "date", "year", "month", 
                 "parcel_id", "gcs_uri", "scraped_at"]
    
    metadata = {}
    for k in meta_spec:
        # 提取值，不存在默认 ""
        val = item.get(k, "")
        if val is None:
            val = ""
            
        # 处理特定整数类型映射
        if k in ["data_level", "year", "month"]:
            try:
                metadata[k] = int(val) if val != "" else 0
            except:
                metadata[k] = 0
        else:
            metadata[k] = str(val)
    
    # 兜底 source 填充 (如果原始 JSON 没有)
    if not metadata["source"]:
        metadata["source"] = source_key
        
    return metadata

def process_source_files(source_key, config, client, embed_fn, batch_size=50):
    """针对单一数据源执行扫描并入库"""
    target_coll_name = config["coll"]
    search_dir = os.path.join(BASE_DATA_DIR, config["sub"])
    pattern = os.path.join(search_dir, "**", config["glob"])
    
    print(f"\n[*] 正在处理数据源: [{source_key}] -> Collection: [{target_coll_name}]")
    print(f"[*] 扫描路径模式: {pattern}")
    
    files = glob.glob(pattern, recursive=True)
    if not files:
        print(f"[!] 未在 {search_dir} 下找到符合 '{config['glob']}' 的文件，跳过。")
        return 0
        
    print(f"[+] 发现待处理 JSON 文件数: {len(files)}")
    
    # 获取对应 Collection
    collection = client.get_or_create_collection(
        name=target_coll_name, 
        embedding_function=embed_fn
    )
    
    template = TEXT_TEMPLATES.get(target_coll_name, "")
    initial_count = collection.count()
    
    items_to_add = []
    processed_files = 0
    
    for fpath in files:
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = json.load(f)
            
            # 统一转换成对象列表
            rows = []
            if isinstance(content, list):
                rows = content
            elif isinstance(content, dict):
                # 兼容 {"data": [...]} 这种包装格式
                if "data" in content and isinstance(content["data"], list):
                    rows = content["data"]
                else:
                    rows = [content]
            
            for item in rows:
                # 1. 构建 ID：{source}_{parcel_id}_{scraped_at[:10]}
                s_at = item.get("scraped_at", "")
                if not s_at:
                    s_at = datetime.datetime.now().strftime("%Y-%m-%d")
                s_date = s_at[:10]
                
                p_id = item.get("parcel_id", "")
                if not p_id:
                    # 兜底，防止 ID 重复引发 Chroma 冲突，加个随机或摘要
                    import uuid
                    p_id = f"gen-{uuid.uuid4().hex[:8]}"
                
                doc_id = f"{source_key}_{p_id}_{s_date}"
                
                # 2. 生成文本
                doc_text = clean_and_format_text(template, item)
                
                # 3. 生成元数据
                metadata = build_metadata(item, source_key)
                
                items_to_add.append({
                    "id": doc_id,
                    "document": doc_text,
                    "metadata": metadata
                })
            processed_files += 1
                
        except Exception as e:
            print(f"[!] 读取文件 {os.path.basename(fpath)} 失败: {e}")
            continue

    total_incoming = len(items_to_add)
    print(f"[+] 数据转换完成。总行数: {total_incoming}，准备分批写入...")
    
    # 4. 批量分包写入 (防溢出)
    success_in = 0
    for i in range(0, total_incoming, batch_size):
        batch = items_to_add[i : i + batch_size]
        
        b_ids = [b["id"] for b in batch]
        b_docs = [b["document"] for b in batch]
        b_metas = [b["metadata"] for b in batch]
        
        try:
            # 使用 upsert 提供天然的去重机制 (更新或插入)
            collection.upsert(
                ids=b_ids,
                documents=b_docs,
                metadatas=b_metas
            )
            success_in += len(batch)
            print(f"    -> 已写入批次: [{i + len(batch)} / {total_incoming}]")
        except Exception as e:
            print(f"    [✕] 写入批次出错: {e}")
    
    # 统计结果
    final_count = collection.count()
    added_net = final_count - initial_count
    
    summary_str = f"{target_coll_name}: +{added_net} docs, final_total={final_count}"
    print(f"\n[✓] 数据源 {source_key} 执行完毕: {summary_str}")
    
    # 写入持久化日志
    log_progress({
        "action": "ingest",
        "source": source_key,
        "collection": target_coll_name,
        "files_scanned": len(files),
        "processed_files": processed_files,
        "attempted_records": total_incoming,
        "net_increase": added_net,
        "total_count": final_count
    })
    
    return added_net

def main():
    parser = argparse.ArgumentParser(description="DDS 数据中心向量入库管道")
    parser.add_argument("--source", type=str, choices=list(SOURCE_CONFIG.keys()), 
                        help="指定单一数据源（如 landchina / hangzhou_deals），留空则全量扫描")
    parser.add_argument("--batch-size", type=int, default=50, help="每批次写入条数 (默认50)")
    
    args = parser.parse_args()
    
    print("=== DDS 向量化入库启动 ===")
    
    # 初始化客户端和 embedding 函数包装器
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    ef_fn = BgeM3EmbeddingFunction() # 此处会触发 Singleton 模型加载
    
    sources_to_run = []
    if args.source:
        sources_to_run = [args.source]
        print(f"[*] 模式: 指定数据源 [{args.source}]")
    else:
        sources_to_run = list(SOURCE_CONFIG.keys())
        print(f"[*] 模式: 全库扫描模式，待检查数据源: {len(sources_to_run)} 个")

    overall_added = 0
    for skey in sources_to_run:
        conf = SOURCE_CONFIG[skey]
        added = process_source_files(skey, conf, client, ef_fn, args.batch_size)
        overall_added += added
        
    print("\n==========================================")
    print(f"🎉 全局入库任务结束。本次净新增: {overall_added} 条记录。")
    print(f"📊 详细记录详见: {LOG_FILE}")
    print("==========================================\n")

if __name__ == "__main__":
    main()
