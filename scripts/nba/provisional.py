"""Provisional schedule slots: durable placeholder identities and their resolutions.

VERSION `nba-provisional-v0.1`. New module, own version id, own exception type, own
reason registry.

WHY THIS EXISTS. The 2026-27 schedule publishes fixtures whose participants are not yet
known -- the two regular-season games per team assigned after NBA Cup Group Play, and the
Knockout Round fixtures themselves. Package 2A requires a league-issued game ID and two
distinct team IDs before a game is canonical, so such a fixture cannot be a game. It is a
PUBLISHED EXPECTATION, and it gets its own namespace.

FOUR RULES

  1. A SLOT IS NOT A GAME. A slot carries no league-issued identifier and no participant
     identifiers. It must be structurally unable to reach `identity.canonical_game()`: the
     only path from slot to game presents a candidate identity to Package 2A's authority,
     which may refuse, and requires independent evidence before it will even do that.

  2. SLOT IDENTITIES ARE DURABLE. A slot id is minted from competition + structural
     position (+ an anchor only where that anchor is itself known and immutable). Dates,
     tip-off times, assignment windows, venues and opponents are NOT key material: each of
     them changes during the life of the expectation, and keying on any would re-identify
     the expectation underneath the ledger.

  3. EXPECTATIONS ARE DECLARED, NEVER COUNTED. A completeness denominator inferred from
     what we captured can never detect a missing slot, because the missing slot is inside
     the number that would have revealed it. `expectation_from_observed_slots()` exists
     solely to refuse that move.

  4. CARDINALITY IS 0..n. A slot may resolve to nothing, to one game, to several (a
     replaced record retained alongside its successor), or to another slot. The relation
     is many-to-many-capable; no field on a slot is nullable-to-mean-resolution.

REASON REGISTRY. Own codes, deliberately NOT added to `identity.QUARANTINE_REASONS`, which
is Package 2A contract. Disjointness is pinned by test.

SYNTHETIC ONLY. Nothing here fetches, stores or publishes provider data; no adapter exists.
"""
import copy
import hashlib
import json
import re

from . import identity

VERSION = 'nba-provisional-v0.1'

# --- competitions (this module's own frozen set) -------------------------------
CUP_KNOCKOUT = 'CUP_KNOCKOUT'
REGULAR_ASSIGNMENT_WINDOW = 'REGULAR_ASSIGNMENT_WINDOW'
COMPETITIONS = frozenset({CUP_KNOCKOUT, REGULAR_ASSIGNMENT_WINDOW})

# --- derived slot states ------------------------------------------------------
SLOT_PENDING = 'pending'
SLOT_RESOLVED = 'resolved'
SLOT_RESOLVED_EMPTY = 'resolved_empty'
SLOT_SUPERSEDED = 'superseded'
SLOT_STATES = frozenset({SLOT_PENDING, SLOT_RESOLVED, SLOT_RESOLVED_EMPTY, SLOT_SUPERSEDED})

# --- resolution roles and targets --------------------------------------------
ROLE_ORIGIN = 'origin'
ROLE_SUCCESSOR = 'successor'
ROLE_ALTERNATE = 'alternate'
ROLES = frozenset({ROLE_ORIGIN, ROLE_SUCCESSOR, ROLE_ALTERNATE})

TARGET_GAME = 'league_game'
TARGET_SLOT = 'slot'
TARGET_EMPTY = 'empty'
TARGET_KINDS = frozenset({TARGET_GAME, TARGET_SLOT, TARGET_EMPTY})

# --- reason registry (ours; see module docstring) -----------------------------
MISSING_FIELD = 'MISSING_FIELD'
MISSING_POSITION = 'MISSING_POSITION'
MISSING_EVIDENCE = 'MISSING_EVIDENCE'
INVALID_SLOT_ID = 'INVALID_SLOT_ID'
DATE_BEARING_POSITION = 'DATE_BEARING_POSITION'
PLACEHOLDER_ANCHOR = 'PLACEHOLDER_ANCHOR'
SLOT_KEY_AS_IDENTITY = 'SLOT_KEY_AS_IDENTITY'
UNVERIFIED_IDENTITY = 'UNVERIFIED_IDENTITY'
UNKNOWN_SLOT = 'UNKNOWN_SLOT'
UNKNOWN_EXPECTATION = 'UNKNOWN_EXPECTATION'
DUPLICATE_IDENTIFIER = 'DUPLICATE_IDENTIFIER'
DUPLICATE_RESOLUTION = 'DUPLICATE_RESOLUTION'
MULTIPLE_ORIGINS = 'MULTIPLE_ORIGINS'
CONTRADICTORY_RESOLUTION = 'CONTRADICTORY_RESOLUTION'
SLOT_ALREADY_SUPERSEDED = 'SLOT_ALREADY_SUPERSEDED'
SLOT_SUPERSESSION_CYCLE = 'SLOT_SUPERSESSION_CYCLE'
COMPETITION_MISMATCH = 'COMPETITION_MISMATCH'
EXPECTATION_ALREADY_SUPERSEDED = 'EXPECTATION_ALREADY_SUPERSEDED'
SLOT_ACCOUNTING_GAP = 'SLOT_ACCOUNTING_GAP'
DECLARATION_WITHOUT_PROVENANCE = 'DECLARATION_WITHOUT_PROVENANCE'
DERIVED_EXPECTATION_REFUSED = 'DERIVED_EXPECTATION_REFUSED'
INVALID_ROLE = 'INVALID_ROLE'
DIGEST_MISMATCH = 'DIGEST_MISMATCH'

PROVISIONAL_REASONS = frozenset({
    MISSING_FIELD, MISSING_POSITION, MISSING_EVIDENCE, INVALID_SLOT_ID,
    DATE_BEARING_POSITION, PLACEHOLDER_ANCHOR, SLOT_KEY_AS_IDENTITY, UNVERIFIED_IDENTITY,
    UNKNOWN_SLOT, UNKNOWN_EXPECTATION, DUPLICATE_IDENTIFIER, DUPLICATE_RESOLUTION,
    MULTIPLE_ORIGINS, CONTRADICTORY_RESOLUTION, SLOT_ALREADY_SUPERSEDED,
    SLOT_SUPERSESSION_CYCLE, COMPETITION_MISMATCH, EXPECTATION_ALREADY_SUPERSEDED,
    SLOT_ACCOUNTING_GAP, DECLARATION_WITHOUT_PROVENANCE, DERIVED_EXPECTATION_REFUSED,
    INVALID_ROLE, DIGEST_MISMATCH,
})

# Structural positions are single uppercase tokens. Prose, venues, opponent phrases and
# lowercase names are not structural positions and are refused.
_POSITION_RE = re.compile(r'^[A-Z0-9][A-Z0-9/._-]*$')
_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})|(\d{8})|(\d{1,2}/\d{1,2})')
_PLACEHOLDERS = frozenset({'TBD', 'TBA', 'UNKNOWN', 'PENDING', 'NONE', 'N/A', '*', '-', '?'})
_OBS_PREFIX = 'obs.'


class ProvisionalError(Exception):
    """A provisional-slot operation that cannot be honoured, with a stable reason code."""

    def __init__(self, reason, detail=None):
        if reason not in PROVISIONAL_REASONS:
            raise ValueError('Unknown provisional reason: %r' % (reason,))
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)

    def as_dict(self):
        return {'reason': self.reason, 'detail': dict(self.detail)}


# ---------------------------------------------------------------- slot identity
def mint_slot_id(competition, position, anchor=None):
    """Mint a durable slot id. Pure, deterministic, and free of mutable facts.

    `competition:position` or `competition:anchor:position`. Nothing date-shaped,
    venue-shaped or opponent-shaped may enter: see the module docstring rule 2.
    """
    if competition not in COMPETITIONS:
        raise ProvisionalError(INVALID_SLOT_ID, {'competition': competition,
                                                 'known': sorted(COMPETITIONS)})
    if not isinstance(position, str) or not position.strip():
        raise ProvisionalError(MISSING_POSITION, {'position': position})
    position = position.strip()
    if _DATE_RE.search(position):
        raise ProvisionalError(DATE_BEARING_POSITION, {'position': position})
    if not _POSITION_RE.match(position):
        raise ProvisionalError(INVALID_SLOT_ID, {'position': position,
                                                 'detail': 'structural positions are single tokens'})
    if anchor is not None:
        if not isinstance(anchor, str) or not anchor.strip():
            raise ProvisionalError(MISSING_FIELD, {'field': 'anchor'})
        anchor = anchor.strip()
        if anchor.upper() in _PLACEHOLDERS or _DATE_RE.search(anchor):
            raise ProvisionalError(PLACEHOLDER_ANCHOR, {'anchor': anchor})
        return '%s:%s:%s' % (competition, anchor, position)
    return '%s:%s' % (competition, position)


def validate_slot_id(slot_id):
    """Accept only ids this module could have minted. Foreign keys are not slots."""
    if not isinstance(slot_id, str) or not slot_id.strip():
        raise ProvisionalError(INVALID_SLOT_ID, {'slot_id': slot_id})
    slot_id = slot_id.strip()
    if slot_id.startswith(_OBS_PREFIX) or re.match(r'^\d{8,}$', slot_id):
        raise ProvisionalError(INVALID_SLOT_ID, {'slot_id': slot_id,
                                                 'detail': 'not a provisional slot key'})
    parts = slot_id.split(':')
    if len(parts) == 2:
        competition, (anchor, position) = parts[0], (None, parts[1])
    elif len(parts) == 3:
        competition, anchor, position = parts
    else:
        raise ProvisionalError(INVALID_SLOT_ID, {'slot_id': slot_id})
    if mint_slot_id(competition, position, anchor=anchor) != slot_id:
        raise ProvisionalError(INVALID_SLOT_ID, {'slot_id': slot_id})
    return slot_id


def _require_evidence(evidence):
    """Independent evidence for an assignment. Required -- never inferred, never assumed."""
    if not isinstance(evidence, dict):
        raise ProvisionalError(MISSING_EVIDENCE, {'evidence': evidence})
    if not str(evidence.get('url') or '').strip() or not str(evidence.get('retrieved_at') or '').strip():
        raise ProvisionalError(MISSING_EVIDENCE, {
            'evidence': dict(evidence),
            'detail': "an assignment needs evidence.url and evidence.retrieved_at"})
    return dict(evidence)


def _refuse_slot_key_as_identity(candidate):
    """A slot key (or an observation key) may never be presented as a league game id."""
    if not isinstance(candidate, str):
        return
    if ':' in candidate or candidate.startswith(_OBS_PREFIX):
        raise ProvisionalError(SLOT_KEY_AS_IDENTITY, {'candidate': candidate})


def slot_digest(record):
    payload = {k: v for k, v in sorted(record.items()) if k != 'slot_digest'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def expectation_digest(record):
    payload = {k: v for k, v in sorted(record.items()) if k != 'expectation_digest'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def resolution_digest(record):
    payload = {k: v for k, v in sorted(record.items()) if k != 'resolution_digest'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def expectation_from_observed_slots(*_args, **_kwargs):
    """REFUSED BY DESIGN. Declaring expectations from captures makes the denominator circular.

    An expectation inferred from what we captured can never detect a missing slot, because
    the missing slot is inside the number that would have revealed it. This function exists
    so that the forbidden move fails loudly and is pinned by test, rather than being
    quietly available to a future caller.
    """
    raise ProvisionalError(DERIVED_EXPECTATION_REFUSED, {
        'detail': 'expected slots must be declared from an independent source, never counted '
                  'from captured observations'})


class SlotRegistry:
    """Append-only slots, expectations and resolutions. Nothing is ever mutated."""

    def __init__(self):
        self._slots = []
        self._expectations = []
        self._resolutions = []

    # ------------------------------------------------------------------ slots
    def declare_slot(self, *, competition, position, provisional_reason,
                     first_observed_observation_id, anchor=None,
                     expected_window_start=None, expected_window_end=None,
                     provider_event_label=None):
        if not isinstance(provisional_reason, str) or not provisional_reason.strip():
            raise ProvisionalError(MISSING_FIELD, {'field': 'provisional_reason'})
        if not isinstance(first_observed_observation_id, str) or not first_observed_observation_id.strip():
            raise ProvisionalError(MISSING_FIELD, {'field': 'first_observed_observation_id'})
        slot_id = mint_slot_id(competition, position, anchor=anchor)
        if any(s['slot_id'] == slot_id for s in self._slots):
            raise ProvisionalError(DUPLICATE_IDENTIFIER, {'slot_id': slot_id})
        record = {
            'slot_version': VERSION,
            'slot_id': slot_id,
            'competition': competition,
            'position': position.strip(),
            'anchor': anchor,
            # Attributes, not key material -- the window may move without re-identifying.
            'expected_window_start': expected_window_start,
            'expected_window_end': expected_window_end,
            'provisional_reason': provisional_reason.strip(),
            'provider_event_label': provider_event_label,
            'first_observed_observation_id': first_observed_observation_id.strip(),
        }
        record['slot_digest'] = slot_digest(record)
        self._slots.append(record)
        return copy.deepcopy(record)

    @property
    def slots(self):
        return tuple(copy.deepcopy(s) for s in self._slots)

    def _slot(self, slot_id):
        for record in self._slots:
            if record['slot_id'] == slot_id:
                return record
        raise ProvisionalError(UNKNOWN_SLOT, {'slot_id': slot_id})

    # ----------------------------------------------------------- declarations
    def declare_expectation(self, *, competition, declared_slots, source_ref, observation_id,
                            declared_at, supersedes_expectation_id=None):
        """Declare which slot identities are expected. Provenance is mandatory."""
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise ProvisionalError(DECLARATION_WITHOUT_PROVENANCE, {'source_ref': source_ref})
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ProvisionalError(DECLARATION_WITHOUT_PROVENANCE, {'observation_id': observation_id})
        if competition not in COMPETITIONS:
            raise ProvisionalError(INVALID_SLOT_ID, {'competition': competition})
        slots = [validate_slot_id(s) for s in (declared_slots or [])]
        if not slots:
            raise ProvisionalError(MISSING_FIELD, {'field': 'declared_slots'})
        if len(set(slots)) != len(slots):
            raise ProvisionalError(DUPLICATE_IDENTIFIER, {'declared_slots': slots})
        # Each competition has its own denominator, so a declaration may only cover slots
        # belonging to the competition being declared. Otherwise the two reports would
        # overlap and both would be wrong, silently.
        foreign = sorted(s for s in slots if s.split(':', 1)[0] != competition)
        if foreign:
            raise ProvisionalError(COMPETITION_MISMATCH, {
                'competition': competition, 'foreign_slots': foreign})
        if supersedes_expectation_id is not None:
            predecessor = next((e for e in self._expectations
                                if e['expectation_id'] == supersedes_expectation_id), None)
            if predecessor is None:
                raise ProvisionalError(UNKNOWN_EXPECTATION,
                                       {'expectation_id': supersedes_expectation_id})
            if predecessor['competition'] != competition:
                # A cross-competition supersession would release another competition's
                # declared slots and corrupt a denominator this declaration is not about.
                raise ProvisionalError(COMPETITION_MISMATCH, {
                    'expectation_id': supersedes_expectation_id,
                    'competition': competition,
                    'predecessor_competition': predecessor['competition']})
            if supersedes_expectation_id in self._superseded_expectations():
                # A predecessor must still be LIVE. Superseding an already-superseded record
                # would release slots that its replacement now owns, orphaning it.
                raise ProvisionalError(EXPECTATION_ALREADY_SUPERSEDED, {
                    'expectation_id': supersedes_expectation_id})
        live = self._live_declared(competition)
        if supersedes_expectation_id is not None:
            # A supersession RELEASES the slots its predecessor declared; otherwise a
            # re-declaration of the same slot would clash with the very record it replaces.
            for record in self._expectations:
                if record['expectation_id'] == supersedes_expectation_id:
                    live -= set(record['declared_slots'])
        clash = sorted(set(slots) & live)
        if clash:
            raise ProvisionalError(DUPLICATE_IDENTIFIER, {
                'slot_ids': clash,
                'detail': 'already declared by a live expectation; supersede it instead'})
        record = {
            'expectation_version': VERSION,
            'expectation_id': 'EXP-%s-%03d' % (competition, len(self._expectations) + 1),
            'competition': competition,
            'declared_slots': slots,
            'source_ref': source_ref.strip(),
            'observation_id': observation_id.strip(),
            'declared_at': declared_at,
            'supersedes_expectation_id': supersedes_expectation_id,
        }
        record['expectation_digest'] = expectation_digest(record)
        self._expectations.append(record)
        return copy.deepcopy(record)

    @property
    def expectations(self):
        return tuple(copy.deepcopy(e) for e in self._expectations)

    def _superseded_expectations(self):
        return {e['supersedes_expectation_id'] for e in self._expectations
                if e['supersedes_expectation_id']}

    def _live_declared(self, competition=None):
        dead = self._superseded_expectations()
        declared = set()
        for record in self._expectations:
            if record['expectation_id'] in dead:
                continue
            if competition is not None and record['competition'] != competition:
                continue
            declared.update(record['declared_slots'])
        return declared

    # ------------------------------------------------------------ resolutions
    def resolve_to_game(self, slot_id, *, league_game_id, home_team_id, away_team_id,
                        observation_id, evidence, role=ROLE_ORIGIN):
        """Record that a slot resolved to a canonical game. The game is NOT created here.

        The candidate identity is presented to Package 2A's authority, which may refuse;
        and it must be supported by independent evidence before it is presented at all.
        """
        self._guard_resolution(slot_id, role)
        _refuse_slot_key_as_identity(league_game_id)
        proven = _require_evidence(evidence)
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ProvisionalError(MISSING_EVIDENCE, {'field': 'observation_id'})
        try:
            canonical = identity.canonical_game({
                'league_game_id': league_game_id,
                'home_team_id': home_team_id,
                'away_team_id': away_team_id})
        except identity.Quarantine as refused:
            raise ProvisionalError(UNVERIFIED_IDENTITY, {
                'package2a_reason': refused.reason, 'package2a_detail': dict(refused.detail)})
        return self._append_resolution(slot_id, kind=TARGET_GAME,
                                       target_ref=canonical['league_game_id'],
                                       role=role, observation_id=observation_id, evidence=proven)

    def _supersession_edges(self):
        """Recorded slot -> slot retirement edges."""
        return {r['slot_id']: r['target_ref'] for r in self._resolutions
                if r['target_kind'] == TARGET_SLOT}

    def _reaches(self, start, goal):
        """Is `goal` reachable from `start` by following retirement edges?"""
        edges = self._supersession_edges()
        seen, frontier = set(), [start]
        while frontier:
            node = frontier.pop()
            if node == goal:
                return True
            if node in seen:
                continue
            seen.add(node)
            nxt = edges.get(node)
            if nxt is not None:
                frontier.append(nxt)
        return False

    def resolve_to_slot(self, slot_id, *, target_slot_id, observation_id, evidence,
                        role=ROLE_ALTERNATE):
        """Retire a slot in favour of a re-issued one. Slot -> slot, not slot -> game."""
        self._guard_resolution(slot_id, role)
        self._slot(target_slot_id)
        if target_slot_id == slot_id:
            raise ProvisionalError(SLOT_SUPERSESSION_CYCLE, {
                'slot_id': slot_id, 'detail': 'a slot cannot be retired in favour of itself'})
        if self._reaches(target_slot_id, slot_id):
            # Accepting this edge would close a loop, leaving every slot on it unorderable
            # (each retired in favour of the next, forever).
            raise ProvisionalError(SLOT_SUPERSESSION_CYCLE, {
                'slot_id': slot_id, 'target_slot_id': target_slot_id,
                'detail': 'this retirement would close a cycle'})
        proven = _require_evidence(evidence)
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ProvisionalError(MISSING_EVIDENCE, {'field': 'observation_id'})
        return self._append_resolution(slot_id, kind=TARGET_SLOT, target_ref=target_slot_id,
                                       role=role, observation_id=observation_id, evidence=proven)

    def resolve_empty(self, slot_id, *, observation_id, evidence):
        """Record an EVIDENCED zero: the fixture was withdrawn, or became unnecessary.

        A zero must be evidenced and explicit. An unevidenced empty is indistinguishable
        from "not yet resolved", and a report that cannot tell those apart cannot detect
        loss.
        """
        self._guard_resolution(slot_id, None)
        proven = _require_evidence(evidence)
        if not isinstance(observation_id, str) or not observation_id.strip():
            raise ProvisionalError(MISSING_EVIDENCE, {'field': 'observation_id'})
        return self._append_resolution(slot_id, kind=TARGET_EMPTY, target_ref=None,
                                       role=None, observation_id=observation_id, evidence=proven)

    def _guard_resolution(self, slot_id, role):
        self._slot(slot_id)
        if role is not None and role not in ROLES:
            raise ProvisionalError(INVALID_ROLE, {'role': role, 'known': sorted(ROLES)})
        if any(r['target_kind'] == TARGET_SLOT for r in self.resolutions_for(slot_id)):
            raise ProvisionalError(SLOT_ALREADY_SUPERSEDED, {
                'slot_id': slot_id, 'detail': 'this slot was retired in favour of another'})

    def _append_resolution(self, slot_id, *, kind, target_ref, role, observation_id, evidence):
        existing = [r for r in self._resolutions if r['slot_id'] == slot_id]
        # The same target may not be recorded twice, whatever role it was given.
        if any(r['target_kind'] == kind and r['target_ref'] == target_ref for r in existing):
            raise ProvisionalError(DUPLICATE_RESOLUTION, {
                'slot_id': slot_id, 'target_ref': target_ref, 'role': role})
        # A slot resolves to nothing, or to something -- not both. And once it has resolved
        # to a canonical game it cannot be retired in favour of another slot: the game it
        # points at already exists, so re-pointing the slot would orphan a canonical record.
        conflicting = ((TARGET_GAME, TARGET_EMPTY), (TARGET_EMPTY, TARGET_GAME),
                       (TARGET_SLOT, TARGET_EMPTY), (TARGET_SLOT, TARGET_GAME),
                       (TARGET_EMPTY, TARGET_SLOT))
        conflict = next((prior for wanted, prior in conflicting
                         if wanted == kind
                         and any(r['target_kind'] == prior for r in existing)), None)
        if conflict is not None:
            raise ProvisionalError(CONTRADICTORY_RESOLUTION, {
                'slot_id': slot_id, 'target_kind': kind, 'conflicts_with': conflict})
        if kind == TARGET_GAME and role == ROLE_ORIGIN and \
                any(r['target_kind'] == TARGET_GAME and r['role'] == ROLE_ORIGIN for r in existing):
            # n > 1 is legal (origin + successors); two origins is not -- it would leave
            # "which record did this slot originally resolve to?" unanswerable.
            raise ProvisionalError(MULTIPLE_ORIGINS, {'slot_id': slot_id})
        record = {
            'resolution_version': VERSION,
            'resolution_id': 'RES-%s-%03d' % (slot_id.replace(':', '_'), len(self._resolutions) + 1),
            'slot_id': slot_id,
            'target_kind': kind,
            'target_ref': target_ref,
            'role': role,
            'observation_id': observation_id.strip(),
            # Deep copy: caller-supplied evidence is nested in practice, and a shallow copy
            # would leave stored evidence aliased to a dict the caller still holds.
            'evidence': copy.deepcopy(evidence),
        }
        record['resolution_digest'] = resolution_digest(record)
        self._resolutions.append(record)
        return copy.deepcopy(record)

    @property
    def resolutions(self):
        return tuple(copy.deepcopy(r) for r in self._resolutions)

    def resolutions_for(self, slot_id):
        return tuple(r for r in self.resolutions if r['slot_id'] == slot_id)

    def verify_digests(self):
        for record in self._slots:
            if slot_digest(record) != record['slot_digest']:
                raise ProvisionalError(DIGEST_MISMATCH, {'slot_id': record['slot_id']})
        for record in self._expectations:
            if expectation_digest(record) != record['expectation_digest']:
                raise ProvisionalError(DIGEST_MISMATCH, {'expectation_id': record['expectation_id']})
        # Resolutions carry a digest too, and are part of the same integrity surface: a
        # tampered target, role or evidence must not escape detection.
        for record in self._resolutions:
            if resolution_digest(record) != record['resolution_digest']:
                raise ProvisionalError(DIGEST_MISMATCH, {'resolution_id': record['resolution_id']})
        return True

    # ------------------------------------------------------------------- state
    def slot_state(self, slot_id):
        """Derived from the resolution relation -- never stored on, or written to, the slot."""
        self._slot(slot_id)
        mine = self.resolutions_for(slot_id)
        if any(r['target_kind'] == TARGET_SLOT for r in mine):
            return SLOT_SUPERSEDED
        if any(r['target_kind'] == TARGET_GAME for r in mine):
            return SLOT_RESOLVED
        if any(r['target_kind'] == TARGET_EMPTY for r in mine):
            return SLOT_RESOLVED_EMPTY
        return SLOT_PENDING


def reconcile_slots(registry, competition=None):
    """Reconcile declared slots against observed slots -- SEPARATE from the game denominator.

    The canonical game count and its exclusive buckets are not represented here at all.
    The overall completeness claim is the CONJUNCTION of this report and the canonical one,
    never a merged number.
    """
    slots = [s for s in registry.slots if competition in (None, s['competition'])]
    observed = sorted(s['slot_id'] for s in slots)
    declared = sorted(registry._live_declared(competition))

    states = {}
    multi = []
    for slot_id in observed:
        states.setdefault(registry.slot_state(slot_id), []).append(slot_id)
    for slot_id in observed:
        games = [r for r in registry.resolutions_for(slot_id) if r['target_kind'] == TARGET_GAME]
        if len(games) > 1:
            # n > 1 is legal, but it is always reported so a consumer cannot miss it.
            multi.append({'slot_id': slot_id, 'games': len(games)})

    missing = sorted(set(declared) - set(observed))
    undeclared = sorted(set(observed) - set(declared))
    report = {
        'version': VERSION,
        'competition': competition or 'ALL',
        'declared_slots': declared,
        'observed_slots': observed,
        'pending': states.get(SLOT_PENDING, []),
        'resolved': states.get(SLOT_RESOLVED, []),
        'resolved_empty': states.get(SLOT_RESOLVED_EMPTY, []),
        'superseded': states.get(SLOT_SUPERSEDED, []),
        'multi_resolved': multi,
        'missing_vs_declaration': missing,
        'undeclared_observations': undeclared,
        'reconciles': not missing and not undeclared,
    }
    report['detail'] = {
        'declared_total': len(declared),
        'observed_total': len(observed),
        'missing_total': len(missing),
        'undeclared_total': len(undeclared),
    }
    return report


def assert_slots_lossless(report):
    """Raise unless every declared slot is pending, resolved or evidenced-empty."""
    if not report.get('reconciles'):
        raise ProvisionalError(SLOT_ACCOUNTING_GAP, {
            'missing_vs_declaration': report.get('missing_vs_declaration'),
            'undeclared_observations': report.get('undeclared_observations'),
            'detail': 'declared and observed slot sets disagree'})
    return True
