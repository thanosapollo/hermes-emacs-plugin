"""Environment-driven configuration for the Emacs MCP bridge.

Config is parsed from env in one place and must never crash before the process
can speak MCP over stdio: malformed values fall back to defaults (see
``_float_env``). Transport-security helpers live here too, but the fail-closed
check is only invoked from ``main`` (never at import).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from urllib.parse import ParseResult, urlparse

from dotenv import load_dotenv

DEFAULT_TOOL_PREFIX = "emacs"
DEFAULT_TIMEOUT = 30.0
MAX_BUFFER_CHARS = 200_000
MAX_WAIT_SECONDS = 30.0
MAX_SEARCH_RESULTS = 200
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
INTERACTIVE_COMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")


def default_env_paths() -> list[Path | None]:
    """Return the dotenv search order, honoring EMACS_EXEC_ENV first."""
    return [
        Path(os.getenv("EMACS_EXEC_ENV", "")) if os.getenv("EMACS_EXEC_ENV") else None,
        Path.home() / ".config" / "hermes-emacs-plugin" / "env",
        Path.cwd() / ".env",
    ]


def load_env() -> None:
    """Load the first dotenv file found in the configured search order."""
    for env_path in default_env_paths():
        if env_path and env_path.exists():
            load_dotenv(env_path)
            break


def _float_env(name: str, default: float) -> float:
    """Return env var NAME as a float, falling back to DEFAULT on a bad value.

    Parsed at import time, so a malformed value must not crash the process
    before it can speak the MCP protocol over stdio.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if isfinite(value) and value > 0 else default


def _allowed_commands(value: str | None) -> set[str]:
    """Return a stripped command allowlist from a comma-separated env value."""
    if not value:
        return set()
    return {command for command in (part.strip() for part in value.split(",")) if command}


def _parse_exec_url(url: str) -> ParseResult | None:
    """Return a validated Emacs endpoint URL, or None for malformed input."""
    try:
        parsed = urlparse(url)
        port = parsed.port
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or username is not None
        or password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    return parsed


def is_loopback(url: str) -> bool:
    """Return True when URL names a loopback interface.

    Loopback is trusted only against remote network peers; local processes may
    still connect. Any non-loopback endpoint is untrusted and must carry a
    shared token.
    """
    parsed = _parse_exec_url(url)
    return parsed is not None and (parsed.hostname or "").casefold() in LOOPBACK_HOSTS


def require_secure_transport(url: str, token: str) -> str | None:
    """Return an error message when URL is non-loopback without a token.

    The bridge POSTs arbitrary Emacs Lisp to URL, so a non-loopback bind with no
    shared token has no transport authentication at all. Refuse it (fail closed):
    every non-loopback host must present a token that the Emacs endpoint enforces.
    """
    parsed = _parse_exec_url(url)
    if parsed is None:
        return "refusing to start: EMACS_EXEC_URL is not a valid HTTP endpoint"
    if (parsed.hostname or "").casefold() in LOOPBACK_HOSTS or token:
        return None
    return (
        f"refusing to start: {url} is not a loopback address and EMACS_EXEC_TOKEN "
        "is unset; set a shared token (and hermes-exec-token in Emacs) or point "
        "EMACS_EXEC_URL at loopback"
    )


@dataclass(frozen=True)
class Config:
    """Resolved bridge configuration. Built once from the environment."""

    host: str
    port: str
    url: str
    timeout: float
    token: str
    tool_prefix: str
    exec_tool_name: str
    allowed_commands: frozenset[str] = field(default_factory=frozenset)

    def names(self, base: str) -> str:
        """Return the MCP tool name for BASE under the configured prefix.

        A single prefix applies uniformly to every tool. An empty prefix yields
        the bare base name, which avoids the redundant ``mcp_<server>_emacs_*``
        double prefix when the agent already prefixes tools with a descriptive
        server name.
        """
        return f"{self.tool_prefix}_{base}" if self.tool_prefix else base


def config_from_env() -> Config:
    """Build a Config from the current environment, never crashing on bad input."""
    host = os.getenv("EMACS_EXEC_HOST", "127.0.0.1")
    port = os.getenv("EMACS_EXEC_PORT", "8237")
    url = (os.getenv("EMACS_EXEC_URL") or f"http://{host}:{port}").rstrip("/")
    timeout = _float_env("EMACS_EXEC_TIMEOUT", DEFAULT_TIMEOUT)
    token = os.getenv("EMACS_EXEC_TOKEN", "")
    tool_prefix = os.getenv("EMACS_MCP_TOOL_PREFIX", DEFAULT_TOOL_PREFIX).strip("_")
    exec_tool_name = os.getenv("EMACS_EXEC_TOOL_NAME") or (
        f"{tool_prefix}_execute_code" if tool_prefix else "execute_code"
    )
    return Config(
        host=host,
        port=port,
        url=url,
        timeout=timeout,
        token=token,
        tool_prefix=tool_prefix,
        exec_tool_name=exec_tool_name,
        allowed_commands=frozenset(_allowed_commands(os.getenv("EMACS_ALLOWED_COMMANDS"))),
    )
