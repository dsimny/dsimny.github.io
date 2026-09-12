"""Football launch cohorts. Never mutate an existing ledger entry."""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

VERSION = "fp-v0.4"
START_DATE = "2026-09-11"


def baseline_digest(games, cutoff_utc):
    """(count, sha256) over the entries committed at or before cutoff_utc.

    The pre-reset subset is reconstructed from the entries' own committed_utc
    rather than from a stored key list, which keeps the reset artifact small and
    makes the claim self-verifying with no git history and no external state.

    The digest is taken over sorted [[game_key, sha256(entry)]] pairs, so a
    DELETION changes the count, a MUTATION changes that entry's hash, and a
    BACK-DATED INSERTION into the pre-reset window changes both. Appends after
    the cutoff are invisible to it, which is the point: this file is live
    append-only state and its whole-file hash is expected to move.
    """
    subset = {k: v for k, v in games.items()
              if (v or {}).get("committed_utc", "") <= cutoff_utc}
    pairs = sorted([k, hashlib.sha256(
        json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()]
        for k, v in subset.items())
    return len(subset), hashlib.sha256(
        json.dumps(pairs, separators=(",", ":")).encode()).hexdigest()


def check_append_only(games, baseline):
    """[] when `games` is an append-only extension of `baseline`, else reasons.

    `baseline` is one entry from the reset artifact's append_only_baselines.
    """
    problems = []
    count, digest = baseline_digest(games, baseline["baseline_cutoff_utc"])
    if count != baseline["entry_count_at_reset"]:
        problems.append(
            f"pre-reset entry count is {count}, expected "
            f"{baseline['entry_count_at_reset']} - an entry was removed from, or "
            f"back-dated into, the window at or before "
            f"{baseline['baseline_cutoff_utc']}")
    elif digest != baseline["baseline_entries_sha256"]:
        problems.append(
            "pre-reset entries hash to "
            f"{digest[:16]}..., expected "
            f"{baseline['baseline_entries_sha256'][:16]}... - the count is right, "
            "so an existing entry was mutated in place")
    return problems


def after_start(kickoff):
    try:
        dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        return dt.tzinfo is not None and dt.astimezone(ZoneInfo("America/New_York")).date().isoformat() >= START_DATE
    except (AttributeError, TypeError, ValueError):
        return False


def is_official(row):
    return (row.get("record_cohort") == VERSION
            and row.get("selection_version") == VERSION
            and row.get("tier") in ("premium", "free")
            and bool(row.get("board_sha256"))
            and after_start(row.get("kickoff_utc")))


def partition(entries):
    official, pilot, research = [], [], []
    for row in entries:
        if is_official(row):
            official.append(row)
        elif row.get("record_cohort") != VERSION:
            pilot.append(row)
        else:
            research.append(row)
    return official, pilot, research
