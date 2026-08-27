"""archlib_vision.py — ArchLib 视觉模型接入层（Phase 1 数据底座）

读 ArchLib 检索系统的逐图 VLM 打标（images_claude.jsonl）+ 项目级（projects.jsonl），
按 `path` 把图级标签【聚合】到项目级视觉特征，提供"业态+档次 → 对标案例 + 设计证据"
检索，供决策引擎从【溢价率 / 品质感 / 舒适度 / 豪宅改善】四个角度做品质价值论证，
用来说服开发商投品质。

设计原则（对齐 DDS"禁止脑补"）：
  - 本层只【surface 真实 VLM 标签】并按透明规则归类，不发明任何溢价数字。
  - 溢价 % 仍由 premium_engine / benchmark 给出；本层提供"同档案例用了哪些设计动作"作佐证。
  - 打标进行中（现 224/约 26000 图，projects.jsonl 多为空），接口按可得数据降级，绝不崩。

纯标准库（json / pathlib / collections / re）。CLI 见 __main__。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ARCHLIB_DEFAULT = r"D:/ArchLib"
IMG_REL = "_检索系统/data/images_claude.jsonl"
PROJ_REL = "_检索系统/data/projects.jsonl"

# 业态 folder（ArchLib 新结构 XY_业态）→ (DDS 业态, 档次提示)
BUSINESS_MAP = {
    "11_公寓住宅": ("公寓住宅", "改善"),
    "12_别墅·私人住宅": ("别墅合院", "高奢"),
    "13_高层豪宅·顶豪": ("高层豪宅", "顶豪"),
    "21_城市酒店": ("城市酒店", "高端"),
    "22_旅游度假": ("度假酒店", "高奢"),
    "23_野奢庄园": ("度假酒店", "顶奢"),
    "24_民宿·精品": ("精品民宿", "改善"),
    "31_售楼中心展示区": ("示范区售楼处", "高端"),
}
CATEGORY_CN = {
    "10_居住": "居住", "20_酒店旅居": "酒店旅居", "30_营销空间": "营销空间",
    "40_商业办公": "商业办公", "50_文化公共": "文化公共", "60_城市·更新": "城市更新",
    "70_室内": "室内", "80_专项库": "专项库",
}

# —— 价值维度归类（透明规则，词表来自真实 VLM vocab）——
# 高质感主材（vs 涂料/玻璃幕墙 等普通项）
QUALITY_MATERIALS = {"石材", "实木/木格栅", "金属格栅", "陶板/陶棍",
                     "清水混凝土", "夯土", "GRC/GFRC", "编织/穿孔板"}
QUALITY_COLORS = {"暖色/米金", "大地色", "深色/黑铜"}
# 舒适度：人居场景 + 关键词词根
COMFORT_SCENES = {"景观庭院", "泳池/水景", "屋顶/退台", "中庭/大堂"}
COMFORT_STEMS = ("泳池", "水庭", "水景", "水台", "水池", "绿植", "露台", "景树",
                 "庭院", "松", "棕榈", "暖光", "挑檐", "退台")
# 溢价：差异化高端风格（标志记忆点）
PREMIUM_STYLES = {"未来主义/参数化", "Art-Deco", "自然有机", "解构", "热带现代"}
# 豪宅改善：尊贵礼序场景 + 词根
UPGRADE_SCENES = {"入口/门头", "中庭/大堂", "客房/户内"}
UPGRADE_STEMS = ("礼仪", "入口", "门头", "格栅", "软装", "挑檐", "铜", "石材", "暖光")


def _norm_path(p: str) -> list[str]:
    return (p or "").replace("/", "\\").split("\\")


def _parse(p: str) -> tuple[str, str, str]:
    """path → (顶层类目, 业态folder, 项目folder)"""
    parts = _norm_path(p)
    cat = parts[0] if parts else ""
    biz = parts[1] if len(parts) > 1 else ""
    proj = parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")
    return cat, biz, proj


def load_images(archlib: str = ARCHLIB_DEFAULT) -> list[dict]:
    root = Path(archlib)
    data_roots = [
        root / "_project" / "_检索系统" / "data",
        root / "_检索系统" / "data",
    ]
    files: list[Path] = []
    for data_root in data_roots:
        for name in ("images.jsonl", "images_claude.jsonl"):
            candidate = data_root / name
            if candidate.exists():
                files.append(candidate)
        shard_dir = data_root / "images"
        if shard_dir.exists():
            files.extend(sorted(shard_dir.glob("*.jsonl")))
    out = []
    seen_paths: set[str] = set()
    for fp in list(dict.fromkeys(files)):
        for line in fp.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            path = str(record.get("path") or "")
            if path and path in seen_paths:
                continue
            if path:
                seen_paths.add(path)
            out.append(record)
    return out


def _top(counter: Counter, k: int) -> list[str]:
    return [x for x, _ in counter.most_common(k)]


def aggregate_projects(images: list[dict]) -> list[dict]:
    """图级标签 → 项目级聚合视觉特征。"""
    by_proj: dict[str, dict] = {}
    for r in images:
        cat, biz, proj = _parse(r.get("path", ""))
        if not proj:
            continue
        key = f"{biz}/{proj}"
        d = by_proj.setdefault(key, {
            "project": proj, "category": cat, "business_folder": biz,
            "_mat": Counter(), "_color": Counter(), "_style": Counter(),
            "_scene": Counter(), "_kw": Counter(), "_lines": [], "image_count": 0,
            "cover": r.get("path", ""),
        })
        d["image_count"] += 1
        for x in (r.get("facade_material") or []):
            d["_mat"][x] += 1
        for x in (r.get("color_tone") or []):
            d["_color"][x] += 1
        for x in (r.get("arch_style") or []):
            d["_style"][x] += 1
        sc = r.get("scene_part")
        if sc:
            d["_scene"][sc] += 1
        for x in (r.get("design_keywords") or []):
            d["_kw"][x] += 1
        ol = r.get("one_liner")
        if ol and len(d["_lines"]) < 4:
            d["_lines"].append(ol)

    res = []
    for d in by_proj.values():
        dds_biz, tier = BUSINESS_MAP.get(d["business_folder"], (d["business_folder"], ""))
        res.append({
            "project": d["project"],
            "category": CATEGORY_CN.get(d["category"], d["category"]),
            "business_folder": d["business_folder"],
            "dds_business": dds_biz,
            "tier": tier,
            "image_count": d["image_count"],
            "materials": _top(d["_mat"], 6),
            "colors": _top(d["_color"], 4),
            "styles": _top(d["_style"], 4),
            "scenes": _top(d["_scene"], 6),
            "design_keywords": _top(d["_kw"], 12),
            "one_liners": d["_lines"],
            "cover": d["cover"],
        })
    res.sort(key=lambda x: -x["image_count"])
    return res


def query(business: str | None = None, tier: str | None = None,
          keywords: list[str] | None = None, top_n: int = 8,
          archlib: str = ARCHLIB_DEFAULT, projects: list[dict] | None = None) -> list[dict]:
    """按业态/档次/关键词检索对标案例（已聚合）。projects 可注入用于测试。"""
    projs = projects if projects is not None else aggregate_projects(load_images(archlib))
    kws = [k for k in (keywords or []) if k]

    def score(p: dict) -> float:
        s = 0.0
        if business and (business in p["dds_business"] or business in p["business_folder"]):
            s += 3
        if tier and tier == p["tier"]:
            s += 2
        if kws:
            blob = " ".join(p["design_keywords"] + p["materials"] + p["one_liners"])
            s += sum(1 for k in kws if k in blob)
        s += min(p["image_count"], 20) / 20.0  # 图多=证据足，轻微加权
        return s

    ranked = sorted(projs, key=score, reverse=True)
    if business or tier or kws:
        ranked = [p for p in ranked if score(p) >= 1]
    return ranked[:top_n]


def _bucket_kw(keywords: list[str], stems: tuple[str, ...]) -> list[str]:
    return [k for k in keywords if any(s in k for s in stems)]


def design_evidence(business: str | None = None, tier: str | None = None,
                    top_n: int = 6, archlib: str = ARCHLIB_DEFAULT,
                    projects: list[dict] | None = None) -> dict:
    """聚合匹配案例 → 四维价值证据（溢价率/品质感/舒适度/豪宅改善）。
    每维给出"设计动作（真实标签）+ 案例证据"，不含任何虚构溢价数字。"""
    cases = query(business, tier, top_n=top_n, archlib=archlib, projects=projects)
    if not cases:
        return {"matched": 0, "dimensions": {}, "cases": [],
                "caveat": "ArchLib 暂无匹配该业态/档次的已打标案例（打标进行中）。"}

    mat = Counter(); col = Counter(); sty = Counter(); scn = Counter(); kw = Counter()
    for c in cases:
        mat.update(c["materials"]); col.update(c["colors"]); sty.update(c["styles"])
        scn.update(c["scenes"]); kw.update(c["design_keywords"])
    allkw = list(kw)

    dims = {
        "premium": {
            "label": "溢价率（设计差异化记忆点）",
            "design_moves": [s for s in _top(sty, 4) if s in PREMIUM_STYLES] + _top(kw, 6),
            "note": "同档案例共有的差异化设计动作；具体溢价% 由 premium_engine 给出，本层只作设计佐证。",
        },
        "quality": {
            "label": "品质感（高质感主材 + 基调）",
            "design_moves": [m for m in _top(mat, 6) if m in QUALITY_MATERIALS]
                            + [c for c in _top(col, 4) if c in QUALITY_COLORS]
                            + _bucket_kw(allkw, ("石材", "木", "铜", "格栅", "陶", "夯土", "混凝土")),
            "note": "可由设计端直接兑现的品质投入。",
        },
        "comfort": {
            "label": "舒适度（人居场景）",
            "design_moves": [s for s in _top(scn, 6) if s in COMFORT_SCENES]
                            + _bucket_kw(allkw, COMFORT_STEMS),
            "note": "对标案例验证有效的人居舒适场景。",
        },
        "upgrade": {
            "label": "豪宅改善（尊贵礼序）",
            "design_moves": [s for s in _top(scn, 6) if s in UPGRADE_SCENES]
                            + _bucket_kw(allkw, UPGRADE_STEMS),
            "note": "面向改善/高奢客群的价值点。",
        },
    }
    # 去重保序
    for v in dims.values():
        seen = set(); v["design_moves"] = [x for x in v["design_moves"]
                                            if not (x in seen or seen.add(x))][:8]

    return {
        "matched": len(cases),
        "query": {"business": business, "tier": tier},
        "dimensions": dims,
        "cases": [{"project": c["project"], "tier": c["tier"],
                   "dds_business": c["dds_business"], "image_count": c["image_count"],
                   "one_liner": (c["one_liners"][0] if c["one_liners"] else ""),
                   "materials": c["materials"][:3], "cover": c["cover"]} for c in cases],
        "caveat": "证据来自 ArchLib 逐图 VLM 打标（进行中，覆盖随打标增长）；溢价数值不在此层生成。",
    }


def _cli(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ArchLib 视觉模型接入层")
    ap.add_argument("cmd", choices=["stats", "list", "query", "evidence"], help="子命令")
    ap.add_argument("--archlib", default=ARCHLIB_DEFAULT)
    ap.add_argument("--business", default=None)
    ap.add_argument("--tier", default=None)
    ap.add_argument("--kw", default=None, help="逗号分隔关键词")
    ap.add_argument("--n", type=int, default=8)
    a = ap.parse_args(argv)

    projs = aggregate_projects(load_images(a.archlib))
    if a.cmd == "stats":
        print(f"已聚合项目 {len(projs)} 个 | 总图 {sum(p['image_count'] for p in projs)}")
        from collections import Counter as C
        print("业态分布:", dict(C(p["dds_business"] for p in projs)))
        print("档次分布:", dict(C(p["tier"] for p in projs)))
    elif a.cmd == "list":
        for p in projs[:a.n]:
            print(f"[{p['tier'] or '—'}] {p['dds_business']} · {p['project']}  "
                  f"({p['image_count']}图) 主材={p['materials'][:3]}")
    elif a.cmd == "query":
        kws = a.kw.split(",") if a.kw else None
        for p in query(a.business, a.tier, kws, a.n, a.archlib, projs):
            print(f"[{p['tier'] or '—'}] {p['dds_business']} · {p['project']}  "
                  f"关键词={p['design_keywords'][:5]}")
    elif a.cmd == "evidence":
        ev = design_evidence(a.business, a.tier, a.n, a.archlib, projs)
        print(f"匹配 {ev['matched']} 案例 | {ev['caveat']}\n")
        for k, v in ev["dimensions"].items():
            print(f"== {v['label']} ==")
            print("  设计动作:", "、".join(v["design_moves"]) or "（暂无）")
            print("  ", v["note"])
        print("\n证据案例:")
        for c in ev["cases"]:
            print(f"  · {c['project']} [{c['tier']}] {c['one_liner']}")


if __name__ == "__main__":
    _cli()
