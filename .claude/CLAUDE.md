# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

DDS (Data Decision System) 是一个地产数据决策引擎，核心目标：输入任意地块坐标 → 输出动态商业决策书（竞品分析、区位配套、最优户型配比、预期溢价率、风险预警）。系统模拟完整投拓团队：数据清洗 → 政策解读 → 人口市场模拟 → 决策综合，多智能体协作。

完整愿景与逻辑架构见 `DDS_main.md`（"地产界彭博终端 + 自动驾驶仪"）。

## 关键命令

### 环境与依赖

```bash
# 核心依赖（requirements.txt 已覆盖）
pip install -r requirements.txt

# 向量数据库（requirements.txt 未包含，需单独安装）
pip install chromadb sentence-transformers torch

# 数据抓取与 PDF 解析（requirements.txt 未包含，需单独安装）
pip install pdfplumber pdf2image pytesseract anthropic Pillow
```

`requirements.txt` 仅含基础依赖（flask, duckdb, pandas, tabulate, anthropic, pytest）。向量库和 PDF 解析依赖需按需单独安装。

系统级依赖：tesseract（OCR）、poppler（pdf2image 需要）。Dockerfile 已包含这些。

### 统一启动入口（推荐）

**本地 Claude Code 会话（DeepSeek 后端）：**
```
启动.bat
```
或直接运行 PowerShell 脚本：
```powershell
.\start-dds-deepseek.ps1
```
该脚本将 Claude Code 配置为使用 DeepSeek Anthropic-compatible API 后端。

**Web 服务（Flask API）：**
```bash
# 1. 复制并配置 .env（填入高德和 DeepSeek 的 API Key）
cp .env.example .env

# 2. 启动
python app.py
# → http://localhost:8080
```

**远程 Cloud Run 服务（已部署）：**
```powershell
.\dds.ps1  # 向 https://dds-landchina-scraper-858943527635.us-central1.run.app 发送请求
```

### Flask API 路由（app.py）

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 前端 Web 界面（Bauhaus 风格 + Loca 3D 地图） |
| `/api/report` | POST | 地块报告生成 |
| `/api/chat` | POST | 同步 AI 对话 |
| `/api/chat_stream` | POST | 流式 AI 对话（SSE） |
| `/api/supplement` | POST | 线上证据补充 |
| `/api/ceo_reweight` | POST | CEO 权重调整 |
| `/api/ceo_presets` | GET | 获取权重预设列表（invest / design / finance） |
| `/api/ceo_record_weights` | POST | 记录用户权重选择 |
| `/api/ceo_learned_weights` | GET/POST | 获取/提交学习后的权重 |

### 核心脚本

```bash
# ── Web 服务 ──
python app.py                                                    # Flask API 服务（9 个路由），启动在 :8080

# ── 交互式客服 Agent（整合地块报告 + 决策推演 + 客户线索）──
python scripts/dds_customer_agent.py
python scripts/dds_customer_agent.py --once "帮我分析三亚海棠区南田路16号，预期均价35000"

# 地块画像报告（地址/坐标 → 竞品 + 配套 + 价格带）
python scripts/report_parcel.py --city 三亚 --address "三亚海棠区南田路16号" --expected-price 35000
python scripts/report_parcel.py --city 三亚 --lng 109.71899 --lat 18.41040 --district 海棠区

# 本地楼盘库查询（DuckDB + 安居客 CSV）
python scripts/query_local.py parcel --city 三亚 --district 海棠区
python scripts/query_local.py avg-price --city 杭州
python scripts/query_local.py competitors --city 三亚 --district 海棠区
python scripts/query_local.py subway --city 杭州 --district 滨江区 --max-dist 1000
python scripts/query_local.py status --city 三亚

# 高德 GIS 增强（地理编码 + POI + 通勤时间）
python scripts/gis_amap.py --address "三亚市海棠区XX路" --city "三亚"
python scripts/gis_amap.py --input data_out/sanya/normalized/2026/05/11/xxx_normalized.json

# 线上证据补充（手动录入）
python scripts/dds_online_research.py --query "三亚海棠湾" --title "来源标题" --url "https://..." --summary "摘要"

# 向量数据库
python scripts/setup_vectordb.py                                    # 初始化 ChromaDB + 下载模型
python scripts/ingest_to_vectordb.py --source landchina             # 向量化入库（来源可选）
python scripts/query_vectordb.py --query "容积率2.0 海棠区" --top 10 # 跨 Collection 语义搜索

# 数据抓取（断点续抓，单线程 + 随机 sleep）
python scripts/scrape_landchina_year.py --days 365                  # LandChina 全国土地出让
python scripts/scrape_hangzhou.py --days 90                         # 杭州土地出让
python scripts/scrape_sanya.py --days 90                            # 三亚土地出让
python scripts/parse_pdf.py --pdf path/to/公告.pdf                   # 三级 PDF 解析（pdfplumber→OCR→Vision）
```

### 测试

```bash
# 运行全部测试（pytest + 端到端 + 时间穿越）
python scripts/run_all_tests.py

# 仅单元测试（ABM 引擎 + 决策引擎）
pytest tests/ -v

# 冒烟测试（Flask API 端点）
pytest test_smoke.py -v

# 端到端测试（三亚海棠湾真实地块）
pytest tests/test_e2e_real_parcel.py -v

# 时间穿越 ABM 测试（Vault 跨年份数据加载）
python scripts/test_time_travel_abm.py
```

测试文件位置：
- `tests/test_abm.py` — ABM 引擎 50+ 用例（样本生成、去化、价格敏感度、户型配比、CEO 权重）
- `tests/test_e2e_real_parcel.py` — 端到端真实地块测试
- `test_smoke.py` — Flask API 冒烟测试（/api/report, /api/chat, /api/chat_stream 等）

## 架构与数据流

### 四层数据信任体系
| 级别 | 来源 | 信任权重 | 状态 |
|------|------|---------|------|
| L1 绝对信任 | 政府出让文件、网签成交、备案价 | 1.0 | 目标（未接入） |
| L2 高信任 | GIS/高德 POI（学校、医院、交通、商业） | 0.7 | 已接入 |
| L3 辅助决策 | 社交媒体舆情、客户偏好 | 0.35 | 需人工校准 |

### 核心数据流
```
用户输入（城市 + 地址/经纬度 + 预期均价）
  ├── app.py（Flask Web API，9 个路由）或 dds_customer_agent.py（CLI REPL）
  ├── report_parcel.py: 高德地理编码 → DuckDB 查本地楼盘库 → Haversine 距离筛选竞品
  │   └── 输出: 周边竞品 + 价格带 + 区位 POI 配套
  ├── dds_decision_engine.py (Agent v2): 合规红线 → 价值机会 → ABM 客群模拟 → 任务书 → 蓝图风险
  │   ├── abm_engine.py（Agent-Based Modeling：蒙特卡洛 + MNL 随机效用）
  │   └── 输出: 决策摘要 + 拿地敏感区间 + 风险曲线
  └── dds_online_research.py / vector_evidence.py: 线上补充证据（不覆盖本地结论）
      └── 输出: 带溯源链接的补充信息
```

### 脚本依赖关系
```
app.py（Flask Web 入口）
  ├── report_parcel.py（地块画像生成）
  │     ├── query_local.py（DuckDB 本地楼盘查询）
  │     ├── gis_amap.py（高德 API：地理编码 + POI）
  │     └── Vault/*.csv / *.parquet（数据源：三亚/杭州/上海/青岛）
  ├── dds_decision_engine.py（多智能体决策编排）
  │     ├── abm_engine.py（ABM 市场模拟）
  │     └── vector_evidence.py（向量证据 + Vault 降级）
  └── dds_online_research.py（线上证据数据模型）

dds_customer_agent.py（CLI REPL 入口 — 复用上述同一套模块）
```

### 数据目录
- `Vault/2026新楼盘/` — 安居客 CSV + Parquet 楼盘数据（三亚、杭州、上海、青岛），DuckDB 直接查询
- `Vault/YYYY年/` — 1995–2026 历史数据（每年 12 个文件：4 城 × 3 数据类型），CSV + Parquet 双格式
  - `{城市}.parquet` — 楼盘数据（Parquet 优先于 CSV 加载）
  - `客群画像-{城市}.parquet` — 客群画像
  - `客群样本-{城市}.parquet` — 客群样本
- `data_out/reports/parcel/` — 地块基础报告输出（.md + .json，部分含 .html）
- `data_out/reports/decision/` — 决策推演报告输出（.md + .json + .html）
- `data_out/online_evidence/` — 线上补充证据 JSON
- `data_out/leads/` — 客户线索 JSONL
- `data_out/landchina/` — 全国土地出让抓取归档（raw + normalized）
- `data_out/hangzhou_price/` `data_out/sanya_price/` — 城市成交价抓取归档
- `data_out/ceo_learning/` — CEO 权重学习 JSONL（EMA 平滑）
- `vectordb/` — ChromaDB 持久化目录
- `models/bge-m3/` — BGE-M3 embedding 模型缓存（~2GB，首次自动下载）

**Parquet 优先规则**：`query_local.py` 和 `report_parcel.py` 通过 DuckDB 加载数据时，若同目录存在 `.parquet` 文件则优先使用，否则回退到 CSV。使用 `convert_csv_to_parquet.py` 可批量转换。

**[T8] 长期格式优化建议**：历史楼盘 CSV 体积~200MB，转换为 Parquet 可压缩至~100MB（50% 存储节省）。建议后续新增数据抓取直接输出 Parquet 格式，长期逐步统一 Vault/* 为 Parquet-first（CSV 可保留为备份）。

### 数据抓取管道（三级信任体系支撑）
```
scrape_landchina_year.py     → 全国土地出让公告（LandChina API，断点续抓）
scrape_hangzhou.py            → 杭州公共资源交易中心（土地出让）
scrape_sanya.py               → 海南公共资源交易平台（三亚土地出让）
scrape_hangzhou_price.py      → 杭州一房一价价格备案（⚠ URL 为 TODO 占位，待替换真实域名）
scrape_sanya_price.py         → 三亚/海南一房一价价格备案（⚠ URL 为 TODO 占位）
scrape_hangzhou_deals.py      → 杭州网签成交数据（⚠ URL 为 TODO 占位）
scrape_sanya_deals.py         → 三亚网签成交数据（⚠ URL 为 TODO 占位）
scrape_fang.py                → 房天下新房列表抓取
scrape_history_loupan.py      → 历史楼盘数据抓取（房天下，2010-2025，56 字段）
anjuke_detail.py              → 安居客户型详情页抓取
scrape_pdf_links.py           → PDF 公告链接发现
parse_pdf.py                  → 三级 PDF 解析（pdfplumber → OCR → Claude Vision）
enrich_detail.py              → 数据增强/归一化
```
所有抓取脚本共享同一结构：每页落盘 + GCS 上传 + checkpoint + 随机 sleep + 单线程。

⚠ 标记为 TODO 的 4 个脚本结构就绪但 URL 待替换，当前无法使用。

### 历史数据生成管道
```
generate_cloned_history.py    → 最近邻克隆（高德 POI + 价格回归）生成历史楼盘
expand_local_v3.py            → 母体基因裂变 + 价格时间回归，扩展 1995-2025
distribute_history_data.py    → 将历史数据重新分桶到 Vault/YYYY年/
fix_all_bom_and_rebuild.py    → 修复 CSV BOM 编码 + 重建历史年份
generate_customer_data.py     → ABM 客群原型落盘为 Vault CSV
convert_csv_to_parquet.py     → CSV → Parquet 批量转换
```

### 向量数据库（语义检索层）
基于 ChromaDB + BGE-M3 embedding（AutoModel + mean pooling），实现跨数据源的语义检索：

```bash
# 初始化 + 下载 BGE-M3 模型（~2GB，需设 HF_ENDPOINT 国内镜像）
$env:HF_ENDPOINT = "https://hf-mirror.com"; python scripts/setup_vectordb.py

# Vault 楼盘数据 → ChromaDB 语义入库（核心数据源）
$env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"
python ingest_vault.py --batch-size 100           # 全量入库
python ingest_vault.py --year 2026 --city 三亚     # 指定城市/年份

# CLI 检索（跨 Collection）
python scripts/query_vectordb.py --query "容积率2.0 海棠区" --top 10
```

五个 Collection：`land_parcels`（土地出让）、`transactions`（成交）、`competitor_projects`（竞品楼盘——主集合）、`pdf_extracts`（PDF 规划条件）、`gis_context`（GIS 画像）。

**向量证据查询**（`scripts/vector_evidence.py`）：
决策引擎通过 `find_similar_parcels()` 获取类似地块证据。优先 ChromaDB 语义检索（BGE-M3 embedding），不可用时降级 Vault CSV 价格相似度匹配。决策引擎的 `value_agent` 自动调用。

**关键踩坑——pyarrow 与 torch 冲突**：
Windows 上 pandas 的 parquet 引擎（pyarrow）与 torch 的 libomp DLL 冲突，会导致 SIGSEGV (0xC0000005)。加载模型时 transformers → sklearn → pandas → pyarrow 的导入链触发崩溃。解决方案：
- 数据读取优先 CSV（`ingest_vault.py` 已适配），避免 `pd.read_parquet()`
- 必须 parquet 时用 `fastparquet` 引擎替代 pyarrow
- 设置 `$env:HF_HUB_OFFLINE = "1"` + `$env:TRANSFORMERS_OFFLINE = "1"`

```

### GCS 数据湖
`scripts/setup_gcs.py` 初始化 Google Cloud Storage 桶结构（`dds-data-lake`），按来源/城市分区存储 raw + normalized + logs。配套脚本：`init_bucket.ps1`、`upload_dirs.ps1`。

## 重要规则

### DDS 投资分析强制流程
针对地块价值、竞品、配套、拿地价、可研、IRR、货值测算、未来推演请求：
1. **必须先运行本地 DDS 脚本**：`dds_customer_agent.py` 或 `report_parcel.py`
2. 本地输出是主证据，必须报告样本数、来源范围和生成时间
3. 线上搜索只能作为补充交叉验证，必须标注来源、日期和口径，不得覆盖本地库结论
4. 禁止只凭模型常识生成 DDS 投资报告

### 浏览器抓取优化（Scraper 子任务）
触发条件：执行涉及网页访问、DOM 解析、数据抓取的子任务时注入：
- 屏蔽 `*.jpg,*.png,*.gif,*.webp,*.ico,*.svg,*.css,*.woff,*.woff2,*.ttf`
- 等待策略优先 `DOMContentLoaded`，目标 DOM 锚点出现即提取数据
- 全局超时 10s，操作间隔 500ms，复杂渲染降级为读原始 HTML

### 技术约束
- **环境变量**：复制 `.env.example` 为 `.env`，填入真实 API Key。`.env` 已在 `.gitignore` 中
- 高德 API Key 硬编码在 `gis_amap.py:31`（`AMAP_KEY`），也存在于 `scrape_history_loupan.py:21` 和 `generate_cloned_history.py:24`，不可提交到公开仓库
- DeepSeek API Key 硬编码在 `start-dds-deepseek.ps1`（`ANTHROPIC_AUTH_TOKEN`），不可提交到公开仓库
- 当前仅支持四城：三亚、杭州、上海、青岛；扩展需：① 新增 Vault CSV/Parquet ② 更新 `query_local.py` 的 `CITY_FILES` ③ 更新 `app.py:164` 的 `ALLOWED_CITIES`
- 城市校验存在三处独立定义（`query_local.py` 的 `CITY_FILES`、`app.py` 的 `ALLOWED_CITIES`、`report_parcel.py:565` 的内联校验），新增城市需三处同步
- BGE-M3 embedding 模型较大（~2GB），首次运行 `setup_vectordb.py` 会自动下载到 `models/bge-m3/`
- PDF 解析需要系统安装 tesseract（OCR）和 poppler（pdf2image）
- `scrape_hangzhou_price.py`、`scrape_sanya_price.py`、`scrape_hangzhou_deals.py`、`scrape_sanya_deals.py` 的 URL 为 `TODO_REPLACE_ME_REAL_DOMAIN` 占位符，待替换真实政府网站域名

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
