#!/usr/bin/env python3
"""
Open Ledger Sports - ML-1-OPS (mercer-live-ml1-ops-v1.0) self-test.

    python scripts/mercer_live_ops/selftest_mercer_live_ops.py

A SEPARATE SUITE FOR A SEPARATE PACKAGE. mercer-live-ml1-v1.0 is frozen; its
suite (scripts/mercer_live/selftest_mercer_live.py, 229 checks) is untouched and
group [0] here asserts it still is. This suite covers only the operational
package: durable evidence, the companion manifest, restore, publication, the
capture host and log hygiene. HERMETIC (requests.get/post fail the suite if
called), temp directories only, the real repository read only for contract
checks, and a NEGATIVE control beside every guard.

Groups:
  [0]  frozen ML-1 is unchanged: 229 checks, frozen digest keys, no ops reference
  [1]  same-hour multi-run evidence: the collision is real, ingest loses nothing
  [2]  repeat execution is a no-op; ingest refuses conflicts and torn sources
  [3]  the manifest: run records proven, one-way binding, wrong pairing refused
  [4]  grow-never-change succession (supersedes / verify_prefix / build)
  [5]  digest_commit: the one two-file repository write, and every refusal
  [6]  time: sealed shards and closed days across DST
  [7]  host spend boundary: odds off unless separately authorised
  [8]  host backup / publish / restore drill against a fake object store
  [9]  host windows and the supervisor loop
  [10] T3: evidence output carries counts/status only (canary)
  [11] workflow contracts: digest workflow, staging guard, smoke workflow, gate
  [12] public-proof boundary and repository safety
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ML1 = os.path.join(ROOT, "scripts", "mercer_live")
for _p in (ML1, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import archive                                                    # noqa: E402
import capture                                                    # noqa: E402  frozen ML-1
import common                                                     # noqa: E402  frozen ML-1
import digest_commit                                              # noqa: E402
import evidence                                                   # noqa: E402
import host                                                       # noqa: E402
from store import Store                                           # noqa: E402  frozen ML-1

fails, n_checks = [], 0


def check(cond, msg):
    global n_checks
    n_checks += 1
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def _no_net(*a, **k):
    raise AssertionError("network call attempted during the hermetic suite")


requests.get = _no_net
requests.post = _no_net

UTC = timezone.utc
CANARY = "CANARY-ML1-7f3a9c"
TEMPS = []


def tmpdir(prefix="mlops"):
    d = tempfile.mkdtemp(prefix=prefix)
    TEMPS.append(d)
    return d


def code_only(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


# ---- fixtures: a real ESPN-shaped payload through the FROZEN capture -------
def team(abbr, name, tid):
    return {"id": tid, "abbreviation": abbr, "displayName": name}


JAX, CLE = team("JAX", "Jacksonville Jaguars", "30"), team("CLE", "Cleveland Browns", "5")
LAR, WSH = team("LAR", "Los Angeles Rams", "14"), team("WSH", "Washington Commanders", "28")


def espn_payload(clock=420.0, canary=CANARY):
    sit = {"down": 2, "distance": 7, "yardLine": 45, "possession": "5", "isRedZone": False,
           "homeTimeouts": 3, "awayTimeouts": 2, "downDistanceText": f"2nd & 7 {canary}",
           "possessionText": f"CLE {canary}",
           "lastPlay": {"id": "9", "type": {"text": f"Rush {canary}"}, "text": f"run {canary}",
                        "scoreValue": 0, "probability": {"homeWinPercentage": 0.61}}}

    def ev(eid, date, home, away, state, hs, as_, situation, period, clk):
        st = {"type": {"name": {"in": "STATUS_IN_PROGRESS", "post": "STATUS_FINAL"}[state],
                       "state": state, "completed": state == "post", "shortDetail": f"x {canary}"},
              "period": period, "clock": clk, "displayClock": f"{int(clk // 60)}:{int(clk % 60):02d}"}
        comp = {"competitors": [{"homeAway": "home", "team": home, "score": hs},
                                {"homeAway": "away", "team": away, "score": as_}], "status": st}
        if situation:
            comp["situation"] = situation
        return {"id": eid, "date": date, "season": {"year": 2026, "type": 2, "slug": "regular-season"},
                "competitions": [comp]}

    return lambda sport, d: {"events": [
        ev("401", "2026-09-21T23:03Z", JAX, CLE, "in", "10", "7", sit, 2, clock),
        ev("403", "2026-09-21T20:00Z", LAR, WSH, "post", "24", "20", None, 4, 0.0),
    ]}


def capture_into(data_dir, now, run_id=None, clock=420.0):
    """One ESPN-only tick of the FROZEN capture into data_dir at `now`."""
    return capture.run_tick(["nfl"], store=Store(data_dir), now_fn=lambda: now, run_id=run_id,
                            odds_enabled=False, api_key=None, espn_fetch=espn_payload(clock),
                            book_fn=lambda *a, **k: (False, "no"))


def all_ids(data_dir):
    ids = []
    for dirpath, _d, files in os.walk(os.path.join(data_dir, "raw")):
        for fn in files:
            if fn.endswith(".jsonl"):
                with io.open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                    ids += [json.loads(l)["observation_id"] for l in f if l.strip()]
    return ids


def tree_bytes(d):
    out = {}
    for dirpath, _dd, files in os.walk(d):
        for fn in files:
            p = os.path.join(dirpath, fn)
            with io.open(p, "rb") as f:
                out[os.path.relpath(p, d).replace(os.sep, "/")] = f.read()
    return out


def flip(path):
    with io.open(path, "r+b") as f:
        b = f.read(1)
        f.seek(0)
        f.write(bytes([b[0] ^ 1]))


def copy_store(src):
    d = tmpdir()
    shutil.copytree(src, d, dirs_exist_ok=True)
    return d


# 2026-09-21 20:46 ET and 20:58 ET: the same ET hour, three runners' shape.
T_A = datetime(2026, 9, 22, 0, 46, 30, tzinfo=UTC)
T_B = datetime(2026, 9, 22, 0, 58, 10, tzinfo=UTC)
T_C = datetime(2026, 9, 22, 3, 20, 21, tzinfo=UTC)           # 23:20 ET, same ET date
DAY = "2026-09-21"
AFTER = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)              # the day is long closed
SHARD = f"raw/nfl/{DAY}/game_state_20.jsonl"

# ============================================================================
print("[0] frozen ML-1 is unchanged")
FROZEN_DIGEST_KEYS = {"_note", "schema_version", "et_date", "generated_at", "shards", "runs",
                      "runs_with_errors", "odds_calls", "odds_http_non_200", "credits_spent",
                      "credits_remaining_at_last_call", "records_written"}
check(archive.DIGEST_KEYS == FROZEN_DIGEST_KEYS, "ML-1-OPS validates against exactly the frozen ml1-v1 digest keys")
p0 = tmpdir()
capture_into(p0, T_A)
d0 = Store(p0).digest(DAY, write=False)
check(set(d0) == FROZEN_DIGEST_KEYS and "run_records" not in d0 and d0["schema_version"] == "ml1-v1",
      "Store.digest() emits the frozen ml1-v1 keys and nothing else (no run_records, no manifest link)")
check(common.SCHEMA_VERSION == "ml1-v1", "frozen schema_version is still ml1-v1")
for fn in sorted(os.listdir(ML1)):
    if fn.endswith(".py"):
        src = io.open(os.path.join(ML1, fn), encoding="utf-8").read()
        check("run_records" not in src and "mercer_live_ops" not in src and "manifest" not in src.lower()
              or fn == "selftest_mercer_live.py" and "mercer_live_ops" not in src,
              f"frozen scripts/mercer_live/{fn} never names the ops package, a manifest or run_records")
check(not any(fn in os.listdir(ML1) for fn in ("archive.py", "digest_commit.py", "evidence.py", "host.py")),
      "no ML-1-OPS module lives inside the frozen package directory")
r0 = subprocess.run([sys.executable, os.path.join(ML1, "selftest_mercer_live.py")], capture_output=True,
                    text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
check(r0.returncode == 0 and "mercer-live selftest: 229 checks, ALL PASSED" in r0.stdout,
      "the frozen ML-1 suite still runs exactly 229 checks, all passing")
freeze = io.open(os.path.join(ROOT, "handoffs", "hermes", "mercer-live", "MERCER_LIVE_FREEZE.md"),
                 encoding="utf-8").read()
check("ML-1-OPS" not in freeze and "manifest" not in freeze, "MERCER_LIVE_FREEZE.md does not mention ML-1-OPS")
arch = io.open(os.path.join(ROOT, "docs", "MERCER_LIVE_ML1_ARCHITECTURE.md"), encoding="utf-8").read()
check("run_records" not in arch and "## 20." not in arch, "the ML-1 architecture document carries no ops amendment")

# ============================================================================
print("\n[1] same-hour multi-run evidence: the collision is real, ingest loses nothing")
A, B, C = tmpdir(), tmpdir(), tmpdir()
capture_into(A, T_A)
capture_into(A, T_A + timedelta(minutes=1), clock=380.0)
capture_into(B, T_B, clock=300.0)
capture_into(C, T_C, clock=11.0)
check(os.path.exists(os.path.join(A, SHARD)) and os.path.exists(os.path.join(B, SHARD)),
      "two separate runners write the SAME relative shard path for the same ET hour")
check(tree_bytes(A)[SHARD] != tree_bytes(B)[SHARD], "... with DIFFERENT bytes")
naive = tmpdir()
for src in (A, B):
    shutil.copytree(src, naive, dirs_exist_ok=True)
expected = set(all_ids(A)) | set(all_ids(B)) | set(all_ids(C))
check(len(set(all_ids(naive))) < len(set(all_ids(A)) | set(all_ids(B))),
      f"NEGATIVE CONTROL: copying stores over each other silently loses observations "
      f"({len(set(all_ids(naive)))} of {len(set(all_ids(A)) | set(all_ids(B)))})")
D = tmpdir()
dst = Store(D)
reports = [archive.ingest(s, dst) for s in (A, B, C)]
check(sum(r["records_written"] for r in reports) == len(expected) and sorted(all_ids(D)) == sorted(expected),
      f"ingest keeps EVERY observation from every runner ({len(all_ids(D))} == {len(expected)})")
check(len(all_ids(D)) == len(set(all_ids(D))), "and no observation twice")
check(sum(r["run_records_copied"] for r in reports) == 4 and len(Store(D).runs_for_date(DAY)) == 4,
      "every run record survives (4 runs from 3 runners)")
DB, MAN, ACT = archive.build(D, DAY, now=AFTER)
check(ACT == "new" and MAN["counts"]["run_records"] == 4 and MAN["counts"]["shard_records"] == len(expected),
      "the pair built from the durable store covers every run and every observation of the day")
check(archive.restore_verify(D, DB, MAN) == [], "and restore-verifies against the durable store")

# ============================================================================
print("\n[2] repeat execution is a no-op; ingest refuses conflicts and torn sources")
before = tree_bytes(D)
again = [archive.ingest(s, dst) for s in (A, B, C)]
check(all(r["records_written"] == 0 and r["run_records_copied"] == 0 for r in again)
      and sum(r["duplicates_skipped"] for r in again) == len(expected),
      "re-ingesting the same stores writes nothing and counts every duplicate")
check(tree_bytes(D) == before, "... and changes no byte of the durable store")
D2 = tmpdir()
for s in (C, B, A):
    archive.ingest(s, Store(D2))
check(sorted(all_ids(D2)) == sorted(all_ids(D)), "ingest order does not change which observations survive")
X = copy_store(A)
rp = [p for p in tree_bytes(X) if p.startswith("raw/runs/")][0]
with io.open(os.path.join(X, rp), "ab") as f:
    f.write(b" ")
snap = tree_bytes(D)
try:
    archive.ingest(X, dst)
    check(False, "a run-record conflict must be refused")
except archive.ArchiveError as e:
    check("conflict" in str(e), "same run_id with different bytes is REFUSED")
check(tree_bytes(D) == snap, "... and nothing at all was written")
T = tmpdir()
capture_into(T, T_A + timedelta(minutes=5))
with io.open(os.path.join(T, SHARD), "ab") as f:
    f.write(b'{"observation_id": "torn')
try:
    archive.ingest(T, dst)
    check(False, "an unparseable source line must be refused")
except archive.ArchiveError as e:
    check("unparseable" in str(e), "a torn line in the source is refused, never silently dropped")
check(tree_bytes(D) == snap, "... and nothing from that source was written")
try:
    archive.ingest(D, dst)
    check(False, "ingesting a store into itself must be refused")
except archive.ArchiveError:
    check(True, "ingesting a store into itself is refused")

# ============================================================================
print("\n[3] the manifest: run records proven, one-way binding, wrong pairing refused")
DIG = json.loads(DB.decode("utf-8"))
check(set(MAN) == archive.MANIFEST_KEYS and MAN["schema_version"] == "ml1-ops-manifest-v1"
      and MAN["package"] == "mercer-live-ml1-ops-v1.0" and MAN["et_date"] == DAY and MAN["generated_at"],
      "manifest carries schema ml1-ops-manifest-v1, package mercer-live-ml1-ops-v1.0, date, generated_at")
check(MAN["ml1_digest"] == {"path": f"data/mercer_live/digest/{DAY}.json",
                            "sha256": archive.sha256_bytes(DB), "bytes": len(DB), "schema_version": "ml1-v1"},
      "manifest -> SHA-256 and byte count of the exact frozen digest bytes")
check("manifest" not in DB.decode("utf-8").lower() and set(DIG) == FROZEN_DIGEST_KEYS,
      "ONE-WAY: the frozen digest names no manifest (no circular hash)")
check(all(set(r) == {"path", "sha256", "bytes"} for r in MAN["run_records"]) and len(MAN["run_records"]) == 4,
      "manifest fingerprints every run record (path, sha256, bytes)")
check(all({"sha256", "bytes", "lines", "parsed"} <= set(s) for s in MAN["shards"])
      and {s["path"] for s in MAN["shards"]} == {s["path"] for s in DIG["shards"]},
      "manifest fingerprints every shard with bytes/lines/records, the same set the digest covers")
check(archive.check_pair(DB, MAN) == [], "the pair validates")
# THE CORE NEGATIVE TEST: a modified run record.
M = copy_store(D)
flip(os.path.join(M, MAN["run_records"][0]["path"]))
digest_only = [s for s in DIG["shards"]
               if archive.sha256_file(os.path.join(M, *s["path"].split("/")))[0] != s["sha256"]]
check(digest_only == [], "NEGATIVE CONTROL: the frozen ML-1 digest ALONE cannot see a modified run record")
check(any("run record" in f for f in archive.restore_verify(M, DB, MAN)),
      "the ML-1-OPS manifest DOES detect the modified run record - restore FAILS")
for label, mutate in (
        ("a flipped byte in a shard", lambda root: flip(os.path.join(root, SHARD))),
        ("a deleted shard", lambda root: os.remove(os.path.join(root, SHARD))),
        ("a deleted run record", lambda root: os.remove(os.path.join(root, MAN["run_records"][1]["path"]))),
        ("an unlisted extra shard", lambda root: shutil.copy(os.path.join(root, SHARD),
                                                             os.path.join(root, SHARD.replace("_20", "_05")))),
        ("an unlisted extra run record", lambda root: shutil.copy(
            os.path.join(root, MAN["run_records"][0]["path"]),
            os.path.join(root, "raw", "runs", DAY, "extra.json")))):
    V = copy_store(D)
    mutate(V)
    check(archive.restore_verify(V, DB, MAN) != [], f"restore FAILS on {label}")
# Wrong pairings.
other = copy_store(D)
capture_into(other, datetime(2026, 9, 21, 16, 5, tzinfo=UTC))
DB_other, MAN_other, _ = archive.build(other, DAY, now=AFTER)
check(archive.check_pair(DB_other, MAN) != [] and archive.restore_verify(D, DB_other, MAN) != [],
      "a manifest paired with a DIFFERENT digest is rejected")
check(archive.check_pair(DB, MAN_other) != [], "... in both directions")
restamped = archive.serialise(dict(DIG, generated_at="2026-09-30T00:00:00Z"))
check(archive.check_pair(restamped, MAN) != [],
      "even a digest that differs only by generated_at is a different digest to the manifest")
check(archive.validate_manifest(dict(MAN, ml1_digest=dict(MAN["ml1_digest"], path="data/ledger.json"))) != [],
      "a manifest pointing its digest reference anywhere else is refused")
check(archive.validate_digest(dict(DIG, run_records=[])) != [],
      "a digest carrying a non-frozen key (run_records) is refused as not ml1-v1")
check(archive.validate_manifest(dict(MAN, counts=dict(MAN["counts"], run_records=99))) != [],
      "manifest counts that disagree with its entries are refused")
check(archive.validate_digest(dict(DIG, shards=[dict(DIG["shards"][0],
                                                      path="raw/nfl/2026-09-21/../../index.html")])) != [],
      "a traversal path is refused")

# ============================================================================
print("\n[4] grow-never-change succession")
DBr, MANr, act = archive.build(D, DAY, previous=(DB, MAN), now=AFTER + timedelta(hours=5))
check(act == "unchanged" and DBr == DB and MANr is MAN,
      "rebuilding unchanged evidence later: UNCHANGED, the published bytes reused (idempotent)")
E = copy_store(D)
capture_into(E, datetime(2026, 9, 21, 16, 5, tzinfo=UTC))       # a NEW hour's shard + run record
DBg, MANg, act = archive.build(E, DAY, previous=(DB, MAN), now=AFTER)
check(act == "grown" and MANg["counts"]["shards"] > MAN["counts"]["shards"]
      and MANg["counts"]["run_records"] == 5, "late new evidence: GROWN")
check(archive.check_pair(DBg, MANg) == [], "the grown pair is itself correctly bound")
ok, _ = archive.supersedes_pair(DIG, MAN, json.loads(DBg.decode("utf-8")), MANg)
check(ok, "the grown pair supersedes the committed pair")
changed = json.loads(archive.serialise(MAN).decode("utf-8"))
changed["run_records"][0]["sha256"] = "0" * 64
check(not archive.supersedes(MAN, changed)[0], "a committed run-record hash change is refused")
shrunk = json.loads(archive.serialise(MAN).decode("utf-8"))
shrunk["shards"][0]["bytes"] -= 1
check(not archive.supersedes(MAN, shrunk)[0], "a shrunk committed shard is refused")
check(not archive.supersedes(MAN, dict(MAN, run_records=MAN["run_records"][1:]))[0],
      "a deleted committed run record is refused")
check(not archive.supersedes(DIG, dict(DIG, shards=DIG["shards"][1:]), entry_keys=("shards",))[0],
      "a deleted committed digest shard is refused")
check(not archive.supersedes(DIG, dict(DIG, runs=DIG["runs"] - 1), entry_keys=("shards",))[0],
      "a digest count that goes DOWN is refused")
try:
    archive.build(D, DAY, now=datetime(2026, 9, 22, 3, 59, tzinfo=UTC))
    check(False, "an open day must be refused")
except archive.ArchiveError as e:
    check("not closed" in str(e), "build refuses a day that has not closed (23:59 ET)")
F = copy_store(D)
capture_into(F, T_A + timedelta(minutes=9))                      # appends to the committed 20 shard
check(archive.verify_prefix(F, MAN) == [], "verify_prefix accepts a shard that was only APPENDED to")
try:
    archive.build(F, DAY, previous=(DB, MAN), now=AFTER)
    check(False, "an appended committed shard must still need a human")
except archive.ArchiveError as e:
    check("REFUSED" in str(e), "... but build still REFUSES to replace a committed shard's hash")
G = copy_store(D)
with io.open(os.path.join(G, SHARD), "r+b") as f:
    f.write(b"X")
pf = archive.verify_prefix(G, MAN)
check(pf and "REWRITTEN" in pf[0], "verify_prefix names a REWRITTEN shard as such")
try:
    archive.build(G, DAY, previous=(DB, MAN), now=AFTER)
    check(False, "a rewritten committed shard must refuse")
except archive.ArchiveError:
    check(True, "build REFUSES when committed evidence was rewritten on disk")
Rr = copy_store(D)
flip(os.path.join(Rr, MAN["run_records"][0]["path"]))
try:
    archive.build(Rr, DAY, previous=(DB, MAN), now=AFTER)
    check(False, "a changed committed run record must refuse")
except archive.ArchiveError:
    check(True, "build REFUSES when a committed run record changed on disk")

# ============================================================================
print("\n[5] digest_commit: the one two-file repository write, and every refusal")
CM = archive.serialise(MAN)
act, bodies, _ = digest_commit.decide(DB, CM, DAY, None, now=AFTER)
check(act == "CREATED" and bodies == (DB, CM), "first pair for a date: CREATED, both files, exact bytes")
act, bodies, _ = digest_commit.decide(DB, CM, DAY, (DB, CM), now=AFTER)
check(act == "UNCHANGED" and bodies is None, "the identical pair again: UNCHANGED, nothing written")
act, bodies, _ = digest_commit.decide(DBg, archive.serialise(MANg), DAY, (DB, CM), now=AFTER)
check(act == "GROWN" and bodies is not None, "a pair that only adds evidence: GROWN")
bad = json.loads(CM.decode("utf-8"))
bad["run_records"][0]["sha256"] = "1" * 64
act, bodies, _ = digest_commit.decide(DB, archive.serialise(bad), DAY, (DB, CM), now=AFTER)
check(act == "REFUSED-DIFFERS" and bodies is None, "a changed committed run-record hash: REFUSED-DIFFERS")
for label, cd, cm, date, now, committed in (
        ("not JSON", b"{nope", CM, DAY, AFTER, None),
        ("non-canonical digest bytes", json.dumps(DIG).encode(), CM, DAY, AFTER, None),
        ("a manifest bound to another digest", DB_other, CM, DAY, AFTER, None),
        ("a different date than requested", DB, CM, "2026-09-20", AFTER, None),
        ("a day that has not closed", DB, CM, DAY, datetime(2026, 9, 22, 3, 0, tzinfo=UTC), None),
        ("a digest with a non-frozen key", archive.serialise(dict(DIG, run_records=[])), CM, DAY, AFTER, None),
        ("a committed digest without its manifest", DB, CM, DAY, AFTER, (DB, None)),
        ("a committed pair that is itself mismatched", DB, CM, DAY, AFTER, (DB_other, CM))):
    act, bodies, _ = digest_commit.decide(cd, cm, date, committed, now=now)
    check(act.startswith("REFUSED") and bodies is None, f"REFUSED: {label}")
# main(): exactly two paths written, nothing else touched, MISSING is red.
R = tmpdir()
protected = ("index.html", "feed.xml", "data/ledger.json", "data/odds_credits.json", "data/commitments.json")
for rel in protected:
    os.makedirs(os.path.dirname(os.path.join(R, rel)) or R, exist_ok=True)
    with io.open(os.path.join(R, rel), "w") as f:
        f.write("untouched\n")
cd_f, cm_f = os.path.join(R, "cand_d.json"), os.path.join(R, "cand_m.json")
with io.open(cd_f, "wb") as f:
    f.write(DB)
with io.open(cm_f, "wb") as f:
    f.write(CM)
snap = tree_bytes(R)
real_closed = archive.closed
archive.closed = lambda d, now=None, grace_min=10: real_closed(d, now=AFTER, grace_min=grace_min)
try:
    rc = digest_commit.main(["--date", DAY, "--candidate-digest", cd_f, "--candidate-manifest", cm_f, "--root", R])
    after = tree_bytes(R)
    new = sorted(set(after) - set(snap))
    check(rc == 0 and new == [f"data/mercer_live/digest/{DAY}.json", f"data/mercer_live/manifest/{DAY}.json"],
          f"main() writes exactly the digest and manifest paths ({new})")
    check(all(after[k] == v for k, v in snap.items()),
          "and changes no other byte (index.html, feed.xml, ledger, credits, commitments untouched)")
    check(after[f"data/mercer_live/digest/{DAY}.json"] == DB, "the committed digest is the host's bytes, unmodified")
    rc2 = digest_commit.main(["--date", DAY, "--candidate-digest", cd_f, "--candidate-manifest", cm_f, "--root", R])
    check(rc2 == 0 and tree_bytes(R) == after, "running it again is idempotent (UNCHANGED, no byte moves)")
    rc = digest_commit.main(["--date", DAY, "--candidate-digest", cd_f,
                             "--candidate-manifest", os.path.join(R, "absent.json"), "--root", R])
    check(rc == 1, "a missing manifest candidate is MISSING and exits 1 (the red run is the alert)")
    rc = digest_commit.main(["--date", "../../x", "--candidate-digest", cd_f, "--candidate-manifest", cm_f,
                             "--root", R])
    check(rc == 1, "a malformed --date is refused before any path is formed")
finally:
    archive.closed = real_closed

# ============================================================================
print("\n[6] time: sealed shards and closed days across DST")
S = tmpdir()
capture_into(S, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))        # 01:30 EDT, Nov 1
capture_into(S, datetime(2026, 11, 1, 6, 30, tzinfo=UTC))        # 01:30 EST, same wall hour
sh01 = "raw/nfl/2026-11-01/game_state_01.jsonl"
check(sh01 not in archive.sealed(S, now=datetime(2026, 11, 1, 6, 45, tzinfo=UTC)),
      "DST end: the 01 shard is NOT sealed after one real hour - the second 01:xx hour is still writing")
check(sh01 in archive.sealed(S, now=datetime(2026, 11, 1, 7, 10, tzinfo=UTC)),
      "... it seals after 02:00 EST + grace")
O = tmpdir()
capture_into(O, datetime(2026, 9, 21, 16, 2, tzinfo=UTC))        # 12:02 ET
open_now = datetime(2026, 9, 21, 16, 30, tzinfo=UTC)
check(not any(p.endswith("_12.jsonl") for p in archive.sealed(O, now=open_now))
      and any(p.startswith("raw/runs/") for p in archive.sealed(O, now=open_now)),
      "the open hour's shard is withheld while run records (atomic) go at once")
check(not archive.closed("2026-09-21", now=datetime(2026, 9, 22, 4, 5, tzinfo=UTC)),
      "a day is not closed at 00:05 ET the next day (grace)")
check(archive.closed("2026-09-21", now=datetime(2026, 9, 22, 4, 11, tzinfo=UTC)), "... and is at 00:11 ET")
check(not archive.closed("2026-11-01", now=datetime(2026, 11, 2, 4, 30, tzinfo=UTC)),
      "the 25-hour DST-end day is still open at 23:30 EST")
check(archive.closed("2026-11-01", now=datetime(2026, 11, 2, 5, 11, tzinfo=UTC)), "... and closed at 00:11 EST")
DST_DB, DST_MAN, _ = archive.build(S, "2026-11-01", now=datetime(2026, 11, 3, tzinfo=UTC))
check(archive.restore_verify(S, DST_DB, DST_MAN) == [] and DST_MAN["counts"]["run_records"] == 2,
      "a pair builds and restore-verifies for the 25-hour day")

# ============================================================================
print("\n[7] host spend boundary: odds off unless separately authorised")
H = tmpdir()
auth = os.path.join(H, "auth.json")
NOW7 = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
check(host.odds_authorisation(auth, now=NOW7)[0] is False, "no authorisation file: odds OFF")
for label, content in (("malformed JSON", "{"),
                       ("missing fields", json.dumps({"authorized_by": "Daniel"})),
                       ("expired yesterday (ET)", json.dumps({"authorized_by": "Daniel", "authorized_on": "2026-09-30",
                                                             "expires_on": "2026-10-03", "reason": "test"})),
                       ("an unparseable expiry", json.dumps({"authorized_by": "Daniel", "authorized_on": "x",
                                                            "expires_on": "soon", "reason": "test"}))):
    with io.open(auth, "w") as f:
        f.write(content)
    check(host.odds_authorisation(auth, now=NOW7)[0] is False, f"{label}: odds OFF")
with io.open(auth, "w") as f:
    json.dump({"authorized_by": "Daniel", "authorized_on": "2026-10-01", "expires_on": "2026-10-04",
               "reason": "two slate weeks"}, f)
check(host.odds_authorisation(auth, now=NOW7)[0] is True, "a valid file expiring TODAY (ET): odds on")
cmd_off, cmd_on = host.capture_command("/data/x", False), host.capture_command("/data/x", True)
check("--no-odds" in cmd_off and "--no-odds" not in cmd_on, "the capture gets --no-odds unless authorised")
check(cmd_off[1].replace(os.sep, "/").endswith("scripts/mercer_live/capture.py"),
      "the host runs the FROZEN capture from scripts/mercer_live/, unmodified")
env_off = host.capture_env(False, base={"ODDS_API_KEY": "secret-key", "PATH": "/bin"})
env_on = host.capture_env(True, base={"ODDS_API_KEY": "secret-key", "PATH": "/bin"})
check("ODDS_API_KEY" not in env_off and env_on.get("ODDS_API_KEY") == "secret-key",
      "and the key is REMOVED from an unauthorised tick's environment")
host_code = code_only(io.open(os.path.join(HERE, "host.py"), encoding="utf-8").read())
check(not re.search(r"open\([^)]*AUTH_FILE[^)]*['\"]w", host_code), "no code writes the authorisation file")

# ============================================================================
print("\n[8] host backup / publish / restore drill against a fake object store")
BUCKET = tmpdir("mlbucket")
DIGBUCKET = tmpdir("mldig")
CALLS = []


def fake_rclone(argv, **kw):
    """Emulates `rclone copy --immutable --files-from` and `rclone copyto`."""
    CALLS.append(list(argv))
    if argv[:2] == ["rclone", "copy"]:
        src = argv[2]
        files = io.open(argv[argv.index("--files-from") + 1], encoding="utf-8").read().split()
        for rel in files:
            s = os.path.join(src, rel)
            d = os.path.join(BUCKET, rel)
            with io.open(s, "rb") as f:
                body = f.read()
            if os.path.exists(d):
                with io.open(d, "rb") as f:
                    if f.read() != body:
                        return SimpleNamespace(returncode=9)     # --immutable: refuse to overwrite
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            with io.open(d, "wb") as f:
                f.write(body)
        return SimpleNamespace(returncode=0)
    if argv[:2] == ["rclone", "copyto"]:
        d = os.path.join(DIGBUCKET, argv[3].split(":", 1)[1].split("/", 1)[1])
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy(argv[2], d)
        return SimpleNamespace(returncode=0)
    return SimpleNamespace(returncode=127)


bc = host.backup_command("/data/mercer_live", "bucket:raw", "/tmp/list")
check(bc[:2] == ["rclone", "copy"] and "--immutable" in bc and "--checksum" in bc,
      "backup is `rclone copy --immutable --checksum`")
check(not any(w in bc for w in ("sync", "move", "delete", "purge", "--delete-after", "--ignore-existing")),
      "backup never syncs, moves, deletes or skips a changed file")
try:
    host.backup_command("/data/x", "", "/tmp/l")
    check(False, "an unset remote must refuse")
except archive.ArchiveError:
    check(True, "an unconfigured remote refuses rather than copying nowhere")
rc, msg = host.backup(D, "bucket:raw", runner=fake_rclone, now=AFTER)
check(rc == 0 and sorted(archive.sealed(D, now=AFTER)) == sorted(tree_bytes(BUCKET)),
      f"backup copies every sealed file off-host ({msg})")
rc, _ = host.backup(D, "bucket:raw", runner=fake_rclone, now=AFTER)
check(rc == 0, "a second backup pass is a clean no-op")
Mb = copy_store(D)
flip(os.path.join(Mb, SHARD))
remote_before = tree_bytes(BUCKET)
rc, _ = host.backup(Mb, "bucket:raw", runner=fake_rclone, now=AFTER)
check(rc != 0 and tree_bytes(BUCKET) == remote_before,
      "a locally rewritten shard makes the backup FAIL and the off-host copy is untouched")
PUB = tmpdir()
rc, msg = host.publish(DAY, data_dir=D, published_dir=PUB, remote="bucket:dig", runner=fake_rclone, now=AFTER)
pub_d = os.path.join(DIGBUCKET, "digests", f"{DAY}.json")
pub_m = os.path.join(DIGBUCKET, "manifests", f"{DAY}.json")
check(rc == 0 and os.path.exists(pub_d) and os.path.exists(pub_m),
      f"publish uploads the closed day's digest AND manifest ({msg})")
n = len(CALLS)
rc, msg = host.publish(DAY, data_dir=D, published_dir=PUB, remote="bucket:dig", runner=fake_rclone,
                       now=AFTER + timedelta(days=1))
check(rc == 0 and "unchanged" in msg and len(CALLS) == n, "publish again a day later: unchanged, no upload")
rc, msg = host.publish(DAY, data_dir=G, published_dir=PUB, remote="bucket:dig", runner=fake_rclone, now=AFTER)
check(rc == 1 and "REFUSED" in msg, "publish REFUSES when published evidence was rewritten")
rc, msg = host.publish(DAY, data_dir=Rr, published_dir=PUB, remote="bucket:dig", runner=fake_rclone, now=AFTER)
check(rc == 1 and "REFUSED" in msg, "publish REFUSES when a published run record changed")
rc, msg = host.publish(DAY, data_dir=D, published_dir=tmpdir(), remote="bucket:dig",
                       runner=lambda a, **k: SimpleNamespace(returncode=5), now=AFTER)
check(rc == 5, "a failed upload is reported and NOT recorded as published (it is retried)")
lonely = tmpdir()
os.makedirs(os.path.join(lonely, "digest"))
shutil.copy(pub_d, os.path.join(lonely, "digest", f"{DAY}.json"))
rc, msg = host.publish(DAY, data_dir=D, published_dir=lonely, remote="bucket:dig", runner=fake_rclone, now=AFTER)
check(rc == 1 and "incomplete" in msg, "a local published record with a digest but no manifest refuses")
with io.open(pub_d, "rb") as f:
    pd_bytes = f.read()
with io.open(pub_m, "rb") as f:
    pm_bytes = f.read()
act, bodies, _ = digest_commit.decide(pd_bytes, pm_bytes, DAY, None, now=AFTER)
check(act == "CREATED", "what the host publishes is exactly what digest_commit accepts")
# RESTORE DRILL: the host is gone; rebuild from the bucket alone.
RESTORED = copy_store(BUCKET)
check(archive.restore_verify(RESTORED, pd_bytes, json.loads(pm_bytes.decode("utf-8"))) == [],
      "RESTORE DRILL: bytes restored from the bucket alone verify against the committed pair")
flip(os.path.join(RESTORED, json.loads(pm_bytes.decode("utf-8"))["run_records"][0]["path"]))
check(archive.restore_verify(RESTORED, pd_bytes, json.loads(pm_bytes.decode("utf-8"))) != [],
      "RESTORE DRILL negative control: one flipped byte in a run record fails")
cli = subprocess.run([sys.executable, os.path.join(HERE, "archive.py"), "restore-verify", "--data-dir", BUCKET,
                      "--digest", pub_d, "--manifest", pub_m], capture_output=True, text=True)
check(cli.returncode == 0 and "OK" in cli.stdout and CANARY not in cli.stdout,
      "the CLI restore-verify prints OK and no observed value")

# ============================================================================
print("\n[9] host windows and the supervisor loop")
W = host.load_windows()
et = common.ET
for when, want, label in ((datetime(2026, 10, 4, 13, 0, tzinfo=et), True, "Sunday 13:00 ET"),
                          (datetime(2026, 10, 6, 10, 0, tzinfo=et), False, "Tuesday 10:00 ET"),
                          (datetime(2026, 10, 4, 0, 30, tzinfo=et), True, "Sunday 00:30 ET (Saturday spill-over)"),
                          (datetime(2026, 10, 5, 0, 30, tzinfo=et), False, "Monday 00:30 ET (Sunday has ended)"),
                          (datetime(2026, 10, 6, 0, 15, tzinfo=et), True, "Tuesday 00:15 ET (Monday spill-over)")):
    check(host.in_window(W, when.astimezone(UTC)) is want, f"in_window {label}: {want}")
check(all(0 <= w["weekday"] <= 6 and 0 < w["minutes"] <= 24 * 60 for w in W), "every window is well-formed")
RUNS = []


def fake_runner(argv, **kw):
    RUNS.append((list(argv), dict(kw.get("env") or {})))
    return SimpleNamespace(returncode=0)


host_saved = (host.DATA_DIR, host.REMOTE_RAW, host.AUTH_FILE)
host.DATA_DIR, host.REMOTE_RAW, host.AUTH_FILE = tmpdir(), "bucket:raw", os.path.join(tmpdir(), "none.json")
os.environ["ODDS_API_KEY"] = "must-not-leak"
try:
    host.serve(max_loops=1, sleep=lambda s: None, runner=fake_runner,
               clock=lambda: datetime(2026, 10, 6, 14, 0, tzinfo=UTC))      # Tue 10:00 ET
    check(not any("capture.py" in " ".join(a) for a, _ in RUNS), "outside every window: no capture tick at all")
    RUNS.clear()
    host.serve(max_loops=1, sleep=lambda s: None, runner=fake_runner,
               clock=lambda: datetime(2026, 10, 4, 17, 0, tzinfo=UTC))      # Sun 13:00 ET
    ticks = [(a, e) for a, e in RUNS if any("capture.py" in x for x in a)]
    check(len(ticks) == 1 and "--no-odds" in ticks[0][0] and "ODDS_API_KEY" not in ticks[0][1],
          "inside a window with no authorisation: ONE tick, --no-odds, and no key in its environment")
finally:
    host.DATA_DIR, host.REMOTE_RAW, host.AUTH_FILE = host_saved
    os.environ.pop("ODDS_API_KEY", None)

# ============================================================================
print("\n[10] T3: evidence output carries counts/status only (canary)")
EV = tmpdir()
for i in range(3):
    capture_into(EV, T_A + timedelta(minutes=i), clock=420.0 - 60 * i)
capture_into(EV, T_A, run_id=Store(EV).runs_for_date(DAY)[0]["run_id"])     # a retry
Store(EV).digest(DAY)
check(CANARY.encode() in b"".join(tree_bytes(EV).values()),
      "the fixture's raw records DO carry the canary (the control is live)")
out = "\n".join(evidence.summarise(EV))
check(CANARY not in out, "evidence.summarise never prints the canary")
p = subprocess.run([sys.executable, os.path.join(HERE, "evidence.py"), EV], capture_output=True, text=True)
check(p.returncode == 0 and CANARY not in p.stdout + p.stderr, "... nor does the CLI the workflow runs")
for value in ("420", "380", "2nd & 7", "Rush", "CLE 45", "0.61", "Jacksonville"):
    check(value not in out, f"no observed value {value!r} in the evidence")
check("records game_state=6" in out, "evidence counts records (2 events x 3 ticks)")
check("FIELD down_distance_text: present 3/6 all, 3/3 in-progress" in out, "evidence proves field PRESENCE by count")
check("strictly_increasing_observed_at=yes" in out and "clock_seconds=2" in out,
      "evidence proves PROGRESSION by change counts (the clock moved twice)")
check(re.search(r"runs records=3 .*distinct run_id=3", out) is not None,
      "evidence shows run identity and that the retry added no run")
check("DIGEST 2026-09-21 schema=ml1-v1" in out and "lines_equal_parsed=yes" in out, "evidence shows the digest shape")
buf = io.StringIO()
saved = sys.stdout
sys.stdout = buf
try:
    print(capture._summary_line(capture_into(tmpdir(), T_A)))
finally:
    sys.stdout = saved
check(CANARY not in buf.getvalue(), "the frozen capture's per-tick summary line carries no observed value")

# ============================================================================
print("\n[11] workflow contracts: digest workflow, staging guard, smoke workflow, gate")
WF = os.path.join(ROOT, ".github", "workflows")
dg_raw = io.open(os.path.join(WF, "mercer-live-digest.yml"), encoding="utf-8").read()
dg = yaml.safe_load(dg_raw)
dg_code = code_only(dg_raw)
trig = dg.get(True) or dg.get("on")
check(set(trig) == {"workflow_dispatch"}, "digest workflow: dispatch-only (no GitHub cron, no push trigger)")
check(dg.get("permissions") == {"contents": "write"}, "digest workflow: permissions are exactly contents: write")
steps = dg["jobs"]["commit"]["steps"]
commit = code_only(next(s for s in steps if s.get("name") == "Commit the digest and manifest")["run"])
adds = re.findall(r"^\s*git add\s+(.+)$", commit, re.M)
check(adds == ['"data/mercer_live/digest/$D.json"', '"data/mercer_live/manifest/$D.json"'],
      f"digest workflow stages exactly TWO literal paths ({adds})")
check('D="${{ steps.d.outputs.date }}"' in commit and commit.count("git commit") == 1,
      "... in exactly one commit")
for banned in ("git add -A", "git add .", "index.html", "feed.xml", "ledger.json", "_ledger", "--force", "--autostash",
               "git pull", "ODDS_API_KEY", "DISCORD", "post_discord", "raw/"):
    check(banned not in dg_code, f"digest workflow never contains {banned!r}")
check(set(re.findall(r"secrets\.([A-Z0-9_]+)", dg_raw)) == {"ML1_DIGEST_R2_ACCESS_KEY_ID",
                                                           "ML1_DIGEST_R2_SECRET_ACCESS_KEY"},
      "digest workflow uses only the read-only digest-bucket credential")
check("scripts/mercer_live_ops/digest_commit.py" in dg_code and "$k/$D.json" in dg_code
      and "for k in digests manifests" in dg_code,
      "it fetches ONLY digests/<date>.json and manifests/<date>.json and places them via digest_commit.py")
# The staging guard, run against real git: it must refuse anything but the pair.
guard = commit[commit.index("git add"):commit.index("git diff --cached --quiet")]


def run_guard(extra_staged, make_pair=True):
    g = tmpdir("mlgit")
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    subprocess.run(["git", "init", "-q", g], check=True, env=env)
    rels = ([f"data/mercer_live/digest/{DAY}.json", f"data/mercer_live/manifest/{DAY}.json"] if make_pair else [])
    for rel in rels + list(extra_staged):
        os.makedirs(os.path.dirname(os.path.join(g, rel)) or g, exist_ok=True)
        with io.open(os.path.join(g, rel), "w") as f:
            f.write("x\n")
    for rel in extra_staged:
        subprocess.run(["git", "add", rel], cwd=g, env=env, check=True)
    return subprocess.run(["bash", "-c", "set -e\n" + guard], cwd=g, env=dict(env, D=DAY),
                          capture_output=True, text=True)


check(run_guard([]).returncode == 0, "staging guard: the digest+manifest pair passes")
for intruder in ("index.html", "feed.xml", "data/ledger.json", "data/odds_credits.json",
                 "data/commitments.json", "data/football/football_ledger.json", "blog/index.html"):
    r = run_guard([intruder])
    check(r.returncode == 1 and "refusing" in r.stdout, f"NEGATIVE CONTROL: with {intruder} also staged, the guard refuses")
check(run_guard([], make_pair=False).returncode != 0, "a missing pair file makes `git add` fail loudly (red, not green-empty)")
# smoke workflow (T3)
sm_raw = io.open(os.path.join(WF, "mercer-live-smoke.yml"), encoding="utf-8").read()
sm = yaml.safe_load(sm_raw)
sm_steps = sm["jobs"]["smoke"]["steps"]
body = code_only(sm_steps[3]["run"])
check(not re.search(r"\bcat\b", body), "smoke: no `cat` of any store file into the log")
for banned in ('print("OBS', 'print("LASTPLAY', 'print("IDENTITY', 'print("MISS', 'json.dumps(r.get("last_play'):
    check(banned not in body, f"smoke: no per-observation value dump ({banned})")
check("scripts/mercer_live_ops/evidence.py" in body, "smoke: prints the compact ML-1-OPS evidence instead")
check('find "$STORE" -type f | sed' not in body, "smoke: the step summary lists no raw file paths")
check("RUNNER_TEMP" in body and "--no-odds" in body and '--run-id "$FIRST"' in body
      and "retry changed no shard byte" in body,
      "smoke: still temp-dir only, still --no-odds by default, still proves the retry")
up = next(s for s in sm_steps if "upload-artifact" in str(s.get("uses", "")))
check(str(up["with"]["path"]).endswith(".tar.age"), "smoke: the ONLY uploaded artifact is the age-encrypted tarball")
enc = next(s for s in sm_steps if s.get("name") == "Encrypt the raw store for the artifact")
check("age -r" in code_only(enc["run"]) and "exit 0" in code_only(enc["run"])
      and "vars.ML1_ARTIFACT_AGE_RECIPIENT" in str(enc.get("env")),
      "smoke: encryption is to a public-key VARIABLE; unset means nothing raw is uploaded")
check(sm_steps.index(enc) < sm_steps.index(up), "smoke: encryption runs before the upload")
gate_raw = io.open(os.path.join(WF, "mercer-live-selftest.yml"), encoding="utf-8").read()
check("scripts/mercer_live_ops/selftest_mercer_live_ops.py" in code_only(gate_raw),
      "the Mercer Live gate runs this suite")
check("scripts/mercer_live/selftest_mercer_live.py" in code_only(gate_raw), "... after the frozen ML-1 suite")
check('"scripts/mercer_live_ops/**"' in gate_raw and '".github/workflows/mercer-live-digest.yml"' in gate_raw,
      "the gate triggers on the ops package and the digest workflow")

# ============================================================================
print("\n[12] public-proof boundary and repository safety")
man_text = archive.serialise(MAN).decode("utf-8")
check(CANARY not in man_text, "the manifest carries no observed value (canary absent)")
for value in ("Jacksonville", "CLE", "2nd & 7", "observed_at", "provider_event_id", "canonical_event_id",
              "price", "bookmaker"):
    check(value not in man_text, f"the manifest carries no {value!r}")
allowed_strings = re.compile(r"^(raw/(nfl|ncaaf|runs)/[\w./-]+|data/mercer_live/digest/[\d-]+\.json|[0-9a-f]{64}|"
                             r"ml1-v1|ml1-ops-manifest-v1|mercer-live-ml1-ops-v1\.0|\d{4}-\d{2}-\d{2}(T[\d:]+Z)?|"
                             r"nfl|ncaaf|game_state|market_event|market_quote)$")


def strings(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k != "_note":
                yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)
    elif isinstance(o, str):
        yield o


odd = [s for s in strings(MAN) if not allowed_strings.match(s)]
check(not odd, f"every string in the manifest is a path, hash, date, version or kind label ({odd[:3]})")
# Run ids are uuid4 hex (the run-record file names); hashes are sha256 hex.
# Anything ELSE long and token-shaped would be a leak.
scrubbed = re.sub(r"raw/runs/\d{4}-\d{2}-\d{2}/[0-9a-f]{32}\.json|[0-9a-f]{64}", "",
                  man_text.replace(MAN["_note"], ""))
check(not re.search(r"[A-Za-z0-9]{32,}", scrubbed), "no secret-shaped token in the manifest")
tracked = subprocess.run(["git", "ls-files", "data/mercer_live"], cwd=ROOT, capture_output=True, text=True).stdout.split()
check(not any(p.startswith("data/mercer_live/raw/") for p in tracked), "no raw Mercer Live file is tracked by git")
check(all(p == "data/mercer_live/README.md"
          or re.fullmatch(r"data/mercer_live/(digest|manifest)/\d{4}-\d{2}-\d{2}\.json", p) for p in tracked),
      f"data/mercer_live holds only the README, dated digests and dated manifests ({tracked})")
gi = code_only(io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read())
check(re.search(r"^data/mercer_live/raw/\s*$", gi, re.M) and "data/mercer_live/manifest" not in gi,
      ".gitignore still ignores raw/ and does NOT ignore the manifest directory")
dock = code_only(io.open(os.path.join(ROOT, "mercer-live-host", "Dockerfile"), encoding="utf-8").read())
fly = code_only(io.open(os.path.join(ROOT, "mercer-live-host", "fly.toml"), encoding="utf-8").read())
copies = [l for l in dock.splitlines() if l.strip().startswith(("COPY", "ADD"))]
check(copies and not any("data/mercer_live" in l.split()[1] or ".env" in l for l in copies),
      f"the image copies no Mercer Live data and no .env ({len(copies)} COPY lines)")
check(not any(l.split()[1] in (".", "./") for l in copies), "the image never copies the whole repository")
check("ODDS_API_KEY" not in dock and "ODDS_API_KEY" not in fly, "no Odds API key is configured for the host")
check("[http_service]" not in fly and "[[services]]" not in fly, "the host exposes no public service")
check("setpriv --reuid=10001" in dock and "scripts/mercer_live_ops/host.py" in dock,
      "the supervisor is the ops package's host.py and drops root before running")
secret_like = re.compile(r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|[0-9a-f]{32}(?![0-9a-f]))")
for rel in ("mercer-live-host/Dockerfile", "mercer-live-host/fly.toml", ".github/workflows/mercer-live-digest.yml",
            "scripts/mercer_live_ops/host.py", "scripts/mercer_live_ops/archive.py",
            "scripts/mercer_live_ops/host_windows.json"):
    check(not secret_like.search(io.open(os.path.join(ROOT, rel), encoding="utf-8").read()),
          f"{rel}: no key-shaped string")
BANNED_LOGIC = ("edge", "pick", "probability", "unit", "signal", "recommend", "kelly", "stake", "ML-2", "ml2")
for rel in ("archive.py", "digest_commit.py", "evidence.py", "host.py"):
    src = code_only(io.open(os.path.join(HERE, rel), encoding="utf-8").read())
    check("requests." not in src and "urllib" not in src and "http.client" not in src,
          f"{rel}: makes no HTTP call of its own")
    for never in ("post_discord", "DISCORD_WEBHOOK", "ledger.json", "commitments.json", "board_",
                  "index.html", "feed.xml"):
        check(never not in src, f"{rel}: never references {never}")
    idents = set(re.findall(r"\b(?:def|class)\s+(\w+)|\b(\w+)\s*=", src))
    names = {a or b for a, b in idents}
    hits = sorted(n for n in names if any(w in n.lower() for w in BANNED_LOGIC))
    check(not hits, f"{rel}: defines no model/pick/probability/edge/unit/signal/ML-2 name ({hits})")

for d in TEMPS:
    shutil.rmtree(d, ignore_errors=True)
print(f"\nmercer-live ML-1-OPS selftest: {n_checks} checks, "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
