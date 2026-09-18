#!/usr/bin/env python3
"""
Offline self-test for research_page.py (Step 4).

Tests:
  1. page renders with no research directory
  2. page renders with no research ledger
  3. page renders one ungraded research board
  4. page renders one graded win
  5. page renders one graded loss
  6. research rows never appear in official W-L (page.py render_hub unchanged)
  7. research labels present (disclaimer, 0 units, not a recommendation)
  8. official language absent (premium pick, recommended bet)
  9. preregistration version / link render correctly
 10. research page link present on the official football hub
 11. hypothetical P/L is labeled as hypothetical
 12. empty-state message shown when no boards exist

Runs fully offline. No network, no Odds API, no encryption key.
All file writes go into a TemporaryDirectory.

  python scripts/football/selftest_research_page.py
"""
import glob
import html
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import research_page as rp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_PREREG_ENTRY = {
    "id": rp.RESEARCH_VERSION_ID,
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
                        side="Home Team", price=240):
    away, home = "Away Team", "Home Team"
    return {
        "tier": "research",
        "research_version_id": rp.RESEARCH_VERSION_ID,
        "research_selection_rule": "fp-v0.4",
        "research_authority": "research_only_no_selection_delivery_or_holdout",
        "record_cohort": rp.RESEARCH_VERSION_ID,
        "units": 0,
        "slate_week": week,
        "asof_utc": "2026-09-21T18:05:00Z",
        "selection_version": "fp-v0.4",
        "decision_made": True,
        "premium": {
            "sport": sport,
            "matchup": f"{away} @ {home}",
            "kickoff_utc": "2026-09-20T17:00:00Z",
            "side": side,
            "home": home, "away": away,
            "best_price": price, "best_book": "draftkings",
            "books_at_best": 3, "eff_overround_pts": 1.8,
            "fair_side": 0.31, "selection_eligible": True,
        },
        "free": None, "games": [],
    }


def make_ledger_entry(result="win", week="2026-09-22"):
    pnl = 2.4 if result == "win" else (-1.0 if result == "loss" else 0.0)
    entry = {
        "research_version_id": rp.RESEARCH_VERSION_ID,
        "research_authority": "research_only_no_selection_delivery_or_holdout",
        "tier": "research_premium",
        "record_cohort": rp.RESEARCH_VERSION_ID,
        "is_official": False,
        "sport": "ncaaf",
        "slate_week": week,
        "matchup": "Away Team @ Home Team",
        "kickoff_utc": "2026-09-20T17:00:00Z",
        "side": "Home Team",
        "best_price": 240,
        "best_book": "draftkings",
        "books_at_best": 3,
        "eff_overround_pts": 1.8,
        "fair_side": 0.31,
        "observed_utc": "2026-09-21T18:05:00Z",
        "espn_event_id": "test_001",
        "final": "28-14",
        "result": result,
        "units": 0,
        "pnl_per_unit": pnl,
        "_pnl_note": ("Hypothetical research analysis only. "
                      "pnl_per_unit shows what one unit would have returned. "
                      "No stake was placed or recorded."),
        "clv_pts": None, "close_capture": None,
        "clv_status": "unavailable",
        "graded_utc": "2026-09-21T08:00:00Z",
        "entry_key": (f"{rp.RESEARCH_VERSION_ID}|{week}|ncaaf|"
                      "Away Team @ Home Team|2026-09-20T17:00:00Z|research_premium"),
    }
    return entry


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class ResearchPageTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Redirect all data paths to tmp
        self.real_data = rp.RESEARCH_DATA_DIR
        self.real_ledger = rp.RESEARCH_LEDGER
        self.real_prereg = rp.PREREG_PATH
        self.real_out = rp.OUT
        rp.RESEARCH_DATA_DIR = os.path.join(self.tmp, "research")
        rp.RESEARCH_LEDGER = os.path.join(rp.RESEARCH_DATA_DIR, "research_ledger.json")
        rp.PREREG_PATH = os.path.join(self.tmp, "research_preregistrations.json")
        rp.OUT = os.path.join(self.tmp, "football", "research")
        # Write registry
        with open(rp.PREREG_PATH, "w", encoding="utf-8") as f:
            json.dump(VALID_REGISTRY, f)

    def tearDown(self):
        rp.RESEARCH_DATA_DIR = self.real_data
        rp.RESEARCH_LEDGER = self.real_ledger
        rp.PREREG_PATH = self.real_prereg
        rp.OUT = self.real_out
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get_html(self):
        rp.render()
        out = os.path.join(rp.OUT, "index.html")
        with open(out, encoding="utf-8") as f:
            return f.read()

    # --- 1. No research directory ---
    def test_1_renders_no_research_dir(self):
        # data/football/research/ does not exist — valid empty state
        h = self.get_html()
        self.assertIn("No live research observations have been published yet.", h)

    # --- 2. No research ledger ---
    def test_2_renders_no_ledger(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        # Directory exists but no ledger and no boards — valid empty state
        h = self.get_html()
        self.assertIn("No live research observations have been published yet.", h)
        self.assertIn("No graded results yet.", h)

    # --- 2b. Malformed existing research board fails loudly ---
    def test_2b_malformed_board_fails_loud(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        bad_path = os.path.join(rp.RESEARCH_DATA_DIR,
                                f"board_2026-09-22_{rp.RESEARCH_VERSION_ID}.json")
        with open(bad_path, "w") as f:
            f.write("{not valid json")
        with self.assertRaises(ValueError,
                               msg="malformed board JSON must fail generation, not produce empty state"):
            rp.load_research_boards()

    # --- 2c. Malformed existing research ledger fails loudly ---
    def test_2c_malformed_ledger_fails_loud(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        with open(rp.RESEARCH_LEDGER, "w") as f:
            f.write("{not valid json")
        with self.assertRaises(ValueError,
                               msg="malformed ledger JSON must fail generation, not produce empty state"):
            rp.load_research_ledger()

    # --- 2d. Generated football/research/index.html exists ---
    def test_2d_generated_file_exists(self):
        rp.render()
        out = os.path.join(rp.OUT, "index.html")
        self.assertTrue(os.path.exists(out),
                        "football/research/index.html must exist after render()")
        with open(out, encoding="utf-8") as f:
            h = f.read()
        self.assertGreater(len(h), 1000, "generated file must not be empty")

    # --- 2e. Official football hub links to /football/research/ ---
    def test_2e_official_hub_links_to_research(self):
        src_path = os.path.join(HERE, "page.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("/football/research/", src)
        self.assertIn("Live research observations", src)

    # --- 3. One ungraded board ---
    def test_3_renders_ungraded_board(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        board = make_research_board()
        bf = os.path.join(rp.RESEARCH_DATA_DIR,
                          f"board_2026-09-22_{rp.RESEARCH_VERSION_ID}.json")
        with open(bf, "w") as f:
            json.dump(board, f)
        h = self.get_html()
        # nice_date renders as "Tuesday, September 22, 2026"; check for the month
        self.assertIn("September 22", h)
        self.assertIn("Away Team @ Home Team", h)
        self.assertIn("Not yet graded", h)

    # --- 4. Graded win ---
    def test_4_renders_graded_win(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        entry = make_ledger_entry(result="win")
        rl = {"research_version_id": rp.RESEARCH_VERSION_ID, "entries": [entry]}
        with open(rp.RESEARCH_LEDGER, "w") as f:
            json.dump(rl, f)
        h = self.get_html()
        self.assertIn("win", h)
        self.assertIn("✅", h)
        self.assertIn("+2.4000u", h)

    # --- 5. Graded loss ---
    def test_5_renders_graded_loss(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        entry = make_ledger_entry(result="loss")
        rl = {"research_version_id": rp.RESEARCH_VERSION_ID, "entries": [entry]}
        with open(rp.RESEARCH_LEDGER, "w") as f:
            json.dump(rl, f)
        h = self.get_html()
        self.assertIn("loss", h)
        self.assertIn("❌", h)
        self.assertIn("-1.0000u", h)

    # --- 6. Research rows never appear in official W-L ---
    def test_6_research_not_in_official_record(self):
        # Verify that research entries are classified is_official=False
        # and record_cohort != record_policy.VERSION
        import record_policy
        entry = make_ledger_entry(result="win")
        self.assertIs(entry["is_official"], False)
        self.assertNotEqual(entry["record_cohort"], record_policy.VERSION)
        # partition() from record_policy must not classify as official
        official, pilot, research = record_policy.partition([entry])
        self.assertEqual(official, [])

    # --- 7. Research disclaimer and labels present ---
    def test_7_research_labels_present(self):
        h = self.get_html()
        self.assertIn("Research Observation", h)
        self.assertIn("0 units", h)
        self.assertIn("Not a recommendation", h)
        self.assertIn("not part of the official football record", h)
        self.assertIn("not eligible for retroactive promotion", h)
        self.assertIn(rp.RESEARCH_VERSION_ID, h)

    # --- 8. Official language absent ---
    def test_8_no_official_language(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        board = make_research_board()
        bf = os.path.join(rp.RESEARCH_DATA_DIR,
                          f"board_2026-09-22_{rp.RESEARCH_VERSION_ID}.json")
        with open(bf, "w") as f:
            json.dump(board, f)
        entry = make_ledger_entry()
        rl = {"research_version_id": rp.RESEARCH_VERSION_ID, "entries": [entry]}
        with open(rp.RESEARCH_LEDGER, "w") as f:
            json.dump(rl, f)
        h = self.get_html()
        h_lower = h.lower()
        for forbidden in ("premium pick", "recommended bet",
                          "recommended play", "proven edge"):
            self.assertNotIn(forbidden, h_lower,
                             f"forbidden phrase {forbidden!r} found in research page")
        # "official pick" appears in "not official picks" (negation) — check the
        # affirmative form only.
        self.assertNotIn("is an official pick", h_lower)
        self.assertNotIn("are official picks", h_lower)

    # --- 9. Preregistration renders correctly ---
    def test_9_prereg_renders(self):
        h = self.get_html()
        self.assertIn(rp.RESEARCH_VERSION_ID, h)
        self.assertIn("2026-09-17", h)  # frozen_date
        self.assertIn("fp-v0.4", h)     # selection_rule
        self.assertIn("docs/FOOTBALL_RESEARCH_FP_V04_OBSERVATION.md", h)
        self.assertIn("959c49e", h)     # first_commit prefix

    # --- 10. Official hub contains research link ---
    def test_10_official_hub_has_research_link(self):
        src_path = os.path.join(HERE, "page.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("/football/research/", src,
                      "page.py must contain link to /football/research/")
        self.assertIn("Live research observations", src,
                      "page.py must contain 'Live research observations' link text")

    # --- 11. Hypothetical P/L labeled as hypothetical ---
    def test_11_pnl_labeled_hypothetical(self):
        os.makedirs(rp.RESEARCH_DATA_DIR, exist_ok=True)
        entry = make_ledger_entry(result="win")
        rl = {"research_version_id": rp.RESEARCH_VERSION_ID, "entries": [entry]}
        with open(rp.RESEARCH_LEDGER, "w") as f:
            json.dump(rl, f)
        h = self.get_html()
        self.assertIn("hypothetical", h.lower(),
                      "P/L must be labeled as hypothetical")
        self.assertIn("0 units staked", h,
                      "must state 0 units staked alongside the P/L")

    # --- 12. Empty state shown when no boards ---
    def test_12_empty_state_message(self):
        h = self.get_html()
        self.assertIn("No live research observations have been published yet.", h)
        # Must not contain Python tracebacks or error messages
        self.assertNotIn("Traceback", h)
        self.assertNotIn("Error", h)
        self.assertNotIn("Exception", h)


if __name__ == "__main__":
    unittest.main()
