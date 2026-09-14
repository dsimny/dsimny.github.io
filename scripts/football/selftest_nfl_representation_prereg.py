import os
import unittest
import asof


class PreregistrationTests(unittest.TestCase):
    def test_frozen_spec_is_in_holdout_registry(self):
        by_name = {name: (path, frozen, exists) for name, path, frozen, exists in asof.spec_status()}
        path, frozen, exists = by_name['nfl-representation-v1']
        self.assertTrue(exists)
        self.assertEqual(frozen, '2026-09-14')
        self.assertNotIn(('nfl-representation-v1', path), asof.unfrozen_specs())

    def test_spec_blocks_release_and_holdout_use(self):
        path = os.path.join(asof.ROOT, 'docs', 'FOOTBALL_PREREG_NFL_REPRESENTATION_V1.md')
        with open(path, encoding='utf-8') as f:
            text = f.read()
        prose = ' '.join(text.split())
        for phrase in ('No recommendation or release authority',
                       '2025: restricted holdout',
                       'delivery remains paused',
                       'random.Random(314159)',
                       '10,000 draws'):
            self.assertIn(phrase, prose)


if __name__ == '__main__': unittest.main()
