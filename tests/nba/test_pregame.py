"""Regression tests for pregame cutoff integrity (Defect 1).

Written BEFORE the fix and run against the prior implementation, where they fail:
`identity.require_pregame` and `identity.effective_start` do not exist, so every test
here errors. That is the demonstration that the guard was absent, not merely
untested.

WHAT THIS PINS. A decision cutoff must not be allowed to sit at or after the moment
the game actually began. A cutoff after tipoff can observe the game, so anything
"decided" with it is not a pregame decision -- it is leakage. Nothing in Package 2A
previously compared the two, so a post-tipoff cutoff was accepted as eligible.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import identity as ident
from nba import schedule as sch


GAME = {'league_game_id': '0022600001', 'home_team_id': 'TEAM-BOS', 'away_team_id': 'TEAM-NYK'}
TIPOFF = '2026-12-09T00:30:00Z'
EVENTUAL = '2026-12-09T01:00:00Z'


class PregameGuard(unittest.TestCase):
    def test_pretipoff_cutoff_is_eligible(self):
        start = ident.require_pregame('2026-12-09T00:00:00Z', TIPOFF)
        self.assertEqual(ident.utc(start), ident.utc(TIPOFF))

    def test_cutoff_equal_to_tipoff_is_refused(self):
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.require_pregame(TIPOFF, TIPOFF)
        self.assertEqual(ctx.exception.reason, ident.PREGAME_AFTER_START)

    def test_posttipoff_cutoff_is_refused(self):
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.require_pregame(EVENTUAL, TIPOFF)
        self.assertEqual(ctx.exception.reason, ident.PREGAME_AFTER_START)

    def test_early_tipoff_refuses_cutoff_inside_the_shifted_window(self):
        # Game actually tipped 30 min early. A cutoff after the REAL start must be
        # refused even though it precedes the scheduled time.
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.require_pregame('2026-12-09T00:15:00Z', TIPOFF,
                                  actual_start='2026-12-09T00:00:00Z')
        self.assertEqual(ctx.exception.reason, ident.PREGAME_AFTER_START)

    def test_confirmed_actual_tipoff_is_accounted_for(self):
        # Actual start matches schedule: a genuinely early cutoff still passes.
        start = ident.require_pregame('2026-12-08T22:00:00Z', TIPOFF, actual_start=TIPOFF)
        self.assertEqual(ident.utc(start), ident.utc(TIPOFF))

    def test_missing_start_evidence_is_refused(self):
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.require_pregame('2026-12-09T00:00:00Z', None)
        self.assertEqual(ctx.exception.reason, ident.START_TIME_UNKNOWN)

    def test_no_evidence_at_all_is_refused(self):
        with self.assertRaises(ident.Quarantine):
            ident.require_pregame('2026-12-09T00:00:00Z', None, actual_start=None)

    def test_actual_start_alone_is_sufficient(self):
        start = ident.require_pregame('2026-12-08T22:00:00Z', None, actual_start=TIPOFF)
        self.assertEqual(ident.utc(start), ident.utc(TIPOFF))

    def test_conflicting_start_evidence_is_refused_not_guessed(self):
        # 8 hours apart is not a delay; it is a conflicting record.
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.require_pregame('2026-12-08T22:00:00Z', TIPOFF,
                                  actual_start='2026-12-09T08:30:00Z')
        self.assertEqual(ctx.exception.reason, ident.START_TIME_CONFLICT)

    def test_ordinary_delay_within_tolerance_is_not_a_conflict(self):
        start = ident.require_pregame('2026-12-08T22:00:00Z', TIPOFF,
                                      actual_start='2026-12-09T01:00:00Z')
        self.assertEqual(ident.utc(start), ident.utc(TIPOFF))   # earliest known wins

    def test_source_availability_still_not_gated_by_future_start(self):
        # Regression for the earlier defect: a future tipoff must NOT be flagged.
        usable, anomalies = ident.blocks_from_features(
            '2026-12-08T12:00:00Z', '2026-12-08T12:00:01Z', '2026-12-09T00:00:00Z')
        self.assertTrue(usable)
        self.assertEqual(anomalies, [])

    def test_availability_after_cutoff_still_ineligible(self):
        usable, anomalies = ident.blocks_from_features(
            '2026-12-09T02:00:00Z', '2026-12-09T02:00:01Z', TIPOFF)
        self.assertFalse(usable)
        self.assertEqual([a['field'] for a in anomalies], ['available_at', 'ingested_at'])


class ChainAwarePregame(unittest.TestCase):
    """The effective start must come from the revision in force, not the original."""

    def _chain(self, first_start=TIPOFF, second_start=None, status=sch.SCHEDULED):
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED,
                                         first_start, source_updated_at='2026-12-01T00:00:00Z'))
        if second_start is not None:
            sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, second_start, status=status,
                           source_updated_at='2026-12-08T00:00:00Z')
        return chain

    def test_reschedule_uses_new_revision_not_obsolete_original(self):
        # Original 12-09; game moved to 12-15. A cutoff before the NEW tipoff is
        # eligible and must not be rejected using the obsolete original time.
        chain = self._chain(second_start='2026-12-15T00:30:00Z')
        start = sch.chain_pregame_start(chain)
        self.assertEqual(ident.utc(start), ident.utc('2026-12-15T00:30:00Z'))
        eligible, reasons = sch.pregame_eligible(chain, '2026-12-14T00:00:00Z')
        self.assertTrue(eligible, reasons)

    def test_obsolete_earlier_original_does_not_falsely_widen_eligibility(self):
        # Original was EARLIER; after reschedule a cutoff after the new tipoff is refused.
        chain = self._chain(second_start='2026-12-15T00:30:00Z')
        eligible, reasons = sch.pregame_eligible(chain, '2026-12-16T00:00:00Z')
        self.assertFalse(eligible)
        self.assertIn(ident.PREGAME_AFTER_START, reasons)

    def test_postponed_without_new_start_refuses_pregame(self):
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, TIPOFF,
                                         source_updated_at='2026-12-01T00:00:00Z'))
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, TIPOFF, status=sch.POSTPONED,
                       source_updated_at='2026-12-08T00:00:00Z')
        chain.current()['event_time'] = None      # postponed, no replacement date yet
        eligible, reasons = sch.pregame_eligible(chain, '2026-12-08T12:00:00Z')
        self.assertFalse(eligible)
        self.assertIn(ident.START_TIME_UNKNOWN, reasons)

    def test_as_of_uses_revision_in_force(self):
        chain = self._chain(second_start='2026-12-15T00:30:00Z')
        # Before the reschedule was recorded, the original time was in force.
        start = sch.chain_pregame_start(chain, cutoff='2026-12-05T00:00:00Z')
        self.assertEqual(ident.utc(start), ident.utc(TIPOFF))

    def test_empty_chain_refuses_pregame(self):
        eligible, reasons = sch.pregame_eligible(sch.RevisionChain('0022600001'), TIPOFF)
        self.assertFalse(eligible)
        self.assertIn(ident.START_TIME_UNKNOWN, reasons)

    def test_availability_check_combines_with_pregame(self):
        chain = self._chain()
        eligible, reasons = sch.pregame_eligible(
            chain, '2026-12-09T00:00:00Z',
            available_at='2026-12-09T00:10:00Z', ingested_at='2026-12-09T00:11:00Z')
        self.assertFalse(eligible)

    def test_pregame_refuses_when_revision_availability_is_absent(self):
        # An undatable revision supports no pregame verdict at all, so the guard
        # reports ineligible with a reason rather than asserting one.
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, TIPOFF))
        eligible, reasons = sch.pregame_eligible(chain, '2026-12-08T00:00:00Z')
        self.assertFalse(eligible)
        self.assertIn(ident.REVISION_TIME_UNKNOWN, reasons)

    def test_chain_start_refuses_when_revision_availability_is_absent(self):
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, TIPOFF))
        with self.assertRaises(ident.Quarantine) as ctx:
            sch.chain_pregame_start(chain, cutoff='2026-12-08T00:00:00Z')
        self.assertEqual(ctx.exception.reason, ident.REVISION_TIME_UNKNOWN)


if __name__ == '__main__':
    unittest.main()
