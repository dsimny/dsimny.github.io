"""Hosted runner endpoint. cron-job.org's only job is to POST here.

    POST /jobs/v01/schedule   -> v01_runner --schedule
    POST /jobs/v01/resolve    -> v01_runner --resolve
    GET  /jobs/v01/status     -> the ledger, read-only

The scheduler is the CLOCK and nothing else. It carries no experiment
semantics, no model version, no window width, no market list and no API key.
Every one of those lives here or in the database, so changing the cadence can
never change what the experiment means.

Environment:
    OLP_JOB_TOKEN        required. Long random bearer token.
    OLP_DATABASE_URL     required.
    THE_ODDS_API_KEY     required for /resolve.

    THE ODDS API KEY LIVES ONLY IN THIS PROCESS'S ENVIRONMENT. It is never
    accepted from the URL, the query string, a header or the body -- a scheduler
    that could supply it is a scheduler that logs it.

Run:
    python scripts/v01_service.py --port 8787

Responses are the runner's own JSON cycle record plus an HTTP status:
    200  cycle completed (including "nothing due", which is the common case)
    409  cycle ran but needs a human -- see .status / .alert / .errors
    401  missing or wrong bearer token
"""
import argparse
import contextlib
import io
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import v01_runner as runner                                    # noqa: E402

# One cycle at a time in this process. The database lease already makes
# concurrent runners safe; this simply avoids paying for a race we can refuse
# for free.
_LOCK = threading.Lock()


def _constant_time_eq(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


def _run(fn) -> tuple:
    """Invoke a runner entry point, capturing its JSON line as the response."""
    if not _LOCK.acquire(blocking=False):
        return 409, {"status": "BUSY",
                     "note": "a cycle is already running in this process; "
                             "the next scheduled tick will pick the work up"}
    try:
        buf = io.StringIO()
        conn = runner._connect()
        try:
            with contextlib.redirect_stdout(buf):
                rc = fn(conn)
        finally:
            conn.close()
    except Exception as exc:                                   # noqa: BLE001
        from ingest.http import redact
        return 500, {"status": "ERROR", "error": redact(str(exc)).splitlines()[0]}
    finally:
        _LOCK.release()

    line = buf.getvalue().strip().splitlines()
    try:
        body = json.loads(line[-1]) if line else {"status": "OK"}
    except (ValueError, IndexError):
        body = {"status": "OK" if not rc else "FAILED", "output": buf.getvalue()}
    return (200 if not rc else 409), body


class Handler(BaseHTTPRequestHandler):
    server_version = "olp-v01-runner"

    def _authorized(self) -> bool:
        want = os.environ.get("OLP_JOB_TOKEN", "")
        got = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return bool(want) and got.startswith(prefix) and \
            _constant_time_eq(got[len(prefix):], want)

    def _reply(self, code: int, body: dict) -> None:
        payload = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:                                 # noqa: N802
        if not self._authorized():
            return self._reply(401, {"status": "UNAUTHORIZED"})
        # The body is read and discarded. Nothing about the experiment is
        # parameterised over the wire: an endpoint that accepted a model
        # version, a window width or a market list would let the scheduler
        # change what the experiment means.
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)

        if self.path.rstrip("/") == "/jobs/v01/schedule":
            return self._reply(*_run(runner.do_schedule))
        if self.path.rstrip("/") == "/jobs/v01/resolve":
            return self._reply(*_run(runner.do_resolve))
        return self._reply(404, {"status": "NOT_FOUND", "path": self.path})

    def do_GET(self) -> None:                                  # noqa: N802
        if not self._authorized():
            return self._reply(401, {"status": "UNAUTHORIZED"})
        if self.path.rstrip("/") == "/jobs/v01/status":
            return self._reply(*_run(runner.show_status))
        return self._reply(404, {"status": "NOT_FOUND", "path": self.path})

    def log_message(self, fmt, *args):
        # Never echo the request line: it is the one place a secret could be
        # written to a log if anyone ever put one in a query string.
        sys.stderr.write(f"{self.address_string()} {self.command} "
                         f"{self.path.split('?')[0]} {args[-1] if args else ''}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    for var in ("OLP_JOB_TOKEN", "OLP_DATABASE_URL"):
        if not os.environ.get(var):
            print(f"{var} is not set.", file=sys.stderr)
            return 2
    if len(os.environ["OLP_JOB_TOKEN"]) < 32:
        print("OLP_JOB_TOKEN is shorter than 32 characters; use a long random "
              "value.", file=sys.stderr)
        return 2
    if not os.environ.get("THE_ODDS_API_KEY"):
        print("warning: THE_ODDS_API_KEY is not set; /resolve will refuse to "
              "capture.", file=sys.stderr)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"listening on {args.host}:{args.port}", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
