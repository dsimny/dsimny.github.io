#!/usr/bin/env python3
"""
Open Ledger Sports - Mercer Live ML-1: the capture host's supervisor.

    python scripts/mercer_live/host.py serve            # the long-running process
    python scripts/mercer_live/host.py status           # what it WOULD do now, no writes
    python scripts/mercer_live/host.py backup           # one off-host copy pass
    python scripts/mercer_live/host.py publish --date YYYY-MM-DD

OPERATIONS, NOT CONTRACT. This runs the frozen capture (capture.main) on a
schedule and moves its bytes to safety. It adds no field, no record kind and no
provider, and it changes no guard in credits.py. The capture cadence, windows
and host are operational settings (architecture section 19: "What is NOT
frozen"). Design: docs/MERCER_LIVE_OPERATIONAL_PATH.md.

    capture.py --no-odds          -> /data/mercer_live  (persistent volume, append-only)
    backup  every 10 min          -> rclone copy --immutable of SEALED files only
                                     -> private bucket  raw/...   (bucket lock: no delete, no overwrite)
    publish daily, D+1 06:30 ET   -> archive.build -> private bucket  digests/D.json
    (GitHub Actions)              -> mercer-live-digest.yml commits data/mercer_live/digest/D.json

ODDS ARE OFF UNLESS SEPARATELY AUTHORISED. The capture always runs with
--no-odds and ODDS_API_KEY is removed from its environment, UNLESS an
authorisation file exists at $ML1_ODDS_AUTH (default /data/odds-authorization.json)
that names who authorised it, is unexpired today (ET), and was written by a
human on the host. The file is not in this repository and no code here writes
it. Deploying this host therefore spends nothing; turning spend on is a
separate, deliberate act with an expiry date. The frozen floor (5,000) and
daily cap (4,000) in credits.py still apply on top when it is on.

NOTE FOR THE DAY ODDS ARE AUTHORISED: capture books credit readings through
scripts/odds_credits.py into data/odds_credits.json RELATIVE TO THE CODE, i.e.
inside the container. That file is not the repository's ledger and is never
pushed from here, so the floor would read a stale balance. Every reading is
still in the run records (and so in the digest's credits_spent), but wiring
the shared ledger is a prerequisite of authorisation, not of this deployment.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archive                                                    # noqa: E402
import common                                                     # noqa: E402

DATA_DIR = os.environ.get("ML1_DATA_DIR", "/data/mercer_live")
PUBLISHED_DIR = os.environ.get("ML1_PUBLISHED_DIR", "/data/published")   # last digest pushed per date
AUTH_FILE = os.environ.get("ML1_ODDS_AUTH", "/data/odds-authorization.json")
WINDOWS_FILE = os.environ.get("ML1_WINDOWS", os.path.join(HERE, "host_windows.json"))
REMOTE_RAW = os.environ.get("ML1_REMOTE_RAW", "")          # e.g. ml1r2:ol-mercer-live-raw
REMOTE_DIGESTS = os.environ.get("ML1_REMOTE_DIGESTS", "")  # e.g. ml1r2:ol-mercer-live-digests
BACKUP_EVERY_S = 600
PUBLISH_AT_ET = (6, 30)
AUTH_KEYS = ("authorized_by", "authorized_on", "expires_on", "reason")


# ------------------------------------------------------------- spend boundary
def odds_authorisation(path=AUTH_FILE, now=None):
    """(allowed, reason). Fail closed on absence, malformed JSON, missing
    fields, an unparseable date, or an expiry before today (ET)."""
    now = now or common.now_utc()
    if not os.path.exists(path):
        return False, "no authorisation file: odds disabled"
    try:
        with io.open(path, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError):
        return False, "authorisation file unreadable: odds disabled"
    if not isinstance(a, dict) or any(not a.get(k) for k in AUTH_KEYS):
        return False, f"authorisation file lacks {list(AUTH_KEYS)}: odds disabled"
    try:
        exp = datetime.strptime(str(a["expires_on"]), "%Y-%m-%d").date()
    except ValueError:
        return False, "authorisation expires_on is not YYYY-MM-DD: odds disabled"
    today = datetime.strptime(common.et_date(now), "%Y-%m-%d").date()
    if exp < today:
        return False, f"authorisation expired {exp}: odds disabled"
    return True, f"authorised by {a['authorized_by']} until {exp}"


def capture_command(data_dir, allowed, sports=("nfl", "ncaaf")):
    """argv for ONE capture tick. --no-odds unless allowed."""
    argv = [sys.executable, os.path.join(HERE, "capture.py"), "--data-dir", data_dir]
    for s in sports:
        argv += ["--sport", s]
    if not allowed:
        argv.append("--no-odds")
    return argv


def capture_env(allowed, base=None):
    env = dict(os.environ if base is None else base)
    if not allowed:
        env.pop("ODDS_API_KEY", None)   # the key never reaches an unauthorised tick
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------- windows
def load_windows(path=WINDOWS_FILE):
    with io.open(path, encoding="utf-8") as f:
        w = json.load(f)
    return w["windows"]


def in_window(windows, now=None):
    """True when `now` (UTC) falls inside any ET window. A window is
    {"weekday": 0-6 (Mon=0), "start": "HH:MM", "minutes": N}; it may run past
    midnight into the next ET day."""
    now = now or common.now_utc()
    local = now.astimezone(common.ET)
    for w in windows:
        hh, mm = (int(x) for x in w["start"].split(":"))
        for back in (0, 1):                      # today's window, or yesterday's spill-over
            day = (local - timedelta(days=back)).date()
            if day.weekday() != w["weekday"]:
                continue
            start = datetime(day.year, day.month, day.day, hh, mm, tzinfo=common.ET)
            end = start.astimezone(common.UTC) + timedelta(minutes=int(w["minutes"]))
            if start.astimezone(common.UTC) <= now < end:
                return True
    return False


# ----------------------------------------------------------------- backup
def backup_command(data_dir, remote, files_from):
    """rclone argv for one off-host pass. COPY, never sync/move/delete;
    --immutable makes a changed file an ERROR instead of an overwrite;
    --checksum compares content, not timestamps."""
    if not remote:
        raise archive.ArchiveError("ML1_REMOTE_RAW is not configured")
    return ["rclone", "copy", data_dir, remote, "--files-from", files_from,
            "--immutable", "--checksum", "--no-traverse", "--s3-no-check-bucket",
            "--retries", "3", "--log-level", "NOTICE"]


def backup(data_dir=DATA_DIR, remote=REMOTE_RAW, runner=subprocess.run, now=None):
    files = archive.sealed(data_dir, now=now)
    if not files:
        return 0, "nothing sealed yet"
    fd, lst = tempfile.mkstemp(prefix="ml1-sealed-", suffix=".txt")
    try:
        with io.open(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(files) + "\n")
        rc = runner(backup_command(data_dir, remote, lst)).returncode
    finally:
        os.remove(lst)
    return rc, f"{len(files)} sealed files offered to {remote} (rc={rc})"


# ---------------------------------------------------------------- publish
def publish(et_date, data_dir=DATA_DIR, published_dir=PUBLISHED_DIR, remote=REMOTE_DIGESTS,
            runner=subprocess.run, now=None):
    """Build the closed day's digest, check it against what was last
    published, and copy it to the digest bucket. Returns (rc, message)."""
    if not remote:
        return 1, "ML1_REMOTE_DIGESTS is not configured"
    os.makedirs(published_dir, exist_ok=True)
    prev_path = os.path.join(published_dir, f"{et_date}.json")
    prev = None
    if os.path.exists(prev_path):
        with io.open(prev_path, encoding="utf-8") as f:
            prev = json.load(f)
    try:
        dig, action = archive.build(data_dir, et_date, previous=prev, now=now)
    except archive.ArchiveError as e:
        return 1, f"REFUSED {et_date}: {e}"
    if action == "unchanged":
        return 0, f"{et_date}: unchanged"
    if not dig["shards"] and not dig["run_records"]:
        return 0, f"{et_date}: nothing captured that day; no digest published"
    tmp = prev_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(archive.serialise(dig))
    # Upload FIRST, record locally only on success, so a failed upload is
    # simply retried by the next pass. The digest bucket is not locked
    # against overwrite: a grown digest must be able to replace its
    # predecessor, and mercer-live-digest.yml refuses anything but growth.
    rc = runner(["rclone", "copyto", tmp, f"{remote}/digests/{et_date}.json",
                 "--checksum", "--s3-no-check-bucket", "--log-level", "NOTICE"]).returncode
    if rc != 0:
        os.remove(tmp)
        return rc, f"{et_date}: upload failed (rc={rc}); will retry"
    os.replace(tmp, prev_path)
    return 0, f"{et_date}: {action} digest published"


def pending_dates(data_dir=DATA_DIR, published_dir=PUBLISHED_DIR, now=None, lookback=7):
    """Closed ET dates in the last `lookback` days that have evidence on disk."""
    now = now or common.now_utc()
    today = datetime.strptime(common.et_date(now), "%Y-%m-%d")
    out = []
    for back in range(1, lookback + 1):
        d = (today - timedelta(days=back)).strftime("%Y-%m-%d")
        if archive.closed(d, now=now) and archive.day_files(os.path.abspath(data_dir), d):
            out.append(d)
    return sorted(out)


# ------------------------------------------------------------------- serve
def serve(max_loops=0, sleep=time.sleep, runner=subprocess.run, clock=None):
    """The supervisor. One tick per minute inside a window; a backup pass
    every BACKUP_EVERY_S; the digest publish after PUBLISH_AT_ET. Every
    child is a subprocess, so a crashing tick cannot take the loop down."""
    clock = clock or common.now_utc
    windows = load_windows()
    last_backup = None
    loops = 0
    while True:
        now = clock()
        allowed, why = odds_authorisation(now=now)
        if in_window(windows, now):
            rc = runner(capture_command(DATA_DIR, allowed), env=capture_env(allowed)).returncode
            print(f"{common.iso(now)} tick rc={rc} odds={'on' if allowed else 'off'} ({why})", flush=True)
        if last_backup is None or (now - last_backup).total_seconds() >= BACKUP_EVERY_S:
            rc, msg = backup(runner=runner, now=now)
            print(f"{common.iso(now)} backup {msg}", flush=True)
            last_backup = now
            local = now.astimezone(common.ET)
            if (local.hour, local.minute) >= PUBLISH_AT_ET:
                for d in pending_dates(now=now):
                    rc, msg = publish(d, runner=runner, now=now)
                    print(f"{common.iso(now)} publish {msg}", flush=True)
        loops += 1
        if max_loops and loops >= max_loops:
            return 0
        nxt = 60 - (clock().second % 60)
        sleep(nxt if nxt > 0 else 60)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("status")
    sub.add_parser("backup")
    p = sub.add_parser("publish")
    p.add_argument("--date", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        return serve()
    if args.cmd == "status":
        now = common.now_utc()
        allowed, why = odds_authorisation(now=now)
        print(f"now={common.iso(now)} in_window={in_window(load_windows(), now)} "
              f"odds={'on' if allowed else 'off'} ({why}) data_dir={DATA_DIR} "
              f"sealed={len(archive.sealed(DATA_DIR, now=now))} pending_digests={pending_dates(now=now)} "
              f"remote_raw={'set' if REMOTE_RAW else 'UNSET'} remote_digests={'set' if REMOTE_DIGESTS else 'UNSET'}")
        return 0
    if args.cmd == "backup":
        rc, msg = backup()
        print(msg)
        return rc
    if args.cmd == "publish":
        rc, msg = publish(args.date)
        print(msg)
        return rc
    return 2


if __name__ == "__main__":
    sys.exit(main())
