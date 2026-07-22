# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC))

from dds.reporting.assets import AssetResolutionError, AssetResolver


def test_resolves_only_relative_files_under_allowlisted_roots(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    expected = assets / "images" / "site.png"
    expected.parent.mkdir()
    expected.write_bytes(b"offline-image")

    resolver = AssetResolver([assets])

    assert resolver.resolve("images/site.png") == expected.resolve()
    assert resolver.read_bytes("images/site.png") == b"offline-image"
    assert resolver.resolve("images/missing.png") is None


@pytest.mark.parametrize(
    "locator",
    [
        "../outside.png",
        "https://example.test/image.png",
        "file:///tmp/image.png",
        r"C:\\private\\image.png",
        r"\\\\server\\share\\image.png",
    ],
)
def test_rejects_traversal_urls_and_absolute_paths(
    tmp_path: Path, locator: str
) -> None:
    resolver = AssetResolver([tmp_path])

    with pytest.raises(AssetResolutionError):
        resolver.resolve(locator)


def test_absolute_path_is_rejected_even_when_it_is_inside_an_allowed_root(
    tmp_path: Path,
) -> None:
    image = tmp_path / "inside.png"
    image.write_bytes(b"image")

    with pytest.raises(AssetResolutionError):
        AssetResolver([tmp_path]).resolve(str(image.resolve()))


def test_duplicate_or_nested_roots_are_normalized_deterministically(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()

    resolver = AssetResolver([nested, tmp_path, tmp_path])

    assert resolver.roots == (nested.resolve(), tmp_path.resolve())


def test_missing_allowlist_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AssetResolutionError, match="unavailable"):
        AssetResolver([tmp_path / "missing"])
