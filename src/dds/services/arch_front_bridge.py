"""Optional process-isolated bridge to the arch-front-html build workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Mapping


class ArchFrontBridgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArchFrontBuildResult:
    status: str
    project_dir: Path
    send_path: Path | None
    payload: Mapping[str, Any]
    stdout: str
    stderr: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


class ArchFrontHtmlBridge:
    """Call the skill as a subprocess so imports and build locks stay isolated."""

    ALLOWED_STATUSES = {"awaiting_user_input", "evidence_frozen", "sendable"}

    def __init__(
        self,
        skill_root: Path,
        *,
        dds_root: Path | None = None,
        python_executable: str = sys.executable,
        runner: Runner = subprocess.run,
    ) -> None:
        self.skill_root = skill_root.resolve()
        self.dds_root = dds_root.resolve() if dds_root else None
        self.python_executable = python_executable
        self.runner = runner
        self.script = self.skill_root / "scripts" / "build_project_report.py"
        if not self.script.is_file():
            raise ArchFrontBridgeError(f"arch-front-html builder missing: {self.script}")

    def build(
        self,
        project_dir: Path,
        *,
        as_of: date | str,
        qa: bool = True,
        freeze_only: bool = False,
        timeout_seconds: int = 1800,
    ) -> ArchFrontBuildResult:
        project_dir = project_dir.resolve()
        if not project_dir.is_dir():
            raise FileNotFoundError(project_dir)
        as_of_text = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
        try:
            if date.fromisoformat(as_of_text).isoformat() != as_of_text:
                raise ValueError
        except ValueError as exc:
            raise ValueError("as_of must be YYYY-MM-DD") from exc
        command = [
            self.python_executable,
            str(self.script),
            "--project-dir",
            str(project_dir),
            "--as-of",
            as_of_text,
        ]
        if self.dds_root is not None:
            command.extend(("--dds-root", str(self.dds_root)))
        if qa:
            command.append("--qa")
        if freeze_only:
            command.append("--freeze-only")
        completed = self.runner(
            command,
            cwd=self.skill_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
        )
        payload = self._parse_payload(completed.stdout)
        status = str(payload.get("status") or "")
        if completed.returncode != 0 or status not in self.ALLOWED_STATUSES:
            raise ArchFrontBridgeError(
                f"arch-front-html build rejected (exit={completed.returncode}, status={status!r}): "
                f"{completed.stderr.strip()}"
            )
        send_path = project_dir / "send" / "index.html"
        if status == "sendable":
            if not send_path.is_file():
                raise ArchFrontBridgeError("builder claimed sendable without send/index.html")
        else:
            send_path = None
        return ArchFrontBuildResult(
            status=status,
            project_dir=project_dir,
            send_path=send_path,
            payload=payload,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @staticmethod
    def _parse_payload(stdout: str) -> Mapping[str, Any]:
        text = stdout.strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArchFrontBridgeError("builder did not return a JSON result") from exc
        if not isinstance(payload, Mapping):
            raise ArchFrontBridgeError("builder JSON result must be an object")
        return dict(payload)


__all__ = [
    "ArchFrontBridgeError",
    "ArchFrontBuildResult",
    "ArchFrontHtmlBridge",
]
