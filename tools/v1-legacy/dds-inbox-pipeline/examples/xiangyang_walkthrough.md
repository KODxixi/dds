# DDS 襄阳项目会话复盘日志

> **会话时间**：2026-07-14 ~ 2026-07-15 凌晨  
> **参与方**：用户 + AI Agent（Antigravity）  
> **工作分支**：`codex/cesium-decision-field`  
> **合入主分支**：`main` @ commit `8a61f69`  

---

## 一、会话目标

用户交付了一批襄阳「国投·建华望府（星河国际）」投拓项目的原始资料，要求：

1. 完整解析全部项目文件并归档
2. 将汇报文本 PDF 转为单张 PNG（≤1MB）
3. 检查并全量更新 DDS Vault 襄阳数据库
4. 审查 ArchLib 数据标签与方案文本的匹配度
5. 审计 DDS 数据基座与治理体系的完备性
6. 修复发现的治理漏洞并合入主分支

---

## 二、工作时间线

| 阶段 | 事项 | 状态 |
|:---|:---|:---:|
| **Phase 1** | 原始资料解析 — 拆解 4 份核心文档（成本、规条、方案、基础资料） | ✅ |
| **Phase 2** | 工作区归档 — 建立 `inbox/` `reports/` `scratch/` 三级目录 | ✅ |
| **Phase 3** | 遗漏资料补充 — 发现并解析 `星河国际基础资料.pdf` | ✅ |
| **Phase 4** | PDF→PNG 导出 — 163 页汇报文本转 PNG，100% ≤ 1MB | ✅ |
| **Phase 5** | 数据库检查 — 确认 Vault 内襄阳数据存在但目标楼盘信息缺失 | ✅ |
| **Phase 6** | 全量数据更新 — 补全目标楼盘字段，修复 18 处坐标异常 | ✅ |
| **Phase 7** | ArchLib 标签体系审查 — 提出 `scene_part` 和 `product_feature` 扩展建议 | ✅ |
| **Phase 8** | 数据治理审计 — **发现重大漏洞：城市级空间门禁缺失** | ✅ |
| **Phase 9** | 治理漏洞修复 — 新增城市级 BBox 校验，修复冒烟测试 | ✅ |
| **Phase 10** | 合入主分支 — Cherry-pick → Fast-forward merge → main | ✅ |

---

## 三、关键成果

### 3.1 项目资料解析与归档

**输入**：用户提供的原始项目文件包（PDF、Excel、图片）

**输出**：

| 产出物 | 路径 | 说明 |
|:---|:---|:---|
| 成本分析报告 | [cost_analysis_report.md](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports/cost_analysis_report.md) | 24.9 KB，含拆迁成本、税费、IRR 测算 |
| 规条分析报告 | [regulation_analysis_report.md](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports/regulation_analysis_report.md) | 16.4 KB，含控规指标、日照、容积率 |
| 方案分析报告 | [scheme_analysis_report.md](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports/scheme_analysis_report.md) | 22.7 KB，含户型配比、四代住宅方案 |
| 基础资料补充报告 | [basic_info_supplement_report.md](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports/basic_info_supplement_report.md) | 9.3 KB，发现安置面积差异 3,237.65 ㎡ |
| 综合对接报告 | [xiangyang_synthesis_and_docking_report.md](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports/xiangyang_synthesis_and_docking_report.md) | 16.4 KB，四维度交叉校验 |
| 汇报文本 PNG | [slides_png/](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports/slides_png) | 163 张，总计 93.1 MB，**0 张超 1MB** |

### 3.2 数据库更新

**目标楼盘**：`国投·建华望府 (星河国际)` — Vault 第 87 行

| 字段 | 更新前 | 更新后 |
|:---|:---|:---|
| 楼盘名称 | 君合星河国际 | 国投·建华望府 (星河国际) |
| 开发商 | — | 湖北襄投置业有限公司 |
| 最新价格 | — | 9500 元/㎡ |
| 容积率 | — | 2.59 |
| 规划户数 | — | 1056 |
| 四代住宅 | — | 是 |

**坐标治理**：

| 异常类别 | 数量 | 修复方式 |
|:---|:---:|:---|
| 坐标为空 | 6 处 | 高德地理编码 API 回填 |
| 坐标越界 | 12 处 | 高德重新编码 + GCJ-02→BD-09 转换 |
| **合计** | **18 处** | 全部修复，QPS 控制 ≤ 1（延迟 1.5s/请求） |

### 3.3 数据治理漏洞修复（本次最关键发现）

> [!CAUTION]
> **重大发现**：原有的 P0 质量门禁和 T7 清洗管道仅使用全国大边界 `CHINA_BBOX` 校验坐标有效性。这意味着一个襄阳楼盘的坐标即使标注在河北唐山，只要它在中国境内，就能静默通过校验并入库。这会严重污染 GIS 选盘分析和 ABM 空间推演的结果。

**修复方案**：

```python
CITY_BBOXES = {
    "三亚":  (108.90, 18.10, 109.95, 18.65),
    "杭州":  (118.35, 29.18, 120.72, 30.55),
    "上海":  (120.85, 30.70, 122.10, 31.88),
    "青岛":  (119.40, 35.55, 121.05, 36.95),
    "济南":  (116.20, 36.00, 117.80, 37.55),
    "襄阳":  (110.70, 31.20, 112.70, 32.60),
}
```

**修复文件**：

| 文件 | 行数 | 作用 |
|:---|:---:|:---|
| [quality_gates.py](file:///c:/Users/shiguanyu/DDS/scripts/governance/quality_gates.py) | 778 行 | P0 入库门禁：按城市动态匹配 BBox，越界即 block |
| [t7_clean_pipeline.py](file:///c:/Users/shiguanyu/DDS/scripts/governance/t7_clean_pipeline.py) | 511 行 | T7 清洗管道：逐行标注 `anomaly`，越界挂起 |
| [test_smoke.py](file:///c:/Users/shiguanyu/DDS/test_smoke.py) | 6 行改动 | 适配 637 城数据库扩容（`北京` → `__不存在城市__`） |

### 3.4 测试修复

**根因**：数据库从最初的 6 城扩展到 637 城后，冒烟测试中将 `"北京"` 作为"不存在的城市"来测试校验拦截的假定失效了。

| 测试用例 | 失败原因 | 修复方式 |
|:---|:---|:---|
| `test_validation_bad_city` | "北京"已入库，`validate_input` 返回成功 | 改用 `"__不存在城市__"` |
| `test_validation_bad_city_endpoint` | 同上，API 返回 200 而非 400 | 同上 |
| `assert "三亚" in e` | 错误信息格式已变为动态文案 | 改为 `assert "数据池" in e` |

### 3.5 Git 合并流程

```mermaid
gitGraph
    commit id: "d05e944" tag: "veFaaS deploy"
    commit id: "cc51109" tag: "docs: define"
    commit id: "4355ce6" tag: "docs: plan"
    branch codex/cesium-decision-field
    commit id: "bde17f9" tag: "feat: location gateway"
    commit id: "1ef936a" tag: "feat: workflow API"
    commit id: "999d1fc" tag: "fix: governance"
    checkout main
    cherry-pick id: "8a61f69" tag: "fix: governance → main"
```

**操作步骤**：

1. `git commit` — 在 `codex/cesium-decision-field` 提交治理修复
2. `git stash` — 暂存 30+ 个未完工的 Cesium 开发文件
3. `git checkout -b hotfix/city-bbox-governance main` — 从 main 创建热修复分支
4. `git cherry-pick 999d1fc` — 挑选治理修复 commit
5. `git checkout main && git merge hotfix/city-bbox-governance` — Fast-forward 合并
6. `git checkout codex/cesium-decision-field && git stash pop` — 回到开发分支，还原工作区
7. `git branch -D hotfix/city-bbox-governance` — 清理临时分支

---

## 四、回归测试结果

```
======================================================================
                    全量测试回归报告汇总
======================================================================
 - pytest Flask API 冒烟测试集         | PASSED | 6.76s
 - pytest ABM 核心算法单元测试集       | PASSED | 14.75s
 - 端到端三亚真实地块投拓决策验证      | PASSED | 2.29s
 - 时空穿越与物理直读高保真验证        | PASSED | 0.77s
----------------------------------------------------------------------
 总耗时: 24.57s | 结果: 100% 绿灯通过
======================================================================
```

---

## 五、经验教训

### 5.1 治理漏洞应在数据库扩容时同步升级

| 教训 | 说明 |
|:---|:---|
| **根因** | 数据池从 6 城扩展到 637 城时，治理规则未同步升级。全国大边界 `CHINA_BBOX` 在少量城市时"碰巧够用"，但扩容后失去了区分能力。 |
| **改进** | 每次新增城市数据时，必须同步在 `CITY_BBOXES` 字典中注册该城市的局域边界。缺少 BBox 的城市仍降级到全国边界，但会在 P0 报告中标记为 `WARN: no city bbox`。 |

### 5.2 测试用例不应硬编码业务数据

| 教训 | 说明 |
|:---|:---|
| **根因** | 冒烟测试将 `"北京"` 硬编码为"不合法城市"，但数据库后续纳入了北京数据，测试假定静默失效。 |
| **改进** | 测试非法输入时，使用明确不可能命中的虚构城市名（如 `"__不存在城市__"`），避免与真实业务数据产生耦合。 |

### 5.3 高德 API QPS 限制须写入团队运维文档

| 教训 | 说明 |
|:---|:---|
| **根因** | 批量坐标回填时多次触发 `CUQPS_HAS_EXCEEDED_THE_LIMIT` 错误，浪费了约 10 分钟排查。 |
| **改进** | 已在脚本中强制 1.5s 延迟。建议将 QPS 限制（当前 key: 1 QPS）写入 [DATA_POOL.md](file:///c:/Users/shiguanyu/DDS/DATA_POOL.md) 的 API 约束章节。 |

---

## 六、遗留项与后续建议

| 优先级 | 事项 | 说明 |
|:---:|:---|:---|
| P1 | 同步 Parquet 至 TOS 云端 | `新楼盘-襄阳.parquet` 已在本地更新，需执行 `upload_tos_vault.py` 推送至火山引擎 TOS |
| P2 | ArchLib 标签扩展 | `tag_schema.json` 需新增 `空中花园/错层露台`、`抬板底盘/架空层` 场景标签 |
| P2 | 新增城市 BBox 自动注册 | 每次 `ingest_purchased.py` 导入新城市数据时，自动从高德行政区划 API 拉取边界并注册 |
| P3 | ABM 襄阳专项推演 | 使用 `xiangyang_indicators.json` 运行 `abm_engine.py`，输出樊城改善客群吸收曲线 |
| P3 | ArchLib 图像自动嵌入报告 | 在 `report_parcel.py` 中自动插入 ArchLib 案例库图片 |

---

## 七、文件变更汇总

### 新建文件

| 文件 | 说明 |
|:---|:---|
| [quality_gates.py](file:///c:/Users/shiguanyu/DDS/scripts/governance/quality_gates.py) | P0 入库质量门禁（含城市级 BBox） |
| [t7_clean_pipeline.py](file:///c:/Users/shiguanyu/DDS/scripts/governance/t7_clean_pipeline.py) | T7 清洗管道（含城市级坐标校验） |
| [convert_pdf_to_png.py](file:///c:/Users/shiguanyu/DDS/Test_襄阳/scratch/convert_pdf_to_png.py) | PDF→PNG 导出脚本 |
| [update_xiangyang_db.py](file:///c:/Users/shiguanyu/DDS/Test_襄阳/scratch/update_xiangyang_db.py) | 数据库更新与坐标回填脚本 |
| 7 份分析报告 | 见 [reports/](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports) 目录 |
| 163 张 PNG | 见 [slides_png/](file:///c:/Users/shiguanyu/DDS/Test_襄阳/reports/slides_png) 目录 |

### 修改文件

| 文件 | 说明 |
|:---|:---|
| [test_smoke.py](file:///c:/Users/shiguanyu/DDS/test_smoke.py) | 适配 637 城数据库，修正 3 处断言 |
| [新楼盘-襄阳.csv](file:///c:/Users/shiguanyu/DDS/Vault/2026新楼盘/新楼盘-襄阳.csv) | 补全目标楼盘、修复 18 处坐标（Vault/.gitignore） |
| [新楼盘-襄阳.parquet](file:///c:/Users/shiguanyu/DDS/Vault/2026新楼盘/新楼盘-襄阳.parquet) | 从 CSV 重新编译（Vault/.gitignore） |

---

> **会话结束时间**：2026-07-15 04:22  
> **main 分支最新 commit**：`8a61f69 fix(governance): enforce city-level coordinate bounding box checks and fix smoke tests`  
> **全量回归测试**：16/16 passed + 47/47 passed + E2E passed + 时空穿越 passed = **100% 绿灯**
