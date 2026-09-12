#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1, shared primitives.

Everything in scripts/mercer_live/ imports from here: paths, the schema
version, UTC time handling and observation identity. Nothing in this file
knows about a game, a price, a model or Discord.

THE ONE RULE THAT LIVES HERE: `observed_at` is the moment OPEN LEDGER received
the data, and it is stamped by this process from its own UTC clock. A provider
timestamp — a bookmaker's `last_update`, an HTTP `Date` header, a scoreboard's
kickoff — is kept beside it, never substituted for it. See
docs/MERCER_LIVE_V0.1_PREREGISTRATION.md section 5.

TIME IS ALWAYS AWARE, ALWAYS UTC. There is no naive datetime anywhere in this
package. `parse_utc` refuses a string with no zone designator rather than
guessing one, and `iso_ms` always writes a trailing `Z`.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

SCHEMA_VERSION = "ml1-1.0"
PACKAGE = "mercer-live-ml1"

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DATA_DIR = os.path.join(ROOT, "data", "mercer_live")
RAW_DIR = os.path.join(DATA_DIR, "raw")               # gitignored, append-only
MANIFEST = os.path.join(DATA_DIR, "manifest.json")    # committed, append-only
PREREG = os.path.join(ROOT, "docs", "MERCER_LIVE_V0.1_PREREGISTRATION.md")

# Directories this package must NEVER write into. Each is somebody else's
# evidence trail or a generated site artifact. store.ObservationStore refuses
# a raw directory that resolves inside any of them; the self-test proves it.
FORBIDDEN_WRITE_DIRS = [
    os.path.join(ROOT, "data", "football"),
    os.path.join(ROOT, "data", "mercer"),
    os.path.join(ROOT, "football"),
    os.path.join(ROOT, "picks"),
    os.path.join(ROOT, "blog"),
    os.path.join(ROOT, "odds"),
]


class MalformedPayload(Exception):
    """A provider response that cannot be read WITHOUT GUESSING.

    Raised, never worked around: a payload missing the fields that identify a
    game or price is recorded as a failed observation, and nothing is written
    for it. Fail closed. Never fabricate, never default.
    """


def now_utc():
    return datetime.now(timezone.utc)


def iso_ms(dt):
    """Aware datetime -> '2026-09-13T17:00:03.412Z'. Refuses naive input."""
    if dt.tzinfo is None:
        raise ValueError("refusing to format a naive datetime")
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def iso_s(dt):
    if dt.tzinfo is None:
        raise ValueError("refusing to format a naive datetime")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    """ISO-8601 with an explicit zone -> aware UTC datetime, else None.

    Accepts ESPN's minute-precision form ('2026-09-12T16:00Z'), the Odds API's
    second form, and fractional seconds. A string with NO zone designator
    returns None: a naive time is ambiguous and this package does not guess.
    """
    if not s or not isinstance(s, str):
        return None
    v = s.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def utc_date(dt):
    """The UTC calendar date a record files under. UTC on purpose: a
    partition keyed by Eastern date would move at DST changes."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def obs_id(*parts):
    """Deterministic observation identity.

    The parts ALWAYS begin with the run_id, which is why a retried execution
    of the same capture (same run_id) produces the same ids and is refused as
    a duplicate, while a deliberate later sample (new run_id) of an identical
    provider state produces new ids and is kept. Values are not part of the
    identity: an observation is "we looked at X at run R", not "X had value V".
    """
    return sha256_hex("|".join("" if p is None else str(p) for p in parts))[:32]


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def slot_run_id(league, at, interval_s):
    """run_id for a scheduled sample: the league plus the interval slot the
    sample belongs to. Two executions inside one slot share a run_id (and are
    therefore idempotent); the next slot is a new observation."""
    epoch = int(at.timestamp())
    slot = epoch - (epoch % max(1, int(interval_s)))
    return f"{league}:{datetime.fromtimestamp(slot, tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def prereg_frozen():
    """The `frozen:` value of the Mercer Live pre-registration, or None.

    Same convention as scripts/football/asof.frozen_date(). ML-1 has nothing to
    gate on it — it produces observations, not positions — but every later
    package that emits a signal must refuse to run while this returns None,
    so the hook lives here from the first package.
    """
    if not os.path.exists(PREREG):
        return None
    with open(PREREG, encoding="utf-8") as f:
        for line in f:
            if line.startswith("frozen:"):
                v = line.split(":", 1)[1].strip()
                return None if v.upper().startswith("NOT") else v
    return None
