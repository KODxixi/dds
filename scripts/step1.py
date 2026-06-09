"""Step 1: add all imports"""
import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import argparse, csv, re, sys
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModel

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "Vault"
CHROMA_PATH = str(ROOT / "vectordb")
MODEL_CACHE = str(ROOT / "models" / "bge-m3")
CITIES = ["三亚", "杭州", "上海", "青岛"]

print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("Loading model...")
model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("Loaded!")
