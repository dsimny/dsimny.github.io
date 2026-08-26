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

| date | deadline | consequence of missing it |
|---|---|---|
| **Sat 2026-08-29** | football capture running automatically | A missed T-24 window is gone permanently. Re-acquiring it means buying historical odds again — the last purchase was $59 and ~10,700 credits. Nothing else in this plan is unrecoverable; this is. |
| **Thu 2026-09-10** | NFL season-type allowlist implemented (gap C) | Preseason entries book into a permanent append-only ledger looking exactly like real ones. `FOOTBALL_PIPELINE.md` section 3a names this failure explicitly and calls it quiet, which is what makes it dangerous. |

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
| A | Branch not merged: `claude/nfl-football-model` carries 32 commits `main` lacks; `main` has moved 31 commits since the split | `main` is what GitHub Pages deploys. Today, zero football code is live. |
| B | **No live board builder** | `grade_football.py` runs the rule AFTER results exist. Nothing runs it BEFORE kickoff. This is the core missing piece. |
| C | NFL grading unwired: `--sport` is `choices=["ncaaf"]`, NFL results store absent, season-type allowlist unimplemented | Hard deadline 2026-09-10. See section 0. |
| D | `data/football/football_ledger.json` does not exist | First entry is permanent under House Rule 1. Create it deliberately, not as a side effect of a test run. |
| E | **Layer 2 does not exist** — zero code | Full-slate reasoning IS the product. The template is written; nothing renders a slate into prose. |
| F | No site surface: `build_site.py` is MLB-only, single-file | No football page, no football ledger view, no fp-v0.1 copy. |
| G | No football Discord delivery | `post_discord.py board` mode is MLB-shaped. |
| H | No football workflow: all 7 files in `.github/workflows/` are MLB | Every football script has only ever been run by hand. |
| I | Odds credits: ~458 of 500 projected for September | Section 6, open decision. |
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

- Merge `claude/nfl-football-model` into `main` (gap A). Expect real conflicts:
  `main` gained offseason handling (`season_state.json`), blog changes and the
  published research post while the branch was out.
- Add `football-capture.yml` (gap H, capture half only): run hourly, call
  `capture_schedule.py --run` for both sports, no-op when no window is open,
  stage `data/football/odds/` AND `data/odds_credits.json` (any new odds caller
  must stage the credit log — this was a real defect on `capture-closing`).
- Resolve the credits decision (section 6). It gates how often this runs.
- **Done when:** NCAAF Week 1 T-24 and closing windows are being satisfied
  automatically and the captures are landing in the repo.

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
| ~2026-09-27 | MLB regular season ends | Credit pressure drops to football-only (~208/mo). Also: decide what $30 buys in October. |

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

## 6. OPEN DECISIONS — needed before Phase 1

**6a. Odds API tier.** Free tier is 500 credits/month.

| caller | credits/month |
|---|---:|
| MLB (`fetch_data` + `fetch_closing`, 8/day) | ~250 |
| Football, h2h only, NCAAF + NFL, T-24 + closing | ~208 |
| **September total** | **~458 / 500** |

It fits on paper with ~8% headroom and no room for a re-run, a backfill, or a
failed workflow retried. Note this is h2h ONLY — football coverage is moneyline
and nothing else; at three markets football alone is ~620 and does not fit.

`CLAUDE.md`'s upgrade decision rule condition 2 — "a market with a real product
surface ships and its markets push the projected monthly spend over 450" — is
arguably now met. **Recommendation: upgrade.** The alternative is cutting MLB
capture cadence during the last month of its season, which degrades a live
product to protect a launching one.

**6b. Price.** Does $30/month stand for a football product that makes no
expectation claim, and what does it buy in October once MLB has ended? This is
a product decision, not a code decision, and Phase 3 cannot ship without it.

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

## 9. Version history

| version | date (2026) | change |
|---|---|---|
| fl-v0.1 | Aug 26 | Initial plan. Gaps A-K, two hard deadlines (capture 08-29, NFL allowlist 09-10), four phases targeting premium live NFL Week 2-3, two open decisions (Odds API tier, price). |
