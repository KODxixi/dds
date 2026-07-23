# DDS v2 数据驱动升级计划

> **状态：历史方案，已被 `DDS_v2三类输入与操盘优化升级策略-2026-07-23.md` 替代。** 本文中的“所有字段永远有值”和城市／全国通用基准降级不再是有效产品策略；当前实现以真实性模型、未知值合法化和 fail-closed 门禁为准。

## 一、核心问题诊断

| 问题 | 根因 | 解决方向 |
|------|------|----------|
| **报告输出不统一** | 契约只有定义，没有强制执行机制 | 引入契约校验层，任何输出必须通过契约校验 |
| **数据经常缺失** | 被动等待上游数据，缺失时直接留空 | 建立主动数据管理层：检测 → 搜索 → 降级 → 标记 |
| **Agent 不主动** | Agent 只管生成，不管数据质量 | Agent 必须先保证数据完整性，才能生成内容 |

---

## 二、架构设计：三层保障体系

```
┌─────────────────────────────────────────────────────────┐
│                ③ 契约强制输出层                          │
│  ─────────────────────────────────────────────────────  │
│  • 输出前必须通过 12 个 decision-unit 契约校验           │
│  • 任何数据缺失必须标注置信度，不能静默留空               │
│  • 统一的降级文案和补充数据提示                           │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                ② 主动数据管理层                          │
│  ─────────────────────────────────────────────────────  │
│  • DataOrchestrator: 检测每个 section 数据完整性         │
│  • DataFetcher: 自动从本地库/向量库搜索缺失数据           │
│  • FallbackEngine: 多级降级策略（真实数据 → 基准值 → 提示）│
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                ① 证据契约基础层                          │
│  ─────────────────────────────────────────────────────  │
│  • 12 decision-units 严格定义（复用旧版）                │
│  • 7 维度置信度计算（复用旧版）                          │
│  • 证据链追溯（复用旧版）                                │
└─────────────────────────────────────────────────────────┘
```

---

## 三、核心模块设计

### 模块 1：数据编排器 (DataOrchestrator)

**职责**：在报告生成前，主动管理每个 decision-unit 的数据完整性

```python
class DataOrchestrator:
    """主动数据管理核心"""

    # 每个 decision-unit 的必填数据字段
    SECTION_REQUIREMENTS = {
        "SC1": ["project_id", "decision_question", "evidence_boundary", "base_date"],
        "SC2": ["macro_indicators", "competitors", "customer_segments", "positive_cases", "negative_cases"],
        "SC3": ["redline", "regulations", "engineering_constraints"],
        "AD1": ["option_1", "option_2", "option_3", "comparison_matrix"],
        "AD2": ["recommended_option", "elimination_reasons", "validation_thresholds"],
        "AD3": ["product_mix", "area_segments", "price_bands", "sales_rhythm"],
        "AD4": ["site_plan", "floor_plans", "facade", "landscape", "show_area"],
        "AD5": ["traditional_factors", "market_perception", "prohibited_uses"],
        "VA1": ["premium_factors", "cost_value_chain"],
        "VA2": ["sales_forecast", "cash_flow", "investment_metrics"],
        "VA3": ["risks", "owners", "triggers", "acceptance_criteria"],
        "CS": ["sources", "methods", "assumptions", "confidence_gaps"],
    }

    async def ensure_section_data(self, section_id: str, project_context: dict) -> SectionData:
        """确保该 section 有完整数据，否则触发搜索或降级"""
        # 1. 检测已有数据
        existing_data = self._extract_existing_data(section_id, project_context)

        # 2. 检测缺失字段
        missing_fields = self._detect_missing_fields(section_id, existing_data)

        # 3. 主动补齐缺失字段
        for field in missing_fields:
            fetched = await self.data_fetcher.fetch(section_id, field, project_context)
            if fetched["found"]:
                existing_data[field] = fetched["data"]
            else:
                # 触发降级策略
                existing_data[field] = self.fallback_engine.get_fallback(section_id, field)

        # 4. 计算该 section 的置信度
        confidence = self._calculate_section_confidence(section_id, existing_data)

        return SectionData(
            section_id=section_id,
            data=existing_data,
            confidence=confidence,
            missing_fields=missing_fields,
            data_origin=self._record_origin(existing_data),  # 标记每个字段的来源
        )
```

---

### 模块 2：数据抓取器 (DataFetcher)

**职责**：自动从多个数据源搜索缺失数据

```python
class DataFetcher:
    """多数据源自动搜索"""

    SOURCE_PRIORITY = [
        "local_parquet_db",      # 1. 本地楼盘数据库（最高优先级）
        "vector_evidence",       # 2. 向量证据库
        "city_benchmarks",       # 3. 城市基准数据
        "project_history",       # 4. 同项目历史数据
    ]

    async def fetch(self, section_id: str, field: str, context: dict) -> FetchResult:
        """按优先级搜索数据，直到找到"""
        city = context.get("city", "")
        project_type = context.get("project_type", "")

        for source in self.SOURCE_PRIORITY:
            result = await self._search_source(source, section_id, field, city, project_type)
            if result["found"]:
                return {**result, "source": source}

        return {"found": False, "field": field}
```

---

### 模块 3：降级引擎 (FallbackEngine)

**职责**：当数据完全缺失时，提供多级降级策略，**绝对不允许静默留空**

```python
class FallbackEngine:
    """多级降级策略，确保永远有内容输出"""

    FALLBACK_STRATEGIES = {
        # 级别 1: 使用城市基准值（带明确标注）
        "city_benchmark": {
            "quality": "synthetic",
            "confidence_penalty": 0.3,  # 置信度惩罚
            "label": "【城市基准数据】",
        },
        # 级别 2: 使用同类项目平均值
        "project_type_average": {
            "quality": "synthetic",
            "confidence_penalty": 0.4,
            "label": "【同类型项目平均值】",
        },
        # 级别 3: 使用全国通用基准值
        "national_benchmark": {
            "quality": "synthetic",
            "confidence_penalty": 0.5,
            "label": "【全国基准数据】",
        },
        # 级别 4: 明确提示需人工补充（最低质量）
        "human_input_required": {
            "quality": "placeholder",
            "confidence_penalty": 0.8,
            "label": "【待人工补充】",
            "template": "该字段暂无数据，请提供{description}相关资料。",
        },
    }

    def get_fallback(self, section_id: str, field: str) -> dict:
        """获取最合适的降级值"""
        # 根据字段类型选择降级策略
        strategy = self._select_fallback_strategy(field)

        return {
            "value": self._generate_fallback_value(section_id, field, strategy),
            "is_fallback": True,
            "fallback_strategy": strategy,
            "label": self.FALLBACK_STRATEGIES[strategy]["label"],
            "confidence_penalty": self.FALLBACK_STRATEGIES[strategy]["confidence_penalty"],
            "action_required": strategy == "human_input_required",
        }
```

---

### 模块 4：契约强制校验层 (ContractEnforcer)

**职责**：任何报告输出前必须通过校验，**不允许输出不符合契约的内容**

```python
class ContractEnforcer:
    """强制保证输出符合 DDS 契约"""

    def validate_report(self, report_data: dict) -> ValidationResult:
        """完整报告校验，不通过则拒绝输出"""
        errors = []
        warnings = []

        # 1. 检查 12 个 decision-unit 是否全部存在
        for section_id in VALID_SECTION_IDS:
            if section_id not in report_data["sections"]:
                errors.append(f"缺少必填章节: {section_id}")
                continue

            section = report_data["sections"][section_id]

            # 2. 检查必填字段
            required_fields = DataOrchestrator.SECTION_REQUIREMENTS[section_id]
            for field in required_fields:
                if field not in section["data"]:
                    errors.append(f"章节 {section_id} 缺少必填字段: {field}")

            # 3. 检查置信度计算
            if "confidence" not in section:
                errors.append(f"章节 {section_id} 缺少置信度计算")

            # 4. 检查降级标记 - 任何降级必须明确标注，不能隐藏
            if any(item.get("is_fallback") for item in section["data"].values()):
                warnings.append(f"章节 {section_id} 包含降级数据，已自动标注")

        # 5. 检查 CS 章节 - 必须明确列出所有数据缺口
        if "CS" in report_data["sections"]:
            cs_section = report_data["sections"]["CS"]
            if "data_gaps" not in cs_section["data"]:
                errors.append("CS 章节必须列出所有数据缺口")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "overall_confidence": self._calculate_overall_confidence(report_data),
        }

    def enforce_contract(self, report_data: dict) -> dict:
        """强制执行契约，不通过则抛出异常"""
        result = self.validate_report(report_data)
        if not result["valid"]:
            raise ContractViolationError(
                f"报告不符合 DDS 契约规范: {result['errors']}"
            )
        return report_data
```

---

## 四、目录结构（聚焦核心）

```
📁 DDS_v2/
├── 📁 src/dds/
│   ├── 📄 __init__.py
│   ├── 📄 contracts.py                  # 契约定义（从旧版迁移并增强）
│   │   ├── REPORT_GROUPS
│   │   ├── REPORT_UNITS
│   │   └── CONFIDENCE_WEIGHTS
│   ├── 📁 data/
│   │   ├── 📄 orchestrator.py           # ✅ 新增：数据编排器（核心）
│   │   ├── 📄 fetcher.py                # ✅ 新增：数据抓取器
│   │   ├── 📄 fallback.py               # ✅ 新增：降级引擎
│   │   └── 📄 local_db.py               # 本地数据库访问
│   ├── 📁 engine/
│   │   ├── 📄 contract_enforcer.py      # ✅ 新增：契约强制校验层（核心）
│   │   ├── 📄 report_document.py        # 从旧版迁移
│   │   └── 📄 build_project_report.py   # 从旧版迁移，改造调用新架构
│   ├── 📁 templates/
│   │   └── dds_report_apple_16x9.html   # 从旧版迁移
│   └── 📄 settings.py                   # 配置管理
├── 📁 tests/
│   ├── 📄 test_data_orchestrator.py     # 数据管理测试
│   ├── 📄 test_contract_enforcer.py     # 契约强制测试
│   └── 📄 test_fallback_engine.py       # 降级策略测试
├── 📄 requirements.txt
├── 📄 pyproject.toml
└── 📄 README.md
```

---

## 五、执行步骤（5 阶段）

### 阶段 1：契约基础层迁移（1-2 天）
**目标**：建立契约基础和统一的数据结构
1. 迁移 `report_structure_contract.py` → `contracts.py`
2. 迁移 `evidence_contract.py` → 整合到 `contracts.py`
3. 定义 `SectionData`、`ValidationResult` 等数据类
4. 迁移 `dds_report_apple_16x9.html` 模板

**验证**：所有契约常量可正确导入，数据类可实例化

---

### 阶段 2：核心数据管理层实现（2-3 天）
**目标**：实现主动数据管理的三个核心组件
1. 实现 `DataFetcher` - 多数据源自动搜索
2. 实现 `FallbackEngine` - 多级降级策略
3. 实现 `DataOrchestrator` - 数据完整性编排

**验证**：
- 对于任意 section_id，能返回完整的数据结构（即使全部是降级数据）
- 每个字段都有来源标记（真实数据 / 降级 / 待补充）
- 置信度计算正确反映数据质量

---

### 阶段 3：契约强制校验层实现（1-2 天）
**目标**：建立输出前的强制校验机制
1. 实现 `ContractEnforcer.validate_report()`
2. 实现 12 个 section 的完整性校验
3. 实现降级标记检查
4. 实现置信度汇总计算

**验证**：
- 缺少任何 section 会被拒绝输出
- 缺少任何必填字段会被拒绝输出
- 降级数据必须明确标注，不能隐藏

---

### 阶段 4：报告生成引擎迁移与改造（2-3 天）
**目标**：迁移旧版报告引擎并接入新架构
1. 迁移 `report_document.py` - 保留核心渲染逻辑
2. 迁移 `build_project_report.py` - 改造为调用新架构
3. 改造渲染逻辑 - 必须显示置信度、数据来源标记、降级提示
4. 强化 CS 章节 - 自动汇总所有数据缺口和补充建议

**验证**：
- 端到端可生成完整报告
- 每个 section 都显示置信度
- 降级数据有明确标注
- CS 章节自动列出所有缺口

---

### 阶段 5：测试与回归（1-2 天）
**目标**：确保质量稳定
1. 编写三个核心模块的单元测试
2. 编写端到端报告生成测试
3. 边缘场景测试：完全无数据时的表现
4. 性能测试：报告生成时间

---

## 六、关键设计决策

| 决策 | 理由 | 影响 |
|------|------|------|
| **绝不允许静默留空** | 用户看到空页面会失去信任，即使是降级数据也要有内容 | 所有字段永远有值，降级数据必须明确标注 |
| **数据来源透明化** | 每个字段标记来源，用户知道哪些是真实数据，哪些是估算 | CS 章节自动生成数据来源清单 |
| **置信度是核心指标** | 报告的价值不是文字，而是判断的置信度 | 每个 section 必须显示置信度分数和分项 |
| **契约是硬边界** | 不通过契约校验的报告不允许输出 | 强制保证 12 个 section 完整，框架统一 |

---

## 七、验收标准

### ✅ 核心功能验收
1. **无静默留空**：任何情况下，报告不会出现空页面或空章节
2. **框架统一**：所有报告都严格遵循 4 组 12 个 decision-unit 框架
3. **数据主动补齐**：Agent 会自动搜索本地库，优先使用真实数据
4. **降级明确标注**：所有非真实数据都有明确的降级标记

### 📊 质量验收
1. **数据完整性分数**：每个 section ≥ 80% 字段有真实数据（非降级）
2. **契约通过率**：100% 报告通过契约校验
3. **置信度透明**：每个 section 和整体报告都显示置信度分数

### 🎯 用户体验验收
1. **数据缺口可见**：CS 章节明确列出所有需要补充的数据
2. **溯源能力**：任何结论都可以追溯到具体数据来源
3. **可操作性**：用户知道下一步该提供什么数据来改善报告质量

---

## 八、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 降级数据质量差 | 用户觉得报告没用 | 严格控制降级数据的质量，基准数据必须来自权威统计 |
| 过度依赖降级 | 真实数据不足 | 在 CS 章节明确列出数据缺口和优先级，引导用户补充 |
| 性能下降 | 主动搜索增加耗时 | 异步预取 + 缓存，搜索超时自动降级 |
