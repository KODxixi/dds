# DDS V2 真实证据闭环工程交接（2026-07-22）

## 交接结论

M0–M6 本轮工程实施已完成，DDS V2 已具备从真实数据需求、证据冻结、③⑤⑥确定性计算、十二单元装配、四级门禁到 V4 离线 HTML 的可复验链路。工程回归为 `130 passed in 2.40s`，Ruff、`compileall` 和 `git diff --check` 全绿。

武汉 SC2 已用 12 个有效竞品完成证据引用闭环，市场层为 `ready`；VA1 仍因缺少可售面积与设计增量成本而保持 `not_assessable`，整包 `delivery_ready=False`。不得把局部闭环或工程完成误写为正式客户交付。

## 已交付能力

- 标准 `src/dds` 工程结构和兼容公共入口。
- 真实性领域模型、六种证据解析状态与四级质量门。
- 参数化 DuckDB、本地 adapters 和项目级 Evidence Store。
- ③竞品、⑤产品定位双模式、⑥溢价及 ABM；输入不足 fail-closed。
- Requirement/Planner/Collector/Resolver/Writer 确定性边界，Writer 不能新增数字。
- V4 ReportDocument、冻结包、语义哈希、compile-only、受控 AssetResolver 和离线 HTML。
- RunStore、幂等/重试/恢复骨架、delivery service 与 `arch-front-html` bridge。

领域类型唯一实现位于 `src/dds/domain/models.py`，十二单元及门禁契约位于 `src/dds/contracts.py`，报告编译唯一实现位于 `src/dds/reporting/compiler.py`。新增公共模块仅做兼容导出，不复制定义。

## 验证证据

| 项目 | 结果 |
| --- | --- |
| pytest | `130 passed in 2.40s` |
| Ruff / `compileall` / `git diff --check` | 全绿 |
| Release wheel | `.tmp/wheel-commit/dds_v2-2.0.0-py3-none-any.whl`；251,950 bytes；83 entries；SHA-256 `5357A9B92D39E99DC8082148CDAC7B1DDCF36D74626C8D1EAFDFEA15EF517EDB` |
| Wheel 边界 | root runtime entries = 0；external import/resources = `true` |
| Browser QA | 1280×720、1440×900、1920×1080 全通过 |
| Print QA | 通过 |
| HTML SHA-256 | `ebdb2590a2b6574b36beb9580fc755db99cc374397143ee8b91605d3f5f02a1e` |
| ReportDocument SHA-256 | `f307afb2652d59f5eccdc66da2e46c5a3cbadbf81e06589bd662cd20a52f41f8` |

QA 报告位于 `.tmp/browser/browser_qa.json` 和 `.tmp/browser/print_qa.json`。`.tmp` 不提交；HTML 改变后必须重跑 QA。

## 武汉最终审计

| 指标 | 实测值 |
| --- | --- |
| 有效竞品 / EvidenceRecord | `12 / 12` |
| readiness / closure | `ready / True` |
| 价格分位 | `7,925 / 10,000 / 14,500` |
| 路径脱敏残留 | `redactions=0` |
| premium | `not_assessable` |
| EvidencePackage hash | `d2e636d62ff3001bcee817c348e46ea6fcd81907abce3ded0d3cd959cd023268` |
| ReportDocument hash | `01e8b5a4b59abd2a9c09758efc05f2e4b5aeeec760dcfc60f43f559cc86e213d` |
| pages / delivery | `4 / False` |

SC2 已闭环；当前项目阻塞集中在 VA1 面积、成本、结果变量和方法证据。

## V1 Golden

- `Test_小镇商住`：139,816,584 bytes，`ready / valid`。用当前 compiler 重新冻结后连续编译两次，ReportDocument hash 均为 `955075714880bf87049e8253cb85376b6f4ba7929a6349b02d4b8320884fa24f`，57 页，`delivery_ready=True`。
- `Test_襄阳`：31,481,432 bytes，`partial / valid`，按预期拒绝编译。
- 旧 fingerprint 包被拒绝是正确门禁行为；ready 样本复验必须先由当前 compiler 重新冻结。

## V1/V2 七文件哈希等价

| 文件 | SHA-256 |
| --- | --- |
| `evidence_contract.py` | `45c36c5d649b96ee1f027da4380e2fb275337a8b3170edd021ee2238a761746f` |
| `report_chart_contract.py` | `0880556cd196baa5af26f57fba4665c269d20692a83320df9662c17f50c685ec` |
| `report_diagram_contract.py` | `c6c4e63184f07f8849d77ebff1b80634800de42c99b0b53c8cf3453cb304330c` |
| `report_structure_contract.py` | `fb80965a85f81b63d0563b21d2badd06b8d1503f59a8bb33c2501317464972cf` |
| `report_document.py` | `4569f2bd86f732ed5c99796014992232fde39a14cdd734d91ee749c9927d2b2d` |
| `dds_report_apple_16x9.html` | `b3f09e54c417c84f502ae489fad9cfa24d10b59345bf1cc99f78e08e391c88ea` |
| `report_template_profile_v1.json` | `c0dbb93f36e1be61eee64b70885e8dca9b4411b6bf84fccfb7e694eebfbd2642` |

runtime bridge 确定性注入，不修改 V1 模板原文件。

## 武汉补证清单

1. 可售计容面积、业态拆分、版本、基准日和强排/规划来源。
2. 设计动作对应的增量工程量、含税单价、成本边界、基准日、来源和误差区间。
3. 售价、去化或土地溢价中的一个主结果变量及可追溯样本。
4. 无设计动作基准、公式、驱动系数来源、敏感性、反证和触发器。
5. 用地、容积率、限高、退界、日照、停车等有效法定条件。
6. 数据责任人、补证截止时间、决策阈值和人工审核签署。

补证不足时，VA1 数值保持为空；`unknown / not_assessable` 是合法结果。

## 后续执行顺序

1. 通过 adapters 将补证资料转换为 `EvidenceRecord`，不得直接写模板。
2. 冻结新 EvidencePackage，记录 package hash 与来源快照。
3. 重跑 SC2 → AD3 → VA1，确认有效竞品不少于 5、数字来源/公式覆盖率 100%、伪基准为 0。
4. 双编译核对 document hash、页序和来源引用。
5. 对最终 HTML 重跑三视口和打印 QA，并绑定 HTML hash。
6. 四级门禁与人工签署全部通过后才可正式交付。

## 不在本轮范围

- 全城市网页采集。
- CAD/GH 几何深化。
- TOS/云端发布。
- V1 旧 Web UI 复刻。
- 未授权数据源或自动外部发布。

恢复上下文时依次阅读 `docs/architecture/truth-evidence-loop.md`、`docs/plans/implementation-status-2026-07-22.md`、本文件和 `README.md`。测试证明工程行为，冻结证据证明项目事实，二者缺一不可。
