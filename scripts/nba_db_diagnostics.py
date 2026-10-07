"""Bounded investigation on a verified disposable database; no production writes."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'open-ledger-play/tests'))
import harness as h
import test_package4 as p4

target = urlparse(os.environ['OLP_DATABASE_URL'])
assert target.hostname == '127.0.0.1' and target.path == '/nba_disposable_baseline'
assert not os.environ.get('OLP_ALLOW_REMOTE') and not os.environ.get('OLP_ALLOW_WIPE_LIVE')
h.migrate(verbose=True)
admin = h.connect()
definitions = {view: h.scalar(admin, 'SELECT pg_get_viewdef(%s::regclass, true)', (f'public.{view}',))
               for view in ('canonical_market', 'executable_market', 'market_movement', 'market_intelligence')}
report = {'server_version': h.scalar(admin, 'SHOW server_version'), 'cases': []}
out = ROOT / 'database-diagnostics'
out.mkdir(exist_ok=True)

def measure(label, view):
    item = {'label': label, 'view': view}
    admin.execute("SET statement_timeout = '15s'")
    started = time.monotonic()
    try:
        plan = h.scalar(admin, f'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT count(*) FROM public.{view}')
        item['plan'] = plan
        item['completed'] = True
    except Exception as exc:
        item.update(completed=False, error=str(exc))
    item['elapsed_seconds'] = round(time.monotonic()-started, 3)
    report['cases'].append(item)
    (out / 'plans.json').write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k:v for k,v in item.items() if k != 'plan'}), flush=True)

sql = (ROOT / 'open-ledger-play/db/migrations/046_p4_materialise_pipeline_ctes.sql').read_text()
sql = sql[sql.index('CREATE OR REPLACE VIEW public.executable_market'):]
sql = sql[:sql.index('COMMENT ON VIEW')]
fenced = sql.replace('WITH cfg AS (', 'WITH canonical AS MATERIALIZED (SELECT * FROM public.canonical_market), cfg AS (', 1)
fenced = fenced.replace('FROM public.canonical_market c', 'FROM canonical c')
combined = fenced.replace('captured_at AS exec_best_captured_at', 'captured_at AS exec_best_captured_at,\n           count(*) OVER (PARTITION BY event_id, market_type, selection, line) AS executable_book_count')
combined = combined.replace('x.executable_book_count', 'b.executable_book_count')
start = combined.index('JOIN exec_counts x')
end = combined.index('WHERE c.market_quality', start)
combined = combined[:start] + combined[end:]
variants = [('original', None), ('canonical_fence', fenced), ('canonical_fence_and_single_execution_relation', combined)]
for shape, seed in [('dense', p4._seed_dense), ('single_event', p4._seed_single)]:
    p4._benchmark_board(admin, seed)
    for label, candidate in variants:
        admin.execute('CREATE OR REPLACE VIEW public.executable_market WITH (security_invoker=true) AS ' + definitions['executable_market'])
        if candidate:
            admin.execute(candidate)
        for view in ('canonical_market', 'executable_market', 'market_movement', 'market_intelligence'):
            measure(shape + '/' + label, view)
admin.execute('CREATE OR REPLACE VIEW public.executable_market WITH (security_invoker=true) AS ' + definitions['executable_market'])
admin.close()
print('Diagnostic variants restored; production migrations unchanged.', flush=True)
