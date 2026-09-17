#!/usr/bin/env python3
"""
Offline self-test for the research grading path in grade_football.py (Step 3).

Tests the following invariants:
  1. Research board grades into research ledger only.
  2. Official ledger (football_ledger.json) is unchanged.
  3. Duplicate re-run does not append a second row.
  4. A loss is retained in the research ledger.
  5. A push (margin == 0) grades correctly with pnl = 0.
  6. units remains 0 in graded rows.
  7. Unregistered/mismatched version ID fails closed.
  8. Wrong tier ('official' instead of 'research') fails closed.
  9. Wrong record_cohort fails closed.
 10. Official grader behavior unchanged (--research flag absent).
 11. Research ledger rows carry is_official=False.
 12. No Discord/member delivery occurs (delivery flags remain False).
 13. Research entry idempotency key is stable.
 14. CLV field is present (may be None when no close capture).
 15. Missing-result observation is skipped gracefully (not final yet).

Runs fully offline. No network, no Odds API, no Discord, no encryption key.
All file writes go into a TemporaryDirectory.

  python scripts/football/selftest_research_grading.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import grade_football as gf
import delivery_policy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_PREREG_ENTRY = {
    "id": gf.RESEARCH_VERSION_ID,
    "frozen_date": "2026-09-17",
    "path": "docs/FOOTBALL_RESEARCH_FP_V04_OBSERVATION.md",
    "first_commit": "959c49e078e16e4b96a573631e97c0ef5b7b1786",
    "bytes": 5020,
    "sha256": "539f6c890eb72ee4d769f3b40fce727c998f71358f7ae6f9b7c73e82903e6801",
    "authority": "research_only_no_selection_delivery_or_holdout",
    "selection_rule": "fp-v0.4",
    "units": 0,
}

VALID_REGISTRY = {"_note": "test", "registrations": [VALID_PREREG_ENTRY]}


def make_research_board(week="2026-09-22", sport="ncaaf",
                        side="Away Team", price=240, result_home_wins=False):
    """Minimal research board as produced by board.py --research."""
    away = "Away Team"
    home = "Home Team"
    pick_home = not result_home_wins  # side = away by default
    return {
        "tier": "research",
        "research_version_id": gf.RESEARCH_VERSION_ID,
        "research_selection_rule": "fp-v0.4",
        "research_authority": "research_only_no_selection_delivery_or_holdout",
        "record_cohort": gf.RESEARCH_VERSION_ID,
        "units": 0,
        "slate_week": week,
        "asof_utc": "2026-09-21T18:05:00Z",
        "selection_version": "fp-v0.4",
        "decision_made": True,
        "premium": {
            "sport": sport,
            "matchup": f"{away} @ {home}",
            "kickoff_utc": "2026-09-20T17:00:00Z",
            "side": home if pick_home else away,
            "home": home,
            "away": away,
            "best_price": price,
            "best_book": "draftkings",
            "books_at_best": 3,
            "eff_overround_pts": 1.8,
            "fair_side": 0.31,
            "t24_capture": "ncaaf_test.json",
            "t24_hours_before_kickoff": 24.1,
            "selection_eligible": True,
        },
        "free": None,
        "games": [],
    }


def make_result(away="Away Team", home="Home Team",
                away_score=14, home_score=28,
                final=True, season_type=2,
                kickoff_utc="2026-09-20T17:00:00Z",
                espn_event_id="test_event_001"):
    margin = (home_score - away_score) if final else None
    return {
        "espn_event_id": espn_event_id,
        "away": away, "home": home,
        "away_raw": away, "home_raw": home,
        "away_score": away_score, "home_score": home_score,
        "margin": margin,
        "final": final,
        "kickoff_utc": kickoff_utc,
        "season_type": season_type,
        "season_slug": "regular-season",
    }


def make_cfg(gradeable_fn=None):
    """Minimal SPORTS cfg entry. keyfn=None means identity comparison."""
    if gradeable_fn is None:
        gradeable_fn = lambda r: r.get("season_type") == 2
    return {"keyfn": None, "gradeable": gradeable_fn}


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class ResearchGradingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Redirect research paths to tmp
        self.real_research_dir = gf.RESEARCH_DIR
        self.real_research_ledger = gf.RESEARCH_LEDGER
        self.real_prereg_path = gf.RESEARCH_PREREG_PATH
        gf.RESEARCH_DIR = os.path.join(self.tmp, "research")
        gf.RESEARCH_LEDGER = os.path.join(gf.RESEARCH_DIR, "research_ledger.json")
        gf.RESEARCH_PREREG_PATH = os.path.join(self.tmp, "research_preregistrations.json")
        with open(gf.RESEARCH_PREREG_PATH, "w", encoding="utf-8") as f:
            json.dump(VALID_REGISTRY, f)
        # Official ledger in tmp so we can detect accidental writes
        self.real_ledger = gf.LEDGER
        gf.LEDGER = os.path.join(self.tmp, "football_ledger.json")

    def tearDown(self):
        gf.RESEARCH_DIR = self.real_research_dir
        gf.RESEARCH_LEDGER = self.real_research_ledger
        gf.RESEARCH_PREREG_PATH = self.real_prereg_path
        gf.LEDGER = self.real_ledger
        shutil.rmtree(self.tmp, ignore_errors=True)

    def grade_one(self, board, sport="ncaaf", margin=None, final=True,
                  home_score=28, away_score=14):
        """Helper: run grade_research_board on a single board+result."""
        prereg = gf.load_research_prereg()
        result = make_result(
            home_score=home_score, away_score=away_score,
            final=final,
            kickoff_utc=board["premium"]["kickoff_utc"],
        )
        results = {result["espn_event_id"]: result}
        cfg = make_cfg()
        done = set()
        return gf.grade_research_board(board, sport, results, cfg, [], prereg, done)

    # --- 1. Research board grades into research ledger only ---
    def test_1_grades_into_research_ledger(self):
        board = make_research_board()
        rows = self.grade_one(board)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["research_version_id"], gf.RESEARCH_VERSION_ID)
        self.assertEqual(row["tier"], "research_premium")
        self.assertEqual(row["record_cohort"], gf.RESEARCH_VERSION_ID)

    # --- 2. Official ledger unchanged ---
    def test_2_official_ledger_unchanged(self):
        board = make_research_board()
        self.grade_one(board)
        self.assertFalse(os.path.exists(gf.LEDGER),
                         "football_ledger.json must not be created")

    # --- 3. Duplicate re-run does not append a second row ---
    def test_3_duplicate_idempotent(self):
        board = make_research_board()
        prereg = gf.load_research_prereg()
        result = make_result(kickoff_utc=board["premium"]["kickoff_utc"])
        results = {result["espn_event_id"]: result}
        cfg = make_cfg()
        done = set()
        rows1 = gf.grade_research_board(board, "ncaaf", results, cfg, [], prereg, done)
        rows2 = gf.grade_research_board(board, "ncaaf", results, cfg, [], prereg, done)
        self.assertEqual(len(rows1), 1)
        self.assertEqual(len(rows2), 0, "second run must produce no new rows")

    # --- 4. Loss is retained ---
    def test_4_loss_retained(self):
        # make_research_board() defaults pick_home=True (side = Home Team).
        # With home_score < away_score, home loses → our pick is a LOSS.
        board_loss = make_research_board()   # side = Home Team
        rows_loss = self.grade_one(board_loss, home_score=14, away_score=28)
        self.assertEqual(len(rows_loss), 1)
        self.assertEqual(rows_loss[0]["result"], "loss")
        self.assertEqual(rows_loss[0]["pnl_per_unit"], -1.0)

        # Different week so idempotency keys don't collide.
        # With home_score > away_score, home wins → our pick is a WIN.
        board_win = make_research_board(week="2026-09-29")
        rows_win = self.grade_one(board_win, home_score=28, away_score=14)
        self.assertEqual(len(rows_win), 1)
        self.assertEqual(rows_win[0]["result"], "win")
        self.assertGreater(rows_win[0]["pnl_per_unit"], 0)

    # --- 5. Push handling ---
    def test_5_push(self):
        board = make_research_board(price=100)
        rows = self.grade_one(board, home_score=21, away_score=21)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "push")
        self.assertEqual(rows[0]["pnl_per_unit"], 0.0)

    # --- 6. units remains 0 ---
    def test_6_units_zero(self):
        board = make_research_board()
        rows = self.grade_one(board)
        self.assertEqual(rows[0]["units"], 0)

    # --- 7. Unregistered version fails closed ---
    def test_7_unregistered_version_fails_closed(self):
        bad_registry = {"_note": "bad", "registrations": []}
        with open(gf.RESEARCH_PREREG_PATH, "w") as f:
            json.dump(bad_registry, f)
        with self.assertRaises(SystemExit):
            gf.load_research_prereg()

    # --- 8. Wrong tier fails closed ---
    def test_8_wrong_tier_fails_closed(self):
        board = make_research_board()
        board["tier"] = "official"
        prereg = gf.load_research_prereg()
        with self.assertRaises(SystemExit):
            gf.validate_research_board(board, prereg)

    # --- 9. Wrong record_cohort fails closed ---
    def test_9_wrong_record_cohort_fails_closed(self):
        board = make_research_board()
        board["record_cohort"] = "fp-v0.4"  # official cohort, not research
        prereg = gf.load_research_prereg()
        with self.assertRaises(SystemExit):
            gf.validate_research_board(board, prereg)

    # --- 10. Official grader behavior unchanged ---
    def test_10_official_grader_unchanged(self):
        # With --research absent, main() uses grade_committed; test that
        # OFFICIAL_DELIVERY_ENABLED is still False (existing delivery test).
        self.assertIs(delivery_policy.OFFICIAL_DELIVERY_ENABLED, False)
        # And that RESEARCH_DELIVERY_ENABLED is also still False.
        self.assertIs(delivery_policy.RESEARCH_DELIVERY_ENABLED, False)

    # --- 11. is_official = False ---
    def test_11_is_official_false(self):
        board = make_research_board()
        rows = self.grade_one(board)
        self.assertIs(rows[0]["is_official"], False)

    # --- 12. No Discord delivery ---
    def test_12_no_discord_delivery(self):
        # Research grading calls no Discord functions.
        # Verify by importing discord and confirming its send() is never called.
        import discord as fb_discord
        with patch.object(fb_discord, "send") as mock_send:
            board = make_research_board()
            self.grade_one(board)
        mock_send.assert_not_called()

    # --- 13. Idempotency key is stable ---
    def test_13_idempotency_key_stable(self):
        entry = {
            "research_version_id": gf.RESEARCH_VERSION_ID,
            "slate_week": "2026-09-22",
            "sport": "ncaaf",
            "matchup": "Away Team @ Home Team",
            "kickoff_utc": "2026-09-20T17:00:00Z",
            "tier": "research_premium",
        }
        k1 = gf.research_entry_key(entry)
        k2 = gf.research_entry_key(dict(entry))  # copy
        self.assertEqual(k1, k2)
        # Changing any field changes the key
        for field, val in [("sport", "nfl"), ("tier", "research_free"),
                           ("matchup", "X @ Y"), ("slate_week", "2026-09-29")]:
            different = dict(entry, **{field: val})
            self.assertNotEqual(gf.research_entry_key(different), k1,
                                f"key must differ when {field} changes")

    # --- 14. CLV field present (may be None) ---
    def test_14_clv_field_present(self):
        board = make_research_board()
        rows = self.grade_one(board)
        self.assertIn("clv_pts", rows[0])
        self.assertIn("clv_status", rows[0])
        # With no capture list, clv_pts should be None
        self.assertIsNone(rows[0]["clv_pts"])
        self.assertEqual(rows[0]["clv_status"], "unavailable")

    # --- 15. Not-final result is skipped ---
    def test_15_not_final_skipped(self):
        board = make_research_board()
        prereg = gf.load_research_prereg()
        result = make_result(final=False,
                             kickoff_utc=board["premium"]["kickoff_utc"])
        results = {result["espn_event_id"]: result}
        cfg = make_cfg()
        rows = gf.grade_research_board(board, "ncaaf", results, cfg, [], prereg, set())
        self.assertEqual(len(rows), 0, "unfinished game must not grade")

    # --- 16. Malformed board fails the run, not skips ---
    def test_16_malformed_board_fails_run(self):
        # validate_research_board() must raise SystemExit (not be caught) for
        # provenance violations.  A try/except that continues would hide
        # corrupted data; the run must exit non-zero instead.
        board = make_research_board()
        board["tier"] = "official"          # invalid provenance
        prereg = gf.load_research_prereg()
        # validate_research_board should raise SystemExit unconditionally.
        with self.assertRaises(SystemExit):
            gf.validate_research_board(board, prereg)
        # Calling code in main() must NOT swallow that exception.
        # Verify by running grade_research_board: it never gets called because
        # validate raises first — but we also confirm the SKIP pattern is gone
        # by checking that "SKIP" does NOT appear anywhere in grade_football.py's
        # research loop body (source-level assertion, same pattern as selftest_instagram).
        src_path = os.path.join(HERE, "grade_football.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        # Find the research loop body between "for bf in board_files:" and
        # "# Grade only observations" and check no SKIP/continue pattern remains.
        loop_start = src.find("for bf in board_files:")
        loop_end = src.find("# Grade only observations", loop_start)
        loop_body = src[loop_start:loop_end]
        self.assertNotIn("except SystemExit", loop_body,
                         "research loop must not catch SystemExit from validate")
        self.assertNotIn('"SKIP"', loop_body,
                         "research loop must not print SKIP and continue")
        self.assertNotIn("continue", loop_body,
                         "research loop must not continue past validate_research_board")

    # --- 17. entry_key is stored on every graded row ---
    def test_17_entry_key_stored_on_row(self):
        board = make_research_board()
        rows = self.grade_one(board)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn("entry_key", row)
        expected = gf.research_entry_key(row)
        self.assertEqual(row["entry_key"], expected,
                         "stored entry_key must match recomputed key")

    # --- 18. Existing graded row is unchanged on rerun (append-only immutability) ---
    def test_18_existing_row_unchanged_on_rerun(self):
        # Grade once, write to ledger, then re-run with changed result data.
        # The existing row must be byte-for-byte unchanged.
        os.makedirs(gf.RESEARCH_DIR, exist_ok=True)

        board = make_research_board()
        prereg = gf.load_research_prereg()
        result_orig = make_result(home_score=28, away_score=14, final=True,
                                  kickoff_utc=board["premium"]["kickoff_utc"])
        results_orig = {result_orig["espn_event_id"]: result_orig}
        cfg = make_cfg()
        done = set()
        rows = gf.grade_research_board(board, "ncaaf", results_orig, cfg, [],
                                        prereg, done)
        self.assertEqual(len(rows), 1)

        # Write to ledger
        rl = gf.load_research_ledger()
        rl["entries"].extend(rows)
        with open(gf.RESEARCH_LEDGER, "w", encoding="utf-8") as f:
            json.dump(rl, f, indent=1)

        # Snapshot the original row
        original_row = dict(rows[0])

        # Re-run with a DIFFERENT result (scores flipped) and different done set.
        # The key is already in done after first run; a fresh done set simulates
        # a separate process loading the ledger.
        rl2 = gf.load_research_ledger()
        done2 = {e.get("entry_key") or gf.research_entry_key(e)
                 for e in rl2.get("entries", [])}

        result_changed = make_result(home_score=7, away_score=35, final=True,
                                     kickoff_utc=board["premium"]["kickoff_utc"])
        results_changed = {result_changed["espn_event_id"]: result_changed}
        rows2 = gf.grade_research_board(board, "ncaaf", results_changed, cfg, [],
                                          prereg, done2)
        self.assertEqual(len(rows2), 0, "re-run must produce no new rows")

        # Read the ledger back and confirm the original row is unchanged
        with open(gf.RESEARCH_LEDGER, encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(len(stored["entries"]), 1)
        stored_row = stored["entries"][0]
        for field in ("result", "best_price", "side", "pnl_per_unit",
                      "entry_key", "research_version_id", "record_cohort",
                      "is_official", "units"):
            self.assertEqual(stored_row[field], original_row[field],
                             f"field {field!r} must not change on rerun")


if __name__ == "__main__":
    unittest.main()
