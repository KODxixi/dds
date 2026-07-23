# DDS 核心文件迁移与架构升级计划

> **状态：历史迁移方案。** 当前工程结构与产品主线已发生变化；后续执行以 `DDS_v2三类输入与操盘优化升级策略-2026-07-23.md`、当前实现状态和最新 handoff 为准，本文仅保留迁移背景。

## 一、当前状态分析

### 现状对比

| 项目 | 旧版 DDS | DDS_v2 |
|------|----------|--------|
| **核心文件** | `app.py` (117KB 单体主程序), `ingest_vault.py`, `test_smoke.py` | 仅 `tools/embed_all_images.py` |
| **目录结构** | `scripts/`, `templates/`, `data/`, `web/`, `reports/`, `tests/` | 空架子: `src/`, `tests/`, `tools/`, `web/`, `data/` |
| **配置文件** | `.env`, `requirements.txt`, `Dockerfile` | `.env.example`, `requirements.txt`, `pyproject.toml` |
| **Python 文件数** | 30+ | 1 |

### 核心结论
**DDS_v2 几乎完全没有迁移旧版 DDS 的核心代码，只有基础项目骨架。** 需要系统性迁移核心文件并进行架构升级。

---

## 二、核心文件迁移清单

### 📋 必须迁移的核心文件（优先级从高到低）

#### **第一优先级：三大核心内容引擎**
这是 DDS 的核心价值板块

| 文件 | 原位置 | 新位置建议 | 迁移策略 |
|------|--------|------------|----------|
| **abm_engine.py** | `scripts/` | `src/dds/engine/` | 迁移+重构，支撑产品定位双模 |
| **premium_engine.py** | `scripts/` | `src/dds/engine/` | 迁移+重构，支撑溢价测算 |
| **dds_decision_engine.py** | `scripts/` | `src/dds/engine/` | 迁移+模块化拆分，驱动完整报告 |

#### **第二优先级：数据与基础设施**
| 文件 | 原位置 | 新位置建议 | 迁移策略 |
|------|--------|------------|----------|
| `query_local.py` | `scripts/` | `src/dds/data/` | 迁移，DuckDB 636城查询底座 |
| `project_panorama.py` | `scripts/` | `src/dds/services/` | 迁移，项目全景调研 |
| `schema_dds.py` | `scripts/` | `src/dds/domain/` | 迁移，数据模型定义 |
| `confidence_engine.py` | `scripts/` | `src/dds/engine/` | 迁移，置信度计算 |
| `benchmark_engine.py` | `scripts/` | `src/dds/engine/` | 迁移，对标基准计算 |
| `report_contract_*.py` | `scripts/` | `src/dds/engine/` | 迁移，报告契约定义 |
| `report_document.py` | `scripts/` | `src/dds/engine/` | 迁移，报告文档生成 |
| `report_template_profile_v1.json` | `data/` | `src/dds/data/assets/` | 迁移，报告模板 |
| `dds_report_apple_16x9.html` | `templates/` | `src/dds/templates/` | 迁移，HTML报告模板 |

#### **第三优先级：Web入口与服务**
| 文件 | 原位置 | 新位置建议 | 迁移策略 |
|------|--------|------------|----------|
| `app.py` (117KB主程序) | 根目录 | `src/dds/main.py` + `api/` | 重构拆分，不复用单体 |
| `decision_field_api.py` | `scripts/` | `src/dds/api/` | 迁移，API接口层 |

#### **第四优先级：测试与数据**
| 文件 | 原位置 | 新位置建议 | 迁移策略 |
|------|--------|------------|----------|
| `test_smoke.py` | 根目录 | `tests/` | 重写冒烟测试 |
| `tests/` (下的关键用例) | `scripts/tests/` | `tests/` | 筛选迁移 |
| `data/*.json` (配置文件) | `data/` | `src/dds/data/assets/` | 筛选迁移 |

---

### ❌ 可废弃的文件（不迁移到 DDS_v2）

| 类别 | 文件列表 | 废弃理由 |
|------|----------|----------|
| **爬虫脚本** | `scrape_*.py`, `crawl_*.py`, `anjuke_detail.py`, `ingest_*.py` | DDS_v2定位是报告引擎，数据采集另建专用管道 |
| **运维脚本** | `*.ps1`, `init_bucket.ps1`, `setup_*.py` | 部署运维独立管理 |
| **历史迁移** | `iceberg_migrate.py`, `loupan_migrate.py`, `*_migrate.py` | 一次性脚本，已完成使命 |
| **外部依赖** | `garchos_openapi_contract.py`, `archlib_*.py` | V1 scope 不包含 archlib 集成 |
| **向量库包袱** | `vector_evidence.py`, `query_vectordb.py` | V2 改用 DuckDB VSS，废弃旧向量栈 |
| **决策场遗留** | `decision_field_*.py` (除 api/models) | 简化架构，报告引擎直接调用 |
| **单体大文件** | `app.py` (117KB) | 完全重构拆分，不整体迁移 |
| **数据输出** | `data_out/`, `output/`, `reports/` | 产物目录运行时生成 |
| **前端旧版** | `web/index.html` (旧查询页) | V1 交付用 arch-front-html skill 标准输出 |

---

## 三、代码重构方案：单体 app.py 模块化拆分

### 重构原则
1. **最小可逆变更**：先迁移验证，再重构，不脑补重写
2. **先平迁、后拆分**：保证功能等价后再做模块化
3. **分层清晰**：API层 → 服务层 → 引擎层 → 数据层 → 领域模型
4. **依赖倒置**：高层不依赖底层，通过接口隔离

### app.py 拆分方案（117KB → 8个模块）

```
📁 src/dds/
├── 📄 main.py              # 入口，FastAPI初始化，仅100行以内
├── 📁 api/
│   ├── 📄 __init__.py
│   ├── 📄 parcel_api.py    # 地块查询接口（原 /parcel/*）
│   ├── 📄 report_api.py    # 报告生成接口（原 /report/*）
│   └── 📄 health_api.py    # 健康检查接口
├── 📁 services/
│   ├── 📄 __init__.py
│   ├── 📄 parcel_service.py    # 竞品对标业务逻辑
│   ├── 📄 report_service.py    # 报告编排服务
│   └── 📄 panorama_service.py  # 项目全景服务
├── 📁 engine/              # 核心引擎层
│   ├── 📄 abm_engine.py        # 产品定位引擎
│   ├── 📄 premium_engine.py    # 溢价测算引擎
│   ├── 📄 dds_decision_engine.py  # 决策引擎
│   └── ...（其他引擎文件）
├── 📁 data/                # 数据访问层
│   ├── 📄 parcel_repo.py
│   ├── 📄 vector_repo.py
│   ├── 📄 query_local.py
│   └── 📁 assets/          # 数据资产
├── 📁 domain/              # 领域模型层（新增）
│   ├── 📄 models.py            # Pydantic模型定义
│   └── 📄 value_objects.py     # 值对象定义
└── 📁 config/              # 配置层
    └── 📄 settings.py
```

### 拆分后各模块职责边界

| 模块 | 职责 | 依赖 | 行数估计 |
|------|------|------|----------|
| `main.py` | FastAPI实例创建、中间件注册、路由挂载 | api层、config | <100行 |
| `parcel_api.py` | REST接口定义、请求参数校验、响应格式化 | parcel_service、domain.models | 200-300行 |
| `report_api.py` | 报告生成异步接口、进度查询、产物下载 | report_service | 200-300行 |
| `parcel_service.py` | 竞品对标业务逻辑、语义检索编排 | data层、engine层契约 | ~500行 |
| `report_service.py` | 报告编排：evidence包 → 编译 → 交付验证 | engine层全部 | ~300行 |
| `panorama_service.py` | 地块基础信息调研、宏观数据聚合 | data层 | ~400行 |
| `abm_engine.py` | 产品定位双模、户型配比、总图强排逻辑 | engine层契约、data层 | ~2000行 |
| `premium_engine.py` | 溢价因子计算、价值兑现测算 | engine层契约、data层 | ~1000行 |

### 关键技术难点与解决方案

#### 1. 硬编码路径问题
**问题**：老代码大量使用 `__file__.parent.parent / "scripts" / "xxx.py"`
**解决方案**：
```python
# 旧代码 ❌
root = Path(__file__).resolve().parents[1]
template_path = root / "templates" / "xxx.html"

# 新代码 ✅
from dds.config.settings import get_settings
settings = get_settings()
template_path = settings.templates_dir / "xxx.html"
```

#### 2. 双分支 import 兼容问题
**问题**：老代码 `try: from scripts.xxx import ... except: from xxx import ...`
**解决方案**：
```python
# DDS_v2 统一绝对导入 ✅
from dds.engine.report_document import compile_report_document
```

#### 3. 全局状态问题
**问题**：app.py 中有大量全局变量和单例
**解决方案**：
- 使用依赖注入模式
- 配置集中管理（settings.py + 环境变量）
- 数据库连接池在服务启动时初始化

---

## 四、目录结构标准化方案

### 最终目标结构

```
📁 DDS_v2/
├── 📄 pyproject.toml          # 项目配置（已存在）
├── 📄 requirements.txt        # 依赖（已存在）
├── 📄 README.md               # 文档（已存在）
├── 📄 run.ps1                 # 启动脚本（已存在）
├── 📄 .env.example            # 环境变量模板（已存在）
├── 📁 src/
│   └── 📁 dds/
│       ├── 📄 __init__.py     # 包版本导出
│       ├── 📄 main.py         # FastAPI入口
│       ├── 📁 api/            # REST接口层
│       ├── 📁 services/       # 业务服务层
│       ├── 📁 engine/         # 核心引擎层
│       ├── 📁 data/           # 数据访问层
│       │   └── 📁 assets/     # 数据资产
│       ├── 📁 domain/         # 领域模型层
│       ├── 📁 config/         # 配置层
│       └── 📁 templates/      # HTML 模板
├── 📁 tests/                  # 测试目录
│   ├── 📁 unit/
│   ├── 📁 integration/
│   └── 📁 e2e/
├── 📁 tools/                  # 工具脚本
├── 📁 web/                    # 静态资源
├── 📁 data/                   # 运行时数据目录
│   ├── 📁 projects/           # 项目工作区
│   ├── 📁 cache/              # 缓存
│   └── 📁 exports/            # 导出产物
└── 📁 docs/                   # 文档
```

### 目录设计原则

1. **src/dds 下按职责分层**：api → services → engine → data → domain → config
2. **数据分离**：代码在 src/，运行时数据在 data/，配置在 config/
3. **可测试性**：每层都可独立单元测试，依赖通过接口注入
4. **可部署性**：整个 src/dds 可作为 Python wheel 包发布

---

## 五、假设与决策

### 关键假设
1. **老 DDS 代码是真理来源**：所有迁移以"与老版逐字节语义对齐"为验证标准
2. **V1 scope 只做核心板块**：竞品对标、产品定位、溢价测算三个核心板块，其余做降级占位
3. **交付形态是标准 HTML 报告**：通过 arch-front-html skill 输出，不复用老 web 页

### 已做决策
1. **架构采用分层设计**：放弃单体 app.py，采用模块化分层架构
2. **数据层使用 DuckDB VSS**：放弃旧向量数据库栈，改用 DuckDB VSS 向量检索
3. **配置集中管理**：所有路径和配置通过 settings.py + 环境变量管理

### 待澄清问题
1. **arch-front-html skill 桥接方式**：是修改 skill 的 discover_dds_root，还是在 DDS_v2 提供兼容入口？
2. **数据资产迁移范围**：哪些 parquet 数据文件需要从旧 DDS 迁移到 DDS_v2？

---

## 六、执行步骤与验证

### 阶段 1：核心引擎迁移
**目标**：迁移三大核心引擎文件
1. 迁移 `abm_engine.py` 到 `src/dds/engine/`
2. 迁移 `premium_engine.py` 到 `src/dds/engine/`
3. 迁移 `dds_decision_engine.py` 到 `src/dds/engine/`
4. 修复导入路径和硬编码问题

**验证**：
- 三个引擎文件可正常导入（无 ImportError）
- 核心函数签名与旧版保持一致

---

### 阶段 2：数据基础设施迁移
**目标**：迁移数据访问层文件
1. 迁移 `query_local.py` 到 `src/dds/data/`
2. 迁移 `schema_dds.py` 到 `src/dds/domain/`
3. 迁移 `project_panorama.py` 到 `src/dds/services/`
4. 迁移 `confidence_engine.py`、`benchmark_engine.py`
5. 迁移报告模板和契约文件

**验证**：
- DuckDB 查询可以正常执行
- 数据模型定义完整加载

---

### 阶段 3：报告契约与模板迁移
**目标**：建立报告生成基础能力
1. 迁移 `report_contract_*.py` 系列文件
2. 迁移 `report_document.py`
3. 迁移 `dds_report_apple_16x9.html` 模板
4. 迁移 `report_template_profile_v1.json`

**验证**：
- 报告契约类可正常实例化
- 模板文件路径可正确解析

---

### 阶段 4：app.py 重构拆分
**目标**：将 117KB 单体拆分为 8 个模块
1. 创建 `main.py` - FastAPI 入口
2. 创建 `api/parcel_api.py` - 地块查询接口
3. 创建 `api/report_api.py` - 报告生成接口
4. 创建 `services/report_service.py` - 报告编排服务
5. 创建 `domain/models.py` - Pydantic 模型定义
6. 创建/完善 `config/settings.py` - 配置管理

**验证**：
- FastAPI 服务可正常启动
- `/health` 端点可访问
- 无循环依赖问题

---

### 阶段 5：测试与验证
**目标**：端到端验证完整流程
1. 重写冒烟测试 `tests/test_smoke.py`
2. 编写核心引擎单元测试
3. 端到端报告生成测试
4. 性能对比测试（与旧版 DDS）

**验证**：
- 冒烟测试通过率 = 100%
- 报告生成时间 ≤ 旧版 × 1.2
- 内存峰值 ≤ 旧版 × 1.3

---

### 阶段 6：文档与收尾
**目标**：完善文档，准备交付
1. 完善 `README.md`
2. 编写架构文档
3. 更新 `pyproject.toml` 和 `requirements.txt`
4. 清理临时文件和调试代码

---

## 七、风险点与缓解措施

### 🔴 高风险点

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| **硬编码路径遗漏** | 运行时 FileNotFoundError | 全局搜索 `Path(__file__`、`scripts/`、`templates/`、`data/`，全部改走 settings |
| **循环依赖** | 导入失败 | 分层设计时严格遵守"上层依赖下层"，数据层不能依赖引擎层 |
| **app.py 全局状态拆解** | 功能不一致 | 先识别所有全局变量，逐个转化为局部状态或单例模式 |
| **abm_engine 依赖链复杂** | 迁移后功能残缺 | 先做静态分析，画出依赖图，按依赖顺序迁移，每迁一个文件就跑一次冒烟 |

---

### 🟡 中风险点

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| **DuckDB 版本差异** | 查询行为不一致 | 锁定 duckdb==1.5.4，与老版一致 |
| **向量检索降级路径** | LMStudio 不可用时崩溃 | vector_repo 设计完整降级路径 |
| **大文件验证慢** | 大证据包验证耗时 | 使用子进程 + 写小哈希文件做比对 |

---

## 八、验收标准

### 功能验收
1. **核心引擎完整迁移**：三大引擎（abm、premium、decision）功能与旧版语义一致
2. **API 接口兼容**：主要 API 端点响应格式与旧版兼容
3. **报告生成完整**：端到端可生成完整的 HTML 报告

### 质量验收
1. **冒烟测试通过率 = 100%**
2. **无循环依赖**：可正常导入所有模块
3. **性能不劣于旧版**：报告生成时间 ≤ 旧版 × 1.2

### 架构验收
1. **分层清晰**：api → services → engine → data → domain → config
2. **配置统一**：无硬编码路径，全部通过 settings.py 管理
3. **可测试性**：每层都可独立单元测试
