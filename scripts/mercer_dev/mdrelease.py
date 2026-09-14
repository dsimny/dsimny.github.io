#!/usr/bin/env python3
"""
D.J. Mercer Developmental Cohorts - artifact, release controls, publish, grade.

THE CHAIN, each link verified by the next:

  draft (author, local, never committed)
    -> ARTIFACT: every ledger field + the exact Discord payload, rendered ONCE,
       fingerprinted, encrypted to data/mercer_dev/artifacts/<play_id>.enc,
       hash committed in the clear to data/mercer_dev/commitments.json
    -> PREVIEW: private review channel or local console; never members
    -> APPROVAL: a named approver supplies the play id AND the artifact hash
    -> RELEASE CHECKS: fresh ESPN event + fresh odds, all must pass
    -> RESERVATION (create-only) -> SEND the stored payload bytes -> VERIFY the
       message Discord stored -> RECEIPT (create-only)
    -> GRADE: after kickoff the publication row enters the cohort ledger from
       artifact + receipt; after the final, a settlement row

Nothing is regenerated after approval. The sender posts artifact["discord_payload"]
exactly, and the payload hash is part of the approved artifact hash.

An uncertain send (timeout, 5xx, unverifiable acknowledgement) leaves the
reservation without a receipt, and every later attempt refuses until a human
reconciles it. There is no automatic retry.

All I/O is injected through `IO` so the self-test runs with no network, no
secrets and no repository writes.
"""
import os
import re
import sys
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "football"))

import mdcore                                        # noqa: E402
import mdmarket                                      # noqa: E402
import settle as fbsettle                            # noqa: E402

DATA = mdcore.DATA
CONTROL = os.path.join(DATA, "control.json")
COMMITMENTS = os.path.join(DATA, "commitments.json")
ARTIFACTS = os.path.join(DATA, "artifacts")
PUBLICATION_ENV = "MERCER_DEV_PUBLICATION"          # must be exactly "enabled"
APPROVERS_ENV = "MERCER_DEV_APPROVERS"              # comma-separated GitHub logins
PLAYS_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL_MERCER_PLAYS"
REVIEW_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL_MERCER_REVIEW"

CONFIDENCE = ("standard", "strong", "spotlight")
DRAFT_FIELDS = ("strategy_id", "version", "sport", "espn_event_id", "odds_event_id",
                "market", "selection", "line", "odds", "book", "recommended_units",
                "confidence", "reasoning", "key_risks", "walk_away")
RG_FOOTER = ("21+ · Gambling problem? Call 1-800-GAMBLER · Open Ledger Sports is "
             "analytics, not a sportsbook, and not betting advice · No guarantee of results")
DEV_DISCLAIMER = ("Developmental strategy, tracked prospectively. It is not proven and "
                  "carries no guarantee. Every released play is recorded and graded, "
                  "including losses.")
LIVE_WARNING = ("LIVE: odds move quickly. Verify the current market before acting. We "
                "never substitute a worse line for the one shown.")
# Claims the cohorts may not make about themselves. Disclaimers that negate them
# ("not proven", "no guarantee") are allowed.
BANNED = [r"(?<!not )\bproven\b", r"(?<!no )\bguarantee(d|s)?\b", r"(?<!not )\bvalidated\b",
          r"\block\b", r"\bsure thing\b", r"\bcan'?t lose\b", r"\bfree money\b",
          r"\bmax bet\b", r"(?<!not )\bbeats? the market\b", r"\bprofitable\b"]
LIMITS = {"content": 2000, "title": 256, "description": 4096, "total": 6000}


class ReleaseRefused(RuntimeError):
    """A check failed. The message is safe for a public log (no actionable fields)."""


# --- artifact -------------------------------------------------------------------

def play_id_for(strategy_id, version, espn_event_id, market):
    return f"{strategy_id}-v{int(version)}-{espn_event_id}-{market}"


def banned_phrases(text):
    t = text.lower()
    return [p for p in BANNED if re.search(p, t)]


def selection_text(a):
    if a["market"] == "moneyline":
        return f"{a['selection']} moneyline"
    if a["market"] == "spread":
        return f"{a['selection']} {a['line']:+g}"
    return f"{a['selection'].capitalize()} {a['line']:g}"


def render_payload(a):
    """The member message. Deterministic in the artifact's fields."""
    live = a["designation"] == "live"
    lines = [
        f"**{a['matchup']}**",
        f"**{selection_text(a)}  {a['odds']:+d}**  at {a['book']}",
        f"Recommended: **{a['recommended_units']:g}u**  ·  Confidence: {a['confidence'].capitalize()}",
        f"Reference: {a['reference_source']}  ·  price as of {a['quote']['captured_utc']}",
        f"Market-implied {100 * a['market_implied_probability']:.1f}%  ·  "
        f"consensus {100 * a['model_probability']:.1f}%  ·  edge {a['edge_pts']:+.1f} pts",
        f"Scheduled start (UTC): {a['event']['scheduled_start_utc']}",
        "",
        f"__Why__\n{a['reasoning']}",
        "",
        f"__Key risks / where the market disagrees__\n{a['key_risks']}",
        "",
    ]
    if live:
        lines += [LIVE_WARNING, ""]
    lines += [DEV_DISCLAIMER, f"Play ID `{a['play_id']}`", RG_FOOTER]
    title = (f"DJ MERCER · {a['league']} · Developmental Cohort v{a['version']} · "
             f"{'LIVE' if live else 'PREGAME'}")
    return {"content": f"**DJ Mercer — {a['league']} developmental play**",
            "embeds": [{"title": title, "description": "\n".join(lines),
                        "color": 0x3987E5}],
            "allowed_mentions": {"parse": []}}


def payload_problems(payload):
    out = []
    if len(payload.get("content", "")) > LIMITS["content"]:
        out.append("content too long")
    total = len(payload.get("content", ""))
    for e in payload.get("embeds", []):
        if len(e.get("title", "")) > LIMITS["title"]:
            out.append("embed title too long")
        if len(e.get("description", "")) > LIMITS["description"]:
            out.append("embed description too long")
        total += len(e.get("title", "")) + len(e.get("description", ""))
    if total > LIMITS["total"]:
        out.append("message exceeds Discord's 6000-character limit")
    if payload.get("allowed_mentions") != {"parse": []}:
        out.append("mentions must be disabled")
    return out


def evaluate_breakers(d, reg, cons, event, now):
    q = reg["min_data_quality"]
    lim = reg["odds_limits"]
    div = reg["max_market_divergence"]
    edge = None
    if cons["reference_probability"] is not None:
        edge = (cons["reference_probability"] - mdcore.implied(d["odds"])) * 100
    kick = mdcore.parse_utc(event.get("kickoff_utc"))
    out = []

    def add(name, ok, detail):
        out.append({"name": name, "passed": bool(ok), "detail": detail})

    add("books_quoting_market", cons["n_books_market"] >= q["min_books_market"],
        f"{cons['n_books_market']} fresh Tier-1 books (need {q['min_books_market']})")
    add("books_at_line", cons["n_books_line"] >= q["min_books_at_line"],
        f"{cons['n_books_line']} at this exact number (need {q['min_books_at_line']})")
    add("market_timestamps", not (q.get("require_market_timestamps") and cons["timestamp_fallback"]),
        "per-market timestamps present" if not cons["timestamp_fallback"]
        else "a book supplied only a book-level timestamp")
    if d["market"] == "moneyline":
        ok = lim["moneyline_min"] <= d["odds"] <= lim["moneyline_max"]
        add("odds_window", ok, f"moneyline window {lim['moneyline_min']}..{lim['moneyline_max']}")
    else:
        ok = lim["price_min"] <= d["odds"] <= lim["price_max"]
        add("odds_window", ok, f"price window {lim['price_min']}..{lim['price_max']}")
    if d["market"] == "spread":
        add("spread_magnitude", abs(float(d["line"])) <= lim["max_spread_magnitude"],
            f"|line| <= {lim['max_spread_magnitude']}")
    add("price_vs_consensus", edge is not None and edge >= -div["max_price_below_consensus_pts"],
        f"taken price no more than {div['max_price_below_consensus_pts']} pts worse than consensus")
    if reg["min_edge_pts"] is not None:
        add("min_edge", edge is not None and edge >= reg["min_edge_pts"],
            f"edge >= {reg['min_edge_pts']} pts")
    bp = cons["prices"].get(d["book"])
    add("book_offers_price", bp is not None and mdmarket.price_not_worse(bp, d["odds"]),
        "the named book offers this number at this price or better")
    w = d["walk_away"] or {}
    ok = True
    if w.get("min_odds") is not None and not mdmarket.price_not_worse(d["odds"], w["min_odds"]):
        ok = False
    if w.get("line_at_least") is not None and float(d["line"]) < float(w["line_at_least"]):
        ok = False
    if w.get("line_at_most") is not None and float(d["line"]) > float(w["line_at_most"]):
        ok = False
    add("inside_walk_away", ok and bool(w), "number is inside the recorded walk-away boundary")
    flat = reg["unit_sizing"]["flat_units"]
    add("units", float(d["recommended_units"]) == float(flat)
        and float(d["recommended_units"]) <= reg["max_play_units"],
        f"flat {flat}u, max {reg['max_play_units']}u")
    lead = reg["selection_time"]["min_minutes_before_start"]
    add("pregame_lead", bool(kick) and kick - now >= timedelta(minutes=lead),
        f">= {lead} minutes before scheduled start")
    add("regular_season", event.get("season_type") == 2, "ESPN regular season")
    add("event_scheduled", event.get("status") in ("STATUS_SCHEDULED",) and not event.get("final"),
        "ESPN status is scheduled")
    return out, edge


def build_artifact(d, registration, quote, event, now):
    reg, entry = registration
    missing = [f for f in DRAFT_FIELDS if f not in d]
    if missing:
        raise ReleaseRefused(f"draft missing {missing}")
    sport = d["sport"]
    if d["strategy_id"] != reg["strategy_id"] or d["version"] != reg["version"] or sport != reg["sport"]:
        raise ReleaseRefused("draft does not belong to the active registration")
    if d["market"] not in reg["eligible_markets"]:
        raise ReleaseRefused("market is not registered")
    if d["confidence"] not in CONFIDENCE:
        raise ReleaseRefused(f"confidence must be one of {CONFIDENCE}")
    for field in ("reasoning", "key_risks"):
        if not str(d[field]).strip():
            raise ReleaseRefused(f"{field} is required")
        hit = banned_phrases(d[field])
        if hit:
            raise ReleaseRefused(f"{field} makes a barred claim ({len(hit)} pattern(s))")
    if (d["market"] == "moneyline") != (d["line"] is None):
        raise ReleaseRefused("moneyline has no line; spread and total require one")
    problems = mdmarket.identity_problems(sport, quote, event, {
        "espn_event_id": d["espn_event_id"], "odds_event_id": d["odds_event_id"],
        "home": quote["home"], "away": quote["away"]})
    if problems:
        raise ReleaseRefused("; ".join(problems))
    valid_sel = ((quote["home"], quote["away"]) if d["market"] != "total" else ("over", "under"))
    sel = d["selection"].lower() if d["market"] == "total" else d["selection"]
    if sel not in valid_sel:
        raise ReleaseRefused("selection is not a side of this market")
    cons = mdmarket.consensus(quote, d["market"], sel, d["line"],
                              reg["min_data_quality"]["max_quote_age_minutes"], now)
    breakers, edge = evaluate_breakers(dict(d, selection=sel), reg, cons, event, now)
    pid = play_id_for(d["strategy_id"], d["version"], d["espn_event_id"], d["market"])
    a = {
        "schema": "mercer-dev-artifact/1", "play_id": pid,
        "strategy_id": reg["strategy_id"], "strategy_name": reg["strategy_name"],
        "version": reg["version"], "registration_sha256": entry["sha256"],
        "sport": sport, "league": reg["league"],
        "event": {"espn_event_id": str(d["espn_event_id"]), "odds_event_id": d["odds_event_id"],
                  "home": quote["home"], "away": quote["away"],
                  "scheduled_start_utc": event["kickoff_utc"]},
        "matchup": f"{quote['away']} @ {quote['home']}",
        "designation": "pregame", "market": d["market"], "selection": sel,
        "line": d["line"], "odds": int(d["odds"]), "book": d["book"],
        "reference_source": "Tier-1 US consensus via The Odds API (proportional de-vig)",
        "model_probability": cons["reference_probability"],
        "market_implied_probability": round(mdcore.implied(d["odds"]), 6),
        "edge_pts": round(edge, 3) if edge is not None else None,
        "confidence": d["confidence"], "recommended_units": float(d["recommended_units"]),
        "reasoning": d["reasoning"].strip(), "key_risks": d["key_risks"].strip(),
        "walk_away": d["walk_away"], "circuit_breakers": breakers,
        "quote": {"captured_utc": quote["captured_utc"], "sha256": mdcore.sha256_of(quote),
                  "n_books_market": cons["n_books_market"], "n_books_line": cons["n_books_line"],
                  "best_price": cons["best_price"], "best_book": cons["best_book"]},
        "created_utc": mdcore.iso(now),
    }
    if a["model_probability"] is None:
        raise ReleaseRefused("no consensus probability at this number")
    a["discord_payload"] = render_payload(a)
    probs = payload_problems(a["discord_payload"])
    if probs:
        raise ReleaseRefused("; ".join(probs))
    failed = [b["name"] for b in breakers if not b["passed"]]
    return a, mdcore.sha256_of(a), failed


# --- release --------------------------------------------------------------------

def control_state(env):
    ctl = mdcore.load_json(CONTROL, {"publication_enabled": False, "sports": {}})
    return ctl, env.get(PUBLICATION_ENV, "") == "enabled"


def check_release(a, sha, approval, commitments, led, quote, event, now, env, registration):
    """Every Phase 6 check. Returns [(name, passed, safe_detail)]."""
    reg, entry = registration if registration else (None, None)
    ctl, env_on = control_state(env)
    checks = []

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    add("kill_switch_off", ctl.get("publication_enabled") is True and env_on
        and ctl.get("sports", {}).get(a["sport"]) is True,
        f"control.json + {PUBLICATION_ENV}=enabled + sport enabled")
    add("registration_active", reg is not None and entry is not None
        and a["strategy_id"] == reg["strategy_id"] and a["version"] == reg["version"]
        and a["registration_sha256"] == entry["sha256"])
    add("artifact_hash", mdcore.sha256_of(a) == sha)
    latest = [c for c in commitments if c["play_id"] == a["play_id"]]
    add("publicly_committed", bool(latest) and latest[-1]["artifact_sha256"] == sha,
        "latest public commitment for this play matches")
    appr_ok = (approval is not None and approval.get("artifact_sha256") == sha
               and approval.get("play_id") == a["play_id"]
               and approval.get("approver") in _approvers(env))
    add("human_approval", appr_ok, "approver is on the allowlist and approved this exact hash")
    add("payload_is_stored_render", a["discord_payload"] == render_payload(a)
        and not payload_problems(a["discord_payload"]))
    ids = mdmarket.identity_problems(a["sport"], quote, event, a["event"])
    add("event_identity_and_teams", not ids, f"{len(ids)} identity problem(s)")
    add("league", a["sport"] == (reg or {}).get("sport") and event.get("season_type") == 2)
    kick = mdcore.parse_utc(event.get("kickoff_utc"))
    lead = (reg or {}).get("selection_time", {}).get("min_minutes_before_start", 30)
    add("pregame_now", a["designation"] == "pregame" and event.get("status") == "STATUS_SCHEDULED"
        and not event.get("final") and bool(kick) and kick - now >= timedelta(minutes=lead))
    add("start_time_unchanged", bool(kick) and abs((kick - mdcore.parse_utc(
        a["event"]["scheduled_start_utc"])).total_seconds()) <= 15 * 60,
        "kickoff moved more than 15 minutes -> re-approve")
    max_age = (reg or {}).get("min_data_quality", {}).get("max_quote_age_minutes", 15)
    cons = mdmarket.consensus(quote, a["market"], a["selection"], a["line"], max_age, now)
    cap = mdcore.parse_utc(quote.get("captured_utc"))
    add("odds_fresh", bool(cap) and timedelta(0) <= now - cap <= timedelta(minutes=max_age))
    q = (reg or {}).get("min_data_quality", {})
    add("market_exists", cons["n_books_market"] >= q.get("min_books_market", 5))
    add("line_exists", cons["n_books_line"] >= q.get("min_books_at_line", 3))
    bp = cons["prices"].get(a["book"])
    add("book_still_offers_price", bp is not None and mdmarket.price_not_worse(bp, a["odds"]),
        "never substitute a worse price or number")
    if cons["reference_probability"] is not None and reg:
        edge_now = (cons["reference_probability"] - mdcore.implied(a["odds"])) * 100
        add("price_vs_consensus_now", edge_now >=
            -reg["max_market_divergence"]["max_price_below_consensus_pts"])
    else:
        add("price_vs_consensus_now", False, "no consensus at this number")
    add("breakers_at_build", all(b["passed"] for b in a["circuit_breakers"]))
    pubs = mdcore.publications(led)
    add("no_duplicate", a["play_id"] not in {p["play_id"] for p in pubs})
    conflict = [p for p in pubs if p["espn_event_id"] == a["event"]["espn_event_id"]
                and p["market"] == a["market"]]
    add("no_conflicting_play", not conflict or (reg or {}).get("conflicting_plays_permitted") is True)
    day = mdcore.et_date(mdcore.parse_utc(a["event"]["scheduled_start_utc"]))
    same_day = [p for p in pubs if mdcore.et_date(mdcore.parse_utc(p["scheduled_start_utc"])) == day]
    units = sum(float(p["recommended_units"]) for p in same_day) + a["recommended_units"]
    add("exposure_limits", reg is not None and a["recommended_units"] <= reg["max_play_units"]
        and units <= reg["max_daily_units"] and len(same_day) + 1 <= reg["max_plays_per_day"])
    return checks


def _approvers(env):
    return {x.strip() for x in env.get(APPROVERS_ENV, "").split(",") if x.strip()}


# --- publish ---------------------------------------------------------------------

class IO:
    """Everything with a side effect. The self-test supplies fakes."""

    def load_artifact(self, play_id): raise NotImplementedError
    def commitments(self): raise NotImplementedError
    def fetch_quote(self, sport, odds_event_id): raise NotImplementedError
    def fetch_event(self, sport, espn_event_id): raise NotImplementedError
    def create_only(self, relpath, obj): raise NotImplementedError   # atomic, refuses existing
    def exists(self, relpath): raise NotImplementedError
    def read(self, relpath): raise NotImplementedError
    def send(self, webhook, payload): raise NotImplementedError       # -> dict or raises
    def fetch_message(self, webhook, message_id): raise NotImplementedError


class Uncertain(RuntimeError):
    pass


class Definitive(RuntimeError):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


def delivery_paths(play_id, attempt=1):
    base = f"data/mercer_dev/deliveries/{play_id}"
    n = "" if attempt == 1 else str(attempt)
    return {"approval": f"data/mercer_dev/approvals/{play_id}.json",
            "reserved": base + f".reserved{n}.json", "delivered": base + ".delivered.json",
            "failed": base + f".failed{n}.json"}


def attempts_state(io, play_id):
    """(latest attempt number or 0, whether that attempt has a definitive failure)."""
    n = 0
    while io.exists(delivery_paths(play_id, n + 1)["reserved"]):
        n += 1
    return n, (n > 0 and io.exists(delivery_paths(play_id, n)["failed"]))


def publish(play_id, approved_sha, approver, now, io, env, run_id="local",
            run_attempt="1", dry_run=False, retry_after_definitive_failure=False):
    """Returns (status, checks). status in {published, refused, dry_run, uncertain, failed}."""
    paths = delivery_paths(play_id)
    a = io.load_artifact(play_id)
    if a is None:
        raise ReleaseRefused("artifact unreadable (missing, or sealed with no key)")
    sport = a["sport"]
    try:
        registration = mdcore.active_registration(sport, now)
        if registration == (None, None):
            registration = None
    except mdcore.IntegrityError:
        registration = None
    led = mdcore.load_ledger(a["strategy_id"])
    if io.exists(paths["delivered"]):
        return "refused", [("no_duplicate", False, "a delivery receipt already exists")]
    last, last_failed = attempts_state(io, play_id)
    if last and not (last_failed and retry_after_definitive_failure):
        return "refused", [("no_uncertain_reservation", False,
                            "a reservation exists without a receipt; reconcile by hand"
                            if not last_failed else
                            "previous attempt was refused by Discord; retry needs the explicit flag")]
    if str(run_attempt) != "1" and not dry_run:
        return "refused", [("first_attempt_only", False, "workflow re-runs never publish")]
    approval = {"play_id": play_id, "artifact_sha256": approved_sha, "approver": approver,
                "approved_utc": mdcore.iso(now), "run_id": run_id}
    quote = io.fetch_quote(sport, a["event"]["odds_event_id"])
    event = io.fetch_event(sport, a["event"]["espn_event_id"])
    checks = check_release(a, approved_sha, approval, io.commitments(), led, quote, event,
                           now, env, registration)
    if dry_run:
        return "dry_run", checks
    if not all(ok for _, ok, _ in checks):
        return "refused", checks
    hook = env.get(PLAYS_WEBHOOK_ENV, "").strip()
    if not hook.startswith("https://discord.com/api/webhooks/") or "?" in hook:
        return "refused", checks + [("credentials", False, f"{PLAYS_WEBHOOK_ENV} missing or not discord.com")]
    if io.exists(paths["approval"]):
        prior = io.read(paths["approval"])
        if prior.get("artifact_sha256") != approved_sha:
            return "refused", checks + [("approval_consistent", False,
                                         "an approval for a different hash already exists")]
    else:
        io.create_only(paths["approval"], approval)
    attempt = last + 1
    ap = delivery_paths(play_id, attempt)
    reservation = {"play_id": play_id, "artifact_sha256": approved_sha, "run_id": run_id,
                   "reserved_utc": mdcore.iso(now), "attempt": attempt,
                   "payload_sha256": mdcore.sha256_of(a["discord_payload"])}
    io.create_only(ap["reserved"], reservation)
    try:
        sent = io.send(hook, a["discord_payload"])
        mid = str(sent.get("id", ""))
        if not mid.isdigit() or not _same_message(sent, a["discord_payload"]):
            raise Uncertain("acknowledgement did not match the stored payload")
        stored = io.fetch_message(hook, mid)
        if not _same_message(stored, a["discord_payload"]):
            raise Uncertain("message stored by Discord differs from the stored payload")
    except Definitive as exc:
        io.create_only(ap["failed"], dict(reservation, http_status=exc.status,
                                          detail="refused by Discord before delivery"))
        return "failed", checks
    except Exception:
        return "uncertain", checks
    receipt = dict(reservation, message_id=mid, channel_id=stored.get("channel_id"),
                   published_utc=_discord_time(stored) or mdcore.iso(now), verified=True,
                   approver=approver, approved_utc=approval["approved_utc"])
    io.create_only(paths["delivered"], receipt)
    return "published", checks


def _same_message(msg, payload):
    if not isinstance(msg, dict):
        return False
    if msg.get("content") != payload["content"]:
        return False
    got = msg.get("embeds") or []
    want = payload["embeds"]
    if len(got) != len(want):
        return False
    return all(g.get("title") == w["title"] and g.get("description") == w["description"]
               for g, w in zip(got, want))


def _discord_time(msg):
    ts = (msg or {}).get("timestamp")
    if not ts:
        return None
    # Discord: 2026-09-20T15:04:05.123000+00:00
    return ts[:19] + "Z" if len(ts) >= 19 else None


# --- grade -----------------------------------------------------------------------

VOID_AFTER = timedelta(days=7)


def publication_row(a, receipt):
    return {
        "row_type": "publication", "play_id": a["play_id"], "strategy_id": a["strategy_id"],
        "strategy_name": a["strategy_name"], "version": a["version"],
        "registration_sha256": a["registration_sha256"], "sport": a["sport"],
        "league": a["league"], "espn_event_id": a["event"]["espn_event_id"],
        "odds_event_id": a["event"]["odds_event_id"],
        "scheduled_start_utc": a["event"]["scheduled_start_utc"],
        "published_utc": receipt["published_utc"], "designation": a["designation"],
        "matchup": a["matchup"], "market": a["market"], "selection": a["selection"],
        "line": a["line"], "odds": a["odds"], "book": a["book"],
        "reference_source": a["reference_source"], "model_probability": a["model_probability"],
        "market_implied_probability": a["market_implied_probability"],
        "edge_pts": a["edge_pts"], "confidence": a["confidence"],
        "recommended_units": a["recommended_units"], "circuit_breakers": a["circuit_breakers"],
        "manual_approver": receipt["approver"], "approved_utc": receipt["approved_utc"],
        "artifact_sha256": receipt["artifact_sha256"],
        "discord_message_ids": [receipt["message_id"]],
    }


def settle_row(pub, event, closing, now):
    """WIN/LOSS/PUSH from the final; VOID only with a stated reason."""
    base = {"row_type": "settlement", "play_id": pub["play_id"], "graded_utc": mdcore.iso(now),
            "home_score": None, "away_score": None, "closing_line": None,
            "closing_odds": None, "closing_probability": None, "clv_pts": None,
            "void_reason": None, "corrects": None, "profit_units": 0.0}
    start = mdcore.parse_utc(pub["scheduled_start_utc"])
    if event is None:
        if now - start > VOID_AFTER:
            return dict(base, result="VOID", void_reason="event not found in results 7 days after start")
        return None
    if event.get("season_type") != 2:
        return dict(base, result="VOID", void_reason="not a regular-season game")
    status = event.get("status", "")
    if status in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED"):
        return dict(base, result="VOID", void_reason=f"game {status.split('_', 1)[1].lower()}")
    if not event.get("final"):
        if now - start > VOID_AFTER:
            return dict(base, result="VOID", void_reason="no final result 7 days after start")
        return None
    hs, as_ = event["home_score"], event["away_score"]
    home, away = pub["matchup"].split(" @ ")[1], pub["matchup"].split(" @ ")[0]
    if pub["market"] == "total":
        res = fbsettle.settle_total(hs, as_, float(pub["line"]), pub["selection"])
    else:
        mine, opp = (hs, as_) if pub["selection"] == home else (as_, hs)
        if pub["selection"] not in (home, away):
            raise mdcore.IntegrityError("selection is not a team in this matchup")
        res = (fbsettle.settle_moneyline(mine, opp) if pub["market"] == "moneyline"
               else fbsettle.settle_spread(mine, opp, float(pub["line"])))
    row = dict(base, result=res, home_score=hs, away_score=as_,
               profit_units=fbsettle.pnl(res, float(pub["recommended_units"]), pub["odds"]))
    if closing and closing.get("reference_probability") is not None:
        row.update(closing_line=closing.get("line"), closing_odds=closing.get("odds"),
                   closing_probability=closing["reference_probability"],
                   clv_pts=round((closing["reference_probability"] -
                                  float(pub["market_implied_probability"])) * 100, 3))
    return row
