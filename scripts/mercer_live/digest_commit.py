#!/usr/bin/env python3
"""
Open Ledger Sports - Mercer Live ML-1: place ONE published digest in the repo.

    python scripts/mercer_live/digest_commit.py --candidate FILE --date YYYY-MM-DD

Runs inside .github/workflows/mercer-live-digest.yml. It is the ONLY Mercer
Live code that writes into the repository, and it writes exactly one path:

    data/mercer_live/digest/<date>.json

The path is computed from --date, never taken from the candidate, so nothing
the capture host publishes can steer the write anywhere else. The candidate is
the file the capture host built with `archive.py build` and published to the
private digest bucket; this job never sees a raw observation.

    candidate absent                         -> MISSING, exit 1 (the red run IS the alert)
    candidate invalid / wrong date / open day-> REFUSED, exit 1
    nothing committed yet                    -> CREATED
    committed and equal (bar generated_at)   -> UNCHANGED, file untouched, exit 0
    committed and candidate only ADDS entries-> GROWN, file replaced (git keeps the old one)
    committed and any entry changed/missing  -> REFUSED-DIFFERS, file untouched, exit 1

Prints only the date, the action, counts and hashes. A digest is public once
committed, but the job log stays as quiet as every other Mercer Live job.
"""
import argparse
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archive                                                    # noqa: E402
import common                                                     # noqa: E402

DIGEST_DIR = os.path.join(common.DATA_DIR, common.DIGEST_SUBDIR)


def target_path(et_date, digest_dir=DIGEST_DIR):
    if not archive.DATE_RE.match(et_date or ""):
        raise archive.ArchiveError(f"bad date {et_date!r}")
    return os.path.join(digest_dir, f"{et_date}.json")


def decide(candidate_bytes, et_date, committed_bytes=None, now=None):
    """Pure decision. Returns (action, bytes_to_write_or_None, detail)."""
    try:
        cand = json.loads(candidate_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "REFUSED", None, "candidate is not JSON"
    probs = archive.validate(cand, et_date)
    if probs:
        return "REFUSED", None, "; ".join(probs[:5])
    if not archive.closed(et_date, now=now):
        return "REFUSED", None, f"{et_date} (ET) has not closed"
    # The committed file is always Store.digest's serialisation, so the host
    # cannot slip in a differently formatted (but equal) file either.
    canon = archive.serialise(cand).encode("utf-8")
    if canon != candidate_bytes:
        return "REFUSED", None, "candidate is not in canonical form (indent=1, sort_keys)"
    if committed_bytes is None:
        return "CREATED", canon, "first digest for this date"
    old = json.loads(committed_bytes.decode("utf-8"))
    ok, why = archive.supersedes(old, cand)
    if not ok:
        return "REFUSED-DIFFERS", None, "; ".join(why[:5])
    if archive.comparable(old) == archive.comparable(cand):
        return "UNCHANGED", None, "committed digest already covers this evidence"
    return "GROWN", canon, "new entries only; every committed entry unchanged"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--digest-dir", default=DIGEST_DIR)
    args = ap.parse_args(argv)
    try:
        path = target_path(args.date, args.digest_dir)
    except archive.ArchiveError as e:
        print(f"REFUSED {e}")
        return 1
    if not os.path.isfile(args.candidate):
        print(f"MISSING {args.date}: no published digest for this date")
        return 1
    with io.open(args.candidate, "rb") as f:
        cand = f.read()
    committed = None
    if os.path.exists(path):
        with io.open(path, "rb") as f:
            committed = f.read()
    action, body, detail = decide(cand, args.date, committed)
    if body is not None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with io.open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
    d = json.loads(cand.decode("utf-8")) if action not in ("REFUSED",) else {}
    print(f"{action} {args.date}: {detail} | shards={len(d.get('shards') or [])} "
          f"run_records={len(d.get('run_records') or [])} "
          f"candidate_sha256={hashlib.sha256(cand).hexdigest()[:16]}")
    return 0 if action in ("CREATED", "UNCHANGED", "GROWN") else 1


if __name__ == "__main__":
    sys.exit(main())
