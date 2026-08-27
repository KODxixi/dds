"""
ArchLib 案例索引桥接 (DDS ←→ ArchLib 视觉案例库)

ArchLib 的 `_检索系统` 逐图 VLM 打标 → `images.jsonl`;项目由图层聚合 → `projects.jsonl`。
本模块是 DDS 侧的轻量桥接:读这两份打标产出,把图层标签聚合到项目,做成可检索的
「视觉案例索引」,供设计师报告的「视觉参考板」与对标层调用。

原则:
- 只读 ArchLib 产出,绝不改写、绝不补造标签(打标是 ArchLib 侧 VLM 的事)。
- 无 Qdrant/向量依赖(MVP 桥接);打标稀疏时优雅降级,随打标完成自动变丰富。
- 项目级字段常为空 → 从图层标签按频次聚合补齐。

数据路径解析顺序:env `ARCHLIB_DATA_DIR` → `D:/ArchLib/_检索系统/data`。找不到则返回 not_found。
"""
from __future__ import annotations
import json
import os
from collections import Counter
from pathlib import Path

# 图层标签字段(聚合到项目)
IMG_TAG_FIELDS = ["arch_style", "facade_material", "color_tone", "scene_part", "view_type"]


def _data_dir(explicit: str | None = None) -> Path | None:
    for d in (
        explicit,
        os.environ.get("ARCHLIB_DATA_DIR"),
        r"D:/ArchLib/_project/_检索系统/data",
        r"D:/ArchLib/_检索系统/data",
    ):
        if d and Path(d).exists():
            return Path(d)
    return None


def _load_jsonl(p: Path) -> list[dict]:
    out = []
    if p and p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def load_index(data_dir: str | None = None) -> dict:
    """加载并聚合 ArchLib 打标产出 → {status, projects, coverage, note}。"""
    dd = _data_dir(data_dir)
    if not dd:
        return {"status": "archlib_not_found", "projects": [], "coverage": {},
                "note": "未找到 ArchLib 打标产出;设 ARCHLIB_DATA_DIR 或置于 D:/ArchLib/_检索系统/data。"}
    projects = _load_jsonl(dd / "projects.jsonl")
    images = _load_jsonl(dd / "images.jsonl")

    # 按 project 聚合图层标签(项目级字段常空,从图层补)
    by_proj: dict[str, dict] = {}
    for im in images:
        pr = im.get("project") or ""
        b = by_proj.setdefault(pr, {"tags": Counter(), "keywords": Counter(),
                                    "one_liners": [], "img_count": 0, "sample_paths": []})
        b["img_count"] += 1
        for f in IMG_TAG_FIELDS:
            v = im.get(f)
            for t in (v if isinstance(v, list) else [v]):
                if t:
                    b["tags"][t] += 1
        for k in (im.get("design_keywords") or []):
            if k:
                b["keywords"][k] += 1
        if im.get("one_liner"):
            b["one_liners"].append(im["one_liner"])
        if im.get("path") and len(b["sample_paths"]) < 6:
            b["sample_paths"].append(im["path"])

    out = []
    for p in projects:
        nm = p.get("project_name") or ""
        agg = by_proj.get(nm, {})
        tags = [t for t, _ in (agg.get("tags") or Counter()).most_common(8)] \
            or list(p.get("arch_style", [])) + list(p.get("facade_material", []))
        kws = [k for k, _ in (agg.get("keywords") or Counter()).most_common(10)] \
            or list(p.get("design_keywords", []))
        out.append({
            "project_name": nm, "building_type": p.get("building_type") or "",
            "firm": p.get("firm") or "", "region": p.get("region") or "",
            "folder_path": p.get("folder_path") or "", "cover_image": p.get("cover_image") or "",
            "image_count": p.get("image_count") or agg.get("img_count", 0),
            "tagged_image_count": agg.get("img_count", 0),
            "tags": tags, "keywords": kws,
            "one_liner": p.get("one_liner") or ((agg.get("one_liners") or [""])[0]),
            "ref_value": p.get("ref_value") or "",
            "positioning_tier": p.get("positioning_tier") or "", "scale": p.get("scale") or "",
            "sample_paths": agg.get("sample_paths", []),
        })

    tagged = sum(1 for o in out if o["tagged_image_count"] > 0)
    return {"status": "ok", "projects": out,
            "coverage": {"projects": len(out), "tagged_images": len(images),
                         "projects_with_tags": tagged},
            "note": "项目级标签由图层聚合(打标进行中,覆盖随之增长)。"}


def _bt_match(want: str, got: str) -> bool:
    if not want:
        return False
    return want in (got or "") or (got and got in want)


def search_archlib(building_type: str | None = None, style=None, material=None,
                   keywords=None, top_k: int = 6, data_dir: str | None = None) -> dict:
    """视觉参考检索:building_type 过滤(降权非剔除) + 风格/材料/关键词 重叠排序。"""
    idx = load_index(data_dir)
    if idx["status"] != "ok":
        return {"status": idx["status"], "count": 0, "items": [], "note": idx.get("note")}
    q = set()
    for v in (style, material):
        if v:
            q |= set(v if isinstance(v, list) else [v])
    for k in (keywords or []):
        if k:
            q.add(k)
    ranked = []
    for p in idx["projects"]:
        pool = set(p["tags"]) | set(p["keywords"])
        overlap = len(q & pool)
        bt = 1.0 if _bt_match(building_type or "", p["building_type"]) else 0.0
        score = overlap + 1.2 * bt + 0.1 * p["tagged_image_count"]
        if score > 0 or not q:
            ranked.append((score, p))
    ranked.sort(key=lambda t: -t[0])
    items = [p for _, p in ranked[:top_k]]
    return {"status": "ok", "count": len(items), "items": items,
            "coverage": idx["coverage"], "note": idx["note"]}


def _norm(s: str) -> str:
    import re
    return re.sub(r"[^\w一-鿿]", "", (s or "").lower())


def link_benchmarks(benchmark_lib_path: str | None = None, data_dir: str | None = None) -> dict:
    """ArchLib 项目 ↔ benchmark_library 案例 按名对齐(归一化 + 子串双向匹配)。
    打通「结构化标杆(分维/decode) + 视觉案例(图/VLM标)」两面。
    返回 {status, matches:[{archlib_project, benchmark_case, archlib_tags, archlib_keywords,
          benchmark_key_tactics, benchmark_category, cover_image}], match_count,
          archlib_total, benchmark_total, note}"""
    bp = Path(benchmark_lib_path) if benchmark_lib_path else \
        (Path(__file__).resolve().parent.parent / "data" / "benchmark_library.json")
    if not bp.exists():
        return {"status": "no_benchmark_lib", "matches": [], "note": "benchmark_library.json 不存在。"}
    idx = load_index(data_dir)
    if idx["status"] != "ok":
        return {"status": idx["status"], "matches": [], "note": idx.get("note")}
    bench = json.loads(bp.read_text(encoding="utf-8")).get("cases", [])
    bnames = [(_norm(c.get("name", "")), c) for c in bench]
    matches, used_b = [], set()
    for p in idx["projects"]:
        pn = _norm(p["project_name"])
        if len(pn) < 3:
            continue
        for bnn, c in bnames:
            if len(bnn) < 3 or c.get("name") in used_b:
                continue
            if pn in bnn or bnn in pn:  # 子串双向匹配,避免乱配
                matches.append({
                    "archlib_project": p["project_name"], "benchmark_case": c.get("name"),
                    "archlib_tags": p["tags"][:5], "archlib_keywords": p["keywords"][:5],
                    "benchmark_key_tactics": (c.get("key_tactics") or [])[:3],
                    "benchmark_category": c.get("category"),
                    "cover_image": p["cover_image"],
                })
                used_b.add(c.get("name"))
                break
    return {"status": "ok", "matches": matches, "match_count": len(matches),
            "archlib_total": len(idx["projects"]), "benchmark_total": len(bench),
            "note": "按名归一化子串匹配;ArchLib 多为酒店/概念图,与现象级住宅标杆名重叠有限,"
                    "匹配数随打标与标杆库扩充增长。"}


if __name__ == "__main__":
    import sys
    if "--link" in sys.argv:
        print(json.dumps(link_benchmarks(), ensure_ascii=False, indent=2)[:2200])
    else:
        r = search_archlib(building_type="度假酒店", style=["新中式/东方"], keywords=["大挑檐"], top_k=5)
        print(json.dumps(r, ensure_ascii=False, indent=2)[:2200])
