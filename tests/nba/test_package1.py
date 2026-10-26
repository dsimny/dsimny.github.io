from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from nba import contracts as c, free_capture as raw


class Contracts(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / 'fixtures/synthetic_cases.json').read_text())

    def test_each_game_phase_including_cup_final(self):
        for case in self.fixture['phases']:
            with self.subTest(case=case):
                self.assertEqual(c.counts_regular(case['phase'], case.get('cup_round')),
                                 case['counts_regular'])

    def test_unknown_phase_or_round_is_not_guessed(self):
        for args in [('UNKNOWN',), ('CUP',), ('CUP', 'FINAL'), ('PRESEASON', 'GROUP')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                c.counts_regular(*args)

    def test_repeated_opponents_have_distinct_league_ids(self):
        games = self.fixture['repeat_matchups']
        self.assertEqual(games[0]['home_id'], games[1]['home_id'])
        self.assertNotEqual(games[0]['league_game_id'], games[1]['league_game_id'])

    def test_dst_repeated_hour_is_not_one_instant(self):
        a, b = map(c.utc, self.fixture['dst'])
        self.assertEqual((b - a).total_seconds(), 3600)

    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            c.utc('2026-10-06T12:00:00')

    def test_observation_is_distinct_from_provider_time(self):
        q = self.fixture['quote']
        self.assertGreater(c.utc(q['observed_at']), c.utc(q['provider_updated_at']))

    def test_future_availability_and_late_ingestion_refused(self):
        early, late = '2026-10-06T10:00:00Z', '2026-10-06T11:00:00Z'
        c.require_available(early, early, early)
        for available, ingested in [(late, early), (early, late)]:
            with self.assertRaises(ValueError):
                c.require_available(available, ingested, early)

    def test_ev_example_and_push(self):
        self.assertAlmostEqual(c.expected_return(.558, .442, 0, -110), .06527272727)
        self.assertEqual(c.expected_return(0, 0, 1, -110), 0)
        self.assertAlmostEqual(c.expected_return(.5, .4, .1, 150), .35)

    def test_invalid_odds_and_probability(self):
        for price in [True, 0, -99, float('nan'), float('inf'), '110']:
            with self.subTest(price=price), self.assertRaises(ValueError):
                c.decimal_odds(price)
        for probs in [(0.5, .6, 0), (True, 0, 0), (.5, float('nan'), .5)]:
            with self.subTest(probs=probs), self.assertRaises(ValueError):
                c.expected_return(*probs, -110)


class Capture(unittest.TestCase):
    source = [('fixture', 'schedule', 'https://www.nba.com/schedule')]

    @staticmethod
    def fake(url):
        return 200, {'Content-Type': 'text/html', 'Last-Modified': 'yesterday'}, b'SYNTHETIC ONLY', url

    def test_two_unchanged_polls_keep_two_observations(self):
        with TemporaryDirectory() as d:
            root, key = Path(d)/'evidence', Path(d)/'secret.key'
            a = raw.capture(root, key, self.source, self.fake)
            b = raw.capture(root, key, self.source, self.fake)
            self.assertNotEqual(a['run_id'], b['run_id'])
            self.assertEqual(raw.verify(root,key), {'runs':2,'observations':2,'fetch_failures':0})
            self.assertEqual(len(list((root/'objects').glob('*'))), 2)
            for obj in (root/'objects').glob('*'):
                self.assertNotIn(b'SYNTHETIC ONLY', obj.read_bytes())

    def test_failure_preserved_without_sensitive_exception_text(self):
        def broken(url):
            raise RuntimeError('secret-canary')
        with TemporaryDirectory() as d:
            root, key = Path(d)/'evidence', Path(d)/'key'
            raw.capture(root,key,self.source,broken)
            self.assertEqual(raw.verify(root,key)['fetch_failures'],1)
            self.assertNotIn('secret-canary',next((root/'observations').glob('*')).read_text())

    def test_http_error_not_valid_coverage(self):
        with TemporaryDirectory() as d:
            root, key = Path(d)/'evidence',Path(d)/'key'
            raw.capture(root,key,self.source,lambda u:(404,{},b'not found',u))
            rec=json.loads(next((root/'observations').glob('*')).read_text())
            self.assertEqual(rec['error_code'],'HTTP_NON_SUCCESS')
            self.assertEqual(rec['semantic_coverage'],'UNVERIFIED')

    def test_lost_encryption_key_is_not_replaced(self):
        with TemporaryDirectory() as d:
            root,key=Path(d)/'evidence',Path(d)/'key'
            raw.capture(root,key,self.source,self.fake)
            key.unlink()
            with self.assertRaises(ValueError):
                raw.capture(root,key,self.source,self.fake)
            self.assertFalse(key.exists())

    def test_tampered_payload_and_manifest_rejected(self):
        with TemporaryDirectory() as d:
            root,key=Path(d)/'evidence',Path(d)/'key'
            raw.capture(root,key,self.source,self.fake)
            obj=next((root/'objects').glob('*'))
            obj.write_bytes(b'corruption')
            with self.assertRaises(ValueError):
                raw.verify(root,key)

    def test_no_overwrite(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'fact'
            raw.exclusive(p,b'original')
            with self.assertRaises(FileExistsError):
                raw.exclusive(p,b'replacement')
            self.assertEqual(p.read_bytes(),b'original')

    def test_modified_observation_and_unmanifested_fact_refused(self):
        with TemporaryDirectory() as d:
            root,key=Path(d)/'evidence',Path(d)/'key'
            raw.capture(root,key,self.source,self.fake)
            record=next((root/'observations').glob('*'))
            record.write_text('{}')
            with self.assertRaises(ValueError):
                raw.verify(root,key)
        with TemporaryDirectory() as d:
            root,key=Path(d)/'evidence',Path(d)/'key'
            raw.capture(root,key,self.source,self.fake)
            (root/'observations'/'orphan.json').write_text('{}')
            with self.assertRaises(ValueError):
                raw.verify(root,key)

    def test_changed_response_new_fact_and_redirect_failure(self):
        with TemporaryDirectory() as d:
            root,key=Path(d)/'evidence',Path(d)/'key'
            raw.capture(root,key,self.source,self.fake)
            raw.capture(root,key,self.source,lambda u:(200,{},b'new announcement',u))
            records=[json.loads(p.read_text()) for p in (root/'observations').glob('*')]
            self.assertEqual(len({r['raw_sha256'] for r in records}),2)
            raw.capture(root,key,self.source,lambda u:(200,{},b'evil','https://example.com/'))
            self.assertEqual(raw.verify(root,key)['fetch_failures'],1)

    def test_evidence_and_key_inside_git_refused(self):
        with TemporaryDirectory() as d:
            p=Path(d)
            (p/'.git').write_text('gitdir: elsewhere')
            with self.assertRaises(ValueError):
                raw.capture(p/'raw',p/'key',self.source,self.fake)

    def test_url_and_redirect_restrictions(self):
        for url in ['http://www.nba.com/', 'https://nba.com.evil.test/',
                    'https://user:pass@www.nba.com/', 'https://www.nba.com/?apiKey=x',
                    'https://api.the-odds-api.com/v4/sports']:
            with self.subTest(url=url),self.assertRaises(ValueError):
                raw.safe_url(url)
        with self.assertRaises(ValueError):
            raw.SafeRedirect().redirect_request(None,None,302,'',{},'https://example.com/')

    def test_size_limit_and_discovery_limit(self):
        html = b''.join(f'<a href="/news/injury-{i}">Availability</a>'.encode() for i in range(20))
        with TemporaryDirectory() as d:
            root,key=Path(d)/'evidence',Path(d)/'key'
            manifest=raw.capture(root,key,self.source,
                lambda u:(200,{'Content-Type':'text/html'},html,u),discovery_limit=2)
            self.assertEqual(len(manifest['observations']),3)
        with TemporaryDirectory() as d:
            root,key=Path(d)/'evidence',Path(d)/'key'
            raw.capture(root,key,self.source,lambda u:(200,{},b'x'*(raw.MAX_BYTES+1),u))
            self.assertEqual(raw.verify(root,key)['fetch_failures'],1)


if __name__ == '__main__':
    unittest.main()
