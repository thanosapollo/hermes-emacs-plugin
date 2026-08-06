"""Read-only context tools: ping, current context, buffer, region, project."""
from __future__ import annotations

import logging
from typing import Any

from mcp.types import ToolAnnotations

from ..config import MAX_BUFFER_CHARS, Config
from ..elisp import context_form, positive_int
from ..eval_client import EvalClient
from .._types import Names, ToolRegistrar

logger = logging.getLogger("hermes-emacs-plugin")


def ping_form() -> str:
    """Return elisp reporting bridge liveness."""
    return '`((pong . t) (emacs_version . ,emacs-version) (bridge . "hermes-emacs-plugin"))'


def current_context_form() -> str:
    """Return elisp for current buffer and project context."""
    return context_form(
        "`((buffer . ,(emacs-mcp--buffer-alist)) (project . ,(emacs-mcp--project-alist)))"
    )


def current_buffer_form(limit: int) -> str:
    """Return elisp reading the current buffer up to LIMIT chars."""
    return context_form(
        f"""
(let* ((max-chars {limit})
       (end (min (point-max) (+ (point-min) max-chars)))
       (text (buffer-substring-no-properties (point-min) end)))
  `((buffer . ,(emacs-mcp--buffer-alist))
    (text . ,text)
    (truncated . ,(if (< end (point-max)) t :json-false))))
""".strip()
    )


def region_text_form() -> str:
    """Return elisp reading the active region, if any."""
    return """
(if (use-region-p)
    `((active . t)
      (text . ,(buffer-substring-no-properties (region-beginning) (region-end))))
  '((active . :json-false) (text . "")))
""".strip()


def project_info_form() -> str:
    """Return elisp describing the current project and a file sample."""
    return context_form(
        """
(if-let* ((project (project-current nil)))
    (let* ((files (or (ignore-errors (project-files project)) '()))
           (sample (cl-subseq files 0 (min (length files) 50))))
      `((project . ,(emacs-mcp--project-alist))
        (file_count . ,(length files))
        (files . ,(vconcat sample))))
  '((project . ((root . :json-false) (name . :json-false)))
    (file_count . 0)
    (files . [])))
""".strip()
    )


def register(mcp: ToolRegistrar, client: EvalClient, names: Names, config: Config) -> None:
    """Register read-only context tools on MCP."""

    @mcp.tool(name=names("ping"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_ping() -> dict[str, Any]:
        """Return Emacs bridge liveness information."""
        logger.info("tool: %s", names("ping"))
        return client.eval_json(ping_form())

    @mcp.tool(name=names("current_context"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_current_context() -> dict[str, Any]:
        """Return current buffer and project context from Emacs."""
        logger.info("tool: %s", names("current_context"))
        return client.eval_json(current_context_form())

    @mcp.tool(name=names("current_buffer"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_current_buffer(max_chars: int = 20000) -> dict[str, Any]:
        """Return current buffer text and metadata, capped by MAX_CHARS."""
        logger.info("tool: %s", names("current_buffer"))
        limit = positive_int(max_chars, 20000, MAX_BUFFER_CHARS)
        return client.eval_json(current_buffer_form(limit))

    @mcp.tool(name=names("region_text"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_region_text() -> dict[str, Any]:
        """Return active region text from the current Emacs buffer."""
        logger.info("tool: %s", names("region_text"))
        return client.eval_json(region_text_form())

    @mcp.tool(name=names("project_info"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_project_info() -> dict[str, Any]:
        """Return current Emacs project metadata and a sample of project files."""
        logger.info("tool: %s", names("project_info"))
        return client.eval_json(project_info_form())
