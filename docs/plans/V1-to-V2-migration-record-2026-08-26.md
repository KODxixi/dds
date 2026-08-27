# DDS V1 → V2 资产迁移记录

- 日期：2026-08-26
- 目标：在 V1（`D:\Vault-assets\AI_Projects\DDS`）删除前，把「优质且成熟、不可再生」的资产搬进 V2（`D:\Vault-assets\AI_Projects\DDS_v2`），补齐 V2 能力缺口。
- 原则：数据/代码「搬彻底」，但**不把 V1 的无证据估计值注册成 V2 的已校准参数**——那会重新引入 V2 证据优先原则要消除的问题。

## 一、结论速览

V2 的核心引擎（9 个）与结构化数据（`curated`/`raw/purchased`）此前已就绪；本轮补齐的是 V2 明确缺的 **价格回测、标杆匹配、决策推理链** 三个算法能力，以及 V1 的**不可再生数据**（客户项目、爬取语料、校准/基准/客群数据）与**基础设施管线**。

## 二、代码迁移（已实现 + 测试通过）

| V1 源文件 | V2 落点 | 补齐的缺口 |
|---|---|---|
| `scripts/backtest_price.py` | `src/dds/engines/backtest.py` | ❌ 价格回测（V2 完全没有，M11 明确列为缺项） |
| `scripts/benchmark_engine.py` | `src/dds/engines/benchmark.py` | ❌ 标杆匹配（V2 无 benchmark 引擎） |
| `scripts/decision_trace.py` | `src/dds/trace.py` | ⚠️ 决策推理链可视化（V2 无独立 trace 层） |

- 三者均为纯标准库实现，无第三方依赖；已从 V1 移植为 V2 dataclass + 类型标注风格，并从 `src/dds/engines/__init__.py` 导出。
- 移植中修复两处 V1 潜在 bug：`decision_trace.trace_context` 成功路径遗留非终态 `"running"`（其 HTML 渲染会 KeyError）；`benchmark_engine.validate` 对 list 型 `layer2_ops`/`layer3_proof` 会 `AttributeError`。
- 测试：`tests/unit/test_backtest_engine.py`、`test_benchmark_engine.py`、`test_trace.py`（共 18 项，全绿）。

## 三、数据迁移

### 已复制（不可再生）

| V1 源 | V2 落点 | 大小 |
|---|---|---|
| `data/benchmark_library.json` | `data/benchmark/` | 168K（81 案例标杆库） |
| `data/{price_band,archetype_real,listing_dimensions,loupan_schema}.json` | `data/` | ~82K |
| `data/golden_inputs/`、`data/ge_expectations/` | `data/golden_inputs/`、`data/ge_expectations/` | 28K |
| `data_out/ceo_learning/` | `data/ceo_learning/` | 439K（用户行为日志） |
| `data_out/archlib_visual_roles.jsonl`+summary | `data/archlib/` | 48M（VLM 打标产物） |
| `output/v4_golden/` | `data/golden/` | 2.7M（含 500 页压测夹具） |
| `Vault/{年份}/客群画像-*、客群样本-*`（512 文件） | `data/knowledge/customer/` | 346M |
| `实际项目/`（6 客户项目 + 路演白皮书） | `data/raw/v1-projects/` | 3.54G（4851 文件，robocopy 完成） |
| `Vault/建筑案例内容/kmlovemilk/` | `data/raw/v1-kmlovemilk/` | 18.0G（45,150 文件，完成） |

> 注：V2 已有 `data/raw/purchased`（223M 原始购买库）与 `data/curated`（51M 结构化楼盘/土地/成交/宏观），无需重复迁移；`data/knowledge/{architecture-cases,dcbbs}`（331M）是 kmlovemilk/DCBBS 的**已处理索引**，本轮补的是**原始媒体归档**。

### 有意不迁（可重建/公开可再获取）

- `models/bge-m3`（8.7G，HuggingFace 可再下载）、`vectordb/`（测试占位）、`data_out/dcbbs`（与 Vault/DCBBS 重复副本）、`data_out/validation`（QA 截图）、年份桶里的楼盘 CSV（从购买库按开盘年可派生）、`Vault/iceberg`（实验）。
- `Vault/DCBBS`（107G 原始归档）：用户决定**留在 V1 不迁**——疑似含大量垃圾数据；V2 已有 `data/knowledge/dcbbs`（已处理索引），原始媒体归档不再搬入。

## 四、基础设施管线（保留到 tools/v1-legacy/）

| V1 源 | V2 落点 | 说明 |
|---|---|---|
| `skills/image-hunter` | `tools/v1-legacy/image-hunter` | 四站每日图片采集管线（唯一图像能力） |
| `cloud/volcengine` | `tools/v1-legacy/volcengine-cloud` | TOS 数据湖 + FC 治理/发布/调度（**已排除密钥 `.env.volcengine`**） |
| `.agents/skills/dds-inbox-pipeline` | `tools/v1-legacy/dds-inbox-pipeline` | 资料入箱→解析→门禁 workflow |
| `.agents/skills/amap-loca-dds` | `tools/v1-legacy/amap-loca-dds` | AMap Loca 场景编译 |
| `skills/arch-dds` | `tools/v1-legacy/arch-dds` | 报告设计知识 |
| `scripts/`（全部 128 个 .py） | `tools/v1-legacy/scripts` | 3.1M，含 27 个可直接复用 + 37 个需适配的算法（见下） |

> 为让 `tools/v1-legacy`（保留的 V1 参考代码）不进入 V2 的 lint 面，已在 `pyproject.toml` 的 `[tool.ruff]` 加 `extend-exclude = ["tools/v1-legacy"]`；该目录只读保留、不在此维护。

## 五、明确「搬但未接」的资产（需重新举证才能进 V2 正式链）

1. **ABM 城市参数**：V1 `abm_engine.CITY_POOLS`（三亚/杭州/上海/青岛/济南 5 城客群池）是「基于公开统计+行业认知」的**分析师估计**（仅济南有 226 份问卷 + E15 可研溯源）。V2 的 `CityModelParameters` 强制要求 `supporting_evidence_refs`，故**不自动注册**。已保留在 `tools/v1-legacy/scripts/abm_engine.py`，待用真实客群证据重新校准后由 V2 正式注册。
2. **决策解释层** `decision_explainer.py`：读 V1 的 report_json 旧 schema，需适配 V2 的 `ReportRun`/`SectionResult` 后接入（`decision_trace` 已迁移，作为其白盒底座）。
3. **其余 35 个「需适配」算法**（premium/investment/macro/social/design_market_link/traditional_spatial_culture/location_gateway/decision_qualification 等）：已整体保留在 `tools/v1-legacy/scripts/`，按需逐个适配。
4. **在线研究凭据**：V2 的 Tavily/火山研究机制完整但无 `TAVILY_API_KEY`/`VOLC_*` 凭据，外部源 `availability=False`。

## 六、有意不迁（非「优质成熟」，删除零损失）

- `.codex/agents/` 36 个 persona toml（纯 prompt 配置，无逻辑）、`.codex/artifacts/`（调试截图）、`.codex/skill-staging/`、`skills/ui-polish`、`skills/light-frontend-design`、`skills/amap-loca-dds-staging`（冗余副本）、`web/decision-field`+`earth-hero`（依赖高德 CDN/Key，与 V2 离线报告链冲突）。

## 七、V1 删除前核对清单

- [x] `data/raw/v1-projects` 4851 文件齐全（robocopy EXIT=1 即成功）
- [x] `data/raw/v1-kmlovemilk` 完成（18.0G，45,150 文件）
- [x] `data/raw/v1-dcbbs` —— 用户决定不迁（留在 V1，疑似垃圾数据）；已停止复制并删除 V2 临时副本
- [x] 抽查：`uv run pytest` 全绿（289 passed）、`ruff check` 全绿
- [x] 确认 volcengine 密钥未随目录复制（已排除 `.env.volcengine`，仅保留 `.example`）

**2026-08-26 已删除 V1 源**：上表「已迁移」资产（5 校准/基准 JSON、golden_inputs、ge_expectations、ceo_learning、archlib_visual_roles、v4_golden、512 客群文件、实际项目、kmlovemilk、image-hunter、arch-dds、dds-inbox-pipeline、amap-loca-dds、volcengine、scripts）已从 V1 删除并抽查确认不存在。V1 现已减至仅 `Vault\DCBBS`（107G 完整保留）。

**补充归档（2026-08-26）**：
- V1 Web UI 源码（app.py / index.html / web / templates / package*.json / requirements.txt / Dockerfile）→ `tools/v1-legacy/webui`（38 文件）
- data_out 抓取数据（jinan / jinan_fang / sanya / sanya_price / hangzhou_price / landchina + jn_* 根文件）→ `data/raw/v1-scraped`（27 文件）
- 注：`.gstack` 空目录因域账号 `hyp\shiguanyu` vs ACL `SHIGUANYU` 歧义拒绝访问，经 takeown + icacls /reset 后删除。

> 注：`data/raw/v1-projects` 已清理测试残留，现为 **2353 个真实文件**（6 个 Test_* 项目 + 路演白皮书）。清理删除 26 个 junk 目录 + 135 个 junk 文件（`__pycache__`/`.pyc`/`.build.lock`/pytest 临时目录/dirty-project 夹具，约 2497 文件），为 V1 迁移时一并带入的测试/构建残留，非客户数据。

## 八、验证证据

- `uv run pytest -p no:cacheprovider` → **289 passed**（含本轮新增 18 项）
- `uv run ruff check --no-cache src tests tools` → **All checks passed**
- 真实 `benchmark_library.json` 加载：81 案例；`match_benchmarks("武汉","改善")` 返回 建发·望京养云/融创壹号院/龙湖·空中院子；`validate` 报 9 条**库本身历史数据问题**（部分四代案例缺 `narrative` 维度分等），非代码缺陷。
