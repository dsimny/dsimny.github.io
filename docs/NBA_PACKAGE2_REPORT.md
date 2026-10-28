# NBA Package 2A — identity, crosswalks, schedules, completeness

**Status:** committed and pushed; **draft PR #11 open** against `main` — not merged, not deployed.
**Branch:** `nba/package2a` (worktree `C:/Dev/ols-nba-package2a`), based on `origin/main`
`0f95a556` at PR creation. The branch was originally cut at `95f5b19f` and fast-forwarded to
`0f95a556` before the Package 2A commit, so no unique history was rewritten.
**Scope:** Package 2 of 11 in `NBA_ARCHITECTURE.md` — *"Canonical identities and complete
phase-aware schedules."* This is 2A: the offline identity/schedule layer only.

## What was built

| Module | Responsibility |
|---|---|
| `scripts/nba/identity.py` | League-ID authority, team IDs, quarantine reasons, eight timestamp fields |
| `scripts/nba/crosswalk.py` | Immutable provider→league mappings, conflict detection, provenance labels |
| `scripts/nba/schedule.py` | Phase classification, neutral site, append-only revision chains |
| `scripts/nba/completeness.py` | Lossless accounting: nothing disappears, gaps raise |

Package 1 (`contracts.py`, `free_capture.py`) is **read, never modified**. `test_package1_unchanged.py`
pins the Package 1 surface Package 2A depends on so a future Package 1 edit cannot silently
change Package 2A.

### Local I/O — one read-only file access

`identity.py`, `schedule.py` and `completeness.py` are pure: no I/O of any kind.
**`crosswalk.py` is not entirely pure.** `crosswalk.load_crosswalk(path)` opens a local JSON
file to build a `Crosswalk`. It is **read-only** — a single `open(path)` with no write, no
`mkdir`, no temp file — and it touches no network, database, credential or environment
variable. An earlier description of this package as having "no I/O" was wrong and is corrected
here: the honest statement is one read-only local file load, and no network or database access
anywhere.

## Test evidence

```
python -B -m unittest discover -s tests/nba -v
```

**Ran 130 tests — OK — exit code 0** (Python 3.12.10, Windows 11 10.0.26300; 0 failures,
0 errors, 0 skips; no database, no network, no credentials). The same command passes on the
GitHub Actions runner (`NBA Package 1 offline contracts`, `pull_request`), which installs
`cryptography tzdata` on Python 3.12.

| Suite | Tests |
|---|---|
| `test_package1.py` (untouched) | 24 |
| `test_package1_unchanged.py` (regression guard) | 5 |
| `test_identity.py` | 15 |
| `test_crosswalk.py` | 16 |
| `test_schedule.py` | 29 |
| `test_completeness.py` (incl. 7 adversarial exclusivity) | 21 |
| `test_pregame.py` (pregame cutoff integrity) | 20 |

## Defects found in final review and fixed

Both were found by the pre-PR review, reproduced before fixing, and are now covered by tests
that fail against the prior implementation.

### Defect 1 — a decision cutoff at or after tipoff qualified as pregame

**Impact:** outcome leakage. A cutoff reaching tipoff can observe the game, so a decision made
with it is not a pregame decision. Nothing compared the two, so such a cutoff was accepted.

**Fix.** `identity.effective_start()` and `identity.require_pregame()` (plus the chain-aware
`schedule.chain_pregame_start()` / `schedule.pregame_eligible()`), with new reasons
`START_TIME_UNKNOWN`, `START_TIME_CONFLICT`, `PREGAME_AFTER_START`.

- The **earliest known start wins**, so an early tipoff cannot leave an eligible window open.
- The **revision in force** supplies the start — never the obsolete original — so a legitimate
  reschedule is not rejected against its old date.
- **Missing or conflicting evidence never establishes eligibility:** no start recorded raises
  `START_TIME_UNKNOWN`; two starts more than 6 hours apart raise `START_TIME_CONFLICT`.
- The availability/ingestion rule is **unchanged and separate**; a future tipoff is still not
  an availability anomaly.

**Evidence:** `test_pregame.py` (18 tests). Against the prior implementation: **16 errors**,
`AttributeError: module 'nba.identity' has no attribute 'require_pregame'`.

### Defect 2 — a game could be counted in two exclusive categories

**Impact:** an absent game could be concealed. Listing one game as both canonical and missing
summed to the expected total and reported success.

**Fix.** `completeness.reconcile()` now audits exclusivity before counting: duplicate ids within
`missing`, overlap between `chains` and `missing`, overlap of a *named* quarantined id with
canonical/missing, and duplicate named ids within quarantined — each raising
`CompletenessError`. A post-accounting check asserts the categories are pairwise disjoint.
Descriptive **status history** (postponed then cancelled) is explicitly not treated as
overlapping categories.

**Evidence:** 7 adversarial tests in `test_completeness.py`. Against the prior implementation:
**2 failures + 1 error** — `AssertionError: CompletenessError not raised`.

## Review corrections before the merge decision

Each was found by the focused pre-merge review, reproduced as failing tests first, and fixed
only after the failure was observed. Defect 5 surfaced while correcting defect 3, by asking
whether the same carry-forward mistake existed elsewhere.

### Defect 3 — a reschedule could silently clear a prior neutral-site venue

`schedule.reschedule()` carried venue facts forward only when the caller left them as `None`,
but declared `neutral_site=False` as its default. `False` is not `None`, so the carry-forward
branch was unreachable for that parameter: every reschedule that did not mention the venue
wrote `neutral_site=False` over a prior `neutral_site=True`.

**Impact:** a venue property was lost by omission, inside a chain whose contract is that
nothing is lost. Nothing consumes the flag today, so no decision was affected; the defect is
that the loss would be silent once a consumer exists.

**Fix.** `neutral_site` now defaults to `None` ("unchanged"). An explicit `False` still clears
the flag — carry-forward made the parameter unreachable, not the intent.

**Evidence:** `test_reschedule_carries_a_neutral_site_forward`; against the prior
implementation, **1 failure** — `AssertionError: False is not true`.
`test_reschedule_may_explicitly_clear_a_neutral_site` pins that explicit clearing still works.
`actual_venue` was already correct, since its default was `None`.

### Defect 4 — a revision's scheduled start was accepted as proof of its availability

`RevisionChain.as_of(cutoff)` selected its revision with
`record.get('source_updated_at') or record.get('event_time')`: when a revision recorded no
`source_updated_at`, the game's **scheduled start** was substituted as the time the revision
became available.

**Impact, stated exactly.** `as_of` answers "what did we believe at date D", which is the
question a later leakage or CLV audit asks. Substituting `event_time` answers it with a fact
about the game instead of a fact about our knowledge, so a historical cutoff could be reported
as having a revision in force that did not yet exist — carrying no evidence that the revision
was ever knowable. **No eligibility leak could be constructed from it:**
`require_pregame` refuses a cutoff at or after the effective start, and the substituted stamp
is itself bounded by `event_time`, so a wrongly-selected revision still failed closed. The
defect was silently unsupported provenance, not a demonstrated qualification of an ineligible
decision. That distinction is recorded rather than rounded up.

**Fix.** `as_of` places revisions in time by `source_updated_at` alone and raises
`Quarantine(REVISION_TIME_UNKNOWN)` when any revision lacks it — a newly registered reason,
since this one is raised rather than only reported. `pregame_eligible()` converts it to
`(False, [REVISION_TIME_UNKNOWN])` instead of letting it escape, and `chain_pregame_start()`
propagates it.

**Evidence:** `test_as_of_refuses_when_a_revision_has_no_availability_time` and
`test_as_of_never_uses_scheduled_start_as_availability`; against the prior implementation,
**2 failures** — `AssertionError: Quarantine not raised` each.
`test_as_of_ignores_a_revision_learned_after_the_cutoff`,
`test_pregame_refuses_when_revision_availability_is_absent` and
`test_chain_start_refuses_when_revision_availability_is_absent` pin that the rule fails closed
without hardening into "always refuse".

### Defect 5 — a cancellation could silently clear venue facts

`schedule.cancelled()` called `schedule_record()` without venue arguments, so it took the
record defaults: every cancellation wrote `neutral_site=False` and `actual_venue=None` over
whatever the chain already held.

**Impact:** identical in kind to defect 3 — a venue property lost by omission, this time on
the one transition that is meant to change nothing but status. Demonstrated before the fix: a
revision holding `actual_venue='Paris, FR'`, `neutral_site=True` became `None`, `False` after
`cancelled()`.

**Fix.** `cancelled()` now inherits both facts from the latest revision, exactly as
`reschedule()` does. A game with no prior revision has nothing to inherit and keeps the record
defaults, and `source_updated_at` is still passed through verbatim — a cancellation records an
availability time only when the caller supplies one, so none is invented here. Because a
cancellation states the game's status rather than its venue, it gains no venue override
parameters.

**Evidence:** `test_cancelled_preserves_a_neutral_site_and_venue` and
`test_cancelled_after_a_reschedule_inherits_the_latest_venue` — the second pins that
inheritance comes from the LATEST revision, since that game was moved TO a neutral site and
only then cancelled. Against the prior implementation: **2 failures** — `AssertionError: False
is not true` each. `test_cancelled_keeps_prior_revisions_byte_identical` and
`test_cancellation_of_an_unrevisioned_game_still_appends_revision_one` pin that prior
revisions stay byte-identical with still-verifying digests and correct revision/`supersedes`
links, and that empty-chain cancellation is unchanged. Counting is unaffected:
`test_status_overlap_does_not_double_count_one_game` (which calls `cancelled()`) and
`test_cancelled_games_still_occupy_their_slot` both pass.

### Description corrections

- `identity.TIMESTAMP_FIELDS` holds **eight** fields, not ten. `timestamp_block`'s own
  docstring and `NBA_IDENTITY_CONTRACT.md` section 5 already said eight; the module table
  above was the outlier. `test_timestamp_field_count_is_eight` now pins the count.
- The `_config.yml` comment now says **seven** NBA documents are excluded: the five Package 1
  scope documents plus the two added here.
- `NBA_IDENTITY_CONTRACT.md` section 7 now states the fail-closed rule this package enforces.

### Defect found while fixing (and corrected)

`identity.effective_start()` initially called `utc()`, which demands a string, while schedule
revisions store parsed datetimes. A `_as_utc()` boundary now accepts either and still refuses a
naive datetime. The first attempt at the completeness post-audit also assumed `detail['missing']`
held bare ids when it holds dicts; corrected.

## A defect this package found in itself

The first draft of `identity.blocks_from_features` gated the event's own start time against the
decision cutoff. Because a game tips off *after* a pre-game cutoff, that flagged **every game
ever played** — the check would have been worthless while appearing to work. The Package 2A
test suite caught it, the function was corrected to gate only availability and ingestion, and
`test_game_start_after_cutoff_is_normal_not_an_anomaly` now guards the regression.

This is recorded rather than smoothed over: it is precisely the class of error that produces
confident, meaningless validation later.

## Coverage handled

Preseason, regular season, all four NBA Cup rounds, play-in, postseason, postponement,
reschedule, cancellation, neutral/international venue, and unknown-phase refusal.

## Explicitly NOT in this package

- Migration `059_p4_consumer_plan_boundaries.sql` and the `production_manifest.txt` change.
- Any real league or ESPN mapping — fixtures are synthetic and labelled as such.
- Any change to Package 1 `SOURCES`; **no live data was fetched**.
- A schedule parser. Architecture decision 4 prefers NBA's structured CDN schedule JSON over
  HTML scraping, and that requires schema, access, coverage and usage-rights verification.
  **This remains the blocking integration decision.**

## Readiness — unchanged

All **16 official-release gates remain FAIL**. `SHADOW_MODE=true`; official NBA picks and
publication remain disabled. No picks, no predictions, no paid calls, no database migration,
no deployment.
