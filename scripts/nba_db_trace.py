"""Run the unchanged database suite with bounded CI-only diagnostic reads."""
import faulthandler
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import psycopg

root = Path(__file__).resolve().parents[1]
stop = threading.Event()

def monitor():
    while not stop.wait(20):
        try:
            with psycopg.connect(os.environ['OLP_DATABASE_URL'], autocommit=True) as conn:
                rows = conn.execute("""SELECT pid, state, wait_event_type, wait_event,
                    extract(epoch from now()-query_start)::int AS age_seconds,
                    pg_blocking_pids(pid), left(query, 1200)
                    FROM pg_stat_activity WHERE datname=current_database()
                    AND pid <> pg_backend_pid() AND state <> 'idle'
                    ORDER BY query_start""").fetchall()
            print('DATABASE_ACTIVITY ' + json.dumps(rows, default=str), flush=True)
        except Exception as exc:
            print('DATABASE_ACTIVITY_ERROR ' + type(exc).__name__, flush=True)

threading.Thread(target=monitor, daemon=True).start()
faulthandler.dump_traceback_later(30, repeat=True)
sys.path.insert(0, str(root / 'open-ledger-play/tests'))
sys.argv = ['tests/run_all.py', '--traceback']
try:
    runpy.run_path(str(root / 'open-ledger-play/tests/run_all.py'), run_name='__main__')
finally:
    stop.set()
    faulthandler.cancel_dump_traceback_later()
