"""Semantic and dense-read regression for migration 059, on disposable CI only."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'open-ledger-play/tests'))
import harness as h
import test_package4 as p4

target = urlparse(os.environ['OLP_DATABASE_URL'])
assert target.hostname == '127.0.0.1' and target.path == '/nba_disposable_baseline'
assert not os.environ.get('OLP_ALLOW_REMOTE') and not os.environ.get('OLP_ALLOW_WIPE_LIVE')
admin = h.connect()
admin.execute("SET statement_timeout = '15s'")
legacy_exec = (root / 'open-ledger-play/db/migrations/046_p4_materialise_pipeline_ctes.sql').read_text()
legacy_exec = legacy_exec[legacy_exec.index('CREATE OR REPLACE VIEW public.executable_market'):]
legacy_exec = legacy_exec[:legacy_exec.index('COMMENT ON VIEW')]
legacy_mi = (root / 'open-ledger-play/db/migrations/041_p4_market_intelligence.sql').read_text()
legacy_mi = legacy_mi[legacy_mi.index('CREATE VIEW public.market_intelligence'):]
legacy_mi = legacy_mi[:legacy_mi.index('COMMENT ON VIEW')].replace('CREATE VIEW', 'CREATE OR REPLACE VIEW', 1)
candidate = (root / 'open-ledger-play/db/migrations/059_p4_consumer_plan_boundaries.sql').read_text()
results = []
for shape, seed in [('dense', p4._seed_dense), ('single_event', p4._seed_single)]:
    p4._benchmark_board(admin, seed)
    admin.execute('BEGIN')  # Same NOW(), rows and price eligibility in both definitions.
    admin.execute(legacy_exec)
    admin.execute(legacy_mi)
    expected = {}
    for view in ('executable_market', 'market_intelligence'):
        expected[view] = h.rows(admin, f'SELECT row_to_json(t)::text FROM public.{view} t ORDER BY row_to_json(t)::text')
    admin.execute(candidate)
    for view, rows in expected.items():
        actual = h.rows(admin, f'SELECT row_to_json(t)::text FROM public.{view} t ORDER BY row_to_json(t)::text')
        assert actual == rows, (shape, view, 'public values changed')
        count = h.scalar(admin, f'SELECT count(*) FROM public.{view}')
        assert count == len(rows) and count > 0
        results.append({'shape': shape, 'view': view, 'equal_rows': count})
    admin.execute('SET ROLE olp_model')
    assert h.scalar(admin, 'SELECT count(*) FROM model_input.market_intelligence') > 0
    assert h.rows(admin, 'SELECT * FROM model_input.market_intelligence LIMIT 1')
    admin.execute('RESET ROLE')
    admin.execute('COMMIT')
admin.close()
print(json.dumps({'migration': '059', 'semantic_comparison': results,
                  'dense_model_count_and_first_row': 'PASS'}), flush=True)
