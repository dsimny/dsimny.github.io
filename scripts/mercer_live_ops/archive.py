#!/usr/bin/env python3
"""
Open Ledger Sports - ML-1-OPS (package mercer-live-ml1-ops-v1.0): evidence
archival, the companion manifest, and restore verification.

    python scripts/mercer_live_ops/archive.py ingest  --src DIR [--src DIR ...] --data-dir DURABLE
    python scripts/mercer_live_ops/archive.py build   --data-dir DIR --date YYYY-MM-DD \
                                                      --out-digest F --out-manifest F \
                                                      [--previous-digest F --previous-manifest F]
    python scripts/mercer_live_ops/archive.py restore-verify --data-dir DIR --digest F --manifest F
    python scripts/mercer_live_ops/archive.py sealed  --data-dir DIR

A SEPARATE PACKAGE. mercer-live-ml1-v1.0 is frozen and is not changed by this
package: the record schema, the store, and the ml1-v1 daily digest are used
exactly as Store.digest() produces them. ML-1-OPS only READS frozen ML-1 output
and adds one artifact of its own: the companion manifest,
data/mercer_live/manifest/<ET date>.json, schema ml1-ops-manifest-v1.

WHY A MANIFEST. The frozen digest fingerprints every observation shard but not
the run records, which carry the error lists, the credit readings and the retry
evidence. The manifest fingerprints BOTH, and binds itself to the day's frozen
digest by that digest's SHA-256. The binding is ONE-WAY - manifest -> digest -
so the frozen digest never names the manifest and nothing is hashed in a
circle. The digest and the manifest are committed in the SAME git commit;
that commit is the pairing.

THE SAME-HOUR COLLISION. A shard is named by its ET hour, so two capture stores
that saw the same hour hold DIFFERENT files at the SAME relative path. `ingest`
merges at the RECORD level through the frozen Store.append (ids kept,
duplicates refused and counted, nothing rewritten), so every observation from
every store survives into one durable store.

GROW, NEVER CHANGE. A committed digest/manifest pair is superseded only by a
pair that keeps every committed entry byte-for-byte and adds new evidence.
Deletion, replacement, a hash change, shrinking, or a manifest bound to a
different digest are refused. Rebuilding unchanged evidence reuses the
published files, so publication is idempotent.

NO NETWORK, NO CREDENTIALS, NO VALUES. This module reads and writes local files
only, and the manifest holds paths, sizes, counts and hashes - never an
observed value, a provider payload, or a secret.
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
ML1 = os.path.abspath(os.path.join(HERE, "..", "mercer_live"))
for _p in (ML1, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common                                                     # noqa: E402  frozen ML-1, read-only
from store import Store, StoreError                               # noqa: E402  frozen ML-1, read-only

PACKAGE = "mercer-live-ml1-ops-v1.0"
MANIFEST_SCHEMA = "ml1-ops-manifest-v1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SHARD_RE = re.compile(r"^raw/(nfl|ncaaf)/(\d{4}-\d{2}-\d{2})/(game_state|market_event|market_quote)_(\d{2})\.jsonl$")
RUN_RE = re.compile(r"^raw/runs/(\d{4}-\d{2}-\d{2})/[A-Za-z0-9_-]+\.json$")
# The frozen ml1-v1 digest's keys, exactly (architecture section 10). A digest
# with any other key is not an ml1-v1 digest and is refused.
DIGEST_KEYS = frozenset({"_note", "schema_version", "et_date", "generated_at", "shards", "runs",
                         "runs_with_errors", "odds_calls", "odds_http_non_200", "credits_spent",
                         "credits_remaining_at_last_call", "records_written"})
MANIFEST_KEYS = frozenset({"_note", "schema_version", "package", "et_date", "generated_at",
                           "ml1_digest", "shards", "run_records", "counts"})
SHARD_ENTRY_KEYS = frozenset({"path", "sport", "kind", "sha256", "bytes", "lines", "parsed"})
RUN_ENTRY_KEYS = frozenset({"path", "sha256", "bytes"})
DIGEST_REF_KEYS = frozenset({"path", "sha256", "bytes", "schema_version"})
DEFAULT_GRACE_MIN = 10


class ArchiveError(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers
def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path, limit=None):
    h, n = hashlib.sha256(), 0
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


def serialise(obj):
    """Exact bytes of a digest or manifest file: the frozen Store.digest
    serialisation (json.dump, indent=1, sort_keys), no trailing newline."""
    return json.dumps(obj, indent=1, sort_keys=True).encode("utf-8")


def comparable(obj):
    """An artifact minus its wall-clock stamp; two builds of unchanged evidence
    differ only in generated_at, which must never count as a change."""
    return {k: v for k, v in obj.items() if k != "generated_at"}


def digest_path(et_date):
    return f"data/mercer_live/digest/{et_date}.json"


def manifest_path(et_date):
    return f"data/mercer_live/manifest/{et_date}.json"


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


def _abs(root, rel):
    return os.path.join(os.path.abspath(root), *rel.split("/"))


# --------------------------------------------------------------- validation
def validate_digest(digest, et_date=None):
    """The frozen ml1-v1 digest, structurally. Returns a list of problems."""
    if not isinstance(digest, dict):
        return ["digest is not a JSON object"]
    probs = []
    if set(digest) != DIGEST_KEYS:
        probs.append(f"digest keys differ from frozen ml1-v1 (extra {sorted(set(digest) - DIGEST_KEYS)}, "
                     f"missing {sorted(DIGEST_KEYS - set(digest))})")
    if digest.get("schema_version") != common.SCHEMA_VERSION:
        probs.append(f"digest schema_version {digest.get('schema_version')!r} != {common.SCHEMA_VERSION!r}")
    d = digest.get("et_date")
    if not isinstance(d, str) or not DATE_RE.match(d):
        return probs + [f"digest has bad et_date {d!r}"]
    if et_date is not None and d != et_date:
        probs.append(f"digest et_date {d} != {et_date}")
    probs += _validate_shards(digest.get("shards"), d, "digest", frozen=True)
    return probs


def validate_manifest(man, et_date=None):
    """ml1-ops-manifest-v1, structurally. Returns a list of problems."""
    if not isinstance(man, dict):
        return ["manifest is not a JSON object"]
    probs = []
    if set(man) != MANIFEST_KEYS:
        probs.append(f"manifest keys differ from {MANIFEST_SCHEMA} (extra {sorted(set(man) - MANIFEST_KEYS)}, "
                     f"missing {sorted(MANIFEST_KEYS - set(man))})")
    if man.get("schema_version") != MANIFEST_SCHEMA:
        probs.append(f"manifest schema_version {man.get('schema_version')!r} != {MANIFEST_SCHEMA!r}")
    if man.get("package") != PACKAGE:
        probs.append(f"manifest package {man.get('package')!r} != {PACKAGE!r}")
    d = man.get("et_date")
    if not isinstance(d, str) or not DATE_RE.match(d):
        return probs + [f"manifest has bad et_date {d!r}"]
    if et_date is not None and d != et_date:
        probs.append(f"manifest et_date {d} != {et_date}")
    ref = man.get("ml1_digest")
    if not isinstance(ref, dict) or set(ref) != DIGEST_REF_KEYS:
        probs.append("manifest ml1_digest must be exactly {path, sha256, bytes, schema_version}")
    else:
        if ref.get("path") != digest_path(d):
            probs.append(f"manifest ml1_digest.path {ref.get('path')!r} != {digest_path(d)!r}")
        if not SHA_RE.match(str(ref.get("sha256", ""))) or not isinstance(ref.get("bytes"), int):
            probs.append("manifest ml1_digest has no sha256/bytes")
        if ref.get("schema_version") != common.SCHEMA_VERSION:
            probs.append("manifest ml1_digest.schema_version is not ml1-v1")
    probs += _validate_shards(man.get("shards"), d, "manifest", frozen=False)
    seen = set()
    runs = man.get("run_records")
    if not isinstance(runs, list):
        probs.append("manifest run_records is not a list")
        runs = []
    for r in runs:
        if not isinstance(r, dict) or set(r) != RUN_ENTRY_KEYS:
            probs.append(f"manifest run record entry has wrong keys: {r!r}"[:200])
            continue
        m = RUN_RE.match(str(r["path"]))
        if not m or m.group(1) != d:
            probs.append(f"run record path not allowed for {d}: {r['path']!r}")
        if not SHA_RE.match(str(r["sha256"])) or not isinstance(r["bytes"], int):
            probs.append(f"run record {r['path']} has no sha256/bytes")
        if r["path"] in seen:
            probs.append(f"run record {r['path']} listed twice")
        seen.add(r["path"])
    c = man.get("counts")
    if isinstance(c, dict) and isinstance(man.get("shards"), list):
        want = _counts(man["shards"], runs)
        if c != want:
            probs.append(f"manifest counts {c} disagree with its entries {want}")
    else:
        probs.append("manifest counts missing")
    return probs


def _validate_shards(shards, d, label, frozen):
    probs, seen = [], set()
    if not isinstance(shards, list):
        return [f"{label} shards is not a list"]
    for s in shards:
        if not isinstance(s, dict):
            probs.append(f"{label} shard entry is not an object")
            continue
        if not frozen and set(s) != SHARD_ENTRY_KEYS:
            probs.append(f"{label} shard entry has wrong keys: {sorted(s)}")
        m = SHARD_RE.match(str(s.get("path", "")))
        if not m:
            probs.append(f"{label} shard path not allowed: {s.get('path')!r}")
            continue
        if m.group(2) != d:
            probs.append(f"{label} shard {s['path']} is not under {d}")
        if s.get("kind") != m.group(3) or s.get("sport") != m.group(1):
            probs.append(f"{label} shard {s['path']} kind/sport labels disagree with its path")
        if not SHA_RE.match(str(s.get("sha256", ""))):
            probs.append(f"{label} shard {s['path']} has no sha256")
        if not isinstance(s.get("bytes"), int) or not isinstance(s.get("lines"), int):
            probs.append(f"{label} shard {s['path']} has no byte/line count")
        if s["path"] in seen:
            probs.append(f"{label} shard {s['path']} listed twice")
        seen.add(s["path"])
    return probs


def _counts(shards, runs):
    return {"shards": len(shards), "run_records": len(runs),
            "shard_bytes": sum(s.get("bytes", 0) for s in shards),
            "shard_lines": sum(s.get("lines", 0) for s in shards),
            "shard_records": sum(s.get("parsed", 0) for s in shards),
            "run_record_bytes": sum(r.get("bytes", 0) for r in runs)}


# ------------------------------------------------------------------ pairing
def check_pair(digest_bytes, man):
    """The one-way binding and the cross-checks between a frozen digest and
    its manifest. Returns a list of problems (empty = a valid pair)."""
    try:
        digest = json.loads(digest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ["digest is not JSON"]
    probs = validate_digest(digest) + validate_manifest(man)
    if probs:
        return probs
    ref = man["ml1_digest"]
    if sha256_bytes(digest_bytes) != ref["sha256"] or len(digest_bytes) != ref["bytes"]:
        probs.append("manifest is bound to a DIFFERENT digest (sha256/bytes mismatch)")
    if digest["et_date"] != man["et_date"]:
        probs.append("digest and manifest are for different dates")
    dsh = {s["path"]: s for s in digest["shards"]}
    msh = {s["path"]: s for s in man["shards"]}
    if set(dsh) != set(msh):
        probs.append("digest and manifest list different shards")
    for p in set(dsh) & set(msh):
        if any(dsh[p].get(k) != msh[p].get(k) for k in ("sha256", "bytes", "lines", "parsed", "kind", "sport")):
            probs.append(f"digest and manifest disagree on shard {p}")
    if isinstance(digest.get("runs"), int) and len(man["run_records"]) < digest["runs"]:
        probs.append(f"manifest lists {len(man['run_records'])} run records but the digest counted {digest['runs']}")
    return probs


# ------------------------------------------------------------------- build
def build_manifest(data_dir, et_date, digest_bytes, now=None):
    """The ml1-ops-manifest-v1 for one ET date, bound to `digest_bytes`
    (the exact bytes of that day's frozen digest file)."""
    root = os.path.abspath(data_dir)
    shards, runs = [], []
    for rel in day_files(root, et_date):
        p = _abs(root, rel)
        if RUN_RE.match(rel):
            sha, n = sha256_file(p)
            runs.append({"path": rel, "sha256": sha, "bytes": n})
            continue
        m = SHARD_RE.match(rel)
        if not m:
            continue
        h, n_bytes, n_lines, n_parsed = hashlib.sha256(), 0, 0, 0
        with io.open(p, "rb") as f:
            for raw in f:
                h.update(raw)
                n_bytes += len(raw)
                n_lines += 1
                try:
                    json.loads(raw.decode("utf-8"))
                    n_parsed += 1
                except ValueError:
                    pass
        shards.append({"path": rel, "sport": m.group(1), "kind": m.group(3), "sha256": h.hexdigest(),
                       "bytes": n_bytes, "lines": n_lines, "parsed": n_parsed})
    return {
        "_note": ("ML-1-OPS companion manifest: SHA-256, byte and line counts of every raw "
                  "observation shard AND every run record for this US/Eastern day, bound one-way "
                  "to the frozen ml1-v1 digest of the same day by that digest's SHA-256. Paths, "
                  "sizes, counts and hashes only - no observed value, no provider payload, no "
                  "secret. Committed in the same git commit as the digest it names."),
        "schema_version": MANIFEST_SCHEMA,
        "package": PACKAGE,
        "et_date": et_date,
        "generated_at": common.iso(now or common.now_utc()),
        "ml1_digest": {"path": digest_path(et_date), "sha256": sha256_bytes(digest_bytes),
                       "bytes": len(digest_bytes), "schema_version": common.SCHEMA_VERSION},
        "shards": shards,
        "run_records": runs,
        "counts": _counts(shards, runs),
    }


def supersedes(old, new, entry_keys=("shards", "run_records")):
    """(ok, reasons). `new` may replace a committed `old` only if every
    committed entry is present and unchanged and no count went down."""
    reasons = []
    if old.get("et_date") != new.get("et_date"):
        reasons.append("different et_date")
    if old.get("schema_version") != new.get("schema_version"):
        reasons.append("different schema_version")
    for key in entry_keys:
        new_by = {e.get("path"): e for e in new.get(key) or []}
        for e in old.get(key) or []:
            n = new_by.get(e.get("path"))
            if n is None:
                reasons.append(f"{key}: {e.get('path')} was committed and is now missing")
            elif n != e:
                reasons.append(f"{key}: {e.get('path')} changed after it was committed")
    for k in ("runs", "records_written", "odds_calls", "credits_spent"):
        if isinstance(old.get(k), int) and isinstance(new.get(k), int) and new[k] < old[k]:
            reasons.append(f"{k} went down ({old[k]} -> {new[k]})")
    oc, nc = old.get("counts"), new.get("counts")
    if isinstance(oc, dict) and isinstance(nc, dict):
        for k, v in oc.items():
            if isinstance(v, int) and isinstance(nc.get(k), int) and nc[k] < v:
                reasons.append(f"counts.{k} went down ({v} -> {nc[k]})")
    return (not reasons), reasons


def supersedes_pair(old_digest, old_man, new_digest, new_man):
    """Both artifacts grow-only; digest entries compared on the frozen shard fields."""
    ok1, r1 = supersedes(old_digest, new_digest, entry_keys=("shards",))
    ok2, r2 = supersedes(old_man, new_man)
    return ok1 and ok2, [f"digest: {x}" for x in r1] + [f"manifest: {x}" for x in r2]


def closed(et_date, now=None, grace_min=DEFAULT_GRACE_MIN):
    """True once the ET day has ended plus the grace period."""
    now = now or common.now_utc()
    day = datetime.strptime(et_date, "%Y-%m-%d").replace(tzinfo=common.ET)
    end = (day + timedelta(days=1)).replace(tzinfo=common.ET)
    return now >= end.astimezone(common.UTC) + timedelta(minutes=grace_min)


def build(data_dir, et_date, previous=None, now=None, grace_min=DEFAULT_GRACE_MIN):
    """Build the (digest bytes, manifest) pair for a CLOSED ET day.

    previous: None, or (digest_bytes, manifest) as last published. Returns
    (digest_bytes, manifest, action) with action 'new' | 'unchanged' | 'grown'.
    Unchanged evidence returns the PUBLISHED bytes untouched, which is what
    makes repeat publication idempotent despite generated_at."""
    if not DATE_RE.match(et_date or ""):
        raise ArchiveError(f"bad date {et_date!r}")
    if not closed(et_date, now=now, grace_min=grace_min):
        raise ArchiveError(f"{et_date} (ET) has not closed yet; a digest is built only for a finished day")
    fresh_digest = Store(data_dir).digest(et_date, write=False)       # frozen ML-1, as frozen
    if previous is not None:
        prev_bytes, prev_man = previous
        probs = check_pair(prev_bytes, prev_man)
        if probs:
            raise ArchiveError("the previously published pair is invalid: " + "; ".join(probs[:3]))
        prev_digest = json.loads(prev_bytes.decode("utf-8"))
        pf = verify_prefix(data_dir, prev_man)
        if pf:
            raise ArchiveError("REFUSED - committed evidence changed on disk: " + "; ".join(pf[:5]))
        if comparable(prev_digest) == comparable(fresh_digest):
            digest_bytes = prev_bytes                                  # reuse: same evidence, same bytes
        else:
            digest_bytes = serialise(fresh_digest)
    else:
        digest_bytes = serialise(fresh_digest)
    man = build_manifest(data_dir, et_date, digest_bytes, now=now)
    probs = check_pair(digest_bytes, man) + restore_verify(data_dir, digest_bytes, man)
    if probs:
        raise ArchiveError("the new pair failed its own checks: " + "; ".join(probs[:5]))
    if previous is None:
        return digest_bytes, man, "new"
    ok, why = supersedes_pair(prev_digest, prev_man, json.loads(digest_bytes.decode("utf-8")), man)
    if not ok:
        raise ArchiveError("REFUSED - the new pair does not only add to the published pair: " + "; ".join(why[:5]))
    if digest_bytes == prev_bytes and comparable(man) == comparable(prev_man):
        return prev_bytes, prev_man, "unchanged"
    return digest_bytes, man, "grown"


# -------------------------------------------------------------- verification
def restore_verify(data_dir, digest_bytes, man):
    """A restore succeeds ONLY if all of these hold (returns failures):
      1. the manifest is bound to exactly these digest bytes (SHA-256, bytes);
      2. the frozen digest independently verifies every shard it covers;
      3. every shard AND every run record matches the manifest;
      4. no shard or run record for that date exists that neither lists."""
    fails = check_pair(digest_bytes, man)
    if fails:
        return fails
    digest = json.loads(digest_bytes.decode("utf-8"))
    root = os.path.abspath(data_dir)
    for s in digest["shards"]:                                         # 2. frozen digest, on its own terms
        p = _abs(root, s["path"])
        if not os.path.isfile(p):
            fails.append(f"digest: missing shard {s['path']}")
            continue
        sha, n = sha256_file(p)
        if sha != s["sha256"] or n != s["bytes"]:
            fails.append(f"digest: shard {s['path']} does not match")
    for s in man["shards"]:                                            # 3. manifest shards
        p = _abs(root, s["path"])
        if not os.path.isfile(p):
            fails.append(f"manifest: missing shard {s['path']}")
            continue
        sha, n = sha256_file(p)
        with io.open(p, "rb") as f:
            lines = sum(1 for _ in f)
        if sha != s["sha256"] or n != s["bytes"] or lines != s["lines"]:
            fails.append(f"manifest: shard {s['path']} does not match")
    for r in man["run_records"]:                                       # 3. manifest run records
        p = _abs(root, r["path"])
        if not os.path.isfile(p):
            fails.append(f"manifest: missing run record {r['path']}")
            continue
        sha, n = sha256_file(p)
        if sha != r["sha256"] or n != r["bytes"]:
            fails.append(f"manifest: run record {r['path']} does not match")
    listed = {s["path"] for s in man["shards"]} | {r["path"] for r in man["run_records"]}
    for rel in day_files(root, man["et_date"]):                        # 4. nothing unlisted
        if rel not in listed:
            fails.append(f"unlisted file on disk for {man['et_date']}: {rel}")
    return fails


def verify_prefix(data_dir, man):
    """Append-only proof against a published manifest: each committed shard's
    first `bytes` bytes still hash to its committed sha256 (appended passes,
    rewritten fails), and each committed run record is unchanged."""
    fails = []
    root = os.path.abspath(data_dir)
    for s in man.get("shards") or []:
        p = _abs(root, s["path"])
        if not os.path.isfile(p):
            fails.append(f"previously committed shard vanished: {s['path']}")
            continue
        sha, n = sha256_file(p, limit=s["bytes"])
        if n != s["bytes"] or sha != s["sha256"]:
            fails.append(f"previously committed shard was REWRITTEN, not appended: {s['path']}")
    for r in man.get("run_records") or []:
        p = _abs(root, r["path"])
        if not os.path.isfile(p):
            fails.append(f"previously committed run record vanished: {r['path']}")
            continue
        sha, n = sha256_file(p)
        if sha != r["sha256"] or n != r["bytes"]:
            fails.append(f"previously committed run record changed: {r['path']}")
    return fails


# ---------------------------------------------------------------------- ingest
def ingest(src_dir, dst):
    """Merge one capture store into the durable store `dst` (a frozen Store),
    record-level and append-only. Refuses, before writing anything, a source
    with an unparseable line, an unexpected file, or a run record whose run_id
    already exists in dst with different bytes."""
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
                continue
            else:
                raise ArchiveError(f"unexpected file in source store: {rel}")
    if bad:
        raise ArchiveError(f"source has unparseable lines, refusing to ingest any of it: {bad[:5]}")
    records.sort(key=lambda r: (str(r.get("observed_at")), str(r.get("observation_id"))))
    runs_new, runs_same = [], 0
    for rel, p in sorted(run_files):
        dst_p = _abs(dst.data_dir, rel)
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
        summary = dst.append(records)                                   # frozen append-only writer
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
    # Named by ET WALL hour: on the DST-end night the 01 shard holds both 01:xx
    # EDT and 01:xx EST, so it ends at the next WALL hour, resolved to UTC only
    # after the wall arithmetic.
    end_wall = (day + timedelta(hours=int(m.group(4)) + 1)).replace(tzinfo=common.ET, fold=0)
    return end_wall.astimezone(common.UTC)


def sealed(data_dir, now=None, grace_min=DEFAULT_GRACE_MIN):
    """Relative paths safe to copy off-host with --immutable: every run record
    (atomic, written once) and every shard whose ET hour ended >= grace ago."""
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


# ------------------------------------------------------------------------ CLI
def _read(path):
    with io.open(path, "rb") as f:
        return f.read()


def _write(path, body):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ingest")
    a.add_argument("--src", required=True, action="append")
    a.add_argument("--data-dir", required=True)
    b = sub.add_parser("build")
    b.add_argument("--data-dir", required=True)
    b.add_argument("--date", required=True)
    b.add_argument("--out-digest", required=True)
    b.add_argument("--out-manifest", required=True)
    b.add_argument("--previous-digest", default=None)
    b.add_argument("--previous-manifest", default=None)
    v = sub.add_parser("restore-verify")
    v.add_argument("--data-dir", required=True)
    v.add_argument("--digest", required=True)
    v.add_argument("--manifest", required=True)
    s = sub.add_parser("sealed")
    s.add_argument("--data-dir", required=True)
    args = ap.parse_args(argv)
    # Output discipline: counts, dates, paths and hashes only - never a record.
    try:
        if args.cmd == "ingest":
            dst = Store(args.data_dir)
            for src in args.src:
                r = ingest(src, dst)
                print(f"ingest {os.path.basename(os.path.normpath(src))}: "
                      + " ".join(f"{k}={v}" for k, v in sorted(r.items())))
            return 0
        if args.cmd == "build":
            prev = None
            if args.previous_digest and args.previous_manifest and os.path.exists(args.previous_digest) \
                    and os.path.exists(args.previous_manifest):
                prev = (_read(args.previous_digest), json.loads(_read(args.previous_manifest).decode("utf-8")))
            db, man, action = build(args.data_dir, args.date, previous=prev)
            if action != "unchanged":
                _write(args.out_digest, db)
                _write(args.out_manifest, serialise(man))
            print(f"build {args.date}: {action} shards={man['counts']['shards']} "
                  f"run_records={man['counts']['run_records']} digest_sha256={sha256_bytes(db)[:16]}")
            return 0
        if args.cmd == "restore-verify":
            db = _read(args.digest)
            man = json.loads(_read(args.manifest).decode("utf-8"))
            fails = restore_verify(args.data_dir, db, man)
            if fails:
                for f in fails:
                    print("FAIL " + f)
                print(f"restore-verify {man.get('et_date')}: {len(fails)} failure(s)")
                return 1
            print(f"restore-verify {man['et_date']}: OK - {man['counts']['shards']} shards and "
                  f"{man['counts']['run_records']} run records match the manifest; the manifest is bound "
                  f"to the digest; the digest verifies its shards")
            return 0
        if args.cmd == "sealed":
            for rel in sealed(args.data_dir):
                print(rel)
            return 0
    except (ArchiveError, ValueError) as e:
        print(f"REFUSED: {e}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
