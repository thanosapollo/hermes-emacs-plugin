"""Best-effort secret redaction for data returned to the model.

Heuristic regex over both dict keys and string values. It is not a guarantee:
treat it as defense in depth, never as the only control. Logging must never
include the elisp payload or secrets either way.
"""
from __future__ import annotations

import re
from typing import Any

SECRET_KEY_RE = re.compile(
    r"(password|secret|token|apikey|api_key|credential|authorization|bearer)", re.I
)
SECRET_VALUE_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(sk-[A-Za-z0-9_-]{16,})|"
    r"(gh[pousr]_[A-Za-z0-9_]{16,})|"
    r"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)|"
    r"((?:token|secret|apikey|api_key|password|credential|bearer)\s*[:=]\s*)[A-Fa-f0-9]{40}"
)


def redact_string(value: str) -> str:
    """Return VALUE with token-like substrings masked.

    A bare 40-hex string (a git commit SHA) is deliberately not redacted; only
    a 40-hex value gated by a secret-looking key is.
    """

    def _replacer(m: re.Match) -> str:
        for prefix in (m.group(1), m.group(5)):
            if prefix is not None:
                return prefix + "[REDACTED]"
        return "[REDACTED]"

    return SECRET_VALUE_RE.sub(_replacer, value)


def safe(obj: Any) -> Any:
    """Redact obvious secret-bearing fields before returning data to the model."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if SECRET_KEY_RE.search(str(k)):
                out[k] = "[REDACTED]"
            else:
                out[k] = safe(v)
        return out
    if isinstance(obj, list):
        return [safe(v) for v in obj]
    if isinstance(obj, str):
        return redact_string(obj)
    return obj
