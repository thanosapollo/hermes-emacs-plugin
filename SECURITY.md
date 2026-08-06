# Security

This bridge can read editor state, evaluate Emacs Lisp, and modify files. Run it
only for trusted MCP clients and trusted Emacs endpoints.

- Prefer loopback. Loopback blocks remote peers but not other local processes.
- Set `EMACS_EXEC_TOKEN` on multi-user machines and for every non-loopback host.
- Never expose the Emacs `/eval` endpoint to the public Internet.
- Treat response redaction as defense in depth, not a secrecy guarantee.
- Keep `execute_code`, edit tools, and interactive-command allowlists minimal.

Report vulnerabilities privately to `bugs@thanosapollo.org`. Do not include
credentials, tokens, private network details, or user data in public reports.