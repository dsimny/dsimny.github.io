"""Hermetic regressions for prospective market data; no prices fetched."""
import copy
import unittest

import market_input as mi
import fetch_odds

CAPTURE = '2026-09-13T12:00:00Z'
KICK = '2026-09-14T12:00:00Z'


def fixture():
    books = [{"book": f"book{i}", "last_update": CAPTURE,
              "market_last_update": {"h2h": CAPTURE},
              "markets": {"h2h": [{"name": "Away", "price": -110},
                                    {"name": "Home", "price": -110}]}}
             for i in range(6)]
    ev = dict(odds_api_event_id='event-1', away_raw='Away', home_raw='Home',
              commence_time=KICK, books=books)
    return dict(sport_key='americanfootball_ncaaf', captured_utc=CAPTURE,
                capture_role='scheduled', tier='A', events=[ev])


class MarketInputTests(unittest.TestCase):
    def test_valid_pipeline(self):
        ev, stamp = mi.event_at(fixture(), 'ncaaf', 'event-1', 'Away', 'Home', KICK, CAPTURE)
        q, rejected = mi.quotes(ev, stamp)
        self.assertEqual(rejected, {})
        out = mi.consensus(q, 'Away', 'Home')
        self.assertEqual(out['fair_away'], .5)
        self.assertIn('PASS', out['recommendation'])

    def test_event_routing(self):
        for args in [('nfl','event-1','Away','Home',KICK,CAPTURE),
                     ('ncaaf','wrong','Away','Home',KICK,CAPTURE),
                     ('ncaaf','event-1','Home','Away',KICK,CAPTURE),
                     ('ncaaf','event-1','Away','Home','2026-09-14T13:00:00Z',CAPTURE),
                     ('ncaaf','event-1','Away','Home',KICK,'2026-09-13T11:00:00Z')]:
            with self.subTest(args=args), self.assertRaises(mi.InvalidMarket):
                mi.event_at(fixture(), *args)

    def test_schedule_identity_ambiguity(self):
        for new_id in ('event-1', 'event-2'):
            s = fixture(); extra = copy.deepcopy(s['events'][0])
            extra['odds_api_event_id'] = new_id
            extra['commence_time'] = '2026-09-14T13:00:00Z'; s['events'].append(extra)
            with self.assertRaises(mi.InvalidMarket):
                mi.event_at(s,'ncaaf','event-1','Away','Home',KICK,CAPTURE)

    def test_capture_provenance_and_window(self):
        for key, value in [('capture_role','research'), ('tier','B'),
                           ('captured_utc','2026-09-12T12:00:00Z')]:
            s=fixture(); s[key]=value
            with self.assertRaises(mi.InvalidMarket):
                mi.event_at(s,'ncaaf','event-1','Away','Home',KICK,CAPTURE)

    def test_price_validation(self):
        for p in (None, True, '110', 0, 99, -99, float('inf'), float('nan')):
            with self.subTest(price=p), self.assertRaises(mi.InvalidMarket): mi.probability(p)
        self.assertEqual(mi.probability(-100), mi.probability(100))

    def test_market_timestamp_not_book_timestamp(self):
        for stamp in (None, 'bad', '2026-09-13T12:00:00',
                      '2026-09-13T12:00:01Z', '2026-09-13T11:44:59Z'):
            ev = fixture()['events'][0]
            ev['books'][0]['market_last_update']['h2h'] = stamp
            q, rejected = mi.quotes(ev, mi.utc(CAPTURE))
            self.assertEqual(len(q), 5); self.assertIn('book0', rejected)
        ev['books'][0]['market_last_update']['h2h'] = '2026-09-13T11:45:00Z'
        self.assertEqual(len(mi.quotes(ev,mi.utc(CAPTURE))[0]),6)

    def test_legacy_missing_market_timestamp_rejected(self):
        ev = fixture()['events'][0]
        for bk in ev['books']: del bk['market_last_update']
        q, rejected = mi.quotes(ev,mi.utc(CAPTURE))
        self.assertEqual(len(rejected),6)
        with self.assertRaises(mi.InvalidMarket): mi.consensus(q,'Away','Home')

    def test_outcome_identity_and_shape(self):
        for outcomes in ([{'name':'Away','price':100}]*2,
                         [{'name':'Away','price':100},{'name':'Other','price':-110}],
                         [{'name':'Away','price':100},{'name':'Home','price':-110,'point':3}],
                         [{'name':'Away','price':100},{'name':'Home','price':-110},{'name':'Draw','price':300}]):
            ev = fixture()['events'][0]; ev['books'][0]['markets']['h2h'] = outcomes
            q, rejected = mi.quotes(ev,mi.utc(CAPTURE))
            self.assertNotIn('book0',q); self.assertIn('book0',rejected)

    def test_duplicate_book_rejected(self):
        ev=fixture()['events'][0]; ev['books'].append(ev['books'][0])
        with self.assertRaises(mi.InvalidMarket): mi.quotes(ev,mi.utc(CAPTURE))

    def test_median_crossing_even_money(self):
        # Signed-American median is zero here (invalid), whereas probability
        # median is .5. This catches the real discontinuity, not rounding drift.
        q={f'b{i}': {'Away':p,'Home':-p} for i,p in enumerate([-110]*3+[110]*3)}
        out=mi.consensus(q,'Away','Home')
        self.assertAlmostEqual(out['fair_away'],.5)
        self.assertAlmostEqual(out['overround_pts'],0)

    def test_normalizer_preserves_market_timestamp(self):
        raw=[dict(id='event-1',away_team='Away',home_team='Home',commence_time=KICK,
                  bookmakers=[dict(key='draftkings',last_update=CAPTURE,
                    markets=[dict(key='h2h',last_update='2026-09-13T11:58:00Z',
                                  outcomes=[dict(name='Away',price=100),dict(name='Home',price=-110)])])])]
        events, missing=fetch_odds.normalise(raw,CAPTURE,'americanfootball_ncaaf','verbatim')
        self.assertFalse(missing)
        self.assertEqual(events[0]['books'][0]['market_last_update']['h2h'],'2026-09-13T11:58:00Z')
        self.assertEqual(events[0]['odds_api_event_id'],'event-1')


if __name__ == '__main__': unittest.main()
