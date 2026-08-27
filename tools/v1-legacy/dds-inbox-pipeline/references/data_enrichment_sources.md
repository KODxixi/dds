# 外部数据源与 API 约束

## 概述

Phase 4（数据补齐）阶段需调用外部 API 补齐缺失字段。本文档定义了可用的数据源、API 约束和安全规则。

## 数据源一览

| 数据源 | 用途 | 协议 | QPS 限制 | Key 位置 |
|:---|:---|:---|:---|:---|
| 高德地理编码 | 地址→坐标 | REST/JSON | **1 QPS** | `.env` → `AMAP_KEY` |
| 高德逆地理编码 | 坐标→行政区 | REST/JSON | 1 QPS | 同上 |
| 高德行政区划 | 获取城市边界 | REST/JSON | 1 QPS | 同上 |
| DDS Vault | 楼盘数据库 | 本地文件 | 无限制 | `Vault/2026新楼盘/` |
| DDS query_local | DuckDB 查询 | Python API | 无限制 | `scripts/query_local.py` |

## 高德 API 详细约束

### 地理编码 API
- **端点**：`https://restapi.amap.com/v3/geocode/geo`
- **参数**：`address={地址}&city={城市}&key={AMAP_KEY}`
- **QPS 限制**：**严格 1 QPS**（超限返回 `CUQPS_HAS_EXCEEDED_THE_LIMIT`）
- **脚本延迟**：每次请求后 `time.sleep(1.5)` （留 0.5s 余量）
- **单次会话上限**：≤ 200 次请求
- **错误处理**：
  - `CUQPS_HAS_EXCEEDED_THE_LIMIT` → 等待 5 秒后重试，最多 3 次
  - `ENGINE_RESPONSE_DATA_ERROR` → 跳过该地址，标记为"编码失败"
  - HTTP 非 200 → 停止批量请求，报告错误

### 逆地理编码 API
- **端点**：`https://restapi.amap.com/v3/geocode/regeo`
- **参数**：`location={lng},{lat}&key={AMAP_KEY}`
- **用途**：从坐标反查行政区划（区/县），用于填充 `区域名称` 字段

### 行政区划 API
- **端点**：`https://restapi.amap.com/v3/config/district`
- **参数**：`keywords={城市名}&subdistrict=0&key={AMAP_KEY}`
- **用途**：获取城市边界多边形（`polyline` 字段），用于注册新城市的 BBox
- **BBox 提取**：从 polyline 点集计算 min/max 经纬度

## DDS Vault 操作约束

### 读取
```python
# 推荐方式：通过 query_local.py
from scripts.query_local import query_city
df = query_city("襄阳", year=2026)
```

### 写入（非破坏性 upsert）
1. **备份**：`cp 新楼盘-{城市}.csv 新楼盘-{城市}_backup_{timestamp}.csv`
2. **合并策略**：按 `楼盘名称` 精确匹配
   - 已有记录：仅更新空字段或用户确认的覆盖字段
   - 新记录：追加到文件末尾
3. **重建 Parquet**：
   ```python
   import pandas as pd
   df = pd.read_csv(csv_path, encoding='utf-8-sig')
   df.to_parquet(parquet_path, index=False)
   ```
4. **编码**：CSV 统一使用 `utf-8-sig`（BOM 头，兼容 Excel）

### Vault 目录结构
```
Vault/
├── 2026新楼盘/
│   ├── _cities_index.csv          # 城市索引
│   ├── 新楼盘-{城市}.csv          # 主数据（中文列名）
│   ├── 新楼盘-{城市}.parquet      # 加速缓存
│   └── 新楼盘-{城市}_imputed.csv  # MICE 填充版（部分城市）
└── _manifest.csv                  # 全量清单
```

### 命名规范
- 文件名中的城市须与 `_cities_index.csv` 中的 `城市名称` 完全一致
- 不要用简称（如"襄"代替"襄阳"）

## 城市 BBox 注册

当首次处理一个新城市（`CITY_BBOXES` 中不存在）时：

1. 调用高德行政区划 API 获取城市边界 polyline
2. 计算 (min_lng, min_lat, max_lng, max_lat) 四元组
3. 添加到以下两个文件的 `CITY_BBOXES` 字典中：
   - `scripts/governance/quality_gates.py`
   - `scripts/governance/t7_clean_pipeline.py`
4. 提交代码变更

## 安全规则

- **不在代码中硬编码 API Key**：从 `.env` 文件或环境变量读取
- **不将 Vault 数据提交到 Git**：`Vault/` 在 `.gitignore` 中
- **API Key 轮换**：如遇持续 QPS 限制，提示用户更换 Key
- **请求日志**：批量 API 请求时记录每次请求的时间戳和结果，便于审计
