# Handoff: DDS v2 三类输入与操盘优化升级策略

- Saved: 2026-07-23T00:40:38+08:00
- Project: D:\Vault-assets\AI_Projects\DDS_v2
- Branch/revision: master / a51e7d8f542b13201858dff0e4d88bb126d9a0a2

## Goal

给出 DDS v2 接下来的升级策略，并保存可由后续 Agent 直接接续的 handoff。最新产品目标是：根据 Input 1/2/3 的资料成熟度输出不同深度的前策报告，同时生成多种产品定位、设计、营销与推售策略，联合优化去化、实现货值、回款和风险，而不是只追求最快去化或最高静态货值。

## Completed

- 新建正式策略：`docs/plans/DDS_v2三类输入与操盘优化升级策略-2026-07-23.md`。
- 将 `docs/plans/DDS_v2数据驱动升级计划.md` 标记为历史方案，废止“所有字段永远有值”和城市／全国通用值自动补齐方向。
- 将 `docs/plans/DDS核心文件迁移与架构升级计划.md` 标记为历史迁移方案。
- 将 `docs/handoffs/2026-07-19_1730_DDS_v2引擎迁移阶段3.md` 标记为历史 handoff。
- 在 `docs/plans/implementation-status-2026-07-22.md` 说明 M0–M6 只代表真实性、报告和交付基础设施完成，不代表三类 Input、VA2 或操盘优化已经实现。
- 只读审计当前 RequirementAgent、ContractEnforcer、ProductEngine、MarketEngine、ABM、PremiumEngine、ReportService、ReportCompilerAdapter、ReportDocument、产品 API 和相关测试。

## Decisions

1. 使用“整体 Input 模式 + 模块级证据门禁”，不使用三套彼此割裂的技术链。
2. Input 1 是场地机会研判，使用标准 100 套、三种市场情景和相对指数；不得输出项目人民币总货值、确定售罄、正式合规或投资承诺。
3. Input 2 是约束驱动前策；明确材料只提升对应字段，不把整个项目自动提升为已验证。
4. Input 3 是方案决策比选；至少两个方案，并允许“现有方案 + DDS 替代方向”。
5. 代码使用 `analysis_profile.requested_level / assessed_level / effective_level`，界面显示 Input 1/2/3；不用裸 `mode`，避免与 ProductMode 和阅读／演示 mode 冲突。
6. profile registry 同时决定 `required / optional / excluded` unit policy、Requirement Graph 和 quantitative authority；由 RequirementAgent、ContractEnforcer、ReportService、ReportCompilerAdapter 和 ReportDocument 共用。required 参与门禁，required 加有内容的 optional 形成 included pages。
7. `delivery_ready` 必须绑定 `decision_scope`。Input 1 可以对机会筛选可交付，但拿地、定价和投资门仍关闭。
8. 默认形成三套操盘策略 × 保守／基准／积极三种市场情景；只展示 Pareto 非支配方案、推荐方案和关键反例。
9. 推荐路线是“产品组合 + 价格阶梯 + 供货顺序 + 营销动作 + 切换触发器”，不只是 A/B/C 标签。
10. 唯一新增计算主链为：月度去化 → 实现货值 → 现金回款 → 风险调整 NPV → Pareto 非支配策略 → 动态操盘触发器。
11. 大模型负责提出、批判和解释策略；确定性引擎负责数字、勾稽、比较、冻结和门禁。Writer 不得新增数字。
12. 用户未确认风险偏好时只输出条件式推荐；`λ`、CVaR `α`、硬约束和目标方向必须显式冻结，不能成为隐藏常量。

## Current state

- 项目版本：`dds-v2 2.1.0`。
- 当前只有一个 worktree：`D:/Vault-assets/AI_Projects/DDS_v2`。
- staged changes：无。
- 工作区在本次策略任务之前已经存在大量未提交代码和数据迁移改动，包括 API、settings、data adapters、product/research/web、新测试和 `tools/dev/migrate_v1_vault.py`；不得回退或覆盖。
- 本次新增／修改仅是策略、状态标记和本 handoff，没有实现 M7–M12 代码。
- 当前可信能力：真实性模型、EvidencePackage、V4 ReportDocument、hash-bound QA、概念方向／现有方案审计、静态货值勾稽、SC2 挂牌价带、ABM choice share/WTP、VA1 售价溢价三情景和研究任务入口。
- 当前明确缺口：没有 InputProfile 分类器；Requirement Graph 固定停在 VA1；ContractEnforcer 不是 profile-aware；ABM 不是月度去化模型；VA2 未接 ReportService；没有现金流、风险调整 NPV、Pareto 或动态触发器。

## Verification

- `uv run ruff check --no-cache src tests tools`：通过。
- `uv run pytest -p no:cacheprovider`：145 passed，1 个 Starlette/httpx2 deprecation warning，1.18s。
- `git diff --check`：无空白错误；仅显示现有 Windows LF→CRLF 警告。
- 已核实 V4 ReportDocument 支持 adaptive `required_units` 与 absorption/investment 槽位，但 RequirementAgent、ContractEnforcer、ReportService 和 ReportCompilerAdapter 尚未形成同一模式闭环。
- 未执行新策略的运行时、浏览器或财务模型验证，因为对应代码尚未实现。

## Remaining work

### M7：输入画像与 profile-aware 四门

- 冻结 `InputProfile`、`AnalysisProfile`、`DecisionScope` 和 profile registry。
- 改造 RequirementAgent，按 profile 生成 SC/AD/VA/CS requirement graph。
- 改造 ContractEnforcer，使结构、证据、决策、置信度四门使用相同 required/optional/excluded unit policy。
- 改造 ReportService.assemble，保存 requirements、analysis_profile、判级依据、模型版本和策略目标。
- 将 mode、decision_scope、分类依据和 quantitative authority 固化进 EvidencePackage/ReportDocument meta。
- 用 Input 1/2/3、冲突、过期和缺位置 fixtures 验证确定性分类。

### M8：Input 1 操盘实验室

- 冻结 StrategyCandidate、MarketScenario、SimulationAssumptions、MonthlyResult 契约。
- 实现标准 100 套月度库存／去化模型，不能由 ABM choice share 直接换算销量。
- 实现三策略 × 三市场情景、相对价格／货值／回款指数、营销漏斗变量和基础非支配筛选。
- 验证库存、去化、回款不变量以及固定参数／seed 可复现。

### M9–M10：真实货量、现金流、风险与 Pareto

- Input 2 接约束登记、分批次／分户型、客户池衰减、渠道容量、季节性和尾盘。
- 建立成交货值、回款时滞、成本、税费、融资和资金峰值模型，避免 VA1 成本／溢价双计。
- Input 3 归一化两个以上方案，冻结硬约束、目标向量、CVaR 与稳定排序；全方案不合格时返回无推荐。

### M11–M12：主链接入、滚动校准和真实验证

- ReportService、ReportCompilerAdapter、API 和 ReportDocument 接入 VA2 及策略前沿页面。
- 按时间切分历史销售回测并与朴素基线比较。
- 完成一个历史项目回放、一个当前项目盲测，以及 Input 1/2/3 全链冻结、双编译、三视口和打印 QA。

## Risks and blockers

- 当前本地楼盘库主要提供挂牌截面；虽有原始去化字段，但尚未形成可复现的 30/90/180 天供应、价格、库存、促销、认购和签约时序，不能直接校准月度去化。
- 武汉 VA1 仍缺可售面积、增量成本、结果变量和方法证据，只能作为 Input 2/3 补证案例，不能人为升级。
- 没有资料完整的历史销售项目时，可以完成 M7/M8 的标准化压力测试，但不能证明 M9–M12 的项目级预测准确性。
- 当前一般交付 validator 明确不授予 investment/pricing/land-bid/IRR/ROI 权限；投决级能力未来需要独立模型验证 attestation 和财务／营销／成本人审。
- 工作区有大量未提交改动；开始实现前应先确认这些改动的归属和版本冻结方式，不得用 reset/clean 清理。

## Next action

从 M7 开始，不先写去化公式：先建立唯一 profile registry 及三类 fixture，让 RequirementAgent、ContractEnforcer、ReportService、ReportCompilerAdapter 和 ReportDocument 对同一 required/included unit policy 得出完全一致的结果。第一条端到端验收应证明：Input 1 对 `opportunity_screening` 可交付，同时人民币货值、确定售罄、法定合规和投资门禁保持关闭。
