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

.github/workflows/capture-closing.yml (several times/day via cron-job.org)
  scripts/fetch_closing.py → data/closing_<date>.json  (last pre-first-pitch line per game, for CLV)

.github/workflows/grade-ledger.yml   (daily 08:10 UTC)
  scripts/grade.py        → data/ledger.json           (final scores → W/L/VOID, units, ROI, CLV; APPEND-ONLY)
                          → data/board_<date>.json     (the reveal: .enc replaced by plaintext)
  scripts/build_site.py   → index.html                 (ledger tab refreshed)
  scripts/post_discord.py recap                        (posts results, wins AND losses)
  scripts/post_social.py  x / facebook                 (daily record → X + FB, wins AND losses)
```

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

## Backtest harness (scripts/backtest.py) — measure the model, tune the knobs

Replays the ACTUAL engine over historical games (calls engine.simulate_game, the
same function the daily board uses — extracted to module level in v0.5 so the
backtest can't drift from production; the refactor was verified byte-for-byte).
Reconstructs every game's inputs STRICTLY as of the morning before it (no
look-ahead): standings?date=<day-before>, pitcher/league byDateRange through the
day before, final scores from the schedule linescore. Reports Brier / log-loss /
accuracy / a calibration table vs the no-skill base rate.

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
  engine_version="0.6-team-regression".
- Market math: edge = blended prob − implied prob of offered price; divergence =
  RAW model prob − de-vigged market prob; EV per unit; quarter-Kelly capped by tier.

SITE COPY (House Rules 4 & 8): the Methodology tab was updated 2026-07-25 to match
v0.3–v0.5 — it now describes the market blend ("We anchor to the market"), the
stabilized ERA+FIP+regression starter rate, the real bullpen ERA, and a
"we lean on the market by design" line in the "does NOT do" list. Deliberately NOT
added yet (not live): CLV figures and backtest numbers. Surface CLV on the ledger
tab only once capture-closing is running and the ledger actually carries clv_pts;
cite backtest calibration only after a full-season run, not the preliminary window.

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
7. Held plays are held, not hidden. Every one is fingerprinted before first
   pitch and published in full after grading, win or lose. Withholding a pick
   before the game is the product; withholding it after is fraud.
8. Site copy must describe what the site actually does today. Two separate
   bugs have already shipped where copy promised something the code no longer
   did (the board "live on Today's Board" after gating, and the Discord join
   block offering held plays to free joiners). Re-read the user-facing copy
   whenever behaviour changes.

## Config (GitHub → Settings)

- Secrets:
  - BOARD_ENCRYPTION_KEY — REQUIRED. No backup exists beyond Daniel's copy.
  - ODDS_API_KEY (optional; without it edge/Kelly/Rules 2+8 inactive, site says so)
  - DISCORD_WEBHOOK_URL (optional; free pick → public channel)
  - DISCORD_WEBHOOK_URL_MEMBERS (optional; held plays → members channel)
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
  Only the wiring is unverified live — it has no segment to send to yet (below).

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

1. Live multi-book best price via The Odds API (fetch_data.py already consumes
   the key; upgrade from median-consensus to per-book best price + book name).
2. Log opening vs closing lines → CLV tracking on the ledger. [CODE SHIPPED
   2026-07-24: scripts/fetch_closing.py + capture-closing.yml + grade.py CLV
   fields/aggregate. REMAINING: set up the cron-job.org triggers for
   capture-closing, and surface CLV on the site's ledger tab (build_site.py).]
3. Statcast feeds → automate Rules 3/5/6, retire "manual review" labels.
4. Third-party verification (Pikkit/Juice Reel) after ~1 month of record.
5. NRFI market (activates the two dormant breakers). Then, only once the ledger
   has earned it: paid tier (Whop on Discord, ~$20-30/mo lane).
