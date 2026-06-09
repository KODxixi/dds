"""二分排查3 - 模型加载后加 chromadb import"""
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse, csv, re, sys
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModel

ROOT = Path(__file__).resolve().parent
MODEL_CACHE = str(ROOT / "models" / "bge-m3")

print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("Loading model...")
model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("Loaded!")

print("Importing chromadb...")
import chromadb
print("chromadb imported OK")

import time
time.sleep(0.5)
print("Post-import sleep done")

def encode(texts):
    tokens = tokenizer(texts, padding=True, truncation=True, max_length=512, return_tensors="pt")
    with torch.no_grad():
        out = model(**tokens)
    attn = tokens["attention_mask"].unsqueeze(-1).float()
    emb = (out.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
    return torch.nn.functional.normalize(emb, p=2, dim=1)

v = encode(["三亚测试"])
print(f"Dim: {v.shape[1]}")
print("OK")
