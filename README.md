# DDS V2

DDS V2 是唯一正式产品主线：以可追溯 EvidenceRecord、显式数据缺口、冻结编译和四道质量门为核心的地产决策报告系统。V1 仅作为只读数据与算法资产来源；根目录旧模块及 `dds.agents.generate_report` 不是正式交付入口。

## 环境与安装

- Python `>=3.11,<3.14`
- 使用 `uv` 管理锁定环境；开发组已包含完整测试依赖和 DuckDB

```powershell
uv sync --dev
```

安装后建议从项目目录之外验证导入，避免仓库目录掩盖打包问题：

```powershell
Push-Location $env:TEMP
python -c "import dds; import dds.domain; print(dds.__file__)"
Pop-Location
```

## 正式主线

正式链路固定为：

```text
真实数据适配器
  → EvidenceRecord / EvidencePackage
  → 业务引擎与 ReportService
  → ReportRun
  → ReportCompilerAdapter.build_frozen_compiler_package
  → V4 ReportDocument 离线编译
  → 与 HTML 字节哈希绑定的真实浏览器 QA
  → DeliveryService 原子交付
```

核心服务入口如下：

```python
from dds.config import Settings
from dds.data.repository import CompetitorQuery
from dds.domain import ProjectContext
from dds.services import (
    DeliveryService,
    ReportCompilerAdapter,
    ReportService,
    ResearchService,
)

settings = Settings.from_env()
context = ProjectContext(
    project_id="WH-001",
    project_name="武汉项目",
    city="武汉",
    project_type="住宅",
    base_date="2026-07-22",
)

# Collector/Resolver 只产生可追溯证据与确定性市场结果，不生成报告措辞。
research_service = ResearchService()
market = research_service.research_market(CompetitorQuery(city="武汉", limit=20))
evidence_snapshot = research_service.freeze_market(context, market)

# 产品与溢价引擎结果在此一并传入；输入不足时字段保持 unknown/not_assessable。
run = ReportService().assemble(
    run_id="WH-001-run-001",
    project_context=context,
    evidence=market.evidence,
    market=market.analysis,
)

package = ReportCompilerAdapter().build_frozen_compiler_package(
    run,
    required_units=("SC2",),
)
delivery = DeliveryService(settings.exports_root)

# delivery.render(package) 会先执行正式交付门；未就绪即抛错。
# 通过后必须对返回的 HTML 原始字节执行三视口、打印、图片、控制台检查，
# 并将官方 QA 报告绑定为 BrowserQAResult 后交给 delivery.deliver(...)。
```

正式流程必须通过 `BrowserQAResult.from_reports(...)` 从官方 `browser_qa.json` 与 `print_qa.json` 构造 QA 结果，随后由 `DeliveryService` 再次复核 HTML hash。手工构造的对象不构成交付证据，也不得用于正式发布。

## 三种项目介入方式

Input 1/2/3 是平行的任务类型，不是资料成熟度等级。系统可以根据地址、约束资料和方案文件给出 `recommended_mode`，但只有用户确认 `selected_mode` 并冻结 `InterventionBrief` 后才能运行；缺资料只会阻断相应结论，不会自动切换任务。

| 介入方式 | DDS 角色 | 决策报告主线 |
|---|---|---|
| Input 1 · 独立研判 | 从场地和市场独立提出方向 | 机会—客群—方向—压力测试—验证 |
| Input 2 · 约束协同 | 在任务书、规划和成本约束内联合策划 | 条件—矛盾—方案—产品操盘—闸门 |
| Input 3 · 方案审查 | 对多个已有方案进行同口径比选 | 基线—比选—价值风险—推荐淘汰—切换 |

同一项目可以由用户在不同阶段分别创建多个介入任务，已确认任务的 `selected_mode` 永不被系统改写。

## Research Control Center

DDS V2 提供本机运行的非技术研究界面。创建项目后，界面先询问“你希望 DDS 怎样介入这个项目？”，展示推荐理由并等待用户确认。确认后 Agent 才会拆解 Data Requirement Graph，并调用当前可用的数据源；搜索结果先进入 `candidate`，必须经过证据资格判定后才能成为报告事实。

```powershell
# 可选：启用 Tavily 外部网页搜索。密钥不会写入任务日志或报告。
$env:TAVILY_API_KEY = "your-key"

# 默认仅监听本机；当前版本未实现远程访问鉴权，禁止直接绑定公网地址。
uv run uvicorn dds.api.app:app --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`，可以创建研究任务、上传项目资料、查看数据源状态、运行日志和带来源哈希的候选证据。未配置的数据源会明确显示为不可用，不会生成假数据。

当前主动研究源：

- `curated-listing-database`：只读访问 V2 curated 楼盘数据，执行参数化城市竞品查询。
- `volcengine-data-search`：通过用户环境中的 `VOLCENGINE_ACCESS_KEY` / `VOLCENGINE_SECRET_KEY` 只读查询火山公开结构化数据；DDS 仅保留隐私白名单字段。
- `tavily-web`：通过 `TAVILY_API_KEY` 调用 Tavily Search API。

任务和上传资料默认写入 `data/projects/research-control-center/`；可用 `DDS_PRODUCT_ROOT` 指向其他 V2 管理目录。

### V1 真实项目迁移

`dds.projects.V1ProjectImporter` 以只读方式盘点 V1 原始资料，计算 SHA-256，复制为 V2 冻结快照，并为 PPTX、DOCX、XLSX、PDF、图片、视频和 DWG 建立可回溯定位或待复核状态。`LegacyProjectReportMigrator` 将客户决策报告与内部证据工作包分开生成；V2 后续运行只依赖冻结快照。

### 中国地产客群与数字人推演

`POST /api/analysis/simulate` 可选接收 `customer_intelligence`。该对象按城市、行政区、时点、版本和证据哈希冻结，只允许进入报告的聚合分群、选择模型和数字人实验结果；原始访谈、逐人轨迹、联系方式与模型思维链会被拒绝。

- C1：地方公开边际与真实客群证据，仅支持定性假设和补证设计。
- C2：同一冻结合成 cohort 上的选择模型与方案比较；choice share 不得直接换算月销量。
- C3：用真实到访、认购或签约漏斗校准后，才可进入风险调整和滚动复盘。

Input 1/2/3 分别输出机会假设、约束匹配和同口径方案比较，并统一补充 SC2 客群、AD3 数字人响应、VA2/VA3 压测与风险、CS 证据和禁止用途。模型模拟不能替代 SC2 的真实观察证据。

## 兼容工作稿入口

旧入口仍保留，目的是避免已有调用方立即中断：

```python
from dds.agents import generate_report
```

但它只生成文件名包含 `Work_Report` 的 `legacy_work_report` 工作稿。其返回值、产物 metadata 和 HTML 元数据都固定为：

```python
{
    "artifact_kind": "legacy_work_report",
    "delivery_ready": False,
}
```

这里的 `success=True` 仅代表工作稿文件写入成功，不代表证据有效、决策就绪或可正式交付。正式客户交付必须走上一节的 frozen V4 compiler + hash-bound browser QA + `DeliveryService`。

## 质量原则

DDS 的承诺是“缺失绝不静默”，不是“永不为空”。没有合格证据时，字段必须进入 `unknown`、`blocked`、`scenario` 或明确补证状态，禁止用无来源的城市／全国基准冒充事实。

报告依次经过四道闸门；决策报告还执行页面价值门，每页必须同时具备具体问题、非重复结论、证据或设计表达，以及决策影响或行动：

1. `structure_valid`：12 个 Decision-Unit 的结构与字段契约有效。
2. `evidence_valid`：证据类型、来源、时间、地域、单位和方法可追溯。
3. `decision_ready`：业务结论达到对应最低输入门槛。
4. `delivery_ready`：冻结包、编译器、资源安全及真实浏览器 QA 全部通过。

不得用单一 `valid=True` 把结构完整等同于正式可交付。

## V2 Curated 数据（只读）

DDS V2 默认读取仓库内 `data/curated/`。该目录由迁移工具生成，运行时只读，不会打入 wheel 或提交 Git。

```powershell
$env:DDS_DATASETS_ROOT = "D:\path\to\dds-v2-curated"
$env:DDS_EXPORTS_ROOT = "D:\path\to\dds-v2-exports"
python -m pytest tests/integration -q
```

运行时证据快照、缓存和报告产物位于 `data/` 或显式配置目录，不属于 Python 包发布内容。wheel 仅包含 `src/dds` 代码以及声明过的报告模板和 profile。

## 测试

```powershell
uv run ruff check --no-cache src tests tools
uv run pytest -p no:cacheprovider
```

只有 pytest 全绿，且目标产物完成哈希绑定浏览器 QA 后，才能进入正式交付阶段。
目录职责和可删除边界见 `PROJECT_STRUCTURE.md`；历史计划与 handoff 统一位于 `docs/`。
