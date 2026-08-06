from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from hermes_emacs_plugin import config as config_mod
from hermes_emacs_plugin import eval_client as eval_client_mod
from hermes_emacs_plugin import redaction
from hermes_emacs_plugin.config import Config, config_from_env
from hermes_emacs_plugin.eval_client import EvalClient, elisp_string
from hermes_emacs_plugin.server import build_server
from hermes_emacs_plugin.tools import buffers, context, edit

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_config(**overrides) -> Config:
    """Return a default-prefix Config, applying any keyword overrides."""
    base = {
        "host": "127.0.0.1",
        "port": "8237",
        "url": "http://127.0.0.1:8237",
        "timeout": 30.0,
        "token": "",
        "tool_prefix": "emacs",
        "exec_tool_name": "emacs_execute_code",
        "allowed_commands": frozenset(),
    }
    base.update(overrides)
    return Config(**base)


def child_env(**extra) -> dict[str, str]:
    """Return a minimal subprocess env so dotenv/host config does not leak in."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "HOME": os.environ.get("HOME", ""),
    }
    env.update(extra)
    return env


INTROSPECT = (
    "import asyncio;"
    "from hermes_emacs_plugin.config import config_from_env;"
    "from hermes_emacs_plugin.server import build_server;"
    "mcp = build_server(config_from_env());"
    "tools = asyncio.run(mcp.list_tools());"
)


class FakeClient:
    """Stand-in for EvalClient that records forms instead of hitting Emacs."""

    def __init__(self, result=None) -> None:
        self.result = result if result is not None else {"ok": True, "result": {"captured": True}}
        self.forms: list[str] = []
        self.raw: list[str] = []

    def request(self, code: str):
        self.raw.append(code)
        return self.result

    def eval_json(self, form: str):
        self.forms.append(form)
        return self.result


def build_with_fake(client, **config_overrides):
    """Build a server wired to CLIENT, monkeypatching EvalClient construction."""
    config = make_config(**config_overrides)
    with mock.patch("hermes_emacs_plugin.server.EvalClient", return_value=client):
        return build_server(config)


def tool_func(mcp, name):
    """Return the registered Python callable for the tool named NAME."""
    return mcp._tool_manager.get_tool(name).fn


class ConfigTest(unittest.TestCase):
    def test_float_env_falls_back_when_timeout_is_malformed(self) -> None:
        with mock.patch.dict(os.environ, {"EMACS_EXEC_TIMEOUT": "soon"}):
            self.assertEqual(config_mod._float_env("EMACS_EXEC_TIMEOUT", 30.0), 30.0)

    def test_float_env_falls_back_when_timeout_is_not_positive_and_finite(self) -> None:
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"EMACS_EXEC_TIMEOUT": value}):
                    self.assertEqual(config_mod._float_env("EMACS_EXEC_TIMEOUT", 30.0), 30.0)

    def test_config_from_env_does_not_crash_on_malformed_timeout(self) -> None:
        with mock.patch.dict(os.environ, {"EMACS_EXEC_TIMEOUT": "nonsense"}):
            cfg = config_from_env()
        self.assertEqual(cfg.timeout, 30.0)

    def test_allowed_commands_strips_comma_separated_env_values(self) -> None:
        self.assertEqual(
            config_mod._allowed_commands(" org-capture,save-some-buffers, , org-agenda "),
            {"org-capture", "save-some-buffers", "org-agenda"},
        )

    def test_default_prefix_is_emacs(self) -> None:
        cfg = make_config()
        self.assertEqual(cfg.names("ping"), "emacs_ping")
        self.assertEqual(cfg.names("apply_patch"), "emacs_apply_patch")

    def test_empty_prefix_yields_bare_names(self) -> None:
        cfg = make_config(tool_prefix="")
        self.assertEqual(cfg.names("ping"), "ping")
        self.assertEqual(cfg.names("execute_code"), "execute_code")

    def test_config_from_env_defaults(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("EMACS_")}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = config_from_env()
        self.assertEqual(cfg.tool_prefix, "emacs")
        self.assertEqual(cfg.exec_tool_name, "emacs_execute_code")
        self.assertEqual(cfg.url, "http://127.0.0.1:8237")
        self.assertEqual(cfg.allowed_commands, frozenset())

    def test_config_from_env_exec_tool_name_follows_empty_prefix(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("EMACS_")}
        env["EMACS_MCP_TOOL_PREFIX"] = ""
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = config_from_env()
        self.assertEqual(cfg.exec_tool_name, "execute_code")

    def test_config_from_env_exec_tool_name_override(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("EMACS_")}
        env["EMACS_EXEC_TOOL_NAME"] = "secondary_emacs_execute_code"
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = config_from_env()
        self.assertEqual(cfg.exec_tool_name, "secondary_emacs_execute_code")


class RedactionTest(unittest.TestCase):
    def test_safe_redacts_secret_keys(self) -> None:
        data = {
            "token": "plain-value",
            "message": "ordinary text",
            "nested": [{"api_key": "local-secret"}],
        }

        self.assertEqual(
            redaction.safe(data),
            {
                "token": "[REDACTED]",
                "message": "ordinary text",
                "nested": [{"api_key": "[REDACTED]"}],
            },
        )

    def test_redact_string_masks_token_like_values(self) -> None:
        token = "sk-" + ("a" * 20)
        self.assertEqual(redaction.redact_string(f"leaked {token}"), "leaked [REDACTED]")

    def test_redact_string_does_not_redact_bare_forty_hex(self) -> None:
        token = "a" * 40
        self.assertEqual(redaction.redact_string(f"token {token}"), f"token {token}")

    def test_redact_string_does_not_redact_git_commit_sha(self) -> None:
        sha = "5c3213ea1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e"
        self.assertEqual(redaction.redact_string(f"commit {sha}"), f"commit {sha}")

    def test_redact_string_redacts_context_gated_forty_hex_token(self) -> None:
        hex_token = "a" * 40
        result = redaction.redact_string(f"token={hex_token}")
        self.assertIn("token=", result)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn(hex_token, result)

    def test_redact_string_redacts_context_gated_hex_with_colon(self) -> None:
        hex_token = "b" * 40
        result = redaction.redact_string(f"secret: {hex_token}")
        self.assertIn("secret:", result)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn(hex_token, result)

    def test_redaction_positive_corpus(self) -> None:
        positives = [
            "Bearer abcDEF123._~+/=-token",
            "sk-" + ("z" * 20),
            "ghp_" + ("A" * 20),
            "gho_" + ("B" * 20),
            "ghs_" + ("C" * 20),
            "ghr_" + ("D" * 20),
            "ghu_" + ("E" * 20),
            "eyJhbGciOi.eyJzdWIiOiI.SflKxwRJSMeKKF2",
            "token=" + ("a" * 40),
            "secret: " + ("b" * 40),
            "apikey=" + ("c" * 40),
        ]
        for value in positives:
            with self.subTest(value=value):
                result = redaction.redact_string(f"x {value} y")
                self.assertIn("[REDACTED]", result)

    def test_redaction_negative_corpus(self) -> None:
        negatives = [
            "a" * 40,
            "5c3213ea1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e",
            "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            "just an ordinary sentence with no secrets",
            "sk-short",
            "version 1.2.3 build 42",
        ]
        for value in negatives:
            with self.subTest(value=value):
                result = redaction.redact_string(f"context {value} end")
                self.assertNotIn("[REDACTED]", result)
                self.assertEqual(result, f"context {value} end")

    def test_safe_leaves_non_string_scalars_untouched(self) -> None:
        self.assertEqual(redaction.safe(42), 42)
        self.assertEqual(redaction.safe(True), True)
        self.assertIsNone(redaction.safe(None))


class EvalClientTest(unittest.TestCase):
    def _client(self) -> EvalClient:
        return EvalClient("http://127.0.0.1:8237", 30.0, "")

    def test_headers_omit_authorization_without_token(self) -> None:
        client = self._client()
        self.assertNotIn("Authorization", client._headers())

    def test_headers_include_bearer_with_token(self) -> None:
        client = EvalClient("http://127.0.0.1:8237", 30.0, "s3cret")
        self.assertEqual(client._headers()["Authorization"], "Bearer s3cret")

    def test_request_reports_non_json_response_with_redacted_body(self) -> None:
        secret = "sk-" + ("b" * 20)
        dummy = _DummyClient(
            _DummyResponse(
                status_code=502,
                json_data=ValueError("not json"),
                text=f"upstream leaked {secret}",
            )
        )
        with mock.patch.object(eval_client_mod.httpx, "Client", return_value=dummy):
            result = self._client().request("(+ 1 2)")

        self.assertFalse(result["ok"])
        self.assertIn("status 502", result["error"])
        self.assertIn("[REDACTED]", result["error"])
        self.assertNotIn(secret, result["error"])

    def test_request_reports_non_2xx_json_with_redaction(self) -> None:
        dummy = _DummyClient(
            _DummyResponse(
                status_code=403,
                json_data={"ok": False, "error": "denied", "authorization": "local-secret"},
            )
        )
        with mock.patch.object(eval_client_mod.httpx, "Client", return_value=dummy):
            result = self._client().request("(+ 1 2)")

        self.assertEqual(result["status_code"], 403)
        self.assertEqual(result["error"]["authorization"], "[REDACTED]")

    def test_request_logs_errors_without_elisp_payload(self) -> None:
        secret = "sk-" + ("c" * 20)
        elisp = '(message "private user data")'
        with mock.patch.object(
            eval_client_mod.httpx, "Client",
            side_effect=eval_client_mod.httpx.ConnectError(f"boom {secret}"),
        ):
            with self.assertLogs("hermes-emacs-plugin", level="ERROR") as logs:
                result = self._client().request(elisp)

        joined = "\n".join(logs.output)
        self.assertFalse(result["ok"])
        self.assertIn("emacs request failed", joined)
        self.assertIn("[REDACTED]", joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn(elisp, joined)
        self.assertNotIn("private user data", joined)

    def test_request_returns_errors_for_invalid_timeouts(self) -> None:
        for timeout in (float("nan"), float("inf"), -1.0):
            with self.subTest(timeout=timeout):
                client = EvalClient("http://127.0.0.1:8237", timeout, "")
                self.assertEqual(client.timeout, config_mod.DEFAULT_TIMEOUT)

    def test_request_returns_parser_and_timeout_errors(self) -> None:
        for error in (ValueError("invalid endpoint"), OverflowError("invalid timeout")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(eval_client_mod.httpx, "Client", side_effect=error):
                    result = self._client().request("(+ 1 2)")
                self.assertFalse(result["ok"])
                self.assertIn("Could not reach Emacs", result["error"])

    def test_request_returns_invalid_url_error(self) -> None:
        result = EvalClient("http://[::1", 1.0, "").request("(+ 1 2)")
        self.assertFalse(result["ok"])
        self.assertIn("Could not reach Emacs", result["error"])

    def test_eval_json_parses_json_result_string(self) -> None:
        client = self._client()
        with mock.patch.object(
            client, "request", return_value={"ok": True, "result": json.dumps({"pong": True})}
        ):
            result = client.eval_json("((pong . t))")
        self.assertEqual(result, {"ok": True, "result": {"pong": True}})

    def test_eval_json_double_decodes_json_string(self) -> None:
        client = self._client()
        inner = json.dumps(json.dumps({"pong": True}))
        with mock.patch.object(client, "request", return_value={"ok": True, "result": inner}):
            result = client.eval_json("((pong . t))")
        self.assertEqual(result, {"ok": True, "result": {"pong": True}})

    def test_eval_json_preserves_request_errors(self) -> None:
        client = self._client()
        error = {"ok": False, "error": "Could not reach Emacs"}
        with mock.patch.object(client, "request", return_value=error):
            self.assertEqual(client.eval_json("t"), error)

    def test_eval_json_wraps_form_in_json_encode(self) -> None:
        client = self._client()
        with mock.patch.object(client, "request", return_value={"ok": True, "result": "1"}) as req:
            client.eval_json("(foo)")
        req.assert_called_once_with("(progn (require 'json) (json-encode (foo)))")


class _DummyResponse:
    def __init__(self, *, status_code: int, json_data=None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if isinstance(self._json_data, BaseException):
            raise self._json_data
        return self._json_data


class _DummyClient:
    def __init__(self, response: _DummyResponse) -> None:
        self.response = response
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers, json):
        self.requests.append((url, headers, json))
        return self.response


class ToolRegistrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.mcp = build_with_fake(self.client)

    def test_namespaced_tools_are_registered_for_mcp_discovery(self) -> None:
        expected = {
            "emacs_execute_code",
            "emacs_ping",
            "emacs_current_buffer",
            "emacs_current_context",
            "emacs_region_text",
            "emacs_project_info",
            "emacs_apply_patch",
            "emacs_list_buffers",
            "emacs_buffer_text",
            "emacs_wait_for_buffer",
            "emacs_diff_buffer",
            "emacs_save_buffer",
            "emacs_revert_buffer",
            "emacs_search",
            "emacs_goto_line",
            "emacs_pop_to_buffer",
        }
        for name in expected:
            with self.subTest(name=name):
                self.assertIsNotNone(self.mcp._tool_manager.get_tool(name))

    def test_search_navigation_tools_have_read_only_annotations(self) -> None:
        for name in ("emacs_search", "emacs_goto_line", "emacs_pop_to_buffer"):
            with self.subTest(name=name):
                tool = self.mcp._tool_manager.get_tool(name)
                self.assertIsNotNone(tool)
                self.assertTrue(tool.annotations.readOnlyHint)

    def test_file_operation_tools_have_expected_annotations(self) -> None:
        diff_tool = self.mcp._tool_manager.get_tool("emacs_diff_buffer")
        self.assertTrue(diff_tool.annotations.readOnlyHint)
        for name in ("emacs_save_buffer", "emacs_revert_buffer"):
            with self.subTest(name=name):
                tool = self.mcp._tool_manager.get_tool(name)
                self.assertTrue(tool.annotations.destructiveHint)

    def test_execute_code_keeps_raw_elisp_passthrough_behavior(self) -> None:
        client = FakeClient(result={"ok": True, "result": "3"})
        mcp = build_with_fake(client)
        result = tool_func(mcp, "emacs_execute_code")("(+ 1 2)")
        self.assertEqual(result, {"ok": True, "result": "3"})
        self.assertEqual(client.raw, ["(+ 1 2)"])

    def test_tool_name_can_be_overridden_for_multiple_emacs_instances(self) -> None:
        script = INTROSPECT + (
            "tool = mcp._tool_manager.get_tool('secondary_emacs_execute_code');"
            "print(tool.name if tool else 'missing')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=child_env(EMACS_EXEC_TOOL_NAME="secondary_emacs_execute_code"),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "secondary_emacs_execute_code")

    def test_tool_prefix_can_be_disabled_when_mcp_server_name_is_descriptive(self) -> None:
        script = INTROSPECT + (
            "names = ['execute_code', 'ping', 'current_buffer', 'current_context', "
            "'region_text', 'project_info', 'apply_patch', 'list_buffers', "
            "'buffer_text', 'wait_for_buffer', 'diff_buffer', 'save_buffer', "
            "'revert_buffer', 'search', 'goto_line', 'pop_to_buffer'];"
            "print('\\n'.join(name for name in names "
            "if mcp._tool_manager.get_tool(name)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=child_env(EMACS_MCP_TOOL_PREFIX=""),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(
            result.stdout.strip().splitlines(),
            [
                "execute_code",
                "ping",
                "current_buffer",
                "current_context",
                "region_text",
                "project_info",
                "apply_patch",
                "list_buffers",
                "buffer_text",
                "wait_for_buffer",
                "diff_buffer",
                "save_buffer",
                "revert_buffer",
                "search",
                "goto_line",
                "pop_to_buffer",
            ],
        )

    def test_call_interactive_is_registered_only_when_allowlist_is_configured(self) -> None:
        script = INTROSPECT + (
            "tool = mcp._tool_manager.get_tool('emacs_call_interactive');"
            "print(tool.name if tool else '')"
        )
        disabled = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=child_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        empty = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=child_env(EMACS_ALLOWED_COMMANDS=" , "),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        enabled = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=child_env(EMACS_ALLOWED_COMMANDS="org-agenda"),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(disabled.stdout.strip(), "")
        self.assertEqual(empty.stdout.strip(), "")
        self.assertEqual(enabled.stdout.strip(), "emacs_call_interactive")

    def test_call_interactive_tool_has_destructive_annotation(self) -> None:
        client = FakeClient()
        mcp = build_with_fake(client, allowed_commands=frozenset({"org-agenda"}))
        tool = mcp._tool_manager.get_tool("emacs_call_interactive")
        self.assertIsNotNone(tool)
        self.assertTrue(tool.annotations.destructiveHint)

    def test_core_tools_expose_expected_annotations(self) -> None:
        expected = {
            "emacs_execute_code": ("destructiveHint", True),
            "emacs_ping": ("readOnlyHint", True),
            "emacs_current_buffer": ("readOnlyHint", True),
            "emacs_current_context": ("readOnlyHint", True),
            "emacs_region_text": ("readOnlyHint", True),
            "emacs_project_info": ("readOnlyHint", True),
            "emacs_apply_patch": ("destructiveHint", True),
            "emacs_list_buffers": ("readOnlyHint", True),
            "emacs_buffer_text": ("readOnlyHint", True),
            "emacs_wait_for_buffer": ("readOnlyHint", True),
            "emacs_search": ("readOnlyHint", True),
            "emacs_goto_line": ("readOnlyHint", True),
            "emacs_pop_to_buffer": ("readOnlyHint", True),
        }
        for name, (field, value) in expected.items():
            with self.subTest(name=name):
                tool = self.mcp._tool_manager.get_tool(name)
                self.assertIsNotNone(tool)
                self.assertEqual(getattr(tool.annotations, field), value)

    def test_resources_are_registered_for_mcp_discovery(self) -> None:
        resources = asyncio.run(self.mcp.list_resources())
        uris = {str(resource.uri) for resource in resources}
        self.assertIn("emacs://buffers", uris)
        self.assertIn("emacs://project", uris)

    def test_resources_read_through_client(self) -> None:
        client = FakeClient(result={"ok": True, "result": [{"buffer_name": "*scratch*"}]})
        mcp = build_with_fake(client)
        contents = list(asyncio.run(mcp.read_resource("emacs://buffers")))
        self.assertEqual(
            json.loads(contents[0].content),
            {"ok": True, "result": [{"buffer_name": "*scratch*"}]},
        )
        self.assertEqual(len(client.forms), 1)

        client2 = FakeClient(result={"ok": True, "result": {"project": {"name": "demo"}}})
        mcp2 = build_with_fake(client2)
        contents = list(asyncio.run(mcp2.read_resource("emacs://project")))
        self.assertEqual(
            json.loads(contents[0].content),
            {"ok": True, "result": {"project": {"name": "demo"}}},
        )
        self.assertEqual(len(client2.forms), 1)


class ToolCommandConstructionTest(unittest.TestCase):
    def assertElispParensBalanced(self, form: str) -> None:
        in_string = False
        escaped = False
        depth = 0
        for char in form:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                self.assertGreaterEqual(depth, 0, form)
        self.assertFalse(in_string, form)
        self.assertEqual(depth, 0, form)

    def test_current_buffer_constructs_limited_buffer_read(self) -> None:
        form = context.current_buffer_form(123)
        self.assertIn("buffer-substring-no-properties", form)
        self.assertIn("max-chars 123", form)
        self.assertIn("truncated", form)

    def test_current_buffer_clamps_oversized_max_chars(self) -> None:
        client = FakeClient()
        mcp = build_with_fake(client)
        tool_func(mcp, "emacs_current_buffer")(max_chars=999999999)
        self.assertIn(f"max-chars {config_mod.MAX_BUFFER_CHARS}", client.forms[0])

    def test_buffer_text_escapes_buffer_name_as_elisp_string(self) -> None:
        name = 'notes "quoted"\\buffer\nline'
        form = buffers.buffer_text_form(name, 77)
        self.assertIn(elisp_string(name), form)
        self.assertIn("get-buffer", form)
        self.assertIn("max-chars 77", form)

    def test_wait_for_buffer_constructs_bounded_wait_loop(self) -> None:
        form = buffers.wait_for_buffer_form("*compilation*", 2.5)
        self.assertIn(elisp_string("*compilation*"), form)
        self.assertIn("accept-process-output", form)
        self.assertIn("2.5", form)

    def test_wait_for_buffer_clamps_oversized_timeout(self) -> None:
        form = buffers.wait_for_buffer_form("*compilation*", 999)
        self.assertIn(f"timeout {config_mod.MAX_WAIT_SECONDS:.1f}", form)

    def test_apply_patch_constructs_literal_replacement_form(self) -> None:
        form = edit.apply_patch_form("/tmp/example.txt", 'old "value"', "new\\value")
        self.assertIn("find-buffer-visiting", form)
        self.assertIn("buffer-modified-p", form)
        self.assertIn("buffer has unsaved changes", form)
        self.assertIn("verify-visited-file-modtime", form)
        self.assertIn("revert-buffer :ignore-auto :noconfirm", form)
        self.assertIn("find-file-noselect", form)
        self.assertIn(elisp_string("/tmp/example.txt"), form)
        self.assertIn(elisp_string('old "value"'), form)
        self.assertIn(elisp_string("new\\value"), form)
        self.assertIn("old text appears more than once", form)
        self.assertIn("(1+ occurrence-start)", form)
        self.assertIn("delete-region", form)
        self.assertIn("insert new", form)
        self.assertElispParensBalanced(form)

    def test_call_interactive_constructs_auto_confirmed_command_form(self) -> None:
        form = edit.call_interactive_form("org-agenda")
        self.assertIn("cl-letf", form)
        self.assertIn("(symbol-function 'y-or-n-p)", form)
        self.assertIn("(symbol-function 'yes-or-no-p)", form)
        self.assertIn("condition-case", form)
        self.assertIn("call-interactively", form)
        self.assertIn("(quote org-agenda)", form)
        self.assertIn(elisp_string("org-agenda"), form)
        self.assertElispParensBalanced(form)

    def test_search_constructs_search_forward_form(self) -> None:
        pattern = 'def "foo"'
        form = buffers.search_form(pattern, None, 50)
        self.assertIn("search-forward", form)
        self.assertIn(elisp_string(pattern), form)
        self.assertIn("max-results", form)
        self.assertElispParensBalanced(form)

    def test_search_with_buffer_uses_get_buffer(self) -> None:
        buffer = 'notes "quoted"\\buffer'
        form = buffers.search_form("hello", buffer, 50)
        self.assertIn("get-buffer", form)
        self.assertIn(elisp_string(buffer), form)
        self.assertElispParensBalanced(form)

    def test_search_clamps_oversized_max_results(self) -> None:
        client = FakeClient()
        mcp = build_with_fake(client)
        tool_func(mcp, "emacs_search")(pattern="hello", max_results=999999)
        self.assertIn(f"max-results {config_mod.MAX_SEARCH_RESULTS}", client.forms[0])

    def test_goto_line_constructs_forward_line_form(self) -> None:
        form = buffers.goto_line_form(42, None)
        self.assertIn("forward-line", form)
        self.assertIn("goto-char", form)
        self.assertIn("current-column", form)
        self.assertElispParensBalanced(form)

    def test_goto_line_with_buffer_escapes_name(self) -> None:
        buffer = '*scratch* "test"'
        form = buffers.goto_line_form(10, buffer)
        self.assertIn("get-buffer", form)
        self.assertIn(elisp_string(buffer), form)
        self.assertElispParensBalanced(form)

    def test_pop_to_buffer_constructs_pop_to_buffer_form(self) -> None:
        name = 'notes "quoted"\\buffer'
        form = buffers.pop_to_buffer_form(name)
        self.assertIn("pop-to-buffer", form)
        self.assertIn("get-buffer", form)
        self.assertIn(elisp_string(name), form)
        self.assertElispParensBalanced(form)

    def test_diff_buffer_constructs_diff_form(self) -> None:
        form = buffers.diff_buffer_form("test.py")
        self.assertIn("diff-buffer-with-file", form)
        self.assertIn("buffer-file-name", form)
        self.assertIn("*Diff*", form)
        self.assertIn(str(config_mod.MAX_BUFFER_CHARS), form)
        self.assertIn("buffer has no associated file", form)
        self.assertElispParensBalanced(form)

    def test_diff_buffer_uses_current_buffer_when_buffer_is_none(self) -> None:
        form = buffers.diff_buffer_form(None)
        self.assertIn("(current-buffer)", form)
        self.assertNotIn('(get-buffer "")', form)
        self.assertElispParensBalanced(form)

    def test_save_buffer_constructs_save_form(self) -> None:
        form = buffers.save_buffer_form("test.py")
        self.assertIn("save-buffer", form)
        self.assertIn("buffer-file-name", form)
        self.assertIn("buffer has no associated file", form)
        self.assertElispParensBalanced(form)

    def test_revert_buffer_constructs_revert_form(self) -> None:
        form = buffers.revert_buffer_form("test.py")
        self.assertIn("revert-buffer", form)
        self.assertIn(":ignore-auto", form)
        self.assertIn(":noconfirm", form)
        self.assertIn("buffer has no associated file", form)
        self.assertElispParensBalanced(form)


class ToolSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeClient()
        self.mcp = build_with_fake(self.client)
        self.commands_mcp = build_with_fake(self.client, allowed_commands=frozenset({"org-agenda"}))

    def test_apply_patch_rejects_empty_old_text_without_calling_emacs(self) -> None:
        result = tool_func(self.mcp, "emacs_apply_patch")(file="/tmp/example", old="", new="replacement")
        self.assertEqual(result["ok"], False)
        self.assertIn("non-empty", result["error"])
        self.assertEqual(self.client.forms, [])

    def test_apply_patch_rejects_missing_file_without_calling_emacs(self) -> None:
        result = tool_func(self.mcp, "emacs_apply_patch")(file="", old="old", new="new")
        self.assertEqual(result["ok"], False)
        self.assertIn("file", result["error"])
        self.assertEqual(self.client.forms, [])

    def test_file_operation_tools_resolve_empty_buffer_name_to_current_buffer(self) -> None:
        for name in ("emacs_diff_buffer", "emacs_save_buffer", "emacs_revert_buffer"):
            with self.subTest(name=name):
                client = FakeClient()
                mcp = build_with_fake(client)
                tool_func(mcp, name)(buffer="")
                self.assertIn("(current-buffer)", client.forms[0])
                self.assertNotIn('(get-buffer "")', client.forms[0])

    def test_search_rejects_empty_pattern_without_calling_emacs(self) -> None:
        result = tool_func(self.mcp, "emacs_search")(pattern="")
        self.assertEqual(result["ok"], False)
        self.assertIn("pattern", result["error"])
        self.assertEqual(self.client.forms, [])

    def test_goto_line_rejects_line_below_one_without_calling_emacs(self) -> None:
        result = tool_func(self.mcp, "emacs_goto_line")(line=0)
        self.assertEqual(result["ok"], False)
        self.assertIn("line", result["error"])
        self.assertEqual(self.client.forms, [])

    def test_pop_to_buffer_rejects_empty_name_without_calling_emacs(self) -> None:
        result = tool_func(self.mcp, "emacs_pop_to_buffer")(name="")
        self.assertEqual(result["ok"], False)
        self.assertIn("name", result["error"])
        self.assertEqual(self.client.forms, [])

    def test_call_interactive_rejects_non_allowlisted_command_without_calling_emacs(self) -> None:
        result = tool_func(self.commands_mcp, "emacs_call_interactive")(command="save-buffers-kill-emacs")
        self.assertEqual(result, {"ok": False, "error": "command not in allowlist"})
        self.assertEqual(self.client.forms, [])

    def test_call_interactive_rejects_invalid_symbol_name_without_calling_emacs(self) -> None:
        command = '(delete-file "~/.emacs")'
        mcp = build_with_fake(self.client, allowed_commands=frozenset({command}))
        result = tool_func(mcp, "emacs_call_interactive")(command=command)
        self.assertEqual(result["ok"], False)
        self.assertIn("invalid command", result["error"])
        self.assertEqual(self.client.forms, [])


class ReadmeInstallCommandTest(unittest.TestCase):
    def test_mcp_add_example_places_env_before_args_passthrough(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        match = re.search(r"hermes mcp add emacs[^\n]+", readme)
        self.assertIsNotNone(match, "README should include a hermes mcp add example")
        command = match.group(0)
        self.assertIn("--env", command)
        self.assertIn("--args", command)
        self.assertLess(command.index("--env"), command.index("--args"))


class TransportSecurityTest(unittest.TestCase):
    def test_loopback_hosts_recognized(self) -> None:
        for url in (
            "http://127.0.0.1:8237",
            "http://localhost:8237",
            "http://[::1]:8237",
            "http://LOCALHOST:8237",
        ):
            self.assertTrue(config_mod.is_loopback(url), url)

    def test_non_loopback_hosts_rejected(self) -> None:
        for url in (
            "http://192.0.2.10:8237",
            "http://198.51.100.20:8237",
            "http://emacs.example.org:8237",
        ):
            self.assertFalse(config_mod.is_loopback(url), url)

    def test_url_without_host_is_not_loopback(self) -> None:
        self.assertFalse(config_mod.is_loopback("not-a-url"))

    def test_malformed_urls_are_not_loopback(self) -> None:
        for url in (
            "http://[::1",
            "http://127.0.0.1:abc",
            "http://127.0.0.1:0",
            "http://127.0.0.1:99999",
            "http://user:secret@127.0.0.1:8237",
            "ftp://127.0.0.1:8237",
            "http://127.0.0.1:8237/path",
            "http://127.0.0.1:8237?query=yes",
            "http://127.0.0.1:8237#fragment",
        ):
            with self.subTest(url=url):
                self.assertFalse(config_mod.is_loopback(url))

    def test_malformed_urls_fail_closed_even_with_token(self) -> None:
        for url in (
            "http://[::1",
            "http://127.0.0.1:abc",
            "http://127.0.0.1:0",
            "http://127.0.0.1:99999",
            "http://user:secret@127.0.0.1:8237",
            "ftp://127.0.0.1:8237",
            "http://127.0.0.1:8237/path",
            "http://127.0.0.1:8237?query=yes",
            "http://127.0.0.1:8237#fragment",
        ):
            with self.subTest(url=url):
                error = config_mod.require_secure_transport(url, "shared-token")
                self.assertEqual(
                    error,
                    "refusing to start: EMACS_EXEC_URL is not a valid HTTP endpoint",
                )
                self.assertIsNotNone(error)
                self.assertNotIn("shared-token", error)

    def test_main_exits_on_malformed_transport_even_with_token(self) -> None:
        from hermes_emacs_plugin import server as server_mod

        with mock.patch.object(server_mod, "config_from_env", return_value=make_config(
            url="http://[::1", token="shared-token"
        )):
            with mock.patch.object(server_mod, "load_env"):
                with mock.patch.object(server_mod, "build_server") as build:
                    with self.assertRaises(SystemExit):
                        server_mod.main()
                    build.assert_not_called()

    def test_loopback_without_token_is_allowed(self) -> None:
        self.assertIsNone(config_mod.require_secure_transport("http://127.0.0.1:8237", ""))

    def test_loopback_with_token_is_allowed(self) -> None:
        self.assertIsNone(config_mod.require_secure_transport("http://127.0.0.1:8237", "s3cret"))

    def test_non_loopback_with_token_is_allowed(self) -> None:
        self.assertIsNone(config_mod.require_secure_transport("http://192.0.2.10:8237", "s3cret"))

    def test_non_loopback_without_token_is_refused(self) -> None:
        error = config_mod.require_secure_transport("http://192.0.2.10:8237", "")
        self.assertIsNotNone(error)
        self.assertIn("refusing to start", error)

    def test_main_exits_on_insecure_transport(self) -> None:
        from hermes_emacs_plugin import server as server_mod

        with mock.patch.object(server_mod, "config_from_env", return_value=make_config(
            url="http://192.0.2.10:8237", token=""
        )):
            with mock.patch.object(server_mod, "load_env"):
                with mock.patch.object(server_mod, "build_server") as build:
                    with self.assertRaises(SystemExit):
                        server_mod.main()
                    build.assert_not_called()

    def test_main_starts_server_on_secure_transport(self) -> None:
        from hermes_emacs_plugin import server as server_mod

        fake_mcp = mock.Mock()
        with mock.patch.object(server_mod, "config_from_env", return_value=make_config()):
            with mock.patch.object(server_mod, "load_env"):
                with mock.patch.object(server_mod, "build_server", return_value=fake_mcp) as build:
                    server_mod.main()
        build.assert_called_once()
        fake_mcp.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
