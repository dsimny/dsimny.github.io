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


# Fields a public commitment entry may carry. THE WHITELIST IS THE CONTROL:
# anything not named here never reaches the public commitment log, so a future
# field cannot leak a pick by being added to the pick schema and forgotten here.
#
# Why matchup / espn_event_id / conviction are present even though they are not
# a fingerprint: the LOCKED public card has to be renderable from this file
# ALONE, with no decryption key, because no CI render step has one. They are
# non-actionable by inspection - knowing a play exists on a named game, and how
# confident the analyst is, does not tell you the side, the number or the price.
PUBLIC_COMMIT_FIELDS = (
    "pick_id", "slate_week", "sport", "sha256", "committed_utc", "kickoff_utc",
    "matchup", "espn_event_id", "conviction", "revealed", "revealed_utc",
    "pregame_public",
)
# Never allowed in the public commitment log, at any point before reveal.
ACTIONABLE_FIELDS = (
    "selection", "market", "side", "team", "line", "price", "units", "book",
    "why", "case_against", "changes_my_mind", "quantitative_case", "walk_away",
    "mercer_number",
)


def public_commit_entry(d):
    """Strip a commitment down to the publishable whitelist."""
    return {k: d[k] for k in PUBLIC_COMMIT_FIELDS if k in d}


def load_commitments():
    return load_json(COMMITMENTS, {
        "_note": ("Per-pick fingerprints for the D.J. Mercer Spotlight. Each "
                  "entry is the SHA-256 of one pick's canonical JSON, stamped when "
                  "`mercer.py commit` first saw it. Append-only: a pick is never "
                  "re-stamped. The grader books a pick only if its EARLIEST stamp "
                  "matches the pick as it stands now AND precedes kickoff; "
                  "otherwise the entry is VOID (never stamped, or stamped late) "
                  "or REFUSED (edited after stamping). The git history of this "
                  "file is the public timestamp. "
                  "PROOF ONLY: this file is public and deliberately carries NO "
                  "actionable pick detail before reveal - no side, line, price, "
                  "book, stake or reasoning. The sha256 proves what was committed "
                  "without disclosing it; the card itself is encrypted until the "
                  "play is graded."),
        "commitments": []})


def week_paths(week):
    """(plaintext, encrypted) paths for a slate week.

    PLAINTEXT IS THE REVEALED STATE, NOT THE WORKING STATE. Before a pick is
    graded its card lives only in the .enc file; the .json appears when the
    reveal happens and is what makes the record publicly checkable afterwards.
    """
    return (os.path.join(WEEKS, f"{week}.json"),
            os.path.join(WEEKS, f"{week}.enc"))


def list_weeks():
    out = set()
    for p in glob.glob(os.path.join(WEEKS, "*.json")) + glob.glob(os.path.join(WEEKS, "*.enc")):
        w = os.path.splitext(os.path.basename(p))[0]
        if WEEK_RE.match(w):
            out.add(w)
    return sorted(out)


def load_week(week):
    """The card, from plaintext if revealed, else by decrypting.

    Returns None when the card is encrypted and no key is available. That is a
    NORMAL state for a public render, not an error: the locked card is built
    from commitments.json, which needs no key.
    """
    plain, enc = week_paths(week)
    if os.path.exists(plain):
        return load_json(plain, None)
    if os.path.exists(enc) and crypto_box.have_key():
        return crypto_box.decrypt_from(enc)
    return None


def save_week(week, doc, revealed):
    """Write the card: plaintext once revealed, encrypted while it is not.

    The CI guard is the load-bearing part. Locally a missing key just means
    plaintext, which keeps development workable. In Actions a missing key means
    the secret vanished, and writing the card in the clear would publish every
    unrevealed selection to a public repository.
    """
    plain, enc = week_paths(week)
    if revealed:
        save_json(plain, doc)
        if os.path.exists(enc):
            os.remove(enc)
        return "plaintext (revealed)"
    crypto_box.refuse_plaintext_in_ci(f"the Mercer card for {week}")
    if crypto_box.have_key():
        os.makedirs(WEEKS, exist_ok=True)
        crypto_box.encrypt_to(enc, doc)
        if os.path.exists(plain):
            os.remove(plain)
        return "encrypted"
    save_json(plain, doc)
    return "plaintext (no key; local only)"


def fingerprint(pick):
    """SHA-256 of the pick's canonical JSON, same canon as the model's board."""
    return crypto_box.sha256_of(pick)


def commit_for(pick_id, commits):
    """The single commitment entry for a pick id, or None."""
    return next((c for c in commits if c.get("pick_id") == pick_id), None)


def is_revealed(c):
    """One reveal rule, used by every renderer and by the grader.

    `pregame_public` is the Week 1 escape hatch and nothing else: that card was
    published in plaintext before this control existed, and pretending otherwise
    would be a lie the git history contradicts. It is set once, by hand, with a
    reason, and it is never set on a card that was actually withheld.
    """
    if not c:
        return False
    return bool(c.get("revealed")) or bool(c.get("pregame_public"))


def week_is_revealed(week, commits):
    """A week is revealed only when EVERY committed pick in it is.

    Deliberately all-or-nothing for the surrounding prose. Title, intro, leans
    and passes are written as one piece and can allude to the play; releasing
    them while any pick in the week is still live would leak by implication.
    """
    ws = [c for c in commits if c.get("slate_week") == week]
    return bool(ws) and all(is_revealed(c) for c in ws)


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
            a, h = ev_names(p["sport"], ev)
            # PROOF ONLY. The sha256 commits the pick; nothing here discloses it.
            # public_commit_entry() enforces the whitelist so a field added to the
            # pick schema later cannot leak by being copied in here by habit.
            entry = public_commit_entry({
                "pick_id": p["id"], "slate_week": w, "sport": p["sport"],
                "sha256": fingerprint(p), "committed_utc": market.iso(now),
                "kickoff_utc": ev["kickoff_utc"], "matchup": f"{a} @ {h}",
                "espn_event_id": ev["espn_event_id"],
                "conviction": str(p.get("conviction") or "").lower() or None,
                "revealed": False, "revealed_utc": None})
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
        # SEAL THE CARD. Stamping without encrypting would leave the actionable
        # selection readable in a public repo, which is the defect this exists
        # to close. Weeks with nothing yet revealed are sealed; a week already
        # revealed stays plaintext.
        for w in weeks:
            doc = load_week(w)
            if doc is None:
                continue
            if week_is_revealed(w, com["commitments"]):
                continue
            how = save_week(w, doc, revealed=False)
            print(f"  sealed {w}: {how}")
    elif added and dry_run:
        print("  --dry-run: commitments.json untouched, card not sealed")
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
        # REVEAL, AND ONLY NOW. Ordering matters and is not arbitrary: the
        # fingerprint was verified inside grade_pick before any of these entries
        # existed, so nothing reaches the ledger on an unverified card, and
        # nothing is disclosed until it has been booked. House Rule 7 - held
        # before, published in full after, win or lose.
        com = load_commitments()
        booked = {e["pick_id"] for e in new}
        touched = set()
        for c in com["commitments"]:
            if c["pick_id"] in booked and not c.get("revealed"):
                c["revealed"] = True
                c["revealed_utc"] = market.iso(now)
                touched.add(c["slate_week"])
        if touched:
            save_json(COMMITMENTS, com)
            for w in sorted(touched):
                if not week_is_revealed(w, com["commitments"]):
                    print(f"  {w}: partly graded; card stays sealed until every "
                          f"pick in it is booked")
                    continue
                doc = load_week(w)
                if doc is None:
                    print(f"  {w}: REVEALED in the log, but the card could not be "
                          f"read to publish (no key?). Plaintext not written.")
                    continue
                print(f"  {w}: revealed -> {save_week(w, doc, revealed=True)}")
    elif dry_run:
        print("--dry-run: ledger untouched, nothing revealed")
    # Refusals go red AFTER everything gradeable was booked: a tampered pick
    # must not hold the honest ones hostage, but it must not pass quietly.
    return 1 if (refused or altered) else 0


# -------------------------------------------------------------- delivery --
#
# WHERE PREMIUM MEMBERS ACTUALLY GET THE PICK, and why it is not the website.
#
# GitHub Pages is static. There is no server, no session and no authentication,
# so the site CANNOT decide who may read a page. Any "members only" area built
# on it would be decoration over content that is already downloadable, which is
# exactly the failure this whole change exists to fix. Shipping a JavaScript
# gate would be worse than the original defect, because it would look solved.
#
# So the public site stays locked for everyone and the actionable card is
# delivered through the channel that already has real access control: the
# Discord members channel, whose role is granted and revoked by Whop on the
# subscription. That gate is not ours to fake - it is enforced by Discord.
#
# Idempotent per (week, mode) through data/post_status.json, the same file and
# schema post_discord.py and discord.py already use, so a re-run cannot
# double-post to members.

MEMBERS_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL_MEMBERS"
STATUS_PATH = os.path.join(ROOT, "data", "post_status.json")
STATUS_KEEP = 200


def _status_load():
    return load_json(STATUS_PATH, {"posts": []})


def already_delivered(week):
    for p in _status_load().get("posts", []):
        if (p.get("date") == week and p.get("mode") == "mercer_members"
                and p.get("result") == "posted"):
            return True
    return False


def _status_record(week, result, status=None, detail=""):
    log = _status_load()
    log["posts"] = [p for p in log.get("posts", [])
                    if not (p.get("date") == week and p.get("mode") == "mercer_members")]
    log["posts"].append({"date": week, "mode": "mercer_members", "result": result,
                         "http_status": status, "detail": str(detail)[:200],
                         "at_utc": market.iso(market.now_utc())})
    log["posts"] = sorted(log["posts"], key=lambda p: p["at_utc"])[-STATUS_KEEP:]
    save_json(STATUS_PATH, log)


def member_messages(week, doc, commits):
    """The full actionable card, for the members channel only."""
    picks = doc.get("picks", [])
    head = (f"**D.J. MERCER — week of {nice_date(week)}**\n"
            f"{len(picks)} official selection{'s' if len(picks) != 1 else ''}. "
            f"Committed and fingerprinted before kickoff; this is the full card, "
            f"including the case against.")
    embeds = []
    for i, p in enumerate(picks, 1):
        c = commit_for(p["id"], commits) or {}
        units = float(p.get("units", 1))
        lines = [
            f"**{selection_text(p)}  {p['price']:+d}**",
            f"Taken at **{p.get('book') or 'unspecified'}**  ·  stake **{units:g}u**"
            + (f"  ·  **{conviction_label(p)}**" if conviction_label(p) else ""),
            "",
            f"__Why Mercer likes it__\n{p.get('why', '')}",
            "",
            f"__The case against__\n{p.get('case_against', '')}",
        ]
        if p.get("changes_my_mind"):
            lines += ["", f"__What would change my mind__\n{p['changes_my_mind']}"]
        if c.get("sha256"):
            lines += ["", f"Commitment `{c['sha256'][:24]}…` at {c.get('committed_utc','')}"]
        body = "\n".join(lines)
        embeds.append({"title": f"Pick #{i} — {p.get('away','')} @ {p.get('home','')}",
                       "description": body[:4000], "color": 0x3987E5})
    msgs = [{"content": head, "embeds": embeds[:10]}]
    for extra in range(10, len(embeds), 10):
        msgs.append({"embeds": embeds[extra:extra + 10]})
    return msgs


def cmd_deliver(week=None, dry_run=False, now=None):
    """Post the full card to the premium members channel."""
    now = now or market.now_utc()
    week = week or current_week(now)
    hook = os.environ.get(MEMBERS_WEBHOOK_ENV, "").strip()
    doc = load_week(week)
    if doc is None:
        print(f"{week}: card unreadable here (sealed and no key). Nothing delivered.")
        return 1
    if not doc.get("picks"):
        print(f"{week}: no official picks; nothing to deliver.")
        return 0
    if already_delivered(week) and not dry_run:
        print(f"{week}: already delivered to members; refusing to double-post.")
        return 0
    commits = load_commitments()["commitments"]
    unstamped = [p["id"] for p in doc["picks"] if not commit_for(p["id"], commits)]
    if unstamped:
        # Delivering before the public commitment exists would hand members a
        # pick with no public proof behind it - the wrong order.
        print(f"{week}: REFUSING to deliver - not fingerprinted yet: {unstamped}")
        return 1
    msgs = member_messages(week, doc, commits)
    if dry_run or not hook:
        if not hook:
            print(f"  {MEMBERS_WEBHOOK_ENV} is not set - printing instead of posting.")
        for i, m in enumerate(msgs, 1):
            print(f"--- members message {i}/{len(msgs)} ---")
            if m.get("content"):
                print(m["content"])
            for e in m.get("embeds", []):
                print(f"  [embed] {e['title']}")
                print("    " + e["description"].replace("\n", "\n    "))
        return 0
    import requests
    for i, m in enumerate(msgs, 1):
        try:
            r = requests.post(hook, json=m, timeout=20)
        except Exception as exc:
            _status_record(week, "failed", None, str(exc))
            print(f"  delivery FAILED on message {i}: {exc}")
            return 1
        if r.status_code >= 300:
            _status_record(week, "failed", r.status_code, r.text[:160])
            print(f"  delivery FAILED on message {i}: HTTP {r.status_code}")
            return 1
    _status_record(week, "posted", 200, f"{len(msgs)} message(s)")
    print(f"{week}: delivered the full card to members ({len(msgs)} message(s))")
    return 0


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
  .pick.locked { border-style:dashed; border-color:rgba(57,135,229,0.45); }
  .lockedsel { color:var(--s1); letter-spacing:0.02em; }
  .chip.lockchip { color:var(--s1); }
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


PREMIUM_URL = os.environ.get("WHOP_CHECKOUT_URL", "").strip()


def premium_cta():
    """The upgrade block. Omitted entirely when no checkout URL is configured.

    Mirrors page.py: the site never advertises something that cannot be bought,
    and the switch is one repository variable rather than an edit.
    """
    if not PREMIUM_URL:
        return ('<p class="mut">Premium membership is not open for signups right now. '
                'The selection publishes here in full once the game is graded.</p>')
    return (f'<a class="joinbtn" href="{E(PREMIUM_URL)}" rel="noopener">'
            f'Join Premium</a>')


def locked_card(i, c):
    """The PUBLIC pregame card, built from the commitment log ALONE.

    It takes the commitment entry, never the pick, and that is the whole point.
    The actionable fields are not omitted from a template that has them in
    scope - they are never in scope. There is no branch of this function that
    could print a side, a number or a price, so no future edit can leak one by
    forgetting a condition.

    No CSS hiding, no blur, no display:none, no collapsed markup, no data
    attributes, no inline JSON. What is absent from the page is absent from the
    source, because it was never read.
    """
    sport = SPORTS.get(c.get("sport"), {}).get("label", "")
    kick = c.get("kickoff_utc") or ""
    try:
        kick_txt = f'{nice_date(kick[:10])}, {et_time(kick)}'
    except Exception:
        kick_txt = E(str(kick))
    sha = str(c.get("sha256") or "")
    conv = CONVICTION_LABEL.get(str(c.get("conviction") or "").lower())
    rows = [("Committed", E(str(c.get("committed_utc") or "unknown")))]
    if conv:
        rows.append(("Conviction", f'<span class="conv">{conv}</span>'))
    prow = "".join(f'<div><span class="plab">{k}</span><span class="pval">{v}</span></div>'
                   for k, v in rows)
    return (f'<article class="pick locked" id="{E(str(c.get("pick_id","")))}">'
            f'<div class="ph"><div>'
            f'<span class="pnum">Mercer\'s pick #{i} · {E(sport)}</span>'
            f'<span class="pmatch">{E(str(c.get("matchup","")))} · {kick_txt}</span></div>'
            f'<div class="chips"><span class="chip lockchip">Premium · locked</span></div>'
            f'</div>'
            f'<div class="sel lockedsel">Premium pick locked</div>'
            f'<div class="prow">{prow}</div>'
            f'<div class="why"><p>This selection was committed before kickoff and '
            f'fingerprinted to the Open Ledger record. The exact wager, the price, the '
            f'book, the stake and the full reasoning are available to Premium members '
            f'now, and publish here for everyone once the game is graded — win or '
            f'lose.</p>'
            f'<p class="mut">Nothing on this page before kickoff can tell you what the '
            f'selection is. That is deliberate: the proof below is what makes the record '
            f'checkable, not the pick.</p>'
            f'{premium_cta()}</div>'
            f'<div class="fp">Commitment fingerprint '
            f'<code>sha256 {E(sha)}</code> — recorded '
            f'{E(str(c.get("committed_utc") or ""))}, before the '
            f'{kick_txt} kickoff. '
            f'<a href="{REPO_BLOB}data/mercer/commitments.json" rel="noopener">verify</a>'
            f'</div></article>')


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
    """One week's public body, locked or revealed PER PICK.

    THE DOC MAY BE None AND THAT IS NORMAL. A public render has no decryption
    key, so the card is unreadable and every pick renders from the commitment
    log. When the doc IS readable - locally, or after reveal - an unrevealed
    pick STILL renders locked. That asymmetry is the control: having the
    plaintext in memory must never be sufficient to publish it, or a developer
    running `render` on their own machine would silently commit the leak.
    """
    doc = doc or {}
    by_id = {e["pick_id"]: e for e in entries}
    wk_commits = [c for c in commits if c.get("slate_week") == week]
    picks = doc.get("picks", [])
    # Commitments are the spine, not the card: they exist for every stamped pick
    # whether or not the card can be read here.
    order = [c for c in wk_commits] or []
    by_pid = {p.get("id"): p for p in picks}
    fully = week_is_revealed(week, commits)

    parts = []
    wr = record(entries, week=week)
    line = (f'This week: <strong>{rec_line(wr)}</strong>, {rec_units(wr)}'
            if wr["n"] else "This week: nothing graded yet")
    n = len(order) or len(picks)
    parts.append(f'<h2 id="card">Mercer\'s card · week of {nice_date(week)}</h2>'
                 f'<p class="mut">{line}. {n} official pick{"s" if n != 1 else ""}. '
                 f'Every one is fingerprinted before kickoff and graded here, win or lose.</p>')
    # Title, intro, leans and passes are written as one piece and can allude to
    # the play, so they wait until the whole week is out.
    if fully and show_title and doc.get("title"):
        parts.append(f'<h3>{E(str(doc["title"]))}</h3>')
    if fully and doc.get("intro"):
        parts.append(f'<p class="lede">{E(str(doc["intro"]))}</p>')
    if not order and not picks:
        parts.append('<p class="callout"><strong>No official pick this week.</strong> Nothing met '
                     'the standard for a play. Passing is a position too, and it is recorded as one '
                     'here rather than papered over with a manufactured pick.</p>')

    if order:
        for i, c in enumerate(order, 1):
            p = by_pid.get(c.get("pick_id"))
            if is_revealed(c) and p is not None:
                ev, _ = resolve_event(p, stores.get(p["sport"], {}))
                parts.append(pick_card(i, p, by_id.get(p["id"]), commits, ev))
            else:
                parts.append(locked_card(i, c))
    else:
        # No commitments yet: an unstamped draft. Never render its contents.
        for i, p in enumerate(picks, 1):
            parts.append(
                f'<article class="pick locked"><div class="ph"><div>'
                f'<span class="pnum">Mercer\'s pick #{i}</span>'
                f'<span class="pmatch">{E(str(p.get("away","")))} @ '
                f'{E(str(p.get("home","")))}</span></div></div>'
                f'<div class="sel lockedsel">Not yet committed</div>'
                f'<div class="why"><p class="mut">This pick has not been '
                f'fingerprinted yet, so it is not on the record and nothing about '
                f'it is published.</p></div></article>')
    if order or picks:
        parts.append(RG_LINE)
    if not fully:
        parts.append('<p class="mut">Leans, the pass list and the week\'s written '
                     'introduction publish with the picks once every selection in this '
                     'week has been graded.</p>')
        return "".join(parts)

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
  (SHA-256) and timestamped when it is filed. The tool <em>refuses</em> to stamp a pick whose game
  has already started, so a late selection never enters the record in the first place. The stamp is
  printed on every card.</li>
  <li><strong>A pick counts only once its fingerprint is public.</strong> A stamp sitting on a
  private machine proves nothing to you, so it is not treated as evidence. The commitment is the
  moment the fingerprint reaches the public repository, before kickoff, where you or anyone else
  can read the timestamp without our help. Both times are printed on every card.</li>
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
  <li><strong>Conviction is three words, not a percentage.</strong>
  <em>Standard</em> qualifies for the ledger. <em>Strong</em> is materially better than an
  ordinary qualifying play. <em>Spotlight</em> is reserved for genuinely exceptional setups and is
  expected to be rare, so there will be weeks with none at all. Conviction never changes the stake
  by itself and is not a probability estimate. Mercer has no calibrated model, so no number is
  invented to look like one, and the ladder itself will be checked against results in public.</li>
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
    doc = load_week(week)          # None when sealed and no key: expected
    # THE WEEK TITLE IS PART OF THE CARD. Mercer's own titles summarise the
    # week ("one play, two passes...") and can hint at the selection, so the
    # public heading stays generic until the week is fully revealed.
    if week_is_revealed(week, commits) and (doc or {}).get("title"):
        title = doc["title"]
    else:
        title = f"Mercer's card, week of {nice_date(week)}"
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
          "D.J. Mercer's official football selections for the week, committed and "
          "fingerprinted before kickoff and revealed in full after grading.")
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
<p>There is a stricter version of that promise, and it is the one that actually binds:
<strong>a selection does not count until its fingerprint is public before kickoff.</strong> A
timestamp on a private machine is not evidence of anything, because you cannot see it. The
fingerprint has to be pushed to the public repository while the game is still unplayed, and every
card prints both the moment it was stamped and how long before kickoff that was.</p>

<h2>Conviction, and why Spotlight should be rare</h2>
<p>Three levels, no percentages. <strong>Standard</strong> qualifies for the ledger.
<strong>Strong</strong> is materially better than an ordinary qualifying play.
<strong>Spotlight</strong> is reserved for genuinely exceptional setups.</p>
<p>There will be weeks with no Spotlight selection at all, and that is the point. A label applied
every week carries no information. Conviction never changes the stake on its own, and it is not a
probability estimate: it is an ordering of one analyst's confidence, published in advance so the
record can be read back by level. If Spotlight picks do not outperform Standard ones over a real
sample, that is a finding about the ladder, and it gets published like everything else.</p>

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
  <img class="sitelogo" src="/assets/branding/ols-horizontal-on-dark-transparent.svg" width="1200" height="200" style="width:300px;max-width:100%;height:auto;border-radius:0" alt="Open Ledger Sports">
  <div>
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
        if doc is None:
            # Sealed and unreadable here. Correct for a public build: the locked
            # card comes from commitments.json, which needs no key.
            print(f"{w}: sealed (no key) - rendering locked cards from commitments")
            render_week(w, entries, commits, stores)
            good.append(w)
            continue
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
    c = sub.add_parser("deliver", help="post the full card to the premium members channel")
    c.add_argument("--week")
    c.add_argument("--dry-run", action="store_true")
    sub.add_parser("render", help="write football/mercer/")
    args = ap.parse_args()
    if args.cmd == "check":
        return cmd_check(args.week)
    if args.cmd == "commit":
        return cmd_commit(args.week, dry_run=args.dry_run)
    if args.cmd == "grade":
        return cmd_grade(args.dry_run)
    if args.cmd == "deliver":
        return cmd_deliver(args.week, args.dry_run)
    if args.cmd == "render":
        return cmd_render()
    return 2


if __name__ == "__main__":
    sys.exit(main())
