"""Read-only buffer tools plus the narrow save/revert/diff file operations."""
from __future__ import annotations

import logging
from typing import Any

from mcp.types import ToolAnnotations

from ..config import MAX_BUFFER_CHARS, MAX_SEARCH_RESULTS, Config
from ..elisp import bounded_timeout, context_form, optional_buffer_form, positive_int
from ..eval_client import EvalClient, elisp_string
from .._types import Names, ToolRegistrar

logger = logging.getLogger("hermes-emacs-plugin")


def list_buffers_form() -> str:
    """Return elisp listing metadata for all live buffers."""
    return context_form("(vconcat (mapcar #'emacs-mcp--buffer-alist (buffer-list)))")


def buffer_text_form(name: str, limit: int) -> str:
    """Return elisp reading buffer NAME up to LIMIT chars."""
    name_s = elisp_string(name)
    return context_form(
        f"""
(let* ((name {name_s})
       (max-chars {limit})
       (buffer (get-buffer name)))
  (unless buffer
    (error "buffer not found: %s" name))
  (with-current-buffer buffer
    (let* ((end (min (point-max) (+ (point-min) max-chars)))
           (text (buffer-substring-no-properties (point-min) end)))
      `((buffer . ,(emacs-mcp--buffer-alist buffer))
        (text . ,text)
        (truncated . ,(if (< end (point-max)) t :json-false))))))
""".strip()
    )


def wait_for_buffer_form(name: str, timeout: float) -> str:
    """Return elisp polling briefly for buffer NAME."""
    name_s = elisp_string(name)
    timeout_s = f"{bounded_timeout(timeout):.1f}"
    return context_form(
        f"""
(let* ((name {name_s})
       (timeout {timeout_s})
       (deadline (+ (float-time) timeout))
       (buffer (get-buffer name)))
  (while (and (not buffer) (< (float-time) deadline))
    (accept-process-output nil 0.1)
    (setq buffer (get-buffer name)))
  (if buffer
      `((found . t) (buffer . ,(emacs-mcp--buffer-alist buffer)))
    `((found . :json-false) (buffer_name . ,name))))
""".strip()
    )


def search_form(pattern: str, buffer: str | None, limit: int) -> str:
    """Return elisp searching for PATTERN in BUFFER, capped at LIMIT results."""
    pattern_s = elisp_string(pattern)
    buffer_form = f"(get-buffer {elisp_string(buffer)})" if buffer is not None else "(current-buffer)"
    return context_form(
        f"""
(let* ((pattern {pattern_s})
       (max-results {limit})
       (buf {buffer_form})
       (results '()))
  (with-current-buffer buf
    (save-excursion
      (goto-char (point-min))
      (while (and (< (length results) max-results)
                  (search-forward pattern nil t))
        (let* ((match-start (match-beginning 0))
               (line-num (line-number-at-pos match-start))
               (line-text (buffer-substring-no-properties
                           (line-beginning-position)
                           (line-end-position))))
          (push (list (cons 'line line-num) (cons 'text line-text)) results))))
    (setq results (nreverse results)))
  `((buffer . ,(emacs-mcp--buffer-alist buf))
    (results . ,(vconcat results))
    (count . ,(length results))))
""".strip()
    )


def goto_line_form(line: int, buffer: str | None) -> str:
    """Return elisp moving point to LINE in BUFFER."""
    buffer_form = f"(get-buffer {elisp_string(buffer)})" if buffer is not None else "(current-buffer)"
    return f"""
(with-current-buffer {buffer_form}
  (goto-char (point-min))
  (forward-line (1- {line}))
  `((line . ,(line-number-at-pos))
    (column . ,(current-column))
    (buffer . ,(buffer-name))))
""".strip()


def pop_to_buffer_form(name: str) -> str:
    """Return elisp switching to buffer NAME in a window."""
    name_s = elisp_string(name)
    return f"""
(let* ((name {name_s})
       (buffer (get-buffer name)))
  (if buffer
      (progn
        (pop-to-buffer buffer)
        `((buffer . ,name)))
    (error "buffer not found: %s" name)))
""".strip()


def diff_buffer_form(buffer: str | None) -> str:
    """Return elisp diffing BUFFER against its file, capped."""
    buffer_form = optional_buffer_form(buffer)
    return f"""
(let* ((buf {buffer_form})
       (max-chars {MAX_BUFFER_CHARS}))
  (unless (bufferp buf)
    (error "buffer not found"))
  (with-current-buffer buf
    (let ((name (buffer-name))
          (file buffer-file-name)
          (modified (buffer-modified-p)))
      (unless file
        (error "buffer has no associated file"))
      (if (not modified)
          `((ok . t)
            (buffer . ,name)
            (modified . :json-false)
            (diff . "")
            (truncated . :json-false))
        (let* ((diff-buf (save-window-excursion
                           (diff-buffer-with-file (current-buffer))
                           (get-buffer "*Diff*")))
               (diff-end (if diff-buf
                             (with-current-buffer diff-buf
                               (min (point-max) (+ (point-min) max-chars)))
                           1))
               (diff-text (if diff-buf
                              (with-current-buffer diff-buf
                                (buffer-substring-no-properties (point-min) diff-end))
                            ""))
               (truncated (if diff-buf
                              (with-current-buffer diff-buf
                                (> (point-max) (+ (point-min) max-chars)))
                            :json-false)))
          `((ok . t)
            (buffer . ,name)
            (modified . ,(if modified t :json-false))
            (diff . ,diff-text)
            (truncated . ,(if truncated t :json-false))))))))
""".strip()


def save_buffer_form(buffer: str | None) -> str:
    """Return elisp saving BUFFER to its file."""
    buffer_form = optional_buffer_form(buffer)
    return f"""
(let ((buf {buffer_form}))
  (unless (bufferp buf)
    (error "buffer not found"))
  (with-current-buffer buf
    (let ((name (buffer-name))
          (file buffer-file-name)
          (modified (buffer-modified-p)))
      (unless file
        (error "buffer has no associated file"))
      (when modified
        (save-buffer))
      `((ok . t)
        (buffer . ,name)
        (file . ,file)
        (saved . ,(if modified t :json-false))))))
""".strip()


def revert_buffer_form(buffer: str | None) -> str:
    """Return elisp reverting BUFFER from disk."""
    buffer_form = optional_buffer_form(buffer)
    return f"""
(let ((buf {buffer_form}))
  (unless (bufferp buf)
    (error "buffer not found"))
  (with-current-buffer buf
    (let ((file buffer-file-name))
      (unless file
        (error "buffer has no associated file"))
      (revert-buffer :ignore-auto :noconfirm)
      `((ok . t)
        (buffer . ,(buffer-name))
        (file . ,file)
        (reverted . t)))))
""".strip()


def register(mcp: ToolRegistrar, client: EvalClient, names: Names, config: Config) -> None:
    """Register read-only buffer tools and the narrow file operations on MCP."""

    @mcp.tool(name=names("list_buffers"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_list_buffers() -> dict[str, Any]:
        """Return metadata for all live Emacs buffers."""
        logger.info("tool: %s", names("list_buffers"))
        return client.eval_json(list_buffers_form())

    @mcp.tool(name=names("buffer_text"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_buffer_text(name: str, max_chars: int = 20000) -> dict[str, Any]:
        """Return text and metadata for the Emacs buffer named NAME."""
        logger.info("tool: %s", names("buffer_text"))
        limit = positive_int(max_chars, 20000, MAX_BUFFER_CHARS)
        return client.eval_json(buffer_text_form(name, limit))

    @mcp.tool(name=names("wait_for_buffer"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_wait_for_buffer(name: str, timeout: float = 10.0) -> dict[str, Any]:
        """Wait briefly for an Emacs buffer named NAME and return its metadata if found."""
        logger.info("tool: %s", names("wait_for_buffer"))
        return client.eval_json(wait_for_buffer_form(name, timeout))

    @mcp.tool(name=names("search"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_search(
        pattern: str, buffer: str | None = None, max_results: int = 50
    ) -> dict[str, Any]:
        """Search for PATTERN in a buffer and return matching lines with line numbers."""
        logger.info("tool: %s", names("search"))
        if not pattern:
            return {"ok": False, "error": f"{names('search')} requires non-empty pattern"}
        limit = positive_int(max_results, 50, MAX_SEARCH_RESULTS)
        return client.eval_json(search_form(pattern, buffer, limit))

    @mcp.tool(name=names("goto_line"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_goto_line(line: int, buffer: str | None = None) -> dict[str, Any]:
        """Move point to LINE in a buffer and return the resulting position."""
        logger.info("tool: %s", names("goto_line"))
        if line < 1:
            return {"ok": False, "error": f"{names('goto_line')} requires line >= 1"}
        return client.eval_json(goto_line_form(line, buffer))

    @mcp.tool(name=names("pop_to_buffer"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_pop_to_buffer(name: str) -> dict[str, Any]:
        """Switch to buffer NAME, making it visible in a window."""
        logger.info("tool: %s", names("pop_to_buffer"))
        if not name:
            return {"ok": False, "error": f"{names('pop_to_buffer')} requires non-empty name"}
        return client.eval_json(pop_to_buffer_form(name))

    @mcp.tool(name=names("diff_buffer"), annotations=ToolAnnotations(readOnlyHint=True))
    def emacs_diff_buffer(buffer: str | None = None) -> dict[str, Any]:
        """Return a capped diff between an Emacs buffer and its associated file."""
        logger.info("tool: %s", names("diff_buffer"))
        return client.eval_json(diff_buffer_form(buffer))

    @mcp.tool(name=names("save_buffer"), annotations=ToolAnnotations(destructiveHint=True))
    def emacs_save_buffer(buffer: str | None = None) -> dict[str, Any]:
        """Save an Emacs buffer to its associated file."""
        logger.info("tool: %s", names("save_buffer"))
        return client.eval_json(save_buffer_form(buffer))

    @mcp.tool(name=names("revert_buffer"), annotations=ToolAnnotations(destructiveHint=True))
    def emacs_revert_buffer(buffer: str | None = None) -> dict[str, Any]:
        """Revert a buffer from disk. Discards unsaved changes. Use diff_buffer first to verify."""
        logger.info("tool: %s", names("revert_buffer"))
        return client.eval_json(revert_buffer_form(buffer))
