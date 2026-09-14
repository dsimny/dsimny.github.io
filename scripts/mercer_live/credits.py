#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1: Odds API credit protection.

LIVE POLLING IS THE FIRST CALLER IN THIS REPOSITORY THAT COULD SPEND THE
ALLOWANCE BY ACCIDENT. Every existing caller makes a handful of calls a day; a
one-minute poller across two sports at three markets makes ~7,500 calls a
week. Three protections, all mechanical:

  FLOOR      no odds call while the latest recorded balance is below
             CREDIT_FLOOR. MLB needs ~8 credits/day and pregame football ~30;
             5,000 keeps both alive for months even if this poller misbehaves.
  DAILY CAP  no odds call once today's Mercer Live run records show
             DAILY_CAP credits spent. Counted from the store, not from memory,
             so a restarted process inherits the day's spend.
  ACCOUNTING every call's reading (remaining / used / cost / markets / regions /
             status) is written into the tick's run record, always. It is
             booked into the shared data/odds_credits.json through the ONE
             writer, scripts/odds_credits.py, at most once per BOOK_INTERVAL
             per sport when the call succeeded — and ALWAYS when it did not.
             That ledger keeps 60 readings; a per-call booking would evict
             every MLB and pregame-football reading within the hour, which is
             the hazard fetch_historical_odds.py already solved by booking one
             reading per batch.

Nothing here makes a network call.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402
import odds_credits                                               # noqa: E402  (scripts/, the one writer)

CREDIT_LOG = os.path.join(common.ROOT, "data", "odds_credits.json")
CREDIT_LOG_KEEP = 60
CREDIT_FLOOR = 5000            # remaining credits below which no live call is made
DAILY_CAP = 4000               # Mercer Live credits per ET day, both sports together
BOOK_INTERVAL_S = 3600         # shared-ledger booking cadence per sport on success
SOURCE_PREFIX = "mercer_live"


def source_tag(sport):
    return f"{SOURCE_PREFIX}:{sport}"


def read_ledger(path=CREDIT_LOG):
    try:
        with io.open(path, encoding="utf-8") as f:
            log = json.load(f)
        readings = log.get("readings") if isinstance(log, dict) else None
        return readings if isinstance(readings, list) else []
    except (OSError, ValueError):
        return []


def latest_remaining(path=CREDIT_LOG):
    """The most recent `remaining` in the shared ledger from ANY caller, or
    None when the ledger is unreadable or empty. None is treated as unknown,
    and unknown does not pass the floor."""
    for r in reversed(read_ledger(path)):
        v = (r or {}).get("remaining")
        if isinstance(v, int):
            return v
    return None


def spent_today(store, et_date):
    """Credits this package spent today, from the run records on disk."""
    total = 0
    for run in store.runs_for_date(et_date):
        for c in run.get("odds_calls") or []:
            cost = (c.get("credits") or {}).get("last_call_cost")
            if isinstance(cost, int):
                total += cost
    return total


def budget_check(remaining, spent, call_cost, floor=CREDIT_FLOOR, cap=DAILY_CAP):
    """(ok, reason). Refuses on unknown balance, on the floor, and on the cap."""
    if remaining is None:
        return False, "balance unknown (no readable credit ledger); refusing to spend blind"
    if remaining < floor:
        return False, f"balance {remaining} below floor {floor}"
    if spent + call_cost > cap:
        return False, f"daily cap: spent {spent} + call {call_cost} > {cap}"
    return True, "ok"


def _last_booked(sport, path):
    tag = source_tag(sport)
    for r in reversed(read_ledger(path)):
        if (r or {}).get("source") == tag:
            return common.parse_utc(r.get("read_utc"))
    return None


def book(reading, sport, path=CREDIT_LOG, now=None):
    """Book a reading into the shared ledger through odds_credits.record().

    Returns (booked: bool, reason: str). Successful calls are throttled to one
    per BOOK_INTERVAL_S per sport; any non-200 reading is booked immediately.
    Never raises (odds_credits.record never raises either).
    """
    now = now or common.now_utc()
    if reading.get("http_status") == 200:
        last = _last_booked(sport, path)
        if last is not None and (now - last).total_seconds() < BOOK_INTERVAL_S:
            return False, "throttled (a success from this sport was booked within the hour)"
    ok = odds_credits.record(reading, path=path, keep=CREDIT_LOG_KEEP, create_parent=False)
    return bool(ok), ("booked" if ok else "odds_credits.record declined (see its NOTE line)")
