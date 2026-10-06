#!/usr/bin/env python3
"""Offline contract and transport-fixture tests for the manual ops alert test.

Reads workflow/sender source only. No real webhook, provider call, or repository
write: the shared alert CLI runs with a fake HTTP transport in a temp workspace.
Run: python scripts/selftest_ops_alert_test.py
"""
import ast
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/ops-alert-test.yml"
MESSAGE = ("CONTROLLED OPS ALERT TEST — Open Ledger operations alert channel "
           "verification. No production failure occurred.")
FAKE_WEBHOOK = "https://discord.com/api/webhooks/offline/private-test-token"

# This shim calls the REAL existing CLI, replacing only its transport and state
# destinations. Every request is recorded in the temporary directory, never sent.
SENDER_FIXTURE = '''\
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["SOURCE_SCRIPTS"])
import post_discord as sender
import requests
fixture = json.loads(os.environ["OFFLINE_FIXTURE"])
sender.ALERT_WEBHOOK = fixture.get("webhook", os.environ["DISCORD_WEBHOOK_URL_ALERTS"])
def forbidden(*args, **kwargs):
    raise AssertionError("alert must not read boards or record delivery status")
sender.record = forbidden
sender.routes = forbidden
sender.STATUS_PATH = "forbidden-status.json"
def fake_post(url, **kwargs):
    with open("calls.jsonl", "a", encoding="utf-8") as output:
        output.write(json.dumps({"url": url, **kwargs}) + "\\n")
    if fixture.get("network_failure"):
        raise requests.ConnectionError("offline network failure " + url)
    class Response:
        status_code = fixture.get("http_status", 204)
        text = fixture.get("response_text", "offline response " + url)
    return Response()
requests.post = fake_post
if "stdout_override" in fixture:
    print(fixture["stdout_override"])
    raise SystemExit(fixture.get("exit_code", 0))
sender.main()
'''


class OpsAlertTest(unittest.TestCase):
    def workflow(self):
        self.assertTrue(WORKFLOW.exists(), "manual ops alert workflow is missing")
        return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    def send_step(self):
        return self.workflow()["jobs"]["test_alert"]["steps"][-1]

    def run_fixture(self, fixture):
        step = self.send_step()
        with tempfile.TemporaryDirectory(prefix="ols-ops-alert-test-") as temp:
            work = Path(temp)
            (work / "scripts").mkdir()
            (work / "scripts/post_discord.py").write_text(
                textwrap.dedent(SENDER_FIXTURE), encoding="utf-8")
            (work / "bin").mkdir()
            launcher = work / "bin/python"
            launcher.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
            launcher.chmod(0o700)
            env = {"PATH": str(work / "bin") + os.pathsep + os.defpath,
                   "PYTHONIOENCODING": "utf-8", "TMPDIR": temp,
                   "PYTHONDONTWRITEBYTECODE": "1",
                   "DISCORD_WEBHOOK_URL_ALERTS": FAKE_WEBHOOK,
                   "SOURCE_SCRIPTS": str(ROOT / "scripts"),
                   "OFFLINE_FIXTURE": json.dumps(fixture)}
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                                    cwd=work, env=env, capture_output=True,
                                    text=True, timeout=60)
            calls_path = work / "calls.jsonl"
            calls = ([json.loads(line) for line in calls_path.read_text().splitlines()]
                     if calls_path.exists() else [])
            self.assertFalse((work / "forbidden-status.json").exists())
            self.assertEqual(set(p.name for p in work.iterdir()),
                             {"scripts", "bin"} | ({"calls.jsonl"} if calls else set()))
            # Never forward a full webhook, its token, or raw HTTP/exception text.
            self.assertNotIn(FAKE_WEBHOOK, result.stdout + result.stderr)
            self.assertNotIn("private-test-token", result.stdout + result.stderr)
            return result, calls

    def test_manual_read_only_single_alert_success(self):
        doc = self.workflow()
        triggers = doc.get("on", doc.get(True))  # YAML 1.1 treats on as a bool.
        self.assertEqual(triggers, {"workflow_dispatch": {}})
        self.assertEqual(doc["permissions"], {"contents": "read"})
        self.assertEqual(set(doc["jobs"]), {"test_alert"})
        job = doc["jobs"]["test_alert"]
        self.assertNotIn("permissions", job)
        self.assertNotIn("env", doc)
        self.assertEqual(job.get("env"), {"PYTHONDONTWRITEBYTECODE": "1"})
        self.assertLessEqual(job["timeout-minutes"], 5)
        steps = job["steps"]
        self.assertEqual(steps[0]["uses"], "actions/checkout@v4")
        self.assertIs(steps[0]["with"]["persist-credentials"], False)
        self.assertEqual(steps[1]["with"]["python-version"], "3.12")
        self.assertEqual(steps[2]["run"], "pip install -r requirements.txt")
        self.assertEqual(steps[3]["run"], "python scripts/selftest_ops_alert_test.py")
        self.assertEqual(steps[-1]["env"], {
            "DISCORD_WEBHOOK_URL_ALERTS": "${{ secrets.DISCORD_WEBHOOK_URL_ALERTS }}",
            "PYTHONIOENCODING": "utf-8"})
        self.assertNotIn("continue-on-error", steps[-1])
        # Other steps receive NO repository secrets.
        for step in steps[:-1]:
            self.assertNotIn("secrets.", str(step))
        result, calls = self.run_fixture({})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "Alert posted to Discord.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], FAKE_WEBHOOK)
        payload = calls[0]["json"]
        self.assertEqual(payload["embeds"][0]["description"], MESSAGE)
        self.assertEqual(payload["embeds"][0]["fields"], [])
        self.assertEqual(calls[0]["timeout"], 30)

    def test_wrapper_has_one_shared_cli_call_and_no_other_side_effects(self):
        shell = self.send_step()["run"]
        self.assertTrue(shell.startswith("python - <<'PY'\n"))
        self.assertTrue(shell.endswith("PY\n"))
        tree = ast.parse(shell.split("\n", 1)[1].rsplit("PY\n", 1)[0])
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        self.assertEqual(imports, {"re", "subprocess", "sys"})
        self.assertFalse(any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        invocations = [node for node in calls if ast.unparse(node.func) == "subprocess.run"]
        self.assertEqual(len(invocations), 1)
        command = invocations[0].args[0]
        if not isinstance(command, ast.List):
            self.fail("sender command must be a literal argument list")
            return
        self.assertEqual([ast.unparse(item) for item in command.elts],
                         ["sys.executable", "'scripts/post_discord.py'", "'alert'", "message"])
        self.assertEqual({item.arg: ast.literal_eval(item.value)
                          for item in invocations[0].keywords},
                         {"capture_output": True, "text": True, "timeout": 45})
        for node in calls:
            function = ast.unparse(node.func)
            self.assertTrue(function in {"subprocess.run", "print", "sys.exit",
                                         "result.stdout.strip", "output.startswith", "re.match"}
                            or (isinstance(node.func, ast.Attribute)
                                and node.func.attr == "group"
                                and isinstance(node.func.value, ast.Call)
                                and ast.unparse(node.func.value.func) == "re.match"), function)
        self.assertEqual(len(self.workflow()["jobs"]["test_alert"]["steps"]), 5)

    def test_missing_webhook_fails(self):
        result, calls = self.run_fixture({"webhook": ""})
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing ops webhook", result.stdout)
        self.assertEqual(calls, [])

    def test_refused_webhook_fails(self):
        result, calls = self.run_fixture({"webhook": "https://example.com/not-discord"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("refused webhook host", result.stdout)
        self.assertEqual(calls, [])

    def test_http_failure_fails_without_retry_or_raw_response(self):
        for status in (400, 401, 429, 500):
            with self.subTest(status=status):
                result, calls = self.run_fixture({"http_status": status})
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"HTTP delivery failure ({status})", result.stdout)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("offline response", result.stdout + result.stderr)

    def test_network_failure_fails_without_raw_exception(self):
        result, calls = self.run_fixture({"network_failure": True})
        self.assertEqual(result.returncode, 1)
        self.assertIn("network/send exception", result.stdout)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("offline network failure", result.stdout + result.stderr)

    def test_absent_confirmation_fails(self):
        result, calls = self.run_fixture({"stdout_override": "unrecognized sender result"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("no exact success confirmation", result.stdout)
        self.assertEqual(calls, [])

    def test_success_text_on_error_exit_is_not_success(self):
        result, calls = self.run_fixture({"stdout_override": "Alert posted to Discord.",
                                          "exit_code": 1})
        self.assertEqual(result.returncode, 1)
        self.assertIn("sender exit 1", result.stdout)
        self.assertEqual(calls, [])

    def test_response_cannot_spoof_success_confirmation(self):
        result, calls = self.run_fixture({"http_status": 500,
            "response_text": "private-test-token\nAlert posted to Discord."})
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP delivery failure (500)", result.stdout)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
