-- Entries: one row per (date, user); the last pre-lock button press wins.
CREATE TABLE IF NOT EXISTS entries (
  date      TEXT    NOT NULL,
  user_id   TEXT    NOT NULL,
  user_name TEXT,
  side      TEXT    NOT NULL CHECK (side IN ('ride', 'fade')),
  ts        INTEGER NOT NULL,          -- unix seconds of the press
  PRIMARY KEY (date, user_id)
);

-- Standings: cumulative per-user record, updated once per graded day by the
-- nightly pipeline via POST /results. Names and ids live ONLY here (and in
-- Discord) — the public repo stores aggregates and HMAC'd ids, never names.
CREATE TABLE IF NOT EXISTS standings (
  user_id          TEXT PRIMARY KEY,
  user_name        TEXT,
  wins             INTEGER NOT NULL DEFAULT 0,
  losses           INTEGER NOT NULL DEFAULT 0,
  voids            INTEGER NOT NULL DEFAULT 0,
  streak           INTEGER NOT NULL DEFAULT 0,   -- +n win streak, -n losing
  best_streak      INTEGER NOT NULL DEFAULT 0,
  month            TEXT,                          -- 'YYYY-MM' the month_ cols count
  month_wins       INTEGER NOT NULL DEFAULT 0,
  month_losses     INTEGER NOT NULL DEFAULT 0,
  last_graded_date TEXT
);
