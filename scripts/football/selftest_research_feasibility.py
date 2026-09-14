import copy
import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest
import research_feasibility as rf
from selftest_market_input import fixture, CAPTURE, KICK


class FeasibilityTests(unittest.TestCase):
    def test_capture_without_fixture_cannot_satisfy_window(self):
        schedule = [{'away': 'Away', 'home': 'Home', 'kickoff_utc': KICK}]
        snap = fixture(); snap['events'] = []
        out = rf.coverage(schedule, [('capture', snap)], 'ncaaf', CAPTURE)
        self.assertEqual(out[0]['status'], 'MISSING_OR_INVALID')
        self.assertIn('absent', out[0]['rejected'][0]['reason'])

    def test_schedule_change_bad_quotes_and_wrong_sport_rejected(self):
        schedule = [{'away': 'Away', 'home': 'Home', 'kickoff_utc': KICK}]
        for mode in ('schedule', 'quotes', 'sport'):
            snap = fixture()
            if mode == 'schedule': snap['events'][0]['commence_time'] = '2026-09-14T13:00:00Z'
            if mode == 'quotes': snap['events'][0]['books'] = []
            if mode == 'sport': snap['sport_key'] = 'americanfootball_nfl'
            self.assertEqual(rf.coverage(schedule, [('capture', snap)], 'ncaaf', CAPTURE)[0]['valid_observations'], 0)

    def test_valid_observation_and_future_not_due(self):
        schedule = [{'away': 'Away', 'home': 'Home', 'kickoff_utc': KICK}]
        snap = fixture(); original = copy.deepcopy(snap)
        self.assertEqual(rf.coverage(schedule, [('capture', snap)], 'ncaaf', CAPTURE)[0]['valid_observations'], 1)
        schedule[0]['kickoff_utc'] = '2026-09-20T12:00:00Z'
        self.assertEqual(rf.coverage(schedule, [('capture', snap)], 'ncaaf', CAPTURE)[0]['status'], 'NOT_YET_DUE')
        self.assertEqual(snap, original)

    def test_schema_hash_and_holdout_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            header = sorted(set().union(*rf.FAMILIES.values()))
            raw = gzip.compress((','.join(header)+'\n').encode())
            Path(td, 'play_by_play_2010.csv.gz').write_bytes(raw)
            Path(td, 'play_by_play_2025.csv.gz').write_bytes(b'never parse holdout')
            manifest = {'seasons': {'2010': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}}
            rows = rf.inventory(td, manifest)
            self.assertEqual(len(rows), 15)
            self.assertTrue(rows[0]['ready'])
            manifest['seasons']['2010']['sha256'] = 'mismatch'
            self.assertFalse(rf.inventory(td, manifest)[0]['ready'])


if __name__ == '__main__': unittest.main()
