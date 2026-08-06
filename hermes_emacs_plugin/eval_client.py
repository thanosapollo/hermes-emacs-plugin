"""HTTP/elisp boundary to the Emacs ``/eval`` endpoint.

Plain HTTP/JSON, not MCP. The reply is always HTTP 200: success is
``{"ok": true, "result": ...}`` and failure ``{"ok": false, "error": ...}``,
so evaluation errors are reported in-band. Redaction is applied on the way out
and logging goes to stderr only, never including the elisp payload or secrets.
"""
from __future__ import annotations

import json
import logging
from math import isfinite
from typing import Any

import httpx

from .config import DEFAULT_TIMEOUT
from .redaction import redact_string, safe

logger = logging.getLogger("hermes-emacs-plugin")


def elisp_string(value: str) -> str:
    """Return VALUE encoded as a safe Emacs Lisp string literal."""
    return json.dumps(value, ensure_ascii=False)


class EvalClient:
    """POSTs elisp to Emacs and returns redacted JSON.

    Holds the url/timeout/token so tool closures depend only on this seam, not on
    module globals. Errors are returned, not raised.
    """

    def __init__(self, url: str, timeout: float, token: str) -> None:
        self.url = url
        self.timeout = timeout if isfinite(timeout) and timeout > 0 else DEFAULT_TIMEOUT
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def request(self, code: str) -> dict[str, Any]:
        """POST elisp to the Emacs eval endpoint and return the redacted JSON."""
        try:
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(f"{self.url}/eval", headers=self._headers(), json={"code": code})
        except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, OverflowError) as e:
            error = redact_string(f"Could not reach Emacs at {self.url}/eval: {e}")
            logger.error("emacs request failed: %s", error)
            return {"ok": False, "error": error}
        try:
            data = r.json()
        except (json.JSONDecodeError, ValueError):
            body = redact_string(r.text[:2000])
            error = f"Non-JSON response from {self.url}/eval (status {r.status_code}): {body}"
            logger.error("emacs request failed: %s", error)
            return {"ok": False, "error": error}
        if r.status_code >= 400:
            logger.error("emacs request failed: status %s", r.status_code)
            return {"ok": False, "status_code": r.status_code, "error": safe(data)}
        return safe(data)

    def eval_json(self, form: str) -> dict[str, Any]:
        """Evaluate FORM through Emacs and parse its JSON-encoded result when possible."""
        response = self.request(f"(progn (require 'json) (json-encode {form}))")
        if not response.get("ok"):
            return response

        result = response.get("result")
        if isinstance(result, str):
            try:
                decoded = json.loads(result)
                if isinstance(decoded, str):
                    decoded = json.loads(decoded)
            except (json.JSONDecodeError, ValueError):
                return response
            parsed = dict(response)
            parsed["result"] = safe(decoded)
            return parsed
        return response
