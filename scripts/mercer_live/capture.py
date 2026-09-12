#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1, the live observation boundary.

ONE JOB: capture and preserve time-stamped live football game state and live
market observations, accurately. Spec: docs/MERCER_LIVE_V0.1_PREREGISTRATION.md
(sections 5-8, 11) and docs/MERCER_LIVE_ML1_ARCHITECTURE.md.

IT DOES NOT: compute an edge, generate a pick, allocate units, post to
Discord, grade anything, call an LLM, or touch the pregame football pipeline.
scripts/mercer_live/selftest_mercer_live.py greps this package to prove the
first five by absence and drives the sixth with fixtures.

A TICK is one sample of one league:
  1. ESPN scoreboard (free)         -> live_game_observation rows
  2. active-game filter             -> is any game actually in progress?
  3. The Odds API (credits)         -> live_market_observation rows, ONLY if
                                       step 2 says yes and the budget allows
  4. join markets to game state     -> event_id / phase on every market row
  5. append everything, then record the run_id (idempotency)

RUN IDENTITY. --run-id, or else the interval slot the tick falls in
(mlcommon.slot_run_id). Re-executing inside the same slot is refused before
anything is written; the next slot is a new observation even if nothing
changed. See pre-registration section 6 and the store's docstring.

FAILURE. A source that fails (network, HTTP error, unparseable body) writes
NOTHING for that source; the failure is written into the tick record instead,
and `once` exits non-zero. The other source's observations are kept. Nothing
is retried with stale data presented as fresh; nothing is fabricated.

SCHEDULING IS SEPARATE. `once` is the callable unit; `loop` is a minimal
in-process scheduler for a local machine or a small host during a game
window. Nothing here runs on GitHub Actions and nothing should: see the
architecture doc for why, and for the recommended production scheduler.

Run:
  python scripts/mercer_live/capture.py once  --leagues nfl,ncaaf --dry-run
  python scripts/mercer_live/capture.py once  --leagues nfl
  python scripts/mercer_live/capture.py loop  --leagues nfl,ncaaf --interval 60 --max-minutes 240
  python scripts/mercer_live/capture.py probe --leagues nfl --out-dir /tmp/probe
  python scripts/mercer_live/capture.py seal
  python scripts/mercer_live/capture.py status
"""
import argparse
import json
import os
import sys
import time
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "football"))

import mlcommon as C                                  # noqa: E402
import gamestate                                      # noqa: E402
import odds                                           # noqa: E402
import store as storemod                              # noqa: E402
import budget as budgetmod                            # noqa: E402

CAPTURE_VERSION = "ml1-capture-1.0"


def _json_or_none(body):
    try:
        return json.loads(body.decode("utf-8") if isinstance(body, bytes) else body), None
    except (ValueError, UnicodeDecodeError) as e:
        return None, f"not JSON: {e}"


def _read_fixture(path):
    with open(path, "rb") as f:
        body = f.read()
    return body, {"provider_http_date": None, "request_sent_at": None,
                  "response_received_at": None, "http_status": 200, "fixture": path}


# ---------------------------------------------------------------- tick --

def tick(league, opts, st, bud, now=None, fixtures=None):
    """One sample of one league. Returns the tick record (also written)."""
    fixtures = fixtures or {}
    now = now or C.now_utc()
    run_id = opts.run_id or C.slot_run_id(league, now, opts.interval)
    date_str = C.utc_date(now)
    rec = {"record": "tick", "schema_version": C.SCHEMA_VERSION,
           "capture_version": CAPTURE_VERSION, "run_id": run_id, "league": league,
           "started_at": C.iso_ms(now), "dry_run": bool(opts.dry_run),
           "interval_s": opts.interval, "markets": opts.markets, "regions": opts.regions,
           "espn": {"ok": False}, "odds": {"called": False, "ok": None}, "errors": [],
           "written": {}}

    if not opts.dry_run and st.run_seen(league, date_str, run_id):
        rec["skipped"] = "duplicate_run"
        rec["note"] = ("this run_id was already written into this partition; "
                       "nothing appended (idempotent re-execution)")
        return rec

    # ---- 1. game state ----
    games, g_errors, g_ctx = [], [], None
    try:
        if "espn" in fixtures:
            body, meta = _read_fixture(fixtures["espn"])
        else:
            body, meta = gamestate.fetch(league, timeout=opts.timeout)
        observed_at = C.parse_utc(meta.get("response_received_at")) or now
        payload, err = _json_or_none(body)
        if err:
            raise C.MalformedPayload(err)
        g_ctx = {"run_id": run_id, "observed_at": C.iso_ms(observed_at),
                 "request_sent_at": meta.get("request_sent_at"),
                 "provider_http_date": meta.get("provider_http_date"),
                 "raw_ref": None if opts.dry_run else
                 f"{league}/{date_str}/payloads/{run_id.replace(':', '_')}.espn.json.gz",
                 "raw_sha256": C.sha256_hex(body)}
        games, g_errors = gamestate.observe(league, payload, g_ctx)
        active = gamestate.active_summary(games, observed_at, opts.lookahead_min * 60)
        rec["espn"] = {"ok": True, "http_status": meta.get("http_status"),
                       "observed_at": g_ctx["observed_at"], "n_games": len(games),
                       "n_errors": len(g_errors), "active": active,
                       "n_missing_required": sum(1 for g in games if g["missing_required"]),
                       "n_anomalies": sum(len(g["anomalies"]) for g in games)}
        g_body = body
    except Exception as e:                               # noqa: BLE001
        rec["espn"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        rec["errors"].append({"source": "espn", "error": f"{type(e).__name__}: {e}"})
        active = {"live": 0, "starting_soon": 0, "later": 0, "final": 0, "live_event_ids": []}
        games, g_body = [], None
    for e in g_errors:
        rec["errors"].append(dict(e, source="espn-event"))

    # ---- 2. should we spend a credit? ----
    cost = odds.call_cost(opts.markets, opts.regions)
    if opts.no_odds:
        want, why = False, "disabled (--no-odds)"
    elif opts.always_odds:
        want, why = True, "forced (--always-odds)"
    elif not rec["espn"]["ok"]:
        want, why = False, "game state unavailable - cannot classify quotes, not calling"
    elif active["live"] > 0:
        want, why = True, f"{active['live']} live game(s)"
    else:
        want, why = False, f"no live game ({active['starting_soon']} starting within {opts.lookahead_min} min)"
    if want:
        # The cap applies to fixture runs too, so the self-test can prove it
        # through the same path a live tick takes; only a REAL call records.
        ok, reason = bud.allow(cost, now)
        if not ok:
            want, why = False, f"budget: {reason}"
    rec["odds"].update({"decision": why, "cost_if_called": cost})

    # ---- 3. markets ----
    rows, cov, m_errors, m_body, credits = [], [], [], None, None
    if want and not opts.dry_run:
        try:
            if "odds" in fixtures:
                body, meta = _read_fixture(fixtures["odds"])
            else:
                import localenv
                key = localenv.require("ODDS_API_KEY")
                body, meta, credits = odds.fetch(league, key, opts.markets, opts.regions,
                                                 timeout=opts.timeout)
                bud.record(cost, credits, now)
            rec["odds"]["called"] = True
            if meta.get("http_status") != 200:
                raise C.MalformedPayload(f"HTTP {meta.get('http_status')}: "
                                         f"{body[:200]!r}")
            observed_at = C.parse_utc(meta.get("response_received_at")) or now
            payload, err = _json_or_none(body)
            if err:
                raise C.MalformedPayload(err)
            m_ctx = {"run_id": run_id, "observed_at": C.iso_ms(observed_at),
                     "request_sent_at": meta.get("request_sent_at"),
                     "provider_http_date": meta.get("provider_http_date"),
                     "raw_ref": f"{league}/{date_str}/payloads/{run_id.replace(':', '_')}.odds.json.gz",
                     "raw_sha256": C.sha256_hex(body)}
            rows, cov, m_errors = odds.observe(league, payload, m_ctx,
                                               gamestate.index(games), opts.markets)
            joins, phases = {}, {}
            for r in cov:
                joins[r["join_status"]] = joins.get(r["join_status"], 0) + 1
                phases[r["phase"]] = phases.get(r["phase"], 0) + 1
            rec["odds"].update({"ok": True, "http_status": meta.get("http_status"),
                                "observed_at": m_ctx["observed_at"],
                                "n_events": len(cov), "n_rows": len(rows),
                                "n_errors": len(m_errors), "joins": joins, "phases": phases,
                                "quote_age": odds.quote_age_summary(rows),
                                "credits": credits})
            m_body = body
        except Exception as e:                           # noqa: BLE001
            rec["odds"].update({"ok": False, "error": f"{type(e).__name__}: {e}",
                                "credits": credits})
            rec["errors"].append({"source": "odds", "error": f"{type(e).__name__}: {e}"})
            rows, cov, m_body = [], [], None
    elif want and opts.dry_run:
        rec["odds"].update({"ok": None, "note": f"--dry-run: would spend {cost} credit(s); not called"})
    for e in m_errors:
        rec["errors"].append(dict(e, source="odds-event"))

    rec["budget"] = bud.summary(now)
    rec["finished_at"] = C.iso_ms(C.now_utc())

    if opts.dry_run:
        rec["written"] = {"note": "dry-run: nothing written"}
        return rec

    # ---- 4. append, then record the run ----
    written = {}
    if g_body is not None:
        st.write_payload(league, date_str, run_id, "espn", g_body)
        written["game_observations"] = st.append(league, date_str, "game_observations", games)
    if m_body is not None:
        st.write_payload(league, date_str, run_id, "odds", m_body)
        written["market_observations"] = st.append(league, date_str, "market_observations", rows)
        written["market_coverage"] = st.append(league, date_str, "market_coverage", cov)
    rec["written"] = written
    st.append(league, date_str, "ticks", [rec])
    # The run is marked seen ONLY when some observation was written. A tick
    # whose every source failed leaves its tick record as the audit trail but
    # stays re-runnable inside the same slot, so a transient failure at :00
    # can still be sampled at :20 rather than costing the whole minute.
    if written:
        st.record_run(league, date_str, run_id)
    return rec


def print_tick(rec):
    e, o = rec["espn"], rec["odds"]
    if rec.get("skipped"):
        print(f"[{rec['league']}] {rec['run_id']}: SKIPPED ({rec['skipped']})")
        return
    if e.get("ok"):
        a = e["active"]
        print(f"[{rec['league']}] {rec['run_id']}: espn ok - {e['n_games']} games "
              f"({a['live']} live, {a['starting_soon']} soon, {a['final']} final"
              f"{', ' + str(e['n_errors']) + ' unreadable' if e['n_errors'] else ''})")
    else:
        print(f"[{rec['league']}] {rec['run_id']}: espn FAILED - {e.get('error')}")
    if o.get("called"):
        if o.get("ok"):
            qa = o.get("quote_age") or {}
            print(f"    odds ok - {o['n_events']} events, {o['n_rows']} book-market rows, "
                  f"joins {o['joins']}, phases {o['phases']}, quote age median "
                  f"{qa.get('median_s')}s max {qa.get('max_s')}s, credits "
                  f"{(o.get('credits') or {}).get('remaining')} remaining")
        else:
            print(f"    odds FAILED - {o.get('error')}")
    else:
        print(f"    odds not called: {o.get('decision')}"
              + (f"  ({o['note']})" if o.get("note") else ""))
    if rec.get("written"):
        print(f"    written: {rec['written']}")


# ---------------------------------------------------------------- CLI ---

def add_common(ap):
    ap.add_argument("--leagues", default="nfl,ncaaf")
    ap.add_argument("--markets", default=odds.DEFAULT_MARKETS)
    ap.add_argument("--regions", default=odds.DEFAULT_REGIONS)
    ap.add_argument("--interval", type=int, default=60, help="sampling slot, seconds")
    ap.add_argument("--raw-dir", default=C.RAW_DIR)
    ap.add_argument("--daily-cap", type=int, default=budgetmod.DEFAULT_DAILY_CAP)
    ap.add_argument("--reserve", type=int, default=budgetmod.DEFAULT_RESERVE)
    ap.add_argument("--lookahead-min", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--no-odds", action="store_true", help="game state only; spend nothing")
    ap.add_argument("--always-odds", action="store_true",
                    help="call the odds endpoint even with no live game (costs credits)")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch game state (free), report what WOULD be done, write nothing")
    ap.add_argument("--run-id", default=None, help="override the slot-derived run identity")
    ap.add_argument("--fixture-espn", default=None, help="offline: read the scoreboard from a file")
    ap.add_argument("--fixture-odds", default=None, help="offline: read the odds payload from a file")


def make_store_and_budget(args):
    st = storemod.ObservationStore(args.raw_dir)
    bud = budgetmod.Budget(os.path.join(args.raw_dir, "budget.json"),
                           daily_cap=args.daily_cap, reserve=args.reserve)
    return st, bud


def cmd_once(args):
    st, bud = make_store_and_budget(args)
    fixtures = {k: v for k, v in (("espn", args.fixture_espn), ("odds", args.fixture_odds)) if v}
    rc = 0
    for league in [l for l in args.leagues.split(",") if l.strip()]:
        if league not in gamestate.LEAGUES:
            print(f"unknown league {league!r}; known: {sorted(gamestate.LEAGUES)}")
            return 2
        rec = tick(league, args, st, bud, fixtures=fixtures)
        print_tick(rec)
        if not rec.get("skipped") and (not rec["espn"].get("ok") or rec["odds"].get("ok") is False):
            rc = 1
    return rc


def cmd_loop(args):
    st, bud = make_store_and_budget(args)
    leagues = [l for l in args.leagues.split(",") if l.strip()]
    start = C.now_utc()
    until = C.parse_utc(args.until) if args.until else None
    if until is None:
        until = start + timedelta(minutes=args.max_minutes)
    print(f"loop: {leagues} every {args.interval}s until {C.iso_s(until)} "
          f"(max {args.max_ticks or 'unbounded'} ticks); daily cap {args.daily_cap}, "
          f"reserve {args.reserve}; {'DRY RUN, ' if args.dry_run else ''}"
          f"raw dir {os.path.relpath(args.raw_dir, C.ROOT)}")
    n = 0
    odds_disabled = None
    while True:
        now = C.now_utc()
        if now >= until or (args.max_ticks and n >= args.max_ticks):
            break
        for league in leagues:
            if odds_disabled:
                args.no_odds = True
            rec = tick(league, args, st, bud, now=now)
            print_tick(rec)
            o = rec["odds"]
            if o.get("called") and o.get("ok") is False and (o.get("credits") or {}).get("http_status") in (401, 429):
                odds_disabled = f"odds disabled for the rest of this loop: HTTP {o['credits']['http_status']}"
                print("    " + odds_disabled)
        n += 1
        # sleep to the next slot boundary, never a fixed delay: a slow tick
        # must not drift the sample grid.
        t = time.time()
        wait = args.interval - (t % args.interval)
        if C.now_utc() + timedelta(seconds=wait) > until:
            break
        time.sleep(wait)
    print(f"loop done: {n} tick(s). Seal the day's partitions with `capture.py seal` "
          f"when the session is over (or when the UTC date has rolled).")
    return 0


def cmd_probe(args):
    """Save one raw payload per source so the schema can be checked by eye.
    Writes ONLY into --out-dir; never into the store."""
    os.makedirs(args.out_dir, exist_ok=True)
    stamp = C.now_utc().strftime("%Y%m%dT%H%M%SZ")
    for league in [l for l in args.leagues.split(",") if l.strip()]:
        body, meta = gamestate.fetch(league, timeout=args.timeout)
        p = os.path.join(args.out_dir, f"espn_{league}_{stamp}.json")
        with open(p, "wb") as f:
            f.write(body)
        payload, err = _json_or_none(body)
        n = len(payload.get("events", [])) if payload and not err else "?"
        states = {}
        sit = 0
        if payload and not err:
            for ev in payload.get("events", []):
                s = (((ev.get("competitions") or [{}])[0].get("status") or ev.get("status") or {})
                     .get("type") or {}).get("state")
                states[s] = states.get(s, 0) + 1
                if isinstance((ev.get("competitions") or [{}])[0].get("situation"), dict):
                    sit += 1
        print(f"{league}: espn -> {p} ({n} events, states {states}, {sit} with a situation block)")
        if args.with_odds:
            import localenv
            key = localenv.require("ODDS_API_KEY")
            body, meta, credits = odds.fetch(league, key, args.markets, args.regions,
                                             timeout=args.timeout)
            p = os.path.join(args.out_dir, f"odds_{league}_{stamp}.json")
            with open(p, "wb") as f:
                f.write(body)
            print(f"{league}: odds -> {p} (HTTP {meta['http_status']}, cost "
                  f"{credits.get('last_call_cost')}, {credits.get('remaining')} remaining)")
    print("probe wrote raw payloads only; the store was not touched.")
    return 0


def cmd_seal(args):
    st = storemod.ObservationStore(args.raw_dir)
    s = storemod.seal(st, args.manifest, include_today=args.include_today)
    print(f"sealed: {len(s['added'])} new file(s), {len(s['unchanged'])} unchanged, "
          f"{len(s['disputed'])} DISPUTED; manifest holds {s['n_files']} files "
          f"-> {os.path.relpath(args.manifest, C.ROOT)}")
    for d in s["disputed"]:
        print(f"  DISPUTED (hash differs from the sealed record, original kept): {d}")
    return 1 if s["disputed"] else 0


def cmd_status(args):
    st = storemod.ObservationStore(args.raw_dir)
    bud = budgetmod.Budget(os.path.join(args.raw_dir, "budget.json"),
                           daily_cap=args.daily_cap, reserve=args.reserve)
    print(f"raw dir: {os.path.relpath(args.raw_dir, C.ROOT)}")
    print(f"budget:  {bud.summary(C.now_utc())}")
    print(f"prereg:  frozen: {C.prereg_frozen() or 'NOT YET'}")
    for league, d in st.partitions():
        runs = len(st.runs_seen(league, d))
        counts = {}
        for kind in storemod.KINDS:
            p = st.path_for(league, d, kind)
            counts[kind] = os.path.getsize(p) if os.path.exists(p) else 0
        print(f"  {league} {d}: {runs} runs; bytes {counts}")
    print("burn table (credits) at this interval/markets: "
          f"{budgetmod.burn_table(args.interval, args.markets, args.regions)}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('Run:')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("once", help="one tick per league")
    add_common(p)

    p = sub.add_parser("loop", help="tick every --interval seconds until --until / --max-minutes")
    add_common(p)
    p.add_argument("--until", default=None, help="ISO-8601 UTC stop time")
    p.add_argument("--max-minutes", type=int, default=360)
    p.add_argument("--max-ticks", type=int, default=0)

    p = sub.add_parser("probe", help="save raw provider payloads for inspection (no store writes)")
    p.add_argument("--leagues", default="nfl")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--markets", default=odds.DEFAULT_MARKETS)
    p.add_argument("--regions", default=odds.DEFAULT_REGIONS)
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--with-odds", action="store_true", help="also call the odds endpoint (spends credits)")

    p = sub.add_parser("seal", help="hash closed partitions into the committed manifest")
    p.add_argument("--raw-dir", default=C.RAW_DIR)
    p.add_argument("--manifest", default=C.MANIFEST)
    p.add_argument("--include-today", action="store_true")

    p = sub.add_parser("status", help="what is on disk, the budget, the burn table")
    add_common(p)

    args = ap.parse_args(argv)
    return {"once": cmd_once, "loop": cmd_loop, "probe": cmd_probe,
            "seal": cmd_seal, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
