#!/usr/bin/env python3
"""
Offline self-test for the research Discord delivery layer (Step 5).

18 tests covering:
  1  research send blocked when RESEARCH_DELIVERY_ENABLED=False
  2  --force cannot bypass research delivery disable
  3  disabled research send does not mutate status
  4  research send uses DISCORD_RESEARCH_WEBHOOK, not official/member webhooks
  5  successful send becomes idempotent
  6  rerun does not duplicate successful research post
  7  failed send remains retryable
  8  missing webhook remains retryable
  9  dry-run does not mutate status
  10 malformed research provenance fails closed
  11 non-zero units fails closed
  12 research post contains "Research Observation"
  13 research post contains "0 units"
  14 research post contains "Not a recommendation"
  15 research version visible in messages
  16 prohibited recommendation language absent
  17 internal premium/free labels not surfaced to user
  18 official Discord modes still use official gate unchanged

Runs fully offline — no network, no webhook calls.

  python scripts/football/selftest_research_discord.py
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

import discord as fbd
import delivery_policy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_PREREG_ENTRY = {
    "id": "fp-v0.4-market-observation-v1",
    "frozen_date": "2026-09-17",
    "authority": "research_only_no_selection_delivery_or_holdout",
    "selection_rule": "fp-v0.4",
    "units": 0,
}
VALID_REGISTRY = {"_note": "test", "registrations": [VALID_PREREG_ENTRY]}


def make_research_board(week="2026-09-22",
                        units=0,
                        tier="research",
                        version_id=None,
                        authority="research_only_no_selection_delivery_or_holdout",
                        selection_rule="fp-v0.4"):
    vid = version_id or fbd.RESEARCH_VERSION_ID
    return {
        "tier": tier,
        "research_version_id": vid,
        "research_selection_rule": selection_rule,
        "research_authority": authority,
        "record_cohort": vid,
        "units": units,
        "slate_week": week,
        "asof_utc": "2026-09-21T18:05:00Z",
        "decision_made": True,
        "n_covered": 5,
        "n_excluded": 1,
        "premium": {
            "sport": "ncaaf",
            "matchup": "Away Team @ Home Team",
            "kickoff_utc": "2026-09-20T17:00:00Z",
            "side": "Away Team",
            "best_price": 240,
            "best_book": "draftkings",
            "books_at_best": 3,
            "n_books": 8,
            "eff_overround_pts": 1.8,
            "fair_side": 0.31,
            "t24_capture": "ncaaf_test.json",
            "t24_hours_before_kickoff": 24.1,
        },
        "free": None,
        "no_market": [{"matchup": "X @ Y", "reason": "NO MARKET (2 books)"}],
    }


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class ResearchDiscordTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.real_status = fbd.STATUS_PATH
        self.real_research_dir = fbd.RESEARCH_DIR
        self.real_prereg_path = fbd.RESEARCH_PREREG_PATH
        fbd.STATUS_PATH = os.path.join(self.tmp, "post_status.json")
        fbd.RESEARCH_DIR = os.path.join(self.tmp, "research")
        fbd.RESEARCH_PREREG_PATH = os.path.join(self.tmp, "research_preregistrations.json")
        os.makedirs(fbd.RESEARCH_DIR, exist_ok=True)
        # Write a valid registry by default; tests that need a bad/absent one override.
        self.write_registry(VALID_REGISTRY)

    def tearDown(self):
        fbd.STATUS_PATH = self.real_status
        fbd.RESEARCH_DIR = self.real_research_dir
        fbd.RESEARCH_PREREG_PATH = self.real_prereg_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_registry(self, registry):
        with open(fbd.RESEARCH_PREREG_PATH, "w", encoding="utf-8") as f:
            json.dump(registry, f)

    def write_board(self, week, board):
        path = os.path.join(fbd.RESEARCH_DIR,
                            f"board_{week}_{fbd.RESEARCH_VERSION_ID}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(board, f)
        return path

    def run_main(self, *argv):
        import io as _io
        from contextlib import redirect_stdout
        buf = _io.StringIO()
        real_argv = sys.argv[:]
        sys.argv = list(argv)
        rc = -1
        try:
            with redirect_stdout(buf):
                rc = fbd.main()
        except SystemExit as e:
            rc = e.code
        finally:
            sys.argv = real_argv
        return rc, buf.getvalue()

    def get_status(self):
        if os.path.exists(fbd.STATUS_PATH):
            with open(fbd.STATUS_PATH, encoding="utf-8") as f:
                return json.load(f)
        return {"posts": []}

    # --- 1. Blocked when RESEARCH_DELIVERY_ENABLED=False ---
    def test_1_blocked_when_disabled(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", False):
            rc, out = self.run_main("discord.py", "research_board",
                                    "--week", "2026-09-22")
        self.assertEqual(rc, 0)
        self.assertIn("no research send", out)

    # --- 2. --force cannot bypass research delivery disable ---
    def test_2_force_cannot_bypass_disable(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", False):
            rc, out = self.run_main("discord.py", "research_board",
                                    "--week", "2026-09-22", "--force")
        self.assertIn("no research send", out)
        posts = self.get_status()["posts"]
        self.assertEqual(len(posts), 0, "--force must not bypass the gate or mutate status")

    # --- 3. Disabled send does not mutate status ---
    def test_3_disabled_does_not_mutate_status(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", False):
            self.run_main("discord.py", "research_board", "--week", "2026-09-22")
        posts = self.get_status()["posts"]
        self.assertEqual(len(posts), 0)

    # --- 4. Research send uses DISCORD_RESEARCH_WEBHOOK, not official webhooks ---
    def test_4_uses_research_webhook(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        # Patch RESEARCH_MODES directly so the webhook value the delivery
        # path reads is our fake URL (module-level RESEARCH_WEBHOOK was
        # captured at import time as empty string).
        fake_url = "https://discord.com/api/webhooks/research/fake"
        sent_to = []
        def fake_send(webhook, messages, dry=False):
            sent_to.append(webhook)
            return True, 200, "ok"
        patched_modes = {
            "research_board": (
                fbd.RESEARCH_STATUS_KEY,
                fake_url,
                "DISCORD_RESEARCH_WEBHOOK",
                fbd.research_board_messages,
            )
        }
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_MODES", patched_modes),              patch.object(fbd, "send", fake_send):
            self.run_main("discord.py", "research_board", "--week", "2026-09-22")
        self.assertEqual(len(sent_to), 1, "send() must be called exactly once")
        self.assertEqual(sent_to[0], fake_url,
                         "send() must use the research webhook URL")

    # --- 5. Successful send becomes idempotent ---
    def test_5_successful_send_idempotent(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        fake_url = "https://discord.com/api/webhooks/x/y"
        def fake_send(webhook, messages, dry=False):
            return True, 200, "ok"
        patched_modes = {"research_board": (
            fbd.RESEARCH_STATUS_KEY, fake_url, "DISCORD_RESEARCH_WEBHOOK",
            fbd.research_board_messages)}
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_MODES", patched_modes),              patch.object(fbd, "send", fake_send):
            self.run_main("discord.py", "research_board", "--week", "2026-09-22")
        idem_key = f"{fbd.RESEARCH_VERSION_ID}|2026-09-22"
        self.assertTrue(fbd.already_posted(idem_key, fbd.RESEARCH_STATUS_KEY))

    # --- 6. Rerun does not duplicate successful post ---
    def test_6_rerun_no_duplicate(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        call_count = [0]
        def fake_send(webhook, messages, dry=False):
            call_count[0] += 1
            return True, 200, "ok"
        fake_url = "https://discord.com/api/webhooks/x/y"
        patched_modes = {"research_board": (
            fbd.RESEARCH_STATUS_KEY, fake_url, "DISCORD_RESEARCH_WEBHOOK",
            fbd.research_board_messages)}
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_MODES", patched_modes),              patch.object(fbd, "send", fake_send):
            self.run_main("discord.py", "research_board", "--week", "2026-09-22")
            rc2, out2 = self.run_main("discord.py", "research_board", "--week", "2026-09-22")
        self.assertEqual(call_count[0], 1, "send called twice")
        self.assertIn("already posted", out2)

    # --- 7. Failed send remains retryable ---
    def test_7_failed_send_retryable(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        def fake_fail(webhook, messages, dry=False):
            return False, 500, "server error"
        fake_url = "https://discord.com/api/webhooks/x/y"
        patched_modes = {"research_board": (
            fbd.RESEARCH_STATUS_KEY, fake_url, "DISCORD_RESEARCH_WEBHOOK",
            fbd.research_board_messages)}
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_MODES", patched_modes),              patch.object(fbd, "send", fake_fail):
            self.run_main("discord.py", "research_board", "--week", "2026-09-22")
        idem_key = f"{fbd.RESEARCH_VERSION_ID}|2026-09-22"
        self.assertFalse(fbd.already_posted(idem_key, fbd.RESEARCH_STATUS_KEY))
        posts = [p for p in self.get_status()["posts"]
                 if p["mode"] == fbd.RESEARCH_STATUS_KEY and p["date"] == idem_key]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["result"], "failed")

    # --- 8. Missing webhook remains retryable ---
    def test_8_missing_webhook_retryable(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_WEBHOOK", ""):  # empty = not set
            self.run_main("discord.py", "research_board", "--week", "2026-09-22")
        self.assertFalse(fbd.already_posted("2026-09-22", fbd.RESEARCH_STATUS_KEY))
        posts = [p for p in self.get_status()["posts"]
                 if p["mode"] == fbd.RESEARCH_STATUS_KEY]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["result"], "no_config")

    # --- 9. --dry-run does not mutate status ---
    def test_9_dry_run_no_mutation(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        fake_url = "https://discord.com/api/webhooks/x/y"
        patched_modes = {"research_board": (
            fbd.RESEARCH_STATUS_KEY, fake_url, "DISCORD_RESEARCH_WEBHOOK",
            fbd.research_board_messages)}
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_MODES", patched_modes):
            self.run_main("discord.py", "research_board",
                          "--week", "2026-09-22", "--dry-run")
        posts = self.get_status()["posts"]
        self.assertEqual(len(posts), 0, "--dry-run must write no status")

    # --- 10. Malformed provenance fails closed ---
    def test_10_malformed_provenance_fails_closed(self):
        board = make_research_board(tier="official")  # wrong tier
        prereg = fbd.load_research_prereg_for_delivery()
        with self.assertRaises(SystemExit):
            fbd.validate_research_board_for_delivery(board, prereg)

    # --- 11. Non-zero units fails closed ---
    def test_11_nonzero_units_fails_closed(self):
        board = make_research_board(units=1)
        prereg = fbd.load_research_prereg_for_delivery()
        with self.assertRaises(SystemExit):
            fbd.validate_research_board_for_delivery(board, prereg)

    # --- 12. Research post contains "Research Observation" ---
    def test_12_contains_research_observation(self):
        board = make_research_board()
        msgs = fbd.research_board_messages(board, "2026-09-22")
        all_text = " ".join(
            (m.get("content") or "") + " ".join(
                e.get("title", "") + e.get("description", "")
                for e in m.get("embeds", [])
            )
            for m in msgs
        )
        self.assertIn("Research Observation", all_text)

    # --- 13. Research post contains "0 units" ---
    def test_13_contains_zero_units(self):
        board = make_research_board()
        msgs = fbd.research_board_messages(board, "2026-09-22")
        all_text = " ".join(m.get("content") or "" for m in msgs)
        self.assertIn("0 units", all_text)

    # --- 14. Research post contains "Not a recommendation" ---
    def test_14_contains_not_a_recommendation(self):
        board = make_research_board()
        msgs = fbd.research_board_messages(board, "2026-09-22")
        all_text = " ".join(m.get("content") or "" for m in msgs)
        self.assertIn("Not a recommendation", all_text)

    # --- 15. Research version visible in messages ---
    def test_15_version_visible(self):
        board = make_research_board()
        msgs = fbd.research_board_messages(board, "2026-09-22")
        all_text = " ".join(
            (m.get("content") or "") + " ".join(
                e.get("title", "") + e.get("description", "")
                for e in m.get("embeds", [])
            )
            for m in msgs
        )
        self.assertIn(fbd.RESEARCH_VERSION_ID, all_text)

    # --- 16. Prohibited language absent ---
    def test_16_no_prohibited_language(self):
        board = make_research_board()
        msgs = fbd.research_board_messages(board, "2026-09-22")
        all_text = " ".join(
            (m.get("content") or "") + " ".join(
                e.get("title", "") + e.get("description", "")
                for e in m.get("embeds", [])
            )
            for m in msgs
        ).lower()
        for phrase in ("best bet", "recommended bet", "lock", "unit play",
                       "bet this", "official pick", "premium pick"):
            self.assertNotIn(phrase, all_text,
                             f"prohibited phrase {phrase!r} found")
        # " edge " in "no edge claim" is acceptable — check the affirmative form
        for phrase in ("edge this", "have an edge", "our edge", "proven edge"):
            self.assertNotIn(phrase, all_text,
                             f"prohibited phrase {phrase!r} found")

    # --- 17. Internal premium/free labels not surfaced ---
    def test_17_no_internal_tier_labels(self):
        board = make_research_board()
        msgs = fbd.research_board_messages(board, "2026-09-22")
        all_text = " ".join(
            (m.get("content") or "") + " ".join(
                e.get("title", "") + e.get("description", "")
                for e in m.get("embeds", [])
            )
            for m in msgs
        )
        # "premium" and "free" as standalone tier labels must not appear
        # (they may appear in context like "free record" on the site, but
        # not as pick labels — the embeds use "Observation A / B")
        self.assertNotIn("PREMIUM PLAY", all_text)
        self.assertNotIn("FREE PLAY", all_text)
        self.assertIn("Observation A", all_text)

    # --- 18. Official modes still use official gate ---
    def test_18_official_modes_use_official_gate(self):
        # With OFFICIAL_DELIVERY_ENABLED=False, official modes are blocked.
        with patch.object(delivery_policy, "OFFICIAL_DELIVERY_ENABLED", False),              patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "send") as mock_send:
            rc, out = self.run_main("discord.py", "free",
                                    "--week", "2026-09-22")
        mock_send.assert_not_called()
        self.assertIn("no send", out.lower())


    # =========================================================================
    # Registry cross-check tests (Correction 1)
    # =========================================================================

    # --- 19. Board with valid self-asserted provenance fails if registry absent ---
    def test_19_valid_board_fails_without_registry(self):
        os.remove(fbd.RESEARCH_PREREG_PATH)
        with self.assertRaises(SystemExit):
            fbd.load_research_prereg_for_delivery()

    # --- 20. Wrong registered authority fails closed ---
    def test_20_wrong_registered_authority_fails(self):
        bad = dict(VALID_PREREG_ENTRY, authority="full_delivery_enabled")
        self.write_registry({"registrations": [bad]})
        with self.assertRaises(SystemExit):
            fbd.load_research_prereg_for_delivery()

    # --- 21. Wrong registered selection rule fails closed ---
    def test_21_wrong_registered_selection_rule_fails(self):
        bad = dict(VALID_PREREG_ENTRY, selection_rule="fp-v0.3")
        self.write_registry({"registrations": [bad]})
        with self.assertRaises(SystemExit):
            fbd.load_research_prereg_for_delivery()

    # --- 22. Non-zero registered units fails closed ---
    def test_22_nonzero_registered_units_fails(self):
        bad = dict(VALID_PREREG_ENTRY, units=1)
        self.write_registry({"registrations": [bad]})
        with self.assertRaises(SystemExit):
            fbd.load_research_prereg_for_delivery()

    # --- 23. Valid board + valid registry proceeds ---
    def test_23_valid_board_and_registry_proceeds(self):
        board = make_research_board()
        prereg = fbd.load_research_prereg_for_delivery()
        # Must not raise
        fbd.validate_research_board_for_delivery(board, prereg)

    # =========================================================================
    # Version-aware idempotency tests (Correction 2)
    # =========================================================================

    # --- 24. Same version + same week after successful post is blocked ---
    def test_24_same_version_same_week_blocked_after_post(self):
        board = make_research_board()
        self.write_board("2026-09-22", board)
        fake_url = "https://discord.com/api/webhooks/x/y"
        patched_modes = {"research_board": (
            fbd.RESEARCH_STATUS_KEY, fake_url, "DISCORD_RESEARCH_WEBHOOK",
            fbd.research_board_messages)}
        def fake_send(webhook, messages, dry=False):
            return True, 200, "ok"
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_MODES", patched_modes),              patch.object(fbd, "send", fake_send):
            self.run_main("discord.py", "research_board", "--week", "2026-09-22")
        # The idempotency key is "version|week"
        idem_key = f"{fbd.RESEARCH_VERSION_ID}|2026-09-22"
        self.assertTrue(fbd.already_posted(idem_key, fbd.RESEARCH_STATUS_KEY))
        # Second run is blocked
        with patch.object(delivery_policy, "RESEARCH_DELIVERY_ENABLED", True),              patch.object(fbd, "RESEARCH_MODES", patched_modes),              patch.object(fbd, "send", fake_send):
            rc, out = self.run_main("discord.py", "research_board",
                                    "--week", "2026-09-22")
        self.assertIn("already posted", out)

    # --- 25. Different version + same week is NOT the same delivery identity ---
    def test_25_different_version_same_week_not_blocked(self):
        # Record a "posted" entry for a hypothetical v2 version at the same week.
        other_version = "fp-v0.4-market-observation-v2"
        other_key = f"{other_version}|2026-09-22"
        fbd.record(other_key, fbd.RESEARCH_STATUS_KEY, "posted", status=200)
        # The v1 key must NOT be considered posted.
        v1_key = f"{fbd.RESEARCH_VERSION_ID}|2026-09-22"
        self.assertFalse(fbd.already_posted(v1_key, fbd.RESEARCH_STATUS_KEY),
                         "a different version's posted record must not block v1")


if __name__ == "__main__":
    unittest.main()
