"""Data ingestion: sportsdataverse ESPN team box scores, schedules, and the
ESPN `pickcenter` betting block.

    python -m ncaa.ingest                 # box scores + schedules, all seasons
    python -m ncaa.ingest --odds 2026     # betting lines for one season
    python -m ncaa.ingest --build         # (re)build the games table + DuckDB

Nothing here invents data. A game with no betting block gets no line; a
team-game with a missing stat stays missing and is flagged by validate.py.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import functools
import gzip
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import config as C

UA = {"User-Agent": "ncaa-model research (open data; contact via repo)"}


# --------------------------------------------------------------------------- download
def _download(url: str, dest: Path, retries: int = 4) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for i in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=120)
            if r.status_code == 200:
                dest.write_bytes(r.content)
                return dest
            if r.status_code == 404:
                raise FileNotFoundError(url)
        except requests.RequestException:
            pass
        time.sleep(2 ** i)
    raise RuntimeError(f"download failed after {retries} tries: {url}")


def download_season(season: int, force: bool = False) -> None:
    for tmpl, name in ((C.TEAM_BOX_URL, "team_box"), (C.SCHEDULE_URL, "mbb_schedule")):
        dest = C.RAW / f"{name}_{season}.parquet"
        # The current season changes daily; always refresh it.
        if force or season == C.CURRENT_SEASON or not dest.exists():
            _download(tmpl.format(season=season), dest)


# --------------------------------------------------------------------------- odds
def _num(x):
    if x is None:
        return np.nan
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().lower().lstrip("ou")
    if s in ("", "even", "ev"):
        return 100.0 if s else np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_pickcenter(game_id: int, doc: dict) -> list[dict]:
    """Flatten ESPN's pickcenter list into one row per provider.

    `spread` is ESPN's home-team line (negative = home favoured); this is
    verified against final margins in validate.check_odds_sign().
    """
    rows = []
    comp = (doc.get("header") or {}).get("competitions") or [{}]
    neutral = comp[0].get("neutralSite")
    for p in doc.get("pickcenter") or []:
        h = p.get("homeTeamOdds") or {}
        a = p.get("awayTeamOdds") or {}
        ps = p.get("pointSpread") or {}
        tot = p.get("total") or {}
        ml = p.get("moneyline") or {}
        g = lambda d, *ks: _num(_dig(d, *ks))
        rows.append(dict(
            game_id=int(game_id),
            provider=(p.get("provider") or {}).get("name"),
            details=p.get("details"),
            spread=_num(p.get("spread")),
            total=_num(p.get("overUnder")),
            home_ml=_num(h.get("moneyLine")), away_ml=_num(a.get("moneyLine")),
            home_spread_odds=_num(h.get("spreadOdds")), away_spread_odds=_num(a.get("spreadOdds")),
            over_odds=_num(p.get("overOdds")), under_odds=_num(p.get("underOdds")),
            # open / close (DraftKings era only; NaN before)
            spread_open=g(ps, "home", "open", "line"), spread_close=g(ps, "home", "close", "line"),
            home_spread_odds_open=g(ps, "home", "open", "odds"),
            away_spread_odds_open=g(ps, "away", "open", "odds"),
            total_open=g(tot, "over", "open", "line"), total_close=g(tot, "over", "close", "line"),
            over_odds_open=g(tot, "over", "open", "odds"), under_odds_open=g(tot, "under", "open", "odds"),
            home_ml_open=g(ml, "home", "open", "odds"), away_ml_open=g(ml, "away", "open", "odds"),
            home_ml_close=g(ml, "home", "close", "odds"), away_ml_close=g(ml, "away", "close", "odds"),
            json_neutral_site=neutral,
        ))
    return rows


def _dig(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _fetch_one(game_id: int, session: requests.Session) -> tuple[int, list[dict] | None]:
    url = C.GAME_JSON_URL.format(game_id=game_id)
    for i in range(3):
        try:
            r = session.get(url, timeout=60)  # requests sends Accept-Encoding: gzip
            if r.status_code == 404:
                return game_id, None
            if r.status_code == 200:
                return game_id, parse_pickcenter(game_id, r.json())
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1 + i)
    return game_id, None


def fetch_odds(season: int, workers: int = 24) -> pd.DataFrame:
    """Fetch the betting block for every COMPLETED game of a season.

    Resumable: progress is appended to a gzip JSONL; finished seasons are
    written to data/odds_cache/odds_<season>.parquet.
    """
    C.ODDS_CACHE.mkdir(parents=True, exist_ok=True)
    out = C.ODDS_CACHE / f"odds_{season}.parquet"
    log = C.ODDS_CACHE / f"odds_{season}.jsonl.gz"
    sched = pd.read_parquet(C.RAW / f"mbb_schedule_{season}.parquet")
    ids = sched.loc[sched.status_type_completed == True, "game_id"].astype(int).unique().tolist()  # noqa: E712
    done: set[int] = set()
    if log.exists():
        with gzip.open(log, "rt") as f:
            for line in f:
                done.add(json.loads(line)["game_id"])
    todo = [g for g in ids if g not in done]
    print(f"odds {season}: {len(ids)} completed games, {len(todo)} to fetch")
    s = requests.Session()
    s.headers.update(UA)
    with gzip.open(log, "at") as f, cf.ThreadPoolExecutor(workers) as ex:
        for n, (gid, rows) in enumerate(ex.map(lambda g: _fetch_one(g, s), todo)):
            f.write(json.dumps({"game_id": gid, "rows": rows}) + "\n")
            if n % 1000 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
    rows = []
    with gzip.open(log, "rt") as f:
        for line in f:
            rec = json.loads(line)
            rows.extend(rec["rows"] or [])
    df = pd.DataFrame(rows)
    if not df.empty:
        df["season"] = season
        df.to_parquet(out, index=False)
    return df


def load_odds(seasons=None) -> pd.DataFrame:
    files = sorted(C.ODDS_CACHE.glob("odds_*.parquet"))
    dfs = [pd.read_parquet(f) for f in files]
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    if seasons is not None:
        df = df[df.season.isin(seasons)]
    return df


# --------------------------------------------------------------------------- build
BOX_STATS = ["field_goals_made", "field_goals_attempted", "three_point_field_goals_made",
             "three_point_field_goals_attempted", "free_throws_made", "free_throws_attempted",
             "offensive_rebounds", "defensive_rebounds", "total_turnovers", "turnovers"]


def possessions(fga, oreb, tov, fta):
    """Standard possession estimate (KenPom/Oliver, FTA coefficient 0.475)."""
    return fga - oreb + tov + 0.475 * fta


def build_games(seasons=None) -> pd.DataFrame:
    """One row per completed game: home/away identity, neutral flag, scores,
    four-factor raw counts for both sides, possessions, conference context.

    'home' follows ESPN's designation; on neutral courts it carries no edge
    (neutral=True) and the models treat it as such.
    """
    seasons = seasons or list(range(C.FIRST_SEASON, C.CURRENT_SEASON + 1))
    out = []
    for y in seasons:
        box = pd.read_parquet(C.RAW / f"team_box_{y}.parquet")
        sch = pd.read_parquet(C.RAW / f"mbb_schedule_{y}.parquet")
        box = box[box.team_home_away.isin(["home", "away"])]
        keep = ["game_id", "team_id", "team_score"] + BOX_STATS
        h = box[box.team_home_away == "home"][keep].add_prefix("h_").rename(columns={"h_game_id": "game_id"})
        a = box[box.team_home_away == "away"][keep].add_prefix("a_").rename(columns={"a_game_id": "game_id"})
        g = h.merge(a, on="game_id", how="inner")
        s = sch[["game_id", "season", "season_type", "game_date", "game_date_time", "neutral_site",
                 "conference_competition", "home_id", "away_id", "home_score", "away_score",
                 "home_conference_id", "away_conference_id", "home_short_display_name",
                 "away_short_display_name", "groups_short_name", "groups_is_conference",
                 "tournament_id", "notes_headline", "status_type_completed"]].drop_duplicates("game_id")
        g = g.merge(s, on="game_id", how="left", validate="one_to_one")
        out.append(g)
    g = pd.concat(out, ignore_index=True)
    g["game_date"] = pd.to_datetime(g["game_date"])
    # Prefer the turnover column that counts team turnovers.
    for side in ("h", "a"):
        tov = g[f"{side}_total_turnovers"].where(g[f"{side}_total_turnovers"].notna(), g[f"{side}_turnovers"])
        g[f"{side}_tov"] = tov
        g[f"{side}_poss_est"] = possessions(g[f"{side}_field_goals_attempted"], g[f"{side}_offensive_rebounds"],
                                            tov, g[f"{side}_free_throws_attempted"])
    g["poss"] = (g.h_poss_est + g.a_poss_est) / 2
    g["h_pts"] = g.h_team_score.astype(float)
    g["a_pts"] = g.a_team_score.astype(float)
    g["margin"] = g.h_pts - g.a_pts
    g["total_pts"] = g.h_pts + g.a_pts
    g["neutral_site"] = g.neutral_site.fillna(False).astype(bool)
    g = g.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    g["d1_game"] = _d1_flags(g, seasons)
    return g


def _d1_flags(g: pd.DataFrame, seasons) -> pd.Series:
    """A team counts as Division I in a season if it has >= half the typical game count on that
    season's FULL schedule (scheduled + played). Non-D1 opponents appear on
    ESPN's D1 schedules only a handful of times. Using the full schedule
    (published before the season) keeps the flag free of outcome leakage."""
    d1 = {}
    for y in seasons:
        sch = pd.read_parquet(C.RAW / f"mbb_schedule_{y}.parquet")
        d1[y] = d1_teams(y)
    return g.apply(lambda r: (r.h_team_id in d1[r.season]) and (r.a_team_id in d1[r.season]), axis=1)


@functools.lru_cache(maxsize=None)
def d1_teams(season: int) -> frozenset[int]:
    sch = pd.read_parquet(C.RAW / f"mbb_schedule_{season}.parquet", columns=["home_id", "away_id"])
    cnt = pd.concat([sch.home_id, sch.away_id]).value_counts()
    # Half the typical schedule length: 2021 (COVID) teams played ~22 games,
    # normal seasons ~31; non-D1 visitors appear 1-5 times.
    thresh = max(8, 0.5 * cnt[cnt >= 10].median())
    return frozenset(cnt[cnt >= thresh].index.astype(int))


def team_names() -> dict[int, str]:
    names = {}
    for f in sorted(C.RAW.glob("mbb_schedule_*.parquet")):
        s = pd.read_parquet(f, columns=["home_id", "home_short_display_name", "away_id", "away_short_display_name"])
        names.update(dict(zip(s.home_id.astype(int), s.home_short_display_name)))
        names.update(dict(zip(s.away_id.astype(int), s.away_short_display_name)))
    return names


def write_db(games: pd.DataFrame, odds: pd.DataFrame | None = None) -> None:
    import duckdb
    C.DATA.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(C.DB_PATH))
    con.execute("CREATE OR REPLACE TABLE games AS SELECT * FROM games")
    if odds is not None and not odds.empty:
        con.execute("CREATE OR REPLACE TABLE odds AS SELECT * FROM odds")
    con.close()
    games.to_parquet(C.DATA / "games.parquet", index=False)


def load_games() -> pd.DataFrame:
    return pd.read_parquet(C.DATA / "games.parquet")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--odds", nargs="*", type=int, help="seasons to fetch betting lines for")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.odds is None and not a.build:
        for y in range(C.FIRST_SEASON, C.CURRENT_SEASON + 1):
            download_season(y, a.force)
            print("downloaded", y)
        a.build = True
    for y in a.odds or []:
        fetch_odds(y)
    if a.build:
        g = build_games()
        write_db(g, load_odds())
        print(f"games table: {len(g)} rows, {g.d1_game.sum()} D1-vs-D1")


if __name__ == "__main__":
    main()
