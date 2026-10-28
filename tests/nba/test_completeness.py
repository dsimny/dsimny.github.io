import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import completeness as comp
from nba import identity as ident
from nba import schedule as sch


GAME = {'league_game_id': '0022600001', 'home_team_id': 'TEAM-BOS', 'away_team_id': 'TEAM-NYK'}
T1 = '2026-12-09T00:00:00Z'


def chain(game_id, status=sch.SCHEDULED, phase='REGULAR_SEASON', cup_round=None):
    game = dict(GAME, league_game_id=game_id)
    c = sch.RevisionChain(game_id)
    c.append(sch.schedule_record(game, phase, cup_round, status, T1))
    return c


class Reconciliation(unittest.TestCase):
    def test_all_canonical_reconciles(self):
        chains = {f'002260000{i}': chain(f'002260000{i}') for i in range(1, 4)}
        report = comp.reconcile(3, chains, [])
        self.assertTrue(report['reconciles'])
        self.assertEqual(report['buckets']['canonical'], 3)
        self.assertEqual(report['accounted'], 3)

    def test_quarantined_records_are_counted_not_dropped(self):
        chains = {'0022600001': chain('0022600001')}
        quarantined = [{'reason': ident.MISSING_LEAGUE_ID, 'detail': {}},
                       {'reason': ident.AMBIGUOUS_PROVIDER_MATCH, 'detail': {}}]
        report = comp.reconcile(3, chains, quarantined)
        self.assertEqual(report['buckets']['quarantined'], 2)
        self.assertEqual(report['accounted'], 3)
        self.assertEqual(report['quarantine_reasons'][ident.MISSING_LEAGUE_ID], 1)

    def test_postponed_and_cancelled_are_separate_buckets(self):
        chains = {
            '0022600001': chain('0022600001'),
            '0022600002': chain('0022600002', status=sch.POSTPONED),
            '0022600003': chain('0022600003', status=sch.CANCELLED),
        }
        report = comp.reconcile(3, chains, [])
        self.assertEqual(report['buckets']['canonical'], 1)
        self.assertEqual(report['buckets']['postponed'], 1)
        self.assertEqual(report['buckets']['cancelled'], 1)

    def test_cancelled_games_still_occupy_their_slot(self):
        chains = {'0022600001': chain('0022600001', status=sch.CANCELLED)}
        report = comp.reconcile(1, chains, [])
        self.assertEqual(report['buckets']['cancelled'], 1)
        self.assertEqual(report['accounted'], 1)      # not deleted from the denominator

    def test_missing_games_reported(self):
        chains = {'0022600001': chain('0022600001')}
        report = comp.reconcile(3, chains, [], missing=['0022600002', '0022600003'])
        self.assertEqual(report['buckets']['missing'], 2)
        self.assertEqual(report['accounted'], 3)

    def test_gap_raises_rather_than_rounding_away(self):
        chains = {'0022600001': chain('0022600001')}
        with self.assertRaises(comp.CompletenessError):
            comp.reconcile(5, chains, [])

    def test_empty_chain_counted_as_missing(self):
        chains = {'0022600001': sch.RevisionChain('0022600001')}
        report = comp.reconcile(1, chains, [])
        self.assertEqual(report['buckets']['missing'], 1)
        self.assertEqual(report['detail']['missing'][0]['reason'], 'EMPTY_CHAIN')

    def test_no_silent_loss_with_mixed_buckets(self):
        chains = {
            '0022600001': chain('0022600001'),
            '0022600002': chain('0022600002', status=sch.POSTPONED),
            '0022600003': chain('0022600003', status=sch.CANCELLED),
            '0022600004': sch.RevisionChain('0022600004'),
        }
        quarantined = [{'reason': ident.UNKNOWN_PHASE, 'detail': {}}]
        report = comp.reconcile(5, chains, quarantined)
        self.assertEqual(report['accounted'], 5)
        self.assertTrue(comp.assert_lossless(report))

    def test_negative_expected_refused(self):
        with self.assertRaises(ValueError):
            comp.reconcile(-1, {}, [])

    def test_coverage_is_identified_not_claimed(self):
        report = comp.reconcile(2, {'0022600001': chain('0022600001')},
                                [{'reason': ident.MISSING_LEAGUE_ID, 'detail': {}}])
        self.assertEqual(report['identified_coverage'], 0.5)

    def test_unreconciled_gap_helper(self):
        self.assertEqual(comp.unreconciled_gap(10, {'canonical': 7}), 3)

    def test_lossless_assertion_rejects_unreconciled_report(self):
        with self.assertRaises(comp.CompletenessError):
            comp.assert_lossless({'reconciles': False})


class AccountingExclusivity(unittest.TestCase):
    """Adversarial tests for Defect 2: duplicate accounting concealing an absence.

    Written BEFORE the fix. Against the prior implementation the first three FAIL --
    reconcile() returns reconciles=True while a genuinely absent game is masked,
    because the same league_game_id could be counted in two categories at once.
    """

    def _chain(self, game_id, status=sch.SCHEDULED):
        game = dict(GAME, league_game_id=game_id)
        c = sch.RevisionChain(game_id)
        c.append(sch.schedule_record(game, 'REGULAR_SEASON', None, status, T1))
        return c

    def test_same_id_in_chains_and_missing_is_rejected(self):
        chains = {'0022600001': self._chain('0022600001')}
        with self.assertRaises(comp.CompletenessError):
            comp.reconcile(2, chains, [], missing=['0022600001'])

    def test_duplicate_ids_within_missing_are_rejected(self):
        chains = {'0022600001': self._chain('0022600001')}
        with self.assertRaises(comp.CompletenessError):
            comp.reconcile(3, chains, [], missing=['0022600002', '0022600002'])

    def test_duplicate_accounting_cannot_conceal_an_absent_game(self):
        # The adversarial case: expected 2, ONE real game, and the same id listed
        # missing twice. Before the fix this summed to 2 and reported success.
        chains = {'0022600001': self._chain('0022600001')}
        with self.assertRaises(comp.CompletenessError):
            comp.reconcile(2, chains, [], missing=['0022600001', '0022600001'])

    def test_status_overlap_does_not_double_count_one_game(self):
        # A game postponed then cancelled has two DESCRIPTIVE statuses in its history,
        # but occupies exactly one accounting category.
        game = dict(GAME, league_game_id='0022600001')
        c = sch.RevisionChain('0022600001')
        c.append(sch.schedule_record(game, 'REGULAR_SEASON', None, sch.SCHEDULED, T1))
        sch.reschedule(c, game, 'REGULAR_SEASON', None, T1, status=sch.POSTPONED)
        sch.cancelled(c, game, 'REGULAR_SEASON', None, T1)
        report = comp.reconcile(1, {'0022600001': c}, [])
        self.assertEqual(report['accounted'], 1)
        self.assertEqual(report['buckets']['cancelled'], 1)
        self.assertEqual(report['buckets']['postponed'], 0)
        self.assertEqual(report['buckets']['canonical'], 0)

    def test_quarantine_semantics_preserved(self):
        chains = {'0022600001': self._chain('0022600001')}
        quarantined = [{'reason': ident.MISSING_LEAGUE_ID, 'detail': {}}]
        report = comp.reconcile(2, chains, quarantined)
        self.assertEqual(report['buckets']['quarantined'], 1)
        self.assertEqual(report['accounted'], 2)
        self.assertTrue(report['reconciles'])

    def test_bucket_exclusivity_across_many_games(self):
        chains = {
            '0022600001': self._chain('0022600001'),
            '0022600002': self._chain('0022600002', status=sch.POSTPONED),
            '0022600003': self._chain('0022600003', status=sch.CANCELLED),
        }
        report = comp.reconcile(5, chains, [], missing=['0022600004', '0022600005'])
        self.assertEqual(report['accounted'], 5)
        seen = set()
        for bucket in ('canonical', 'postponed', 'cancelled', 'missing'):
            for entry in report['detail'][bucket]:
                # canonical/postponed/cancelled hold bare ids; missing holds dicts.
                game_id = entry.get('league_game_id') if isinstance(entry, dict) else entry
                self.assertNotIn(game_id, seen, 'game counted in two categories')
                seen.add(game_id)

    def test_lossless_accounting_still_holds_with_rejections(self):
        chains = {'0022600001': self._chain('0022600001')}
        report = comp.reconcile(1, chains, [])
        self.assertTrue(comp.assert_lossless(report))


class PhaseCoverageInAccounting(unittest.TestCase):
    def test_cup_championship_counts_in_canonical_bucket_but_not_regular(self):
        c = chain('0022600001', phase='CUP', cup_round='CHAMPIONSHIP')
        report = comp.reconcile(1, {'0022600001': c}, [])
        self.assertEqual(report['buckets']['canonical'], 1)
        self.assertFalse(c.current()['counts_regular'])   # counted, but not regular

    def test_play_in_and_postseason_are_counted(self):
        chains = {
            '0022600001': chain('0022600001', phase='PLAY_IN'),
            '0022600002': chain('0022600002', phase='POSTSEASON'),
            '0022600003': chain('0022600003', phase='PRESEASON'),
        }
        report = comp.reconcile(3, chains, [])
        self.assertEqual(report['accounted'], 3)
        for c in chains.values():
            self.assertFalse(c.current()['counts_regular'])


if __name__ == '__main__':
    unittest.main()
