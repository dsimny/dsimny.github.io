# D.J. Mercer developmental cohorts — release controls and operating manual

Updated 2026-09-14 for Daniel's owner decisions. Code: `scripts/mercer_dev/`
(`mdcore.py` registry and ledgers, `mdmarket.py` quotes and identity,
`mdrelease.py` artifact, checks, publish, grading, `render.py` public page,
`mercer_dev.py` CLI and real I/O). Tests: `scripts/mercer_dev/selftest_mercer_dev.py`.
Workflows: `mercer-dev-release.yml` (dispatch only), grading and render steps in
`football-grade.yml`, hourly render in `football-capture.yml`,
`mercer-dev-selftest.yml`.

**State: built and tested, NOT ACTIVE.** Registrations are PROPOSED,
`data/mercer_dev/control.json` is off, `MERCER_DEV_PUBLICATION` is off, and no
webhook exists. Every control answers a defect in
`FOOTBALL_INCIDENT_ROOT_CAUSE_2026-09-14.md`.

## 1. The owner decisions this implements

| # | decision | where enforced |
|---|---|---|
| 1 | Spotlight stays the weekly premium featured selection | Spotlight card unchanged; cohort artifact carries `featured` + Spotlight link and a ★ SPOTLIGHT label |
| 2 | Separate NFL and NCAA cohorts | two registrations, two ledgers, two page sections, no combined figure (tested) |
| 3, 5 | Human-researched; no proven model or model edge; NCAA no model | registrations `model_type: none`; copy checks [24]; barred phrases in artifacts |
| 4, 6 | NFL research may continue; 2025 holdout unspent | registration `historical_research`; preservation checks [P] |
| 7 | A Spotlight pick counts on exactly one record | `mercer.cohort_gate`, grade skip, deliver skip; test [S] |
| 8 | Prospective only | `validate_publication` refuses rows before effective; `cohort_gate` refuses pre-effective cohort ids |
| 9 | 0.25u per play, ≤1u per day across all developmental plays | registration fields; `exposure_limits` counts both ledgers AND released receipts; test [X] |
| 10 | Daniel approves every exact artifact | `human_approval` binds the full artifact hash; registration `manual_review` |
| 11 | Mercer Live capture-only | nothing here imports or schedules it; check [P] |
| 12 | Publication off until commissioning | `control.json` off; variable off; commissioning mode |
| 13 | Automated pause and incident preserved | check [P]; unchanged files |
| 14 | Daily Pick at 0u | check [P] |

## 2. Relationship between Spotlight and the cohort records

```
               commit time < cohort effective_utc           commit time >= effective_utc
Spotlight      pick has NO cohort_play_id                   pick MUST carry cohort_play_id
OFFICIAL pick  -> mercer_ledger.json (Spotlight record)     = <strategy>-v<version>-<ESPN event>-<market>
               -> delivered hourly to #members-only         and units == 0.25
                                                            -> NOT booked on the Spotlight ledger
                                                            -> NOT delivered by the hourly step
                                                            -> released as a featured cohort artifact
                                                               through the approved path
                                                            -> graded ONLY in its cohort ledger
                                                            -> Spotlight card revealed once the
                                                               cohort ledger has settled it
```

- `mercer.py commit` refuses a post-effective official pick without the right
  `cohort_play_id`, and a pre-effective pick that claims one (no backfill).
- `mercer.py grade` never books a cohort-owned pick; it reveals the Spotlight card
  only after the cohort ledger holds a settlement for that play.
- `mercer.py deliver` never sends a cohort-owned pick.
- `mercer_dev.py candidate --spotlight-week W --pick-id P` builds the artifact from
  the fingerprinted pick (refusing an edited one) and checks the play id matches.
- The Spotlight page card says which cohort record holds the pick.
- Leans and passes stay editorial on the Spotlight card and are never graded.

## 3. The release chain

```
draft or Spotlight pick (local)                                   author
  → candidate: fresh quote + ESPN event, circuit breakers, Discord payload rendered
    ONCE, artifact sealed (.enc), SHA-256 appended to commitments.json
  → commit and PUSH artifact + commitment to main                  author
  → preview: #mercer-review only (CI) or console (local)
  → commission (optional, required before activation): offline double
  → publish dispatch: play id + full artifact SHA-256              Daniel
      approval (create-only) → release checks on a FRESH quote and ESPN event
      → reservation (create-only) → send stored payload bytes
      → fetch the message back and compare → receipt (create-only)
  → football-grade.yml daily: publication row after kickoff, settlement on the
    final, CLV where data exists, artifact revealed
  → render: /football/mercer/developmental/ (grade run and hourly capture run)
```

## 4. Release checks (names print in the public log; details do not)

| check | answers |
|---|---|
| `kill_switch_off` — control.json on for the sport AND `MERCER_DEV_PUBLICATION=enabled` | no independent stop existed |
| `registration_active` — REGISTERED, effective, version and hash match | unregistered rule reached members |
| `artifact_hash`, `artifact_committed_publicly`, `publicly_committed` — the sealed file on origin/main is byte-identical, and origin/main's latest commitment matches | posts preceded the public commitment |
| `human_approval` — approver on `MERCER_DEV_APPROVERS` approved this exact hash | no human reviewed the board |
| `payload_is_stored_render` — stored payload equals the render of the stored fields, within Discord limits | PASS reasons dropped in rendering |
| `event_identity_and_teams` — ESPN id, odds id, both names, home/away (never kickoff) | kickoff-drift duplicates |
| `league`, `pregame_now`, `start_time_unchanged` | started games posted |
| `odds_fresh`, `market_exists`, `line_exists` | stale, thin markets |
| `book_still_offers_price`, `price_vs_consensus_now` | never a worse number |
| `breakers_at_build` | — |
| `no_duplicate`, `no_conflicting_play` — both ledgers AND released receipts | duplicates |
| `exposure_limits` — flat 0.25u; ≤1.00u and ≤4 plays per ET day across both cohorts, released-but-not-started included | — |

Before any reservation a `discord.com` webhook must exist (publish) — or, in
commissioning, the non-URL offline marker is the only possible target.

## 5. Delivery states

| state | files | action |
|---|---|---|
| published | `.reserved`, `.delivered` | none |
| failed | `.reserved`, `.failed` | Discord refused (4xx). Fix cause; retry needs `--retry-after-definitive-failure` |
| uncertain | `.reserved` only | **do not re-run.** Check `#dj-mercer-plays`; record what you found in a dated note. Nothing retries it |
| refused | nothing new | read the check names |
| commissioned | nothing written | commissioning run completed; nothing was sent |

Workflow re-runs never publish. A second publish of a play with a receipt is refused.

## 6. Grading and metrics

`football-grade.yml` runs `mercer_dev.py grade` **before** the Spotlight grade:

- A released play enters its ledger only after kickoff (publication row from the
  sealed artifact + receipt); it settles only on an ESPN final, or VOIDs with a
  stated reason (postponed/cancelled, not regular season, no final 7 days after start).
- Results, units, ROI: every graded play (price and stake are in the artifact).
- CLV: **moneyline only**, from existing football captures — the latest pregame
  capture within 6 h of kickoff with ≥3 fresh Tier-1 books. Otherwise, and always
  for spreads and totals, the settlement records `clv_unavailable_reason`.
- Calibration uses the recorded market reference probability and is labelled as
  describing the market reference, not a model.
- Settled artifacts are revealed to `data/mercer_dev/revealed/`.

## 7. Public record

`/football/mercer/developmental/`: one section per cohort with registration and
publication status, record, units/ROI and CLV tiles (CLV "unavailable" with
reasons), released-not-started plays as proof only (matchup, start, release time,
artifact hash), the graded table, and NFL historical research shown separately
and labelled as not an untouched test. NCAA states that no model exists. There
is no combined figure.

## 8. Kill switches

| switch | stops | does not touch |
|---|---|---|
| variable `MERCER_DEV_PUBLICATION` ≠ `enabled` | developmental publication | MLB, grading, capture, site, Spotlight |
| `control.json` sport false | that sport | everything else |
| variable `MERCER_DELIVERY=paused` | hourly Spotlight delivery of pre-cohort picks | everything else |
| `delivery_policy.PAUSED` | the automated fp-v0.4 pipeline | everything else |

## 9. Discord structure

| channel | who | secret | content |
|---|---|---|---|
| `#dj-mercer-plays` | paid Members role | `DISCORD_WEBHOOK_URL_MERCER_PLAYS` | every developmental play, including the ★ Spotlight featured play once its sport's cohort is effective |
| `#mercer-review` | Daniel only | `DISCORD_WEBHOOK_URL_MERCER_REVIEW` | previews headed PREVIEW ONLY with the hash to approve |
| `#members-only` | paid | `DISCORD_WEBHOOK_URL_MEMBERS` | MLB board; pre-cohort Spotlight cards |

**Decision for Daniel before registration:** featured Spotlight plays move to
`#dj-mercer-plays` once cohorts are effective. If they should stay in
`#members-only`, that is a registration field (`publication.channel_secret`) and
must be settled before the file is frozen.

## 10. Commissioning checklist (all required, in order)

**Decisions**
1. Approve the registrations as updated, including the Discord channel for featured plays.
2. Approve an `effective_utc` (proposed: 2026-09-22T04:00:00Z, Tuesday 00:00 ET).

**Discord (Daniel, manual)**
3. Create `#dj-mercer-plays`: deny `@everyone` View Channel; allow the Whop-managed
   Members role View Channel and Read Message History; deny Send Messages.
4. Create `#mercer-review`: deny `@everyone`; allow only your account. Not Members.
5. In each: Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL.

**GitHub (Daniel, Settings → Secrets and variables → Actions)**
6. Secrets `DISCORD_WEBHOOK_URL_MERCER_PLAYS`, `DISCORD_WEBHOOK_URL_MERCER_REVIEW`
   (paste straight from Discord; never into chat or a file).
7. Variables `MERCER_DEV_APPROVERS` = your GitHub login; `MERCER_DEV_PUBLICATION` = `off`.

**Registration and merge**
8. Set both files REGISTERED with the approved `effective_utc`, timestamp and code
   commit; run `mercer_dev.py register` for each; commit and push with the branch merge.
9. Confirm the next grade run renders `/football/mercer/developmental/` and stays green.

**Commissioning — per sport, before any member send**
10. Pick a real upcoming regular-season game. Build a candidate locally, commit and push.
11. Dispatch `preview`: confirm the message in `#mercer-review` looks right.
12. Dispatch `commission` with the play id and hash: every check PASS except
    `kill_switch_off` (expected OFF); status COMMISSIONED; nothing in any customer channel.
13. Dispatch `dry-run`: same checks, nothing written.
14. Repeat 10–13 across two weekends per sport (college Saturday, NFL Sunday and a
    standalone game), per the v1 gates' operational minimum, recording any mismatch.

**Activation (Daniel's explicit go)**
15. Commit `control.json` with the sport enabled; set `MERCER_DEV_PUBLICATION=enabled`;
    update the homepage paragraph in the same commit ("releases have not started"
    stops being true).
16. Publish one approved play. Confirm the message in `#dj-mercer-plays`, the receipt,
    and nothing in `#members-only`.
