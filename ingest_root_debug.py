"""从 DDS 根目录运行的简化版 ingest——排查 scripts/ 子目录 crash 问题"""
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse, csv, re, sys
from collections import defaultdict
from pathlib import Path

import torch

from transformers import AutoTokenizer, AutoModel

# ⚠ chromadb 必须在模型加载之后导入

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "Vault"
CHROMA_PATH = str(ROOT / "vectordb")
MODEL_CACHE = str(ROOT / "models" / "bge-m3")

print("[*] 加载 tokenizer...")
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("[*] 加载 model...")
model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("[*] model loaded!")

def encode(texts):
    tokens = tokenizer(list(texts), padding=True, truncation=True, max_length=512, return_tensors="pt")
    with torch.no_grad():
        out = model(**tokens)
    attn = tokens["attention_mask"].unsqueeze(-1).float()
    emb = (out.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
    return torch.nn.functional.normalize(emb, p=2, dim=1)

print("[*] 连接 ChromaDB...")
import chromadb
client = chromadb.PersistentClient(path=CHROMA_PATH)
coll = client.get_or_create_collection(name="competitor_projects", metadata={"hnsw:space": "cosine"})
print(f"[*] competitor_projects count={coll.count()}")

# 读2026三亚
print("[*] 读取 2026/三亚...")
import pandas as pd
df = pd.read_parquet(VAULT / "2026年" / "三亚.parquet")
rows = df.to_dict("records")
print(f"[*] {len(rows)} rows")

# Encode + upsert 5 rows
texts = []
for row in rows[:5]:
    safe = defaultdict(str)
    for k, v in row.items():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        s = str(v).strip()
        if s not in ("nan", "None", "", "[]", "{}"):
            safe[k] = s
    text = f'{safe["城市名称"]}{safe["区域名称"]}的{safe["楼盘名称"]}，户型{safe["户型文本描述"]}，价格{safe["最新价格"]}'
    texts.append(re.sub(r"\s+", " ", text))

print(f"[*] encoding {len(texts)} docs...")
emb = encode(texts)
print(f"[*] encoded, shape={emb.shape}")

ids = [f"root_test_{i}" for i in range(len(texts))]
metas = [{"city": "三亚", "year": 2026} for _ in texts]
coll.upsert(ids=ids, embeddings=emb.tolist(), documents=texts, metadatas=metas)
print(f"[*] upserted, count={coll.count()}")

# Query
print("[*] query...")
q_emb = encode(["三亚海棠区低密商墅"])
res = coll.query(query_embeddings=q_emb.tolist(), n_results=3)
for d in res.get("documents", [[]])[0]:
    print(f"  -> {d[:150]}")
print("DONE")
