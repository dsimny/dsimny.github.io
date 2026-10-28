"""Observation records: distinct capture identity, separate clocks, cutoff-bounded replay.

VERSION `nba-observation-v0.1`. New module, own version id, own reason registry.

WHAT THIS IS FOR. The ledger has to answer one question without inventing a fact: *at
cutoff T, which observations did we hold?* This module shapes the records that answer it.

THREE RULES THIS MODULE ENFORCES

  1. AN OBSERVATION IS NOT ITS PAYLOAD. `observation_id` identifies the ACT of observing
     and is unique per capture event; `payload_sha256` describes the CONTENT and may
     legitimately repeat. Two byte-identical polls are two observations. Collapsing them
     by content hash would destroy the temporal record and make "we polled and nothing had
     changed" unprovable. Storage may deduplicate the payload BLOB by hash; observations
     are never deduplicated.

  2. ONE FIELD PER FACT, CLASSIFIED BY WHOSE FACT IT IS. Provider claims
     (`source_published_at`, `source_updated_at`) are somebody else's statements and are
     independently nullable. Our own clocks -- request, receipt, storage, availability --
     are facts about us. No field is ever derived from another, and in particular
     `available_at` is DECLARED by the pipeline, never inferred from `ingested_at`.

  3. FAIL CLOSED, RETAIN THE EVIDENCE. An observation that is not eligible at a cutoff is
     excluded from the decision and REPORTED with a reason. It is never silently dropped:
     ineligibility is not deletion.

REASON REGISTRY. This module registers its own reason codes rather than adding to
`identity.QUARANTINE_REASONS`, which is Package 2A contract -- adding to it would force a
version bump this milestone is not authorised to make. The two registries are disjoint by
test.

PURE. No network, no database, no credentials, no file I/O. Payloads are held in memory.
Nothing here is published, and nothing derived from a licensed source may be.
"""
import copy
import hashlib
import json
import re
from datetime import datetime, timezone

from . import identity

VERSION = 'nba-observation-v0.1'

# --- reason registry (ours; see module docstring) -----------------------------
MISSING_FIELD = 'MISSING_FIELD'
CLOCK_MISSING = 'CLOCK_MISSING'
CLOCK_INVALID = 'CLOCK_INVALID'
CLOCK_OUT_OF_ORDER = 'CLOCK_OUT_OF_ORDER'
AFTER_CUTOFF = 'AFTER_CUTOFF'
UNKNOWN_OBSERVATION = 'UNKNOWN_OBSERVATION'
UNKNOWN_PAYLOAD = 'UNKNOWN_PAYLOAD'
DUPLICATE_OBSERVATION_ID = 'DUPLICATE_OBSERVATION_ID'
WRITER_MISMATCH = 'WRITER_MISMATCH'
PAYLOAD_HASH_MISMATCH = 'PAYLOAD_HASH_MISMATCH'
INVALID_PAYLOAD_HASH = 'INVALID_PAYLOAD_HASH'
INVALID_PAYLOAD = 'INVALID_PAYLOAD'
DIGEST_MISMATCH = 'DIGEST_MISMATCH'
# Retention (policy is source-specific; permanent retention is never assumed).
# Three different facts about a payload, three different reasons: it was never stored, it was
# deleted under a named policy, or it is present but no longer matches its recorded hash.
PAYLOAD_NOT_STORED = 'PAYLOAD_NOT_STORED'
PAYLOAD_UNAVAILABLE = 'PAYLOAD_UNAVAILABLE'
PAYLOAD_CORRUPT = 'PAYLOAD_CORRUPT'
ALREADY_DELETED = 'ALREADY_DELETED'

# Retention states, derived from appended tombstones -- never written onto a record.
RETAINED = 'retained'
DELETED = 'deleted'

OBSERVATION_REASONS = frozenset({
    MISSING_FIELD, CLOCK_MISSING, CLOCK_INVALID, CLOCK_OUT_OF_ORDER, AFTER_CUTOFF,
    UNKNOWN_OBSERVATION, UNKNOWN_PAYLOAD, DUPLICATE_OBSERVATION_ID, WRITER_MISMATCH,
    PAYLOAD_HASH_MISMATCH, INVALID_PAYLOAD_HASH, INVALID_PAYLOAD, DIGEST_MISMATCH,
    PAYLOAD_NOT_STORED, PAYLOAD_UNAVAILABLE, PAYLOAD_CORRUPT, ALREADY_DELETED,
})

# Clock taxonomy. `request_started_at` is optional (ordering is still enforced when it is
# given); the other three are required for decision eligibility.
ORDERED_CLOCKS = ('request_started_at', 'response_received_at', 'ingested_at', 'available_at')
REQUIRED_CLOCKS = ('response_received_at', 'ingested_at', 'available_at')
PROVIDER_CLOCKS = ('source_published_at', 'source_updated_at')

_HASH_RE = re.compile(r'^[0-9a-f]{64}$')
_HASH_FIELDS = {'sha256': 'payload_sha256'}


class ObservationError(Exception):
    """A record that cannot be admitted as evidence, with a stable reason code.

    Raising is the fail-closed path: nothing is written for a refused observation.
    """

    def __init__(self, reason, detail=None):
        if reason not in OBSERVATION_REASONS:
            raise ValueError('Unknown observation reason: %r' % (reason,))
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)

    def as_dict(self):
        return {'reason': self.reason, 'detail': dict(self.detail)}


def utc(value, field):
    """An aware UTC datetime for a clock field, or fail closed with a named reason.

    A missing value is CLOCK_MISSING; an unusable one -- naive, non-string, unparseable
    -- is CLOCK_INVALID. Neither is ever guessed at.
    """
    if value is None:
        raise ObservationError(CLOCK_MISSING, {'field': field})
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ObservationError(CLOCK_INVALID, {'field': field, 'detail': 'naive datetime'})
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        raise ObservationError(CLOCK_INVALID, {'field': field, 'detail': 'not an ISO-8601 string'})
    if not value.strip():
        raise ObservationError(CLOCK_MISSING, {'field': field})
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as bad:
        raise ObservationError(CLOCK_INVALID, {'field': field, 'detail': str(bad)})
    if stamp.tzinfo is None:
        raise ObservationError(CLOCK_INVALID, {'field': field, 'detail': 'timezone required'})
    return stamp.astimezone(timezone.utc)


def iso(stamp):
    """Stored timestamp format, matching Package 2A: ISO-8601 UTC with a Z suffix."""
    return identity.iso(stamp)


def content_hash(payload):
    """Content hash of a payload body. Describes CONTENT, never the observation.

    Deliberately NOT named `payload_sha256`: `ObservationLog.append` takes a parameter of
    that name, and a same-named module function would be shadowed inside it.
    """
    if isinstance(payload, str):
        payload = payload.encode()
    if not isinstance(payload, (bytes, bytearray)):
        raise ObservationError(INVALID_PAYLOAD, {'type': type(payload).__name__})
    return hashlib.sha256(bytes(payload)).hexdigest()


def _require_hash(value, field='payload_sha256'):
    if not isinstance(value, str) or not _HASH_RE.match(value):
        raise ObservationError(INVALID_PAYLOAD_HASH, {'field': field, 'value': value})
    return value


def _instant(stamp):
    """A stored clock as an AWARE datetime, for ordering.

    String comparison is not chronological: '2026-11-01T12:00:00.500Z' sorts BEFORE
    '2026-11-01T12:00:00Z' because '.' precedes 'Z'. Ordering must therefore parse.
    A stored clock that cannot be parsed is ordered last rather than crashing a replay --
    refusing a tampered record is verify_digests()'s job, not the replayer's.
    """
    try:
        return utc(stamp, 'clock')
    except ObservationError:
        return datetime.max.replace(tzinfo=timezone.utc)


def observation_digest(record):
    """Stable content hash of an observation, excluding the digest field itself."""
    payload = {k: v for k, v in sorted(record.items()) if k != 'observation_digest'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ObservationLog:
    """An append-only stream of observations written by ONE logical writer.

    Single-writer by construction: every minted id carries the writer's name, and a record
    minted by a different writer is refused rather than admitted. Records are never mutated
    -- a caller's copy is a deep copy, and stored digests still verify after use.
    """

    def __init__(self, writer_id):
        if not isinstance(writer_id, str) or not writer_id.strip():
            raise ObservationError(MISSING_FIELD, {'field': 'writer_id'})
        self.writer_id = writer_id.strip()
        self._records = []
        self._tombstones = []
        self._payloads = {}
        # Blob reference counts, keyed by content hash: which RETAINED observations still
        # require these bytes. See delete_payload().
        self._payload_refs = {}
        self._seq = 0

    # ------------------------------------------------------------------ writing
    def append(self, *, source_id, response_received_at=None, ingested_at=None,
               available_at=None, request_started_at=None, payload=None, payload_sha256=None,
               payload_ref=None, http_status=None, source_published_at=None,
               source_updated_at=None, observation_id=None):
        """Record one capture event. Raises rather than writing anything unusable.

        Every clock defaults to None so that a caller OMITTING one hits the named
        CLOCK_MISSING path rather than a bare TypeError: a missing required clock must
        fail closed with a reason code, not a Python arity error.
        """
        if not isinstance(source_id, str) or not source_id.strip():
            raise ObservationError(MISSING_FIELD, {'field': 'source_id'})

        given = {'request_started_at': request_started_at,
                 'response_received_at': response_received_at,
                 'ingested_at': ingested_at,
                 'available_at': available_at,
                 'source_published_at': source_published_at,
                 'source_updated_at': source_updated_at}

        clocks = {}
        for field in ORDERED_CLOCKS:
            value = given[field]
            clocks[field] = utc(value, field) if value is not None else None
        for field in REQUIRED_CLOCKS:
            if clocks[field] is None:
                raise ObservationError(CLOCK_MISSING, {'field': field})

        # Ordering invariant: request <= receipt <= storage <= availability.
        chain = [(f, clocks[f]) for f in ORDERED_CLOCKS if clocks[f] is not None]
        for (earlier, first), (later, second) in zip(chain, chain[1:]):
            if second < first:
                raise ObservationError(CLOCK_OUT_OF_ORDER, {
                    'field': later, 'must_not_precede': earlier,
                    'value': iso(second), 'earlier': iso(first)})

        providers = {}
        for field in PROVIDER_CLOCKS:
            value = given[field]
            providers[field] = iso(utc(value, field)) if value is not None else None

        body, stored_hash = None, None
        if payload is not None:
            body = payload.encode() if isinstance(payload, str) else payload
            if not isinstance(body, (bytes, bytearray)):
                raise ObservationError(INVALID_PAYLOAD, {'type': type(payload).__name__})
            body = bytes(body)
            stored_hash = content_hash(body)
            if payload_sha256 is not None:
                _require_hash(payload_sha256)
                if payload_sha256 != stored_hash:
                    raise ObservationError(PAYLOAD_HASH_MISMATCH,
                                           {'supplied': payload_sha256, 'computed': stored_hash})
        elif payload_sha256 is not None:
            stored_hash = _require_hash(payload_sha256)
        else:
            raise ObservationError(MISSING_FIELD, {'field': 'payload'})

        if observation_id is None:
            while True:
                self._seq += 1
                candidate = 'obs.%s.%08d' % (self.writer_id, self._seq)
                if not any(r['observation_id'] == candidate for r in self._records):
                    observation_id = candidate
                    break
        else:
            if not isinstance(observation_id, str) or not observation_id.strip():
                raise ObservationError(MISSING_FIELD, {'field': 'observation_id'})
            if not observation_id.startswith('obs.%s.' % self.writer_id):
                raise ObservationError(WRITER_MISMATCH, {
                    'observation_id': observation_id, 'writer_id': self.writer_id,
                    'detail': 'this log has a single logical writer'})
            if any(r['observation_id'] == observation_id for r in self._records):
                raise ObservationError(DUPLICATE_OBSERVATION_ID,
                                       {'observation_id': observation_id})

        record = {
            'observation_version': VERSION,
            'observation_id': observation_id,
            'source_id': source_id.strip(),
            'request_started_at': iso(clocks['request_started_at']) if clocks['request_started_at'] else None,
            'response_received_at': iso(clocks['response_received_at']),
            'ingested_at': iso(clocks['ingested_at']),
            'available_at': iso(clocks['available_at']),
            # Provider claims: nullable, never substituted for one of our clocks.
            'source_published_at': providers['source_published_at'],
            'source_updated_at': providers['source_updated_at'],
            'payload_sha256': stored_hash,
            'payload_size': len(body) if body is not None else None,
            'payload_ref': payload_ref or ('memory:%s' % stored_hash if body is not None else None),
            'http_status': http_status,
        }
        record['observation_digest'] = observation_digest(record)
        self._records.append(record)
        if body is not None:
            # Content-addressed BLOB storage: identical bodies share one blob (see module
            # docstring rule 1 -- observations are still distinct). The blob is
            # reference-counted by the observations that require it, so deleting one
            # observation's payload cannot remove bytes a retained peer still needs.
            self._payloads.setdefault(stored_hash, body)
            self._payload_refs.setdefault(stored_hash, set()).add(observation_id)
        return copy.deepcopy(record)

    # ------------------------------------------------------------------ reading
    @property
    def records(self):
        """Deep copies, so a caller cannot corrupt the stream (deliberately O(n))."""
        return tuple(copy.deepcopy(r) for r in self._records)

    def get(self, observation_id):
        for record in self._records:
            if record['observation_id'] == observation_id:
                return copy.deepcopy(record)
        raise ObservationError(UNKNOWN_OBSERVATION, {'observation_id': observation_id})

    def payload_availability(self, observation_id):
        """(reproducible, reason) for the payload of an observation we hold.

        'Retained' and 'reproducible' are different facts. A hash-only observation is
        retained by policy yet was never stored, so it cannot be reproduced; a tombstoned one
        stays logically inaccessible even while a peer keeps the shared blob alive. Every
        failure mode is named, and none of them is silently reported as available.
        """
        record = self.get(observation_id)
        if self.retention_state(observation_id) == DELETED:
            return False, PAYLOAD_UNAVAILABLE
        digest = record['payload_sha256']
        if digest is None or digest not in self._payloads:
            return False, PAYLOAD_NOT_STORED
        if content_hash(self._payloads[digest]) != digest:
            return False, PAYLOAD_CORRUPT
        return True, None

    def payload_for(self, observation_id):
        """The bytes for a retained observation, verified against the recorded hash."""
        record = self.get(observation_id)
        reproducible, reason = self.payload_availability(observation_id)
        if not reproducible:
            raise ObservationError(reason, {
                'observation_id': observation_id,
                'payload_sha256': record['payload_sha256']})
        return self._payloads[record['payload_sha256']]

    # -------------------------------------------------------------- retention
    @property
    def tombstones(self):
        """Appended retention events, oldest first. Deep copies."""
        return tuple(copy.deepcopy(t) for t in self._tombstones)

    def retention_state(self, observation_id):
        """Derived from appended tombstones. The observation record is never touched."""
        self.get(observation_id)
        if any(t['observation_id'] == observation_id for t in self._tombstones):
            return DELETED
        return RETAINED

    def delete_payload(self, observation_id, *, policy_ref, deleted_at, reason=None):
        """Remove a payload blob and APPEND a tombstone. The record itself is untouched.

        Retention is a policy decision that belongs to the source's licence: there is no
        default, no implied permanence, and no deletion without naming the policy relied on.
        After this, replay still proves WHAT we held (hash and size) but not that we can
        reproduce it.
        """
        record = self.get(observation_id)
        if not isinstance(policy_ref, str) or not policy_ref.strip():
            raise ObservationError(MISSING_FIELD, {'field': 'policy_ref'})
        if deleted_at is None:
            raise ObservationError(MISSING_FIELD, {'field': 'deleted_at'})
        if self.retention_state(observation_id) == DELETED:
            raise ObservationError(ALREADY_DELETED, {'observation_id': observation_id})
        tombstone = {
            'tombstone_version': VERSION,
            'observation_id': observation_id,
            'payload_sha256': record['payload_sha256'],
            'payload_size': record['payload_size'],
            'policy_ref': policy_ref.strip(),
            'reason': reason,
            'deleted_at': iso(utc(deleted_at, 'deleted_at')),
        }
        # Remove the bytes only if this was the last retained observation needing them.
        self._release_payload(record['payload_sha256'], observation_id)
        self._tombstones.append(tombstone)
        return copy.deepcopy(tombstone)

    def _release_payload(self, digest, observation_id):
        """Drop one observation's claim on a blob; return True if the bytes were removed.

        A blob is removed only once NO retained observation still requires it. Deleting one
        observation must never remove bytes a peer is still relying on.
        """
        if digest is None:
            return False
        holders = self._payload_refs.get(digest)
        if holders is None:
            return False
        holders.discard(observation_id)
        if holders:
            return False
        self._payloads.pop(digest, None)
        self._payload_refs.pop(digest, None)
        return True

    def verify_digests(self):
        for record in self._records:
            if observation_digest(record) != record['observation_digest']:
                raise ObservationError(DIGEST_MISMATCH, {'observation_id': record['observation_id']})
        return True

    # ------------------------------------------------------------------ eligibility
    def eligibility(self, observation_id, cutoff):
        """(eligible, reason) for a decision at `cutoff`. Evidence is never discarded."""
        record = self.get(observation_id)
        limit = utc(cutoff, 'cutoff')
        for field in REQUIRED_CLOCKS:
            if record[field] is None:
                return False, CLOCK_MISSING
            if utc(record[field], field) > limit:
                return False, AFTER_CUTOFF
        return True, None

    def replay(self, cutoff, require_payloads=False):
        """Deterministic, cutoff-bounded replay. Same cutoff ⇒ identical output.

        Admits every observation whose required clocks all precede the cutoff, ordered by
        receipt, then storage, then id. Reads nothing above the cutoff and never consults
        current state to fill a gap -- backfilling later knowledge into a past cutoff is
        structurally impossible here.

        `require_payloads=True` demands reproducible payloads -- bytes actually retrievable
        and matching their recorded hash -- so a tombstoned, never-stored or corrupted
        observation is excluded with its own reason rather than silently counted. Either way
        the report states whether payload-dependent replay is available: a deleted payload is
        never represented as reproducible evidence.
        """
        limit = utc(cutoff, 'cutoff')
        admitted, excluded = [], []
        # Ordered by PARSED instants: receipt, then storage, then id. See _instant().
        ordered = sorted(self._records,
                         key=lambda r: (_instant(r['response_received_at']),
                                        _instant(r['ingested_at']), r['observation_id']))
        for record in ordered:
            eligible, reason = self.eligibility(record['observation_id'], cutoff)
            if not eligible:
                excluded.append({'observation_id': record['observation_id'], 'reason': reason})
                continue
            retained = self.retention_state(record['observation_id']) == RETAINED
            reproducible, payload_reason = self.payload_availability(record['observation_id'])
            if require_payloads and not reproducible:
                excluded.append({'observation_id': record['observation_id'],
                                 'reason': payload_reason})
                continue
            admitted.append({
                'observation_id': record['observation_id'],
                'source_id': record['source_id'],
                'response_received_at': record['response_received_at'],
                'ingested_at': record['ingested_at'],
                'available_at': record['available_at'],
                'payload_sha256': record['payload_sha256'],
                'payload_retained': retained,
                'payload_reproducible': reproducible,
                'provider_claims': {'source_published_at': record['source_published_at'],
                                    'source_updated_at': record['source_updated_at']},
            })
        unavailable = sorted(a['observation_id'] for a in admitted
                             if not a['payload_reproducible'])
        report = {
            'version': VERSION,
            'cutoff': iso(limit),
            'order_by': list(('response_received_at', 'ingested_at', 'observation_id')),
            'admitted': admitted,
            'excluded': excluded,
            'cited_observation_ids': [a['observation_id'] for a in admitted],
            'payload_dependent_available': not unavailable,
            'unavailable_payload_observation_ids': unavailable,
        }
        # The digest covers what the replay ASSERTS -- cutoff, order and the admitted
        # evidence -- not the excluded diagnostics. A record appended later, ineligible at
        # this cutoff, must not change what we believed then.
        asserted = {'version': VERSION, 'cutoff': report['cutoff'], 'order_by': report['order_by'],
                    'evidence': [(a['observation_id'], a['response_received_at'], a['ingested_at'],
                                  a['available_at'], a['payload_sha256']) for a in admitted]}
        report['replay_digest'] = hashlib.sha256(
            json.dumps(asserted, sort_keys=True, default=str).encode()).hexdigest()
        return report
