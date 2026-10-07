"""Bounded investigation on a verified disposable database; no production writes."""
import json
import os
from pathlib import Path
import sys
import time
import re
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'open-ledger-play/tests'))
import harness as h
import test_package4 as p4

target = urlparse(os.environ['OLP_DATABASE_URL'])
assert target.hostname == '127.0.0.1' and target.path == '/nba_disposable_baseline'
assert not os.environ.get('OLP_ALLOW_REMOTE') and not os.environ.get('OLP_ALLOW_WIPE_LIVE')
h.migrate(verbose=True)
if os.environ.get('NBA_SEQUENCE_DIAGNOSTICS') == '1':
    import run_all
    for group in (run_all.ACCEPTANCE, run_all.CONCURRENCY, run_all.SECURITY,
                  run_all.SECURITY_EXTRA, run_all.PACKAGE2, run_all.BOUNDARY,
                  run_all.PACKAGE3, run_all.PACKAGE4[:-1]):
        for test_id, name, fn in group:
            fn()
            print('PREFIX PASS ' + test_id, flush=True)
admin = h.connect()
definitions = {view: h.scalar(admin, 'SELECT pg_get_viewdef(%s::regclass, true)', (f'public.{view}',))
               for view in ('canonical_market', 'executable_market', 'market_movement', 'market_intelligence')}
report = {'server_version': h.scalar(admin, 'SHOW server_version'), 'cases': []}
out = ROOT / 'database-diagnostics'
out.mkdir(exist_ok=True)

def measure(label, view, query='count'):
    item = {'label': label, 'view': view, 'query': query}
    admin.execute("SET statement_timeout = '15s'")
    started = time.monotonic()
    admin.execute('SAVEPOINT diagnostic_query')
    try:
        table = view if '.' in view else 'public.' + view
        if view.startswith('model_input.'):
            admin.execute('SET ROLE olp_model')
        read = f'SELECT count(*) FROM {table}' if query == 'count' else f'SELECT * FROM {table} LIMIT 1'
        plan = h.scalar(admin, 'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + read)
        item['plan'] = plan
        item['completed'] = True
    except Exception as exc:
        admin.execute('ROLLBACK TO SAVEPOINT diagnostic_query')
        item.update(completed=False, error=str(exc))
    finally:
        admin.execute('RESET ROLE')
        admin.execute('RELEASE SAVEPOINT diagnostic_query')
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
mi = definitions['market_intelligence']
mi = re.sub(r'\b(?:public\.)?canonical_market c\b', 'canonical c', mi)
mi = re.sub(r'\b(?:public\.)?executable_market x\b', 'executable x', mi)
mi = re.sub(r'\b(?:public\.)?market_movement m\b', 'movement m', mi)
mi_fenced = 'CREATE OR REPLACE VIEW public.market_intelligence WITH (security_invoker=true) AS WITH canonical AS MATERIALIZED (SELECT * FROM public.canonical_market), executable AS MATERIALIZED (SELECT * FROM public.executable_market), movement AS MATERIALIZED (SELECT * FROM public.market_movement) ' + mi
sequence = os.environ.get('NBA_SEQUENCE_DIAGNOSTICS') == '1'
variants = [('original', None), ('statistics_refresh', None)] if sequence else [('original', None), ('single_execution_relation_and_consumer_fences', combined)]
for shape, seed in [('dense', p4._seed_dense), ('single_event', p4._seed_single)]:
    p4._benchmark_board(admin, seed)
    stats = h.rows(admin, "SELECT relname, reltuples, relpages FROM pg_class WHERE oid IN ('public.system_settings'::regclass,'public.events'::regclass,'public.market_snapshots'::regclass)")
    print('RELATION_STATISTICS ' + json.dumps(stats, default=str), flush=True)
    expected = None
    for label, candidate in variants:
        if label == 'statistics_refresh':
            admin.execute('VACUUM ANALYZE public.system_settings')
        admin.execute('BEGIN')
        admin.execute('CREATE OR REPLACE VIEW public.executable_market WITH (security_invoker=true) AS ' + definitions['executable_market'])
        admin.execute('CREATE OR REPLACE VIEW public.market_intelligence WITH (security_invoker=true) AS ' + definitions['market_intelligence'])
        if candidate:
            admin.execute(candidate)
            admin.execute(mi_fenced)
        if not sequence:
            actual = h.rows(admin, 'SELECT row_to_json(t)::text FROM public.executable_market t ORDER BY row_to_json(t)::text')
            if expected is None:
                expected = actual
            else:
                assert actual == expected, 'Executable output changed'
            print(f'{shape}/{label}: all {len(actual)} executable rows equal', flush=True)
        for view in ('canonical_market', 'executable_market', 'market_movement', 'market_intelligence', 'model_input.market_intelligence'):
            measure(shape + '/' + label, view)
            measure(shape + '/' + label, view, 'first_row')
        admin.execute('COMMIT')
admin.execute('CREATE OR REPLACE VIEW public.executable_market WITH (security_invoker=true) AS ' + definitions['executable_market'])
admin.close()
print('Diagnostic variants restored; production migrations unchanged.', flush=True)
