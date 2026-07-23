# V1 Vault 内容审计与 V2 精简迁移提案

审计对象：`D:\Vault-assets\AI_Projects\DDS\Vault`

审计日期：2026-07-22

状态：**精简迁移已执行；V1 原库保留未删除**

## 1. 项目类型与目标

DDS V2 是 `Python 后端/本地应用 + data-research` 项目。判断证据：

- 正式代码采用 `src/dds`，依赖和测试由 `pyproject.toml`、`uv.lock`、`tests/` 管理。
- 运行时通过 DuckDB 读取楼盘、成交、宏观和土地 Parquet，并把记录转换为带来源的 EvidenceRecord。
- V1 Vault 同时混放查询数据、购买原包、抓取原文、媒体附件、预览生成物、日志和迁移备份。

V2 升级目标不是镜像 V1，而是建立职责唯一的三层边界：运行数据、研究知识资产、冷归档。

## 2. 审计覆盖与方法

- 全目录枚举：519,599 文件，65,807 目录，123.716 GiB；扫描错误 0，重解析点 0。
- 格式读取：1,017 个 Parquet、1,091 个 CSV 全部可读取；唯一 SQLite `quick_check=ok`。
- 清单核对：主 manifest 1,059 条，缺失路径 0，重复路径 0。
- 内容分类：按顶层/二级目录、扩展名、来源、信任级别、是否合成、运行时消费者分类。
- 媒体去重：使用 DCBBS SQLite 已记录的 SHA-256 计算真实重复内容。
- 双格式一致性：核对 1,017 对 CSV/Parquet 的行数和字段名。
- 文本内容：读取各层 manifest、README、QA 报告、错误清单和抓取日志；检查 SQLite 表、行数、状态和样例。

“内容审计”指对每个文件进行枚举/格式/清单归属检查，并对结构化记录、元数据和内容哈希进行全量统计；不等同于人工逐张观看 390,000 余张图片。

## 3. 关键发现

### 3.1 体量与职责混装

| 内容 | 文件数 | 体积 | 结论 |
|---|---:|---:|---|
| DCBBS | 472,331 | 104.802 GiB | 受限内部研究档案，不应进入 V2 live 数据 |
| 建筑案例内容 | 45,150 | 17.962 GiB | 独立知识包，不应与城市数据库混装 |
| 2026 新楼盘 | 1,276 | 0.321 GiB | V2 当前运行时需要 |
| 购买原包 | 4 | 0.217 GiB | 不可变原始归档，不作为直接查询层 |
| 其他年度/成交/宏观/土地 | 余量 | 约 0.4 GiB | 需按来源和消费者精简 |

### 3.2 主数据可信度

| 来源等级 | 文件/记录 | 处理建议 |
|---|---|---|
| L1 官方 | 5 个数据集，1,022 行 | 迁入 `curated/official` |
| L2 购买/派生 | 798 个数据集，约 160,000 行 | 原包进 `raw/purchased`，规范化结果进 `curated` |
| L3 合成 | 256 个数据集，370,672 行 | 不进入事实库；如需保留，仅进隔离 archive |

1995–2026 每年重复放置 L3 客群样本和画像；75 个 manifest 项为零行。合成数据必须与事实证据物理隔离。

### 3.3 CSV/Parquet 双轨问题

- 1,017 对 CSV/Parquet 中，1 对行数不一致：迁移前济南备份 CSV 14 行、Parquet 397 行。
- 740 对存在字段名漂移：CSV `建筑类型_1`，Parquet `建筑类型.1`。
- V2 不应继续维护双权威格式。应先固定 canonical schema，再从选定源重建单一 Parquet。

### 3.4 DCBBS 不一致与重复

- SQLite 实际包含 86,009 篇新闻、17,250 条资源、268,850 个新闻媒体记录。
- `manifest_latest.json` 仍声明新闻仅 5 篇、119,418 个新闻 ID 未处理，已落后于 SQLite 和后续抓取日志。
- 抓取状态另含 25,412 个 `link_gap`、8,993 个 `missing`；不能标记为完整档案。
- 按已记录 SHA-256，媒体可去重约 19.46 GiB：新闻图片 18.266 GiB、预览图 1.183 GiB、资源图 0.011 GiB。
- 权利范围为 `restricted/internal-research`，禁止作为客户交付素材默认外发。

### 3.5 建筑案例知识包

- 656 个项目、602 个标签、4,381 条关系、656 个 RAG 分块。
- 22,358 个内容寻址对象，占 17.87 GiB；已有 SHA-256 去重。
- 6 条原站 404 报告附件保留在错误清单；项目媒体关系无缺失。
- 该资产适合作为独立 `knowledge/architecture-cases`，不应并入城市市场数据库。

## 4. V2 最小目录树

```text
data/
├── raw/                         # 不可变来源，仅内部保存，不直接查询
│   └── purchased/
├── curated/                     # V2 唯一运行查询层
│   ├── listings/
│   ├── transactions/
│   ├── macro/
│   └── land/
├── knowledge/                   # 研究知识资产，与事实数据库隔离
│   ├── architecture-cases/
│   └── dcbbs/
│       ├── index/
│       └── objects/
└── manifests/                   # 数据集级来源、哈希、schema、许可和生成关系
```

`archive/` 不放进 DDS V2 仓库目录；冷归档保留在 V1 隔离区或独立外部归档根，避免运行边界再次膨胀。

## 5. 文件落位表

| 文件类别 | 权威目录 | Git | 允许消费者 | 禁止事项 | 理由 |
|---|---|---|---|---|---|
| 购买原始 CSV | `data/raw/purchased` | ignore | 数据构建工具 | 运行时直接查询、修改原文件 | 保留来源真相 |
| 规范化楼盘 Parquet | `data/curated/listings` | ignore | listings adapter | CSV/Parquet 双权威 | V2 查询层 |
| 成交 Parquet | `data/curated/transactions` | ignore | transaction adapter | 与挂牌价格混用 | 观测类型隔离 |
| 官方宏观 Parquet | `data/curated/macro` | ignore | macro adapter | 丢失行级 URL/发布日期 | L1 证据 |
| 官方土地 Parquet | `data/curated/land` | ignore | land adapter | 自动回填宗地成交价 | 口径不同 |
| 建筑案例规范化 JSONL/索引 | `data/knowledge/architecture-cases` | ignore | 研究/RAG adapter | 当市场事实使用 | 独立知识域 |
| DCBBS SQLite/索引 | `data/knowledge/dcbbs/index` | ignore | 内部研究检索 | 客户外发、宣称完整 | 受限且 partial |
| DCBBS 去重媒体对象 | `data/knowledge/dcbbs/objects` | ignore | DCBBS 索引 | 按文章重复保存 | 内容寻址 |
| 合成客群数据 | 外部隔离 archive | external | 明确的模拟实验 | 进入 EvidenceRecord 事实链 | L3 合成 |
| 日志、重试记录、预览缓存 | 不迁入；必要摘要进 manifest | external | 审计人员 | 进入运行数据 | 可重建/过程状态 |

## 6. 迁移表（待确认）

| V1 当前路径 | V2/归档目标 | 动作 | 影响 | 验证 | 回滚 |
|---|---|---|---|---|---|
| `_purchased/*.csv` | `data/raw/purchased/` | 复制，保持只读与哈希 | 新增 raw 根 | SHA-256、行列数 | 删除 V2 副本 |
| `2026新楼盘/` | `data/curated/listings/` | 按 canonical schema 重建 Parquet；不复制 CSV 双份 | 更新 catalog 路径 | schema、行数、城市查询测试 | 切回 V1 环境变量 |
| `成交数据/*.parquet` | `data/curated/transactions/` | 复制有效 Parquet | 更新 adapter 根 | 20,307 行及接口测试 | 切回旧根 |
| `宏观数据/*.parquet` | `data/curated/macro/` | 复制 L1 数据和 manifest | 更新 adapter 根 | 行级来源字段检查 | 切回旧根 |
| `土地数据/*.parquet` | `data/curated/land/` | 复制 L1 数据和 manifest | 更新 adapter 根 | 8 行及口径测试 | 切回旧根 |
| `1995年`–`2026年` 客群样本/画像 | 外部隔离 archive | 不迁入 V2 | 移除事实库可见性 | 搜索无 L3 live 路径 | 保留 V1 原件 |
| `_backup_premigration/` | 外部隔离 archive | 不迁入 V2 | 无运行影响 | 记录哈希与冲突说明 | 保留 V1 原件 |
| `建筑案例内容/kmlovemilk` | `data/knowledge/architecture-cases/` | 迁规范化 JSONL、索引、manifest；媒体对象独立复制 | 后续新增 adapter | 关系完整性、对象哈希 | 保留 V1 原件 |
| `DCBBS/normalized/dcbbs.sqlite3` | `data/knowledge/dcbbs/index/` | 先重建一致 manifest，再复制 | 后续新增内部 adapter | quick_check、表计数、权限标签 | 保留 V1 原件 |
| `DCBBS/*media*`、`previews` | `data/knowledge/dcbbs/objects/` | 按 SHA-256 内容寻址去重迁移 | SQLite local_path 需重写 | 对象哈希、引用闭包 | 保留 V1 原件 |
| `DCBBS/raw`、日志、旧 manifests | 外部隔离 archive | 不进入 V2 live；只保留审计摘要 | 无运行影响 | 最新状态与失败数入新 manifest | 保留 V1 原件 |

## 7. 风险与执行门禁

1. 未先固定 `建筑类型` 重复字段的 canonical 命名，不迁楼盘主池。
2. 未重建 DCBBS manifest 并闭合 86,009 篇新闻引用，不迁 DCBBS。
3. 未确认 `restricted/internal-research` 权限策略，不把 DCBBS/建筑案例接入客户交付链。
4. 每批采用“复制 → 哈希/行数/引用验证 → 切换配置 → 测试 → V1 保留隔离副本”；不直接删除源目录。
5. V2 当前有用户未提交改动；迁移只修改明确的数据配置、测试和结构文档，不覆盖现有工作。

## 8. 已执行验证

- CSV 全量解析：1,091/1,091 通过。
- Parquet 全量读取：1,017/1,017 通过。
- SQLite：`PRAGMA quick_check = ok`。
- V2 现有 V1 访问链路：4 个针对性测试通过。
- 已执行：637 个 canonical 楼盘 Parquet、成交/宏观/土地数据、建筑案例规范化知识和 DCBBS 高价值文本迁移。`n- DCBBS 仅保留 86,005 篇有效正文、17,250 条资源元数据、24 条公开来源匹配；媒体与 raw 镜像未迁入。`n- 迁移结果：663 个 manifest 数据文件，631,251,388 bytes；逐文件 SHA-256 校验异常 0。`n- V2 默认路径已切换为 `data/curated`；V1 原库未修改、未删除。
