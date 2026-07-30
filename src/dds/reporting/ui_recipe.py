"""Validated, package-local Gary-UI decision-report recipe."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
RECIPE_ROOT = PACKAGE_ROOT / "vendor" / "gary_ui" / "decision_report"
TEMPLATE_PATH = PACKAGE_ROOT / "templates" / "dds_report_liquid_glass_v4.html"
_DISTRIBUTION_FILES = (
    "shell.html",
    "decision-report.bundle.css",
    "decision-report.bundle.js",
)
_ENTRYPOINTS = {
    "shell": "shell.html",
    "styles": "decision-report.bundle.css",
    "controller": "decision-report.bundle.js",
}
_ABSOLUTE_OR_EXTERNAL = re.compile(
    r"(?i)\bhttps?://|\bfile://|(?:^|[\s\"'(=])[a-z]:[\\/]|\\\\"
)
_SHELL_EXTERNAL = re.compile(
    r"(?i)<(?:link|script)\b[^>]*(?:href|src)\s*="
)
_CSS_EXTERNAL = re.compile(r"(?i)@import\b|url\s*\(")
_JS_EXTERNAL = re.compile(
    r"(?i)\b(?:fetch|XMLHttpRequest|WebSocket)\b|"
    r"\bimport\s*(?:\(|[\"'{*])"
)


class RecipeBundleError(RuntimeError):
    """Raised when the vendored UI recipe is missing, unsafe, or changed."""


def _file_hash(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise RecipeBundleError(f"recipe file is unavailable: {path.name}") from exc
    return sha256(payload).hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RecipeBundleError(
            f"recipe file must be readable UTF-8: {path.name}"
        ) from exc


def _projection_hash(file_hashes: Mapping[str, str]) -> str:
    digest = sha256()
    for name in sorted(file_hashes):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hashes[name].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_inline_safety(name: str, text: str) -> None:
    if _ABSOLUTE_OR_EXTERNAL.search(text):
        raise RecipeBundleError(f"recipe file contains an external path: {name}")
    if name == "shell.html" and _SHELL_EXTERNAL.search(text):
        raise RecipeBundleError("recipe shell contains an external asset tag")
    if name.endswith(".css") and (
        _CSS_EXTERNAL.search(text) or "</style" in text.casefold()
    ):
        raise RecipeBundleError("recipe stylesheet is unsafe to inline")
    if name.endswith(".js") and (
        _JS_EXTERNAL.search(text) or "</script" in text.casefold()
    ):
        raise RecipeBundleError("recipe controller is unsafe to inline")


def recipe_component_paths() -> dict[str, Path]:
    """Return logical compiler inputs for the fixed Gary recipe snapshot."""
    return {
        f"gary-ui/decision-report/{name}": RECIPE_ROOT / name
        for name in (*_DISTRIBUTION_FILES, "profile.json")
    }


def load_decision_report_recipe(
    root: Path | None = None,
) -> dict[str, Any]:
    """Load and independently validate a decision-report recipe snapshot."""
    recipe_root = (root or RECIPE_ROOT).resolve()
    profile_path = recipe_root / "profile.json"
    try:
        raw_profile = json.loads(_read_text(profile_path))
    except json.JSONDecodeError as exc:
        raise RecipeBundleError("recipe profile must be valid JSON") from exc
    if not isinstance(raw_profile, Mapping):
        raise RecipeBundleError("recipe profile must be a JSON object")
    profile = deepcopy(dict(raw_profile))
    if profile.get("recipeId") != "gary-ui.decision-report":
        raise RecipeBundleError("recipe identity must be gary-ui.decision-report")
    if profile.get("version") != "1.0.0":
        raise RecipeBundleError("unsupported Gary decision-report recipe version")

    distribution = profile.get("distribution")
    if not isinstance(distribution, Mapping):
        raise RecipeBundleError("recipe distribution must be an object")
    if distribution.get("entrypoints") != _ENTRYPOINTS:
        raise RecipeBundleError("recipe entrypoints are not the fixed contract")
    if (
        distribution.get("offline") is not True
        or distribution.get("externalDependencies") != []
    ):
        raise RecipeBundleError("recipe must be dependency-free and offline")

    declared_files = distribution.get("files")
    if not isinstance(declared_files, Mapping) or set(declared_files) != set(
        _DISTRIBUTION_FILES
    ):
        raise RecipeBundleError("recipe distribution file set is invalid")

    texts: dict[str, str] = {}
    actual_hashes: dict[str, str] = {}
    for name in _DISTRIBUTION_FILES:
        path = recipe_root / name
        text = _read_text(path)
        _validate_inline_safety(name, text)
        texts[name] = text
        actual_hashes[name] = _file_hash(path)
        declaration = declared_files.get(name)
        if (
            not isinstance(declaration, Mapping)
            or declaration.get("sha256") != actual_hashes[name]
        ):
            raise RecipeBundleError(f"recipe file hash drifted: {name}")

    projection_hash = _projection_hash(actual_hashes)
    if distribution.get("projectionHash") != projection_hash:
        raise RecipeBundleError("recipe projection hash drifted")

    origin = profile.get("origin")
    if not isinstance(origin, Mapping):
        raise RecipeBundleError("recipe origin must be an object")
    if origin.get("integrationMode") != "approved-template-origin":
        raise RecipeBundleError(
            "recipe origin must declare approved-template-origin integration"
        )
    compiled_template_hash = _file_hash(TEMPLATE_PATH)
    if origin.get("templateSha256") != compiled_template_hash:
        raise RecipeBundleError(
            "compiled report template no longer matches the approved Gary origin"
        )

    return {
        "root": recipe_root,
        "profile": profile,
        "projection_hash": projection_hash,
        "integration_mode": origin["integrationMode"],
        "compiled_template_hash": compiled_template_hash,
        "shell": texts["shell.html"],
        "styles": texts["decision-report.bundle.css"],
        "controller": texts["decision-report.bundle.js"],
    }


__all__ = [
    "RECIPE_ROOT",
    "RecipeBundleError",
    "load_decision_report_recipe",
    "recipe_component_paths",
]
