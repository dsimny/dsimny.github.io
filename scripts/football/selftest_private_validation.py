"""Hermetic diagnostic gates; synthetic fixtures cannot certify a strategy."""
import copy
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import private_validation as pv
import market_input as mi
from selftest_market_input import fixture, CAPTURE


class PrivateValidationTests(unittest.TestCase):
    def test_source_choice_ignores_future_and_research_captures(self):
        with tempfile.TemporaryDirectory() as td:
            for name, stamp, role in [('old', CAPTURE, 'scheduled'),
                    ('future', '2026-09-13T12:01:00Z', 'scheduled'),
                    ('research', CAPTURE, 'research')]:
                snap = fixture(); snap.update(captured_utc=stamp, capture_role=role)
                Path(td, 'ncaaf_' + name + '.json').write_text(json.dumps(snap), encoding='utf-8')
            path, raw, snap = pv.latest_capture(Path(td), 'ncaaf', CAPTURE)
            self.assertEqual(path.name, 'ncaaf_old.json')
            self.assertEqual(json.loads(raw), snap)

    def test_cli_preserves_sources_and_refuses_output_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'repo'; odds = root / 'data/football/odds'
            odds.mkdir(parents=True)
            for sport, key in mi.SPORT_KEYS.items():
                snap = fixture(); snap['sport_key'] = key
                (odds / (sport + '_fixture.json')).write_text(json.dumps(snap), encoding='utf-8')
            originals = {p: p.read_bytes() for p in odds.iterdir()}
            output = Path(td) / 'private.json'
            with patch.object(pv, 'ROOT', root), patch('sys.argv', ['audit', '--asof', CAPTURE, '--output', str(output)]):
                pv.main()
                first = output.read_bytes()
                with self.assertRaises(FileExistsError): pv.main()
                self.assertEqual(output.read_bytes(), first)
            self.assertEqual({p: p.read_bytes() for p in odds.iterdir()}, originals)
            report = json.loads(first)
            for value in report['sports'].values(): pv.verify_bundle(value)

    def test_cli_refuses_public_output(self):
        with patch('sys.argv', ['audit', '--asof', CAPTURE, '--output', str(pv.ROOT / 'unsafe.json')]):
            with self.assertRaises(SystemExit): pv.main()

    def test_both_sports_never_release_even_with_complete_data(self):
        for sport, key in mi.SPORT_KEYS.items():
            snap = fixture(); snap['sport_key'] = key
            report = pv.audit_snapshot(snap, sport, CAPTURE)
            self.assertEqual(report['events'][0]['status'], 'OBSERVATION_ONLY')
            self.assertFalse(report['release_allowed'])
            pv.verify_bundle(pv.bundle(report))

    def test_wrong_sport_and_stale_capture_refused(self):
        for sport, asof in [('nfl', CAPTURE), ('ncaaf', '2026-09-13T13:00:01Z'), ('ncaaf', '2026-09-13T11:59:59Z')]:
            with self.assertRaises(mi.InvalidMarket): pv.audit_snapshot(fixture(), sport, asof)

    def test_started_event_cannot_be_pregame(self):
        snap = fixture(); snap['events'][0]['commence_time'] = CAPTURE
        report = pv.audit_snapshot(snap, 'ncaaf', CAPTURE)
        self.assertIn('already started', report['events'][0]['reason'])

    def test_missing_market_timestamps_are_visible(self):
        snap = fixture()
        for book in snap['events'][0]['books']: del book['market_last_update']
        report = pv.audit_snapshot(snap, 'ncaaf', CAPTURE)
        self.assertEqual(report['events'][0]['status'], 'REJECTED')
        self.assertEqual(len(report['events'][0]['quote_rejections']), 6)

    def test_report_and_preview_tampering_rejected(self):
        original = pv.bundle(pv.audit_snapshot(fixture(), 'ncaaf', CAPTURE))
        for field in ('report', 'discord_preview'):
            value = copy.deepcopy(original)
            value[field]['unexpected'] = 'changed'
            with self.assertRaises(ValueError): pv.verify_bundle(value)

    def test_fixture_immutable_and_deterministic(self):
        snap = fixture(); original = copy.deepcopy(snap)
        a = pv.bundle(pv.audit_snapshot(snap, 'ncaaf', CAPTURE))
        self.assertEqual(a, pv.bundle(pv.audit_snapshot(snap, 'ncaaf', CAPTURE)))
        self.assertEqual(snap, original)


if __name__ == '__main__': unittest.main()
