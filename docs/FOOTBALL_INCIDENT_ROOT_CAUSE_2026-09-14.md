# Root cause: the "September 8" automated football Discord slate

Written 2026-09-14 from repository evidence and the GitHub Actions log of the
posting run. It supplements `FOOTBALL_CONTAINMENT_2026-09-13.md`. It does not
modify, reclassify or re-grade anything. The incident board, its ledger rows,
commitments and the correction notice are left exactly as they are.

## 1. Summary

The questionable plays were produced by the **current production workflow at the
time**, not by an obsolete route, a test path or an unauthorized sender. The two
featured selections were computed correctly *under the rule as written*
(fp-v0.4), from prices that exist in the stored captures. What made the post
unfit for paying members was a stack of release-process and presentation
defects that let an unreviewed, partly stale, partly duplicated board reach
Discord automatically:

| # | defect | class |
|---|---|---|
| D1 | 24 of the 70 "rest of the slate" cards were games that had already kicked off (some finished) when posted | stale-data / release control |
| D2 | 8 games appeared twice, because the provider rewrote `commence_time` to the actual start after kickoff and game identity included kickoff | data-mapping (identity) |
| D3 | The Discord renderer dropped each card's `selection_reason` (32 cards were `PASS: outside launch moneyline range`), so every card showed a side and a price and read like a recommendation, e.g. "Richmond Spiders +4000" | Discord rendering |
| D4 | The board declared `coverage_status: MANUAL REVIEW` (39% of the week excluded) and was posted with no human review | release control |
| D5 | The board step exited 1 (all 72 writeups failed) but the posting steps ran anyway (`if: !cancelled()`) | release control |
| D6 | Prices were shown with no quote timestamp; the premium price was ~20 h old and the free price ~23 h old at post time | stale-data presentation |
| D7 | Discord posted at 18:37:45Z and 18:38:02Z; the fingerprint reached the public repository at 18:38:03Z — after both posts | commitment ordering |
| D8 | The selection rule itself carries no forecasting claim; both featured sides were priced *worse* than de-vigged consensus (relative discount −0.48% and −0.93%) | selection model (no edge by design) |

It was **not** a market-normalization failure (prices match captures), **not** a
live-odds leak into the featured picks (both were pregame, from T−24 captures),
and **not** an unauthorized or obsolete posting route.

## 2. Trace

| item | evidence |
|---|---|
| Workflow | `Football capture and board`, run **34711740282**, `workflow_dispatch` (cron-job.org), started 2026-09-12T18:37:04Z, concluded **failure** |
| Code version | checkout at `c7ae425` (the hourly bot commit; code = fp-v0.4 from `5d7326a`, 2026-09-12 08:30 ET) |
| Selector | `scripts/football/board.py --sports nfl,ncaaf --writeups` (step "Build the board"), `market.py` evaluate/rank, `record_policy` fp-v0.4 eligibility |
| Decision | slate week `2026-09-08` (Tuesday-anchored UTC week label), decision moment D = 2026-09-12T18:00:00Z (Sat 14:00 ET), board generated 18:37:26Z |
| Source data | `data/football/odds/ncaaf_20260911T223720Z.json` (premium), `ncaaf_20260911T193723Z.json` (free), plus the week's T−24 captures |
| Output artifact | `data/football/board_2026-09-08.enc`, sha256 `da33da68ad7e9f44…5a34`, commitment recorded 2026-09-12T18:37:41Z; revealed plaintext `board_2026-09-08.json` on 2026-09-13 |
| Posting script | `scripts/football/discord.py free` and `discord.py slate` |
| Webhooks | `DISCORD_WEBHOOK_URL` (free: 2 messages, HTTP 204, 18:37:45Z) and `DISCORD_WEBHOOK_URL_MEMBERS` (members: 11 messages, HTTP 204, 18:38:02Z); rows `fb_free` / `fb_slate` in `data/post_status.json` |
| Target channels | per repository configuration, #free-pick and #members-only. The webhook *values* were not inspected, so the channel binding is inferred from configuration names only |
| Pregame or live | both featured selections pregame (free kickoff 19:30Z, 53 min after post; premium kickoff 2026-09-13T04:00Z). 24 coverage cards were post-kickoff at post time |
| Push of the commitment | `99ea4c9` pushed 18:38:03Z |

Featured selections as posted and as stored (identical numbers):

| tier | selection | price / book | fair | quote captured | rank |
|---|---|---|---|---|---|
| premium | New Mexico State Aggies @ Hawaii, NMSU ML | +250 DraftKings | 28.4% | 2026-09-11T22:37:20Z | 7 |
| free | UCF Knights @ Pittsburgh, UCF ML | +240 DraftKings | 29.1% | 2026-09-11T19:37:23Z | 10 |

Both prices were verified against the raw capture books. Both games, teams and
markets existed in the underlying data.

## 3. Each defect, with evidence

**D1 — started games in a pregame post.** `board.py` covers every game in the
slate week that has a T−24 capture, including games before the decision moment.
At post time (18:37:45Z) 24 of 72 cards had `kickoff_utc` earlier than the post
(e.g. Richmond @ NC State, kicked off 2026-09-11T23:00Z). The members message
presented them under "The rest of the slate (70 games, tightest market first)"
with a side and a price. Nothing in `discord.py` (at `99ea4c9`) compared kickoff
with the send time.

**D2 — duplicate identities.** Oklahoma @ Michigan appears twice: kickoff
16:00:00Z (T−24 capture `ncaaf_20260911T153718Z.json`) and 16:14:37Z
(`ncaaf_20260911T163732Z.json`). The capture taken at 2026-09-12T16:37:21Z, while
the game was in progress, reports `commence_time` 16:14:37Z — the provider
replaced the scheduled time with the actual start. Identity keyed on
(sport, matchup, kickoff) split one game into two. Eight matchups were
duplicated this way; `NE @ SEA` is also listed twice in `no_market`.

**D3 — PASS cards rendered as picks.** The stored board carries
`selection_reason` on every card (40 `eligible`, 32 `PASS: outside launch
moneyline range (20%-80% fair, max +400)`). `discord.py game_embed` at `99ea4c9`
did not render it. A dry-run of that exact code against the revealed board
reproduces 11 messages whose coverage cards read, for example,
"**Richmond Spiders** +4000 at fanduel", with no PASS label. The message
therefore differed in meaning from the stored artifact even though every
number matched. (Fixed in `92f5f16`: "MARKET COVERAGE ONLY - not a recommended
play." plus the reason.)

**D4 — manual-review coverage was not a gate.** The run log prints
`72 covered | 46 excluded (39.0%) -> MANUAL REVIEW`; the board stores
`coverage_status` accordingly. `discord.py` had no check. (Now
`delivery_policy.validate` raises on anything but `covered`.)

**D5 — a failed build still posted.** The Anthropic call failed 72 times with
HTTP 400 `model: String should have at least 1 character`.
`football-capture.yml` sets `OLS_WRITEUP_MODEL: ${{ vars.OLS_WRITEUP_MODEL }}`
(since `93d4768`); no such repository variable exists, so the value is the empty
string, which `os.environ.get(..., "claude-opus-5").strip()` returns as `""`.
`board.py` publishes the board and then exits 1 by design. Every later step,
including both Discord posts, carried `if: ${{ !cancelled() }}`, which is true
after a failure.

**D6 — undisclosed quote age.** The embed showed price and book only. The
premium quote was 20.0 h old and the free quote 23.0 h old when posted.

**D7 — posts before public commitment.** Step order was build → render →
Mercer deliver → post free → post slate → commit and push. The Mercer Spotlight
operating rule already states that a selection is not publicly committed until
its fingerprint is pushed; the football workflow posted first.

**D8 — the rule.** fp-v0.4 was a launch product rule, documented as "not a
validated predictive model". Both featured sides had negative
`relative_price_discount`, i.e. the offered best price was below proportional
de-vigged fair. The rule made no claim otherwise, but members receive a play
labelled PREMIUM PLAY.

## 4. Could anything obsolete still post?

Inventory of every Discord posting path (`post_discord.py` pick/board/recap/blog/
alert, `football/discord.py` free/slate, `football/mercer.py deliver`,
`football/member_correction.py`, `post_pickem.py`, `grade_pickem.py`; the pick'em
Worker and `scripts/mercer_live/` do not post):

- `football/discord.py` free/slate: blocked by `delivery_policy.PAUSED`, checked
  before `--force`; `board.py` also refuses new boards. **Contained.**
- `member_correction.py`: dispatch-only, requires `PAUSED`, and its reservation
  file now exists, so a re-run fails before sending. **Contained.**
- No obsolete workflow, scheduled job, fallback or test path that posts football
  content was found. The old alert fallback to the members channel was removed
  on 2026-09-11.
- **Not contained: `football/mercer.py deliver`** — the D.J. Mercer Spotlight
  member delivery. It is a separate, human-authored product and did not produce
  the incident, but it runs hourly from the same workflow, does not consult the
  pause, has no webhook-host check, no kickoff check, and relies on a
  `post_status.json` row that other writers trim to the newest 30 rows (about
  four days of history), after which a still-current week could re-post. A
  missing webhook secret also makes it print the full actionable card into a
  public Actions log. Addressed by the Mercer release controls
  (`docs/MERCER_DEVELOPMENTAL_RELEASE_CONTROLS.md`).

## 5. What was not provable

- The exact Discord channel each webhook targets (secret values were not read).
- What members saw beyond the payload: Discord message receipts were not stored,
  only HTTP 204.
- Whether any member acted on a card for a game already in progress.

## 6. Note on the name

The containment notice and member correction call this the "September 8" slate.
That is the slate-week label (Tuesday 2026-09-08 UTC anchor). The posts were made
on **Saturday 2026-09-12**. The correction has already been sent and is not
edited; this note exists so the date is not misread later.

## 7. Controls this incident requires before any football post resumes

1. Human approval of the exact artifact, bound by hash, before any member send.
2. Every posted selection must be pregame at send time, re-checked at send time.
3. Stable provider event identity, never kickoff-in-key; duplicates block.
4. The message is rendered once, stored, hashed and approved; the sender posts
   those bytes and nothing regenerated.
5. Quote timestamp and freshness limit shown and enforced.
6. Publication steps may not run after a failed upstream step.
7. The public commitment is pushed before the send.
8. Idempotency that cannot be trimmed away, with reservation → acknowledgement →
   verified receipt, and no automatic retry of an uncertain send.
9. A kill switch independent of MLB, grading, capture and the site.

These are implemented for the D.J. Mercer developmental cohorts in
`scripts/mercer_dev/` and documented in
`docs/MERCER_DEVELOPMENTAL_RELEASE_CONTROLS.md`.
