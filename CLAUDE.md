# Open Ledger Sports — project brief for Claude Code

MLB picks site with a real Monte Carlo engine and an append-only public ledger.
Brand thesis: radical transparency ("the aquarium, not the magic show") — the
site publishes its record, its rules, its limitations, and its losses.
Live repo: github.com/dsimny/dsimny.github.io (GitHub Pages serves index.html).
Live site: https://openledgersports.com (custom domain; www and the
dsimny.github.io URL both 301 to the apex). Local working copy lives at
claude-code-projects/openledger-sports.

## NEVER commit a locally built index.html or feed.xml

They are generated files that CI rebuilds each run from the day's board.
Since 2026-07-23 the board is encrypted, so a local machine WITHOUT
BOARD_ENCRYPTION_KEY can only build an OLD date's board, and committing that
index.html silently reverts the live site's free pick to a stale (already
played) game. This happened once. When changing build_site.py or feed.py,
commit ONLY the source: run `git checkout -- index.html feed.xml` to discard
the local build first, then `git add` the specific source files (not -A). Let
a workflow rebuild them. To fix the live site after a bad commit, trigger the
"Rebuild site" workflow (workflow_dispatch) — it has the key and posts nothing
to Discord.

## Architecture (daily pipeline, runs via GitHub Actions)

```
.github/workflows/morning-board.yml  (daily 15:10 UTC)
  scripts/fetch_data.py   → data/snapshot_<date>.enc   (MLB Stats API; The Odds API if ODDS_API_KEY)
  scripts/engine.py       → data/board_<date>.enc      (10,000 sims/game + circuit breakers)
                          → data/commitments.json      (SHA-256 of the board, IN THE CLEAR)
  scripts/build_site.py   → index.html                 (single-file site, incl. auto-written analysis)
  scripts/post_discord.py pick                         (free pick → public channel)
  scripts/post_discord.py board                        (held plays → members channel)
  scripts/blog.py         → blog/<date>.html + blog/index.html + data/blog_items.json
                                                       (The Morning Line: deterministic daily post;
                                                        merges blog items into feed.xml)
  scripts/game_pages.py   → picks/mlb/<date>/<slug>/   (board-day version: held plays REDACTED)
  scripts/post_discord.py blog                         (blog title + teaser + link → free channel)

.github/workflows/capture-closing.yml (several times/day via cron-job.org)
  scripts/fetch_closing.py → data/closing_<date>.json  (last pre-first-pitch line per game, for CLV;
                                                        since 2026-08-08 also a deduped per-game
                                                        capture "history" + team names + first pitch)
  scripts/odds_page.py     → odds/index.html           (line-movement page; reads ONLY closing files —
                                                        no key, no model output, descriptive language only)

.github/workflows/grade-ledger.yml   (daily 08:10 UTC)
  scripts/grade.py        → data/ledger.json           (final scores → W/L/VOID, units, ROI, CLV; APPEND-ONLY)
                          → data/totals_ledger.json    (totals paper track, separate)
                          → data/watchlist.json        (v0.14 watch tier paper record, separate; never mixes with ledger.json)
                          → data/board_<date>.json     (the reveal: .enc replaced by plaintext)
  scripts/build_site.py   → index.html                 (ledger tab refreshed)
  scripts/blog.py --rebuild-only                       (re-renders blog/ from the store, re-merges
                                                        blog items into feed.xml; adds NO post)
  scripts/game_pages.py <yesterday ET>                 (the reveal: same URLs re-render with picks,
                                                        closing lines, results, postgame notes)
  scripts/post_discord.py recap                        (posts results, wins AND losses)
  scripts/post_social.py  x / facebook                 (daily record → X + FB, wins AND losses)
  scripts/grade_pickem.py                              (pick'em: grade entries, update Worker
                                                        standings, commit aggregates, post leaderboard)
  scripts/grade.py also →  data/daily_ledger.json      (v0.15 Daily Pick strategy record, separate)
  scripts/export_training_rows.py → data/model/training_rows.csv  (point-in-time challenger data)

.github/workflows/heartbeat.yml     (GitHub cron, daily 19:00 UTC / ~3 PM ET)
  checks data/board_<today ET>.{json,enc} + a commitments.json entry for that date
  → scripts/post_discord.py alert    ONLY when today's board is missing (silent otherwise)

morning-board.yml, grade-ledger.yml and capture-closing.yml each carry a second
job, `alert`, that runs on `always() && needs.<job>.result != 'success'` and posts
the failing step name, the run URL and the last 20 log lines to Discord.
```

## Never serve a stale board silently (2026-08-17 incident)

WHAT HAPPENED: the morning board did not publish. The cron-job.org trigger for
11:10 AM ET never fired, so no workflow ran — the Actions history shows no
Morning board run at 15:10 UTC that day, only a manual one at 17:04 UTC that
went fully green. Grading ran normally at 08:10 UTC. Nothing failed, because
nothing STARTED, so no `if: failure()` alert could ever have caught it. For
~6 hours the site served the 2026-08-16 board with no indication it was a day
old: a visitor would reasonably have read yesterday's picks and yesterday's
prices as today's. On a site whose whole claim is "you see exactly what we see,
when we see it", that is worse than publishing nothing.

THE RULE THIS ENCODES: the site must always be honest about its own freshness,
and a failed run must be loud. Both halves are now structural.

FRESHNESS (build_site.py). Two independent layers, because the site is static
and a build-time check freezes at the moment the page was written — which is
exactly what bit us:
- Build time: `STALE = DATE != today ET`. Stale renders the banner into the
  HTML, adds `class="stale"` to `<body>`, swaps the hero chip to
  `ARCHIVED — <date>`, renames the board tab to `Board · <Mon D>`, and routes
  every "today's"/"today" phrase through `TODAYS` / `TODAY_ADV` so the page
  states the board's real date instead of implying currency. Works with JS off.
- Page load: an inline guard re-runs the same comparison against the VISITOR's
  America/New_York clock, using `<body data-board-date>`. This is the layer that
  catches the actual incident — a page built yesterday, correct when written,
  never rebuilt. It toggles `.stale`, un-hides the banner, and swaps every
  `data-live-label` / `data-archived-label` pair in both directions.
- TWO WORDINGS, one honest each: before 11:15 AM ET (`BOARD_DUE_ET_MIN`) the
  banner says the board isn't up YET — the 4:10 AM ET grading run legitimately
  rebuilds the site against yesterday's board, and calling that a failure would
  be crying wolf every morning. After it, it says the board failed to publish.
  Both carry the same do-not-bet warning, because in both the prices are old.
- Stale de-emphasises the free/daily-pick cards and the board tab's cards to 45%
  opacity. It NEVER hides them: House Rule 7 is that held plays are held, not
  hidden, and an archived board stays fully readable.
- Zero visual change on the happy path: the banner ships `hidden`, `<body>` has
  no `stale` class, all copy reads "today's" as before.

LOUD FAILURE. Three layers, because they catch different things:
- `alert` job in morning-board.yml, grade-ledger.yml and capture-closing.yml —
  `always() && result != 'success'`, so it also fires on a CANCELLED or
  runner-killed job, not just a non-zero step. Reads the failing step name and
  the last 20 log lines via `gh api --jq` (gh's built-in jq, not the standalone
  binary) and posts them with the run URL. Each message states that job's REAL
  blast radius — a missed capture costs one window's CLV precision, not a board
  — because an alert channel that treats everything as an emergency gets muted.
  capture-closing was added because it was already failing unnoticed: its
  2026-08-16 22:30 UTC run died at the commit step (a push race on the shared
  data/ directory) and nothing said so.
- heartbeat.yml — the watchdog for "nothing ran", which the above can NEVER
  catch. Daily 19:00 UTC on GitHub's own cron: the 2026-07-23 reasons for
  dropping GitHub cron (fires late, re-posts Discord) do not apply to a job that
  posts nothing on a healthy day and whose whole purpose is to notice a missing
  run. Its check is Python, not shell+jq, so a missing tool can't turn it into a
  false alarm; it wants BOTH a board file for today ET and a commitments.json
  entry for that date.
- `post_discord.py alert "<msg>" [--detail-file f]` — swallows every exception
  and always exits 0 (an alerting path that can break a run is worse than none),
  and validates the webhook host against discord.com before sending, so a
  swapped env var can't POST a CI log somewhere else. Webhook order:
  DISCORD_WEBHOOK_URL_ALERTS → _MEMBERS → the free channel. A visible failure is
  on-brand; do not soften or suppress these.

NOT DONE, deliberately: a missed day stays missed. No board is ever backfilled
for a day the pipeline skipped — the banner IS how a missed day is communicated.

## Daily Pick strategy (engine v0.15, effective 2026-08-09)

The product answer to the quiet board: an ALWAYS-ON second strategy beside
the Qualified Plays, never replacing them. engine.py selects at most one
daily_pick per slate from the leans + the would-be free pick (NEVER a held
play — the candidate rule protects the paywall structurally). Precommitted
eligibility: market odds present · no Rule 8 hold · raw model and blend agree
on the side · edge STRICTLY positive at best price. Deterministic ranking
(daily_score: 0.40 EV + 0.25 agreement + 0.15 completeness + 0.10 price
quality + 0.10 starter reliability − breaker penalties; weights provisional,
fixed in code, change only by version bump). No survivor → honest pass.

Staking: DAILY_PICK_UNITS = 0.0 through DAILY_PROVING_END (2026-09-08);
grade.py's grade_daily() books every pick into data/daily_ledger.json
(append-only, idempotent) with pnl_staked at real units AND pnl_paper at the
flat 0.25u basis + CLV, so the scheduled review reads a real record. Surfaces:
free tab hero on no-qualified days (labeled 🎯, 0u proving), its own ledger
section (never merged with Qualified tiles/rows), feed/email item, Discord
pick post, blog block, pick'em featured-game fallback (free pick → daily →
Best of Board). Free = acquisition product per Daniel 2026-08-09; members
keep the full board, held plays, and depth.

## Challenger-model groundwork (2026-08-09)

scripts/export_training_rows.py appends point-in-time feature rows to
data/model/training_rows.csv nightly (grade workflow; first run backfills all
revealed boards). Rows come from REVEALED boards — pregame by construction,
so no leakage; market features are board-time consensus (decision-time), and
closing odds are DELIBERATELY excluded from features. Label = home_won from
MLB finals. Plan (agreed 2026-08-09): logistic calibrator first (learned
blend replacing fixed MODEL_WEIGHT), JSON coefficients artifact (never
pickle), chronological/walk-forward validation only, evaluated vs market /
raw sim / current blend on Brier + log loss + calibration, promoted only
through the Watch-List-style forward gate. Do NOT train until enough
decision-time rows exist; do NOT randomly split games across time.

## Pick'em pilot ("Beat the Engine", built 2026-08-08 — ships DARK)

Demand-validation pilot per the engagement roadmap: one featured game a day
(free pick, else ✳ Best of Board — both already public), two buttons in the
free Discord (ride/fade), graded overnight, points and streaks ONLY (no
prizes — sweepstakes exposure). Native accounts get built only if this
sustains ~100–250 recurring weekly participants; the Monday audit publishes
the participation numbers either way.

Architecture ("keep authoritative records on the site; test social behavior
somewhere already equipped to manage people"):
- worker/pickem/ — Cloudflare Worker, the ONLY new runtime surface: Discord
  interactions endpoint (Ed25519-verified), D1 entries + standings tables,
  Bearer-token /entries and /results routes. A dumb inbox/scoreboard: it
  NEVER decides who won. If it's down, entries pause; board/ledger/site
  untouched. Deploy checklist: worker/pickem/README.md (~30 min, user-only:
  Discord app + wrangler + secrets).
- scripts/post_pickem.py (morning) — announce with buttons via BOT token
  (webhooks can't carry components). custom_id encodes side:date:lockEpoch.
  select_featured() lives here; grade_pickem imports it so announce and
  grading can never disagree. NOT idempotent (like the other Discord posts).
- scripts/grade_pickem.py (nightly, after grade.py) — the LAW on locking
  (entries stamped >= first pitch don't count, whatever the button said).
  Grades vs the revealed board + MLB linescore (ML by winner, -1.5 RL by
  margin>=2, VOID non-final). Pushes per-user results to the Worker (names
  and ids live ONLY in Discord + D1), commits AGGREGATES + HMAC'd participant
  ids to data/pickem.json (append-only, idempotent by date; HMAC key =
  READ_TOKEN so uniques are computable in CI but not reversible publicly).
  Offline test: --entries-file/--scores-file/--dry-run.
- Surfaces: ledger tab shows the aggregate block only (community record vs
  engine, entries, distinct players — NO names on the site, by decision);
  Monday audit reports weekly entries/uniques/repeat rate.
- Secrets (all optional — every step skips cleanly until they exist):
  DISCORD_BOT_TOKEN, PICKEM_WORKER_URL, PICKEM_READ_TOKEN (secrets);
  PICKEM_CHANNEL_ID (variable). Worker-side: DISCORD_PUBLIC_KEY, READ_TOKEN
  (same value as PICKEM_READ_TOKEN).
- CI: .github/workflows/worker-check.yml runs on any push touching worker/ —
  node --check, sqlite3 schema apply, and `wrangler deploy --dry-run`
  (bundles with no credentials). The Worker is validated on push, not at
  launch; it's the repo's only push-triggered workflow (fires rarely, on
  worker/ changes only — the cron-removal rationale doesn't apply to it).

## The Morning Line (daily blog, shipped 2026-08-07)

One post a day at blog/<date>.html, archive at blog/ (linked from the site
nav). scripts/blog.py composes it DETERMINISTICALLY — no LLM at run time, no
hand edits: game days walk the slate from the SAME board build_site renders
(yesterday's graded ledger, per-game table, "three angles" scored from the
data, watch list); days with no MLB slate publish the next unused piece from
the evergreen bettor-education library in scripts/blog_evergreen.py (12 pieces:
CLV, de-vig, Kelly, park factors, variance, etc. — rotation state = count of
evergreen items already in the store).

Mechanics mirror feed.py: rendered articles persist in data/blog_items.json
(append-only by date, KEEP=120), so ALL pages regenerate from the store alone —
that's why grade-ledger/rebuild-site can run `blog.py --rebuild-only` while
today's board is still encrypted. feed._write_xml always merges blog items
(guid olsb-<date>) next to the pick items (olsp-<date>); send_email.py is
untouched (reads feed_items.json only). post_discord.py's `blog` mode posts
title+teaser+link to the FREE channel; like pick/board it is NOT idempotent.

REDACTION RULE (House Rule 7): a held play appears in the blog as matchup +
time ONLY — no side, price, projection, total, or probability (a projection
plus a total is most of a pick). Free pick, leans, scratches, and watch picks
are already public on the site and appear in full. The evergreen library obeys
House Rules 4/5/8: no automation claims beyond what's live, legal footer on
every page, copy that describes what the site actually does.

MONDAY AUDIT (2026-08-08): weekly_audit_section() adds a "What we learned:
<prev Mon–Sun>" section to Monday posts (slate AND evergreen kinds): board
aggregates for the week (slates/sims/plays/units/watch/scratches), the staked
ledger's graded picks with per-pick P&L, week CLV when entries carry clv_pts,
and the watchlist paper record by tag. Reads only graded/public records;
still-encrypted boards are skipped, so it can never discuss an unrevealed pick.

ODDS-MOVEMENT PAGE (scripts/odds_page.py, shipped 2026-08-08): odds/index.html
renders the consensus ML + total per game across the day's captures, from the
"history" arrays fetch_closing.py now appends (deduped — identical captures
collapse to one point; top-level closing fields unchanged, grade.py's CLV read
untouched). HONESTY CONSTRAINTS, deliberate: the page reads ONLY closing_*.json
(plus a REVEALED board for legacy matchup names) so it can never leak a held
play and needs no key; movement is described (side + de-vigged points), never
explained — no "sharp action" claims; the header disclaimer says captures are
a few scheduled snapshots, not a live feed. Rebuilt by capture-closing (each
run), morning-board, and rebuild-site. Legacy closing files (pre-history)
render as one capture, no movement. Do not add model numbers to this page.

GAME PAGES (scripts/game_pages.py, shipped 2026-08-08): one permanent URL per
boarded game — picks/mlb/<date>/<away>-at-<home>/ — written twice by the
pipeline and enriched IN PLACE (no preview/result URL fragmentation): the
morning run writes the public version (free pick full; leans/scratches/watch
with numbers; HELD plays as matchup+time+venue+tier+check-count ONLY), the
grading run re-renders with the revealed pick, open-vs-close (with the
capture's own weak-close caveat verbatim), ledger result + CLV, watch paper
grades, and a deterministic postgame note. Reveal detection is STRUCTURAL:
a date renders as revealed iff data/board_<date>.json exists in plaintext
(written only after grade.py's fingerprint check) — so held plays stay
redacted no matter who runs the script or when. Date index per slate +
picks/index.html archive rebuilt from disk each run. Blog slate tables link
every matchup to its page; board tab links the archive. --unrevealed flag
forces board-day redaction (offline tests only). All URLs root-absolute.

RESPONSIBLE GAMBLING (2026-08-08): RG_BLOCK in build_site.py renders at the
point of decision — after the free-pick card, atop the board tab, under the
ledger splits — 21+, 1-800-GAMBLER, NCPG safer-sports-betting link; .rgline
CSS. Blog page footers carry the same NCPG link via blog.py's LEGAL. Do not
remove or soften (House Rule 5).

CLV (closing-line value): fetch_closing.py re-fetches the schedule (MLB API, no
key) plus current odds (ODDS_API_KEY) and records each game's last line BEFORE
first pitch in data/closing_<date>.json — an already-started game is never
overwritten with an in-play line, so repeated runs converge on the true close.
grade.py reads that file and books open-vs-close per pick (open_ml, close_ml,
clv_pts = de-vigged prob points where + means we beat the close, beat_close),
plus a `clv` aggregate block. Entries with no closing line stay blank, so the
append-only history is never rewritten. Wire capture-closing.yml to a few
cron-job.org triggers through the day (suggested times are in the workflow).

Social posting (scripts/post_social.py) reads data/ledger.json — the same record
the site and the Discord recap use — and posts yesterday's results plus the
running ledger to X (text, OAuth 1.0a, <=280 chars) and a Facebook Page. Losing
days post too (generated from the ledger; no skip path) and every post carries
21+/1-800-GAMBLER/not-betting-advice (house rules 1 and 5). Idempotent like the
email sender: records "posted" per platform in data/post_status.json and refuses
to re-post a date. Each platform skips cleanly if its credentials are unset and
never fails the grading run. Preview: `python scripts/post_social.py x <date> --dry-run`.

X specifics (learned 2026-07-23, first live attempt): OAuth 1.0a User Context
DOES work for POST /2/tweets despite the v2 docs listing only OAuth 2.0 — the
first live post returned 402 "credits depleted", i.e. it authenticated and was
refused only for billing, not auth. X has NO free tier: it is pay-per-use,
credits bought upfront in the developer console. Posting costs $0.015/request
but $0.20 if the post contains a URL (~13x). So the X post carries NO link on
purpose (build_x_text) — it's ~13x cheaper and X downranks link posts anyway;
the site lives in the account bio. Do not re-add SITE to the X post. Until
credits are funded, the X step records "failed" 402 each night (harmless; the
board, ledger, site, Discord and email are all unaffected). Facebook has no
such cost — Meta's Graph API is not metered — so its post keeps the link.

## The Odds API: tier, credit budget, and when to upgrade (2026-08-17)

TIER IN USE: the FREE tier — 500 credits per calendar month, one key
(ODDS_API_KEY). The `/v4/sports/.../odds` endpoint bills ONE CREDIT PER MARKET
PER REGION PER CALL. So the markets string is a price tag, not a preference.

THE ACTUAL BUDGET, counted from the code and the run history:

| caller | markets × regions | calls/day | credits/day |
|---|---|---|---|
| fetch_data.py (morning board) | h2h,totals × us = 2 | 1 | 2 |
| fetch_closing.py (capture-closing) | h2h,totals × us = 2 | 3 | 6 |
| **total** | | **4** | **8** |

8/day ≈ 250/month against a 500 cap — roughly 50% headroom. Two things eat it
fast, and neither may be done casually: adding a market (spreads would be +50%
across BOTH callers, not just the board) or adding capture runs (each new
cron-job.org trigger is +2/day ≈ +60/month).

SPREADS ARE NOT FETCHED, and adding them was considered and declined here:
nothing is staked on spreads today, so it would be a 50% budget increase for a
market with no product surface. F5 and any other future market gets the same
test. If a market IS added later and the budget gets tight, the lever is
CADENCE, not deletion: fetch the staked markets every run and the extra market
on a reduced schedule (e.g. the morning call only). Every run now logs which
markets it fetched, so that decision can be checked against reality.

VISIBILITY (this is what the 08-17 review actually added — the credits were
never the cause, but the run was flying blind on them):
- Both callers read `x-requests-remaining` / `x-requests-used` /
  `x-requests-last` and print them.
- Both append the reading to `data/odds_credits.json`, IN THE CLEAR. The
  snapshot carries the same numbers under `odds_credits`, but the snapshot is
  encrypted until grading, so it cannot be what makes the balance visible in the
  repo. The plaintext log can, and it leaks nothing: a counter and a timestamp.
- The reading is captured BEFORE `raise_for_status`, so a 401 (dead key) or 429
  (month spent) — the readings that matter most — survive the exception.
- Below 50 remaining, fetch_data posts a Discord alert. ~5 days of headroom at
  8/day, so it never arrives as a surprise.

DEGRADE, NEVER DIE: any odds failure (quota, 401, 429, timeout, garbage) is
caught, logged, and the board publishes anyway with an empty odds dict. The site
then renders a "Market odds unavailable this run" notice on the free and board
tabs saying these are model fair lines only and that edge / EV / quarter-Kelly /
Rule 2 / Rule 8 all read the market and are inactive. A missing price feed costs
us picks, never the whole board.

KNOWN GAP, flagged 2026-08-17, NOT fixed here because it is a gate change:
with `mkt_odds is None` the engine's edge gate and Rule 8 both no-op (`edge is
None`), so `risk_tier()` still assigns units and the board STILL PUBLISHES
STAKED PLAYS priced off the model fair line, which then enter the real ledger.
The site copy above is written to describe that truthfully rather than claim a
no-stake behaviour the code does not have (House Rule 4/8). Whether a no-odds
day should instead publish zero staked plays is a real product decision — it
touches sizing, so per House Rule 9 it ships as a version bump, not a patch.

TIER HISTORY — the rule fired once, and was reverted (2026-08-20).
Upgraded free -> 100K ($59) under rule #2: the NFL football track needed spreads
plus a one-time historical T-24 pull that no free allowance could cover. Spent
10,713 credits (357 historical snapshots at 30 each, plus a probe and live
preseason captures). The pull answered its question - the market beat the model
on all three markets in all three seasons, see docs/FOOTBALL_RESULT_T24.md - so
the football track was shelved and the account went straight back to FREE.

Two things worth keeping from that episode. The rule worked exactly as written:
a real product surface needed a market, the projected spend cleared 450, the
upgrade happened for a stated reason and ended when the reason did. And ~89,000
paid credits went unused, because a monthly allowance does not carry over - if a
future upgrade happens for a one-time pull, do the pull and anything else worth
pulling in the SAME billing period.

MLB alone runs ~250/month against the free 500, so the free tier is correctly
sized again. It has no room for a second market or extra capture runs, which is
precisely what the rule below is for.

DECISION RULE FOR UPGRADING TO THE PAID TIER: upgrade when any ONE of these is
true, and not before —
1. Two consecutive months close above 400 credits used (i.e. under 20% headroom).
2. A market with a real product surface (spreads, F5, NRFI) ships and its
   markets push the projected monthly spend over 450.
3. The low-credit alert fires in a month where the feed actually ran dry, i.e.
   a board published without prices because of quota.
Until then the free tier is correctly sized and the money stays unspent. Do not
upgrade "to be safe" — `data/odds_credits.json` is the evidence, so check it.

## Backtest harness (scripts/backtest.py) — measure the model, tune the knobs

Replays the ACTUAL engine over historical games (calls engine.simulate_game, the
same function the daily board uses — extracted to module level in v0.5 so the
backtest can't drift from production; the refactor was verified byte-for-byte).
Reconstructs every game's inputs STRICTLY as of the morning before it (no
look-ahead): standings?date=<day-before>, pitcher/league byDateRange through the
day before, final scores from the schedule linescore. Reports Brier / log-loss /
accuracy / a calibration table vs the no-skill base rate.

v0.9 adds Rule 6 to the backtest: reconstruct() fetches the trailing-14-day
wOBA point-in-time (byDateRange respects cutoffs, unlike the reliever split)
and `--sweep woba_tax:0,0.04,0.08,0.12` tunes the tax. Two caveats: snapshots
cached before v0.9 lack the wOBA fields (pass --refresh once), and judge a
woba_tax sweep on the TOTALS calibration report too, not just moneyline Brier —
the tax cuts away run totals on triggered games, the same blind spot as the
DISPERSION trap below.

Two honest limits, both documented in the file's header: (1) no historical odds on
the free Odds API tier, so it scores the RAW model's win probabilities, not ROI or
CLV, and can't tune MODEL_WEIGHT — pass --odds-dir with odds_<date>.json to unlock
market comparison + flat-stake ROI; (2) the reliever statSplit IGNORES its date
cutoff (returns full-season, i.e. leaks the future) so the backtest uses the
engine's team-RA fallback for the bullpen rather than the real pen split.

Reconstructed snapshots cache under data/backtest/ (gitignored — do NOT commit;
CI's `git add data/` would otherwise sweep them in). Sweep a knob with
`--sweep prior_ip:30,60,120` (also fip_weight, model_weight, dispersion, hfa,
factor_shrink). NOTE on interpreting sweeps: DISPERSION is a TRAP — lowering it
improves moneyline Brier by dumping unrealistic run variance in, which this
moneyline-only backtest can't see it wrecking the totals; the honest lever for the
raw model's over-dispersion is FACTOR_SHRINK (v0.6), not DISPERSION. Also: a few weeks is
noise — differences under ~0.001 Brier over a few hundred games are not real. Run
a full season (or more) before retuning PRIOR_IP / FIP_WEIGHT / MODEL_WEIGHT off it.
  python scripts/backtest.py --start 2026-04-01 --end 2026-09-28 --sims 3000
  python scripts/backtest.py --days 30 --sweep prior_ip:30,60,120

Test locally without network: `python scripts/engine.py 2026-07-22` against an
existing snapshot, then `python scripts/build_site.py 2026-07-22`; grade with
`python scripts/grade.py <date> --scores-file <fake_scores.json>`; preview
Discord payloads with `python scripts/post_discord.py pick <date> --dry-run`.
Deps: `pip install -r requirements.txt` (numpy, requests, tzdata, cryptography).

Without BOARD_ENCRYPTION_KEY set, the scripts read and write plaintext, which
keeps local work simple. In Actions a missing key aborts the run rather than
publishing picks in the clear.

## Commit and reveal (scripts/crypto_box.py) — the paywall's actual mechanism

The repo is public and the engine is deterministic, so publishing the board (or
even just the snapshot) before first pitch hands every pick to anyone who looks.
Hiding cards on the page does nothing about that. So:

- Morning: the board and snapshot are committed ENCRYPTED, next to a plaintext
  SHA-256 of the board in `data/commitments.json`. Committing pre-game proves
  the picks existed and were not edited; encryption stops anyone reading them.
- Night: grading decrypts, checks the board against its published fingerprint
  BEFORE writing anything, grades, then publishes the plaintext.

The order matters and is not negotiable: the ledger is append-only, so a board
that fails its fingerprint check must never reach it. `grade.py` exits non-zero
and writes nothing if the hashes disagree.

`commitments.json` is append-only like the ledger. A re-run never rewrites a
commitment that was already published.

### The key

`BOARD_ENCRYPTION_KEY` (repo secret, Fernet, generated by `scripts/genkey.py`).
Daniel holds the only backup.

LOSING IT IS UNRECOVERABLE. Any board still encrypted under a lost key can never
be graded and never be revealed: those days would be permanently missing from
the ledger, on a site whose whole claim is that nothing goes missing. Treat it
with more care than the API keys, which are all replaceable.

## The model (engine.py) — v0.6, documented on the site's Methodology tab

- Team run rates (RS/RA per game from standings), normalized to league average and
  (v0.6, FACTOR_SHRINK=0.6) regressed 40% toward it in engine.simulate_game() —
  season rates overstate the true talent spread (noisy, schedule-unadjusted). The
  full-season backtest (1414 games) showed the raw model over-dispersed on
  favourites (0.6-0.7 bucket predicted 64%, went 55%); the shrink dropped Brier
  from 0.2504 to 0.2482 (below the no-skill baseline) and de-biased that bucket,
  WITHOUT the run-total distortion that lowering DISPERSION would have caused.
- v0.4 RUN PREVENTION = STARTER over 5.5/9 of the game + the team's real BULLPEN
  ERA (reliever statSplit, fetched in fetch_data.py as teams[].pen_era) over the
  rest, each vs league ERA. Replaces v0.3's team-RA×starter-blend, which
  double-counted the staff and modelled no bullpen. Missing pen_era → falls back
  to team RA/G (engine.prevention()), so old snapshots still grade.
- v0.5 STARTER RATE is not raw ERA but engine.starter_rate(): ERA blended with
  FIP (FIP_WEIGHT=0.5; strips defense/luck; needs pitcher hr/bb/hbp/k + the
  snapshot's league_pitching totals for the FIP constant) then regressed toward
  league average by innings pitched (PRIOR_IP=60, so a 20-IP hot streak is pulled
  toward the mean). Missing FIP components → ERA-only, still regressed. Board logs
  each SP's stab_rate.
- Static park factors (dict in fetch_data.py); home advantage ×1.026 on runs.
- Negative binomial scoring (Gamma-Poisson, DISPERSION=2.4); extra innings
  simulated at 1.9× per-inning rates until decided.
- Per-date seed = int(YYYYMMDD) → every board is reproducible. Never use
  wall-clock randomness.
- v0.3 MARKET BLEND: the raw sim is systematically overconfident, so before any
  edge is priced the model win prob is shrunk toward the de-vigged market:
  blended = MODEL_WEIGHT·model + (1−MODEL_WEIGHT)·market (MODEL_WEIGHT=0.5).
  Pick side, confidence, fair line, edge, EV and sizing all run on the blended
  prob; the raw prob is kept for Rule 8 and logged (model_conf/p_mkt_devig) for
  calibration. No market line → pure model (no blend). Board stamps
  engine_version="0.14-watchlist".
- v0.13 BEST-PRICE: fetch_data.consolidate_odds() (pure, unit-testable) records
  the median consensus AND the best price per side across US books with the
  book name; totals best prices only at the consensus line. Edge/EV/Kelly and
  Rule 2 evaluate at BEST price (what a bettor can actually get; raises every
  measured edge 1–3 pts); the market blend, Rule 8 divergence, and CLV stay
  anchored to the CONSENSUS medians so best-price can't flatter them. Pick
  labels carry the book; ledger odds_basis = {price, book, basis, consensus}.
- v0.14 WATCH LIST: engine emits watch_picks (0u, tracked-not-staked), three
  tagged sources: "market-proving" (totals clearing edge+divergence gates while
  totals are paper-only), "edge-band" (ML near-misses with edge in
  [WATCH_EDGE_MIN=1.0%, MIN_EDGE) — the visible near-miss band; Rule 2 pivots
  excluded, their edge prices the ML not the RL), "R8-hold" (Divergence
  Governor holds, recorded as the ML side). grade.py grades them nightly into
  data/watchlist.json (append-only, flat 1u PAPER, aggregates per tag/market)
  which NEVER mixes with ledger.json in any display, total, or post. Site
  renders the section + the promotion criteria verbatim; members Discord post
  carries a "WATCH — tracked, not staked" divider embed.
- v0.9 RULE 6 DETECTION / v0.12 TAX: fetch_data.py pulls each team's
  trailing-14-day wOBA (one byDateRange hitting call; wOBA computed from
  components with static weights — error cancels in the team-vs-league gap)
  plus the league's over the same window. engine.simulate_game() takes
  league_woba, computes the Rule 6 trigger (away wOBA trails league by >
  WOBA_GAP=.035) and applies WOBA_TAX=0.08 to the away run rate when fired.
  0.08 was SET BY THE FULL-SEASON SWEEP (2026-07-29, 1454 games, 152
  triggered): untaxed model over-projected triggered totals +0.31 runs, 0.08
  zeroed the bias AND won Brier on all games and the fired subset; the
  playbook's 12% over-corrected. Missing data → cards say "manual review",
  never "passed". Re-tune only via the sweep, never by taste.
- Market math: edge = blended prob − implied prob of offered price; divergence =
  RAW model prob − de-vigged market prob; EV per unit; quarter-Kelly capped by tier.

SITE COPY (House Rules 4 & 8): the Methodology tab was updated 2026-07-25 to match
v0.3–v0.5 — it now describes the market blend ("We anchor to the market"), the
stabilized ERA+FIP+regression starter rate, the real bullpen ERA, and a
"we lean on the market by design" line in the "does NOT do" list. CLV ON THE LEDGER TAB
SHIPPED 2026-08-08 as CONDITIONAL rendering: a "Close · CLV" column per pick
(— for picks with no captured close; never backfilled) and a CLV tile that
renders only while aggregates.clv.graded_with_clv > 0, so the site can never
show a CLV figure the ledger doesn't hold. Monthly + bet-type splits render
from the full entry list at build time (derived, never stored). Still
deliberately NOT added: backtest numbers — cite calibration only after a
full-season run, not the preliminary window.

## Totals paper track (v0.7) — measuring before staking

The full-season backtest showed the model's run-total distribution is well-calibrated
(bias +0.08 runs over 1414 games, flat PIT) while its moneyline barely beats a coin
flip — so totals is the arena where a fundamentals model has a real shot. v0.7 starts
MEASURING it without risking the record:

- fetch_data.py / fetch_closing.py now capture the over/under PRICES (not just the line).
- engine.py logs a `total_pick` per game (side = the one the model rates above the
  de-vigged market, with line/price/model_p/edge). It is NOT staked and NOT in the
  moneyline exposure — purely recorded.
- grade.py's grade_totals() paper-grades those into data/totals_ledger.json (SEPARATE
  from the real ledger): W/L/PUSH at a flat 1u, plus totals CLV = closing line movement
  toward our side (runs), price de-vig as the tiebreak when the line is unchanged. Own
  aggregates (record, win%, paper ROI, avg_clv_runs, beat_close%).
- WEATHER SHIPPED (v0.10, 2026-07-29): fetch_data.py pulls the game-hour forecast per
  OUTDOOR park from Open-Meteo (free, no key; static PARK_COORDS with roof flags —
  roofed/retractable parks get none; UTC hourly grid matched to the game's UTC start,
  no tz math). engine.py runs a SECOND sim on a SEPARATE rng stream (SEED+1) with
  temperature (±WX_TEMP_COEF per °F around 70) plus a hot-day (≥85°F) kicker vs
  fly-ball starters (air-out-share proxy, ao/go now in pitcher stats) — used ONLY for
  total_pick. The staked board is bit-identical with or without weather data (the
  separate stream is what guarantees later games' draws don't shift). total_pick logs
  wx_applied/wx_mult/model_p_nowx and grade.py stamps wx_applied into the totals
  ledger, so the track's win%/CLV can be split weather vs not — that split IS the
  validation. Wind speed/direction are fetched but NOT modeled (park orientation
  azimuths would be needed to sign the wind effect; don't guess them).
- Honest ceiling: calibrated-to-reality ≠ beats-the-market. The closing total is sharp
  too. The real edge comes from adding info the opening line lags — weather (now
  measuring), then bullpen availability, then umpires. This track is
  the gate: only promote totals to real staked plays (real board + ledger + site) once
  its CLV is convincingly positive. Nothing staked on the public site changes until then.

## Engine version history (update this table on every version bump)

| version | date (2026) | change |
|---|---|---|
| 0.1 | Jul 21 | initial engine: team rates, park, HFA, negative binomial, Rules 2/4/7 |
| 0.2 | ~Jul 23 | market gates: real odds, edge gate, Rule 8, quarter-Kelly, Rule 2 on market lines |
| 0.3 | ~Jul 24 | market blend (MODEL_WEIGHT=0.5 toward de-vigged consensus) |
| 0.4 | ~Jul 24 | real bullpen ERA over the non-starter innings |
| 0.5 | ~Jul 25 | stabilized starter rate (ERA+FIP, regressed by IP); simulate_game extracted for the backtest |
| 0.6 | ~Jul 26 | FACTOR_SHRINK=0.6 team-rate regression (full-season backtest: Brier 0.2504→0.2482) |
| 0.7 | ~Jul 28 | totals paper track (total_pick + totals_ledger.json; measuring before staking) |
| 0.8 | Jul 29 | Rule 2 day/night caps −180/−170, venue-local (playbook adoption; strictly tighter than road −180/home −220) |
| 0.9 | Jul 29 | Rule 6 road-wOBA detection (14-day byDateRange splits), tax gated at 0 |
| 0.10 | Jul 29 | weather on the totals paper track (second sim, separate rng stream; temp + hot-day fly-ball kicker) |
| 0.11 | Jul 29 | ✳ Best of Board lean when the gates leave the members channel empty (House Rule 6 amendment) |
| 0.12 | Jul 29 | WOBA_TAX=0.08, sized by full-season sweep (1454 games: zeroed +0.31-run triggered-totals bias, won Brier; 12% over-corrected) |
| 0.13 | Aug 6 | best-price odds ingestion: edge/EV/Kelly/Rule 2 at best book price w/ book names; consensus kept for de-vig + CLV |
| 0.14 | Aug 6 | Watch List tier: watch_picks (market-proving / edge-band / R8-hold), watchlist.json paper record, site + Discord surfaces |
| 0.15 | Aug 9 | Daily Pick strategy: always-on, separate ledger (daily_ledger.json), precommitted eligibility + deterministic ranking, 0u proving window through Sep 8 (House Rules 6/9 amended per Daniel — see below) |

## Circuit breakers (the product's identity — never weaken silently)

- Rule 2 (v0.8, 2026-07-29): no favorites −180+ at night / −170+ in day games
  (market line; day = first pitch before 5 PM venue-local, static venue→tz map in
  engine.py) → pivot to −1.5 run line. Replaced v0.2's road −180 / home −220 split
  per docs/SYSTEM_PLAYBOOK.md — strictly TIGHTER (home favorites −220..−181 that
  the old rule allowed straight now pivot too). Site rulecard copy updated same day.
- Rule 4 (heuristic): starter < 60 IP this deep in season → units downgraded one tier.
- Rule 6 (detection v0.9, tax v0.12, both 2026-07-29): away trailing-14d wOBA
  vs league (threshold .035, MLB API byDateRange splits) → 8% run tax on the
  away rate when fired. The size came from the full-season sweep, NOT the
  playbook's 12% (which over-corrected); cards print the tax on every flag;
  missing data reads "manual review", never "passed". Re-tune only via
  `--sweep woba_tax` on a full season (Actions "Backtest sweep" workflow).
- Rules 3/5: NOT automated (need Statcast telemetry) — always surfaced as
  "manual review" on cards, never silently claimed. Automating these is roadmap.
- Rule 7: TBD starter → game scratched, published with reason.
- Rule 8 (Divergence Governor): |model − de-vigged market| > 12 pts → held as
  lean, no allocation ("the market knows something our inputs don't").
- Edge gate: < 2-pt edge vs offered price → no allocation.
- Sizing: min(confidence tier 3u/2u/1u, quarter-Kelly), 10u daily exposure cap.

## SYSTEM_PLAYBOOK (docs/SYSTEM_PLAYBOOK.md) — the adjustment spec, adopted 2026-07-29

Daniel's rule spec for where the model should go. It is a SPEC, not live
behavior: a playbook rule exists only once implemented in engine.py, and House
Rule 4 forbids claiming otherwise. The doc's appendix carries the authoritative
per-rule status; summary: Rule 2 day/night caps LIVE (v0.8), Rule 7 was already
live, Road wOBA Suppression LIVE END-TO-END (detection v0.9; tax v0.12 at 8%,
sized by the 2026-07-29 full-season sweep — the playbook's 12% over-corrected), Thermal/Venue
LIVE on the totals paper track (v0.10: temperature multiplier + hot-day fly-ball
kicker on the paper pick only — the literal 35% HR/FB tax cannot map onto a model
with no HR/FB component), Pérez/Cole rules are blocked on a props product that doesn't exist,
Pederson/Two-Out-WHIP rules are dormant until NRFI ships, and the parlay
templates/commands have no product surface. When touching the engine, check
changes against BOTH this playbook and the circuit-breaker section above; if
they conflict, the tighter rule wins and the conflict gets flagged to Daniel.

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
   position too." Never manufacture a pick. (Amended 2026-08-09, per Daniel:
   the always-on DAILY PICK strategy (v0.15) may fill the free slot on
   no-qualified days. It is NOT a manufactured Qualified Play: separate
   strategy, separate ledger, precommitted lower bar — positive edge at best
   price, model+blend side agreement, no Rule 8 hold — 0u through its proving
   window, always labeled as the lower-bar strategy. When even the Daily
   Pick's rules produce nothing, the no-play message still runs unchanged.)
   (Earlier amendment 2026-07-29, per Daniel:
   whenever the gates leave the members channel empty — 0 published plays, or
   exactly 1 which becomes the free pick — the engine marks a ✳ Best of Board
   lean: the top non-Rule-8 lean by edge, at 0 units, failed gates printed on
   the card. It is explicitly NOT staked, never enters the ledger, and the
   no-play message still runs alongside it. Site shows the ✳ flag; the members
   Discord post carries it so members always see at least one play. Rule 8
   demotions are never eligible.)
7. Held plays are held, not hidden. Every one is fingerprinted before first
   pitch and published in full after grading, win or lose. Withholding a pick
   before the game is the product; withholding it after is fraud.
8. Site copy must describe what the site actually does today. Two separate
   bugs have already shipped where copy promised something the code no longer
   did (the board "live on Today's Board" after gating, and the Discord join
   block offering held plays to free joiners). Re-read the user-facing copy
   whenever behaviour changes.
9. Gate thresholds change only through the gate study (adopted 2026-08-06 from
   the v0.3 addendum, Section C): MIN_EDGE, the Rule 8 cap, and (later) the
   Kelly fraction may change ONLY if the proposed band is profitable on the
   tuning season AND validates on the held-out season AND the change ships as
   a version bump with both seasons' numbers recorded in the version-history
   table. No threshold changes outside this process — especially not during a
   cold streak or a quiet-board streak; that is precisely when the temptation
   peaks and the discipline is the product. (The 2026-08-01 board confirmed
   the quiet board is a true reading — all candidate edges negative at
   consensus prices — not a stuck gauge. Volume comes from better prices,
   more candidates, and better inputs, never from quietly loosening gates.
   The near-miss band is visible on the Watch List instead.)
   (Amendment 2026-08-09, per Daniel, on the Daily Pick: DAILY_EDGE_MIN is a
   NEW strategy's precommitted floor, not a change to any Qualified gate —
   MIN_EDGE, the Rule 8 cap, and sizing are untouched and the records never
   mix. The historical sweep this floor would ideally come from is impossible
   today (no historical odds — backtest.py's limit), so the floor is the most
   conservative defensible value (strictly positive edge at best price) and
   the strategy proves itself FORWARD at 0u with a SCHEDULED staking review
   (2026-09-08, target flat 0.25u). The review happens on that date only —
   never early, never in response to a streak in either direction.)

## Config (GitHub → Settings)

- Secrets:
  - BOARD_ENCRYPTION_KEY — REQUIRED. No backup exists beyond Daniel's copy.
  - ODDS_API_KEY (optional; without it edge/Kelly/Rules 2+8 inactive, site says so)
  - DISCORD_WEBHOOK_URL (optional; free pick → public channel)
  - DISCORD_WEBHOOK_URL_MEMBERS (optional; held plays → members channel)
  - DISCORD_WEBHOOK_URL_ALERTS (optional; ops/admin channel for pipeline failure
    alerts, the heartbeat, and the low-credit warning. Unset → alerts fall back
    to the members channel, then the free channel. Set this: a members channel
    is a fine fallback but a poor permanent home for ops noise.)
  - RESEND_API_KEY (optional; daily free-pick email — skipped when unset)
  - X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET (optional; all
    four required to post the daily record to X — OAuth 1.0a User Context)
  - FB_PAGE_ACCESS_TOKEN (optional; daily record → Facebook Page)
- Variables: SITE_URL, DISCORD_INVITE_URL (join prompt omitted when unset),
  RESEND_SEGMENT_ID (all-subscribers segment; email step no-ops when unset),
  EMAIL_FROM (optional; defaults to picks@send.openledgersports.com),
  FB_PAGE_ID (optional; the Page to post the record to).
- Discord: server "Open Ledger Sports", #free-pick (public) and #members-only
  (gated by a Whop-managed "Members" role; Whop Bot must sit ABOVE Members in
  the role list or grants silently fail).
- Actions → General → Workflow permissions must be "Read and write".
- Pages: deploy from branch `main`, folder `/ (root)`.
- DNS (Cloudflare, DNS-only / grey cloud — the records point straight at GitHub
  Pages, no Cloudflare proxy). The apex needs BOTH IPv4 and IPv6, or the site is
  unreachable on IPv6-only networks (most cell carriers) while still working on
  IPv4 wifi — it presents in mobile browsers as "address is invalid", not an
  outage (diagnosed 2026-07-25: apex had only A records; www worked on wifi but
  redirects to the apex so it failed on cellular too).
  - `@` (apex)  A     185.199.108.153 / .109.153 / .110.153 / .111.153
  - `@` (apex)  AAAA  2606:50c0:8000::153 / 8001::153 / 8002::153 / 8003::153
  - `www`       CNAME dsimny.github.io  (301s to the apex)
  If the site loads on wifi but not cellular AFTER the AAAA records have
  propagated, suspect a carrier gambling-content filter on the line, not DNS.

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

Scheduling: GitHub cron proved unreliable (grading ran 5h late, the board
3h38m late on 2026-07-22). The real trigger is now cron-job.org, which POSTs
to the workflow_dispatch API on time:
  - OLS Grade Ledger  -> grade-ledger.yml/dispatches   daily 4:10 AM ET
  - OLS Morning Board -> morning-board.yml/dispatches   daily 11:10 AM ET
Auth is a fine-grained GitHub PAT (repo dsimny.github.io, Actions read/write
only, expires 2027-07-23), held in cron-job.org, not in the repo.

The GitHub cron schedules were REMOVED 2026-07-23. They fired hours late AND
each late run re-posted the free pick and members board to Discord (the
idempotency guard blocks a board rebuild but not a Discord re-post), spamming
duplicate notifications. cron-job.org fires each job once, on time, via the
workflow_dispatch API, so the workflows now have only `workflow_dispatch` and
no `schedule`. If cron-job.org ever fails, trigger the workflow manually from
the Actions tab. NOTE: post_discord is NOT idempotent — any second run of the
morning board (manual or otherwise) will re-post to Discord.

First real ledger, 2026-07-22 slate graded 2026-07-23: 2-1, -0.32u, ROI
-15.8%. The grade engine fetched real final scores and priced W/L/pnl
correctly. Both premium picks won; the free pick lost.

Gotcha worth remembering: the scripts date everything by US Eastern, so
triggering "Morning board" late at night builds a board for a slate that has
already been played. The 2026-07-21 board created that way was removed before
grading so the ledger would not open with picks that were never live before
first pitch. Only run it manually between midnight ET and first pitch.

## Email service — IN PROGRESS as of 2026-07-23 (pick up here)

Goal: (1) email the free pick daily to a list, (2) a content-free "board's up,
check Discord" nudge to premium members. Beehiiv was tried and DROPPED (its
automated RSS-to-send needs a Max/Enterprise tier not worth it for an empty
list; all beehiiv code was removed from the site). Now using RESEND.

Done:
- Resend account "openledgersports". API key saved as the GitHub secret
  RESEND_API_KEY (Full access).
- Sending domain send.openledgersports.com VERIFIED (DKIM/SPF/MX live in
  Cloudflare via Resend auto-configure; DMARC optional, not yet added — add
  TXT _dmarc = "v=DMARC1; p=none;" for deliverability).
- feed.xml already published (tool-agnostic RSS of the free pick, built by
  build_site.py -> feed.py; premium never in it).
- scripts/send_email.py + a "Email the free pick" step in morning-board.yml.
  It reads the day's item from data/feed_items.json (so the email is byte-for-
  byte the free pick — never a premium play), POSTs it as a Resend broadcast
  (segment_id, from, subject, html/text, send:true), and — unlike post_discord
  — is IDEMPOTENT: it records "sent" in data/post_status.json and refuses to
  re-send that date, so a repeat morning run never double-mails the list. Skips
  cleanly (exit 0) if RESEND_API_KEY or RESEND_SEGMENT_ID is unset; never fails
  the board. Dry-run: `python scripts/send_email.py pick <date> --dry-run`.
  Payload shape confirmed 2026-07-25 against Resend's current docs: segment_id +
  send:true (in the create call) is correct — audiences were renamed to segments.
  BUG FIXED 2026-07-25: the emailer recorded idempotency under mode "pick", which
  collides with post_discord's "pick" key in the SHARED post_status.json —
  post_discord runs first each morning and its record() deletes any (date,"pick")
  entry, so a repeat morning run wiped the emailer's "sent" guard and would
  double-mail. The emailer now records under a dedicated mode "email" (STATUS_MODE).
  Still needs RESEND_SEGMENT_ID + a contact in the segment to actually go live (below).

Resend model gotchas learned:
- "Audiences" are DEPRECATED in favor of SEGMENTS. Broadcasts send to a
  segment_id (POST /broadcasts: segment_id, from, subject, html/text,
  send:true or scheduled_at). No audience_id. Contacts.create needs no
  audienceId.
- Resend has NO drop-in signup form. Capturing site signups needs a small
  server-side proxy (Cloudflare Worker — user has Cloudflare) because the API
  key can't be client-side.

Next steps, in order:
1. GO-LIVE for the daily send (code done, just config + one test): user gets an
   "all-subscribers" SEGMENT id from the Segments tab and sets it as the repo
   VARIABLE RESEND_SEGMENT_ID (Settings → Secrets and variables → Actions →
   Variables). Add yourself as a contact in that segment first, then trigger
   "Morning board" once and confirm the pick lands in your inbox. Until the
   variable is set the email step no-ops, so shipping the code now is safe.
2. Capture: a Cloudflare Worker that receives the site form POST and calls
   Resend contacts.create. Re-add the email form on the free-pick page pointing
   at the Worker. (The .emailcap wrapper CSS is still in build_site.py.)
3. Premium nudge: Whop webhook (membership went valid/invalid) -> sync members
   into a "premium" segment -> daily content-free nudge. Lower priority; premium
   members already get the board on Discord.

Other open threads (not code): testers have promo codes and an affiliate
referral link; Instagram + Facebook accounts created (channel/social copy and
the two canonical links — openledgersports.com and discord.gg/8EVazMtydq —
were provided); Whop upgrade button on the site is still dormant
(WHOP_CHECKOUT_URL unset) pending a real record.

## Roadmap (in trust-building order)

1. Live multi-book best price via The Odds API. [SHIPPED 2026-08-06, v0.13:
   best price per side + book name; consensus median retained for de-vig/CLV.]
2. Log opening vs closing lines → CLV tracking on the ledger. [DONE: capture
   code 2026-07-24, cron-job.org triggers live (capture runs several times
   daily), ledger-tab surfacing shipped 2026-08-08 (conditional Close · CLV
   column + tile + monthly/bet-type splits in build_site.py). First staked
   pick graded from here on carries its close automatically.]
3. Statcast feeds → automate Rules 3/5/6, retire "manual review" labels.
4. Third-party verification (Pikkit/Juice Reel) after ~1 month of record.
5. NRFI market (activates the two dormant breakers). Then, only once the ledger
   has earned it: paid tier (Whop on Discord, ~$20-30/mo lane).
