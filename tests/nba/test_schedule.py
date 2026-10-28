import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import identity as ident
from nba import schedule as sch


GAME = {'league_game_id': '0022600001', 'home_team_id': 'TEAM-BOS', 'away_team_id': 'TEAM-NYK'}
T1 = '2026-12-09T00:00:00Z'


class PhaseClassification(unittest.TestCase):
    def setUp(self):
        self.fx = json.loads((Path(__file__).parent / 'fixtures/schedule_cases.json').read_text())

    def test_every_declared_phase_matches_frozen_package1_counting(self):
        for case in self.fx['phases']:
            with self.subTest(case=case):
                block = sch.classify_phase(case['phase'], case.get('cup_round'))
                self.assertEqual(block['counts_regular'], case['counts_regular'])

    def test_cup_championship_does_not_count_as_regular(self):
        self.assertFalse(sch.classify_phase('CUP', 'CHAMPIONSHIP')['counts_regular'])

    def test_cup_group_quarterfinal_semifinal_do_count(self):
        for round_name in ('GROUP', 'QUARTERFINAL', 'SEMIFINAL'):
            with self.subTest(cup_round=round_name):
                self.assertTrue(sch.classify_phase('CUP', round_name)['counts_regular'])

    def test_unknown_phase_quarantines(self):
        with self.assertRaises(ident.Quarantine) as ctx:
            sch.classify_phase('EXHIBITION')
        self.assertEqual(ctx.exception.reason, ident.UNKNOWN_PHASE)

    def test_cup_without_round_quarantines(self):
        with self.assertRaises(ident.Quarantine) as ctx:
            sch.classify_phase('CUP')
        self.assertEqual(ctx.exception.reason, ident.UNKNOWN_PHASE)

    def test_cup_round_on_non_cup_game_quarantines(self):
        with self.assertRaises(ident.Quarantine):
            sch.classify_phase('REGULAR_SEASON', 'GROUP')

    def test_phase_is_never_inferred_from_a_date(self):
        # Same date, three different phases. Classification follows the evidence.
        for phase, kwargs in (('REGULAR_SEASON', {}), ('CUP', {'cup_round': 'SEMIFINAL'}),
                              ('PLAY_IN', {})):
            with self.subTest(phase=phase):
                record = sch.schedule_record(GAME, phase, kwargs.get('cup_round'),
                                             sch.SCHEDULED, '2026-12-09T00:00:00Z')
                self.assertEqual(record['phase'], phase)


class NeutralSite(unittest.TestCase):
    def test_neutral_site_is_a_property_not_a_phase_input(self):
        neutral = sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, T1,
                                      actual_venue='Paris, FR', neutral_site=True)
        self.assertTrue(neutral['neutral_site'])
        self.assertEqual(neutral['phase'], 'REGULAR_SEASON')   # unchanged by venue

    def test_neutral_site_does_not_alter_regular_counting(self):
        neutral = sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, T1, neutral_site=True)
        self.assertTrue(neutral['counts_regular'])

    def test_reschedule_carries_a_neutral_site_forward(self):
        # A revision that says nothing about the venue must not clear it. The
        # neutral flag is a venue property, so losing it by omission would be a
        # silent data change inside an append-only chain.
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, T1,
                                         actual_venue='Paris, FR', neutral_site=True,
                                         source_updated_at='2026-11-01T00:00:00Z'))
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-01-05T00:00:00Z',
                       source_updated_at='2026-12-08T00:00:00Z')
        self.assertTrue(chain.current()['neutral_site'])
        self.assertEqual(chain.current()['actual_venue'], 'Paris, FR')

    def test_reschedule_may_explicitly_clear_a_neutral_site(self):
        # Carry-forward must not make the property unchangeable: an explicit
        # False is a caller decision and must win.
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, T1,
                                         neutral_site=True, source_updated_at='2026-11-01T00:00:00Z'))
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-01-05T00:00:00Z',
                       neutral_site=False, source_updated_at='2026-12-08T00:00:00Z')
        self.assertFalse(chain.current()['neutral_site'])


class RevisionChains(unittest.TestCase):
    def _chain(self):
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, T1,
                                         source_updated_at='2026-11-01T00:00:00Z'))
        return chain

    def test_first_revision_appends(self):
        self.assertEqual(self._chain().current()['revision'], 1)

    def test_postponement_appends_and_preserves_original(self):
        chain = self._chain()
        original = chain.revisions[0]
        original_digest = original['revision_digest']
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-01-05T00:00:00Z',
                       status=sch.POSTPONED, source_updated_at='2026-12-08T00:00:00Z')
        self.assertEqual(len(chain.revisions), 2)
        self.assertEqual(chain.current()['status'], sch.POSTPONED)
        # original byte-identical and still verifying
        self.assertEqual(chain.revisions[0]['revision_digest'], original_digest)
        self.assertTrue(chain.verify_digests())

    def test_reschedule_links_backwards(self):
        chain = self._chain()
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-01-05T00:00:00Z')
        self.assertEqual(chain.current()['supersedes'], 1)
        self.assertEqual(chain.current()['revision'], 2)

    def test_revision_numbers_must_be_contiguous(self):
        chain = self._chain()
        with self.assertRaises(ValueError):
            chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, T1,
                                             revision=5, supersedes=1))

    def test_revision_for_other_game_refused(self):
        chain = self._chain()
        other = dict(GAME, league_game_id='0022600099')
        with self.assertRaises(ValueError):
            chain.append(sch.schedule_record(other, 'REGULAR_SEASON', None, sch.SCHEDULED, T1,
                                             revision=2, supersedes=1))

    def test_no_cycles(self):
        chain = self._chain()
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-01-05T00:00:00Z')
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-02-05T00:00:00Z')
        self.assertFalse(chain.has_cycles())
        self.assertTrue(chain.verify_digests())

    def test_as_of_returns_revision_in_force(self):
        chain = self._chain()
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-01-05T00:00:00Z',
                       source_updated_at='2026-12-08T00:00:00Z')
        in_force = chain.as_of('2026-12-01T00:00:00Z')
        self.assertEqual(in_force['revision'], 1)
        later = chain.as_of('2027-01-01T00:00:00Z')
        self.assertEqual(later['revision'], 2)

    def test_as_of_refuses_when_a_revision_has_no_availability_time(self):
        # The reschedule carries no source_updated_at. Its event_time is a fact
        # about the GAME, not about when the revision could be known, so it cannot
        # establish that this revision was in force at the cutoff.
        chain = self._chain()                       # r1 recorded 2026-11-01
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2027-01-05T00:00:00Z')
        with self.assertRaises(ident.Quarantine) as ctx:
            chain.as_of('2027-02-01T00:00:00Z')
        self.assertEqual(ctx.exception.reason, ident.REVISION_TIME_UNKNOWN)

    def test_as_of_never_uses_scheduled_start_as_availability(self):
        # Single undated revision whose scheduled start precedes the cutoff.
        # Calling it "in force" would be an invented availability claim -- and a
        # historical/CLV audit would be reasoning on evidence that never existed.
        chain = sch.RevisionChain('0022600001')
        chain.append(sch.schedule_record(GAME, 'REGULAR_SEASON', None, sch.SCHEDULED, T1))
        with self.assertRaises(ident.Quarantine) as ctx:
            chain.as_of('2026-12-09T00:00:00Z')
        self.assertEqual(ctx.exception.reason, ident.REVISION_TIME_UNKNOWN)

    def test_as_of_ignores_a_revision_learned_after_the_cutoff(self):
        # The fail-closed rule must not become "always refuse": where provenance IS
        # recorded, a revision whose event_time precedes the cutoff but which was
        # only learned afterwards is correctly NOT in force.
        chain = self._chain()
        sch.reschedule(chain, GAME, 'REGULAR_SEASON', None, '2026-11-02T00:00:00Z',
                       source_updated_at='2026-12-08T00:00:00Z')
        in_force = chain.as_of('2026-11-15T00:00:00Z')
        self.assertEqual(in_force['revision'], 1)

    def test_cancelled_is_marked_and_retained(self):
        chain = self._chain()
        sch.cancelled(chain, GAME, 'REGULAR_SEASON', None, T1)
        self.assertEqual(chain.current()['status'], sch.CANCELLED)
        self.assertEqual(len(chain.revisions), 2)

    def test_unknown_status_refused(self):
        with self.assertRaises(ValueError):
            sch.schedule_record(GAME, 'REGULAR_SEASON', None, 'ABANDONED', T1)

    def test_schedule_record_never_invents_identity(self):
        with self.assertRaises(ident.Quarantine):
            sch.schedule_record({'home_team_id': 'A', 'away_team_id': 'B'},
                                'REGULAR_SEASON', None, sch.SCHEDULED, T1)

    def test_revision_carries_provider_ids_from_identity(self):
        game = dict(GAME, provider_ids={'espn': '401810001'})
        record = sch.schedule_record(game, 'REGULAR_SEASON', None, sch.SCHEDULED, T1)
        self.assertEqual(record['provider_ids'], {'espn': '401810001'})
        self.assertEqual(record['league_game_id'], '0022600001')


if __name__ == '__main__':
    unittest.main()
