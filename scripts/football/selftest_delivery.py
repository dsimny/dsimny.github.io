"""Offline delivery incident regressions; no real webhook or status writes."""
import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import delivery_policy as policy
import discord
from selftest_discord import board


class DeliveryTests(unittest.TestCase):
    def test_shipped_pause_is_enabled(self):
        self.assertIs(policy.PAUSED, True)

    def test_public_pause_preserves_history(self):
        with patch.object(policy, 'PAUSED', True), \
             patch.object(discord.fbpage, 'PREMIUM_URL', 'https://example.com/checkout'):
            self.assertIn('recommendations are paused', discord.fbpage.pause_notice())
            self.assertEqual(discord.fbpage.upgrade_block(), '')
            card = discord.fbpage.game_card(self.b['free'], True)
            self.assertIn('Historical captured price', card)
            self.assertIn(self.b['free']['side'], card)

    def setUp(self):
        self.now = datetime(2026, 9, 12, 18, 40, tzinfo=timezone.utc)
        self.b = board(3)
        self.b['selection_version'] = policy.record_policy.VERSION
        for g in self.b['games']:
            g.update(sport='ncaaf', selection_version=policy.record_policy.VERSION,
                     selection_eligible=True, commitment_sha='fixture')

    def check(self, b):
        policy.validate(b, '2026-09-08', self.now)

    def test_valid(self):
        self.check(self.b)

    def test_manual_review(self):
        self.b['coverage_status'] = 'manual review'
        with self.assertRaises(ValueError): self.check(self.b)

    def test_invalid_cases(self):
        for key, value in [('selection_version', 'legacy'), ('slate_week', '2026-09-01'),
                           ('decision_made', False)]:
            with self.subTest(key=key):
                b = copy.deepcopy(self.b); b[key] = value
                with self.assertRaises(ValueError): self.check(b)
        for key, value in [('kickoff_utc', '2026-09-12T18:00:00Z'),
                           ('fair_side', float('nan')), ('best_price', 0),
                           ('sport', 'preseason'), ('selection_eligible', False),
                           ('commitment_sha', None)]:
            with self.subTest(key=key):
                b = copy.deepcopy(self.b); b['premium'][key] = value
                with self.assertRaises(ValueError): self.check(b)

    def test_duplicate_schedule(self):
        g = dict(self.b['games'][0]); g['kickoff_utc'] = '2026-09-12T21:00:00Z'
        self.b['games'].append(g)
        with self.assertRaises(ValueError): self.check(self.b)

    def test_pause_cannot_force_send_or_mutate(self):
        with patch.object(policy, 'PAUSED', True), patch.object(discord, 'send') as send, \
             patch.object(discord, 'record') as record, \
             patch.object(discord.fbpage, 'load_board') as load:
            for mode in ('free', 'slate'):
                with patch('sys.argv', ['discord.py', mode, '--week', '2026-09-08', '--force']):
                    self.assertEqual(discord.main(), 0)
            send.assert_not_called(); record.assert_not_called(); load.assert_not_called()

    def test_coverage_explanation(self):
        text = discord.game_embed(self.b['games'][2])['description']
        self.assertIn('not a recommended play', text)
        self.assertIn('not expected returns', text)
        self.assertIn('Historical capture', text)

    def test_screenshot_market_math(self):
        # Median and best prices independently transcribed from saved captures.
        for away, home, best_a, best_h, raw, effective, fair in (
                (237, -295, 250, -275, 4.357, 1.905, .28435),
                (230, -280, 240, -265, 3.987, 2.015, .29141),
                (3500, -12000, 4000, -6500, 1.951, .924, .02725)):
            ia, ih = policy.market.implied(away), policy.market.implied(home)
            self.assertEqual(round(100 * (ia + ih - 1), 3), raw)
            self.assertEqual(round(100 * (policy.market.implied(best_a) +
                                         policy.market.implied(best_h) - 1), 3), effective)
            self.assertEqual(round(ia / (ia + ih), 5), fair)

    def test_pause_prevents_new_weekly_board(self):
        import board as boardmod
        b = dict(self.b, n_newly_committed=0)
        with patch('sys.argv', ['board.py', '--week', '2026-09-08', '--writeups']), \
             patch.object(boardmod, 'build', return_value=b), \
             patch.object(boardmod, 'render', return_value='fixture'), \
             patch.object(boardmod, 'record_commitment') as commit, \
             patch.object(boardmod.crypto_box, 'encrypt_to') as encrypt, \
             patch.object(policy, 'PAUSED', True):
            self.assertEqual(boardmod.main(), 0)
            commit.assert_not_called(); encrypt.assert_not_called()


if __name__ == '__main__':
    unittest.main()
