# NBA Package 1 Core — implementation and baseline report

## Current status — this candidate (NBA Package 1 Core)

**This candidate is offline-only and contains no database work.** It ships the pure contract
invariants, the free raw-capture lane, their explicitly synthetic fixtures, the scope documents,
and one read-only offline CI workflow. **The 20 offline tests pass on Python 3.12** — command,
full output and exit code are recorded in the test evidence below.

Deliberately **not** included in this candidate, and therefore **not** validated by it:

- migration `059_p4_consumer_plan_boundaries.sql`;
- the `production_manifest.txt` migration-list change;
- the database diagnostics scripts (`scripts/nba_db_*.py`);
- the database CI workflows (`nba-database-baseline.yml`, `nba-database-diagnostic.yml`);
- `docs/NBA_DATABASE_VALIDATION.md`, the record of the database validation.

Database-backed validation is a **separate, later change.** Nothing in this candidate is a
database result, and nothing here should be read as one.

---

## Historical evidence — retained, and NOT applicable to this candidate

### October 7, 2026 — development-branch database validation (separate change)

On the development branch `codex/nba-contracts-v01`, a disposable-PostgreSQL baseline passed
214/214 existing database tests, 20/20 NBA offline tests, and market-output equivalence checks.
That result belongs to the **development branch**, at validated commit `fad1345`
(CI run `37558699934`), and it included migration 059.

**It is retained here as historical evidence only.** Every artifact that result depended on is
excluded from this candidate, so it neither describes nor verifies NBA Package 1 Core. Its full
record lives with the development branch (`docs/NBA_DATABASE_VALIDATION.md`), not in this
candidate.

### October 6, 2026 — original report body (historical, retained as written)

Date: October 6, 2026. Branch: codex/nba-contracts-v01.
Base commit: c50c4a4c306ea3fa7bc492d532e6901884f72913.
Revised source: NBA Model V1.docx, SHA-256
F87B51D25797775B1C810C5189A796512BB7CF9E322F319EC850568F367F25DA.

## Result

Contracts, offline fixtures, read-only CI definition and the free raw-capture lane
are implemented in a separate linked checkout. Twenty NBA tests pass. Initial
capture retained nine encrypted observations; integrity/decryption verification
passes. Hourly capture is active in this chat. No scoring model, official plays,
paid API calls, migration, deployment or external publication was performed.

Package 1 has a conditional acceptance result: the existing PostgreSQL database
baseline remains BLOCKED by Windows Application Control. The full package must not
be marked accepted or used to assert production readiness until that baseline runs
in an approved disposable environment. Predictive experiment activation also stays
blocked pending a timestamped NBA data inventory and predeclared validation supplement.

## Implementation

- docs/NBA_ARCHITECTURE.md records revised scope, phase ceilings and package sequence.
- docs/NBA_DATA_CONTRACT.md freezes identities, UTC availability, selected-team spread
  signs, push-aware pricing, provenance and append-only raw storage.
- docs/NBA_PREREG_V01.md freezes initial research screens and provisional sanity
  ceilings; it prevents experiment activation before the data/holdout/power supplement.
- scripts/nba/contracts.py supplies pure phase/time/price invariants.
- scripts/nba/free_capture.py collects bounded unauthenticated NBA-domain responses,
  encrypts bytes, retains successful/failed/unchanged observations and creates immutable
  run manifests. Its verifier detects changed payloads, changed observations, lost-key
  failures and unfinished unmanifested records.
- tests/nba contains explicitly synthetic fixtures and 20 offline tests. No fixture
  is represented as a real historical NBA observation.
- .github/workflows/nba-package1-test.yml is validation only: read-only permissions,
  no network collection, publication, push or deployment. It runs the offline suite
  and nothing else; it was exercised on GitHub on the development branch (last
  success 2026-10-07) and local runs use the identical command.
- docs/NBA_FREE_CAPTURE_RUNBOOK.md records running, verification, limitations and rollback.

## Test evidence for this candidate

Command (from the candidate root):

    "C:/Dev/dsimny.github.io/.venv/Scripts/python.exe" -B -m unittest discover -s tests/nba -v

Result: **Ran 20 tests in 0.124s — OK — exit code 0.** Twenty tests, zero failures,
zero errors, zero skips. Environment: Python 3.12.10, cryptography 50.0.2, fully
offline (no database, no network). The suite is `tests/nba/test_package1.py`
(9 `Contracts` tests, 11 `Capture` tests). No database test was run for this
candidate, because no database artifact is included in it.

## Existing-system baseline

Ran 33 existing suites with external database and credential variables removed.
The 23 football CI suites, frozen Mercer ML-1 suite and eight shared suites passed.
The initial ML-1-OPS suite passed 245 of 246 checks, failing the short substring
canary “no observed value 420 in evidence.” It passed 246/246 on a clean repeat.
Source inspection shows random run IDs are printed, and the check searches the
entire output for three-digit substrings; an incidental run-ID match is a plausible
false positive. This is recorded as an intermittent baseline check, not silently
erased, conclusively diagnosed, or repaired in frozen infrastructure.

All original tracked-file hashes were recorded before testing, and the baseline
reported zero original tracked-file content changes. Existing generated index.html,
feed.xml, public ledgers, frozen ML-1 code, football and market SQL are unchanged.
The recovered main checkout still contains only its original untracked Claude outputs
folder. Both original recovered linked worktrees retain their detached commits.

Database baseline: prepared a task-local environment and official portable PostgreSQL
binaries; Windows blocked initdb.exe with an Application Control error. No cluster
was initialized, no external database was contacted, no schema was dropped and no
security policy was bypassed. Run open-ledger-play/tests/run_all.py in an approved
fresh disposable PostgreSQL environment before accepting Package 1. Do not use the
recovered capture database or waive its guards to obtain a green report.

## Free source capture evidence and limits

Initial run adcaff0ed2284457ae5d3c640a7c8524 retained 9 observations: 8 HTTP 200
responses and 1 HTTP 404. Captured the published schedule page, Official index,
NBA news index and five discovered linked pages. Three discovered pages were
availability-related/news pages; two were older injury-report season indexes.
All source content is semantically UNVERIFIED until parsed and reconciled; an HTML
schedule shell is not proof of a complete game slate. Older season reports are raw
context only, never current-season injury coverage.

The prospective current-season official injury-report URL returned 404, and the
failure is durably recorded. Full player/game availability and complete team
announcement coverage are not established. These gaps block readiness, not capture
of public evidence that is already available.

“NBA free evidence capture” is ACTIVE hourly, running collector plus verifier in this
chat, and remains quiet for routine captures/unchanged states. The machine and desktop
app must remain running for local scheduled execution. No paid data subscription or
Odds API request is part of it. App/model usage for scheduled runs is separate from
the zero sportsbook-data quota consumed by this lane.

Raw evidence and the encryption key are stored outside all Git worktrees. No raw
licensed material or key is included in the source patch. An independent retained
backup and semantic source parser are not yet configured; do not claim that hashes
alone establish complete immutability, external timestamps or disaster recovery.

## Next steps and rollback

1. Complete the database baseline in an approved disposable environment; retain the
   full test report and inspect the intermittent ML-1-OPS assertion separately.
2. Package 2: implement league/provider game crosswalks and phase-aware complete
   schedule parsing/reconciliation, preserving unresolved games and revisions.
3. Validate current-season availability sources and add verified official team pages
   to capture configuration. Keep collecting evidence while documenting known gaps.
4. Before paid capture, establish account budget, deployed migration state and source
   licensing/retention permissions. Before experiment activation, freeze its data,
   holdout and power supplement without viewing final-period outcomes.

Rollback: pause the hourly capture automation and remove/disable the new offline
workflow if needed; retain all captured observations, manifests and original key.
No existing production behavior needs reverting. Do not delete the checkout while
the automation references its script. Official NBA publication stays disabled and
all 16 revised readiness gates remain FAIL/not demonstrated.
