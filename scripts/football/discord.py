#!/usr/bin/env python3
"""
Open Ledger Sports — football Discord delivery (gap G).

Two modes, two channels, and the split is the product:

  free   -> DISCORD_WEBHOOK_URL          the free play, in full
  slate  -> DISCORD_WEBHOOK_URL_MEMBERS  the FULL SLATE plus the premium play

THE MEMBERS' POST IS THE PRODUCT, NOT A BONUS. `docs/FOOTBALL_LAUNCH.md` gap F
records why: the selection rule is public and deterministic, so printing every
covered game's numbers publicly before kickoff would hand over the premium play
exactly. The whole reasoned slate is therefore what members buy - timing and
coverage, per fp-v0.1 section 5 - and the public page carries the free play, the
coverage summary and the NO MARKET list until the week is graded.

IDEMPOTENT, DELIBERATELY DIFFERENT FROM post_discord.py. CLAUDE.md records that
the MLB poster is intentionally NOT idempotent: a re-run re-posts one pick, which
is a small, tolerable duplicate. This posts a WEEK - up to a dozen messages
carrying ~57 games - and re-running it would bury the channel it is meant to
serve. So both modes check data/post_status.json first and skip if that slate
week already went out.

The status modes are `fb_free` and `fb_slate`, distinct from `pick`/`board`/
`email`, for the reason send_email.py documents: record() deletes any existing
(key, mode) row, so a shared mode would let one sender wipe another's guard and
double-post. Keyed by SLATE WEEK rather than date, because football's unit is a
week.

DEGRADE, NEVER DIE. Missing webhook, missing board, HTTP failure: log it, record
it, exit 0. A delivery problem must never fail the run that produced the board.

Run:
  python scripts/football/discord.py free  --week 2026-09-01 --dry-run
  python scripts/football/discord.py slate --week 2026-09-01 --dry-run
  python scripts/football/discord.py slate --week 2026-09-01
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import delivery_policy
import page as fbpage                                # noqa: E402
from post_discord import webhook_host_ok, FOOTER     # noqa: E402

ROOT = os.path.join(HERE, "..", "..")
STATUS_PATH = os.path.join(ROOT, "data", "post_status.json")
STATUS_KEEP = 30
SITE = (os.environ.get("SITE_URL", "").strip()
        or "https://openledgersports.com").rstrip("/")

FREE_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
MEMBERS_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL_MEMBERS", "")

# Research delivery uses a DEDICATED webhook, never the official or member
# channels.  The variable is read here so it is visible at module level;
# the gate (RESEARCH_DELIVERY_ENABLED) controls whether it is ever used.
RESEARCH_WEBHOOK = os.environ.get("DISCORD_RESEARCH_WEBHOOK", "")

BLUE, GREEN, GREY = 0x2C7BE5, 0x2E9E5B, 0x6B7280

# KEEP MESSAGE TEXT INSIDE cp1252. Discord renders any unicode fine, but
# --dry-run prints these to a console, and on Windows that console is cp1252: a
# "->" arrow (U+2192) is outside it and crashed the first real dry-run with
# UnicodeEncodeError. Em-dashes and middots are inside cp1252 and are fine, so
# this is not an ASCII rule - it is "nothing exotic in text a human previews".
# The dry-run is how copy gets checked before it reaches members, and a preview
# that only works on the CI runner is not a preview.

# Discord's own limits. Embed descriptions cap at 4096 and the TOTAL characters
# across all embeds in one message cap at 6000, which is the binding constraint
# here: a writeup plus its numbers runs ~700 characters, so ten embeds would
# overflow a limit that ten-per-message alone would not catch.
MAX_EMBEDS = 10
MAX_EMBED_CHARS = 5200          # under 6000, with headroom for titles
PACE = 1.2                      # seconds between messages; webhooks rate-limit


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------

def load_status():
    if os.path.exists(STATUS_PATH):
        try:
            with io.open(STATUS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            pass
    return {"posts": []}


def already_posted(week, mode):
    for p in load_status().get("posts", []):
        if (p.get("date") == week and p.get("mode") == mode
                and p.get("result") == "posted"):
            return True
    return False


def record(week, mode, result, status=None, detail=""):
    """Same file and schema as post_discord.py / send_email.py."""
    try:
        log = load_status()
        log["posts"] = [p for p in log.get("posts", [])
                        if not (p.get("date") == week and p.get("mode") == mode)]
        log["posts"].append({
            "date": week, "mode": mode, "result": result,
            "http_status": status, "detail": str(detail)[:200],
            "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        log["posts"] = sorted(log["posts"], key=lambda p: p["at_utc"])[-STATUS_KEEP:]
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        with io.open(STATUS_PATH, "w", encoding="utf-8", newline="\n") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:                         # noqa: BLE001
        print(f"NOTE: could not record post status: {exc}")


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------

NOCLAIM = ("No expectation claim is made. Two published studies found this "
           "market cannot be out-forecast at the moment we can act, so this is "
           "sold as process and receipts, not as an edge. Staked at 0 units, "
           "and every play is graded in public.")


def money(p):
    return f"{p:+d}" if isinstance(p, int) else str(p)


def game_embed(g, color=GREY, label=None):
    lines = []
    if not label:
        lines.append("MARKET COVERAGE ONLY - not a recommended play.")
        if g.get("selection_reason"):
            lines.append(g["selection_reason"])
    lines.append(f"Kickoff (UTC): {g.get('kickoff_utc', 'unknown')} | "
                 f"Historical capture: {g.get('t24_capture', 'unknown')}")
    if g.get("writeup"):
        lines.append(g["writeup"])
    lines.append(
        f"**{g.get('side')}** {money(g.get('best_price'))} at "
        f"{g.get('best_book')} | {g.get('books_at_best')} Tier-1 books at/near it")
    fair = g.get("fair_side")
    lines.append(
        f"Market-implied probability (proportional de-vig): {fair * 100:.1f}% | "
        f"Median-market overround {g.get('raw_overround_pts')} percentage points; "
        f"cross-book best-price overround **{g.get('eff_overround_pts')}** "
        f"percentage points | {g.get('n_books')} books. "
        "These are market margins, not expected returns.")
    off = g.get("offshore_best")
    if off:
        lines.append(f"offshore colour only: {money(off.get('price'))} "
                     f"at {off.get('book')}")
    title = f"{g.get('league','')} - {g.get('matchup','')}"
    if label:
        title = f"{label} — {title}"
    return {"title": title[:256], "description": "\n".join(lines)[:4096],
            "color": color}


def chunk(embeds):
    """Group embeds under BOTH Discord limits: count and total characters."""
    out, cur, chars = [], [], 0
    for e in embeds:
        n = len(e.get("title", "")) + len(e.get("description", ""))
        if cur and (len(cur) >= MAX_EMBEDS or chars + n > MAX_EMBED_CHARS):
            out.append(cur)
            cur, chars = [], 0
        cur.append(e)
        chars += n
    if cur:
        out.append(cur)
    return out


def free_messages(b, week):
    g = b.get("free")
    url = f"{SITE}/football/{week}/"
    if not g:
        return [{"username": "Open Ledger Sports",
                 "content": (f"**Football — week of {week}**\nNo qualifying "
                             f"free play this week. Passing is a position.\n"
                             f"{url}\n_{FOOTER}_")}]
    return [{
        "username": "Open Ledger Sports",
        "content": (f"**Football — the free play, week of {week}**\n{url}"),
        "embeds": [game_embed(g, GREEN, "FREE PLAY")],
    }, {
        "username": "Open Ledger Sports",
        "content": f"_{NOCLAIM}_\n_{FOOTER}_",
    }]


def slate_messages(b, week):
    url = f"{SITE}/football/{week}/"
    head = (f"**Football — the full slate, week of {week}**\n"
            f"{b.get('n_covered', 0)} games covered | "
            f"{b.get('n_excluded', 0)} no market | "
            f"coverage: {b.get('coverage_status', 'covered')}\n"
            f"{url}")
    msgs = [{"username": "Open Ledger Sports", "content": head}]

    prem, free = b.get("premium"), b.get("free")
    top = []
    if prem:
        top.append(game_embed(prem, BLUE, "PREMIUM PLAY · 0 units"))
    if free:
        top.append(game_embed(free, GREEN, "FREE PLAY"))
    if top:
        msgs.append({"username": "Open Ledger Sports", "embeds": top})

    rest = [g for g in b.get("games", []) if g.get("tier") == "slate"]
    for i, group in enumerate(chunk([game_embed(g) for g in rest])):
        msgs.append({"username": "Open Ledger Sports",
                     "content": (f"**The rest of the slate** "
                                 f"({len(rest)} games, tightest market first)"
                                 if i == 0 else None),
                     "embeds": group})

    nm = b.get("no_market", [])
    if nm:
        lines = [f"• {n.get('matchup','')} — {n.get('reason','')}" for n in nm]
        body = "\n".join(lines)
        # Named, never silently dropped (spec s.3). Clip rather than omit, and
        # say so, because a truncated list that looks complete is worse than a
        # short one that admits it.
        if len(body) > 1600:
            body = body[:1600].rsplit("\n", 1)[0] + f"\n… full list at {url}"
        msgs.append({"username": "Open Ledger Sports",
                     "content": f"**No market ({len(nm)})**\n{body}"})

    msgs.append({"username": "Open Ledger Sports",
                 "content": f"_{NOCLAIM}_\n_{FOOTER}_"})
    return [{k: v for k, v in m.items() if v is not None} for m in msgs]


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------

def send(webhook, messages, dry=False):
    """Post each message in order. Returns (ok, last_status, detail)."""
    if dry:
        for i, m in enumerate(messages, 1):
            print(f"--- message {i}/{len(messages)} ---")
            if m.get("content"):
                print(m["content"])
            for e in m.get("embeds", []):
                print(f"  [embed] {e['title']}")
                print("    " + e["description"].replace("\n", "\n    "))
        return True, None, "dry-run"
    last = None
    for i, m in enumerate(messages, 1):
        for attempt in range(3):
            try:
                r = requests.post(webhook, json=m, timeout=20)
            except requests.RequestException as exc:
                return False, None, f"message {i}: {exc}"
            last = r.status_code
            if r.status_code == 429:
                # Honour Discord's own backoff rather than guessing.
                wait = 2.0
                try:
                    wait = float(r.json().get("retry_after", 2.0))
                except ValueError:
                    pass
                print(f"    rate limited, waiting {wait:.1f}s")
                time.sleep(min(wait + 0.2, 10))
                continue
            if r.status_code >= 300:
                return False, r.status_code, f"message {i}: {r.text[:160]}"
            break
        else:
            return False, last, f"message {i}: still rate limited after 3 tries"
        time.sleep(PACE)
    return True, last, f"{len(messages)} messages"


MODES = {
    "free":  ("fb_free",  FREE_WEBHOOK,    "DISCORD_WEBHOOK_URL",         free_messages),
    "slate": ("fb_slate", MEMBERS_WEBHOOK, "DISCORD_WEBHOOK_URL_MEMBERS",
              slate_messages),
}

# ---------------------------------------------------------------------------
# Research delivery — completely separate from official modes above.
# Reads only from data/football/research/; uses DISCORD_RESEARCH_WEBHOOK;
# gated by RESEARCH_DELIVERY_ENABLED (independent of OFFICIAL_DELIVERY_ENABLED).
# ---------------------------------------------------------------------------

RESEARCH_VERSION_ID = "fp-v0.4-market-observation-v1"
RESEARCH_DIR        = os.path.join(ROOT, "data", "football", "research")
RESEARCH_STATUS_KEY = "research_board"   # distinct from fb_free / fb_slate
RESEARCH_PREREG_PATH = os.path.join(ROOT, "data", "football",
                                    "research_preregistrations.json")

# Colour for research embeds: amber — visually distinct from official blue/green.
AMBER = 0xF59E0B

# The disclaimer that must appear in the FIRST message of every research post.
# Every sentence is a factual statement from the preregistration document.
RESEARCH_HEADER = (
    "**🔬 Research Observation — 0 units — Not a recommendation.**\n"
    f"Version: `{RESEARCH_VERSION_ID}`\n"
    "These observations are **not part of the official Open Ledger football record** "
    "and are **not eligible for retroactive promotion** into the official record. "
    "No edge claim is made. All observations are 0 units. "
    "Nothing here is betting advice."
)


def _research_board_path(week):
    return os.path.join(RESEARCH_DIR, f"board_{week}_{RESEARCH_VERSION_ID}.json")


def load_research_board(week):
    """Load a research board from data/football/research/.

    Returns the board dict, or None if the file does not exist.
    Raises ValueError on malformed JSON — a corrupt board must not be
    silently treated as missing.
    Never reads official board files (board_*.enc / board_*.json).
    """
    path = _research_board_path(week)
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)   # ValueError propagates — fail loud, not silent


def load_research_prereg_for_delivery():
    """Load and validate the registry entry for RESEARCH_VERSION_ID.

    Fails closed if:
    - research_preregistrations.json is missing or corrupt
    - the version ID is not registered
    - authority, selection_rule, or units do not match expected values

    A board that self-asserts valid provenance cannot proceed if the registry
    does not independently confirm it.  This is the external cross-check.
    """
    if not os.path.exists(RESEARCH_PREREG_PATH):
        raise SystemExit(
            f"research_preregistrations.json not found at {RESEARCH_PREREG_PATH}; "
            f"refusing research send")
    try:
        with io.open(RESEARCH_PREREG_PATH, encoding="utf-8") as f:
            registry = json.load(f)
    except ValueError as exc:
        raise SystemExit(
            f"research_preregistrations.json is corrupt ({exc}); "
            f"refusing research send")
    entry = next((r for r in registry.get("registrations", [])
                  if r.get("id") == RESEARCH_VERSION_ID), None)
    if entry is None:
        raise SystemExit(
            f"research version {RESEARCH_VERSION_ID!r} not in registry; "
            f"refusing research send")
    if entry.get("authority") != "research_only_no_selection_delivery_or_holdout":
        raise SystemExit(
            f"registry authority {entry.get('authority')!r} is not research-only; "
            f"refusing research send")
    if entry.get("selection_rule") != "fp-v0.4":
        raise SystemExit(
            f"registry selection_rule {entry.get('selection_rule')!r} "
            f"!= 'fp-v0.4'; refusing research send")
    if entry.get("units") != 0:
        raise SystemExit(
            f"registry units={entry.get('units')} — must be 0; "
            f"refusing research send")
    return entry


def validate_research_board_for_delivery(b, prereg):
    """Fail closed if the board is not a legitimate research observation.

    `prereg` is the registry entry returned by load_research_prereg_for_delivery().
    Every check cross-references the board against the externally registered
    entry — self-declared provenance alone is insufficient.
    """
    if b.get("tier") != "research":
        raise SystemExit(
            f"board tier is {b.get('tier')!r}, not 'research'; refusing send")
    if b.get("research_version_id") != RESEARCH_VERSION_ID:
        raise SystemExit(
            f"board research_version_id {b.get('research_version_id')!r} "
            f"!= {RESEARCH_VERSION_ID!r}; refusing send")
    if b.get("record_cohort") != RESEARCH_VERSION_ID:
        raise SystemExit(
            f"board record_cohort {b.get('record_cohort')!r} "
            f"!= {RESEARCH_VERSION_ID!r}; refusing send")
    # Cross-check board's claimed selection rule against the registered entry.
    if b.get("research_selection_rule") != prereg.get("selection_rule"):
        raise SystemExit(
            f"board selection rule {b.get('research_selection_rule')!r} "
            f"!= registered {prereg.get('selection_rule')!r}; refusing send")
    if b.get("units", -1) != 0:
        raise SystemExit(
            f"board units={b.get('units')} — research boards must be 0 units; "
            f"refusing send")
    # Cross-check board's authority against the registered entry.
    board_auth = b.get("research_authority", "")
    if board_auth != prereg.get("authority"):
        raise SystemExit(
            f"board authority {board_auth!r} != registered "
            f"{prereg.get('authority')!r}; refusing send")


def _observation_embed(g, label, color=AMBER):
    """One research observation embed.

    DELIBERATELY avoids the words 'premium', 'free', 'pick', 'recommended',
    'bet', 'edge', 'lock', or 'unit play'.  Internal tier names from the
    board object are replaced with neutral labels ('Observation A / B').
    """
    lines = [
        f"**Observed side:** {g.get('side', '—')} "
        f"at {money(g.get('best_price'))} ({g.get('best_book', '—')})",
        f"Kickoff (UTC): {g.get('kickoff_utc', '—')}",
        f"Eligible books: {g.get('n_books', '—')} | "
        f"Corroborating books at best price: {g.get('books_at_best', '—')}",
        f"Effective overround at best prices: "
        f"{g.get('eff_overround_pts', '—')} pts "
        f"(market margin, not an expected return)",
        f"T-24 capture: {g.get('t24_capture', '—')} | "
        f"{g.get('t24_hours_before_kickoff', '—')}h before kickoff",
        "0 units — research observation, not a recommendation.",
    ]
    fair = g.get("fair_side")
    if fair is not None:
        lines.insert(2, f"Market-implied probability (de-vigged): {fair * 100:.1f}%")
    return {
        "title": f"{label} — {g.get('matchup', '—')} ({g.get('sport', '').upper()})",
        "description": "\n".join(lines)[:4096],
        "color": color,
    }


def research_board_messages(b, week):
    """Build the Discord message list for one research board.

    Layout:
      Message 1: header with full disclaimer + board summary
      Message 2: observation embeds (one per selected game, neutral labels)
      Message 3: coverage / no-market list
      Message 4: footer with version and authority statement

    The disclaimer appears in Message 1, not hidden in a footer.
    Internal 'premium' / 'free' field names map to 'Observation A / B'.
    """
    url = f"{SITE}/football/research/"

    # Map internal tier keys to neutral presentation labels.
    observations = []
    for internal_key, label in (("premium", "Observation A"),
                                 ("free",    "Observation B")):
        g = b.get(internal_key)
        if g:
            observations.append((g, label))

    obs_count = len(observations)
    covered   = b.get("n_covered", 0)
    excluded  = b.get("n_excluded", 0)

    msg1 = {
        "username": "Open Ledger Sports",
        "content": (
            f"{RESEARCH_HEADER}\n\n"
            f"**Week of {week}** — {covered} games evaluated | "
            f"{obs_count} observation(s) selected | "
            f"{excluded} no market\n"
            f"{url}"
        ),
    }

    msgs = [msg1]

    if observations:
        embeds = [_observation_embed(g, lbl) for g, lbl in observations]
        msgs.append({"username": "Open Ledger Sports", "embeds": embeds})

    nm = b.get("no_market", [])
    if nm:
        lines = [f"• {n.get('matchup', '')} — {n.get('reason', '')}" for n in nm]
        body = "\n".join(lines)
        if len(body) > 1600:
            body = body[:1600].rsplit("\n", 1)[0] + f"\n... full list at {url}"
        msgs.append({
            "username": "Open Ledger Sports",
            "content": f"**No market this week ({len(nm)} games)**\n{body}",
        })

    msgs.append({
        "username": "Open Ledger Sports",
        "content": (
            f"_Version: `{RESEARCH_VERSION_ID}` | "
            f"Authority: research only — not official, not a recommendation | "
            f"{FOOTER}_"
        ),
    })

    return [{k: v for k, v in m.items() if v is not None} for m in msgs]


RESEARCH_MODES = {
    "research_board": (
        RESEARCH_STATUS_KEY,
        RESEARCH_WEBHOOK,
        "DISCORD_RESEARCH_WEBHOOK",
        research_board_messages,
    ),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=sorted(MODES) + sorted(RESEARCH_MODES))
    ap.add_argument("--week", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="post even if this week already went out")
    args = ap.parse_args()

    # ---- RESEARCH BRANCH ------------------------------------------------
    # Completely separate gate, separate board source, separate status key.
    # --force does not bypass RESEARCH_DELIVERY_ENABLED.
    if args.mode in RESEARCH_MODES:
        if not delivery_policy.RESEARCH_DELIVERY_ENABLED and not args.dry_run:
            print(delivery_policy.RESEARCH_PAUSE_REASON
                  + "; no research send and no status mutation.")
            return 0

        status_mode, webhook, varname, builder = RESEARCH_MODES[args.mode]
        week = args.week

        # VERSION-AWARE IDEMPOTENCY KEY.  "research_board" alone is the mode;
        # the status date carries "version|week" so a future cohort can post
        # during the same slate week without colliding with this cohort's record.
        idem_key = f"{RESEARCH_VERSION_ID}|{week}"

        if not args.dry_run and not args.force and already_posted(idem_key, status_mode):
            print(f"{args.mode}: {idem_key} already posted; skipping.")
            return 0

        b = load_research_board(week)
        if b is None:
            print(f"no research board for {week}; nothing to post.")
            if not args.dry_run:
                record(idem_key, status_mode, "no_board")
            return 0

        # Fail closed: load registry first, then cross-check board against it.
        # Self-declared provenance alone is insufficient.
        prereg = load_research_prereg_for_delivery()
        validate_research_board_for_delivery(b, prereg)

        messages = builder(b, week)
        print(f"{args.mode}: {len(messages)} message(s) for week {week}")

        if not args.dry_run:
            if not webhook:
                print(f"{varname} is not set; skipping (this never fails a run).")
                record(idem_key, status_mode, "no_config")
                return 0
            if not webhook_host_ok(webhook):
                print(f"WARNING: {varname} is not a discord.com URL — refusing.")
                record(idem_key, status_mode, "refused",
                       detail="webhook host not discord")
                return 0

        ok, status, detail = send(webhook, messages, dry=args.dry_run)
        if args.dry_run:
            print("\n(--dry-run: nothing sent, nothing recorded)")
            return 0
        record(idem_key, status_mode, "posted" if ok else "failed", status, detail)
        print(("posted " if ok else "FAILED ") + str(detail))
        return 0

    # ---- OFFICIAL BRANCH ------------------------------------------------
    if not delivery_policy.OFFICIAL_DELIVERY_ENABLED and not args.dry_run:
        print(delivery_policy.OFFICIAL_PAUSE_REASON + "; no send and no status mutation.")
        return 0

    status_mode, webhook, varname, builder = MODES[args.mode]
    week = args.week

    if not args.dry_run and not args.force and already_posted(week, status_mode):
        print(f"{args.mode}: week {week} already posted; skipping. (--force "
              f"overrides, but a week is many messages - be sure.)")
        return 0

    b, revealed = fbpage.load_board(week)
    if b is None:
        print(f"no board for {week}; nothing to post.")
        # NOT under --dry-run. A preview that writes state is not a preview, and
        # this one did: two dry-runs left "no_board" rows in post_status.json,
        # which is a file CI commits. Every other write in this file was already
        # guarded; this path was missed.
        if not args.dry_run:
            record(week, status_mode, "no_board")
        return 0
    if not b.get("decision_made"):
        print(f"week {week} has no chosen play yet (decision moment "
              f"{b.get('decision_moment_utc')}); not posting.")
        return 0

    try:
        delivery_policy.validate(b, week)
    except (ValueError, TypeError):
        print("Football delivery blocked: board failed delivery safety checks.")
        return 1
    messages = builder(b, week)
    print(f"{args.mode}: {len(messages)} message(s) for week {week}")

    if not args.dry_run:
        if not webhook:
            print(f"{varname} is not set; skipping (this never fails a run).")
            record(week, status_mode, "no_config")
            return 0
        if not webhook_host_ok(webhook):
            print(f"WARNING: {varname} is not a discord.com URL — refusing to send.")
            record(week, status_mode, "refused", detail="webhook host not discord")
            return 0

    ok, status, detail = send(webhook, messages, dry=args.dry_run)
    if args.dry_run:
        print("\n(--dry-run: nothing sent, nothing recorded)")
        return 0
    record(week, status_mode, "posted" if ok else "failed", status, detail)
    print(("posted " if ok else "FAILED ") + str(detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
