"""Pure elisp form builders and input clamps shared across tool modules.

No effects here: each function returns an elisp string or a bounded number.
User-supplied strings must be encoded through ``elisp_string`` before they reach
any of these, never interpolated raw.
"""
from __future__ import annotations

from .config import MAX_WAIT_SECONDS
from .eval_client import elisp_string


def context_form(body: str) -> str:
    """Wrap BODY with small generic helpers for buffer and project metadata."""
    return f"""
(progn
  (require 'cl-lib)
  (require 'project)
  (require 'subr-x)
  (cl-labels
      ((emacs-mcp--buffer-alist
        (&optional buffer)
        (with-current-buffer (or buffer (current-buffer))
          `((buffer_name . ,(buffer-name))
            (file_name . ,(or buffer-file-name :json-false))
            (major_mode . ,(symbol-name major-mode))
            (point . ,(point))
            (mark_active . ,(if (use-region-p) t :json-false))
            (region_begin . ,(if (use-region-p) (region-beginning) :json-false))
            (region_end . ,(if (use-region-p) (region-end) :json-false)))))
       (emacs-mcp--project-alist
        ()
        (if-let* ((project (project-current nil)))
            `((root . ,(project-root project))
              (name . ,(file-name-nondirectory
                        (directory-file-name (project-root project)))))
          '((root . :json-false) (name . :json-false)))))
    {body}))
""".strip()


def optional_buffer_form(buffer: str | None) -> str:
    """Return an Emacs Lisp form resolving BUFFER or the current buffer."""
    return f"(get-buffer {elisp_string(buffer)})" if buffer else "(current-buffer)"


def positive_int(value: int | None, default: int, maximum: int | None = None) -> int:
    """Return VALUE clamped to [1, MAXIMUM], or DEFAULT when unset/invalid."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    parsed = max(1, parsed)
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def bounded_timeout(value: float | int | None, default: float = 10.0) -> float:
    """Return VALUE clamped to [0, MAX_WAIT_SECONDS], or DEFAULT when unset/invalid."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(0.0, parsed), MAX_WAIT_SECONDS)
