# Pregame feasibility audit — September 14, 2026 UTC

Disposition: NFL feature research is feasible from existing sources. NCAA model
research is not covered by those sources or by the NFL preregistration. Neither
sport has passed a strategy release gate. No fit, prediction, score, wager or
holdout read was performed in this audit.

## Verified source readiness

All 15 cached NFL play-by-play seasons, 2010–2024, match the SHA-256 and byte count
in `data/football/pbp_manifest.json`. Each contains the columns named by all six
feature families in `FOOTBALL_PREREG_V03.md`. The cache resides in the original
working copy; it is gitignored and absent from a fresh clone. The new
`research_feasibility.inventory` checks hashes and gzip CSV headers only. It does
not aggregate rows or inspect 2025, even if a 2025 file is present.

Schema existence is not proof of completeness, feature meaning, historical
availability or predictive power. Those are separate tests before fitting.
No raw data was copied into Git or downloaded. NCAA source readiness is not
established by an NFL manifest.

## Fresh capture coverage check

At 2026-09-14 01:48:40 UTC, a read-only ESPN schedule request for each sport
covered September 14–21 (college FBS group 80). Exact-name, exact-kickoff joins
against stored captures produced:

| Sport | Usable T-24 observation found | Not yet in T-24 window |
|---|---:|---:|
| NFL | 1 | 16 |
| NCAA | 0 | 75 |

These are the provider's returned events, not a certification of the provider's
schedule completeness. The private report retains the query, retrieval time,
response hash, schedule metadata and per-event decisions. No results are scored.
Cross-provider name differences fail visibly; no fuzzy identity repair is used.
An observation is not proof of a currently executable price or valid strategy.

The earlier diagnostic found a stale NCAA capture at a chosen decision time.
That does NOT establish an operational outage: here no NCAA window is due.
Future NCAA captures must be examined when their actual windows open; do not
purchase an unnecessary pull merely to replace an old file with a newer one.

## Scheduler limitation exposed

`capture_schedule.captures` collects timestamps from filenames. `main` labels a
window satisfied when any capture timestamp lies inside it, without checking that
the particular fixture, prices or market timestamps are present. Separately,
`kickoffs` continues after a schedule-fetch error, so incomplete retrieval can look
like no games. These are inadequate as release evidence.

The new private `research_feasibility.coverage` requires the actual fixture,
matching sport/identity/kickoff and valid complete timestamped markets. Tests
reproduce an empty capture inside a window and show it cannot satisfy coverage.
This audit does not change production polling or cause additional paid calls.
Before release, scheduler health must expose schedule failures and missing-event
coverage to an operator. A guarded capture retry needs an explicit budget policy;
blind retries are not introduced here.

## NFL candidate: repair the existing draft before fitting

The existing representation hypothesis is a defensible research question:
whether richer summaries of prior play-by-play improve the existing forecast.
It is not a proven betting advantage. Preserve all six declared families and
the original draft as evidence. A superseding preregistration must resolve:

1. **Selection/evaluation conflict.** Section 8 says selection uses TUNE only;
   section 9 chooses combined-model membership using VALIDATE. Choose families
   only within chronological development folds ending in 2021, and lock that
   choice before reporting 2022–2024. Treat those already-seen later years as
   historical comparison, never untouched confirmation. A final untouched
   prospective evaluation needs its own freeze date and sample-size analysis.
2. **Inference precision.** "Holm-adjusted bootstrap CIs" is not an executable
   method without specifying statistics, p-values/inversion, resampling unit,
   draws, seed and treatment of correlated games/weeks. Specify the complete
   procedure and simulate its null behavior before seeing treatment scores.
3. **Feature semantics.** Fix denominators, sack/scramble handling, drive boundaries,
   offensive versus defensive scores, turnovers, red-zone trips, overtime, null
   handling, sample thresholds and train-only standardization. Existing `pass`
   includes sacks and scrambles; it is not simply completed/attempted passes.
4. **Availability.** Register derived feature clocks with the existing as-of
   guards before fitting. A kickoff+36h convention is an explicit assumption;
   modern reconstructed historical files are not archival publication receipts.
5. **Release separation.** Improving the old model is not beating an executable
   market price. The development score cannot authorize picks, stakes, purchased
   holdouts or automatic publication. Existing holdout restrictions remain.

The methodological selection issue is consistent with the official
[scikit-learn discussion of selection bias](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html).
Column semantics are documented by
[nflfastR](https://nflfastr.com/reference/clean_pbp.html).

## NCAA disposition

The existing draft explicitly excludes NCAA FBS. No NCAA-specific historical
feature manifest, point-in-time availability contract, candidate model or
untouched evaluation is established by the reviewed NFL assets. Keep NCAA PASS
until those are defined separately. Do not extend NFL weights or interpret the
NFL study's success/failure as NCAA evidence. Any new paid source, API access or
historical acquisition requires a concrete feasibility and budget proposal.

## Next deliverables

- Superseding NFL development preregistration with executable feature definitions,
  chronological selection and inference plan; freeze before feature scoring.
- Synthetic feature and timing tests, followed by source completeness checks
  on allowed development years. No 2025 access and no reopening closed studies.
- NCAA source/identity/availability proposal with a distinct candidate hypothesis.
- Private event-level checks at the next real capture windows; no automatic
  monitoring or recommendation dispatch was enabled by this work.
