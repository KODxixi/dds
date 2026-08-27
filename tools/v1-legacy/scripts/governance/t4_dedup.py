# -*- coding: utf-8 -*-
"""
T4 去重模块 — MinHash + SimHash 双重哈希近似去重
================================================
功能：按「楼盘名称+城市名称+地址」组合键进行近似匹配去重。
      MinHash 估算 Jaccard 相似度（基于 n-gram shingles），
      SimHash 生成 64 位指纹用于快速 LSH 近邻检索。
      非破坏式——标记 dup_group_id 和 dup_rank 列，不删行。

设计原则
--------
- 纯标准库实现（hashlib / csv / json），不依赖 pandas
- 行列表 ``list[dict]`` 作为通用表格表示，等价于 DataFrame 逐行
- 非破坏式：只追加标记列，不修改/删除原始行

CLI
---
    python scripts/governance/t4_dedup.py --city 三亚 --out dedup_report.json
    python scripts/governance/t4_dedup.py --csv Vault/2026新楼盘/新楼盘-三亚.csv --out dedup_report.json
    python scripts/governance/t4_dedup.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# ── 默认参数 ──────────────────────────────────────────────────────────
DEFAULT_KEYS = ["楼盘名称", "城市名称", "地址"]
MINHASH_NUM_PERM = 128       # MinHash 置换数（越多越准，越慢）
SIMHASH_BITS = 64           # SimHash 指纹位数
JACCARD_THRESHOLD = 0.85    # MinHash Jaccard 相似度阈值（≥ 视为重复）
SIMHASH_HAMMING_THRESHOLD = 3  # SimHash 海明距离阈值（≤ 视为疑似重复）
SHINGLE_SIZE = 3            # n-gram shingle 大小（按字符）


# ═══════════════════════════════════════════════════════════════════════
#  文本预处理
# ═══════════════════════════════════════════════════════════════════════

# 中文数字 → 阿拉伯数字映射（用于归一化楼盘名称中的数字差异）
_NUMERAL_MAP = {
    "零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
    "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
    "十": "10", "壹": "1", "贰": "2", "叁": "3", "肆": "4",
    "伍": "5", "陆": "6", "柒": "7", "捌": "8", "玖": "9", "拾": "10",
}


def normalize_text(text: str) -> str:
    """文本归一化：去空白/标点/转小写 + 中文数字转阿拉伯数字。"""
    if not text:
        return ""
    s = str(text)
    # 中文数字 → 阿拉伯数字（在去标点前处理，避免丢失信息）
    for cn, ar in _NUMERAL_MAP.items():
        s = s.replace(cn, ar)
    # 去除所有空白和标点（中英文）
    cleaned = re.sub(r"[\s\u3000\W_]+", "", s, flags=re.UNICODE)
    return cleaned.lower()


def shingle(text: str, k: int = SHINGLE_SIZE) -> set[str]:
    """将文本切分为 k-gram 字符 shingle 集合（用于 MinHash）。"""
    text = normalize_text(text)
    if len(text) < k:
        return {text} if text else set()
    return {text[i : i + k] for i in range(len(text) - k + 1)}


def build_dedup_key(row: dict, keys: list[str]) -> str:
    """从行字典中提取去重键值并拼接为归一化字符串。"""
    parts = []
    for k in keys:
        v = row.get(k, "")
        parts.append(normalize_text(str(v)) if v else "")
    return "|".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  MinHash 签名
# ═══════════════════════════════════════════════════════════════════════

def _hash_shingle(shingle_str: str, seed: int) -> int:
    """对单个 shingle 用指定种子做哈希，返回 32 位无符号整数。"""
    h = hashlib.md5(f"{seed}:{shingle_str}".encode("utf-8")).digest()
    return int.from_bytes(h[:4], "little")


def minhash_signature(text: str, num_perm: int = MINHASH_NUM_PERM) -> list[int]:
    """
    计算 MinHash 签名。

    参数:
        text: 待签名的文本
        num_perm: 置换函数数量（签名长度）

    返回:
        长度 ``num_perm`` 的整数列表，每个元素是该置换下的最小哈希值
    """
    shingles = shingle(text)
    if not shingles:
        return [0xFFFFFFFF] * num_perm
    signature = []
    for i in range(num_perm):
        min_hash = min(_hash_shingle(s, i) for s in shingles)
        signature.append(min_hash)
    return signature


def minhash_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    """通过 MinHash 签名估算 Jaccard 相似度（匹配位数/总位数）。"""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(1 for a, b in zip(sig_a, sig_b) if a == b)
    return matches / len(sig_a)


# ═══════════════════════════════════════════════════════════════════════
#  SimHash 指纹
# ═══════════════════════════════════════════════════════════════════════

def _feature_hash(feature: str) -> int:
    """特征 → 32 位哈希值。"""
    return int.from_bytes(
        hashlib.md5(feature.encode("utf-8")).digest()[:4], "little"
    )


def simhash_fingerprint(text: str, num_bits: int = SIMHASH_BITS) -> int:
    """
    计算 SimHash 指纹。

    对每个 shingle 特征：取其哈希值，对指纹每一位加权累加（哈希位为 1 则 +权重，为 0 则 -权重），
    最终每一位 sum>0 取 1，否则取 0。

    参数:
        text: 待指纹的文本
        num_bits: 指纹位数（默认 64）

    返回:
        ``num_bits`` 位的整数指纹
    """
    shingles = shingle(text)
    if not shingles:
        return 0
    # 每位累加器
    v = [0] * num_bits
    for s in shingles:
        h = _feature_hash(s)
        weight = 1  # 等权重
        for i in range(num_bits):
            bit = (h >> i) & 1
            v[i] += weight if bit else -weight
    # 组装指纹
    fingerprint = 0
    for i in range(num_bits):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def simhash_hamming_distance(fp_a: int, fp_b: int) -> int:
    """计算两个 SimHash 指纹的海明距离（不同位数）。"""
    xor_val = fp_a ^ fp_b
    distance = 0
    while xor_val:
        distance += xor_val & 1
        xor_val >>= 1
    return distance


# ═══════════════════════════════════════════════════════════════════════
#  去重主函数
# ═══════════════════════════════════════════════════════════════════════

def dedup(
    rows: list[dict],
    keys: list[str] | None = None,
    jaccard_threshold: float = JACCARD_THRESHOLD,
    simhash_hamming_threshold: int = SIMHASH_HAMMING_THRESHOLD,
) -> list[dict]:
    """
    对行列表进行双重哈希近似去重（非破坏式标记）。

    算法流程:
        1. 对每行提取去重键 → 计算 MinHash 签名 + SimHash 指纹
        2. 用 SimHash 海明距离做快速初筛（≤ 阈值视为候选对）
        3. 对候选对用 MinHash Jaccard 精确验证（≥ 阈值视为重复）
        4. 并查集合并重复组 → 分配 dup_group_id
        5. 组内按去重键字典序分配 dup_rank（0 = 保留首选，1+ = 重复）

    参数:
        rows: 行列表（list[dict]，每行一个字典，等价 DataFrame 逐行表示）
        keys: 去重键列名列表（默认 ["楼盘名称", "城市名称", "地址"]）
        jaccard_threshold: MinHash Jaccard 相似度阈值
        simhash_hamming_threshold: SimHash 海明距离阈值

    返回:
        带 ``dup_group_id``（重复组 ID，-1 表示无重复）和
        ``dup_rank``（组内排名，0=首选）两列的行列表（非破坏，不删行）
    """
    if keys is None:
        keys = list(DEFAULT_KEYS)
    n = len(rows)
    if n == 0:
        return []

    # 1. 预计算每行的签名和指纹
    signatures: list[list[int]] = []
    fingerprints: list[int] = []
    for row in rows:
        key_str = build_dedup_key(row, keys)
        signatures.append(minhash_signature(key_str))
        fingerprints.append(simhash_fingerprint(key_str))

    # 2. 并查集（Union-Find）
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 3. 双重哈希去重：SimHash 初筛 + MinHash 精验
    for i in range(n):
        for j in range(i + 1, n):
            # SimHash 快速初筛
            hamming = simhash_hamming_distance(fingerprints[i], fingerprints[j])
            if hamming > simhash_hamming_threshold:
                continue
            # MinHash 精确验证
            jac = minhash_jaccard(signatures[i], signatures[j])
            if jac >= jaccard_threshold:
                union(i, j)

    # 4. 分组 + 分配 dup_group_id 和 dup_rank
    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    # 为每行追加标记列（非破坏）
    result = [dict(row) for row in rows]  # 浅拷贝
    group_counter = 0
    for root, members in groups.items():
        if len(members) <= 1:
            # 无重复
            result[members[0]]["dup_group_id"] = -1
            result[members[0]]["dup_rank"] = 0
            continue
        # 有重复：分配组 ID
        gid = group_counter
        group_counter += 1
        # 组内按去重键字典序排序，rank=0 为首选保留
        sorted_members = sorted(members, key=lambda idx: build_dedup_key(rows[idx], keys))
        for rank, idx in enumerate(sorted_members):
            result[idx]["dup_group_id"] = gid
            result[idx]["dup_rank"] = rank

    return result


def dedup_report(rows: list[dict], keys: list[str] | None = None) -> dict[str, Any]:
    """
    生成去重报告摘要。

    返回:
        包含 total_rows / dup_groups / dup_rows / dedup_rate / groups_detail 的字典
    """
    if keys is None:
        keys = list(DEFAULT_KEYS)
    marked = dedup(rows, keys)
    total = len(marked)
    dup_rows = sum(1 for r in marked if r.get("dup_group_id", -1) >= 0)
    group_ids = set(r["dup_group_id"] for r in marked if r.get("dup_group_id", -1) >= 0)
    groups_detail = []
    for gid in sorted(group_ids):
        members = [(r.get("dup_rank", 0), i) for i, r in enumerate(marked) if r.get("dup_group_id") == gid]
        groups_detail.append({
            "dup_group_id": gid,
            "member_count": len(members),
            "members": [{"row_index": idx, "dup_rank": rank, "key": build_dedup_key(marked[idx], keys)}
                        for rank, idx in sorted(members)],
        })
    return {
        "total_rows": total,
        "dup_groups": len(group_ids),
        "dup_rows": dup_rows,
        "dedup_rate": round(dup_rows / total, 4) if total else 0.0,
        "keys": keys,
        "jaccard_threshold": JACCARD_THRESHOLD,
        "simhash_hamming_threshold": SIMHASH_HAMMING_THRESHOLD,
        "groups_detail": groups_detail,
    }


# ═══════════════════════════════════════════════════════════════════════
#  CSV 读写（纯标准库）
# ═══════════════════════════════════════════════════════════════════════

def read_csv_rows(csv_path: str | Path) -> list[dict]:
    """用 csv 模块读取 CSV 文件为行列表。"""
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def write_csv_rows(rows: list[dict], csv_path: str | Path) -> None:
    """将行列表写入 CSV 文件。"""
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ═══════════════════════════════════════════════════════════════════════
#  selftest
# ═══════════════════════════════════════════════════════════════════════

def _selftest() -> None:
    """内置自测：5 条测试数据（3 条重复），验证 dup_group_id 正确标记。"""
    # 测试数据：5 条，其中 3 条是近似重复（楼盘名称/地址略有差异）
    test_rows = [
        {
            "楼盘名称": "海棠湾一号",
            "城市名称": "三亚",
            "地址": "海棠区南田路16号",
            "最新价格": "35000",
        },
        {
            "楼盘名称": "海棠湾一号",       # 完全相同
            "城市名称": "三亚",
            "地址": "海棠区南田路16号",
            "最新价格": "35500",
        },
        {
            "楼盘名称": "海棠湾1号",         # 名称近似（中文数字 vs 阿拉伯数字）
            "城市名称": "三亚",
            "地址": "海棠区南田路16号",
            "最新价格": "35000",
        },
        {
            "楼盘名称": "亚龙湾翡翠谷",
            "城市名称": "三亚",
            "地址": "吉阳区亚龙湾路88号",
            "最新价格": "28000",
        },
        {
            "楼盘名称": "清水湾度假村",
            "城市名称": "三亚",
            "地址": "陵水县清水湾大道1号",
            "最新价格": "22000",
        },
    ]

    marked = dedup(test_rows)

    # 断言 1：前 3 条应被标记为同一重复组
    group_ids = [r["dup_group_id"] for r in marked]
    assert group_ids[0] >= 0, f"第 0 行应有重复组 ID，实际 {group_ids[0]}"
    assert group_ids[0] == group_ids[1] == group_ids[2], \
        f"前 3 行应同组：{group_ids[:3]}"
    print(f"  [OK] 前 3 条标记为重复组 #{group_ids[0]}")

    # 断言 2：后 2 条不应有重复组（dup_group_id == -1）
    assert group_ids[3] == -1, f"第 3 行不应有重复组：{group_ids[3]}"
    assert group_ids[4] == -1, f"第 4 行不应有重复组：{group_ids[4]}"
    print(f"  [OK] 第 3-4 行无重复（dup_group_id == -1）")

    # 断言 3：重复组内 dup_rank 从 0 开始递增
    group_0_members = [(i, r["dup_rank"]) for i, r in enumerate(marked)
                       if r.get("dup_group_id") == group_ids[0]]
    ranks = [r for _, r in group_0_members]
    assert ranks == sorted(ranks), f"dup_rank 应递增：{ranks}"
    assert ranks[0] == 0, f"组内首选 rank 应为 0：{ranks[0]}"
    print(f"  [OK] 重复组内 dup_rank 递增：{ranks}")

    # 断言 4：非破坏式——原始数据未被删除
    assert len(marked) == 5, f"行数不应变化：{len(marked)} != 5"
    print(f"  [OK] 非破坏式：行数保持 {len(marked)}")

    # 断言 5：去重报告
    report = dedup_report(test_rows)
    assert report["total_rows"] == 5
    assert report["dup_groups"] == 1
    assert report["dup_rows"] == 3
    assert 0 < report["dedup_rate"] <= 1.0
    print(f"  [OK] 去重报告：{report['dup_groups']} 组，{report['dup_rows']} 重复行，"
          f"去重率 {report['dedup_rate']:.1%}")

    # 断言 6：MinHash/SimHash 基础函数
    sig1 = minhash_signature("海棠湾一号三亚海棠区南田路16号")
    sig2 = minhash_signature("海棠湾一号三亚海棠区南田路16号")
    assert minhash_jaccard(sig1, sig2) == 1.0, "相同文本 Jaccard 应为 1.0"
    print(f"  [OK] MinHash 相同文本 Jaccard = 1.0")

    fp1 = simhash_fingerprint("海棠湾一号三亚")
    fp2 = simhash_fingerprint("海棠湾一号三亚")
    assert simhash_hamming_distance(fp1, fp2) == 0, "相同文本海明距离应为 0"
    print(f"  [OK] SimHash 相同文本海明距离 = 0")

    print("\n  === T4 去重模块 selftest 全部通过 ===")


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="T4 去重模块 — MinHash+SimHash 双重哈希近似去重（非破坏式）"
    )
    parser.add_argument("--csv", type=str, help="输入 CSV 文件路径")
    parser.add_argument("--city", type=str, default="三亚", help="城市名（用于查找默认 CSV）")
    parser.add_argument("--out", type=str, default="dedup_report.json", help="输出报告 JSON 路径")
    parser.add_argument("--keys", type=str, default=None,
                        help="去重键列名（逗号分隔，默认：楼盘名称,城市名称,地址）")
    parser.add_argument("--jaccard", type=float, default=JACCARD_THRESHOLD,
                        help=f"MinHash Jaccard 阈值（默认 {JACCARD_THRESHOLD}）")
    parser.add_argument("--hamming", type=int, default=SIMHASH_HAMMING_THRESHOLD,
                        help=f"SimHash 海明距离阈值（默认 {SIMHASH_HAMMING_THRESHOLD}）")
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

    keys = args.keys.split(",") if args.keys else None

    print(f"[T4] 读取数据：{csv_path}")
    rows = read_csv_rows(csv_path)
    print(f"[T4] 总行数：{len(rows)}")

    marked = dedup(rows, keys=keys,
                   jaccard_threshold=args.jaccard,
                   simhash_hamming_threshold=args.hamming)

    report = dedup_report(rows, keys=keys)
    report["jaccard_threshold"] = args.jaccard
    report["simhash_hamming_threshold"] = args.hamming

    # 写报告
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[T4] 去重报告已写入：{out_path}")
    print(f"[T4] 重复组：{report['dup_groups']}，重复行：{report['dup_rows']}，"
          f"去重率：{report['dedup_rate']:.1%}")


if __name__ == "__main__":
    main()
