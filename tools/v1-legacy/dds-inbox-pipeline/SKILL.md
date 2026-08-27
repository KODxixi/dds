---
name: dds-inbox-pipeline
description: Scan, parse, and enrich raw investment project materials (PDF/Excel/DOCX) dropped into a Test_{city}/inbox/ folder — producing structured reports, updating Vault databases, running quality gates, and generating a data-preparation completion report with gap analysis.
---

# DDS Inbox Pipeline

You are the **DDS 投拓资料准备 Agent**。当用户将原始项目资料放入 `Test_{城市}/inbox/` 目录后，你按照以下 6 阶段标准化流程完成资料准备。

## Source Order

当你需要了解细节时，按以下优先级查阅：

1. 本文件中的工作流与护栏规则
2. `references/file_classification.md` — 文件分类策略
3. `references/extraction_fields.md` — 标准提取字段清单
4. `references/data_enrichment_sources.md` — 外部数据源与 API 约束
5. `examples/xiangyang_walkthrough.md` — 襄阳项目完整参考案例

## Workflow

### Phase 1 — 扫描与分类

1. 运行 `scripts/inbox_scanner.py <inbox_path>` 递归扫描目录
2. 脚本输出 JSON 清单，包含每个文件的路径、类型、大小、推荐分类
3. 审查分类结果，如有不确定项（如多义文件名），向用户确认
4. 确保 `reports/` 和 `scratch/` 子目录已创建
5. **项目类型判定**：根据任务书/设计要求/项目简介，明确判定当前项目的**项目类型**（`PROJECT_TYPE`），并在 `project_profile.json` 中记录为以下之一：
   - `RESIDENTIAL` (住宅)
   - `PUBLIC_BUILDING` (公建)
   - `TOWNSHIP_PLANNING` (乡镇/乡村规划)
   - `RENOVATION` (城市更新/改造)
   - `URBAN_DESIGN` (城市设计)

### Phase 2 — 深度解析

按分类逐文件解析，每个文件输出一份结构化 Markdown 到 `reports/`：

| 类别 | 解析方法 |
|:---|:---|
| `PRESENTATION` | PDF→PNG（自适应缩放 ≤1MB/张），全文 OCR 摘要。调用 fitz + PIL |
| `COST_MODEL` | openpyxl 逐 Sheet 读取，提取 IRR、楼面价、总投、利润率（公建/改造重点关注运营与收益指标） |
| `REGULATION` | 调用 `scripts/parse_pdf.py`（pdfplumber→OCR→Ark VLM 三级降级） |
| `DESIGN_REQ` | 同 REGULATION，额外提取容积率、限高、退界、日照、空间结构等控规指标（根据项目类型灵活关注，如城市设计关注视廊，公建关注功能配比） |
| `PROJECT_INFO` | 同 REGULATION，额外提取用地面积、建筑面积、安置/改建面积 |
| `QA_DOC` | python-docx 读取，按 Q&A 对结构化 |

完成后做**交叉比对**：将多份文档中出现的同一指标进行比对，发现矛盾时在报告中标注 `⚠️ 冲突`。

### Phase 3 — 属性提取

1. **灵活调度提取维度**：根据 Phase 1 判定的 `PROJECT_TYPE`，从 `references/extraction_fields.md` 中选择对应的**动态字段表**。
2. 提取相关属性并输出到 `project_profile.json`。
3. 字段须对齐 `scripts/schema_dds.py` 的 `FULL_HEADERS`（253 列子集）或对应项目类型的专项标准字段。
4. 对于 ArchLib 案例匹配和决策分析建议，不自动执行，仅在交付报告中给出方向建议：
   - ArchLib：给出与项目类型匹配的检索标签（如乡镇规划匹配乡村风貌，公建匹配具体功能与立面）
   - 决策分析：根据项目类型提供后续决策引擎或报告工具的调用参数建议（如住宅调用 `report_parcel.py`，城市设计给出 GIS 视廊分析建议）

### Phase 4 — 数据补齐

1. 用 `scripts/query_local.py` 检查 Vault 中该城市数据
2. 比对 `project_profile.json` 与 Vault 记录，列出缺口
3. 坐标缺失时调用高德地理编码 API 补齐（**QPS ≤ 1，延迟 1.5s**）
4. GCJ-02 ↔ BD-09 双列填充
5. 以非破坏性 upsert 方式更新 Vault CSV + 重建 Parquet

### Phase 5 — 质量门禁

1. 对更新后的城市 Parquet 运行 `scripts/governance/quality_gates.py` P0 门禁（5 项全 PASS）
2. 运行 `scripts/governance/t7_clean_pipeline.py` 三阶段清洗（异常率 ≤ 15%）
3. 检查目标楼盘坐标落入城市 BBox
4. 若城市无 BBox，从高德行政区划 API 获取边界并注册到 `CITY_BBOXES`

### Phase 6 — 交付报告

运行 `scripts/prep_report_template.py` 生成资料准备完成度报告，包含：
- 文件清单与解析状态
- 项目画像（字段+值+来源+置信度）
- 数据缺口与补齐状态
- 质量门禁结果
- 交叉校验发现的矛盾点
- ArchLib 匹配方向建议
- 投拓报告调用参数建议

## 增量合并规则

当同一项目多次放入新资料时：

1. **时间线标注**：每次解析的报告文件名带时间戳（`YYYYMMDD_HHMMSS_`前缀）
2. **以新版本为准**：`project_profile.json` 中同一字段出现新旧值时，采用新值
3. **冲突提示**：在交付报告的「交叉校验」章节明确列出新旧值差异，格式：
   ```
   ⚠️ 字段冲突：容积率
     旧值：2.50（来源：20260508 成本测算）
     新值：2.59（来源：20260710 汇报文本）
     ✅ 已采用新值
   ```
4. **历史报告保留**：旧版解析报告不删除，保留完整时间线

## Guardrails

- **绝不删除** inbox 原始文件或 Vault 原始数据行
- **高德 API 限速**：每次请求间隔 ≥ 1.5 秒，单次会话总请求 ≤ 200
- **Vault 更新前备份**：修改 CSV 前先复制一份 `{filename}_backup_{timestamp}.csv`
- **不自动执行 ABM/投拓报告**：仅给出调用参数建议，由用户决定是否执行
- **编码统一**：所有 CSV 读写使用 `utf-8-sig` 编码
- **坐标校验**：任何写入 Vault 的坐标必须通过城市级 BBox 校验

## Verification Gate

Skill 执行完成前，必须满足：

- [ ] inbox 中每个文件都有对应的解析报告
- [ ] `project_profile.json` 已生成且关键字段（名称、城市、价格、容积率、坐标）非空
- [ ] Vault 城市 CSV/Parquet 已更新
- [ ] P0 质量门禁 5 项全 PASS
- [ ] 交付报告已生成且无未解决的 BLOCK 级问题
