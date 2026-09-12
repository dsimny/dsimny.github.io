"""Offline regression tests for the disclosed reset and committed settlement."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import board
import crypto_box
import grade_committed as grader
import grade_football
import market
import record_policy as policy


def fixture():
    g = dict(sport="nfl", matchup="A @ H", away="A", home="H", side="H",
             kickoff_utc="2026-09-13T17:00:00Z", best_price=-150, best_book="draftkings",
             selection_version=policy.VERSION, selection_eligible=True)
    g["commitment_sha"] = crypto_box.sha256_of(g)
    g.update(committed_utc="2026-09-12T17:00:00Z", tier="premium", rank=1, restated=False)
    b = dict(slate_week="2026-09-08", selection_version=policy.VERSION,
             decision_moment_utc="2026-09-12T18:00:00Z", premium=g, free=None)
    c = dict(selection_version=policy.VERSION, slate_week=b["slate_week"], board_sha256=crypto_box.sha256_of(b),
             committed_utc="2026-09-12T18:01:00Z")
    r = dict(away="A", home="H", kickoff_utc=g["kickoff_utc"], final=True,
             home_score=24, away_score=17, espn_event_id="123")
    return g, b, c, {"123": r}


class ResetTests(unittest.TestCase):
    def test_eastern_boundary_and_fail_closed(self):
        self.assertFalse(policy.after_start("2026-09-11T03:59:59Z"))
        self.assertTrue(policy.after_start("2026-09-11T04:00:00Z"))
        self.assertFalse(policy.after_start("2026-09-11"))
        self.assertFalse(policy.is_official(dict(tier="premium", kickoff_utc="2026-09-12T00:00:00Z")))

    def test_uniform_vig_does_not_choose_longshot(self):
        books = sorted(market.TIER1)[:5]
        q = {bk: {"A": 3500, "H": -10000} for bk in books}
        m = market.evaluate(q, "A", "H")
        self.assertEqual(m["side"], "H")
        self.assertFalse(m["selection_eligible"])
        self.assertEqual(market.assign([m]), (None, None))
        for price in (1600, 1800, 3500):
            m = market.evaluate({bk: {"A": price, "H": -10000} for bk in books}, "A", "H")
            self.assertFalse(m["selection_eligible"])

    def test_hash_and_late_commit_refusals(self):
        g, b, c, _ = fixture()
        self.assertTrue(grader.validate(b, c))
        changed = copy.deepcopy(b); changed["premium"]["best_price"] = 200
        with self.assertRaises(ValueError): grader.validate(changed, c)
        c["committed_utc"] = g["kickoff_utc"]
        with self.assertRaises(ValueError): grader.validate(b, c)

    def test_settlement_no_close_win_loss_push(self):
        g, b, c, results = fixture()
        cfg = dict(keyfn=None, gradeable=lambda r: True)
        for score, expected in ((24, "win"), (10, "loss"), (17, "push")):
            results["123"]["home_score"] = score
            row = grader.settle(g, "premium", b, c, results, cfg, [])
            self.assertEqual(row["result"], expected)
            self.assertIsNone(row["clv_pts"])
            self.assertTrue(policy.is_official(row))
        results["123"]["final"] = False
        self.assertIsNone(grader.settle(g, "premium", b, c, results, cfg, []))

    def test_idempotence_and_pilot_week_does_not_block(self):
        g, b, c, results = fixture()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/"commitments.json"
            p.write_text(json.dumps({"commitments": [c]}))
            with patch.object(board, "COMMITMENTS", str(p)), patch.object(grader, "read_board", return_value=b):
                ledger = {"entries": [dict(slate_week=b["slate_week"], sport="nfl", tier="premium", result="loss")]}
                original = copy.deepcopy(ledger["entries"])
                rows = grader.additions("nfl", ledger, results, dict(keyfn=None, gradeable=lambda r: True), [])
                self.assertEqual(len(rows), 1)
                ledger["entries"].extend(rows)
                self.assertEqual(grader.additions("nfl", ledger, results, dict(keyfn=None, gradeable=lambda r: True), []), [])
                self.assertEqual(ledger["entries"][:1], original)

    def test_frozen_block_and_no_postkickoff_commit(self):
        g, _, _, _ = fixture()
        g = {k: v for k, v in g.items() if k not in ("commitment_sha", "committed_utc", "tier", "rank", "restated")}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(board, "GAME_COMMITMENTS", str(Path(td)/"games.json")):
                board.commit_games([g], market.parse_utc("2026-09-12T17:00:00Z"))
                g["best_price"] = 200
                board.commit_games([g], market.parse_utc("2026-09-12T17:30:00Z"))
                self.assertEqual(g["best_price"], -150)
                self.assertTrue(g["restated"])
                late = dict(g, matchup="X @ Y")
                _, n = board.commit_games([late], market.parse_utc("2026-09-13T18:00:00Z"))
                self.assertEqual(n, 0)

    def test_corrupt_ledger_is_not_empty(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/"ledger.json"; p.write_text("broken")
            with patch.object(grade_football, "LEDGER", str(p)):
                with self.assertRaises(ValueError): grade_football.load_ledger()

    def test_partial_cross_sport_reveal_waits_and_preserves_hash(self):
        g, b, c, _ = fixture()
        other = copy.deepcopy(g)
        other.update(sport="ncaaf", matchup="B @ C", away="B", home="C", side="C")
        block = {k: v for k, v in other.items() if k not in
                 ("commitment_sha", "committed_utc", "rank", "tier", "restated")}
        other["commitment_sha"] = crypto_box.sha256_of(block)
        b["free"] = other
        c["board_sha256"] = crypto_box.sha256_of(b)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/"commitments.json"
            p.write_text(json.dumps({"commitments": [c]}))
            out = str(Path(td)/"board.json")
            row = dict(record_cohort=policy.VERSION, selection_version=policy.VERSION,
                       tier="premium", board_sha256=c["board_sha256"], kickoff_utc=g["kickoff_utc"])
            ledger = {"entries": [row]}
            with patch.object(board, "COMMITMENTS", str(p)), patch.object(grader, "read_board", return_value=b), patch.object(board, "board_paths", return_value=(out, out+'.enc')):
                grader.reveal_completed(ledger)
                self.assertFalse(Path(out).exists())
                ledger["entries"].append(dict(row, tier="free"))
                grader.reveal_completed(ledger)
                self.assertEqual(crypto_box.sha256_of(json.loads(Path(out).read_text())), c["board_sha256"])
                self.assertTrue(json.loads(p.read_text())["commitments"][0]["revealed"])

    def _graded_unrevealed(self, td):
        """A board that is committed, settled, and not yet revealed."""
        g, b, c, _ = fixture()
        p = Path(td)/"commitments.json"
        p.write_text(json.dumps({"commitments": [dict(c)]}))
        out = str(Path(td)/"board.json")
        ledger = {"entries": [dict(record_cohort=policy.VERSION,
                                  selection_version=policy.VERSION, tier="premium",
                                  board_sha256=c["board_sha256"],
                                  kickoff_utc=g["kickoff_utc"])]}
        return b, c, p, out, ledger

    def test_reveal_does_not_gate_on_the_commitment_cohort(self):
        """HOUSE RULE 7: disclosure is not a cohort question.

        The reveal guard used to read
            if c.get("revealed") or c.get("selection_version") != VERSION
        so a commitment carrying any other version was skipped PERMANENTLY, not
        deferred. A settled board would sit held forever, which is the state the
        rule exists to prevent. Here the commitment's version is wrong and the
        board's is right: reveal must still happen.
        """
        with tempfile.TemporaryDirectory() as td:
            b, c, p, out, ledger = self._graded_unrevealed(td)
            stale = dict(c, selection_version="fp-v0.3")
            p.write_text(json.dumps({"commitments": [stale]}))
            with patch.object(board, "COMMITMENTS", str(p)), \
                 patch.object(grader, "read_board", return_value=b), \
                 patch.object(board, "board_paths", return_value=(out, out+'.enc')):
                grader.reveal_completed(ledger)
            self.assertTrue(Path(out).exists(),
                            "a settled board was withheld because its commitment "
                            "carried a superseded selection_version")

    def test_superseded_version_reveals_but_never_becomes_official(self):
        """THE CONTRACT: cohort controls OFFICIAL RECORD ELIGIBILITY, never
        POST-GRADE DISCLOSURE of a committed held play.

        A board committed and graded under fp-v0.4 must still reveal after a bump
        to fp-v0.5, and must stay out of the official record forever. Three gates
        used to stand in the way - the reveal guard, validate()'s per-game raise,
        and the is_official() filter building `done` - and any one of them left
        standing strands a settled play held, which House Rule 7 calls fraud.

        Covers, in order: a committed board exists; it is graded; revealed starts
        false; its version is superseded; reveal succeeds; revealed becomes true;
        it is NOT added to the official record; and a second reveal is a no-op.
        """
        with tempfile.TemporaryDirectory() as td:
            b, c, p, out, ledger = self._graded_unrevealed(td)
            # (1) committed board exists  (2) graded  (3) not yet revealed
            self.assertEqual(c["board_sha256"], crypto_box.sha256_of(b))
            self.assertEqual([e["tier"] for e in ledger["entries"]], ["premium"])
            self.assertFalse(json.loads(p.read_text())["commitments"][0].get("revealed"))
            # (4) the version this board was selected under is now superseded
            with patch.object(board, "COMMITMENTS", str(p)), \
                 patch.object(grader, "read_board", return_value=b), \
                 patch.object(board, "board_paths", return_value=(out, out+'.enc')), \
                 patch.object(policy, "VERSION", "fp-v0.5"):
                self.assertNotEqual(b["selection_version"], policy.VERSION)
                # (5) reveal succeeds - no raise, no skip
                grader.reveal_completed(ledger)
                self.assertTrue(Path(out).exists(),
                                "a graded board was withheld because its version "
                                "was superseded")
                self.assertEqual(crypto_box.sha256_of(json.loads(Path(out).read_text())),
                                 c["board_sha256"], "published board is not the "
                                                    "one that was committed")
                # (6) revealed flips
                self.assertTrue(json.loads(p.read_text())["commitments"][0]["revealed"])
                # (7) and it is STILL not official, now or ever
                row = ledger["entries"][0]
                self.assertFalse(policy.is_official(row))
                official, pilot, research = policy.partition(ledger["entries"])
                self.assertEqual((len(official), len(pilot)), (0, 1))
                # (8) idempotent - a second pass changes nothing and does not raise
                stamp = Path(out).read_text()
                commits = p.read_text()
                grader.reveal_completed(ledger)
                self.assertEqual(Path(out).read_text(), stamp)
                self.assertEqual(p.read_text(), commits)

    def test_integrity_failure_blocks_disclosure_even_when_settled(self):
        """(9) Dropping the cohort gates must not drop the integrity gates.

        A board whose contents no longer hash to the public commitment must never
        publish, settled or not, current version or superseded. Publishing a board
        that does not match its fingerprint is worse than not publishing at all -
        the fingerprint is the only thing making the commit-and-reveal claim
        checkable.
        """
        # EACH FINGERPRINT LAYER GETS A TAMPER ONLY IT CAN CATCH. A naive edit to
        # a game breaks the per-game commitment_sha AND the board sha, so the
        # board-level check fires first and the per-game one is never exercised -
        # deleting either would leave such a test green, which is the hole a
        # control exists to find.
        #   board only  - adds a top-level key: moves sha256_of(b) and nothing
        #                 else, so only the board-level check can catch it.
        #   game only   - edits the game AND re-stamps the commitment's
        #                 board_sha256 to match, which is the realistic second-
        #                 layer attack: someone who can rewrite the board file and
        #                 its commitment still cannot forge commitment_sha, which
        #                 was published per game before kickoff.
        def tamper_board(x, commitment):
            x["late_addition"] = "not present when the fingerprint was taken"

        def tamper_game(x, commitment):
            x["premium"]["best_price"] = -101
            commitment["board_sha256"] = crypto_box.sha256_of(x)

        #   week only  - swaps the board's slate_week and re-stamps the board
        #                hash. slate_week is not inside any game block, so both
        #                fingerprint layers still agree and only the identity
        #                check can tell that this is a different week's board
        #                being published against this week's commitment.
        def tamper_week(x, commitment):
            x["slate_week"] = "2026-09-15"
            commitment["board_sha256"] = crypto_box.sha256_of(x)

        cases = [(f"{w} / {v}", fn, bump)
                 for w, fn in (("board only", tamper_board), ("game only", tamper_game),
                               ("week only", tamper_week))
                 for v, bump in (("current version", False), ("superseded version", True))]
        for label, tamper, bump in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as td:
                b, c, p, out, ledger = self._graded_unrevealed(td)
                stored = json.loads(p.read_text())["commitments"][0]
                tampered = copy.deepcopy(b)
                tamper(tampered, stored)            # after the fingerprint was taken
                p.write_text(json.dumps({"commitments": [stored]}))
                ctx = [patch.object(board, "COMMITMENTS", str(p)),
                       patch.object(grader, "read_board", return_value=tampered),
                       patch.object(board, "board_paths", return_value=(out, out+'.enc'))]
                if bump:
                    ctx.append(patch.object(policy, "VERSION", "fp-v0.5"))
                for x in ctx:
                    x.start()
                try:
                    with self.assertRaises(ValueError):
                        grader.reveal_completed(ledger)
                finally:
                    for x in reversed(ctx):
                        x.stop()
                self.assertFalse(Path(out).exists(),
                                 "a tampered board was published")
                self.assertFalse(json.loads(p.read_text())["commitments"][0].get("revealed"))

    def test_every_remaining_integrity_check_blocks_disclosure(self):
        """(9, completed) The non-fingerprint integrity checks are on the reveal
        path too, and none of them was pinned by anything.

        Removing the tz-aware-stamp check or the duplicate-selection check left
        the whole suite green before this test existed - they were unpinned even
        before the integrity/policy split, so a later cleanup could have deleted
        either without a single failure. Both now abort disclosure, and both are
        proven to.
        """
        def naive_stamp(b, commitment):
            # A commitment time with no timezone cannot be ordered against
            # kickoff, so "frozen before kickoff" becomes unprovable.
            commitment["committed_utc"] = "2026-09-12T18:01:00"

        def duplicate_game(b, commitment):
            # The same game sold as both the premium and the free play: one
            # position, counted twice in the record.
            b["free"] = copy.deepcopy(b["premium"])
            commitment["board_sha256"] = crypto_box.sha256_of(b)

        def frozen_after_decision(b, commitment):
            # Frozen AFTER the published decision moment, still before kickoff:
            # the pick was chosen with more information than the stated rule
            # allows. committed_utc is excluded from the game block hash, so
            # only the board hash moves and only this check can catch it.
            b["premium"]["committed_utc"] = "2026-09-12T19:00:00Z"
            commitment["board_sha256"] = crypto_box.sha256_of(b)

        for label, tamper in (("naive commitment stamp", naive_stamp),
                              ("same game in both tiers", duplicate_game),
                              ("frozen after the decision moment", frozen_after_decision)):
            with self.subTest(label), tempfile.TemporaryDirectory() as td:
                b, c, p, out, ledger = self._graded_unrevealed(td)
                stored = json.loads(p.read_text())["commitments"][0]
                broken = copy.deepcopy(b)
                tamper(broken, stored)
                p.write_text(json.dumps({"commitments": [stored]}))
                with patch.object(board, "COMMITMENTS", str(p)), \
                     patch.object(grader, "read_board", return_value=broken), \
                     patch.object(board, "board_paths", return_value=(out, out+'.enc')):
                    with self.assertRaises(ValueError):
                        grader.reveal_completed(ledger)
                self.assertFalse(Path(out).exists())
                self.assertFalse(json.loads(p.read_text())["commitments"][0].get("revealed"))

    def test_append_only_baseline_detects_every_violation(self):
        """game_commitments.json is LIVE append-only state, not a frozen file.

        It was listed under historical_files_sha256, whose meaning is "this whole
        file stays byte-identical forever". That was never true of it - the
        pipeline appends a per-game commitment on every run, and origin/main had
        already moved past the recorded hash within hours. A public integrity
        claim that cannot verify is worse than no claim, so it is reclassified
        under append_only_baselines and the property actually asserted is the one
        that must hold: the pre-reset entries are all still there, unchanged.

        HERMETIC ON PURPOSE. This tests the checker against synthetic stores; the
        LIVE file is checked by selftest_capture_isolation, which runs inside
        football-capture.yml - the workflow that writes it - and in the daily data
        monitor. A red run in this gate must mean a code regression, never drift.
        """
        cut = "2026-09-12T00:00:00Z"
        base_games = {
            f"nfl|A{i} @ H{i}|2026-09-10T17:00:00Z": {
                "committed_utc": f"2026-09-09T0{i}:00:00Z", "sha": f"x{i}"}
            for i in range(1, 5)}
        n, d = policy.baseline_digest(base_games, cut)
        baseline = {"entry_count_at_reset": n, "baseline_cutoff_utc": cut,
                    "baseline_entries_sha256": d}
        self.assertEqual(n, 4)

        # the honest case: later appends, nothing else touched
        extended = dict(base_games)
        extended["nfl|A9 @ H9|2026-09-14T17:00:00Z"] = {
            "committed_utc": "2026-09-13T17:00:00Z", "sha": "x9"}
        self.assertEqual(policy.check_append_only(extended, baseline), [],
                         "a pure append was reported as a violation")

        # unchanged store is trivially fine, and the digest ignores ordering
        self.assertEqual(policy.check_append_only(dict(reversed(list(
            base_games.items()))), baseline), [])

        for label, games in (
                ("deletion", {k: v for k, v in list(base_games.items())[1:]}),
                ("mutation", dict(base_games, **{
                    list(base_games)[0]: {"committed_utc": "2026-09-09T01:00:00Z",
                                          "sha": "TAMPERED"}}),),
                ("back-dated insertion", dict(base_games, **{
                    "nfl|SNUCK @ IN|2026-09-10T17:00:00Z": {
                        "committed_utc": "2026-09-08T00:00:00Z", "sha": "new"}})),
                ("append plus a deletion", {
                    **{k: v for k, v in list(base_games.items())[1:]},
                    "nfl|A9 @ H9|2026-09-14T17:00:00Z": {
                        "committed_utc": "2026-09-13T17:00:00Z", "sha": "x9"}}),
        ):
            with self.subTest(label):
                self.assertTrue(policy.check_append_only(games, baseline),
                                f"{label} was NOT detected")

    def test_whole_file_sha_is_not_the_claim(self):
        """The artifact must not re-assert byte-equality on the live file.

        This is the regression that motivated the reclassification: anyone
        re-adding game_commitments.json to historical_files_sha256 is re-making a
        claim that is already false in production.
        """
        art = json.loads((Path(__file__).resolve().parents[2] /
                          "data" / "football" / "reset_2026-09-11.json").read_text(
                              encoding="utf-8"))
        gc = "data/football/game_commitments.json"
        self.assertNotIn(gc, art["historical_files_sha256"],
                         "live append-only state is listed as a frozen file")
        self.assertIn(gc, art["append_only_baselines"])
        b = art["append_only_baselines"][gc]
        for field in ("entry_count_at_reset", "sha256_at_reset",
                      "baseline_cutoff_utc", "baseline_entries_sha256", "note"):
            self.assertIn(field, b)
        self.assertIn("EXPECTED to diverge", b["note"])

    def test_duplicate_and_nonregular_results_fail_closed(self):
        g, b, c, results = fixture()
        cfg = dict(keyfn=None, gradeable=lambda r: False)
        with self.assertRaises(ValueError): grader.settle(g, "premium", b, c, results, cfg, [])
        results["copy"] = copy.deepcopy(results["123"])
        with self.assertRaises(ValueError): grader.settle(g, "premium", b, c, results, cfg, [])

    def test_pilot_losses_excluded_without_mutation(self):
        g, b, c, results = fixture()
        row = grader.settle(g, "premium", b, c, results, dict(keyfn=None, gradeable=lambda r: True), [])
        old = dict(tier="premium", result="loss", pnl_per_unit=-1, kickoff_utc=g["kickoff_utc"])
        entries = [old, row]
        saved = copy.deepcopy(entries)
        official, pilot, _ = policy.partition(entries)
        self.assertEqual(official, [row]); self.assertEqual(pilot, [old])
        self.assertEqual(entries, saved)

    def test_writer_notes_do_not_change_game_hash(self):
        _, b, c, _ = fixture()
        b["premium"].update(writeup="Market coverage.", writeup_note="ok")
        c["board_sha256"] = crypto_box.sha256_of(b)
        self.assertTrue(grader.validate(b, c))



if __name__ == "__main__":
    unittest.main()
