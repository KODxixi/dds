# DDS V2 升级实施状态（2026-07-22）

> **范围说明：** 本文中的 M0–M6 完成指真实性、报告和交付基础设施阶段，不代表三类 Input、月度去化、VA2 现金流或去化—货值 Pareto 优化已经实现。后续产品路线以 `DDS_v2三类输入与操盘优化升级策略-2026-07-23.md` 为准。

## 结论

DDS V2 的 M0–M6 本轮工程实施已经完成：标准 `src/dds` 包、真实性契约、本地只读数据平面、③⑤⑥引擎、Evidence Store、V4 离线报告内核、Agent 确定性边界、运行恢复和浏览器交付门禁均已落地并通过工程验证。

工程完成不等于真实武汉工作切片已成为正式客户报告。武汉 SC2 已完成 12 个竞品样本的证据引用闭环，市场层 `readiness=ready`；但 VA1 因缺少可售面积和设计增量成本，仍为 `not_assessable`，所以整包 `delivery_ready=False`。工程完成、局部证据闭环与项目可交付必须分别判定。

迁移前原型基线提交为 `51b98d5`。V1 继续作为只读能力资产库和 Golden 回归来源，不是 V2 代码运行时依赖；V2 首期仅可配置只读访问 V1 Vault 数据。

## 工程验证

| 检查 | 实测结果 |
| --- | --- |
| pytest | `130 passed in 2.40s` |
| Ruff | 全绿 |
| `compileall` | 全绿 |
| `git diff --check` | 全绿 |
| Release wheel | `.tmp/wheel-commit/dds_v2-2.0.0-py3-none-any.whl`；251,950 bytes；83 entries；SHA-256 `5357A9B92D39E99DC8082148CDAC7B1DDCF36D74626C8D1EAFDFEA15EF517EDB` |
| Wheel 边界 | root runtime entries = 0；external import/resources = `true` |
| 官方浏览器 QA | 1280×720、1440×900、1920×1080 全部通过；无控制台错误、外部依赖请求和横向溢出 |
| 打印 QA | 打印媒体、页面、图片解码、离线运行和横向溢出检查全部通过 |
| QA HTML SHA-256 | `ebdb2590a2b6574b36beb9580fc755db99cc374397143ee8b91605d3f5f02a1e` |
| QA ReportDocument SHA-256 | `f307afb2652d59f5eccdc66da2e46c5a3cbadbf81e06589bd662cd20a52f41f8` |

浏览器与打印 QA 均绑定同一 HTML 哈希，不能复用于其他 HTML 字节。

## 武汉最终审计

| 指标 | 实测值 |
| --- | --- |
| 有效竞品样本 | 12 |
| 市场 readiness | `ready` |
| 价格分位 | P25 `7,925` / P50 `10,000` / P75 `14,500` |
| EvidenceRecord | 12 |
| 证据引用闭环 | `closure=True` |
| 路径脱敏残留 | `redactions=0` |
| 溢价状态 | `not_assessable` |
| EvidencePackage hash | `d2e636d62ff3001bcee817c348e46ea6fcd81907abce3ded0d3cd959cd023268` |
| ReportDocument hash | `01e8b5a4b59abd2a9c09758efc05f2e4b5aeeec760dcfc60f43f559cc86e213d` |
| 页数 | 4 |
| 交付状态 | `delivery_ready=False` |

SC2 的竞品数量、引用和路径脱敏已闭环；当前阻塞仅位于 VA1 的面积、成本、结果变量与方法证据。该 4 页产物仍是工作切片，不是正式客户交付。

## V1 Golden 回归

| 样本 | 原始包 | 当前结果 |
| --- | --- | --- |
| `Test_小镇商住` | `ready / valid`，139,816,584 bytes | 用当前 compiler 重新冻结后连续编译两次，ReportDocument hash 均为 `955075714880bf87049e8253cb85376b6f4ba7929a6349b02d4b8320884fa24f`；57 页；`delivery_ready=True` |
| `Test_襄阳` | `partial / valid`，31,481,432 bytes | 按预期拒绝编译；partial 包不得因结构存在而升级为可交付 |

旧包携带旧 compiler fingerprint 时被拒绝是正确行为。Golden 复验必须用当前 compiler 重新冻结 ready 源包，不能绕过 fingerprint 门禁。

## V1 报告内核字节等价

| 文件 | V1/V2 共同 SHA-256 |
| --- | --- |
| `evidence_contract.py` | `45c36c5d649b96ee1f027da4380e2fb275337a8b3170edd021ee2238a761746f` |
| `report_chart_contract.py` | `0880556cd196baa5af26f57fba4665c269d20692a83320df9662c17f50c685ec` |
| `report_diagram_contract.py` | `c6c4e63184f07f8849d77ebff1b80634800de42c99b0b53c8cf3453cb304330c` |
| `report_structure_contract.py` | `fb80965a85f81b63d0563b21d2badd06b8d1503f59a8bb33c2501317464972cf` |
| `report_document.py` | `4569f2bd86f732ed5c99796014992232fde39a14cdd734d91ee749c9927d2b2d` |
| `dds_report_apple_16x9.html` | `b3f09e54c417c84f502ae489fad9cfa24d10b59345bf1cc99f78e08e391c88ea` |
| `report_template_profile_v1.json` | `c0dbb93f36e1be61eee64b70885e8dca9b4411b6bf84fccfb7e694eebfbd2642` |

runtime bridge 在编译时确定性注入，不修改 V1 模板原文件。

## 里程碑状态

| 阶段 | 工程状态 | 已落地 | 项目级后续 |
| --- | --- | --- | --- |
| M0 | 完成 | Git 基线、标准包、测试分层、构建与导入验证 | 无工程阻塞 |
| M1 | 完成 | 真实性模型、六种解析状态、七维置信度、四级门禁、无伪基准 | 按项目持续补证 |
| M2A | 完成 | V4 内核、冻结与哈希、compile-only、离线资源、浏览器与打印 QA | 每个客户报告仍须逐包 QA |
| M2B | 首期完成 | V1 Vault 只读、参数化 DuckDB、本地 adapters、EvidencePackage | 后续按授权扩展来源和城市 |
| M3 | 工作切片完成 | SC2 以 12 个样本完成引用闭环；SC2 → AD3 → VA1 → EvidencePackage → V4 HTML 与 E2E | VA1 缺证据，整包 `delivery_ready=False` |
| M4 | 完成 | 双模式、三概念方向、AD1–AD4、勾稽、ABM fail-closed | 用真实项目参数校准 |
| M5 | 完成 | 显式输入、三情景、敏感性结构、缺输入即 `not_assessable` | 补武汉面积、成本和反事实证据 |
| M6 | 本轮完成 | Agent 边界、RunStore、API 骨架、恢复、delivery service、arch-front bridge | 生产部署与外部数据授权另行立项 |

## 当前业务卡点：武汉 VA1 补证

1. 可售计容面积、业态拆分、测算版本、基准日、编制人与强排/规划来源。
2. 每项设计动作的增量工程量、含税单价、成本边界、成本基准日、来源与不确定区间。
3. 明确售价、去化或土地溢价中的一个主结果变量，并提供可追溯样本。
4. 冻结无设计动作基准情景、公式、驱动系数来源、敏感性范围、反证与失效触发器。
5. 用地、容积率、限高、退界、日照、停车等有效法定条件。
6. 数据责任人、补证截止时间、决策阈值和人工审核签署。

补证前，VA1 数值字段必须保持为空，报告只能标记为工作切片。

## 不在本轮范围

- 全城市网页采集。
- CAD/GH 几何深化。
- TOS/云端发布。
- V1 旧 Web UI 复刻。
- 未授权数据源或自动外部发布。

阶段完成以测试、冻结证据和质量门结果为准。任何客户可见数字的来源或公式覆盖率必须为 100%；没有来源的数据保持未知或不可评估。
