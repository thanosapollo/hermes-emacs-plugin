# Hermes Emacs Plugin

Stdio MCP server. Hermes Agent calls namespaced tools; this process forwards
conservative Emacs Lisp to a minimal HTTP-JSON `/eval` endpoint in the user's
Emacs.

## Install

```bash
pipx install git+https://git.thanosapollo.org/hermes-emacs-plugin
# or, in a venv:
python -m pip install git+https://git.thanosapollo.org/hermes-emacs-plugin
```

Dev checkout:

```bash
git clone https://git.thanosapollo.org/hermes-emacs-plugin
cd hermes-emacs-plugin
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Entry points: `hermes-emacs-mcp` and `python -m hermes_emacs_plugin`.

## Config

Env or dotenv (`EMACS_EXEC_ENV`, `~/.config/hermes-emacs-plugin/env`, `./.env`):

| Variable | Default | Notes |
|---|---|---|
| `EMACS_EXEC_HOST` | `127.0.0.1` | Emacs eval host |
| `EMACS_EXEC_PORT` | `8237` | Emacs eval port |
| `EMACS_EXEC_URL` | from host/port | Full override |
| `EMACS_EXEC_TIMEOUT` | `30` | Seconds |
| `EMACS_MCP_TOOL_PREFIX` | `emacs` | Empty drops prefix |
| `EMACS_EXEC_TOOL_NAME` | `<prefix>_execute_code` | Raw eval tool name |
| `EMACS_EXEC_TOKEN` | unset | Required off-loopback; match `hermes-exec-token` |
| `EMACS_ALLOWED_COMMANDS` | unset | Comma allowlist enables `call_interactive` |

Hermes registration example: `examples/hermes-config.yaml`. Keep `--env`
before `--args`; Hermes treats everything after `--args` as subprocess args.

```bash
hermes mcp add emacs --command /absolute/path/to/hermes-emacs-plugin/.venv/bin/python --env EMACS_EXEC_HOST=127.0.0.1 EMACS_EXEC_PORT=8237 --args -m hermes_emacs_plugin
hermes mcp test emacs
```

## Tools

Prefix default `emacs_`. Core surface:

- read-only: `ping`, `current_context`, `current_buffer`, `region_text`,
  `project_info`, `list_buffers`, `buffer_text`, `wait_for_buffer`, `search`,
  `goto_line`, `pop_to_buffer`, `diff_buffer`
- destructive: `execute_code`, `apply_patch` (exact single match),
  `save_buffer`, `revert_buffer`
- optional: `call_interactive` when `EMACS_ALLOWED_COMMANDS` is set

`apply_patch` is one exact buffer-backed replacement. Multi-file patches stay on
the agent's native patcher.

## Security

- Loopback blocks remote peers, not other local processes. Use a token on
  multi-user machines.
- Bridge refuses start off-loopback without token (fail closed).
- Token is auth over plain HTTP, not confidentiality. Prefer loopback or private
  network; never public bind.
- Treat the MCP client as trusted: `execute_code` evaluates raw Emacs Lisp and
  edit tools can modify files. Emacs-side approval remains the final destructive
  control.
- Response secret redaction is best-effort regex.

HTTP contract: `POST {URL}/eval` body `{"code":"<elisp>"}`; always HTTP 200 with
`{"ok":true,"result":...}` or `{"ok":false,"error":...}`.

## Test

```bash
python -m unittest discover -s tests
# or
nix flake check
```

## License

MIT. See `LICENSE`.
