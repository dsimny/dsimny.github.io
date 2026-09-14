"""Read-only schema/hash and per-event capture coverage checks. No model scoring."""
import csv
import gzip
import hashlib
import json
from pathlib import Path

import market_input as mi

FAMILIES = {
    'pass_rush_epa': {'epa', 'pass', 'rush'},
    'success': {'success', 'pass', 'rush'},
    'explosiveness': {'yards_gained', 'pass', 'rush'},
    'disruption': {'sack', 'qb_hit', 'pass'},
    'red_zone': {'yardline_100', 'fixed_drive', 'fixed_drive_result'},
    'drive_points': {'fixed_drive', 'fixed_drive_result'},
}


def inventory(cache, manifest):
    """Only declared 2010–2024 bytes/headers; never read holdout or game rows."""
    out = []
    for year in range(2010, 2025):
        path = Path(cache) / f'play_by_play_{year}.csv.gz'
        expected = manifest.get('seasons', {}).get(str(year))
        row = {'season': year, 'exists': path.is_file(), 'ready': False}
        if not path.is_file() or not expected:
            row['reason'] = 'missing cache or manifest entry'
        else:
            raw = path.read_bytes()
            row['sha256'] = hashlib.sha256(raw).hexdigest()
            row['bytes'] = len(raw)
            if row['sha256'] != expected['sha256'] or len(raw) != expected['bytes']:
                row['reason'] = 'source fingerprint mismatch'
            else:
                with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as f:
                    header = next(csv.reader(f))
                row['missing_columns'] = {name: sorted(cols - set(header)) for name, cols in FAMILIES.items()}
                row['ready'] = not any(row['missing_columns'].values())
                row['reason'] = 'schema/hash verified; feature completeness and point-in-time validity untested'
        out.append(row)
    return out


def coverage(schedule, snapshots, sport, asof):
    """Schedule must be normalized independently; exact names only, no fuzzy join.

Cross-provider name differences remain visible as unmatched. Capture time alone
never satisfies an event. All inputs are caller-owned; no fetching or mutation.
"""
    decision = mi.utc(asof)
    out = []
    for event in schedule:
        kick = mi.utc(event['kickoff_utc'])
        row = dict(event, valid_observations=0, rejected=[], status='NOT_YET_DUE')
        if (kick - decision).total_seconds() > 30 * 3600:
            out.append(row); continue
        row['status'] = 'MISSING_OR_INVALID'
        for source, snap in snapshots:
            captured = mi.utc(snap.get('captured_utc'))
            if captured > decision or not 18 <= (kick-captured).total_seconds()/3600 <= 30:
                continue
            matches = [e for e in snap.get('events', []) if
                       (e.get('away_raw'), e.get('home_raw')) == (event['away'], event['home'])]
            try:
                if len(matches) != 1:
                    raise mi.InvalidMarket('schedule fixture absent or ambiguous in capture')
                ev, stamp = mi.event_at(snap, sport, matches[0].get('odds_api_event_id'),
                    event['away'], event['home'], event['kickoff_utc'], asof)
                q, rejected = mi.quotes(ev, stamp)
                mi.consensus(q, event['away'], event['home'])
                row['valid_observations'] += 1
            except mi.InvalidMarket as exc:
                row['rejected'].append({'source': source, 'reason': str(exc)})
        if row['valid_observations']:
            row['status'] = 'OBSERVATION_FOUND_NOT_EXECUTION_PROOF'
        out.append(row)
    return out
