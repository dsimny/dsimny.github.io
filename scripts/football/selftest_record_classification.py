import unittest
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import board
import grade_committed as grader
import page
import member_correction
from unittest.mock import MagicMock
from selftest_reset import fixture
import record_policy as policy


class ClassificationTests(unittest.TestCase):
    def test_notice_refuses_rerun_before_contacting_discord(self):
        with patch.dict("os.environ", {"GITHUB_RUN_ATTEMPT": "2"}), patch.object(member_correction.urllib.request, "urlopen") as send:
            with self.assertRaises(RuntimeError): member_correction.main()
            send.assert_not_called()

    def test_notice_reservation_is_atomic_create_without_overwrite_sha(self):
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/repo"}), patch.object(member_correction.subprocess, "run", return_value=MagicMock(returncode=1)) as run:
            with self.assertRaises(RuntimeError): member_correction.create_evidence("reserved", {"notice": "test"})
            body = json.loads(run.call_args.kwargs["input"])
            self.assertNotIn("sha", body)
            self.assertEqual(body["branch"], "main")

    def test_only_incident_hash_is_excluded(self):
        base = {"record_cohort": policy.VERSION, "selection_version": policy.VERSION,
                "tier": "free", "kickoff_utc": "2026-09-13T04:00:00Z"}
        bad = dict(base, board_sha256=next(iter(policy.INVALIDATED_BOARD_SHA256)))
        good = dict(base, board_sha256="other-board-hash")
        self.assertFalse(policy.is_official(bad))
        self.assertTrue(policy.is_official(good))

    def test_invalidated_rows_remain_partitioned(self):
        row = {"record_cohort": policy.VERSION, "selection_version": policy.VERSION,
               "tier": "premium", "board_sha256": next(iter(policy.INVALIDATED_BOARD_SHA256)),
               "kickoff_utc": "2026-09-13T04:00:00Z"}
        official, pilot, research = policy.partition([row])
        self.assertEqual(official, [])
        self.assertEqual(pilot, [row])
        self.assertEqual(research, [])

    def test_exclusion_is_outcome_independent_and_does_not_mutate(self):
        rows = [dict(board_sha256=next(iter(policy.INVALIDATED_BOARD_SHA256)),
                     result=r, tier="free", record_cohort=policy.VERSION)
                for r in ("win", "loss", "push")]
        original = copy.deepcopy(rows)
        self.assertEqual(policy.partition(rows), ([], rows, []))
        self.assertEqual(rows, original)

    def test_repeated_grading_does_not_duplicate_excluded_settlement(self):
        g, b, c, results = fixture()
        cfg = {"keyfn": None, "gradeable": lambda r: True}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "commitments.json"
            p.write_text(json.dumps({"commitments": [c]}), encoding="utf-8")
            with patch.object(board, "COMMITMENTS", str(p)), patch.object(grader, "read_board", return_value=b), patch.object(policy, "INVALIDATED_BOARD_SHA256", {c["board_sha256"]}):
                ledger = {"entries": []}
                ledger["entries"].extend(grader.additions("nfl", ledger, results, cfg, []))
                self.assertEqual(len(ledger["entries"]), 1)
                self.assertFalse(policy.is_official(ledger["entries"][0]))
                original = copy.deepcopy(ledger)
                for _ in range(2):
                    self.assertEqual(grader.additions("nfl", ledger, results, cfg, []), [])
                self.assertEqual(ledger, original)

    def test_page_retains_incident_losses_outside_research(self):
        row = dict(board_sha256=next(iter(policy.INVALIDATED_BOARD_SHA256)),
                   record_cohort=policy.VERSION, tier="premium", result="loss",
                   matchup="Incident fixture", price=250)
        with tempfile.TemporaryDirectory() as td:
            Path(td, "football_ledger.json").write_text(json.dumps({"entries": [row]}), encoding="utf-8")
            with patch.object(page, "FB", td), patch.object(page, "OUT", td), patch.object(page, "write") as write:
                page.render_hub()
                html = write.call_args.args[1]
                incident_section = html.split('id="incident"')[1].split('id="pilot"')[0]
                self.assertIn("Incident fixture", incident_section)
                self.assertIn("loss", incident_section)
                self.assertIn("0 committed plays", html)
                self.assertNotIn("Incident fixture", html.split("Full covered slate — research")[1])


if __name__ == '__main__': unittest.main()
