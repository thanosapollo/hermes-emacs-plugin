"""Stdio MCP bridge from the Hermes agent host to the user's Emacs.

The bridge exposes namespaced MCP tools that forward conservative Emacs Lisp
snippets to a minimal HTTP-JSON eval endpoint running inside the user's Emacs.
The Hermes agent (an MCP client) spawns this process over stdio. The agent never
talks to Emacs directly: this process dials back to Emacs, usually over loopback
or a private network.

Transport: stdio only.
Secrets: loaded from env or a user-owned dotenv file.
"""
from __future__ import annotations

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP

from .config import Config, config_from_env, load_env, require_secure_transport
from .eval_client import EvalClient
from .tools import register_all
from .tools.buffers import list_buffers_form
from .tools.context import current_context_form

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("hermes-emacs-plugin")


def build_server(config: Config) -> FastMCP:
    """Construct and wire a FastMCP server for CONFIG, registering all tools.

    Pure construction: no network calls and no transport-security check. Side
    effects (the fail-closed check, mcp.run) belong to main.
    """
    mcp = FastMCP(
        "emacs",
        instructions=(
            "MCP bridge to the user's running Emacs via a local HTTP-JSON eval "
            "endpoint. Read-only tools inspect buffers and project context; "
            "execute_code and apply_patch are destructive."
        ),
    )
    client = EvalClient(config.url, config.timeout, config.token)
    register_all(mcp, client, config.names, config)
    _register_resources(mcp, client)
    return mcp


def _register_resources(mcp: FastMCP, client: EvalClient) -> None:
    """Register the emacs:// resources backed by CLIENT."""

    @mcp.resource("emacs://buffers", description="Live Emacs buffer list as JSON")
    def emacs_buffers_resource() -> str:
        """Return the current Emacs buffer list as a JSON string."""
        return json.dumps(client.eval_json(list_buffers_form()))

    @mcp.resource("emacs://project", description="Current Emacs project and buffer context as JSON")
    def emacs_project_resource() -> str:
        """Return the current Emacs project context as a JSON string."""
        return json.dumps(client.eval_json(current_context_form()))


def main() -> None:
    """Run the stdio MCP server."""
    load_env()
    config = config_from_env()
    error = require_secure_transport(config.url, config.token)
    if error:
        logger.error("%s", error)
        raise SystemExit(1)
    mcp = build_server(config)
    logger.info(
        "starting emacs MCP bridge: host=%s port=%s tool=%s",
        config.host,
        config.port,
        config.exec_tool_name,
    )
    mcp.run()


if __name__ == "__main__":
    main()
