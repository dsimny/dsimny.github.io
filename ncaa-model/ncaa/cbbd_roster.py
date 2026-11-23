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
returning-minutes figures sites publish), not CBBD's feed: no player names
or ids. The workflow commits that one file to the PR branch so the whole
backtest can be re-run without a key, and later runs reuse it instead of
spending API calls (`--refresh` rebuilds it).

Every feature for season s uses only information published before season s
starts: last season's player minutes, this season's roster, and the
recruiting class that enrolled. See `build_features` for the one known
caveat (roster snapshots are not timestamped).
"""
from __future__ import annotations

import argparse
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


def _seasons_list(d):
    return d if isinstance(d, list) else []


def fetch_all(key, seasons):
    """Rosters for seasons, player stats for seasons-1, recruiting classes for
    seasons-1. Cached on the runner only."""
    ros, st, rec = {}, {}, {}
    for s in seasons:
        ros[s] = _seasons_list(_fetch("/teams/roster", {"season": s}, key, f"roster_{s}"))
        st[s - 1] = _seasons_list(_fetch("/stats/player/season", {"season": s - 1}, key, f"pstats_{s - 1}"))
        rec[s - 1] = _seasons_list(_fetch("/recruiting/players", {"year": s - 1}, key, f"recruits_{s - 1}"))
        print(f"  season {s}: roster teams {len(ros[s])}, player-stat rows (s-1) {len(st[s - 1])}, "
              f"recruits (class {s - 1}) {len(rec[s - 1])}", flush=True)
        if len(st[s - 1]) in (1000, 2000, 2500, 3000, 5000, 10000):
            print(f"::warning::player stats {s - 1} returned {len(st[s - 1])} rows - possibly capped")
    return ros, st, rec


def build_features(ros, st, rec, sr: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per (season, ESPN team id). sr = data-only season ratings
    (ncaa.priors.season_ratings) on ESPN ids, used for transfer source quality.

    CAVEAT (documented in MODEL_LOG): CBBD rosters are season rosters without
    a capture date, so a player who joined mid-season can appear. Minutes and
    production come from LAST season only, so no current-season outcome
    enters a feature, but roster membership itself is not timestamped.
    """
    # CBBD team id -> ESPN id (CBBD's sourceId is ESPN's id)
    tmap = {}
    for s, teams in ros.items():
        for t in teams:
            try:
                tmap[int(t["teamId"])] = int(t["teamSourceId"])
            except (TypeError, ValueError, KeyError):
                pass
    srx = sr.set_index(["season", "team_id"])
    rows, diag = [], dict(id_overlap=[], unmapped_teams=0)
    for s in sorted(ros):
        prev = pd.json_normalize(st.get(s - 1, []))
        if prev.empty:
            continue
        prev = prev[["teamId", "athleteId", "minutes", "PORPAG", "winShares.total"]].rename(
            columns={"winShares.total": "ws"})
        prev["minutes"] = prev.minutes.fillna(0).astype(float)
        prev["PORPAG"] = prev.PORPAG.fillna(0).astype(float)
        prev["ws"] = prev.ws.fillna(0).astype(float)
        team_min = prev.groupby("teamId").minutes.sum()
        team_ws = prev.groupby("teamId").ws.sum()
        team_por = prev.groupby("teamId").PORPAG.apply(lambda v: v.clip(lower=0).sum())
        # athlete -> (team, minutes, porpag, ws) last season; a player on two
        # teams last season keeps his larger-minutes stint
        pl = prev.sort_values("minutes").drop_duplicates("athleteId", keep="last").set_index("athleteId")
        roster_ids = set()
        rc = pd.json_normalize(rec.get(s - 1, []))
        for t in ros[s]:
            tid = int(t["teamId"])
            espn = tmap.get(tid)
            if espn is None:
                diag["unmapped_teams"] += 1
                continue
            players = [p_["id"] for p_ in (t.get("players") or []) if p_.get("id") is not None]
            roster_ids.update(players)
            on = pl.reindex(players).dropna(subset=["teamId"])
            same = on[on.teamId == tid]
            other = on[on.teamId != tid]
            tm = float(team_min.get(tid, np.nan))
            r = dict(season=s, team_id=espn, roster_n=len(players))
            if tm and tm > 0:
                r["ret_min"] = float(same.minutes.sum() / tm)
                tw = float(team_ws.get(tid, 0))
                r["ret_ws"] = float(same.ws.sum() / tw) if tw > 0 else np.nan
                tp = float(team_por.get(tid, 0))
                r["ret_porpag"] = float(same.PORPAG.clip(lower=0).sum() / tp) if tp > 0 else np.nan
                r["tr_in_min"] = float(other.minutes.sum() / tm)
            r["tr_in_n"] = int(len(other))
            r["tr_in_porpag"] = float(other.PORPAG.sum())
            r["tr_in_ws"] = float(other.ws.sum())
            r["ret_abs_porpag"] = float(same.PORPAG.sum())
            # quality of the teams the incoming players came from (minutes-weighted)
            if len(other) and other.minutes.sum() > 0:
                src = [srx.loc[(s - 1, tmap[int(a)])] if (s - 1, tmap.get(int(a), -1)) in srx.index else None
                       for a in other.teamId]
                ok = [i for i, v in enumerate(src) if v is not None]
                if ok:
                    w = other.minutes.values[ok]
                    r["tr_in_src_off"] = float(np.average([src[i]["off"] for i in ok], weights=w))
                    r["tr_in_src_dfn"] = float(np.average([src[i]["dfn"] for i in ok], weights=w))
                    r["tr_in_src_share"] = float(w.sum() / other.minutes.sum())
            # recruiting class that enrolled for season s
            if not rc.empty and "committedTo.id" in rc:
                mine = rc[rc["committedTo.id"] == tid]
                r["rec_n"] = int(len(mine))
                r["rec_top50"] = int((mine.ranking <= 50).sum())
                # rank-based points: #1 -> 1.0, #300 -> ~0
                r["rec_score"] = float(np.clip(1 - (mine.ranking - 1) / 300, 0, 1).sum())
            rows.append(r)
        diag["id_overlap"].append((s, round(float(prev.athleteId.isin(roster_ids).mean()), 3)))
    return pd.DataFrame(rows), diag


def features(key, seasons):
    from .ingest import load_games
    from .priors import TEAM_PRIORS_CSV, season_ratings
    from .backtest import load_params

    p, _ = load_params()
    games = load_games()
    sr = season_ratings(games, p, seasons=range(min(seasons) - 1, max(seasons) + 1))
    ros, st, rec = fetch_all(key, seasons)
    df, diag = build_features(ros, st, rec, sr)
    # keep D1 teams with ESPN ratings only
    d1 = set(zip(sr.season, sr.team_id)) | {(s, t) for s in seasons for t in
                                             __import__("ncaa.ingest", fromlist=["d1_teams"]).d1_teams(s)}
    df = df[[(a, b) in d1 for a, b in zip(df.season, df.team_id)]]
    df = df.drop_duplicates(["season", "team_id"]).sort_values(["season", "team_id"])
    TEAM_PRIORS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(TEAM_PRIORS_CSV, index=False, float_format="%.4f")
    print("CBBD calls used:", CALLS["n"])
    print("unmapped CBBD teams:", diag["unmapped_teams"])
    print("share of last-season player-stat rows whose athlete id appears on SOME roster:", diag["id_overlap"])
    summ = df.groupby("season").agg(teams=("team_id", "size"), ret_min=("ret_min", "mean"),
                                    tr_in_min=("tr_in_min", "mean"), tr_in_n=("tr_in_n", "mean"),
                                    rec_n=("rec_n", "sum"), roster_n=("roster_n", "mean"))
    print(summ.round(3).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["probe", "features"])
    ap.add_argument("--seasons", default="2016-2027")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    from .priors import TEAM_PRIORS_CSV
    if a.cmd == "features" and TEAM_PRIORS_CSV.exists() and not a.refresh:
        print(f"{TEAM_PRIORS_CSV.name} is committed; reusing it (no CBBD calls). Pass --refresh to rebuild.")
        return
    key = _key()
    if a.cmd == "probe":
        probe(key)
    else:
        lo, hi = (int(v) for v in a.seasons.split("-"))
        features(key, list(range(lo, hi + 1)))


if __name__ == "__main__":
    main()
