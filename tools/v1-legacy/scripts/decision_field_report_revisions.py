"""Immutable DDS HTML/JSON report bundle revisions with verified visual assets."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import threading
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from decision_field_artifact_reader import ArtifactReader, VerifiedArtifact
from garchos_openapi_contract import OpenAPIContract
from report_document import build_report_document


SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
    ).encode("utf-8")


class ReportIdempotencyConflictError(RuntimeError):
    pass


class ReportRevisionConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReportRevision:
    directory: Path
    bundle: dict[str, Any]
    html_bytes: bytes
    json_bytes: bytes
    replayed: bool = False


@dataclass(frozen=True)
class ResolvedReportArtifact:
    artifact: dict[str, Any]
    content: bytes


class DecisionFieldReportRevisionStore:
    def __init__(
        self,
        root: str | Path,
        *,
        artifact_reader: ArtifactReader | Any,
        contract: OpenAPIContract | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_reader = artifact_reader
        self.contract = contract or OpenAPIContract.from_environment()
        self._lock = threading.RLock()

    def resolve_artifact(
        self,
        *,
        owner: str,
        workflow_id: str,
        artifact_id: str,
    ) -> ResolvedReportArtifact:
        self.contract.validate_schema("StableId", workflow_id)
        self.contract.validate_schema("StableId", artifact_id)
        owner_segment = str(owner or "")
        if not SAFE_PATH_PART.fullmatch(owner_segment):
            raise KeyError(artifact_id)
        owner_root = self.root / "owners" / owner_segment
        if not owner_root.exists():
            raise KeyError(artifact_id)
        for project_dir in sorted(path for path in owner_root.iterdir() if path.is_dir()):
            if not SAFE_PATH_PART.fullmatch(project_dir.name):
                continue
            for directory in sorted(project_dir.glob("rev_[0-9][0-9][0-9][0-9]")):
                resolved = self._resolve_from_directory(
                    directory,
                    project_id=project_dir.name,
                    workflow_id=workflow_id,
                    artifact_id=artifact_id,
                )
                if resolved is not None:
                    return resolved
        raise KeyError(artifact_id)

    def freeze(
        self,
        *,
        owner: str,
        project_id: str,
        payload: dict[str, Any],
        key: str,
    ) -> ReportRevision:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            raise ValueError("idempotency key is required")
        self._validate_payload(project_id, payload)
        fingerprint = hashlib.sha256(_json_bytes(payload)).hexdigest()
        with self._lock:
            project_dir = self._project_dir(owner, project_id)
            idempotency = self._read_idempotency(project_dir)
            existing = idempotency.get(normalized_key)
            if existing:
                if existing.get("fingerprint") != fingerprint:
                    raise ReportIdempotencyConflictError(
                        "idempotency key was used for a different report payload"
                    )
                revision = self.get(
                    owner=owner,
                    project_id=project_id,
                    report_revision_id=existing["report_revision_id"],
                )
                return replace(revision, replayed=True)

            workflow_id = str(payload["workflow_id"])
            selected_ids = list(payload.get("selected_visual_artifact_ids") or [])
            visuals = [
                self.artifact_reader.read(
                    artifact_id=artifact_id,
                    owner=owner,
                    workflow_id=workflow_id,
                )
                for artifact_id in selected_ids
            ]
            for requested_id, visual in zip(selected_ids, visuals, strict=True):
                if visual.artifact["artifact_id"] != requested_id:
                    raise ValueError("ArtifactReader returned a different artifact")
                if visual.artifact["project_id"] != project_id:
                    raise ValueError("visual artifact project_id does not match")
                self.contract.validate_schema("ArtifactRef", visual.artifact)

            revision_number = self._next_revision_number(project_dir)
            created_at = _now()
            report_payload = self._build_report_payload(
                project_id=project_id,
                workflow_id=workflow_id,
                revision_number=revision_number,
                created_at=created_at,
                payload=payload,
                visuals=visuals,
            )
            report_json_bytes = _json_bytes(report_payload)
            report_html_bytes = self._render_html(payload["selected_scenario"], visuals)
            bundle = self._build_bundle(
                owner=owner,
                project_id=project_id,
                revision_number=revision_number,
                created_at=created_at,
                html_bytes=report_html_bytes,
                json_bytes=report_json_bytes,
            )
            self.contract.validate_schema("ReportBundleRef", bundle)

            final_dir = project_dir / f"rev_{revision_number:04d}"
            temp_dir = project_dir / f"rev_{revision_number:04d}.tmp-{uuid4().hex}"
            if final_dir.exists():
                raise ReportRevisionConflictError(f"report revision already exists: {final_dir.name}")
            temp_dir.mkdir(parents=True, exist_ok=False)
            try:
                (temp_dir / "report.html").write_bytes(report_html_bytes)
                (temp_dir / "report.json").write_bytes(report_json_bytes)
                (temp_dir / "bundle.json").write_bytes(_json_bytes(bundle))
                temp_dir.replace(final_dir)
            except Exception:
                if temp_dir.exists():
                    for path in temp_dir.iterdir():
                        path.unlink(missing_ok=True)
                    temp_dir.rmdir()
                raise

            idempotency[normalized_key] = {
                "fingerprint": fingerprint,
                "report_revision_id": bundle["report_revision_id"],
                "created_at": created_at,
            }
            self._write_idempotency(project_dir, idempotency)
            return ReportRevision(
                directory=final_dir,
                bundle=bundle,
                html_bytes=report_html_bytes,
                json_bytes=report_json_bytes,
            )

    def replay_if_exists(
        self,
        *,
        owner: str,
        project_id: str,
        payload: dict[str, Any],
        key: str,
    ) -> ReportRevision | None:
        """Return an existing exact idempotent result without creating a revision."""

        normalized_key = str(key or "").strip()
        if not normalized_key:
            raise ValueError("idempotency key is required")
        self._validate_payload(project_id, payload)
        fingerprint = hashlib.sha256(_json_bytes(payload)).hexdigest()
        with self._lock:
            project_dir = self._project_dir(owner, project_id, create=False)
            if not project_dir.exists():
                return None
            existing = self._read_idempotency(project_dir).get(normalized_key)
            if not existing:
                return None
            if existing.get("fingerprint") != fingerprint:
                raise ReportIdempotencyConflictError(
                    "idempotency key was used for a different report payload"
                )
            revision = self.get(
                owner=owner,
                project_id=project_id,
                report_revision_id=existing["report_revision_id"],
            )
            return replace(revision, replayed=True)

    def get(
        self,
        *,
        owner: str,
        project_id: str,
        report_revision_id: str,
    ) -> ReportRevision:
        project_dir = self._project_dir(owner, project_id, create=False)
        if not project_dir.exists():
            raise KeyError(report_revision_id)
        for directory in sorted(project_dir.glob("rev_[0-9][0-9][0-9][0-9]")):
            bundle_path = directory / "bundle.json"
            if not bundle_path.exists():
                continue
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            if bundle.get("report_revision_id") == report_revision_id:
                return ReportRevision(
                    directory=directory,
                    bundle=bundle,
                    html_bytes=(directory / "report.html").read_bytes(),
                    json_bytes=(directory / "report.json").read_bytes(),
                )
        raise KeyError(report_revision_id)

    def _validate_payload(self, project_id: str, payload: dict[str, Any]) -> None:
        allowed = {
            "workflow_id",
            "selected_scenario",
            "evidence_refs",
            "selected_visual_artifact_ids",
        }
        if set(payload) != allowed:
            raise ValueError("report payload fields do not match the fixed contract")
        self.contract.validate_schema("StableId", project_id)
        self.contract.validate_schema("StableId", payload["workflow_id"])
        self.contract.validate_schema("DecisionScenario", payload["selected_scenario"])
        if payload["selected_scenario"]["project_id"] != project_id:
            raise ValueError("selected scenario project_id does not match")
        if payload["selected_scenario"]["workflow_id"] != payload["workflow_id"]:
            raise ValueError("selected scenario workflow_id does not match")
        evidence_refs = payload.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            raise ValueError("evidence_refs must be an array")
        for evidence in evidence_refs:
            self.contract.validate_schema("EvidenceRef", evidence)
        selected = payload.get("selected_visual_artifact_ids")
        if not isinstance(selected, list) or len(selected) > 4 or len(set(selected)) != len(selected):
            raise ValueError("selected_visual_artifact_ids must contain at most four unique IDs")
        for artifact_id in selected:
            self.contract.validate_schema("StableId", artifact_id)

    @staticmethod
    def _build_report_payload(
        *,
        project_id: str,
        workflow_id: str,
        revision_number: int,
        created_at: str,
        payload: dict[str, Any],
        visuals: list[VerifiedArtifact],
    ) -> dict[str, Any]:
        scenario = deepcopy(payload["selected_scenario"])
        report_document = build_report_document(
            {
                "meta": {"generated_at": created_at},
                "project": {"id": project_id, "title": scenario["name"]},
                "decision": {"summary": scenario["summary"]},
            }
        )
        return {
            "schema_version": "garchos-dds-report-v1",
            "project_id": project_id,
            "workflow_id": workflow_id,
            "revision": revision_number,
            "created_at": created_at,
            "selected_scenario": scenario,
            "evidence_refs": deepcopy(payload["evidence_refs"]),
            "visual_artifacts": [deepcopy(item.artifact) for item in visuals],
            "report_document": report_document,
        }

    @staticmethod
    def _render_html(
        scenario: dict[str, Any], visuals: list[VerifiedArtifact]
    ) -> bytes:
        metrics = "".join(
            "<tr><th>{}</th><td>{} {}</td></tr>".format(
                html.escape(str(item.get("label") or item.get("code") or "")),
                html.escape(str(item.get("value") or "")),
                html.escape(str(item.get("unit") or "")),
            )
            for item in scenario.get("metrics") or []
        )
        images = "".join(
            '<figure><img alt="AI concept visual" src="data:{};base64,{}">'
            "<figcaption>AI 概念示意</figcaption></figure>".format(
                item.artifact["media_type"],
                base64.b64encode(item.content).decode("ascii"),
            )
            for item in visuals
        )
        document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(scenario['name']))}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:32px;color:#171717}}figure{{margin:24px 0}}img{{display:block;width:100%;height:auto}}figcaption{{margin-top:8px;color:#8a4b08;font-weight:700}}table{{border-collapse:collapse}}th,td{{padding:8px 12px;border:1px solid #ddd;text-align:left}}</style>
</head><body><main><h1>{html.escape(str(scenario['name']))}</h1><p>{html.escape(str(scenario['summary']))}</p><table>{metrics}</table>{images}</main></body></html>"""
        return document.encode("utf-8")

    def _build_bundle(
        self,
        *,
        owner: str,
        project_id: str,
        revision_number: int,
        created_at: str,
        html_bytes: bytes,
        json_bytes: bytes,
    ) -> dict[str, Any]:
        revision_seed = f"{owner}:{project_id}:{revision_number}:{created_at}".encode("utf-8")
        revision_hash = hashlib.sha256(revision_seed).hexdigest()
        html_sha = hashlib.sha256(html_bytes).hexdigest()
        json_sha = hashlib.sha256(json_bytes).hexdigest()
        html_artifact_hash = hashlib.sha256(
            f"{revision_hash}:{html_sha}".encode("utf-8")
        ).hexdigest()
        json_artifact_hash = hashlib.sha256(
            f"{revision_hash}:{json_sha}".encode("utf-8")
        ).hexdigest()
        core = {
            "report_revision_id": f"reportrev_{revision_hash[:24]}",
            "html_artifact_id": f"reporthtml_{html_artifact_hash[:24]}",
            "html_content_sha256": html_sha,
            "json_artifact_id": f"reportjson_{json_artifact_hash[:24]}",
            "json_content_sha256": json_sha,
        }
        bundle_sha = hashlib.sha256(_json_bytes(core)).hexdigest()
        return {
            "report_bundle_id": f"reportbundle_{bundle_sha[:24]}",
            **core,
            "bundle_sha256": bundle_sha,
            "created_at": created_at,
        }

    def _resolve_from_directory(
        self,
        directory: Path,
        *,
        project_id: str,
        workflow_id: str,
        artifact_id: str,
    ) -> ResolvedReportArtifact | None:
        try:
            bundle = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
            report_payload = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if report_payload.get("workflow_id") != workflow_id:
            return None
        if report_payload.get("project_id") != project_id:
            return None
        if artifact_id == bundle.get("html_artifact_id"):
            kind = "report_html"
            media_type = "text/html"
            filename = "report.html"
            digest_field = "html_content_sha256"
        elif artifact_id == bundle.get("json_artifact_id"):
            kind = "report_json"
            media_type = "application/json"
            filename = "report.json"
            digest_field = "json_content_sha256"
        else:
            return None
        try:
            content = (directory / filename).read_bytes()
        except OSError as exc:
            raise ValueError("immutable report artifact content is missing") from exc
        digest = hashlib.sha256(content).hexdigest()
        if digest != bundle.get(digest_field):
            raise ValueError("immutable report artifact digest does not match bundle")
        source_ids: list[str] = []
        for source in report_payload.get("visual_artifacts") or []:
            source_id = str(source.get("artifact_id") or "")
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)
        artifact = {
            "artifact_id": artifact_id,
            "project_id": project_id,
            "workflow_id": workflow_id,
            "producer": "dds",
            "kind": kind,
            "version": int(report_payload.get("revision") or 1),
            "media_type": media_type,
            "size_bytes": len(content),
            "content_sha256": digest,
            "sensitivity": "internal",
            "synthetic": False,
            "source_artifact_ids": source_ids,
            "generation": None,
            "created_at": bundle.get("created_at"),
        }
        self.contract.validate_schema("ArtifactRef", artifact)
        return ResolvedReportArtifact(artifact=artifact, content=content)

    def _project_dir(
        self, owner: str, project_id: str, *, create: bool = True
    ) -> Path:
        if not SAFE_PATH_PART.fullmatch(str(owner or "")):
            raise ValueError("invalid owner")
        if not SAFE_PATH_PART.fullmatch(str(project_id or "")):
            raise ValueError("invalid project_id")
        path = self.root / "owners" / str(owner) / str(project_id)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _next_revision_number(project_dir: Path) -> int:
        existing = [
            int(path.name.removeprefix("rev_"))
            for path in project_dir.glob("rev_[0-9][0-9][0-9][0-9]")
        ]
        return max(existing, default=0) + 1

    @staticmethod
    def _read_idempotency(project_dir: Path) -> dict[str, Any]:
        path = project_dir / "_idempotency.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_idempotency(project_dir: Path, payload: dict[str, Any]) -> None:
        path = project_dir / "_idempotency.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_bytes(_json_bytes(payload))
        temp.replace(path)
