#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1: the append-only observation store.

SHAPE. JSON Lines, one record per line, sharded:

    <data_dir>/raw/<sport>/<ET date>/<kind>_<ET hour:02d>.jsonl
    <data_dir>/raw/runs/<ET date>/<run_id>.json          one per capture execution
    <data_dir>/digest/<ET date>.json                     fingerprints, committed

WHY THE RAW STREAM IS NOT COMMITTED. Measured before this was written: one NFL
Sunday at one-minute cadence with three markets compresses to ~4 MB, a college
Saturday to ~4x that, a season to hundreds of MB. GitHub Pages serves this
repository. So `data/mercer_live/raw/` is gitignored — exactly as
data/football/raw/pbp/ is — and the DIGEST is what gets committed: per shard,
the SHA-256, byte count, line count and observed_at span; per day, the run
count, error count, odds calls and credits spent. A shard whose bytes change
after its digest was committed is detectable by anyone with the file.

APPEND-ONLY, ENFORCED THE ONLY WAY A FILE CAN ENFORCE IT: this module opens
shards in append mode only, never rewrites, never deletes, and never exposes a
function that modifies a record. Every record has a deterministic
`observation_id`; appending a record whose id already exists in its shard is
refused and COUNTED (that is what makes an accidental retry of one execution
idempotent), while a fresh execution has a fresh run_id and therefore fresh
ids even when every value is identical (that is what makes a repeated
observation a distinct observation, by design — preregistration section 6).

WRITES ARE CONFINED. Every path is checked to lie inside data_dir before it is
opened. The default data_dir is data/mercer_live; tests and the smoke workflow
pass a temporary one.
"""
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common                                                     # noqa: E402


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, data_dir=None):
        self.data_dir = os.path.abspath(data_dir or common.DATA_DIR)
        self.raw_dir = os.path.join(self.data_dir, common.RAW_SUBDIR)
        self.digest_dir = os.path.join(self.data_dir, common.DIGEST_SUBDIR)

    # ---- confinement ------------------------------------------------------
    def _inside(self, path):
        p = os.path.abspath(path)
        if os.path.commonpath([p, self.data_dir]) != self.data_dir:
            raise StoreError(f"refusing to write outside {self.data_dir}: {p}")
        return p

    # ---- shards -----------------------------------------------------------
    def shard_path(self, kind, sport, observed_at_iso):
        if kind not in common.KINDS:
            raise StoreError(f"unknown record kind {kind!r}")
        dt = common.parse_utc(observed_at_iso)
        if dt is None:
            raise StoreError(f"record has no aware observed_at ({observed_at_iso!r})")
        return self._inside(os.path.join(
            self.raw_dir, sport, common.et_date(dt), f"{kind}_{common.et_hour(dt):02d}.jsonl"))

    @staticmethod
    def _existing_ids(path):
        ids = set()
        if not os.path.exists(path):
            return ids
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ids.add(json.loads(line).get("observation_id"))
                except ValueError:
                    # A torn line from a crashed writer. It is left in place
                    # (never rewritten) and reported by the digest's line
                    # count vs parse count; it cannot shadow a real id.
                    continue
        return ids

    def append(self, records):
        """Append records to their shards. Returns a summary dict.

        Duplicate observation_ids within a shard are skipped and counted.
        Records are validated for the three fields the store itself needs
        (kind, sport, observed_at, observation_id); their content is the
        parser's business and is written as given.
        """
        by_shard = {}
        for r in records:
            for f in ("kind", "sport", "observed_at", "observation_id"):
                if not r.get(f):
                    raise StoreError(f"record missing {f}: {common.canonical_json(r)[:200]}")
            by_shard.setdefault(self.shard_path(r["kind"], r["sport"], r["observed_at"]), []).append(r)
        written, dup, shards = 0, 0, []
        for path, recs in sorted(by_shard.items()):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            seen = self._existing_ids(path)
            lines = []
            for r in recs:
                if r["observation_id"] in seen:
                    dup += 1
                    continue
                seen.add(r["observation_id"])
                lines.append(common.canonical_json(r) + "\n")
            if lines:
                # A writer that died mid-line leaves a torn tail with no
                # newline. It is never rewritten (append-only), but the next
                # record must not be glued onto it, so start on a fresh line.
                needs_nl = False
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    with io.open(path, "rb") as f:
                        f.seek(-1, os.SEEK_END)
                        needs_nl = f.read(1) != b"\n"
                with io.open(path, "a", encoding="utf-8", newline="\n") as f:
                    if needs_nl:
                        f.write("\n")
                    f.write("".join(lines))
                    f.flush()
                    os.fsync(f.fileno())
                written += len(lines)
            shards.append(os.path.relpath(path, self.data_dir))
        return {"written": written, "duplicates_skipped": dup, "shards": shards}

    # ---- runs -------------------------------------------------------------
    def run_path(self, run_id, started_at_iso):
        dt = common.parse_utc(started_at_iso)
        if dt is None:
            raise StoreError("run record needs an aware started_at")
        safe = "".join(c for c in str(run_id) if c.isalnum() or c in "-_")
        if not safe:
            raise StoreError("run_id is empty after sanitising")
        return self._inside(os.path.join(self.raw_dir, "runs", common.et_date(dt), f"{safe}.json"))

    def find_run(self, run_id):
        """The existing run record for run_id, or None. Scans the runs tree;
        it is small (one file per tick) and this is called once per tick."""
        base = os.path.join(self.raw_dir, "runs")
        if not os.path.isdir(base):
            return None
        safe = "".join(c for c in str(run_id) if c.isalnum() or c in "-_") + ".json"
        for day in sorted(os.listdir(base)):
            p = os.path.join(base, day, safe)
            if os.path.exists(p):
                with io.open(p, encoding="utf-8") as f:
                    return json.load(f)
        return None

    def write_run(self, run):
        path = self.run_path(run["run_id"], run["started_at"])
        if os.path.exists(path):
            raise StoreError(f"run record already exists, never overwritten: {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(run, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)          # atomic: a run record is whole or absent
        return os.path.relpath(path, self.data_dir)

    def runs_for_date(self, et_date):
        base = os.path.join(self.raw_dir, "runs", et_date)
        out = []
        if not os.path.isdir(base):
            return out
        for fn in sorted(os.listdir(base)):
            if fn.endswith(".json"):
                with io.open(os.path.join(base, fn), encoding="utf-8") as f:
                    try:
                        out.append(json.load(f))
                    except ValueError:
                        continue
        return out

    # ---- digest -----------------------------------------------------------
    def digest(self, et_date, write=True):
        """Fingerprint one ET day of raw shards and runs. Returns the digest;
        writes <digest_dir>/<date>.json when write=True. Rewriting a digest is
        allowed: it is a DERIVATION of the raw files, not a record, and the
        raw shards it fingerprints are the thing that must not move."""
        shards = []
        for sport in sorted(common.SPORTS):
            d = os.path.join(self.raw_dir, sport, et_date)
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".jsonl"):
                    continue
                p = os.path.join(d, fn)
                h = hashlib.sha256()
                n_lines = n_parsed = 0
                first = last = None
                with io.open(p, "rb") as f:
                    for raw in f:
                        h.update(raw)
                        n_lines += 1
                        try:
                            rec = json.loads(raw.decode("utf-8"))
                            n_parsed += 1
                        except ValueError:
                            continue
                        oa = rec.get("observed_at")
                        if oa:
                            first = oa if first is None or oa < first else first
                            last = oa if last is None or oa > last else last
                shards.append({"path": os.path.relpath(p, self.data_dir).replace(os.sep, "/"),
                               # rsplit, NOT split: a shard is named <kind>_<ET
                               # hour>.jsonl and every kind contains an
                               # underscore, so split("_")[0] truncated
                               # game_state to "game" and collapsed BOTH
                               # market_event and market_quote to "market" -
                               # two different record kinds under one label in
                               # the committed artifact. Caught by the first
                               # live smoke run, 2026-09-21.
                               "sport": sport, "kind": fn.rsplit("_", 1)[0],
                               "sha256": h.hexdigest(), "bytes": os.path.getsize(p),
                               "lines": n_lines, "parsed": n_parsed,
                               "first_observed_at": first, "last_observed_at": last})
        runs = self.runs_for_date(et_date)
        odds_calls = [c for r in runs for c in (r.get("odds_calls") or [])]
        credits = [c.get("credits", {}).get("last_call_cost") for c in odds_calls]
        remaining = [c.get("credits", {}).get("remaining") for c in odds_calls
                     if c.get("credits", {}).get("remaining") is not None]
        dig = {
            "_note": ("Mercer Live ML-1 daily digest: SHA-256 fingerprints of the "
                      "append-only raw observation shards for this US/Eastern day, "
                      "plus run and credit counts. The raw shards are gitignored "
                      "(volume); this file is committed so the bytes can be "
                      "verified by anyone holding them. Derived, never edited by "
                      "hand. No model output, no pick, no unit."),
            "schema_version": common.SCHEMA_VERSION,
            "et_date": et_date,
            "generated_at": common.iso(common.now_utc()),
            "shards": shards,
            "runs": len(runs),
            "runs_with_errors": sum(1 for r in runs if r.get("errors")),
            "odds_calls": len(odds_calls),
            "odds_http_non_200": sum(1 for c in odds_calls
                                     if c.get("credits", {}).get("http_status") != 200),
            "credits_spent": sum(c for c in credits if isinstance(c, int)),
            "credits_remaining_at_last_call": remaining[-1] if remaining else None,
            "records_written": sum(r.get("records_written", 0) for r in runs),
        }
        if write:
            os.makedirs(self.digest_dir, exist_ok=True)
            path = self._inside(os.path.join(self.digest_dir, f"{et_date}.json"))
            tmp = path + ".tmp"
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(dig, f, indent=1, sort_keys=True)
            os.replace(tmp, path)
        return dig
