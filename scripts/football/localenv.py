#!/usr/bin/env python3
"""
Open Ledger Sports — local secret loading for football scripts (fb-v0.1).

CI supplies ODDS_API_KEY from repo secrets. Local runs need it too, and the two
bad ways to do that are pasting the key into a chat or a command line (where it
lands in scrollback, shell history and transcripts) and hardcoding it in a
script (where it lands in git). So: an untracked `.env.local` at the repo root,
read into the environment at import time.

RULES THIS FILE ENFORCES:
  - .env.local is gitignored. Committing a key is the failure mode that matters.
  - A value already in the real environment always WINS. CI must never be
    silently overridden by a stray local file.
  - Nothing here ever prints, logs or returns a secret value. The only thing
    reported is which NAMES were loaded, never what they contain.

Usage:  import localenv; localenv.load()
"""
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
ENV_FILE = os.path.join(ROOT, ".env.local")

# Names this project expects to find. Anything else in .env.local is ignored
# rather than loaded, so the file cannot quietly set PATH or similar.
ALLOWED = {"ODDS_API_KEY", "ODDS_MARKETS", "ODDS_REGIONS"}


def load(verbose=True):
    """Populate os.environ from .env.local. Returns the names loaded."""
    if not os.path.exists(ENV_FILE):
        return []
    loaded, skipped = [], []
    with open(ENV_FILE, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name not in ALLOWED:
                skipped.append(name)
                continue
            if os.environ.get(name):
                continue          # the real environment wins, always
            if value and not value.startswith("<"):   # ignore the placeholder
                os.environ[name] = value
                loaded.append(name)
    if verbose and loaded:
        print(f"loaded from .env.local: {', '.join(sorted(loaded))}  (values not shown)")
    if verbose and skipped:
        print(f"ignored unexpected name(s) in .env.local: {', '.join(sorted(set(skipped)))}")
    return loaded


def require(name):
    """Fetch a required secret, with an actionable message and no leakage."""
    load(verbose=False)
    v = os.environ.get(name)
    if not v:
        raise SystemExit(
            f"{name} is not set.\n"
            f"  Put it in {os.path.relpath(ENV_FILE, ROOT)} as  {name}=your-key-here\n"
            f"  (that file is gitignored), or export it in your shell.\n"
            f"  Do not paste it into a chat or a commit."
        )
    return v


def fingerprint(value):
    """A stable, non-reversible tag for a secret, so two keys can be COMPARED
    (did the upgrade change my key?) without either being displayed."""
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:12]
