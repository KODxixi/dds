# DDS v2 - 数据驱动的地产决策报告引擎

## 核心改进

### 1. 数据主动管理

**核心原则：绝对不允许静默留空**

当数据缺失时，系统自动按优先级进行多级降级填充：
- `真实数据` → 用户传入或从数据源获取的数据
- `城市基准` → 基于所在城市的行业基准数据
- `项目类型平均` → 基于同类项目的行业平均值
- `全国基准` → 全国通用的地产行业基准
- `待人工补充` → 明确标记需要人工补充的数据

### 2. 契约强制校验

任何报告输出前必须通过完整的契约校验：
- ✅ 12 个 decision-unit 完整性检查
- ✅ 每个 section 必填字段完整性
- ✅ 置信度计算完整性
- ✅ CS 章节必须包含数据缺口汇总
- ❌ 不通过校验的报告直接拒绝输出

### 3. 置信度透明化

每个字段都有来源标记，每个 section 都有 7 维度置信度计算：
- 数据源质量（0.25）
- 数据覆盖度（0.15）
- 新鲜度（0.15）
- 独立交叉验证（0.15）
- 地理相关性（0.15）
- 方法适配度（0.10）
- 稳定性（0.05）

## 架构设计

```
dds/
├── contracts.py            # 契约定义（报告结构、证据类型、置信度计算）
├── data/
│   ├── __init__.py
│   ├── orchestrator.py     # DataOrchestrator - 数据编排核心
│   ├── fetcher.py          # DataFetcher - 多数据源自动搜索
│   └── fallback.py         # FallbackEngine - 多级降级策略
└── engine/
    ├── __init__.py
    └── contract_enforcer.py # ContractEnforcer - 契约强制校验
```

## 快速开始

```python
import asyncio
from dds.data import DataOrchestrator
from dds.engine import ContractEnforcer

# 初始化
orchestrator = DataOrchestrator(city="北京", project_type="住宅")
enforcer = ContractEnforcer()

# 准备项目上下文
project_context = {"city": "北京", "project_type": "住宅"}

# 传入部分真实数据
existing_data = {
    "SC1": {
        "project_id": "PROJECT-001",
        "decision_question": "北京朝阳核心区高端住宅开发决策分析",
    }
}

# 自动填充所有 section
all_sections = asyncio.run(orchestrator.ensure_all_sections(project_context, existing_data))

# 执行契约校验
result = enforcer.validate_report(all_sections, {})
print(f"校验结果: {'PASS' if result.valid else 'FAIL'}")
print(f"整体置信度: {result.overall_confidence:.2f}")

# 获取数据质量报告
quality_report = orchestrator.get_data_quality_report(all_sections)
print(f"真实数据占比: {quality_report['summary']['real_ratio']:.1%}")
```

## 运行测试

```bash
python tests/test_core_functionality.py
```

## 12 个 Decision-Unit

| 组别 | ID | 名称 | 必填字段数 |
|------|-----|------|----------|
| **战略语境 (SC)** | | | |
| | SC1 | 投决命题与证据边界 | 4 |
| | SC2 | 市场机会、客群洞察与竞品实证 | 5 |
| | SC3 | 场地、法定条件与工程边界 | 3 |
| **产品方案 (AD)** | | | |
| | AD1 | 方案1／2／3强排比选 | 4 |
| | AD2 | 主推方案与决策闸门 | 3 |
| | AD3 | 产品定位、面积段与货量兑现 | 4 |
| | AD4 | 建筑与空间落地 | 5 |
| | AD5 | 传统空间文化与市场感知 | 3 |
| **价值校验 (VA)** | | | |
| | VA1 | 设计价值溢价 | 2 |
| | VA2 | 去化、现金流与投资验证 | 3 |
| | VA3 | 风险与实施闭环 | 4 |
| **置信状态 (CS)** | | | |
| | CS | 来源、方法与置信状态 | 5 |

## 核心 API

### DataOrchestrator

```python
# 准备单个 section
await ensure_section_data(section_id, project_context, existing_data)

# 准备所有 12 个 section
await ensure_all_sections(project_context, existing_sections)

# 获取整体置信度
calculate_overall_confidence(sections)

# 获取数据质量报告
get_data_quality_report(sections)
```

### ContractEnforcer

```python
# 校验报告
validate_report(sections, metadata)

# 强制执行契约，失败抛出异常
enforce_contract(sections, metadata)

# 获取校验摘要
get_validation_summary(result)
```

## 置信度等级

| 分数 | 等级 | 说明 |
|------|------|------|
| ≥ 0.75 | 高 | 数据质量良好，可用于决策 |
| ≥ 0.55 | 中 | 数据基本可用，建议补充 |
| ≥ 0.35 | 低 | 数据质量较差，谨慎使用 |
| < 0.35 | 不可判定 | 大部分为降级数据，建议人工补充 |

## 设计理念

### 消除静默空值

传统报告系统中，数据缺失时会留下空白，用户可能不会注意到。DDS v2 采取截然不同的策略：

> 数据缺失时，**绝对不静默留空**，而是：
> 1. 自动从多级数据源搜索
> 2. 使用最佳可用的降级值填充
> 3. **明确标记**数据来源和置信度惩罚
> 4. 在 CS 章节汇总所有数据缺口，提示用户需要补充哪些信息

### 契约驱动质量

契约不是建议，而是硬约束：
- 所有 12 个 section 必须完整
- 所有必填字段必须填充（即使是降级值）
- 所有 section 必须有置信度计算
- CS 章节必须包含完整的数据缺口清单

### 数据透明化

每个数据点都有可追溯的来源标记，用户可以清晰地看到：
- 哪些是真实数据
- 哪些是基于城市基准的推断
- 哪些是行业平均值
- 哪些数据需要人工补充

这种透明性让用户对报告质量有清晰的预期，而不是盲目相信"AI 黑箱"。
