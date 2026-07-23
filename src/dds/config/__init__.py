"""DDS runtime configuration.

Configuration is deliberately small and path based.  Importing this package never
touches the network and never mutates curated datasets.
"""

from .settings import Settings

__all__ = ["Settings"]
