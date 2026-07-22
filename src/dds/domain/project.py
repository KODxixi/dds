"""Project-domain compatibility exports.

The implementation lives in :mod:`dds.domain.models`.  This module only
provides the stable path proposed by the DDS V2 package layout.
"""

from dds.domain.models import ProjectContext


__all__ = ["ProjectContext"]
