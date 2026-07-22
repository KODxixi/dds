from __future__ import annotations

from pathlib import Path

from dds.config import Settings


def test_default_v1_vault_is_derived_from_repository_sibling(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DDS_V1_VAULT_ROOT", raising=False)
    repository_root = tmp_path / "arbitrary-parent" / "renamed-v2-repository"

    settings = Settings.from_env(repository_root=repository_root)

    assert settings.repository_root == repository_root.resolve()
    assert settings.v1_vault_root == (
        repository_root.resolve().parent / "DDS" / "Vault"
    ).resolve()


def test_v1_vault_environment_override_remains_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    repository_root = tmp_path / "repository"
    override = tmp_path / "explicit-v1-vault"
    monkeypatch.setenv("DDS_V1_VAULT_ROOT", str(override))

    settings = Settings.from_env(repository_root=repository_root)

    assert settings.v1_vault_root == override.resolve()

