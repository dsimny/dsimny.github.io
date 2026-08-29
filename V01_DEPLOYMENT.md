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

Exactly three actions before enabling either job.

### 1. Rotate `THE_ODDS_API_KEY`

The previous key was pasted into a chat transcript and **must not be used**.
Rotate it at the-odds-api.com, put the new value in the hosted runner's
environment only, and never pass it as a command-line argument.

### 2. Deploy the runner at `058`

Migrations `001`–`058` applied. Verify:

```bash
python scripts/v01_runner.py --status
```

### 3. Verify both endpoints manually, then activate

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
python scripts/v01_runner.py --activate --by "<name>" --note "enabling hourly schedule + 5-minute resolve"
```

Finally enable both cron-job.org jobs.

---

## Activation is the moment the experiment becomes prospective

> Anything formed after the activation timestamp belongs to the pre-registered
> sample; anything before it does not.

That is enforced, not merely written down (migration `058`):

- `model.experiment_activation` — one row per model version, **append-only**.
  `UPDATE` and `DELETE` raise `APPEND_ONLY_VIOLATION`; a second
  `activate_experiment` raises `ALREADY_ACTIVATED`. A movable boundary would be
  a free parameter that could exclude a disappointing month after the fact.
- `model.activate_experiment` has **no timestamp parameter**. It stamps `NOW()`.
  Back-dating exists only as an explicitly-named test fixture.
- `grading.calibration_bins` measures the **pre-registered sample**, so the
  shakedown beliefs from step 3 above cannot leak into it.
- **No activation means an EMPTY sample, not an unfiltered one.** An experiment
  nobody remembered to activate reports nothing rather than quietly accumulating
  a full sample and publishing a standing on it.

A new model version (a different `k`) starts a new sample from zero, which is
what `MODEL_V0_1_PREREG.md` §4.1 already requires.

---

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
