import copy
import hashlib
import json
from pathlib import Path
import unittest
import nfl_representation as rep


def play(**changes):
    row = dict(game_id='g1', season_type='REG', posteam='A', defteam='B',
        fixed_drive='1', fixed_drive_result='Touchdown', epa='0.5',
        rush='0', success='1', yards_gained='16', sack='0', qb_hit='1',
        qb_kneel='0', qb_spike='0', yardline_100='18')
    row['pass'] = '1'
    row.update(changes); return row


class RepresentationTests(unittest.TestCase):
    def test_frozen_manifest_matches_bytes_and_first_commit(self):
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root/'data/football/research_preregistrations.json').read_text())
        entry = manifest['registrations'][0]
        raw = (root/entry['path']).read_bytes()
        self.assertEqual(len(raw), entry['bytes'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
        self.assertEqual(entry['first_commit'], '153580b752305d75b2f81db9d47858ad99d8d450')

    def test_aggregator_has_no_market_delivery_or_result_dependency(self):
        path = Path(rep.__file__)
        source = path.read_text(encoding='utf-8')
        for forbidden in ('import market', 'import discord', 'import grade',
                          'football_ledger', 'requests', 'urllib'):
            self.assertNotIn(forbidden, source)

    def test_pass_rush_precedence_rates_and_missing_values(self):
        rows = [play(), play(fixed_drive='2', fixed_drive_result='Punt',
                    rush='1', epa='-0.5', success='', yards_gained='', sack='', qb_hit='0', yardline_100='80'),
                play(fixed_drive='3', fixed_drive_result='Punt', rush='1',
                    epa='1.0', success='0', yards_gained='9', yardline_100='50'),
                play(fixed_drive='4', fixed_drive_result='Punt', qb_kneel='1', epa='99')]
        rows[2]['pass'] = '0'
        out = rep.aggregate_rows(rows, 'source')[0]
        self.assertEqual(out['pass_n'], 2); self.assertEqual(out['rush_n'], 1)
        self.assertEqual(out['pass_epa'], 0.0); self.assertEqual(out['rush_epa'], 1.0)
        self.assertEqual(out['pass_success_rate'], 1.0); self.assertEqual(out['rush_success_rate'], 0.0)
        self.assertEqual(out['pass_explosive_rate'], 1.0); self.assertEqual(out['rush_explosive_rate'], 0.0)
        self.assertEqual(out['sack_rate'], 0.0); self.assertEqual(out['qb_hit_rate'], 0.5)
        self.assertEqual(out['missing_success_n'], 1); self.assertEqual(out['missing_yards_n'], 1)

    def test_drive_dedup_red_zone_values_conflicts_and_unknowns(self):
        rows = [play(), play(epa='0.1'),
                play(fixed_drive='2', fixed_drive_result='Field goal', yardline_100='20'),
                play(fixed_drive='3', fixed_drive_result='Opp touchdown', yardline_100='50'),
                play(fixed_drive='4', fixed_drive_result='Punt'),
                play(fixed_drive='4', fixed_drive_result='Turnover'),
                play(fixed_drive='5', fixed_drive_result='Mystery'),
                play(fixed_drive='', fixed_drive_result='Punt')]
        out = rep.aggregate_rows(rows, 'source')[0]
        self.assertEqual(out['valid_drive_n'], 3)
        self.assertEqual(out['invalid_drive_n'], 2)
        self.assertEqual(out['missing_drive_identity_n'], 1)
        self.assertAlmostEqual(out['drive_value_per_drive'], 1.0)
        self.assertEqual(out['red_zone_trip_n'], 2)
        self.assertEqual(out['red_zone_score_rate'], 1.0)
        self.assertEqual(out['red_zone_value_per_trip'], 5.0)

    def test_null_denominators_nonfinite_and_input_immutable(self):
        rows = [play(epa='nan', rush='0', fixed_drive_result='Punt')]
        rows[0]['pass'] = '0'
        original = copy.deepcopy(rows)
        out = rep.aggregate_rows(rows, 'source')[0]
        for key in ('pass_epa','rush_epa','pass_success_rate','sack_rate'):
            self.assertIsNone(out[key])
        self.assertEqual(rows, original)

    def test_opponent_conflict_fails_closed(self):
        with self.assertRaises(ValueError):
            rep.aggregate_rows([play(), play(defteam='C')], 'source')


if __name__ == '__main__': unittest.main()
