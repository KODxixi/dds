"""ArchLib → DDS 对标库同步。

ArchLib(D:\\ArchLib)是建筑案例库 SSOT：业态单轴归档 + 逐图 VLM 打标，其检索系统
导出 `_检索系统/data/dds_benchmark.json`（与 DDS 对标库同结构：meta/scoring_scale/
dimensions/cases 十维量化）。本脚本把该导出同步进 DDS `data/benchmark_library.json`，
供 benchmark_engine.py 消费。原样复制（先校验 JSON 可解析、cases 非空、必填字段齐），
保留 ArchLib 导出格式，避免双份漂移。

注意：旧的 `ingest_archref.py`（扫 .md 笔记表格）针对的是已废弃的 D:/Vault/20-ArchRefer
结构；新库由 ArchLib 自身打标管线产出 benchmark，本脚本即新的接入口。

用法：
  python scripts/sync_archlib_benchmark.py                       # 默认 D:/ArchLib
  python scripts/sync_archlib_benchmark.py --archlib D:/ArchLib  # 指定库根
  python scripts/sync_archlib_benchmark.py --dry-run             # 只校验+对比，不写入
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DST = ROOT / "data" / "benchmark_library.json"
DEFAULT_ARCHLIB = r"D:/ArchLib"
SRC_REL = "_检索系统/data/dds_benchmark.json"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ArchLib → DDS 对标库同步")
    ap.add_argument("--archlib", default=DEFAULT_ARCHLIB, help="ArchLib 库根，默认 D:/ArchLib")
    ap.add_argument("--dry-run", action="store_true", help="只校验+对比差异，不写入")
    a = ap.parse_args(argv)

    src = Path(a.archlib) / SRC_REL
    if not src.exists():
        print(f"[error] ArchLib 对标库导出不存在: {src}")
        print("        请确认 --archlib 路径，且 ArchLib 检索系统已生成 dds_benchmark.json。")
        return 2

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[error] 源 JSON 解析失败: {e}")
        return 2

    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        print("[error] 源缺少非空 cases 数组，拒绝同步（防止把空库覆盖进 DDS）")
        return 2

    missing = [(c.get("id") or c.get("name") or "?") for c in cases
               if not (c.get("id") and isinstance(c.get("scores"), dict))]
    meta = data.get("meta", {})
    print(f"源 : {src}")
    print(f"     案例 {len(cases)} | schema {meta.get('schema_version')} | 更新 {meta.get('last_updated')}")
    if missing:
        print(f"[warn] {len(missing)} 条缺 id/scores（仍会同步，但建议在 ArchLib 侧补全）: {missing[:5]}")

    old_n = None
    if DST.exists():
        try:
            old_n = len(json.loads(DST.read_text(encoding="utf-8")).get("cases", []))
        except Exception:
            old_n = "?"
    print(f"目标: {DST}  现有案例 {old_n} → 将变为 {len(cases)}")

    if a.dry_run:
        print("[dry-run] 未写入。")
        return 0

    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, DST)
    print(f"[ok] 已同步 {len(cases)} 个对标案例到 {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
