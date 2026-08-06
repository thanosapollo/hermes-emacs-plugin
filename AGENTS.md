# hermes-emacs-plugin

A stdio MCP server that bridges the Hermes agent to a user's running Emacs. It
exposes namespaced MCP tools (a large read-only inspection surface plus a few
narrow destructive tools) that forward conservative Emacs Lisp snippets to a
minimal HTTP-JSON `/eval` endpoint inside Emacs, redacting secrets and clamping
sizes on the way back.

It is **not** the chat/dashboard integration. That is the `emacs-hermes` package
talking to the Hermes dashboard gateway over a JSON-RPC WebSocket, the opposite
direction. The only place the two meet is `hermes-exec.el` (the `/eval` server).

## The triad

Three projects, two distinct protocols. This plugin sits in the middle of only
one of the two Emacs↔Hermes paths.

- **hermes-agent** — the Python LLM agent. It is the MCP *client*: it spawns this
  plugin and calls its tools.
- **hermes-emacs-plugin** (this repo) — the MCP *server*. The sole MCP-speaking
  component and the sole holder of the Emacs address/timeout/token.
- **emacs-hermes** — the Emacs Lisp side. `hermes-exec.el` serves the `/eval`
  endpoint this plugin POSTs to.

Sibling repo locations are not hardcoded: the Emacs endpoint is discovered via
`EMACS_EXEC_URL` or `EMACS_EXEC_HOST`/`EMACS_EXEC_PORT`, and the agent side via
its own `mcp_servers` config. Keep this doc free of machine-specific paths,
hostnames, and private network addresses.

## Integration contract with hermes-agent

Be a spec-compliant MCP server. The agent connects over stdio, calls
`initialize` → `list_tools` → `tools/call`. Concrete constraints, verified
against hermes-agent's `tools/mcp_tool.py`:

- It reads **only** each tool's `name`, `description`, and `inputSchema`. It
  **drops** `ToolAnnotations` (`readOnlyHint`/`destructiveHint`) and
  `outputSchema`. Emit plain `object` JSON Schemas (FastMCP does this for us).
- It **re-prefixes** every tool to `mcp_<server>_<tool>`, sanitizing both parts
  with `[^A-Za-z0-9_] → _`. With server name `emacs` and our default prefix,
  `emacs_ping` becomes `mcp_emacs_emacs_ping` — a double prefix. Set
  `EMACS_MCP_TOOL_PREFIX=` (empty) or choose the server name deliberately to
  avoid the redundancy. Our prefix is independent of the agent's.
- Parallel tool execution is **server-level** (`supports_parallel_tool_calls` in
  the agent's config), never derived from annotations.

Consequence: annotations and the agent's approval behavior are decoupled. The
real safety controls are the elisp a tool emits, the input clamps, and the
Emacs-side approval gate — not the hints.

## Driving Emacs: the `/eval` channel

Plain HTTP/JSON, not MCP. `POST {URL}/eval` with body `{"code": "<elisp>"}`
(default `127.0.0.1:8237`). The reply is **always HTTP 200**; success is
`{"ok": true, "result": <value>}` and failure is `{"ok": false, "error": ...}`,
so evaluation errors are reported in-band, never as HTTP failures.

Rules when adding or changing eval paths:

- **Never interpolate user input into elisp.** Encode every user-supplied string
  through `elisp_string` (`json.dumps`, in `eval_client.py`), which produces a
  safe elisp literal.
- `EvalClient.eval_json` wraps the form in `(json-encode ...)` and decodes the
  result (sometimes twice); use it when you want structured data back,
  `EvalClient.request` for a raw printed value.
- **Clamp every bound.** Hard caps live in `config.py`
  (`MAX_BUFFER_CHARS`, `MAX_WAIT_SECONDS`, `MAX_SEARCH_RESULTS`); new tools must
  bound their inputs through the `positive_int` / `bounded_timeout` helpers in
  `elisp.py`.
- Errors are returned, not raised: an unreachable Emacs or a bad response yields
  `{"ok": false, "error": ...}`.

## Security model

Network reachability is the primary control; a shared bearer token is the
enforced second layer.

- **Loopback excludes remote peers, not local processes.** A token remains
  recommended on multi-user machines.
- **Non-loopback fails closed on both ends.** The plugin refuses to start when
  the host is non-loopback and `EMACS_EXEC_TOKEN` is unset; `hermes-exec.el`
  likewise refuses to bind a non-loopback host without `hermes-exec-token`, and
  rejects any request whose `Authorization: Bearer` does not match (constant-time
  compare).
- **The token is auth, not confidentiality.** It travels over plain HTTP and
  only authenticates peers. Never bind a public interface.
- **The Emacs `y-or-n-p` approval gate is the real destructive control**, not the
  annotations the agent ignores.
- Secret redaction (`safe` / `redact_string` in `redaction.py`) is heuristic
  regex over keys and values; it is best-effort, not a guarantee. Logging goes to
  stderr only and must never include the elisp payload or secrets.

## Tool conventions

- Names go through `config.names("...")` (a single configurable prefix, default
  `emacs`) so `EMACS_MCP_TOOL_PREFIX` applies uniformly to every tool.
- Each tool is a pure module-level form-builder returning the elisp string plus a
  thin registered closure that calls `client.eval_json(builder(...))`. Builders
  are unit-tested directly; closures expose only model-facing params.
- Mark intent with `ToolAnnotations(readOnlyHint=True)` or `destructiveHint=True`
  for documentation and other clients, knowing hermes-agent drops them.
- Destructive tools stay narrow: `apply_patch` is a single exact match (rejects
  zero or multiple), large multi-file edits are delegated to the agent's native
  patcher by design.
- Optional surfaces are registered conditionally from their own modules:
  `call_interactive` only when `EMACS_ALLOWED_COMMANDS` is set (allowlist +
  symbol-name regex).
- To add a tool: write a pure `*_form(...)` builder in the right `tools/*` module,
  register a thin closure with `@mcp.tool(name=names("..."), annotations=…)`,
  encode user input via `elisp_string`, clamp bounds with `positive_int` /
  `bounded_timeout`, and return through `client.eval_json` or `client.request`.

## Python code style

- Functional and composable: pure elisp/JSON builders separated from a thin
  effect layer (`EvalClient`). Minimize mutation; prefer transforming data.
- Small, focused functions (~30 lines). Extract helpers rather than growing one.
- `from __future__ import annotations`; `str | None` unions; inject the
  `EvalClient` and `names` callable into tool registrations rather than reaching
  for module globals.
- Config is built once from env in `config.py` and **must never crash before MCP
  can speak** — malformed values fall back to defaults (`_float_env` is the
  pattern). The fail-closed transport check runs only in `main`, never at import.
- One space between tokens; never column-align values. Comments explain *why*,
  read like a human wrote them, and avoid decorative separators.

## Testing

- Tests live in `tests/test_server.py` (stdlib `unittest`). The HTTP/elisp
  boundary is stubbed: pure `*_form(...)` builders are tested directly (with a
  paren-balance check), and a `FakeClient` injected into `build_server` captures
  the forms for dispatch and safety tests. Coverage spans redaction (positive and
  negative corpora, incl. the 40-hex git-SHA carve-out), `Config`/env parsing,
  `require_secure_transport`, conditional registration, annotations, and safety
  rejections that must never reach Emacs.
- Run with `python -m unittest discover -s tests` or `nix flake check` (the flake
  compiles the package and runs the same unittest discovery — no pytest).
- The live path (a real Emacs + `hermes-exec.el`) is not exercised here; any
  integration test must be added deliberately behind a marker.

## Git & workflow

- Conventional commits with a scope, matching the existing history
  (`feat:`, `fix:`, `refactor:`). Subject line only unless a body is truly needed.
- Atomic commits, one logical change each. Discuss the approach before editing.
- Never commit AI/agent aids (`CLAUDE.md` and friends are gitignored). `AGENTS.md`
  is the tracked, shareable foundation.
- Never push; the maintainer handles pushes. Ask before amend/rebase or any
  destructive git operation.

## Architecture & repository layout

An installable package, `hermes_emacs_plugin`, with a clean seam between pure
form-builders and the thin effect layer:

```
hermes_emacs_plugin/
  __init__.py        # version + build_server()
  __main__.py        # python -m hermes_emacs_plugin -> main()
  config.py          # Config dataclass from env; names(); is_loopback;
                     #   require_secure_transport; clamps and regexes
  redaction.py       # safe(), redact_string(), SECRET_* regexes
  eval_client.py     # EvalClient(url, timeout, token): request(), eval_json();
                     #   elisp_string()
  elisp.py           # shared form helpers (context_form, optional_buffer_form,
                     #   positive_int, bounded_timeout)
  tools/
    __init__.py      # register_all(mcp, client, names, config)
    context.py       # ping, current_context, current_buffer, region_text,
                     #   project_info
    buffers.py       # list_buffers, buffer_text, wait_for_buffer, search,
                     #   goto_line, pop_to_buffer, diff_buffer, save_buffer,
                     #   revert_buffer
    edit.py          # execute_code, apply_patch, call_interactive
  server.py          # build_server(config) wires FastMCP + EvalClient + tools
                     #   and the emacs:// resources; main()
```

Supporting files: `tests/` (stdlib unittest), `examples/hermes-config.yaml`,
`pyproject.toml` (console script `hermes-emacs-mcp` ->
`hermes_emacs_plugin.server:main`), and a Nix `flake.nix` whose check compiles
the package and runs the test suite.

Internal elisp helper labels are namespaced `emacs-mcp--*`; these are ephemeral
`cl-labels`, not public API.
