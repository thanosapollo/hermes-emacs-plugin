"""Destructive tools: raw eval, single-match patch, allowlisted interactive calls."""
from __future__ import annotations

import logging
from typing import Any

from mcp.types import ToolAnnotations

from ..config import INTERACTIVE_COMMAND_RE, Config
from ..eval_client import EvalClient, elisp_string
from .._types import Names, ToolRegistrar

logger = logging.getLogger("hermes-emacs-plugin")


def apply_patch_form(file: str, old: str, new: str) -> str:
    """Return elisp performing one exact text replacement in FILE and saving."""
    file_s = elisp_string(file)
    old_s = elisp_string(old)
    new_s = elisp_string(new)
    return f"""
(let ((file {file_s})
      (old {old_s})
      (new {new_s}))
  (let ((buffer (find-buffer-visiting file)))
    (when (and buffer (buffer-modified-p buffer))
      (error "buffer has unsaved changes: %s" file))
    (when (and buffer (not (verify-visited-file-modtime buffer)))
      (with-current-buffer buffer
        (revert-buffer :ignore-auto :noconfirm)))
    (with-current-buffer (or buffer (find-file-noselect file))
      (save-excursion
        (goto-char (point-min))
        (let ((matches '()))
          (while (search-forward old nil t)
            (let ((occurrence-start (match-beginning 0))
                  (occurrence-end (match-end 0)))
              (push (cons occurrence-start occurrence-end) matches)
              (goto-char (1+ occurrence-start))))
          (unless matches
            (error "old text not found in %s" file))
          (when (cdr matches)
            (error "old text appears more than once in %s" file))
          (let ((match-begin (caar matches))
                (match-end (cdar matches)))
            (delete-region match-begin match-end)
            (goto-char match-begin)
            (insert new)
            (save-buffer)))))
  `((file . ,file) (applied . t))))
""".strip()


def call_interactive_form(command: str) -> str:
    """Return elisp calling COMMAND interactively with yes/no auto-confirmed."""
    command_s = elisp_string(command)
    return f"""
(progn
  (require 'cl-lib)
  (condition-case err
      (cl-letf (((symbol-function 'y-or-n-p) (lambda (&rest _args) t))
                ((symbol-function 'yes-or-no-p) (lambda (&rest _args) t)))
        (call-interactively (quote {command}))
        `((ok . t)
          (command . ,{command_s})))
    (error
     `((ok . :json-false)
       (command . ,{command_s})
       (error . ,(error-message-string err))))))
""".strip()


def register(mcp: ToolRegistrar, client: EvalClient, names: Names, config: Config) -> None:
    """Register destructive tools, gating call_interactive on the allowlist."""

    @mcp.tool(name=config.exec_tool_name, annotations=ToolAnnotations(destructiveHint=True))
    def emacs_execute_code(code: str) -> dict[str, Any]:
        """Evaluate an Emacs Lisp expression in the user's running Emacs and return the printed result."""
        logger.info("tool: %s", config.exec_tool_name)
        return client.request(code)

    @mcp.tool(name=names("apply_patch"), annotations=ToolAnnotations(destructiveHint=True))
    def emacs_apply_patch(file: str, old: str, new: str) -> dict[str, Any]:
        """Apply one explicit text replacement to FILE in Emacs and save it."""
        label = names("apply_patch")
        logger.info("tool: %s", label)
        if not file:
            return {"ok": False, "error": f"{label} requires file"}
        if not old:
            return {"ok": False, "error": f"{label} requires non-empty old text"}
        if new is None:
            return {"ok": False, "error": f"{label} requires new text"}
        return client.eval_json(apply_patch_form(file, old, new))

    if config.allowed_commands:

        def emacs_call_interactive(command: str) -> dict[str, Any]:
            """Call an allowlisted interactive Emacs command with yes/no prompts auto-confirmed."""
            if command not in config.allowed_commands:
                return {"ok": False, "error": "command not in allowlist"}
            if not INTERACTIVE_COMMAND_RE.fullmatch(command):
                return {"ok": False, "error": "invalid command name"}
            return client.eval_json(call_interactive_form(command))

        mcp.tool(
            name=names("call_interactive"),
            annotations=ToolAnnotations(destructiveHint=True),
        )(emacs_call_interactive)
