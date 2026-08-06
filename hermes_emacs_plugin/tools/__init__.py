"""Tool registration. One module per cohesive group, wired by register_all."""
from __future__ import annotations

from .._types import Names, ToolRegistrar
from ..config import Config
from ..eval_client import EvalClient
from . import buffers, context, edit


def register_all(mcp: ToolRegistrar, client: EvalClient, names: Names, config: Config) -> None:
    """Register every enabled tool group on MCP.

    call_interactive registers conditionally from its own module based on
    CONFIG, so the surface is the same whether driven here or by a directly
    constructed server.
    """
    context.register(mcp, client, names, config)
    buffers.register(mcp, client, names, config)
    edit.register(mcp, client, names, config)
