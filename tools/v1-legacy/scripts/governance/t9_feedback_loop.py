# -*- coding: utf-8 -*-
"""
T9 持续迭代反馈循环 -- 字段补采优先级排序
==========================================
功能：读取字段使用频率日志（field_usage.jsonl）和字段质量分数（quality_scores.json），
      按 ``调用频率 * 质量缺口 = 优先补采排序`` 计算每个字段的补采优先级，
      输出 ``priority_fill_queue.csv``。

计算公式
--------
    优先级 = 归一化调用频率 * 质量缺口

其中:
    - 归一化调用频率 = 该字段调用次数 / 最大字段调用次数
    - 质量缺口 = (100 - 质量分数) / 100

优先级越高 = 调用频繁但质量不足 = 最应优先补采。

设计原则
--------
- 纯标准库（json / csv），不依赖 pandas
- 输入支持 JSONL（逐行 JSON）和 JSON（字典）
- 输出 CSV 可被人工或自动流程消费
- 支持从 CEO 用户权重 JSONL 自动生成 field_usage.jsonl（--generate-usage）

数据来源
--------
- field_usage.jsonl：由 --generate-usage 首次生成，后续由决策引擎每次调用时追加
  来源：data_out/ceo_learning/ 下各用户权重 JSONL 聚合
- quality_scores.json：由 T8 质量评估（t8_quality_assess.py）生成
  来源：data_out/governance/t8_quality/quality_scores.json

CLI
---
    # 从 CEO 用户权重文件生成 field_usage.jsonl
    python scripts/governance/t9_feedback_loop.py --generate-usage

    # 运行反馈循环（输出优先补采队列）
    python scripts/governance/t9_feedback_loop.py
        --usage data_out/ceo_learning/field_usage.jsonl
        --quality data_out/governance/t8_quality/quality_scores.json
        --out priority_fill_queue.csv

    # 自测
    python scripts/governance/t9_feedback_loop.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ── Agent → 字段映射（来源：DDS 决策引擎六智能体依赖分析） ─────────────────
# 每个 agent 使用的字段由 dds_decision_engine.py 的 run_*_agent 函数确定
AGENT_TO_FIELDS: dict[str, list[str]] = {
    "abm": [
        # abm_engine.py：蒙特卡洛 + MNL 随机效用模拟
        "最新价格", "容积率", "绿化率", "建筑面积", "规划户数",
        "开盘日期", "产权年限", "区域名称", "城市名称",
    ],
    "blueprint": [
        # 任务书/蓝图 agent：产品力与户型配比
        "容积率", "绿化率", "建筑类型", "占地面积", "规划户数",
        "车位数", "装修情况",
    ],
    "compliance": [
        # 合规红线 agent：政策与证件
        "预售证号", "产权年限", "发证时间", "用地条件和用地类型", "城市名称",
    ],
    "migration": [
        # 人口/市场 agent：价格趋势与区位
        "最新价格", "参考价格", "开盘日期", "区域名称", "城市名称", "环线位置",
    ],
    "unit_mix": [
        # 户型配比 agent：户型设计与面积
        "户型文本描述", "全部户型", "面积范围", "建筑面积", "规划户数", "容积率",
    ],
    "value": [
        # 价值机会 agent：溢价与风险
        "最新价格", "参考价格", "开发商", "物业公司", "车位数", "绿化率",
        "容积率", "周边配套", "房价市场状况",
    ],
}


# ═══════════════════════════════════════════════════════════════════════
#  field_usage.jsonl 生成器
# ═══════════════════════════════════════════════════════════════════════

def generate_field_usage(
    ceo_dir: str | Path,
    out_path: str | Path,
    agent_map: dict[str, list[str]] | None = None,
) -> int:
    """从 CEO 用户权重 JSONL 文件聚合生成 field_usage.jsonl。

    算法：
        1. 遍历 data_out/ceo_learning/ 下所有 user_*.jsonl
        2. 读取每条记录的 weights 字段（agent → weight 映射）
        3. 对权重 > 0 的 agent，累加其依赖字段的调用计数
        4. 按 call_count 降序输出到 field_usage.jsonl

    参数：
        ceo_dir:  CEO 学习数据目录（含 user_*.jsonl）
        out_path: 输出的 field_usage.jsonl 路径
        agent_map: agent → 字段映射表（默认使用 AGENT_TO_FIELDS）

    返回：
        生成的字段数
    """
    ceo_path = Path(ceo_dir)
    if not ceo_path.exists():
        raise FileNotFoundError(f"CEO 学习目录不存在: {ceo_path}")

    if agent_map is None:
        agent_map = AGENT_TO_FIELDS

    user_files = list(ceo_path.glob("user_*.jsonl"))
    if not user_files:
        raise FileNotFoundError(f"CEO 学习目录中无 user_*.jsonl 文件: {ceo_path}")

    field_counts: dict[str, int] = {}
    total_lines = 0

    for user_file in user_files:
        try:
            for line in open(user_file, "r", encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                weights = data.get("weights", {})
                for agent, w in weights.items():
                    if w is None or float(w) <= 0:
                        continue
                    fields = agent_map.get(agent, [])
                    for field in fields:
                        field_counts[field] = field_counts.get(field, 0) + 1
        except Exception as exc:
            print(f"  [WARN] 读取 {user_file.name} 失败: {exc}", file=sys.stderr)
            continue

    # 按调用次数降序排列
    sorted_fields = sorted(field_counts.items(), key=lambda x: x[1], reverse=True)

    # 写 field_usage.jsonl
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")

    with open(out_path, "w", encoding="utf-8") as f:
        for field, count in sorted_fields:
            record = {
                "field": field,
                "call_count": count,
                "cities": [],  # 后续决策引擎调用时补充
                "timestamp": now,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"[T9] field_usage.jsonl 已生成: {out_path}")
    print(f"[T9]   用户文件: {len(user_files)} 个")
    print(f"[T9]   总记录行: {total_lines}")
    print(f"[T9]   字段数:   {len(sorted_fields)}")
    print(f"[T9]   Top 5:")
    for field, count in sorted_fields[:5]:
        print(f"[T9]     {field}: {count}")

    return len(sorted_fields)


# ═══════════════════════════════════════════════════════════════════════
#  数据读取
# ═══════════════════════════════════════════════════════════════════════

def read_field_usage(path: str | Path) -> list[dict[str, Any]]:
    """
    读取字段使用频率日志。

    支持两种格式:
        - JSONL（每行一个 JSON 对象，含 field/call_count/timestamp 等字段）
        - JSON（列表 或 {field: count} 字典）

    返回:
        字段使用记录列表，每条至少包含 ``field`` 和 ``call_count``
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"字段使用日志不存在: {p}")

    content = p.read_text(encoding="utf-8").strip()
    if not content:
        return []

    # 尝试 JSONL（逐行）
    records = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            records.append(obj)
        except json.JSONDecodeError:
            continue

    # 如果只有一行且是 JSON，尝试解析为整体
    if not records:
        try:
            data = json.loads(content)
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                # {field: count} 格式
                records = [{"field": k, "call_count": v} for k, v in data.items()]
        except json.JSONDecodeError:
            pass

    # 归一化：确保每条记录有 field 和 call_count
    normalized = []
    for r in records:
        field = r.get("field") or r.get("字段") or r.get("name")
        count = r.get("call_count") or r.get("count") or r.get("calls") or 0
        if field:
            normalized.append({
                "field": str(field),
                "call_count": int(count) if count else 0,
                "cities": r.get("cities", []),
                "last_used": r.get("timestamp") or r.get("last_used", ""),
                **{k: v for k, v in r.items()
                   if k not in ("field", "字段", "name", "call_count", "count", "calls")},
            })
    return normalized


def read_quality_scores(path: str | Path) -> dict[str, int]:
    """
    读取字段质量分数。

    支持两种格式:
        - JSON 字典: ``{"field_name": 85, ...}``
        - JSON 嵌套: ``{"field_name": {"score": 85, ...}, ...}``
        - 含 overall/grade 等元数据的 JSON

    返回:
        ``{field_name: quality_score (0-100)}``
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"质量分数文件不存在: {p}")

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    scores = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if k in ("overall", "grade", "total_rows", "assessed_at", "weights"):
                continue
            if isinstance(v, dict):
                score = v.get("score", 0)
            elif isinstance(v, (int, float)):
                score = v
            else:
                continue
            scores[k] = int(score) if score else 0
    return scores


# ═══════════════════════════════════════════════════════════════════════
#  优先级计算
# ═══════════════════════════════════════════════════════════════════════

def compute_priority(
    field_usage_path: str | Path,
    quality_scores_path: str | Path,
) -> list[dict[str, Any]]:
    """
    计算字段补采优先级。

    算法:
        1. 聚合字段调用频率（跨城市/时间汇总）
        2. 归一化调用频率 = count / max_count
        3. 质量缺口 = (100 - quality_score) / 100
        4. 优先级 = 归一化频率 * 质量缺口
        5. 按优先级降序排列

    参数:
        field_usage_path: 字段使用日志路径（JSONL 或 JSON）
        quality_scores_path: 字段质量分数路径（JSON）

    返回:
        优先级排序列表，每条包含：
        ``{field, call_count, normalized_freq, quality_score, quality_gap, priority, recommendation}``
    """
    usage_records = read_field_usage(field_usage_path)
    quality_scores = read_quality_scores(quality_scores_path)

    if not usage_records:
        return []

    # 1. 聚合调用频率
    field_counts: dict[str, int] = {}
    field_cities: dict[str, set] = {}
    for r in usage_records:
        f = r["field"]
        field_counts[f] = field_counts.get(f, 0) + r["call_count"]
        cities = r.get("cities", [])
        if cities:
            field_cities.setdefault(f, set()).update(cities)

    max_count = max(field_counts.values()) if field_counts else 1

    # 2. 计算优先级
    result = []
    for field, count in field_counts.items():
        norm_freq = count / max_count if max_count > 0 else 0
        q_score = quality_scores.get(field, 50)  # 默认 50 分
        quality_gap = (100 - q_score) / 100
        priority = round(norm_freq * quality_gap, 4)

        # 补采建议
        if priority >= 0.5:
            recommendation = "紧急补采"
        elif priority >= 0.3:
            recommendation = "优先补采"
        elif priority >= 0.1:
            recommendation = "建议补采"
        else:
            recommendation = "暂缓"

        result.append({
            "field": field,
            "call_count": count,
            "normalized_freq": round(norm_freq, 4),
            "quality_score": q_score,
            "quality_gap": round(quality_gap, 4),
            "priority": priority,
            "recommendation": recommendation,
            "cities_covered": len(field_cities.get(field, set())),
        })

    # 3. 按优先级降序
    result.sort(key=lambda x: x["priority"], reverse=True)
    return result


def compute_priority_from_csv(
    csv_path: str | Path,
    field_usage_path: str | Path,
) -> list[dict[str, Any]]:
    """从 CSV 直接计算字段缺失率作为质量分数，结合 field_usage 计算优先级。

    当 T8 quality_scores.json 不可用时，用此降级路径：
    - 质量分数 = 填充率 * 100（即非空值占比）
    - 质量缺口 = 缺失率

    参数:
        csv_path: 楼盘 CSV 路径
        field_usage_path: field_usage.jsonl 路径

    返回:
        优先级排序列表（同 compute_priority）
    """
    csv_p = Path(csv_path)
    if not csv_p.exists():
        raise FileNotFoundError(f"CSV 不存在: {csv_p}")

    # 读取 CSV 计算字段缺失率
    with open(csv_p, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return []

    field_quality: dict[str, int] = {}
    for col in rows[0].keys():
        non_empty = sum(1 for r in rows if r.get(col, "").strip())
        fill_rate = non_empty / len(rows) if rows else 0
        field_quality[col] = round(fill_rate * 100)

    # 写临时 quality_scores.json
    tmp_quality = csv_p.parent.parent / "data_out" / "governance" / "t9_temp_quality_scores.json"
    tmp_quality.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_quality, "w", encoding="utf-8") as f:
        json.dump(field_quality, f, ensure_ascii=False, indent=2)

    return compute_priority(field_usage_path, tmp_quality)


def priority_summary(priority_queue: list[dict]) -> dict[str, Any]:
    """生成优先级摘要。"""
    if not priority_queue:
        return {"total_fields": 0}
    urgent = sum(1 for p in priority_queue if p["recommendation"] == "紧急补采")
    high = sum(1 for p in priority_queue if p["recommendation"] == "优先补采")
    medium = sum(1 for p in priority_queue if p["recommendation"] == "建议补采")
    low = sum(1 for p in priority_queue if p["recommendation"] == "暂缓")
    return {
        "total_fields": len(priority_queue),
        "urgent": urgent,
        "high_priority": high,
        "medium_priority": medium,
        "low_priority": low,
        "top_5_fields": [p["field"] for p in priority_queue[:5]],
    }


# ═══════════════════════════════════════════════════════════════════════
#  CSV 输出
# ═══════════════════════════════════════════════════════════════════════

def write_priority_csv(priority_queue: list[dict], out_path: str | Path) -> None:
    """将优先级队列写入 CSV 文件。"""
    if not priority_queue:
        return
    fieldnames = ["field", "call_count", "normalized_freq", "quality_score",
                  "quality_gap", "priority", "recommendation", "cities_covered"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in priority_queue:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


# ═══════════════════════════════════════════════════════════════════════
#  Pipeline 入口（供 pipeline_scheduler.py 调用）
# ═══════════════════════════════════════════════════════════════════════

def run_pipeline(
    usage_path: str | None = None,
    quality_path: str | None = None,
    out_path: str | None = None,
    city: str = "三亚",
) -> dict[str, Any]:
    """T9 反馈循环 pipeline 入口函数。

    供 pipeline_scheduler.py 和 FC 云端调用。

    参数:
        usage_path:  field_usage.jsonl 路径（默认自动检测）
        quality_path: quality_scores.json 路径（默认自动检测）
        out_path:     输出 CSV 路径（默认 data_out/governance/t9_priority/priority_fill_queue.csv）
        city:         目标城市名

    返回:
        {"status": "ok", "summary": {...}, "output_file": "..."}
    """
    if usage_path is None:
        usage_path = str(PROJECT_ROOT / "data_out" / "ceo_learning" / "field_usage.jsonl")
    if quality_path is None:
        quality_path = str(PROJECT_ROOT / "data_out" / "governance" / "t8_quality" / "quality_scores.json")
    if out_path is None:
        out_path = str(PROJECT_ROOT / "data_out" / "governance" / "t9_priority" / "priority_fill_queue.csv")

    print(f"[T9] 读取字段使用日志：{usage_path}")
    print(f"[T9] 读取质量分数：{quality_path}")

    # 如果 field_usage.jsonl 不存在，先尝试从 CEO 数据生成
    usage_p = Path(usage_path)
    if not usage_p.exists():
        ceo_dir = PROJECT_ROOT / "data_out" / "ceo_learning"
        if ceo_dir.exists() and list(ceo_dir.glob("user_*.jsonl")):
            print("[T9] field_usage.jsonl 不存在，从 CEO 用户权重聚合生成...")
            generate_field_usage(ceo_dir, usage_p)

    # 如果 quality_scores.json 不存在，降级到 CSV 缺失率
    quality_p = Path(quality_path)
    if not quality_p.exists():
        csv_path = PROJECT_ROOT / "Vault" / "2026新楼盘" / f"新楼盘-{city}.csv"
        if csv_path.exists():
            print(f"[T9] quality_scores.json 不存在，降级：从 CSV 缺失率计算质量分数 ({csv_path})")
            priority = compute_priority_from_csv(csv_path, usage_p)
        else:
            return {"status": "error", "error": f"CSV 不存在: {csv_path}"}
    else:
        priority = compute_priority(usage_p, quality_p)

    if not priority:
        return {"status": "error", "error": "无数据，请检查输入文件"}

    # 写 CSV
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    write_priority_csv(priority, out_p)

    summary = priority_summary(priority)
    print(f"[T9] 优先级队列已写入：{out_p}")
    print(f"[T9] 汇总：{summary['total_fields']} 字段，"
          f"紧急 {summary['urgent']}，高优 {summary['high_priority']}")
    print(f"[T9] Top 5 补采字段：")
    for p in priority[:5]:
        print(f"      {p['field']:15s}  优先级={p['priority']:.4f}  "
              f"频率={p['normalized_freq']:.2f}  质量={p['quality_score']}  "
              f"建议={p['recommendation']}")

    return {
        "status": "ok",
        "summary": summary,
        "output_file": str(out_p),
        "mode": "csv_fallback" if not quality_p.exists() else "full",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


# ═══════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """内置自测：验证完整流程。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # ── 测试 1: 生成 field_usage ──
        # 构造模拟 CEO 用户数据
        ceo_dir = tmpdir / "ceo_learning"
        ceo_dir.mkdir()
        user_file = ceo_dir / "user_test.jsonl"
        with open(user_file, "w", encoding="utf-8") as f:
            for _ in range(10):
                f.write(json.dumps({
                    "ts": "2026-07-01T00:00:00",
                    "weights": {"abm": 0.3, "value": 0.3, "unit_mix": 0.2, "blueprint": 0.1, "compliance": 0.05, "migration": 0.05},
                }, ensure_ascii=False) + "\n")

        usage_path = tmpdir / "field_usage.jsonl"
        n = generate_field_usage(ceo_dir, usage_path)
        assert n > 0, f"应生成 > 0 个字段，实际 {n}"
        print(f"  [OK] generate_field_usage: 生成 {n} 个字段")

        # ── 测试 2: 优先级计算 ──
        quality_path = tmpdir / "quality_scores.json"
        quality_scores = {
            "最新价格": 40,   # 质量低 -> 质量缺口大
            "楼盘名称": 95,   # 质量高 -> 质量缺口小
            "容积率": 60,     # 中等
            "绿化率": 55,
            "车位数": 45,
            "建筑面积": 70,
            "规划户数": 65,
            "开发商": 30,
            "物业公司": 50,
            "城市名称": 98,
            "区域名称": 90,
            "产权年限": 75,
        }
        with open(quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_scores, f, ensure_ascii=False)

        priority = compute_priority(usage_path, quality_path)

        # 断言 1：返回非空
        assert len(priority) > 0, "应有字段"
        print(f"  [OK] 返回 {len(priority)} 个字段")

        # 断言 2：按优先级降序
        priorities = [p["priority"] for p in priority]
        assert priorities == sorted(priorities, reverse=True), \
            f"应按优先级降序：{priorities}"
        print(f"  [OK] 按优先级降序排列")

        # 断言 3：CSV 输出
        csv_path = tmpdir / "priority_fill_queue.csv"
        write_priority_csv(priority, csv_path)
        assert csv_path.exists(), "CSV 文件应已生成"
        csv_content = csv_path.read_text(encoding="utf-8-sig")
        assert "priority" in csv_content
        print(f"  [OK] CSV 输出成功（{len(csv_content)} 字符）")

        # 断言 4：摘要
        summary = priority_summary(priority)
        assert summary["total_fields"] == len(priority)
        print(f"  [OK] 摘要：{summary['total_fields']} 字段，"
              f"紧急 {summary['urgent']}，高优 {summary['high_priority']}")

        # 断言 5：未在质量分数中的字段用默认值 50
        unknown_fields = [p for p in priority if p["quality_score"] == 50
                          and p["field"] not in quality_scores]
        if unknown_fields:
            print(f"  [OK] 未覆盖字段使用默认质量 50："
                  f"{', '.join(p['field'] for p in unknown_fields[:3])}")

        # ── 测试 3: read_quality_scores 嵌套格式 ──
        nested_quality = tmpdir / "nested_quality.json"
        with open(nested_quality, "w", encoding="utf-8") as f:
            json.dump({
                "completeness": {"score": 85},
                "consistency": {"score": 90},
                "overall": 88,
                "grade": "B",
                "weights": {},
            }, f, ensure_ascii=False)
        scores = read_quality_scores(nested_quality)
        assert scores["completeness"] == 85
        assert scores["consistency"] == 90
        assert "overall" not in scores  # 元数据键应被过滤
        print(f"  [OK] read_quality_scores 嵌套格式: {scores}")

    print("\n  === T9 反馈循环 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="T9 持续迭代反馈循环 -- 字段补采优先级排序"
    )
    parser.add_argument("--usage", type=str,
                        default="data_out/ceo_learning/field_usage.jsonl",
                        help="字段使用日志路径（JSONL）")
    parser.add_argument("--quality", type=str,
                        default="data_out/governance/t8_quality/quality_scores.json",
                        help="字段质量分数路径（JSON）")
    parser.add_argument("--out", type=str, default="priority_fill_queue.csv",
                        help="输出优先级队列 CSV 路径")
    parser.add_argument("--city", type=str, default="三亚",
                        help="城市名（降级模式从 CSV 计算质量分数时使用）")
    parser.add_argument("--generate-usage", action="store_true",
                        help="从 CEO 用户权重 JSONL 生成 field_usage.jsonl")
    parser.add_argument("--ceo-dir", type=str,
                        default="data_out/ceo_learning",
                        help="CEO 学习数据目录（--generate-usage 时使用）")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    if args.generate_usage:
        usage_out = Path(args.usage)
        if not usage_out.is_absolute():
            usage_out = PROJECT_ROOT / args.usage
        ceo_dir = Path(args.ceo_dir)
        if not ceo_dir.is_absolute():
            ceo_dir = PROJECT_ROOT / args.ceo_dir
        generate_field_usage(ceo_dir, usage_out)
        return

    # 运行 pipeline
    usage_path = Path(args.usage)
    if not usage_path.is_absolute():
        usage_path = PROJECT_ROOT / args.usage
    quality_path = Path(args.quality)
    if not quality_path.is_absolute():
        quality_path = PROJECT_ROOT / args.quality
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = PROJECT_ROOT / args.out

    result = run_pipeline(
        usage_path=str(usage_path),
        quality_path=str(quality_path),
        out_path=str(out_path),
        city=args.city,
    )

    if result["status"] == "error":
        print(f"[T9] 错误: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()