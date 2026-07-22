# ruff: noqa: F401,F403
"""V1 builder API wrapper limited to the offline freeze/compile kernel.

TOS synchronization and application orchestration intentionally do not live in
the reporting package.
"""

from pathlib import Path
from typing import Any

try:
    from .assets import AssetResolver
    from .compiler import *
    from .compiler import (
        _compiler_fingerprint,
        _validate_frozen_package,
        freeze_report_seed_assets as _freeze_report_seed_assets_impl,
    )
except ImportError:  # pragma: no cover - top-level V1 import style
    from assets import AssetResolver
    from compiler import *
    from compiler import (
        _compiler_fingerprint,
        _validate_frozen_package,
        freeze_report_seed_assets as _freeze_report_seed_assets_impl,
    )


def _freeze_report_seed_assets(
    report_seed: dict[str, Any] | None,
    *,
    project: str | Path,
    work: str | Path,
) -> dict[str, Any] | None:
    """V1-signature adapter backed by the strict offline AssetResolver.

    V1 accepted arbitrary absolute asset locators.  The compatibility adapter
    deliberately does not: assets must be expressed relative to ``project`` or
    ``work`` so the payload cannot read outside those caller-owned roots.
    """
    project_root = Path(project).resolve(strict=True)
    work_root = Path(work).resolve(strict=True)
    resolver = AssetResolver((project_root, work_root))
    return _freeze_report_seed_assets_impl(
        report_seed,
        asset_resolver=resolver,
        snapshot_dir=work_root / "source_snapshots" / "assets",
    )
