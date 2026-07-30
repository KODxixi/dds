"""Migrate a V1 decision report into a self-contained V2 project package."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from dds.analysis_profile import resolve_intervention_profile
from dds.reporting import (
    build_frozen_package,
    compile_frozen_package,
    decision_pages,
    render_frozen_package,
)

from .intake import ProjectManifest, SourceAsset, V1ProjectImporter


_SOURCE_DIRECTORIES = ("inbox", "前期资料")
_SUPPORTED_DECISION_CHARTS = {
    "cashflow_curve",
    "combo_bar_line",
    "diverging_bar",
    "heatmap",
    "ranked_bar",
    "scatter_bubble",
    "stacked_bar",
    "time_series",
    "tornado",
    "waterfall",
}
_CHAPTER_PREVIEW_MARKER = """
<style>
  [data-dds-artifact="chapter-preview"] {
    position: fixed;
    right: 20px;
    bottom: 18px;
    z-index: 2147483647;
    padding: 8px 12px;
    border: 1px solid rgba(255, 255, 255, 0.24);
    border-radius: 999px;
    background: rgba(15, 18, 22, 0.58);
    box-shadow: 0 10px 32px rgba(0, 0, 0, 0.24);
    color: rgba(255, 255, 255, 0.92);
    font: 600 12px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    letter-spacing: 0.04em;
    backdrop-filter: blur(18px) saturate(140%);
    -webkit-backdrop-filter: blur(18px) saturate(140%);
  }
@media print {
  [data-dds-artifact="chapter-preview"] {
    display: none !important;
  }
  .print-page {
    position: relative;
  }
  .print-page::after {
    content: "章节工作稿 · 非正式交付";
    position: absolute;
    right: 14px;
    bottom: 10px;
    z-index: 2147483647;
    padding: 4px 8px;
    border: 1px solid rgba(255, 255, 255, 0.22);
    border-radius: 999px;
    background: rgba(15, 18, 22, 0.42);
    color: rgba(255, 255, 255, 0.74);
    font: 600 9px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    letter-spacing: 0.04em;
  }
}
</style>
<div
  data-dds-artifact="chapter-preview"
  role="status"
  aria-label="章节工作稿，非正式交付"
>章节工作稿 · 非正式交付</div>
""".strip()


def discover_v1_source_materials(source_root: Path) -> tuple[Path, ...]:
    """Select original inputs while excluding V1 work and delivery products."""

    root = source_root.resolve()
    selected = [
        path
        for path in root.iterdir()
        if path.is_file()
    ]
    for directory_name in _SOURCE_DIRECTORIES:
        directory = root / directory_name
        if directory.is_dir():
            selected.extend(path for path in directory.rglob("*") if path.is_file())
    return tuple(sorted(selected))


def _render_chapter_preview(frozen_package: Mapping[str, Any]) -> str:
    rendered = render_frozen_package(frozen_package)
    return rendered.replace(
        "</body>",
        f"{_CHAPTER_PREVIEW_MARKER}\n</body>",
        1,
    )


def _source_admission(source: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    source_type = str(source.get("source_type") or "")
    if source_type in {
        "official_web",
        "government_web",
        "official_planning_document",
    }:
        return (
            "qualified",
            [
                "statutory_or_geographic_boundary",
                "public_project_status",
                "public_planning_scale",
                "future_demand_event",
            ],
            [
                "Only the cited geography, version, status date, and published "
                "scale are supported; residential conversion remains an inference."
            ],
        )
    if source_type == "corporate_official":
        return (
            "qualified",
            [
                "company_project_status",
                "company_operational_statement",
                "future_demand_event",
            ],
            [
                "A company statement cannot independently establish residential "
                "demand, purchase power, or conversion."
            ],
        )
    if source_type == "internal_case_library":
        return (
            "qualified",
            ["design_mechanism", "spatial_translation"],
            ["Case analogies do not support price, absorption, or financial claims."],
        )
    if source_type == "news_media":
        return (
            "needs_review",
            ["market_clue", "cross_validation"],
            ["Media evidence cannot independently establish price or absorption."],
        )
    return (
        "needs_review",
        ["project_goal", "scheme_state", "project_constraint"],
        ["Client-provided material does not independently establish market facts."],
    )


def _supplemental_registry(
    sources: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    registry: list[dict[str, Any]] = []
    snapshots: dict[str, dict[str, Any]] = {}
    for raw in sources:
        item = deepcopy(dict(raw))
        source_id = str(item.get("source_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", source_id):
            raise ValueError("supplemental source_id must be a portable identifier")
        canonical_url = str(
            item.get("canonical_url")
            or item.get("url")
            or item.get("canonical_ref")
            or ""
        ).strip()
        if not canonical_url.startswith(("https://", "http://")):
            raise ValueError(
                f"supplemental source {source_id} requires an http(s) URL"
            )
        excerpt = str(
            item.get("snapshot_excerpt")
            or item.get("memo_excerpt")
            or ""
        ).strip()
        if not excerpt:
            raise ValueError(
                f"supplemental source {source_id} requires a frozen excerpt"
            )
        snapshot = {
            "source_id": source_id,
            "title": str(item.get("title") or item.get("name") or source_id),
            "publisher": str(item.get("publisher") or ""),
            "canonical_url": canonical_url,
            "published_at": str(item.get("published_at") or ""),
            "captured_at": str(item.get("captured_at") or ""),
            "snapshot_excerpt": excerpt,
        }
        snapshot_hash = sha256(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        status, allowed_uses, policy_limitations = _source_admission(item)
        item.update(
            {
                "source_id": source_id,
                "name": snapshot["title"],
                "canonical_url": canonical_url,
                "canonical_ref": canonical_url,
                "snapshot_ref": f"web-source-snapshots/{source_id}.json",
                "snapshot_hash": snapshot_hash,
                "sha256": snapshot_hash,
                "memo_excerpt": excerpt,
                "status": status,
                "qualification_status": status,
                "allowed_uses": allowed_uses,
                "limitations": list(item.get("limitations") or [])
                + policy_limitations,
            }
        )
        item.pop("snapshot_excerpt", None)
        snapshot["snapshot_hash"] = snapshot_hash
        registry.append(item)
        snapshots[source_id] = snapshot
    return registry, snapshots


def _asset_registry_item(asset: SourceAsset) -> dict[str, Any]:
    if asset.extraction_status == "unsupported":
        status = "rejected"
        qualification_reasons = ["unsupported_material_format"]
    else:
        status = "needs_review"
        qualification_reasons = (
            ["evidence_admission_pending"]
            if asset.extraction_status == "extracted"
            else ["extraction_or_human_review_pending"]
        )
    return {
        "source_id": asset.source_id,
        "name": asset.original_name,
        "source_type": "client_provided_material",
        "trust_tier": "project_input",
        "status": status,
        "qualification_status": status,
        "qualification_reasons": qualification_reasons,
        "canonical_ref": f"dds:project-asset/{asset.source_id}",
        "sha256": asset.sha256,
        "snapshot_ref": asset.snapshot_ref,
        "relative_source_path": asset.relative_source_path,
        "rights_status": asset.rights_status,
        "extraction_status": asset.extraction_status,
        "allowed_uses": [],
        "limitations": list(asset.limitations)
        + [
            "Client-provided material does not independently establish market facts.",
            "Project identity and geography are unverified; the asset cannot "
            "support decision fields until evidence admission is completed.",
        ],
    }


def _legacy_registry(
    batch: Mapping[str, Any],
    manifest: ProjectManifest,
) -> list[dict[str, Any]]:
    assets_by_path = {
        asset.relative_source_path: asset for asset in manifest.source_assets
    }
    registry: list[dict[str, Any]] = []
    used_asset_ids: set[str] = set()
    for raw in batch.get("sources") or []:
        if not isinstance(raw, Mapping):
            continue
        item = deepcopy(dict(raw))
        item["name"] = str(
            item.get("name") or item.get("title") or item["source_id"]
        )
        status, allowed_uses, policy_limitations = _source_admission(item)
        relative_path = str(
            item.get("relative_path") or item.get("input_ref") or ""
        ).replace("\\", "/")
        asset = assets_by_path.get(relative_path)
        if asset is not None:
            used_asset_ids.add(asset.source_id)
            item["snapshot_ref"] = asset.snapshot_ref
            item["sha256"] = asset.sha256
        item["status"] = status
        item["qualification_status"] = status
        item["allowed_uses"] = allowed_uses
        item["limitations"] = list(item.get("limitations") or []) + policy_limitations
        registry.append(item)
    registry.extend(
        _asset_registry_item(asset)
        for asset in manifest.source_assets
        if asset.source_id not in used_asset_ids
    )
    return registry


def _page_confidence(
    source_refs: Sequence[str],
    registry_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    scores: list[float] = []
    for source_ref in source_refs:
        source = registry_by_id.get(str(source_ref))
        if source is None:
            continue
        status = str(
            source.get("qualification_status") or source.get("status") or ""
        )
        source_type = str(source.get("source_type") or "")
        if status == "qualified" and source_type == "official_web":
            scores.append(0.85)
        elif status == "qualified":
            scores.append(0.65)
        elif status == "needs_review":
            scores.append(0.40)
        elif status == "conflict":
            scores.append(0.20)
        else:
            scores.append(0.05)
    return {
        "score": round(sum(scores) / len(scores), 2) if scores else 0.20,
        "basis": "source_admission_policy",
    }


def _decision_impact(section_id: str) -> str:
    impacts = {
        "SC1": "确认本轮以景观资源向货值转化为核心命题，并补证高地价下的成立条件。",
        "SC2": "据此锁定价格与流速验证任务，不把媒体线索直接写成售价承诺。",
        "SC3": "将限高、屏风、铁路与连廊作为所有方案的统一硬边界。",
        "AD1": "淘汰无法同口径比较的方案，并保留景观、容量和实施指标。",
        "AD2": "以 C+ 为主推路线；海景户占比实测不足时切换至 A。",
        "AD3": "按总价承受和景观分级复核面积段、户配与货量。",
        "AD4": "把第六立面、架空会所和示范区转为可校核的设计任务。",
        "AD5": "文化叙事仅服务表达，不替代市场和法定证据。",
        "VA1": "优先投入可测量的景观捕获动作，再评估会所与花园增强项。",
        "VA2": "在真实货量、成本与网签数据接入前，不形成现金流或去化承诺。",
        "VA3": "为每项刚性风险指定责任人、触发条件和回退方案。",
        "CS": "仅在允许用途内引用结论，并按来源状态安排复核与补证。",
    }
    return impacts.get(section_id, "将本页结论转为下一步决策或验证动作。")


def _decision_question(section_id: str) -> str:
    questions = {
        "SC1": "高地价项目最需要回答的核心价值命题是什么？",
        "SC2": "真实价格带、流速与塔尖客群是否支持当前方向？",
        "SC3": "哪些法定与工程边界会直接淘汰方案？",
        "AD1": "三套总图在统一约束下各自兑现了什么、牺牲了什么？",
        "AD2": "为什么主推 C+，什么条件下必须切换？",
        "AD3": "面积段、户配和货量能否匹配目标客群与总价？",
        "AD4": "景观捕获、会所与第六立面如何落实为设计动作？",
        "AD5": "文化表达如何服务产品，又不冒充市场事实？",
        "VA1": "设计投入通过什么路径转化为可验证价值？",
        "VA2": "现有证据能否支持去化、货值和现金流判断？",
        "VA3": "哪些风险会触发切换、暂停或回退？",
        "CS": "本报告的来源、方法和禁止用途是什么？",
    }
    return questions.get(section_id, "本页需要支持哪个具体决定？")


@dataclass(frozen=True, slots=True)
class LegacyMigrationResult:
    manifest: ProjectManifest
    decision_seed: dict[str, Any]
    frozen_package: dict[str, Any]
    report_document: dict[str, Any]
    evidence_workbook: dict[str, Any]
    output_root: Path


class LegacyProjectReportMigrator:
    """Produce separate decision and evidence outputs from a V1 project."""

    def migrate(
        self,
        source_root: Path,
        target_root: Path,
        *,
        project_id: str,
        project_name: str,
        imported_at: str,
        as_of: str,
        selected_mode: int,
        intervention_brief: Mapping[str, Any],
        input_profile: Mapping[str, Any],
        legacy_seed_path: Path,
        evidence_batch_path: Path,
        include_paths: Sequence[Path] | None = None,
        page_manifest_override: Sequence[Mapping[str, Any]] | None = None,
        supplemental_sources: Sequence[Mapping[str, Any]] | None = None,
        supplemental_claims: Sequence[Mapping[str, Any]] | None = None,
    ) -> LegacyMigrationResult:
        if not bool(intervention_brief.get("confirmed_at")):
            raise ValueError("confirmed intervention brief is required")
        output = target_root.resolve()
        legacy_report_path = output / "report.html"
        archived_report_path = output / "invalid-legacy-report.html"
        if legacy_report_path.exists() and archived_report_path.exists():
            raise FileExistsError(
                "cannot archive report.html because "
                "invalid-legacy-report.html already exists"
            )
        manifest = V1ProjectImporter().import_project(
            source_root,
            output / "source_snapshot",
            project_id=project_id,
            project_name=project_name,
            imported_at=imported_at,
            include_paths=(
                include_paths
                if include_paths is not None
                else discover_v1_source_materials(source_root)
            ),
        )
        profile = resolve_intervention_profile(
            input_profile,
            selected_mode=selected_mode,
            confirmed=True,
        )
        if profile["mode_status"] != "confirmed":
            raise ValueError(
                "selected intervention mode is blocked: "
                + ", ".join(profile["missing_inputs"])
            )
        seed = json.loads(legacy_seed_path.read_text(encoding="utf-8"))
        batch = json.loads(evidence_batch_path.read_text(encoding="utf-8"))
        source_registry = _legacy_registry(batch, manifest)
        supplemental_registry, supplemental_snapshots = _supplemental_registry(
            supplemental_sources or ()
        )
        existing_source_ids = {
            str(item.get("source_id") or "") for item in source_registry
        }
        duplicate_source_ids = existing_source_ids & set(supplemental_snapshots)
        if duplicate_source_ids:
            raise ValueError(
                "supplemental source ids already exist: "
                + ", ".join(sorted(duplicate_source_ids))
            )
        source_registry.extend(supplemental_registry)
        registry_by_id = {
            str(item["source_id"]): item for item in source_registry
        }
        supplemental_claim_items = [
            deepcopy(dict(item)) for item in supplemental_claims or ()
        ]
        unknown_claim_sources = {
            str(source_ref)
            for claim in supplemental_claim_items
            for source_ref in claim.get("source_refs") or ()
            if str(source_ref) not in registry_by_id
        }
        if unknown_claim_sources:
            raise ValueError(
                "supplemental claims reference unknown sources: "
                + ", ".join(sorted(unknown_claim_sources))
            )
        pages: list[dict[str, Any]] = []
        source_pages = (
            page_manifest_override
            if page_manifest_override is not None
            else seed.get("page_manifest") or []
        )
        for raw in source_pages:
            page = deepcopy(dict(raw))
            section_id = str(page.get("section_id") or "")
            page["chapter_id"] = section_id.lower()
            page.setdefault("decision_question", _decision_question(section_id))
            page.setdefault("decision_impact", _decision_impact(section_id))
            page["confidence"] = _page_confidence(
                page.get("source_refs") or [],
                registry_by_id,
            )
            page["chart_specs"] = [
                chart
                for chart in page.get("chart_specs") or []
                if str(chart.get("type") or "").lower()
                in _SUPPORTED_DECISION_CHARTS
            ]
            pages.append(page)
        pages = decision_pages(pages)
        if not pages:
            raise ValueError("legacy report has no pages passing the value gate")
        included = list(
            dict.fromkeys(str(page["section_id"]) for page in pages)
        )
        brief = deepcopy(dict(intervention_brief))
        brief["selected_mode"] = selected_mode
        decision_seed = deepcopy(dict(seed))
        decision_seed["meta"] = {
            **dict(decision_seed.get("meta") or {}),
            "as_of": as_of,
            "report_edition": "decision_report",
            "analysis_profile": profile,
            "intervention_brief": brief,
            "migration_manifest_hash": manifest.source_root_hash,
        }
        decision_seed["project"] = {
            **dict(decision_seed.get("project") or {}),
            "project_id": project_id,
            "name": project_name,
        }
        decision_seed["project_panorama"] = {
            "required_units": list(profile["required_units"]),
            "included_units": included,
            "intervention_required_units": profile["required_units"],
            "unit_policy": profile["unit_policy"],
        }
        decision_seed["page_manifest"] = pages
        decision_seed["source_registry"] = source_registry
        decision_seed["claims"] = list(decision_seed.get("claims") or []) + (
            supplemental_claim_items
        )
        decision_seed["evidence_gaps"] = []
        frozen_package = build_frozen_package(
            decision_seed,
            project_id=project_id,
            as_of=as_of,
        )
        report_document = compile_frozen_package(frozen_package)
        evidence_workbook = {
            "schema_version": "dds.evidence-workbook/2.0",
            "project_id": project_id,
            "as_of": as_of,
            "report_edition": "evidence_workbook",
            "intervention_brief": brief,
            "project_manifest": manifest.to_dict(),
            "source_registry": decision_seed["source_registry"],
            "claim_candidates": list(batch.get("candidates") or [])
            + supplemental_claim_items,
            "conflicts": [
                item
                for item in batch.get("candidates") or []
                if item.get("status") == "conflict"
            ],
            "missing_claim_ids": list(batch.get("missing_claim_ids") or []),
            "freeze": {
                "source_root_hash": manifest.source_root_hash,
                "package_hash": frozen_package["package_hash"],
                "decision_document_sha256": sha256(
                    json.dumps(
                        report_document,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            },
        }
        output.mkdir(parents=True, exist_ok=True)
        snapshot_root = output / "web-source-snapshots"
        if supplemental_snapshots:
            snapshot_root.mkdir(parents=True, exist_ok=True)
        for source_id, snapshot in supplemental_snapshots.items():
            (snapshot_root / f"{source_id}.json").write_text(
                json.dumps(
                    snapshot,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        artifacts: dict[str, Any] = {
            "intervention-brief.json": brief,
            "decision-report-seed.json": decision_seed,
            "frozen-package.json": frozen_package,
            "report-document.json": report_document,
            "evidence-workbook.json": evidence_workbook,
        }
        if legacy_report_path.exists():
            if archived_report_path.exists():
                raise FileExistsError(
                    "cannot archive report.html because "
                    "invalid-legacy-report.html already exists"
                )
            legacy_report_path.rename(archived_report_path)
        for filename, payload in artifacts.items():
            (output / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        (output / "chapter-preview.html").write_bytes(
            _render_chapter_preview(frozen_package).encode("utf-8")
        )
        return LegacyMigrationResult(
            manifest=manifest,
            decision_seed=decision_seed,
            frozen_package=frozen_package,
            report_document=report_document,
            evidence_workbook=evidence_workbook,
            output_root=output,
        )


__all__ = [
    "LegacyMigrationResult",
    "LegacyProjectReportMigrator",
    "discover_v1_source_materials",
]
