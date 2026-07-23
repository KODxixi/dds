"""Migrate a V1 decision report into a self-contained V2 project package."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
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


def _source_admission(source: Mapping[str, Any]) -> tuple[str, list[str], list[str]]:
    source_type = str(source.get("source_type") or "")
    if source_type == "official_web":
        return (
            "qualified",
            ["statutory_or_geographic_boundary"],
            ["Only the cited geography, version, and effective date are supported."],
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


def _asset_registry_item(asset: SourceAsset) -> dict[str, Any]:
    status = (
        "rejected"
        if asset.extraction_status == "unsupported"
        else "needs_review"
        if asset.extraction_status != "extracted"
        else "qualified"
    )
    allowed_uses = ["project_goal", "scheme_state", "project_constraint"]
    if asset.original_name.lower().endswith(".pptx"):
        allowed_uses = ["scheme_state", "design_expression"]
    if asset.original_name.lower().endswith(".dwg"):
        allowed_uses = []
    return {
        "source_id": asset.source_id,
        "name": asset.original_name,
        "source_type": "client_provided_material",
        "trust_tier": "project_input",
        "status": status,
        "canonical_ref": f"dds:project-asset/{asset.source_id}",
        "sha256": asset.sha256,
        "snapshot_ref": asset.snapshot_ref,
        "relative_source_path": asset.relative_source_path,
        "rights_status": asset.rights_status,
        "extraction_status": asset.extraction_status,
        "allowed_uses": allowed_uses,
        "limitations": list(asset.limitations)
        + ["Client-provided material does not independently establish market facts."],
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
    ) -> LegacyMigrationResult:
        if not bool(intervention_brief.get("confirmed_at")):
            raise ValueError("confirmed intervention brief is required")
        output = target_root.resolve()
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
        registry_by_id = {
            str(item["source_id"]): item for item in source_registry
        }
        pages: list[dict[str, Any]] = []
        for raw in seed.get("page_manifest") or []:
            page = deepcopy(dict(raw))
            section_id = str(page.get("section_id") or "")
            page["chapter_id"] = section_id.lower()
            page["decision_question"] = _decision_question(section_id)
            page["decision_impact"] = _decision_impact(section_id)
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
            "required_units": included,
            "included_units": included,
            "intervention_required_units": profile["required_units"],
            "unit_policy": profile["unit_policy"],
        }
        decision_seed["page_manifest"] = pages
        decision_seed["source_registry"] = source_registry
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
            "claim_candidates": list(batch.get("candidates") or []),
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
        artifacts: dict[str, Any] = {
            "intervention-brief.json": brief,
            "decision-report-seed.json": decision_seed,
            "frozen-package.json": frozen_package,
            "report-document.json": report_document,
            "evidence-workbook.json": evidence_workbook,
        }
        for filename, payload in artifacts.items():
            (output / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        (output / "report.html").write_text(
            render_frozen_package(frozen_package),
            encoding="utf-8",
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
