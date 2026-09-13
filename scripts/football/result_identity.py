"""Explicit append-only result identity adjudications, never fuzzy matching."""
import json
from pathlib import Path
import market

DIRECTORY = Path(__file__).resolve().parents[2] / 'data/football/result_identity'


def resolve(g, commitment, results, keyfn):
    candidates = []
    for path in sorted(DIRECTORY.glob('*.json')):
        item = json.loads(path.read_text(encoding='utf-8'))
        if (item.get('board_sha256') == commitment.get('board_sha256')
                and item.get('game_commitment_sha') == g.get('commitment_sha')):
            candidates.append(item)
    if not candidates:
        return None, None
    if len(candidates) != 1:
        raise ValueError('Multiple result identity adjudications')
    item = candidates[0]
    if (item['sport'] != g['sport']
            or market.parse_utc(item['committed_kickoff_utc']) != market.parse_utc(g['kickoff_utc'])
            or (keyfn(item['away']), keyfn(item['home'])) != (keyfn(g['away']), keyfn(g['home']))):
        raise ValueError('Adjudication does not match committed event')
    matches = [r for r in results.values() if str(r.get('espn_event_id')) == item['espn_event_id']]
    if len(matches) > 1:
        raise ValueError('Adjudicated result ID is ambiguous')
    if not matches:
        return None, None
    r = matches[0]
    if ((keyfn(r['away']), keyfn(r['home'])) != (keyfn(item['away']), keyfn(item['home']))
            or market.parse_utc(r.get('kickoff_utc')) != market.parse_utc(item['result_kickoff_utc'])):
        raise ValueError('Adjudicated result changed; manual review required')
    return r, item
