from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from dds.services.arch_front_bridge import (
    ArchFrontBridgeError,
    ArchFrontHtmlBridge,
)


def skill_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "skill"
    script = root / "scripts" / "build_project_report.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture", encoding="utf-8")
    return root


def test_bridge_uses_argument_vector_and_supports_freeze_only(tmp_path):
    skill = skill_fixture(tmp_path)
    project = tmp_path / "project with spaces"
    project.mkdir()
    observed = {}

    def runner(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"status": "evidence_frozen"}), ""
        )

    result = ArchFrontHtmlBridge(skill, runner=runner).build(
        project, as_of="2026-07-22", qa=False, freeze_only=True
    )
    assert result.status == "evidence_frozen"
    assert result.send_path is None
    assert observed["command"][2:4] == ["--project-dir", str(project.resolve())]
    assert "--freeze-only" in observed["command"]
    assert observed["kwargs"]["cwd"] == skill.resolve()


def test_sendable_status_requires_materialized_send_file(tmp_path):
    skill = skill_fixture(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"status": "sendable"}), ""
        )

    with pytest.raises(ArchFrontBridgeError, match="without send"):
        ArchFrontHtmlBridge(skill, runner=runner).build(
            project, as_of="2026-07-22"
        )


def test_non_json_or_rejected_build_fails_closed(tmp_path):
    skill = skill_fixture(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, "not json", "qa reject")

    with pytest.raises(ArchFrontBridgeError):
        ArchFrontHtmlBridge(skill, runner=runner).build(
            project, as_of="2026-07-22"
        )
