#!/usr/bin/env python3
"""
Open Ledger Sports — play-by-play -> per-team-game efficiency (fb-v0.1).

The ridge model needs efficiency, not scores, and efficiency lives in the
play-by-play. This script does the expensive part ONCE per season and writes a
small aggregate the model can read cheaply: one row per team per game, carrying
EPA and play counts on offence.

WHY NO NEW DEPENDENCY. nflverse publishes play-by-play as both parquet and
csv.gz. Parquet would mean adding pyarrow to a project whose entire dependency
list is five packages, for a script that runs weekly at most. Streaming the
csv.gz through stdlib gzip + csv costs about 2 seconds a season, so the heavy
dependency buys nothing. This script is also NOT part of the daily pipeline.

A DOCUMENTED DEVIATION FROM THE PRE-REGISTRATION. Section 5 says every raw
artifact is stored content-addressed with a manifest. Play-by-play is ~20MB per
season, ~300MB across the window, which does not belong in a repo that GitHub
Pages serves. So the compromise, stated rather than quietly taken:
  - the raw seasons are cached under data/football/raw/pbp/ and GITIGNORED
  - the sha256 of each season's bytes IS committed, in the manifest
which means we cannot reproduce from the repo alone, but we CAN detect that
nflverse rebuilt a season under us - which is the failure that would actually
corrupt a fit. Re-running with --refresh re-downloads and re-checks the shas.

WHAT COUNTS AS A PLAY. Scrimmage plays only - passes and rushes with a non-null
EPA. Special teams, kneels, spikes and penalty-only rows are excluded, because
an offence's efficiency should not be diluted by its punt team. That choice is
recorded here because it is a modelling decision, not a data-cleaning detail.

Run: python scripts/football/pbp_aggregate.py [--seasons 2010-2024] [--refresh]
"""
import argparse, csv, gzip, hashlib, io, json, os, sys, time

import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
FB = os.path.join(ROOT, "data", "football")
CACHE = os.path.join(FB, "raw", "pbp")
OUTFILE = os.path.join(FB, "team_game_efficiency.csv")
MANIFEST = os.path.join(FB, "pbp_manifest.json")
URL = ("https://github.com/nflverse/nflverse-data/releases/download/pbp/"
       "play_by_play_{season}.csv.gz")

NEEDED = ["game_id", "season", "week", "season_type", "posteam", "defteam",
          "home_team", "away_team", "epa", "pass", "rush", "play_type"]


def season_blob(season, refresh=False):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"play_by_play_{season}.csv.gz")
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as f:
            return f.read(), path, True
    r = requests.get(URL.format(season=season), timeout=300)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return r.content, path, False


def aggregate_season(blob, season):
    """Stream the season, returning {(game_id, team): row}."""
    stream = io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(blob)),
                              encoding="utf-8")
    rdr = csv.reader(stream)
    hdr = next(rdr)
    missing = [c for c in NEEDED if c not in hdr]
    if missing:
        raise SystemExit(
            f"season {season}: nflverse play-by-play is missing column(s) "
            f"{missing}. Refusing to aggregate against a schema we do not "
            "recognise rather than silently producing a different metric.")
    ix = {c: hdr.index(c) for c in NEEDED}

    out, skipped = {}, {"no_epa": 0, "not_scrimmage": 0, "no_posteam": 0}
    for row in rdr:
        pos = row[ix["posteam"]]
        if not pos:
            skipped["no_posteam"] += 1
            continue
        # Scrimmage plays only. nflverse's pass/rush indicators are 1/0.
        if row[ix["pass"]] != "1" and row[ix["rush"]] != "1":
            skipped["not_scrimmage"] += 1
            continue
        epa = row[ix["epa"]]
        if epa in ("", "NA"):
            skipped["no_epa"] += 1
            continue
        gid = row[ix["game_id"]]
        key = (gid, pos)
        rec = out.get(key)
        if rec is None:
            rec = out[key] = {
                "game_id": gid, "season": row[ix["season"]],
                "week": row[ix["week"]], "season_type": row[ix["season_type"]],
                "team": pos, "opponent": row[ix["defteam"]],
                "is_home": "1" if pos == row[ix["home_team"]] else "0",
                "n_plays": 0, "epa_sum": 0.0,
            }
        rec["n_plays"] += 1
        rec["epa_sum"] += float(epa)
    return out, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2010-2024",
                    help="inclusive range, e.g. 2010-2024")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download even if cached, and re-check the shas")
    args = ap.parse_args()

    a, _, b = args.seasons.partition("-")
    seasons = list(range(int(a), int(b or a) + 1))

    manifest = {"seasons": {}}
    if os.path.exists(MANIFEST):
        with io.open(MANIFEST, encoding="utf-8") as f:
            manifest = json.load(f)

    rows, drift = [], []
    for s in seasons:
        t0 = time.time()
        blob, path, cached = season_blob(s, refresh=args.refresh)
        sha = hashlib.sha256(blob).hexdigest()
        prev = manifest["seasons"].get(str(s), {}).get("sha256")
        if prev and prev != sha:
            drift.append(s)
        agg, skipped = aggregate_season(blob, s)
        rows.extend(agg.values())
        manifest["seasons"][str(s)] = {
            "sha256": sha, "bytes": len(blob),
            "team_games": len(agg), "source": URL.format(season=s),
        }
        print(f"  {s}  {len(agg):>4} team-games  {len(blob)/1e6:>5.1f}MB  "
              f"{'cached' if cached else 'downloaded'}  {time.time()-t0:>4.1f}s"
              f"   skipped: {skipped['not_scrimmage']:,} non-scrimmage, "
              f"{skipped['no_epa']:,} no-EPA")

    rows.sort(key=lambda r: (int(r["season"]), r["game_id"], r["team"]))
    for r in rows:
        r["epa_per_play"] = round(r["epa_sum"] / r["n_plays"], 6) if r["n_plays"] else ""
        r["epa_sum"] = round(r["epa_sum"], 4)

    cols = ["game_id", "season", "week", "season_type", "team", "opponent",
            "is_home", "n_plays", "epa_sum", "epa_per_play"]
    with io.open(OUTFILE, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    manifest["_note"] = (
        "sha256 of each nflverse play-by-play season as aggregated. The raw "
        "files are cached under data/football/raw/pbp/ and GITIGNORED (~20MB "
        "each): too large for this repo, so we keep the hashes instead. That "
        "means we cannot rebuild from the repo alone, but we CAN detect that a "
        "season was rebuilt upstream - which is the failure that would corrupt "
        "a fit. See the deviation note in pbp_aggregate.py.")
    manifest["scrimmage_plays_only"] = (
        "passes and rushes with non-null EPA; special teams, kneels and spikes "
        "excluded - a modelling decision, not data cleaning")
    with io.open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print(f"\nwrote {os.path.relpath(OUTFILE, ROOT)}  {len(rows):,} team-games")
    print(f"wrote {os.path.relpath(MANIFEST, ROOT)}  {len(seasons)} seasons hashed")

    if drift:
        print(f"\nWARNING: nflverse rebuilt season(s) {drift} since the last run "
              "- the bytes changed under us. Any fit made before this run used "
              "different data. This is exactly what the hashes are for.")
        return 1

    # A team-game with very few plays is a data problem, not a blowout.
    thin = [r for r in rows if r["n_plays"] < 30]
    if thin:
        print(f"\nNOTE: {len(thin)} team-game(s) with under 30 scrimmage plays "
              f"(e.g. {thin[0]['game_id']} {thin[0]['team']} {thin[0]['n_plays']}) "
              "- check before trusting their efficiency.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
