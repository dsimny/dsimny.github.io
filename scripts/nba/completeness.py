"""Lossless completeness accounting. Pure, offline.

THE ONE INVARIANT: nothing disappears. Every expected game ends in exactly one
terminal bucket, and the buckets must sum back to the expected count. A discrepancy
is a FAILURE with a reason, never a rounded-away remainder.

This matters because "we have complete schedules" is otherwise an unfalsifiable
claim. Making the accounting an asserted identity turns it into a test.

Expected games come from a DECLARED source (a league-issued count or schedule
manifest). This module never fabricates an expectation to make the numbers close.
"""
from . import identity
from . import schedule

VERSION = 'nba-completeness-v0.1'

BUCKETS = ('canonical', 'quarantined', 'postponed', 'cancelled', 'missing')


class CompletenessError(Exception):
    """The accounting did not reconcile. Never swallowed."""


def _bucket_for(record):
    """Which terminal bucket a schedule revision belongs in.

    Quarantine is decided upstream (it has no canonical identity), so records that
    arrive here are already canonical. Status decides the rest.
    """
    status = record.get('status')
    if status == schedule.CANCELLED:
        return 'cancelled'
    if status == schedule.POSTPONED:
        return 'postponed'
    return 'canonical'


def reconcile(expected, chains, quarantined, missing=()):
    """Reconcile expected games against what was actually accounted for.

    expected    : int -- the declared count of games that should exist
    chains      : {league_game_id: RevisionChain} -- canonical games with revisions
    quarantined : [{"reason":..., "detail":...}, ...] -- records withheld for identity
    missing     : [league_game_id, ...] -- expected but never observed

    Returns a report whose buckets ALWAYS sum to `expected`. If they do not, raises.
    """
    if not isinstance(expected, int) or expected < 0:
        raise ValueError('expected must be a non-negative int')

    missing = list(missing or ())

    # --- exclusivity audit (Defect 2) -----------------------------------------
    # A league game occupies exactly ONE accounting category. The categories are
    # mutually exclusive; a game's STATUS HISTORY is not (a game may be postponed
    # and later cancelled, and both are true of it), but that history must not
    # translate into two counted categories.
    #
    # Without this, a caller could list one game as both canonical and missing and
    # the totals would still sum, reporting success while a genuinely absent game
    # went unnoticed. Duplicates inflate the sum in exactly the way that hides a gap.
    duplicated = sorted({g for g in missing if missing.count(g) > 1})
    if duplicated:
        raise CompletenessError(
            'Duplicate league_game_id within missing: %s' % ', '.join(duplicated))

    overlap = sorted(set(missing) & set(chains))
    if overlap:
        raise CompletenessError(
            'league_game_id present in both chains and missing: %s' % ', '.join(overlap))

    # A quarantined record normally has NO canonical identity -- that is why it is
    # quarantined -- but one may name the game it failed to identify. A named id
    # makes it identifiable, so it cannot also be canonical or missing, and two
    # named records cannot both claim it.
    quarantined_ids = []
    for item in quarantined or ():
        gid = ((item or {}).get('detail') or {}).get('league_game_id')
        if gid:
            quarantined_ids.append(gid)
    both = sorted(set(quarantined_ids) & (set(chains) | set(missing)))
    if both:
        raise CompletenessError(
            'league_game_id present in both canonical/missing and quarantined: %s'
            % ', '.join(both))
    duplicated_quarantined = sorted({g for g in quarantined_ids if quarantined_ids.count(g) > 1})
    if duplicated_quarantined:
        raise CompletenessError(
            'Duplicate league_game_id within quarantined: %s' % ', '.join(duplicated_quarantined))

    counts = {name: 0 for name in BUCKETS}
    detail = {'canonical': [], 'postponed': [], 'cancelled': [], 'missing': [], 'quarantined': []}

    for league_game_id in sorted(chains):
        chain = chains[league_game_id]
        current = chain.current()
        if current is None:
            # A chain with no revisions cannot be accounted for; treat as missing
            # rather than pretending it is a canonical game.
            counts['missing'] += 1
            detail['missing'].append({'league_game_id': league_game_id, 'reason': 'EMPTY_CHAIN'})
            continue
        bucket = _bucket_for(current)
        counts[bucket] += 1
        detail[bucket].append(league_game_id)

    for record in quarantined or ():
        counts['quarantined'] += 1
        detail['quarantined'].append({
            'reason': (record or {}).get('reason'),
            'detail': (record or {}).get('detail') or {},
        })

    for league_game_id in missing:
        counts['missing'] += 1
        detail['missing'].append({'league_game_id': league_game_id, 'reason': 'NEVER_OBSERVED'})

    # Defensive: the categories must be pairwise disjoint after accounting too.
    seen_categories = {}
    for bucket in ('canonical', 'postponed', 'cancelled', 'missing'):
        for entry in detail[bucket]:
            # canonical/postponed/cancelled hold bare ids; missing holds dicts.
            game_id = entry.get('league_game_id') if isinstance(entry, dict) else entry
            if game_id is None:
                continue
            if game_id in seen_categories:
                raise CompletenessError(
                    'league_game_id %s counted in both %s and %s'
                    % (game_id, seen_categories[game_id], bucket))
            seen_categories[game_id] = bucket

    accounted = sum(counts.values())
    if accounted != expected:
        raise CompletenessError(
            'Completeness does not reconcile: expected %d, accounted %d (%s)'
            % (expected, accounted, ', '.join('%s=%d' % (k, counts[k]) for k in BUCKETS)))

    reasons = {}
    for item in detail['quarantined']:
        reasons[item['reason']] = reasons.get(item['reason'], 0) + 1

    return {
        'completeness_version': VERSION,
        'expected': expected,
        'accounted': accounted,
        'reconciles': True,
        'buckets': counts,
        'quarantine_reasons': reasons,
        'detail': detail,
        # Coverage is stated over the games we could identify. It is NOT a claim
        # about model accuracy, source licensing or production readiness.
        'identified_coverage': (counts['canonical'] / expected) if expected else None,
    }


def assert_lossless(report):
    """Belt-and-braces check on a report produced elsewhere."""
    if not report or not report.get('reconciles'):
        raise CompletenessError('Report is not reconciled')
    buckets = report.get('buckets') or {}
    if sum(buckets.get(name, 0) for name in BUCKETS) != report.get('expected'):
        raise CompletenessError('Bucket sum does not equal expected')
    return True


def unreconciled_gap(expected, buckets):
    """The gap, for reporting a failure without raising."""
    return expected - sum((buckets or {}).get(name, 0) for name in BUCKETS)
