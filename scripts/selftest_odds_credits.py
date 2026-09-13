#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for scripts/odds_credits.py, the shared writer of
data/odds_credits.json (record_credits() consolidation, Phase 1, 2026-09-13).

    python scripts/selftest_odds_credits.py

Makes NO network call, spends NO credits, reads NO secret and never writes the
real data/odds_credits.json: every write lands in a temp directory, and [10]
proves the real ledger's bytes did not move.

WHAT THIS EXISTS TO PROVE. Four Odds API callers each carried a private copy of
the ledger append - football/fetch_odds.py, fetch_closing.py, fetch_data.py and
football/fetch_historical_odds.py. Phase 1 replaced the four copies with one
helper and changed NO behaviour, including the behaviours that look wrong.

  [0] the frozen originals are hash-pinned, and the harness can see the
      differences the originals are known to have;
  [1] GOLDEN EQUIVALENCE. The four ORIGINAL functions are frozen at the bottom
      of this file, verbatim from commit 5d90a93. Each caller's record_credits()
      must match its frozen original exactly - ledger bytes, stdout, return
      value, exception, which directories exist - in every GOLDEN case: every
      ledger state in SCENARIOS, every odd CREDIT_LOG_KEEP value (0, negative,
      True, non-integers), and the path boundaries (ROOT or CREDIT_LOG broken,
      CREDIT_LOG pointed somewhere else). ZERO intentional differences;
  [2] the same states asserted by MEANING, so equivalence cannot pass by both
      sides being wrong in the same new way - including the pinned oddities;
  [3] THE TRUNCATION HAZARD, byte-exact: a failure after the ledger is opened
      for writing leaves exactly the chunks json.dump streamed before it failed;
  [4] the helper's own contract;
  [5] wiring: thin wrappers, path boundaries, CREDIT_LOG_KEEP still 60, and the
      football scripts still import cleanly when run as scripts;
  [6]-[9] CALLER CHARACTERISATION, one per fetcher: when the reading is booked
      relative to the request, the status check and the body parse, and which
      clock stamps it. fetch_historical_odds.py books ONCE PER BATCH;
  [10] NEGATIVE CONTROLS: deliberately broken helpers and wrappers must fail [1];
  [11] the suite's own CI gate, .github/workflows/odds-credits-selftest.yml:
       read-only, no secrets, runs only this suite, identical push/PR filters
       that cover this suite's import graph - RE-DERIVED here on every run, so a
       new import cannot silently fall outside the trigger. (That the suite is
       NOT in Football code self-tests is asserted by selftest_workflows.py,
       the suite that runs when that gate's file changes.);
  [12] the real ledger is untouched.

THESE TESTS PIN PHASE 1. THEY DO NOT ENDORSE WHAT THEY PIN. Before- vs
after-response timestamps, batch-level historical booking, a silent NOTE on a
corrupt ledger, the missing-directory split, an unvalidated keep, and the
truncating non-atomic write are all open questions for a later, separate
change - and that change must edit this file deliberately, not route around it.
"""
import ast
import contextlib
import copy
import hashlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

import requests as real_requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SCRIPTS = os.path.join(ROOT, "scripts")
FOOTBALL = os.path.join(SCRIPTS, "football")
sys.path.insert(0, FOOTBALL)
sys.path.insert(0, SCRIPTS)

REAL_LEDGER = os.path.join(ROOT, "data", "odds_credits.json")


def _sha(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


REAL_LEDGER_SHA = _sha(REAL_LEDGER)

_argv = sys.argv
sys.argv = [os.path.join(SCRIPTS, "fetch_data.py")]    # fetch_data reads DATE from argv
try:
    import fetch_closing as fc                          # noqa: E402
    import fetch_data as fd                             # noqa: E402
    import fetch_odds as fo                             # noqa: E402
    import fetch_historical_odds as fho                 # noqa: E402
finally:
    sys.argv = _argv
try:
    import odds_credits as oc                           # noqa: E402
except ImportError:
    oc = None

EXPECTED_CHECKS = 489
fails = []
n = [0]


def check(cond, msg):
    n[0] += 1
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


LEDGER_REL = os.path.join("data", "odds_credits.json")
CALLERS = ("fetch_odds", "fetch_closing", "fetch_data", "fetch_historical_odds")
MODULES = {"fetch_odds": fo, "fetch_closing": fc, "fetch_data": fd,
           "fetch_historical_odds": fho}
PATHS = {"fetch_odds": os.path.join(FOOTBALL, "fetch_odds.py"),
         "fetch_closing": os.path.join(SCRIPTS, "fetch_closing.py"),
         "fetch_data": os.path.join(SCRIPTS, "fetch_data.py"),
         "fetch_historical_odds": os.path.join(FOOTBALL, "fetch_historical_odds.py")}
CREATES_PARENT = {"fetch_odds": True, "fetch_data": True,
                  "fetch_closing": False, "fetch_historical_odds": False}
FD_NOTE = ("The Odds API bills one credit per market per region per call. "
           "Free tier: 500/month. See CLAUDE.md for the budget and the "
           "decision rule for upgrading.")
NOTE_PREFIX = "NOTE: could not record odds credits: "
READING_KEYS = ["remaining", "used", "last_call_cost", "markets", "regions",
                "http_status", "source", "read_utc"]


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


# ---------------------------------------------------------------------------
# implementations under comparison
# ---------------------------------------------------------------------------
def bindings(root, overrides=None):
    """The module globals a record_credits() reads, for a temp ROOT. `overrides`
    values may be callables of the root (for paths under it)."""
    b = {"ROOT": root, "CREDIT_LOG": os.path.join(root, "data", "odds_credits.json"),
         "CREDIT_LOG_KEEP": 60}
    for k, v in (overrides or {}).items():
        b[k] = v(root) if callable(v) else v
    return b


def frozen_fn(name, b):
    """The ORIGINAL record_credits() of `name`, executed against bindings `b`."""
    g = {"__builtins__": __builtins__, "json": json, "os": os, "io": io, **b}
    exec(compile(FROZEN[name], f"<frozen {name} @ {FROZEN_FROM}>", "exec"), g)
    return g["record_credits"]


def live_fn(name, b):
    """The CURRENT record_credits() of `name`, with its module globals patched to
    bindings `b` - only the globals the module actually has. ROOT and CREDIT_LOG
    are both always patched where they exist, so no implementation can reach the
    real ledger."""
    mod = MODULES[name]
    attrs = {k: v for k, v in b.items() if hasattr(mod, k)}

    def call(reading):
        with patched(mod, **attrs):
            return mod.record_credits(reading)
    return call


def reading(i, source="seed"):
    return {"remaining": 100000 - i, "used": i, "last_call_cost": 2,
            "markets": "h2h,totals", "regions": "us", "http_status": 200,
            "source": source, "read_utc": f"2026-09-13T00:{i // 60:02d}:{i % 60:02d}Z"}


NEW = reading(999, "new")
RICH = {**NEW, "extra": {"nested": [1, None, 2.5]}, "flag": False, "zz": None}
UNICODE = {**NEW, "source": "café — \U0001F3C8"}
UNSERIALISABLE = {**NEW, "bad": {1, 2}}


def put(root, obj=None, raw=None):
    d = os.path.join(root, "data")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "odds_credits.json")
    if raw is not None:
        with open(p, "wb") as f:
            f.write(raw)
    elif obj is not None:
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, indent=1))


def seeded(k, **extra):
    return lambda r: put(r, obj={"readings": [reading(i) for i in range(k)], **extra})


def _data_file(r):
    with open(os.path.join(r, "data"), "w") as f:
        f.write("not a directory")


MALFORMED = ("malformed_json", "empty_file", "truncated_json", "utf8_bom",
             "readings_dict", "readings_string", "readings_null", "readings_number",
             "top_level_list", "top_level_string", "top_level_null", "top_level_number")

SCENARIOS = [
    ("fresh", lambda r: put(r), NEW),
    ("three_readings", seeded(3), NEW),
    ("fifty_nine_readings", seeded(59), NEW),
    ("sixty_readings_at_cap", seeded(60), NEW),
    ("seventy_five_over_cap", seeded(75), NEW),
    ("unrelated_top_level_keys",
     lambda r: put(r, obj={"alpha": 1, "readings": [reading(0)],
                           "meta": {"x": [1, 2]}, "zeta": "z"}), NEW),
    ("existing_note", lambda r: put(r, obj={"readings": [reading(0)],
                                            "note": "a hand-written note"}), NEW),
    ("note_before_readings", lambda r: put(r, obj={"note": "first",
                                                   "readings": [reading(0)]}), NEW),
    ("no_readings_key", lambda r: put(r, obj={"other": True}), NEW),
    ("empty_object", lambda r: put(r, raw=b"{}"), NEW),
    ("indent4_existing", lambda r: put(r, raw=json.dumps(
        {"readings": [reading(0)]}, indent=4).encode()), NEW),
    ("crlf_existing", lambda r: put(r, raw=json.dumps(
        {"readings": [reading(0)]}, indent=1).replace("\n", "\r\n").encode()), NEW),
    ("malformed_json", lambda r: put(r, raw=b"{not json"), NEW),
    ("empty_file", lambda r: put(r, raw=b""), NEW),
    ("truncated_json", lambda r: put(r, raw=b'{"readings": [{"used": 1}'), NEW),
    ("utf8_bom", lambda r: put(r, raw=b"\xef\xbb\xbf" + b'{"readings": []}'), NEW),
    ("readings_dict", lambda r: put(r, obj={"readings": {"a": 1}}), NEW),
    ("readings_string", lambda r: put(r, obj={"readings": "abc"}), NEW),
    ("readings_null", lambda r: put(r, obj={"readings": None}), NEW),
    ("readings_number", lambda r: put(r, obj={"readings": 5}), NEW),
    ("top_level_list", lambda r: put(r, raw=b"[1, 2]"), NEW),
    ("top_level_string", lambda r: put(r, raw=b'"text"'), NEW),
    ("top_level_null", lambda r: put(r, raw=b"null"), NEW),
    ("top_level_number", lambda r: put(r, raw=b"42"), NEW),
    ("missing_parent", lambda r: None, NEW),
    ("data_is_a_file", _data_file, NEW),
    ("ledger_is_a_directory",
     lambda r: os.makedirs(os.path.join(r, "data", "odds_credits.json")), NEW),
    ("unknown_reading_fields", seeded(2), RICH),
    ("non_ascii_reading", seeded(1), UNICODE),
    ("minimal_reading", seeded(1), {"used": 1}),
    ("unserialisable_reading", seeded(3), UNSERIALISABLE),
    ("unserialisable_trailing_key",
     lambda r: put(r, obj={"readings": [reading(0)], "meta": {"kept": False}}), UNSERIALISABLE),
]
SCENARIO = {s[0]: s for s in SCENARIOS}

BASE = tempfile.mkdtemp(prefix="olscredits_")
RUN = os.path.join(BASE, "run")          # ONE fixed path, so NOTE text compares exactly


def fresh_run_dir():
    shutil.rmtree(RUN, ignore_errors=True)
    os.makedirs(RUN)
    return RUN


ALT_REL = os.path.join("alt", "credits.json")


def ledger_bytes(root, rel=LEDGER_REL):
    p = os.path.join(root, rel)
    if os.path.isfile(p):
        with open(p, "rb") as f:
            return f.read()
    return "<directory>" if os.path.isdir(p) else None


def run_case(factory, scenario):
    """Run one implementation against one scenario; return everything observable."""
    _, setup, rd = scenario
    root = fresh_run_dir()
    setup(root)
    before = ledger_bytes(root)
    fn = factory(root)
    buf, ret, exc = io.StringIO(), None, None
    with contextlib.redirect_stdout(buf):
        try:
            ret = fn(copy.deepcopy(rd))
        except BaseException as e:                     # noqa: BLE001 - observed, not handled
            exc = f"{type(e).__name__}: {e}"
    return {"ret": ret, "exc": exc, "stdout": buf.getvalue(),
            "ledger": ledger_bytes(root),
            "data_dir": os.path.isdir(os.path.join(root, "data")),
            "alt_ledger": ledger_bytes(root, ALT_REL),
            "alt_dir": os.path.isdir(os.path.join(root, "alt")),
            "_before": before}


def load(out):
    return json.loads(out["ledger"].decode("utf-8"))


def _alt(root):
    return os.path.join(root, ALT_REL)


def _with_alt_dir(setup):
    def s(root):
        setup(root)
        os.makedirs(os.path.join(root, "alt"))
    return s


THREE_WITH_ALT_DIR = ("three_readings+alt_dir", _with_alt_dir(seeded(3)), NEW)
ODD_KEEPS = (0, -1, 1, True, 2.5, "60", None)
HAS_KEEP = ("fetch_odds", "fetch_closing", "fetch_data")      # historical hardcoded 60
PATH_FROM_ROOT = ("fetch_closing", "fetch_data")             # joined OUTSIDE the old try
PATH_FROM_CREDIT_LOG = ("fetch_odds", "fetch_historical_odds")  # read INSIDE the old try


def golden_cases(name):
    """Every (label, scenario, module-global overrides) the caller is compared on."""
    cases = [(sc[0], sc, {}) for sc in SCENARIOS]
    if name in HAS_KEEP:
        for k in ODD_KEEPS:
            for scn in ("seventy_five_over_cap", "fresh", "malformed_json"):
                cases.append((f"{scn} CREDIT_LOG_KEEP={k!r}", SCENARIO[scn],
                              {"CREDIT_LOG_KEEP": k}))
    cases += [
        ("ROOT=None", SCENARIO["fresh"], {"ROOT": None}),
        ("CREDIT_LOG=None", SCENARIO["fresh"], {"CREDIT_LOG": None}),
        ("ROOT=None, CREDIT_LOG=None", SCENARIO["fresh"], {"ROOT": None, "CREDIT_LOG": None}),
        ("CREDIT_LOG elsewhere", THREE_WITH_ALT_DIR, {"CREDIT_LOG": _alt}),
        ("CREDIT_LOG elsewhere, no parent", SCENARIO["three_readings"], {"CREDIT_LOG": _alt}),
    ]
    return cases


def compare(name, case, impl=None):
    label, sc, ov = case
    a = run_case(lambda r: frozen_fn(name, bindings(r, ov)), sc)
    b = run_case(lambda r: (impl or live_fn)(name, bindings(r, ov)), sc)
    return [k for k in a if a[k] != b[k]]


def golden_mismatches(name, impl=None):
    return [c[0] for c in golden_cases(name) if compare(name, c, impl)]


def main():
    try:
        sections()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"\n{n[0]} checks, {len(fails)} failed")
    if EXPECTED_CHECKS is not None and n[0] != EXPECTED_CHECKS:
        print(f"FAIL: expected {EXPECTED_CHECKS} checks, ran {n[0]} - a section was "
              f"skipped or added without updating EXPECTED_CHECKS")
        return 1
    return 1 if fails else 0


def sections():
    # ---- 0. the frozen originals are the originals -------------------------
    print(f"\n[0] the frozen references are byte-pinned to {FROZEN_FROM}")
    for name in CALLERS:
        check(hashlib.sha256(FROZEN[name].encode("utf-8")).hexdigest() == FROZEN_SHA256[name],
              f"{name}: frozen original unchanged since it was captured")
    fz = lambda nm, sc, ov=None: run_case(lambda r: frozen_fn(nm, bindings(r, ov)), SCENARIO[sc])  # noqa: E731
    check(fz("fetch_data", "fresh")["ledger"] != fz("fetch_closing", "fresh")["ledger"]
          and fz("fetch_odds", "missing_parent")["ledger"]
          != fz("fetch_closing", "missing_parent")["ledger"]
          and fz("fetch_odds", "fresh", {"ROOT": None})["exc"]
          != fz("fetch_closing", "fresh", {"ROOT": None})["exc"],
          "harness sensitivity: the frozen originals DO differ where they are known to "
          "(fetch_data's note; the missing-directory split; the path boundary), so "
          "equality below means something")

    # ---- 1. golden equivalence ---------------------------------------------
    print("\n[1] golden equivalence: each caller's record_credits() vs its frozen original")
    total = 0
    for name in CALLERS:
        for case in golden_cases(name):
            diff = compare(name, case)
            total += 1
            check(not diff, f"{name:<22} {case[0]:<40} identical"
                  + (f"  (differs in {diff})" if diff else ""))
    print(f"  ({total} golden comparisons)")

    # ---- 2. the same states, by meaning -------------------------------------
    print("\n[2] the same states asserted by meaning, per caller")
    for name in CALLERS:
        meaning(name)

    # ---- 3. the truncation hazard --------------------------------------------
    print("\n[3] KNOWN HAZARD, PINNED NOT ENDORSED: a failure after the ledger is opened "
          "for writing truncates it")
    check(oc is not None, "scripts/odds_credits.py imports")
    hazard()

    # ---- 4. the helper's contract --------------------------------------------
    print("\n[4] scripts/odds_credits.py contract")
    if oc is not None:
        helper_contract()

    # ---- 5. wiring -----------------------------------------------------------
    print("\n[5] wiring: thin wrappers, path boundaries, unchanged constants, import paths")
    wiring()

    # ---- 6-9. caller characterisation ----------------------------------------
    print("\n[6] fetch_odds.py: booked BEFORE the status check and the body parse, "
          "stamped BEFORE the request")
    characterise_fetch_odds()
    print("\n[7] fetch_closing.py: booked BEFORE raise_for_status()/r.json(), "
          "stamped AFTER the response")
    characterise_fetch_closing()
    print("\n[8] fetch_data.py: credits filled before raise_for_status; booked after the "
          "try/except, then the low-credit alert")
    characterise_fetch_data()
    print("\n[9] fetch_historical_odds.py: non-200 books that call; success books ONCE "
          "per batch; early exits book nothing")
    characterise_historical()

    # ---- 10. negative controls -----------------------------------------------
    print("\n[10] negative controls: deliberately broken helpers and wrappers must fail [1]")
    if oc is not None:
        negative_controls()
    else:
        check(False, "negative controls need scripts/odds_credits.py")

    # ---- 11. this suite's own CI gate -----------------------------------------
    print("\n[11] the Odds credit self-tests workflow: offline, read-only, and "
          "triggered by the suite's whole import graph")
    ci_contract()

    # ---- 12. the real ledger --------------------------------------------------
    print("\n[12] the real ledger")
    check(_sha(REAL_LEDGER) == REAL_LEDGER_SHA,
          "data/odds_credits.json is byte-for-byte what it was before this test ran")


def meaning(name):
    def L(sc, ov=None):
        scenario = sc if isinstance(sc, tuple) else SCENARIO[sc]
        return run_case(lambda r: live_fn(name, bindings(r, ov)), scenario)
    is_fd = name == "fetch_data"
    tail = {"note": FD_NOTE} if is_fd else {}

    o = L("fresh")
    check(o["exc"] is None and o["ret"] is None and o["stdout"] == ""
          and load(o) == {"readings": [NEW], **tail}
          and list(load(o)) == ["readings", *tail],
          f"{name}: fresh ledger -> exactly one reading" + (", then the note" if is_fd else ""))
    o = L("three_readings")
    check(load(o)["readings"] == [reading(i) for i in range(3)] + [NEW],
          f"{name}: 3 readings -> 4, appended last")
    o = L("sixty_readings_at_cap")
    rs = load(o)["readings"]
    check(len(rs) == 60 and rs[0] == reading(1) and rs[-1] == NEW,
          f"{name}: the 61st reading evicts the oldest; the cap stays 60")
    o = L("seventy_five_over_cap")
    rs = load(o)["readings"]
    check(len(rs) == 60 and rs[0] == reading(16) and rs[-1] == NEW,
          f"{name}: an over-cap ledger is trimmed to the newest 60")
    o = L("unrelated_top_level_keys")
    lg = load(o)
    check(list(lg) == ["alpha", "readings", "meta", "zeta", *tail]
          and lg["alpha"] == 1 and lg["meta"] == {"x": [1, 2]} and lg["zeta"] == "z",
          f"{name}: unrelated top-level keys survive, in their original order")
    o = L("existing_note")
    check(load(o)["note"] == (FD_NOTE if is_fd else "a hand-written note"),
          f"{name}: an existing note is " + ("OVERWRITTEN with the fixed note (as before)"
                                             if is_fd else "left alone"))
    o = L("note_before_readings")
    check(list(load(o)) == ["note", "readings"],
          f"{name}: key order is preserved when the note comes first")
    for sc in MALFORMED:
        o = L(sc)
        check(o["exc"] is None and o["ledger"] == o["_before"]
              and o["stdout"].startswith(NOTE_PREFIX) and o["stdout"].count("\n") == 1,
              f"{name}: {sc} -> no exception, NO WRITE, existing bytes preserved, one NOTE line")
    o = L("missing_parent")
    if CREATES_PARENT[name]:
        check(o["exc"] is None and o["data_dir"] and load(o)["readings"] == [NEW],
              f"{name}: missing data/ -> created, reading written (as before)")
    else:
        check(o["exc"] is None and not o["data_dir"] and o["ledger"] is None
              and o["stdout"].startswith(NOTE_PREFIX),
              f"{name}: missing data/ -> NOT created, reading dropped with a NOTE (as before)")
    o = L("unknown_reading_fields")
    check(load(o)["readings"][-1] == RICH, f"{name}: unknown reading fields preserved exactly")
    o = L("minimal_reading")
    check(load(o)["readings"][-1] == {"used": 1},
          f"{name}: the writer adds no fields of its own to a reading")
    o = L("three_readings")
    expect = {"readings": [reading(i) for i in range(3)] + [NEW], **tail}
    check(o["ledger"].decode("utf-8").replace("\r\n", "\n") == json.dumps(expect, indent=1),
          f"{name}: formatting is json.dump(..., indent=1)")
    o = L("non_ascii_reading")
    check(b"\\u00e9" in o["ledger"] and "é".encode("utf-8") not in o["ledger"],
          f"{name}: non-ASCII stays \\u-escaped (ensure_ascii default)")

    # CREDIT_LOG_KEEP is used unvalidated, as it always was. PINNED, NOT ENDORSED.
    if name in HAS_KEEP:
        o = L("seventy_five_over_cap", {"CREDIT_LOG_KEEP": 0})
        check(len(load(o)["readings"]) == 76,
              f"{name}: PINNED - CREDIT_LOG_KEEP=0 keeps EVERYTHING ([-0:] is the whole list)")
        o = L("seventy_five_over_cap", {"CREDIT_LOG_KEEP": -1})
        rs = load(o)["readings"]
        check(len(rs) == 75 and rs[0] == reading(1) and rs[-1] == NEW,
              f"{name}: PINNED - CREDIT_LOG_KEEP=-1 drops only the oldest reading ([1:])")
        o = L("seventy_five_over_cap", {"CREDIT_LOG_KEEP": True})
        check(load(o)["readings"] == [NEW],
              f"{name}: PINNED - CREDIT_LOG_KEEP=True keeps one reading (True == 1)")
        for k in (2.5, "60", None):
            o = L("seventy_five_over_cap", {"CREDIT_LOG_KEEP": k})
            check(o["exc"] is None and o["ledger"] == o["_before"]
                  and o["stdout"].startswith(NOTE_PREFIX),
                  f"{name}: PINNED - CREDIT_LOG_KEEP={k!r} fails with a NOTE, no write")

    # The path boundary: where a broken path fails depends on where the original
    # built it, and that is preserved per caller.
    o = L("fresh", {"ROOT": None, "CREDIT_LOG": None})
    if name in PATH_FROM_ROOT:
        check(o["exc"] is not None and o["exc"].startswith("TypeError") and o["stdout"] == "",
              f"{name}: a broken ROOT RAISES out of record_credits() - the original built "
              f"the path before its try, and so does the wrapper")
    else:
        check(o["exc"] is None and o["stdout"].startswith(NOTE_PREFIX) and o["ledger"] is None,
              f"{name}: a broken CREDIT_LOG is caught (NOTE, no write) - the original read it "
              f"inside its try, and so does the helper")
    o = L(THREE_WITH_ALT_DIR, {"CREDIT_LOG": _alt})
    if name in PATH_FROM_CREDIT_LOG:
        check(o["alt_ledger"] is not None and json.loads(o["alt_ledger"]) == {"readings": [NEW]}
              and o["ledger"] == o["_before"],
              f"{name}: CREDIT_LOG CONTROLS THE PATH - pointed elsewhere, the reading goes "
              f"there and data/odds_credits.json is untouched")
    else:
        check(o["alt_ledger"] is None and load(o)["readings"][-1] == NEW,
              f"{name}: has no CREDIT_LOG; the path comes from ROOT, as before")


def streamed_prefix(log):
    """What json.dump(log, f, indent=1) has written by the time serialisation fails:
    every chunk the encoder yielded before it raised. With indent set, json.dump
    uses exactly this pure-Python iterencode and writes each chunk as it comes."""
    out = []
    try:
        for chunk in json.JSONEncoder(indent=1).iterencode(log):
            out.append(chunk)
    except TypeError:
        return "".join(out)
    raise AssertionError("that log serialises; there is no truncation to predict")


def _text(ledger):
    return ledger.decode("utf-8").replace("\r\n", "\n") if isinstance(ledger, bytes) else None


HAZARD_NOTE = NOTE_PREFIX + "Object of type set is not JSON serializable\n"


def hazard():
    logs = {
        "unserialisable_reading":
            lambda: {"readings": [reading(i) for i in range(3)] + [UNSERIALISABLE]},
        "unserialisable_trailing_key":
            lambda: {"readings": [reading(0), UNSERIALISABLE], "meta": {"kept": False}},
    }
    for name in CALLERS:
        tail = {"note": FD_NOTE} if name == "fetch_data" else {}
        for scn, logf in logs.items():
            o = run_case(lambda r: live_fn(name, bindings(r)), SCENARIO[scn])
            expect = streamed_prefix({**logf(), **tail})
            check(o["exc"] is None and o["ret"] is None and o["stdout"] == HAZARD_NOTE
                  and _text(o["ledger"]) == expect,
                  f"{name}: {scn} -> one NOTE, no exception, and the ledger now holds EXACTLY "
                  f"the {len(expect)} characters json.dump streamed before it failed")
    if oc is None:
        return
    o = run_case(lambda r: (lambda x: oc.record(x, path=os.path.join(r, LEDGER_REL))),
                 SCENARIO["unserialisable_trailing_key"])
    text, before = _text(o["ledger"]), _text(o["_before"])
    check(o["ret"] is False and o["exc"] is None and o["stdout"] == HAZARD_NOTE,
          "record() returns False and does not raise")
    check(text is not None and text != before and text.startswith('{\n "readings": [\n  {'),
          "the previous bytes are GONE: open(path, 'w') emptied the file before json.dump ran")
    parses = True
    try:
        json.loads(text)
    except ValueError:
        parses = False
    check(not parses, "what is left is not valid JSON")
    check('"source": "seed"' in text and '"bad": ' in text,
          "readings streamed before the failure survive as text, cut off inside the bad reading")
    check('"meta"' in before and '"meta"' not in text,
          "every top-level key after the failure point is lost")
    o = run_case(lambda r: (lambda x: oc.record(x, path=os.path.join(r, LEDGER_REL))),
                 SCENARIO["malformed_json"])
    check(o["ret"] is False and o["ledger"] == o["_before"],
          "contrast: an UNREADABLE existing ledger fails BEFORE the open for writing, so "
          "its bytes are preserved")


def helper_contract():
    sig = inspect.signature(oc.record)
    ps = sig.parameters
    check(list(ps) == ["reading", "path", "note", "keep", "create_parent"]
          and ps["reading"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
          and all(ps[k].kind == inspect.Parameter.KEYWORD_ONLY
                  for k in ("path", "note", "keep", "create_parent"))
          and ps["path"].default is inspect.Parameter.empty
          and ps["note"].default is None and ps["keep"].default == 60
          and ps["create_parent"].default is False,
          f"signature is record(reading, *, path, note=None, keep=KEEP, "
          f"create_parent=False) {sig}")
    check(oc.KEEP == 60, "KEEP == 60")
    check(sorted(k for k in vars(oc) if not k.startswith("_")) == ["KEEP", "json", "os", "record"],
          "the module exposes KEEP and record() and nothing else")

    def Hc(scenario, **kw):
        sc = scenario if isinstance(scenario, tuple) else SCENARIO[scenario]
        return run_case(lambda r: (lambda x: oc.record(x, path=os.path.join(r, LEDGER_REL), **kw)),
                        sc)

    o = Hc("fresh")
    check(o["ret"] is True and load(o) == {"readings": [NEW]}, "a successful write returns True")
    o = Hc("malformed_json")
    check(o["ret"] is False and o["ledger"] == o["_before"]
          and o["stdout"].startswith(NOTE_PREFIX),
          "an unreadable existing ledger returns False, is not written, prints the NOTE")
    o = Hc("top_level_list")
    check(o["ret"] is False and o["ledger"] == o["_before"], "a non-object ledger returns False")
    o = Hc("readings_dict")
    check(o["ret"] is False and o["ledger"] == o["_before"],
          "a non-list 'readings' returns False")
    o = Hc("missing_parent")
    check(o["ret"] is False and not o["data_dir"],
          "create_parent=False: no directory made, returns False")
    o = Hc("missing_parent", create_parent=True)
    check(o["ret"] is True and o["data_dir"] and load(o) == {"readings": [NEW]},
          "create_parent=True: directory created, returns True")
    o = Hc(("five", seeded(5), NEW), keep=3)
    check(o["ret"] is True and load(o)["readings"] == [reading(3), reading(4), NEW],
          "keep=N keeps the newest N")
    o = Hc("seventy_five_over_cap", keep=0)
    check(o["ret"] is True and len(load(o)["readings"]) == 76,
          "PINNED - keep=0 keeps everything (unvalidated, as the originals were)")
    o = Hc("seventy_five_over_cap", keep=-1)
    check(o["ret"] is True and len(load(o)["readings"]) == 75,
          "PINNED - keep=-1 drops only the oldest")
    o = Hc("seventy_five_over_cap", keep=True)
    check(o["ret"] is True and load(o)["readings"] == [NEW], "PINNED - keep=True keeps one")
    for k in (2.5, "60", None):
        o = Hc("seventy_five_over_cap", keep=k)
        check(o["ret"] is False and o["exc"] is None and o["ledger"] == o["_before"]
              and o["stdout"].startswith(NOTE_PREFIX),
              f"PINNED - keep={k!r} is a failure: NOTE, no write, no raise")
    o = Hc("existing_note")
    check(load(o)["note"] == "a hand-written note", "note=None leaves an existing note alone")
    o = Hc("existing_note", note="")
    check(load(o)["note"] == "", "note='' is still written: only None means 'leave it'")
    o = run_case(lambda r: (lambda x: oc.record(x, path=None)), SCENARIO["fresh"])
    check(o["ret"] is False and o["exc"] is None and o["stdout"].startswith(NOTE_PREFIX),
          "path=None is caught inside the helper (NOTE, False) - where CREDIT_LOG-reading "
          "originals caught it")
    o = run_case(lambda r: (lambda x: oc.record(x, path=_alt(r))), THREE_WITH_ALT_DIR)
    check(o["ret"] is True and json.loads(o["alt_ledger"]) == {"readings": [NEW]}
          and o["ledger"] == o["_before"],
          "the path argument alone decides where the ledger is written")


def _record_credits_node(name):
    with open(PATHS[name], encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "record_credits":
            return node
    return None


def _kw_of(name, kwname):
    node = _record_credits_node(name)
    for c in (ast.walk(node) if node else []):
        if isinstance(c, ast.Call):
            for k in c.keywords:
                if k.arg == kwname:
                    return k.value
    return None


def wiring():
    for name in CALLERS:
        mod = MODULES[name]
        node = _record_credits_node(name)
        want_arg = "entry" if name == "fetch_historical_odds" else "credits"
        check(node is not None and [a.arg for a in node.args.args] == [want_arg],
              f"{name}: record_credits({want_arg}) still exists under its original name")
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)] if node else []
        names = sorted(ast.unparse(c.func) for c in calls)
        want_names = sorted(["odds_credits.record"]
                            + (["os.path.join"] if name in PATH_FROM_ROOT else []))
        check(names == want_names,
              f"{name}: record_credits() calls only {want_names} - no open/json.dump/"
              f"makedirs of its own {names}")
        check(node is not None and not any(isinstance(x, ast.Try) for x in ast.walk(node)),
              f"{name}: the wrapper has no try of its own - it catches nothing the "
              f"original did not")
        kw = {k.arg: ast.unparse(k.value) for c in calls
              if ast.unparse(c.func) == "odds_credits.record" for k in c.keywords}
        want_cp = "True" if CREATES_PARENT[name] else None
        check(kw.get("create_parent") == want_cp,
              f"{name}: {'create_parent=True' if want_cp else 'no create_parent (default False)'}"
              f" {kw}")
        if name in PATH_FROM_CREDIT_LOG:
            check(kw.get("path") == "CREDIT_LOG"
                  and MODULES[name].CREDIT_LOG == os.path.join(MODULES[name].ROOT, "data",
                                                               "odds_credits.json"),
                  f"{name}: path=CREDIT_LOG - the constant still decides the path, and still "
                  f"names <ROOT>/data/odds_credits.json")
        else:
            assigns = [x for x in node.body if isinstance(x, ast.Assign)] if node else []
            check(len(assigns) == 1 and ast.unparse(assigns[0].targets[0]) == "path"
                  and ast.unparse(assigns[0].value)
                  == "os.path.join(ROOT, 'data', 'odds_credits.json')"
                  and node.body.index(assigns[0]) < len(node.body) - 1
                  and kw.get("path") == "path"
                  and not hasattr(MODULES[name], "CREDIT_LOG"),
                  f"{name}: builds path from ROOT in the wrapper, BEFORE the helper's try, "
                  f"exactly as the original built it before its own; no CREDIT_LOG invented")
        check(("note" in kw) == (name == "fetch_data"),
              f"{name}: {'passes' if name == 'fetch_data' else 'does not pass'} a note")
        rets = [r for r in ast.walk(node) if isinstance(r, ast.Return)] if node else []
        check(node is not None and not rets,
              f"{name}: the wrapper still returns None, as the original did")
        check(oc is not None and getattr(mod, "odds_credits", None) is oc
              and os.path.normcase(os.path.abspath(oc.__file__))
              == os.path.normcase(os.path.join(SCRIPTS, "odds_credits.py")),
              f"{name}: its odds_credits is scripts/odds_credits.py")
    for name in ("fetch_odds", "fetch_closing", "fetch_data"):
        check(getattr(MODULES[name], "CREDIT_LOG_KEEP", None) == 60,
              f"{name}: CREDIT_LOG_KEEP is still 60")
    keeps = {nm: _kw_of(nm, "keep") for nm in CALLERS}
    check(all(keeps[nm] is not None and ast.unparse(keeps[nm]) == "CREDIT_LOG_KEEP"
              for nm in ("fetch_odds", "fetch_closing", "fetch_data"))
          and keeps["fetch_historical_odds"] is None,
          "the three modules with CREDIT_LOG_KEEP pass it as keep=; fetch_historical_odds "
          "(which hardcoded 60) takes the helper's KEEP=60")
    fd_note = _kw_of("fetch_data", "note")
    check(fd_note is not None and ast.unparse(fd_note) == "CREDIT_LOG_NOTE"
          and getattr(fd, "CREDIT_LOG_NOTE", None) == FD_NOTE,
          "fetch_data passes its fixed note text, character for character")

    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "ODDS_API_KEY", "BOARD_ENCRYPTION_KEY")}
    cwd = tempfile.mkdtemp(prefix="olscredits_cwd_")
    try:
        r = subprocess.run([sys.executable, os.path.join(FOOTBALL, "fetch_odds.py"),
                            "--sport", "preseason", "--dry-run"],
                           cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
        check(r.returncode == 0 and "--dry-run: no call made" in r.stdout,
              "fetch_odds.py still runs AS A SCRIPT from a foreign cwd (--dry-run, no call)"
              + ("" if r.returncode == 0 else f": {r.stderr[-300:]}"))
        probe = (
            "import os, sys\n"
            f"sys.path[0] = {FOOTBALL!r}\n"
            "import fetch_odds, fetch_historical_odds, teams, localenv, asof\n"
            "norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(p)))\n"
            "print(norm(fetch_odds.odds_credits.__file__))\n"
            "print(norm(fetch_historical_odds.odds_credits.__file__))\n"
            "print(all(norm(os.path.dirname(m.__file__)) == norm(sys.path[0]) "
            "for m in (teams, localenv, asof)))\n"
            "entries = [norm(p) for p in sys.path]\n"
            f"print(entries.index(norm({FOOTBALL!r})) < entries.index(norm({SCRIPTS!r})))\n")
        r = subprocess.run([sys.executable, "-c", probe], cwd=cwd, env=env,
                           capture_output=True, text=True, timeout=120)
        lines = r.stdout.split()
        want = os.path.normcase(os.path.join(SCRIPTS, "odds_credits.py"))
        check(r.returncode == 0 and lines[:2] == [want, want],
              "with only scripts/football on sys.path (as when run as a script), both football "
              "fetchers resolve scripts/odds_credits.py"
              + ("" if r.returncode == 0 else f": {r.stderr[-300:]}"))
        check(lines[2:4] == ["True", "True"],
              "scripts/ is added AFTER scripts/football: no football module is shadowed")
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


# ---------------------------------------------------------------------------
# fakes for characterisation
# ---------------------------------------------------------------------------
class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)

    def tick(self, s):
        self.t += timedelta(seconds=s)


def fake_datetime(clock):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.t if tz is not None else clock.t.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return clock.t.replace(tzinfo=None)
    return FakeDT


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def H(rem, used=None, last=None):
    h = {"x-requests-remaining": str(rem)} if rem is not None else {}
    if used is not None:
        h["x-requests-used"] = str(used)
    if last is not None:
        h["x-requests-last"] = str(last)
    return h


class Resp:
    def __init__(self, status=200, body=None, headers=None, bad_json=False):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.bad_json, self.text = bad_json, "selftest body"
        self.json_calls, self.rfs_calls = 0, 0
        self.on_json = self.on_rfs = None

    def json(self):
        self.json_calls += 1
        if self.on_json:
            self.on_json()
        if self.bad_json:
            raise ValueError("selftest: body is not JSON")
        return self._body

    def raise_for_status(self):
        self.rfs_calls += 1
        if self.on_rfs:
            self.on_rfs()
        if self.status_code >= 400:
            raise real_requests.HTTPError(f"selftest HTTP {self.status_code}")


class FakeRequests:
    RequestException = real_requests.RequestException
    ConnectionError = real_requests.ConnectionError
    HTTPError = real_requests.HTTPError

    def __init__(self, script, clock, tick=5, on_call=None):
        self.script, self.clock, self.step, self.on_call = list(script), clock, tick, on_call
        self.calls = []

    def get(self, url, params=None, timeout=None):
        if "the-odds-api.com" not in url:
            raise AssertionError(f"unexpected network call: {url}")
        if self.on_call:
            self.on_call()
        self.calls.append(dict(params or {}))
        self.clock.tick(self.step)             # the response arrives LATER than the request
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def readings_in(root):
    b = ledger_bytes(root)
    if not isinstance(b, bytes):
        return None
    return json.loads(b.decode("utf-8"))["readings"]


def tmp_root(with_data=True):
    d = tempfile.mkdtemp(prefix="char_", dir=BASE)
    if with_data:
        os.makedirs(os.path.join(d, "data"))
    return d


STUB_LOCALENV = types.SimpleNamespace(require=lambda name: "selftest-not-a-key",
                                      fingerprint=lambda v: "selftest",
                                      load=lambda **k: [])


def characterise_fetch_odds():
    def run(script, root, argv=("--sport", "nfl")):
        clock = Clock()
        fake = FakeRequests(script, clock)
        buf = io.StringIO()
        with patched(fo, ROOT=root, CREDIT_LOG=os.path.join(root, LEDGER_REL),
                     ODDS_DIR=os.path.join(root, "data", "football", "odds"),
                     requests=fake, datetime=fake_datetime(clock), localenv=STUB_LOCALENV), \
                patched(sys, argv=["fetch_odds.py", *argv]), contextlib.redirect_stdout(buf):
            t0 = clock.t
            ret = fo.main()
        return ret, fake, t0, clock, buf.getvalue()

    root = tmp_root()
    resp = Resp(401, headers=H(0, 500, 1))
    ret, fake, t0, clock, _ = run([resp], root)
    rs = readings_in(root)
    check(ret == 1 and rs is not None and len(rs) == 1 and rs[0] == {
        "remaining": 0, "used": 500, "last_call_cost": 1, "markets": "h2h", "regions": "us",
        "http_status": 401, "source": "football_fetch_odds:nfl", "read_utc": iso(t0)}
          and list(rs[0]) == READING_KEYS,
          "a 401 is booked (the reading that says the key died): exact 8-field reading")
    check(rs is not None and rs[0]["read_utc"] == iso(t0) and iso(clock.t) != iso(t0),
          "read_utc is the moment BEFORE the request, not when the response arrived")
    check(resp.json_calls == 0, "a non-200 body is never parsed")

    root = tmp_root()
    seen = []
    resp = Resp(200, headers=H(900, 100, 1), bad_json=True)
    resp.on_json = lambda: seen.append(len(readings_in(root) or []))
    ret, *_ = run([resp], root)
    check(ret == 1 and seen == [1] and len(readings_in(root) or []) == 1,
          "a garbled 200 body: the reading was ALREADY booked when r.json() was called")

    root = tmp_root()
    seen = []
    resp = Resp(200, body=[{"away_team": "Nowhere Nobodies", "home_team": "Also Nobody"}],
                headers=H(898, 102, 1))
    resp.on_json = lambda: seen.append(len(readings_in(root) or []))
    ret, *_ = run([resp], root)
    check(ret == 1 and seen == [1] and len(readings_in(root) or []) == 1
          and not os.path.exists(os.path.join(root, "data", "football", "odds")),
          "an unmatched-team abort still leaves the reading booked, and writes no snapshot")

    root = tmp_root()
    ret, *_ = run([Resp(200, body=[], headers=H(897, 103, 1))], root)
    rs = readings_in(root)
    check(ret == 0 and rs is not None and len(rs) == 1 and rs[0]["http_status"] == 200,
          "an empty 200 (no events) is booked")

    root = tmp_root()
    ret, *_ = run([Resp(200, body=[], headers=H(896, 104, 1)),
                   Resp(200, body=[], headers=H(895, 105, 1))], root)
    ret2, *_ = run([Resp(200, body=[], headers=H(895, 105, 1))], root)
    check(len(readings_in(root) or []) == 2, "two runs, two calls -> exactly two readings")

    root = tmp_root()
    ret, *_ = run([real_requests.ConnectionError("selftest reset")], root)
    check(ret == 1 and readings_in(root) is None,
          "a network failure (no response, no headers) books nothing")

    root = tmp_root(with_data=False)
    ret, *_ = run([Resp(200, body=[], headers=H(10, 1, 1))], root)
    check(ret == 0 and readings_in(root) is not None,
          "no data/ directory: fetch_odds CREATES it and books the reading (as before)")


def characterise_fetch_closing():
    def run(script, root):
        clock = Clock()
        fake = FakeRequests(script, clock)
        buf, exc, out = io.StringIO(), None, None
        with patched(fc, ROOT=root, requests=fake, datetime=fake_datetime(clock)), \
                contextlib.redirect_stdout(buf):
            try:
                out = fc.fetch_market_odds([], {}, "selftest-not-a-key")
            except BaseException as e:           # noqa: BLE001
                exc = e
        return out, exc, clock, buf.getvalue()

    root = tmp_root()
    seen = []
    resp = Resp(429, headers=H(0, 20000, 2))
    resp.on_rfs = lambda: seen.append(len(readings_in(root) or []))
    out, exc, clock, _ = run([resp], root)
    rs = readings_in(root)
    check(isinstance(exc, real_requests.HTTPError) and seen == [1] and resp.json_calls == 0,
          "a 429 is booked BEFORE raise_for_status() raises; the body is never parsed")
    check(rs is not None and rs[0] == {
        "remaining": 0, "used": 20000, "last_call_cost": 2, "markets": fc.ODDS_MARKETS,
        "regions": fc.ODDS_REGIONS, "http_status": 429, "source": "fetch_closing",
        "read_utc": iso(clock.t)} and list(rs[0]) == READING_KEYS,
          "exact 8-field reading, read_utc stamped AFTER the response arrived")

    root = tmp_root()
    seen = []
    resp = Resp(200, headers=H(5, 1, 2), bad_json=True)
    resp.on_json = lambda: seen.append(len(readings_in(root) or []))
    out, exc, clock, _ = run([resp], root)
    check(isinstance(exc, ValueError) and seen == [1] and len(readings_in(root) or []) == 1,
          "a garbled 200: booked BEFORE r.json() raised")

    root = tmp_root()
    out, exc, clock, _ = run([Resp(200, body=[], headers=H(5, 1, 2))], root)
    check(exc is None and out == {} and len(readings_in(root) or []) == 1, "an empty 200 is booked")

    root = tmp_root()
    out, exc, clock, _ = run([real_requests.ConnectionError("selftest reset")], root)
    check(isinstance(exc, real_requests.ConnectionError) and readings_in(root) is None,
          "a network failure books nothing")

    root = tmp_root(with_data=False)
    out, exc, clock, printed = run([Resp(200, body=[], headers=H(5, 1, 2))], root)
    check(exc is None and out == {} and not os.path.isdir(os.path.join(root, "data"))
          and NOTE_PREFIX in printed,
          "no data/ directory: NOT created, the reading is dropped with a NOTE, and the "
          "fetch carries on (as before)")


def _odds_block():
    """The live odds block of fetch_data.main() - from the odds/odds_source/odds_credits
    initialiser through the end of `if key:` - compiled from the real source."""
    with open(PATHS["fetch_data"], encoding="utf-8") as f:
        tree = ast.parse(f.read())
    main_fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "main")
    body = main_fn.body
    start = next(i for i, s in enumerate(body) if isinstance(s, ast.Assign)
                 and ast.unparse(s.targets[0]) == "(odds, odds_source, odds_credits)")
    end = next(i for i, s in enumerate(body) if i > start and isinstance(s, ast.If)
               and ast.unparse(s.test) == "key")
    record_calls = [c for c in ast.walk(main_fn) if isinstance(c, ast.Call)
                    and ast.unparse(c.func) == "record_credits"]
    block = ast.Module(body=body[start:end + 1], type_ignores=[])
    return compile(block, PATHS["fetch_data"], "exec"), len(record_calls)


def characterise_fetch_data():
    def run_fetch(resp_or_exc, out):
        clock = Clock()
        fake = FakeRequests([resp_or_exc], clock)
        exc = None
        with patched(fd, requests=fake, datetime=fake_datetime(clock)):
            try:
                fd.fetch_odds("selftest-not-a-key", out)
            except BaseException as e:           # noqa: BLE001
                exc = e
        return exc, clock

    out, filled = {}, []
    resp = Resp(429, headers=H(0, 700, 2))
    resp.on_rfs = lambda: filled.append(dict(out))
    exc, clock = run_fetch(resp, out)
    check(isinstance(exc, real_requests.HTTPError) and filled == [out] and resp.json_calls == 0,
          "fetch_odds() fills credits_out BEFORE raise_for_status(); the body is never parsed")
    check(out == {"remaining": 0, "used": 700, "last_call_cost": 2,
                  "markets": fd.ODDS_MARKETS, "regions": fd.ODDS_REGIONS,
                  "http_status": 429, "source": "fetch_data",
                  "read_utc": iso(clock.t)} and list(out) == READING_KEYS,
          "exact 8-field reading, read_utc from utcnow() AFTER the response (as before)")
    out = {}
    exc, _ = run_fetch(Resp(200, headers=H(9, 1, 2), bad_json=True), out)
    check(isinstance(exc, ValueError) and out.get("http_status") == 200,
          "a garbled 200 leaves credits_out filled for main() to book")
    out = {}
    exc, _ = run_fetch(real_requests.ConnectionError("selftest reset"), out)
    check(isinstance(exc, real_requests.ConnectionError) and out == {},
          "a network failure leaves credits_out empty")

    code, n_calls = _odds_block()
    check(n_calls == 1, f"main() books credits in exactly one place ({n_calls})")

    def run_block(fill, raise_after_fill=None, raise_before=None, consolidate_raises=False):
        root = tmp_root()
        events = []

        def fake_fetch(key, credits_out):
            if raise_before:
                raise raise_before
            credits_out.update(fill)
            if raise_after_fill:
                raise raise_after_fill
            return []

        def fake_consolidate(evs, games, teams):
            if consolidate_raises:
                raise RuntimeError("selftest consolidate failure")
            return {}

        def logged_record(c):
            events.append("record")
            return fd.record_credits(c)

        ns = dict(fd.__dict__)
        ns.update(fetch_odds=fake_fetch, consolidate_odds=fake_consolidate,
                  record_credits=logged_record,
                  alert_low_credits=lambda rem: events.append(f"alert:{rem}"),
                  os=types.SimpleNamespace(environ={"ODDS_API_KEY": "selftest-not-a-key"}),
                  games=[], teams={})
        with patched(fd, ROOT=root), contextlib.redirect_stdout(io.StringIO()):
            exec(code, ns)
        return events, readings_in(root), ns, root

    base = {"remaining": 40, "used": 460, "last_call_cost": 2, "markets": "h2h,totals",
            "regions": "us", "http_status": 200, "source": "fetch_data",
            "read_utc": "2026-09-13T12:00:05Z"}
    ev, rs, ns, root = run_block(base)
    lb = ledger_bytes(root)
    lg = json.loads(lb.decode("utf-8")) if isinstance(lb, bytes) else {}
    check(ev == ["record", "alert:40"] and rs == [base] and lg.get("note") == FD_NOTE,
          "success below CREDITS_LOW: booked (with the fixed note), THEN alerted")
    ev, rs, ns, _ = run_block({**base, "remaining": 30, "http_status": 429},
                              raise_after_fill=RuntimeError("selftest 429"))
    check(ev == ["record", "alert:30"] and rs is not None and len(rs) == 1
          and rs[0]["http_status"] == 429 and ns["odds"] == {},
          "a 429 after credits were filled: still booked and alerted; the board goes odds-less")
    ev, rs, ns, _ = run_block(base, raise_before=RuntimeError("selftest network"))
    check(ev == [] and rs is None,
          "a failure before credits were filled: nothing booked, no alert")
    ev, rs, _, _ = run_block({**base, "remaining": None})
    check(ev == ["record"] and rs is not None and len(rs) == 1,
          "remaining unknown: booked, no alert")
    ev, _, _, _ = run_block({**base, "remaining": fd.CREDITS_LOW})
    check(ev == ["record"], f"remaining == CREDITS_LOW ({fd.CREDITS_LOW}): booked, no alert")
    ev, _, _, _ = run_block({**base, "remaining": fd.CREDITS_LOW - 1})
    check(ev == ["record", f"alert:{fd.CREDITS_LOW - 1}"], "one below CREDITS_LOW: alerted")
    ev, rs, _, _ = run_block({**base, "remaining": 900}, consolidate_raises=True)
    check(ev == ["record"] and rs is not None and len(rs) == 1,
          "a consolidation failure after a good call: still booked")


def characterise_historical():
    slots = {"2024-09-01T12:00:00Z": ["g1"], "2024-09-02T12:00:00Z": ["g2"],
             "2024-09-03T12:00:00Z": ["g3"]}

    def payload(stamp):
        return {"timestamp": stamp, "previous_timestamp": None, "next_timestamp": None,
                "data": []}

    def ok(rem, stamp):
        return Resp(200, body=payload(stamp), headers=H(rem, 90000 - rem, 30))

    def run(script):
        root = tmp_root()
        clock = Clock()
        at_call = []
        fake = FakeRequests(script, clock, tick=7,
                            on_call=lambda: at_call.append(len(readings_in(root) or [])))
        sleeps = []
        stub_time = types.SimpleNamespace(time=lambda: 0.0, sleep=sleeps.append)
        hist = os.path.join(root, "data", "football", "odds", "hist")
        exc, ret = None, None
        with patched(fho, ROOT=root, CREDIT_LOG=os.path.join(root, LEDGER_REL), SNAPS=hist,
                     INDEX=os.path.join(root, "data", "football", "odds", "hist_index.json"),
                     slots=lambda seasons, at="t24": (dict(slots),
                                                      [{"game_id": g} for g in ("g1", "g2", "g3")]),
                     localenv=STUB_LOCALENV, requests=fake, datetime=fake_datetime(clock),
                     time=stub_time), \
                patched(sys, argv=["fetch_historical_odds.py", "--seasons", "2024"]), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                ret = fho.main()
            except BaseException as e:           # noqa: BLE001
                exc = e
        snaps = len([f for f in os.listdir(hist)]) if os.path.isdir(hist) else 0
        return root, ret, exc, clock, at_call, snaps, sleeps, fake

    root, ret, exc, clock, at_call, snaps, sleeps, fake = run(
        [ok(9000, "a"), ok(8970, "b"), ok(8940, "c")])
    rs = readings_in(root)
    check(ret == 0 and exc is None and snaps == 3 and len(fake.calls) == 3,
          "a clean 3-call batch completes (3 calls, 3 snapshots)")
    check(rs is not None and len(rs) == 1 and rs[0] == {
        "remaining": 8940, "used": 90000 - 8940, "last_call_cost": 30,
        "markets": "h2h,spreads,totals", "regions": "us", "http_status": 200,
        "source": "football_historical", "read_utc": iso(clock.t)}
          and list(rs[0]) == READING_KEYS,
          "BATCH-LEVEL BOOKING: 3 calls -> ONE reading, carrying the LAST call's balance")
    check(at_call == [0, 0, 0],
          "nothing is booked while the batch runs, so a long backfill cannot flush the "
          "operational readings out of the 60-slot window")
    check(rs is not None and rs[0]["read_utc"] == iso(clock.t)
          and rs[0]["read_utc"] != iso(Clock().t + timedelta(seconds=7)),
          "the batch reading is stamped AFTER the whole batch, not at its first call")

    root, ret, exc, clock, at_call, snaps, sleeps, fake = run(
        [ok(9000, "a"), Resp(401, headers=H(9000, 81000, 0))])
    rs = readings_in(root)
    check(isinstance(exc, SystemExit) and snaps == 1 and rs is not None and len(rs) == 1
          and rs[0]["http_status"] == 401 and rs[0]["remaining"] == 9000
          and rs[0]["read_utc"] == iso(clock.t) and list(rs[0]) == READING_KEYS,
          "a non-200 mid-batch books THAT call (stamped after its response) and stops; the "
          "earlier successful call is not booked")

    root, ret, exc, clock, at_call, snaps, sleeps, fake = run([ok(9000, "a"), ok(4000, "b")])
    check(isinstance(exc, SystemExit) and "credit floor" in str(exc) and snaps == 1
          and readings_in(root) is None,
          "a credit-floor stop books nothing (as before - PINNED, NOT ENDORSED)")

    garbled = Resp(200, headers=H(8970, 81030, 30), bad_json=True)
    root, ret, exc, clock, at_call, snaps, sleeps, fake = run([ok(9000, "a"), garbled])
    check(isinstance(exc, ValueError) and readings_in(root) is None,
          "a garbled body mid-batch raises and books nothing (as before)")

    root, ret, exc, clock, at_call, snaps, sleeps, fake = run(
        [real_requests.ConnectionError("selftest reset")] * 4)
    check(isinstance(exc, SystemExit) and "network failure" in str(exc)
          and readings_in(root) is None and sleeps == [3.0, 6.0, 12.0],
          "a network failure after every retry books nothing (retry schedule unchanged)")


MUTANTS = [
    ("indent changed", "json.dump(log, f, indent=1)", "json.dump(log, f, indent=2)"),
    ("create_parent ignored", "if create_parent:", "if False:"),
    ("parent always created", "if create_parent:", "if True:"),
    ("note written unconditionally", "if note is not None:", "if True:"),
    ("corrupt ledger 'repaired'", "log = json.load(f)",
     "log = (lambda t: json.loads(t) if t.strip().startswith('{') else "
     "{'readings': []})(f.read())"),
    ("cap off by one", "[-keep:]", "[-(keep - 1):]"),
    ("NOTE text changed", 'f"NOTE: could not record odds credits:',
     'f"NOTE: could not record credits:'),
    ("ensure_ascii dropped", "json.dump(log, f, indent=1)",
     "json.dump(log, f, indent=1, ensure_ascii=False)"),
    # Phase 1 must not quietly fix what it pins. Each of these is a plausible
    # "improvement" - and a behaviour change - so each must go red.
    ("keep validated", 'log = {"readings": []}',
     'if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:\n'
     '            raise ValueError(keep)\n'
     '        log = {"readings": []}'),
    ("write made atomic", 'with open(path, "w", encoding="utf-8") as f:\n'
     '            json.dump(log, f, indent=1)',
     'text = json.dumps(log, indent=1)\n'
     '        with open(path, "w", encoding="utf-8") as f:\n'
     '            f.write(text)'),
]


def _wrapper_mutants():
    """Broken WRAPPERS: the path boundary and the CREDIT_LOG seam live there."""
    def closing_path_inside_try(name, b):
        def call(reading):
            try:
                path = os.path.join(b["ROOT"], "data", "odds_credits.json")
            except Exception as exc:
                print(f"NOTE: could not record odds credits: {exc}")
                return None
            oc.record(reading, path=path, keep=b["CREDIT_LOG_KEEP"])
        return call

    def odds_ignores_credit_log(name, b):
        def call(reading):
            oc.record(reading, path=os.path.join(b["ROOT"], "data", "odds_credits.json"),
                      keep=b["CREDIT_LOG_KEEP"], create_parent=True)
        return call

    def historical_ignores_credit_log(name, b):
        def call(reading):
            oc.record(reading, path=os.path.join(b["ROOT"], "data", "odds_credits.json"))
        return call

    return [("fetch_closing builds its path inside a try", "fetch_closing", closing_path_inside_try),
            ("fetch_odds derives its path from ROOT, ignoring CREDIT_LOG", "fetch_odds",
             odds_ignores_credit_log),
            ("fetch_historical_odds derives its path from ROOT, ignoring CREDIT_LOG",
             "fetch_historical_odds", historical_ignores_credit_log)]


CI_WORKFLOW = os.path.join(ROOT, ".github", "workflows", "odds-credits-selftest.yml")
CREDIT_PATHS = ["scripts/odds_credits.py", "scripts/selftest_odds_credits.py",
                "scripts/fetch_closing.py", "scripts/fetch_data.py",
                "scripts/football/fetch_odds.py", "scripts/football/fetch_historical_odds.py",
                ".github/workflows/odds-credits-selftest.yml"]
# Not derivable from imports: the job installs from requirements.txt, and
# teams.from_name() READS the names table during [6]'s unmatched-team case.
NON_IMPORT_PATHS = ["requirements.txt", "data/football/team_names.json"]


def module_level_imports(path):
    """Top-level module names imported when `path` is IMPORTED - including imports
    inside module-level try/if/with blocks, excluding function and class bodies,
    which run only if called."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = []

    def visit(stmts):
        for s in stmts:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(s, ast.Import):
                names.extend(a.name.split(".")[0] for a in s.names)
            elif isinstance(s, ast.ImportFrom) and s.level == 0 and s.module:
                names.append(s.module.split(".")[0])
            for field in ("body", "orelse", "finalbody", "handlers"):
                sub = getattr(s, field, None)
                if isinstance(sub, list):
                    visit(sub)
    visit(tree.body)
    return names


def import_closure():
    """Project files reached from this suite through module-level imports."""
    start = os.path.join(SCRIPTS, "selftest_odds_credits.py")
    seen, queue = {start}, [start]
    while queue:
        for name in module_level_imports(queue.pop()):
            for d in (SCRIPTS, FOOTBALL):
                p = os.path.join(d, name + ".py")
                if os.path.exists(p) and p not in seen:
                    seen.add(p)
                    queue.append(p)
    return sorted(os.path.relpath(p, ROOT).replace(os.sep, "/") for p in seen)


def ci_contract():
    import yaml
    check(os.path.isfile(CI_WORKFLOW), ".github/workflows/odds-credits-selftest.yml exists")
    if not os.path.isfile(CI_WORKFLOW):
        return
    with open(CI_WORKFLOW, encoding="utf-8") as f:
        raw = f.read()
    doc = yaml.safe_load(raw)
    code = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    on = doc.get("on", doc.get(True)) or {}                 # YAML 1.1 reads `on` as True
    check(doc.get("name") == "Odds credit self-tests", f"named 'Odds credit self-tests' "
          f"({doc.get('name')!r})")
    check(sorted(on) == ["pull_request", "push", "workflow_dispatch"],
          f"triggers are exactly workflow_dispatch, push and pull_request ({sorted(on)})")
    push, pr = on.get("push") or {}, on.get("pull_request") or {}
    check(push.get("branches") == ["main"] and pr.get("branches") == ["main"],
          "push and pull_request are filtered to main")
    paths = push.get("paths") or []
    check(paths and paths == pr.get("paths"),
          "push and pull_request path filters are IDENTICAL, entry for entry")
    check(len(paths) == len(set(paths)), "no path is listed twice")
    check(not any(k in push or k in pr for k in ("paths-ignore", "branches-ignore", "tags")),
          "no paths-ignore / branches-ignore / tags that could narrow the filter")
    missing = [p for p in CREDIT_PATHS if p not in paths]
    check(not missing, f"all six credit files and the workflow itself are in the filter "
          f"(missing {missing})")
    closure = import_closure()
    missing = [p for p in closure if p not in paths]
    check(not missing, f"EVERY project module in the suite's module-level import closure is "
          f"in the filter - derived now, not remembered: {closure} (missing {missing})")
    missing = [p for p in NON_IMPORT_PATHS if p not in paths]
    check(not missing, f"requirements.txt and the runtime-read team_names.json are in the "
          f"filter (missing {missing})")
    stale = [p for p in paths if not os.path.exists(os.path.join(ROOT, p))]
    extra = [p for p in paths if p not in set(CREDIT_PATHS) | set(closure) | set(NON_IMPORT_PATHS)]
    check(not stale and not extra,
          f"every filter entry exists and has a derived reason (stale {stale}, "
          f"unexplained {extra})")
    check("secrets." not in code and "secrets" not in json.dumps(doc),
          "no secrets.* reference anywhere in the workflow")
    check(doc.get("permissions") == {"contents": "read"},
          f"permissions: contents: read ({doc.get('permissions')})")
    jobs = doc.get("jobs") or {}
    check(len(jobs) == 1 and all("permissions" not in j for j in jobs.values()),
          "one job, and it does not widen permissions")
    steps = [s for j in jobs.values() for s in (j.get("steps") or [])]
    runs = [s["run"].strip() for s in steps if "run" in s]
    check(runs == ["pip install -r requirements.txt", "python scripts/selftest_odds_credits.py"],
          f"it runs only the dependency install and python scripts/selftest_odds_credits.py "
          f"({runs})")
    py = [s.get("with", {}).get("python-version") for s in steps
          if str(s.get("uses", "")).startswith("actions/setup-python")]
    check(py == ["3.12"], f"Python 3.12 ({py})")
    envs = [k for s in steps for k in (s.get("env") or {})] + \
           [k for j in jobs.values() for k in (j.get("env") or {})] + list(doc.get("env") or {})
    check(envs == ["PYTHONIOENCODING"],
          f"no environment beyond PYTHONIOENCODING - nothing a key could ride in on ({envs})")


def negative_controls():
    with open(oc.__file__, encoding="utf-8") as f:
        src = f.read()
    # Mutate CODE only: the docstrings quote some of these lines on purpose.
    head, sep, body = src.partition("\ndef record(")
    for label, old, new in MUTANTS:
        check(body.count(old) == 1,
              f"mutant '{label}': its target text exists exactly once in record()")
        mutant = types.ModuleType("odds_credits_mutant")
        mutant.__file__ = oc.__file__
        exec(compile(head + sep + body.replace(old, new), f"<mutant: {label}>", "exec"),
             mutant.__dict__)
        caught = {}
        for name in CALLERS:
            with patched(MODULES[name], odds_credits=mutant):
                bad = golden_mismatches(name)
            if bad:
                caught[name] = bad
        check(bool(caught), f"mutant '{label}' is caught by [1] "
              f"({', '.join(f'{k}: {len(v)}' for k, v in caught.items()) or 'NOT CAUGHT'})")
    for label, name, impl in _wrapper_mutants():
        bad = golden_mismatches(name, impl)
        check(bool(bad), f"wrapper mutant '{label}' is caught by [1] ({bad or 'NOT CAUGHT'})")


# ---------------------------------------------------------------------------
# FROZEN ORIGINALS - generated from `git show <commit>:<path>`, never hand-edited.
# ---------------------------------------------------------------------------
FROZEN_FROM = "5d90a93"
FROZEN = {
    'fetch_odds': (
        'def record_credits(credits):\n'
        '    """Append a plaintext credit reading. Never raises - mirrors fetch_closing."""\n'
        '    try:\n'
        '        log = {"readings": []}\n'
        '        if os.path.exists(CREDIT_LOG):\n'
        '            with open(CREDIT_LOG, encoding="utf-8") as f:\n'
        '                log = json.load(f)\n'
        '        log["readings"] = (log.get("readings", []) + [credits])[-CREDIT_LOG_KEEP:]\n'
        '        os.makedirs(os.path.dirname(CREDIT_LOG), exist_ok=True)\n'
        '        with open(CREDIT_LOG, "w", encoding="utf-8") as f:\n'
        '            json.dump(log, f, indent=1)\n'
        '    except Exception as exc:\n'
        '        print(f"NOTE: could not record odds credits: {exc}")\n'
    ),
    'fetch_closing': (
        'def record_credits(credits):\n'
        '    """Append a plaintext credit reading to data/odds_credits.json. Never raises."""\n'
        '    path = os.path.join(ROOT, "data", "odds_credits.json")\n'
        '    try:\n'
        '        log = {"readings": []}\n'
        '        if os.path.exists(path):\n'
        '            with open(path, encoding="utf-8") as f:\n'
        '                log = json.load(f)\n'
        '        log["readings"] = (log.get("readings", []) + [credits])[-CREDIT_LOG_KEEP:]\n'
        '        with open(path, "w", encoding="utf-8") as f:\n'
        '            json.dump(log, f, indent=1)\n'
        '    except Exception as exc:\n'
        '        print(f"NOTE: could not record odds credits: {exc}")\n'
    ),
    'fetch_data': (
        'def record_credits(credits):\n'
        '    """Append the reading to data/odds_credits.json, IN THE CLEAR.\n'
        '\n'
        '    The snapshot carries the same numbers, but the snapshot is encrypted until\n'
        '    grading reveals it — so it cannot be what makes the balance "visible in the\n'
        '    repo". This file can, and it costs nothing: no picks, no model output, just\n'
        '    a counter and a timestamp.\n'
        '    """\n'
        '    path = os.path.join(ROOT, "data", "odds_credits.json")\n'
        '    try:\n'
        '        log = {"readings": []}\n'
        '        if os.path.exists(path):\n'
        '            with open(path, encoding="utf-8") as f:\n'
        '                log = json.load(f)\n'
        '        log["readings"] = (log.get("readings", []) + [credits])[-CREDIT_LOG_KEEP:]\n'
        '        log["note"] = ("The Odds API bills one credit per market per region per call. "\n'
        '                       "Free tier: 500/month. See CLAUDE.md for the budget and the "\n'
        '                       "decision rule for upgrading.")\n'
        '        os.makedirs(os.path.dirname(path), exist_ok=True)\n'
        '        with open(path, "w", encoding="utf-8") as f:\n'
        '            json.dump(log, f, indent=1)\n'
        '    except Exception as exc:                 # telemetry must never sink the board\n'
        '        print(f"NOTE: could not record odds credits: {exc}")\n'
    ),
    'fetch_historical_odds': (
        'def record_credits(entry):\n'
        '    try:\n'
        '        log = {"readings": []}\n'
        '        if os.path.exists(CREDIT_LOG):\n'
        '            with io.open(CREDIT_LOG, encoding="utf-8") as f:\n'
        '                log = json.load(f)\n'
        '        log["readings"] = (log.get("readings", []) + [entry])[-60:]\n'
        '        with io.open(CREDIT_LOG, "w", encoding="utf-8") as f:\n'
        '            json.dump(log, f, indent=1)\n'
        '    except Exception as exc:\n'
        '        print(f"NOTE: could not record odds credits: {exc}")\n'
    ),
}
FROZEN_SHA256 = {
    'fetch_odds': '5173e9fb85c786f1e867446d53965b48e7a7a5099399a8a1881052bef1ed4a2d',
    'fetch_closing': '47a5856e30cdbe79ffc516cb29406862449ee23885416c47d9fae23b5e64d873',
    'fetch_data': '3f02210785feaef25bb2b9524dae162102f24e70ea2b4cb4acf7efbc4fad89fd',
    'fetch_historical_odds': 'd29688a55ba4ff7bd09f12568811cc0df1b7d2da2c6d15496d629942e8ec1854',
}


if __name__ == "__main__":
    sys.exit(main())
