"""Frozen NFL v1 feature aggregation. No fitting, scoring, market or delivery."""
from collections import defaultdict
import math

DRIVE_VALUE = {
    'Touchdown': 7.0, 'Field goal': 3.0, 'Safety': -2.0,
    'Opp touchdown': -7.0, 'Punt': 0.0, 'Turnover': 0.0,
    'Turnover on downs': 0.0, 'End of half': 0.0,
    'Missed field goal': 0.0,
}


def number(value):
    if value in (None, '') or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def flag(value):
    value = number(value)
    return int(value) if value in (0.0, 1.0) else None


def mean(total, count):
    return total / count if count else None


def blank(game_id, team, opponent, source_sha256):
    return {'game_id': game_id, 'team': team, 'opponent': opponent,
            'source_sha256': source_sha256,
            'pass_n': 0, 'pass_epa_sum': 0.0, 'rush_n': 0, 'rush_epa_sum': 0.0,
            'pass_success_n': 0, 'pass_success_sum': 0,
            'rush_success_n': 0, 'rush_success_sum': 0,
            'pass_yards_n': 0, 'pass_explosive_n': 0,
            'rush_yards_n': 0, 'rush_explosive_n': 0,
            'sack_n': 0, 'sack_sum': 0, 'qb_hit_n': 0, 'qb_hit_sum': 0,
            'missing_success_n': 0, 'missing_yards_n': 0,
            'missing_disruption_flag_n': 0, 'valid_drive_n': 0,
            'drive_value_sum': 0.0, 'red_zone_trip_n': 0,
            'red_zone_score_n': 0, 'red_zone_value_sum': 0.0,
            'invalid_drive_n': 0, 'missing_drive_identity_n': 0}


def aggregate_rows(rows, source_sha256):
    """Return one transparent offensive feature row per team-game."""
    stats, drives = {}, {}
    for raw in rows:
        if raw.get('season_type') != 'REG':
            continue
        game, team, opp = (str(raw.get(k) or '').strip()
                           for k in ('game_id', 'posteam', 'defteam'))
        if not game or not team or not opp or team == opp:
            continue
        key = (game, team)
        out = stats.setdefault(key, blank(game, team, opp, source_sha256))
        if out['opponent'] != opp:
            raise ValueError('team-game has multiple defensive opponents')

        drive = str(raw.get('fixed_drive') or '').strip()
        if not drive:
            out['missing_drive_identity_n'] += 1
        else:
            d = drives.setdefault((game, team, drive),
                {'out': out, 'results': set(), 'red_zone': False})
            result = str(raw.get('fixed_drive_result') or '').strip()
            if result:
                d['results'].add(result)

        epa = number(raw.get('epa'))
        if epa is None or flag(raw.get('qb_kneel')) == 1 or flag(raw.get('qb_spike')) == 1:
            continue
        is_pass = flag(raw.get('pass')) == 1
        is_rush = flag(raw.get('rush')) == 1 and not is_pass
        group = 'pass' if is_pass else ('rush' if is_rush else None)
        if group:
            out[group + '_n'] += 1
            out[group + '_epa_sum'] += epa
            success = flag(raw.get('success'))
            if success is None:
                out['missing_success_n'] += 1
            else:
                out[group + '_success_n'] += 1
                out[group + '_success_sum'] += success
            yards = number(raw.get('yards_gained'))
            if yards is None:
                out['missing_yards_n'] += 1
            else:
                out[group + '_yards_n'] += 1
                out[group + '_explosive_n'] += int(yards >= (15 if is_pass else 10))
        if is_pass:
            for name in ('sack', 'qb_hit'):
                value = flag(raw.get(name))
                if value is None:
                    out['missing_disruption_flag_n'] += 1
                else:
                    out[name + '_n'] += 1
                    out[name + '_sum'] += value
        if drive:
            yardline = number(raw.get('yardline_100'))
            if yardline is not None and yardline <= 20:
                drives[(game, team, drive)]['red_zone'] = True

    for drive in drives.values():
        out, results = drive['out'], drive['results']
        if len(results) != 1 or next(iter(results), None) not in DRIVE_VALUE:
            out['invalid_drive_n'] += 1
            continue
        result = next(iter(results)); value = DRIVE_VALUE[result]
        out['valid_drive_n'] += 1
        out['drive_value_sum'] += value
        if drive['red_zone']:
            out['red_zone_trip_n'] += 1
            out['red_zone_score_n'] += int(result in ('Touchdown', 'Field goal'))
            out['red_zone_value_sum'] += value

    for out in stats.values():
        out.update({
            'pass_epa': mean(out['pass_epa_sum'], out['pass_n']),
            'rush_epa': mean(out['rush_epa_sum'], out['rush_n']),
            'pass_success_rate': mean(out['pass_success_sum'], out['pass_success_n']),
            'rush_success_rate': mean(out['rush_success_sum'], out['rush_success_n']),
            'pass_explosive_rate': mean(out['pass_explosive_n'], out['pass_yards_n']),
            'rush_explosive_rate': mean(out['rush_explosive_n'], out['rush_yards_n']),
            'sack_rate': mean(out['sack_sum'], out['sack_n']),
            'qb_hit_rate': mean(out['qb_hit_sum'], out['qb_hit_n']),
            'drive_value_per_drive': mean(out['drive_value_sum'], out['valid_drive_n']),
            'red_zone_score_rate': mean(out['red_zone_score_n'], out['red_zone_trip_n']),
            'red_zone_value_per_trip': mean(out['red_zone_value_sum'], out['red_zone_trip_n']),
        })
    return [stats[k] for k in sorted(stats)]
