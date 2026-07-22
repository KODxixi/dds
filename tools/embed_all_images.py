"""批量向量化 ArchLib 居住类项目图像，并挂到对应楼盘。

用法: python tools/embed_all_images.py [--city 杭州] [--limit 5] [--dry-run]
  --city     只处理指定城市楼盘（可选）
  --limit    只处理前 N 个项目（用于测试）
  --dry-run  只显示将处理的文件，不跑 embedding

原理:
  1. 扫描 ArchLib/10_居住 下的 jpg/png
  2. 从文件名提取项目名（比如 "宁波凤起潮鸣"）
  3. 和 Vault 2026新楼盘 的楼盘名模糊匹配
  4. 匹配成功 → embedding → 挂到 project_images 表
  5. 断点续跑：已存在的项目跳过
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# DDS_v2 src 进路径
PKG_ROOT = Path(__file__).parent.parent
SRC = PKG_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 在 dds 包导入前先给 settings 注入 VSS 扩展本地路径
# 因为脚本首次运行时 vector_repo/parcel_repo 都会触发 settings 加载
from dds.config import settings as _s_mod
_original_get_settings = _s_mod.get_settings


def _patched_settings():
    s = _original_get_settings()
    if not s.vss_extension_path:
        # 临时注入 VSS 扩展路径（用户环境里的临时文件，随环境变化）
        ext_path = Path(r"c:\Users\shiguanyu\.trae-cn\work\6a5cf0de832ff6e55bdc2080\ext\vss.duckdb_extension")
        if ext_path.exists():
            s.vss_extension_path = str(ext_path)
    return s


_s_mod.get_settings = _patched_settings

from dds.data import image_repo, parcel_repo, vault_paths


# ── 文件名解析工具 ─────────────────────────────────────────────────────────────
def _extract_project_name(path: Path) -> str | None:
    """从 ArchLib 路径提取楼盘名："0-印度蓝-----------宁波凤起潮鸣---GOA" → "宁波凤起潮鸣"。"""
    parts = path.parts
    for p in parts:
        if "---" in p:
            chunks = [c.strip() for c in re.split(r"-{3,}", p) if c.strip()]
            # 通常倒数第二个是中文名，最后一个是设计院
            if len(chunks) >= 2:
                name = chunks[-2]
                # 纯中文，长度合理
                if 2 <= len(name) <= 20 and all(0x4e00 <= ord(ch) <= 0x9fff for ch in name if ch.strip()):
                    return name
            if len(chunks) >= 1:
                first = chunks[0]
                if len(first) >= 2 and all(0x4e00 <= ord(ch) <= 0x9fff for ch in first):
                    return first
    return None


def scan_images(archlib_root: str | Path, city_filter: str | None = None) -> dict[str, list[Path]]:
    """扫描 ArchLib 居住类图像。返回: {project_name: [image_paths]}"""
    root = Path(archlib_root) / "10_居住"
    if not root.exists():
        print(f"[warn] ArchLib 居住目录不存在: {root}")
        return {}
    images: dict[str, list[Path]] = {}
    scanned = 0
    for p in root.rglob("*.jpg"):
        scanned += 1
        proj = _extract_project_name(p)
        if not proj:
            continue
        if city_filter and city_filter not in p.stem and city_filter not in str(p.parent):
            continue
        images.setdefault(proj, []).append(p)
    for p in root.rglob("*.png"):
        scanned += 1
        proj = _extract_project_name(p)
        if not proj:
            continue
        if city_filter and city_filter not in p.stem and city_filter not in str(p.parent):
            continue
        images.setdefault(proj, []).append(p)
    print(f"[scan] 扫描了 {scanned} 个图像文件 → {len(images)} 个项目（有可识别名）")
    return images


def load_vault_projects() -> set[str]:
    """从 Vault CSV 加载所有楼盘名集合（去重）。"""
    cities = vault_paths.list_pool_cities()
    projects = set()
    for city in cities:
        csv = vault_paths.get_csv(city)
        if not csv:
            continue
        try:
            rows = parcel_repo.duckdb.query(
                f'SELECT DISTINCT "楼盘名称" AS n FROM read_csv_auto(\'{csv}\', header=true)'
            ).fetchall()
            for r in rows:
                if r[0]:
                    projects.add(str(r[0]).strip())
        except Exception:
            pass
    print(f"[vault] 楼盘数据集中有 {len(projects)} 个楼盘")
    return projects


def fuzzy_match(proj_name: str, vault: set[str]) -> str | None:
    """ArchLib 项目名 → Vault 楼盘名的模糊匹配。"""
    # 1) 精确子串
    for v in vault:
        if proj_name in v or v in proj_name:
            return v
    # 2) 短名匹配（比如去掉"绿城"前缀）
    short_proj = re.sub(r"^(绿城|万科|保利|华润|中海|融创|金地|龙湖)", "", proj_name)
    if len(short_proj) >= 2 and short_proj != proj_name:
        for v in vault:
            if short_proj in v or v in short_proj:
                return v
    return None


# ── 主流程 ───────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="批量向量化 ArchLib 居住类项目图像")
    p.add_argument("--city", type=str, default="", help="只处理指定城市")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 个项目（测试用）")
    p.add_argument("--dry-run", action="store_true", help="只显示文件，不跑 embedding")
    args = p.parse_args()

    s = _patched_settings()
    city_filter = args.city.strip() or None

    # 先检验 LMStudio 连通性
    test_jpg = None
    for p in Path(s.archlib_dir).rglob("*.jpg"):
        test_jpg = p
        break
    if test_jpg:
        print(f"[check] 尝试验证 LMStudio 连通性（用 {test_jpg.name} 试 embedding）")
        vecs = image_repo.embed_images([test_jpg])
        if vecs is None:
            print("[error] LMStudio 不通或 qwen3-vl-7b-instruct 模型未加载。请先启动 LMStudio 加载视觉模型。")
            sys.exit(1)
        print(f"[ok] LMStudio 连通正常，维度={len(vecs[0])}")

    # 扫描 + 匹配
    images = scan_images(s.archlib_dir, city_filter)
    if not images:
        print("没有找到符合条件的图像")
        sys.exit(0)

    vault_projects = load_vault_projects()
    matched_pairs: list[tuple[str, str, list[Path]]] = []  # archlib_proj, vault_proj, paths
    for arch_proj, paths in sorted(images.items()):
        vault_proj = fuzzy_match(arch_proj, vault_projects)
        if vault_proj:
            matched_pairs.append((arch_proj, vault_proj, paths))
            if args.limit and len(matched_pairs) >= args.limit:
                break

    print(f"[match] 与 Vault 楼盘匹配成功：{len(matched_pairs)} 个项目")
    if not matched_pairs:
        print("提示：ArchLib 里的项目名与新楼盘 CSV 里的楼盘名对不上。")
        print("例：ArchLib 项目文件夹名一般格式是『风格号---城市项目名---设计院』")
        sys.exit(0)

    # 跳过已挂的项目
    already_linked = set(image_repo.list_linked_projects())
    todo = [(a, v, p) for a, v, p in matched_pairs if v not in already_linked]
    print(f"[todo] 去掉已挂项目，剩余待处理：{len(todo)} 个")

    if args.dry_run:
        print("\n--dry-run 模式，将处理以下项目：")
        for arch, vault, paths in todo:
            print(f"  {arch} → {vault}  ({len(paths)} 张图)")
        sys.exit(0)

    # 开始批量 link
    success, failed, skipped = 0, 0, 0
    start = time.time()
    for i, (arch, vault, paths) in enumerate(todo, 1):
        elapsed = time.time() - start
        eta = elapsed / i * len(todo) - elapsed if i > 1 else 0.0
        print(f"\r[{i}/{len(todo)}] {vault} ({len(paths)} 张图) · eta={eta:.0f}s", end="", flush=True)
        if len(paths) > 12:  # 单项目限制最多 12 张（避免 embedding 太久）
            paths = paths[:12]
            skipped += len(paths) - 12
        ok = image_repo.link_project(vault, paths)
        if ok:
            success += 1
        else:
            failed += 1
    print()

    # 结果
    print(f"\n{'='*60}")
    print(f"完成: {len(matched_pairs)} 个项目")
    print(f"  成功: {success}")
    print(f"  失败: {failed}")
    print(f"  图像限制: 每个项目最多 12 张，共跳过 {skipped} 张超量图")
    print(f"  耗时: {time.time() - start:.1f} 秒")
    print(f"{'='*60}")
    print("\n当前仓储统计:")
    st = image_repo.stats()
    print(json.dumps(st, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
