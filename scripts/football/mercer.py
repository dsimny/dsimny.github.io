#!/usr/bin/env python3
"""
Open Ledger Sports — the D.J. Mercer Spotlight (football/mercer/).

Human-researched NFL and college football picks under the D.J. Mercer byline,
kept on their OWN append-only record. This is the editorial voice of the site;
the model's football ledger (data/football/football_ledger.json) is the
algorithmic one, and the two never mix.

------------------------------------------------------------------------------
WHAT THIS IS, AND WHAT IT IS NOT
------------------------------------------------------------------------------
D.J. Mercer is a sports enthusiast and independent analyst. No credentials are
claimed for him, no résumé is invented, and no expectation claim is made for
his picks. The record IS the credential, and the record is built so that it
cannot be flattered:

  * Every official pick is FINGERPRINTED (SHA-256 of the pick, timestamped)
    before kickoff. A pick that was not fingerprinted before its game started
    books as VOID, in public, with the reason. No screenshot handicapping.
  * A pick whose text changes after it was fingerprinted is REFUSED by the
    grader, loudly, until a human looks at it. Sharpening a number after the
    fact is exactly what the fingerprint exists to catch.
  * The ledger is APPEND-ONLY (House Rule 1). A graded pick is never edited
    or deleted; the record is recomputed from full history every render.
  * Leans and passes are published but NEVER graded into the record. They are
    opinion, labelled as opinion, and they earn nothing.
  * NFL and college football keep SEPARATE records, plus a combined one,
    because they are different markets and one strong season in one must not
    hide a weak one in the other.

Words barred in football copy on this site ("edge", "+EV", "value", "the
model likes") stay barred here. Mercer's number is Mercer's opinion and is
labelled as such.

------------------------------------------------------------------------------
THE FILES
------------------------------------------------------------------------------
  data/mercer/weeks/<slate_week>.json   one hand-written card per slate week
                                        (picks, spotlight, leans, passes)
  data/mercer/commitments.json          per-pick fingerprints + timestamps,
                                        append-only
  data/mercer/mercer_ledger.json        the graded record, append-only
  football/mercer/                      the rendered pages

Slate weeks are Tuesday-anchored, exactly like the model's (market.slate_week).

------------------------------------------------------------------------------
THE WORKFLOW FOR A WEEK
------------------------------------------------------------------------------
  1. write data/mercer/weeks/<week>.json (see docs/MERCER_SPOTLIGHT.md)
  2. python scripts/football/mercer.py check            # resolves every game
  3. python scripts/football/mercer.py commit           # fingerprints new picks
  4. python scripts/football/mercer.py render           # writes the pages
  5. git add data/mercer football/mercer && git commit && git push
     (the push is the public timestamp; the fingerprint is what it timestamps)
  6. football-grade.yml runs `grade` and `render` daily; nothing to do.

A pick added mid-week (a Thursday play, then Sunday's) is fine: `commit` is
incremental and fingerprints only picks it has not seen. Re-running it never
re-stamps a pick it already holds.
"""
import argparse
import glob
import html
import io
import json
import os
import re
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import market                                        # noqa: E402
import settle                                        # noqa: E402
import teams                                         # noqa: E402
import espn_nfl                                      # noqa: E402
import espn_ncaaf                                    # noqa: E402
import crypto_box                                    # noqa: E402
from blog import PAGE_CSS, LEGAL, nice_date, et_time, ET  # noqa: E402

ROOT = os.path.join(HERE, "..", "..")
DATA = os.path.join(ROOT, "data", "mercer")
WEEKS = os.path.join(DATA, "weeks")
LEDGER = os.path.join(DATA, "mercer_ledger.json")
COMMITMENTS = os.path.join(DATA, "commitments.json")
OUT = os.path.join(ROOT, "football", "mercer")
REPO_BLOB = "https://github.com/dsimny/dsimny.github.io/blob/main/"

E = html.escape

SPORTS = {
    "nfl": {"label": "NFL", "results": espn_nfl,
            "away_key": "away", "home_key": "home",
            "away_name": "away_name", "home_name": "home_name"},
    "ncaaf": {"label": "College Football", "results": espn_ncaaf,
              "away_key": "away_key", "home_key": "home_key",
              "away_name": "away", "home_name": "home"},
}
MARKETS = ("spread", "total", "moneyline")

# THE TAXONOMY. Only OFFICIAL touches the record. The three kinds live in three
# separate lists in the week file so they cannot be confused by a typo, and the
# status field is validated on top of that, so a mislabelled entry is refused
# rather than silently graded (or silently not graded).
OFFICIAL, LEAN, PASS = "OFFICIAL", "LEAN", "PASS"

# CONVICTION IS THREE WORDS, NOT A PERCENTAGE. Mercer has no calibrated model,
# so "78.4% confident" would be invented precision dressed as a measurement -
# the exact failure the model's writeup validator exists to prevent. Three
# levels say what they mean and claim nothing they cannot back.
CONVICTIONS = ("standard", "strong", "spotlight")
CONVICTION_LABEL = {"standard": "Standard", "strong": "Strong", "spotlight": "Spotlight"}

# Renamed fields, refused BY NAME so an old card fails loudly instead of
# silently dropping its prose.
RETIRED_FIELDS = {
    "worries": "case_against", "watching": "changes_my_mind",
    "verdict": "status", "confidence": "conviction",
}
# Football copy rule (page.py docstring): no expectation claims. Word-bounded,
# so "ledger", "knowledge" and "hedge" do not trip it.
BARRED_RE = re.compile(r"(?<![a-z])(edge|\+ev|the model likes)(?![a-z])")
WEEK_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slate_week(kickoff):
    """The Tuesday on or before kickoff, ANCHORED IN EASTERN TIME.

    THIS DELIBERATELY DIFFERS FROM market.slate_week, AND THE DIFFERENCE IS THE
    POINT. The model's version takes the UTC date, which is fine for its own
    grouping but puts MONDAY NIGHT FOOTBALL IN THE FOLLOWING WEEK: a Monday
    20:15 ET kickoff is Tuesday 00:15 UTC, so the Tuesday anchor lands on the
    game's own UTC date and splits the NFL week that began on Thursday.

    Measured on the 2026 calendar:
        Thu 2026-09-10 20:20 ET -> both agree, 2026-09-08
        Sun 2026-09-13 20:20 ET -> both agree, 2026-09-08
        Mon 2026-09-14 20:15 ET -> market.slate_week gives 2026-09-15 (next week)
                                   this gives 2026-09-08 (with the rest of Week 1)

    A person writing "Week 1" means Thursday through Monday, so the editorial
    record has to group them that way or a card refuses its own Monday pick.

    market.slate_week IS NOT CHANGED. It is the model's frozen grouping,
    football_ledger.json is already keyed by it, and re-anchoring it mid-season
    would re-split weeks the model has already graded.

    Tuesday and Wednesday games (college midweek, a rare NFL makeup) anchor to
    their own Tuesday, which is what those words mean.
    """
    d = kickoff.astimezone(ET).date()
    return (d - timedelta(days=(d.weekday() - 1) % 7)).isoformat()


def current_week(now=None):
    return slate_week(now or market.now_utc())


# ---------------------------------------------------------------- storage --

def load_json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")


def load_ledger():
    return load_json(LEDGER, {
        "_note": ("D.J. Mercer Spotlight record, append-only (House Rule 1). "
                  "Human-researched football picks under the Mercer byline. "
                  "NEVER mixed with football_ledger.json (the model's record) "
                  "or any MLB ledger. A VOID entry is a pick that could not be "
                  "graded honestly — its void_reason says why, and it stays here "
                  "in public. Leans and passes are never booked."),
        "entries": []})


def load_commitments():
    return load_json(COMMITMENTS, {
        "_note": ("Per-pick fingerprints for the D.J. Mercer Spotlight. Each "
                  "entry is the SHA-256 of one pick's canonical JSON, stamped when "
                  "`mercer.py commit` first saw it. Append-only: a pick is never "
                  "re-stamped. The grader books a pick only if its EARLIEST stamp "
                  "matches the pick as it stands now AND precedes kickoff; "
                  "otherwise the entry is VOID (never stamped, or stamped late) "
                  "or REFUSED (edited after stamping). The git history of this "
                  "file is the public timestamp."),
        "commitments": []})


def list_weeks():
    out = []
    for p in sorted(glob.glob(os.path.join(WEEKS, "*.json"))):
        w = os.path.basename(p)[:-5]
        if WEEK_RE.match(w):
            out.append(w)
    return out


def load_week(week):
    return load_json(os.path.join(WEEKS, f"{week}.json"), None)


def fingerprint(pick):
    """SHA-256 of the pick's canonical JSON, same canon as the model's board."""
    return crypto_box.sha256_of(pick)


# ------------------------------------------------------------- validation --

def selection_text(p):
    m = p.get("market")
    if m == "spread":
        return f'{p.get("team", "")} {float(p.get("line", 0)):+g}'
    if m == "total":
        return f'{str(p.get("side", "")).capitalize()} {float(p.get("line", 0)):g}'
    if m == "moneyline":
        return f'{p.get("team", "")} ML'
    return "?"


def validate_pick(p, where):
    errs = []
    # THE CASE AGAINST IS REQUIRED, not optional. It is the thing that makes
    # this section different from every other picks page, so the validator
    # enforces it rather than trusting the writer to remember.
    for k in ("id", "sport", "away", "home", "kickoff_utc", "market", "price",
              "why", "case_against"):
        if p.get(k) in (None, ""):
            errs.append(f"{where}: missing {k!r}")
    for old, new in RETIRED_FIELDS.items():
        if old in p:
            errs.append(f"{where}: {old!r} was renamed to {new!r}")
    st = p.get("status", OFFICIAL)
    if st != OFFICIAL:
        errs.append(f"{where}: status must be {OFFICIAL!r} in 'picks' (got {st!r}) - "
                    f"move a lean to 'leans' and a pass to 'passes'")
    cv = p.get("conviction")
    if cv is not None and str(cv).lower() not in CONVICTIONS:
        errs.append(f"{where}: conviction must be one of {CONVICTIONS} - no numeric "
                    f"confidence, because this is not a calibrated model")
    if "book" in p and not str(p.get("book") or "").strip():
        errs.append(f"{where}: 'book' is present but empty; name the book or omit it")
    if p.get("sport") not in SPORTS:
        errs.append(f"{where}: sport must be one of {sorted(SPORTS)}")
    if p.get("market") not in MARKETS:
        errs.append(f"{where}: market must be one of {MARKETS}")
    if p.get("market") in ("spread", "total") and not isinstance(p.get("line"), (int, float)):
        errs.append(f"{where}: {p.get('market')} needs a numeric 'line'")
    if p.get("market") in ("spread", "moneyline") and not p.get("team"):
        errs.append(f"{where}: {p.get('market')} needs 'team' (must equal away or home)")
    if p.get("market") in ("spread", "moneyline") and p.get("team") not in (p.get("away"), p.get("home")):
        errs.append(f"{where}: 'team' must be written exactly as 'away' or 'home'")
    if p.get("market") == "total" and str(p.get("side", "")).lower() not in ("over", "under"):
        errs.append(f"{where}: total needs side 'over' or 'under'")
    if not isinstance(p.get("price"), int) or isinstance(p.get("price"), bool):
        errs.append(f"{where}: price must be an integer American price (e.g. -110)")
    u = p.get("units", 1)
    if not isinstance(u, (int, float)) or isinstance(u, bool) or u <= 0:
        errs.append(f"{where}: units must be a positive number "
                    f"(a 0-unit official pick is a lean; put it in 'leans')")
    if market.parse_utc(p.get("kickoff_utc")) is None:
        errs.append(f"{where}: kickoff_utc must be ISO-8601 UTC, e.g. 2026-09-13T17:00:00Z")
    text = " ".join(str(p.get(k, "")) for k in
                    ("why", "case_against", "changes_my_mind", "mercer_number")).lower()
    for m in BARRED_RE.finditer(text):
        errs.append(f"{where}: the word {m.group(1)!r} is barred in football copy on this site")
    return errs


def validate_week(week, doc):
    errs = []
    if not isinstance(doc, dict):
        return [f"{week}: not a JSON object"]
    if doc.get("slate_week") != week:
        errs.append(f"{week}: slate_week must equal the filename ({doc.get('slate_week')!r})")
    ids = set()
    for i, p in enumerate(doc.get("picks", [])):
        where = f"{week} pick[{i}] {p.get('id', '?')}"
        errs += validate_pick(p, where)
        if p.get("id") in ids:
            errs.append(f"{where}: duplicate id")
        ids.add(p.get("id"))
        k = market.parse_utc(p.get("kickoff_utc"))
        if k and slate_week(k) != week:
            errs.append(f"{where}: kickoff falls in slate week {slate_week(k)}, not {week}")
    for i, l in enumerate(doc.get("leans", [])):
        where = f"{week} lean[{i}]"
        for k in ("sport", "away", "home", "lean"):
            if not l.get(k):
                errs.append(f"{where}: missing {k!r}")
        if l.get("sport") not in SPORTS:
            errs.append(f"{where}: sport must be one of {sorted(SPORTS)}")
        if l.get("status", LEAN) != LEAN:
            errs.append(f"{where}: status must be {LEAN!r} in 'leans' (got {l.get('status')!r})")
        if l.get("units"):
            errs.append(f"{where}: a lean carries no units - it is opinion and is never graded")
    for i, x in enumerate(doc.get("passes", [])):
        where = f"{week} pass[{i}]"
        for k in ("sport", "away", "home", "temptation", "reason"):
            if not x.get(k):
                errs.append(f"{where}: missing {k!r}")
        if x.get("status", PASS) != PASS:
            errs.append(f"{where}: status must be {PASS!r} in 'passes' (got {x.get('status')!r})")
        if x.get("units"):
            errs.append(f"{where}: a pass carries no units - it is the absence of a bet")
    return errs


# -------------------------------------------------------------- resolving --

def _team_key(sport, name):
    if sport == "nfl":
        return teams.from_name(name, source="mercer card")
    return espn_ncaaf.key_for(name)


def _match_team(sport, typed, actual):
    if typed == actual:
        return True
    # College only: "Alabama" -> "alabamacrimsontide". Accepted only when it is
    # UNIQUE on that kickoff (checked by the caller), never as a general fuzz.
    return sport == "ncaaf" and len(typed) >= 4 and actual.startswith(typed)


def resolve_event(item, events):
    """The ESPN event for a pick/lean/pass, or (None, why)."""
    sport = item.get("sport")
    cfg = SPORTS.get(sport)
    if not cfg:
        return None, f"unknown sport {sport!r}"
    try:
        ak, hk = _team_key(sport, item.get("away", "")), _team_key(sport, item.get("home", ""))
    except teams.UnknownTeam as e:
        return None, str(e)
    if item.get("espn_event_id"):
        ev = events.get(str(item["espn_event_id"]))
        if not ev:
            return None, (f"espn_event_id {item['espn_event_id']} is not in the "
                          f"{sport} results store")
        # The id pins the game; the typed names must still agree with it, or a
        # spread could be settled on the wrong side without anyone noticing.
        if not (_match_team(sport, ak, ev.get(cfg["away_key"], "")) and
                _match_team(sport, hk, ev.get(cfg["home_key"], ""))):
            a, h = ev_names(sport, ev)
            return None, (f"espn_event_id {item['espn_event_id']} is {a} @ {h}, which does "
                          f"not match {item.get('away')!r} @ {item.get('home')!r}")
        return ev, None
    k0 = market.parse_utc(item.get("kickoff_utc"))
    if k0 is None:
        return None, "kickoff_utc missing or unparseable"
    hits = []
    for ev in events.values():
        k = market.parse_utc(ev.get("kickoff_utc"))
        if k is None or abs((k - k0).total_seconds()) > 36 * 3600:
            continue
        if _match_team(sport, ak, ev.get(cfg["away_key"], "")) and \
           _match_team(sport, hk, ev.get(cfg["home_key"], "")):
            hits.append(ev)
    if not hits:
        return None, (f"no {cfg['label']} game within 36h of {item.get('kickoff_utc')} "
                      f"matches {item.get('away')!r} @ {item.get('home')!r} — check the names, "
                      f"or fetch that date: python scripts/football/"
                      f"{'espn_nfl' if sport == 'nfl' else 'espn_ncaaf'}.py --dates "
                      f"{k0.strftime('%Y%m%d')}")
    if len(hits) > 1:
        return None, (f"ambiguous: {len(hits)} games match; set espn_event_id to one of "
                      + ", ".join(h["espn_event_id"] for h in hits))
    return hits[0], None


def ev_names(sport, ev):
    cfg = SPORTS[sport]
    return ev.get(cfg["away_name"], ""), ev.get(cfg["home_name"], "")


def load_stores():
    return {s: SPORTS[s]["results"].load_store().get("events", {}) for s in SPORTS}


# ---------------------------------------------------------------- commands --

def cmd_check(week, stores=None, now=None):
    weeks = [week] if week else list_weeks()
    if not weeks:
        print("no week files in data/mercer/weeks/.")
        return 0
    stores = stores if stores is not None else load_stores()
    now = now or market.now_utc()
    committed = {c["pick_id"] for c in load_commitments()["commitments"]}
    bad = 0
    for w in weeks:
        doc = load_week(w)
        if doc is None:
            print(f"{w}: no such week file"); bad += 1; continue
        errs = validate_week(w, doc)
        for e in errs:
            print("  PROBLEM " + e)
        bad += len(errs)
        print(f"{w}: {len(doc.get('picks', []))} picks, {len(doc.get('leans', []))} leans, "
              f"{len(doc.get('passes', []))} passes")
        for kind in ("picks", "leans", "passes"):
            for it in doc.get(kind, []):
                ev, why = resolve_event(it, stores.get(it.get("sport"), {}))
                if ev is None:
                    print(f"  UNRESOLVED {kind[:-1]} {it.get('id', '')} "
                          f"{it.get('away')} @ {it.get('home')}: {why}")
                    bad += 1
                else:
                    a, h = ev_names(it["sport"], ev)
                    note = ""
                    if kind == "picks" and it["id"] not in committed:
                        gate = commit_gate(it, ev, now)
                        if gate:
                            note = f"  [CANNOT BE FINGERPRINTED: {gate}]"
                            bad += 1
                    elif kind == "picks":
                        note = "  [already fingerprinted]"
                    print(f"  {'!!' if note.startswith('  [CANNOT') else 'ok'} "
                          f"{kind[:-1]:<4} {it.get('id', ''):<22} {a} @ {h}  "
                          f"kick {ev['kickoff_utc']}  espn {ev['espn_event_id']}{note}")
    print("check: " + ("clean" if not bad else f"{bad} problem(s)"))
    return 1 if bad else 0


def commit_gate(p, ev, now):
    """Why this pick may NOT be fingerprinted now, or None if it may.

    THE GATE IS THE WHOLE PRODUCT. A pick stamped after its game has started is
    not a prediction, and a record that accepts one is a scrapbook. So the
    refusal happens at the moment of writing, not later at grading: the grader's
    matching VOID rule stays as defence in depth for a hand-edited commitments
    file, but in normal operation nothing late ever gets stamped at all.

    It is also what forbids a BACKFILLED record. Old opinions cannot be
    imported, because every one of them would fail this test.
    """
    if ev is None:
        return "cannot resolve the game (run `check`)"
    kick = market.parse_utc(ev["kickoff_utc"])
    if kick is None:
        return "the results store has no parseable kickoff for this game"
    if now >= kick:
        late = (now - kick).total_seconds() / 3600.0
        return (f"kickoff was {late:.1f}h ago ({ev['kickoff_utc']}). A pick is "
                f"fingerprinted BEFORE the game or not at all. Nothing is backfilled.")
    if not SPORTS[p["sport"]]["results"].gradeable(ev):
        return (f"season type {ev.get('season_slug', 'unknown')!r} can never be graded "
                f"(regular season only). Publish it as a lean instead of an official pick "
                f"that could only ever book VOID.")
    return None


def cmd_commit(week, now=None, write=True, stores=None, dry_run=False):
    write = write and not dry_run
    now = now or market.now_utc()
    stores = stores if stores is not None else load_stores()
    weeks = [week] if week else list_weeks()
    com = load_commitments()
    by_id = {}
    for c in com["commitments"]:
        by_id.setdefault(c["pick_id"], c)
    added, refused = 0, []
    for w in weeks:
        doc = load_week(w)
        if doc is None:
            print(f"{w}: no such week file"); return 1
        errs = validate_week(w, doc)
        if errs:
            for e in errs:
                print("  PROBLEM " + e)
            print(f"{w}: refusing to fingerprint a card with problems.")
            return 1
        for p in doc.get("picks", []):
            prior = by_id.get(p["id"])
            if prior:
                # ALREADY STAMPED. Never re-stamp: the first stamp is the
                # commitment. Say so loudly if the text has since moved, because
                # that is the one case where silence would look like agreement.
                if prior["sha256"] != fingerprint(p):
                    refused.append(f"{p['id']}: already fingerprinted at "
                                   f"{prior['committed_utc']} and the text has CHANGED since. "
                                   f"The stamp is not reissued. Restore the original text, or "
                                   f"give the new pick a new id if the game has not started.")
                continue
            ev, _why = resolve_event(p, stores.get(p["sport"], {}))
            gate = commit_gate(p, ev, now)
            if gate:
                refused.append(f"{p['id']}: NOT fingerprinted - {gate}")
                continue
            entry = {
                "pick_id": p["id"], "slate_week": w, "sport": p["sport"],
                "selection": selection_text(p), "price": p["price"],
                "units": float(p.get("units", 1)), "book": p.get("book"),
                "espn_event_id": ev["espn_event_id"], "kickoff_utc": ev["kickoff_utc"],
                "sha256": fingerprint(p), "committed_utc": market.iso(now)}
            com["commitments"].append(entry)
            by_id[p["id"]] = entry
            added += 1
            hrs = (market.parse_utc(ev["kickoff_utc"]) - now).total_seconds() / 3600.0
            print(f"  stamped {p['id']}  {selection_text(p)} {p['price']:+d}  "
                  f"sha {fingerprint(p)[:16]}…  at {market.iso(now)} "
                  f"({hrs:.1f}h before kickoff)")
    for m in refused:
        print("  REFUSED " + m)
    if added and write:
        save_json(COMMITMENTS, com)
    elif added and dry_run:
        print("  --dry-run: commitments.json untouched")
    print(f"commit: {added} new fingerprint(s), {len(refused)} refused"
          + ("" if added or refused else " - every pick on disk is already stamped"))
    return 1 if refused else 0


def _provenance(p):
    """The number Mercer actually took, recorded as taken.

    NEVER REPLACED BY THE CLOSING NUMBER. The line and price stored here are the
    ones on the fingerprinted pick; a later, better-looking close does not
    overwrite them, and grading reads these fields rather than re-fetching a
    market. That is what makes the P/L column mean what it says.
    """
    return {"market": p["market"], "selection": selection_text(p),
            "line": p.get("line"), "side": p.get("side"), "team": p.get("team"),
            "price": p["price"], "units": float(p.get("units", 1)),
            "book": p.get("book"), "conviction": p.get("conviction"),
            "status": OFFICIAL}


def _void(p, week, ev, reason, now):
    a, h = ev_names(p["sport"], ev) if ev else (p.get("away"), p.get("home"))
    e = {"pick_id": p["id"], "slate_week": week, "sport": p["sport"],
         "matchup": f"{a} @ {h}", "kickoff_utc": (ev or p).get("kickoff_utc"),
         "result": "VOID", "void_reason": reason, "pnl": 0.0,
         "espn_event_id": (ev or {}).get("espn_event_id"),
         "graded_utc": market.iso(now)}
    e.update(_provenance(p))
    return e


def grade_pick(p, week, ev, commits, now):
    """One pick -> ledger entry, None (not yet gradeable) or ('refuse', why)."""
    sport = p["sport"]
    cfg = SPORTS[sport]
    status = str(ev.get("status", ""))
    if "CANCEL" in status.upper():
        return _void(p, week, ev, "game cancelled", now)
    if not ev.get("final"):
        return None
    if not cfg["results"].gradeable(ev):
        return _void(p, week, ev, f"season type not gradeable "
                     f"({ev.get('season_slug', 'unknown')}); only regular season books", now)
    first = min((c for c in commits if c["pick_id"] == p["id"]),
                key=lambda c: c["committed_utc"], default=None)
    if first is None:
        return _void(p, week, ev, "never fingerprinted before kickoff", now)
    if first["sha256"] != fingerprint(p):
        return ("refuse", f"{p['id']}: pick text changed after it was fingerprinted "
                          f"({first['committed_utc']}). Restore the original or void "
                          f"it by hand — the grader will not book an edited pick.")
    kick = market.parse_utc(ev["kickoff_utc"])
    stamped = market.parse_utc(first["committed_utc"])
    if stamped >= kick:
        return _void(p, week, ev, f"fingerprinted after kickoff "
                     f"({first['committed_utc']} vs kickoff {ev['kickoff_utc']})", now)

    hs, as_ = ev["home_score"], ev["away_score"]
    if p["market"] == "total":
        res = settle.settle_total(hs, as_, float(p["line"]), p["side"])
    else:
        typed = _team_key(sport, p["team"])
        on_home = _match_team(sport, typed, ev.get(cfg["home_key"], ""))
        ts, os_ = (hs, as_) if on_home else (as_, hs)
        res = (settle.settle_spread(ts, os_, float(p["line"])) if p["market"] == "spread"
               else settle.settle_moneyline(ts, os_))
    units = float(p.get("units", 1))
    a, h = ev_names(sport, ev)
    hrs = (kick - stamped).total_seconds() / 3600.0
    e = {"pick_id": p["id"], "slate_week": week, "sport": sport,
         "matchup": f"{a} @ {h}", "kickoff_utc": ev["kickoff_utc"],
         "result": res, "pnl": settle.pnl(res, units, p["price"]),
         "final": f"{a} {as_}, {h} {hs}", "espn_event_id": ev["espn_event_id"],
         "committed_utc": first["committed_utc"], "commitment_sha256": first["sha256"],
         "hours_before_kickoff": round(hrs, 1), "graded_utc": market.iso(now)}
    e.update(_provenance(p))
    return e


def cmd_grade(dry_run=False, stores=None, now=None):
    now = now or market.now_utc()
    stores = stores or load_stores()
    ledger = load_ledger()
    done = {e["pick_id"] for e in ledger["entries"]}
    commits = load_commitments()["commitments"]
    new, refused, waiting, altered = [], [], 0, []
    booked = {e["pick_id"]: e for e in ledger["entries"]}
    for w in list_weeks():
        doc = load_week(w)
        if validate_week(w, doc):
            print(f"{w}: card has problems (run `check`); skipping its grading.")
            continue
        for p in doc.get("picks", []):
            if p["id"] in done:
                # ALREADY GRADED, SO THE LEDGER WINS - the entry is never
                # recomputed and never touched (House Rule 1). But a card edited
                # after grading would leave the page showing one pick beside
                # another pick's result, so the divergence is reported here and
                # shown on the card.
                b = booked[p["id"]]
                if b.get("commitment_sha256") and b["commitment_sha256"] != fingerprint(p):
                    altered.append(f"{p['id']}: the card was edited AFTER this pick was "
                                   f"graded {b.get('graded_utc')}. The ledger keeps the "
                                   f"original ({b['selection']} {b['price']:+d}, "
                                   f"{b['result']}) and is not recomputed.")
                continue
            ev, why = resolve_event(p, stores.get(p["sport"], {}))
            if ev is None:
                print(f"  {p['id']}: cannot resolve — {why}")
                continue
            r = grade_pick(p, w, ev, commits, now)
            if r is None:
                waiting += 1
                continue
            if isinstance(r, tuple):
                refused.append(r[1]); continue
            new.append(r)
            tag = r["result"] + (f" ({r['void_reason']})" if r["result"] == "VOID" else
                                 f" {r['pnl']:+.2f}u")
            print(f"  {r['pick_id']}: {r['selection']} {r['price']:+d} -> {tag}")
    for m in refused:
        print("  REFUSED " + m)
    for m in altered:
        print("  ALTERED-AFTER-GRADING " + m)
    print(f"grade: {len(new)} new, {waiting} waiting on finals, {len(refused)} refused, "
          f"{len(altered)} altered after grading, {len(done)} already booked")
    if new and not dry_run:
        ledger["entries"].extend(new)
        save_json(LEDGER, ledger)
        print(f"wrote {os.path.relpath(LEDGER, ROOT)}")
    elif dry_run:
        print("--dry-run: ledger untouched")
    # Refusals go red AFTER everything gradeable was booked: a tampered pick
    # must not hold the honest ones hostage, but it must not pass quietly.
    return 1 if (refused or altered) else 0


# ------------------------------------------------------------------ record --

def record(entries, sport=None, week=None):
    es = [e for e in entries if (sport is None or e["sport"] == sport)
          and (week is None or e["slate_week"] == week)]
    w = sum(1 for e in es if e["result"] == "WIN")
    l = sum(1 for e in es if e["result"] == "LOSS")
    p = sum(1 for e in es if e["result"] == "PUSH")
    v = sum(1 for e in es if e["result"] == "VOID")
    risked = sum(e["units"] for e in es if e["result"] in ("WIN", "LOSS", "PUSH"))
    raw = sum(e["pnl"] for e in es)
    # ROI from the unrounded sum: rounding first shifts it by up to 0.005/risked.
    return {"w": w, "l": l, "p": p, "v": v, "n": len(es), "risked": risked,
            "pnl": round(raw, 2), "roi": (raw / risked) if risked else None}


def rec_line(r):
    return f'{r["w"]}–{r["l"]}' + (f'–{r["p"]}' if r["p"] else "")


def rec_units(r):
    return f'{r["pnl"]:+.2f}u'


def rec_roi(r):
    return "—" if r["roi"] is None else f'{r["roi"] * 100:+.1f}%'


# ---------------------------------------------------------------- render --

EXTRA_CSS = """
  :root { --warn:#fab219; --serious:#ec835a; }
  .subnav { display:flex; gap:8px; flex-wrap:wrap; margin:6px 0 20px; }
  .subnav a { font-size:0.74rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase;
    color:var(--ink2); text-decoration:none; padding:6px 12px; border-radius:99px;
    border:1px solid var(--ring); background:var(--surface); }
  .subnav a:hover { border-color:var(--s1); color:var(--ink); }
  .subnav a.here { color:var(--ink); border-color:var(--s1); }
  .profile { display:flex; gap:16px; align-items:center; margin:8px 0 18px; }
  .avatar { flex:none; width:64px; height:64px; border-radius:50%; display:flex; align-items:center;
    justify-content:center; font-weight:800; font-size:1.05rem; letter-spacing:0.04em; color:#0d0d0d;
    background:linear-gradient(135deg,var(--s1),var(--s2)); }
  .profile .who { font-weight:800; font-size:1.05rem; color:var(--ink); }
  .profile .role { display:block; font-size:0.72rem; letter-spacing:0.12em; text-transform:uppercase; color:var(--muted); }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:8px 0 18px; }
  .tile { background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:14px 16px; }
  .tile .tl { display:block; font-size:0.66rem; text-transform:uppercase; letter-spacing:0.09em; color:var(--muted); }
  .tile .tv { display:block; font-size:1.5rem; font-weight:750; margin-top:2px; font-variant-numeric:tabular-nums; }
  .tile .td { display:block; font-size:0.76rem; color:var(--muted); font-variant-numeric:tabular-nums; }
  .pick { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:18px 18px 14px; margin-bottom:14px; }
  .pick.graded-WIN { border-color:rgba(12,163,12,0.55); }
  .pick.graded-LOSS { border-color:rgba(208,59,59,0.55); }
  .pick.graded-VOID { border-style:dashed; }
  .ph { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; flex-wrap:wrap; }
  .pnum { font-size:0.68rem; font-weight:800; letter-spacing:0.12em; text-transform:uppercase; color:var(--muted); }
  .pmatch { display:block; color:var(--ink2); font-size:0.88rem; margin-top:2px; }
  .sel { font-size:1.3rem; font-weight:800; color:var(--ink); margin:6px 0 2px; }
  .sel small { font-size:0.85rem; font-weight:600; color:var(--ink2); }
  .chips { display:flex; gap:6px; flex-wrap:wrap; }
  .chip { font-size:0.66rem; font-weight:700; letter-spacing:0.06em; padding:3px 9px; border-radius:99px;
    background:var(--surface2); border:1px solid var(--ring); color:var(--ink2); }
  .chip.play { color:var(--good); }
  .chip.status-OFFICIAL { color:var(--good); border-color:rgba(12,163,12,0.45); }
  .chip.status-LEAN { color:var(--warn); }
  .chip.status-PASS { color:var(--muted); }
  .conv { font-weight:700; }
  .against { border-left:3px solid var(--serious); padding-left:12px; margin:10px 0 0; }
  .altered { margin-top:10px; padding:10px 12px; border:1px solid var(--crit);
    border-left:4px solid var(--crit); border-radius:8px; background:rgba(208,59,59,0.08);
    font-size:0.8rem; color:var(--ink2); line-height:1.5; }
  .rgline { margin:16px 0 6px; padding:10px 14px; border:1px solid var(--ring);
    border-left:3px solid var(--warn); border-radius:10px; background:var(--surface);
    font-size:0.8rem; color:var(--ink2); line-height:1.5; }
  .chip.res-WIN { color:var(--good); } .chip.res-LOSS { color:var(--crit); }
  .chip.res-PUSH { color:var(--ink2); } .chip.res-VOID { color:var(--warn); }
  .prow { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:8px 12px;
    margin:12px 0 4px; padding:10px 12px; background:var(--surface2); border-radius:10px; }
  .plab { display:block; font-size:0.62rem; text-transform:uppercase; letter-spacing:0.09em; color:var(--muted); }
  .pval { font-size:0.9rem; font-weight:650; font-variant-numeric:tabular-nums; color:var(--ink); }
  .stars { color:var(--warn); letter-spacing:0.05em; }
  .why { margin-top:12px; }
  .why h4 { font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin:10px 0 4px; }
  .why p { margin-bottom:6px; font-size:0.92rem; }
  .fp { margin-top:10px; padding-top:10px; border-top:1px solid var(--grid); font-size:0.74rem; color:var(--muted); line-height:1.5; }
  .fp code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:0.7rem; color:var(--s1); word-break:break-all; }
  .fp .bad { color:var(--crit); font-weight:700; }
  .fp .pending { color:var(--warn); font-weight:700; }
  .spot { background:var(--surface); border:1px solid var(--ring); border-left:3px solid var(--s2); border-radius:12px; padding:16px 18px; margin-bottom:14px; }
  .spot h3 { margin:0 0 6px; }
  .spot h4 { font-size:0.72rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin:12px 0 4px; }
  .callout { background:var(--surface); border:1px solid var(--ring); border-left:3px solid var(--s1); border-radius:10px; padding:14px 16px; margin:16px 0; color:var(--ink2); font-size:0.9rem; }
  .res-WIN { color:var(--good); } .res-LOSS { color:var(--crit); } .res-PUSH { color:var(--ink2); } .res-VOID { color:var(--warn); }
  .quiet { color:var(--muted); font-size:0.8rem; }
"""

# House Rule 5, rendered AT THE POINT OF DECISION rather than only in the
# footer - the same placement build_site.py uses for the model's cards.
RG_LINE = ('<p class="rgline"><strong>21+.</strong> Open Ledger Sports is an analytics '
           'publication, not a sportsbook, and accepts no wagers. Nothing here is betting '
           'advice and no outcome is guaranteed. If you or someone you know has a gambling '
           'problem, call or text <strong>1-800-GAMBLER</strong>, or see the '
           '<a href="https://www.ncpgambling.org/responsible-gambling/safer-sports-betting/" '
           'rel="noopener">NCPG\'s safer sports betting resources</a>.</p>')

SUBNAV = [("/football/mercer/", "Spotlight"), ("/football/mercer/record/", "The Mercer Ledger"),
          ("/football/mercer/about/", "About D.J. Mercer"), ("/football/", "Model football record")]


def subnav(here):
    links = []
    for h, t in SUBNAV:
        cls = ' class="here"' if h == here else ""
        links.append(f'<a href="{h}"{cls}>{t}</a>')
    return '<nav class="subnav">' + "".join(links) + "</nav>"


def conviction_label(p):
    """Three words, never a number. See CONVICTIONS."""
    cv = str(p.get("conviction") or "").lower()
    return CONVICTION_LABEL.get(cv, "")


def profile_strip():
    return ('<div class="profile"><div class="avatar" aria-hidden="true">DJM</div>'
            '<div><span class="who">D.J. Mercer</span>'
            '<span class="role">Sports enthusiast · Football analyst · Open Ledger Sports</span>'
            '</div></div>')


def tiles(entries):
    parts = []
    for label, sport in (("NFL", "nfl"), ("College Football", "ncaaf"), ("Combined football", None)):
        r = record(entries, sport)
        void = f' · {r["v"]} void' if r["v"] else ""
        parts.append(f'<div class="tile"><span class="tl">{label}</span>'
                     f'<span class="tv">{rec_line(r)}</span>'
                     f'<span class="td">{rec_units(r)} · ROI {rec_roi(r)}{void}</span></div>')
    return '<div class="tiles">' + "".join(parts) + "</div>"


def commit_status(p, commits, ev):
    mine = [c for c in commits if c["pick_id"] == p["id"]]
    if not mine:
        return ('<span class="pending">Not yet fingerprinted.</span> This pick is not on the '
                'record until <code>mercer.py commit</code> stamps it. If its game starts first, '
                'it books as VOID.')
    first = min(mine, key=lambda c: c["committed_utc"])
    if first["sha256"] != fingerprint(p):
        return ('<span class="bad">Edited after it was fingerprinted.</span> The text you are '
                'reading does not match what was stamped at ' + E(first["committed_utc"]) +
                '. The grader refuses to book this pick until it is restored.')
    kick = market.parse_utc((ev or p).get("kickoff_utc"))
    stamped = market.parse_utc(first["committed_utc"])
    when = ""
    if kick and stamped:
        hrs = (kick - stamped).total_seconds() / 3600.0
        when = (f" — {hrs:.1f}h before kickoff" if hrs > 0
                else f' — <span class="bad">{-hrs:.1f}h AFTER kickoff; books as VOID</span>')
    return (f'Fingerprinted {E(first["committed_utc"])}{when}. '
            f'<code>sha256 {E(first["sha256"])}</code> — '
            f'<a href="{REPO_BLOB}data/mercer/commitments.json" rel="noopener">verify</a>')


def pick_card(i, p, entry, commits, ev):
    sport = SPORTS[p["sport"]]["label"]
    a, h = ev_names(p["sport"], ev) if ev else (p.get("away"), p.get("home"))
    kick = ev["kickoff_utc"] if ev else p.get("kickoff_utc")
    try:
        kick_txt = f'{nice_date(kick[:10])}, {et_time(kick)}'
    except Exception:
        kick_txt = E(str(kick))
    chips = [f'<span class="chip status-{OFFICIAL}">Official pick</span>']
    cls = ""
    if entry:
        cls = f' graded-{entry["result"]}'
        pnl = f' {entry["pnl"]:+.2f}u' if entry["result"] != "VOID" else ""
        chips.append(f'<span class="chip res-{entry["result"]}">{entry["result"]}{pnl}</span>')

    # ONCE GRADED, THE LEDGER IS THE SOURCE OF TRUTH FOR THE NUMBERS. The card
    # file is prose that a person can still edit; the ledger entry is the thing
    # that was committed and settled. Rendering the entry's selection, price and
    # stake means an edited card cannot restate a graded pick on its own page.
    src = entry if entry else p
    altered = ""
    if entry and entry.get("commitment_sha256") and entry["commitment_sha256"] != fingerprint(p):
        altered = ('<div class="altered"><strong>This card was edited after the pick was '
                   'graded.</strong> The numbers and result above are the committed ones, read '
                   'from the append-only ledger, and they are not recomputed. The written '
                   'reasoning below may differ from what was published before kickoff.</div>')

    units = float(src.get("units", 1))
    rows = [("Price", f'{src["price"]:+d}'),
            ("Stake", f"{units:g} unit{'s' if units != 1 else ''}")]
    if src.get("book"):
        rows.append(("Taken at", E(str(src["book"]))))
    if conviction_label(src):
        rows.append(("Conviction", f'<span class="conv">{conviction_label(src)}</span>'))
    if p.get("mercer_number"):
        rows.append(("Mercer's number (opinion)", E(str(p["mercer_number"]))))
    if entry and entry.get("final"):
        rows.append(("Final", E(entry["final"])))
    if entry and entry["result"] == "VOID":
        rows.append(("Void because", E(entry.get("void_reason", ""))))
    prow = "".join(f'<div><span class="plab">{k}</span><span class="pval">{v}</span></div>'
                   for k, v in rows)

    # THE CASE AGAINST IS NOT OPTIONAL AND IT IS NOT BURIED. Every other picks
    # page argues one side; this one prints the strongest reason it could be
    # wrong, in the same type size, on the same card, before the game.
    why = f'<h4>Why Mercer likes it</h4><p>{E(str(p.get("why", "")))}</p>'
    why += (f'<div class="against"><h4>The case against</h4>'
            f'<p>{E(str(p.get("case_against", "")))}</p></div>')
    if p.get("changes_my_mind"):
        why += (f'<h4>What would change my mind</h4>'
                f'<p>{E(str(p["changes_my_mind"]))}</p>')
    why += f'<h4>Status</h4><p><strong>{OFFICIAL} PICK</strong></p>'

    return (f'<article class="pick{cls}" id="{E(p["id"])}"><div class="ph"><div>'
            f'<span class="pnum">Mercer\'s pick #{i} · {sport}</span>'
            f'<span class="pmatch">{E(a)} @ {E(h)} · {kick_txt}</span></div>'
            f'<div class="chips">{"".join(chips)}</div></div>'
            f'<div class="sel">{E(str(src["selection"] if entry else selection_text(p)))} '
            f'<small>{src["price"]:+d}</small></div>'
            f'<div class="prow">{prow}</div>{altered}'
            f'<div class="why">{why}</div>'
            f'<div class="fp">{commit_status(p, commits, ev)}</div></article>')


def spotlight_block(s):
    body = ""
    for head, text in (s.get("sections") or {}).items():
        body += f'<h4>{E(str(head))}</h4><p>{E(str(text))}</p>'
    sub = f'<p class="quiet">{E(str(s["matchup"]))}</p>' if s.get("matchup") else ""
    return f'<div class="spot"><h3>{E(str(s.get("title", "Game spotlight")))}</h3>{sub}{body}</div>'


def game_result_note(item, stores):
    ev, _ = resolve_event(item, stores.get(item.get("sport"), {}))
    if ev and ev.get("final"):
        a, h = ev_names(item["sport"], ev)
        return f'<em>Final: {E(a)} {ev["away_score"]}, {E(h)} {ev["home_score"]}</em>'
    return ""


def _opinion_table(items, stores, col3, col4, key3, key4, status):
    """Leans and passes. The STATUS column is printed on every row on purpose:
    the taxonomy is only real if a reader can see which bucket a line is in."""
    rows = []
    for it in items:
        rows.append(
            f'<tr><td><span class="chip status-{status}">{status}</span></td>'
            f'<td>{E(SPORTS.get(it.get("sport"), {}).get("label", ""))}</td>'
            f'<td>{E(str(it.get("away")))} @ {E(str(it.get("home")))}<br>{game_result_note(it, stores)}</td>'
            f'<td><strong>{E(str(it.get(key3, "")))}</strong></td><td>{E(str(it.get(key4, "")))}</td></tr>')
    return (f'<div class="tablewrap"><table><thead><tr><th>Status</th><th>League</th>'
            f'<th>Game</th><th>{col3}</th><th>{col4}</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def week_body(week, doc, entries, commits, stores, show_title=False):
    picks = doc.get("picks", [])
    by_id = {e["pick_id"]: e for e in entries}
    parts = []
    wr = record(entries, week=week)
    line = (f'This week: <strong>{rec_line(wr)}</strong>, {rec_units(wr)}'
            if wr["n"] else "This week: nothing graded yet")
    n = len(picks)
    parts.append(f'<h2 id="card">Mercer\'s card · week of {nice_date(week)}</h2>'
                 f'<p class="mut">{line}. {n} official pick{"s" if n != 1 else ""}. '
                 f'Every one is fingerprinted before kickoff and graded here, win or lose.</p>')
    if show_title and doc.get("title"):
        parts.append(f'<h3>{E(str(doc["title"]))}</h3>')
    if doc.get("intro"):
        parts.append(f'<p class="lede">{E(str(doc["intro"]))}</p>')
    if not picks:
        parts.append('<p class="callout"><strong>No official pick this week.</strong> Nothing met '
                     'the standard for a play. Passing is a position too, and it is recorded as one '
                     'here rather than papered over with a manufactured pick.</p>')
    for i, p in enumerate(picks, 1):
        ev, _ = resolve_event(p, stores.get(p["sport"], {}))
        parts.append(pick_card(i, p, by_id.get(p["id"]), commits, ev))
    if picks:
        parts.append(RG_LINE)

    if doc.get("spotlight"):
        parts.append('<h2 id="spotlight">Game spotlight</h2>')
        parts.append('<p class="mut">A deeper look at the matchups Mercer found most interesting. '
                     'Analysis, not a pick, unless the same game appears on the card above.</p>')
        parts += [spotlight_block(s) for s in doc["spotlight"]]

    parts.append('<h2 id="leans">Mercer\'s leans</h2>')
    parts.append('<p class="mut"><strong>Only OFFICIAL picks touch the record.</strong> '
                 'Everything below this line is opinion: it is published so you can see what '
                 'Mercer was thinking, and it earns nothing, costs nothing and never appears '
                 'in the ledger.</p>')
    if doc.get("leans"):
        parts.append('<p class="mut">Games worth watching that did not meet the standard for an '
                     'official pick. Leans are opinion: they are published so you can see them, '
                     'and they are <strong>never</strong> graded into the record.</p>')
        parts.append(_opinion_table(doc["leans"], stores, "Lean", "Why it is not a pick",
                                    "lean", "note", LEAN))
    else:
        parts.append('<p class="mut">No leans this week.</p>')

    parts.append('<h2 id="pass">The pass list</h2>')
    if doc.get("passes"):
        parts.append('<p class="mut">Popular games or tempting numbers Mercer deliberately stayed '
                     'away from, and why. Also never graded.</p>')
        parts.append(_opinion_table(doc["passes"], stores, "The temptation", "Why it is a pass",
                                    "temptation", "reason", PASS))
    else:
        parts.append('<p class="mut">No passes listed this week.</p>')
    return "".join(parts)


HOW_THE_RECORD_WORKS = """
<h2>How the record works</h2>
<ul>
  <li><strong>Fingerprinted before kickoff, or not at all.</strong> Each official pick is hashed
  (SHA-256) and timestamped when it is filed, and that stamp is committed to a public repository.
  The tool <em>refuses</em> to stamp a pick whose game has already started, so a late selection
  never enters the record in the first place. The stamp is printed on every card.</li>
  <li><strong>Nothing is backfilled.</strong> This record begins at the first publicly committed
  pick. Older opinions cannot be added later, because every one of them would fail the test above.
  A short honest record is the point; a long retrospective one would be worthless.</li>
  <li><strong>Late is void.</strong> If a pick reaches grading with no stamp, or a stamp later than
  its kickoff, it books as <span class="res-VOID">VOID</span> with the reason, and it stays on the
  record where you can see it.</li>
  <li><strong>Edited is refused.</strong> If a pick's text no longer matches its stamp, the grader
  refuses to book it and says so on the page until it is restored.</li>
  <li><strong>Graded is frozen.</strong> Once a pick is settled, its selection, line, price, stake
  and result are never recomputed. Editing the card afterwards changes nothing in the ledger, and
  the card says plainly that it was edited.</li>
  <li><strong>The number is the number Mercer took.</strong> The line, the price and the book are
  recorded as they were at the moment of the pick, and are never quietly replaced by a better
  closing number.</li>
  <li><strong>Only OFFICIAL picks count.</strong> Leans and passes are published as opinion, carry
  no stake, and never touch the record.</li>
  <li><strong>Separate records.</strong> NFL and college football are kept apart, with a combined
  line, and none of it mixes with the site's model-driven football ledger.</li>
  <li><strong>Pushes return the stake.</strong> ROI is profit divided by units risked on decided
  picks (wins, losses and pushes); void picks are excluded from both.</li>
  <li><strong>Conviction is three words, not a percentage.</strong> Standard, Strong or Spotlight.
  Mercer has no calibrated model, so no number is invented to look like one.</li>
</ul>
<p class="mut">Not yet tracked: closing-line value on Mercer's picks, and whether Mercer and the
model agreeing on a game means anything. Agreement will be counted before it is ever described as
making a pick stronger. Both appear here when they are built, not before.</p>
"""


def pick_display_week(weeks, now=None):
    """Which week the hub shows.

    The week containing today if a card exists for it, else the most recent week
    already under way, else the newest card on disk. Chosen so that drafting
    NEXT week's card early does not replace the live one on the front page,
    which "newest file wins" would do.
    """
    if not weeks:
        return None
    today = current_week(now)
    if today in weeks:
        return today
    past = [w for w in weeks if w <= today]
    return past[-1] if past else weeks[-1]


def render_hub(entries, commits, stores, weeks=None, now=None):
    weeks = list_weeks() if weeks is None else weeks
    latest = pick_display_week(weeks, now)
    if latest:
        current = week_body(latest, load_week(latest), entries, commits, stores, show_title=True)
    else:
        current = ('<h2 id="card">Mercer\'s card</h2>'
                   '<p class="callout"><strong>No card has been filed yet.</strong> The first one '
                   'appears here once it is written and fingerprinted before kickoff. An empty '
                   'record and a hidden one look identical, so we say which this is.</p>')
    wl = "".join(f'<li><a href="/football/mercer/{w}/">Week of {nice_date(w)}</a>'
                 + ("" if w != latest else ' <span class="quiet">← shown above</span>')
                 + "</li>"
                 for w in reversed(weeks)) or "<li>No weeks published yet.</li>"
    inner = f'''
<div class="idx">
  <span class="kicker">D.J. Mercer Spotlight</span>
  <h1>The games are complicated. The record shouldn't be.</h1>
  <p class="lede">Football through the eyes of a fan. Picks through the discipline of an analyst.
  Every official pick is fingerprinted before kickoff and graded here in public, win or lose.
  <strong>No claim is made that these picks win.</strong> The record below is the only argument
  offered, and it is <a href="/football/mercer/about/">explained in full</a>.</p>
  {subnav("/football/mercer/")}
  {profile_strip()}
  <h2>The Mercer Ledger</h2>
  {tiles(entries)}
  <p class="mut">Append-only. <a href="/football/mercer/record/">Every graded pick</a>, including
  the losses and the voids. Only OFFICIAL picks count; leans and passes are opinion and are never
  graded.</p>
  {current}
  <h2>Every week</h2>
  <ul class="plain">{wl}</ul>
  <p class="backline"><a href="/football/mercer/about/">About D.J. Mercer</a> ·
  <a href="/football/">Model football record</a> · <a href="/">Today's board</a></p>
</div>'''
    write(OUT, inner, "D.J. Mercer Spotlight — Open Ledger Sports",
          "D.J. Mercer's NFL and college football picks, fingerprinted before kickoff and "
          "graded in public, win or lose. No outcome is guaranteed.")
    print(f"wrote football/mercer/index.html ({len(entries)} graded, {len(weeks)} weeks)")


def render_week(week, entries, commits, stores):
    doc = load_week(week)
    title = doc.get("title") or f"Mercer's card, week of {nice_date(week)}"
    inner = f'''
<div class="idx">
  <span class="kicker">D.J. Mercer Spotlight · week</span>
  <span class="postdate">{nice_date(week)}</span>
  <h1>{E(str(title))}</h1>
  {subnav("/football/mercer/")}
  {profile_strip()}
  {week_body(week, doc, entries, commits, stores)}
  <p class="backline"><a href="/football/mercer/">Spotlight</a> ·
  <a href="/football/mercer/record/">The Mercer Ledger</a> ·
  <a href="/football/mercer/about/">About D.J. Mercer</a></p>
</div>'''
    write(os.path.join(OUT, week), inner,
          f"D.J. Mercer, week of {nice_date(week)} — Open Ledger Sports",
          "D.J. Mercer's official football picks for the week, with the case for, the case "
          "against, and the fingerprint that proves when they were filed.")
    print(f"wrote football/mercer/{week}/index.html ({len(doc.get('picks', []))} picks)")


def record_opened(entries):
    """When this record starts, stated on the page so it cannot look longer
    than it is. Read from the ledger, not from a constant."""
    led = load_ledger()
    stamps = [e.get("committed_utc") for e in entries if e.get("committed_utc")]
    first = min(stamps) if stamps else None
    if first:
        return (f'<p class="mut">This record opened with its first committed pick on '
                f'<strong>{E(nice_date(first[:10]))}</strong>. Nothing before that date was '
                f'backfilled, and nothing can be: a pick is fingerprinted before kickoff or it '
                f'never enters the record.</p>')
    created = (led.get("created_utc") or "")[:10]
    when = f' on <strong>{E(nice_date(created))}</strong>' if created else ""
    return (f'<p class="mut">This record was opened empty{when} and is waiting for its first '
            f'committed pick. Nothing is backfilled, so it starts at zero rather than with a '
            f'history nobody could check.</p>')


def render_record(entries):
    if entries:
        rows = []
        for e in sorted(entries, key=lambda e: (e.get("kickoff_utc") or "", e["pick_id"])):
            void = f'<br><em>{E(e.get("void_reason", ""))}</em>' if e["result"] == "VOID" else ""
            rows.append(
                f'<tr><td>{E(e["slate_week"])}</td><td>{E(SPORTS[e["sport"]]["label"])}</td>'
                f'<td>{E(e["matchup"])}</td><td><strong>{E(e["selection"])}</strong></td>'
                f'<td class="num">{e["price"]:+d}</td>'
                f'<td>{E(str(e.get("book") or "—"))}</td>'
                f'<td class="num">{e["units"]:g}</td>'
                f'<td class="res-{e["result"]}">{e["result"]}{void}</td>'
                f'<td class="num">{e["pnl"]:+.2f}</td>'
                f'<td>{E(e.get("final", "—"))}</td>'
                f'<td class="num">{e.get("hours_before_kickoff", "—")}</td></tr>')
        table = (f'<div class="tablewrap"><table><thead><tr><th>Week</th><th>League</th>'
                 f'<th>Game</th><th>Pick</th><th>Price</th><th>Book</th><th>Units</th>'
                 f'<th>Result</th><th>P/L</th><th>Final</th>'
                 f'<th>Hours before kickoff</th></tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table></div>')
    else:
        table = ('<p class="mut"><strong>Nothing has been graded yet.</strong> This table is empty '
                 'because no Mercer pick has settled, not because anything was removed. An empty '
                 'record and a hidden one look identical, so we say which this is.</p>')
    inner = f'''
<div class="idx">
  <span class="kicker">D.J. Mercer Spotlight · record</span>
  <h1>The Mercer Ledger</h1>
  <p class="lede">Every official selection. Every result. No deleted picks, no disappearing bad
  weeks. <strong>No claim is made that these picks win</strong>; the table is the whole argument.</p>
  {subnav("/football/mercer/record/")}
  {record_opened(entries)}
  {tiles(entries)}
  {table}
  {RG_LINE}
  <p class="mut">Raw data: <a href="{REPO_BLOB}data/mercer/mercer_ledger.json" rel="noopener">mercer_ledger.json</a>
  · fingerprints: <a href="{REPO_BLOB}data/mercer/commitments.json" rel="noopener">commitments.json</a>.</p>
  {HOW_THE_RECORD_WORKS}
  <p class="backline"><a href="/football/mercer/">Spotlight</a> ·
  <a href="/football/mercer/about/">About D.J. Mercer</a> ·
  <a href="/football/">Model football record</a></p>
</div>'''
    write(os.path.join(OUT, "record"), inner, "The Mercer Ledger — Open Ledger Sports",
          "Every D.J. Mercer football pick, graded in public. Wins, losses, pushes and voids, "
          "append-only.")
    print(f"wrote football/mercer/record/index.html ({len(entries)} entries)")


ABOUT_HTML = """
<h2>Who</h2>
<p>D.J. Mercer is a sports enthusiast and independent football analyst who believes the best
sports analysis doesn't begin with a pick. It begins with a question.</p>
<ul>
  <li>What is the market telling us?</li>
  <li>What might the numbers be missing?</li>
  <li>Where does perception differ from reality?</li>
  <li>And, most importantly: is there actually enough evidence to make a play?</li>
</ul>
<p>Mercer's approach combines statistical analysis, market information, matchup research,
historical context, situational factors, and the part of the sport that numbers can't completely
capture: how teams actually play the game.</p>
<p>The goal isn't to predict every game. It's to identify the situations worth paying attention
to, and to be willing to say <strong>PASS</strong> when there isn't one.</p>
<p class="mut">No professional credentials are claimed, because none are needed to be judged.
D.J. Mercer is a pen name. The record is published under it, in full, and the record is the
only thing offered as evidence.</p>

<h2>NFL and college football</h2>
<p>Throughout the football season, the D.J. Mercer Spotlight follows the NFL and NCAA football
landscape with weekly analysis, including:</p>
<ul>
  <li><strong>Mercer's Card.</strong> The official selections D.J. Mercer is willing to put on the
  Open Ledger record. Every card carries four things: the pick, why Mercer likes it,
  <strong>the case against it</strong>, and what would change his mind.</li>
  <li><strong>Game Spotlight.</strong> A deeper look at selected matchups: the case for the play,
  the case against it, the numbers that matter, market movement, and what would make the analysis
  wrong.</li>
  <li><strong>Mercer's Leans.</strong> Games worth watching that don't meet the standard for an
  official pick. Published, labelled as opinion, never graded.</li>
  <li><strong>The Pass List.</strong> Popular games or tempting lines Mercer intentionally stays
  away from, along with the reason why.</li>
  <li><strong>The Mercer Ledger.</strong> Every official selection. Every result. Wins and losses.
  No deleted picks. No disappearing bad weeks. NFL and college kept as separate records.</li>
</ul>

<h2>The philosophy</h2>
<p>Sports betting content has traditionally been built around the prediction. D.J. Mercer is more
interested in the process behind it.</p>
<p>The objective isn't to convince you that a pick is guaranteed to win. There are no guarantees.
The objective is to make the reasoning visible so you can evaluate the evidence yourself.</p>
<p>That's the Open Ledger approach: <strong>show the work. Track the result. Study what happened.
Get better.</strong></p>

<h2>The case against</h2>
<p>Every official card states the strongest legitimate reason the pick could be wrong, in the same
type, on the same card, before the game is played. Not a disclaimer at the bottom. Not a hedge
added afterwards.</p>
<p>Most people selling picks tell you why they are right. The interesting half is the other one:
what would have to be true for this to be a bad bet, and what would change Mercer's mind. If that
section is ever missing from a card, the card does not publish - the tool that builds these pages
refuses it.</p>

<h2>No screenshot handicapping</h2>
<p>Anybody can post a winning ticket after the game. That's not analysis.</p>
<p>Official Mercer selections are fingerprinted and published before the event and become part of
the permanent record. The winners stay. So do the losers. Because credibility shouldn't come from
one great weekend. It should come from what the ledger says over time.</p>

<h2>Mercer and the model</h2>
<p>Open Ledger Sports also publishes a <a href="/football/">model-driven football record</a> built
from market prices under a pre-registered rule. The two are deliberately separate: the model is
algorithmic and makes no expectation claim; Mercer is a person doing research. Neither record is
allowed to borrow credibility from the other.</p>
<p>There are three layers, and they answer three different questions. The
<strong>model</strong> says what the system sees. The <strong>Spotlight</strong> says what the
analyst sees. The <strong>ledger</strong> says what actually happened.</p>
<p class="mut">When Mercer and the model land on the same side, that is currently just an
observation. It will be counted before it is ever described as making a pick stronger, and it will
never quietly increase a stake. Agreement that has not been measured is a coincidence with a
marketing department.</p>
"""


def render_about():
    inner = f'''
<div class="idx">
  <span class="kicker">D.J. Mercer Spotlight · about</span>
  <h1>About D.J. Mercer</h1>
  <p class="lede">Sports enthusiast. Football analyst. The editorial voice of Open Ledger Sports
  for the NFL and college football season.</p>
  {subnav("/football/mercer/about/")}
  {profile_strip()}
  {ABOUT_HTML}
  {HOW_THE_RECORD_WORKS}
  <p class="callout">Analysis and commentary are for informational and entertainment purposes
  only. Open Ledger Sports is not a sportsbook and does not accept wagers. No outcome is
  guaranteed. 21+. If you or someone you know has a gambling problem, call or text
  1-800-GAMBLER.</p>
  <p class="backline"><a href="/football/mercer/">Spotlight</a> ·
  <a href="/football/mercer/record/">The Mercer Ledger</a> ·
  <a href="/football/">Model football record</a></p>
</div>'''
    write(os.path.join(OUT, "about"), inner, "About D.J. Mercer — Open Ledger Sports",
          "Who D.J. Mercer is, how the Spotlight works, and why every pick is fingerprinted "
          "before kickoff and graded in public.")
    print("wrote football/mercer/about/index.html")


def write(dirpath, inner, title, desc):
    shell = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{E(title)}</title>
<meta name="description" content="{E(desc)}">
<link rel="icon" href="/assets/favicon-32.png" sizes="32x32" type="image/png">
<link rel="shortcut icon" href="/favicon.ico">
<style>{PAGE_CSS}
  ul.plain {{ list-style:none; padding-left:0; }}
  ul.plain li {{ padding:3px 0; }}
{EXTRA_CSS}</style>
</head>
<body>
<header class="site"><div class="wrap sitebar">
  <img class="sitelogo" src="/assets/logo.jpg" width="440" height="440" alt="">
  <div><span class="markname"><span class="open">OPEN LEDGER</span> SPORTS</span>
    <small class="marksub">D.J. Mercer Spotlight · picks on the record, before kickoff</small></div>
  <nav class="navlinks">
    <a href="/">Today's Board</a>
    <a href="/football/">Football</a>
    <a href="/football/mercer/" class="here">D.J. Mercer</a>
    <a href="/blog/">Blog</a>
    <a href="/odds/">Odds</a>
  </nav>
</div></header>
<div class="wrap">
{inner}
<footer class="legal"><p>{LEGAL}</p></footer>
</div>
</body>
</html>'''
    os.makedirs(dirpath, exist_ok=True)
    with io.open(os.path.join(dirpath, "index.html"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(shell)


def cmd_render(stores=None, now=None):
    stores = stores if stores is not None else load_stores()
    entries = load_ledger()["entries"]
    commits = load_commitments()["commitments"]
    bad, good = 0, []
    for w in list_weeks():
        doc = load_week(w)
        errs = validate_week(w, doc)
        if errs:
            for e in errs:
                print("  PROBLEM " + e)
            print(f"{w}: not rendering a card with problems.")
            bad += 1
            continue
        render_week(w, entries, commits, stores)
        good.append(w)
    # The hub shows the latest VALID week; a half-written card must not take
    # the front page down with it.
    render_hub(entries, commits, stores, good, now)
    render_record(entries)
    render_about()
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="The D.J. Mercer Spotlight: check, commit, grade, render.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate week files and resolve every game against ESPN")
    c.add_argument("--week")
    c = sub.add_parser("commit", help="fingerprint every pick not yet stamped")
    c.add_argument("--week")
    c.add_argument("--dry-run", action="store_true",
                   help="show what would be stamped and refused; write nothing")
    c = sub.add_parser("grade", help="book finished picks into the Mercer ledger")
    c.add_argument("--dry-run", action="store_true")
    sub.add_parser("render", help="write football/mercer/")
    args = ap.parse_args()
    if args.cmd == "check":
        return cmd_check(args.week)
    if args.cmd == "commit":
        return cmd_commit(args.week, dry_run=args.dry_run)
    if args.cmd == "grade":
        return cmd_grade(args.dry_run)
    if args.cmd == "render":
        return cmd_render()
    return 2


if __name__ == "__main__":
    sys.exit(main())
