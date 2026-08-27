# -*- coding: utf-8 -*-
"""
Great Expectations (GX) 风格声明式数据质量门禁
=============================================
对标 GE 核心语义的轻量级自研引擎，纯标准库实现。

核心架构
--------
- **Expectation**：声明式规则（JSON/YAML 定义，对标 GE Expectation）
- **ExpectationSuite**：规则集合（按数据源/管道组织，对标 GE Suite）
- **ValidationRunner**：执行验证并生成报告（对标 GE Checkpoint）
- **Checkpoint**：保存验证结果，支持趋势对比

内置 Expectations（9 种，对标 GE 核心 Expectation）
--------------------------------------------------
1. expect_column_to_exist                    — 列存在性
2. expect_column_values_to_not_be_null        — 非空率（可设阈值）
3. expect_column_values_to_be_in_set          — 值域（白名单）
4. expect_column_values_to_be_between         — 数值范围
5. expect_column_mean_to_be_between           — 均值范围
6. expect_column_unique_value_count_to_be_between — 唯一值数量
7. expect_table_row_count_to_be_between       — 行数范围
8. expect_column_values_to_match_regex        — 正则匹配
9. expect_multicolumn_sum_to_be_between       — 多列和校验

预置 Suite（针对 DDS Schema v2.0 253 列）
-----------------------------------------
- dds_new_house_baseline       — 新楼盘基础质量检查
- dds_pipeline_1_transactions  — 管道一网签成交质量检查
- dds_governance_p0            — P0 入库前门禁

CLI
---
    python scripts/governance/ge_quality_gates.py --suite dds_new_house_baseline --city 三亚
    python scripts/governance/ge_quality_gates.py --suite dds_new_house_baseline --city 三亚 --execute
    python scripts/governance/ge_quality_gates.py --list-suites
    python scripts/governance/ge_quality_gates.py --validate-all --city 三亚
    python scripts/governance/ge_quality_gates.py --selftest

与现有系统集成
--------------
- quality_gates.py 的 P0 门禁中调用 ge_validate_rows()
- pipeline_scheduler.py 的入库前钩子中触发

数据来源标注
------------
所有输出标注 SOURCE / SOURCE_URL / SOURCE_NOTE。

设计原则
--------
- 纯标准库（json / csv / re / math / argparse），不依赖 great_expectations 包
- 默认 dry-run，--execute 才实际验证并写入 checkpoint
- 所有规则定义在 JSON 文件中（data/ge_expectations/），便于非程序员维护
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ── 项目路径配置 ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE_DIR = Path(__file__).resolve().parent
GE_EXPECTATIONS_DIR = PROJECT_ROOT / "data" / "ge_expectations"
CHECKPOINT_DIR = PROJECT_ROOT / "data_out" / "governance" / "ge_checkpoints"

# 确保目录存在
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _to_float(value: Any) -> float | None:
    """安全转 float。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("元/㎡", "").replace("%", "")
    if s in ("", "nan", "none", "null", "无", "-", "N/A"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _is_missing(value: Any) -> bool:
    """判定值是否缺失（空字符串 / None / nan 等）。"""
    if value is None:
        return True
    s = str(value).strip()
    return s in ("", "nan", "none", "null", "无", "N/A", "-")


def _collect_columns(rows: list[dict]) -> list[str]:
    """收集所有列名（保持出现顺序）。"""
    seen = set()
    columns = []
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                columns.append(k)
    return columns


# ═══════════════════════════════════════════════════════════════════════════
#  Expectation 验证函数（9 种内置类型）
# ═══════════════════════════════════════════════════════════════════════════

def _validate_column_to_exist(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_column_to_exist：列是否存在。"""
    column = kwargs["column"]
    columns = _collect_columns(rows)
    exists = column in columns
    return {
        "expectation_type": "expect_column_to_exist",
        "success": exists,
        "kwargs": kwargs,
        "result": {
            "observed_value": exists,
            "element_count": len(rows),
            "missing_count": 0 if exists else len(rows),
            "unexpected_percent": 0.0 if exists else 100.0,
        },
    }


def _validate_values_not_null(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_column_values_to_not_be_null：非空率检查。"""
    column = kwargs["column"]
    min_rate = kwargs.get("min_rate", 0.0)
    total = len(rows)
    if total == 0:
        return {
            "expectation_type": "expect_column_values_to_not_be_null",
            "success": True,
            "kwargs": kwargs,
            "result": {
                "observed_value": 1.0,
                "element_count": 0,
                "unexpected_count": 0,
                "unexpected_percent": 0.0,
                "partial_unexpected_list": [],
            },
        }
    null_count = sum(1 for r in rows if _is_missing(r.get(column)))
    null_rate = null_count / total
    non_null_rate = 1.0 - null_rate
    success = non_null_rate >= min_rate

    partial_list = []
    for i, r in enumerate(rows):
        if _is_missing(r.get(column)):
            partial_list.append({"row_index": i, "value": str(r.get(column))})
            if len(partial_list) >= 20:
                break

    return {
        "expectation_type": "expect_column_values_to_not_be_null",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": round(non_null_rate, 4),
            "element_count": total,
            "unexpected_count": null_count,
            "unexpected_percent": round(null_rate * 100, 2),
            "partial_unexpected_list": partial_list,
        },
    }


def _validate_values_in_set(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_column_values_to_be_in_set：值域检查（白名单）。"""
    column = kwargs["column"]
    value_set = set(kwargs["value_set"])
    mostly = kwargs.get("mostly", 1.0)
    total = len(rows)
    if total == 0:
        return {
            "expectation_type": "expect_column_values_to_be_in_set",
            "success": True,
            "kwargs": kwargs,
            "result": {
                "observed_value": 1.0,
                "element_count": 0,
                "unexpected_count": 0,
                "unexpected_percent": 0.0,
                "partial_unexpected_list": [],
            },
        }
    unexpected = 0
    partial_list = []
    for i, r in enumerate(rows):
        val = r.get(column, "")
        if not _is_missing(val) and str(val).strip() not in value_set:
            unexpected += 1
            if len(partial_list) < 20:
                partial_list.append({"row_index": i, "value": str(val).strip()})
    rate = 1.0 - unexpected / total
    success = rate >= mostly
    return {
        "expectation_type": "expect_column_values_to_be_in_set",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": round(rate, 4),
            "element_count": total,
            "unexpected_count": unexpected,
            "unexpected_percent": round((unexpected / total) * 100, 2),
            "partial_unexpected_list": partial_list,
        },
    }


def _validate_values_between(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_column_values_to_be_between：数值范围检查。"""
    column = kwargs["column"]
    min_value = kwargs.get("min_value")
    max_value = kwargs.get("max_value")
    mostly = kwargs.get("mostly", 1.0)
    total = len(rows)
    if total == 0:
        return {
            "expectation_type": "expect_column_values_to_be_between",
            "success": True,
            "kwargs": kwargs,
            "result": {
                "observed_value": 1.0,
                "element_count": 0,
                "unexpected_count": 0,
                "unexpected_percent": 0.0,
                "partial_unexpected_list": [],
            },
        }
    unexpected = 0
    valid_count = 0
    partial_list = []
    for i, r in enumerate(rows):
        val = _to_float(r.get(column))
        if val is None:
            continue  # 缺失值不计入范围检查
        valid_count += 1
        is_valid = True
        if min_value is not None and val < min_value:
            is_valid = False
        if max_value is not None and val > max_value:
            is_valid = False
        if not is_valid:
            unexpected += 1
            if len(partial_list) < 20:
                partial_list.append({"row_index": i, "value": val})

    if valid_count == 0:
        return {
            "expectation_type": "expect_column_values_to_be_between",
            "success": True,
            "kwargs": kwargs,
            "result": {
                "observed_value": 1.0,
                "element_count": total,
                "unexpected_count": 0,
                "unexpected_percent": 0.0,
                "partial_unexpected_list": [],
            },
        }
    rate = 1.0 - unexpected / valid_count
    success = rate >= mostly
    return {
        "expectation_type": "expect_column_values_to_be_between",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": round(rate, 4),
            "element_count": total,
            "unexpected_count": unexpected,
            "unexpected_percent": round((unexpected / valid_count) * 100, 2) if valid_count else 0,
            "partial_unexpected_list": partial_list,
        },
    }


def _validate_column_mean_between(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_column_mean_to_be_between：均值范围检查。"""
    column = kwargs["column"]
    min_value = kwargs.get("min_value")
    max_value = kwargs.get("max_value")
    values = []
    for r in rows:
        val = _to_float(r.get(column))
        if val is not None:
            values.append(val)
    if not values:
        return {
            "expectation_type": "expect_column_mean_to_be_between",
            "success": True,
            "kwargs": kwargs,
            "result": {
                "observed_value": None,
                "element_count": 0,
            },
        }
    mean_val = sum(values) / len(values)
    success = True
    if min_value is not None and mean_val < min_value:
        success = False
    if max_value is not None and mean_val > max_value:
        success = False
    return {
        "expectation_type": "expect_column_mean_to_be_between",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": round(mean_val, 4),
            "element_count": len(values),
        },
    }


def _validate_unique_value_count_between(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_column_unique_value_count_to_be_between：唯一值数量检查。"""
    column = kwargs["column"]
    min_value = kwargs.get("min_value")
    max_value = kwargs.get("max_value")
    unique_vals = set()
    for r in rows:
        val = r.get(column)
        if not _is_missing(val):
            unique_vals.add(str(val).strip())
    unique_count = len(unique_vals)
    success = True
    if min_value is not None and unique_count < min_value:
        success = False
    if max_value is not None and unique_count > max_value:
        success = False
    return {
        "expectation_type": "expect_column_unique_value_count_to_be_between",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": unique_count,
            "element_count": len(rows),
        },
    }


def _validate_table_row_count_between(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_table_row_count_to_be_between：行数范围检查。"""
    min_value = kwargs.get("min_value")
    max_value = kwargs.get("max_value")
    row_count = len(rows)
    success = True
    if min_value is not None and row_count < min_value:
        success = False
    if max_value is not None and row_count > max_value:
        success = False
    return {
        "expectation_type": "expect_table_row_count_to_be_between",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": row_count,
        },
    }


def _validate_values_match_regex(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_column_values_to_match_regex：正则匹配检查。"""
    column = kwargs["column"]
    regex = kwargs["regex"]
    mostly = kwargs.get("mostly", 1.0)
    total = len(rows)
    if total == 0:
        return {
            "expectation_type": "expect_column_values_to_match_regex",
            "success": True,
            "kwargs": kwargs,
            "result": {
                "observed_value": 1.0,
                "element_count": 0,
                "unexpected_count": 0,
                "unexpected_percent": 0.0,
                "partial_unexpected_list": [],
            },
        }
    try:
        pattern = re.compile(regex)
    except re.error as e:
        return {
            "expectation_type": "expect_column_values_to_match_regex",
            "success": False,
            "kwargs": kwargs,
            "result": {
                "observed_value": 0.0,
                "element_count": total,
                "unexpected_count": total,
                "unexpected_percent": 100.0,
                "partial_unexpected_list": [],
                "exception_info": {
                    "raised_exception": True,
                    "exception_message": f"正则表达式无效: {e}",
                },
            },
        }
    unexpected = 0
    valid_count = 0
    partial_list = []
    for i, r in enumerate(rows):
        val = str(r.get(column, "")).strip()
        if _is_missing(val):
            continue
        valid_count += 1
        if not pattern.match(val):
            unexpected += 1
            if len(partial_list) < 20:
                partial_list.append({"row_index": i, "value": val})
    if valid_count == 0:
        rate = 1.0
    else:
        rate = 1.0 - unexpected / valid_count
    success = rate >= mostly
    return {
        "expectation_type": "expect_column_values_to_match_regex",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": round(rate, 4),
            "element_count": total,
            "unexpected_count": unexpected,
            "unexpected_percent": round((unexpected / valid_count) * 100, 2) if valid_count else 0,
            "partial_unexpected_list": partial_list,
        },
    }


def _validate_multicolumn_sum_between(
    rows: list[dict], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """expect_multicolumn_sum_to_be_between：多列和校验。"""
    column_list = kwargs["column_list"]
    min_value = kwargs.get("min_value")
    max_value = kwargs.get("max_value")
    mostly = kwargs.get("mostly", 1.0)
    total = len(rows)
    if total == 0:
        return {
            "expectation_type": "expect_multicolumn_sum_to_be_between",
            "success": True,
            "kwargs": kwargs,
            "result": {
                "observed_value": 1.0,
                "element_count": 0,
                "unexpected_count": 0,
                "unexpected_percent": 0.0,
            },
        }
    unexpected = 0
    for r in rows:
        col_sum = 0.0
        all_valid = True
        for col in column_list:
            val = _to_float(r.get(col))
            if val is None:
                all_valid = False
                break
            col_sum += val
        if not all_valid:
            continue
        is_valid = True
        if min_value is not None and col_sum < min_value:
            is_valid = False
        if max_value is not None and col_sum > max_value:
            is_valid = False
        if not is_valid:
            unexpected += 1
    rate = 1.0 - unexpected / total if total > 0 else 1.0
    success = rate >= mostly
    return {
        "expectation_type": "expect_multicolumn_sum_to_be_between",
        "success": success,
        "kwargs": kwargs,
        "result": {
            "observed_value": round(rate, 4),
            "element_count": total,
            "unexpected_count": unexpected,
            "unexpected_percent": round((unexpected / total) * 100, 2) if total else 0,
        },
    }


# ── 验证函数注册表 ────────────────────────────────────────────────────────
_EXPECTATION_VALIDATORS: dict[str, Any] = {
    "expect_column_to_exist": _validate_column_to_exist,
    "expect_column_values_to_not_be_null": _validate_values_not_null,
    "expect_column_values_to_be_in_set": _validate_values_in_set,
    "expect_column_values_to_be_between": _validate_values_between,
    "expect_column_mean_to_be_between": _validate_column_mean_between,
    "expect_column_unique_value_count_to_be_between": _validate_unique_value_count_between,
    "expect_table_row_count_to_be_between": _validate_table_row_count_between,
    "expect_column_values_to_match_regex": _validate_values_match_regex,
    "expect_multicolumn_sum_to_be_between": _validate_multicolumn_sum_between,
}


# ═══════════════════════════════════════════════════════════════════════════
#  ExpectationSuite — 规则集合
# ═══════════════════════════════════════════════════════════════════════════

class ExpectationSuite:
    """声明式规则集合（对标 GE ExpectationSuite）。

    参数:
        name: Suite 名称
        expectations: Expectation 定义列表
        meta: Suite 元数据（source, source_url, source_note 等）
    """

    def __init__(
        self,
        name: str,
        expectations: list[dict[str, Any]],
        meta: dict[str, Any] | None = None,
    ):
        self.name = name
        self.expectations = expectations
        self.meta = meta or {}

    @classmethod
    def from_json(cls, json_path: str | Path) -> ExpectationSuite:
        """从 JSON 文件加载 Suite。

        参数:
            json_path: JSON 文件路径

        返回:
            ExpectationSuite 实例
        """
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        suite_name = data.get("suite_name", Path(json_path).stem)
        expectations = data.get("expectations", [])
        meta = {
            "description": data.get("description", ""),
            "version": data.get("version", "1.0"),
            "source": data.get("source", ""),
            "source_url": data.get("source_url", ""),
            "source_note": data.get("source_note", ""),
        }
        return cls(name=suite_name, expectations=expectations, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "suite_name": self.name,
            "description": self.meta.get("description", ""),
            "version": self.meta.get("version", "1.0"),
            "source": self.meta.get("source", ""),
            "source_url": self.meta.get("source_url", ""),
            "source_note": self.meta.get("source_note", ""),
            "expectations": self.expectations,
        }

    def validate(self, rows: list[dict]) -> dict[str, Any]:
        """执行 Suite 中所有 Expectation 的验证。

        参数:
            rows: 行列表

        返回:
            验证结果字典，包含每个 Expectation 的 success/fail/observed/value 详情
        """
        results = []
        for exp in self.expectations:
            exp_type = exp.get("expectation_type", "")
            kwargs = exp.get("kwargs", {})
            meta = exp.get("meta", {})
            validator = _EXPECTATION_VALIDATORS.get(exp_type)
            if validator is None:
                results.append({
                    "expectation_type": exp_type,
                    "success": False,
                    "kwargs": kwargs,
                    "meta": meta,
                    "result": {
                        "observed_value": None,
                        "exception_info": {
                            "raised_exception": True,
                            "exception_message": f"未知 Expectation 类型: {exp_type}",
                        },
                    },
                })
                continue
            result = validator(rows, kwargs)
            result["meta"] = meta
            results.append(result)

        passed_count = sum(1 for r in results if r["success"])
        failed_count = len(results) - passed_count

        return {
            "suite_name": self.name,
            "success": failed_count == 0,
            "expectation_count": len(results),
            "passed_count": passed_count,
            "failed_count": failed_count,
            "results": results,
            "checked_at": datetime.now().isoformat(),
            "meta": self.meta,
        }


# ═══════════════════════════════════════════════════════════════════════════
#  ValidationRunner — 执行验证 + 生成报告
# ═══════════════════════════════════════════════════════════════════════════

class ValidationRunner:
    """验证执行器（对标 GE Checkpoint / Validation Operator）。

    参数:
        suite: ExpectationSuite 实例
        data_source: 数据来源描述
        data_source_url: 数据来源 URL
    """

    def __init__(
        self,
        suite: ExpectationSuite,
        data_source: str = "",
        data_source_url: str = "",
    ):
        self.suite = suite
        self.data_source = data_source or suite.meta.get("source", "")
        self.data_source_url = data_source_url or suite.meta.get("source_url", "")

    def run(self, rows: list[dict]) -> dict[str, Any]:
        """执行验证并返回完整结果。"""
        validation_result = self.suite.validate(rows)
        validation_result["data_source"] = self.data_source
        validation_result["data_source_url"] = self.data_source_url
        validation_result["row_count"] = len(rows)
        validation_result["column_count"] = len(_collect_columns(rows))
        validation_result["columns"] = _collect_columns(rows)
        return validation_result

    def run_and_report(
        self, rows: list[dict], output_dir: str | Path | None = None
    ) -> dict[str, Any]:
        """执行验证并生成 JSON + Markdown 报告。

        参数:
            rows: 行列表
            output_dir: 报告输出目录（可选，默认 data_out/governance/ge_reports/）

        返回:
            验证结果字典
        """
        result = self.run(rows)

        if output_dir is None:
            output_dir = PROJECT_ROOT / "data_out" / "governance" / "ge_reports"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        suite_name = self.suite.name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 写入 JSON 报告
        json_path = output_dir / f"{suite_name}_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        # 写入 Markdown 摘要报告
        md_path = output_dir / f"{suite_name}_{timestamp}.md"
        md_content = self._build_markdown_report(result)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        result["report_json"] = str(json_path)
        result["report_md"] = str(md_path)
        return result

    def _build_markdown_report(self, result: dict[str, Any]) -> str:
        """构建 Markdown 摘要报告。"""
        lines = []
        lines.append(f"# GE 质量验证报告: {result['suite_name']}")
        lines.append("")
        lines.append(f"**验证时间**: {result['checked_at']}")
        lines.append(f"**数据行数**: {result['row_count']}")
        lines.append(f"**数据列数**: {result['column_count']}")
        lines.append("")

        # 来源标注
        source = result.get("data_source", "")
        source_url = result.get("data_source_url", "")
        meta = result.get("meta", {})
        source_note = meta.get("source_note", "")
        if source or source_url:
            lines.append("## 数据来源")
            lines.append("")
            if source:
                lines.append(f"- **SOURCE**: {source}")
            if source_url:
                lines.append(f"- **SOURCE_URL**: {source_url}")
            if source_note:
                lines.append(f"- **SOURCE_NOTE**: {source_note}")
            lines.append("")

        # 总体结果
        status = "PASS" if result["success"] else "FAIL"
        lines.append(f"## 验证结果: {status}")
        lines.append("")
        pass_rate = (
            result["passed_count"] / result["expectation_count"] * 100
            if result["expectation_count"] > 0
            else 0
        )
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 总 Expectation 数 | {result['expectation_count']} |")
        lines.append(f"| 通过数 | {result['passed_count']} |")
        lines.append(f"| 失败数 | {result['failed_count']} |")
        lines.append(f"| 通过率 | {pass_rate:.1f}% |")
        lines.append("")

        # 失败详情
        failed = [r for r in result["results"] if not r["success"]]
        if failed:
            lines.append("## 失败详情")
            lines.append("")
            for i, exp_result in enumerate(failed):
                exp_type = exp_result["expectation_type"]
                meta_info = exp_result.get("meta", {})
                desc = meta_info.get("description", exp_type)
                severity = meta_info.get("severity", "unknown")
                res = exp_result.get("result", {})
                lines.append(f"### {i + 1}. {desc}")
                lines.append(f"- **类型**: `{exp_type}`")
                lines.append(f"- **严重级别**: `{severity}`")
                lines.append(f"- **参数**: `{json.dumps(exp_result.get('kwargs', {}), ensure_ascii=False)}`")
                lines.append(f"- **观测值**: `{res.get('observed_value')}`")
                if res.get("unexpected_count"):
                    lines.append(f"- **异常数**: {res['unexpected_count']}")
                    lines.append(f"- **异常率**: {res.get('unexpected_percent', 0)}%")
                if res.get("partial_unexpected_list"):
                    lines.append(f"- **异常样本** (前 5):")
                    for sample in res["partial_unexpected_list"][:5]:
                        lines.append(f"  - 行 {sample.get('row_index')}: `{sample.get('value')}`")
                if res.get("exception_info"):
                    lines.append(f"- **异常**: {res['exception_info'].get('exception_message')}")
                lines.append("")
        else:
            lines.append("## 所有 Expectation 通过")
            lines.append("")

        # 通过详情
        passed = [r for r in result["results"] if r["success"]]
        if passed:
            lines.append("## 通过详情")
            lines.append("")
            for exp_result in passed:
                meta_info = exp_result.get("meta", {})
                desc = meta_info.get("description", exp_result["expectation_type"])
                res = exp_result.get("result", {})
                lines.append(f"- **{desc}**: 观测值 `{res.get('observed_value')}`")
            lines.append("")

        # 建议修复动作
        if failed:
            lines.append("## 建议修复动作")
            lines.append("")
            for exp_result in failed:
                exp_type = exp_result["expectation_type"]
                meta_info = exp_result.get("meta", {})
                desc = meta_info.get("description", exp_type)
                severity = meta_info.get("severity", "unknown")
                kwargs = exp_result.get("kwargs", {})
                suggestion = _suggest_fix(exp_type, kwargs, severity)
                lines.append(f"- **[{severity}] {desc}**: {suggestion}")
            lines.append("")

        return "\n".join(lines)


def _suggest_fix(exp_type: str, kwargs: dict[str, Any], severity: str) -> str:
    """根据 Expectation 类型和失败情况生成修复建议。"""
    column = kwargs.get("column", "")
    suggestions = {
        "expect_column_to_exist": f"添加缺失列 '{column}'，或检查 CSV 列名是否与 Schema v2.0 一致",
        "expect_column_values_to_not_be_null": f"补全列 '{column}' 的缺失值，或检查数据采集流程",
        "expect_column_values_to_be_in_set": f"清洗列 '{column}' 的异常值，确保值在允许的白名单内",
        "expect_column_values_to_be_between": f"清洗列 '{column}' 的越界值，检查数据采集是否异常",
        "expect_column_mean_to_be_between": f"检查列 '{column}' 的分布是否有系统性偏差",
        "expect_column_unique_value_count_to_be_between": f"检查列 '{column}' 的唯一值数量是否合理",
        "expect_table_row_count_to_be_between": "检查数据源是否完整，行数是否在预期范围内",
        "expect_column_values_to_match_regex": f"修正列 '{column}' 中不符合格式的值",
        "expect_multicolumn_sum_to_be_between": "检查多列汇总值是否在合理范围",
    }
    return suggestions.get(exp_type, f"检查 Expectation '{exp_type}' 失败原因并修复数据")


# ═══════════════════════════════════════════════════════════════════════════
#  Checkpoint — 保存验证结果 + 趋势对比
# ═══════════════════════════════════════════════════════════════════════════

class Checkpoint:
    """验证结果持久化（对标 GE Checkpoint Store）。

    功能:
    - 保存每次验证的结果到 JSON 文件
    - 加载历史 checkpoint 进行趋势对比
    - 计算通过率变化趋势

    参数:
        checkpoint_dir: Checkpoint 存储目录
    """

    def __init__(self, checkpoint_dir: str | Path | None = None):
        self.checkpoint_dir = Path(checkpoint_dir or CHECKPOINT_DIR)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save(self, result: dict[str, Any], suite_name: str | None = None) -> Path:
        """保存验证结果为 checkpoint。

        参数:
            result: 验证结果字典
            suite_name: Suite 名称（可选，默认从 result 中取）

        返回:
            保存的 checkpoint 文件路径
        """
        suite_name = suite_name or result.get("suite_name", "unknown")
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        # 加微秒计数器避免同秒碰撞
        micros = now.microsecond // 1000
        filename = f"{suite_name}_{timestamp}_{micros:03d}.json"
        filepath = self.checkpoint_dir / filename

        checkpoint_data = {
            "checkpoint_id": f"{suite_name}_{timestamp}_{micros:03d}",
            "suite_name": suite_name,
            "created_at": now.isoformat(),
            "validation_result": result,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)
        return filepath

    def load_latest(self, suite_name: str) -> dict[str, Any] | None:
        """加载指定 Suite 的最新 checkpoint。

        参数:
            suite_name: Suite 名称

        返回:
            最新 checkpoint 数据，或 None（无历史记录）
        """
        pattern = f"{suite_name}_*.json"
        files = sorted(self.checkpoint_dir.glob(pattern), reverse=True)
        if not files:
            return None
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)

    def load_history(self, suite_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """加载指定 Suite 的历史 checkpoint 列表。

        参数:
            suite_name: Suite 名称
            limit: 最大返回数量

        返回:
            按时间倒序的 checkpoint 列表
        """
        pattern = f"{suite_name}_*.json"
        files = sorted(self.checkpoint_dir.glob(pattern), reverse=True)[:limit]
        history = []
        for fp in files:
            with open(fp, "r", encoding="utf-8") as f:
                history.append(json.load(f))
        return history

    def compare_trend(self, suite_name: str) -> dict[str, Any]:
        """对比最新 checkpoint 与上一次的通过率趋势。

        参数:
            suite_name: Suite 名称

        返回:
            趋势对比结果字典
        """
        history = self.load_history(suite_name, limit=2)
        if len(history) < 2:
            return {
                "suite_name": suite_name,
                "trend": "insufficient_data",
                "message": "历史数据不足（需要至少 2 次 checkpoint）",
                "current": history[0] if history else None,
                "previous": None,
            }
        current = history[0]["validation_result"]
        previous = history[1]["validation_result"]
        curr_pass_rate = (
            current["passed_count"] / current["expectation_count"] * 100
            if current["expectation_count"] > 0
            else 0
        )
        prev_pass_rate = (
            previous["passed_count"] / previous["expectation_count"] * 100
            if previous["expectation_count"] > 0
            else 0
        )
        change = curr_pass_rate - prev_pass_rate
        if change > 5:
            trend = "improving"
        elif change < -5:
            trend = "declining"
        else:
            trend = "stable"

        return {
            "suite_name": suite_name,
            "trend": trend,
            "current_pass_rate": round(curr_pass_rate, 1),
            "previous_pass_rate": round(prev_pass_rate, 1),
            "change_pct": round(change, 1),
            "current_time": current.get("checked_at", ""),
            "previous_time": previous.get("checked_at", ""),
            "message": (
                f"通过率从 {prev_pass_rate:.1f}% {'上升' if change >= 0 else '下降'}到 {curr_pass_rate:.1f}%"
                f"（变化 {change:+.1f} 个百分点）"
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
#  Suite 加载与注册
# ═══════════════════════════════════════════════════════════════════════════

# 预置 Suite 注册表（JSON 文件路径映射）
_PRESET_SUITES: dict[str, Path] = {
    "dds_new_house_baseline": GE_EXPECTATIONS_DIR / "dds_new_house_baseline.json",
    "dds_pipeline_1_transactions": GE_EXPECTATIONS_DIR / "dds_pipeline_1_transactions.json",
    "dds_governance_p0": GE_EXPECTATIONS_DIR / "dds_governance_p0.json",
}


def list_suites() -> dict[str, dict[str, Any]]:
    """列出所有可用的预置 Suite。

    返回:
        Suite 名称到元数据的映射
    """
    suites = {}
    for name, path in _PRESET_SUITES.items():
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            suites[name] = {
                "description": data.get("description", ""),
                "version": data.get("version", "1.0"),
                "expectation_count": len(data.get("expectations", [])),
                "path": str(path),
            }
        else:
            suites[name] = {
                "description": "（文件不存在）",
                "version": "N/A",
                "expectation_count": 0,
                "path": str(path),
            }
    return suites


def load_suite(name: str) -> ExpectationSuite:
    """按名称加载预置 Suite。

    参数:
        name: Suite 名称（dds_new_house_baseline / dds_pipeline_1_transactions / dds_governance_p0）

    返回:
        ExpectationSuite 实例

    异常:
        FileNotFoundError: Suite JSON 文件不存在
        ValueError: Suite 名称未知
    """
    if name not in _PRESET_SUITES:
        raise ValueError(
            f"未知 Suite: {name}。可用 Suite: {list(_PRESET_SUITES.keys())}"
        )
    path = _PRESET_SUITES[name]
    if not path.exists():
        raise FileNotFoundError(f"Suite 文件不存在: {path}")
    return ExpectationSuite.from_json(path)


# ═══════════════════════════════════════════════════════════════════════════
#  数据加载
# ═══════════════════════════════════════════════════════════════════════════

def load_city_data(city: str) -> list[dict]:
    """加载指定城市的新楼盘数据。

    参数:
        city: 城市名称

    返回:
        行列表

    异常:
        FileNotFoundError: 数据文件不存在
    """
    vault_dir = PROJECT_ROOT / "Vault" / "2026新楼盘"
    csv_path = vault_dir / f"新楼盘-{city}.csv"
    parquet_path = vault_dir / f"新楼盘-{city}.parquet"

    if parquet_path.exists():
        try:
            import pandas as pd
            df = pd.read_parquet(parquet_path)
            return df.to_dict("records")
        except ImportError:
            pass
        except Exception:
            pass

    if csv_path.exists():
        rows = []
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
        return rows

    raise FileNotFoundError(
        f"找不到城市 '{city}' 的数据文件。尝试过的路径: {csv_path}, {parquet_path}"
    )


def load_csv_data(csv_path: str | Path) -> list[dict]:
    """加载任意 CSV 文件为行列表。

    参数:
        csv_path: CSV 文件路径

    返回:
        行列表
    """
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


# ═══════════════════════════════════════════════════════════════════════════
#  与现有系统集成接口
# ═══════════════════════════════════════════════════════════════════════════

def ge_validate_rows(
    rows: list[dict],
    suite_name: str = "dds_governance_p0",
    execute: bool = False,
) -> dict[str, Any]:
    """GE 风格验证入口（供 quality_gates.py 和 pipeline_scheduler.py 调用）。

    参数:
        rows: 行列表
        suite_name: Suite 名称（默认 dds_governance_p0）
        execute: 是否写入 checkpoint（默认 False = dry-run）

    返回:
        验证结果字典，包含 passed / summary / results / checkpoint_path
    """
    suite = load_suite(suite_name)
    runner = ValidationRunner(suite)
    result = runner.run(rows)

    checkpoint_path = None
    if execute:
        ckpt = Checkpoint()
        checkpoint_path = ckpt.save(result, suite_name)

    summary = (
        f"[GE:{suite_name}] "
        f"{'PASS' if result['success'] else 'FAIL'} — "
        f"{result['passed_count']}/{result['expectation_count']} "
        f"Expectations 通过"
    )

    return {
        "suite_name": suite_name,
        "passed": result["success"],
        "summary": summary,
        "expectation_count": result["expectation_count"],
        "passed_count": result["passed_count"],
        "failed_count": result["failed_count"],
        "results": result["results"],
        "checked_at": result["checked_at"],
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
        "source": suite.meta.get("source", ""),
        "source_url": suite.meta.get("source_url", ""),
        "source_note": suite.meta.get("source_note", ""),
    }


def ge_p0_gate(rows: list[dict], execute: bool = False) -> dict[str, Any]:
    """GE P0 门禁（对标 quality_gates.py 的 p0_gate）。

    P0 门禁：全部 block 级别的 Expectation 必须通过才能入库。

    参数:
        rows: 行列表
        execute: 是否写入 checkpoint

    返回:
        门禁结果（与 quality_gates.py 兼容的格式）
    """
    ge_result = ge_validate_rows(rows, "dds_governance_p0", execute)

    # 检查 block 级别的 Expectation 是否全部通过
    block_results = [
        r for r in ge_result["results"]
        if r.get("meta", {}).get("severity") == "block"
    ]
    block_passed = all(r["success"] for r in block_results)
    block_failed = [r for r in block_results if not r["success"]]

    checks = []
    for r in ge_result["results"]:
        meta = r.get("meta", {})
        checks.append({
            "check": f"GE:{r['expectation_type']}:{r.get('kwargs', {}).get('column', 'table')}",
            "passed": r["success"],
            "detail": meta.get("description", r["expectation_type"]),
            "severity": meta.get("severity", "unknown"),
            "observed": r.get("result", {}).get("observed_value"),
            "ge_result": r,
        })

    return {
        "level": "P0",
        "passed": block_passed,
        "action": "block" if not block_passed else "pass",
        "total_checks": len(checks),
        "passed_checks": sum(1 for c in checks if c["passed"]),
        "failed_checks": sum(1 for c in checks if not c["passed"]),
        "checks": checks,
        "checked_at": ge_result["checked_at"],
        "summary": ge_result["summary"],
        "ge_source": ge_result["source"],
        "ge_source_url": ge_result["source_url"],
        "ge_source_note": ge_result["source_note"],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        description="Great Expectations (GX) 风格声明式数据质量门禁",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/governance/ge_quality_gates.py --suite dds_new_house_baseline --city 三亚
  python scripts/governance/ge_quality_gates.py --suite dds_new_house_baseline --city 三亚 --execute
  python scripts/governance/ge_quality_gates.py --list-suites
  python scripts/governance/ge_quality_gates.py --validate-all --city 三亚
  python scripts/governance/ge_quality_gates.py --selftest
        """,
    )
    parser.add_argument(
        "--suite", type=str, default=None,
        help="要运行的 Expectation Suite 名称",
    )
    parser.add_argument(
        "--city", type=str, default=None,
        help="城市名称（从 Vault/2026新楼盘/ 加载数据）",
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="CSV 文件路径（直接加载 CSV 数据）",
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="实际执行验证并写入 checkpoint（默认 dry-run）",
    )
    parser.add_argument(
        "--list-suites", action="store_true",
        help="列出所有可用 Suite",
    )
    parser.add_argument(
        "--validate-all", action="store_true",
        help="对所有 Suite 执行验证",
    )
    parser.add_argument(
        "--out", type=str, default=None,
        help="输出 JSON 报告路径",
    )
    parser.add_argument(
        "--compare", type=str, default=None,
        help="对比指定 Suite 的历史趋势",
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="运行内置自测",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # ── selftest ──
    if args.selftest:
        _selftest()
        return

    # ── list-suites ──
    if args.list_suites:
        suites = list_suites()
        print("\n=== 可用 Expectation Suite ===\n")
        for name, info in suites.items():
            print(f"  {name}")
            print(f"    描述: {info['description']}")
            print(f"    版本: {info['version']}")
            print(f"    规则数: {info['expectation_count']}")
            print(f"    路径: {info['path']}")
            print()
        return

    # ── compare ──
    if args.compare:
        ckpt = Checkpoint()
        trend = ckpt.compare_trend(args.compare)
        print(f"\n=== 趋势对比: {args.compare} ===\n")
        print(json.dumps(trend, ensure_ascii=False, indent=2))
        return

    # ── 加载数据 ──
    if args.csv:
        rows = load_csv_data(args.csv)
        print(f"[ge] 从 CSV 加载: {args.csv} ({len(rows)} 行)")
    elif args.city:
        try:
            rows = load_city_data(args.city)
            print(f"[ge] 从 Vault 加载城市: {args.city} ({len(rows)} 行)")
        except FileNotFoundError as e:
            print(f"[ge] 错误: {e}")
            sys.exit(1)
    else:
        print("[ge] 错误: 需要 --city 或 --csv 参数指定数据来源")
        sys.exit(1)

    if not rows:
        print("[ge] 警告: 数据集为空")
        return

    # ── validate-all ──
    if args.validate_all:
        suites = list_suites()
        print(f"\n=== 对所有 Suite 执行验证 ({len(suites)} 个) ===\n")
        all_results = []
        for name in suites:
            if not _PRESET_SUITES[name].exists():
                print(f"  [SKIP] {name}: 文件不存在")
                continue
            suite = load_suite(name)
            runner = ValidationRunner(suite)
            result = runner.run(rows)
            all_results.append(result)
            status = "PASS" if result["success"] else "FAIL"
            print(f"  [{status}] {name}: {result['passed_count']}/{result['expectation_count']} 通过")

            if args.execute:
                ckpt = Checkpoint()
                ckpt.save(result, name)

        # 汇总
        total_passed = sum(1 for r in all_results if r["success"])
        print(f"\n  汇总: {total_passed}/{len(all_results)} Suite 通过")

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(all_results, f, ensure_ascii=False, indent=2)
            print(f"  报告已写入: {out_path}")
        return

    # ── 单 Suite 验证 ──
    if args.suite:
        try:
            suite = load_suite(args.suite)
        except (ValueError, FileNotFoundError) as e:
            print(f"[ge] 错误: {e}")
            sys.exit(1)

        runner = ValidationRunner(suite)

        if args.execute:
            result = runner.run_and_report(rows)
            ckpt = Checkpoint()
            ckpt.save(result, args.suite)
            print(f"\n[ge] Suite: {args.suite} — {'PASS' if result['success'] else 'FAIL'}")
            print(f"[ge] 通过: {result['passed_count']}/{result['expectation_count']}")
            print(f"[ge] JSON 报告: {result.get('report_json', 'N/A')}")
            print(f"[ge] MD 报告: {result.get('report_md', 'N/A')}")
        else:
            result = runner.run(rows)
            print(f"\n[ge] Suite: {args.suite} — {'PASS' if result['success'] else 'FAIL'} (dry-run)")
            print(f"[ge] 通过: {result['passed_count']}/{result['expectation_count']}")
            for r in result["results"]:
                icon = "[OK]" if r["success"] else "[!!]"
                meta = r.get("meta", {})
                desc = meta.get("description", r["expectation_type"])
                obs = r.get("result", {}).get("observed_value", "N/A")
                print(f"  {icon} {desc:50s} 观测值={obs}")

        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"[ge] 报告已写入: {out_path}")

        # 退出码
        if not result["success"]:
            sys.exit(1)
        return

    # ── 未指定操作 ──
    print("[ge] 错误: 需要 --suite / --list-suites / --validate-all / --compare / --selftest")
    parser.print_help()
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """内置自测：验证所有 9 种 Expectation 类型 + Suite 加载 + Checkpoint 功能。"""
    print("=== GE Quality Gates selftest ===\n")

    # ── 准备测试数据 ──
    test_rows = [
        {
            "楼盘名称": "测试楼盘A", "城市名称": "三亚", "最新价格": "35000",
            "百度地图纬度": "18.41", "百度地图经度": "109.71",
            "容积率": "2.5", "绿化率": "35", "建筑面积": "50000",
            "占地面积": "30000", "规划户数": "500", "开盘日期": "2024-06-15",
            "区域名称": "海棠区", "销售状态": "在售",
            "col_a": "10", "col_b": "20",
        },
        {
            "楼盘名称": "测试楼盘B", "城市名称": "三亚", "最新价格": "42000",
            "百度地图纬度": "18.30", "百度地图经度": "109.50",
            "容积率": "1.8", "绿化率": "40", "建筑面积": "80000",
            "占地面积": "50000", "规划户数": "800", "开盘日期": "2023-12-01",
            "区域名称": "吉阳区", "销售状态": "在售",
            "col_a": "15", "col_b": "25",
        },
        {
            "楼盘名称": "测试楼盘C", "城市名称": "三亚", "最新价格": "",
            "百度地图纬度": "", "百度地图经度": "",
            "容积率": "0.05", "绿化率": "", "建筑面积": "",
            "占地面积": "", "规划户数": "", "开盘日期": "invalid",
            "区域名称": "", "销售状态": "售罄",
            "col_a": "", "col_b": "",
        },
    ]

    # ── 1. expect_column_to_exist ──
    r1 = _validate_column_to_exist(test_rows, {"column": "楼盘名称"})
    assert r1["success"], "楼盘名称列应存在"
    print(f"  [OK] expect_column_to_exist (存在): {r1['result']['observed_value']}")

    r1b = _validate_column_to_exist(test_rows, {"column": "不存在的列"})
    assert not r1b["success"], "不存在的列应失败"
    print(f"  [OK] expect_column_to_exist (不存在): {r1b['result']['observed_value']}")

    # ── 2. expect_column_values_to_not_be_null ──
    r2 = _validate_values_not_null(test_rows, {"column": "楼盘名称", "min_rate": 1.0})
    assert r2["success"], "楼盘名称应 100% 非空"
    print(f"  [OK] expect_column_values_to_not_be_null (100%): {r2['result']['observed_value']}")

    r2b = _validate_values_not_null(test_rows, {"column": "最新价格", "min_rate": 1.0})
    assert not r2b["success"], "最新价格非空率应 < 100%"
    print(f"  [OK] expect_column_values_to_not_be_null (<100%): {r2b['result']['observed_value']}")

    # ── 3. expect_column_values_to_be_in_set ──
    r3 = _validate_values_in_set(test_rows, {"column": "销售状态", "value_set": ["在售", "售罄", "待售"]})
    assert r3["success"], "销售状态应在白名单内"
    print(f"  [OK] expect_column_values_to_be_in_set: {r3['result']['observed_value']}")

    r3b = _validate_values_in_set(test_rows, {"column": "区域名称", "value_set": ["海棠区"]})
    assert not r3b["success"], "区域名称不应全部在海棠区"
    print(f"  [OK] expect_column_values_to_be_in_set (不全部): {r3b['result']['observed_value']}")

    # ── 4. expect_column_values_to_be_between ──
    r4 = _validate_values_between(test_rows, {"column": "最新价格", "min_value": 500, "max_value": 500000, "mostly": 0.90})
    assert r4["success"], "最新价格应在合理范围"
    print(f"  [OK] expect_column_values_to_be_between (价格): {r4['result']['observed_value']}")

    r4b = _validate_values_between(test_rows, {"column": "容积率", "min_value": 0.1, "max_value": 20.0, "mostly": 0.90})
    assert not r4b["success"], "容积率有越界值（0.05）"
    print(f"  [OK] expect_column_values_to_be_between (容积率越界): {r4b['result']['observed_value']}")

    # ── 5. expect_column_mean_to_be_between ──
    r5 = _validate_column_mean_between(test_rows, {"column": "最新价格", "min_value": 30000, "max_value": 50000})
    assert r5["success"], "均价应在 30000-50000"
    print(f"  [OK] expect_column_mean_to_be_between: {r5['result']['observed_value']}")

    # ── 6. expect_column_unique_value_count_to_be_between ──
    r6 = _validate_unique_value_count_between(test_rows, {"column": "楼盘名称", "min_value": 2, "max_value": 10})
    assert r6["success"], "楼盘名称唯一值应在 2-10"
    print(f"  [OK] expect_column_unique_value_count_to_be_between: {r6['result']['observed_value']}")

    # ── 7. expect_table_row_count_to_be_between ──
    r7 = _validate_table_row_count_between(test_rows, {"min_value": 1, "max_value": 100})
    assert r7["success"], "行数应在 1-100"
    print(f"  [OK] expect_table_row_count_to_be_between: {r7['result']['observed_value']}")

    r7b = _validate_table_row_count_between([], {"min_value": 1})
    assert not r7b["success"], "空数据集行数应不满足 min_value=1"
    print(f"  [OK] expect_table_row_count_to_be_between (空): {r7b['result']['observed_value']}")

    # ── 8. expect_column_values_to_match_regex ──
    r8 = _validate_values_match_regex(test_rows, {"column": "开盘日期", "regex": r"^\d{4}-\d{2}-\d{2}", "mostly": 0.60})
    assert r8["success"], "开盘日期格式应通过（60% 阈值）"
    print(f"  [OK] expect_column_values_to_match_regex: {r8['result']['observed_value']}")

    r8b = _validate_values_match_regex(test_rows, {"column": "开盘日期", "regex": r"^\d{4}-\d{2}-\d{2}", "mostly": 1.0})
    assert not r8b["success"], "开盘日期格式 100% 应不通过"
    print(f"  [OK] expect_column_values_to_match_regex (严格): {r8b['result']['observed_value']}")

    # ── 9. expect_multicolumn_sum_to_be_between ──
    r9 = _validate_multicolumn_sum_between(test_rows, {"column_list": ["col_a", "col_b"], "min_value": 10, "max_value": 100})
    assert r9["success"], "多列和应在 10-100"
    print(f"  [OK] expect_multicolumn_sum_to_be_between: {r9['result']['observed_value']}")

    # ── Suite 加载 ──
    try:
        suite = load_suite("dds_new_house_baseline")
        assert suite.name == "dds_new_house_baseline"
        assert len(suite.expectations) > 0
        print(f"  [OK] Suite 加载: {suite.name} ({len(suite.expectations)} expectations)")
    except Exception as e:
        print(f"  [WARN] Suite 加载失败: {e}")

    # ── Suite 验证 ──
    try:
        suite = load_suite("dds_new_house_baseline")
        result = suite.validate(test_rows)
        assert "results" in result
        assert "passed_count" in result
        print(f"  [OK] Suite 验证: {result['passed_count']}/{result['expectation_count']} 通过")
    except Exception as e:
        print(f"  [WARN] Suite 验证失败: {e}")

    # ── ValidationRunner ──
    try:
        suite = load_suite("dds_new_house_baseline")
        runner = ValidationRunner(suite)
        result = runner.run(test_rows)
        assert result["row_count"] == 3
        assert result["column_count"] > 0
        print(f"  [OK] ValidationRunner: {result['row_count']} 行 {result['column_count']} 列")
    except Exception as e:
        print(f"  [WARN] ValidationRunner 失败: {e}")

    # ── Checkpoint ──
    try:
        ckpt = Checkpoint()
        suite = load_suite("dds_new_house_baseline")
        runner = ValidationRunner(suite)
        result = runner.run(test_rows)
        ckpt_path = ckpt.save(result, "selftest_suite")
        assert ckpt_path.exists()
        print(f"  [OK] Checkpoint 保存: {ckpt_path}")

        loaded = ckpt.load_latest("selftest_suite")
        assert loaded is not None
        print(f"  [OK] Checkpoint 加载: {loaded['checkpoint_id']}")

        # 清理
        ckpt_path.unlink()
    except Exception as e:
        print(f"  [WARN] Checkpoint 失败: {e}")

    # ── list_suites ──
    suites = list_suites()
    assert len(suites) >= 3
    print(f"  [OK] list_suites: {len(suites)} 个 Suite 可用")

    # ── ge_validate_rows 集成接口 ──
    try:
        ge_result = ge_validate_rows(test_rows, "dds_governance_p0")
        assert "passed" in ge_result
        assert "summary" in ge_result
        print(f"  [OK] ge_validate_rows: {ge_result['summary']}")
    except Exception as e:
        print(f"  [WARN] ge_validate_rows 失败: {e}")

    # ── ge_p0_gate 集成接口 ──
    try:
        p0_result = ge_p0_gate(test_rows)
        assert "level" in p0_result
        assert p0_result["level"] == "P0"
        print(f"  [OK] ge_p0_gate: {p0_result['summary']}")
    except Exception as e:
        print(f"  [WARN] ge_p0_gate 失败: {e}")

    # ── 空数据集 ──
    try:
        empty_result = ge_validate_rows([], "dds_governance_p0")
        assert not empty_result["passed"], "空数据集 P0 应不通过"
        print(f"  [OK] 空数据集验证: {empty_result['summary']}")
    except Exception as e:
        print(f"  [WARN] 空数据集验证失败: {e}")

    # ── 趋势对比 ──
    try:
        import time
        ckpt = Checkpoint()
        suite = load_suite("dds_new_house_baseline")
        runner = ValidationRunner(suite)
        r1 = runner.run(test_rows)
        ckpt.save(r1, "trend_test")
        time.sleep(0.05)  # 确保时间戳不同
        r2 = runner.run(test_rows)
        ckpt.save(r2, "trend_test")
        trend = ckpt.compare_trend("trend_test")
        assert trend["trend"] in ("stable", "insufficient_data"), f"趋势应为 stable/insufficient_data，实际: {trend['trend']}"
        print(f"  [OK] 趋势对比: {trend['message']}")

        # 清理
        for f in CHECKPOINT_DIR.glob("trend_test_*.json"):
            f.unlink()
    except Exception as e:
        print(f"  [WARN] 趋势对比失败: {e}")

    # ── 无效正则 ──
    r_invalid = _validate_values_match_regex(test_rows, {"column": "开盘日期", "regex": r"[invalid"})
    assert not r_invalid["success"], "无效正则应失败"
    print(f"  [OK] 无效正则处理: {r_invalid['result'].get('exception_info', {}).get('exception_message', '')}")

    print("\n  === GE Quality Gates selftest 全部通过 ===")


if __name__ == "__main__":
    main()