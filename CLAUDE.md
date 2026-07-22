# Open Ledger Sports — project brief for Claude Code

MLB picks site with a real Monte Carlo engine and an append-only public ledger.
Brand thesis: radical transparency ("the aquarium, not the magic show") — the
site publishes its record, its rules, its limitations, and its losses.
Live repo: github.com/dsimny/dsimny.github.io (GitHub Pages serves index.html).
Live site: https://openledgersports.com (custom domain; www and the
dsimny.github.io URL both 301 to the apex). Local working copy lives at
claude-code-projects/openledger-sports.

## Architecture (daily pipeline, runs via GitHub Actions)

```
.github/workflows/morning-board.yml  (daily 15:10 UTC)
  scripts/fetch_data.py   → data/snapshot_<date>.json  (MLB Stats API; The Odds API if ODDS_API_KEY)
  scripts/engine.py       → data/board_<date>.json     (10,000 sims/game + circuit breakers)
  scripts/build_site.py   → index.html                 (single-file site, incl. auto-written analysis)
  scripts/post_discord.py pick                         (optional; DISCORD_WEBHOOK_URL secret)

.github/workflows/grade-ledger.yml   (daily 08:10 UTC)
  scripts/grade.py        → data/ledger.json           (final scores → W/L/VOID, units, ROI; APPEND-ONLY)
  scripts/build_site.py   → index.html                 (ledger tab refreshed)
  scripts/post_discord.py recap                        (posts results, wins AND losses)
```

Test locally without network: `python scripts/engine.py 2026-07-22` against an
existing snapshot, then `python scripts/build_site.py 2026-07-22`; grade with
`python scripts/grade.py <date> --scores-file <fake_scores.json>`; preview
Discord payloads with `python scripts/post_discord.py pick <date> --dry-run`.
Deps: `pip install -r requirements.txt` (numpy, requests).

## The model (engine.py) — honest v0.2, documented on the site's Methodology tab

- Team run rates (RS/RA per game from standings) normalized to league average.
- Starter ERA vs league, weighted over 5.5/9 of the game; bullpen = team rate.
- Static park factors (dict in fetch_data.py); home advantage ×1.026 on runs.
- Negative binomial scoring (Gamma-Poisson, DISPERSION=2.4); extra innings
  simulated at 1.9× per-inning rates until decided.
- Per-date seed = int(YYYYMMDD) → every board is reproducible. Never use
  wall-clock randomness.
- Market math: edge = model prob − implied prob of offered price; divergence =
  model prob − de-vigged market prob; EV per unit; quarter-Kelly capped by tier.

## Circuit breakers (the product's identity — never weaken silently)

- Rule 2: no road favorites −180+ / home −220+ (market line) → pivot to −1.5 run line.
- Rule 4 (heuristic): starter < 60 IP this deep in season → units downgraded one tier.
- Rules 3/5/6: NOT automated (need Statcast/wOBA feeds) — always surfaced as
  "manual review" on cards, never silently claimed. Automating these is roadmap.
- Rule 7: TBD starter → game scratched, published with reason.
- Rule 8 (Divergence Governor): |model − de-vigged market| > 12 pts → held as
  lean, no allocation ("the market knows something our inputs don't").
- Edge gate: < 2-pt edge vs offered price → no allocation.
- Sizing: min(confidence tier 3u/2u/1u, quarter-Kelly), 10u daily exposure cap.

## House rules (non-negotiable; they ARE the brand)

1. Ledger is append-only: entries are never edited after grading; aggregates
   recomputed from full history. Never backfill, never delete a loss.
2. Free pick = cleanest lower-board play (no flags), NEVER the top pick
   (selection logic duplicated in build_site.py and post_discord.py — keep in sync).
3. Every card publishes its full circuit-breaker log, including passed checks.
4. Never claim an automated check that isn't automated; site copy must state
   current limitations (see Methodology tab's "does NOT do" list).
5. Legal footer everywhere: analytics not a sportsbook, no bets accepted,
   21+, 1-800-GAMBLER, no-guarantee language. Do not remove or soften.
6. If no plays clear the gates: publish "no qualifying plays — passing is a
   position too." Never manufacture a pick.

## Config (GitHub → Settings)

- Secrets: ODDS_API_KEY (optional; without it edge/Kelly/Rules 2+8 inactive and
  the site says so), DISCORD_WEBHOOK_URL (optional; posts skip gracefully).
- Variable: SITE_URL (links Discord posts back to the site).
- Actions → General → Workflow permissions must be "Read and write".
- Pages: deploy from branch `main`, folder `/ (root)`.

## Current deploy status (as of 2026-07-22)

DEPLOYED AND VERIFIED. The earlier note that files were "pushed incl. both
workflows" was wrong — the repo held only a stub README; the browser upload
never landed. Everything was re-pushed via git (which does not drop
`.github/`). Setup complete: repo renamed, Pages live, custom domain with
HTTPS, ODDS_API_KEY and SITE_URL set.

fetch_data.py is no longer untested — the first live run pulled 15 real games
from the MLB Stats API and the full pipeline (fetch → engine → build → commit)
went green.

Known-good local test: the engine reproduces a committed board byte-for-byte
from the per-date seed, and grade.py refuses to double-grade a date.

The 2026-07-22 board/snapshot that shipped with the deploy kit are SAMPLE
data — they credit "Covers.com" and carry a future timestamp, neither of
which the code produces (fetch_data.py calls api.the-odds-api.com). The first
real morning run overwrites both. If the site ever shows Covers.com as the
odds source again, it is serving sample data and the run did not happen.

Gotcha worth remembering: the scripts date everything by US Eastern, so
triggering "Morning board" late at night builds a board for a slate that has
already been played. The 2026-07-21 board created that way was removed before
grading so the ledger would not open with picks that were never live before
first pitch. Only run it manually between midnight ET and first pitch.

## Roadmap (in trust-building order)

1. Live multi-book best price via The Odds API (fetch_data.py already consumes
   the key; upgrade from median-consensus to per-book best price + book name).
2. Log opening vs closing lines → CLV tracking on the ledger.
3. Statcast feeds → automate Rules 3/5/6, retire "manual review" labels.
4. Third-party verification (Pikkit/Juice Reel) after ~1 month of record.
5. NRFI market (activates the two dormant breakers). Then, only once the ledger
   has earned it: paid tier (Whop on Discord, ~$20-30/mo lane).
