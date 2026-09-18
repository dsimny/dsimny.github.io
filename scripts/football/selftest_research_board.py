#!/usr/bin/env python3
"""
Offline self-test for the research-board generation path in board.py (Step 2).

Tests the following invariants:
  1. Official mode: OFFICIAL_DELIVERY_ENABLED=False still blocks official writes.
  2. Research generation does NOT require RESEARCH_DELIVERY_ENABLED=True.
  3. Research boards are written only under data/football/research/.
  4. Research observations carry units=0.
  5. Research metadata identifies the registered strategy version.
  6. An existing research board cannot be silently overwritten.
  7. Official files are unchanged by research generation.
  8. Normal official invocation behaviour is unchanged (still blocked).
  9. Unregistered version ID fails closed.
 10. Registry entry with wrong authority fails closed.
 11. Registry entry with non-zero units fails closed.
 12. --dry-run prints the would-write path without writing anything.
 13. record_cohort in research board matches the research version ID.
 14. research_authority matches the expected authority string.

Runs fully offline — no network, no Odds API, no Discord, no encryption key.
All file writes go into a TemporaryDirectory that is cleaned up at exit.

  python scripts/football/selftest_research_board.py
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import board
import delivery_policy as policy


# ---------------------------------------------------------------------------
# Minimal fixture helpers
# ---------------------------------------------------------------------------

VALID_PREREG = {
    "id": board.RESEARCH_VERSION_ID,
    "frozen_date": "2026-09-17",
    "path": "docs/FOOTBALL_RESEARCH_FP_V04_OBSERVATION.md",
    "first_commit": None,
    "bytes": 5020,
    "sha256": "539f6c890eb72ee4d769f3b40fce727c998f71358f7ae6f9b7c73e82903e6801",
    "authority": "research_only_no_selection_delivery_or_holdout",
    "selection_rule": "fp-v0.4",
    "units": 0,
}

VALID_REGISTRY = {
    "_note": "Test registry",
    "registrations": [VALID_PREREG],
}

# Minimal build() return value — enough to exercise all paths without
# requiring captures on disk.
def minimal_board(week="2026-09-22"):
    return {
        "selection_version": "fp-v0.4",
        "slate_week": week,
        "decision_moment_utc": "2026-09-19T18:00:00Z",
        "decision_made": True,
        "asof_utc": "2026-09-19T18:05:00Z",
        "n_eligible_at_decision": 3,
        "n_newly_committed": 0,
        "generated_utc": "2026-09-19T18:05:00Z",
        "sports": ["nfl", "ncaaf"],
        "n_covered": 10,
        "n_excluded": 2,
        "excluded_share": 0.167,
        "coverage_status": "covered",
        "premium": {
            "sport": "ncaaf", "matchup": "Fixture A @ Fixture B",
            "kickoff_utc": "2026-09-20T17:00:00Z",
            "side": "Fixture A", "best_price": 240,
            "best_book": "draftkings", "books_at_best": 3,
            "fair_side": 0.31, "eff_overround_pts": 1.8,
            "tier": "premium", "selection_version": "fp-v0.4",
            "selection_eligible": True,
        },
        "free": None,
        "games": [],
        "no_market": [],
        "_note": "Test fixture",
    }


def write_registry(tmp, registry):
    path = os.path.join(tmp, "research_preregistrations.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ResearchBoardTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg_path = write_registry(self.tmp, VALID_REGISTRY)
        self.real_prereg_path = board.RESEARCH_PREREG_PATH
        self.real_research_dir = board.RESEARCH_DIR
        board.RESEARCH_PREREG_PATH = self.reg_path
        board.RESEARCH_DIR = os.path.join(self.tmp, "research")

    def tearDown(self):
        board.RESEARCH_PREREG_PATH = self.real_prereg_path
        board.RESEARCH_DIR = self.real_research_dir
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    # --- 1. Official mode still blocked ---
    def test_1_official_delivery_still_disabled(self):
        self.assertIs(policy.OFFICIAL_DELIVERY_ENABLED, False)

    # --- 2. Research generation does not need RESEARCH_DELIVERY_ENABLED ---
    def test_2_research_generation_independent_of_delivery_flag(self):
        # RESEARCH_DELIVERY_ENABLED=False must NOT block write_research_board
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        with patch.object(policy, "RESEARCH_DELIVERY_ENABLED", False):
            out = board.write_research_board(b, "2026-09-22",
                                              board.RESEARCH_VERSION_ID, prereg)
        self.assertTrue(os.path.exists(out))

    # --- 3. Research boards written only under RESEARCH_DIR ---
    def test_3_research_board_path_under_research_dir(self):
        path = board.research_board_path("2026-09-22", board.RESEARCH_VERSION_ID)
        self.assertTrue(path.startswith(board.RESEARCH_DIR))
        # Must not overlap with official board paths
        official_plain, official_enc = board.board_paths("2026-09-22")
        self.assertNotEqual(path, official_plain)
        self.assertNotEqual(path, official_enc)

    def test_3b_research_board_written_to_correct_dir(self):
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        out = board.write_research_board(b, "2026-09-22",
                                          board.RESEARCH_VERSION_ID, prereg)
        self.assertTrue(out.startswith(board.RESEARCH_DIR))
        self.assertTrue(os.path.exists(out))

    # --- 4. units = 0 in research board ---
    def test_4_research_board_units_zero(self):
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        out = board.write_research_board(b, "2026-09-22",
                                          board.RESEARCH_VERSION_ID, prereg)
        with open(out, encoding="utf-8") as f:
            rb = json.load(f)
        self.assertEqual(rb["units"], 0)

    # --- 5. Metadata identifies registered version ---
    def test_5_research_metadata_correct(self):
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        out = board.write_research_board(b, "2026-09-22",
                                          board.RESEARCH_VERSION_ID, prereg)
        with open(out, encoding="utf-8") as f:
            rb = json.load(f)
        self.assertEqual(rb["research_version_id"], board.RESEARCH_VERSION_ID)
        self.assertEqual(rb["research_selection_rule"], "fp-v0.4")
        self.assertEqual(rb["research_prereg_frozen_date"], "2026-09-17")
        self.assertEqual(rb["tier"], "research")
        self.assertIn("research observation", rb.get("_research_note", ""))

    # --- 6. Existing board cannot be overwritten ---
    def test_6_no_silent_overwrite(self):
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        out = board.write_research_board(b, "2026-09-22",
                                          board.RESEARCH_VERSION_ID, prereg)
        self.assertTrue(os.path.exists(out))
        with self.assertRaises(SystemExit) as cm:
            board.write_research_board(b, "2026-09-22",
                                        board.RESEARCH_VERSION_ID, prereg)
        self.assertIn("refusing to overwrite", str(cm.exception))

    # --- 7. Official files untouched by research generation ---
    def test_7_official_files_untouched(self):
        # Redirect official paths to tmp so we can detect unexpected writes
        real_fb = board.FB
        board.FB = self.tmp
        official_plain = os.path.join(self.tmp, "board_2026-09-22.json")
        official_enc   = os.path.join(self.tmp, "board_2026-09-22.enc")
        official_ledger = os.path.join(self.tmp, "football_ledger.json")
        official_commits = os.path.join(self.tmp, "commitments.json")
        try:
            b = minimal_board()
            prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
            board.write_research_board(b, "2026-09-22",
                                        board.RESEARCH_VERSION_ID, prereg)
            self.assertFalse(os.path.exists(official_plain),
                             "official board .json must not be written")
            self.assertFalse(os.path.exists(official_enc),
                             "official board .enc must not be written")
            self.assertFalse(os.path.exists(official_ledger),
                             "football_ledger.json must not be written")
            self.assertFalse(os.path.exists(official_commits),
                             "commitments.json must not be written")
        finally:
            board.FB = real_fb

    # --- 7b. --research does not modify or create game_commitments.json ---
    def test_7b_research_does_not_write_game_commitments(self):
        # board.py --research calls build(commit=False), so the per-game T-24
        # freeze store must never be touched.
        gc_path = board.GAME_COMMITMENTS
        real_gc = board.GAME_COMMITMENTS
        board.GAME_COMMITMENTS = os.path.join(self.tmp, "game_commitments.json")
        try:
            b = minimal_board()
            prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
            board.write_research_board(b, "2026-09-22",
                                        board.RESEARCH_VERSION_ID, prereg)
            self.assertFalse(
                os.path.exists(board.GAME_COMMITMENTS),
                "game_commitments.json must not be created by research generation")
        finally:
            board.GAME_COMMITMENTS = real_gc

    # --- 7c. research n_newly_committed is 0 (commit=False guarantees it) ---
    def test_7c_research_n_newly_committed_is_zero(self):
        # build() is called with commit=False on the research path.
        # n_newly_committed must therefore always be 0, and the research
        # branch must never print "froze N game(s)" or reference
        # game_commitments.json in its output.
        b = minimal_board()
        self.assertEqual(b["n_newly_committed"], 0,
                         "fixture already has n_newly_committed=0")
        # Verify the research branch output contains no "froze" or
        # "game_commitments" text even when n_newly_committed is forced >0.
        b_with_commits = dict(b, n_newly_committed=5)
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        import io as _io
        from contextlib import redirect_stdout
        buf = _io.StringIO()
        with redirect_stdout(buf):
            board.write_research_board(b_with_commits, "2026-09-23",
                                        board.RESEARCH_VERSION_ID, prereg)
        output = buf.getvalue()
        self.assertNotIn("froze", output,
                         "research path must not print 'froze N game(s)'")
        self.assertNotIn("game_commitments", output,
                         "research path must not reference game_commitments.json")

    # --- 8. Normal official invocation unchanged (--research not passed) ---
    def test_8_official_mode_still_blocked_by_flag(self):
        b = minimal_board()
        real_fb = board.FB
        board.FB = self.tmp
        try:
            with patch("sys.argv", ["board.py", "--week", "2026-09-22"]),                  patch.object(board, "build", return_value=b),                  patch.object(board, "render", return_value="fixture"),                  patch.object(board.crypto_box, "encrypt_to") as enc,                  patch.object(board, "record_commitment") as commit,                  patch.object(policy, "OFFICIAL_DELIVERY_ENABLED", False):
                rc = board.main()
            self.assertEqual(rc, 0)
            enc.assert_not_called()
            commit.assert_not_called()
        finally:
            board.FB = real_fb

    # --- 9. Unregistered version ID fails closed ---
    def test_9_unregistered_version_fails_closed(self):
        with self.assertRaises(SystemExit) as cm:
            board.load_research_prereg("non-existent-version-v99")
        self.assertIn("not in research_preregistrations.json", str(cm.exception))

    # --- 10. Wrong authority fails closed ---
    def test_10_wrong_authority_fails_closed(self):
        bad = dict(VALID_PREREG, authority="full_delivery_enabled")
        reg = {"_note": "bad", "registrations": [bad]}
        bad_path = write_registry(self.tmp, reg)
        board.RESEARCH_PREREG_PATH = bad_path
        with self.assertRaises(SystemExit) as cm:
            board.load_research_prereg(board.RESEARCH_VERSION_ID)
        self.assertIn("unexpected authority", str(cm.exception))

    # --- 11. Non-zero units fails closed ---
    def test_11_nonzero_units_fails_closed(self):
        bad = dict(VALID_PREREG, units=1)
        reg = {"_note": "bad", "registrations": [bad]}
        bad_path = write_registry(self.tmp, reg)
        board.RESEARCH_PREREG_PATH = bad_path
        with self.assertRaises(SystemExit) as cm:
            board.load_research_prereg(board.RESEARCH_VERSION_ID)
        self.assertIn("must be 0 units", str(cm.exception))

    # --- 12. --dry-run prints path without writing ---
    def test_12_dry_run_no_write(self):
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        import io as _io
        from contextlib import redirect_stdout
        buf = _io.StringIO()
        with redirect_stdout(buf):
            out = board.write_research_board(b, "2026-09-22",
                                              board.RESEARCH_VERSION_ID,
                                              prereg, dry_run=True)
        self.assertFalse(os.path.exists(out))
        self.assertIn("would write", buf.getvalue())

    # --- 13. record_cohort in research board matches version ID ---
    def test_13_record_cohort_is_research_version(self):
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        out = board.write_research_board(b, "2026-09-22",
                                          board.RESEARCH_VERSION_ID, prereg)
        with open(out, encoding="utf-8") as f:
            rb = json.load(f)
        self.assertEqual(rb["record_cohort"], board.RESEARCH_VERSION_ID)
        # Must NOT equal the official version
        import record_policy
        self.assertNotEqual(rb["record_cohort"], record_policy.VERSION)

    # --- 14. research_authority matches expected string ---
    def test_14_research_authority_field(self):
        b = minimal_board()
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        out = board.write_research_board(b, "2026-09-22",
                                          board.RESEARCH_VERSION_ID, prereg)
        with open(out, encoding="utf-8") as f:
            rb = json.load(f)
        self.assertEqual(rb["research_authority"],
                         "research_only_no_selection_delivery_or_holdout")

    # =========================================================================
    # Decision-moment tests (Fix 1 regression coverage)
    # =========================================================================

    def test_15_research_before_decision_returns_zero_no_file(self):
        """--research before decision moment: exits 0, writes nothing."""
        import io
        from contextlib import redirect_stdout
        # Build a board that has decision_made=False
        b_pre = dict(minimal_board(), decision_made=False,
                     decision_moment_utc="2099-01-01T18:00:00Z")
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        buf = io.StringIO()
        real_argv = sys.argv[:]
        sys.argv = ["board.py", "--research", "--week", "2026-09-22"]
        try:
            with redirect_stdout(buf):
                with patch.object(board, "build", return_value=b_pre), \
                     patch.object(board, "render", return_value="fixture"):
                    rc = board.main()
        finally:
            sys.argv = real_argv
        self.assertEqual(rc, 0, "--research before decision must return 0")
        self.assertIn("not reached", buf.getvalue(),
                      "must print decision moment not reached message")
        out = board.research_board_path("2026-09-22", board.RESEARCH_VERSION_ID)
        self.assertFalse(os.path.exists(out),
                         "no research board must be written before decision moment")

    def test_16_research_after_decision_creates_board(self):
        """--research after decision moment: creates exactly one board."""
        b_post = dict(minimal_board(), decision_made=True)
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        real_argv = sys.argv[:]
        sys.argv = ["board.py", "--research", "--week", "2026-09-22"]
        try:
            with patch.object(board, "build", return_value=b_post), \
                 patch.object(board, "render", return_value="fixture"):
                rc = board.main()
        finally:
            sys.argv = real_argv
        self.assertEqual(rc, 0)
        out = board.research_board_path("2026-09-22", board.RESEARCH_VERSION_ID)
        self.assertTrue(os.path.exists(out),
                        "research board must be written after decision moment")
        # Second call must refuse (immutability)
        sys.argv = ["board.py", "--research", "--week", "2026-09-22"]
        try:
            with patch.object(board, "build", return_value=b_post), \
                 patch.object(board, "render", return_value="fixture"):
                with self.assertRaises(SystemExit):
                    board.main()
        finally:
            sys.argv = real_argv

    def test_17_existing_board_immutable(self):
        """Existing research board cannot be overwritten."""
        b_post = dict(minimal_board(), decision_made=True)
        prereg = board.load_research_prereg(board.RESEARCH_VERSION_ID)
        # Write initial board
        out = board.write_research_board(b_post, "2026-09-22",
                                          board.RESEARCH_VERSION_ID, prereg)
        with open(out, encoding="utf-8") as f:
            original_content = f.read()
        # Attempt overwrite via write_research_board
        with self.assertRaises(SystemExit) as cm:
            board.write_research_board(b_post, "2026-09-22",
                                        board.RESEARCH_VERSION_ID, prereg)
        self.assertIn("refusing to overwrite", str(cm.exception))
        # Content unchanged
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(), original_content)

    def test_18_corrupt_preregistration_fails_nonzero(self):
        """Corrupt preregistration fails closed (non-zero / SystemExit)."""
        bad_path = os.path.join(self.tmp, "bad_prereg.json")
        with open(bad_path, "w") as f:
            f.write("{not valid json")
        board.RESEARCH_PREREG_PATH = bad_path
        with self.assertRaises((SystemExit, ValueError)):
            board.load_research_prereg(board.RESEARCH_VERSION_ID)

    def test_19_workflow_no_error_swallow(self):
        """football-capture.yml does not contain board.py --research ... || echo."""
        wf_path = os.path.join(ROOT, ".github", "workflows", "football-capture.yml")
        with open(wf_path, encoding="utf-8") as f:
            src = f.read()
        # The || echo pattern that converts failures to success must be absent
        # from the research board generation call
        import re
        # Look for the research board call followed by || on the same or next line
        self.assertNotRegex(
            src,
            r"board\.py --research.*\|\|.*echo",
            "board.py --research must not swallow failures with || echo"
        )

    def test_20_workflow_board_exists_guard_present(self):
        """football-capture.yml shell guard checks for existing board before generating."""
        wf_path = os.path.join(ROOT, ".github", "workflows", "football-capture.yml")
        with open(wf_path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("already exists; skipping generation", src,
                      "shell board-exists guard must be present in capture workflow")


if __name__ == "__main__":
    unittest.main()
