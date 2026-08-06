# hermes-emacs-plugin

Stdio MCP server. Hermes Agent calls namespaced tools; this process forwards
conservative Emacs Lisp to a minimal HTTP-JSON `/eval` endpoint in the user's
Emacs, redacting secrets and clamping sizes on return.

Not the chat/dashboard path. That is `emacs-hermes` over JSON-RPC WebSocket to
the Hermes dashboard gateway (opposite direction). Only shared piece is
`hermes-exec.el` (`/eval` server).

## Public safety

- Public document. No local paths, users, hosts, IPs, private topology,
  secrets, tokens, private issues, logs, or personal config.
- Repository-relative paths and generic examples only.
- Keep durable architecture and rules. Verify facts against current source.

## Authority

Three projects, two protocols. This repo owns only the stdio MCP path.

- **hermes-agent** — MCP client; spawns this process and calls tools.
- **hermes-emacs-plugin** — MCP server; sole MCP speaker and sole holder of
  Emacs address, timeout, and token.
- **emacs-hermes** — `hermes-exec.el` serves `/eval`.

Discover endpoint via `EMACS_EXEC_URL` or `EMACS_EXEC_HOST`/`EMACS_EXEC_PORT`.
Agent registration lives in agent `mcp_servers` config. No sibling-repo paths.

## Architecture

Package `hermes_emacs_plugin`. Pure elisp/JSON form-builders separate from thin
`EvalClient` effects.

- `config.py` — env `Config`, `names()`, `is_loopback`,
  `require_secure_transport`, clamps, regexes
- `redaction.py` — `safe` / `redact_string`
- `eval_client.py` — `request` / `eval_json`, `elisp_string`
- `elisp.py` — shared form helpers (`positive_int`, `bounded_timeout`, …)
- `tools/` — `context`, `buffers`, `edit`; `register_all` wires them
- `server.py` — FastMCP + client + tools + `emacs://` resources; `main`

Entry: `hermes-emacs-mcp` → `hermes_emacs_plugin.server:main`. Ephemeral elisp
helpers inside forms use `emacs-mcp--*` labels; not public API.

## MCP contract (hermes-agent)

Stdio MCP: `initialize` → `list_tools` → `tools/call`. Verified against
hermes-agent `tools/mcp_tool.py`:

- Agent reads only `name`, `description`, `inputSchema`. Drops
  `ToolAnnotations` and `outputSchema`. Emit plain `object` JSON Schemas.
- Agent re-prefixes tools to `mcp_<server>_<tool>` (`[^A-Za-z0-9_]` → `_`).
  Default local prefix `emacs` plus server name `emacs` →
  `mcp_emacs_emacs_ping`. Set `EMACS_MCP_TOOL_PREFIX=` empty or pick server
  name deliberately. Local prefix independent of agent prefix.
- Parallel calls are server-level agent config
  (`supports_parallel_tool_calls`), never from annotations.

Annotations document intent for other clients. Real safety: emitted elisp,
input clamps, Emacs-side approval — not hints the agent ignores.

## `/eval` channel

Plain HTTP/JSON, not MCP. `POST {URL}/eval` body `{"code":"<elisp>"}`
(default loopback port `8237`). Always HTTP 200 with
`{"ok":true,"result":...}` or `{"ok":false,"error":...}`. Errors in-band.

Rules for eval paths:

- Never interpolate user input into elisp. Encode every user string with
  `elisp_string` (`json.dumps` in `eval_client.py`).
- `eval_json` wraps `(json-encode ...)` and decodes (sometimes twice).
  `request` returns raw printed value.
- Clamp every bound. Caps in `config.py`: `MAX_BUFFER_CHARS`,
  `MAX_WAIT_SECONDS`, `MAX_SEARCH_RESULTS`. Bound inputs via `positive_int` /
  `bounded_timeout` in `elisp.py`.
- Errors returned, not raised. Unreachable Emacs or bad response →
  `{"ok":false,"error":...}`.

## Security

Network reachability primary; shared bearer token enforced second layer.

- Loopback blocks remote peers, not other local processes. Token still
  recommended on multi-user machines.
- Non-loopback fails closed both ends. Plugin refuses start when host is
  non-loopback and `EMACS_EXEC_TOKEN` unset. `hermes-exec.el` refuses
  non-loopback bind without `hermes-exec-token` and rejects mismatched
  `Authorization: Bearer` (constant-time).
- Token is auth, not confidentiality (plain HTTP). Never public bind.
- Emacs `y-or-n-p` is the real destructive control.
- Redaction (`safe` / `redact_string`) best-effort regex, not a guarantee.
  Log stderr only; never log elisp payload or secrets.

## Tools

- Names through `Config.names(...)` so `EMACS_MCP_TOOL_PREFIX` is uniform.
- Pure `*_form(...)` builders return elisp; thin closures call
  `client.eval_json(builder(...))`. Unit-test builders directly.
- `ToolAnnotations(readOnlyHint=True)` or `destructiveHint=True` for docs.
- Destructive surface narrow: `apply_patch` is one exact match (reject 0 or
  many). Multi-file edits stay on the agent's native patcher.
- `call_interactive` only when `EMACS_ALLOWED_COMMANDS` set (allowlist +
  symbol-name regex).
- New tool: pure builder in `tools/*`; `@mcp.tool` closure; `elisp_string`
  user input; clamp with `positive_int` / `bounded_timeout`; return via
  `eval_json` or `request`.

## Python

- Pure builders; thin effects. Prefer transform over mutation. ~30-line
  functions.
- `from __future__ import annotations`; `str | None`; inject `EvalClient` and
  `names` into registrations.
- Config from env once; must never crash before MCP can speak. Malformed
  values fall back (`_float_env`). Fail-closed transport check only in
  `main`, never at import.
- One space between tokens. No column alignment. Comments explain why.

## Verification

```sh
python -m unittest discover -s tests   # FakeClient stub; no live Emacs
nix flake check                        # compile package + same discovery
git diff --check
```

- Tests in `tests/test_server.py` (stdlib unittest). Pure forms (paren
  balance) plus `FakeClient` in `build_server` for dispatch/safety.
- Cover redaction (incl. 40-hex git-SHA carve-out), `Config`/env,
  `require_secure_transport`, conditional registration, annotations, and
  safety rejections that must never reach Emacs.
- Live Emacs + `hermes-exec.el` not exercised here. Integration tests only
  behind an explicit marker.

## Workflow

1. Inspect Git state and scoped source/tests.
2. Smallest coherent change. Preserve unrelated work.
3. Focused checks, then risk-appropriate gates. Full-diff review.

- Conventional commits with scope (`feat:`, `fix:`, `refactor:`). Subject-only
  unless body needed. One logical change per commit.
- Never commit AI/agent aids (`CLAUDE.md` et al. gitignored). `AGENTS.md` is
  the tracked public foundation.
- No commit, amend, rebase, push, or destructive Git without explicit
  authorization. Stage only scoped verified files. No generated-by or
  co-author metadata.
