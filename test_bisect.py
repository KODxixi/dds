"""逐步排查 - 从成功的 test_auto_model 开始逐步添加特征"""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# 添加特征1: 额外的 imports
import argparse, csv, re, sys
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModel

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

v = encode(["三亚测试"])
print(f"Dim: {v.shape[1]}")
print("OK")
