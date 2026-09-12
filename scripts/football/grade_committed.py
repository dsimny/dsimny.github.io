"""Settle the actual fingerprinted weekly selections, never re-rank results."""
import json
import os

import board
import crypto_box
import market
import record_policy


def read_board(week):
    plain, enc = board.board_paths(week)
    if os.path.exists(plain):
        with open(plain, encoding="utf-8") as f:
            return json.load(f)
    if os.path.exists(enc):
        return crypto_box.decrypt_from(enc)
    raise ValueError(f"Missing committed board: {week}")


def validate_integrity(b, commitment):
    """Is this board the one that was committed, and was it frozen honestly?

    RAISES on any violation; returns True otherwise. Consults NO record-cohort
    policy - not selection_version, not is_official(), not the reset boundary
    date. Every question here has the same answer whichever cohort a board
    belongs to, which is exactly what makes it safe on the disclosure path.

    THE INVARIANT THIS SPLIT EXISTS TO ENFORCE: selection/cohort version controls
    OFFICIAL RECORD ELIGIBILITY and must NEVER control POST-GRADE DISCLOSURE of a
    committed held play. A board may stay non-official forever and must still be
    revealed once graded, or House Rule 7 is broken by a version bump.

    `stamp >= kick` stays here rather than moving to policy: a commitment made
    after kickoff is not an ineligible selection, it is a dishonest one, and a
    board that fails it must never publish regardless of cohort.
    """
    sha = crypto_box.sha256_of(b)
    if sha != commitment["board_sha256"]:
        raise ValueError("Board fingerprint mismatch; no ledger writes permitted")
    if b.get("slate_week") != commitment["slate_week"]:
        raise ValueError("Board week mismatch")
    stamp = market.parse_utc(commitment["committed_utc"])
    if stamp is None or stamp.tzinfo is None:
        raise ValueError("Missing timezone-aware commitment time")
    seen = set()
    for tier in ("premium", "free"):
        g = b.get(tier)
        if not g:
            continue
        key = board.game_key(g)
        if key in seen:
            raise ValueError("Duplicate official selection")
        seen.add(key)
        kick = market.parse_utc(g["kickoff_utc"])
        if stamp >= kick:
            raise ValueError("Commitment time is not before kickoff")
        frozen_at = market.parse_utc(g.get("committed_utc"))
        if not frozen_at or frozen_at > market.parse_utc(b["decision_moment_utc"]) or frozen_at >= kick:
            raise ValueError("Selection not frozen before decision and kickoff")
        block = {k: v for k, v in g.items() if k not in
                 ("rank", "tier", "committed_utc", "commitment_sha", "restated", "writeup", "writeup_note")}
        # Writer annotations belong to the board hash; game hash covers layer 1.
        if crypto_box.sha256_of(block) != g.get("commitment_sha"):
            raise ValueError("Selected game fingerprint mismatch")
    return True


def is_selectable(b):
    """May this board enter the OFFICIAL record? Cohort policy, nothing else.

    Purely a classification question, and deliberately unreachable from the
    disclosure path. Every check here is one the reset exists to enforce:
    relaxing any of them would retroactively select legacy boards into the
    official record. A False here means "not official" - never "not publishable".
    """
    if b.get("selection_version") != record_policy.VERSION:
        return False
    for tier in ("premium", "free"):
        g = b.get(tier)
        if not g:
            continue
        if (not record_policy.after_start(g["kickoff_utc"])
                or g.get("selection_version") != record_policy.VERSION
                or not g.get("selection_eligible")):
            return False
    return True


def validate(b, commitment):
    """Integrity AND cohort eligibility, as the SELECTION path requires.

    Unchanged behaviour for selection: integrity violations raise their specific
    errors, an ineligible board raises the original combined message. The reveal
    path deliberately does NOT call this - it calls validate_integrity() alone.
    """
    validate_integrity(b, commitment)
    if not is_selectable(b):
        raise ValueError("Invalid official selection date, version, range or commitment time")
    return True


def settle(g, tier, b, commitment, results, cfg, snaps):
    keyfn = cfg["keyfn"] or (lambda x: x)
    matches = [r for r in results.values()
               if f'{keyfn(r["away"])} @ {keyfn(r["home"])}' ==
               ' @ '.join(keyfn(x) for x in g["matchup"].split(' @ '))
               and market.parse_utc(r.get("kickoff_utc")) == market.parse_utc(g["kickoff_utc"])]
    if len(matches) > 1:
        raise ValueError("Ambiguous result identity")
    if not matches or not matches[0].get("final"):
        return None
    r = matches[0]
    if not cfg["gradeable"](r):
        raise ValueError("Committed selection is not regular season; manual audit required")
    if g["side"] not in (g["away"], g["home"]):
        raise ValueError("Unknown selected side")
    home = g["side"] == g["home"]
    margin = r["home_score"] - r["away_score"]
    result = "push" if margin == 0 else ("win" if home == (margin > 0) else "loss")
    clv, close_file = None, None
    kick = market.parse_utc(g["kickoff_utc"])
    for t, name, snap in reversed(snaps):
        if not 0 < (kick - t).total_seconds() <= market.MAX_CLOSE_H * 3600:
            continue
        ev = market.find_event(snap, r["away"], r["home"], cfg["keyfn"])
        if not ev:
            continue
        f = market.fair(market.eligible(ev, t), ev["away_raw"], ev["home_raw"])
        if f:
            side = ev["home_raw"] if home else ev["away_raw"]
            clv = round(100 * (f[side] - market.implied(g["best_price"])), 3)
            close_file = name
            break
    return dict(sport=g["sport"], slate_week=b["slate_week"], tier=tier,
                selection_version=record_policy.VERSION, record_cohort=record_policy.VERSION,
                board_sha256=commitment["board_sha256"], committed_utc=commitment["committed_utc"],
                game_commitment_sha=g["commitment_sha"], espn_event_id=r["espn_event_id"],
                matchup=g["matchup"], kickoff_utc=g["kickoff_utc"], side=g["side"],
                price=g["best_price"], book=g["best_book"], result=result,
                final=f'{r["away_score"]}-{r["home_score"]}', units=0,
                pnl_per_unit=round(market.payout(g["best_price"]), 4) if result == "win" else (-1 if result == "loss" else 0),
                clv_pts=clv, close_capture=close_file,
                clv_status="measured" if clv is not None else "unavailable; settlement retained")


def additions(sport, ledger, results, cfg, snaps):
    if not os.path.exists(board.COMMITMENTS):
        return []
    with open(board.COMMITMENTS, encoding="utf-8") as f:
        commitments = json.load(f)["commitments"]
    done = {(e.get("board_sha256"), e.get("tier")) for e in ledger["entries"]
            if record_policy.is_official(e)}
    out = []
    for c in commitments:
        if c.get("selection_version") != record_policy.VERSION:
            continue  # Legacy evidence is retained; never retroactively selected.
        b = read_board(c["slate_week"])
        # MOVED OUT OF validate(). Selection gates on version; disclosure does
        # not. The check above reads the COMMITMENT, this one reads the BOARD -
        # they are different objects and both must agree before a row may enter
        # the official record, so dropping to the commitment alone would let a
        # board whose own version disagrees with its commitment be selected.
        if b.get("selection_version") != record_policy.VERSION:
            continue
        if not validate(b, c):
            continue
        for tier in ("premium", "free"):
            g = b.get(tier)
            if not g or g["sport"] != sport or (c["board_sha256"], tier) in done:
                continue
            row = settle(g, tier, b, c, results, cfg, snaps)
            if row:
                out.append(row)
    return out


def reveal_completed(ledger):
    """Reveal only after all selected positions settle, across both sports."""
    if not os.path.exists(board.COMMITMENTS):
        return
    with open(board.COMMITMENTS, encoding="utf-8") as f:
        commitments = json.load(f)["commitments"]
    for c in commitments:
        # NO COHORT GATE ON THIS PATH, ANYWHERE. Whether a settled board may be
        # published is not a cohort question. Three gates used to stand between a
        # graded board and disclosure, and every one of them consulted
        # record_policy.VERSION:
        #
        #   1. this guard, which also read c["selection_version"] != VERSION;
        #   2. validate(), which raised on the board's and each game's version
        #      and on the reset boundary date;
        #   3. the `done` set below, built through record_policy.is_official().
        #
        # Any one of them left standing means a VERSION bump strands a graded
        # board held after settling - which House Rule 7 calls fraud - so all
        # three are gone from here. An old-version board stays NON-OFFICIAL
        # forever and is still revealed.
        if c.get("revealed"):
            continue
        b = read_board(c["slate_week"])
        # INTEGRITY ONLY. A fingerprint or freeze-time violation still aborts
        # disclosure by raising, because publishing a board that does not match
        # its commitment is worse than not publishing at all.
        if not validate_integrity(b, c):
            continue
        required = {t for t in ("premium", "free") if b.get(t)}
        # SETTLED, not OFFICIAL. `done` asks "has this position been graded?",
        # and a ledger row keyed to this board's fingerprint answers yes whatever
        # cohort it was later classified into. Filtering through is_official()
        # here made a pilot-cohort row invisible, so a fully graded legacy board
        # could never satisfy `required <= done`.
        done = {e["tier"] for e in ledger["entries"]
                if e.get("board_sha256") == c["board_sha256"]
                and e.get("tier") in ("premium", "free")}
        if required <= done:
            plain, _ = board.board_paths(c["slate_week"])
            with open(plain, "w", encoding="utf-8") as f:
                json.dump(b, f, indent=1, sort_keys=True)
            board.mark_revealed(c["slate_week"])
