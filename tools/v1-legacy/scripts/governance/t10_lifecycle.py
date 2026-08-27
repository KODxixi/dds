# -*- coding: utf-8 -*-
"""
T10 数据生命周期管理 — 热温冷分层 + TOS 策略
=============================================
功能：按数据年龄将 Vault 中的文件分层（热/温/冷/归档/冷归档），
      生成 TOS（Tier of Storage）生命周期策略 JSON，指导存储迁移和保留。

生命周期分层
------------
| 层级       | 年龄      | 存储介质  | 访问频率 | 策略           |
|------------|-----------|-----------|----------|----------------|
| hot        | 0-30 天   | NVMe/SSD  | 高       | 保持在线       |
| warm       | 31-180 天 | SSD/HDD   | 中       | 保持在线       |
| cold       | 181-365 天| HDD       | 低       | 可压缩         |
| archive    | 1-3 年    | 冷存储    | 极低     | 迁移至冷存储   |
| cold_archive| >3 年    | 冰川存储  | 合规     | 仅保留合规需要 |

设计原则
--------
- 纯标准库（os / json / time / csv），不依赖 pandas
- 基于文件修改时间分类
- 非破坏：只生成策略，不实际迁移文件

CLI
---
    python scripts/governance/t10_lifecycle.py --vault Vault/ --out lifecycle_policy.json
    python scripts/governance/t10_lifecycle.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# ── 生命周期分层定义 ──────────────────────────────────────────────────
LIFECYCLE_TIERS = [
    {
        "name": "hot",
        "label": "热数据",
        "max_age_days": 30,
        "storage": "NVMe SSD",
        "access_frequency": "高",
        "retention": "永久保持在线",
        "compression": False,
        "migration": "无",
    },
    {
        "name": "warm",
        "label": "温数据",
        "max_age_days": 180,
        "storage": "SSD / HDD",
        "access_frequency": "中",
        "retention": "保持在线",
        "compression": False,
        "migration": "无",
    },
    {
        "name": "cold",
        "label": "冷数据",
        "max_age_days": 365,
        "storage": "HDD",
        "access_frequency": "低",
        "retention": "可压缩存储",
        "compression": True,
        "migration": "可选压缩",
    },
    {
        "name": "archive",
        "label": "归档数据",
        "max_age_days": 1095,  # 3 年
        "storage": "冷存储",
        "access_frequency": "极低",
        "retention": "迁移至冷存储",
        "compression": True,
        "migration": "迁移至冷存储",
    },
    {
        "name": "cold_archive",
        "label": "冷归档数据",
        "max_age_days": 99999,  # 无上限
        "storage": "冰川存储",
        "access_frequency": "合规",
        "retention": "仅保留合规需要",
        "compression": True,
        "migration": "迁移至冰川存储",
    },
]


# ═══════════════════════════════════════════════════════════════════════
#  生命周期分类
# ═══════════════════════════════════════════════════════════════════════

def get_file_age_days(mtime: float) -> int:
    """根据文件修改时间戳计算年龄（天）。"""
    now = time.time()
    return int((now - mtime) / 86400)


def classify_tier(age_days: int) -> dict[str, Any]:
    """根据文件年龄确定生命周期层级。"""
    for tier in LIFECYCLE_TIERS:
        if age_days <= tier["max_age_days"]:
            return tier
    return LIFECYCLE_TIERS[-1]  # 默认归到最冷层


def classify_lifecycle(vault_path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    扫描 Vault 目录，对每个文件进行生命周期分层。

    参数:
        vault_path: Vault 根目录路径

    返回:
        (file_classifications, tos_policy) —
        file_classifications: 每文件的分层信息列表
        tos_policy: TOS 生命周期策略 JSON（汇总 + 迁移建议）
    """
    vault = Path(vault_path)
    if not vault.exists():
        raise FileNotFoundError(f"Vault 路径不存在: {vault}")

    file_classifications = []
    tier_counts: dict[str, int] = {t["name"]: 0 for t in LIFECYCLE_TIERS}
    tier_sizes: dict[str, int] = {t["name"]: 0 for t in LIFECYCLE_TIERS}
    total_files = 0
    total_size = 0

    # 递归扫描所有文件
    for root, dirs, files in os.walk(vault):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            fpath = Path(root) / fname
            try:
                stat = fpath.stat()
            except OSError:
                continue

            age_days = get_file_age_days(stat.st_mtime)
            tier = classify_tier(age_days)
            file_size = stat.st_size

            file_classifications.append({
                "path": str(fpath.relative_to(vault)),
                "filename": fname,
                "size_bytes": file_size,
                "size_mb": round(file_size / (1024 * 1024), 2),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "age_days": age_days,
                "lifecycle_tier": tier["name"],
                "lifecycle_label": tier["label"],
                "storage": tier["storage"],
                "recommendation": tier["migration"],
            })

            tier_counts[tier["name"]] += 1
            tier_sizes[tier["name"]] += file_size
            total_files += 1
            total_size += file_size

    # 生成 TOS 策略
    tos_policy = {
        "policy_name": "DDS Vault Lifecycle Policy",
        "generated_at": datetime.now().isoformat(),
        "vault_path": str(vault),
        "total_files": total_files,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "tier_summary": {
            t["name"]: {
                "label": t["label"],
                "file_count": tier_counts[t["name"]],
                "size_mb": round(tier_sizes[t["name"]] / (1024 * 1024), 2),
                "storage": t["storage"],
                "access_frequency": t["access_frequency"],
                "retention": t["retention"],
                "compression": t["compression"],
                "migration": t["migration"],
                "max_age_days": t["max_age_days"],
            }
            for t in LIFECYCLE_TIERS
        },
        "migration_recommendations": _gen_migration_recs(tier_counts, tier_sizes),
    }

    return file_classifications, tos_policy


def _gen_migration_recs(
    tier_counts: dict[str, int], tier_sizes: dict[str, int]
) -> list[dict[str, Any]]:
    """生成迁移建议列表。"""
    recs = []
    # 冷数据压缩建议
    if tier_counts.get("cold", 0) > 0:
        recs.append({
            "action": "compress",
            "target_tier": "cold",
            "file_count": tier_counts["cold"],
            "estimated_saving_pct": 50,
            "reason": "冷数据可压缩以节省存储",
        })
    # 归档迁移建议
    if tier_counts.get("archive", 0) > 0:
        recs.append({
            "action": "migrate_to_cold_storage",
            "target_tier": "archive",
            "file_count": tier_counts["archive"],
            "reason": "归档数据应迁移至冷存储降低成本",
        })
    # 冷归档迁移建议
    if tier_counts.get("cold_archive", 0) > 0:
        recs.append({
            "action": "migrate_to_glacier",
            "target_tier": "cold_archive",
            "file_count": tier_counts["cold_archive"],
            "reason": "冷归档数据仅保留合规需要，迁移至冰川存储",
        })
    return recs


# ═══════════════════════════════════════════════════════════════════════
#  辅助：用于 selftest 的虚拟文件列表
# ═══════════════════════════════════════════════════════════════════════

def classify_files_by_age(
    file_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    对虚拟文件列表（含 age_days）进行生命周期分层（用于 selftest）。

    参数:
        file_list: 每条包含 ``filename``, ``age_days``, ``size_bytes`` 的字典列表

    返回:
        每文件的分层信息列表
    """
    result = []
    for f in file_list:
        age = f.get("age_days", 0)
        tier = classify_tier(age)
        result.append({
            "filename": f.get("filename", ""),
            "age_days": age,
            "size_bytes": f.get("size_bytes", 0),
            "lifecycle_tier": tier["name"],
            "lifecycle_label": tier["label"],
            "storage": tier["storage"],
            "recommendation": tier["migration"],
        })
    return result


# ═══════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """内置自测：测试文件列表（含不同修改时间），验证分类正确。"""
    # 虚拟文件列表（不同年龄 → 不同生命周期层）
    test_files = [
        {"filename": "新楼盘-三亚.csv", "age_days": 5, "size_bytes": 1048576},      # hot
        {"filename": "新楼盘-杭州.csv", "age_days": 60, "size_bytes": 2097152},     # warm
        {"filename": "二手房-济南.csv", "age_days": 200, "size_bytes": 5242880},     # cold
        {"filename": "2018年-三亚.csv", "age_days": 500, "size_bytes": 1048576},     # archive
        {"filename": "2015年-杭州.csv", "age_days": 1500, "size_bytes": 1048576},    # cold_archive
        {"filename": "新楼盘-济南.csv", "age_days": 15, "size_bytes": 3145728},     # hot
        {"filename": "2020年-三亚.csv", "age_days": 900, "size_bytes": 2097152},    # archive
    ]

    classified = classify_files_by_age(test_files)

    # 断言 1：文件数正确
    assert len(classified) == 7, f"应分类 7 个文件，实际 {len(classified)}"
    print(f"  [OK] 分类 {len(classified)} 个文件")

    # 断言 2：分层正确
    tier_map = {f["filename"]: f["lifecycle_tier"] for f in classified}
    assert tier_map["新楼盘-三亚.csv"] == "hot", f"5天应为 hot，实际 {tier_map['新楼盘-三亚.csv']}"
    assert tier_map["新楼盘-济南.csv"] == "hot", f"15天应为 hot，实际 {tier_map['新楼盘-济南.csv']}"
    assert tier_map["新楼盘-杭州.csv"] == "warm", f"60天应为 warm，实际 {tier_map['新楼盘-杭州.csv']}"
    assert tier_map["二手房-济南.csv"] == "cold", f"200天应为 cold，实际 {tier_map['二手房-济南.csv']}"
    assert tier_map["2018年-三亚.csv"] == "archive", f"500天应为 archive，实际 {tier_map['2018年-三亚.csv']}"
    assert tier_map["2020年-三亚.csv"] == "archive", f"900天应为 archive，实际 {tier_map['2020年-三亚.csv']}"
    assert tier_map["2015年-杭州.csv"] == "cold_archive", f"1500天应为 cold_archive，实际 {tier_map['2015年-杭州.csv']}"
    print(f"  [OK] 分层正确：hot={sum(1 for f in classified if f['lifecycle_tier']=='hot')}, "
          f"warm={sum(1 for f in classified if f['lifecycle_tier']=='warm')}, "
          f"cold={sum(1 for f in classified if f['lifecycle_tier']=='cold')}, "
          f"archive={sum(1 for f in classified if f['lifecycle_tier']=='archive')}, "
          f"cold_archive={sum(1 for f in classified if f['lifecycle_tier']=='cold_archive')}")

    # 断言 3：边界值测试
    assert classify_tier(0)["name"] == "hot", "0天应 hot"
    assert classify_tier(30)["name"] == "hot", "30天应 hot"
    assert classify_tier(31)["name"] == "warm", "31天应 warm"
    assert classify_tier(180)["name"] == "warm", "180天应 warm"
    assert classify_tier(181)["name"] == "cold", "181天应 cold"
    assert classify_tier(365)["name"] == "cold", "365天应 cold"
    assert classify_tier(366)["name"] == "archive", "366天应 archive"
    assert classify_tier(1095)["name"] == "archive", "1095天(3年)应 archive"
    assert classify_tier(1096)["name"] == "cold_archive", "1096天应 cold_archive"
    print(f"  [OK] 边界值测试通过（30/31/180/181/365/366/1095/1096 天）")

    # 断言 4：存储介质对应正确
    assert classified[0]["storage"] == "NVMe SSD", f"hot 应 NVMe SSD"
    assert classified[2]["storage"] == "HDD", f"cold 应 HDD"
    print(f"  [OK] 存储介质对应正确")

    # 断言 5：TOS 策略生成（用实际目录测试）
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试文件
        tmpdir = Path(tmpdir)
        (tmpdir / "hot.csv").write_text("test", encoding="utf-8")
        # 修改时间设为 5 天前
        old_time = time.time() - 5 * 86400
        os.utime(str(tmpdir / "hot.csv"), (old_time, old_time))

        file_cls, tos = classify_lifecycle(tmpdir)
        assert tos["total_files"] >= 1, "应至少扫描到 1 个文件"
        assert "tier_summary" in tos
        assert "migration_recommendations" in tos
        assert "hot" in tos["tier_summary"]
        print(f"  [OK] TOS 策略生成：{tos['total_files']} 文件，"
              f"{tos['total_size_mb']} MB")

    print("\n  === T10 数据生命周期管理 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="T10 数据生命周期管理 — 热温冷分层 + TOS 策略"
    )
    parser.add_argument("--vault", type=str, default="Vault/",
                        help="Vault 根目录路径")
    parser.add_argument("--out", type=str, default="lifecycle_policy.json",
                        help="输出 TOS 生命周期策略 JSON 路径")
    parser.add_argument("--csv-out", type=str, default=None,
                        help="同时输出文件级分类 CSV")
    parser.add_argument("--selftest", action="store_true", help="运行内置自测")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return

    # 确定 Vault 路径
    vault_path = Path(args.vault)
    if not vault_path.is_absolute():
        vault_path = Path(__file__).resolve().parent.parent.parent / args.vault

    print(f"[T10] 扫描 Vault：{vault_path}")
    file_cls, tos = classify_lifecycle(vault_path)

    # 写 TOS 策略 JSON
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tos, f, ensure_ascii=False, indent=2)
    print(f"[T10] TOS 策略已写入：{out_path}")

    # 可选 CSV
    if args.csv_out:
        import csv
        csv_path = Path(args.csv_out)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["path", "filename", "size_mb", "modified_at", "age_days",
                      "lifecycle_tier", "lifecycle_label", "storage", "recommendation"]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for fc in file_cls:
                writer.writerow({k: fc.get(k, "") for k in fieldnames})
        print(f"[T10] 文件分类 CSV 已写入：{csv_path}")

    print(f"[T10] 总文件：{tos['total_files']}，总大小：{tos['total_size_mb']} MB")
    for tier_name, tier_info in tos["tier_summary"].items():
        if tier_info["file_count"] > 0:
            print(f"      {tier_info['label']:8s} ({tier_name:12s}): "
                  f"{tier_info['file_count']:4d} 文件, "
                  f"{tier_info['size_mb']:8.1f} MB, "
                  f"存储={tier_info['storage']}")


if __name__ == "__main__":
    main()
