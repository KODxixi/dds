"""测试：用裸 transformers (不用 sentence-transformers) 生成 embedding"""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from transformers import AutoTokenizer, AutoModel
import torch

CACHE = r"C:\Users\shiguanyu\DDS\models\bge-m3"

print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=CACHE)
print("Loading model...")
model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=CACHE)
print("Loaded!")

def encode(texts):
    tokens = tokenizer(
        texts, padding=True, truncation=True, max_length=512, return_tensors="pt"
    )
    with torch.no_grad():
        out = model(**tokens)
    attn = tokens["attention_mask"].unsqueeze(-1).float()
    emb = (out.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
    return torch.nn.functional.normalize(emb, p=2, dim=1)

# Test
test_texts = ["三亚海棠区低密商墅 稀缺岸线独栋别墅", "杭州市中心三室公寓 刚需盘"]
v = encode(test_texts)
print(f"Embedding shape: {v.shape}")  # should be (2, 1024)

# Cosine similarity
sim = torch.mm(v, v.t())
print(f"Similarity matrix:\n{sim}")

# Also load chromadb and upsert
print("\nConnecting ChromaDB...")
import chromadb
client = chromadb.PersistentClient(path=r"C:\Users\shiguanyu\DDS\vectordb")
coll = client.get_or_create_collection(
    name="competitor_projects", metadata={"hnsw:space": "cosine"}
)
print(f"Collection count: {coll.count()}")

if coll.count() == 0:
    # Insert test data
    print("Inserting test docs...")
    emb_list = v.tolist()
    coll.upsert(
        ids=["test_villa", "test_apartment"],
        embeddings=emb_list,
        documents=test_texts,
        metadatas=[
            {"city": "三亚", "year": 2026, "project_name": "test_villa"},
            {"city": "杭州", "year": 2026, "project_name": "test_apartment"},
        ],
    )
    print(f"Count after upsert: {coll.count()}")

# Query
print("\nQuery: 三亚低密商墅...")
q = encode(["三亚低密商墅 高端 稀缺"])
q_list = q.tolist()
res = coll.query(query_embeddings=q_list, n_results=2)
for d, dist in zip(
    res.get("documents", [[]])[0], res.get("distances", [[]])[0]
):
    print(f"  [{dist:.4f}] {d[:100]}")

print("\nDONE! 裸 transformers 方案一切正常")
