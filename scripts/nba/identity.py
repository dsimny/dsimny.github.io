"""Canonical identity for NBA Package 2A. Pure functions, no I/O, no network.

IDENTITY AUTHORITY (architecture decision 1, 2026-10-26):

  The NBA league-issued game ID is the ONLY authority for canonical game identity.
  Provider IDs (ESPN and any future provider) are secondary labels that point AT a
  canonical game. They never establish it.

That single rule is what the rest of this module enforces:

  - A game without a league ID cannot become canonical. Not by team names, not by
    date, not by provider confidence. It is quarantined.
  - Team names never match anything. They are display strings, recorded as observed
    text and compared only for reporting, never for identity.
  - Two providers disagreeing is not resolved by picking one. It is quarantined.

Ambiguity is a RESULT, not an error to be smoothed over. A quarantine carries a
reason code so completeness accounting can report exactly what was withheld and why
(see completeness.reconcile) instead of quietly dropping it.
"""
from datetime import datetime, timedelta, timezone

VERSION = 'nba-identity-v0.1'

# The one attribute that may establish canonical identity.
IDENTITY_AUTHORITY = 'league'

# Quarantine reasons. Stable strings: downstream accounting and tests match on
# these, so they are contract, not diagnostics.
AMBIGUOUS_PROVIDER_MATCH = 'AMBIGUOUS_PROVIDER_MATCH'
MISSING_LEAGUE_ID = 'MISSING_LEAGUE_ID'
UNKNOWN_PHASE = 'UNKNOWN_PHASE'
LEAGUE_ID_CONFLICT = 'LEAGUE_ID_CONFLICT'
DUPLICATE_LEAGUE_ID = 'DUPLICATE_LEAGUE_ID'
INVALID_TEAM_ID = 'INVALID_TEAM_ID'
INSUFFICIENT_PROVENANCE = 'INSUFFICIENT_PROVENANCE'
# Pregame integrity (Defect 1 fix).
START_TIME_UNKNOWN = 'START_TIME_UNKNOWN'
START_TIME_CONFLICT = 'START_TIME_CONFLICT'
PREGAME_AFTER_START = 'PREGAME_AFTER_START'
AVAILABILITY_AFTER_CUTOFF = 'AVAILABILITY_AFTER_CUTOFF'

QUARANTINE_REASONS = frozenset({
    AMBIGUOUS_PROVIDER_MATCH, MISSING_LEAGUE_ID, UNKNOWN_PHASE,
    LEAGUE_ID_CONFLICT, DUPLICATE_LEAGUE_ID, INVALID_TEAM_ID,
    INSUFFICIENT_PROVENANCE,
    START_TIME_UNKNOWN, START_TIME_CONFLICT, PREGAME_AFTER_START,
})


class Quarantine(Exception):
    """A record that cannot be canonically identified.

    Carries a stable reason code and the observed facts that caused it. Raising is
    the fail-closed path: nothing is written for a quarantined record.
    """

    def __init__(self, reason, detail=None):
        if reason not in QUARANTINE_REASONS:
            raise ValueError('Unknown quarantine reason: %r' % (reason,))
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)

    def as_dict(self):
        return {'quarantined': True, 'reason': self.reason, 'detail': dict(self.detail)}


def utc(value):
    """Timezone-aware UTC datetime. A naive timestamp is refused, never assumed.

    Duplicated deliberately from contracts.utc() rather than imported: Package 2A
    must read Package 1 without modifying it, and an import would couple this
    module's refactor to a frozen artefact. The behaviour is asserted identical by
    test_package1_unchanged.
    """
    if not isinstance(value, str):
        raise ValueError('Timestamp must be an ISO-8601 string')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timezone required')
    return stamp.astimezone(timezone.utc)


def require_league_id(record):
    """The league ID, or quarantine. Never substituted, never inferred."""
    if not isinstance(record, dict):
        raise Quarantine(INSUFFICIENT_PROVENANCE, {'type': type(record).__name__})
    league_id = record.get('league_game_id')
    if league_id is None or (isinstance(league_id, str) and not league_id.strip()):
        raise Quarantine(MISSING_LEAGUE_ID, {'provider_ids': dict(record.get('provider_ids') or {})})
    if not isinstance(league_id, str):
        raise Quarantine(INSUFFICIENT_PROVENANCE, {'league_game_id_type': type(league_id).__name__})
    return league_id.strip()


def require_team_id(team_id, field='team'):
    """Team identity is a stable ID. A bare name is not an identity."""
    if team_id is None or (isinstance(team_id, str) and not team_id.strip()):
        raise Quarantine(INVALID_TEAM_ID, {'field': field})
    if not isinstance(team_id, str):
        raise Quarantine(INVALID_TEAM_ID, {'field': field, 'type': type(team_id).__name__})
    return team_id.strip()


def normalise_provider_ids(provider_ids):
    """Provider IDs are labels: {provider: id}. Never used to create identity."""
    if not provider_ids:
        return {}
    if not isinstance(provider_ids, dict):
        raise Quarantine(INSUFFICIENT_PROVENANCE, {'provider_ids_type': type(provider_ids).__name__})
    out = {}
    for provider, value in provider_ids.items():
        if not isinstance(provider, str) or not provider.strip():
            raise Quarantine(INSUFFICIENT_PROVENANCE, {'provider': provider})
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not isinstance(value, (str, int)):
            raise Quarantine(INSUFFICIENT_PROVENANCE, {'provider': provider, 'type': type(value).__name__})
        out[provider.strip()] = str(value).strip()
    return out


def canonical_game(record):
    """Build the canonical identity of a game, or quarantine.

    Returns a dict with ONLY identity-bearing fields. Scheduling, phase and status
    belong to schedule.py; timestamps to the timestamp block below. Keeping identity
    narrow is what makes "who is this game" answerable without reference to "when is
    it" -- the conflation the data contract forbids.
    """
    league_id = require_league_id(record)
    home = require_team_id(record.get('home_team_id'), 'home_team_id')
    away = require_team_id(record.get('away_team_id'), 'away_team_id')
    if home == away:
        raise Quarantine(INVALID_TEAM_ID, {'detail': 'home and away are the same team', 'team_id': home})
    return {
        'identity_version': VERSION,
        'identity_authority': IDENTITY_AUTHORITY,
        'league_game_id': league_id,
        'home_team_id': home,
        'away_team_id': away,
        'provider_ids': normalise_provider_ids(record.get('provider_ids')),
        'provider_names_observed': dict(record.get('provider_names_observed') or {}),
    }


# ---------------------------------------------------------------- timestamps

# Every timestamp the data contract requires to persist SEPARATELY. Combining any
# two of these destroys the availability/leakage evidence later packages depend on.
TIMESTAMP_FIELDS = (
    'event_time',            # when the game is scheduled
    'source_published_at',   # when the source says it published
    'source_updated_at',     # when the source says it last updated
    'request_started_at',    # when we asked
    'response_received_at',  # when we heard back
    'available_at',          # when the fact was available to a decision maker
    'ingested_at',           # when we stored it
    'decision_cutoff',       # the cutoff the fact must precede
)


def timestamp_block(record):
    """Extract the eight timestamps, each independently, none derived.

    A missing timestamp stays absent. It is NEVER filled from a neighbouring one --
    'assume available_at is ingested_at' is exactly the silent substitution the
    contract forbids, and it would make a later leakage audit meaningless.
    """
    out = {}
    for field in TIMESTAMP_FIELDS:
        raw = record.get(field)
        if raw is None:
            continue
        out[field] = utc(raw) if isinstance(raw, str) else raw
    return out


def blocks_from_features(available_at, ingested_at, cutoff):
    """Availability/ingestion must precede the decision cutoff. Returns (usable, anomalies).

    ONLY availability and ingestion are gated here. The event's own start time is
    deliberately NOT an input: a game tips off AFTER a pre-game decision cutoff by
    definition, so gating it against the cutoff would flag every game ever played and
    the check would be worthless. This was a real defect in the first draft of this
    module, caught by its own test.

    Future-dated availability or ingestion is retained as an anomalous raw fact but
    must not reach features. Anomalies are reported, not raised: the record stays as
    evidence and the FEATURE layer is what refuses it.
    """
    anomalies = []
    cutoff_utc = utc(cutoff)
    for name, value in (('available_at', available_at), ('ingested_at', ingested_at)):
        if value is None:
            continue
        stamp = utc(value)
        if stamp > cutoff_utc:
            anomalies.append({'field': name, 'value': value, 'reason': 'AFTER_CUTOFF'})
    return (not anomalies), anomalies


# ------------------------------------------------------------- pregame integrity

# How far a confirmed actual tipoff may sit from the scheduled start before the two
# are treated as CONFLICTING records rather than a normal early start or delay.
# Six hours is deliberately generous for a delay, and far short of the gap a
# rescheduled date would produce -- a reschedule belongs in a new revision, not here.
START_CONFLICT_TOLERANCE_SECONDS = 6 * 3600


def iso(stamp):
    """ISO-8601 UTC with a Z suffix, matching the stored timestamp format."""
    return stamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _as_utc(value):
    """Accept an ISO-8601 string OR an already-parsed datetime.

    Schedule revisions store timezone-aware datetimes while callers naturally pass
    strings, so the boundary normalises rather than forcing one representation on
    the other. A naive datetime is still refused.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError('Timezone required')
        return value.astimezone(timezone.utc)
    return utc(value)


def effective_start(scheduled_start=None, actual_start=None):
    """The start time that governs pregame eligibility.

    THE EARLIEST KNOWN START WINS. A confirmed tipoff earlier than the scheduled
    time means the game really began then, so a cutoff between the two is already
    post-tipoff. Taking the earlier value is what stops an early start from being
    mistaken for an eligible window.

    A LATE tipoff therefore yields the SCHEDULED time, which is the conservative
    direction: it refuses a cutoff that a delayed game might have made legitimately
    pregame. Refusing is safe; wrongly qualifying is leakage.

    MISSING OR CONFLICTING EVIDENCE NEVER ESTABLISHES ELIGIBILITY. No start time at
    all raises; two start times more than START_CONFLICT_TOLERANCE_SECONDS apart
    raise rather than silently picking one.
    """
    if scheduled_start is None and actual_start is None:
        raise Quarantine(START_TIME_UNKNOWN, {'detail': 'no scheduled or actual start recorded'})
    if actual_start is None:
        return _as_utc(scheduled_start)
    if scheduled_start is None:
        return _as_utc(actual_start)
    scheduled, actual = _as_utc(scheduled_start), _as_utc(actual_start)
    if abs((scheduled - actual).total_seconds()) > START_CONFLICT_TOLERANCE_SECONDS:
        raise Quarantine(START_TIME_CONFLICT, {
            'scheduled_start': scheduled_start, 'actual_start': actual_start,
            'tolerance_seconds': START_CONFLICT_TOLERANCE_SECONDS})
    return min(scheduled, actual)


def effective_start_iso(scheduled_start=None, actual_start=None):
    return iso(effective_start(scheduled_start, actual_start))


def require_pregame(cutoff, scheduled_start=None, actual_start=None):
    """A decision cutoff strictly BEFORE the effective start. Returns that start.

    At or after the start is refused: a cutoff that reaches tipoff can observe the
    game, so a decision made with it is not a pregame decision. Nothing compared
    these two before this guard existed (Defect 1).

    This is additive. The availability/ingestion cutoff rule is unchanged and is
    applied separately by blocks_from_features -- a future tipoff must never be
    treated as an availability problem.
    """
    start = effective_start(scheduled_start, actual_start)
    if _as_utc(cutoff) >= start:
        raise Quarantine(PREGAME_AFTER_START,
                         {'cutoff': cutoff, 'effective_start': iso(start)})
    return iso(start)
