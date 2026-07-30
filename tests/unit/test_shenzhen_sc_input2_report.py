from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "tools/dev/build_shenzhen_sc_report.py"


def test_sc_build_freezes_import_and_confirmation_time() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "datetime.now" not in source
    assert 'FROZEN_AT = "2026-07-24T16:40:21+08:00"' in source

    migrate_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "migrate"
    )
    imported_at = next(
        keyword.value
        for keyword in migrate_call.keywords
        if keyword.arg == "imported_at"
    )
    intervention_brief = next(
        keyword.value
        for keyword in migrate_call.keywords
        if keyword.arg == "intervention_brief"
    )
    confirmed_at = next(
        value
        for key, value in zip(
            intervention_brief.keys,
            intervention_brief.values,
            strict=True,
        )
        if isinstance(key, ast.Constant) and key.value == "confirmed_at"
    )

    assert isinstance(imported_at, ast.Name) and imported_at.id == "FROZEN_AT"
    assert isinstance(confirmed_at, ast.Name) and confirmed_at.id == "FROZEN_AT"
