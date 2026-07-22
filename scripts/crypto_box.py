#!/usr/bin/env python3
"""
Open Ledger Sports — commit-and-reveal helpers.

The morning run publishes two things for each day's board:

  data/board_<date>.enc   the board, encrypted, unreadable without the key
  data/commitments.json   the SHA-256 of the PLAINTEXT board, in the clear

Committing both before first pitch proves the picks existed and were not
edited afterwards. Publishing only the fingerprint in the clear means nobody
can read the picks in advance. After grading, the plaintext is revealed and
anyone can hash it themselves and compare against the fingerprint we
published that morning.

The key lives in the repo secret BOARD_ENCRYPTION_KEY. Generate one with
`python scripts/genkey.py` and keep a copy somewhere safe: without it, an
encrypted board can never be graded or revealed.
"""
import hashlib, json, os

from cryptography.fernet import Fernet, InvalidToken

ENV_KEY = "BOARD_ENCRYPTION_KEY"


class KeyMissing(RuntimeError):
    """No encryption key available. Never silently continue past this."""


def _fernet():
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise KeyMissing(
            f"{ENV_KEY} is not set. The board cannot be encrypted or decrypted "
            f"without it. Generate one with `python scripts/genkey.py` and save "
            f"it as a repository secret."
        )
    try:
        return Fernet(key.encode())
    except Exception as exc:                     # malformed key
        raise KeyMissing(f"{ENV_KEY} is not a valid Fernet key: {exc}") from exc


def sha256_of(obj):
    """Fingerprint a board/snapshot dict.

    Hashes the same canonical bytes we encrypt, so a reader who decrypts (or
    reads the revealed file) recomputes exactly this digest.
    """
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def canonical_bytes(obj):
    """One fixed serialisation, so the hash is reproducible anywhere."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt_to(path, obj):
    """Write obj to `path` encrypted. Returns the plaintext fingerprint."""
    data = canonical_bytes(obj)
    with open(path, "wb") as f:
        f.write(_fernet().encrypt(data))
    return hashlib.sha256(data).hexdigest()


def decrypt_from(path):
    """Read an encrypted file back to a dict.

    Raises rather than returning partial data: a failure here must stop the
    grading run, never let it write a half-truth to the ledger.
    """
    with open(path, "rb") as f:
        blob = f.read()
    try:
        plain = _fernet().decrypt(blob)
    except InvalidToken as exc:
        raise RuntimeError(
            f"{path} could not be decrypted. Either {ENV_KEY} is the wrong key "
            f"or the file was altered after it was written."
        ) from exc
    return json.loads(plain)


def already_published(root, date):
    """Has this date's board already been built and committed?

    The board runs on several cron windows because GitHub's scheduler is
    unreliable; whichever fires first wins and the rest must do nothing. Re-
    fetching would also overwrite the snapshot whose hash is already recorded
    in the commitment, which would break the reveal check months later.
    """
    if commitment_for(root, date) is not None:
        return True
    plain_path, enc_path = paths_for(root, "board", date)
    return os.path.exists(plain_path) or os.path.exists(enc_path)


def have_key():
    return bool(os.environ.get(ENV_KEY, "").strip())


def refuse_plaintext_in_ci(what):
    """Guard: never publish picks in the clear from an automated run.

    Locally, a missing key just means plaintext files, which keeps development
    simple. In Actions it means the secret went missing, and writing the board
    unencrypted would quietly hand away every pick. Fail the run instead.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true" and not have_key():
        raise SystemExit(
            f"REFUSING to write {what} unencrypted from CI: {ENV_KEY} is not set. "
            f"Check the repository secret. No picks have been published."
        )


def paths_for(root, kind, date):
    base = os.path.join(root, "data", f"{kind}_{date}")
    return base + ".json", base + ".enc"


def save_dataset(root, kind, date, obj):
    """Write board/snapshot encrypted when a key is present, plaintext when not.

    Returns (path_written, sha256_of_plaintext, encrypted?).
    """
    refuse_plaintext_in_ci(f"{kind}_{date}")
    plain_path, enc_path = paths_for(root, kind, date)
    if have_key():
        sha = encrypt_to(enc_path, obj)
        # A stale plaintext from an earlier local run would leak the picks.
        if os.path.exists(plain_path):
            os.remove(plain_path)
        return enc_path, sha, True
    with open(plain_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    return plain_path, sha256_of(obj), False


def load_dataset(root, kind, date):
    """Read a board/snapshot, whether it was left plaintext or encrypted."""
    plain_path, enc_path = paths_for(root, kind, date)
    if os.path.exists(plain_path):
        with open(plain_path, encoding="utf-8") as f:
            return json.load(f)
    if os.path.exists(enc_path):
        return decrypt_from(enc_path)
    return None


def record_commitment(root, date, board_sha, snapshot_sha, committed_utc):
    """Append today's fingerprints to the public, append-only commitment log.

    Mirrors the ledger's rule: entries are added, never edited. If a date is
    already present we leave the original alone, so a re-run can never rewrite
    a commitment that was already published.
    """
    path = os.path.join(root, "data", "commitments.json")
    log = {"commitments": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            log = json.load(f)
    if any(c["date"] == date for c in log["commitments"]):
        return path, False
    log["commitments"].append({
        "date": date,
        "board_sha256": board_sha,
        "snapshot_sha256": snapshot_sha,
        "committed_utc": committed_utc,
        "revealed": False,
    })
    log["commitments"].sort(key=lambda c: c["date"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    return path, True


def mark_revealed(root, date):
    """Flip a commitment to revealed once the plaintext is published."""
    path = os.path.join(root, "data", "commitments.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        log = json.load(f)
    for c in log["commitments"]:
        if c["date"] == date:
            c["revealed"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)


def commitment_for(root, date):
    """The published fingerprints for a date, or None."""
    path = os.path.join(root, "data", "commitments.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        log = json.load(f)
    return next((c for c in log["commitments"] if c["date"] == date), None)
