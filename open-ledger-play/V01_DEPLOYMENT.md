# Model v0.1 — deployment and the two cron jobs

cron-job.org is **the clock and nothing else**. It POSTs to a hosted runner
endpoint; it carries no model version, no window width, no market list, no API
key, and no Package #5 semantics. Changing the cadence must never be able to
change what the experiment means.

```
cron-job.org          the clock          POST, bearer token, nothing else
v01_service.py        the endpoint       hosted, holds the API key
v01_runner.py         the orchestrator   Package #5
ingest/               the collector      Package #3, knows nothing of v0.1
PostgreSQL            the semantics      windows, eligibility, the sample
```

---

## Job 1 — OLP v0.1 — Schedule Opportunities

```
Name        OLP v0.1 - Schedule Opportunities
Request     POST https://<host>/jobs/v01/schedule
Header      Authorization: Bearer <OLP_JOB_TOKEN>
Cadence     Every hour at HH:00:15
Retry       default is fine (this job makes no provider call)
Timeout     30s
Treat as failure   HTTP status != 200
```

Runs `python scripts/v01_runner.py --schedule`.

- Discovers upcoming NFL moneyline opportunities entering the T−24h horizon.
- Creates missing `model.formation_schedule` rows.
- **Never modifies an existing opportunity** — the table is append-only and
  `uq_schedule_per_wager` (with `NULLS NOT DISTINCT`) makes a duplicate
  impossible, so re-running is a no-op.
- Safe when nothing is new.
- **Does not invoke Model v0.1.**
- **Consumes no Odds API credits.** `provider_credits_used` is `0` by
  construction — this path never touches a provider.

`:00:15` rather than exactly on the hour, to stay clear of other top-of-hour
infrastructure.

Response:

```json
{"job":"v01.schedule","status":"OK","created":6,"scheduled_total":6,
 "unresolved":6,"provider_polls":0,"provider_credits_used":0,"at":"..."}
```

---

## Job 2 — OLP v0.1 — Resolve Formations

```
Name        OLP v0.1 - Resolve Formations
Request     POST https://<host>/jobs/v01/resolve
Header      Authorization: Bearer <OLP_JOB_TOKEN>
Cadence     Every 5 minutes at HH:02:00, HH:07:00, ... HH:57:00
Retry       *** DISABLED ***  (see below)
Timeout     120s
Treat as failure   HTTP status != 200
```

Runs `python scripts/v01_runner.py --resolve`. Market request: **h2h only**.

- Does nothing when no opportunities are due.
- **Zero provider calls when nothing is claimed** — the claim happens first, and
  the poll is reached only if the claim returned work.
- Atomically claims due work (`ON CONFLICT ... WHERE lease_expires_at < NOW()`).
- **At most ONE ingestion poll per runner cycle**, enforced by
  `model.experiment_runs.ck_one_poll_per_cycle`.
- That one poll serves every simultaneously due opportunity — 16 games × 2
  selections is one board refresh, not 32 calls.
- Evaluates eligibility **before** invoking v0.1.
- Forms ordinary immutable beliefs for `ELIGIBLE`.
- Terminally records every other outcome.
- Respects T−24h ± 60m; never substitutes an observation outside the window.

### Retries stay OFF, deliberately

The database owns retries. A claim carries a lease; if the runner dies mid-cycle
the lease expires and the work returns to the pool, recovered by the next
ordinary tick. An external scheduler firing a second HTTP request after a
timeout would instead race a cycle that may still be running — and while the
record-level guarantee holds (`P5-T53` proves duplicate beliefs are impossible
even with claims disabled entirely), it would burn a second provider credit for
nothing. **The next normal five-minute tick is the retry.**

The service also refuses an overlapping request in-process with `409 BUSY`.

### Response

```json
{"job":"v01.resolve","status":"OK","run_id":"...",
 "started_at":"...","finished_at":"...",
 "opportunities_due":6,"opportunities_claimed":6,
 "claimed_inside_window":6,"claimed_past_window":0,
 "provider":"THE_ODDS_API","provider_polls":1,"provider_credits_used":1,
 "quota_remaining":71583,"parse_errors":0,
 "outcomes":{"ELIGIBLE":6},
 "formed":6,"ineligible":0,"no_window_capture":0,"unresolved":0,
 "belief_ids":["..."],"belief_count":6,"errors":[]}
```

`provider_polls`, `resolved` and `unresolved` are read back **from PostgreSQL**,
not counted in Python: the log reports what the database recorded, not what the
process believes it did.

### The loud condition

```
provider_polls > 1   ->   status: FAILED, exit 2, HTTP 409
                          alert: PROVIDER_POLLS_EXCEEDED
```

`ck_one_poll_per_cycle` already makes this unreachable at the database level. It
is asserted again in the runner because the two answer different questions: **the
constraint protects integrity, the job log makes a violation visible** without
anyone opening PostgreSQL. If it ever fires, the constraint has been dropped or
bypassed and the cycle is a failure regardless of how well the rest of it went.

Proven to fire, not assumed: negative control NC-7 drops the constraint, forces a
second poll, and the runner reports `status FAILED`, `rc 2`,
`PROVIDER_POLLS_EXCEEDED`.

---

## Secrets

```
OLP_JOB_TOKEN        long random bearer token, >= 32 chars (service refuses shorter)
OLP_DATABASE_URL     the runner's database
THE_ODDS_API_KEY     the ROTATED key
```

All three live **only in the hosted runner's environment**. The Odds API key is
never accepted from the URL, query string, header or body — a scheduler that
could supply it is a scheduler that logs it. The service reads the request body
and discards it: no experiment parameter is settable over the wire.

The request line is logged with the query string stripped.

---

## Deployment sequence

Four actions before enabling either job.

### 1. Rotate `THE_ODDS_API_KEY`

The previous key was pasted into a chat transcript and **must not be used**.
Rotate it at the-odds-api.com, put the new value in the hosted runner's
environment only, and never pass it as a command-line argument.

### 2. Deploy the runner at `058`

Migrations `001`–`058` applied. Verify:

```bash
python scripts/v01_runner.py --status
```

### 3. Declare the experiment

```bash
python scripts/v01_runner.py --declare --note "NFL moneyline, k=1.10, T-24h"
```

Creates it in `DRAFT`. `schedule_v01` refuses to create an opportunity with no
experiment, because such an opportunity could never belong to a cohort. Nothing
formed against a DRAFT experiment counts — which is exactly what the shakedown
needs.

### 4. Verify both endpoints manually, then activate

```bash
curl -sS -X POST -H "Authorization: Bearer $OLP_JOB_TOKEN" https://<host>/jobs/v01/schedule
```

```bash
curl -sS -X POST -H "Authorization: Bearer $OLP_JOB_TOKEN" https://<host>/jobs/v01/resolve
```

Check, in the responses:

| Check | Expect |
|---|---|
| schedule consumed no credit | `"provider_credits_used": 0` |
| resolve with nothing due consumed no credit | `"opportunities_claimed": 0`, `"provider_polls": 0` |
| the log and the database agree | `run_id` exists in `model.experiment_runs` with matching `ingestion_polls` / `claimed_count` |

Then, and only then:

```bash
python scripts/v01_runner.py --activate --by "<name>" --source-commit 9e7c59b
```

The provenance is **derived, not typed**. The runner reads `k`, the horizon and
the window from `pg_proc` — what the database will actually do, rather than what
a constant in the runner claims — takes the deployment commit from its own
checked-out HEAD, and establishes the schema version by probing for marker
objects. It **refuses to activate** when the shipped migration files and the
live database disagree: activating `058` code against a `057` database would
stamp an un-movable boundary nobody could correct.

Only `--source-commit` has to be supplied, because it names the source project
and cannot be derived on the deploy host.

Recorded on the activation:

```json
{"source_commit": "9e7c59b", "deployment_commit": "5981050",
 "schema_version": "058", "schema_files_max": "058", "model": "v01/0.1.0",
 "k": "1.10", "formation_target": "'24:00:00'::interval",
 "window_seconds": "3600", "market": "h2h"}
```

Finally enable both cron-job.org jobs.

---

## Activation is the moment the experiment becomes prospective

> A belief belongs to the v0.1 prospective evaluation sample only if its
> scheduled formation opportunity was created and resolved under the activated
> experiment, AND its formation occurred at or after `activated_at`.

Two **independent** requirements, both enforced by `model.experiment_cohort`
(migration `058`):

| | Requirement | What it rules out |
|---|---|---|
| **Time** | `formed_at >= activated_at` | shakedown beliefs from step 3 below |
| **Lineage** | experiment → scheduled opportunity → terminal attempt → belief | a manual insert, a fixture row, or the producer'''s own `attempt_belief` path |

A timestamp filter alone is too weak: it admits any belief carrying a late
enough `formed_at`. `P5-T60` proves the point directly — eight beliefs all
formed **after** activation, of which only the four with lifecycle lineage
count.

Every scoreboard draws from `grading.evaluation_sample`, so Brier, the Brier
Skill Score, log loss, calibration and the **N = 500 gate** all see the same
cohort. Before `058`, `standing_report` aggregated on `model_id` +
`model_version` alone — any graded row carrying the right version string
counted.

### Lifecycle

```
DRAFT  ->  ACTIVE  ->  COLLECTION_COMPLETE  ->  EVALUATED
```

- `DRAFT → ACTIVE` stamps `activated_at` **once**.
- Thereafter `activated_at` is **immutable** — `ACTIVATION_IMMUTABLE`. Sliding it
  forward would erase early losses from the scoreboard; sliding it backward
  would admit historical rows.
- Forward-only. No return to `DRAFT`, no skipping — `ILLEGAL_TRANSITION`.
- The experiment cannot be deleted or renamed — `EXPERIMENT_IMMUTABLE`.
- The cohort **survives** `COLLECTION_COMPLETE` and `EVALUATED`, so declaring
  collection finished does not silently empty the scoreboard (`P5-T62`).
- `model.activate_experiment` has **no timestamp parameter**. It stamps `NOW()`.

### Fails closed

No activated experiment means an **EMPTY** sample, not an unfiltered one. The
cohort is an inner join. `NC-10` proves the fail-open shape — *"filter by the
experiment if it has been activated"* — would let a DRAFT experiment publish a
standing.

A new `k` is a new model version and therefore a new experiment and a new
sample from zero, which is what `MODEL_V0_1_PREREG.md` §4.1 already requires.

### The four things the gate proves

| | Proof | Test | Control |
|---|---|---|---|
| Pre-activation exclusion | a fully valid runner-produced belief before `activated_at` does not count | `P5-T59` | `NC-8` |
| Lineage exclusion | a post-activation belief not produced through experiment → schedule → attempt → belief does not count | `P5-T60` | `NC-9` |
| Positive inclusion | a correct cohort belief counts in calibration, standing, Brier/BSS/log-loss and `n` | `P5-T67` | `NC-16` |
| Immutability | `activated_at` cannot move in either direction once ACTIVE | `P5-T61` | `NC-12` |

Two of those need a word on why they are not what they look like.

**`n` is necessary and not sufficient.** A count assertion passes happily while a
hidden aggregate path still averages contaminated rows — which is exactly what
`standing_report` was doing. `P5-T65` plants three pre-activation beliefs at
Brier 0.9801, enough to move the naive average from **0.2233 to 0.4756** and
flip the skill score, then asserts every scoreboard number is identical to a
model that never saw them. `P5-T66` does the same for post-activation junk.
`NC-14` and `NC-15` restore the old aggregation and both tests fail.

**Lineage protects, not database cleanliness.** `P5-T66` runs against a
deliberately filthy database: ten graded rows under one model version, four of
them junk — beliefs formed directly, beliefs through the producer'''s own
`attempt_belief` path, and beliefs belonging to a different experiment. The
cohort holds at six and no number moves. Fixture and test rows may coexist with
live evidence; they simply have no lineage.

**Positive inclusion is not decoration.** Every exclusion test in the suite
would pass on a system that counted nothing. `P5-T67` checks Brier, market
Brier, BSS, both log losses, the bin counts and the bin means against values
computed independently in Python. `NC-16` severs the lineage join and it fires.

## Expected cost

`markets=h2h`, `regions=us` → **1 credit per call**, not 3.

NFL kickoffs cluster into roughly five distinct times a week (Thu, Sun early,
Sun late, Sun night, Mon), and each cluster resolves in one cycle. Expect
**single-digit credits per week** against ~71,600.

The five-minute cadence is not five-minute polling: a tick with nothing due
makes no provider call at all.

**Note:** these cycles request `h2h` only, so they do not refresh spreads or
totals. That is correct separation — v0.1 is moneyline-only — but any other
track needs its own polling.
