#!/usr/bin/env python3
"""
Open Ledger Sports - Mercer Live ML-1: the durable-evidence layer.

OPERATIONS, NOT CONTRACT. Nothing here changes what ML-1 records or how. It is
the machinery that keeps the append-only raw stream alive past the machine that
captured it and proves, from the committed digest, that the bytes held later
are the bytes observed then. docs/MERCER_LIVE_OPERATIONAL_PATH.md is the design.

    python scripts/mercer_live/archive.py ingest   --src DIR --data-dir DURABLE
    python scripts/mercer_live/archive.py verify   --data-dir DIR --digest FILE
    python scripts/mercer_live/archive.py sealed   --data-dir DIR [--grace-min 10]
    python scripts/mercer_live/archive.py build    --data-dir DIR --date YYYY-MM-DD
                                                  [--previous FILE] --out FILE

THE PROBLEM IT SOLVES. A shard is named by its ET hour, so two capture stores
that each observed the same hour hold two DIFFERENT files at the SAME relative
path (raw/nfl/2026-09-21/game_state_20.jsonl). Copying one store over another,
or digesting each store on its own and committing the last, silently loses the
earlier file's observations. That is not hypothetical: the 2026-09-21 ET smoke
evidence came from three separate runners. `ingest` merges at the RECORD level
through Store.append, which is the frozen append-only writer - observation ids
are preserved, duplicates are refused and counted, nothing existing is
rewritten - so every observation from every store survives into one durable
store, and the digest built from it then proves all of them.

THE DIGEST MAY GROW, NEVER CHANGE. A digest already committed is superseded
only by one that contains every committed shard and run record UNCHANGED plus
anything new (a late-ingested store). A changed or missing entry is refused:
either the evidence was altered or the day was digested before it closed, and
both need a human. On the capture host `build --previous` also re-hashes each
previously committed shard's first `bytes` bytes, so a shard that was only
APPENDED to is distinguished from one that was rewritten.

NO NETWORK, NO CREDENTIALS. This module reads and writes local files only.
Moving bytes to and from object storage is the host's rclone, configured from
secrets that live on the host and never in this repository.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402
from store import Store, StoreError                               # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHARD_RE = re.compile(r"^raw/(nfl|ncaaf)/(\d{4}-\d{2}-\d{2})/(game_state|market_event|market_quote)_(\d{2})\.jsonl$")
RUN_RE = re.compile(r"^raw/runs/(\d{4}-\d{2}-\d{2})/[A-Za-z0-9_-]+\.json$")
# The only top-level keys an ml1-v1 digest may carry (section 10 plus the
# section 20 amendment). Anything else is refused rather than committed.
DIGEST_KEYS = {"_note", "schema_version", "et_date", "generated_at", "shards", "run_records",
               "runs", "runs_with_errors", "odds_calls", "odds_http_non_200",
               "credits_spent", "credits_remaining_at_last_call", "records_written"}
DEFAULT_GRACE_MIN = 10


class ArchiveError(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers
def sha256_file(path, limit=None):
    h = hashlib.sha256()
    n = 0
    with io.open(path, "rb") as f:
        while True:
            want = 1 << 16 if limit is None else min(1 << 16, limit - n)
            if want <= 0:
                break
            chunk = f.read(want)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def count_lines(path):
    with io.open(path, "rb") as f:
        return sum(1 for _ in f)


def serialise(digest):
    """The exact bytes of a digest file, as Store.digest writes them."""
    return json.dumps(digest, indent=1, sort_keys=True)


def comparable(digest):
    """A digest minus its wall-clock stamp: two builds of unchanged evidence
    differ only in generated_at, and that must never count as a change."""
    return {k: v for k, v in digest.items() if k != "generated_at"}


# ------------------------------------------------------------------ validate
def validate(digest, et_date=None):
    """Structural validation of one digest. Returns a list of problems."""
    probs = []
    if not isinstance(digest, dict):
        return ["digest is not a JSON object"]
    extra = set(digest) - DIGEST_KEYS
    if extra:
        probs.append(f"unknown top-level keys {sorted(extra)}")
    for k in ("schema_version", "et_date", "shards", "run_records", "runs"):
        if k not in digest:
            probs.append(f"missing {k}")
    if digest.get("schema_version") != common.SCHEMA_VERSION:
        probs.append(f"schema_version {digest.get('schema_version')!r} != {common.SCHEMA_VERSION!r}")
    d = digest.get("et_date")
    if not isinstance(d, str) or not DATE_RE.match(d):
        probs.append(f"bad et_date {d!r}")
        return probs
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        probs.append(f"et_date {d!r} is not a calendar date")
    if et_date is not None and d != et_date:
        probs.append(f"et_date {d} does not match the requested date {et_date}")
    seen = set()
    for s in digest.get("shards") or []:
        m = SHARD_RE.match(str(s.get("path", "")))
        if not m:
            probs.append(f"shard path not allowed: {s.get('path')!r}")
            continue
        if m.group(2) != d:
            probs.append(f"shard {s['path']} is not under {d}")
        if s.get("kind") != m.group(3) or s.get("sport") != m.group(1):
            probs.append(f"shard {s['path']} kind/sport labels disagree with its path")
        if not re.fullmatch(r"[0-9a-f]{64}", str(s.get("sha256", ""))):
            probs.append(f"shard {s['path']} has no sha256")
        if not isinstance(s.get("bytes"), int) or not isinstance(s.get("lines"), int):
            probs.append(f"shard {s['path']} has no byte/line count")
        if s["path"] in seen:
            probs.append(f"shard {s['path']} listed twice")
        seen.add(s["path"])
    for r in digest.get("run_records") or []:
        m = RUN_RE.match(str(r.get("path", "")))
        if not m or m.group(1) != d:
            probs.append(f"run record path not allowed for {d}: {r.get('path')!r}")
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", str(r.get("sha256", ""))) or not isinstance(r.get("bytes"), int):
            probs.append(f"run record {r['path']} has no sha256/bytes")
        if r["path"] in seen:
            probs.append(f"run record {r['path']} listed twice")
        seen.add(r["path"])
    return probs


def supersedes(old, new):
    """(ok, reasons). `new` may replace a committed `old` only if it keeps every
    old shard and run record byte-for-byte (same sha256/bytes/lines) and only
    ADDS entries. Identical-apart-from-generated_at is ok (a no-op)."""
    reasons = []
    if old.get("et_date") != new.get("et_date"):
        reasons.append("different et_date")
    if old.get("schema_version") != new.get("schema_version"):
        reasons.append("different schema_version")
    for key in ("shards", "run_records"):
        new_by = {e.get("path"): e for e in new.get(key) or []}
        for e in old.get(key) or []:
            n = new_by.get(e.get("path"))
            if n is None:
                reasons.append(f"{key}: {e.get('path')} was committed and is now missing")
            elif any(n.get(f) != e.get(f) for f in ("sha256", "bytes", "lines")):
                reasons.append(f"{key}: {e.get('path')} changed after it was committed")
    for k in ("runs", "records_written", "odds_calls", "credits_spent"):
        if isinstance(old.get(k), int) and isinstance(new.get(k), int) and new[k] < old[k]:
            reasons.append(f"{k} went down ({old[k]} -> {new[k]})")
    return (not reasons), reasons


# --------------------------------------------------------------- verification
def verify(data_dir, digest):
    """Prove the bytes under data_dir are the bytes the digest fingerprints.
    Returns a list of failures (empty = verified). Also reports any shard or
    run record for that date that exists on disk but is NOT in the digest,
    because an unlisted file is evidence the digest does not cover."""
    fails = list(validate(digest))
    if fails:
        return fails
    root = os.path.abspath(data_dir)
    listed = set()
    for s in digest["shards"]:
        listed.add(s["path"])
        p = os.path.join(root, *s["path"].split("/"))
        if not os.path.isfile(p):
            fails.append(f"missing shard {s['path']}")
            continue
        sha, n = sha256_file(p)
        if sha != s["sha256"] or n != s["bytes"]:
            fails.append(f"shard {s['path']}: sha256/bytes differ from the digest")
        elif count_lines(p) != s["lines"]:
            fails.append(f"shard {s['path']}: line count differs from the digest")
    for r in digest["run_records"]:
        listed.add(r["path"])
        p = os.path.join(root, *r["path"].split("/"))
        if not os.path.isfile(p):
            fails.append(f"missing run record {r['path']}")
            continue
        sha, n = sha256_file(p)
        if sha != r["sha256"] or n != r["bytes"]:
            fails.append(f"run record {r['path']}: sha256/bytes differ from the digest")
    for rel in day_files(root, digest["et_date"]):
        if rel not in listed:
            fails.append(f"unlisted file on disk for {digest['et_date']}: {rel}")
    return fails


def verify_prefix(data_dir, old):
    """Append-only proof against a previously committed digest: each old
    shard's first `bytes` bytes still hash to the old sha256 (a shard that was
    appended to passes; one that was rewritten fails), and each old run record
    is unchanged. Returns a list of failures."""
    fails = []
    root = os.path.abspath(data_dir)
    for s in old.get("shards") or []:
        p = os.path.join(root, *s["path"].split("/"))
        if not os.path.isfile(p):
            fails.append(f"previously committed shard vanished: {s['path']}")
            continue
        sha, n = sha256_file(p, limit=s["bytes"])
        if n != s["bytes"] or sha != s["sha256"]:
            fails.append(f"previously committed shard was REWRITTEN, not appended: {s['path']}")
    for r in old.get("run_records") or []:
        p = os.path.join(root, *r["path"].split("/"))
        if not os.path.isfile(p):
            fails.append(f"previously committed run record vanished: {r['path']}")
            continue
        sha, n = sha256_file(p)
        if sha != r["sha256"] or n != r["bytes"]:
            fails.append(f"previously committed run record changed: {r['path']}")
    return fails


def day_files(root, et_date):
    """Relative paths of every shard and run record on disk for one ET date."""
    out = []
    raw = os.path.join(root, common.RAW_SUBDIR)
    for sport in sorted(common.SPORTS):
        d = os.path.join(raw, sport, et_date)
        if os.path.isdir(d):
            out += [f"raw/{sport}/{et_date}/{fn}" for fn in sorted(os.listdir(d)) if fn.endswith(".jsonl")]
    d = os.path.join(raw, "runs", et_date)
    if os.path.isdir(d):
        out += [f"raw/runs/{et_date}/{fn}" for fn in sorted(os.listdir(d)) if fn.endswith(".json")]
    return out


# ---------------------------------------------------------------------- ingest
def ingest(src_dir, dst):
    """Merge one capture store (an ephemeral runner's, a rescued artifact's)
    into the durable store `dst` (a Store). Record-level, append-only:

      * shard lines go through Store.append, so observation ids are kept,
        a record already present is refused and COUNTED, and no existing
        byte in dst is rewritten;
      * run records are copied byte-for-byte; one already present with
        identical bytes is skipped, one present with DIFFERENT bytes is a
        conflict and nothing further is written;
      * a line in the source that does not parse is NEVER dropped silently:
        ingest refuses before writing anything.

    Returns a report dict. Raises ArchiveError on any refusal."""
    src = os.path.abspath(src_dir)
    if os.path.abspath(dst.data_dir) == src:
        raise ArchiveError("source and destination are the same store")
    raw = os.path.join(src, common.RAW_SUBDIR)
    if not os.path.isdir(raw):
        raise ArchiveError(f"no raw/ directory under {src}")
    records, bad, run_files = [], [], []
    for dirpath, _dirs, files in os.walk(raw):
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, src).replace(os.sep, "/")
            if RUN_RE.match(rel):
                run_files.append((rel, p))
            elif SHARD_RE.match(rel):
                with io.open(p, "rb") as f:
                    for i, line in enumerate(f, 1):
                        if not line.strip():
                            continue
                        try:
                            records.append(json.loads(line.decode("utf-8")))
                        except ValueError:
                            bad.append(f"{rel}:{i}")
            elif fn.endswith(".tmp"):
                continue                                   # never a finished record
            else:
                raise ArchiveError(f"unexpected file in source store: {rel}")
    if bad:
        raise ArchiveError(f"source has unparseable lines, refusing to ingest any of it: {bad[:5]}")
    # Deterministic order: by observed_at, then id. Two ingests of the same set
    # of stores in any order produce the same records; the ORDER within a
    # shard follows ingest order, which is why the durable store, not the
    # ephemeral ones, is what the committed digest fingerprints.
    records.sort(key=lambda r: (str(r.get("observed_at")), str(r.get("observation_id"))))
    # Conflicts are checked before any write.
    runs_new, runs_same = [], 0
    for rel, p in sorted(run_files):
        dst_p = os.path.join(dst.data_dir, *rel.split("/"))
        with io.open(p, "rb") as f:
            body = f.read()
        try:
            json.loads(body.decode("utf-8"))
        except ValueError:
            raise ArchiveError(f"source run record does not parse: {rel}")
        if os.path.exists(dst_p):
            with io.open(dst_p, "rb") as f:
                if f.read() != body:
                    raise ArchiveError(f"run record conflict (same run_id, different bytes): {rel}")
            runs_same += 1
        else:
            runs_new.append((dst_p, body))
    try:
        summary = dst.append(records)
    except StoreError as e:
        raise ArchiveError(str(e))
    for dst_p, body in runs_new:
        dst._inside(dst_p)
        os.makedirs(os.path.dirname(dst_p), exist_ok=True)
        tmp = dst_p + ".tmp"
        with io.open(tmp, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst_p)
    return {"records_read": len(records), "records_written": summary["written"],
            "duplicates_skipped": summary["duplicates_skipped"],
            "run_records_copied": len(runs_new), "run_records_already_present": runs_same}


# --------------------------------------------------------------- sealed files
def _shard_hour_end(rel):
    m = SHARD_RE.match(rel)
    if not m:
        return None
    day = datetime.strptime(m.group(2), "%Y-%m-%d")
    # A shard is named by its ET WALL hour, so on the DST-end night the 01
    # shard holds BOTH 01:xx EDT and 01:xx EST - two real hours. Its end is
    # therefore the next WALL hour (02:00 EST), resolved to UTC only after the
    # wall arithmetic; adding one real hour to 01:00 EDT would seal it while
    # the second 01:xx hour is still being written.
    end_wall = (day + timedelta(hours=int(m.group(4)) + 1)).replace(tzinfo=common.ET, fold=0)
    return end_wall.astimezone(common.UTC)


def sealed(data_dir, now=None, grace_min=DEFAULT_GRACE_MIN):
    """Relative paths safe to copy off-host with --immutable: every run record
    (written once, atomically) and every shard whose ET hour ended at least
    grace_min ago (nothing will append to it again). The open hour's shard is
    NOT sealed; it is copied on the first pass after it closes."""
    now = now or common.now_utc()
    root = os.path.abspath(data_dir)
    raw = os.path.join(root, common.RAW_SUBDIR)
    out = []
    if not os.path.isdir(raw):
        return out
    for dirpath, _dirs, files in os.walk(raw):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            if RUN_RE.match(rel):
                out.append(rel)
            else:
                end = _shard_hour_end(rel)
                if end is not None and now >= end + timedelta(minutes=grace_min):
                    out.append(rel)
    return sorted(out)


def closed(et_date, now=None, grace_min=DEFAULT_GRACE_MIN):
    """True once the ET day has ended plus the grace period. A digest is only
    ever built for a closed day, so a committed digest never has to grow
    because the day was still being written."""
    now = now or common.now_utc()
    day = datetime.strptime(et_date, "%Y-%m-%d").replace(tzinfo=common.ET)
    end = (day + timedelta(days=1)).replace(tzinfo=common.ET)
    return now >= end.astimezone(common.UTC) + timedelta(minutes=grace_min)


# ---------------------------------------------------------------------- build
def build(data_dir, et_date, previous=None, now=None, grace_min=DEFAULT_GRACE_MIN):
    """Build the day's digest for publication. Refuses an open day, a digest
    that fails validation or verification, and one that cannot supersede the
    previously published digest. Returns (digest, action) where action is
    'new', 'unchanged' or 'grown'."""
    if not DATE_RE.match(et_date or ""):
        raise ArchiveError(f"bad date {et_date!r}")
    if not closed(et_date, now=now, grace_min=grace_min):
        raise ArchiveError(f"{et_date} (ET) has not closed yet; a digest is built only for a finished day")
    store = Store(data_dir)
    dig = store.digest(et_date, write=False)
    probs = validate(dig, et_date) + verify(data_dir, dig)
    if probs:
        raise ArchiveError("digest failed its own checks: " + "; ".join(probs[:5]))
    if previous is None:
        return dig, "new"
    pf = verify_prefix(data_dir, previous)
    ok, why = supersedes(previous, dig)
    if pf or not ok:
        raise ArchiveError("REFUSED - the evidence behind the published digest changed: "
                           + "; ".join((pf + why)[:5]))
    if comparable(previous) == comparable(dig):
        return previous, "unchanged"
    return dig, "grown"


# ------------------------------------------------------------------------ CLI
def _load(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest")
    a.add_argument("--src", required=True, action="append", help="repeatable")
    a.add_argument("--data-dir", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--data-dir", required=True)
    v.add_argument("--digest", required=True)
    s = sub.add_parser("sealed")
    s.add_argument("--data-dir", required=True)
    s.add_argument("--grace-min", type=int, default=DEFAULT_GRACE_MIN)
    b = sub.add_parser("build")
    b.add_argument("--data-dir", required=True)
    b.add_argument("--date", required=True)
    b.add_argument("--previous", default=None)
    b.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    # Output discipline: counts, paths and hashes only - never a record.
    try:
        if args.cmd == "ingest":
            dst = Store(args.data_dir)
            for src in args.src:
                r = ingest(src, dst)
                print(f"ingest {os.path.basename(os.path.normpath(src))}: " +
                      " ".join(f"{k}={v}" for k, v in sorted(r.items())))
            return 0
        if args.cmd == "verify":
            dig = _load(args.digest)
            fails = verify(args.data_dir, dig)
            n = len(dig.get("shards") or []) + len(dig.get("run_records") or [])
            if fails:
                for f in fails:
                    print("FAIL " + f)
                print(f"verify {dig.get('et_date')}: {len(fails)} failure(s) across {n} fingerprinted files")
                return 1
            print(f"verify {dig['et_date']}: OK - {len(dig['shards'])} shards and "
                  f"{len(dig['run_records'])} run records match the digest byte-for-byte")
            return 0
        if args.cmd == "sealed":
            for rel in sealed(args.data_dir, grace_min=args.grace_min):
                print(rel)
            return 0
        if args.cmd == "build":
            prev = _load(args.previous) if args.previous and os.path.exists(args.previous) else None
            dig, action = build(args.data_dir, args.date, previous=prev)
            if action != "unchanged":
                tmp = args.out + ".tmp"
                with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(serialise(dig))
                os.replace(tmp, args.out)
            body = serialise(dig).encode("utf-8")
            print(f"build {args.date}: {action} shards={len(dig['shards'])} "
                  f"run_records={len(dig['run_records'])} sha256={hashlib.sha256(body).hexdigest()[:16]}")
            return 0
    except ArchiveError as e:
        print(f"REFUSED: {e}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
