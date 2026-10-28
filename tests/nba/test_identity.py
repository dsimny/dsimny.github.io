import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import identity as ident


class LeagueIdAuthority(unittest.TestCase):
    def setUp(self):
        self.fx = json.loads((Path(__file__).parent / 'fixtures/identity_cases.json').read_text())

    def test_valid_game_is_canonical(self):
        game = ident.canonical_game(self.fx['valid_game'])
        self.assertEqual(game['league_game_id'], '0022600001')
        self.assertEqual(game['identity_authority'], 'league')

    def test_missing_league_id_quarantines_despite_provider_ids(self):
        # The ESPN label is present and plausible. It still cannot make a game.
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.canonical_game(self.fx['missing_league_id'])
        self.assertEqual(ctx.exception.reason, ident.MISSING_LEAGUE_ID)

    def test_names_never_establish_identity(self):
        # Identical names, no league id -> still quarantined. Names are display text.
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.canonical_game({'home_team_id': 'TEAM-BOS', 'away_team_id': 'TEAM-NYK',
                                  'provider_names_observed': {'espn': 'New York Knicks at Boston Celtics'}})
        self.assertEqual(ctx.exception.reason, ident.MISSING_LEAGUE_ID)

    def test_same_team_both_sides_refused(self):
        with self.assertRaises(ident.Quarantine) as ctx:
            ident.canonical_game(self.fx['same_team_both_sides'])
        self.assertEqual(ctx.exception.reason, ident.INVALID_TEAM_ID)

    def test_quarantine_carries_stable_reason(self):
        payload = ident.Quarantine(ident.AMBIGUOUS_PROVIDER_MATCH, {'a': 1}).as_dict()
        self.assertTrue(payload['quarantined'])
        self.assertEqual(payload['reason'], 'AMBIGUOUS_PROVIDER_MATCH')

    def test_unknown_quarantine_reason_refused(self):
        with self.assertRaises(ValueError):
            ident.Quarantine('NOT_A_REAL_REASON')


class Timestamps(unittest.TestCase):
    def setUp(self):
        self.fx = json.loads((Path(__file__).parent / 'fixtures/identity_cases.json').read_text())

    def test_all_eight_timestamps_persist_independently(self):
        block = ident.timestamp_block(self.fx['timestamps'])
        self.assertEqual(sorted(block), sorted(ident.TIMESTAMP_FIELDS))
        # distinct values stay distinct -- none collapsed onto another
        self.assertNotEqual(block['available_at'], block['ingested_at'])
        self.assertNotEqual(block['source_updated_at'], block['source_published_at'])
        self.assertNotEqual(block['response_received_at'], block['request_started_at'])

    def test_timestamp_field_count_is_eight(self):
        # The identity contract documents eight fields. A ninth is a contract
        # amendment (a new version), not an implementation detail, and a duplicate
        # would silently collapse two distinct pieces of evidence.
        self.assertEqual(len(ident.TIMESTAMP_FIELDS), 8)
        self.assertEqual(len(set(ident.TIMESTAMP_FIELDS)), 8)

    def test_missing_timestamp_is_absent_not_filled_from_neighbour(self):
        block = ident.timestamp_block({'event_time': '2026-10-22T23:30:00Z',
                                       'ingested_at': '2026-10-21T09:00:02Z'})
        self.assertNotIn('available_at', block)      # never substituted from ingested_at
        self.assertNotIn('decision_cutoff', block)

    def test_naive_timestamp_refused(self):
        with self.assertRaises(ValueError):
            ident.utc('2026-10-22T23:30:00')

    def test_future_availability_blocked_from_features_but_retained(self):
        fx = self.fx['future_dated']
        usable, anomalies = ident.blocks_from_features(
            fx['available_at'], fx['ingested_at'], fx['decision_cutoff'])
        self.assertFalse(usable)
        self.assertEqual([a['field'] for a in anomalies], ['available_at'])
        self.assertEqual(anomalies[0]['value'], fx['available_at'])   # retained as evidence

    def test_timestamps_within_cutoff_are_usable(self):
        fx = self.fx['timestamps']
        usable, anomalies = ident.blocks_from_features(
            fx['available_at'], fx['ingested_at'], fx['decision_cutoff'])
        self.assertTrue(usable)
        self.assertEqual(anomalies, [])
        self.assertGreater(ident.utc(fx['decision_cutoff']), ident.utc(fx['ingested_at']))

    def test_game_start_after_cutoff_is_normal_not_an_anomaly(self):
        # Regression guard for the defect this test file caught: gating the event's
        # own start against a pre-game cutoff would flag EVERY game ever played.
        fx = self.fx['timestamps']
        self.assertLess(ident.utc(fx['decision_cutoff']), ident.utc(fx['event_time']))
        usable, anomalies = ident.blocks_from_features(
            fx['available_at'], fx['ingested_at'], fx['decision_cutoff'])
        self.assertTrue(usable)
        self.assertEqual([a for a in anomalies if a['field'] == 'event_time'], [])


class ProviderIds(unittest.TestCase):
    def test_provider_ids_are_labels_only(self):
        game = ident.canonical_game({'league_game_id': '0022600001', 'home_team_id': 'A',
                                     'away_team_id': 'B', 'provider_ids': {'espn': 401810001}})
        self.assertEqual(game['provider_ids'], {'espn': '401810001'})
        self.assertEqual(game['league_game_id'], '0022600001')

    def test_non_mapping_provider_ids_refused(self):
        with self.assertRaises(ident.Quarantine):
            ident.canonical_game({'league_game_id': '0022600001', 'home_team_id': 'A',
                                  'away_team_id': 'B', 'provider_ids': ['espn']})


if __name__ == '__main__':
    unittest.main()
