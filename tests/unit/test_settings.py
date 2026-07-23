from __future__ import annotations

from pathlib import Path

from dds.config import Settings


def test_default_datasets_root_is_inside_v2(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DDS_DATASETS_ROOT", raising=False)
    repository_root = tmp_path / "arbitrary-parent" / "renamed-v2-repository"

    settings = Settings.from_env(repository_root=repository_root)

    assert settings.repository_root == repository_root.resolve()
    assert settings.datasets_root == (
        repository_root.resolve() / "data" / "curated"
    ).resolve()


def test_datasets_environment_override_remains_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    repository_root = tmp_path / "repository"
    override = tmp_path / "explicit-curated-datasets"
    monkeypatch.setenv("DDS_DATASETS_ROOT", str(override))

    settings = Settings.from_env(repository_root=repository_root)

    assert settings.datasets_root == override.resolve()

