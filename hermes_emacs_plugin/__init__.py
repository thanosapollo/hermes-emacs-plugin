"""hermes-emacs-plugin: a stdio MCP bridge to the user's running Emacs."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .server import build_server

try:
    __version__ = version("hermes-emacs-plugin")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+source"

__all__ = ["build_server", "__version__"]
