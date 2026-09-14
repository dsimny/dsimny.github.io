#!/usr/bin/env python3
"""
D.J. Mercer Developmental Cohorts - registry and append-only ledgers.

Two strategies, two records, never combined:

    mercer-nfl-dev    DJ Mercer NFL  - Developmental Cohort
    mercer-ncaaf-dev  DJ Mercer NCAA - Developmental Cohort

REGISTRY. A registration is a JSON file under data/mercer_dev/registrations/.
It becomes binding only when `register` appends its SHA-256 to
data/mercer_dev/registry_log.json. From then on the file must hash to that
value forever; a changed file is refused everywhere it is read. Clerical
amendments are separate files with their own log entries and may not touch any
methodology field - those need a new version.

LEDGERS. One file per strategy, data/mercer_dev/ledgers/<strategy_id>.json.
Rows are appended, never edited: a `publication` row when a play is released,
a `settlement` row when it is graded or voided. A correction is a new row that
names the row it corrects. Aggregates are recomputed from the rows every time.

This module never reads or writes football_ledger.json, the Mercer Spotlight
ledger or any MLB ledger. The self-test proves it from the source.
"""
import copy
import hashlib
import io
import json
import math
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "data", "mercer_dev")
REGISTRATIONS = os.path.join(DATA, "registrations")
AMENDMENTS = os.path.join(DATA, "amendments")
REGISTRY_LOG = os.path.join(DATA, "registry_log.json")
LEDGERS = os.path.join(DATA, "ledgers")
ET = ZoneInfo("America/New_York")

SPORTS = {"nfl": {"strategy_id": "mercer-nfl-dev", "league": "NFL",
                  "name": "DJ Mercer NFL — Developmental Cohort"},
          "ncaaf": {"strategy_id": "mercer-ncaaf-dev", "league": "NCAA FBS",
                    "name": "DJ Mercer NCAA — Developmental Cohort"}}
STRATEGY_SPORT = {v["strategy_id"]: k for k, v in SPORTS.items()}
MARKETS = ("moneyline", "spread", "total")

# Phase 4 field list. A registration missing any of these is refused.
REQUIRED_FIELDS = (
    "strategy_id", "strategy_name", "sport", "league", "version", "status",
    "registration_timestamp_utc", "effective_utc", "data_sources", "feature_definitions",
    "model_type", "selection_source", "training_window", "validation_window",
    "untouched_test_window", "eligible_markets", "selection_time", "min_edge_pts",
    "max_market_divergence", "min_data_quality", "odds_limits", "unit_sizing",
    "max_play_units", "max_daily_units", "max_plays_per_day", "manual_review",
    "live_plays_permitted", "conflicting_plays_permitted", "known_limitations",
    "change_control", "code_commit", "daily_exposure_scope", "featured_selection",
    "metric_availability")
# Changing any of these is a new version, never an amendment.
METHODOLOGY_FIELDS = (
    "feature_definitions", "model_type", "selection_source", "eligible_markets",
    "selection_time", "min_edge_pts", "max_market_divergence", "min_data_quality",
    "odds_limits", "unit_sizing", "max_play_units", "max_daily_units",
    "max_plays_per_day", "live_plays_permitted", "conflicting_plays_permitted",
    "data_sources", "sport", "strategy_id", "version")
STATUSES = ("PROPOSED", "REGISTERED")

PUBLICATION_FIELDS = (
    "row_type", "play_id", "strategy_id", "strategy_name", "version",
    "registration_sha256", "sport", "league", "espn_event_id", "odds_event_id",
    "scheduled_start_utc", "published_utc", "designation", "matchup", "market",
    "selection", "line", "odds", "book", "reference_source", "model_probability",
    "market_implied_probability", "edge_pts", "confidence", "recommended_units",
    "circuit_breakers", "manual_approver", "approved_utc", "artifact_sha256",
    "discord_message_ids", "featured", "spotlight_pick_id")
SETTLEMENT_FIELDS = (
    "row_type", "play_id", "result", "profit_units", "graded_utc", "home_score",
    "away_score", "closing_line", "closing_odds", "closing_probability", "clv_pts",
    "clv_unavailable_reason", "void_reason", "corrects")
RESULTS = ("WIN", "LOSS", "PUSH", "VOID")


class IntegrityError(RuntimeError):
    """A record or registration failed verification. Never continue past it."""


# --- utilities ----------------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_of(obj):
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def load_json(path, default=None):
    if not os.path.exists(path):
        return copy.deepcopy(default)
    with io.open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            # A corrupt record is never treated as empty: that is how a ledger
            # silently restarts at 0-0.
            raise IntegrityError(f"{os.path.relpath(path, ROOT)} is not valid JSON: {exc}")


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def et_date(dt):
    return dt.astimezone(ET).date().isoformat()


def implied(american):
    a = float(american)
    return 100.0 / (a + 100.0) if a > 0 else -a / (-a + 100.0)


def payout(american):
    a = float(american)
    return a / 100.0 if a > 0 else 100.0 / -a


# --- registry -----------------------------------------------------------------

def registration_path(strategy_id, version):
    return os.path.join(REGISTRATIONS, f"{strategy_id}-v{int(version)}.json")


def load_log():
    log = load_json(REGISTRY_LOG, {
        "_note": ("Append-only. One entry per registration or amendment, with the "
                  "SHA-256 of the exact file. A registration file that no longer "
                  "hashes to its entry is refused everywhere."),
        "entries": []})
    if not isinstance(log.get("entries"), list):
        raise IntegrityError("registry_log.json has no entries list")
    return log


def validate_registration(reg):
    missing = [f for f in REQUIRED_FIELDS if f not in reg]
    if missing:
        raise IntegrityError(f"registration missing fields: {missing}")
    sport = reg["sport"]
    if sport not in SPORTS:
        raise IntegrityError(f"unknown sport {sport!r}")
    if reg["strategy_id"] != SPORTS[sport]["strategy_id"]:
        raise IntegrityError(f"strategy_id {reg['strategy_id']!r} does not belong to {sport}")
    if not isinstance(reg["version"], int) or reg["version"] < 1:
        raise IntegrityError("version must be a positive integer")
    if reg["status"] not in STATUSES:
        raise IntegrityError(f"status must be one of {STATUSES}")
    bad = [m for m in reg["eligible_markets"] if m not in MARKETS]
    if bad or not reg["eligible_markets"]:
        raise IntegrityError(f"eligible_markets invalid: {reg['eligible_markets']}")
    for k in ("max_play_units", "max_daily_units"):
        v = reg[k]
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise IntegrityError(f"{k} must be a finite non-negative number")
    units = reg["unit_sizing"].get("flat_units")
    if not isinstance(units, (int, float)) or units < 0 or units > reg["max_play_units"]:
        raise IntegrityError("unit_sizing.flat_units must be within max_play_units")
    if reg["live_plays_permitted"] not in (True, False):
        raise IntegrityError("live_plays_permitted must be boolean")
    if reg["daily_exposure_scope"] != "all_developmental_cohorts":
        raise IntegrityError("daily exposure is capped across ALL developmental cohorts combined")
    if float(reg["max_play_units"]) != float(units):
        raise IntegrityError("developmental plays are flat-staked: max_play_units must equal flat_units")
    return True


def log_entries(strategy_id, version=None, kind="registration"):
    return [e for e in load_log()["entries"]
            if e["strategy_id"] == strategy_id and e.get("kind") == kind
            and (version is None or e["version"] == version)]


def register(strategy_id, version, now, approved_by):
    """Bind a PROPOSED file whose status has been set to REGISTERED.

    The caller edits status/timestamps/code_commit BEFORE calling this; from
    this call on the bytes are frozen. Refuses a second registration of the same
    version, a backdated effective date and an effective date in the past.
    """
    path = registration_path(strategy_id, version)
    reg = load_json(path)
    if reg is None:
        raise IntegrityError(f"no registration file for {strategy_id} v{version}")
    validate_registration(reg)
    if reg["status"] != "REGISTERED":
        raise IntegrityError("set status to REGISTERED before binding; PROPOSED is never binding")
    if log_entries(strategy_id, version):
        raise IntegrityError(f"{strategy_id} v{version} is already registered; "
                             "changes require a new version or an amendment")
    eff = parse_utc(reg["effective_utc"])
    stamp = parse_utc(reg["registration_timestamp_utc"])
    if not eff or not stamp:
        raise IntegrityError("effective_utc and registration_timestamp_utc must be UTC ISO stamps")
    if eff < now or stamp > now or eff < stamp:
        raise IntegrityError("effective_utc must be in the future and after the "
                             "registration timestamp; nothing is registered retroactively")
    if not reg["code_commit"] or len(str(reg["code_commit"])) < 7:
        raise IntegrityError("code_commit must identify the code the registration runs on")
    log = load_log()
    entry = {"kind": "registration", "strategy_id": strategy_id, "version": version,
             "sha256": sha256_of(reg), "file": os.path.relpath(path, ROOT).replace("\\", "/"),
             "logged_utc": iso(now), "effective_utc": reg["effective_utc"],
             "approved_by": approved_by}
    log["entries"].append(entry)
    save_json(REGISTRY_LOG, log)
    return entry


def verified_registration(strategy_id, version):
    """The registration as bound, or IntegrityError."""
    entries = log_entries(strategy_id, version)
    if len(entries) != 1:
        raise IntegrityError(f"{strategy_id} v{version} is not registered")
    reg = load_json(registration_path(strategy_id, version))
    if reg is None or sha256_of(reg) != entries[0]["sha256"]:
        raise IntegrityError(f"{strategy_id} v{version} registration file changed after "
                             "registration - refused")
    validate_registration(reg)
    return reg, entries[0]


def active_registration(sport, at):
    """Highest registered, effective, non-retired version for a sport at `at`."""
    sid = SPORTS[sport]["strategy_id"]
    best = None
    for e in log_entries(sid):
        eff = parse_utc(e["effective_utc"])
        if eff is None or eff > at:
            continue
        if any(a.get("retire") and parse_utc(a.get("effective_utc")) and
               parse_utc(a["effective_utc"]) <= at for a in amendments(sid, e["version"])):
            continue
        if best is None or e["version"] > best["version"]:
            best = e
    if best is None:
        return None, None
    return verified_registration(sid, best["version"])


def amendments(strategy_id, version):
    out = []
    for e in log_entries(strategy_id, version, kind="amendment"):
        doc = load_json(os.path.join(ROOT, e["file"]))
        if doc is None or sha256_of(doc) != e["sha256"]:
            raise IntegrityError(f"amendment {e['file']} changed after logging - refused")
        out.append(doc)
    return out


def amend(strategy_id, version, amendment, now):
    """Clerical amendment (notes, retirement). Methodology fields are refused."""
    verified_registration(strategy_id, version)
    touched = [f for f in METHODOLOGY_FIELDS if f in amendment.get("changes", {})]
    if touched:
        raise IntegrityError(f"{touched} are methodology fields: publish a new version")
    if not amendment.get("reason"):
        raise IntegrityError("an amendment must state its reason")
    n = len(log_entries(strategy_id, version, kind="amendment")) + 1
    path = os.path.join(AMENDMENTS, f"{strategy_id}-v{version}-A{n}.json")
    if os.path.exists(path):
        raise IntegrityError(f"{path} exists; amendments are never overwritten")
    doc = dict(amendment, strategy_id=strategy_id, version=version, number=n,
               amended_utc=iso(now))
    save_json(path, doc)
    log = load_log()
    log["entries"].append({"kind": "amendment", "strategy_id": strategy_id,
                           "version": version, "number": n, "sha256": sha256_of(doc),
                           "file": os.path.relpath(path, ROOT).replace("\\", "/"),
                           "logged_utc": iso(now)})
    save_json(REGISTRY_LOG, log)
    return doc


# --- ledgers ------------------------------------------------------------------

def ledger_path(strategy_id):
    if strategy_id not in STRATEGY_SPORT:
        raise IntegrityError(f"unknown strategy {strategy_id!r}")
    return os.path.join(LEDGERS, f"{strategy_id}.json")


def empty_ledger(strategy_id):
    sport = STRATEGY_SPORT[strategy_id]
    return {"_note": ("Append-only prospective record for ONE developmental cohort. "
                      "Rows are never edited or removed. Historical development "
                      "results are NOT in this file and never will be."),
            "strategy_id": strategy_id, "sport": sport, "rows": []}


def load_ledger(strategy_id):
    led = load_json(ledger_path(strategy_id), empty_ledger(strategy_id))
    if led.get("strategy_id") != strategy_id or led.get("sport") != STRATEGY_SPORT[strategy_id]:
        raise IntegrityError(f"{strategy_id} ledger header does not match its file")
    if not isinstance(led.get("rows"), list):
        raise IntegrityError(f"{strategy_id} ledger has no rows list")
    return led


def publications(led):
    return [r for r in led["rows"] if r["row_type"] == "publication"]


def settlements(led):
    return [r for r in led["rows"] if r["row_type"] == "settlement"]


def validate_publication(row, strategy_id, led, registration):
    missing = [f for f in PUBLICATION_FIELDS if f not in row]
    if missing:
        raise IntegrityError(f"publication row missing {missing}")
    sport = STRATEGY_SPORT[strategy_id]
    if row["strategy_id"] != strategy_id or row["sport"] != sport:
        raise IntegrityError("a play may only enter its own cohort's ledger")
    if registration is None:
        raise IntegrityError("no registration: nothing may be published")
    reg, entry = registration
    if row["version"] != reg["version"] or row["registration_sha256"] != entry["sha256"]:
        raise IntegrityError("play does not carry the active registration version and hash")
    pub = parse_utc(row["published_utc"])
    start = parse_utc(row["scheduled_start_utc"])
    eff = parse_utc(entry["effective_utc"])
    if not pub or not start or not eff:
        raise IntegrityError("publication, start and effective times must be UTC stamps")
    if pub < eff:
        raise IntegrityError("published before the registration was effective - no backfill")
    if row["designation"] != "pregame" and not reg["live_plays_permitted"]:
        raise IntegrityError("live plays are not permitted by this registration")
    if row["designation"] == "pregame" and pub >= start:
        raise IntegrityError("a pregame play must be published before its scheduled start")
    if row["market"] not in reg["eligible_markets"]:
        raise IntegrityError(f"market {row['market']} is not registered")
    if row["play_id"] in {p["play_id"] for p in publications(led)}:
        raise IntegrityError(f"duplicate play_id {row['play_id']}")
    return True


def validate_settlement(row, led):
    missing = [f for f in SETTLEMENT_FIELDS if f not in row]
    if missing:
        raise IntegrityError(f"settlement row missing {missing}")
    pubs = {p["play_id"]: p for p in publications(led)}
    if row["play_id"] not in pubs:
        raise IntegrityError("cannot settle a play that was never published")
    if row["result"] not in RESULTS:
        raise IntegrityError(f"result must be one of {RESULTS}")
    prior = [s for s in settlements(led) if s["play_id"] == row["play_id"]]
    if prior and not row.get("corrects"):
        raise IntegrityError("already settled; a change must be an explicit correction row")
    if row.get("corrects") and not prior:
        raise IntegrityError("a correction must reference an existing settlement")
    if row["result"] == "VOID" and not row.get("void_reason"):
        raise IntegrityError("a VOID must carry its reason")
    return True


def append_row(strategy_id, row, registration=None):
    """Append one row after validation. Returns the new ledger."""
    led = load_ledger(strategy_id)
    if row.get("row_type") == "publication":
        validate_publication(row, strategy_id, led, registration)
    elif row.get("row_type") == "settlement":
        validate_settlement(row, led)
    else:
        raise IntegrityError("row_type must be publication or settlement")
    before = copy.deepcopy(led["rows"])
    led["rows"].append(row)
    assert_append_only(before, led["rows"])
    save_json(ledger_path(strategy_id), led)
    return led


def assert_append_only(old_rows, new_rows):
    if len(new_rows) < len(old_rows) or new_rows[:len(old_rows)] != old_rows:
        raise IntegrityError("ledger history changed: rows may only be appended")


def effective_settlements(led):
    """Latest settlement per play (corrections supersede, originals stay)."""
    out = {}
    for s in settlements(led):
        out[s["play_id"]] = s
    return out


def aggregates(led):
    """Recomputed from rows. Never stored as truth."""
    pubs = publications(led)
    sets = effective_settlements(led)
    graded = []
    for p in sorted(pubs, key=lambda r: (r["scheduled_start_utc"], r["play_id"])):
        s = sets.get(p["play_id"])
        if s and s["result"] != "VOID":
            graded.append((p, s))
    w = sum(1 for _, s in graded if s["result"] == "WIN")
    l = sum(1 for _, s in graded if s["result"] == "LOSS")
    pu = sum(1 for _, s in graded if s["result"] == "PUSH")
    risked = sum(float(p["recommended_units"]) for p, s in graded if s["result"] != "PUSH")
    units = sum(float(s["profit_units"]) for _, s in graded)
    eq = peak = dd = 0.0
    streak = worst = 0
    for _, s in graded:
        eq += float(s["profit_units"])
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        streak = streak + 1 if s["result"] == "LOSS" else 0
        worst = max(worst, streak)
    clv = [s["clv_pts"] for _, s in graded if isinstance(s.get("clv_pts"), (int, float))]
    clv_missing = {}
    for _, s in graded:
        if not isinstance(s.get("clv_pts"), (int, float)):
            why = s.get("clv_unavailable_reason") or "no closing line recorded"
            clv_missing[why] = clv_missing.get(why, 0) + 1
    decided = [(p, s) for p, s in graded if s["result"] in ("WIN", "LOSS")]
    brier = None
    if decided:
        brier = round(sum((float(p["model_probability"]) - (1.0 if s["result"] == "WIN" else 0.0)) ** 2
                          for p, s in decided) / len(decided), 5)
    buckets = {}
    for p, s in decided:
        b = min(int(float(p["model_probability"]) * 10), 9)
        k = f"{b / 10:.1f}-{(b + 1) / 10:.1f}"
        buckets.setdefault(k, []).append((float(p["model_probability"]),
                                          1.0 if s["result"] == "WIN" else 0.0))
    return {
        "strategy_id": led["strategy_id"], "sport": led["sport"],
        "published": len(pubs), "graded": len(graded),
        "voids": sum(1 for s in sets.values() if s["result"] == "VOID"),
        "pending": len([p for p in pubs if p["play_id"] not in sets]),
        "record": f"{w}-{l}" + (f"-{pu}" if pu else ""),
        "units": round(units, 3), "risked_units": round(risked, 3),
        "roi_pct": round(100 * units / risked, 2) if risked else None,
        "max_drawdown_units": round(dd, 3), "longest_losing_streak": worst,
        "clv": {"n": len(clv), "of_graded": len(graded),
                "mean_pts": round(sum(clv) / len(clv), 3) if clv else None,
                "beat_close_pct": round(100 * sum(c > 0 for c in clv) / len(clv), 1) if clv else None,
                "unavailable": clv_missing},
        "featured": sum(1 for p in pubs if p.get("featured")),
        "brier_reference_probability": brier,
        "calibration": {k: {"n": len(v), "mean_p": round(sum(a for a, _ in v) / len(v), 4),
                            "win_rate": round(sum(b for _, b in v) / len(v), 4)}
                        for k, v in sorted(buckets.items())},
    }
