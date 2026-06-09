# DDS GCS 初始化 — 后台上传脚本
# 运行：.\scripts\run_gcs_init.ps1
$ErrorActionPreference = "Continue"
$b = "gs://dds-data-lake"
$base = "C:\Users\shiguanyu\DDS\data_out\_gcs_init"
$log = "C:\Users\shiguanyu\DDS\data_out\gcs_init.log"

function Log($msg) {
    $ts = Get-Date -Format "HH:mm:ss"
    "$ts $msg" | Tee-Object -FilePath $log -Append
}

Log "=== DDS GCS 初始化开始 ==="

# 1. 批量上传占位文件
Log "[1/3] 上传目录骨架..."
gsutil -m cp -r "$base\*" "$b/" 2>&1 | ForEach-Object { Log $_ }

# 2. 上传 CATALOG.md
Log "[2/3] 上传 CATALOG.md..."
$catalog = @"
# DDS Data Lake — CATALOG

bucket: dds-data-lake
region: asia-east1
created: $(Get-Date -Format "yyyy-MM-ddTHH:mm:ss+08:00")

## 目录结构

gs://dds-data-lake/
├── real_estate_data/
│   ├── landchina/          # 全国土地出让
│   │   ├── raw/
│   │   ├── normalized/
│   │   └── logs/
│   ├── provincial/
│   │   ├── hangzhou/       # 杭州公共资源交易中心
│   │   └── sanya/          # 海南/三亚公共资源平台
│   ├── client_provided/    # 甲方提供数据
│   ├── social_sentiment/   # 社媒舆情（三级数据）
│   └── gis/                # GIS/地图数据
└── shared/
    ├── schemas/             # 字段标准
    ├── manifests/           # 抓取清单
    └── index.json

## 数据分级
| 级别 | 来源           | 信任度   |
|------|----------------|----------|
| 一级 | 政府/招拍挂    | 最高     |
| 二级 | GIS/地图 API   | 高       |
| 三级 | 社媒舆情       | 需人工校准 |
"@
$catalogPath = "$env:TEMP\CATALOG.md"
$catalog | Out-File $catalogPath -Encoding utf8
gsutil cp $catalogPath "$b/CATALOG.md" 2>&1 | ForEach-Object { Log $_ }

# 3. 上传 schema
Log "[3/3] 上传 schema..."
$schema = @'
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "land_parcel_normalized",
  "description": "DDS 土地地块归一化字段标准 v1.1",
  "type": "object",
  "required": ["source", "category", "scraped_at", "title"],
  "properties": {
    "source":            { "type": "string"  },
    "category":          { "type": "string"  },
    "scraped_at":        { "type": "string", "format": "date-time" },
    "published_date":    { "type": ["string","null"] },
    "province":          { "type": ["string","null"] },
    "city":              { "type": ["string","null"] },
    "district":          { "type": ["string","null"] },
    "region_code":       { "type": ["string","null"] },
    "region_full_name":  { "type": ["string","null"] },
    "title":             { "type": "string"  },
    "parcel_id":         { "type": ["string","null"] },
    "detail_url":        { "type": ["string","null"] },
    "announcement_type": { "type": ["string","null"] },
    "land_use":          { "type": ["string","null"] },
    "area_sqm":          { "type": ["number","null"] },
    "starting_price":    { "type": ["number","null"] },
    "deal_price":        { "type": ["number","null"] },
    "floor_area_ratio":  { "type": ["number","null"] },
    "building_height":   { "type": ["number","null"] },
    "lng":               { "type": ["number","null"] },
    "lat":               { "type": ["number","null"] },
    "confidence":        { "type": ["string","null"], "enum": ["high","medium","low",null] },
    "raw_record":        { "type": ["object","null"] }
  }
}
'@
$schemaPath = "$env:TEMP\land_parcel_normalized.schema.json"
$schema | Out-File $schemaPath -Encoding utf8
gsutil cp $schemaPath "$b/shared/schemas/land_parcel_normalized.schema.json" 2>&1 | ForEach-Object { Log $_ }

Log "=== 完成 ==="
Log "验证："
gsutil ls "$b/" 2>&1 | ForEach-Object { Log $_ }
