#!/usr/bin/env python3
"""
Open Ledger Sports - ML-1-OPS (mercer-live-ml1-ops-v1.0): compact, value-free
smoke evidence.

    python scripts/mercer_live_ops/evidence.py <store dir>

WHY. The smoke workflow used to `cat` every shard, run record and digest into
the job log and print every observed field value. Workflow logs of a public
repository are readable by ANY signed-in GitHub account (verified 2026-09-27:
anonymous requests get a sign-in wall or a 403, but GitHub's own docs grant
log and artifact access to every signed-in user with read access, which on a
public repository is everyone with an account). Raw observations are private
by design, so the log may carry only what PROVES the capture worked:

  * which fields were present, as counts per field name - never a value;
  * that observations progressed: distinct observed_at, strictly ordered per
    event, and for each field how many consecutive pairs CHANGED - never what
    they changed from or to;
  * run identity: run ids (random uuids, no content), ticks, errors, and the
    duplicates a retry refused;
  * digest shape: shard and run-record counts, and whether lines == parsed.

Every line this prints is built from counts, field NAMES, run ids, record-kind
names, status-state labels (pre/in/post) and booleans. The self-test feeds it
records carrying a canary in every value position and asserts the canary never
appears in the output.
"""
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ML1 = os.path.abspath(os.path.join(HERE, "..", "mercer_live"))
if ML1 not in sys.path:
    sys.path.insert(0, ML1)
import common                                                     # noqa: E402  frozen ML-1, read-only

# Fields whose PRESENCE and MOVEMENT the smoke run exists to evidence. Names
# only; the values stay in the private artifact.
FIELDS = ["status_state", "status_name", "period", "display_clock", "clock_seconds",
          "away_score", "home_score", "possession", "down", "distance", "yard_line",
          "is_red_zone", "home_timeouts", "away_timeouts", "down_distance_text",
          "possession_text", "espn_win_probability_home", "last_play"]
STATES = ("pre", "in", "post")


def _records(store, kind):
    out, bad = [], 0
    for f in sorted(glob.glob(os.path.join(store, "raw", "*", "*", f"{kind}_*.jsonl"))):
        with io.open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    bad += 1
    return out, bad


def _runs(store):
    out = []
    for f in sorted(glob.glob(os.path.join(store, "raw", "runs", "*", "*.json"))):
        try:
            with io.open(f, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except ValueError:
            out.append(None)
    return out


def summarise(store):
    """Lines of compact evidence. Pure: reads the store, returns strings."""
    L = []
    gs, gs_bad = _records(store, "game_state")
    me, me_bad = _records(store, "market_event")
    mq, mq_bad = _records(store, "market_quote")
    L.append(f"records game_state={len(gs)} market_event={len(me)} market_quote={len(mq)} "
             f"unparseable_lines={gs_bad + me_bad + mq_bad}")
    L.append(f"distinct observation_id={len({r.get('observation_id') for r in gs + me + mq})} "
             f"of {len(gs) + len(me) + len(mq)}")
    L.append(f"game_state distinct run_id={len({r.get('run_id') for r in gs})} "
             f"distinct observed_at={len({r.get('observed_at') for r in gs})} "
             f"distinct events={len({r.get('provider_event_id') for r in gs})}")
    hist = {s: sum(1 for r in gs if r.get("status_state") == s) for s in STATES}
    other = len(gs) - sum(hist.values())
    L.append("status_state " + " ".join(f"{s}={hist[s]}" for s in STATES) + f" other/null={other}")
    L.append(f"complete={sum(1 for r in gs if r.get('complete'))} "
             f"missing_required_nonempty={sum(1 for r in gs if r.get('missing_required'))} "
             f"missing_required_nonempty_in_progress="
             f"{sum(1 for r in gs if r.get('missing_required') and r.get('status_state') == 'in')}")
    live = [r for r in gs if r.get("status_state") == "in"]
    for f in FIELDS:
        present_all = sum(1 for r in gs if r.get(f) is not None)
        present_live = sum(1 for r in live if r.get(f) is not None)
        L.append(f"FIELD {f}: present {present_all}/{len(gs)} all, {present_live}/{len(live)} in-progress")
    # Progression, per event, in observed_at order. Only counts leave here.
    by_event = {}
    for r in gs:
        by_event.setdefault(r.get("provider_event_id"), []).append(r)
    ordered = True
    changed = {f: 0 for f in FIELDS}
    pairs = identical = 0
    for rows in by_event.values():
        rows.sort(key=lambda r: str(r.get("observed_at")))
        stamps = [r.get("observed_at") for r in rows]
        if len(set(stamps)) != len(stamps):
            ordered = False
        for a, b in zip(rows, rows[1:]):
            pairs += 1
            diff = [f for f in FIELDS if a.get(f) != b.get(f)]
            for f in diff:
                changed[f] += 1
            if not diff:
                identical += 1
    L.append(f"progression events={len(by_event)} consecutive_pairs={pairs} "
             f"strictly_increasing_observed_at={'yes' if ordered else 'NO'} "
             f"pairs_with_no_field_change={identical}")
    L.append("changed_pairs " + " ".join(f"{f}={changed[f]}" for f in FIELDS))
    if me:
        L.append(f"market_event identity_status resolved={sum(1 for r in me if r.get('identity_status') == 'resolved')} "
                 f"join_status_joined={sum(1 for r in me if r.get('join_status') == 'joined')} "
                 f"no_quotes={sum(1 for r in me if r.get('no_quotes'))}")
    runs = _runs(store)
    good = [r for r in runs if isinstance(r, dict)]
    L.append(f"runs records={len(runs)} unreadable={len(runs) - len(good)} "
             f"distinct run_id={len({r.get('run_id') for r in good})} "
             f"with_errors={sum(1 for r in good if r.get('errors'))} "
             f"errors_total={sum(len(r.get('errors') or []) for r in good)} "
             f"records_written={sum(r.get('records_written', 0) for r in good)} "
             f"duplicates_skipped={sum(r.get('duplicates_skipped', 0) for r in good)} "
             f"odds_calls={sum(len(r.get('odds_calls') or []) for r in good)} "
             f"exit_codes={sorted({r.get('exit_code') for r in good}, key=str)}")
    for r in sorted(good, key=lambda r: str(r.get("started_at"))):
        L.append(f"RUN {r.get('run_id')} written={r.get('records_written')} "
                 f"dup={r.get('duplicates_skipped')} errors={len(r.get('errors') or [])} "
                 f"exit={r.get('exit_code')}")
    for f in sorted(glob.glob(os.path.join(store, "digest", "*.json"))):
        try:
            with io.open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except ValueError:
            L.append(f"DIGEST {os.path.basename(f)} unreadable")
            continue
        sh = d.get("shards") or []
        L.append(f"DIGEST {d.get('et_date')} schema={d.get('schema_version')} shards={len(sh)} "
                 f"runs={d.get('runs')} "
                 f"lines_equal_parsed={'yes' if all(s.get('lines') == s.get('parsed') for s in sh) else 'NO'} "
                 f"kinds={sorted({s.get('kind') for s in sh})}")
    return L


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not os.path.isdir(argv[0]):
        print("usage: evidence.py <store dir>")
        return 2
    for line in summarise(argv[0]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
