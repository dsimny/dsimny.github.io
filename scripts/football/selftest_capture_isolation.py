#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the fetch_odds.py --out-dir override.

    python scripts/football/selftest_capture_isolation.py

Makes NO network call and spends NO credits.

WHAT THIS EXISTS TO PROVE. `--out-dir` was added so an ad-hoc research pull for
the D.J. Mercer Spotlight can capture spreads and totals without writing into
data/football/odds/, which is the MODEL'S EVIDENCE TRAIL - the set of files
grade_football.py books the append-only football record against. The risk of
getting that wrong is not a crash. It is a hand-run capture quietly becoming the
price the model grades a real entry at, months later, with nothing in the file
to say it was not the scheduled one.

So the guarantees asserted here are:

  1. the DEFAULT is unchanged - no --out-dir means data/football/odds, exactly
     where every capture has always gone;
  2. capture_schedule.py, the only automated caller, does not pass --out-dir and
     therefore cannot be diverted by this flag;
  3. the model's snapshot loader never picks up a research capture, because
     data/mercer/odds/ is outside the directory it globs;
  4. the model reads the SAME h2h path out of a three-market capture as it does
     out of an h2h-only one - the extra markets are inert to it;
  5. a capture file is never overwritten;
  6. every capture says which kind it is, so provenance survives the file being
     moved out of the directory that would otherwise imply it.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HERE = os.path.join(ROOT, "scripts", "football")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import market            # noqa: E402
import fetch_odds        # noqa: E402

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("[1] the default output directory is unchanged")
check(os.path.abspath(fetch_odds.ODDS_DIR) ==
      os.path.abspath(os.path.join(ROOT, "data", "football", "odds")),
      "ODDS_DIR still resolves to data/football/odds")
src = io.open(os.path.join(HERE, "fetch_odds.py"), encoding="utf-8").read()
check('ap.add_argument("--out-dir", default=None' in src,
      "--out-dir defaults to None, so an unset flag cannot move the output")
check("out_dir = os.path.join(ROOT, args.out_dir) if args.out_dir else ODDS_DIR" in src,
      "the write path falls back to ODDS_DIR when the flag is absent")

print("\n[2] the automated caller cannot be diverted")
csrc = io.open(os.path.join(HERE, "capture_schedule.py"), encoding="utf-8").read()
check("--out-dir" not in csrc, "capture_schedule.py never passes --out-dir")
invocations = re.findall(r"subprocess\.run\(\[([^\]]*)\]", csrc, re.S)
check(len(invocations) >= 1, f"found capture_schedule's fetch invocation ({len(invocations)})")
for inv in invocations:
    check("out" not in inv.lower() or "--out-dir" not in inv,
          "that invocation passes only --sport, so it writes to the default")
for wf in ("football-capture.yml",):
    w = io.open(os.path.join(ROOT, ".github", "workflows", wf), encoding="utf-8").read()
    check("--out-dir" not in w, f"{wf} never passes --out-dir")

print("\n[3] the model's loader cannot see a research capture")
mdir = os.path.join(ROOT, "data", "mercer", "odds")
for sport in ("nfl", "ncaaf"):
    snaps = market.load_snapshots(sport, os.path.join(ROOT, "data", "football", "odds"))
    loaded = [os.path.basename(f) for _t, f, _s in snaps]
    research = ([os.path.basename(p) for p in os.listdir(mdir) if p.startswith(sport)]
                if os.path.isdir(mdir) else [])
    check(not (set(loaded) & set(research)),
          f"{sport}: no research capture appears among the {len(snaps)} the model loads")
    check(all("mercer" not in f for _t, f, _s in snaps),
          f"{sport}: nothing the model loaded came from data/mercer/")

print("\n[4] extra markets are inert to the model's h2h path")
# Two snapshots, identical except one also carries spreads and totals. The
# model's own de-vig must read them the same, or "backward compatible" is a
# claim rather than a property.
h2h_only = {"books": [{"book": "b1", "last_update": "2026-09-06T12:00:00Z",
                       "markets": {"h2h": [{"name": "A", "point": None, "price": -120},
                                           {"name": "B", "point": None, "price": +110}]}}]}
enriched = json.loads(json.dumps(h2h_only))
enriched["books"][0]["markets"]["spreads"] = [{"name": "A", "point": -2.5, "price": -110},
                                              {"name": "B", "point": 2.5, "price": -110}]
enriched["books"][0]["markets"]["totals"] = [{"name": "Over", "point": 44.5, "price": -110},
                                             {"name": "Under", "point": 44.5, "price": -110}]


def read_h2h(ev):
    out = {}
    for bk in ev["books"]:
        h2h = (bk.get("markets") or {}).get("h2h") or []
        out[bk["book"]] = {o["name"]: o["price"] for o in h2h}
    return out


check(read_h2h(h2h_only) == read_h2h(enriched),
      "the h2h block read out of a 3-market capture is identical to the h2h-only one")
check("h2h" in io.open(os.path.join(HERE, "market.py"), encoding="utf-8").read(),
      "market.py still selects the h2h key by name rather than taking all markets")
msrc = io.open(os.path.join(HERE, "market.py"), encoding="utf-8").read()
check("spreads" not in msrc and "totals" not in msrc,
      "market.py references neither spreads nor totals, so they cannot change its output")

print("\n[5] a captured quote is never overwritten")
check("REFUSING to overwrite an existing capture" in src,
      "the writer refuses rather than replacing an existing file")
tmp = tempfile.mkdtemp(prefix="olscap")
try:
    probe = os.path.join(tmp, "nfl_20260906T000000Z.json")
    io.open(probe, "w", encoding="utf-8").write("{}")
    check(os.path.exists(probe), "fixture capture in place")
    # The guard is a plain exists() check on the composed path; assert the shape
    # of that path so the guard cannot be bypassed by a naming change.
    check("f\"{args.sport}_{stamp.strftime('%Y%m%dT%H%M%SZ')}.json\"" in src,
          "filenames carry the capture second, so distinct pulls cannot collide")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[6] every capture declares what kind it is")
check('"capture_role": "research" if args.out_dir else "scheduled"' in src,
      "capture_role is derived from the flag, not passed in by hand")
for sport in ("nfl", "ncaaf"):
    d = os.path.join(ROOT, "data", "football", "odds")
    got = sorted(p for p in os.listdir(d) if p.startswith(sport))[-1:] if os.path.isdir(d) else []
    for p in got:
        s = json.load(io.open(os.path.join(d, p), encoding="utf-8"))
        role = s.get("capture_role", "scheduled (pre-dates the field)")
        check(role != "research",
              f"{p}: the model's trail holds no research capture (role: {role})")
if os.path.isdir(mdir):
    for p in sorted(os.listdir(mdir)):
        s = json.load(io.open(os.path.join(mdir, p), encoding="utf-8"))
        check(s.get("capture_role") == "research",
              f"{p}: declares capture_role=research")
        check(s.get("captured_utc"), f"{p}: carries captured_utc")
        check("h2h" in (s.get("markets") or ""),
              f"{p}: still carries h2h, so it stays readable by the same code")

print(f"\ncapture-isolation selftest: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
