"""
DDS GCS 数据湖初始化脚本
运行一次即可建好完整桶结构
用法：python setup_gcs.py [--bucket dds-data-lake] [--project YOUR_PROJECT_ID]
"""
import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))


CATALOG = """\
# DDS Data Lake — CATALOG

bucket: {bucket}
region: asia-east1
created: {ts}
owner: DDS Project

## 目录结构

```
gs://{bucket}/
├── real_estate_data/
│   ├── landchina/          # 全国土地出让（LandChina）
│   │   ├── raw/            # 原始 API 响应（按 YYYY/MM/DD 分区）
│   │   ├── normalized/     # 归一化 JSON（同上分区）
│   │   └── logs/           # 抓取日志
│   ├── provincial/         # 省市级平台
│   │   ├── hangzhou/       # 杭州公共资源交易中心
│   │   │   ├── raw/
│   │   │   └── normalized/
│   │   └── sanya/          # 海南省/三亚公共资源平台
│   │       ├── raw/
│   │       └── normalized/
│   ├── client_provided/    # 甲方提供数据（人工导入）
│   │   └── YYYY/MM/        # 按时间归档
│   ├── social_sentiment/   # 小红书/抖音舆情（三级数据）
│   └── gis/                # GIS 坐标、行政区边界
│
└── shared/
    ├── schemas/             # 字段标准定义
    ├── manifests/           # 每次抓取的索引清单
    └── index.json           # 全局数据索引
```

## 数据分级

| 级别 | 来源 | 信任度 |
|------|------|--------|
| 一级 | 政府红头文件、招拍挂、网签 | 最高 |
| 二级 | GIS、地图 API、气象局 | 高 |
| 三级 | 社媒舆情（需人工校准） | 需校准 |

## 字段规范

所有 normalized 文件必须包含：
- source / category / scraped_at
- province / city / district
- published_date / title / parcel_id
- raw_record（原始数据备份）
"""

LAND_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "land_parcel_normalized",
    "description": "DDS 土地地块归一化字段标准 v1.1",
    "type": "object",
    "required": ["source", "category", "scraped_at", "title"],
    "properties": {
        "source":            {"type": "string",  "description": "数据源标识，如 landchina / hangzhou_ctc / hainan_sanya"},
        "category":          {"type": "string",  "description": "数据类别，如 transfer_announcement / land_transfer"},
        "scraped_at":        {"type": "string",  "format": "date-time", "description": "抓取时间 ISO8601"},
        "published_date":    {"type": ["string", "null"], "description": "发布/公告日期"},
        "province":          {"type": ["string", "null"], "description": "省份"},
        "city":              {"type": ["string", "null"], "description": "城市"},
        "district":          {"type": ["string", "null"], "description": "区县"},
        "region_code":       {"type": ["string", "null"], "description": "行政区代码"},
        "region_full_name":  {"type": ["string", "null"], "description": "行政区全称"},
        "title":             {"type": "string",  "description": "公告标题"},
        "parcel_id":         {"type": ["string", "null"], "description": "地块/公告唯一 ID"},
        "detail_url":        {"type": ["string", "null"], "description": "原始详情页 URL"},
        "announcement_type": {"type": ["string", "null"], "description": "公告类型"},
        "land_use":          {"type": ["string", "null"], "description": "土地用途"},
        "area_sqm":          {"type": ["number", "null"], "description": "用地面积（㎡）"},
        "starting_price":    {"type": ["number", "null"], "description": "起拍价（元）"},
        "deal_price":        {"type": ["number", "null"], "description": "成交价（元）"},
        "floor_area_ratio":  {"type": ["number", "null"], "description": "容积率"},
        "coverage_ratio":    {"type": ["number", "null"], "description": "建筑密度"},
        "green_ratio":       {"type": ["number", "null"], "description": "绿地率"},
        "building_height":   {"type": ["number", "null"], "description": "限高（米）"},
        "lng":               {"type": ["number", "null"], "description": "经度"},
        "lat":               {"type": ["number", "null"], "description": "纬度"},
        "confidence":        {"type": ["string", "null"], "enum": ["high", "medium", "low", None], "description": "数据置信度"},
        "raw_record":        {"type": ["object", "null"], "description": "原始记录备份"},
    }
}

INDEX = {
    "version": "1.0",
    "created": "",
    "bucket": "",
    "sources": {
        "landchina":    {"status": "active",  "path": "real_estate_data/landchina/",         "description": "全国土地出让公告"},
        "hangzhou_ctc": {"status": "active",  "path": "real_estate_data/provincial/hangzhou/","description": "杭州公共资源交易中心"},
        "hainan_sanya": {"status": "active",  "path": "real_estate_data/provincial/sanya/",  "description": "海南省/三亚公共资源平台"},
        "client":       {"status": "pending", "path": "real_estate_data/client_provided/",   "description": "甲方提供数据"},
        "sentiment":    {"status": "pending", "path": "real_estate_data/social_sentiment/",  "description": "社媒舆情"},
        "gis":          {"status": "pending", "path": "real_estate_data/gis/",               "description": "GIS 数据"},
    }
}


def gcs(args_list: list[str]) -> None:
    cmd = ["gcloud", "storage"] + args_list
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def upload_text(content: str, uri: str, content_type: str = "text/plain;charset=utf-8") -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = f.name
    gcs(["cp", "--content-type", content_type, tmp, uri])
    Path(tmp).unlink(missing_ok=True)


def upload_json(obj: dict, uri: str) -> None:
    upload_text(json.dumps(obj, ensure_ascii=False, indent=2), uri, "application/json")


def placeholder(uri: str) -> None:
    """在 GCS 创建占位文件以模拟目录"""
    upload_text("", uri)


def main() -> int:
    parser = argparse.ArgumentParser(description="DDS GCS 数据湖初始化")
    parser.add_argument("--bucket",  default="dds-data-lake")
    parser.add_argument("--project", default="", help="GCP 项目 ID（可选，已登录则自动识别）")
    args = parser.parse_args()

    b   = args.bucket
    gs  = f"gs://{b}"
    ts  = datetime.now(TZ).isoformat()

    # ── 1. 创建存储桶 ────────────────────────────────────────────────────
    print(f"\n[1/4] 创建存储桶 {gs} (asia-east1, Standard) …")
    create_args = [
        "buckets", "create", gs,
        "--location=asia-east1",
        "--default-storage-class=STANDARD",
        "--uniform-bucket-level-access",  # 统一权限（推荐）
    ]
    if args.project:
        create_args += [f"--project={args.project}"]
    gcs(create_args)

    # ── 2. 建目录骨架（占位文件）────────────────────────────────────────
    print("\n[2/4] 初始化目录结构 …")
    dirs = [
        "real_estate_data/landchina/raw/.keep",
        "real_estate_data/landchina/normalized/.keep",
        "real_estate_data/landchina/logs/.keep",
        "real_estate_data/provincial/hangzhou/raw/.keep",
        "real_estate_data/provincial/hangzhou/normalized/.keep",
        "real_estate_data/provincial/sanya/raw/.keep",
        "real_estate_data/provincial/sanya/normalized/.keep",
        "real_estate_data/client_provided/.keep",
        "real_estate_data/social_sentiment/.keep",
        "real_estate_data/gis/.keep",
        "shared/manifests/.keep",
    ]
    for d in dirs:
        placeholder(f"{gs}/{d}")

    # ── 3. 上传元数据文件 ────────────────────────────────────────────────
    print("\n[3/4] 上传 CATALOG / schema / index …")
    upload_text(CATALOG.format(bucket=b, ts=ts), f"{gs}/CATALOG.md", "text/markdown")

    schema = LAND_SCHEMA.copy()
    upload_json(schema, f"{gs}/shared/schemas/land_parcel_normalized.schema.json")

    index = INDEX.copy()
    index["created"] = ts
    index["bucket"]  = b
    upload_json(index, f"{gs}/shared/index.json")

    # ── 4. 验证 ──────────────────────────────────────────────────────────
    print("\n[4/4] 验证桶内容 …")
    gcs(["ls", f"{gs}/"])

    print(f"""
╔══════════════════════════════════════════════╗
║  DDS GCS 数据湖初始化完成                   ║
║  bucket : gs://{b:<28} ║
║  region : asia-east1                         ║
╚══════════════════════════════════════════════╝

下一步：
  1. 更新 scraper 的 --bucket 参数：--bucket {b}
  2. LandChina 等 WAF 冷却后重新抓取
  3. 甲方数据手动上传至: {gs}/real_estate_data/client_provided/
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
