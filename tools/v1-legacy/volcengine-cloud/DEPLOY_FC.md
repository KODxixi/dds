# DDS 云端治理 + 索引 部署指南

## 概述

TOS 上传数据后，火山引擎 FC 自动触发治理和归一化，全链路在火山云内闭环。

```
┌─────────────────────────────────────────────────────────────────────┐
│                        火山云内闭环                                  │
│                                                                     │
│  TOS vault/ 新 CSV 上传                                             │
│       │                                                             │
│       ├──→ FC: dds-pipeline-governance                              │
│       │      ├── T7 清洗（去重→过滤→混洗）                           │
│       │      ├── T8 质量评估（八维指标 + 标准工件）                   │
│       │      ├── P0 质量门禁                                         │
│       │      └── 输出 → governance/                                 │
│       │                                                             │
│       └──→ FC: dds-pipeline-normalize                               │
│              ├── CSV → DuckDB → Parquet (zstd)                      │
│              ├── 更新 _meta/data_lineage.json                       │
│              └── 输出 → normalized/                                 │
│                                                                     │
│  本地                                                                 │
│       │                                                             │
│       ├── pipeline_scheduler.py --mode fc --run governance          │
│       │   手动触发云端治理（按城市）                                  │
│       │                                                             │
│       └── query_local.py (DDS_MODE=cloud)                           │
│           Parquet 优先，fallback 到 TOS 下载                          │
└─────────────────────────────────────────────────────────────────────┘
```

## 新增文件

| 文件 | 用途 |
|------|------|
| `cloud/volcengine/fc_governance.py` | FC 治理函数：TOS 事件 → T7→T8→门禁 → governance/ |
| `cloud/volcengine/fc_normalize.py` | FC 归一化函数：TOS 事件 → CSV→Parquet → normalized/ |
| `cloud/volcengine/pipeline_scheduler.py` | 改造：增加 `--mode fc` 远程触发 |

## 改造文件

| 文件 | 变更 |
|------|------|
| `cloud/volcengine/pipeline_scheduler.py` | 新增 FC 模式、FC 管道配置、`_invoke_fc()` |
| `cloud/volcengine/requirements.volcengine.txt` | 新增 `duckdb>=1.0.0` |
| `cloud/volcengine/service/Dockerfile` | PYTHONPATH 增加 governance 路径 |

## 部署步骤

### Step 1: 准备 FC 函数代码包

```bash
# 打包 governance 脚本 + FC 入口
# 最小包：fc_governance.py + tos_client.py + scripts/governance/*.py
cd C:\Users\shiguanyu\DDS

# 创建函数包目录
mkdir -p cloud/volcengine/fc_packages/governance
mkdir -p cloud/volcengine/fc_packages/normalize

# governance 包
cp cloud/volcengine/fc_governance.py cloud/volcengine/fc_packages/governance/main.py
cp cloud/volcengine/tos_client.py cloud/volcengine/fc_packages/governance/
cp scripts/governance/*.py cloud/volcengine/fc_packages/governance/

# normalize 包
cp cloud/volcengine/fc_normalize.py cloud/volcengine/fc_packages/normalize/main.py
cp cloud/volcengine/tos_client.py cloud/volcengine/fc_packages/normalize/
```

> **安全闸门：禁止把任何 `.env*`、`credentials*`、私钥或其他凭证文件复制进函数目录或 ZIP。**
> `deploy_fc.py` 会在重打包前和云上传前扫描 ZIP；命中敏感文件名、私钥标记或高置信凭证赋值后立即失败，且错误信息不会打印匹配内容。
> ZIP 只包含显式列出的源码、依赖清单和运行时资源。无密钥的现有代码包仍可正常完成 `main.py → index.py` 重打包。

### Step 2: 在火山引擎控制台创建 FC 函数

**函数一：dds-pipeline-governance**
- 运行时：Python 3.9+
- 入口函数：`main.handler`
- 内存：512 MB
- 超时：300 秒
- 运行时配置：通过 veFaaS 环境变量或最小权限角色注入；不得读取包内 `.env`
- 代码包：上传 `fc_packages/governance/` 目录的 zip

**函数二：dds-pipeline-normalize**
- 运行时：Python 3.9+
- 入口函数：`main.handler`
- 内存：512 MB（DuckDB 需要较多内存）
- 超时：300 秒
- 依赖：需要 `duckdb` 层或 requirements.txt 包含 duckdb
- 运行时配置：通过 veFaaS 环境变量或最小权限角色注入；不得读取包内 `.env`
- 代码包：上传 `fc_packages/normalize/` 目录的 zip

两类函数至少需要 `DDS_TOS_BUCKET`、`DDS_TOS_ENDPOINT` 和 `DDS_TOS_REGION`。当前部署脚本可把 `DDS_TOS_ACCESS_KEY_ID`、`DDS_TOS_SECRET_ACCESS_KEY` 作为 veFaaS 运行时环境变量注入；生产环境优先使用绑定到函数的短期身份/最小权限角色。部署机上的 `.env.volcengine` 仅供本地控制面读取，永远不得进入代码包。

### Step 3: 配置 TOS 事件触发器

在火山引擎 TOS 控制台 → dds-data-lake bucket → 事件通知：

```
触发器名称: dds-governance-trigger
事件类型:   tos:ObjectCreated:Put, tos:ObjectCreated:Post
前缀:       vault/
后缀:       .csv
目标:       dds-pipeline-governance (FC 函数)

触发器名称: dds-normalize-trigger
事件类型:   tos:ObjectCreated:Put, tos:ObjectCreated:Post
前缀:       vault/
后缀:       .csv
目标:       dds-pipeline-normalize (FC 函数)
```

### Step 4: 本地测试

```bash
# 测试治理函数（本地 CSV 文件）
python cloud/volcengine/fc_governance.py --csv "Vault/2026新楼盘/新楼盘-三亚.csv"

# 测试治理函数（模拟 TOS 事件）
python cloud/volcengine/fc_governance.py --object-key "vault/2026新楼盘/新楼盘-三亚.csv"

# 测试归一化函数（本地 CSV 文件）
python cloud/volcengine/fc_normalize.py --csv "Vault/2026新楼盘/新楼盘-三亚.csv" --city 三亚

# 测试 FC 调度器（dry-run）
python cloud/volcengine/pipeline_scheduler.py --run governance --mode fc --city 三亚

# 测试 FC 调度器（实际执行）
python cloud/volcengine/pipeline_scheduler.py --run governance --mode fc --city 三亚 --execute
```

### Step 5: 验证

上传一个 CSV 到 TOS vault/ 后，检查：

```bash
# 检查 governance/ 输出
python cloud/volcengine/tos_data_lake.py --list  # 查看 governance/ 分区

# 检查 normalized/ 输出
python cloud/volcengine/tos_data_lake.py --list  # 查看 normalized/ 分区

# 查看审计日志
# TOS: governance/_audit/{城市}_{timestamp}_governance.json

# 查看到溯源
# TOS: _meta/data_lineage.json
```

## TOS governance/ 分区结构

```
governance/
├── t7_clean/{城市}/{timestamp}_clean_report.json     # T7 清洗报告
├── t8_quality/{城市}/{timestamp}_quality_report.md    # T8 质量报告
├── t8_quality/{城市}/{timestamp}_data_card.md         # 数据卡片
├── t8_quality/{城市}/{timestamp}_data_feasibility.md  # 可行性评估
├── t8_quality/{城市}/{timestamp}_quality_scores.json  # 质量分数
├── gates/{城市}/{timestamp}_p0_gate.json              # P0 门禁结果
├── _alerts/{城市}_{timestamp}_p0_fail.json            # P0 失败告警
└── _audit/{城市}_{timestamp}_governance.json          # 完整审计日志
```

## TOS normalized/ 分区结构

```
normalized/
└── 2026新楼盘/新楼盘-{城市}.parquet    # 归一化 Parquet（zstd 压缩）
```

## 注意事项

1. **FC 超时**：默认 300s，大 CSV（>50MB）可能超时，需调大或分批处理
2. **T7-T10 零依赖**：治理脚本纯标准库，无需额外 pip install
3. **fc_normalize 需要 DuckDB**：需在 FC 层或函数包中包含 duckdb
4. **门禁失败不阻断**：P0 未通过时写告警但不回滚，数据仍入库
5. **本地索引不变**：BGE-M3 embedding 模型 ~2GB，继续本地运行 `ingest_vault.py`
6. **向后兼容**：`pipeline_scheduler.py` 默认 `--mode local`，原有行为不变
7. **历史对象不自动处置**：安全扫描只阻止新包继续上传，不会删除既有本地/远端 ZIP，也不会轮换已暴露凭证；这两项必须由有权限的人工变更单独完成
