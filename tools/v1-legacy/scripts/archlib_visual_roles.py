"""ArchLib visual role adapter for DDS Architecture Director.

Reads ArchLib image-level JSONL exports and assigns DDS-specific image_role
values so case retrieval can target intention images, evidence photos,
masterplans, unit plans, facade details, luxury references, and sales-center
scenes while excluding PDF thumbnails and non-case documents.

This module is read-only against ArchLib. It writes optional DDS-side indexes
under data_out/archlib_visual_roles.*.
"""
from __future__ import annotations

import json
import os
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHLIB_DATA = Path(os.environ.get("ARCHLIB_DATA_DIR") or r"D:\ArchLib\_project\_检索系统\data")
DEFAULT_OUT_JSONL = ROOT / "data_out" / "archlib_visual_roles.jsonl"
DEFAULT_OUT_SUMMARY = ROOT / "data_out" / "archlib_visual_roles_summary.json"

ROLE_ORDER = [
    "intention_image",
    "evidence_image",
    "masterplan",
    "unit_plan",
    "facade_detail",
    "luxury_aesthetic",
    "sales_center",
    "non_case_document",
]

NON_CASE_STEMS = ["_thumb_pdf", "公文", "封面", "品牌标识", "平面设计", "文字排版", "行政", "通知", "目录"]
MASTERPLAN_STEMS = ["总图", "总平", "总平面", "强排", "鸟瞰", "分析图", "规划", "流线", "剖面", "平立剖"]
UNIT_PLAN_STEMS = ["户型", "平面户型", "标准层", "套型", "LDK", "房型", "客厅", "卧室", "主卧", "玄关"]
FACADE_STEMS = ["立面", "外立面", "幕墙", "石材", "铝板", "金属", "格栅", "陶板", "玻璃", "细部", "节点", "材质"]
LUXURY_STEMS = ["豪宅", "顶豪", "高奢", "礼序", "私密", "会所", "大堂", "酒店式", "圈层", "石材", "铜", "水院", "归家"]
SALES_STEMS = ["售楼", "示范区", "展示区", "样板房", "案场", "会所", "销售中心", "沙盘", "接待", "水吧"]
INTENTION_STEMS = ["概念", "意象", "效果图", "渲染", "氛围", "鸟瞰", "透视", "方案", "概念意象"]


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x not in (None, "")]
    if value in (None, ""):
        return []
    return [str(value)]


def _blob(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "path", "project", "project_name", "view_type", "scene_part", "one_liner",
        "building_type", "folder_path", "image_type"
    ):
        parts.extend(_as_list(record.get(key)))
    for key in ("arch_style", "facade_material", "color_tone", "spatial_org", "room_type", "material_detail", "design_keywords"):
        parts.extend(_as_list(record.get(key)))
    return " ".join(parts)


def _hit(text: str, stems: Iterable[str]) -> int:
    return sum(1 for stem in stems if stem and stem in text)


def classify_visual_role(record: dict[str, Any]) -> dict[str, Any]:
    text = _blob(record)
    path = str(record.get("path") or "")
    project = str(record.get("project") or record.get("project_name") or "")
    scene = str(record.get("scene_part") or "")
    view = str(record.get("view_type") or "")

    scores = {role: 0 for role in ROLE_ORDER}
    reasons: list[str] = []

    if _hit(text, NON_CASE_STEMS) or "_thumb_pdf" in path or project == "_thumb_pdf":
        scores["non_case_document"] += 100
        reasons.append("pdf_thumb_or_document")

    sales = _hit(text, SALES_STEMS)
    if sales:
        scores["sales_center"] += 25 + 8 * sales
        reasons.append("sales_center_keywords")

    facade = _hit(text, FACADE_STEMS) + len(_as_list(record.get("facade_material"))) + len(_as_list(record.get("material_detail")))
    if facade:
        scores["facade_detail"] += 20 + 5 * facade
        reasons.append("facade_or_material_evidence")

    master = _hit(text, MASTERPLAN_STEMS)
    if view in ("平立剖", "总平面", "分析图"):
        master += 2
    if scene == "平面图纸":
        master += 1
    if master:
        scores["masterplan"] += 18 + 6 * master
        reasons.append("masterplan_or_analysis_drawing")

    unit = _hit(text, UNIT_PLAN_STEMS) + len(_as_list(record.get("room_type")))
    if unit:
        scores["unit_plan"] += 18 + 7 * unit
        reasons.append("unit_plan_or_room_tags")

    luxury = _hit(text, LUXURY_STEMS)
    if "10_居住" in path and luxury:
        luxury += 1
    if luxury:
        scores["luxury_aesthetic"] += 18 + 6 * luxury
        reasons.append("luxury_aesthetic_mechanism")

    intention = _hit(text, INTENTION_STEMS)
    if view in ("概念意象", "效果图", "人视透视", "鸟瞰"):
        intention += 1
    if intention:
        scores["intention_image"] += 12 + 6 * intention
        reasons.append("intention_or_render_reference")

    if not reasons:
        scores["evidence_image"] += 20
        reasons.append("default_real_case_evidence")
    elif scores["non_case_document"] < 100:
        scores["evidence_image"] += 8

    image_role = max(scores.items(), key=lambda kv: kv[1])[0]
    if image_role == "non_case_document":
        quality = 0.1
    else:
        quality = 0.45
        quality += min(0.25, 0.04 * len(_as_list(record.get("design_keywords"))))
        quality += 0.1 if record.get("one_liner") else 0
        quality += 0.08 if record.get("facade_material") else 0
        quality += 0.07 if record.get("view_type") else 0
        quality = min(1.0, quality)

    return {
        "image_role": image_role,
        "role_scores": scores,
        "role_reasons": reasons,
        "visual_quality_score": round(quality, 2),
        "exclude_by_default": image_role == "non_case_document" or quality < 0.35,
    }


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
            if limit and len(rows) >= limit:
                break
    return rows


def _source_files(data_dir: Path) -> list[Path]:
    candidates = [data_dir / "images.jsonl", data_dir / "images_claude.jsonl"]
    image_dir = data_dir / "images"
    if image_dir.exists():
        candidates.extend(sorted(image_dir.glob("*.jsonl")))
    return [p for p in candidates if p.exists()]


def iter_archlib_images(data_dir: str | Path | None = None, limit: int | None = None) -> Iterable[dict[str, Any]]:
    base = Path(data_dir) if data_dir else DEFAULT_ARCHLIB_DATA
    seen = 0
    for file in _source_files(base):
        rows = _read_jsonl(file, None if limit is None else max(0, limit - seen))
        for row in rows:
            row["_source_jsonl"] = str(file)
            yield row
            seen += 1
            if limit and seen >= limit:
                return


def build_visual_role_index(data_dir: str | Path | None = None, limit: int | None = None) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    for idx, record in enumerate(iter_archlib_images(data_dir, limit=limit), 1):
        role = classify_visual_role(record)
        normalized_path = str(record.get("path") or "").replace("\\", "/").strip().lower()
        stable_digest = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()[:16]
        asset = {
            "asset_id": "va_" + stable_digest,
            "project": record.get("project") or record.get("project_name") or "",
            "path": record.get("path") or "",
            "source_jsonl": record.get("_source_jsonl") or "",
            "view_type": record.get("view_type") or "",
            "scene_part": record.get("scene_part") or "",
            "arch_style": _as_list(record.get("arch_style")),
            "facade_material": _as_list(record.get("facade_material")),
            "color_tone": _as_list(record.get("color_tone")),
            "design_keywords": _as_list(record.get("design_keywords"))[:16],
            "one_liner": record.get("one_liner") or "",
            **role,
        }
        assets.append(asset)
    role_counts = Counter(a["image_role"] for a in assets)
    usable = [a for a in assets if not a["exclude_by_default"]]
    return {
        "status": "ok" if assets else "empty",
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "data_dir": str(Path(data_dir) if data_dir else DEFAULT_ARCHLIB_DATA),
        "asset_count": len(assets),
        "usable_count": len(usable),
        "role_counts": dict(role_counts),
        "assets": assets,
    }


def write_visual_role_index(data_dir: str | Path | None = None, limit: int | None = None,
                            out_jsonl: Path = DEFAULT_OUT_JSONL,
                            out_summary: Path = DEFAULT_OUT_SUMMARY) -> dict[str, Any]:
    index = build_visual_role_index(data_dir, limit=limit)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as f:
        for asset in index["assets"]:
            f.write(json.dumps(asset, ensure_ascii=False) + "\n")
    summary = {k: v for k, v in index.items() if k != "assets"}
    summary["jsonl_path"] = str(out_jsonl)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def load_visual_role_index(path: Path = DEFAULT_OUT_JSONL, limit: int | None = None) -> list[dict[str, Any]]:
    if path.exists():
        return _read_jsonl(path, limit=limit)
    return build_visual_role_index(limit=limit).get("assets", [])


def search_visual_assets(intent: str = "", strategy_tags: list[str] | None = None,
                         must_have_roles: list[str] | None = None, top_k: int = 8,
                         index_path: Path = DEFAULT_OUT_JSONL) -> dict[str, Any]:
    assets = load_visual_role_index(index_path)
    tags = [str(x) for x in (strategy_tags or []) if x]
    roles = set(must_have_roles or [])
    intent_text = intent or ""
    ranked = []
    for asset in assets:
        if asset.get("exclude_by_default"):
            continue
        role = asset.get("image_role")
        if roles and role not in roles:
            continue
        text = " ".join([
            asset.get("project", ""), asset.get("path", ""), asset.get("view_type", ""),
            asset.get("scene_part", ""), asset.get("one_liner", ""),
            " ".join(_as_list(asset.get("design_keywords"))),
            " ".join(_as_list(asset.get("facade_material"))),
        ])
        score = float(asset.get("visual_quality_score") or 0)
        score += 0.4 if role in roles else 0
        score += 0.18 * _hit(text, tags)
        score += 0.12 * _hit(text, [intent_text]) if intent_text else 0
        ranked.append((score, asset))
    ranked.sort(key=lambda x: x[0], reverse=True)
    items = []
    for score, asset in ranked[:top_k]:
        item = dict(asset)
        item["asset_score"] = round(score, 3)
        items.append(item)
    return {
        "status": "ok",
        "intent": intent,
        "strategy_tags": tags,
        "must_have_roles": list(roles),
        "count": len(items),
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Build/search DDS ArchLib visual role index")
    parser.add_argument("cmd", choices=["build", "search", "stats"])
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--roles", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--intent", default="")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args(argv)
    if args.cmd == "build":
        print(json.dumps(write_visual_role_index(args.data_dir, args.limit), ensure_ascii=False, indent=2))
    elif args.cmd == "stats":
        summary = write_visual_role_index(args.data_dir, args.limit)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        roles = [x.strip() for x in args.roles.split(",") if x.strip()]
        tags = [x.strip() for x in args.tags.split(",") if x.strip()]
        print(json.dumps(search_visual_assets(args.intent, tags, roles, args.top_k), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
