"""Offline, allowlisted asset resolution for report compilation.

Asset locators are portable paths relative to an explicitly supplied root.
URLs, URI schemes, drive-qualified paths, UNC paths, and traversal are rejected
before any filesystem access.  This keeps a report payload from turning the
compiler into an arbitrary local-file reader.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


class AssetResolutionError(ValueError):
    """Raised when an asset locator violates the offline path contract."""


class AssetResolver:
    """Resolve portable asset locators under a fixed root allowlist.

    Roots are canonicalized once and kept in caller order.  Locators are
    always relative, including when an absolute path happens to live beneath
    an allowed root; callers must express that file using its portable
    root-relative name.
    """

    def __init__(self, roots: Iterable[str | Path] | str | Path = ()) -> None:
        if isinstance(roots, (str, Path)):
            candidates: Iterable[str | Path] = (roots,)
        else:
            candidates = roots

        normalized: list[Path] = []
        seen: set[str] = set()
        for raw_root in candidates:
            try:
                root = Path(raw_root).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise AssetResolutionError(
                    f"asset root is unavailable: {raw_root}"
                ) from exc
            if not root.is_dir():
                raise AssetResolutionError(f"asset root is not a directory: {raw_root}")
            marker = os.path.normcase(str(root))
            if marker not in seen:
                normalized.append(root)
                seen.add(marker)
        self._roots = tuple(normalized)

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    @staticmethod
    def _portable_path(locator: str | Path) -> Path | None:
        text = str(locator).strip()
        if not text:
            return None
        if "\x00" in text:
            raise AssetResolutionError("asset locator contains a null byte")
        if (
            _URI_SCHEME.match(text)
            or _WINDOWS_DRIVE.match(text)
            or text.startswith(("\\\\", "//"))
        ):
            raise AssetResolutionError(
                "asset locator must be a relative offline path, not a URI or absolute path"
            )

        path = Path(text)
        if path.is_absolute() or path.drive or path.root:
            raise AssetResolutionError("absolute asset locators are forbidden")
        if any(part == ".." for part in path.parts):
            raise AssetResolutionError(
                "asset locator may not traverse outside its root"
            )
        return path

    @staticmethod
    def _is_within(candidate: Path, root: Path) -> bool:
        try:
            return os.path.commonpath((str(candidate), str(root))) == str(root)
        except ValueError:
            return False

    def resolve(
        self,
        locator: str | Path,
        *,
        required: bool = False,
    ) -> Path | None:
        relative = self._portable_path(locator)
        if relative is None:
            if required:
                raise FileNotFoundError("asset locator is empty")
            return None

        for root in self._roots:
            candidate = (root / relative).resolve(strict=False)
            if not self._is_within(candidate, root):
                raise AssetResolutionError(
                    "asset locator resolves outside its allowlisted root"
                )
            if candidate.is_file():
                return candidate

        if required:
            raise FileNotFoundError(
                f"asset was not found in allowlisted roots: {relative}"
            )
        return None

    def read_bytes(self, locator: str | Path) -> bytes:
        path = self.resolve(locator, required=True)
        assert path is not None  # ``required=True`` guarantees a path.
        return path.read_bytes()


__all__ = ["AssetResolutionError", "AssetResolver"]
