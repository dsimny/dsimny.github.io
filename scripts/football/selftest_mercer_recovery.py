"""Regression checks for the disclosed September 20 Discord ledger recovery."""
import copy
import json
import tempfile
from pathlib import Path
import mercer
import settle

entries = mercer.load_ledger()['entries']
recovered = [e for e in entries if e.get('provenance_type') == 'discord_recovery']
assert len(recovered) == 1
entry = recovered[0]
assert entry['line'] == 47.5 and entry['units'] == 1 and entry['price'] == -102
assert settle.settle_total(9, 3, entry['line'], entry['side']) == entry['result'] == 'WIN'
assert settle.pnl('WIN', 1, -102) == entry['pnl'] == 0.9804
assert entry['commitment_sha256'] is None and entry['committed_utc'] is None
assert mercer.market.parse_utc(entry['published_utc']) < mercer.market.parse_utc(entry['kickoff_utc'])
assert mercer.market.parse_utc(entry['recorded_utc']) > mercer.market.parse_utc(entry['kickoff_utc'])
assert not any(c['pick_id'] == entry['pick_id'] for c in mercer.load_commitments()['commitments'])
assert len({e['pick_id'] for e in entries}) == len(entries)
original = copy.deepcopy(entries)
assert mercer.recovered_picks([]) == ''
with tempfile.TemporaryDirectory() as d:
    mercer.OUT = d
    mercer.render_record(entries)
    page = (Path(d) / 'record/index.html').read_text(encoding='utf-8')
    for text in ['Under 47.5', 'FanDuel', '-102', 'Discord recovery; no fingerprint',
                 'Disclosed ledger exception', 'Green Bay Packers', 'LOSS', '+0.73', '+58.4%']:
        assert text in page, text
    mercer.render_record(entries)
    assert (Path(d) / 'record/index.html').read_text(encoding='utf-8') == page
    block = mercer.recovered_picks(entries)
    assert 'WIN +0.9804u' in block and 'No pregame fingerprint' in block
    losing = copy.deepcopy(entry)
    losing.update(result='LOSS', pnl=-1.0)
    assert 'LOSS -1.0000u' in mercer.recovered_picks([losing])
assert entries == original
print('Discord recovery regression: settlement, evidence, mixed record, loss rendering and repeat rendering passed')
