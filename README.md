# DDS · Decision Data System

地产数据决策引擎 — 输入地块坐标，输出 Bauhaus 风格商业决策书。

## 架构

```
用户输入（地址/坐标 + 预期均价 + 产品定位）
  ├─ Flask API (app.py)
  ├─ DuckDB 本地楼盘库 (CSV → SQL)
  ├─ 高德 GIS（地理编码 + POI 配套）
  ├─ DeepSeek AI（竞品策略 + 风险推演 + 决策建议）
  └─ 前端渲染（Bauhaus 风格 + Loca 3D 地图）
```

## 启动

```bash
# 1. 安装依赖
pip install duckdb pandas flask python-dotenv anthropic tabulate

# 2. 配置 .env（复制 .env.example 并填入 Key）
cp .env.example .env

# 3. 启动
python app.py
# → http://localhost:8080
```

## 使用

**Web 界面**：打开 `http://localhost:8080`，选择城市，输入地址或坐标，提交即可。

**CLI**：
```bash
python scripts/report_parcel.py --city 三亚 --address "三亚海棠区南田路16号" --expected-price 35000
python scripts/report_parcel.py --city 三亚 --lng 109.71899 --lat 18.41040 --district 海棠区
```

## 数据源

| 来源 | 信任层级 | 说明 |
|------|---------|------|
| 安居客 CSV（本地库） | L2 高信任 | DuckDB 直读，4 城 2480+ 条在售/尾盘记录 |
| 高德 POI（实时） | L2 高信任 | 学校、医院、地铁、商场、公园配套 |
| 房天下（实时抓取） | L3 辅助 | 新房列表交叉验证 |
| DeepSeek AI | L3 辅助 | 竞品策略解读、风险推演 |

## 支持城市

三亚 / 杭州 / 上海 / 青岛（可扩展新城市 CSV）

## 技术栈

Python 3.13 + Flask + DuckDB + 高德 JS API v2.0 + Loca v2 + DeepSeek
