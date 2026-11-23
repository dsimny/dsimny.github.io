# Data sources: what was verified, what it costs, what it can't do

Checked 2026-11-23 from the build environment (a sandboxed cloud container with
an outbound allow-list). "Reachable" means a request from this environment
succeeded. It does NOT mean the source is unreachable for you at home.

## Used in Phase 1 (free, verified working)

| source | what | seasons | access | licence / terms |
|---|---|---|---|---|
| **sportsdataverse / hoopR-mbb-data** (GitHub releases) | ESPN team box scores (FGA, 3PA, FTA, OREB, DREB, TO, …), full schedules incl. future games, neutral-site flag, conference ids | 2003–present; **2026-27 updated daily** (box scores through 2026-11-22 at build time) | free, no key | MIT-licensed code; data is scraped from ESPN's public endpoints. Fine for private research; ESPN's own terms govern redistribution, so the raw files are **not** committed here |
| **hoopR-mbb-raw** per-game ESPN JSON (`raw.githubusercontent.com`) | the `pickcenter` betting block: spread, total, moneylines; DraftKings era also carries **open and close** | lines found for 2015-16 → 2022-23 and 2025-26 → present; **none for 2023-24 and 2024-25** | free, no key | same as above |

### What the betting lines actually are (read before trusting any market number)

- **One book per game, not a consensus across books.** Provider varies by
  era: `consensus` and `Caesars Sportsbook (New Jersey)` (2016–2023),
  `DraftKings` (2025-26 onward). ESPN also stores `numberfire` (a projection
  model, NOT a sportsbook) and `teamrankings` (no spread) — both are
  **excluded** from every market test (`config.REAL_BOOK_PROVIDERS`).
- **No capture timestamps.** ESPN does not say when a pre-2025 line was
  taken. It behaves like a closing line (validated: correlation with the final
  margin ≈ −0.7 to −0.8, MAE ≈ 8.5–9.5 pts), but it is treated as
  "closing, unverified time".
- **DraftKings era has open + close** but still no timestamp for the open, so
  "bet the open" results are an approximation of what was available early.
- The sign convention (spread = home line, negative = home favoured) is
  checked against final margins every run (`validate.odds_checks`).
- **Two seasons have no lines at all**, which leaves the validation window
  with market data for 2022-23 only. That limits how strongly any
  market-related result can be stated.

## Investigated, not used (and why)

| source | status from this environment | cost / terms (as published; verify before buying) | what it would add |
|---|---|---|---|
| **CollegeBasketballData.com** (CBBD, sister of CollegeFootballData) | API host blocked here | free key with a monthly call cap; higher caps via Patreon tiers ([tiers](https://collegefootballdata.com/api-tiers), [terms](https://collegefootballdata.com/terms), [Python client](https://pypi.org/project/cbbd/1.20.2/)) | its own game/team stats, adjusted ratings, **historical lines incl. 2023-24/2024-25** (would fill the gap), player data for returning-production priors. **Best next step: Dan creates a free key** |
| **The Odds API** | blocked here | free tier 500 credits/month, live odds only; **historical snapshots are paid-plan only** ([historical](https://the-odds-api.com/historical-odds-data/), [NCAAB](https://the-odds-api.com/sports/ncaab-odds.html)). The Open Ledger account already has a key | multi-book live prices for the daily board, timestamped captures → real CLV going forward. Free tier is enough for ~1 pull/day of NCAAB spreads+totals if the MLB budget allows |
| **KenPom** | blocked here | paid subscription; API access listed at $95/year ([register-api](https://kenpom.com/register-api.php)) | benchmark ratings to compare against. Not needed: the model rebuilds the same type of metric from box scores |
| **Bart Torvik** (barttorvik.com) | blocked here | free website; no formal API or published data licence. Community wrappers exist (toRvik, [cbbdata](https://github.com/andreweatherman/cbbdata/)) | T-Rank and game-level four factors. Since there's no licence, use it only as a benchmark, not as an ingested feed |
| **ESPN site API** directly | blocked here | unofficial, no key | live scoreboard + pregame odds for today's games. Running on Dan's machine or in GitHub Actions would likely work |
| **SportsDataIO** | not tried | paid ([betting data](https://sportsdata.io/betting-data)); free trial data is scrambled | not justified at this stage (rule 11: no paid data without authorisation) |

## Gaps that limit what can be tested right now

1. **No live or pregame odds** reachable from this environment, so today's
   board has projections only, with no EV and no Qualified/Watchlist labels.
2. **No timestamped historical market snapshots** → line-movement models and
   true CLV can't be tested historically. The DraftKings open/close pair is the
   only movement signal.
3. **No 2023-24 / 2024-25 lines** in the open source.
4. **No roster continuity / transfer / injury data** yet. Player box scores
   exist in the same sportsdataverse release (returning-minutes priors are
   feasible in Phase 3); injuries are not verifiable from any free source
   found so far.

## Recommendation

- **Stats:** keep sportsdataverse/ESPN box scores as the primary feed (free,
  10+ seasons, updated daily, stable ESPN team ids).
- **Lines, historical:** ESPN pickcenter now; add CBBD's lines with a free key
  to fill 2023-25 and cross-check providers.
- **Lines, live:** The Odds API (existing key, free tier) captured at fixed
  times each day, stored append-only with timestamps. That's the only way to
  get honest CLV going forward.
