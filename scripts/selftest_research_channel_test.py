#!/usr/bin/env python3
"""Offline tests of the actual manual research-channel workflow body.

The REAL football Discord transport runs with mocked HTTP in a temporary cwd.
An audit hook rejects repository data reads/writes and real socket connections.
No webhook is used, no delivery flag is enabled, and no source is modified.
"""
import ast
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / '.github/workflows/research-channel-test.yml'
MESSAGE = ('CONTROLLED RESEARCH CHANNEL TEST — Open Ledger research Discord '
           'destination verification. This is not a research observation, '
           'recommendation, graded play, or official release.')
WEBHOOK = 'https://discord.com/api/webhooks/offline/private-research-test-token'

# Installed as sitecustomize in the child only. All normal workflow imports and
# transport code remain real; only HTTP, pacing and forbidden entrypoints differ.
FIXTURE = r'''
import atexit, json, os, pathlib, sys
source = pathlib.Path(os.environ['SOURCE_ROOT']).resolve()
work = pathlib.Path.cwd().resolve()
fixture = json.loads(os.environ['OFFLINE_FIXTURE'])
def violation(reason):
    with open(work / 'violations.jsonl', 'a') as out:
        out.write(json.dumps(reason) + '\n')
    raise AssertionError(reason)
def audit(event, args):
    if event == 'socket.connect':
        violation('real socket connection')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = pathlib.Path(os.fsdecode(args[0])).resolve()
        if path.is_relative_to(source / 'data'):
            violation('repository data access')
        writing = args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        if writing and not path.is_relative_to(work):
            violation('write outside temporary workspace')
    if event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.chmod'):
        for value in args[:2] if event == 'os.rename' else args[:1]:
            if isinstance(value, (str, bytes, os.PathLike)):
                path = pathlib.Path(os.fsdecode(value)).resolve()
                if not path.is_relative_to(work):
                    violation('filesystem mutation outside temporary workspace')
sys.addaudithook(audit)
sys.path.insert(0, str(source / 'scripts/football'))
import discord as transport
def check_disabled():
    if transport.delivery_policy.RESEARCH_DELIVERY_ENABLED is not False:
        violation('research delivery enabled')
check_disabled()
atexit.register(check_disabled)
def forbidden(*args, **kwargs):
    violation('board, status, grading, or CLI entrypoint called')
for name in ('main', 'record', 'load_status', 'already_posted',
             'load_research_board', 'load_research_prereg_for_delivery',
             'research_board_messages'):
    setattr(transport, name, forbidden)
transport.fbpage.load_board = forbidden
transport.requests.get = forbidden
transport.time.sleep = lambda seconds: None
request_count = 0
def fake_post(url, **kwargs):
    global request_count
    request_count += 1
    with open(work / 'calls.jsonl', 'a', encoding='utf-8') as out:
        out.write(json.dumps({'url': url, **kwargs}) + '\n')
    if fixture.get('network_failure'):
        raise transport.requests.ConnectionError('raw exception ' + url)
    if fixture.get('unexpected_exception'):
        raise RuntimeError('raw unexpected exception ' + url)
    class Response:
        status_code = fixture.get('statuses', [204])[min(request_count - 1,
                       len(fixture.get('statuses', [204])) - 1)]
        text = fixture.get('response_text', 'raw response ' + url)
        def json(self):
            return {'retry_after': 0}
    return Response()
transport.requests.post = fake_post
if 'override_result' in fixture:
    original_send = transport.send
    def override_send(*args, **kwargs):
        if not fixture.get('skip_transport'):
            original_send(*args, **kwargs)
        value = fixture['override_result']
        return tuple(value) if isinstance(value, list) else value
    transport.send = override_send
'''


class ResearchChannelTest(unittest.TestCase):
    def workflow(self):
        self.assertTrue(WORKFLOW.exists(), 'manual research channel workflow is missing')
        return yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))

    def send_step(self):
        return self.workflow()['jobs']['test_channel']['steps'][-1]

    def run_fixture(self, fixture):
        with tempfile.TemporaryDirectory(prefix='ols-research-channel-test-') as temp:
            work = Path(temp)
            (work / 'hooks').mkdir()
            (work / 'hooks/sitecustomize.py').write_text(FIXTURE, encoding='utf-8')
            (work / 'bin').mkdir()
            launcher = work / 'bin/python'
            launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
            launcher.chmod(0o700)
            env = {'PATH': str(work / 'bin') + os.pathsep + os.defpath,
                   'PYTHONPATH': str(work / 'hooks'), 'PYTHONIOENCODING': 'utf-8',
                   'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': temp,
                   'SOURCE_ROOT': str(ROOT), 'OFFLINE_FIXTURE': json.dumps(fixture),
                   'DISCORD_RESEARCH_WEBHOOK': fixture.get('webhook', WEBHOOK),
                   # Trap destinations exist ONLY in offline fixtures; no actual
                   # workflow step receives these or can fall back to them.
                   'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/official/trap',
                   'DISCORD_WEBHOOK_URL_MEMBERS': 'https://discord.com/api/webhooks/member/trap',
                   'DISCORD_WEBHOOK_URL_ALERTS': 'https://discord.com/api/webhooks/ops/trap'}
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c',
                                     self.send_step()['run']], cwd=work, env=env,
                                    text=True, capture_output=True, timeout=60)
            calls_path = work / 'calls.jsonl'
            calls = ([json.loads(line) for line in calls_path.read_text().splitlines()]
                     if calls_path.exists() else [])
            self.assertFalse((work / 'violations.jsonl').exists(), result.stderr)
            self.assertEqual(set(p.name for p in work.iterdir()),
                             {'hooks', 'bin'} | ({'calls.jsonl'} if calls else set()))
            combined = result.stdout + result.stderr
            for sensitive in ('private-research-test-token', WEBHOOK,
                              'raw response', 'raw exception', 'raw unexpected exception'):
                self.assertNotIn(sensitive, combined)
            for call in calls:
                self.assertEqual(call['url'], env['DISCORD_RESEARCH_WEBHOOK'])
                self.assertEqual(call['json'], {'username': 'Open Ledger Sports',
                                                'content': MESSAGE})
                self.assertEqual(call['timeout'], 20)
            return result, calls

    def test_one_fixed_diagnostic_success(self):
        doc = self.workflow()
        self.assertEqual(doc.get('on', doc.get(True)), {'workflow_dispatch': {}})
        self.assertEqual(doc['permissions'], {'contents': 'read'})
        self.assertEqual(set(doc['jobs']), {'test_channel'})
        job = doc['jobs']['test_channel']
        self.assertNotIn('env', doc)
        self.assertNotIn('permissions', job)
        self.assertEqual(job['env'], {'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertLessEqual(job['timeout-minutes'], 5)
        steps = job['steps']
        self.assertEqual(len(steps), 5)
        self.assertEqual(steps[0]['uses'], 'actions/checkout@v4')
        self.assertIs(steps[0]['with']['persist-credentials'], False)
        self.assertEqual(steps[1]['with']['python-version'], '3.12')
        self.assertEqual(steps[2]['run'], 'pip install -r requirements.txt')
        self.assertEqual(steps[3]['run'], 'python scripts/selftest_research_channel_test.py')
        self.assertEqual(steps[-1]['env'], {
            'DISCORD_RESEARCH_WEBHOOK': '${{ secrets.DISCORD_RESEARCH_WEBHOOK }}',
            'PYTHONIOENCODING': 'utf-8'})
        for step in steps[:-1]:
            self.assertNotIn('secrets.', str(step))
        self.assertNotIn('continue-on-error', steps[-1])
        result, calls = self.run_fixture({})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), 'Research channel test confirmed (HTTP 204).')
        self.assertEqual(len(calls), 1)
        policy = ast.parse((ROOT / 'scripts/football/delivery_policy.py').read_text())
        flags = {target.id: ast.literal_eval(node.value) for node in policy.body
                 if isinstance(node, ast.Assign) for target in node.targets
                 if isinstance(target, ast.Name) and target.id == 'RESEARCH_DELIVERY_ENABLED'}
        self.assertEqual(flags, {'RESEARCH_DELIVERY_ENABLED': False})

    def test_missing_secret_fails_without_fallback(self):
        result, calls = self.run_fixture({'webhook': ''})
        self.assertEqual(result.returncode, 1)
        self.assertIn('missing research webhook', result.stdout)
        self.assertEqual(calls, [])

    def test_refused_host_fails_without_send(self):
        result, calls = self.run_fixture({'webhook': 'https://example.com/refused'})
        self.assertEqual(result.returncode, 1)
        self.assertIn('refused webhook host', result.stdout)
        self.assertEqual(calls, [])

    def test_http_failure_cannot_spoof_success(self):
        for status in (100, 199, 300, 302, 400, 401, 500):
            with self.subTest(status=status):
                result, calls = self.run_fixture({'statuses': [status],
                    'response_text': 'Research channel test confirmed (HTTP 204). private-research-test-token'})
                self.assertEqual(result.returncode, 1)
                self.assertIn(f'HTTP delivery failure ({status})', result.stdout)
                self.assertEqual(len(calls), 1)

    def test_network_failure_withholds_raw_exception(self):
        result, calls = self.run_fixture({'network_failure': True})
        self.assertEqual(result.returncode, 1)
        self.assertIn('network/send exception', result.stdout)
        self.assertEqual(len(calls), 1)

    def test_unexpected_send_exception_is_sanitized(self):
        result, calls = self.run_fixture({'unexpected_exception': True})
        self.assertEqual(result.returncode, 1)
        self.assertIn('network/send exception', result.stdout)
        self.assertEqual(len(calls), 1)

    def test_unambiguous_transport_result_required(self):
        for value in ([True, 204, 'unexpected detail'], None, [],
                      ['true', 204, '1 messages'], [True, True, '1 messages'],
                      [True, '204', '1 messages'], [True, None, 'dry-run'],
                      [False, 204, '1 messages']):
            with self.subTest(value=value):
                result, calls = self.run_fixture({'override_result': value})
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('test confirmed', result.stdout)
                self.assertEqual(len(calls), 1)

    def test_no_request_cannot_be_reported_as_success(self):
        result, calls = self.run_fixture({'override_result': [True, 204, '1 messages'],
                                          'skip_transport': True})
        self.assertEqual(result.returncode, 1)
        self.assertIn('ambiguous attempt count', result.stdout)
        self.assertEqual(calls, [])

    def test_rate_limit_cannot_issue_a_second_http_attempt(self):
        result, calls = self.run_fixture({'statuses': [429, 204]})
        self.assertEqual(result.returncode, 1)
        self.assertIn('HTTP delivery failure (429)', result.stdout)
        self.assertEqual(len(calls), 1)

    def test_redirects_are_not_followed(self):
        result, calls = self.run_fixture({})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0].get('allow_redirects'), False)

    def test_returned_status_must_match_actual_response(self):
        result, calls = self.run_fixture({'override_result': [True, 201, '1 messages']})
        self.assertEqual(result.returncode, 1)
        self.assertIn('no unambiguous success result', result.stdout)
        self.assertEqual(len(calls), 1)

    def test_non_https_webhook_is_refused(self):
        result, calls = self.run_fixture({'webhook': WEBHOOK.replace('https://', 'http://')})
        self.assertEqual(result.returncode, 1)
        self.assertIn('refused webhook host or scheme', result.stdout)
        self.assertEqual(calls, [])

    def test_wrapper_uses_only_low_level_transport(self):
        run = self.send_step()['run']
        tree = ast.parse(run.split('\n', 1)[1].rsplit('PY\n', 1)[0])
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        self.assertEqual(imports, {'contextlib', 'io', 'os', 'sys', 'discord'})
        transport_calls = [ast.unparse(node.func) for node in ast.walk(tree)
                           if isinstance(node, ast.Call)
                           and ast.unparse(node.func).startswith('transport.')]
        self.assertEqual(sorted(transport_calls),
                         ['transport.send', 'transport.webhook_host_ok'])
        self.assertNotIn('research_board', run)
        self.assertNotIn('post_status', run)
        self.assertNotIn('delivery_policy', run)
        for name in ('DISCORD_WEBHOOK_URL_ALERTS', 'DISCORD_WEBHOOK_URL_MEMBERS',
                     'DISCORD_WEBHOOK_URL', 'ODDS_API_KEY'):
            self.assertNotIn(name, WORKFLOW.read_text())


if __name__ == '__main__':
    unittest.main()
