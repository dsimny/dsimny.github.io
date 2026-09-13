"""Exact adjudication tests; no legacy pick or kickoff is rewritten."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import result_identity
import grade_committed as grader
from selftest_reset import fixture


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch=patch.object(result_identity,'DIRECTORY',Path(self.tmp.name))
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.g,self.b,self.c,self.results=fixture()
        self.results['123']['kickoff_utc']='2026-09-13T16:59:00Z'
        self.cfg={'keyfn':None,'gradeable':lambda r:True}
        self.item=dict(resolution_id='fixture',sport='nfl',away='A',home='H',
            committed_kickoff_utc=self.g['kickoff_utc'],result_kickoff_utc='2026-09-13T16:59:00Z',
            espn_event_id='123',board_sha256=self.c['board_sha256'],game_commitment_sha=self.g['commitment_sha'])

    def save(self):
        (Path(self.tmp.name)/'one.json').write_text(json.dumps(self.item))

    def settle(self):
        return grader.settle(self.g,'premium',self.b,self.c,self.results,self.cfg,[])

    def test_no_automatic_tolerance(self):
        self.assertIsNone(self.settle())

    def test_exact_adjudication_preserves_original(self):
        self.save(); before=copy.deepcopy(self.g)
        row=self.settle()
        self.assertEqual(row['result_identity_resolution'],'fixture')
        self.assertEqual(row['kickoff_utc'],before['kickoff_utc'])
        self.assertEqual(row['result_kickoff_utc'],'2026-09-13T16:59:00Z')
        self.assertEqual(self.g,before)

    def test_no_cross_commitment_reuse(self):
        for key in ('board_sha256','game_commitment_sha'):
            saved=self.item[key]; self.item[key]='wrong'; self.save()
            self.assertIsNone(self.settle()); self.item[key]=saved

    def test_changed_result_fails(self):
        self.save()
        for key,value in [('home','Other'),('kickoff_utc','2026-09-13T16:58:00Z')]:
            saved=self.results['123'][key]; self.results['123'][key]=value
            with self.assertRaises(ValueError): self.settle()
            self.results['123'][key]=saved

    def test_duplicate_adjudication_fails(self):
        self.save(); (Path(self.tmp.name)/'two.json').write_text(json.dumps(self.item))
        with self.assertRaises(ValueError): self.settle()

    def test_nonfinal_does_not_grade(self):
        self.save(); self.results['123']['final']=False
        self.assertIsNone(self.settle())

    def test_corrected_kickoff_excludes_inplay_close(self):
        self.save()
        snap=(grader.market.parse_utc('2026-09-13T16:59:30Z'),'late.json',{})
        with patch.object(grader.market,'find_event') as find:
            row=grader.settle(self.g,'premium',self.b,self.c,self.results,self.cfg,[snap])
            find.assert_not_called()
            self.assertIsNone(row['clv_pts'])


if __name__=='__main__': unittest.main()
