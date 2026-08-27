"""Build deterministic DDS v4 golden ReportDocuments.

The golden suite is deliberately renderer-neutral.  Browser QA can load the
generated JSON directly and pass it to ``render_cinematic_html`` without
re-running market or GIS collection.  Source-backed samples fail closed when a
frozen source digest drifts, so a silent data change cannot rewrite the visual
baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Package import when called from ``python -m scripts...``.
    from .report_document import (
        PAGE_REQUIRED_FIELDS,
        SCHEMA_VERSION,
        TEMPLATE_ID,
        build_report_document,
        compile_page_manifest,
    )
    from .traditional_spatial_culture import build_traditional_spatial_culture
except ImportError:  # Existing DDS convention when ``scripts`` is on sys.path.
    from report_document import (
        PAGE_REQUIRED_FIELDS,
        SCHEMA_VERSION,
        TEMPLATE_ID,
        build_report_document,
        compile_page_manifest,
    )
    from traditional_spatial_culture import build_traditional_spatial_culture


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_AS_OF = "2026-07-12T00:00:00+08:00"
GOLDEN_SUITE_VERSION = "dds.golden-suite/1.0"
E15_CONTEXT_PATH = ROOT / "data" / "golden_inputs" / "e15_site_context.json"
E15_REPORT_PATH = ROOT / "Test_济南" / "E15_DDS报告_v3_精确坐标.json"
WUHAN_REPORT_PATH = (
    ROOT / "Test_武汉" / "20260707_180136_武汉_114_246618_30_656082.json"
)
WUHAN_REPORT_SHA256 = (
    "2f87c6867df6c63bbc9612b89308b5d7556a4dfaba1090c2d55db247d9c4642b"
)

GOLDEN_SAMPLE_IDS = (
    "jinan_coordinate_g0",
    "wuhan_historical",
    "e15_g3",
    "stress_500",
)

_SAMPLE_FILENAMES = {
    sample_id: f"{sample_id}.report.json" for sample_id in GOLDEN_SAMPLE_IDS
}

_STRESS_DIMENSIONS = (
    "宏观周期",
    "城市基本面",
    "土地供需",
    "住宅库存",
    "竞品价格",
    "竞品加推",
    "去化节奏",
    "客群证据",
    "产品适配",
    "设计溢价",
    "场地约束",
    "交通可达",
    "公共服务",
    "传统空间文化",
    "成本边界",
    "现金流",
    "收益敏感性",
    "风险登记",
    "补证计划",
    "决策门槛",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _canonical_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return (text + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _assert_frozen_source(path: Path, expected_sha256: str) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"golden source is missing: {_relative(path)}")
    actual = _sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(
            "golden source digest drifted: "
            f"{_relative(path)} expected={expected_sha256} actual={actual}"
        )
    return actual


def load_e15_site_context() -> dict[str, Any]:
    """Load E15 G3 inputs and verify every referenced project source."""

    context = _load_json(E15_CONTEXT_PATH)
    registry = context.get("source_registry")
    if not isinstance(registry, list) or not registry:
        raise ValueError("E15 golden context requires a non-empty source_registry")
    seen: set[str] = set()
    for source in registry:
        if not isinstance(source, dict):
            raise ValueError("E15 source_registry entries must be objects")
        source_id = str(source.get("source_id") or "")
        relative_path = str(source.get("path") or "")
        expected = str(source.get("sha256") or "")
        if not source_id or source_id in seen:
            raise ValueError(f"invalid or duplicate E15 source_id: {source_id!r}")
        if not relative_path or len(expected) != 64:
            raise ValueError(f"incomplete frozen E15 source: {source_id}")
        seen.add(source_id)
        _assert_frozen_source(ROOT / Path(relative_path), expected)
    return deepcopy(context)


def _coordinate_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": "input:jinan-coordinate-117.1668-36.6821",
        "kind": "user_coordinate_input",
        "sha256": _sha256_bytes(_canonical_bytes(dict(payload))),
    }


def _source_snapshot(
    *, source_id: str, path: Path, expected_sha256: str | None = None
) -> dict[str, Any]:
    actual = _sha256_file(path)
    if expected_sha256 is not None:
        _assert_frozen_source(path, expected_sha256)
    return {
        "source_id": source_id,
        "path": _relative(path),
        "sha256": actual,
        "size_bytes": path.stat().st_size,
    }


def _attach_golden_metadata(
    document: dict[str, Any],
    *,
    sample_id: str,
    source_snapshots: Sequence[Mapping[str, Any]],
    expectations: Mapping[str, Any],
) -> dict[str, Any]:
    document["golden"] = {
        "schema_version": GOLDEN_SUITE_VERSION,
        "sample_id": sample_id,
        "frozen_at": GOLDEN_AS_OF,
        "source_snapshots": [deepcopy(dict(item)) for item in source_snapshots],
        "expectations": deepcopy(dict(expectations)),
        "qa_entrypoint": {
            "kind": "report_document_json",
            "initial_page": (
                document.get("page_manifest") or [{}]
            )[0].get("page_id", ""),
        },
    }
    return document


def _build_jinan_coordinate_g0() -> dict[str, Any]:
    parcel = {
        "city": "济南",
        "lng": 117.1668,
        "lat": 36.6821,
        "analysis_mode": "coordinate",
    }
    input_snapshot = _coordinate_snapshot(parcel)
    culture = build_traditional_spatial_culture(parcel, {})
    report = {
        "meta": {
            "generated_at": GOLDEN_AS_OF,
            "compiled_at": GOLDEN_AS_OF,
            "profile": "golden/jinan-coordinate-g0",
        },
        "project_context": {
            "project_id": "jinan-coordinate-1171668-366821",
            "name": "济南坐标级输入",
            "stage": "coordinate_only",
        },
        "parcel": parcel,
        "traditional_spatial_culture": culture,
        "source_registry": [
            {
                "source_id": input_snapshot["source_id"],
                "name": "DDS 坐标输入",
                "kind": input_snapshot["kind"],
                "content_sha256": input_snapshot["sha256"],
                "evidence_type": "observed_fact",
                "captured_at": GOLDEN_AS_OF,
            }
        ],
    }
    document = build_report_document(report)
    return _attach_golden_metadata(
        document,
        sample_id="jinan_coordinate_g0",
        source_snapshots=[input_snapshot],
        expectations={
            "traditional_input_level": "G0",
            "traditional_status": "not_assessable",
            "minimum_pages": 1,
        },
    )


def _amenity_coverage(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = []
    amenities = snapshot.get("amenities")
    if not isinstance(amenities, Mapping):
        return coverage
    for category, value in amenities.items():
        item = value if isinstance(value, Mapping) else {}
        rows = item.get("items") if isinstance(item.get("items"), list) else []
        coverage.append(
            {
                "category": str(category),
                "label": str(item.get("label") or category),
                "status": "available" if rows else "missing",
                "item_count": len(rows),
                "source_refs": ["wuhan-historical-json"],
            }
        )
    return coverage


def _build_wuhan_historical() -> dict[str, Any]:
    snapshot = _load_json(WUHAN_REPORT_PATH)
    input_data = snapshot.get("input") if isinstance(snapshot.get("input"), dict) else {}
    source_snapshot = _source_snapshot(
        source_id="wuhan-historical-json",
        path=WUHAN_REPORT_PATH,
        expected_sha256=WUHAN_REPORT_SHA256,
    )
    parcel = {
        "city": str(input_data.get("city") or "武汉"),
        "district": str(input_data.get("district") or ""),
        "address": input_data.get("address"),
        "lng": input_data.get("lng"),
        "lat": input_data.get("lat"),
        "analysis_mode": "historical_snapshot",
    }
    culture = build_traditional_spatial_culture(parcel, {})
    market_summary = deepcopy(snapshot.get("market_summary") or {})
    market = {
        "status": "historical_snapshot",
        "as_of": (snapshot.get("meta") or {}).get("generated_at"),
        "market_summary": market_summary,
        "competitors": deepcopy(snapshot.get("nearby_competitors") or []),
        "price_band_competitors": deepcopy(
            snapshot.get("price_band_competitors") or {}
        ),
        "source_refs": ["wuhan-historical-json"],
    }
    report = {
        "meta": {
            "generated_at": (snapshot.get("meta") or {}).get("generated_at"),
            "compiled_at": GOLDEN_AS_OF,
            "source_version": (snapshot.get("meta") or {}).get("version"),
            "profile": "golden/wuhan-historical",
        },
        "project_context": {
            "project_id": "wuhan-dongxihu-114246618-30656082",
            "name": "武汉东西湖历史报告",
            "stage": "historical_snapshot",
        },
        "parcel": parcel,
        "site": {
            "status": "partial",
            "coordinates": {"lng": parcel["lng"], "lat": parcel["lat"]},
            "amenity_coverage": _amenity_coverage(snapshot),
            "evidence_gaps": [
                "历史快照中的 POI 请求失败；不得把空列表解释为周边无配套。"
            ],
            "source_refs": ["wuhan-historical-json"],
        },
        "market": market,
        "traditional_spatial_culture": culture,
        "source_registry": [
            {
                "source_id": "wuhan-historical-json",
                "name": "武汉东西湖 DDS 历史报告 JSON",
                "path": source_snapshot["path"],
                "sha256": source_snapshot["sha256"],
                "captured_at": (snapshot.get("meta") or {}).get("generated_at"),
                "evidence_type": "observed_fact",
                "rights_status": "project_input",
            }
        ],
    }
    document = build_report_document(report)
    return _attach_golden_metadata(
        document,
        sample_id="wuhan_historical",
        source_snapshots=[source_snapshot],
        expectations={
            "city": "武汉",
            "market_sample_count": 60,
            "market_synthetic_count": 0,
            "traditional_input_level": "G0",
            "poi_status": "partial",
        },
    )


def _build_e15_g3() -> dict[str, Any]:
    wrapper = _load_json(E15_REPORT_PATH)
    legacy = wrapper.get("report_json")
    if not isinstance(legacy, dict):
        raise ValueError("E15 source report is missing report_json")
    context = load_e15_site_context()
    parcel = deepcopy(legacy.get("parcel") or {})
    parcel["coordinates"] = {
        "lng": parcel.get("lng"),
        "lat": parcel.get("lat"),
        "evidence_span": "E15 DDS 精确坐标报告中的 report_json.parcel。",
        "source_refs": ["e15-dds-coordinate-report"],
    }
    culture = build_traditional_spatial_culture(parcel, context)
    culture["source_registry"] = deepcopy(context["source_registry"])
    site = {
        "status": "source_backed",
        "coordinates": {"lng": parcel.get("lng"), "lat": parcel.get("lat")},
        **{
            key: deepcopy(context[key])
            for key in (
                "site_boundary",
                "true_north",
                "roads",
                "water",
                "terrain",
                "environment",
                "masterplan",
                "building_orientations",
                "main_entrance",
            )
        },
        "source_refs": [
            source["source_id"] for source in context["source_registry"]
        ],
    }
    legacy_meta = legacy.get("meta") if isinstance(legacy.get("meta"), dict) else {}
    report = {
        "meta": {
            "generated_at": legacy_meta.get("generated_at"),
            "data_timestamp": legacy_meta.get("data_timestamp"),
            "target_year": legacy_meta.get("target_year"),
            "compiled_at": GOLDEN_AS_OF,
            "profile": "golden/e15-g3",
        },
        "project_context": {
            "project_id": "jinan-changlingshan-e15",
            "name": "济南历下长岭山 E15",
            "stage": "pre_investment_design_review",
        },
        "parcel": parcel,
        "site": site,
        "amenities": deepcopy(legacy.get("amenities") or {}),
        "market": deepcopy(legacy.get("market") or {}),
        "decision": deepcopy(legacy.get("decision") or {}),
        "decision_full": deepcopy(legacy.get("decision_full") or {}),
        "deep_analysis": deepcopy(legacy.get("deep_analysis") or {}),
        "traditional_spatial_culture": culture,
        "source_registry": deepcopy(context["source_registry"]),
    }
    document = build_report_document(report)
    source_snapshots = [
        {
            "source_id": source["source_id"],
            "path": source["path"],
            "sha256": source["sha256"],
            "size_bytes": (ROOT / Path(source["path"])).stat().st_size,
        }
        for source in context["source_registry"]
    ]
    return _attach_golden_metadata(
        document,
        sample_id="e15_g3",
        source_snapshots=source_snapshots,
        expectations={
            "traditional_input_level": "G3",
            "traditional_status": "design_review",
            "onsite_compass": "missing",
            "expert_review": "missing",
            "source_backed_required_fields": 9,
        },
    )


def build_stress_report_document(page_count: int = 500) -> dict[str, Any]:
    """Construct a legal, non-empty PageManifest of independent test claims."""

    if isinstance(page_count, bool) or not isinstance(page_count, int):
        raise TypeError("page_count must be an integer")
    if not 1 <= page_count <= 2000:
        raise ValueError("page_count must be between 1 and 2000")

    raw_pages: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    evidence_nodes: list[dict[str, Any]] = []
    for offset in range(page_count):
        number = offset + 1
        dimension = _STRESS_DIMENSIONS[offset % len(_STRESS_DIMENSIONS)]
        chapter_number = offset // 25 + 1
        page_id = f"stress-{number:04d}"
        source_id = f"stress:evidence:{number:04d}"
        evidence_id = f"stress:claim:{number:04d}"
        threshold = 5 + (number * 7) % 91
        takeaway = (
            f"压力证据 {number:03d}：{dimension}测试阈值为 {threshold}，"
            "仅用于验证独立页面、检索和深链接，不进入真实投决。"
        )
        raw_pages.append(
            {
                "page_id": page_id,
                "chapter_id": f"stress-chapter-{chapter_number:02d}",
                "layout": "analysis",
                "title": f"{dimension} · 压力页 {number:03d}",
                "takeaway": takeaway,
                "blocks": [
                    {
                        "type": "narrative",
                        "title": f"独立判断 {number:03d}",
                        "text": (
                            f"这是第 {number} 个有界渲染单元，对应证据 {evidence_id}。"
                            "内容非空、可检索、可打印，并明确标记为模型模拟压力数据。"
                        ),
                        "evidence_type": "model_simulation",
                        "source_refs": [source_id],
                        "confidence": {"score": 0.25, "level": "low"},
                    },
                    {
                        "type": "metrics",
                        "title": "运行预算",
                        "metrics": [
                            {"label": "页序", "value": number},
                            {"label": "测试阈值", "value": threshold},
                            {"label": "章节序", "value": chapter_number},
                        ],
                        "source_refs": [source_id],
                    },
                ],
                "chart_specs": [],
                "asset_refs": [],
                "source_refs": [source_id],
                "confidence": {"score": 0.25, "level": "low"},
                "evidence_type": "model_simulation",
                "load_priority": "high" if number <= 3 else "normal",
                "print_policy": {
                    "include": True,
                    "page_break_after": True,
                    "allow_internal_scroll": False,
                },
            }
        )
        sources.append(
            {
                "source_id": source_id,
                "name": f"压力测试合成证据 {number:03d}",
                "kind": "synthetic_qa_fixture",
                "evidence_type": "model_simulation",
                "rights_status": "generated_test_data",
                "claim_ref": evidence_id,
            }
        )
        evidence_nodes.append(
            {
                "evidence_id": evidence_id,
                "claim": takeaway,
                "evidence_type": "model_simulation",
                "source_refs": [source_id],
                "confidence": {"score": 0.25, "level": "low"},
                "conflicts": [],
                "needs_human_review": True,
            }
        )

    manifest = compile_page_manifest(raw_pages)
    document = {
        "schema_version": SCHEMA_VERSION,
        "template_id": TEMPLATE_ID,
        "meta": {
            "generated_at": GOLDEN_AS_OF,
            "compiled_at": GOLDEN_AS_OF,
            "contract": SCHEMA_VERSION,
            "profile": f"golden/stress-{page_count}",
        },
        "project": {
            "project_id": f"dds-renderer-stress-{page_count}",
            "name": f"DDS {page_count} 页渲染压力样本",
            "stage": "qa_only",
        },
        "macro_context": {"status": "qa_only"},
        "social_intelligence": {"status": "qa_only"},
        "persona_evidence_profiles": [],
        "synthetic_personas": {"status": "qa_only", "items": []},
        "traditional_spatial_culture": {
            "status": "qa_only",
            "input_level": "G0",
            "traditional_readings": [],
        },
        "competitor_series": {"status": "qa_only", "items": []},
        "premium_analysis": {"status": "qa_only", "items": []},
        "absorption_forecast": {"status": "qa_only", "items": []},
        "investment_case": {"status": "qa_only", "items": []},
        "modules": {},
        "evidence_graph": {"nodes": evidence_nodes, "edges": []},
        "page_manifest": manifest,
        "source_registry": sources,
        "qa": {
            "status": "ready",
            "page_count": page_count,
            "independent_claim_count": page_count,
            "synthetic_fixture": True,
        },
    }
    if page_count == 500:
        return _attach_golden_metadata(
            document,
            sample_id="stress_500",
            source_snapshots=[],
            expectations={
                "page_count": 500,
                "independent_claim_count": 500,
                "no_gap_pages": True,
            },
        )
    return document


def build_sample(sample_id: str) -> dict[str, Any]:
    """Return one in-memory ReportDocument suitable for browser QA."""

    builders = {
        "jinan_coordinate_g0": _build_jinan_coordinate_g0,
        "wuhan_historical": _build_wuhan_historical,
        "e15_g3": _build_e15_g3,
        "stress_500": build_stress_report_document,
    }
    try:
        document = builders[sample_id]()
    except KeyError as exc:
        raise ValueError(
            f"unknown golden sample {sample_id!r}; expected one of {GOLDEN_SAMPLE_IDS}"
        ) from exc
    issues = validate_golden_sample(sample_id, document)
    if issues:
        raise ValueError(f"invalid golden sample {sample_id}: {'; '.join(issues)}")
    return document


def validate_golden_sample(
    sample_id: str, document: Mapping[str, Any]
) -> list[str]:
    """Return contract violations without mutating or rendering the sample."""

    issues: list[str] = []
    golden = document.get("golden")
    if not isinstance(golden, Mapping) or golden.get("sample_id") != sample_id:
        issues.append("golden.sample_id mismatch")
    pages = document.get("page_manifest")
    if not isinstance(pages, list) or not pages:
        return issues + ["page_manifest must be a non-empty list"]
    page_ids: list[str] = []
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            issues.append(f"page {index + 1} is not an object")
            continue
        missing = [field for field in PAGE_REQUIRED_FIELDS if field not in page]
        if missing:
            issues.append(f"page {index + 1} missing fields: {','.join(missing)}")
        page_ids.append(str(page.get("page_id") or ""))
        print_policy = page.get("print_policy")
        if not isinstance(print_policy, Mapping) or print_policy.get(
            "allow_internal_scroll"
        ) is not False:
            issues.append(f"page {index + 1} permits internal scroll")
    if len(page_ids) != len(set(page_ids)):
        issues.append("page_id values must be unique")

    culture = document.get("traditional_spatial_culture")
    level = culture.get("input_level") if isinstance(culture, Mapping) else None
    expected_levels = {
        "jinan_coordinate_g0": "G0",
        "wuhan_historical": "G0",
        "e15_g3": "G3",
    }
    expected_level = expected_levels.get(sample_id)
    if expected_level is not None and level != expected_level:
        issues.append(
            f"traditional input level must be {expected_level}, got {level!r}"
        )

    if sample_id == "stress_500":
        if len(pages) != 500:
            issues.append(f"stress_500 must contain 500 pages, got {len(pages)}")
        if any(page.get("layout") == "gap" for page in pages if isinstance(page, Mapping)):
            issues.append("stress_500 cannot contain gap pages")
        takeaways = [
            str(page.get("takeaway") or "")
            for page in pages
            if isinstance(page, Mapping)
        ]
        if len(takeaways) != len(set(takeaways)):
            issues.append("stress_500 takeaways must be independent")
        nodes = (document.get("evidence_graph") or {}).get("nodes")
        if not isinstance(nodes, list) or len(nodes) != 500:
            issues.append("stress_500 requires 500 evidence nodes")
    return issues


def build_golden_samples(
    output_dir: str | Path,
    sample_ids: Sequence[str] | None = None,
) -> dict[str, Path]:
    """Write deterministic ReportDocuments plus a browser-QA suite index."""

    selected = tuple(sample_ids or GOLDEN_SAMPLE_IDS)
    unknown = [sample_id for sample_id in selected if sample_id not in GOLDEN_SAMPLE_IDS]
    if unknown:
        raise ValueError(f"unknown golden samples: {', '.join(unknown)}")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    suite_entries: list[dict[str, Any]] = []
    for sample_id in selected:
        document = build_sample(sample_id)
        filename = _SAMPLE_FILENAMES[sample_id]
        report_path = destination / filename
        report_bytes = _canonical_bytes(document, pretty=True)
        report_path.write_bytes(report_bytes)
        entry = {
            "sample_id": sample_id,
            "report_path": filename,
            "sha256": _sha256_bytes(report_bytes),
            "page_count": len(document["page_manifest"]),
            "expectations": deepcopy(document["golden"]["expectations"]),
            "qa_entrypoint": {
                "kind": "report_document_json",
                "report_path": filename,
                "initial_hash": (
                    "#page=" + str(document["page_manifest"][0]["page_id"])
                ),
            },
        }
        suite_entries.append(entry)
        written[sample_id] = report_path

    suite = {
        "schema_version": GOLDEN_SUITE_VERSION,
        "generated_at": GOLDEN_AS_OF,
        "renderer_contract": SCHEMA_VERSION,
        "samples": suite_entries,
    }
    suite_path = destination / "golden-suite.json"
    suite_path.write_bytes(_canonical_bytes(suite, pretty=True))
    written["suite"] = suite_path
    return written


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "output" / "v4_golden",
        help="Directory for ReportDocument JSON files and golden-suite.json",
    )
    parser.add_argument(
        "--samples",
        nargs="+",
        choices=GOLDEN_SAMPLE_IDS,
        default=list(GOLDEN_SAMPLE_IDS),
        help="Subset of frozen samples to generate",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    written = build_golden_samples(args.output_dir, args.samples)
    summary = {
        "status": "ok",
        "output_dir": str(args.output_dir.resolve()),
        "samples": [sample for sample in args.samples],
        "suite": str(written["suite"].resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a QA entrypoint.
    raise SystemExit(main())


__all__ = [
    "E15_CONTEXT_PATH",
    "GOLDEN_SAMPLE_IDS",
    "build_golden_samples",
    "build_sample",
    "build_stress_report_document",
    "load_e15_site_context",
    "main",
    "validate_golden_sample",
]
