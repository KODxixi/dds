"""DDS Architecture Director readiness audit.

Checks whether the current repository has the minimum evidence, UI, report,
case, and learning scaffolding needed for the Architecture Director contract.
Pure stdlib; safe to run locally.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "data" / "architecture_director_contract.json"
OUT_PATH = ROOT / "data_out" / "reports" / "architecture_director_readiness.json"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _resolve_path(value: str) -> Path:
    text = str(value).replace("/", "\\")
    if len(text) >= 2 and text[1] == ":":
        return Path(text)
    return ROOT / value


def _exists(value: str) -> dict[str, Any]:
    path = _resolve_path(value)
    exists = path.exists()
    info: dict[str, Any] = {
        "path": value,
        "resolved": str(path),
        "exists": exists,
        "type": "missing",
    }
    if exists:
        if path.is_dir():
            try:
                children = list(path.iterdir())
            except Exception:
                children = []
            info.update({"type": "dir", "items": len(children)})
        else:
            info.update({"type": "file", "bytes": path.stat().st_size})
    return info


def _marker_check(path: Path, markers: list[str]) -> dict[str, Any]:
    text = _read_text(path)
    found = [m for m in markers if m in text]
    missing = [m for m in markers if m not in text]
    return {
        "path": str(path),
        "exists": path.exists(),
        "found": found,
        "missing": missing,
        "coverage": round(len(found) / max(1, len(markers)), 3),
    }


def _contract_checks(contract: dict[str, Any]) -> dict[str, Any]:
    required = [
        "contract_version",
        "non_negotiables",
        "source_tiers",
        "roles",
        "data_domains",
        "visual_asset_roles",
        "deliverables",
        "schemas",
        "scoring",
        "human_review_triggers",
        "learning_loop",
        "required_repository_paths",
    ]
    missing = [k for k in required if k not in contract]
    role_count = len(contract.get("roles", [])) if isinstance(contract.get("roles"), list) else 0
    deliverable_count = len(contract.get("deliverables", [])) if isinstance(contract.get("deliverables"), list) else 0
    return {
        "required_keys": required,
        "missing_keys": missing,
        "role_count": role_count,
        "deliverable_count": deliverable_count,
        "ok": not missing and role_count >= 8 and deliverable_count >= 6,
    }


def _dataset_snapshot() -> dict[str, Any]:
    tx_csv = ROOT / "Vault" / "成交数据" / "成交-武汉.csv"
    archlib = Path(r"D:\ArchLib\_project\_检索系统\data")
    archlib_expected = ["images_manifest.json", "project_images.json", "projects.jsonl", "images.jsonl"]
    return {
        "vault_manifest": _exists("Vault/_manifest.json"),
        "vault_sources": _exists("Vault/_sources.csv"),
        "wuhan_transaction_csv": _exists("Vault/成交数据/成交-武汉.csv"),
        "wuhan_transaction_approx_mb": round(tx_csv.stat().st_size / 1024 / 1024, 2) if tx_csv.exists() else 0,
        "land_data": _exists("Vault/土地数据"),
        "macro_data": _exists("Vault/宏观数据"),
        "benchmark_candidate_data": _exists("Vault/全国标杆新楼盘"),
        "archlib_data_root": _exists(str(archlib)),
        "archlib_expected_files": [_exists(str(archlib / name)) for name in archlib_expected],
    }


def _latest_interactive_report() -> Path | None:
    base = ROOT / "data_out" / "reports" / "interactive"
    if not base.exists():
        return None
    pages = sorted(base.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    return pages[0] if pages else None


def _gaps(contract: dict[str, Any], checks: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    path_missing = [x for x in checks["repository_paths"] if not x["exists"]]
    if path_missing:
        gaps.append({
            "gap_id": "required_path_missing",
            "severity": "blocking" if any("skills/arch-dds" in x["path"] or "app.py" in x["path"] for x in path_missing) else "confidence_affecting",
            "scope": [x["path"] for x in path_missing],
            "recommended_action": "Restore or explicitly mark these dependencies as pending before relying on the director workflow.",
        })
    arch_expected = checks["dataset_snapshot"]["archlib_expected_files"]
    arch_missing = [x["path"] for x in arch_expected if not x["exists"]]
    if arch_missing:
        gaps.append({
            "gap_id": "archlib_visual_manifest_gap",
            "severity": "confidence_affecting",
            "scope": arch_missing,
            "recommended_action": "Point ARCHLIB_DATA_DIR to the live ArchLib export or add adapters for the currently available shard layout.",
        })
    if checks["frontend_markers"].get("missing"):
        gaps.append({
            "gap_id": "web_workspace_shell_gap",
            "severity": "blocking",
            "scope": checks["frontend_markers"]["missing"],
            "recommended_action": "Restore the full DDS Web workbench before testing report depth or map/case workflows.",
        })
    report_missing = checks["deck_script_markers"].get("missing")
    if report_missing:
        gaps.append({
            "gap_id": "deck_export_contract_gap",
            "severity": "confidence_affecting",
            "scope": report_missing,
            "recommended_action": "Update the 16:9 export renderer so map, case board, source matrix and playback markers remain visible.",
        })
    visual_role_index = ROOT / "data_out" / "archlib_visual_roles.jsonl"
    if not visual_role_index.exists() or visual_role_index.stat().st_size == 0:
        gaps.append({
            "gap_id": "visual_role_missing",
            "severity": "confidence_affecting",
            "scope": ["visual_asset.image_role"],
            "recommended_action": "Derive image_role from ArchLib view_type, scene_part, folder and VLM tags; exclude non_case_document by default.",
        })

    learning_events = ROOT / "data_out" / "architecture_learning" / "learning_events.jsonl"
    if not learning_events.exists() or learning_events.stat().st_size == 0:
        gaps.append({
            "gap_id": "learning_event_store_missing",
            "severity": "confidence_affecting",
            "scope": ["learning_event", "case feedback", "section rewrite feedback"],
            "recommended_action": "Extend the existing CEO weight learning path to case acceptance/rejection and report rewrite feedback.",
        })
    runtime_packet_ready = (ROOT / "scripts" / "architecture_director.py").exists() and "attach_architecture_director_packet" in _read_text(ROOT / "app.py")
    if not runtime_packet_ready:
        gaps.append({
            "gap_id": "delivery_packet_not_yet_runtime_field",
            "severity": "implementation_pending",
            "scope": ["/api/report", "report_json"],
            "recommended_action": "Wrap current report_json in DeliveryBrief + DeliverablePacket without breaking existing frontend fields.",
        })
    return gaps


def main() -> int:
    if not CONTRACT_PATH.exists():
        raise SystemExit(f"contract not found: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    repo_paths = [_exists(p) for p in contract.get("required_repository_paths", [])]
    frontend_markers = _marker_check(ROOT / "index.html", contract.get("frontend_required_markers", []))
    deck_script_markers = _marker_check(ROOT / "scripts" / "dds_cinematic_deck.py", contract.get("report_required_markers", []))
    latest = _latest_interactive_report()
    latest_report_markers = _marker_check(latest, contract.get("report_required_markers", [])) if latest else {
        "path": None, "exists": False, "found": [], "missing": contract.get("report_required_markers", []), "coverage": 0.0
    }

    checks = {
        "contract": _contract_checks(contract),
        "repository_paths": repo_paths,
        "frontend_markers": frontend_markers,
        "deck_script_markers": deck_script_markers,
        "latest_interactive_report_markers": latest_report_markers,
        "dataset_snapshot": _dataset_snapshot(),
    }

    path_coverage = sum(1 for x in repo_paths if x["exists"]) / max(1, len(repo_paths))
    frontend_coverage = frontend_markers["coverage"]
    deck_coverage = max(deck_script_markers["coverage"], latest_report_markers["coverage"])
    contract_ok = 1.0 if checks["contract"]["ok"] else 0.0
    readiness_score = round(100 * (0.35 * path_coverage + 0.25 * frontend_coverage + 0.25 * deck_coverage + 0.15 * contract_ok), 1)

    checks["knowledge_gaps"] = _gaps(contract, checks)
    blockers = [g for g in checks["knowledge_gaps"] if g.get("severity") == "blocking"]
    status = "ready_for_runtime_integration" if readiness_score >= 85 and not blockers else "partial_ready"

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "contract_version": contract.get("contract_version"),
        "status": status,
        "readiness_score": readiness_score,
        "coverage": {
            "repository_paths": round(path_coverage, 3),
            "frontend_markers": frontend_coverage,
            "deck_markers": deck_coverage,
            "contract": contract_ok,
        },
        "checks": checks,
        "next_actions": [
            "Add DeliveryBrief and DeliverablePacket wrappers around build_report_json output.",
            "Build visual_asset.image_role adapter for ArchLib and expose candidate board in report_json.",
            "Persist learning_event for case accept/reject, section rewrite and prediction backtest.",
            "Promote architecture_director_audit.py into smoke/CI once runtime integration begins."
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": status,
        "readiness_score": readiness_score,
        "output": str(OUT_PATH),
        "blocking_gaps": [g["gap_id"] for g in blockers],
        "gap_count": len(checks["knowledge_gaps"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())