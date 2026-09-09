#!/usr/bin/env python3
"""
Open Ledger Sports — post the daily record to social media.

Runs after nightly grading (grade-ledger.yml), alongside the Discord recap, and
posts YESTERDAY's graded results plus the running ledger. Reads the same
data/ledger.json the site and the Discord recap read, so the three never
disagree about the record.

  x         short post (<=280 chars) to X / Twitter.
  facebook  longer post to a Facebook Page.

TWO STRATEGIES, TWO LEDGERS, NEVER MIXED (v0.15; house rules 6 and 9). The
Qualified Plays ledger (data/ledger.json) has gone quiet for weeks at a time
while the always-on Daily Pick (data/daily_ledger.json) graded every day, so a
social poster bound to the Qualified ledger alone simply falls silent. It now
falls back, in this strict priority order:

  1. settled Qualified entries for the date  -> the Qualified recap, unchanged.
  2. else a settled Daily Pick entry         -> a recap LABELED "Daily Pick".
  3. else                                    -> nothing_to_post, as before.

The two records are computed by two separate functions off two separate files
and are never summed, averaged, or shown in the same post. That is structural,
not stylistic: the schemas differ (the Daily ledger has no `pnl`, `units`,
`units_net` or `roi_pct` — it carries pnl_paper / pnl_staked / units_staked and
paper_units_net / paper_roi_pct), so a blend would raise rather than mislead.
The Daily Pick post always says it is the lower-bar strategy, and it leads with
whichever basis is OFFICIAL for that pick: the paper figure while the strategy
allocates 0u, and the RECORDED ALLOCATION once a human authorizes units (the
paper number then survives only as a labelled comparison). Calling an allocated
pick paper-only, or leading with its paper figure, would break house rule 8 —
copy must describe what the site actually does.

ALLOCATION IS NOT A WAGER. This pipeline records unit allocations. It does not
place bets, never contacts a sportsbook, and has no way to verify that money
moved: grade.py:246 reads the number the engine wrote and nothing more. Public
copy therefore says "recorded allocation", never "real stake" or "staked" —
house rule 5's "analytics not a sportsbook, no bets accepted", stated in the
post itself rather than left to the footer.

NO DATE EVER AUTHORIZES AN ALLOCATION. engine.py:164 holds DAILY_PICK_UNITS =
0.0 and engine.py:165's DAILY_PROVING_END is never compared to anything — it is
a note for a human review, not a switch. This file only reports what the ledger
already records; it can neither start nor schedule an allocation.

Two brand rules are baked in and must stay that way (house rules 1 and 5):
  - Losing days post too. The text is generated from the ledger, wins and losses
    alike; there is no path here that skips a bad day. Voids post too.
  - Every post carries responsible-gambling language (21+, 1-800-GAMBLER, not
    betting advice).

IDEMPOTENT, like the email sender and unlike post_discord.py: it records
"posted" per (date, platform) in data/post_status.json (which the workflow
commits) and refuses to re-post that date, so a repeat grading run never
double-posts. Only a failed or missing post is retried.

Missing credentials for a platform? It skips that platform (exit 0) and never
fails the grading run — the ledger and site are unaffected either way.

Run:  python scripts/post_social.py x        [YYYY-MM-DD] [--dry-run]
      python scripts/post_social.py facebook [YYYY-MM-DD] [--dry-run]
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SITE = (os.environ.get("SITE_URL", "").rstrip("/") or "https://openledgersports.com")

CHIP = {"WIN": "✅", "LOSS": "❌", "VOID": "⚪"}
RG = "21+ · 1-800-GAMBLER · not betting advice"
X_LIMIT = 280

# A result only counts once it is settled. grade.py appends nothing else today,
# so on live data this filter is a no-op; it exists so that an entry written in
# any other state can never be reported as a graded result.
SETTLED = ("WIN", "LOSS", "VOID")

STATUS_PATH = os.path.join(ROOT, "data", "post_status.json")
STATUS_KEEP = 30   # matches post_discord.py / send_email.py

# Module-level so the offline self-test can point them at fixtures. Nothing in
# this file ever WRITES to either ledger — both are read-only here.
LEDGER_PATH = os.path.join(ROOT, "data", "ledger.json")              # Qualified
DAILY_LEDGER_PATH = os.path.join(ROOT, "data", "daily_ledger.json")  # Daily Pick


def load_status():
    if os.path.exists(STATUS_PATH):
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"posts": []}


def already_posted(date, mode):
    """True if this date already went to this platform. The committed status log
    is the cross-run memory the next grading run checks out."""
    for p in load_status().get("posts", []):
        if p.get("date") == date and p.get("mode") == mode and p.get("result") == "posted":
            return True
    return False


def record(mode, date, result, status=None, detail=""):
    """Append the outcome to data/post_status.json (shared with the Discord and
    email posters). Never records a token. Telemetry must never break a run."""
    try:
        log = load_status()
        log["posts"] = [p for p in log.get("posts", [])
                        if not (p.get("date") == date and p.get("mode") == mode)]
        log["posts"].append({
            "date": date, "mode": mode, "result": result,
            "http_status": status, "detail": str(detail)[:200],
            "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        log["posts"] = sorted(log["posts"], key=lambda p: p["at_utc"])[-STATUS_KEEP:]
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:
        print(f"NOTE: could not record social status: {exc}")


def _load(path):
    if not os.path.exists(path):
        return {"entries": [], "aggregates": None}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_ledger():
    """The Qualified Plays ledger. Read-only here."""
    return _load(LEDGER_PATH)


def load_daily_ledger():
    """The Daily Pick ledger (v0.15). A SEPARATE record with a separate schema;
    read-only here, and never merged with the Qualified ledger."""
    return _load(DAILY_LEDGER_PATH)


def _settled(entries, date):
    return [e for e in entries if e.get("date") == date and e.get("result") in SETTLED]


def _day_counts(entries):
    return (sum(1 for e in entries if e["result"] == "WIN"),
            sum(1 for e in entries if e["result"] == "LOSS"),
            sum(1 for e in entries if e["result"] == "VOID"))


def _day_record(w, l, v):
    """1-0, 0-1, and 0-0-1 for a void — the void is only shown when there is
    one, so a normal day reads the way it always has."""
    return f"{w}-{l}" + (f"-{v}" if v else "")


def recap(date):
    """The day's graded QUALIFIED entries + that ledger's running aggregates, or
    None if nothing settled for that date. Unchanged in shape and output."""
    L = load_ledger()
    entries = _settled(L["entries"], date)
    if not entries or not L.get("aggregates"):
        return None
    agg = L["aggregates"]
    _d = datetime.strptime(date, "%Y-%m-%d")
    w, l, v = _day_counts(entries)
    return {
        "nice": f"{_d:%A, %B} {_d.day}",
        "entries": entries,
        "day_pnl": sum(e["pnl"] for e in entries),
        "day_w": w,
        "day_l": l,
        "day_v": v,
        "record": agg["record"],
        "units_net": agg["units_net"],
        "roi_pct": agg.get("roi_pct"),
    }


def daily_recap(date):
    """The day's settled DAILY PICK entries + that ledger's own aggregates, or
    None. Deliberately a separate function reading a separate file: it touches
    no Qualified figure, and the Qualified builders touch none of these.

    Carries BOTH bases so the builders can lead with whichever is real:
    `day_pnl_paper` / `paper_units_net` / `paper_roi_pct` for the paper track the
    Daily ledger keeps continuously, and `staked_units` / `staked_pnl` /
    `staked_net` for units a human actually authorized. `staked_units` is the
    single switch the builders read; it is 0 for every entry written so far."""
    L = load_daily_ledger()
    entries = _settled(L.get("entries", []), date)
    if not entries or not L.get("aggregates"):
        return None
    agg = L["aggregates"]
    _d = datetime.strptime(date, "%Y-%m-%d")
    w, l, v = _day_counts(entries)
    return {
        "nice": f"{_d:%A, %B} {_d.day}",
        "entries": entries,
        "day_pnl_paper": sum(e.get("pnl_paper") or 0 for e in entries),
        "day_w": w,
        "day_l": l,
        "day_v": v,
        "record": agg["record"],
        "paper_units_net": agg.get("paper_units_net"),
        "paper_roi_pct": agg.get("paper_roi_pct"),
        "basis": (entries[0].get("paper_basis") or 0),
        "staked_units": sum(e.get("units_staked") or 0 for e in entries),
        "staked_pnl": sum(e.get("pnl_staked") or 0 for e in entries),
        "staked_net": agg.get("staked_units_net"),
    }


def select_recap(date):
    """The priority rule, in one place so X and Facebook can never disagree
    about which strategy a given night belongs to:

      1. Qualified, whenever it has a settled entry for the date.
      2. else Daily Pick, when it does.
      3. else None -> the caller records nothing_to_post, exactly as before.

    Returns (kind, recap) with kind in {"qualified", "daily"}, or (None, None).
    """
    r = recap(date)
    if r is not None:
        return "qualified", r
    d = daily_recap(date)
    if d is not None:
        return "daily", d
    return None, None


def _running(r):
    roi = f" · ROI {r['roi_pct']:+.1f}%" if r["roi_pct"] is not None else ""
    return f"{r['record']} · {r['units_net']:+.2f}u net{roi}"


def _running_daily(r):
    """The Daily Pick's OWN running PAPER line. Every number comes from
    daily_ledger.json. The caller supplies the "(paper)" label, so this does not
    repeat the word inside the figures."""
    roi = f" · ROI {r['paper_roi_pct']:+.1f}%" if r["paper_roi_pct"] is not None else ""
    net = r["paper_units_net"] if r["paper_units_net"] is not None else 0.0
    return f"{r['record']} · {net:+.2f}u{roi}"


def _running_daily_short(r):
    """Same line with the ROI dropped — the first thing to give way on X, because
    a paper ROI computed on zero risk is the least load-bearing figure in the
    post and the disclosure must never be what gets trimmed."""
    net = r["paper_units_net"] if r["paper_units_net"] is not None else 0.0
    return f"{r['record']} · {net:+.2f}u"


def _basis_str(r):
    """The flat paper accounting basis, as it appears in the disclosure.
    grade.py hardcodes paper_basis 0.25 on every Daily entry (grade.py:246), so
    the fallback is the documented value rather than a misleading 0.00."""
    return f"{(r['basis'] or 0.25):.2f}"


LEGAL = ("Every pick is committed to the public record before first pitch and graded "
         "here after — wins and losses alike. Analytics, not betting advice. 21+. "
         "If you or someone you know has a gambling problem, call or text 1-800-GAMBLER.")

# THE DAILY PICK DISCLOSURE. Four facts that must all survive on every platform:
#   1. published for transparency
#   2. tracked separately
#   3. paper-only, on a flat {basis}u accounting basis
#   4. contributes 0u to the official Qualified record and bankroll
#
# WHY IT NO LONGER SAYS "tracked separately at 0 units". That phrasing sat in the
# same post as "+0.25u paper" and a "+1.38u" running line, and the two readings of
# "unit" — the notional basis a result is SCORED on, and the stake actually RISKED —
# were never distinguished. A reasonable reader saw a contradiction, or concluded
# the 0u was boilerplate. The wording below separates the two senses explicitly:
# the basis is named as paper accounting, and the 0u is scoped to what the pick
# contributes to the Qualified record. House rule 8 — copy must describe what the
# site actually does today.
#
# X gets a compressed wording only because 280 chars cannot hold the long one —
# never a softer one. Facts are never dropped to make a post fit; the ledger line
# degrades first (see _x_daily).
DISCLOSURE_FB = ("Daily Picks are published for transparency and tracked separately on a "
                 "paper-only flat {basis}u basis. They contribute 0u to the official "
                 "Qualified record and bankroll.")
DISCLOSURE_X = ("Daily Picks: published for transparency, tracked separately, paper-only at a "
                "flat {basis}u basis, contributing 0u to the official Qualified record and "
                "bankroll.")

# Once a human authorizes a non-zero allocation, "paper-only" and "contributes
# 0u" become FALSE of that pick, and printing them anyway is the false claim
# house rule 8 exists to prevent.
#
# WHAT THIS COPY MAY NOT SAY. The pipeline records a UNIT ALLOCATION. It does not
# place a bet, does not talk to a sportsbook, and has no way to confirm money
# ever moved — grade.py:246 simply reads the number the engine wrote. So the copy
# says "recorded allocation", never "real stake" or "staked", which would assert
# a wager the software never placed and cannot verify. House rule 5 is explicit
# that this is analytics, not a sportsbook, and that no bets are accepted; the
# no-wager sentence states that in the same breath as the allocation so the two
# are never read apart.
DISCLOSURE_FB_STAKED = ("Daily Picks are published for transparency and tracked separately from "
                        "the Qualified Plays. A human authorized and recorded a {stake}u "
                        "allocation on this pick. It is tracked exclusively in the Daily Pick "
                        "ledger and does not enter the official Qualified record or bankroll. "
                        "A recorded allocation is an accounting entry, not a placed bet — Open "
                        "Ledger Sports does not place or accept wagers.")
DISCLOSURE_X_STAKED = ("Published for transparency: a human-authorized allocation recorded in the "
                       "Daily Pick ledger only, never the official Qualified record or bankroll. "
                       "We do not place or accept wagers.")


def _x_trim(head, led, tag):
    """Shared X assembly: the tagline is the only line that may be dropped, so
    the record and 1-800-GAMBLER always survive."""
    text = "\n".join([head, led, tag, RG])
    if len(text) > X_LIMIT:                       # drop the tagline first
        text = "\n".join([head, led, RG])
    return text[:X_LIMIT]


def _x_qualified(r):
    day = _day_record(r["day_w"], r["day_l"], r["day_v"])
    return _x_trim(
        f"Yesterday graded: {day}, {r['day_pnl']:+.2f}u.",
        f"Ledger: {_running(r)}.",
        "Every pick public before first pitch, every result graded — wins and losses.")


def _x_daily(r):
    """Same shape, but every figure is the Daily Pick's own and is named as such
    in BOTH lines, so the post can never be read as the Qualified record.

    The trim order is DELIBERATELY different from the Qualified post's. There the
    last line is a tagline and dropping it costs nothing. Here it is the
    disclosure, and a paper unit figure published without it is the misleading
    thing — so the LEDGER line degrades first (ROI dropped, then the whole line)
    and the disclosure is never what gives way. At today's figures the full form
    fits with ~3 characters to spare, so this is a guard, not the usual path."""
    day = _day_record(r["day_w"], r["day_l"], r["day_v"])
    staked = r["staked_units"]
    if staked:
        # A recorded allocation exists: the OFFICIAL allocated result leads. The
        # paper figure is a second accounting basis for the same pick, so it does
        # not appear on X at all — omitted rather than shown unlabelled beside
        # the recorded one. The disclosure is long here (it must also say we
        # place no wagers), so the ladder normally lands on [head, disc, RG].
        net = r["staked_net"] if r["staked_net"] is not None else 0.0
        head = f"Daily Pick graded: {day}, {r['staked_pnl']:+.2f}u allocated."
        led = f"Daily Pick ledger (recorded allocation): {r['record']} · {net:+.2f}u net."
        led_short = f"Daily Pick ledger (recorded allocation): {net:+.2f}u net."
        disc = DISCLOSURE_X_STAKED
    else:
        head = f"Daily Pick graded: {day}, {r['day_pnl_paper']:+.2f}u paper."
        led = f"Daily Pick ledger (paper): {_running_daily(r)}."
        led_short = f"Daily Pick ledger (paper): {_running_daily_short(r)}."
        disc = DISCLOSURE_X.format(basis=_basis_str(r))
    for lines in ([head, led, disc, RG],
                  [head, led_short, disc, RG],
                  [head, disc, RG]):
        text = "\n".join(lines)
        if len(text) <= X_LIMIT:
            return text
    return text[:X_LIMIT]


def build_x_text(date):
    """<=280 chars, and DELIBERATELY carries no URL.

    X's API prices a post with a link at $0.20 vs $0.015 without one — ~13x — and
    X's own feed downranks link posts, so the site link is left off here. It lives
    in the account bio instead. Do NOT add SITE back to this post without knowing
    it multiplies the per-post cost. (The Facebook post keeps its link: Meta's API
    is not metered.) Drops the tagline before the record or the legal line — the
    ledger numbers and 1-800-GAMBLER always survive the trim."""
    kind, r = select_recap(date)
    if kind is None:
        return None
    return _x_qualified(r) if kind == "qualified" else _x_daily(r)


def _fb_qualified(r):
    day = _day_record(r["day_w"], r["day_l"], r["day_v"])
    lines = [f"\U0001f4ca Open Ledger Sports — results for {r['nice']}", ""]
    for e in r["entries"]:
        lines.append(f"{CHIP.get(e['result'], '•')} {e['pick']}: "
                     f"{e.get('final_score') or 'void'} ({e['pnl']:+.2f}u)")
    lines += [
        "",
        f"Day: {day}, {r['day_pnl']:+.2f}u",
        f"Running ledger: {_running(r)}",
        "",
        LEGAL,
        "",
        SITE,
    ]
    return "\n".join(lines)


def _fb_daily(r):
    """The Daily Pick's own card. Labeled in the headline, in the day line, in
    the ledger line and in a paragraph that says outright it is a different
    strategy with a different record — house rules 8 and 9."""
    day = _day_record(r["day_w"], r["day_l"], r["day_v"])
    staked = r["staked_units"]
    lines = [f"\U0001f3af Open Ledger Sports — Daily Pick result for {r['nice']}", ""]
    for e in r["entries"]:
        # The per-pick figure is whichever basis is OFFICIAL for this pick: the
        # recorded allocation when a human authorized one, the paper basis
        # otherwise. Neither wording asserts that a bet was placed.
        amt = (f"{(e.get('pnl_staked') or 0):+.2f}u allocated" if staked
               else f"{(e.get('pnl_paper') or 0):+.2f}u paper")
        lines.append(f"{CHIP.get(e['result'], '•')} {e['pick']}: "
                     f"{e.get('final_score') or 'void'} ({amt})")
    lines.append("")
    if staked:
        # HIERARCHY: with a human-authorized allocation the OFFICIAL recorded
        # result leads. The paper basis becomes the secondary accounting for the
        # same pick, so it survives only as an explicitly labelled comparison —
        # never as the headline, and never unlabelled beside the recorded figure.
        net = r["staked_net"] if r["staked_net"] is not None else 0.0
        lines += [
            f"Day: {day}, {r['staked_pnl']:+.2f}u on a {staked:.2f}u recorded allocation",
            f"Daily Pick ledger (recorded allocation): {r['record']} · {net:+.2f}u net",
            f"Paper comparison, flat {_basis_str(r)}u basis (not the recorded allocation): "
            f"{_running_daily(r)}",
        ]
        disc = DISCLOSURE_FB_STAKED.format(stake=f"{staked:.2f}")
    else:
        lines += [
            f"Day: {day}, {r['day_pnl_paper']:+.2f}u paper",
            f"Daily Pick ledger (paper): {_running_daily(r)}",
        ]
        disc = DISCLOSURE_FB.format(basis=_basis_str(r))
    lines += [
        "",
        disc,
        "",
        LEGAL,
        "",
        SITE,
    ]
    return "\n".join(lines)


def build_fb_text(date):
    """No length cap on a Page post — the full per-pick result list."""
    kind, r = select_recap(date)
    if kind is None:
        return None
    return _fb_qualified(r) if kind == "qualified" else _fb_daily(r)


def post_to_x(text):
    """POST /2/tweets with OAuth 1.0a User Context (4 static keys, no refresh)."""
    import requests
    from requests_oauthlib import OAuth1
    auth = OAuth1(os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
                  os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"])
    r = requests.post("https://api.twitter.com/2/tweets",
                      json={"text": text}, auth=auth, timeout=30)
    if r.status_code < 300:
        pid = ""
        try:
            pid = str(r.json().get("data", {}).get("id", ""))
        except Exception:
            pass
        return True, r.status_code, pid
    return False, r.status_code, r.text[:300]


def post_to_facebook(text):
    """POST /{page-id}/feed with a Page access token."""
    import requests
    pid = os.environ["FB_PAGE_ID"]
    r = requests.post(f"https://graph.facebook.com/v21.0/{pid}/feed",
                      data={"message": text, "access_token": os.environ["FB_PAGE_ACCESS_TOKEN"]},
                      timeout=30)
    if r.status_code < 300:
        post_id = ""
        try:
            post_id = str(r.json().get("id", ""))
        except Exception:
            pass
        return True, r.status_code, post_id
    return False, r.status_code, r.text[:300]


# mode -> (text builder, poster, required env var names)
def routes():
    return {
        "x": (build_x_text, post_to_x,
              ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")),
        "facebook": (build_fb_text, post_to_facebook,
                     ("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN")),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = args[0] if args else "x"
    table = routes()
    if mode not in table:
        print(f"Unknown platform {mode!r}. Use one of: {', '.join(table)}.")
        return
    build, poster, env_names = table[mode]

    # Grading posts yesterday's results, so default to yesterday like the recap.
    default = (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    date = args[1] if len(args) > 1 else default
    dry = "--dry-run" in sys.argv

    text = build(date)
    if text is None:
        print(f"No graded entries for {date} — nothing to post to {mode}.")
        if not dry:
            record(mode, date, "nothing_to_post")
        return
    if dry:
        print(f"[{mode}] {len(text)} chars:\n{text}")
        return

    missing = [n for n in env_names if not os.environ.get(n)]
    if missing:
        print(f"NOTE: {', '.join(missing)} not set — skipping {mode} "
              f"(ledger and site are unaffected).")
        record(mode, date, "no_config", detail=f"missing {', '.join(missing)}")
        return
    if already_posted(date, mode):
        print(f"{mode} post for {date} already sent — refusing to re-post.")
        return

    try:
        ok, status, detail = poster(text)
    except Exception as exc:
        print(f"WARNING: {mode} post failed to send: {exc}")
        record(mode, date, "failed", detail=str(exc))
        return
    if not ok:
        # Never fail the pipeline over a social post. "failed" does not block a
        # later retry; only "posted" does.
        print(f"WARNING: {mode} post failed ({status}): {detail}")
        record(mode, date, "failed", status=status, detail=detail)
        return
    print(f"Posted {mode} record for {date}" + (f" (id {detail})." if detail else "."))
    record(mode, date, "posted", status=status, detail=detail)


if __name__ == "__main__":
    main()
