"""Paths, data-source URLs and season splits. One place, so nothing drifts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
ODDS_CACHE = DATA / "odds_cache"
DB_PATH = DATA / "ncaa.duckdb"
REPORTS = ROOT / "reports"
BOARDS = ROOT / "boards"

# sportsdataverse / hoopR: open ESPN-derived data, rebuilt daily in season.
RELEASES = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
TEAM_BOX_URL = RELEASES + "/espn_mens_college_basketball_team_boxscores/team_box_{season}.parquet"
SCHEDULE_URL = RELEASES + "/espn_mens_college_basketball_schedules/mbb_schedule_{season}.parquet"
# Per-game ESPN summary JSON (holds the `pickcenter` betting block).
GAME_JSON_URL = "https://raw.githubusercontent.com/sportsdataverse/hoopR-mbb-raw/main/mbb/json/final/{game_id}.json"

# Season = the year the season ENDS (2027 = the 2026-27 season, current).
FIRST_SEASON = 2015          # 2015 only seeds priors for 2016
CURRENT_SEASON = 2027

# Chronological split, fixed BEFORE any results were looked at.
#   tune:       hyper-parameters are chosen here and only here
#   validation: confirms the tuned settings; no re-tuning after looking
#   holdout:    run once per model version, never used for a decision
TUNE_SEASONS = list(range(2016, 2023))        # 2016-2022 (2021 = COVID season, kept, flagged)
VALIDATION_SEASONS = [2023, 2024, 2025]
HOLDOUT_SEASONS = [2026]

# Books whose lines are real prices. ESPN's pickcenter also carries
# "numberfire" (a projection model, not a book) and "teamrankings"
# (no spread); those are excluded from every market test.
REAL_BOOK_PROVIDERS = {"consensus", "Caesars Sportsbook (New Jersey)", "DraftKings",
                       "ESPN BET", "William Hill (New Jersey)"}
