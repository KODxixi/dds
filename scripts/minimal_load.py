import os
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import torch
from transformers import AutoTokenizer, AutoModel

CACHE = r"C:\Users\shiguanyu\DDS\models\bge-m3"

print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=CACHE)
print("Loading model...")
model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=CACHE)
print("Loaded!")
