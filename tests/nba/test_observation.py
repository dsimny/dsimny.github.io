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


if __name__ == '__main__':
    unittest.main()
