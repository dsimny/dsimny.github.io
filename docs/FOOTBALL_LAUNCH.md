# Football launch plan — getting premium live for the 2026 season

Written 2026-08-26. This is an EXECUTION plan, not a spec and not a
pre-registration. It commits to no methodology; `docs/FOOTBALL_PIPELINE.md`
(fp-v0.1, frozen 2026-08-24) already did that and this plan may not amend it.

WRITTEN ON `main` ON PURPOSE, even though most files it names still live on
`claude/nfl-football-model`. Step A is the merge, so this doc describes main's
near future; its sibling football docs arrive with that merge.

Required reading before touching anything here:
- `docs/FOOTBALL_PIPELINE.md` — the product spec and the selection rule.
- `docs/FOOTBALL_RESULT_T24.md` — the model does not beat the market.
- `docs/FOOTBALL_RESULT_PRICE.md` — best-price shopping does not clear the vig.
- `PLAN-paid-tier.md` — the paywall mechanism, built and proven on MLB.
- `CLAUDE.md` — house rules, config names, the commit-and-reveal section.

## 0. The two hard deadlines

Everything else in this document can slip. These two cannot.

| date (UTC) | deadline | consequence of missing it |
|---|---|---|
| **2026-08-28 10:00Z** | football capture running automatically | The first NCAAF Week 1 T-24 window (North Carolina @ TCU). A missed T-24 window is gone permanently — re-acquiring it means buying historical odds again. Nothing else in this plan is unrecoverable; this is. |
| **Thu 2026-09-10** | NFL season-type allowlist implemented (gap C) | Preseason entries book into a permanent append-only ledger looking exactly like real ones. `FOOTBALL_PIPELINE.md` section 3a names this failure explicitly and calls it quiet, which is what makes it dangerous. |

Both dates are MEASURED, not assumed — `capture_schedule.py` reads real kickoffs
and reports the windows. Re-run it rather than trusting this table:

    python scripts/football/capture_schedule.py --sport ncaaf
    python scripts/football/capture_schedule.py --sport nfl --days 18

As at 2026-08-26 that reported 16 NCAAF windows and 36 NFL windows upcoming,
0 satisfied, 0 missed. **A preseason NFL T-24 window opens earlier still
(2026-08-26 17:00Z, Steelers @ Bills).** Missing that one costs only a
rehearsal, not evidence — preseason is captured and never graded (section 3a) —
but it is the cheapest possible end-to-end test of the capture path against a
real board, so take it if the workflow is ready in time.

CAPTURE BEFORE YOU PUBLISH. The capture path is independent of every other gap
here — it needs no site, no ledger, no writeup, no paywall. Turn it on first and
let NCAAF Week 1 accumulate on disk while the rest is built. Data captured and
unused costs nothing. Data not captured cannot be bought back cheaply.

## 1. What is ALREADY DONE — do not rebuild any of it

Recorded here because the amount already finished is not obvious from `main`,
where none of the football work is visible yet.

**The commercial side is complete and end-to-end verified.**
- Whop product "Open Ledger Sports Member", $30/month, no trial. Checkout link
  is live and unlisted only because nothing points at it (URL in
  `PLAN-paid-tier.md`).
- Identity verified, bank account connected for payouts (2026-07-22).
- Discord app connected: grants the Members role, removes it on cancel.
  "Assign this role after past due bill" left EMPTY on purpose.
- The whole grant chain was VERIFIED 2026-07-22 with an outside tester, not the
  server owner: checkout -> membership -> connect Discord -> Members role ->
  `#members-only` visible.
- `#free-pick`, `#members-only` and `#ols-log` all exist and post.

**The paywall mechanism is proven in production on MLB.**
- `scripts/crypto_box.py` (Fernet) + SHA-256 commitment, tested against nine
  scenarios including a tampered board and a missing key in CI.
- Verification runs BEFORE anything is written to the append-only store. That
  ordering was a real bug found in testing and it is load-bearing; reuse the
  pattern for football rather than re-deriving it.

**The football layer-1 math is written and characterised.** On
`claude/nfl-football-model`: `capture_schedule.py` (kickoff-relative windows,
not weekday assumptions), `fetch_odds.py`, `price_test.py`, `pipeline_rule.py`
(the rule's 2022-2024 behaviour, run before launch on purpose),
`grade_football.py` (NCAAF), `espn_ncaaf.py`, `teams.py`, plus the frozen spec,
the writeup template, and both research results published.

**The negative results are published.** fb-v0.1 and fb-v0.2 both say the market
is efficient at the moment we can act. That is settled and is the reason the
product is sold as process and receipts. Nothing in this plan reopens it.

## 2. The gaps — what actually has to be built

| # | gap | why it blocks launch |
|---|---|---|
| A | ~~Branch not merged~~ **DONE 2026-08-26** (merge commit `b55f732`) | `main` is what GitHub Pages deploys. All 32 football commits are now on it; no behaviour changed, because no workflow runs them yet. |
| B | **Board builder DONE 2026-08-26** — `market.py` + `board.py` + `selftest_board.py` | The rule now lives in ONE place that both the board and the grader call. Remaining: wiring it to a workflow, and decision 6c below. |
| C | **NFL grading DONE 2026-08-26** — `espn_nfl.py`, the season-type allowlist, `--sport nfl`, and `selftest_allowlist.py` | Beat the 2026-09-10 deadline. Two real bugs found and fixed on the way; see below. |
| D | `data/football/football_ledger.json` does not exist | First entry is permanent under House Rule 1. Create it deliberately, not as a side effect of a test run. |
| E | **Layer 2 BUILT 2026-08-26** — `writeup.py` + `selftest_writeup.py` | Claude via the Messages API, with the numeral validator enforced. Needs `ANTHROPIC_API_KEY` set before it can generate; the round-trip is unproven until then. |
| F | **Site surface BUILT 2026-08-26** — `page.py` + `selftest_page.py` | `/football/` hub and `/football/<week>/`. Redacted before grading, full after. Still to wire: a link from the MLB home page and a rebuild step in CI. |
| G | **Discord delivery BUILT 2026-08-26** — `discord.py` + `selftest_discord.py` | `free` to the public channel, `slate` (full slate + premium) to members. Idempotent per slate week. Needs the webhooks already configured for MLB. |
| H | **Workflows DONE 2026-08-26** — `football-capture.yml` (capture + board, merged) and `football-grade.yml`, plus the home-page link | One external trigger hourly at :37; grade daily at 11:00Z. |
| I | ~~Odds credits~~ **NOT A GAP** — paid tier live, 71,695 remaining | Section 6a. Recorded because the free-tier assumption shaped earlier decisions and should not be inherited. |
| J | **Copy DONE 2026-08-26** — home-page premium block rewritten, football CTA added | Both sports described separately because they claim different things. Invisible until K. |
| K | `WHOP_CHECKOUT_URL` unset | One repo variable. This is the go-live switch. Flip it LAST. |

### B — the live board builder — BUILT 2026-08-26

Shipped as three files:

- **`scripts/football/market.py`** — layer 1, and now the ONLY implementation of
  it. Eligibility, de-vigged consensus, best takeable price, effective
  overround, the corroboration guard and the side the rule takes. Knows no
  sport: every function takes quotes, team names and timestamps.
- **`scripts/football/board.py`** — the pre-kickoff board. Covered games with
  their data blocks, the premium and free plays, every excluded game named with
  its reason, the >25% manual-review flag, and an encrypted board plus a
  plaintext SHA-256 in football's own `commitments.json`. Publishes nothing in
  the clear.
- **`scripts/football/selftest_board.py`** — run after touching either.

`grade_football.py` was REFACTORED to import from `market.py` rather than keep
its own copies. That is the whole point: the grader and the board now call the
same `evaluate()` on the same snapshot, so a play the board publishes is by
construction a play the grader accepts.

VERIFIED BY EQUIVALENCE, following the repo's own precedent for
`engine.simulate_game` in v0.5 (extract, then prove nothing moved):
- Every extracted function compared against the pre-refactor copy across 399
  real events — **11,340 assertions, all equal**.
- `build()` compared end to end on a two-snapshot fixture — **32 candidates and
  79 skip reasons identical**, with win/loss/push all exercised.
- One intended change: the unclassified-book message now ends "can be used"
  rather than "can be graded", because two callers share it.

That one-time proof needed a pristine pre-refactor copy and is not re-runnable.
The invariant that must hold forever — board and grader agree — is asserted by
`selftest_board.py` instead, which is.

WHY THE SELF-TEST NEEDS A FIXTURE. Only one capture per sport exists and no game
has been played, so the real data cannot exercise this path at all: today's
board correctly covers 0 games and names all 8 as "no T-24 capture yet". The
fixture rebuilds what a week of capturing leaves on disk — one capture per
kickoff, each at that game's T-24 — from the real capture's real books and
prices. Only timestamps move. It cannot invent books that had not opened yet,
which is why the college games two weeks out stay excluded, correctly.

### The original specification, for reference

The one genuinely new component. Given the latest captures for a slate week it
must:

1. apply section 3's coverage filter (>=5 eligible books any tier, quotes within
   15 min of snapshot, resolvable kickoff and teams, recommendation available at
   a Tier-1 book corroborated by >=2 Tier-1 books);
2. emit the per-game layer-1 data block — exactly the fields in
   `FOOTBALL_WRITEUP_TEMPLATE.md` section 2 and nothing else;
3. apply section 4's rule to assign rank 1 -> premium, next qualifier -> free;
4. list every excluded game as NO MARKET, never silently dropped, and flag
   "manual review" if the excluded share exceeds 25%;
5. encrypt the board to `data/football/board_<week>.enc` and commit its SHA-256
   before kickoff, mirroring the MLB commit-and-reveal path.

**IT MUST SHARE CODE WITH `grade_football.py`, NOT REIMPLEMENT IT.** The grader
is the thing that books the record, and the spec already says the grader wins
where the two disagree. Two independent implementations of the same rule will
drift, and the drift will surface as a premium play the ledger refuses to grade.
Extract the shared coverage/consensus/selection functions into a module both
import.

### E — layer 2 — BUILT 2026-08-26

`scripts/football/writeup.py` (generator + validator) and
`scripts/football/selftest_writeup.py` (17 adversarial cases).

**Model: Claude, `claude-sonnet-5` by default**, over the Messages API using
plain `requests` — no new dependency. Sonnet because the task is constrained
narration over fixed numbers rather than open reasoning, and it runs ~57 times a
week. `OLS_WRITEUP_MODEL` overrides it; `claude-opus-5` is a one-variable change
if the prose should be richer. The system prompt is sent with `cache_control`,
since it is identical for every game on the slate and only the data block
changes.

**NEW CONFIG REQUIRED: `ANTHROPIC_API_KEY` as a repo secret.** Until it is set,
`annotate()` marks every game `no writeup (no API key configured)` and the board
publishes numbers without prose — the same degrade-never-die rule as a missing
Discord webhook. **The API round-trip is therefore UNPROVEN**; the validator,
the prompt and the degrade path are all tested, the live call is not.

**A CONFIGURED KEY THAT STOPS WORKING IS NOT ALLOWED TO BE SILENT.** Degrading
rather than raising is right — a board with numbers and no prose is still a
board — but it means an expired key, a dry balance or a typo'd model name
produces a green run, a normal-looking page, and prose that quietly stopped
appearing. That is the shape of the dead free-pick webhook that went unnoticed
for a day, and it is why `post_status.json` exists.

So `annotate()` returns a status, and when a key IS configured but fewer than
half the games get prose, `board.py` exits non-zero **after publishing
everything** — the board, its fingerprint, the pages and the Discord posts all
land first, and only then does the run go red into the existing alert. Failing
earlier would withhold a good board because its prose was missing, which is the
wrong trade.

The threshold separates the two failure modes cleanly and needs no tuning: the
validator rejecting one awkward game costs one writeup, while a broken
credential costs all of them. Tested at four points — no key (not degraded),
healthy, every call failing (alarm), and one validator rejection out of eight
(no alarm).

**Three API defects were found here by checking the current contract rather than
recalling it**, each of which would have surfaced on the first live run as a
slate with no prose: it sent `temperature` (removed on Sonnet 5 / Opus 5, and a
400 — every call would have failed); it set `cache_control` on a ~500-token
system prompt when the minimum cacheable prefix is ~1024, so it would never have
cached while reading like an optimisation; and it defaulted to Sonnet to save
money, which is the owner's decision rather than one to bake in quietly. The
retry now names the exact numerals the validator rejected instead of varying a
sampling parameter the API no longer accepts.

THE VALIDATOR CAUGHT TWO REAL HOLES IN ITSELF, both found by writing the tests
adversarially rather than to pass:

1. **Tolerance on both sides is a hole, not double safety.** The first version
   re-rounded the CANDIDATE token as well as expanding the allowed values, so
   any number within rounding distance of a real one validated: "moved 2.5
   points" rounds to "2", `books_at_best` is 2, and a fabricated line movement
   passed against an unrelated book count. Rounding tolerance now lives ONLY in
   the allowed set; the candidate is matched exactly.
2. **A validator that cries wolf gets loosened.** "more than one regulated
   book" was rejected because "one" is a spelled numeral. In English "one" is
   overwhelmingly a determiner, so it is now exempt — a documented, bounded hole
   (a count could be understated as "one") taken deliberately, because constant
   false rejections are how a safety check gets switched off.

Masking matters as much: team and book names are blanked BEFORE extraction, so
"San Francisco 49ers" and "888sport" cannot fail an honest writeup. Masking a
name cannot let a fabrication through, whereas whitelisting `49` could.

WHAT IT DOES NOT CATCH, so nobody trusts it further than it goes: it checks
NUMBERS, not claims. "the market is drifting toward Buffalo" contains no numeral
and would pass while being unsupported. Only the prompt bars that. The validator
is a floor.

### The original specification, for reference

`FOOTBALL_WRITEUP_TEMPLATE.md` section 1 states the rule: the model receives a
filled data block and may not introduce a number that is not in it.

**A PROSE INSTRUCTION IS NOT AN ENFORCEMENT MECHANISM.** Ship a validator that
extracts every numeral from the generated writeup and refuses any that does not
appear in that game's data block (allowing formatting variants — `-115` vs
`115`, `1.11` vs `1.1 pts`). A writeup that fails validation is regenerated
once, then dropped to NO WRITEUP rather than published.

The reason is recorded in the template itself and should not need re-arguing:
the source format's analysis lines carried invented statistics that looked
exactly like measurements, and its own ledger figures contradicted themselves
across two documents. Publishing generated numbers beside an audited
append-only ledger, in the same voice, is the specific failure this product
cannot survive. The validator is the difference between a rule and a hope.

Ledger figures are read from `football_ledger.json` and interpolated. The model
never writes them.

## 3. Phases

Dates are targets. The section 0 deadlines are not.

### Phase 0 — capture (now -> Sat 2026-08-29)

- ~~Merge `claude/nfl-football-model` into `main`~~ **DONE 2026-08-26**, commit
  `b55f732`. Four conflicts (`.gitignore`, `CLAUDE.md`, `odds_credits.json`,
  `season_state.json`), all resolved as unions except `season_state.json` which
  took main's newer CI-written value. `index.html` / `feed.xml` untouched. All
  six football modules import from `main` and the scheduler runs.
- ~~Resolve the credits decision~~ **MOOT** — see section 6a.
- ~~Add `football-capture.yml`~~ **BUILT 2026-08-26**, and **inert until
  pushed** — a workflow file on a local branch never runs. Hourly GitHub cron at
  :07, both sports, reports window state every run before deciding anything,
  stages `data/football/odds/` AND `data/odds_credits.json` (any new odds caller
  must stage the credit log — a real defect on `capture-closing` until
  2026-08-20). Two choices in it are worth not reverting by accident:
  - **`--eager`**, so a capture fires as soon as any window is open rather than
    waiting for the cheapest moment. GitHub's scheduler is known in this repo to
    fire hours late, and hours late against the script's 2h default lead is a
    missed window. Credits are abundant (section 6a) and windows are not; spend
    the abundant one.
  - **A missing `ODDS_API_KEY` FAILS this run**, unlike the MLB jobs where it
    no-ops. There a missing key costs one day of prices; here it costs a
    permanently ungradeable game.
- **Done when:** NCAAF Week 1 T-24 and closing windows are being satisfied
  automatically and the captures are landing in the repo.

VERIFY IT WITHOUT SPENDING ANYTHING: `capture_schedule.py` without `--run`
reports and spends nothing, which is also the workflow's own first step — so
every run logs what the scheduler believed, including the runs that do nothing.
When a window is eventually missed, that log is the record of why.

### Phase 1 — book it (Sat 2026-08-29 -> Sun 2026-09-06)

- Board builder (gap B), sharing code with the grader.
- NFL results store + `--sport nfl` + the season-type allowlist (gap C).
- Create `football_ledger.json` with its `_note` header and no entries (gap D).
- `football-grade.yml`: grade a slate week after it closes, at 0 units.
- Run NCAAF Weeks 1-2 **fully dark**: real captures, real board, real grading
  into the real ledger, nothing published anywhere.
- **Done when:** two NCAAF weeks are graded in `football_ledger.json`, the
  allowlist is proven to refuse a preseason event, and a board's revealed
  plaintext verifies against its pre-kickoff hash.

### Phase 2 — publish free (Mon 2026-09-07 -> Sat 2026-09-13)

- Layer 2 generator + the numeral validator (gap E).
- Football site surface (gap F): weekly slate page with full reasoning on every
  covered game, the free play in full, premium as counts and exposure only until
  graded, the football ledger, the NO MARKET list, and section 1's no-claim copy
  stated plainly.
- Football Discord delivery (gap G): full slate + premium play to
  `#members-only`, free play to `#free-pick`.
- NFL Week 1 (Thu 2026-09-10) publishes free. **Premium still dark.**
- **Done when:** a member-shaped post and a public page both render a real NFL
  week, and the copy contains no barred language.

### Phase 3 — premium live (~Mon 2026-09-15 -> Mon 2026-09-22)

- Football premium copy and pricing decision (gap J, section 6).
- Set `WHOP_CHECKOUT_URL` (gap K). The upgrade button appears on the next build
  and not before.
- **Target: premium live for NFL Week 2 or Week 3.**

**WHY NOT SOONER, STATED PLAINLY.** The site's own premium copy says *check the
record before you pay*. Launching with an empty football ledger contradicts the
only thing being sold. Two or three graded football weeks on the public ledger
is the asset that makes the paywall honest, and it costs about two weeks.

## 4. Calendar collisions

| date | event | interaction |
|---|---|---|
| 2026-08-29 | NCAAF Week 1 | Capture deadline. Nothing publishes. |
| 2026-09-08 | Daily Pick STAKING REVIEW | Separate strategy, separate decision. Football is 0u regardless and this review authorises nothing here. Do not move the date. |
| 2026-09-10 | NFL opener | Allowlist deadline. First free football publication. |
| ~2026-09-27 | MLB regular season ends | Decide what $30 buys in October, when football is the only live product. Not a credit event — see section 6a. |

## 5. State of the acquisition asset — flagged, not solved

`PLAN-paid-tier.md` assumed the public ledger would be the free evidence that
justifies charging. As of 2026-08-26 it is thinner than that assumed:

| file | entries | last graded |
|---|---:|---|
| `ledger.json` (staked) | 10 | 2026-07-24 |
| `daily_ledger.json` | 16 | 2026-08-24 |
| `totals_ledger.json` (paper) | 366 | 2026-08-24 |

This strengthens the process-and-receipts framing already chosen, and it means
the football ledger will be doing most of the persuading by itself. It is a
reason to launch with graded football weeks in hand rather than without them.

## 6. DECISIONS — 6a resolved, 6b still open and still blocking Phase 3

**6a. Odds API tier — RESOLVED, and it was already resolved before this plan
was written.** This section originally said the free tier's 500/month cap made
September tight (~458/500) and recommended upgrading. **That was wrong.** The
merge surfaced the correction in two places that main had not yet seen: the
branch's own CLAUDE.md notes the paid tier went live, and
`data/odds_credits.json` shows the balance directly.

| | |
|---|---:|
| plan | PAID, 100,000 credits / calendar month (live since ~2026-08-21) |
| used, month to 2026-08-26T01:30Z | 28,305 |
| **remaining** | **71,695** |
| football need, h2h, NCAAF + NFL, T-24 + closing | ~208 / month |

**The allowance does not carry over**, so an unspent month is simply gone.
Football's cost is a rounding error and credits gate nothing in this plan.

Two corrections that follow, and neither should be inherited from the old
framing:
- Capture cadence is free to be generous. Run the scheduler hourly; do not
  economise on windows.
- `fetch_odds.py --markets h2h` stays the default, but **for a product reason,
  not a budget one**: the selection rule uses the moneyline and nothing else,
  and layer 2 may not mention a number layer 1 did not produce. Adding spreads
  or totals is now affordable and is still a spec change under
  `FOOTBALL_PIPELINE.md` section 7, not a flag flip.

The lesson worth keeping: the budget arithmetic in CLAUDE.md was correct and
its conclusion was stale, because the constraint moved and the doc did not.
`data/odds_credits.json` is the evidence and it is in the repo in the clear —
check it rather than the prose.

**6b. What $30/month buys.** PARTLY DECIDED 2026-08-26.

**Settled: ONE premium play a week, not one per sport.** NFL and NCAA FBS rank
in a single pool (`FOOTBALL_PIPELINE.md` s.4 step 0, fp-v0.2). The measured
consequence is that the play is usually a COLLEGE game — college fields ~3x the
games, rank 1 is a minimum rather than a median, and more draws produce a better
tail. Copy must set that expectation before a member forms the wrong one.

**Settled 2026-08-26: the price HOLDS AT $30/month.** Decided by Daniel with the
shape of the product on the table, not by default.

The framing that has to go with it is not optional. One graded play a week is
~4 a month; sold as a pick service that is indefensible at $30, and it should
not be defended that way. The volume is in layer 2 — full reasoning on every
covered game, ~57 a week measured on the 2026-08-25 board. **The product is a
weekly research publication in which one play is graded in public for
accountability.** Copy that leads with the play instead of the slate prices the
wrong product and invites exactly the comparison this brand loses: against touts
willing to claim an edge, honesty is a handicap. Against research, it is the
moat.

Also unresolved and dated: on ~2026-09-27 MLB ends and anyone who subscribed for
a daily product starts receiving a weekly one. That is a material change to what
they bought. Handle it before it happens; with `WHOP_CHECKOUT_URL` still unset
the member count is likely zero, which makes it free to handle now and expensive
to handle later.

**6c. WHEN is the week's play chosen? NEW, found while building gap B, and the
spec does not answer it.**

Each game is evaluated at ITS OWN T−24. So the week's full field never exists at
one moment: a Sunday NFL game's T−24 lands Saturday, by which time most of
Saturday's college slate has kicked off. **There is no instant at which every
T−24 exists and no game has started.** This is structural, not a bug, and it
follows directly from fp-v0.2's decision to rank both sports in one pool.

`board.py` therefore takes a decision moment (`--asof`) and ranks the games that
are, at that moment, both evaluable and unplayed. Measured on the self-test
fixture, the field really does move:

| decision moment | covered | premium |
|---|---:|---|
| Wed, before anything kicks | 9 | BUF @ HOU, eff 1.22 |
| Sat morning | 8 | BUF @ HOU, eff 1.22 |
| Sun 11:00Z | 8 | BUF @ HOU, eff 1.22 |

The play was stable here, but nothing guarantees that — a tighter market can
appear at any later T−24, and if the rule is re-run it would move. **A premium
play that can change after it is committed is not a commitment**, which is why
this must be settled before week 1 rather than discovered in week 3.

The options, none of them free:
- **Fixed weekly moment.** Precommit a time (say Friday 18:00Z), rank what
  exists, commit once. Simple and honest, but it structurally excludes Sunday
  and Monday NFL games from ever being the premium play.
- **Commit per game, choose per week.** Fingerprint each game at its own T−24 as
  it arrives, and precommit the moment at which rank 1 among everything
  committed so far becomes the play.
- **Per-sport decision moments** — but that reopens fp-v0.2 and would give two
  plays a week again.

RECOMMENDATION: option 2. It matches how the captures actually arrive, keeps the
NFL eligible, and still yields exactly one committed play. It needs a spec bump
before it is real.

### F — the site surface — BUILT 2026-08-26

`scripts/football/page.py` writes `/football/` (the record, and every week) and
`/football/<week>/` (one slate). `selftest_page.py` renders both states and
asserts the redaction holds.

**A FINDING THAT SHAPED THE WHOLE PAGE, and it was not in the plan.** The
selection rule is public and deterministic: rank 1 is the lowest effective
overround, and the side is the one furthest above de-vigged fair. Both are
computed from exactly the fields a full-slate writeup prints. **Measured on the
live capture: given the 32 covered games' data blocks, recomputing the rule
reproduces rank 1 and its side exactly.** So publishing every covered game's
numbers before kickoff does not hint at the premium play — it hands it over, and
a paywall over that is theatre.

This is the same failure `PLAN-paid-tier.md` names for the MLB engine ("the
engine is public and deterministic... anyone can re-run it"), and it bites harder
here because the football rule is arithmetic rather than a simulation.

So the full slate IS the members' product — which is not a paywall bolted on,
it is what fp-v0.1 section 5 already said members buy: timing and the full
slate. Before grading the public page carries the free play in full, the
coverage summary, the complete NO MARKET list with reasons, and the premium play
as **matchup and kickoff only** (House Rule 7's redaction — no side, no price,
no probability, and 0 units stated). After grading, the same URL reveals
everything.

The reveal is driven by the commitment log, not by a person deciding weekly.
Withholding a pick before kickoff is the product; withholding it after is fraud,
so that switch must not be a judgement call.

The NO MARKET list is public in **both** states. It is the proof of no
cherry-picking and it leaks nothing: a game we could not cover is a game we have
no play on.

TESTED: legal footer and the no-claim paragraph present in both states, all four
barred phrases absent, and the redacted page proven not to contain the premium
play's side, price, book, or either overround figure. One false alarm on the
first run — the leak check matched `max-width:100%` in the stylesheet and
reported the premium price as leaked. The test now strips CSS before checking,
for the same reason the numeral validator exempts "one": a test that cries wolf
gets loosened.

### G — Discord delivery — BUILT 2026-08-26

`scripts/football/discord.py`, two modes:

- `free` → `DISCORD_WEBHOOK_URL` — the free play in full, two messages.
- `slate` → `DISCORD_WEBHOOK_URL_MEMBERS` — the whole reasoned slate plus the
  premium play. **This is the product**, for the reason gap F records: the
  selection rule is public, so the full slate cannot be published before
  kickoff without handing over the premium play.

**IDEMPOTENT, deliberately unlike `post_discord.py`.** CLAUDE.md records that
the MLB poster is intentionally not idempotent — a re-run re-posts one pick,
a small and tolerable duplicate. This posts a WEEK. Measured on the self-test:
57 games is **11 messages**, 120 games is 18. Re-running that buries the channel
it exists to serve, so both modes check `post_status.json` and skip a week
already sent. Status modes `fb_free` / `fb_slate` are distinct from
`pick`/`board`/`email` for the reason `send_email.py` documents — `record()`
deletes any existing (key, mode) row, so a shared mode lets one sender wipe
another's guard and double-post.

**Discord's limits are the real constraint and the character cap is the one that
bites.** 2000 characters of content, 10 embeds, and 6000 characters across all
embeds in one message. A writeup plus its number line runs ~700 characters, so
ten embeds overflow a limit that a ten-per-message rule alone never catches. The
chunker respects both, and the self-test runs 2, 9, 57 and 120-game slates
because the week this breaks would be the biggest week of the season.

Also asserted: the free post carries the free play and NOT the premium one, the
members' post states 0 units, and no barred phrase appears in either channel.

ONE SMALL THING WORTH THE NOTE: the first dry-run crashed with
`UnicodeEncodeError` on a "→" (U+2192), which is outside Windows' cp1252
console encoding. Message text now stays inside cp1252. The dry-run is how copy
gets checked before it reaches members, and a preview that only works on the CI
runner is not a preview.

### H — the workflows — DONE 2026-08-26

Three jobs, and the difference between them is what happens when one is missed.

| workflow | when | missing a run costs |
|---|---|---|
| `football-capture.yml` (capture **and board**) | cron-job.org hourly :37, GitHub :07 as backstop | **a price, permanently** (capture half) |
| `football-grade.yml` | daily 11:00Z | a day of delay |

**MERGED 2026-08-26, and the measurement forced it.** These began as three
workflows. `football-board.yml` sat at **ZERO runs for nine and a half hours** on
an hourly GitHub cron, while capture — which also has an external cron-job.org
trigger — fired every hour. GitHub's scheduler is not merely late in this repo,
it drops slots entirely. Rather than add a second external entry, the board half
now runs inside the job whose trigger demonstrably works: one entry to maintain,
and the board cannot be orphaned by a scheduler that never fires.

**CAPTURES ARE COMMITTED BEFORE THE BOARD RUNS, and that is what makes the merge
safe.** The risk of putting slower work in the same job is that a hang in the
board half tears down the runner with the prices still on it, unpushed — losing
the one thing that cannot be re-fetched. So there are two commit steps: prices
are pushed the moment they exist, before anything else is attempted.

The alert now keys off the failing step name, because the two halves need
different reactions: a capture step failing is unrecoverable and wants attention
now; a board step failing is self-healing and costs delay.

**That distinction is why only capture needed moving off GitHub cron.** Board and
grade are self-healing: the next run picks up exactly where the last stopped,
because `board.py` refuses to overwrite an existing board, `discord.py` skips a
week already sent, the ledger refuses a week already graded, and `page.py`
re-renders from scratch. Capture is the one job whose missed window cannot be
recovered at any price short of buying historical odds.

**The hourly board run is a no-op almost every time, deliberately.** Before the
decision moment it only freezes newly-evaluable games at their own T−24. At the
decision moment it writes the board ONCE. After that every step declines to
repeat itself. So the cadence costs nothing and no single failed run can lose
the week.

**Writeups are generated BEFORE the fingerprint** (`board.py --writeups`), so
the published SHA-256 proves the prose was not edited after kickoff either — not
just the numbers. It also bounds the API cost to one slate per week, because
that branch is reached only when the board is first written.

**Grading drives the reveal, off the LEDGER rather than off a date.** A week is
revealed exactly when it has actually been graded, which is the only definition
that cannot drift from what the record says. House Rule 7 is not a thing anyone
should have to remember to do.

`page.py --all` re-renders EVERY week, not just the current one, because a
week's page changes when grading flips it to revealed — days after that page was
last written. Rendering only "this week" would leave graded weeks frozen in
their redacted form, which is precisely the failure House Rule 7 forbids.

TWO BUGS FOUND WHILE TESTING, both silent:
- The alert steps ended their message with a trailing `\`, so `exit 0` became
  two extra arguments to the alerter instead of a command. Caught by reading the
  bytes rather than trusting that the YAML parsed.
- A `discord.py --dry-run` against a week with no board WROTE to
  `post_status.json` — a file CI commits. Every other write was guarded; that
  path was missed. A preview that mutates state is not a preview.

### J — the premium copy — DONE 2026-08-26

Two places: the home-page `upgrade_block` in `build_site.py`, and a football CTA
on `/football/` and every week page. Both stay invisible until
`WHOP_CHECKOUT_URL` is set, so the site never advertises something that cannot
be bought.

**THE TWO SPORTS ARE DESCRIBED SEPARATELY, and that is the load-bearing
decision.** Baseball has a model, an edge gate and a circuit-breaker log.
Football has two published studies saying its market cannot be out-forecast,
makes no expectation claim, and is staked at zero units. A single "our edge"
spanning both would be a claim we have already published the evidence against —
the most damaging sentence that could appear on this site, because it would be
contradicted by our own documents.

**Football leads with the SLATE, never with the play.** One committed play a
week is ~4 a month; sold as a pick service that is indefensible at $30 and
invites comparison with people willing to promise a win — a comparison this
brand loses by design, having disarmed on claims. The reasoned slate is the
product, and it is what members actually receive.

The copy says **"what arrives depends on what is in season"** out loud. That is
the ~2026-09-27 problem handled in advance rather than discovered by a member in
October, when MLB has ended and football is the whole product.

Kept verbatim from the MLB block, because it is the best line on the page:
*"If the ledger is not good enough to justify this, do not buy it."*

STILL A HUMAN TASK, not a code one: anyone who subscribes before late September
buys a daily product and will start receiving a weekly one. With
`WHOP_CHECKOUT_URL` unset the member count is likely zero, which makes telling
them free now and awkward later.

## 7. What this plan may NOT be used to justify

- Re-cutting the selection rule, the book tiers, the staleness window or the
  minimum-book filter. All were fixed in advance. `FOOTBALL_PIPELINE.md`
  section 7: only a completed fb-v0.3 changes any of it.
- Any expectation language in copy — "our edge", "+EV play", "the model likes"
  are barred until such a study exists.
- Staking football. 0 units through the proving window, per pipeline spec
  section 6.
- Spending the 2025 holdout. It remains unspent.
- Grading postseason. `type 3` is deliberately not on the allowlist and needs
  its own decision.

## 8. Definition of done

- Football captures fire automatically off kickoff windows, and the credit log
  reaches the repo on every run.
- Every covered game gets reasoning; every excluded game is named NO MARKET.
- No generated number reaches the page — enforced by a validator, not a prompt.
- The premium play is encrypted and hashed before kickoff and published in full
  after grading, win or lose.
- Both plays are graded at 0 units into `football_ledger.json`, append-only.
- Site copy states the no-expectation-claim position plainly.
- Preseason is captured and never graded.
- A missing webhook, a missing key or a dead odds feed degrades the run; it
  never fails it and never publishes a stale board.

## 9. The year-round direction, and the ONE thing it changes today

Stated by Daniel 2026-08-26: the goal is a subscription that delivers value
every month regardless of which season is running — eventually all sports —
sold on trust, transparency and information rather than on picks alone.

**THE ARCHITECTURAL POINT, and it is the reason to write this down before gap B
rather than after.** The three-layer design is SPORT-AGNOSTIC, and it is
sport-agnostic precisely because it is market-derived. Layer 1 needs an odds
feed and nothing else — no team ratings, no park factors, no starting pitchers.
Layer 2 writes from layer 1's numbers. Layer 3 ranks by effective overround,
which is defined for any two-way market in any sport. Contrast MLB's
`engine.py`, which is irreducibly baseball: innings, pitchers, parks, wOBA.

So the football pipeline is not a football pipeline. **It is the year-round
chassis, and adding a sport is closer to adding an odds key than to building a
model.** `fetch_odds.py` already carries a `SPORT_KEYS` dict; the Odds API
covers NBA, NCAAB, NHL, MLB, soccer and more on the same `h2h` endpoint, at the
same 1 credit per call, against an allowance with ~71,695 credits spare.

**CONSEQUENCE FOR GAP B AND GAP E — decide it now, not in a later refactor:**
write the board builder and the writeup generator **parameterised by sport from
the first line**. No `football` in a function name, no NFL/NCAAF branching
outside a config table, no hardcoded ESPN path. The things that ARE sport-
specific — the results source, the team-identity mode (canonical vs verbatim,
see `fetch_odds.py`), the season-type allowlist, what a "slate" means — belong
in a per-sport config, not in the code. Football is then the first tenant of the
chassis rather than the chassis itself.

WHAT THIS DOES NOT LICENSE. Adding a sport still needs: a results source wired
and verified, a season-type allowlist for that sport (the 3a failure mode is
generic — any league with exhibition games can quietly poison a permanent
ledger), a decision about identity mode, and its own ledger. None of that is
free. The claim is only that the EXPENSIVE part — a defensible model — is not
required, because this product does not make a model claim.

**ON "a model that beats the market", kept honestly.** Two frozen studies say
no for NFL moneyline at T−24 (`FOOTBALL_RESULT_T24.md`, `FOOTBALL_RESULT_PRICE.md`).
Neither generalises beyond that market — they say nothing about NCAAB, NHL,
lower-liquidity leagues, or non-moneyline markets, and it is genuinely plausible
that a thinner market is beatable. That ambition is not dead; it is GATED. The
route is another pre-registration frozen before scoring, with its own clean test
season, exactly as v0.1 and v0.2 were. The 2025 holdout is unspent and reserved
for it. Until such a study clears, the product ships with no expectation claim
and the copy says so — and the process product earns while the research runs,
which is the arrangement that makes patient research affordable.

THE THIN MONTH IS JULY–AUGUST. Everything else has a major market: NFL/NCAAF
Sep–Jan, NBA/NCAAB/NHL Oct–Jun, MLB Apr–Sep. Midsummer is MLB alone, and that is
where a year-round subscription is hardest to justify. Whatever fills it —
another sport, more MLB markets, or seasonal pricing — is a decision to make
deliberately rather than discover next July.

## 10. Version history

| version | date (2026) | change |
|---|---|---|
| fl-v0.1 | Aug 26 | Initial plan. Gaps A-K, two hard deadlines (capture 08-29, NFL allowlist 09-10), four phases targeting premium live NFL Week 2-3, two open decisions (Odds API tier, price). |
| fl-v0.2 | Aug 26 | Gap A closed (branch merged, `b55f732`). **Gap I withdrawn: the free-tier premise was wrong** — a paid 100,000/month tier has been live since ~08-21 with 71,695 remaining, so credits gate nothing and decision 6a is resolved rather than open. Capture deadline sharpened from "Sat 08-29" to the measured first window, 2026-08-28 10:00Z, with the 08-26 17:00Z preseason rehearsal noted. |
| fl-v0.3 | Aug 26 | Capture half of gap H shipped and pushed (`football-capture.yml`, live). Decision 6b half-settled: ONE combined slate, one premium play a week (spec bumped to fp-v0.2), with the college-skew measured and recorded; price still open and reframed around layer 2's volume rather than four picks a month. New section 9 records the year-round direction and the one thing it changes immediately — gaps B and E get written sport-parameterised from the first line, because the market-derived chassis is not football-specific. |
