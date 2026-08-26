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
| B | **No live board builder** | `grade_football.py` runs the rule AFTER results exist. Nothing runs it BEFORE kickoff. This is the core missing piece. |
| C | NFL grading unwired: `--sport` is `choices=["ncaaf"]`, NFL results store absent, season-type allowlist unimplemented | Hard deadline 2026-09-10. See section 0. |
| D | `data/football/football_ledger.json` does not exist | First entry is permanent under House Rule 1. Create it deliberately, not as a side effect of a test run. |
| E | **Layer 2 does not exist** — zero code | Full-slate reasoning IS the product. The template is written; nothing renders a slate into prose. |
| F | No site surface: `build_site.py` is MLB-only, single-file | No football page, no football ledger view, no fp-v0.1 copy. |
| G | No football Discord delivery | `post_discord.py board` mode is MLB-shaped. |
| H | Football workflows: **capture DONE** (`football-capture.yml`, 2026-08-26) — board and grade runs still missing | Capture is the half with the unrecoverable deadline. It is inert until pushed. |
| I | ~~Odds credits~~ **NOT A GAP** — paid tier live, 71,695 remaining | Section 6a. Recorded because the free-tier assumption shaped earlier decisions and should not be inherited. |
| J | Premium copy is MLB-flavoured and claims nothing about football | House Rules 4 and 8. Football makes NO expectation claim and the copy must say so. |
| K | `WHOP_CHECKOUT_URL` unset | One repo variable. This is the go-live switch. Flip it LAST. |

### B — the live board builder, in detail

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

### E — layer 2, and the one thing that makes it safe

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

**Still open: the price itself**, and it should be decided knowing the real
shape of the thing. One graded play a week is ~4 a month. Sold as a pick
service that is a hard number to defend for $30, and it should not be defended
that way. The volume is in layer 2 — full reasoning on every covered game,
~57 a week measured on the 2026-08-25 board. **The product is a weekly research
publication in which one play is graded in public for accountability.** Price it
as that, or reprice it, but do not sell four picks a month.

Also unresolved and dated: on ~2026-09-27 MLB ends and anyone who subscribed for
a daily product starts receiving a weekly one. That is a material change to what
they bought. Handle it before it happens; with `WHOP_CHECKOUT_URL` still unset
the member count is likely zero, which makes it free to handle now and expensive
to handle later.

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
