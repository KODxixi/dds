# DDS Deep Research Agent 设计规格

## 概述

接入 DeepSeek API，构建自主深度研究 Agent，实现三层宏观研究（全国 → 城市 → 地块微观），与本地高质量 CSV 数据整合，为 DDS 决策引擎提供结构化线上上下文。

核心原则：**本地数据是骨架，线上 research 是血肉**——填补空白、验证疑点、更新时效，不重复搜本地已有的东西。

---

## 数据流

```
用户输入（地块 + 开发参数）
  │
  ├── Phase 0: report_parcel.py（前置）
  │     └─ 输出: parcel_report（竞品 + 配套 + 价格带）
  │     └─ 提取关键缺口 → GapReport
  │
  ├── Phase 1-4: dds_deep_research_agent.py（新增）
  │     ├─ 宏观扫盘（缓存 7d，30s 超时）
  │     ├─ 城市聚焦（缓存 7d，30s 超时）
  │     ├─ 地块钻孔（实时，45s 超时）
  │     └─ 交叉验证 vs 本地数据
  │     └─ 输出: MacroContext
  │
  └── dds_decision_engine.py（小改）
        └─ 新增 macro_context 可选入参
        └─ 本地数据始终为权威源
```

---

## 架构

### 文件结构

```
scripts/
├── dds_deep_research_agent.py   ← 新增（~350 行）
├── dds_customer_agent.py        ← 修改（集成调用，~15 行）
└── dds_decision_engine.py       ← 修改（新增入参，~30 行）

data_out/
└── macro_cache/                 ← 新增
    ├── {city}_macro.json        ← 全国宏观（7d TTL）
    ├── {city}_city.json         ← 城市聚焦（7d TTL）
    └── {city}_micro_{date}.json ← 地块钻孔（按日期）
```

### 类结构

```
dds_deep_research_agent.py

├─ class CacheManager
│   ├─ get(key) → MacroContext | None
│   ├─ put(key, data, ttl_days)
│   └─ storage: data_out/macro_cache/（JSON 文件）

├─ class ResearchPlanner
│   ├─ analyze_gaps(parcel_report) → GapReport
│   │   └─ 识别: L1 硬约束缺失、价格验证目标、政策风险点、竞品疑点
│   └─ build_queries(gaps, city, district, product) → list[SearchTask]
│       └─ task: {level: macro|city|micro, queries: [...], priority: 1-3}

├─ class WebResearcher
│   ├─ search(query) → results        ← WebSearch
│   ├─ deep_read(url, prompt) → summary ← WebFetch
│   └─ batch_research(tasks) → raw_findings

├─ class CrossValidator
│   ├─ validate(online_finding, local_data) → verdict
│   │   ├─ 数值偏差 <15% + 同向 → corroborate
│   │   ├─ 线上有/本地无 → supplement
│   │   └─ 偏差 >30% 或反向 → conflict（以本地为准）
│   └─ score_confidence(source, freshness) → 0.0~1.0

└─ class DeepResearchAgent（入口）
    └─ run(parcel_report, client_goal, city, district) → MacroContext
```

---

## 研究流程

### Phase 0: 本地数据前置

`report_parcel.py` 先完成，提取缺口分析：

| 缺口类型 | 识别规则 | 示例 |
|---------|---------|------|
| L1 合规缺口 | `compliance_agent.hard_constraints_missing` 非空 | 容积率上限、用地性质未确认 |
| 价格验证 | benchmark 溢价 >2x 或 价格样本 <5 | 保利棠隐 175000 vs 均价 78636 |
| 政策风险 | 产品 = 商墅/别墅 且 本地无政策记录 | 商墅分割销售政策 |
| 竞品疑点 | benchmark 业态标签异常 | 保利棠隐标为"临街店铺" |

### Phase 1: 宏观扫盘（缓存 7d）

```
关键词模板:
  - "2026 中国房地产调控政策 土地出让新规"
  - "LPR利率最新 2026年5月"
  - "房地产融资政策 三道红线 2026"

搜索: WebSearch ×3 → WebFetch top2 → 摘要
超时: 30s
```

### Phase 2: 城市聚焦（缓存 7d）

```
关键词模板:
  - "{city} 房价走势 2026"
  - "{city} 人口流入 产业发展 2026"
  - "{city} 重大基建规划 2026"

搜索: WebSearch ×3 → WebFetch top2 → 摘要
超时: 30s
```

### Phase 3: 地块钻孔（实时，基于缺口定向）

```
关键词动态生成:
  - "{district} 最新土地出让 2026"
  - "{district} 新楼盘 配套规划"
  - "{benchmark} 真实成交 去化"（如有对标）
  - "{product_type} {city} 市场走势 政策限制"

自主追加规则:
  - benchmark 溢价 >2x → 追加 "{benchmark} 网签成交价"
  - 含"商墅" → 追加 "商墅 分割销售 政策 {city}"
  - L1 硬约束全缺失 → 追加 "{district} 控规 用地性质"

搜索: WebSearch ×4~6 → WebFetch top3 → 摘要
超时: 45s
```

### Phase 4: 交叉验证

每条线上发现与本地 CSV 数据对照：
- `corroborate` — 数值偏差 <15%，互相印证
- `supplement` — 线上有本地无（非价格硬数据），补充信息
- `conflict` — 偏差 >30% 或结论反向，标记但不覆盖本地

---

## 输出结构

```python
MacroContext = {
    "meta": {"generated_at": str, "cache_hit": dict, "total_duration_s": float},
    "macro": [
        {"topic": str, "finding": str, "source_url": str, "confidence": 0.0-1.0, "date": str}
    ],
    "city": [
        {"topic": str, "finding": str, "source_url": str, "confidence": 0.0-1.0, "date": str}
    ],
    "micro": [
        {"topic": str, "finding": str, "source_url": str, "confidence": 0.0-1.0, "date": str}
    ],
    "cross_check": [
        {"online_finding": str, "local_data": str, "verdict": "corroborate|supplement|conflict"}
    ]
}
```

---

## 集成点

### dds_customer_agent.py 改动

```python
# handle_development_advice 中:
result = run_parcel_report(report_slots)                           # Phase 0
agent = DeepResearchAgent()
macro = agent.run(result["report"], dev_slots, city, district)     # Phase 1-4
decision = run_decision_engine(
    result["report"], dev_slots, evidence, macro_context=macro     # 融合
)
```

触发规则：
- `development_advice` 意图 → 自动触发完整链路
- REPL 中 `research` 命令 → 手动触发独立研究
- 纯 `report` 意图 → 不触发 research，只产生地块报告

### dds_decision_engine.py 改动

```python
def run_decision_engine(parcel_report, client_goal, online_evidence=None, macro_context=None):
    # macro_context 可选，不传行为与当前一致
```

各 Agent 增强：
- `data_foundation`: 注入 `macro_enrichment` 字段
- `compliance_agent`: 利用 macro_context.micro 中的政策信息
- `value_agent`: 利用 macro_context.micro 中的市场走势 + cross_check
- `traceability`: 新增 `macro_context_sources` 来源清单

---

## 容错与降级

### 超时控制

| 阶段 | 超时 | 降级策略 |
|------|------|---------|
| Phase 1 宏观 | 30s | 跳过，不影响其余 |
| Phase 2 城市 | 30s | 跳过，不影响其余 |
| Phase 3 地块 | 45s | 已有结果立即返回 |
| 总超时 | 120s | 返回已有结果 |

### 搜索失败降级链

```
WebSearch 失败/超时 10s
  → WebFetch 直搜已知 URL（政府网站、行业平台）
    → 仍失败: 标记 topic 为 unavailable，继续其余 topic
```

### 缓存策略

- 命中且 <7d → 直接返回
- 命中但 >7d → 返回过期数据 + `stale=True`，`confidence *= 0.5`
- 未命中 → 网络搜索 + 写入缓存

### 最低保障

断网环境：`DeepResearchAgent.run()` 返回空 `MacroContext`，决策引擎照常产出完整报告。Deep Research 是加分项，不是必需品。

---

## CLI

```bash
# 独立运行（手动触发）
python scripts/dds_deep_research_agent.py \
  --input <parcel_report.json> \
  --city 三亚 --product 商墅 --benchmark 保利棠隐

# 清除城市缓存
python scripts/dds_deep_research_agent.py --clear-cache --city 三亚

# 查看缓存状态
python scripts/dds_deep_research_agent.py --cache-status
```

---

## 数据优先级规则

1. L1 政府法定数据（未来接入）> 2. 本地 CSV 楼盘库 > 3. 高德 GIS POI > 4. Deep Research 线上发现

    本地数据始终为权威源，线上发现标记 `conflict` 时不覆盖本地结论
