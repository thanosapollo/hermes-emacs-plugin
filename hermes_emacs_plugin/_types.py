"""Shared type seams used across the package."""
from __future__ import annotations

from typing import Any, Callable, Protocol

Names = Callable[[str], str]


class ToolRegistrar(Protocol):
    """The slice of FastMCP the tool modules depend on."""

    def tool(
        self, *args: Any, **kwargs: Any
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Return a decorator that registers an MCP tool."""
        ...
