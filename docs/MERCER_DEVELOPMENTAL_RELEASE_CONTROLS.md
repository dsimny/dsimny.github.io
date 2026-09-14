# D.J. Mercer developmental release controls — design and operating manual

Written 2026-09-14. Code: `scripts/mercer_dev/` (`mdcore.py` registry and
ledgers, `mdmarket.py` quotes and identity, `mdrelease.py` artifact, checks,
publish and grading, `mercer_dev.py` CLI and real I/O). Tests:
`scripts/mercer_dev/selftest_mercer_dev.py`. Workflows:
`mercer-dev-release.yml` (dispatch only), `mercer-dev-selftest.yml`.

**Current state: built, tested, NOT ACTIVE.** Both registrations are
`PROPOSED`; `data/mercer_dev/control.json` is off; no webhook exists. Nothing
can reach a member until every item in section 7 is done.

Every control here answers a defect in `FOOTBALL_INCIDENT_ROOT_CAUSE_2026-09-14.md`.

## 1. The chain

```
draft (local, gitignored)                         author
  → candidate: fresh quote + ESPN event, circuit breakers,
    Discord payload rendered ONCE, artifact sealed (.enc),
    SHA-256 appended to data/mercer_dev/commitments.json   author, then push
  → preview: private review channel (CI) or console (local)
  → publish dispatch: play id + full artifact SHA-256      Daniel (approver)
      approval record (create-only)
      release checks against a FRESH quote and ESPN event
      reservation (create-only)  → send stored payload bytes
      → fetch the message back from Discord and compare
      → receipt (create-only)
  → grade (after kickoff): publication row from artifact + receipt,
    then settlement row from the ESPN final                automated later
```

## 2. Release checks (all must pass; names print in the public log, details do not)

| check | incident defect it answers |
|---|---|
| `kill_switch_off` — control.json enabled for the sport AND `MERCER_DEV_PUBLICATION=enabled` | no independent stop existed |
| `registration_active` — REGISTERED, effective, version and hash match the artifact | unregistered rule reached members |
| `artifact_hash`, `publicly_committed` — latest public commitment equals the approved hash | posts preceded the public commitment (D7) |
| `human_approval` — approver on `MERCER_DEV_APPROVERS` approved this exact hash | no human saw the board (D4) |
| `payload_is_stored_render` — the stored payload equals the render of the stored fields, within Discord limits | PASS reasons dropped in rendering (D3) |
| `event_identity_and_teams` — ESPN id, odds id, both team names, home/away | kickoff-drift duplicates (D2) |
| `league` — sport matches the registration, ESPN regular season | — |
| `pregame_now` — ESPN status scheduled, ≥30 min before start | 24 started games posted (D1) |
| `start_time_unchanged` — kickoff within 15 min of the approved artifact | stale schedule |
| `odds_fresh` — quote ≤15 min old at publication | undisclosed 20-23 h old prices (D6) |
| `market_exists`, `line_exists` — ≥5 fresh Tier-1 books, ≥3 at the exact number | thin markets |
| `book_still_offers_price` — the named book still offers this number at this price or better | never substitute a worse line |
| `price_vs_consensus_now` — price no more than 3.0 pts worse than consensus | buying bad numbers |
| `breakers_at_build` — every circuit breaker passed when sealed | — |
| `no_duplicate`, `no_conflicting_play` — one position per event and market | duplicates |
| `exposure_limits` — per-play and per-ET-day units, plays per day | — |

Before any reservation: a `discord.com` webhook must be set. A missing or foreign
webhook is refused with nothing written.

## 3. Delivery states and what to do

| state | files | meaning | action |
|---|---|---|---|
| published | `.reserved`, `.delivered` | sent and verified | none |
| failed | `.reserved`, `.failed` | Discord refused (HTTP 4xx) before delivery | fix cause; re-dispatch with `--retry-after-definitive-failure` via a local run or a workflow edit reviewed by Daniel |
| uncertain | `.reserved` only | timeout, 5xx, or the stored message differs | **do not re-run.** Look in the channel. Record what you found in a dated note beside the reservation. No automatic path retries this |
| refused | nothing new | a check failed | read the check names in the log |

Workflow re-runs (`GITHUB_RUN_ATTEMPT` > 1) never publish. A second publish of a
play with a receipt is refused.

## 4. Kill switches

| switch | stops | does not touch |
|---|---|---|
| repository variable `MERCER_DEV_PUBLICATION` ≠ `enabled` | developmental publication | MLB, grading, capture, site, Spotlight |
| `data/mercer_dev/control.json` sport false | that sport's publication | everything else |
| repository variable `MERCER_DELIVERY=paused` | Mercer **Spotlight** member delivery (hourly) | everything else |
| `delivery_policy.PAUSED` (existing) | the automated fp-v0.4 football pipeline | everything else |

## 5. Discord structure (recommendation)

| channel | who sees it | webhook secret | content |
|---|---|---|---|
| `#dj-mercer-plays` | paid Members role only | `DISCORD_WEBHOOK_URL_MERCER_PLAYS` | released developmental plays, one message each |
| `#mercer-review` | Daniel (and any approver) only; **not** the Members role | `DISCORD_WEBHOOK_URL_MERCER_REVIEW` | previews headed "PREVIEW ONLY" with the hash to approve |
| `#members-only` (existing) | paid | `DISCORD_WEBHOOK_URL_MEMBERS` | MLB board, Mercer Spotlight card; never developmental plays |
| alerts (existing) | ops | `DISCORD_WEBHOOK_URL_ALERTS` | pipeline failures |

`#mercer-live` is not recommended yet: Mercer Live is a research-only shadow
capture and v1 registrations forbid live plays.

Each released message shows: DJ Mercer branding, NFL or NCAA, developmental
cohort and version, PREGAME/LIVE, matchup, selection, line and odds, book and
reference source, price timestamp, recommended units, brief reasoning, key risks,
play ID and the responsible-gambling footer. **Publication timestamp:** Discord
displays the message time natively and the receipt records it; it is not written
into the payload, because the payload is fixed at approval and must not change
afterwards.

## 6. Premium withholding

Pregame, the public repository holds only proof: sealed artifact, commitment
hash, approval, reservation and receipt — none carries side, line, price, book or
reasoning (tested). Publication rows enter the ledger only after kickoff, from
the sealed artifact plus the receipt, so the public record never leaks a live
play and never omits a released one.

## 7. Activation checklist (all required, in order)

**Daniel — decisions**
1. Approve or amend the proposed v1 registrations (protocol section 7). Confirm the
   selection source is the Mercer research process and that no model edge is
   claimed.
2. Decide NCAA: prospective human track, shadow only, or wait for data.
3. Decide how Mercer Spotlight relates to the cohorts after cutover (recommended:
   Spotlight's existing record closes after its pre-cutover picks settle, and new
   Mercer picks go only through the cohorts, so one play is never on two records).
4. Confirm the 2025 holdout stays unspent.

**Daniel — Discord (manual; no secret values are needed by Claude)**
5. Server Settings → Channels → create `#dj-mercer-plays`. Permissions: deny
   `@everyone` View Channel; allow the Whop-managed Members role View Channel and
   Read Message History; deny Send Messages for Members.
6. Create `#mercer-review`. Deny `@everyone` View Channel; allow only your own
   account or an admin role. Do NOT allow the Members role.
7. In each channel: Edit Channel → Integrations → Webhooks → New Webhook → name it
   (e.g. "DJ Mercer plays", "DJ Mercer review") → Copy Webhook URL.

**Daniel — GitHub (Settings → Secrets and variables → Actions)**
8. Secrets: `DISCORD_WEBHOOK_URL_MERCER_PLAYS` and
   `DISCORD_WEBHOOK_URL_MERCER_REVIEW` — paste each URL straight from Discord.
   Never paste them into chat or a file.
9. Variables: `MERCER_DEV_APPROVERS` = your GitHub login (e.g. `dsimny`);
   `MERCER_DEV_PUBLICATION` = `off` for now.

**Build — after decisions 1-4**
10. Set each approved registration to `REGISTERED` with a future `effective_utc`,
    `registration_timestamp_utc` and the code commit; run
    `python scripts/mercer_dev/mercer_dev.py register --strategy <id> --version 1 --approved-by <you>`;
    commit and push the registration and `registry_log.json`.
11. Wire `mercer_dev.py grade` into `football-grade.yml` (stage
    `data/mercer_dev/ledgers/`), add a closing capture for released plays, and a
    public cohort page. Not built yet.
12. Update site and member copy to describe the cohorts as live, only once they are.

**Commissioning — before the first member send**
13. One complete dry run per sport on a real upcoming game: candidate → preview →
    `dry-run` dispatch with every check PASS, with `MERCER_DEV_PUBLICATION` still off.
14. Flip `control.json` for that sport (committed, reviewed) and set
    `MERCER_DEV_PUBLICATION=enabled`. Publish one play. Confirm the message in
    `#dj-mercer-plays`, the receipt, and nothing in `#members-only`.
