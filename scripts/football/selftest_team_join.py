#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the NFL team-profile joins.

    python scripts/football/selftest_team_join.py

Makes no network call.

WHY THIS EXISTS. A dossier build reported "0 teams with 2025 EPA" and the
obvious diagnosis - the two tables key teams differently - was WRONG. Both
sides already use identical canonical franchise keys. The real cause was that
`team_game_efficiency.csv` ends at 2024, so a filter for season 2025 correctly
matched nothing.

That is worth a permanent test for two reasons. An empty join looks the same
whether the keys disagree or the season is absent, and the two have opposite
fixes: one needs a mapping, the other needs the filter corrected or the claim
dropped. Guessing wrong means either an invented mapping that hides a real
identity bug, or a silently stale profile presented as current form.

So this asserts BOTH halves separately:

  1. IDENTITY - every franchise in the results store resolves in both profile
     tables, with no fuzzy matching and no mapping layer;
  2. COVERAGE - what the newest season in each table actually is, so a stale
     table is a visible fact rather than an empty result.
"""
import collections
import csv
import io
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts", "football"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import teams as teamsmod   # noqa: E402
import espn_nfl            # noqa: E402

EPA = os.path.join(ROOT, "data", "football", "team_game_efficiency.csv")
GAMES = os.path.join(ROOT, "data", "football", "games.csv")

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def rows(path):
    with io.open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


print("[1] identity - the keys on each side of the join")
res = espn_nfl.load_store()["events"]
res_keys = {e["away"] for e in res.values()} | {e["home"] for e in res.values()}
epa_rows = rows(EPA)
epa_keys = {r["team"] for r in epa_rows}
gm_rows = rows(GAMES)
gm_keys = {r["away_team"] for r in gm_rows} | {r["home_team"] for r in gm_rows}

print(f"       results store : {len(res_keys)} keys, e.g. {sorted(res_keys)[:6]}")
print(f"       EPA table     : {len(epa_keys)} keys, e.g. {sorted(epa_keys)[:6]}")
print(f"       games.csv     : {len(gm_keys)} keys, e.g. {sorted(gm_keys)[:6]}")

missing_epa = sorted(res_keys - epa_keys)
missing_gm = sorted(res_keys - gm_keys)
check(not missing_epa, f"every results-store franchise appears in the EPA table "
                       f"(unresolved: {missing_epa or 'none'})")
check(not missing_gm, f"every results-store franchise appears in games.csv "
                      f"(unresolved: {missing_gm or 'none'})")
check(len(res_keys) == 32, f"the results store holds exactly 32 franchises ({len(res_keys)})")

# games.csv legitimately carries retired spellings (relocations). They must
# resolve through teams.py rather than being silently dropped or fuzzy-matched.
extra = sorted(gm_keys - res_keys)
print(f"       games.csv-only keys (historical spellings): {extra or 'none'}")
for k in extra:
    try:
        canon = teamsmod.canonical(k, source="games.csv")
        check(canon in res_keys, f"historical key {k!r} resolves to a current franchise "
                                 f"({canon})")
    except teamsmod.UnknownTeam:
        check(False, f"historical key {k!r} does not resolve through teams.py")

print("\n[2] no mapping layer is needed, and none is used")
check(res_keys == epa_keys,
      "results-store and EPA key sets are IDENTICAL - a mapping would be inventing "
      "a fix for a bug that does not exist")
check(not (epa_keys - res_keys), "the EPA table carries no franchise the store lacks")

print("\n[3] every franchise round-trips through teams.canonical()")
bad = []
for k in sorted(res_keys):
    try:
        if teamsmod.canonical(k, source="selftest") != k:
            bad.append(k)
    except teamsmod.UnknownTeam:
        bad.append(k)
check(not bad, f"all 32 canonical keys are stable under canonical() ({bad or 'none bad'})")

print("\n[4] coverage - what each table actually contains, so an empty join is legible")
epa_seasons = sorted({r["season"] for r in epa_rows})
gm_seasons = sorted({r["season"] for r in gm_rows})
epa_latest, gm_latest = epa_seasons[-1], gm_seasons[-1]
print(f"       EPA table newest season   : {epa_latest}")
print(f"       games.csv newest season   : {gm_latest}")
check(int(gm_latest) >= 2025, f"games.csv reaches 2025 or later ({gm_latest}) - the "
                              f"prior-season profile is available")
s25 = [r for r in gm_rows if r["season"] == "2025" and r["game_type"] == "REG"
       and r["away_score"] and r["home_score"]]
per = collections.Counter()
for r in s25:
    per[r["away_team"]] += 1
    per[r["home_team"]] += 1
check(len(s25) == 272, f"2025 regular season is complete in games.csv ({len(s25)} games)")
check(len(per) == 32 and set(per.values()) == {17},
      f"all 32 franchises have 17 scored 2025 games ({len(per)} teams)")

# THE ACTUAL BUG, pinned so it cannot silently return.
n2025_epa = sum(1 for r in epa_rows if r["season"] == "2025")
check(n2025_epa == 0,
      f"the EPA table has no 2025 rows ({n2025_epa}) - this, not a key mismatch, is "
      f"why a 2025 EPA filter returns nothing")
print(f"       -> any EPA profile is at best {epa_latest}, two seasons before the 2026 "
      f"season. Callers must label the season or omit the claim.")

print(f"\nteam-join selftest: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
