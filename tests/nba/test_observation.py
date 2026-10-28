"""M0 regression tests for the observation record stream (nba-observation-v0.1).

WRITTEN BEFORE THE IMPLEMENTATION. Run against a tree where `nba.observation` does not
exist, every test in this file errors on import -- that is the demonstration that none of
this behaviour existed, not merely that it was untested.

WHAT THIS PINS. Four durable facts the ledger depends on:

  1. An observation and its payload hash are DIFFERENT facts. Two byte-identical responses
     are two observations; collapsing them by content hash would destroy the temporal
     record and make "we polled and nothing had changed" unprovable.
  2. Receipt, storage and availability are separate clocks. `available_at` is MANDATORY and
     declared by the pipeline -- never derived from a neighbour.
  3. Decision eligibility is cutoff-bounded on every required clock, and an ineligible
     observation is RETAINED and reported, never silently discarded.
  4. A deleted payload is not reproducible evidence: payload-dependent replay is marked
     unavailable, while the observation record and its hash stay intact.

SYNTHETIC ONLY. Every payload here is invented bytes. No real NBA-derived data is used.
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import identity as ident
from nba import observation as obs


BASE = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
SYNTHETIC = b'{"synthetic":true,"note":"invented payload, not provider data"}'


def at(hours: Any) -> str:
    """ISO-8601 UTC string, `hours` from BASE. `Any` because the negative tests pass junk."""
    return (BASE + timedelta(hours=float(hours))).isoformat().replace('+00:00', 'Z')


def add(log, received: Any = 0, ingested: Any = 1, available: Any = 2,
        payload: Any = SYNTHETIC, source: Any = 'synthetic.alpha', **kw) -> dict:
    """A well-formed append: request < receipt < ingest < available.

    Parameters are deliberately `Any`: the negative tests pass malformed clock values
    (naive strings, integers) to prove the implementation refuses them.
    """
    kwargs = dict(source_id=source, request_started_at=at(received - 0.5),
                  response_received_at=at(received), ingested_at=at(ingested),
                  available_at=at(available), payload=payload)
    kwargs.update(kw)
    return log.append(**kwargs)


class ObservationIdentity(unittest.TestCase):
    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def test_repeated_identical_payloads_produce_distinct_observation_ids(self):
        first = add(self.log)
        second = add(self.log)
        self.assertNotEqual(first['observation_id'], second['observation_id'])
        # identical content, two pieces of evidence
        self.assertEqual(first['payload_sha256'], second['payload_sha256'])
        self.assertEqual(len(self.log.records), 2)

    def test_identical_payloads_share_one_blob_but_not_one_observation(self):
        first = add(self.log)
        second = add(self.log)
        self.assertEqual(first['payload_sha256'], second['payload_sha256'])
        self.assertIsNot(first, second)
        # storage may deduplicate by content; observations may not be deduplicated
        self.assertEqual(self.log.payload_for(first['observation_id']),
                         self.log.payload_for(second['observation_id']))

    def test_observation_id_is_never_the_payload_hash(self):
        record = add(self.log)
        self.assertNotEqual(record['observation_id'], record['payload_sha256'])
        self.assertNotIn(record['payload_sha256'], record['observation_id'])

    def test_observation_ids_are_unique_across_many_appends(self):
        ids = {add(self.log, payload=('p%d' % i).encode())['observation_id'] for i in range(25)}
        self.assertEqual(len(ids), 25)

    def test_duplicate_observation_id_refused(self):
        first = add(self.log)
        with self.assertRaises(obs.ObservationError) as ctx:
            add(self.log, observation_id=first['observation_id'])
        self.assertEqual(ctx.exception.reason, obs.DUPLICATE_OBSERVATION_ID)

    def test_foreign_writer_id_refused(self):
        # A single logical writer: an id minted by another writer may not be appended here.
        other = obs.ObservationLog(writer_id='w2')
        foreign = add(other)
        with self.assertRaises(obs.ObservationError) as ctx:
            add(self.log, observation_id=foreign['observation_id'])
        self.assertEqual(ctx.exception.reason, obs.WRITER_MISMATCH)

    def test_writer_id_required(self):
        with self.assertRaises(obs.ObservationError):
            obs.ObservationLog(writer_id='')

    def test_source_id_required(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            add(self.log, source='')
        self.assertEqual(ctx.exception.reason, obs.MISSING_FIELD)


class ClockValidation(unittest.TestCase):
    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def test_available_at_is_mandatory(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.append(source_id='synthetic.alpha', request_started_at=at(0),
                            response_received_at=at(0.5), ingested_at=at(1),
                            payload=SYNTHETIC)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_MISSING)
        self.assertEqual(ctx.exception.detail.get('field'), 'available_at')

    def test_available_at_is_never_derived_from_ingested_at(self):
        # Omitting it must fail closed and write NOTHING -- no silent substitution.
        with self.assertRaises(obs.ObservationError):
            self.log.append(source_id='synthetic.alpha', request_started_at=at(0),
                            response_received_at=at(0.5), ingested_at=at(1),
                            payload=SYNTHETIC)
        self.assertEqual(len(self.log.records), 0)

    def test_missing_response_received_at_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.append(source_id='synthetic.alpha', ingested_at=at(1),
                            available_at=at(2), payload=SYNTHETIC)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_MISSING)
        self.assertEqual(ctx.exception.detail.get('field'), 'response_received_at')

    def test_missing_ingested_at_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.append(source_id='synthetic.alpha', response_received_at=at(0.5),
                            available_at=at(2), payload=SYNTHETIC)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_MISSING)
        self.assertEqual(ctx.exception.detail.get('field'), 'ingested_at')

    def test_naive_clock_refused(self):
        # Passed directly, not through the helper: a naive string is not arithmetic.
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.append(source_id='synthetic.alpha', request_started_at=at(0),
                            response_received_at='2026-11-01T12:00:00', ingested_at=at(1),
                            available_at=at(2), payload=SYNTHETIC)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_INVALID)

    def test_non_string_clock_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.append(source_id='synthetic.alpha', request_started_at=at(0),
                            response_received_at=12345, ingested_at=at(1),
                            available_at=at(2), payload=SYNTHETIC)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_INVALID)

    def test_out_of_order_available_before_ingested_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            add(self.log, received=0, ingested=5, available=1)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_OUT_OF_ORDER)

    def test_out_of_order_ingested_before_receipt_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            add(self.log, received=5, ingested=1, available=6)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_OUT_OF_ORDER)

    def test_out_of_order_request_after_receipt_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.append(source_id='synthetic.alpha', request_started_at=at(3),
                            response_received_at=at(1), ingested_at=at(4), available_at=at(5),
                            payload=SYNTHETIC)
        self.assertEqual(ctx.exception.reason, obs.CLOCK_OUT_OF_ORDER)

    def test_ordering_violation_writes_nothing(self):
        with self.assertRaises(obs.ObservationError):
            add(self.log, received=0, ingested=5, available=1)
        self.assertEqual(len(self.log.records), 0)

    def test_provider_stamps_are_optional(self):
        record = add(self.log)
        self.assertIsNone(record['source_published_at'])
        self.assertIsNone(record['source_updated_at'])

    def test_provider_stamps_are_recorded_when_supplied(self):
        record = add(self.log, source_published_at=at(-100), source_updated_at=at(-50))
        self.assertEqual(record['source_updated_at'], at(-50))

    def test_payload_hash_mismatch_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            add(self.log, payload_sha256='0' * 64)
        self.assertEqual(ctx.exception.reason, obs.PAYLOAD_HASH_MISMATCH)

    def test_invalid_supplied_hash_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.append(source_id='synthetic.alpha', request_started_at=at(0),
                            response_received_at=at(0.5), ingested_at=at(1), available_at=at(2),
                            payload_sha256='not-a-hash')
        self.assertEqual(ctx.exception.reason, obs.INVALID_PAYLOAD_HASH)


class DecisionEligibility(unittest.TestCase):
    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def test_all_clocks_before_cutoff_is_eligible(self):
        record = add(self.log, available=2)
        eligible, reason = self.log.eligibility(record['observation_id'], at(3))
        self.assertTrue(eligible, reason)
        self.assertIsNone(reason)

    def test_available_at_after_cutoff_blocks_eligibility(self):
        record = add(self.log, available=5)
        eligible, reason = self.log.eligibility(record['observation_id'], at(3))
        self.assertFalse(eligible)
        self.assertEqual(reason, obs.AFTER_CUTOFF)

    def test_receipt_after_cutoff_blocks_eligibility(self):
        record = add(self.log, received=5, ingested=6, available=7)
        eligible, reason = self.log.eligibility(record['observation_id'], at(3))
        self.assertFalse(eligible)
        self.assertEqual(reason, obs.AFTER_CUTOFF)

    def test_ineligible_observation_is_retained_not_discarded(self):
        record = add(self.log, available=5)
        self.log.eligibility(record['observation_id'], at(3))
        self.assertEqual(len(self.log.records), 1)
        self.assertEqual(self.log.get(record['observation_id'])['observation_id'],
                         record['observation_id'])

    def test_provider_stamp_is_not_used_as_a_clock(self):
        # Provider says it updated long before the cutoff; availability is after it.
        record = add(self.log, available=5, source_updated_at=at(-500), source_published_at=at(-600))
        eligible, reason = self.log.eligibility(record['observation_id'], at(3))
        self.assertFalse(eligible)
        self.assertEqual(reason, obs.AFTER_CUTOFF)

    def test_missing_provider_stamp_does_not_block_eligibility(self):
        record = add(self.log, available=2)
        eligible, _why = self.log.eligibility(record['observation_id'], at(3))
        self.assertTrue(eligible)

    def test_backfilled_observation_with_early_receipt_is_ineligible(self):
        # Received before the cutoff but only STORED after it: hindsight, so excluded.
        record = add(self.log, received=1, ingested=9, available=10)
        eligible, reason = self.log.eligibility(record['observation_id'], at(3))
        self.assertFalse(eligible)
        self.assertEqual(reason, obs.AFTER_CUTOFF)

    def test_unknown_observation_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.eligibility('obs.w1.99999999', at(3))
        self.assertEqual(ctx.exception.reason, obs.UNKNOWN_OBSERVATION)


class Replay(unittest.TestCase):
    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def test_replay_is_cutoff_bounded(self):
        add(self.log, received=1, ingested=2, available=3)
        add(self.log, received=10, ingested=11, available=12)
        report = self.log.replay(at(5))
        self.assertEqual(len(report['admitted']), 1)
        self.assertEqual(len(report['excluded']), 1)

    def test_replay_is_deterministic(self):
        for h in (1, 3, 5):
            add(self.log, received=h, ingested=h + 1, available=h + 2)
        first = self.log.replay(at(9))
        second = self.log.replay(at(9))
        self.assertEqual(first['replay_digest'], second['replay_digest'])
        self.assertEqual(first['cited_observation_ids'], second['cited_observation_ids'])

    def test_replay_cites_every_admitted_observation(self):
        ids = [add(self.log, received=h, ingested=h + 1, available=h + 2)['observation_id']
               for h in (1, 3)]
        report = self.log.replay(at(9))
        self.assertEqual(sorted(report['cited_observation_ids']), sorted(ids))
        self.assertEqual(len(report['admitted']), 2)

    def test_replay_never_uses_a_record_appended_later(self):
        add(self.log, received=1, ingested=2, available=3)
        early = self.log.replay(at(5))['replay_digest']
        add(self.log, received=10, ingested=11, available=12)   # appended afterwards
        self.assertEqual(self.log.replay(at(5))['replay_digest'], early)

    def test_replay_digest_changes_when_admitted_set_changes(self):
        add(self.log, received=1, ingested=2, available=3)
        before = self.log.replay(at(5))['replay_digest']
        add(self.log, received=2, ingested=3, available=4)
        self.assertNotEqual(self.log.replay(at(5))['replay_digest'], before)

    def test_replay_order_is_receipt_then_ingest_then_id(self):
        late = add(self.log, received=5, ingested=6, available=7)
        early = add(self.log, received=1, ingested=8, available=9)
        report = self.log.replay(at(20))
        self.assertEqual([a['observation_id'] for a in report['admitted']],
                         [early['observation_id'], late['observation_id']])

    def test_excluded_observations_are_reported_with_reasons(self):
        add(self.log, received=1, ingested=2, available=3)
        add(self.log, received=10, ingested=11, available=12)
        report = self.log.replay(at(5))
        self.assertEqual([e['reason'] for e in report['excluded']], [obs.AFTER_CUTOFF])
        self.assertEqual(report['order_by'],
                         ['response_received_at', 'ingested_at', 'observation_id'])

    def test_replay_reports_version_and_cutoff(self):
        report = self.log.replay(at(5))
        self.assertEqual(report['version'], obs.VERSION)
        self.assertEqual(obs.utc(report['cutoff'], 'cutoff'), ident.utc(at(5)))


class RetentionTombstones(unittest.TestCase):
    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def test_deleting_a_payload_leaves_the_observation_record_intact(self):
        record = add(self.log, received=1, ingested=2, available=3)
        before = dict(record)
        self.log.delete_payload(record['observation_id'], policy_ref='licence:clause-8',
                                deleted_at=at(100))
        after = self.log.get(record['observation_id'])
        self.assertEqual(before, after)
        self.assertTrue(self.log.verify_digests())

    def test_tombstone_names_hash_size_and_policy(self):
        record = add(self.log, received=1, ingested=2, available=3)
        tomb = self.log.delete_payload(record['observation_id'], policy_ref='licence:clause-8',
                                       deleted_at=at(100))
        self.assertEqual(tomb['observation_id'], record['observation_id'])
        self.assertEqual(tomb['payload_sha256'], record['payload_sha256'])
        self.assertEqual(tomb['payload_size'], len(SYNTHETIC))
        self.assertEqual(tomb['policy_ref'], 'licence:clause-8')

    def test_payload_for_after_deletion_refuses(self):
        record = add(self.log, received=1, ingested=2, available=3)
        self.log.delete_payload(record['observation_id'], policy_ref='p', deleted_at=at(100))
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.payload_for(record['observation_id'])
        self.assertEqual(ctx.exception.reason, obs.PAYLOAD_UNAVAILABLE)

    def test_retention_state_reports_deleted(self):
        record = add(self.log, received=1, ingested=2, available=3)
        self.assertEqual(self.log.retention_state(record['observation_id']), obs.RETAINED)
        self.log.delete_payload(record['observation_id'], policy_ref='p', deleted_at=at(100))
        self.assertEqual(self.log.retention_state(record['observation_id']), obs.DELETED)

    def test_second_deletion_refused(self):
        record = add(self.log, received=1, ingested=2, available=3)
        self.log.delete_payload(record['observation_id'], policy_ref='p', deleted_at=at(100))
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.delete_payload(record['observation_id'], policy_ref='p', deleted_at=at(101))
        self.assertEqual(ctx.exception.reason, obs.ALREADY_DELETED)

    def test_deletion_requires_policy_and_timestamp(self):
        record = add(self.log, received=1, ingested=2, available=3)
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.delete_payload(record['observation_id'], policy_ref='', deleted_at=at(100))
        self.assertEqual(ctx.exception.reason, obs.MISSING_FIELD)

    def test_deleting_an_unknown_observation_refused(self):
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.delete_payload('obs.w1.99999999', policy_ref='p', deleted_at=at(100))
        self.assertEqual(ctx.exception.reason, obs.UNKNOWN_OBSERVATION)

    def test_deleted_payload_marks_payload_dependent_replay_unavailable(self):
        record = add(self.log, received=1, ingested=2, available=3)
        self.log.delete_payload(record['observation_id'], policy_ref='p', deleted_at=at(100))
        report = self.log.replay(at(5))
        self.assertFalse(report['payload_dependent_available'])
        self.assertEqual(report['unavailable_payload_observation_ids'],
                         [record['observation_id']])

    def test_deleted_payload_is_not_reproducible_but_metadata_replay_stands(self):
        # The observation is still admitted as METADATA evidence (its clocks were real and
        # its hash still identifies what we held), but it cannot be replayed as payload.
        record = add(self.log, received=1, ingested=2, available=3)
        self.log.delete_payload(record['observation_id'], policy_ref='p', deleted_at=at(100))
        report = self.log.replay(at(5))
        self.assertEqual(report['cited_observation_ids'], [record['observation_id']])
        self.assertFalse(report['admitted'][0]['payload_retained'])

    def test_require_payloads_excludes_tombstoned_observation_with_reason(self):
        kept = add(self.log, received=1, ingested=2, available=3, payload=b'{"synthetic":1}')
        gone = add(self.log, received=1, ingested=2, available=3, payload=b'{"synthetic":2}')
        self.log.delete_payload(gone['observation_id'], policy_ref='p', deleted_at=at(100))
        report = self.log.replay(at(5), require_payloads=True)
        self.assertEqual(report['cited_observation_ids'], [kept['observation_id']])
        self.assertEqual([e['reason'] for e in report['excluded']], [obs.PAYLOAD_UNAVAILABLE])

    def test_retention_policy_is_recorded_per_source_and_never_invented(self):
        record = add(self.log, received=1, ingested=2, available=3)
        self.log.delete_payload(record['observation_id'], policy_ref='licence:A',
                                deleted_at=at(100))
        self.assertEqual(self.log.tombstones[-1]['policy_ref'], 'licence:A')
        self.assertNotIn('permanent', json.dumps(self.log.tombstones))


class Integrity(unittest.TestCase):
    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def test_stored_records_are_not_mutated_by_the_caller(self):
        record = add(self.log, received=1, ingested=2, available=3)
        digest = record['observation_digest']
        record['available_at'] = at(999)
        record['payload_sha256'] = 'f' * 64
        self.assertEqual(self.log.get(record['observation_id'])['observation_digest'], digest)
        self.assertTrue(self.log.verify_digests())

    def test_verify_digests_detects_tampering(self):
        add(self.log, received=1, ingested=2, available=3)
        # Simulate corruption of internal storage (the public view returns deep copies).
        self.log._records[0]['available_at'] = at(999)
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.verify_digests()
        self.assertEqual(ctx.exception.reason, obs.DIGEST_MISMATCH)

    def test_no_new_reason_codes_entered_the_package2a_registry(self):
        # The reason registry is Package 2A contract. M0 registers its own codes instead.
        self.assertEqual(obs.OBSERVATION_REASONS & ident.QUARANTINE_REASONS, frozenset())

    def test_frozen_package2a_surfaces_are_unchanged(self):
        self.assertEqual(len(ident.TIMESTAMP_FIELDS), 8)
        self.assertEqual(len(ident.QUARANTINE_REASONS), 11)
        from nba import schedule as sch
        self.assertEqual(len(sch.STATUSES), 5)
        self.assertEqual(sch.VERSION, 'nba-schedule-v0.1')


class SharedPayloadRetention(unittest.TestCase):
    """P1: identical bytes shared by two observations must survive deleting one of them.

    Storage deduplicates the BLOB by content hash; observations are never deduplicated. The
    two requirements collide the moment a retention policy deletes one observation's payload:
    naively removing the blob would destroy bytes another RETAINED observation still needs.
    """

    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def _add(self, source):
        return add(self.log, received=1, ingested=2, available=3, source=source)

    def test_deleting_one_of_two_observations_sharing_bytes_keeps_the_bytes(self):
        first, second = self._add('synthetic.alpha'), self._add('synthetic.beta')
        self.assertEqual(first['payload_sha256'], second['payload_sha256'])
        self.log.delete_payload(first['observation_id'], policy_ref='licence:A', deleted_at=at(100))
        self.assertEqual(self.log.payload_for(second['observation_id']), SYNTHETIC)

    def test_deleted_observation_stays_inaccessible_while_a_peer_retains_the_blob(self):
        first, second = self._add('synthetic.alpha'), self._add('synthetic.beta')
        self.log.delete_payload(first['observation_id'], policy_ref='licence:A', deleted_at=at(100))
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.payload_for(first['observation_id'])
        self.assertEqual(ctx.exception.reason, obs.PAYLOAD_UNAVAILABLE)
        self.assertEqual(self.log.retention_state(first['observation_id']), obs.DELETED)
        # ...while the peer is unaffected.
        self.assertEqual(self.log.retention_state(second['observation_id']), obs.RETAINED)
        self.assertEqual(self.log.payload_for(second['observation_id']), SYNTHETIC)

    def test_shared_blob_is_removed_only_after_the_last_reference_is_deleted(self):
        first, second = self._add('synthetic.alpha'), self._add('synthetic.beta')
        digest = first['payload_sha256']
        self.log.delete_payload(first['observation_id'], policy_ref='p', deleted_at=at(100))
        self.assertIn(digest, self.log._payloads)          # still required by the peer
        self.log.delete_payload(second['observation_id'], policy_ref='p', deleted_at=at(101))
        self.assertNotIn(digest, self.log._payloads)       # last reference gone

    def test_shared_bytes_across_different_sources_are_refcounted_separately(self):
        ids = [self._add(source)['observation_id']
               for source in ('synthetic.alpha', 'synthetic.beta', 'synthetic.gamma')]
        for observation_id in ids[:2]:
            self.log.delete_payload(observation_id, policy_ref='p', deleted_at=at(100))
        self.assertEqual(self.log.payload_for(ids[2]), SYNTHETIC)
        self.log.delete_payload(ids[2], policy_ref='p', deleted_at=at(100))
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.payload_for(ids[2])
        self.assertEqual(ctx.exception.reason, obs.PAYLOAD_UNAVAILABLE)

    def test_per_observation_retention_policies_are_recorded_independently(self):
        first, second = self._add('synthetic.alpha'), self._add('synthetic.beta')
        self.log.delete_payload(first['observation_id'], policy_ref='licence:A', deleted_at=at(100))
        self.log.delete_payload(second['observation_id'], policy_ref='licence:B', deleted_at=at(101))
        self.assertEqual([t['policy_ref'] for t in self.log.tombstones], ['licence:A', 'licence:B'])
        self.assertEqual([t['observation_id'] for t in self.log.tombstones],
                         [first['observation_id'], second['observation_id']])


class PayloadAvailability(unittest.TestCase):
    """P1: 'retained' is not the same fact as 'reproducible'.

    A hash-only observation was never stored, so it is retained by policy yet cannot be
    reproduced. Reporting it as available would let a replay claim payload evidence it does
    not have.
    """

    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def _hash_only(self):
        return self.log.append(source_id='synthetic.alpha', request_started_at=at(0),
                               response_received_at=at(1), ingested_at=at(2), available_at=at(3),
                               payload_sha256=obs.content_hash(SYNTHETIC))

    def test_hash_only_observation_is_not_reproducible(self):
        self._hash_only()
        report = self.log.replay(at(5))
        self.assertTrue(report['admitted'][0]['payload_retained'])     # not deleted
        self.assertFalse(report['admitted'][0]['payload_reproducible'])
        self.assertFalse(report['payload_dependent_available'])
        self.assertEqual(report['unavailable_payload_observation_ids'],
                         [self.log.records[0]['observation_id']])

    def test_require_payloads_excludes_a_never_stored_payload_with_its_own_reason(self):
        self._hash_only()
        report = self.log.replay(at(5), require_payloads=True)
        self.assertEqual(report['cited_observation_ids'], [])
        self.assertEqual([e['reason'] for e in report['excluded']], [obs.PAYLOAD_NOT_STORED])

    def test_payload_for_refuses_a_never_stored_payload_with_an_explicit_reason(self):
        record = self._hash_only()
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.payload_for(record['observation_id'])
        self.assertEqual(ctx.exception.reason, obs.PAYLOAD_NOT_STORED)

    def test_corrupted_payload_fails_closed(self):
        record = add(self.log, received=1, ingested=2, available=3)
        self.log._payloads[record['payload_sha256']] = b'tampered bytes'
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.payload_for(record['observation_id'])
        self.assertEqual(ctx.exception.reason, obs.PAYLOAD_CORRUPT)

    def test_corrupt_payload_is_excluded_when_payloads_are_required(self):
        record = add(self.log, received=1, ingested=2, available=3)
        self.log._payloads[record['payload_sha256']] = b'tampered bytes'
        report = self.log.replay(at(5), require_payloads=True)
        self.assertEqual([e['reason'] for e in report['excluded']], [obs.PAYLOAD_CORRUPT])

    def test_reproducible_payload_is_reported_reproducible(self):
        add(self.log, received=1, ingested=2, available=3)
        report = self.log.replay(at(5), require_payloads=True)
        self.assertTrue(report['admitted'][0]['payload_reproducible'])
        self.assertTrue(report['payload_dependent_available'])
        self.assertEqual(report['unavailable_payload_observation_ids'], [])


class ChronologicalReplay(unittest.TestCase):
    """P2: replay must order by parsed instants, not by ISO strings.

    Stored timestamps are ISO-8601 UTC, but string order is not chronological order: a
    fractional second sorts BEFORE the whole second it follows, because '.' precedes 'Z'.
    """

    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def _append_at(self, received):
        return self.log.append(source_id='synthetic.alpha',
                               request_started_at='2026-11-01T11:00:00Z',
                               response_received_at=received,
                               ingested_at='2026-11-01T13:00:00Z',
                               available_at='2026-11-01T14:00:00Z', payload=SYNTHETIC)

    def test_fractional_second_timestamps_order_chronologically(self):
        whole = self._append_at('2026-11-01T12:00:00Z')
        fractional = self._append_at('2026-11-01T12:00:00.500Z')
        self.assertLess('2026-11-01T12:00:00.500Z', '2026-11-01T12:00:00Z')   # string order is wrong
        report = self.log.replay(at(20))
        self.assertEqual(report['cited_observation_ids'],
                         [whole['observation_id'], fractional['observation_id']])

    def test_replay_order_uses_parsed_instants_not_strings(self):
        late_ms = self._append_at('2026-11-01T12:00:00.900Z')
        early_s = self._append_at('2026-11-01T12:00:00Z')
        report = self.log.replay(at(20))
        self.assertEqual([a['observation_id'] for a in report['admitted']],
                         [early_s['observation_id'], late_ms['observation_id']])

    def test_equivalent_timezone_offsets_are_equivalent(self):
        utc = self._append_at('2026-11-01T12:00:00Z')
        offset = self._append_at('2026-11-01T13:00:00+01:00')
        self.assertEqual(utc['response_received_at'], offset['response_received_at'])
        report = self.log.replay(at(20))
        self.assertEqual(report['cited_observation_ids'],
                         [utc['observation_id'], offset['observation_id']])

    def test_equal_times_tie_break_by_observation_id(self):
        ids = [self._append_at('2026-11-01T12:00:00Z')['observation_id'] for _ in range(3)]
        report = self.log.replay(at(20))
        self.assertEqual(report['cited_observation_ids'], sorted(ids))

    def test_cutoff_still_excludes_after_reordering(self):
        self._append_at('2026-11-01T12:00:00.500Z')
        self.log.append(source_id='synthetic.alpha',
                        request_started_at='2026-11-01T15:00:00Z',
                        response_received_at='2026-11-01T16:00:00Z',
                        ingested_at='2026-11-01T17:00:00Z',
                        available_at='2026-11-01T18:00:00Z', payload=SYNTHETIC)
        report = self.log.replay('2026-11-01T15:00:00Z')
        self.assertEqual(len(report['admitted']), 1)
        self.assertEqual([e['reason'] for e in report['excluded']], [obs.AFTER_CUTOFF])

    def test_reordering_preserves_determinism(self):
        for stamp in ('2026-11-01T12:00:00.900Z', '2026-11-01T12:00:00Z',
                      '2026-11-01T12:00:01Z', '2026-11-01T11:59:59.999Z'):
            self._append_at(stamp)
        self.assertEqual(self.log.replay(at(20))['replay_digest'],
                         self.log.replay(at(20))['replay_digest'])


class PayloadProvenance(unittest.TestCase):
    """Blob PRESENCE is not payload PROVENANCE.

    Blobs are content-addressed, so bytes captured for observation B can match the hash of
    an observation A that never stored a payload. A must stay PAYLOAD_NOT_STORED: presence of
    matching bytes somewhere in the store says nothing about whether A's payload was ever
    captured. Treating presence as provenance would also make an historical replay change
    retroactively when an unrelated later observation happened to supply the same bytes.
    """

    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')

    def _hash_only(self, source='synthetic.alpha', hours=(0, 1, 2, 3)):
        request, received, ingested, available = hours
        return self.log.append(source_id=source, request_started_at=at(request),
                               response_received_at=at(received), ingested_at=at(ingested),
                               available_at=at(available),
                               payload_sha256=obs.content_hash(SYNTHETIC))

    def _supplier(self, source='synthetic.beta', hours=(12, 13, 14, 15)):
        request, received, ingested, available = hours
        return self.log.append(source_id=source, request_started_at=at(request),
                               response_received_at=at(received), ingested_at=at(ingested),
                               available_at=at(available), payload=SYNTHETIC)

    def test_hash_only_stays_not_stored_when_later_bytes_match_its_hash(self):
        a = self._hash_only()
        self.assertEqual(self.log.payload_availability(a['observation_id']),
                         (False, obs.PAYLOAD_NOT_STORED))
        b = self._supplier()                      # AFTER the cutoff T used below
        self.assertEqual(b['payload_sha256'], a['payload_sha256'])
        self.assertEqual(self.log.payload_availability(a['observation_id']),
                         (False, obs.PAYLOAD_NOT_STORED))
        report = self.log.replay(at(10), require_payloads=True)     # T = 22:00, after A
        self.assertNotIn(a['observation_id'], report['cited_observation_ids'])
        self.assertEqual([e['reason'] for e in report['excluded']
                          if e['observation_id'] == a['observation_id']],
                         [obs.PAYLOAD_NOT_STORED])

    def test_later_bytes_do_not_retroactively_upgrade_an_earlier_replay(self):
        a = self._hash_only()
        before = self.log.replay(at(10), require_payloads=True)['cited_observation_ids']
        self._supplier()
        after = self.log.replay(at(10), require_payloads=True)['cited_observation_ids']
        self.assertEqual(after, before)
        self.assertEqual(after, [])

    def test_deleting_the_supplier_leaves_the_hash_only_record_not_stored(self):
        a = self._hash_only()
        b = self._supplier()
        before = self.log.replay(at(10), require_payloads=True)['cited_observation_ids']
        self.log.delete_payload(b['observation_id'], policy_ref='licence:B', deleted_at=at(20))
        self.assertEqual(self.log.payload_availability(a['observation_id']),
                         (False, obs.PAYLOAD_NOT_STORED))
        self.assertEqual(self.log.replay(at(10), require_payloads=True)['cited_observation_ids'],
                         before)

    def test_history_is_stable_across_supplier_appearance_and_deletion(self):
        a = self._hash_only()
        none_yet = self.log.replay(at(10))['cited_observation_ids']
        b = self._supplier()
        with_bytes = self.log.replay(at(10))['cited_observation_ids']
        self.log.delete_payload(b['observation_id'], policy_ref='licence:B', deleted_at=at(20))
        after_delete = self.log.replay(at(10))['cited_observation_ids']
        self.assertEqual(none_yet, with_bytes)
        self.assertEqual(with_bytes, after_delete)

    def test_observations_that_supplied_bytes_are_reproducible_and_deduplicated(self):
        a = self._supplier('synthetic.alpha', (0, 1, 2, 3))
        b = self._supplier('synthetic.beta', (0, 1, 2, 3))
        self.assertEqual(a['payload_sha256'], b['payload_sha256'])
        self.assertEqual(len(self.log._payloads), 1)                # one shared blob
        self.assertEqual(self.log.payload_availability(a['observation_id']), (True, None))
        self.assertEqual(self.log.payload_availability(b['observation_id']), (True, None))
        self.log.delete_payload(a['observation_id'], policy_ref='licence:A', deleted_at=at(9))
        self.assertEqual(self.log.payload_availability(b['observation_id']), (True, None))

    def test_a_hash_only_observation_is_never_registered_as_a_holder(self):
        a = self._hash_only()
        self._supplier()
        digest = a['payload_sha256']
        self.assertNotIn(a['observation_id'], self.log._payload_refs.get(digest, set()))

    def test_last_holder_deletion_still_removes_the_blob(self):
        self._hash_only()
        b = self._supplier()
        digest = b['payload_sha256']
        self.log.delete_payload(b['observation_id'], policy_ref='licence:B', deleted_at=at(20))
        self.assertNotIn(digest, self.log._payloads)
        self.assertEqual(self.log.payload_availability(b['observation_id']),
                         (False, obs.PAYLOAD_UNAVAILABLE))

    def test_deleting_a_hash_only_record_does_not_release_the_suppliers_bytes(self):
        a = self._hash_only()
        b = self._supplier()
        # A has its own tombstone, and no bytes of its own to release.
        self.log.delete_payload(a['observation_id'], policy_ref='licence:A', deleted_at=at(20))
        self.assertEqual([t['observation_id'] for t in self.log.tombstones],
                         [a['observation_id']])
        self.assertIn(b['payload_sha256'], self.log._payloads)
        self.assertEqual(self.log.payload_availability(b['observation_id']), (True, None))
        self.assertEqual(self.log.payload_availability(a['observation_id']),
                         (False, obs.PAYLOAD_UNAVAILABLE))


class ReplayIntegrityBoundary(unittest.TestCase):
    """Replay verifies the store's own integrity INLINE -- no separate caller step.

    Leaving this to a caller who must remember to call verify_digests() first means the
    default path admits tampered evidence. A digest-invalid record is refused by replay
    itself, with a reason, for every field the digest covers.
    """

    def setUp(self):
        self.log = obs.ObservationLog(writer_id='w1')
        self.records = [add(self.log, received=h, ingested=h + 1, available=h + 2)
                        for h in (1, 3, 5)]
        self.ids = [r['observation_id'] for r in self.records]

    def _tamper(self, index, field, value):
        self.log._records[index][field] = value

    def _replay(self, cutoff=None, **kw):
        return self.log.replay(cutoff or at(20), **kw)

    def test_replay_rejects_a_record_with_a_tampered_source(self):
        self._tamper(1, 'source_id', 'synthetic.injected')
        report = self._replay()
        self.assertNotIn(self.ids[1], report['cited_observation_ids'])
        self.assertEqual([e['reason'] for e in report['excluded']
                          if e['observation_id'] == self.ids[1]], [obs.DIGEST_MISMATCH])

    def test_replay_rejects_a_record_with_a_tampered_clock(self):
        self._tamper(1, 'available_at', at(2))      # move it earlier, inside the cutoff
        report = self._replay()
        self.assertNotIn(self.ids[1], report['cited_observation_ids'])
        self.assertEqual([e['reason'] for e in report['excluded']
                          if e['observation_id'] == self.ids[1]], [obs.DIGEST_MISMATCH])

    def test_replay_rejects_a_record_with_a_tampered_payload_hash(self):
        self._tamper(1, 'payload_sha256', 'a' * 64)
        report = self._replay(require_payloads=True)
        self.assertNotIn(self.ids[1], report['cited_observation_ids'])
        self.assertEqual([e['reason'] for e in report['excluded']
                          if e['observation_id'] == self.ids[1]], [obs.DIGEST_MISMATCH])

    def test_replay_reports_its_integrity_state(self):
        self._tamper(2, 'ingested_at', at(4))
        report = self._replay()
        self.assertEqual(report['digest_invalid_observation_ids'], [self.ids[2]])
        self.assertFalse(report['integrity_ok'])
        clean = obs.ObservationLog(writer_id='w2')
        only = add(clean, received=1, ingested=2, available=3)['observation_id']
        report = clean.replay(at(20))
        self.assertEqual(report['digest_invalid_observation_ids'], [])
        self.assertTrue(report['integrity_ok'])
        self.assertEqual(report['cited_observation_ids'], [only])

    def test_replay_needs_no_separate_verification_step(self):
        self._tamper(1, 'source_id', 'synthetic.injected')
        for require in (False, True):
            report = self._replay(require_payloads=require)
            self.assertNotIn(self.ids[1], report['cited_observation_ids'])
            self.assertEqual(report['cited_observation_ids'],
                             [self.ids[0], self.ids[2]])

    def test_tampering_does_not_disturb_valid_ordering_or_cutoff(self):
        self._tamper(1, 'source_id', 'synthetic.injected')
        report = self._replay()
        self.assertEqual(report['cited_observation_ids'], [self.ids[0], self.ids[2]])
        # ordering of the survivors is still receipt order
        self.assertEqual([a['response_received_at'] for a in report['admitted']],
                         [self.records[0]['response_received_at'],
                          self.records[2]['response_received_at']])
        # and the cutoff still binds, independently of the integrity refusal
        bounded = self._replay(at(4))
        self.assertEqual(bounded['cited_observation_ids'], [self.ids[0]])
        self.assertEqual({e['reason'] for e in bounded['excluded']},
                         {obs.DIGEST_MISMATCH, obs.AFTER_CUTOFF})

    def test_replay_stays_deterministic_with_a_tampered_record(self):
        self._tamper(1, 'payload_sha256', 'b' * 64)
        first, second = self._replay(), self._replay()
        self.assertEqual(first['replay_digest'], second['replay_digest'])
        self.assertEqual(first['cited_observation_ids'], second['cited_observation_ids'])

    def test_verify_digests_agrees_with_the_replay_boundary(self):
        self._tamper(1, 'ingested_at', at(6))       # genuinely different from the stored value
        report = self._replay()
        self.assertFalse(report['integrity_ok'])
        with self.assertRaises(obs.ObservationError) as ctx:
            self.log.verify_digests()
        self.assertEqual(ctx.exception.reason, obs.DIGEST_MISMATCH)


if __name__ == '__main__':
    unittest.main()
