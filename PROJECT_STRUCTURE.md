# DDS V2 项目结构

DDS V2 只采用 `src` 布局。根目录不再保留第二份 `dds/`，文档描述与磁盘结构以本文件为准。

```text
DDS_v2/
├── src/dds/              # 唯一正式 Python 包
├── tests/
│   ├── unit/             # 纯模块与契约测试
│   ├── integration/      # 跨模块、数据适配与服务测试
│   ├── e2e/              # 完整编排与垂直切片
│   ├── golden/           # 报告编译基准测试
│   └── fixtures/         # 固定测试输入
├── tools/dev/            # 正式开发诊断工具（migrate_v1_vault.py 等）
├── tools/                # 一次性 QA/分析脚本与 v1-legacy 遗留入口（非正式交付）
├── scripts/              # 可运行管线/报告脚本（build_project_report.py 等）
├── docs/
│   ├── architecture/     # 当前架构说明
│   ├── plans/            # 历史与实施计划
│   ├── handoffs/         # 唯一 handoff 位置
│   └── research/         # 调研材料
├── data/                 # 数据资产与运行状态边界，不进入 wheel
├── pyproject.toml        # 包、依赖与工具配置
└── uv.lock               # 可复现依赖锁
```

## 边界

- `src/dds` 是唯一代码真相；不得恢复根级 `dds/`。
- `docs/handoffs` 是唯一 handoff 位置；根级 `agent_handoffs` 已废弃。
- `data/raw` 保存不可变购买原包，运行时禁止直接查询。`n- `data/curated` 是 V2 唯一结构化事实查询层。`n- `data/knowledge` 保存与事实库隔离的受限内部研究知识。`n- `data/manifests` 保存来源、访问等级、schema、哈希和生成关系。`n- `data/projects`、`data/cache`、`data/exports` 是本地或可重建产物；整个 `data/` 均不进入 wheel。
- `.venv`、缓存、覆盖率、构建目录和字节码均为可重建内容。
- V1 Vault 仅作为迁移来源保留；V2 默认读取 `data/curated`，可用 `DDS_DATASETS_ROOT` 覆盖。

## 数据子目录台账

`data/` 除结构化事实层与知识层外，另含 V1→V2 迁移产出的数据产物，全部不进入 wheel、不提交 Git（`.gitignore` 已覆盖）：

| 子目录 | 内容 | 归属 |
|---|---|---|
| `data/curated/` | land / listings / macro / transactions（parquet） | V2 唯一结构化查询层 |
| `data/knowledge/` | architecture-cases / archlib / ceo_learning / dcbbs / customer | 受限内部研究知识 |
| `data/raw/` | 不可变购买原包 | 运行时禁止直接查询 |
| `data/manifests/` | 来源 / 访问等级 / schema / 哈希台账 | 溯源 |
| `data/qa/` | ge_expectations（治理阈值）/ benchmark（标杆库）/ golden + golden_inputs（压测夹具） | 质量/测试/治理资产（非知识） |
| `data/projects` `data/cache` `data/exports` | 本地或可重建产物 | 运行状态 |

## 知识库治理

知识库统一收在 `data/knowledge/`，**源与索引分离、源只读不动、全部留在 DDS 仓库内治理**（不外迁 D 盘母目录）。

**可查询知识层 = `data/knowledge/`**

| 子库 | 内容 | 来源 |
|---|---|---|
| `architecture-cases/` | 建筑案例视觉角色 | V1 `建筑案例内容/kmlovemilk` 迁移 |
| `archlib/` | 建筑案例 VLM 打标（`archlib_visual_roles.jsonl`，48M） | `data_out/archlib_visual_roles` 迁移 |
| `ceo_learning/` | 客群/CEO 学习行为日志（`user_*.jsonl`） | `data_out/ceo_learning` 迁移 |
| `dcbbs/` | DCBBS 处理索引（`news/resources/open_source_matches.jsonl.gz`，285M） | 由源 sqlite 单向导出 |
| `customer/` | 客群画像（346M） | V1 客群样本迁移 |

**源层（只读、不查询、gitignored）**

- `Vault/`：V1 只读迁移源。当前仅含 `Vault/DCBBS/`（107G 原始归档，其中 `normalized/dcbbs.sqlite3` 2.14G 为索引之源）。生成规则：`tools/dev/migrate_v1_vault.py::_export_dcbbs` 从 `Vault/DCBBS/normalized/dcbbs.sqlite3` 单向导出到 `data/knowledge/dcbbs/`；源永不动、只读。

**外部应用归档（gitignored，整体保留可还原）**

- `cibuddy-archive/`（458M）：CI-Buddy 后端完整归档（`cibuddy-backend.db` + 171 skills + `runtime/python` + 会话/记忆），是外部应用 `%APPDATA%\cibuddy` 的镜像，**非 DDS 研究知识**，只作外部应用归档，不拆入 `data/knowledge/`。

以上目录均 `.gitignore`，留在 DDS 内治理，不提交 Git、不外迁。

## 标准验收

```powershell
uv sync --dev
uv run ruff check --no-cache src tests tools
uv run pytest -p no:cacheprovider
```
