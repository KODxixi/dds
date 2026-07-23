from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest


from dds.reporting.assets import AssetResolver
from dds.reporting.compiler import (
    EVIDENCE_PACKAGE_SCHEMA,
    REPORT_COMPILER_INPUT_VERSION,
    SUPPORTED_PROFILE,
    FrozenEvidencePackageError,
    build_frozen_package,
    compile_frozen_package,
    compiler_fingerprint,
    compute_package_hash,
    compute_report_document_hash,
)
from test_reporting_document import report_seed


def _package() -> dict:
    package = {
        "schema_version": EVIDENCE_PACKAGE_SCHEMA,
        "package_id": "package-golden",
        "request_id": "request-golden",
        "project_id": "golden-001",
        "status": "ready",
        "validation_status": "valid",
        "as_of": "2026-07-22",
        "compiler_input_version": REPORT_COMPILER_INPUT_VERSION,
        "compiler_fingerprint": compiler_fingerprint(),
        "sources": [],
        "source_registry": [],
        "claims": [],
        "module_inputs": {"report_seed": report_seed()},
        "gaps": [],
        "errors": [],
        "created_at": "2026-07-22T00:00:00Z",
    }
    package["package_hash"] = compute_package_hash(package)
    return package


def test_compiler_fingerprint_covers_every_runtime_semantic_component() -> None:
    fingerprint = compiler_fingerprint()

    assert set(fingerprint["components"]) == {
        "assets.py",
        "compiler.py",
        "dds_cinematic_deck.py",
        "dds_report_apple_16x9.html",
        "evidence_contract.py",
        "report_chart_contract.py",
        "report_diagram_contract.py",
        "report_document.py",
        "report_structure_contract.py",
        "report_template_profile_v1.json",
        "renderer.py",
        "runtime_compat.py",
    }
    assert len(fingerprint["fingerprint"]) == 64


def test_compile_frozen_package_is_offline_and_matches_v1_document_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_forbidden(*args, **kwargs):
        raise AssertionError("report compilation attempted network I/O")

    monkeypatch.setattr("socket.create_connection", network_forbidden)
    monkeypatch.setattr("urllib.request.urlopen", network_forbidden)

    package = _package()
    document = compile_frozen_package(package)
    repeated = compile_frozen_package(package)
    v1_semantics = deepcopy(document)
    v1_semantics["meta"].pop("evidence_package_hash", None)

    assert compute_report_document_hash(document) == compute_report_document_hash(
        repeated
    )
    assert compute_report_document_hash(v1_semantics) == (
        "7a325ea78cefd259626f950d2ab0d4eb39b04765ef37e0d86a2d6303f1aab950"
    )
    assert document["template_profile_version"] == SUPPORTED_PROFILE


def test_freeze_embeds_only_allowlisted_relative_assets(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    image = assets / "site.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nOFFLINE")
    seed = report_seed()
    seed["asset_registry"] = [
        {
            "asset_id": "ASSET-1",
            "data_or_object_ref": "site.png",
            "rights_status": "test_fixture",
        }
    ]

    resolver = AssetResolver([assets])
    package = build_frozen_package(
        seed,
        project_id="golden-001",
        as_of="2026-07-22",
        asset_resolver=resolver,
    )
    repeated = build_frozen_package(
        seed,
        project_id="golden-001",
        as_of="2026-07-22",
        asset_resolver=resolver,
    )
    frozen = package["module_inputs"]["report_seed"]["asset_registry"][0]

    assert package == repeated
    assert frozen["embed_status"] == "embedded"
    assert frozen["data_uri"].startswith("data:image/png;base64,")
    assert frozen["content_hash"]
    assert "data_or_object_ref" not in frozen
    assert compute_package_hash(package) == package["package_hash"]


def test_compile_rejects_fingerprint_drift() -> None:
    package = deepcopy(_package())
    package["compiler_fingerprint"]["fingerprint"] = "0" * 64
    package["package_hash"] = compute_package_hash(package)

    with pytest.raises(FrozenEvidencePackageError, match="fingerprint"):
        compile_frozen_package(package)


