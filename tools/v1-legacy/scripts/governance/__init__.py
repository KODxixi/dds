# -*- coding: utf-8 -*-
"""DDS 数据治理模块（T1-T10 + 漂移监控 + 质量门禁）

T1-T6 现有治理层在 scripts/ 下（geo_clean.py, build_provenance.py, data_health_check.py）
T7-T10 新增治理层在本目录下，借鉴 DeepSeek 三阶段数据预处理方法论。

模块清单
--------
- ``t4_dedup``           — T4 去重模块（MinHash+SimHash 双重哈希，非破坏式标记）
- ``t5_missing_profile``— T5 缺失值画像（253 列逐列缺失率 + 热力图 HTML）
- ``t7_clean_pipeline``  — T7 清洗管道（去重→过滤→混洗，DeepSeek 三阶段）
- ``t8_quality_assess``  — T8 数据质量评估（八维指标 + 标准工件）
- ``t9_feedback_loop``   — T9 持续迭代反馈循环（字段使用频率×质量=优先补采）
- ``t10_lifecycle``      — T10 数据生命周期管理（热/温/冷/归档分层 + TOS 策略）
- ``drift_monitor``      — 漂移监控（reference vs current 六维对比 + 三级告警）
- ``quality_gates``      — 三级质量门禁（P0 入库前 / P1 日度 / P2 周度）

设计原则
--------
1. **非破坏式**：所有治理脚本只标记不删行，保留原始数据可回溯
2. **纯标准库**：无 pandas/duckdb 等外部依赖（CLI 批量处理时可选 import）
3. **单一真值源**：Schema 列名引用 ``scripts/schema_dds.py``，不自定义列名
4. **可自测**：每个模块 ``--selftest`` 用内置合成数据验证核心逻辑
"""
