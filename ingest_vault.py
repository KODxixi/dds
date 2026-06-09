"""
DDS Vault → ChromaDB 语义向量入库（AutoModel + mean pooling）
运行: python ingest_vault.py [--year 2026] [--city 三亚]
"""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import argparse
import csv
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModel

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "Vault"
CHROMA_PATH = str(ROOT / "vectordb")
MODEL_CACHE = str(ROOT / "models" / "bge-m3")
CITIES = ["三亚", "杭州", "上海", "青岛"]

DOC_TEMPLATE = (
    "{城市名称}{区域名称}{子区域名称}的{楼盘名称}，"
    "地址{地址}，"
    "物业类型{物业类型}，建筑类型{建筑类型}，"
    "户型{户型文本描述}，面积{面积范围}，"
    "最新价格{最新价格}元/㎡，参考价格{参考价格}元/㎡，"
    "开发商{开发商}{开发商品牌}，"
    "容积率{容积率}，绿化率{绿化率}，"
    "装修{装修情况}，物业费{物业管理费}，"
    "标签{标签列表}"
)


def clean_val(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("nan", "None", "", "[]", "{}") else s


def read_rows(year_dir_name, city):
    year_dir = VAULT / year_dir_name
    if not year_dir.exists():
        return []
    # 优先 CSV（避免 pyarrow C 扩展与 torch libomp 冲突导致 SIGSEGV）
    csv_path = year_dir / f"{city}.csv"
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    # 没有 CSV 才 fallback parquet
    pq = year_dir / f"{city}.parquet"
    if pq.exists():
        import pandas as pd
        return pd.read_parquet(pq).to_dict("records")
    return []


def main():
    parser = argparse.ArgumentParser(description="Vault → ChromaDB 语义入库")
    parser.add_argument("--year", help="年份，不传则全部")
    parser.add_argument("--city", help="城市，默认全部")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.year:
        year_dirs = [f"{args.year}年"]
    else:
        year_dirs = sorted(
            [d.name for d in VAULT.iterdir()
             if d.is_dir() and re.match(r"\d{4}年", d.name)],
            reverse=True,
        )
    cities = [args.city] if args.city else CITIES
    year_dirs = [y for y in year_dirs if (VAULT / y).exists()]

    if args.dry_run:
        total = 0
        for yd in year_dirs:
            for c in cities:
                rows = read_rows(yd, c)
                total += len(rows)
                print(f"  {yd}/{c}: {len(rows)} rows")
        print(f"\nTotal: {total}")
        return

    # ---- 加载模型 ----
    print("[*] Loading BGE-M3...")
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
    model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
    print("[+] Model ready")

    def encode(texts):
        tokens = tokenizer(
            list(texts), padding=True, truncation=True,
            max_length=512, return_tensors="pt",
        )
        with torch.no_grad():
            out = model(**tokens)
        attn = tokens["attention_mask"].unsqueeze(-1).float()
        emb = (out.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
        return torch.nn.functional.normalize(emb, p=2, dim=1)

    # ---- ChromaDB ----
    print("[*] 连接 ChromaDB...")
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    coll = client.get_or_create_collection(
        name="competitor_projects", metadata={"hnsw:space": "cosine"},
    )
    initial = coll.count()
    print(f"[*] competitor_projects 现有 {initial} 条，开始入库...")

    # ---- 入库 ----
    for yd in year_dirs:
        for city in cities:
            rows = read_rows(yd, city)
            if not rows:
                continue
            year_num = yd[:4]
            ids, docs, metas = [], [], []
            for row in rows:
                safe = defaultdict(str)
                for k, v in row.items():
                    cv = clean_val(v)
                    if cv:
                        safe[k] = cv
                doc_text = re.sub(r"\s+", " ", DOC_TEMPLATE.format_map(safe))
                city_name = safe.get("城市名称", city)
                parcel_id = safe.get("楼盘ID", f"gen-{len(ids)}")
                ids.append(f"vault_{year_num}_{city_name}_{parcel_id}")
                docs.append(doc_text)
                metas.append({
                    "year": int(year_num) if year_num.isdigit() else 0,
                    "city": city_name,
                    "district": safe.get("区域名称", ""),
                    "project_name": safe.get("楼盘名称", ""),
                    "developer": safe.get("开发商", ""),
                    "property_type": safe.get("物业类型", ""),
                    "unit_price": int(m.group(1)) if (m := re.search(r"(\d{3,})", safe.get("最新价格", ""))) else 0,
                    "source": "vault",
                })

            added = 0
            for i in range(0, len(ids), args.batch_size):
                batch_ids = ids[i:i + args.batch_size]
                batch_docs = docs[i:i + args.batch_size]
                batch_metas = metas[i:i + args.batch_size]
                try:
                    batch_emb = encode(batch_docs).tolist()
                    coll.upsert(
                        ids=batch_ids, embeddings=batch_emb,
                        documents=batch_docs, metadatas=batch_metas,
                    )
                    added += len(batch_ids)
                except Exception as e:
                    print(f"  [!] {yd}/{city} batch {i}: {e}")
            print(f"  {yd}/{city}: {added} rows")

    final = coll.count()
    print(f"\n[✓] 完成。competitor_projects: {initial} → {final} (净增 {final - initial})")


if __name__ == "__main__":
    main()
