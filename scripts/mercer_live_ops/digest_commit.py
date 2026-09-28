#!/usr/bin/env python3
"""
Open Ledger Sports - ML-1-OPS (mercer-live-ml1-ops-v1.0): place ONE day's
frozen ML-1 digest and its ML-1-OPS manifest in the repository, together.

    python scripts/mercer_live_ops/digest_commit.py --date YYYY-MM-DD \
        --candidate-digest FILE --candidate-manifest FILE

Runs inside .github/workflows/mercer-live-digest.yml. It writes exactly two
paths, both computed from --date and never from the candidates:

    data/mercer_live/digest/<date>.json      frozen ml1-v1 digest, byte-for-byte as published
    data/mercer_live/manifest/<date>.json    ml1-ops-manifest-v1, bound to that digest's SHA-256

The workflow then stages exactly those two paths in ONE git commit; that
commit is the pairing. Nothing is written unless BOTH pass:

    either candidate absent                        -> MISSING, exit 1
    invalid, open day, non-canonical, bad pairing  -> REFUSED, exit 1
    nothing committed yet                          -> CREATED (both)
    committed pair and candidate pair identical    -> UNCHANGED, nothing written, exit 0
    candidate pair only ADDS evidence              -> GROWN (both replaced; git keeps the old)
    any committed entry changed / missing / shrunk -> REFUSED-DIFFERS, nothing written, exit 1

A committed pair that is itself inconsistent (one file without the other, or a
manifest bound to a different digest) is also REFUSED: it needs a human.

Prints the date, the action, counts and hashes only.
"""
import argparse
import hashlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import archive                                                    # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def targets(et_date, root=ROOT):
    if not archive.DATE_RE.match(et_date or ""):
        raise archive.ArchiveError(f"bad date {et_date!r}")
    return (os.path.join(root, *archive.digest_path(et_date).split("/")),
            os.path.join(root, *archive.manifest_path(et_date).split("/")))


def decide(cand_digest, cand_manifest, et_date, committed=None, now=None):
    """Pure decision. `committed` is None or (digest_bytes|None, manifest_bytes|None).
    Returns (action, (digest_bytes, manifest_bytes) or None, detail)."""
    try:
        man = json.loads(cand_manifest.decode("utf-8"))
        dig = json.loads(cand_digest.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "REFUSED", None, "a candidate is not JSON"
    probs = archive.validate_digest(dig, et_date) + archive.validate_manifest(man, et_date)
    if probs:
        return "REFUSED", None, "; ".join(probs[:5])
    if not archive.closed(et_date, now=now):
        return "REFUSED", None, f"{et_date} (ET) has not closed"
    if archive.serialise(dig) != cand_digest or archive.serialise(man) != cand_manifest:
        return "REFUSED", None, "a candidate is not in canonical form (indent=1, sort_keys)"
    pair = archive.check_pair(cand_digest, man)
    if pair:
        return "REFUSED", None, "pairing: " + "; ".join(pair[:5])
    if committed is None or committed == (None, None):
        return "CREATED", (cand_digest, cand_manifest), "first digest+manifest for this date"
    old_d, old_m = committed
    if old_d is None or old_m is None:
        return "REFUSED", None, "the committed pair is incomplete (one file without the other) - needs a human"
    old_man = json.loads(old_m.decode("utf-8"))
    if archive.check_pair(old_d, old_man):
        return "REFUSED", None, "the committed pair is inconsistent - needs a human"
    if old_d == cand_digest and old_m == cand_manifest:
        return "UNCHANGED", None, "committed pair already covers this evidence"
    ok, why = archive.supersedes_pair(json.loads(old_d.decode("utf-8")), old_man, dig, man)
    if not ok:
        return "REFUSED-DIFFERS", None, "; ".join(why[:5])
    if archive.comparable(json.loads(old_d.decode("utf-8"))) == archive.comparable(dig) \
            and archive.comparable(old_man) == archive.comparable(man):
        # Same evidence, re-stamped: keep the committed bytes (idempotent).
        return "UNCHANGED", None, "same evidence, different generated_at - committed pair kept"
    return "GROWN", (cand_digest, cand_manifest), "new evidence only; every committed entry unchanged"


def _read(p):
    if not os.path.isfile(p):
        return None
    with io.open(p, "rb") as f:
        return f.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", required=True)
    ap.add_argument("--candidate-digest", required=True)
    ap.add_argument("--candidate-manifest", required=True)
    ap.add_argument("--root", default=ROOT, help="repository root (tests only)")
    args = ap.parse_args(argv)
    try:
        dpath, mpath = targets(args.date, args.root)
    except archive.ArchiveError as e:
        print(f"REFUSED {e}")
        return 1
    cd, cm = _read(args.candidate_digest), _read(args.candidate_manifest)
    if cd is None or cm is None:
        print(f"MISSING {args.date}: published digest={'yes' if cd else 'no'} manifest={'yes' if cm else 'no'}")
        return 1
    committed = (_read(dpath), _read(mpath))
    action, bodies, detail = decide(cd, cm, args.date, committed)
    if bodies is not None:
        # Both or neither: write to temp names first, then rename both.
        tmps = []
        for path, body in zip((dpath, mpath), bodies):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with io.open(tmp, "wb") as f:
                f.write(body)
            tmps.append((tmp, path))
        for tmp, path in tmps:
            os.replace(tmp, path)
    print(f"{action} {args.date}: {detail} | digest_sha256={hashlib.sha256(cd).hexdigest()[:16]} "
          f"manifest_sha256={hashlib.sha256(cm).hexdigest()[:16]}")
    return 0 if action in ("CREATED", "UNCHANGED", "GROWN") else 1


if __name__ == "__main__":
    sys.exit(main())
