"""Forward repository-root imports to the authoritative ``src/dds`` package.

The root ``dds`` directory is retained only for repository compatibility. It
must never fall back to the legacy implementation that remains beside this
shim.
"""

from pathlib import Path as _Path

_SRC_PACKAGE = (_Path(__file__).resolve().parents[1] / "src" / "dds").resolve()
_SRC_INIT = _SRC_PACKAGE / "__init__.py"

if not _SRC_PACKAGE.is_dir() or not _SRC_INIT.is_file():
    raise ImportError(f"authoritative DDS src package is missing: {_SRC_INIT}")

__path__ = [str(_SRC_PACKAGE)]
__file__ = str(_SRC_INIT)

if __spec__ is not None:
    __spec__.origin = str(_SRC_INIT)
    if __spec__.submodule_search_locations is not None:
        __spec__.submodule_search_locations[:] = __path__

exec(compile(_SRC_INIT.read_bytes(), str(_SRC_INIT), "exec"), globals(), globals())
