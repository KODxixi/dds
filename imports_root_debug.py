"""最小复现：模拟 ingest 脚本的 import 顺序"""
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModel

print("All imports done")

ROOT = Path(__file__).resolve().parent
MODEL_CACHE = str(ROOT / "models" / "bge-m3")

print("Loading model...")
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
model.eval()
print("Model loaded!")

def encode(texts):
    tokens = tokenizer(
        list(texts), padding=True, truncation=True,
        max_length=512, return_tensors="pt"
    )
    with torch.no_grad():
        out = model(**tokens)
    attn = tokens["attention_mask"].unsqueeze(-1).float()
    emb = (out.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
    return torch.nn.functional.normalize(emb, p=2, dim=1)

v = encode(["测试"])
print(f"Dim: {v.shape[1]}")
print("OK")
