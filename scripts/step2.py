"""Step 2: add function definitions"""
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
    pq = year_dir / f"{city}.parquet"
    if pq.exists():
        import pandas as pd
        return pd.read_parquet(pq).to_dict("records")
    csv_path = year_dir / f"{city}.csv"
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    return []


print("Loading tokenizer...")
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("Loading model...")
model = AutoModel.from_pretrained("BAAI/bge-m3", cache_dir=MODEL_CACHE)
print("Loaded!")
