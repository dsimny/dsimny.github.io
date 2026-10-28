import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from nba import contracts
from nba import identity as ident


class Package1Preserved(unittest.TestCase):
    """Package 2A reads Package 1; it must not alter it.

    These assertions pin the Package 1 surface Package 2A depends on, so a future
    edit to Package 1 that would silently change Package 2A's behaviour fails here.
    """

    def test_package1_test_file_unchanged(self):
        path = Path(__file__).parent / 'test_package1.py'
        # 20 original tests; the hardening commit added 4. Exactly 24 expected.
        text = path.read_text(encoding='utf-8')
        self.assertEqual(text.count('    def test_'), 24)

    def test_phase_sets_unchanged(self):
        self.assertEqual(contracts.PHASES,
                         {'PRESEASON', 'REGULAR_SEASON', 'CUP', 'PLAY_IN', 'POSTSEASON'})
        self.assertEqual(contracts.CUP_ROUNDS,
                         {'GROUP', 'QUARTERFINAL', 'SEMIFINAL', 'CHAMPIONSHIP'})

    def test_counts_regular_authority_preserved(self):
        self.assertTrue(contracts.counts_regular('CUP', 'SEMIFINAL'))
        self.assertFalse(contracts.counts_regular('CUP', 'CHAMPIONSHIP'))
        self.assertTrue(contracts.counts_regular('REGULAR_SEASON'))
        for phase in ('PRESEASON', 'PLAY_IN', 'POSTSEASON'):
            self.assertFalse(contracts.counts_regular(phase))

    def test_identity_utc_matches_contracts_utc(self):
        # Duplicated by design (no import), so asserted identical.
        for value in ('2026-10-06T12:00:00Z', '2026-11-01T01:30:00-04:00'):
            self.assertEqual(ident.utc(value), contracts.utc(value))

    def test_identity_utc_refuses_naive_like_contracts_does(self):
        for module in (ident, contracts):
            with self.subTest(module=module.__name__), self.assertRaises(ValueError):
                module.utc('2026-10-06T12:00:00')


if __name__ == '__main__':
    unittest.main()
