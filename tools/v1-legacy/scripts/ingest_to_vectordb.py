"""
脚本: ingest_to_vectordb.py (已切换至 Gavis LanceDB)
功能: 读取本地 normalized JSON 文件，转换文本模板并生成 Embedding 写入统一索引中枢
使用方式: python ingest_to_vectordb.py [--source landchina|hangzhou|...]
"""
import os
import sys
import json
import glob
import datetime
import argparse
from collections import defaultdict
from pathlib import Path

# 指向 Gavis 统一索引器
GAVIS_CORE = Path(os.path.expanduser("~")) / "Gavis" / "gavis-core"
if str(GAVIS_CORE) not in sys.path:
    sys.path.insert(0, str(GAVIS_CORE))

from unified_indexer import get_indexer, ALL_COLLECTIONS

# --- 基础配置 ---
ROOT = Path(__file__).resolve().parent.parent
BASE_DATA_DIR = ROOT / "data_out"
LOG_FILE = BASE_DATA_DIR / "logs" / "ingest_to_vectordb.jsonl"

# --- 数据源到 Collection 和匹配模式的配置映射 ---
# data_source_key -> { "coll": target_collection_name, "path_sub": 目录子路径, "file_glob": 匹配模式 }
SOURCE_CONFIG = {
    "landchina":      {"coll": "dds_land_parcels", "sub": "landchina",      "glob": "normalized*_normalized.json"},
    "hangzhou":       {"coll": "dds_land_parcels", "sub": "hangzhou",       "glob": "normalized*_normalized.json"},
    "sanya":          {"coll": "dds_land_parcels", "sub": "sanya",          "glob": "normalized*_normalized.json"},
    "hangzhou_deals": {"coll": "dds_transactions", "sub": "hangzhou_deals", "glob": "normalized*_normalized.json"},
    "sanya_deals":    {"coll": "dds_transactions", "sub": "sanya_deals",    "glob": "normalized*_normalized.json"},
    "pdfs":           {"coll": "dds_pdf_extracts", "sub": "pdfs",           "glob": "parsed*_parsed.json"}
}

# --- 向量化文本模板 (按 Collection) ---
TEXT_TEMPLATES = {
    "land_parcels": "{city}{district}土地出让，用途{land_use}，面积{area_sqm}㎡，容积率{floor_area_ratio}，起拍价{starting_price_cny}元，发布日期{published_date}，{announcement_type}",

    "transactions": "{city}{district}{project_name}，{room_type}户型，建筑面积{area_sqm}㎡，成交单价{unit_price_cny}元/㎡，成交总价{total_price_cny}元，楼层{floor}，签约日期{deal_date}",

    "pdf_extracts": "{city}{district}地块规划条件，容积率{floor_area_ratio}，限高{building_height_m}米，绿地率{green_ratio}%，起拍价{starting_price_cny}元，出让年限{supply_years}年，政策标签：{policy_tags}，约束：{constraints}",

    "gis_context": "{city}{district}地块GIS画像，最近地铁{gis_nearest_subway}距{gis_nearest_subway_m}米，最近学校{gis_nearest_school}距{gis_nearest_school_m}米，通勤{gis_commute_summary}"
}


def log_progress(msg_dict):
    """写入进度记录到 jsonl"""
    msg_dict["@timestamp"] = datetime.datetime.now().isoformat()
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(msg_dict, ensure_ascii=False) + "\n")


def clean_and_format_text(template, item):
    """按照 prompt 要求：None 转为空字符串拼接文本"""
    data = defaultdict(str)
    for key, val in item.items():
        if val is None:
            data[key] = ""
        else:
            data[key] = str(val)
    return template.format_map(data)


def build_metadata(item, source_key):
    """提炼元数据"""
    meta_spec = ["source", "category", "data_level", "confidence", "city",
                 "province", "district", "date", "year", "month",
                 "parcel_id", "gcs_uri", "scraped_at"]
    metadata = {}
    for k in meta_spec:
        val = item.get(k, "")
        if val is None:
            val = ""
        if k in ["data_level", "year", "month"]:
            try:
                metadata[k] = int(val) if val != "" else 0
            except Exception:
                metadata[k] = 0
        else:
            metadata[k] = str(val)
    if not metadata.get("source"):
        metadata["source"] = source_key
    return metadata


def process_source_files(source_key, config, idx, batch_size=50):
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

    spec = ALL_COLLECTIONS[target_coll_name]
    template = TEXT_TEMPLATES.get(target_coll_name, "")

    items_to_add = []
    processed_files = 0

    for fpath in files:
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = json.load(f)

            rows = []
            if isinstance(content, list):
                rows = content
            elif isinstance(content, dict):
                if "data" in content and isinstance(content["data"], list):
                    rows = content["data"]
                else:
                    rows = [content]

            for item in rows:
                s_at = item.get("scraped_at", "")
                if not s_at:
                    s_at = datetime.datetime.now().strftime("%Y-%m-%d")
                s_date = s_at[:10]
                p_id = item.get("parcel_id", "")
                if not p_id:
                    import uuid
                    p_id = f"gen-{uuid.uuid4().hex[:8]}"

                doc_id = f"{source_key}_{p_id}_{s_date}"
                doc_text = clean_and_format_text(template, item)
                metadata = build_metadata(item, source_key)

                items_to_add.append({
                    "id": doc_id,
                    "text": doc_text,
                    "metadata": metadata,
                })
            processed_files += 1

        except Exception as e:
            print(f"[!] 读取文件 {os.path.basename(fpath)} 失败: {e}")
            continue

    total_incoming = len(items_to_add)
    print(f"[+] 数据转换完成。总行数: {total_incoming}，准备分批写入...")

    # 批量生成向量并写入
    success_in = 0
    for i in range(0, total_incoming, batch_size):
        batch = items_to_add[i: i + batch_size]
        texts = [b["text"] for b in batch]

        try:
            embeddings = idx.embed_text(texts, dim=spec.embedding_dim)
        except Exception as e:
            print(f"    [✕] Embedding 生成失败: {e}")
            continue

        records = []
        for b, emb in zip(batch, embeddings):
            r = {
                "id": b["id"],
                "vector": emb,
                "text": b["text"],
                "source_file": b["metadata"].get("source", ""),
                "city": b["metadata"].get("city", ""),
                "district": b["metadata"].get("district", ""),
                "indexed_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            records.append(r)

        try:
            tbl = idx._ensure_table(spec, records[0])
            if tbl is not None:
                # 对已有 ID 做 upsert
                existing_ids = {b["id"] for b in batch}
                try:
                    id_list = ", ".join(f"'{eid}'" for eid in existing_ids)
                    tbl.delete(f"id IN ({id_list})")
                except Exception:
                    pass
                tbl.add(records)
                success_in += len(batch)
                print(f"    -> 已写入批次: [{min(i + batch_size, total_incoming)} / {total_incoming}]")
        except Exception as e:
            print(f"    [✕] 写入批次出错: {e}")

    final_count = idx.count(target_coll_name)
    summary_str = f"{target_coll_name}: 写入 {success_in} docs, total={final_count}"
    print(f"\n[✓] 数据源 {source_key} 执行完毕: {summary_str}")

    log_progress({
        "action": "ingest",
        "source": source_key,
        "collection": target_coll_name,
        "files_scanned": len(files),
        "processed_files": processed_files,
        "attempted_records": total_incoming,
        "written_records": success_in,
        "total_count": final_count,
    })

    return success_in


def main():
    parser = argparse.ArgumentParser(description="DDS 数据中心向量入库管道 (LanceDB)")
    parser.add_argument("--source", type=str, choices=list(SOURCE_CONFIG.keys()),
                        help="指定单一数据源，留空则全量扫描")
    parser.add_argument("--batch-size", type=int, default=50, help="每批次写入条数 (默认50)")

    args = parser.parse_args()

    print("=== DDS 向量化入库启动 (LanceDB) ===")

    idx = get_indexer()
    print(f"[*] LanceDB = {idx.persist_dir}")

    sources_to_run = [args.source] if args.source else list(SOURCE_CONFIG.keys())
    print(f"[*] 模式: {'指定数据源' if args.source else '全库扫描'}，待检查数据源: {len(sources_to_run)} 个")

    overall_added = 0
    for skey in sources_to_run:
        conf = SOURCE_CONFIG[skey]
        added = process_source_files(skey, conf, idx, args.batch_size)
        overall_added += added

    print("\n==========================================")
    print(f"🎉 全局入库任务结束。本次净新增: {overall_added} 条记录。")
    print(f"📊 详细记录详见: {LOG_FILE}")
    print("==========================================\n")


if __name__ == "__main__":
    main()
