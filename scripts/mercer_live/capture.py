#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1: the live observation capture.

ONE TICK = for each sport: read ESPN game state for today's US/Eastern slate
(and yesterday's before 5 AM ET, for games that crossed midnight), store one
game_state record per event, and — ONLY if some game is in progress or kicks
off within --lead-min, and ONLY if the credit budget allows — make ONE Odds
API call for that sport and store every quote it returned for events in the
live window, phase-labelled and joined to the game state observed in the same
tick. Then write ONE run record. That is all it does.

    python scripts/mercer_live/capture.py --sport nfl --sport ncaaf
    python scripts/mercer_live/capture.py --no-odds                 # spends nothing
    python scripts/mercer_live/capture.py --loop --interval 60 --max-ticks 240
    python scripts/mercer_live/capture.py --data-dir /tmp/ml --dry-run

WHAT IT DOES NOT DO, BY CONSTRUCTION: no edge, no pick, no unit, no Discord,
no LLM, no write outside its data directory, no read of any ledger, no change
to any pregame file. docs/MERCER_LIVE_ML1_ARCHITECTURE.md is the spec.

SCHEDULER-AGNOSTIC. A tick is a function (run_tick) and a process (this CLI).
The loop mode exists so a small host can run it for a game window; nothing
here assumes GitHub Actions, and the architecture doc says why it must not be
run there at one-minute cadence.

IDEMPOTENT RETRY, DISTINCT SAMPLES. Every tick has a run_id (uuid4 unless
--run-id is given). Re-running with the SAME run_id after a crash is a no-op
if the run record exists and otherwise re-derives identical observation_ids,
which the store refuses as duplicates. A new tick has a new run_id and its
records are new observations even when every value is unchanged — sampling
five minutes later is a different observation of the world.

DEGRADE, NEVER DIE, NEVER INVENT. A provider failure is recorded in the run
record with its reason and the tick continues with whatever it did observe.
Nothing is fabricated, nothing stale is re-stamped as fresh, and a failed tick
never touches any other Open Ledger workflow because it shares nothing with
them but a read of the credit ledger.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402
import credits                                                    # noqa: E402
import espn_state                                                 # noqa: E402
import identity                                                   # noqa: E402
import odds_quotes                                                # noqa: E402
from store import Store, StoreError                               # noqa: E402

PACKAGE_VERSION = "ml1-v1"
DEFAULT_MARKETS = "h2h,spreads,totals"
DEFAULT_REGIONS = "us"
DEFAULT_LEAD_MIN = 20          # start polling odds this many minutes before kickoff
WINDOW_BACK_H = 8              # commenceTimeFrom: no football game lasts longer
EARLY_ET_HOUR = 5              # before this ET hour also read yesterday's slate

EXIT_OK, EXIT_NOTHING_OBSERVED, EXIT_STORE = 0, 1, 2


def default_api_key():
    """The Odds API key from the environment or .env.local, or None. Never
    printed; the run record carries only a fingerprint."""
    v = os.environ.get("ODDS_API_KEY")
    if v:
        return v
    try:
        import localenv                                            # scripts/football, read-only
        localenv.load(verbose=False)
    except Exception:                                              # noqa: BLE001
        return None
    return os.environ.get("ODDS_API_KEY") or None


def _fingerprint(key):
    try:
        import localenv
        return localenv.fingerprint(key)
    except Exception:                                              # noqa: BLE001
        import hashlib
        return hashlib.sha256(key.encode()).hexdigest()[:12]


def espn_dates_for(now):
    """ESPN calendar days to read: today ET, plus yesterday ET early morning."""
    days = [now.astimezone(common.ET).date()]
    if common.et_hour(now) < EARLY_ET_HOUR:
        days.insert(0, days[0] - timedelta(days=1))
    return [d.strftime("%Y%m%d") for d in days]


def capture_game_state(sport, now_fn, run_id, espn_fetch):
    """Fetch and parse ESPN for one sport. Returns (records, report)."""
    report = {"dates": [], "observed_at": None, "n_events": 0, "n_records": 0,
              "duplicates_across_dates": 0, "errors": [], "fetch_error": None,
              "by_state": {}}
    records, seen = [], set()
    for d in espn_dates_for(now_fn()):
        report["dates"].append(d)
        try:
            payload = espn_fetch(sport, d)
        except Exception as e:                                      # noqa: BLE001
            report["fetch_error"] = f"{d}: {type(e).__name__}: {str(e)[:200]}"
            break
        observed_at = now_fn()
        report["observed_at"] = common.iso(observed_at)
        recs, errs = espn_state.parse_scoreboard(payload, sport, observed_at, run_id)
        report["n_events"] += len(recs) + len(errs)
        report["errors"].extend(errs)
        for r in recs:
            if r["provider_event_id"] in seen:
                report["duplicates_across_dates"] += 1
                continue
            seen.add(r["provider_event_id"])
            records.append(r)
    report["n_records"] = len(records)
    report["by_state"] = {k: len(v) for k, v in espn_state.classify(records).items()}
    report["incomplete"] = sum(1 for r in records if not r["complete"])
    return records, report


def live_window(records, now, lead_min):
    """(active, imminent): in-progress events, and pre events kicking off soon."""
    active = [r for r in records if r.get("status_state") == espn_state.STATE_IN]
    imminent = []
    horizon = now + timedelta(minutes=lead_min)
    for r in records:
        if r.get("status_state") != espn_state.STATE_PRE:
            continue
        k = common.parse_utc(r.get("scheduled_start"))
        if k is not None and k <= horizon:
            imminent.append(r)
    return active, imminent


def capture_odds(sport, state_records, now_fn, run_id, *, store, api_key, markets,
                 regions, lead_min, floor, cap, credit_log, odds_fetch, book_fn):
    """Decide, fetch, parse, and report the market side for one sport.
    Returns (quote_records, event_records, report)."""
    now = now_fn()
    report = {"decision": None, "reason": None, "observed_at": None,
              "http_status": None, "credits": None, "booked": None,
              "n_events_returned": 0, "n_events_stored": 0,
              "n_events_dropped_pregame_outside_window": 0, "n_quotes": 0,
              "phases": {}, "joins": {}, "errors": [], "fetch_error": None}
    active, imminent = live_window(state_records, now, lead_min)
    report["active_events"] = len(active)
    report["imminent_events"] = len(imminent)
    if not active and not imminent:
        report.update(decision="skipped", reason="no event in progress or within the lead window")
        return [], [], report
    if not api_key:
        report.update(decision="skipped", reason="ODDS_API_KEY not available to this process")
        return [], [], report

    n_markets = len([m for m in markets.split(",") if m.strip()])
    n_regions = len([r for r in regions.split(",") if r.strip()])
    call_cost = n_markets * n_regions
    remaining = credits.latest_remaining(credit_log)
    spent = credits.spent_today(store, common.et_date(now))
    ok, why = credits.budget_check(remaining, spent, call_cost, floor=floor, cap=cap)
    report["budget"] = {"remaining_before": remaining, "spent_today_before": spent,
                        "call_cost": call_cost, "floor": floor, "daily_cap": cap}
    if not ok:
        report.update(decision="skipped", reason=f"budget: {why}")
        return [], [], report

    try:
        resp = odds_fetch(sport, api_key, markets, regions,
                          commence_from=now - timedelta(hours=WINDOW_BACK_H),
                          commence_to=now + timedelta(minutes=lead_min))
    except Exception as e:                                          # noqa: BLE001
        report.update(decision="called", reason="transport failure",
                      fetch_error=f"{type(e).__name__}: {str(e)[:200]}")
        return [], [], report

    observed_at = resp.received_at
    reading = resp.credit_reading(markets, regions, credits.source_tag(sport))
    booked, book_why = book_fn(reading, sport, credit_log, observed_at)
    report.update(decision="called", observed_at=common.iso(observed_at),
                  http_status=resp.status_code, credits=reading,
                  booked={"booked": booked, "why": book_why})
    if resp.status_code != 200:
        report["reason"] = f"HTTP {resp.status_code}; nothing parsed, nothing stored"
        return [], [], report

    state_index = identity.index_by_canonical(state_records)
    quotes, events, errs = odds_quotes.parse_odds(resp.body, sport, observed_at, run_id,
                                                  state_index, markets)
    report["errors"].extend(errs)
    report["n_events_returned"] = len(events)

    # THE WINDOW FILTER. Pregame quotes for games kicking off beyond the lead
    # window are the pregame pipeline's business (football-capture.yml already
    # holds them); storing them every minute would multiply the stream by the
    # size of next week's board. Every other phase is kept in full. This is a
    # sampling decision, recorded in the run, not a deduplication.
    horizon = now + timedelta(minutes=lead_min)
    keep_ids = set()
    for ev in events:
        c = common.parse_utc(ev.get("commence_time"))
        if ev["quote_phase"] == odds_quotes.PHASE_PREGAME and (c is None or c > horizon):
            report["n_events_dropped_pregame_outside_window"] += 1
            continue
        keep_ids.add(ev["provider_event_id"])
    events = [e for e in events if e["provider_event_id"] in keep_ids]
    quotes = [q for q in quotes if q["provider_event_id"] in keep_ids]
    report["n_events_stored"] = len(events)
    report["n_quotes"] = len(quotes)
    for e in events:
        report["phases"][e["quote_phase"]] = report["phases"].get(e["quote_phase"], 0) + 1
        report["joins"][e["join_status"]] = report["joins"].get(e["join_status"], 0) + 1
    report["reason"] = "ok"
    return quotes, events, report


def run_tick(sports, *, store, now_fn=common.now_utc, run_id=None, odds_enabled=True,
             markets=DEFAULT_MARKETS, regions=DEFAULT_REGIONS, lead_min=DEFAULT_LEAD_MIN,
             floor=credits.CREDIT_FLOOR, cap=credits.DAILY_CAP, credit_log=credits.CREDIT_LOG,
             api_key=None, espn_fetch=espn_state.fetch_scoreboard,
             odds_fetch=odds_quotes.fetch_odds, book_fn=credits.book, dry_run=False):
    """One capture execution across the given sports. Returns the run record.
    Every dependency with a side effect is injectable so the self-test can run
    the whole tick with no network and a temporary store."""
    run_id = run_id or uuid.uuid4().hex
    existing = store.find_run(run_id)
    if existing is not None:
        existing["replayed"] = True
        return existing

    started = now_fn()
    run = {"run_id": run_id, "package": common.PACKAGE, "package_version": PACKAGE_VERSION,
           "schema_version": common.SCHEMA_VERSION, "started_at": common.iso(started),
           "sports": {}, "odds_calls": [], "records_written": 0, "duplicates_skipped": 0,
           "shards": [], "errors": [], "dry_run": bool(dry_run), "replayed": False,
           "odds_enabled": bool(odds_enabled), "markets": markets, "regions": regions,
           "lead_min": lead_min,
           "api_key_fingerprint": _fingerprint(api_key) if api_key else None}
    all_records = []
    observed_any = False
    for sport in sports:
        state_records, gs = capture_game_state(sport, now_fn, run_id, espn_fetch)
        observed_any = observed_any or bool(state_records)
        for e in gs["errors"]:
            run["errors"].append({"sport": sport, "source": common.GAME_STATE_SOURCE, **e})
        if gs["fetch_error"]:
            run["errors"].append({"sport": sport, "source": common.GAME_STATE_SOURCE,
                                  "reason": gs["fetch_error"]})
        all_records.extend(state_records)

        if odds_enabled:
            quotes, events, od = capture_odds(
                sport, state_records, now_fn, run_id, store=store, api_key=api_key,
                markets=markets, regions=regions, lead_min=lead_min, floor=floor, cap=cap,
                credit_log=credit_log, odds_fetch=odds_fetch, book_fn=book_fn)
        else:
            quotes, events = [], []
            od = {"decision": "disabled", "reason": "--no-odds", "errors": []}
        for e in od.get("errors", []):
            run["errors"].append({"sport": sport, "source": common.MARKET_SOURCE, **e})
        if od.get("fetch_error"):
            run["errors"].append({"sport": sport, "source": common.MARKET_SOURCE,
                                  "reason": od["fetch_error"]})
        if od.get("credits"):
            run["odds_calls"].append({"sport": sport, "observed_at": od["observed_at"],
                                      "credits": od["credits"]})
        all_records.extend(events)
        all_records.extend(quotes)
        run["sports"][sport] = {"game_state": gs, "odds": od}

    if not dry_run and all_records:
        summary = store.append(all_records)
        run["records_written"] = summary["written"]
        run["duplicates_skipped"] = summary["duplicates_skipped"]
        run["shards"] = summary["shards"]
    run["finished_at"] = common.iso(now_fn())
    run["exit_code"] = EXIT_OK if observed_any else EXIT_NOTHING_OBSERVED
    if not dry_run:
        run["run_record"] = store.write_run(run)
    return run


def _summary_line(run):
    parts = [f"run {run['run_id'][:12]} {run['started_at']}"]
    for sport, s in run["sports"].items():
        gs, od = s["game_state"], s["odds"]
        st = gs.get("by_state", {})
        parts.append(f"{sport}: state pre={st.get('pre', 0)} in={st.get('in', 0)} "
                     f"post={st.get('post', 0)} | odds {od.get('decision')}"
                     + (f" ({od.get('reason')})" if od.get('reason') not in (None, 'ok') else "")
                     + (f" quotes={od.get('n_quotes')} phases={od.get('phases')}"
                        if od.get('decision') == 'called' and od.get('http_status') == 200 else ""))
    parts.append(f"written={run['records_written']} dup={run['duplicates_skipped']} "
                 f"errors={len(run['errors'])} exit={run['exit_code']}")
    return " | ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", action="append", choices=sorted(common.SPORTS),
                    help="repeatable; default both")
    ap.add_argument("--data-dir", default=None,
                    help=f"store root (default {os.path.relpath(common.DATA_DIR, common.ROOT)})")
    ap.add_argument("--no-odds", action="store_true", help="game state only; spends nothing")
    ap.add_argument("--markets", default=DEFAULT_MARKETS)
    ap.add_argument("--regions", default=DEFAULT_REGIONS)
    ap.add_argument("--lead-min", type=int, default=DEFAULT_LEAD_MIN)
    ap.add_argument("--credit-floor", type=int, default=credits.CREDIT_FLOOR)
    ap.add_argument("--daily-cap", type=int, default=credits.DAILY_CAP)
    ap.add_argument("--run-id", default=None, help="reuse to make a retry idempotent")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report; write nothing")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60, help="seconds between ticks (loop)")
    ap.add_argument("--max-ticks", type=int, default=0, help="stop after N ticks (loop; 0 = no limit)")
    ap.add_argument("--until", default=None, help="ISO-8601 UTC instant to stop at (loop)")
    ap.add_argument("--digest", action="store_true",
                    help="after the tick(s), write today's digest file")
    args = ap.parse_args(argv)

    sports = args.sport or sorted(common.SPORTS)
    store = Store(args.data_dir)
    api_key = None if args.no_odds else default_api_key()
    if not args.no_odds and not api_key:
        print("NOTE: ODDS_API_KEY not available; game state will be captured, "
              "odds will be skipped and the run record will say so.")

    until = common.parse_utc(args.until) if args.until else None
    if args.until and until is None:
        ap.error("--until must be an aware ISO-8601 instant, e.g. 2026-09-20T23:30:00Z")

    ticks, last_rc = 0, EXIT_OK
    t0 = time.monotonic()
    while True:
        try:
            run = run_tick(sports, store=store, run_id=args.run_id if ticks == 0 else None,
                           odds_enabled=not args.no_odds, markets=args.markets,
                           regions=args.regions, lead_min=args.lead_min,
                           floor=args.credit_floor, cap=args.daily_cap,
                           api_key=api_key, dry_run=args.dry_run)
        except StoreError as e:
            print(f"STORE FAILURE: {e}")
            return EXIT_STORE
        print(_summary_line(run))
        if args.dry_run and not args.loop:
            print(json.dumps({k: v for k, v in run.items() if k != "sports"}, indent=1, sort_keys=True))
        last_rc = run.get("exit_code", EXIT_OK)
        ticks += 1
        if not args.loop:
            break
        if args.max_ticks and ticks >= args.max_ticks:
            break
        if until is not None and common.now_utc() >= until:
            break
        # Align to the cadence rather than sleeping a flat interval, so drift
        # from slow responses does not accumulate.
        target = t0 + ticks * args.interval
        time.sleep(max(0.0, target - time.monotonic()))

    if args.digest and not args.dry_run:
        d = store.digest(common.et_date(common.now_utc()))
        print(f"digest: {d['et_date']} shards={len(d['shards'])} runs={d['runs']} "
              f"credits_spent={d['credits_spent']}")
    return last_rc


if __name__ == "__main__":
    sys.exit(main())
