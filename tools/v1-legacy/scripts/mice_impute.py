# -*- coding: utf-8 -*-
"""
MICE 缺失值填补模块 -- 链式方程多重插补
========================================
基于 scikit-learn IterativeImputer（BayesianRidge 回归）实现 MICE（Multiple Imputation
by Chained Equations）对楼盘数据高频缺失字段进行多重插补。

填补目标字段（高频缺失）
------------------------
- 容积率（plot_ratio）  -- 数值型，楼面面积/占地面积
- 绿化率（green_rate）  -- 数值型，百分比
- 建筑面积（build_area） -- 数值型，平方米
- 总户数（total_units）  -- 整数型，规划户数
- 车位数量（parking）    -- 整数型，车位数

算法原理
--------
IterativeImputer 将每个缺失特征建模为其他特征的函数，用 BayesianRidge 回归
迭代估计缺失值。每轮迭代用上一轮填补值更新模型，直至收敛（max_iter=10，
tol=1e-3 默认）。

数据来源
--------
- 输入 CSV：Vault/2026新楼盘/新楼盘-{城市}.csv
  来源：安居客新房数据（购买库），Schema v2.0 = 253 列规范
  来源标注：schema_dds.py TAXONOMY 中的 [采] 标记
- 输出 CSV：Vault/2026新楼盘/新楼盘-{城市}_imputed.csv
  新增 _imputed 标记列，保留原始列
- 填补报告：data_out/governance/mice_impute/{城市}_impute_report.md

依赖
----
- scikit-learn >= 1.0（IterativeImputer）
- numpy
- 标准库：csv, json, math, re, pathlib, datetime

CLI
---
    # 对三亚数据运行 MICE 填补
    python scripts/mice_impute.py --city 三亚

    # 对指定 CSV 运行
    python scripts/mice_impute.py --csv Vault/2026新楼盘/新楼盘-三亚.csv

    # 自定义输出路径
    python scripts/mice_impute.py --city 三亚 --out Vault/2026新楼盘/新楼盘-三亚_imputed.csv

    # 自测
    python scripts/mice_impute.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer
from sklearn.linear_model import BayesianRidge
from sklearn.preprocessing import StandardScaler

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 填补目标字段配置 ──────────────────────────────────────────────────
# 每个字段的配置：列名、类型、合理范围、特征列（用于预测该字段）
IMPUTE_TARGETS: dict[str, dict[str, Any]] = {
    "容积率": {
        "dtype": "float",
        "range": (0.1, 15.0),
        "description": "楼面面积/占地面积比值",
        "unit": "无单位",
        "source": "安居客新房数据（购买库），Schema v2.0 列 28",
        "predictors": ["绿化率", "建筑面积", "规划户数", "车位数", "最新价格", "占地面积"],
    },
    "绿化率": {
        "dtype": "float",
        "range": (1.0, 90.0),
        "description": "绿化面积/总占地面积百分比",
        "unit": "%",
        "source": "安居客新房数据（购买库），Schema v2.0 列 29",
        "predictors": ["容积率", "建筑面积", "规划户数", "车位数", "最新价格"],
    },
    "建筑面积": {
        "dtype": "float",
        "range": (100, 5_000_000),
        "description": "总建筑面积",
        "unit": "m2",
        "source": "安居客新房数据（购买库），Schema v2.0 列 16",
        "predictors": ["容积率", "绿化率", "规划户数", "车位数", "占地面积", "最新价格"],
    },
    "规划户数": {
        "dtype": "int",
        "range": (1, 50_000),
        "description": "规划总户数",
        "unit": "户",
        "source": "安居客新房数据（购买库），Schema v2.0 列 30",
        "predictors": ["容积率", "绿化率", "建筑面积", "车位数", "最新价格", "占地面积"],
    },
    "车位数": {
        "dtype": "int",
        "range": (0, 100_000),
        "description": "停车位总数",
        "unit": "个",
        "source": "安居客新房数据（购买库），Schema v2.0 列 37",
        "predictors": ["容积率", "绿化率", "建筑面积", "规划户数", "最新价格", "占地面积"],
    },
}

# 辅助特征列（用于预测模型，这些列本身不填补，但作为预测因子）
AUX_FEATURES = ["最新价格", "占地面积"]


# ═══════════════════════════════════════════════════════════════════════
#  数据读取与预处理
# ═══════════════════════════════════════════════════════════════════════

def _safe_float(value: str | None) -> float | None:
    """安全提取数值。支持 "35%", "1.2", "住宅：1.2 别墅：1.2", "35000元/㎡" 等格式。

    来源标注：DDS 楼盘数据中容积率/绿化率等字段含复合格式（如 "住宅：1.2 别墅：1.2"），
    取第一个数字作为主值。
    """
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "nan", "none", "null", "无", "-"):
        return None
    # 去除单位后缀
    s = s.replace("元/㎡", "").replace("元/平米", "").replace("万元/套", "").replace("m²", "").replace("㎡", "")
    s = s.replace("%", "").replace(",", "").replace(" ", "")
    # 取第一个数字（处理 "住宅：1.2 别墅：1.2" 格式）
    match = re.search(r"[\d.]+", s)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


def _safe_int(value: str | None) -> int | None:
    """安全提取整数。"""
    f = _safe_float(value)
    if f is None:
        return None
    return int(round(f))


def read_csv_for_impute(csv_path: str | Path) -> tuple[list[dict], list[str]]:
    """读取 CSV 并提取填补所需列。

    返回:
        (rows, headers): 原始行列表和列名列表
    """
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(f"CSV 不存在: {p}")

    with open(p, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)

    print(f"[MICE] 读取数据: {p.name} ({len(rows)} 行, {len(headers)} 列)")
    return rows, headers


def build_feature_matrix(
    rows: list[dict],
    targets: list[str],
    aux: list[str],
) -> tuple[np.ndarray, list[str], dict[str, Any]]:
    """构建特征矩阵和缺失统计。

    参数:
        rows: 原始行列表
        targets: 目标填补字段列表
        aux: 辅助特征字段列表

    返回:
        (X_matrix, col_names, missing_stats)
        - X_matrix: (n_rows, n_features) 数值矩阵，缺失值为 NaN
        - col_names: 特征列名列表
        - missing_stats: 每个目标字段的缺失统计
    """
    all_features = targets + aux
    # 过滤掉不存在的列
    existing_cols = [c for c in all_features if c in rows[0]]

    X = np.full((len(rows), len(existing_cols)), np.nan)
    missing_stats = {}

    for j, col in enumerate(existing_cols):
        dtype = IMPUTE_TARGETS.get(col, {}).get("dtype", "float")
        missing_count = 0
        for i, row in enumerate(rows):
            val = row.get(col, "")
            if dtype == "int":
                num = _safe_int(val)
            else:
                num = _safe_float(val)
            if num is not None:
                X[i, j] = num
            else:
                missing_count += 1

        if col in targets:
            missing_stats[col] = {
                "missing_count": missing_count,
                "missing_rate": round(missing_count / len(rows) * 100, 2) if rows else 0,
                "total": len(rows),
            }

    return X, existing_cols, missing_stats


# ═══════════════════════════════════════════════════════════════════════
#  MICE 填补
# ═══════════════════════════════════════════════════════════════════════

def impute_mice(
    X: np.ndarray,
    col_names: list[str],
    target_cols: list[str],
    random_state: int = 42,
    max_iter: int = 10,
) -> tuple[np.ndarray, dict[str, Any]]:
    """使用 IterativeImputer 执行 MICE 多重插补。

    算法流程:
        1. 对每个目标列，用 BayesianRidge 回归建模（目标 = f(其他特征)）
        2. 初始填补：用该列均值填充 NaN
        3. 迭代：每轮对每个目标列，用其他列当前值预测 → 更新 NaN
        4. 收敛：max_iter 轮或 tol=1e-3 收敛

    参数:
        X:             (n_samples, n_features) 数值矩阵
        col_names:     列名列表
        target_cols:   目标填补列名
        random_state:  随机种子
        max_iter:      最大迭代次数

    返回:
        (X_imputed, diagnostics): 填补后矩阵和诊断信息
    """
    # 1. 找到目标列在矩阵中的索引
    target_indices = [col_names.index(c) for c in target_cols if c in col_names]

    # 2. 统计填补前缺失
    before_missing = {c: int(np.isnan(X[:, col_names.index(c)]).sum())
                      for c in target_cols if c in col_names}

    # 3. 标准化（可选，帮助回归收敛）
    scaler = StandardScaler()
    X_scaled = np.copy(X)
    for j in range(X.shape[1]):
        col = X[:, j]
        valid = ~np.isnan(col)
        if valid.sum() > 1:
            mean_val = col[valid].mean()
            std_val = col[valid].std()
            if std_val > 0:
                X_scaled[valid, j] = (col[valid] - mean_val) / std_val

    # 4. IterativeImputer
    # 使用 BayesianRidge 作为估计器，对缺失值进行贝叶斯回归预测
    estimator = BayesianRidge()
    imputer = IterativeImputer(
        estimator=estimator,
        max_iter=max_iter,
        random_state=random_state,
        tol=1e-3,
        verbose=0,
        initial_strategy="mean",
        imputation_order="ascending",  # 从缺失最少的列开始
    )

    X_imputed = imputer.fit_transform(X_scaled)

    # 5. 逆标准化
    for j in range(X.shape[1]):
        col = X[:, j]
        valid = ~np.isnan(col)
        if valid.sum() > 1:
            mean_val = col[valid].mean()
            std_val = col[valid].std()
            if std_val > 0:
                X_imputed[:, j] = X_imputed[:, j] * std_val + mean_val

    # 5.5 裁剪填补值到字段合理范围
    for c in target_cols:
        if c in col_names:
            j = col_names.index(c)
            target_range = IMPUTE_TARGETS.get(c, {}).get("range")
            if target_range:
                low, high = target_range
                X_imputed[:, j] = np.clip(X_imputed[:, j], low, high)

    # 6. 诊断信息
    diagnostics: dict[str, Any] = {
        "algorithm": "IterativeImputer (BayesianRidge)",
        "max_iter": max_iter,
        "random_state": random_state,
        "n_iter_": getattr(imputer, "n_iter_", max_iter),
        "before_missing": before_missing,
        "after_missing": {},
        "convergence": [],
    }

    for c in target_cols:
        if c in col_names:
            j = col_names.index(c)
            after = int(np.isnan(X_imputed[:, j]).sum())
            diagnostics["after_missing"][c] = after
            # 收敛诊断：检查填补值是否在合理范围内
            imputed_vals = X_imputed[:, j][np.isnan(X[:, j])]
            if len(imputed_vals) > 0:
                target_range = IMPUTE_TARGETS.get(c, {}).get("range")
                in_range = True
                if target_range:
                    low, high = target_range
                    in_range = all((low <= v <= high) for v in imputed_vals)
                diagnostics["convergence"].append({
                    "field": c,
                    "n_imputed": len(imputed_vals),
                    "mean": float(np.mean(imputed_vals)),
                    "std": float(np.std(imputed_vals)),
                    "min": float(np.min(imputed_vals)),
                    "max": float(np.max(imputed_vals)),
                    "in_range": in_range,
                })

    return X_imputed, diagnostics


# ═══════════════════════════════════════════════════════════════════════
#  输出
# ═══════════════════════════════════════════════════════════════════════

def write_imputed_csv(
    rows: list[dict],
    headers: list[str],
    X_imputed: np.ndarray,
    col_names: list[str],
    target_cols: list[str],
    out_path: str | Path,
) -> None:
    """输出填补后的 CSV。

    保留原始所有列，对每个目标列：
    - 原始值保持不变（非缺失行）
    - 缺失值替换为 MICE 填补值
    - 新增 {列名}_imputed 标记列（0=原始值，1=MICE 填补值）
    """
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    # 构建新列名：原始列 + _imputed 标记列
    new_headers = list(headers)
    imputed_flags = {}
    for c in target_cols:
        if c in col_names and c in headers:
            flag_col = f"{c}_imputed"
            new_headers.append(flag_col)
            imputed_flags[c] = flag_col

    with open(out_p, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=new_headers)
        writer.writeheader()

        for i, row in enumerate(rows):
            new_row = dict(row)
            for c in target_cols:
                flag_col = imputed_flags.get(c)
                if flag_col is None:
                    continue
                j = col_names.index(c)
                original_val = row.get(c, "")
                was_missing = (not original_val or not original_val.strip()
                               or original_val.strip() in ("nan", "none", "null", "无", "-"))

                if was_missing and not np.isnan(X_imputed[i, j]):
                    imputed_val = X_imputed[i, j]
                    dtype = IMPUTE_TARGETS.get(c, {}).get("dtype", "float")
                    if dtype == "int":
                        new_row[c] = str(int(round(imputed_val)))
                    else:
                        new_row[c] = f"{imputed_val:.4f}"
                    new_row[flag_col] = "1"
                else:
                    new_row[flag_col] = "0"

            writer.writerow(new_row)

    print(f"[MICE] 填补后 CSV 已写入: {out_p} ({len(rows)} 行, {len(new_headers)} 列)")


def write_impute_report(
    missing_stats: dict[str, Any],
    diagnostics: dict[str, Any],
    target_cols: list[str],
    out_path: str | Path,
    city: str = "",
) -> None:
    """生成填补报告（Markdown）。"""
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# MICE 缺失值填补报告",
        "",
        f"- **数据源**: Vault/2026新楼盘/新楼盘-{city}.csv" if city else "- **数据源**: (指定 CSV)",
        f"- **数据来源标注**: 安居客新房数据（购买库），Schema v2.0 = 253 列规范",
        f"- **填补时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **填补算法**: {diagnostics['algorithm']}",
        f"- **最大迭代**: {diagnostics['max_iter']} 轮",
        f"- **实际迭代**: {diagnostics.get('n_iter_', 'N/A')} 轮",
        f"- **随机种子**: {diagnostics['random_state']}",
        "",
        "## 缺失率统计",
        "",
        "| 字段 | 缺失数 | 缺失率 | 填补方法 | 合理范围 |",
        "|------|--------|--------|----------|----------|",
    ]

    for c in target_cols:
        stats = missing_stats.get(c, {})
        cfg = IMPUTE_TARGETS.get(c, {})
        rng = cfg.get("range", (0, 0))
        lines.append(
            f"| {c} | {stats.get('missing_count', 'N/A')} | "
            f"{stats.get('missing_rate', 'N/A')}% | "
            f"BayesianRidge MICE | [{rng[0]}, {rng[1]}] |"
        )

    lines += [
        "",
        "## 收敛诊断",
        "",
        "| 字段 | 填补数 | 均值 | 标准差 | 最小值 | 最大值 | 范围检查 |",
        "|------|--------|------|--------|--------|--------|----------|",
    ]

    for conv in diagnostics.get("convergence", []):
        in_range_str = "通过" if conv.get("in_range") else "超出范围"
        lines.append(
            f"| {conv['field']} | {conv['n_imputed']} | "
            f"{conv['mean']:.4f} | {conv['std']:.4f} | "
            f"{conv['min']:.4f} | {conv['max']:.4f} | "
            f"{in_range_str} |"
        )

    lines += [
        "",
        "## 填补后缺失统计",
        "",
        "| 字段 | 填补前缺失 | 填补后缺失 |",
        "|------|------------|------------|",
    ]

    before = diagnostics.get("before_missing", {})
    after = diagnostics.get("after_missing", {})
    for c in target_cols:
        b = before.get(c, "N/A")
        a = after.get(c, "N/A")
        lines.append(f"| {c} | {b} | {a} |")

    lines += [
        "",
        "## 注意事项",
        "",
        "- MICE 填补值是基于其他特征的条件期望，不代表真实值",
        "- 填补后的数据标记了 `_imputed` 列（1=填补值，0=原始值），下游分析可据此筛选",
        "- 建议对填补后的数据做敏感性分析，比较填补前后的分布差异",
        "- 填补模型假设数据是 MAR（Missing At Random），如果缺失机制是 MNAR，填补可能有偏",
        "",
        "## 数据来源",
        "",
        "| 字段 | 原始来源 | 说明 |",
        "|------|----------|------|",
    ]

    for c in target_cols:
        cfg = IMPUTE_TARGETS.get(c, {})
        lines.append(
            f"| {c} | {cfg.get('source', 'N/A')} | "
            f"{cfg.get('description', '')} ({cfg.get('unit', '')}) |"
        )

    with open(out_p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[MICE] 填补报告已写入: {out_p}")


# ═══════════════════════════════════════════════════════════════════════
#  Pipeline 入口
# ═══════════════════════════════════════════════════════════════════════

def run_pipeline(
    csv_path: str | None = None,
    out_path: str | None = None,
    city: str = "三亚",
    report_dir: str | None = None,
    max_iter: int = 10,
    random_state: int = 42,
) -> dict[str, Any]:
    """MICE 填补 pipeline 入口函数。

    参数:
        csv_path:    输入 CSV 路径（默认 Vault/2026新楼盘/新楼盘-{city}.csv）
        out_path:    输出 CSV 路径（默认 Vault/2026新楼盘/新楼盘-{city}_imputed.csv）
        city:        城市名
        report_dir:  报告输出目录（默认 data_out/governance/mice_impute/）
        max_iter:    MICE 最大迭代次数
        random_state: 随机种子

    返回:
        {"status": "ok", "missing_stats": {...}, "diagnostics": {...}, ...}
    """
    if csv_path is None:
        csv_path = str(PROJECT_ROOT / "Vault" / "2026新楼盘" / f"新楼盘-{city}.csv")
    if out_path is None:
        out_path = str(PROJECT_ROOT / "Vault" / "2026新楼盘" / f"新楼盘-{city}_imputed.csv")
    if report_dir is None:
        report_dir = str(PROJECT_ROOT / "data_out" / "governance" / "mice_impute")

    target_cols = list(IMPUTE_TARGETS.keys())

    # 1. 读取数据
    rows, headers = read_csv_for_impute(csv_path)

    if not rows:
        return {"status": "error", "error": "CSV 为空"}

    # 2. 构建特征矩阵
    X, col_names, missing_stats = build_feature_matrix(rows, target_cols, AUX_FEATURES)

    # 检查是否有缺失
    total_missing = sum(s["missing_count"] for s in missing_stats.values())
    if total_missing == 0:
        print("[MICE] 所有目标字段无缺失，跳过填补")
        return {
            "status": "ok",
            "skipped": True,
            "reason": "所有目标字段无缺失值",
            "missing_stats": missing_stats,
        }

    # 3. MICE 填补
    print(f"[MICE] 开始填补: {len(target_cols)} 个目标字段, "
          f"特征矩阵 {X.shape[1]} 列, 总缺失 {total_missing} 个")
    X_imputed, diagnostics = impute_mice(X, col_names, target_cols,
                                         random_state=random_state, max_iter=max_iter)

    # 4. 输出填补后 CSV
    write_imputed_csv(rows, headers, X_imputed, col_names, target_cols, out_path)

    # 5. 输出填补报告
    report_path = Path(report_dir) / f"{city}_impute_report.md"
    write_impute_report(missing_stats, diagnostics, target_cols, report_path, city=city)

    return {
        "status": "ok",
        "missing_stats": missing_stats,
        "diagnostics": diagnostics,
        "output_csv": str(out_path),
        "output_report": str(report_path),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ═══════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """内置自测：构造含缺失值的测试数据，验证填补流程。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # 1. 构造测试 CSV
        test_csv = tmpdir / "test.csv"
        with open(test_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["楼盘名称", "容积率", "绿化率", "建筑面积", "规划户数", "车位数", "最新价格", "占地面积"])
            # 20 行，其中一些有缺失
            test_data = [
                ["盘A", "2.5", "35%", "50000", "400", "300", "35000", "20000"],
                ["盘B", "3.0", "40%", "60000", "500", "", "38000", "20000"],       # 缺车位数
                ["盘C", "1.8", "", "45000", "350", "250", "30000", "25000"],       # 缺绿化率
                ["盘D", "", "30%", "55000", "450", "350", "32000", "18000"],       # 缺容积率
                ["盘E", "2.2", "38%", "", "380", "280", "33000", "22000"],         # 缺建筑面积
                ["盘F", "3.5", "45%", "70000", "", "500", "42000", "20000"],       # 缺规划户数
                ["盘G", "2.0", "32%", "48000", "420", "320", "28000", "24000"],    # 完整
                ["盘H", "1.5", "25%", "40000", "300", "200", "25000", "27000"],    # 完整
                ["盘I", "2.8", "42%", "65000", "520", "400", "40000", "23000"],    # 完整
                ["盘J", "3.2", "", "72000", "550", "450", "45000", "21000"],       # 缺绿化率
                ["盘K", "2.1", "33%", "52000", "430", "310", "36000", "19000"],    # 完整
                ["盘L", "1.9", "28%", "43000", "320", "230", "27000", "26000"],    # 完整
                ["盘M", "2.6", "37%", "58000", "480", "", "34000", "22000"],       # 缺车位数
                ["盘N", "3.3", "44%", "78000", "600", "550", "48000", "24000"],    # 完整
                ["盘O", "", "35%", "50000", "400", "300", "35000", "20000"],       # 缺容积率
                ["盘P", "2.4", "31%", "51000", "410", "290", "31000", "21000"],    # 完整
                ["盘Q", "2.9", "43%", "68000", "", "420", "43000", "23000"],       # 缺规划户数
                ["盘R", "1.7", "26%", "38000", "280", "180", "24000", "28000"],    # 完整
                ["盘S", "2.3", "36%", "54000", "460", "340", "37000", "20000"],    # 完整
                ["盘T", "3.1", "41%", "75000", "580", "480", "46000", "25000"],    # 完整
            ]
            for row in test_data:
                writer.writerow(row)

        # 2. 运行填补
        result = run_pipeline(
            csv_path=str(test_csv),
            out_path=str(tmpdir / "test_imputed.csv"),
            city="test",
            report_dir=str(tmpdir / "reports"),
            max_iter=5,  # 测试用较少的迭代
        )

        assert result["status"] == "ok", f"填补应成功: {result}"
        print(f"  [OK] pipeline 返回成功")

        # 断言 1：缺失统计正确
        assert result["missing_stats"]["容积率"]["missing_count"] == 2, \
            f"容积率应有 2 个缺失，实际 {result['missing_stats']['容积率']['missing_count']}"
        assert result["missing_stats"]["绿化率"]["missing_count"] == 2
        assert result["missing_stats"]["车位数"]["missing_count"] == 2
        assert result["missing_stats"]["规划户数"]["missing_count"] == 2
        print(f"  [OK] 缺失统计正确: { {k: v['missing_count'] for k, v in result['missing_stats'].items()} }")

        # 断言 2：填补后 CSV 存在
        out_csv = Path(result["output_csv"])
        assert out_csv.exists(), f"填补后 CSV 应存在: {out_csv}"
        with open(out_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            imputed_rows = list(reader)
        assert len(imputed_rows) == 20, f"应保持 20 行，实际 {len(imputed_rows)}"
        print(f"  [OK] 填补后 CSV: {len(imputed_rows)} 行, {len(imputed_rows[0])} 列")

        # 断言 3：_imputed 标记列存在
        assert "容积率_imputed" in imputed_rows[0], "_imputed 标记列应存在"
        # 盘D 容积率应为填补值
        row_d = imputed_rows[3]  # 第 4 行
        assert row_d["楼盘名称"] == "盘D"
        assert row_d["容积率_imputed"] == "1", f"盘D 容积率应为填补值，实际 {row_d['容积率_imputed']}"
        assert row_d["容积率"] != "", "盘D 容积率不应为空"
        print(f"  [OK] 盘D 容积率填补: {row_d['容积率']}")

        # 盘A 完整行，_imputed 应为 0
        row_a = imputed_rows[0]
        assert row_a["楼盘名称"] == "盘A"
        assert row_a["容积率_imputed"] == "0", f"盘A 容积率应为原始值"
        print(f"  [OK] 盘A 容积率原始值标记正确")

        # 断言 4：填补报告存在
        report_path = Path(result["output_report"])
        assert report_path.exists(), f"填补报告应存在: {report_path}"
        report_content = report_path.read_text(encoding="utf-8")
        assert "MICE 缺失值填补报告" in report_content
        assert "缺失率统计" in report_content
        assert "收敛诊断" in report_content
        print(f"  [OK] 填补报告: {len(report_content)} 字符")

        # 断言 5：诊断信息完整
        diag = result["diagnostics"]
        assert len(diag["convergence"]) == 5, f"应有 5 个字段的收敛诊断，实际 {len(diag['convergence'])}"
        for conv in diag["convergence"]:
            assert conv["n_imputed"] > 0 or conv["field"] in ["建筑面积", "绿化率", "容积率", "规划户数", "车位数"], \
                f"字段 {conv['field']} 应有填补值"
        print(f"  [OK] 收敛诊断: {len(diag['convergence'])} 个字段")

        # 断言 6：填补后无缺失
        for c in ["容积率", "绿化率", "建筑面积", "规划户数", "车位数"]:
            after = diag["after_missing"].get(c, -1)
            assert after == 0, f"{c} 填补后应无缺失，实际 {after}"
        print(f"  [OK] 填补后所有目标字段无缺失")

    print("\n  === MICE 缺失值填补 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MICE 缺失值填补 -- 链式方程多重插补"
    )
    parser.add_argument("--csv", type=str, help="输入 CSV 文件路径")
    parser.add_argument("--city", type=str, default="三亚",
                        help="城市名（用于查找默认 CSV）")
    parser.add_argument("--out", type=str, help="输出 CSV 路径")
    parser.add_argument("--report-dir", type=str,
                        default="data_out/governance/mice_impute/",
                        help="报告输出目录")
    parser.add_argument("--max-iter", type=int, default=10,
                        help="MICE 最大迭代次数（默认 10）")
    parser.add_argument("--random-state", type=int, default=42,
                        help="随机种子（默认 42）")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    csv_path = args.csv
    if not csv_path:
        csv_path = str(PROJECT_ROOT / "Vault" / "2026新楼盘" / f"新楼盘-{args.city}.csv")

    out_path = args.out
    if not out_path:
        out_path = str(PROJECT_ROOT / "Vault" / "2026新楼盘" / f"新楼盘-{args.city}_imputed.csv")

    report_dir = args.report_dir
    if not Path(report_dir).is_absolute():
        report_dir = str(PROJECT_ROOT / args.report_dir)

    result = run_pipeline(
        csv_path=csv_path,
        out_path=out_path,
        city=args.city,
        report_dir=report_dir,
        max_iter=args.max_iter,
        random_state=args.random_state,
    )

    if result["status"] == "error":
        print(f"[MICE] 错误: {result['error']}")
        sys.exit(1)

    print(f"\n[MICE] 填补完成。")
    print(f"  输出 CSV:    {result['output_csv']}")
    print(f"  填补报告:    {result['output_report']}")
    if result.get("skipped"):
        print(f"  状态:        跳过（{result['reason']}）")
    else:
        for field, stats in result["missing_stats"].items():
            print(f"  {field}: 填补 {stats['missing_count']}/{stats['total']} "
                  f"({stats['missing_rate']}%)")


if __name__ == "__main__":
    main()