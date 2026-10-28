"""Phase-aware schedules and immutable revision chains. Pure, offline.

PHASE COMES FROM EVIDENCE, NEVER FROM A CALENDAR (NBA_DATA_CONTRACT.md:11,
NBA_ARCHITECTURE.md:31). A date cannot tell you whether a game is a Cup semifinal
or a regular-season game -- those are simultaneous in the NBA -- so no code path
here infers phase. Missing or unrecognised phase refuses classification.

REVISIONS ARE APPEND-ONLY. A postponement or reschedule creates a NEW revision and
links the old one forward with `superseded_by`. Nothing is overwritten, because a
later leakage or CLV audit has to be able to ask "what did we believe on date D",
and an overwritten row cannot answer that.

CUP COUNTING IS NOT DECIDED HERE. `contracts.counts_regular()` is the frozen
authority (Package 1) and is called, not reimplemented. This module owns WHERE a
game sits in time and in a revision chain; Package 1 owns what counts.
"""
import hashlib
import json

from . import contracts
from . import identity

VERSION = 'nba-schedule-v0.1'

PHASES = contracts.PHASES
CUP_ROUNDS = contracts.CUP_ROUNDS

# Game status. CANCELLED and POSTPONED are MARKED terminal/pending states, never
# deletions -- a cancelled game still occupies its slot in the completeness
# denominator, which is the whole point of counting it.
SCHEDULED = 'SCHEDULED'
POSTPONED = 'POSTPONED'
RESCHEDULED = 'RESCHEDULED'
CANCELLED = 'CANCELLED'
FINAL = 'FINAL'
STATUSES = frozenset({SCHEDULED, POSTPONED, RESCHEDULED, CANCELLED, FINAL})


def classify_phase(phase, cup_round=None):
    """Validate a phase and its Cup round against the frozen Package 1 sets.

    Refuses rather than guesses. `counts_regular` is delegated so Cup inclusion has
    exactly one definition in the codebase.
    """
    if phase not in PHASES:
        raise identity.Quarantine(identity.UNKNOWN_PHASE, {'phase': phase})
    if phase == 'CUP':
        if cup_round not in CUP_ROUNDS:
            raise identity.Quarantine(identity.UNKNOWN_PHASE, {'phase': phase, 'cup_round': cup_round})
    elif cup_round is not None:
        raise identity.Quarantine(identity.UNKNOWN_PHASE, {'phase': phase, 'cup_round': cup_round})
    return {'phase': phase, 'cup_round': cup_round, 'counts_regular': contracts.counts_regular(phase, cup_round)}


def schedule_record(game, phase, cup_round, status, event_time,
                    actual_venue=None, neutral_site=False, revision=1,
                    supersedes=None, source_updated_at=None, actual_start_time=None):
    """One schedule revision for a game.

    Identity is supplied by identity.canonical_game, so this function cannot invent
    a game ID: it takes a canonical game and adds scheduling facts to it.
    """
    if status not in STATUSES:
        raise ValueError('Unknown status %r' % (status,))
    phase_block = classify_phase(phase, cup_round)
    canonical = identity.canonical_game(game)
    record = {
        'schedule_version': VERSION,
        'league_game_id': canonical['league_game_id'],
        'home_team_id': canonical['home_team_id'],
        'away_team_id': canonical['away_team_id'],
        'provider_ids': canonical['provider_ids'],
        'event_time': identity.utc(event_time),
        'actual_venue': actual_venue,
        # Neutral site is a VENUE PROPERTY. It is deliberately not a phase input and
        # is not consulted by classify_phase (NBA_ARCHITECTURE.md:30).
        'neutral_site': bool(neutral_site),
        'status': status,
        'revision': int(revision),
        'supersedes': supersedes,
        'superseded_by': None,
        'source_updated_at': identity.utc(source_updated_at) if source_updated_at else None,
        # Confirmed actual tipoff, when known. Optional and separate from the
        # scheduled time: it is evidence about what happened, not what was planned.
        'actual_start_time': identity.utc(actual_start_time) if actual_start_time else None,
    }
    record.update(phase_block)
    record['revision_digest'] = digest(record)
    return record


def digest(record):
    """Stable content hash of a revision, excluding the digest field itself."""
    payload = {k: (v.isoformat() if hasattr(v, 'isoformat') else v)
               for k, v in sorted(record.items()) if k != 'revision_digest'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class RevisionChain:
    """An append-only chain of schedule revisions for ONE canonical game.

    append() never mutates an existing revision. Superseding a revision records the
    forward link on the old record's COPY returned in the chain view, and the stored
    record is preserved verbatim so its digest still verifies.
    """

    def __init__(self, league_game_id):
        self.league_game_id = league_game_id
        self._revisions = []

    def append(self, record):
        if record['league_game_id'] != self.league_game_id:
            raise ValueError('Revision belongs to a different game')
        expected = len(self._revisions) + 1
        if record['revision'] != expected:
            raise ValueError('Non-contiguous revision: expected %d, got %d'
                             % (expected, record['revision']))
        if record['revision'] > 1:
            previous = self._revisions[-1]
            if record['supersedes'] != previous['revision']:
                raise ValueError('Revision %d must supersede %d'
                                 % (record['revision'], previous['revision']))
        self._revisions.append(record)
        return record

    @property
    def revisions(self):
        return tuple(self._revisions)

    def current(self):
        """The latest revision, or None. Latest, not best, not most-complete."""
        return self._revisions[-1] if self._revisions else None

    def as_of(self, cutoff):
        """The revision in force at a cutoff. This is why revisions append.

        FAIL CLOSED ON MISSING AVAILABILITY PROVENANCE. A revision is placed in time
        only by its OWN `source_updated_at` -- when the source says the revision became
        true. `event_time` (the scheduled start) is NOT a substitute for it: that is a
        fact about the GAME, not about when we could have known the revision. Using it
        lets a historical cutoff be answered with a revision that did not yet exist,
        and the answer would carry no evidence that the revision was ever knowable --
        which makes a later leakage or CLV audit meaningless. Whenever any revision
        lacks `source_updated_at`, no revision's in-force-ness can be established, so
        this raises rather than returning an unsupported answer.
        """
        cutoff_utc = identity.utc(cutoff) if isinstance(cutoff, str) else cutoff
        for record in self._revisions:
            if record.get('source_updated_at') is None:
                raise identity.Quarantine(identity.REVISION_TIME_UNKNOWN, {
                    'league_game_id': self.league_game_id,
                    'revision': record['revision'],
                    'detail': 'revision has no source_updated_at; event_time is not '
                              'evidence of when the revision became available'})
        in_force = None
        for record in self._revisions:
            if record['source_updated_at'] <= cutoff_utc:
                in_force = record
        return in_force

    def verify_digests(self):
        """Every stored revision must still hash to its recorded digest."""
        for record in self._revisions:
            if digest(record) != record['revision_digest']:
                raise ValueError('Revision digest mismatch for %s r%d'
                                 % (self.league_game_id, record['revision']))
        return True

    def has_cycles(self):
        """Revisions are an ordered list, so a cycle is impossible by construction.
        Asserted rather than assumed, because a future rewrite must not break it."""
        seen, revision = set(), 1
        for record in self._revisions:
            if record['revision'] in seen:
                return True
            if record['revision'] != revision:
                return True
            seen.add(record['revision'])
            revision += 1
        return False


def reschedule(chain, game, phase, cup_round, event_time, status=RESCHEDULED,
               actual_venue=None, neutral_site=None, source_updated_at=None):
    """Append a reschedule/postponement revision to a chain.

    VENUE FACTS CARRY FORWARD UNLESS EXPLICITLY OVERRIDDEN. Both `actual_venue` and
    `neutral_site` default to None, meaning "unchanged". A `False` default for
    neutral_site is indistinguishable from a caller saying "this game is no longer at
    a neutral site", so with one, any revision that did not mention the venue would
    silently clear a prior neutral_site=True -- a venue property lost by omission,
    inside a chain that promises nothing is lost. Pass an explicit value (including
    False) to change it.
    """
    previous = chain.current()
    if previous is None:
        raise ValueError('Cannot revise a game with no prior revision')
    record = schedule_record(
        game, phase, cup_round, status, event_time,
        actual_venue=actual_venue if actual_venue is not None else previous['actual_venue'],
        neutral_site=neutral_site if neutral_site is not None else previous['neutral_site'],
        revision=previous['revision'] + 1, supersedes=previous['revision'],
        source_updated_at=source_updated_at)
    return chain.append(record)


def cancelled(chain, game, phase, cup_round, event_time, source_updated_at=None):
    """Mark a game cancelled. The game REMAINS in the chain and in any denominator.

    VENUE FACTS ARE INHERITED FROM THE LATEST REVISION, exactly as reschedule() does.
    A cancellation states the game's STATUS, not where it would have been played, so
    it must not clear venue it never mentioned: by taking schedule_record's defaults it
    wrote neutral_site=False and actual_venue=None over whatever the chain already
    held -- the same loss-by-omission as the reschedule default, inside a chain whose
    contract is that nothing is lost.

    A game with no prior revision has nothing to inherit and keeps the record defaults.
    `source_updated_at` is passed through verbatim: when the caller supplies none, the
    revision records none, so an availability time is never invented here.
    """
    previous = chain.current()
    revision = (previous['revision'] + 1) if previous else 1
    record = schedule_record(game, phase, cup_round, CANCELLED, event_time,
                             actual_venue=previous['actual_venue'] if previous else None,
                             neutral_site=previous['neutral_site'] if previous else False,
                             revision=revision,
                             supersedes=previous['revision'] if previous else None,
                             source_updated_at=source_updated_at)
    return chain.append(record)


# ------------------------------------------------------------ pregame integrity

def chain_pregame_start(chain, cutoff=None):
    """The effective start from the schedule revision IN FORCE.

    Uses the current revision, or the revision in force at `cutoff` when one is
    given. It deliberately does NOT scan for the earliest start in the chain: a
    rescheduled game would then be judged against its obsolete original time and a
    legitimately pregame decision would be rejected (defect-1 review requirement).

    Raises identity.Quarantine when no revision is in force, when the revision in
    force carries no usable start time, or when a revision carries no
    source_updated_at at all (REVISION_TIME_UNKNOWN -- see RevisionChain.as_of).
    """
    record = chain.as_of(cutoff) if cutoff is not None else chain.current()
    if record is None:
        raise identity.Quarantine(identity.START_TIME_UNKNOWN,
                                  {'detail': 'no schedule revision in force'})
    return identity.effective_start_iso(record.get('event_time'), record.get('actual_start_time'))


def pregame_eligible(chain, cutoff, actual_start=None, available_at=None, ingested_at=None):
    """(eligible, reasons) for a pregame decision on this game at this cutoff.

    Combines two independent rules and reports every reason it fails, rather than
    stopping at the first:

      1. the cutoff must precede the effective start (chain_pregame_start)
      2. any supplied availability/ingestion must precede the cutoff

    `actual_start` overrides the revision's recorded actual tipoff when the caller
    has fresher confirmation. Missing evidence yields ineligible with a reason --
    never a silent pass.
    """
    reasons = []
    if cutoff is None:
        record = chain.current()
    else:
        try:
            record = chain.as_of(cutoff)
        except identity.Quarantine as blocked:
            # An undatable revision supports no pregame claim at all, so fail closed
            # with the reason instead of guessing which revision was in force.
            return False, [blocked.reason]
    if record is None:
        return False, [identity.START_TIME_UNKNOWN]
    actual = actual_start if actual_start is not None else record.get('actual_start_time')
    try:
        identity.require_pregame(cutoff, record.get('event_time'), actual)
    except identity.Quarantine as blocked:
        reasons.append(blocked.reason)
    if available_at is not None or ingested_at is not None:
        usable, _anomalies = identity.blocks_from_features(available_at, ingested_at, cutoff)
        if not usable:
            reasons.append(identity.AVAILABILITY_AFTER_CUTOFF)
    return (not reasons), reasons
