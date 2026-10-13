import contextlib
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import v01_service as service
import v01_runner as runner
from ingest.resilient import CycleResult
from ingest.worker import IngestResult

class CommissioningTests(unittest.TestCase):
    def test_schedule_created_rows_are_http_success(self):
        conn = Mock()
        def schedule(_):
            print(json.dumps({'created': 4, 'status': 'OK'}))
            return 4
        with patch.dict(os.environ, {'OLP_JOB_TOKEN': 'x' * 40}), patch.object(runner, '_connect', return_value=conn), patch.object(runner, 'do_schedule', side_effect=schedule):
            server = service.ThreadingHTTPServer(('127.0.0.1', 0), service.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                req = Request(f'http://127.0.0.1:{server.server_port}/jobs/v01/schedule', data=b'', headers={'Authorization': 'Bearer ' + 'x' * 40})
                with urlopen(req) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)['created'], 4)
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_discovery_uses_only_nfl_moneyline(self):
        poll = CycleResult('TEST', schedule=IngestResult('s', events_created=3), odds=IngestResult('o', snapshots_written=20))
        with patch.object(runner, 'TheOddsApiProvider') as provider, patch.object(runner, 'run_poll_cycle', return_value=poll), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(runner.do_discover(Mock()), 0)
        provider.assert_called_once_with(sport='americanfootball_nfl', markets='h2h')
        self.assertEqual(json.loads(out.getvalue())['events_created'], 3)

    def test_partial_ingestion_failure_is_not_success(self):
        poll = CycleResult('TEST', odds=IngestResult('o', snapshots_failed=1, errors=['refused quote']))
        with patch.object(runner, 'run_poll_cycle', return_value=poll), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.do_discover(Mock(), provider=Mock()), 1)

    def test_circuit_skip_is_not_success(self):
        poll = CycleResult('TEST', skipped=True, skip_reason='open circuit')
        with patch.object(runner, 'run_poll_cycle', return_value=poll), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.do_discover(Mock(), provider=Mock()), 1)

if __name__ == '__main__':
    unittest.main()
