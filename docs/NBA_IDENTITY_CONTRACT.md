# NBA identity contract v0.1

Contract ID: **`nba-identity-v0.1`**. Frozen for Package 2A; amendments get a new version.

This contract governs ONE question: *which game is this?* Scheduling, phase and status
live in `nba-schedule-v0.1`; accounting in `nba-completeness-v0.1`.

## 1. Identity authority

**The NBA league-issued game ID is the only authority for canonical game identity**
(architecture decision, 2026-10-26). Provider IDs point AT a canonical game; they never
establish one.

Three consequences, each enforced in code rather than by convention:

| Rule | Enforcement |
|---|---|
| No league ID → not canonical | `identity.require_league_id` raises `Quarantine(MISSING_LEAGUE_ID)` |
| Team names never establish identity | No code path accepts a name as an identity input |
| Providers cannot disagree silently | `crosswalk` marks the label conflicted and resolves it to `None` |

## 2. Provider roles

**ESPN is the preferred SECONDARY provider for future cross-checking** (decision 2). It is
not an authority for inventing league IDs. `crosswalk.PROVIDERS` currently admits `nba` and
`espn`; adding a provider is a contract amendment, not a config change.

## 3. Provenance labels — synthetic vs verified

Every crosswalk entry carries a provenance label:

- **`synthetic`** (default) — hand-authored to exercise this code. **Not provider evidence.**
- **`verified`** — requires `evidence.url` and `evidence.retrieved_at`, and is refused
  without them.

**Nothing in Package 2A promotes a real mapping.** Real mappings require verifiable source
evidence before promotion (decision 3), so a fixture cannot silently become production truth.
`crosswalk.digest()` gives a stable content hash for review records.

## 4. Ambiguity is a result, not a failure to be smoothed over

Ambiguous records are **quarantined** with a stable reason code, never guessed:

`AMBIGUOUS_PROVIDER_MATCH`, `MISSING_LEAGUE_ID`, `UNKNOWN_PHASE`, `LEAGUE_ID_CONFLICT`,
`DUPLICATE_LEAGUE_ID`, `INVALID_TEAM_ID`, `INSUFFICIENT_PROVENANCE`.

Quarantined records are **counted** in completeness accounting, so "we hold complete
schedules" is a falsifiable statement rather than a hope.

## 5. Time

All stored times are timezone-aware UTC; naive input is refused. The eight fields in
`identity.TIMESTAMP_FIELDS` persist **separately and are never derived from one another** —
a missing timestamp stays absent rather than being filled from a neighbour.

**Availability and ingestion must precede the decision cutoff.** The event's own start time
is *not* gated against the cutoff: a game tips off after a pre-game cutoff by definition, so
gating it would flag every game and be worthless. Future-dated availability or ingestion is
retained as an anomalous raw fact and blocked from features.

## 6. Cup and phase counting

Counting is **delegated** to Package 1's frozen `contracts.counts_regular()`. Phase comes from
schedule evidence and is **never inferred from a date**. Cup `GROUP`/`QUARTERFINAL`/`SEMIFINAL`
count as regular season; `CHAMPIONSHIP` does not. Neutral site is a venue property and is never
a phase input.

## 7. Revisions are append-only

A postponement, reschedule or cancellation **appends** a revision. Prior revisions are
preserved verbatim and their digests still verify. `RevisionChain.as_of(cutoff)` answers "what
did we believe then?" — impossible if revisions were overwritten.

A revision is placed in time **only by its own `source_updated_at`**. `event_time` is a fact
about the game, not about when the revision became knowable, so it is never used as a
substitute: if any revision records no `source_updated_at`, the chain's in-force state is
undeterminable and `as_of` raises `REVISION_TIME_UNKNOWN` rather than answering.
`schedule.pregame_eligible()` reports that as ineligible. Venue facts (`actual_venue` and
`neutral_site`) carry forward across a revision unless the caller states a new value, and an
explicit `neutral_site=False` clears the flag.

## 8. Not claimed

Package 2A establishes **no** provider coverage, licensing, model accuracy or production
readiness. All 16 official-release gates remain FAIL. `SHADOW_MODE=true`; official publication
disabled. No real data was fetched; no league or ESPN endpoint was contacted.
