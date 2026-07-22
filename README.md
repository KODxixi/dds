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

报告依次经过四道闸门：

1. `structure_valid`：12 个 Decision-Unit 的结构与字段契约有效。
2. `evidence_valid`：证据类型、来源、时间、地域、单位和方法可追溯。
3. `decision_ready`：业务结论达到对应最低输入门槛。
4. `delivery_ready`：冻结包、编译器、资源安全及真实浏览器 QA 全部通过。

不得用单一 `valid=True` 把结构完整等同于正式可交付。

## 武汉 V1 Vault（只读）

真实武汉数据通过环境变量指向现有 V1 Vault。DDS V2 不修改、不迁移该目录，也不会将其中内容打入 wheel。

```powershell
$env:DDS_V1_VAULT_ROOT = "D:\path\to\read-only-v1-vault"
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
