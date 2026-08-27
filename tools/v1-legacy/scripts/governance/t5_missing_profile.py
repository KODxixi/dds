# -*- coding: utf-8 -*-
"""
T5 缺失值画像模块 — 253 列逐列缺失率统计 + 热力图 HTML
========================================================
功能：按全量列逐列统计缺失率，支持按城市/区域/数据来源分组。
      输出自包含的热力图 HTML（纯 CSS/JS，无外部依赖）。

设计原则
--------
- 纯标准库（csv / json / html），不依赖 pandas
- 行列表 ``list[dict]`` 作为通用表格表示
- 缺失判定：空字符串 / None / "nan" / "NULL" 均视为缺失

CLI
---
    python scripts/governance/t5_missing_profile.py --city 三亚 --out missing_heatmap.html
    python scripts/governance/t5_missing_profile.py --csv data.csv --out missing_heatmap.html --group-by 城市名称
    python scripts/governance/t5_missing_profile.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from html import escape
from pathlib import Path
from typing import Any

# ── 缺失值判定集合 ─────────────────────────────────────────────────────
MISSING_VALUES = {"", "nan", "none", "null", "n/a", "na", "-", "——", "无"}


# ═══════════════════════════════════════════════════════════════════════
#  缺失值判定
# ═══════════════════════════════════════════════════════════════════════

def is_missing(value: Any) -> bool:
    """
    判断单个值是否为缺失。

    缺失判定：None / 空字符串 / "nan" / "NULL" / "无" 等占位符。
    """
    if value is None:
        return True
    s = str(value).strip().lower()
    if s in MISSING_VALUES:
        return True
    # 纯空白
    if not s:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════
#  缺失率统计
# ═══════════════════════════════════════════════════════════════════════

def _collect_columns(rows: list[dict]) -> list[str]:
    """收集所有行中出现过的列名（保持首次出现顺序）。"""
    seen = set()
    columns = []
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                columns.append(k)
    return columns


def profile_missing(rows: list[dict]) -> list[dict]:
    """
    逐列统计缺失率。

    参数:
        rows: 行列表（list[dict]）

    返回:
        每列的缺失统计，按缺失率降序排列：
        ``[{"column": str, "total": int, "missing": int, "missing_rate": float, "fill_rate": float}, ...]``
    """
    if not rows:
        return []
    columns = _collect_columns(rows)
    total = len(rows)
    result = []
    for col in columns:
        missing_count = sum(1 for row in rows if is_missing(row.get(col)))
        missing_rate = round(missing_count / total, 4) if total else 0.0
        result.append({
            "column": col,
            "total": total,
            "missing": missing_count,
            "missing_rate": missing_rate,
            "fill_rate": round(1.0 - missing_rate, 4),
        })
    # 按缺失率降序
    result.sort(key=lambda x: x["missing_rate"], reverse=True)
    return result


def profile_missing_grouped(
    rows: list[dict],
    group_col: str = "城市名称",
) -> dict[str, list[dict]]:
    """
    按指定列分组后逐列统计缺失率。

    参数:
        rows: 行列表
        group_col: 分组列名（如 "城市名称" / "区域名称"）

    返回:
        ``{group_value: [per-column-missing-stats], ...}``
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        gval = str(row.get(group_col, "未知")).strip() or "未知"
        groups.setdefault(gval, []).append(row)
    return {g: profile_missing(rs) for g, rs in groups.items()}


def missing_summary(rows: list[dict]) -> dict[str, Any]:
    """
    生成缺失值画像摘要报告。

    返回:
        包含 total_rows / total_columns / high_missing_columns /
        overall_missing_rate / per_column 的摘要字典
    """
    profile = profile_missing(rows)
    if not profile:
        return {"total_rows": 0, "total_columns": 0, "per_column": []}
    total_rows = len(rows)
    total_cols = len(profile)
    # 缺失率 > 50% 的列
    high_missing = [p for p in profile if p["missing_rate"] > 0.5]
    # 整体缺失率（所有单元格）
    total_cells = total_rows * total_cols
    total_missing = sum(p["missing"] for p in profile)
    overall_rate = round(total_missing / total_cells, 4) if total_cells else 0.0
    return {
        "total_rows": total_rows,
        "total_columns": total_cols,
        "total_cells": total_cells,
        "total_missing_cells": total_missing,
        "overall_missing_rate": overall_rate,
        "high_missing_columns": len(high_missing),
        "high_missing_column_names": [p["column"] for p in high_missing],
        "per_column": profile,
    }


# ═══════════════════════════════════════════════════════════════════════
#  热力图 HTML 生成
# ═══════════════════════════════════════════════════════════════════════

def generate_heatmap_html(
    profile: list[dict],
    title: str = "缺失值画像热力图",
    grouped: dict[str, list[dict]] | None = None,
) -> str:
    """
    生成自包含的热力图 HTML（纯 CSS/JS，无外部依赖）。

    参数:
        profile: ``profile_missing()`` 的返回值
        title: HTML 页面标题
        grouped: 分组数据（可选），用于多组对比热力图

    返回:
        完整的 HTML 字符串
    """
    rows_html = []
    for p in profile:
        rate = p["missing_rate"]
        # 颜色：0% 绿色 → 50%+ 红色
        if rate <= 0.05:
            color = "#2ecc71"
        elif rate <= 0.20:
            color = "#f1c40f"
        elif rate <= 0.50:
            color = "#e67e22"
        else:
            color = "#e74c3c"
        pct = f"{rate * 100:.1f}%"
        rows_html.append(f"""
        <tr>
          <td class="col-name">{escape(p['column'])}</td>
          <td class="num">{p['total']}</td>
          <td class="num">{p['missing']}</td>
          <td class="num">{pct}</td>
          <td>
            <div class="bar-container">
              <div class="bar" style="width:{pct}; background:{color};"></div>
            </div>
          </td>
        </tr>""")

    grouped_section = ""
    if grouped:
        group_tables = []
        for gname, gprofile in grouped.items():
            g_rows = []
            for p in gprofile[:20]:  # 每组最多展示 20 列
                rate = p["missing_rate"]
                pct = f"{rate * 100:.1f}%"
                g_rows.append(f'<tr><td>{escape(p["column"])}</td><td>{pct}</td></tr>')
            group_tables.append(f"""
            <div class="group-card">
              <h3>{escape(gname)}</h3>
              <table class="group-table">
                <tr><th>列名</th><th>缺失率</th></tr>
                {''.join(g_rows)}
              </table>
            </div>""")
        grouped_section = f"""
        <h2>分组缺失对比</h2>
        <div class="groups-grid">{''.join(group_tables)}</div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(title)}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif;
           background: #1a1a2e; color: #e0e0e0; padding: 24px; }}
    h1 {{ color: #00d4ff; margin-bottom: 16px; }}
    h2 {{ color: #f1c40f; margin: 24px 0 12px; }}
    .summary {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
    .stat-card {{ background: #16213e; border-radius: 8px; padding: 16px 24px;
                  border: 1px solid #0f3460; min-width: 150px; }}
    .stat-card .label {{ font-size: 12px; color: #8899aa; margin-bottom: 4px; }}
    .stat-card .value {{ font-size: 28px; font-weight: 700; color: #00d4ff; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; }}
    th {{ background: #0f3460; color: #00d4ff; padding: 10px 12px;
          text-align: left; font-size: 13px; border-bottom: 2px solid #00d4ff; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #1a1a3e; font-size: 13px; }}
    td.col-name {{ color: #e0e0e0; font-weight: 500; }}
    td.num {{ text-align: right; font-family: "Courier New", monospace; color: #f1c40f; }}
    .bar-container {{ background: #0a0a1e; border-radius: 4px; height: 20px; overflow: hidden; }}
    .bar {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
    .groups-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }}
    .group-card {{ background: #16213e; border-radius: 8px; padding: 16px; border: 1px solid #0f3460; }}
    .group-card h3 {{ color: #00d4ff; margin-bottom: 8px; font-size: 14px; }}
    .group-table {{ font-size: 12px; }}
    .group-table th {{ padding: 6px 8px; }}
    .group-table td {{ padding: 4px 8px; }}
  </style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <div class="summary">
    <div class="stat-card"><div class="label">总行数</div><div class="value">{profile[0]['total'] if profile else 0}</div></div>
    <div class="stat-card"><div class="label">总列数</div><div class="value">{len(profile)}</div></div>
    <div class="stat-card"><div class="label">缺失率>50%列</div><div class="value">{sum(1 for p in profile if p['missing_rate'] > 0.5)}</div></div>
  </div>
  <table>
    <tr>
      <th>列名</th>
      <th>总行数</th>
      <th>缺失数</th>
      <th>缺失率</th>
      <th>可视化</th>
    </tr>
    {''.join(rows_html)}
  </table>
  {grouped_section}
</body>
</html>"""
    return html


# ═══════════════════════════════════════════════════════════════════════
#  CSV 读写
# ═══════════════════════════════════════════════════════════════════════

def read_csv_rows(csv_path: str | Path) -> list[dict]:
    """用 csv 模块读取 CSV 文件为行列表。"""
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


# ═══════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """内置自测：10 条测试数据（含缺失），验证缺失率计算正确。"""
    # 测试数据：10 条，6 列，部分缺失
    test_rows = [
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "区域名称": "海棠区", "最新价格": "35000", "地址": "南田路16号", "百度地图纬度": "18.41"},
        {"楼盘名称": "海棠湾一号", "城市名称": "三亚", "区域名称": "海棠区", "最新价格": "",       "地址": "南田路16号", "百度地图纬度": "18.41"},  # 缺价格
        {"楼盘名称": "亚龙湾翡翠谷", "城市名称": "三亚", "区域名称": "吉阳区", "最新价格": "28000", "地址": "亚龙湾路88号", "百度地图纬度": "18.20"},
        {"楼盘名称": "",             "城市名称": "三亚", "区域名称": "吉阳区", "最新价格": "29000", "地址": "亚龙湾路88号", "百度地图纬度": ""},  # 缺名称+缺坐标
        {"楼盘名称": "清水湾度假村", "城市名称": "三亚", "区域名称": "",       "最新价格": "22000", "地址": "清水湾大道1号", "百度地图纬度": "18.50"},  # 缺区域
        {"楼盘名称": "清水湾度假村", "城市名称": "三亚", "区域名称": "陵水县", "最新价格": "nan",    "地址": "清水湾大道1号", "百度地图纬度": "18.50"},  # 缺价格(nan)
        {"楼盘名称": "香水湾一号", "城市名称": "三亚", "区域名称": "陵水县", "最新价格": "30000", "地址": "无", "百度地图纬度": "18.30"},           # 地址="无"=缺失
        {"楼盘名称": "香水湾一号", "城市名称": "三亚", "区域名称": "陵水县", "最新价格": "30000", "地址": "香水湾路1号", "百度地图纬度": "18.30"},
        {"楼盘名称": "香水湾一号", "城市名称": "三亚", "区域名称": "陵水县", "最新价格": "30000", "地址": "香水湾路1号", "百度地图纬度": "18.30"},
        {"楼盘名称": "香水湾一号", "城市名称": "三亚", "区域名称": "陵水县", "最新价格": "30000", "地址": "香水湾路1号", "百度地图纬度": "18.30"},
    ]

    profile = profile_missing(test_rows)

    # 断言 1：总列数 = 6
    assert len(profile) == 6, f"总列数应为 6，实际 {len(profile)}"
    print(f"  [OK] 总列数：{len(profile)}")

    # 断言 2：最新价格缺失 2 条（第1行空、第5行 nan），缺失率 = 2/10 = 0.2
    price_stat = next(p for p in profile if p["column"] == "最新价格")
    assert price_stat["missing"] == 2, f"最新价格缺失应为 2，实际 {price_stat['missing']}"
    assert price_stat["missing_rate"] == 0.2, f"最新价格缺失率应为 0.2，实际 {price_stat['missing_rate']}"
    print(f"  [OK] 最新价格缺失 {price_stat['missing']}/{price_stat['total']} = {price_stat['missing_rate']:.0%}")

    # 断言 3：楼盘名称缺失 1 条
    name_stat = next(p for p in profile if p["column"] == "楼盘名称")
    assert name_stat["missing"] == 1, f"楼盘名称缺失应为 1，实际 {name_stat['missing']}"
    print(f"  [OK] 楼盘名称缺失 {name_stat['missing']} 条")

    # 断言 4：地址缺失 1 条（"无" 视为缺失）
    addr_stat = next(p for p in profile if p["column"] == "地址")
    assert addr_stat["missing"] == 1, f"地址缺失应为 1（含 '无'），实际 {addr_stat['missing']}"
    print(f"  [OK] 地址缺失 {addr_stat['missing']} 条（含占位符 '无'）")

    # 断言 5：城市名称无缺失
    city_stat = next(p for p in profile if p["column"] == "城市名称")
    assert city_stat["missing"] == 0, f"城市名称不应有缺失"
    assert city_stat["missing_rate"] == 0.0
    print(f"  [OK] 城市名称无缺失")

    # 断言 6：按缺失率降序排列
    rates = [p["missing_rate"] for p in profile]
    assert rates == sorted(rates, reverse=True), f"应按缺失率降序：{rates}"
    print(f"  [OK] 按缺失率降序排列")

    # 断言 7：分组统计
    grouped = profile_missing_grouped(test_rows, "区域名称")
    assert "海棠区" in grouped, "应包含海棠区分组"
    assert "陵水县" in grouped, "应包含陵水县分组"
    print(f"  [OK] 分组统计：{list(grouped.keys())}")

    # 断言 8：摘要报告
    summary = missing_summary(test_rows)
    assert summary["total_rows"] == 10
    assert summary["total_columns"] == 6
    assert summary["total_cells"] == 60
    print(f"  [OK] 摘要：{summary['total_rows']} 行 × {summary['total_columns']} 列 = {summary['total_cells']} 单元格")

    # 断言 9：HTML 生成
    html = generate_heatmap_html(profile, title="T5 Selftest 热力图", grouped=grouped)
    assert "<table>" in html
    assert "最新价格" in html
    assert "海棠区" in html
    print(f"  [OK] HTML 热力图生成成功（{len(html)} 字符）")

    print("\n  === T5 缺失值画像 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="T5 缺失值画像 — 253 列逐列缺失率统计 + 热力图 HTML"
    )
    parser.add_argument("--csv", type=str, help="输入 CSV 文件路径")
    parser.add_argument("--city", type=str, default="三亚", help="城市名（用于查找默认 CSV）")
    parser.add_argument("--out", type=str, default="missing_heatmap.html", help="输出热力图 HTML 路径")
    parser.add_argument("--group-by", type=str, default=None, help="分组列名（如 城市名称 / 区域名称）")
    parser.add_argument("--json-out", type=str, default=None, help="同时输出 JSON 格式的缺失报告")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    # 确定输入 CSV
    csv_path = args.csv
    if not csv_path:
        vault = Path(__file__).resolve().parent.parent.parent / "Vault"
        csv_path = str(vault / "2026新楼盘" / f"新楼盘-{args.city}.csv")

    print(f"[T5] 读取数据：{csv_path}")
    rows = read_csv_rows(csv_path)
    print(f"[T5] 总行数：{len(rows)}")

    profile = profile_missing(rows)
    grouped = profile_missing_grouped(rows, args.group_by) if args.group_by else None
    summary = missing_summary(rows)

    # 生成 HTML
    html = generate_heatmap_html(profile, title=f"缺失值画像 — {args.city}", grouped=grouped)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"[T5] 热力图已写入：{out_path}")

    # 可选 JSON
    if args.json_out:
        json_path = Path(args.json_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"[T5] JSON 报告已写入：{json_path}")

    print(f"[T5] 总列数：{summary['total_columns']}，整体缺失率：{summary['overall_missing_rate']:.1%}")
    print(f"[T5] 高缺失列（>50%）：{summary['high_missing_column_names'][:10]}")


if __name__ == "__main__":
    main()
