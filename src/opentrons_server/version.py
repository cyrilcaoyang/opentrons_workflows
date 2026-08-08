"""The gateway's own version — one number, derived from package metadata.

Kept in a leaf module with no imports of its own so anything in the package
(including ``gateway/``) can read it without pulling in the driver stack.

Read from the installed distribution rather than hard-coded here, because a
second hand-maintained copy drifts: ``__init__.__version__`` said ``0.2.0``
while ``pyproject.toml`` said ``0.3.0``. ``pyproject.toml`` is the only place
the number is written.
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

try:
    __version__ = _dist_version("opentrons_server")
except PackageNotFoundError:  # a source tree with no installed distribution
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
