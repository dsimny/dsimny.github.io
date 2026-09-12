#!/usr/bin/env python3
"""
Open Ledger Sports — Mercer Live ML-1, the append-only observation store.

SHAPE. Newline-delimited JSON, gzip-appended, partitioned by league and UTC
date, one file per record kind:

    data/mercer_live/raw/<league>/<YYYY-MM-DD>/game_observations.ndjson.gz
    data/mercer_live/raw/<league>/<YYYY-MM-DD>/market_observations.ndjson.gz
    data/mercer_live/raw/<league>/<YYYY-MM-DD>/market_coverage.ndjson.gz
    data/mercer_live/raw/<league>/<YYYY-MM-DD>/ticks.ndjson.gz
    data/mercer_live/raw/<league>/<YYYY-MM-DD>/payloads/<run_id>.<source>.json.gz
    data/mercer_live/raw/<league>/<YYYY-MM-DD>/runs.txt

APPEND-ONLY, IN THE FILE FORMAT ITSELF. gzip members concatenate, so every
append is a new member at the end of the file and nothing before it is
touched; gzip.open reads the concatenation as one stream. There is no
"update" path in this module. A hundred observations of one game are a
hundred lines (pre-registration section 6). The convenience "latest" view is
whatever reads the last line per event — the history stays.

WHY NOT GIT. One NFL Sunday at one-minute cadence is on the order of half a
million market rows. Committing that to a public Pages repository that seven
workflows already race to push to is not a storage plan, and it is the exact
shape of trouble scripts/football/selftest_push_pattern.py exists to prevent.
So raw/ is GITIGNORED, like data/football/raw/pbp/ and odds/hist/, and the
repository carries the MANIFEST instead: sha256, byte size and row count per
sealed file, append-only, the same pattern as data/football/pbp_manifest.json.
The raw files are the evidence; the manifest is what makes them tamper-evident
once they are backed up off-machine.

IDEMPOTENCY. runs.txt lists every run_id that has been written into that
partition. A tick whose run_id is already listed is refused BEFORE anything
is appended, so an accidental re-execution of the same capture writes nothing.
The run_id is recorded only after every append for that tick succeeded, so a
crash mid-tick leaves the run re-runnable (and any partial lines carry the
same obs_ids, which is how a reader detects and collapses them).

REFUSALS. The store will not open under any directory in
mlcommon.FORBIDDEN_WRITE_DIRS — data/football/, data/mercer/, football/ and
the site directories. A defect here can fill a disk; it cannot touch the
pregame evidence trail or a ledger.
"""
import gzip
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mlcommon as C                                  # noqa: E402

KINDS = ("game_observations", "market_observations", "market_coverage", "ticks")


class RefusedDirectory(Exception):
    """The raw directory resolves into somebody else's evidence trail."""


class DuplicateRun(Exception):
    """This run_id has already been written into this partition."""


def _inside(path, parent):
    path, parent = os.path.abspath(path), os.path.abspath(parent)
    return path == parent or path.startswith(parent + os.sep)


class ObservationStore:
    def __init__(self, raw_dir=C.RAW_DIR):
        raw_dir = os.path.abspath(raw_dir)
        for bad in C.FORBIDDEN_WRITE_DIRS:
            if _inside(raw_dir, bad):
                raise RefusedDirectory(
                    f"refusing to write observations under {os.path.relpath(bad, C.ROOT)} "
                    f"- that directory belongs to another record")
        if raw_dir == os.path.abspath(C.ROOT):
            raise RefusedDirectory("refusing to use the repository root as a raw directory")
        self.raw_dir = raw_dir

    # ---- paths ----
    def partition(self, league, date_str):
        return os.path.join(self.raw_dir, league, date_str)

    def path_for(self, league, date_str, kind):
        if kind not in KINDS:
            raise ValueError(f"unknown record kind {kind!r}")
        return os.path.join(self.partition(league, date_str), f"{kind}.ndjson.gz")

    def runs_path(self, league, date_str):
        return os.path.join(self.partition(league, date_str), "runs.txt")

    # ---- idempotency ----
    def runs_seen(self, league, date_str):
        p = self.runs_path(league, date_str)
        if not os.path.exists(p):
            return set()
        with io.open(p, encoding="utf-8") as f:
            return {l.strip() for l in f if l.strip()}

    def run_seen(self, league, date_str, run_id):
        return run_id in self.runs_seen(league, date_str)

    def record_run(self, league, date_str, run_id):
        os.makedirs(self.partition(league, date_str), exist_ok=True)
        with io.open(self.runs_path(league, date_str), "a", encoding="utf-8", newline="\n") as f:
            f.write(run_id + "\n")

    # ---- append ----
    def append(self, league, date_str, kind, records):
        """Append records as gzip members. Returns the number written."""
        if not records:
            return 0
        path = self.path_for(league, date_str, kind)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        buf = "".join(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n" for r in records)
        with open(path, "ab") as raw:
            with gzip.GzipFile(fileobj=raw, mode="ab", mtime=0) as gz:
                gz.write(buf.encode("utf-8"))
        return len(records)

    def write_payload(self, league, date_str, run_id, source, body):
        """Keep the provider's raw bytes, gzipped, so any derived field can be
        re-derived and PROVEN rather than reconstructed. Returns (ref, sha)."""
        d = os.path.join(self.partition(league, date_str), "payloads")
        os.makedirs(d, exist_ok=True)
        name = f"{run_id.replace(':', '_')}.{source}.json.gz"
        path = os.path.join(d, name)
        if os.path.exists(path):
            # Same run, same source: the retry guard should have stopped us
            # earlier. Never overwrite a captured payload.
            raise DuplicateRun(f"payload already exists for {run_id} {source}")
        if isinstance(body, str):
            body = body.encode("utf-8")
        with gzip.open(path, "wb") as gz:
            gz.write(body)
        return os.path.relpath(path, self.raw_dir).replace(os.sep, "/"), C.sha256_hex(body)

    # ---- read ----
    def read(self, league, date_str, kind):
        path = self.path_for(league, date_str, kind)
        if not os.path.exists(path):
            return
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def partitions(self):
        out = []
        if not os.path.isdir(self.raw_dir):
            return out
        for league in sorted(os.listdir(self.raw_dir)):
            ld = os.path.join(self.raw_dir, league)
            if not os.path.isdir(ld):
                continue
            for d in sorted(os.listdir(ld)):
                if os.path.isdir(os.path.join(ld, d)):
                    out.append((league, d))
        return out


# ------------------------------------------------------------ manifest --

def _count_rows(path):
    n = 0
    with gzip.open(path, "rb") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _file_sha(path):
    import hashlib
    hh = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            hh.update(chunk)
    return hh.hexdigest()


def load_manifest(path=C.MANIFEST):
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "_note": ("Mercer Live ML-1 raw observation manifest. The raw files under "
                  "data/mercer_live/raw/ are GITIGNORED (too large for a Pages repo) "
                  "and this records their sha256, size and row count once a partition "
                  "is sealed. APPEND-ONLY: an entry is never rewritten. If a sealed "
                  "file's hash later disagrees, the disagreement is appended under "
                  "`discrepancies` and the original entry stands. Nothing here is a "
                  "pick, a price or a model output."),
        "schema_version": C.SCHEMA_VERSION,
        "files": {},
        "discrepancies": [],
    }


def seal(store, manifest_path=C.MANIFEST, now=None, include_today=False):
    """Hash every closed partition into the manifest. Returns a summary.

    A partition is closed once its UTC date is in the past; today's is still
    being appended to, so it is skipped unless include_today is set (end of a
    session, or a test). Existing entries are never rewritten.
    """
    now = now or C.now_utc()
    today = C.utc_date(now)
    man = load_manifest(manifest_path)
    added, unchanged, disputed = [], [], []
    for league, date_str in store.partitions():
        if date_str >= today and not include_today:
            continue
        pdir = store.partition(league, date_str)
        for root, _dirs, files in os.walk(pdir):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, store.raw_dir).replace(os.sep, "/")
                sha = _file_sha(full)
                entry = {"sha256": sha, "bytes": os.path.getsize(full),
                         "league": league, "date": date_str,
                         "sealed_at": C.iso_s(now)}
                if fn.endswith(".ndjson.gz"):
                    entry["rows"] = _count_rows(full)
                prev = man["files"].get(rel)
                if prev is None:
                    man["files"][rel] = entry
                    added.append(rel)
                elif prev["sha256"] == sha:
                    unchanged.append(rel)
                else:
                    man["discrepancies"].append({"file": rel, "recorded_sha256": prev["sha256"],
                                                 "observed_sha256": sha, "noticed_at": C.iso_s(now)})
                    disputed.append(rel)
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with io.open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(man, f, indent=1, sort_keys=True)
    return {"added": added, "unchanged": unchanged, "disputed": disputed,
            "n_files": len(man["files"])}
