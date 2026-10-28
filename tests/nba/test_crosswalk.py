import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import crosswalk as cw
from nba import identity as ident


class CrosswalkResolution(unittest.TestCase):
    def setUp(self):
        self.fx = json.loads((Path(__file__).parent / 'fixtures/crosswalk_cases.json').read_text())
        self.crosswalk = cw.Crosswalk(self.fx['entries'])

    def test_espn_label_resolves_to_league_id(self):
        self.assertEqual(self.crosswalk.resolve('espn', '401810001'), '0022600001')

    def test_nba_label_resolves(self):
        self.assertEqual(self.crosswalk.resolve('nba', '0022600001'), '0022600001')

    def test_unmapped_label_is_unresolved_not_invented(self):
        self.assertIsNone(self.crosswalk.resolve('espn', '401899999'))

    def test_unknown_provider_refused_at_construction(self):
        with self.assertRaises(ValueError):
            cw.Crosswalk([{'provider': 'yahoo', 'provider_id': '1', 'league_game_id': 'L1'}])

    def test_entry_without_league_id_refused(self):
        with self.assertRaises(ValueError):
            cw.Crosswalk([{'provider': 'espn', 'provider_id': '1', 'league_game_id': ''}])


class Conflicts(unittest.TestCase):
    def setUp(self):
        fx = json.loads((Path(__file__).parent / 'fixtures/crosswalk_cases.json').read_text())
        self.conflicted = cw.Crosswalk(fx['conflicting_entries'])

    def test_two_claims_for_one_label_conflict(self):
        self.assertTrue(self.conflicted.is_conflicted('espn', '401819999'))

    def test_conflicted_label_resolves_to_none(self):
        self.assertIsNone(self.conflicted.resolve('espn', '401819999'))

    def test_conflict_detail_lists_both_candidates(self):
        detail = self.conflicted.conflict_detail('espn', '401819999')
        self.assertEqual(sorted(detail), ['0022600010', '0022600011'])


class Provenance(unittest.TestCase):
    def test_verified_entry_requires_evidence(self):
        with self.assertRaises(ValueError):
            cw.Crosswalk([{'provider': 'espn', 'provider_id': '1', 'league_game_id': 'L1',
                           'provenance': 'verified'}])

    def test_verified_entry_with_evidence_accepted(self):
        entry = {'provider': 'espn', 'provider_id': '1', 'league_game_id': 'L1',
                 'provenance': 'verified',
                 'evidence': {'url': 'https://example.test/boxscore', 'retrieved_at': '2026-10-26T00:00:00Z'}}
        self.assertEqual(cw.Crosswalk([entry]).resolve('espn', '1'), 'L1')

    def test_synthetic_is_the_default(self):
        self.assertEqual(cw.Crosswalk([{'provider': 'espn', 'provider_id': '1',
                                        'league_game_id': 'L1'}]).provenance_of('espn', '1'), 'synthetic')

    def test_digest_is_stable_and_order_independent(self):
        a = [{'provider': 'espn', 'provider_id': '1', 'league_game_id': 'L1'},
             {'provider': 'espn', 'provider_id': '2', 'league_game_id': 'L2'}]
        self.assertEqual(cw.digest(a), cw.digest(list(reversed(a))))


class ApplyingCrosswalk(unittest.TestCase):
    def setUp(self):
        fx = json.loads((Path(__file__).parent / 'fixtures/crosswalk_cases.json').read_text())
        self.crosswalk = cw.Crosswalk(fx['entries'])

    def test_mapped_offer_resolves(self):
        resolved, quarantined = cw.apply_crosswalk(
            [{'provider_id': '401810001', 'record': {'home_team_id': 'B', 'away_team_id': 'N'}}],
            self.crosswalk, 'espn')
        self.assertEqual(resolved[0]['league_game_id'], '0022600001')
        self.assertEqual(quarantined, [])

    def test_unmapped_offer_quarantined_not_guessed(self):
        # Plausible names and a real-looking date are present. They are not consulted.
        resolved, quarantined = cw.apply_crosswalk(
            [{'provider_id': '401899999',
              'record': {'home_team_id': 'TEAM-NYK', 'away_team_id': 'TEAM-BOS',
                         'event_time': '2026-10-22T23:30:00Z'}}],
            self.crosswalk, 'espn')
        self.assertEqual(resolved, [])
        self.assertEqual(quarantined[0]['reason'], ident.MISSING_LEAGUE_ID)

    def test_conflicted_offer_quarantined_as_ambiguous(self):
        fx = json.loads((Path(__file__).parent / 'fixtures/crosswalk_cases.json').read_text())
        conflicted = cw.Crosswalk(fx['conflicting_entries'])
        resolved, quarantined = cw.apply_crosswalk(
            [{'provider_id': '401819999', 'record': {}}], conflicted, 'espn')
        self.assertEqual(resolved, [])
        self.assertEqual(quarantined[0]['reason'], ident.AMBIGUOUS_PROVIDER_MATCH)
        self.assertEqual(sorted(quarantined[0]['detail']['candidates']), ['0022600010', '0022600011'])

    def test_later_invalid_offer_does_not_invalidate_earlier_valid_one(self):
        resolved, quarantined = cw.apply_crosswalk(
            [{'provider_id': '401810001', 'record': {}},
             {'provider_id': '', 'record': {}}],
            self.crosswalk, 'espn')
        self.assertEqual(len(resolved), 1)
        self.assertEqual(len(quarantined), 1)


if __name__ == '__main__':
    unittest.main()
