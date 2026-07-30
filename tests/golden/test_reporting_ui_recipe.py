from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

import pytest

from dds.reporting.compiler import TEMPLATE_PATH, compiler_component_paths
from dds.reporting.ui_recipe import (
    RecipeBundleError,
    load_decision_report_recipe,
)


def test_vendored_gary_recipe_is_hash_pinned_and_matches_compiled_template() -> None:
    bundle = load_decision_report_recipe()
    profile = bundle["profile"]

    assert profile["recipeId"] == "gary-ui.decision-report"
    assert profile["version"] == "1.0.0"
    assert profile["distribution"]["offline"] is True
    assert profile["distribution"]["externalDependencies"] == []
    assert bundle["integration_mode"] == "approved-template-origin"
    assert bundle["compiled_template_hash"] == profile["origin"]["templateSha256"]
    assert profile["origin"]["templateSha256"] == sha256(
        TEMPLATE_PATH.read_bytes()
    ).hexdigest()
    assert "C:\\AI" not in str(bundle)
    assert "file://" not in str(bundle).lower()


def test_recipe_files_are_part_of_the_compiler_fingerprint() -> None:
    components = compiler_component_paths()

    assert {
        "gary-ui/decision-report/profile.json",
        "gary-ui/decision-report/shell.html",
        "gary-ui/decision-report/decision-report.bundle.css",
        "gary-ui/decision-report/decision-report.bundle.js",
        "ui_recipe.py",
    }.issubset(components)


@pytest.mark.parametrize(
    ("file_name", "payload"),
    [
        ("decision-report.bundle.css", '@import url("https://example.test/ui.css");'),
        ("decision-report.bundle.js", 'fetch("https://example.test/runtime.js");'),
        ("shell.html", '<script src="https://example.test/runtime.js"></script>'),
    ],
)
def test_recipe_loader_rejects_drift_and_external_runtime(
    tmp_path: Path,
    file_name: str,
    payload: str,
) -> None:
    source = load_decision_report_recipe()["root"]
    candidate = tmp_path / "decision-report"
    shutil.copytree(source, candidate)
    (candidate / file_name).write_text(payload, encoding="utf-8")

    with pytest.raises(RecipeBundleError):
        load_decision_report_recipe(candidate)
