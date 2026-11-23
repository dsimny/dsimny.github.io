"""Roster, transfer and recruiting priors from CollegeBasketballData (CBBD).

    CBBD_API_KEY=... python -m ncaa.cbbd_roster probe
    CBBD_API_KEY=... python -m ncaa.cbbd_roster features --seasons 2016-2027

Runs only inside the `NCAA model` workflow (the build container cannot reach
CBBD). Raw CBBD rows (player names, ids, per-player stats) stay in
data/cbbd_cache/ on the runner and are never committed, uploaded or printed.

What leaves the runner is ONE derived table, one row per (season, ESPN team):
`data/derived/team_priors.csv` - returning-minutes share, incoming-transfer
minutes and the quality of the teams they came from, and recruit-class
strength. These are team-level aggregates computed here (like the
returning-minutes figures sites publish), not CBBD's feed. The table is
printed to the job log as gzip+base64 so it can be committed and the whole
backtest re-run without a key.

Every feature for season s uses only information published before season s
starts: last season's player minutes, this season's roster, and the
recruiting class that enrolled. See `build_features` for the one known
caveat (roster snapshots are not timestamped).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import sys

import numpy as np
import pandas as pd

from . import config as C
from .cbbd import CACHE, _get, _key

CALLS = {"n": 0}


def _fetch(path, params, key, cache_name=None, cache=True):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{cache_name}.json" if cache_name else None
    if cache and f is not None and f.exists():
        return json.loads(f.read_text())
    CALLS["n"] += 1
    data = _get(path, params, key)
    if f is not None:
        f.write_text(json.dumps(data))
    return data


def _shape(x, depth=0, maxdepth=3):
    """Key structure only (no values), so the public log never carries data."""
    if depth > maxdepth:
        return "…"
    if isinstance(x, dict):
        return {k: _shape(v, depth + 1) for k, v in x.items()}
    if isinstance(x, list):
        return [_shape(x[0], depth + 1)] if x and isinstance(x[0], (dict, list)) else f"list[{len(x)}]"
    return type(x).__name__


def probe(key):
    s = 2025
    tries = [("/teams/roster", {"season": s}), ("/stats/player/season", {"season": s}),
             ("/recruiting/players", {"year": s - 1}), ("/recruiting/portal", {"year": s - 1}),
             ("/recruiting/portal", {"year": s}), ("/coaches", {"season": s}), ("/teams", {"season": s})]
    for path, params in tries:
        try:
            d = _fetch(path, params, key, cache=False)
        except SystemExit as e:
            print(f"{path} {params}: {e}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"{path} {params}: FAILED {type(e).__name__}")
            continue
        n = len(d) if isinstance(d, list) else 1
        print(f"\n== {path} {params}: {type(d).__name__} len={n}")
        if isinstance(d, list) and d:
            print(json.dumps(_shape(d[0]), indent=1))
            if isinstance(d[0], dict):
                # which fields are populated, as a share (no values)
                df = pd.json_normalize(d[:3000])
                print("non-null share:", {c: round(float(df[c].notna().mean()), 2) for c in df.columns})
    print("calls used:", CALLS["n"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe"])
    a = ap.parse_args()
    key = _key()
    if a.cmd == "probe":
        probe(key)


if __name__ == "__main__":
    main()
