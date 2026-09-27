#!/usr/bin/env python3
"""
Open Ledger Sports - Mercer Live ML-1 OPERATIONAL-PATH self-test (T1, T2, T3).

    python scripts/mercer_live/selftest_mercer_live_ops.py

Separate from selftest_mercer_live.py on purpose: that suite is the frozen
ML-1 contract (its count is quoted in the freeze record); this one covers the
operational layer added 2026-09-27 - durable digests, durable raw retention,
and log hygiene. Same rules: HERMETIC (requests.get/post fail the suite if
called), temp directories only, the real repository read only for contract
checks, and a NEGATIVE control beside every guard.

Groups:
  [1]  multi-run / same ET day: the collision is real, ingest loses nothing
  [2]  repeat execution is a no-op; ingest refuses conflicts and torn sources
  [3]  digest integrity: run records fingerprinted; tamper / unlisted detected
  [4]  append-only digest succession (supersedes / verify_prefix / build)
  [5]  digest_commit: the one repository write, and every refusal
  [6]  time: sealed shards and closed days across DST
  [7]  host spend boundary: odds off unless separately authorised
  [8]  host backup / publish / restore drill against a fake object store
  [9]  host windows and the supervisor loop
  [10] T3: evidence output carries no observed value (canary)
  [11] workflow contracts: digest workflow, smoke workflow, gate
  [12] repository safety: no raw evidence, no secrets, deploy files
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
sys.path.insert(0, HERE)
import archive                                                    # noqa: E402
import capture                                                    # noqa: E402
import common                                                     # noqa: E402
import digest_commit                                              # noqa: E402
import evidence                                                   # noqa: E402
import host                                                       # noqa: E402
from store import Store                                           # noqa: E402

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


# ---- fixtures: a real ESPN-shaped payload through the frozen capture ------
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
                out[os.path.relpath(p, d)] = f.read()
    return out


# 2026-09-21 20:46 ET and 20:58 ET: the same ET hour, three runners' shape.
T_A = datetime(2026, 9, 22, 0, 46, 30, tzinfo=UTC)
T_B = datetime(2026, 9, 22, 0, 58, 10, tzinfo=UTC)
T_C = datetime(2026, 9, 22, 3, 20, 21, tzinfo=UTC)           # 23:20 ET, same ET date
DAY = "2026-09-21"
AFTER = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)              # the day is long closed

# ============================================================================
print("[1] multi-run / same ET day: the collision is real, ingest loses nothing")
A, B, C = tmpdir(), tmpdir(), tmpdir()
capture_into(A, T_A)
capture_into(A, T_A + timedelta(minutes=1), clock=380.0)
capture_into(B, T_B, clock=300.0)
capture_into(C, T_C, clock=11.0)
shard = f"raw/nfl/{DAY}/game_state_20.jsonl"
check(os.path.exists(os.path.join(A, shard)) and os.path.exists(os.path.join(B, shard)),
      "two separate runners write the SAME relative shard path for the same ET hour")
check(tree_bytes(A)[shard] != tree_bytes(B)[shard], "... with DIFFERENT bytes")
# Negative control: the naive way (one store copied over another) loses data.
naive = tmpdir()
for src in (A, B):
    shutil.copytree(src, naive, dirs_exist_ok=True)
expected = set(all_ids(A)) | set(all_ids(B)) | set(all_ids(C))
check(len(set(all_ids(naive))) < len(set(all_ids(A)) | set(all_ids(B))),
      f"NEGATIVE CONTROL: copying stores over each other silently loses observations "
      f"({len(set(all_ids(naive)))} of {len(set(all_ids(A)) | set(all_ids(B)))})")
dig_a = Store(A).digest(DAY, write=False)
dig_b = Store(B).digest(DAY, write=False)
check([s["sha256"] for s in dig_a["shards"] if s["path"] == shard]
      != [s["sha256"] for s in dig_b["shards"] if s["path"] == shard],
      "NEGATIVE CONTROL: per-runner digests disagree on the same path - committing the last loses the first")
D = tmpdir()
dst = Store(D)
reports = [archive.ingest(s, dst) for s in (A, B, C)]
check(sum(r["records_written"] for r in reports) == len(expected) and sorted(all_ids(D)) == sorted(expected),
      f"ingest keeps EVERY observation from every runner ({len(all_ids(D))} == {len(expected)})")
check(len(all_ids(D)) == len(set(all_ids(D))), "and no observation twice")
check(sum(r["run_records_copied"] for r in reports) == 4 and len(Store(D).runs_for_date(DAY)) == 4,
      "every run record survives (4 runs from 3 runners)")
dig = Store(D).digest(DAY, write=False)
check(dig["runs"] == 4 and len(dig["run_records"]) == 4
      and sum(s["parsed"] for s in dig["shards"]) == len(expected),
      "the durable digest covers all runs and all observations of the day")
check(archive.verify(D, dig) == [], "and verifies against the durable store byte-for-byte")

# ============================================================================
print("\n[2] repeat execution is a no-op; ingest refuses conflicts and torn sources")
before = tree_bytes(D)
again = [archive.ingest(s, dst) for s in (A, B, C)]
check(all(r["records_written"] == 0 and r["run_records_copied"] == 0 for r in again)
      and sum(r["duplicates_skipped"] for r in again) == len(expected),
      "re-ingesting the same stores writes nothing and counts every duplicate")
check(tree_bytes(D) == before, "... and changes no byte of the durable store")
dig2 = Store(D).digest(DAY, write=False)
check(archive.comparable(dig2) == archive.comparable(dig), "the rebuilt digest is identical bar generated_at")
# Order independence of the observation SET.
D2 = tmpdir()
for s in (C, B, A):
    archive.ingest(s, Store(D2))
check(sorted(all_ids(D2)) == sorted(all_ids(D)), "ingest order does not change which observations survive")
# Run-record conflict: same run_id, different bytes -> refused, nothing written.
X = tmpdir()
shutil.copytree(A, X, dirs_exist_ok=True)
rp = [p for p in tree_bytes(X) if p.startswith(os.path.join("raw", "runs"))][0]
with io.open(os.path.join(X, rp), "ab") as f:
    f.write(b" ")
snap = tree_bytes(D)
try:
    archive.ingest(X, dst)
    check(False, "a run-record conflict must be refused")
except archive.ArchiveError as e:
    check("conflict" in str(e), "same run_id with different bytes is REFUSED")
check(tree_bytes(D) == snap, "... and nothing at all was written")
# Torn line in the source: refuse the whole source rather than drop a line.
T = tmpdir()
capture_into(T, T_A + timedelta(minutes=5))
tp = os.path.join(T, shard)
with io.open(tp, "ab") as f:
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
print("\n[3] digest integrity: run records fingerprinted; tamper / unlisted detected")
check(all(re.fullmatch(r"[0-9a-f]{64}", r["sha256"]) for r in dig["run_records"]),
      "every run record carries a sha256 (section 20 amendment)")
check(set(dig) <= archive.DIGEST_KEYS and archive.validate(dig, DAY) == [],
      "the digest validates and carries only known keys")
old_keys = {"_note", "schema_version", "et_date", "generated_at", "shards", "runs", "runs_with_errors",
            "odds_calls", "odds_http_non_200", "credits_spent", "credits_remaining_at_last_call",
            "records_written"}
check(old_keys <= set(dig) and set(dig) - old_keys == {"run_records"},
      "the amendment is ADDITIVE: every ml1-v1 key kept, exactly one added")
check(dig["schema_version"] == "ml1-v1", "schema_version unchanged (ml1-v1)")
for label, mutate in (
        ("a flipped byte in a shard", lambda root: _flip(os.path.join(root, shard))),
        ("an edited run record", lambda root: _flip(os.path.join(root, dig["run_records"][0]["path"]))),
        ("a deleted shard", lambda root: os.remove(os.path.join(root, shard))),
        ("an unlisted extra shard", lambda root: shutil.copy(os.path.join(root, shard),
                                                             os.path.join(root, shard.replace("_20", "_05"))))):
    V = tmpdir()
    shutil.copytree(D, V, dirs_exist_ok=True)

    def _flip(p):
        with io.open(p, "r+b") as f:
            b = f.read(1)
            f.seek(0)
            f.write(bytes([b[0] ^ 1]))

    mutate(V)
    check(archive.verify(V, dig) != [], f"verify detects {label}")
check(archive.validate(dict(dig, shards=[dict(dig["shards"][0], path="raw/nfl/2026-09-21/../../index.html")]))
      != [], "a traversal path in a digest is refused")
check(archive.validate(dict(dig, extra_field=1)) != [], "an unknown top-level key is refused")

# ============================================================================
print("\n[4] append-only digest succession")
ok, why = archive.supersedes(dig, dig2)
check(ok, "an identical rebuild supersedes cleanly")
E = tmpdir()
shutil.copytree(D, E, dirs_exist_ok=True)
capture_into(E, datetime(2026, 9, 21, 16, 5, tzinfo=UTC))       # a NEW hour's shard (12 ET)
grown = Store(E).digest(DAY, write=False)
ok, why = archive.supersedes(dig, grown)
check(ok and len(grown["shards"]) > len(dig["shards"]), "a digest that only ADDS entries supersedes")
changed = json.loads(json.dumps(dig))
changed["shards"][0]["sha256"] = "0" * 64
check(not archive.supersedes(dig, changed)[0], "a changed committed entry is refused")
dropped = dict(dig, shards=dig["shards"][1:])
check(not archive.supersedes(dig, dropped)[0], "a missing committed entry is refused")
check(not archive.supersedes(dig, dict(dig2, runs=dig["runs"] - 1))[0], "a count that goes DOWN is refused")
# build(): the host-side gate
try:
    archive.build(D, DAY, now=datetime(2026, 9, 22, 3, 59, tzinfo=UTC))
    check(False, "an open day must be refused")
except archive.ArchiveError as e:
    check("not closed" in str(e), "build refuses a day that has not closed (23:59 ET)")
b1, act = archive.build(D, DAY, now=AFTER)
check(act == "new", "build of a closed day: new")
b2, act2 = archive.build(D, DAY, previous=b1, now=AFTER)
check(act2 == "unchanged" and b2 is b1, "build again with nothing new: unchanged (the published file is reused)")
b3, act3 = archive.build(E, DAY, previous=b1, now=AFTER)
check(act3 == "grown", "build after a late ingest of a new hour: grown")
# appended-to vs rewritten, as seen by verify_prefix
F = tmpdir()
shutil.copytree(D, F, dirs_exist_ok=True)
capture_into(F, T_A + timedelta(minutes=9))                    # appends to the committed 20 shard
check(archive.verify_prefix(F, b1) == [], "verify_prefix accepts a shard that was only APPENDED to")
try:
    archive.build(F, DAY, previous=b1, now=AFTER)
    check(False, "an appended committed shard must still need a human")
except archive.ArchiveError as e:
    check("REFUSED" in str(e), "... but build still REFUSES to replace a committed shard's hash (human path)")
G = tmpdir()
shutil.copytree(D, G, dirs_exist_ok=True)
with io.open(os.path.join(G, shard), "r+b") as f:
    f.write(b"X")
pf = archive.verify_prefix(G, b1)
check(pf and "REWRITTEN" in pf[0], "verify_prefix names a REWRITTEN shard as such")

# ============================================================================
print("\n[5] digest_commit: the one repository write, and every refusal")
canon = archive.serialise(b1).encode("utf-8")
act, body, _ = digest_commit.decide(canon, DAY, None, now=AFTER)
check(act == "CREATED" and body == canon, "first digest for a date: CREATED with the canonical bytes")
later = archive.serialise(dict(b1, generated_at="2026-09-24T10:00:00Z")).encode("utf-8")
act, body, _ = digest_commit.decide(later, DAY, canon, now=AFTER)
check(act == "UNCHANGED" and body is None, "same evidence, new generated_at: UNCHANGED, nothing written")
act, body, _ = digest_commit.decide(archive.serialise(b3).encode("utf-8"), DAY, canon, now=AFTER)
check(act == "GROWN" and body is not None, "only additions: GROWN")
act, body, _ = digest_commit.decide(archive.serialise(changed).encode("utf-8"), DAY, canon, now=AFTER)
check(act == "REFUSED-DIFFERS" and body is None, "a changed entry: REFUSED-DIFFERS, nothing written")
for label, cand, date, now in (
        ("not JSON", b"{nope", DAY, AFTER),
        ("non-canonical formatting", json.dumps(b1).encode(), DAY, AFTER),
        ("a different date than requested", canon, "2026-09-20", AFTER),
        ("a day that has not closed", canon, DAY, datetime(2026, 9, 22, 3, 0, tzinfo=UTC)),
        ("a traversal shard path", archive.serialise(dict(b1, shards=[dict(b1["shards"][0],
                                                                        path="raw/nfl/2026-09-21/../../../feed.xml")])).encode(), DAY, AFTER),
        ("a wrong schema_version", archive.serialise(dict(b1, schema_version="ml2")).encode(), DAY, AFTER)):
    act, body, _ = digest_commit.decide(cand, date, None, now=now)
    check(act == "REFUSED" and body is None, f"REFUSED: {label}")
# main(): exactly one path written, nothing else touched, MISSING is red.
R = tmpdir()
ddir = os.path.join(R, "data", "mercer_live", "digest")
os.makedirs(ddir)
for fn in ("index.html", "feed.xml"):
    with io.open(os.path.join(R, fn), "w") as f:
        f.write("site\n")
cf = os.path.join(R, "cand.json")
with io.open(cf, "wb") as f:
    f.write(canon)
snap = tree_bytes(R)
real_closed = archive.closed
archive.closed = lambda d, now=None, grace_min=10: real_closed(d, now=AFTER, grace_min=grace_min)
try:
    rc = digest_commit.main(["--candidate", cf, "--date", DAY, "--digest-dir", ddir])
    after = tree_bytes(R)
    new = sorted(set(after) - set(snap))
    check(rc == 0 and new == [os.path.join("data", "mercer_live", "digest", f"{DAY}.json")],
          f"main() writes exactly data/mercer_live/digest/{DAY}.json ({new})")
    check(all(after[k] == v for k, v in snap.items()), "and changes no other byte (index.html, feed.xml untouched)")
    rc = digest_commit.main(["--candidate", os.path.join(R, "absent.json"), "--date", DAY, "--digest-dir", ddir])
    check(rc == 1, "a missing candidate is MISSING and exits 1 (the red run is the alert)")
    rc = digest_commit.main(["--candidate", cf, "--date", "../../x", "--digest-dir", ddir])
    check(rc == 1, "a malformed --date is refused before any path is formed")
finally:
    archive.closed = real_closed
check(digest_commit.target_path(DAY).replace(os.sep, "/").endswith(f"data/mercer_live/digest/{DAY}.json"),
      "the default target is data/mercer_live/digest/<date>.json in this repository")

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
check(all(p.startswith("raw/runs/") or p.endswith(".jsonl") for p in archive.sealed(S, now=AFTER.replace(month=12))),
      "sealed lists only shards and run records")
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

# ============================================================================
print("\n[7] host spend boundary: odds off unless separately authorised")
H = tmpdir()
auth = os.path.join(H, "auth.json")
NOW7 = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)
check(host.odds_authorisation(auth, now=NOW7) == (False, "no authorisation file: odds disabled"),
      "no authorisation file: odds OFF")
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
env_off = host.capture_env(False, base={"ODDS_API_KEY": "secret-key", "PATH": "/bin"})
env_on = host.capture_env(True, base={"ODDS_API_KEY": "secret-key", "PATH": "/bin"})
check("ODDS_API_KEY" not in env_off and env_on.get("ODDS_API_KEY") == "secret-key",
      "and the key is REMOVED from an unauthorised tick's environment")
host_src = io.open(os.path.join(HERE, "host.py"), encoding="utf-8").read()
check(not re.search(r"(io\.)?open\([^)]*AUTH_FILE[^)]*['\"]w", host_src) and "json.dump(" not in
      "\n".join(l for l in host_src.splitlines() if "auth" in l.lower()),
      "no code writes the authorisation file")

# ============================================================================
print("\n[8] host backup / publish / restore drill against a fake object store")
BUCKET = tmpdir("mlbucket")
DIGBUCKET = tmpdir("mldig")
CALLS = []


def fake_rclone(argv, **kw):
    """Emulates `rclone copy --immutable --files-from` and `rclone copyto`
    into local directories standing in for the two buckets."""
    CALLS.append(list(argv))
    if argv[:2] == ["rclone", "copy"]:
        src, remote = argv[2], argv[3]
        rootdst = BUCKET if remote == "bucket:raw" else None
        files = io.open(argv[argv.index("--files-from") + 1], encoding="utf-8").read().split()
        for rel in files:
            s = os.path.join(src, rel)
            d = os.path.join(rootdst, rel)
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
check(rc == 0 and sorted(archive.sealed(D, now=AFTER)) == sorted(
      os.path.relpath(os.path.join(dp, f), BUCKET).replace(os.sep, "/")
      for dp, _d, fs in os.walk(BUCKET) for f in fs), f"backup copies every sealed file off-host ({msg})")
rc, _ = host.backup(D, "bucket:raw", runner=fake_rclone, now=AFTER)
check(rc == 0, "a second backup pass is a clean no-op")
# A local file that changes after it went off-host is an ERROR, never an overwrite.
M = tmpdir()
shutil.copytree(D, M, dirs_exist_ok=True)
with io.open(os.path.join(M, shard), "r+b") as f:
    f.write(b"Z")
remote_before = tree_bytes(BUCKET)
rc, _ = host.backup(M, "bucket:raw", runner=fake_rclone, now=AFTER)
check(rc != 0 and tree_bytes(BUCKET) == remote_before,
      "a locally rewritten shard makes the backup FAIL and the off-host copy is untouched")
# publish
PUB = tmpdir()
rc, msg = host.publish(DAY, data_dir=D, published_dir=PUB, remote="bucket:dig", runner=fake_rclone, now=AFTER)
check(rc == 0 and os.path.exists(os.path.join(DIGBUCKET, "digests", f"{DAY}.json"))
      and os.path.exists(os.path.join(PUB, f"{DAY}.json")), f"publish uploads the closed day's digest ({msg})")
n = len(CALLS)
rc, msg = host.publish(DAY, data_dir=D, published_dir=PUB, remote="bucket:dig", runner=fake_rclone, now=AFTER)
check(rc == 0 and "unchanged" in msg and len(CALLS) == n, "publish again: unchanged, no upload")
rc, msg = host.publish(DAY, data_dir=G, published_dir=PUB, remote="bucket:dig", runner=fake_rclone, now=AFTER)
check(rc == 1 and "REFUSED" in msg, "publish REFUSES when the published evidence was rewritten")
rc, msg = host.publish("2026-09-21", data_dir=D, published_dir=tmpdir(), remote="bucket:dig",
                       runner=lambda a, **k: SimpleNamespace(returncode=5), now=AFTER)
check(rc == 5, "a failed upload is reported and NOT recorded as published (it is retried)")
rc, msg = host.publish(DAY, data_dir=D, published_dir=tmpdir(), remote="", runner=fake_rclone, now=AFTER)
check(rc == 1, "publish with no digest bucket configured fails loudly")
with io.open(os.path.join(DIGBUCKET, "digests", f"{DAY}.json"), "rb") as f:
    pub_bytes = f.read()
act, body, _ = digest_commit.decide(pub_bytes, DAY, None, now=AFTER)
check(act == "CREATED", "what the host publishes is exactly what digest_commit accepts")
# RESTORE DRILL: the host is gone; rebuild from the bucket, verify vs the committed digest.
RESTORED = tmpdir()
shutil.copytree(BUCKET, RESTORED, dirs_exist_ok=True)
committed = json.loads(body.decode("utf-8"))
check(archive.verify(RESTORED, committed) == [],
      "RESTORE DRILL: bytes restored from the bucket alone verify against the committed digest")
_rf = os.path.join(RESTORED, shard)
with io.open(_rf, "r+b") as f:
    b0 = f.read(1)
    f.seek(0)
    f.write(bytes([b0[0] ^ 1]))
check(archive.verify(RESTORED, committed) != [], "RESTORE DRILL negative control: one flipped byte fails")
rc = subprocess.run([sys.executable, os.path.join(HERE, "archive.py"), "verify", "--data-dir", BUCKET,
                     "--digest", os.path.join(DIGBUCKET, "digests", f"{DAY}.json")],
                    capture_output=True, text=True)
check(rc.returncode == 0 and "OK" in rc.stdout and CANARY not in rc.stdout,
      "the CLI verify prints OK and no observed value")

# ============================================================================
print("\n[9] host windows and the supervisor loop")
W = host.load_windows()
et = common.ET
cases = [(datetime(2026, 10, 4, 13, 0, tzinfo=et), True, "Sunday 13:00 ET"),
         (datetime(2026, 10, 6, 10, 0, tzinfo=et), False, "Tuesday 10:00 ET"),
         (datetime(2026, 10, 4, 0, 30, tzinfo=et), True, "Sunday 00:30 ET (Saturday's window spills over)"),
         (datetime(2026, 10, 5, 0, 30, tzinfo=et), False, "Monday 00:30 ET (Sunday's window has ended)"),
         (datetime(2026, 10, 6, 0, 15, tzinfo=et), True, "Tuesday 00:15 ET (Monday night spill-over)")]
for when, want, label in cases:
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
print("\n[10] T3: evidence output carries no observed value (canary)")
EV = tmpdir()
for i in range(3):
    capture_into(EV, T_A + timedelta(minutes=i), clock=420.0 - 60 * i)
capture_into(EV, T_A, run_id=Store(EV).runs_for_date(DAY)[0]["run_id"])     # a retry
Store(EV).digest(DAY)
raw_blob = b"".join(tree_bytes(EV).values())
check(CANARY.encode() in raw_blob, "the fixture's raw records DO carry the canary (the control is live)")
out = "\n".join(evidence.summarise(EV))
check(CANARY not in out, "evidence.summarise never prints the canary")
p = subprocess.run([sys.executable, os.path.join(HERE, "evidence.py"), EV], capture_output=True, text=True)
check(p.returncode == 0 and CANARY not in p.stdout + p.stderr, "... nor does the CLI the workflow runs")
for value in ("420", "380", "2nd & 7", "Rush", "CLE 45", "0.61", "Jacksonville"):
    check(value not in out, f"no observed value {value!r} in the evidence")
check("records game_state=6" in out, "evidence counts records (2 events x 3 ticks)")
check("FIELD down_distance_text: present 3/6 all, 3/3 in-progress" in out,
      "evidence proves field PRESENCE by count")
check("strictly_increasing_observed_at=yes" in out and "clock_seconds=2" in out,
      "evidence proves PROGRESSION by change counts (the clock moved twice)")
check("duplicates_skipped=" in out and re.search(r"runs records=3 .*distinct run_id=3", out),
      "evidence shows run identity and that the retry added no run")
check("DIGEST 2026-09-21 schema=ml1-v1" in out and "lines_equal_parsed=yes" in out, "evidence shows the digest shape")
# capture's own per-tick line is counts only too
orig_stdout = sys.stdout
buf = io.StringIO()
sys.stdout = buf
try:
    print(capture._summary_line(capture_into(tmpdir(), T_A)))
finally:
    sys.stdout = orig_stdout
check(CANARY not in buf.getvalue(), "capture's per-tick summary line carries no observed value")

# ============================================================================
print("\n[11] workflow contracts: digest workflow, smoke workflow, gate")
WF = os.path.join(ROOT, ".github", "workflows")


def code_only(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


dg_raw = io.open(os.path.join(WF, "mercer-live-digest.yml"), encoding="utf-8").read()
dg = yaml.safe_load(dg_raw)
dg_code = code_only(dg_raw)
trig = dg.get(True) or dg.get("on")
check(set(trig) == {"workflow_dispatch"}, "digest workflow: dispatch-only (no GitHub cron, no push trigger)")
check(dg.get("permissions") == {"contents": "write"}, "digest workflow: permissions are exactly contents: write")
steps = dg["jobs"]["commit"]["steps"]
commit = code_only(next(s for s in steps if s.get("name") == "Commit the digest")["run"])
adds = re.findall(r"^\s*git add\s+(.+)$", commit, re.M)
check(adds == ['"data/mercer_live/digest/$D.json"'], f"digest workflow stages exactly ONE literal path ({adds})")
check('D="${{ steps.d.outputs.date }}"' in commit and 'git diff --cached --name-only' in commit and "refusing to commit" in commit,
      "and asserts the index holds that one path before committing")
for banned in ("git add -A", "git add .", "index.html", "feed.xml", "--force", "--autostash", "git pull",
               "ODDS_API_KEY", "DISCORD", "post_discord", "raw/"):
    check(banned not in dg_code, f"digest workflow never contains {banned!r}")
secrets_used = set(re.findall(r"secrets\.([A-Z0-9_]+)", dg_raw))
check(secrets_used == {"ML1_DIGEST_R2_ACCESS_KEY_ID", "ML1_DIGEST_R2_SECRET_ACCESS_KEY"},
      f"digest workflow uses only the read-only digest-bucket credential ({sorted(secrets_used)})")
check("digest_commit.py" in dg_code and "digests/$D.json" in dg_code,
      "it fetches ONLY digests/<date>.json and places it via digest_commit.py")
# the staged-set guard really bites (negative control against real git)
g = tmpdir("mlgit")
env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
subprocess.run(["git", "init", "-q", g], check=True, env=env)
for rel in ("data/mercer_live/digest/2026-09-21.json", "index.html"):
    os.makedirs(os.path.dirname(os.path.join(g, rel)) or g, exist_ok=True)
    with io.open(os.path.join(g, rel), "w") as f:
        f.write("x\n")
guard = commit[commit.index('git add'):commit.index('git diff --cached --quiet')]
ok_run = subprocess.run(["bash", "-c", guard], cwd=g, env=dict(env, D="2026-09-21"), capture_output=True, text=True)
check(ok_run.returncode == 0, "staging guard: the one digest path passes")
subprocess.run(["git", "add", "index.html"], cwd=g, env=env, check=True)
bad_run = subprocess.run(["bash", "-c", guard], cwd=g, env=dict(env, D="2026-09-21"), capture_output=True, text=True)
check(bad_run.returncode == 1 and "refusing" in bad_run.stdout,
      "NEGATIVE CONTROL: with index.html also staged, the guard refuses")
# smoke workflow (T3)
sm_raw = io.open(os.path.join(WF, "mercer-live-smoke.yml"), encoding="utf-8").read()
sm = yaml.safe_load(sm_raw)
sm_steps = sm["jobs"]["smoke"]["steps"]
body = code_only(sm_steps[3]["run"])
check(not re.search(r"\bcat\b", body), "smoke: no `cat` of any store file into the log")
for banned in ('print("OBS', 'print("LASTPLAY', 'print("IDENTITY', 'print("MISS', "json.dumps(r.get(\"last_play"):
    check(banned not in body, f"smoke: no per-observation value dump ({banned})")
check("evidence.py" in body, "smoke: prints the compact evidence instead")
check('find "$STORE" -type f | sed' not in body, "smoke: the step summary no longer lists raw file paths")
check("RUNNER_TEMP" in body and "--no-odds" in body and '--run-id "$FIRST"' in body and "retry changed no shard byte" in body,
      "smoke: still temp-dir only, still --no-odds by default, still proves the retry")
up = next(s for s in sm_steps if "upload-artifact" in str(s.get("uses", "")))
check(str(up["with"]["path"]).endswith(".tar.age"), "smoke: the ONLY uploaded artifact is the age-encrypted tarball")
enc = next(s for s in sm_steps if s.get("name") == "Encrypt the raw store for the artifact")
enc_code = code_only(enc["run"])
check("age -r" in enc_code and "exit 0" in enc_code and "vars.ML1_ARTIFACT_AGE_RECIPIENT" in str(enc.get("env")),
      "smoke: encryption is to a public-key VARIABLE; unset means nothing raw is uploaded")
check(sm_steps.index(enc) < sm_steps.index(up), "smoke: encryption runs before the upload")
check("secrets.ML1" not in sm_raw, "smoke: no new secret")
# the gate runs this suite
gate_raw = io.open(os.path.join(WF, "mercer-live-selftest.yml"), encoding="utf-8").read()
check("selftest_mercer_live_ops.py" in code_only(gate_raw), "the Mercer Live gate runs this suite")
check(".github/workflows/mercer-live-digest.yml" in gate_raw, "the gate triggers on the digest workflow")

# ============================================================================
print("\n[12] repository safety: no raw evidence, no secrets, deploy files")
tracked = subprocess.run(["git", "ls-files", "data/mercer_live"], cwd=ROOT, capture_output=True, text=True).stdout.split()
check(not any(p.startswith("data/mercer_live/raw/") for p in tracked), "no raw Mercer Live file is tracked")
check(all(p == "data/mercer_live/README.md" or re.fullmatch(r"data/mercer_live/digest/\d{4}-\d{2}-\d{2}\.json", p)
          for p in tracked), f"data/mercer_live holds only the README and dated digests ({tracked})")
dock = io.open(os.path.join(ROOT, "mercer-live-host", "Dockerfile"), encoding="utf-8").read()
fly = io.open(os.path.join(ROOT, "mercer-live-host", "fly.toml"), encoding="utf-8").read()
dock_code, fly_code = code_only(dock), code_only(fly)
copies = [l for l in dock_code.splitlines() if l.strip().startswith(("COPY", "ADD"))]
check(copies and not any("mercer_live/raw" in l or "data/mercer_live" in l.split()[1] or ".env" in l for l in copies),
      f"the image copies no Mercer Live data and no .env ({len(copies)} COPY lines)")
check(not any(l.split()[1] in (".", "./") for l in copies), "the image never copies the whole repository")
check("ODDS_API_KEY" not in dock_code and "ODDS_API_KEY" not in fly_code, "no Odds API key is configured for the host")
check("[http_service]" not in fly_code and "[[services]]" not in fly_code, "the host exposes no public service")
check("setpriv --reuid=10001" in dock_code, "the supervisor drops root before running")
secret_like = re.compile(r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|[0-9a-f]{32}(?![0-9a-f]))")
for rel in ("mercer-live-host/Dockerfile", "mercer-live-host/fly.toml", ".github/workflows/mercer-live-digest.yml",
            "scripts/mercer_live/host.py", "scripts/mercer_live/archive.py", "scripts/mercer_live/host_windows.json"):
    txt = io.open(os.path.join(ROOT, rel), encoding="utf-8").read()
    check(not secret_like.search(txt), f"{rel}: no key-shaped string")
for rel in ("archive.py", "digest_commit.py", "evidence.py", "host.py"):
    src = code_only(io.open(os.path.join(HERE, rel), encoding="utf-8").read())
    check("requests." not in src and "urllib" not in src and "http.client" not in src,
          f"{rel}: makes no HTTP call of its own")
    for never in ("post_discord", "DISCORD_WEBHOOK", "ledger.json", "commitments.json", "board_"):
        check(never not in src, f"{rel}: never references {never}")

for d in TEMPS:
    shutil.rmtree(d, ignore_errors=True)
print(f"\nmercer-live ops selftest: {n_checks} checks, "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
