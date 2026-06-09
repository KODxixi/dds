"""
把 Vault/{year}年/{city}.csv (或 .parquet) 的楼盘数据灌入 ChromaDB competitor_projects 集合。
优先读 parquet（列式快），fallback 到 csv。

用法: python scripts/ingest_vault_to_vectordb.py [--year 2026] [--city 三亚]
"""
import os
import sys

# ⚠ 必须在导入 transformers 之前设置离线模式
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

from transformers import AutoTokenizer, AutoModel

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CHROMA_PATH = str(ROOT / "vectordb")
MODEL_CACHE = str(ROOT / "models" / "bge-m3")
CITIES = ["三亚", "杭州", "上海", "青岛"]

# ---- 文档模板：拼接所有可用的文本字段供 embedding ----
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
    """None / nan / 空列表都转空字符串"""
    if v is None:
        return ""
    s = str(v).strip()
    if s in ("nan", "None", "", "[]", "{}"):
        return ""
    return s


def build_document(row: dict) -> str:
    """把一行 CSV 拼成中文描述文本"""
    safe = defaultdict(str)
    for k, v in row.items():
        safe[k] = clean_val(v)
    text = DOC_TEMPLATE.format_map(safe)
    return re.sub(r"\s+", " ", text)


def build_metadata(row: dict, year: str, source: str) -> dict:
    """提取结构化元数据（方便后续过滤）"""
    meta = {
        "year": int(year) if year.isdigit() else 0,
        "city": clean_val(row.get("城市名称", "")),
        "district": clean_val(row.get("区域名称", "")),
        "project_name": clean_val(row.get("楼盘名称", "")),
        "developer": clean_val(row.get("开发商", "")),
        "property_type": clean_val(row.get("物业类型", "")),
        "source": source,
    }
    price_str = clean_val(row.get("最新价格", ""))
    m = re.search(r"(\d{3,})", price_str)
    meta["unit_price"] = int(m.group(1)) if m else 0
    return meta


def read_rows(year_dir_name: str, city: str):
    """读取某年份某城市的楼盘数据，优先 parquet"""
    year_dir = VAULT / year_dir_name
    if not year_dir.exists():
        return []

    pq = year_dir / f"{city}.parquet"
    if pq.exists():
        import pandas as pd
        df = pd.read_parquet(pq)
        return df.to_dict("records")

    csv_path = year_dir / f"{city}.csv"
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    return []


def main():
    parser = argparse.ArgumentParser(description="Vault 楼盘数据 → ChromaDB 语义向量入库")
    parser.add_argument("--year", help="指定年份，不传则扫描全部 {year}年 目录")
    parser.add_argument("--city", help="指定城市，默认全部四城")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不入库")
    args = parser.parse_args()

    if args.year:
        year_dirs = [f"{args.year}年"]
    else:
        year_dirs = sorted(
            [d.name for d in VAULT.iterdir() if d.is_dir() and re.match(r"\d{4}年", d.name)],
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
        print(f"\nTotal: {total} rows across {len(year_dirs)} years x {len(cities)} cities")
        return

    # ---- 加载模型（AutoModel + mean pooling，避免 SentenceTransformer 的 SIGSEGV）----
    print("[*] 加载 tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
    print("[*] 加载 model...")
    model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
    print("[*] model loaded, eval mode...")
    # model.eval()  # 先注释掉排查

    def encode(texts):
        import torch
        tokens = tokenizer(
            list(texts), padding=True, truncation=True,
            max_length=512, return_tensors="pt"
        )
        with torch.no_grad():
            out = model(**tokens)
        attn = tokens["attention_mask"].unsqueeze(-1).float()
        emb = (out.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
        return torch.nn.functional.normalize(emb, p=2, dim=1)

    print("[+] 模型就绪")

    # ---- 连接 ChromaDB ----
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    coll = client.get_or_create_collection(
        name="competitor_projects",
        metadata={"hnsw:space": "cosine"},
    )
    initial = coll.count()
    print(f"[*] competitor_projects 现有 {initial} 条，开始入库...")

    # ---- 逐城市逐年扫描入库 ----
    for yd in year_dirs:
        for city in cities:
            rows = read_rows(yd, city)
            if not rows:
                continue

            ids, docs, metas = [], [], []
            year_num = yd[:4]

            for row in rows:
                doc_text = build_document(row)
                city_name = clean_val(row.get("城市名称", city))
                parcel_id = clean_val(row.get("楼盘ID", ""))
                doc_id = f"vault_{year_num}_{city_name}_{parcel_id}"
                ids.append(doc_id)
                docs.append(doc_text)
                metas.append(build_metadata(row, year_num, "vault"))

            added = 0
            for i in range(0, len(ids), args.batch_size):
                batch_ids = ids[i:i + args.batch_size]
                batch_docs = docs[i:i + args.batch_size]
                batch_metas = metas[i:i + args.batch_size]
                try:
                    batch_emb = encode(batch_docs).tolist()
                    coll.upsert(
                        ids=batch_ids,
                        embeddings=batch_emb,
                        documents=batch_docs,
                        metadatas=batch_metas,
                    )
                    added += len(batch_ids)
                except Exception as e:
                    print(f"  [!] {yd}/{city} batch {i}: {e}")

            print(f"  {yd}/{city}: {added} rows")

    final = coll.count()
    print(f"\n[✓] 完成。competitor_projects: {initial} → {final} (净增 {final - initial})")


if __name__ == "__main__":
    main()
