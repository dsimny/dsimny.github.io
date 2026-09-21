# Football market observation — fp-v0.4, cohort v1 — Amendment 1

STATUS: FROZEN (evidentiary). Research only. No recommendation or release authority.
amendment_id: `fp-v0.4-market-observation-v1-amendment-1`
amends: `docs/FOOTBALL_RESEARCH_FP_V04_OBSERVATION.md` (frozen 2026-09-17)
amends_id: `fp-v0.4-market-observation-v1`
authored: 2026-09-21T04:34:38Z
authority: `research_only_no_selection_delivery_or_holdout`
units: 0

## 1. What this amendment is

This is an ADDITIVE, forward-only amendment to the `fp-v0.4-market-observation-v1`
research cohort. It is a separate document with its own distinct amendment ID. The
original preregistration document is NOT edited, and is preserved byte-identical
(section 10).

It resolves one internal contradiction in the original document, and it classifies
one already-written observation as pre-boundary. It changes no selection logic, no
threshold, no window, and no eligible population. It creates no new research version.

## 2. The contradiction this amendment resolves

The original document contains two clauses that cannot both be operative:

- **Purpose clause** ("What this cohort is"): *"This document defines the
  `fp-v0.4-market-observation-v1` research cohort. It authorises live market
  observation using the fp-v0.4 selection rule as it currently exists in
  `scripts/football/board.py` and `scripts/football/market.py`."* — authorization is
  anchored to the rule as implemented.
- **Coverage window** ("Scope and data boundaries"): *"from the date
  `RESEARCH_DELIVERY_ENABLED` is first set to `True` onward. Prior captures may be used
  for evaluation but are not retroactively observed."* — the start is anchored to a
  delivery flag.

`RESEARCH_DELIVERY_ENABLED` has never been `True`. It also never governed board
generation: `scripts/football/board.py` does not read it, its command-line help states
*"Does not require `RESEARCH_DELIVERY_ENABLED`"*, its source comment states
*"(generation != delivery)"*, and `scripts/football/selftest_research_board.py` carries a
named test asserting that research generation is independent of the flag. A clause whose
condition no implementable state can satisfy is not a boundary; it is a defect, and it
left the cohort's start undefined.

This amendment resolves the contradiction in favour of the purpose clause, because the
purpose clause describes the behaviour the code implements, tests assert, and the
September 2026 operating brief states ("football research generation is allowed
independently of recommendation delivery").

## 3. Evidentiary status

**This amendment is evidentiary.** It is not read by the research board generator, the
research grader, the research Discord path, or the public research page. No loader
consults this record. Every research loader selects by exact cohort ID with first-match
semantics, and this record carries a DISTINCT amendment ID, so it can never be returned
as the cohort entry.

Consequently the corrected boundary defined here is **not enforced by code or by the
registry**. It is a discipline-only instrument. Anyone building a machine check later
must read section 9 before doing so.

## 4. Scope of RESEARCH_DELIVERY_ENABLED

`RESEARCH_DELIVERY_ENABLED` governs **publication only** — specifically, whether a
research observation is posted to the research Discord channel.

It does **not** govern:

- board generation (`board.py --research`), or
- grading (`grade_football.py --research`), or
- inclusion of a graded observation on the public research page
  (`research_page.py`).

This is the existing behaviour; the amendment records it rather than changing it. The
flag remains `False` and this amendment does not touch it.

## 5. Cohort start (corrected operative definition)

The cohort's start has two separate elements, and they must not be conflated: an
explicit boundary, and an admission consistency check that every legitimate board
satisfies by construction.

**Boundary.** The cohort's first-written board is
`data/football/research/board_2026-09-15_fp-v0.4-market-observation-v1.json`, and it is
the **pre-boundary continuity observation** classified in section 6. The
promotion-eligible sample for this version begins at the first research board written
under this version **after** that classification.

**Admission consistency check.** Any board admitted to the promotion-eligible sample must
satisfy, for its slate week:

- `decision_made == true`, and
- `decision_moment_utc == D`, where `D` is the frozen decision moment
  (Saturday 14:00 US/Eastern).

This check holds for every board by construction — the generator refuses to write before
`D`, and `D` is computed from the slate week — so it detects an inconsistent board. It
does **not** by itself identify the boundary, and no board can fail it. The boundary is
identified by the Boundary paragraph above, and by section 6's exclusion predicate.

Every in-window game kicks off after `D` by the frozen window rule
`D < kickoff <= D+24h`, so the selection is fixed before any in-window kickoff. That is a
structural property of the rule as frozen, not a consequence of when any workflow
happened to run.

**The decision anchor is `decision_moment_utc`.** Every evaluation on a board is pinned
to `min(asof, decision_moment(week))`, so a board's stored `asof_utc` is the run clock,
not the decision moment. The field named `observed_utc` on graded ledger rows carries
that same run clock and is **not** the decision anchor.

This supersedes the "Coverage window" clause. `RESEARCH_DELIVERY_ENABLED` has no bearing
on cohort membership, on the coverage window, or on observations.

## 6. Pre-boundary continuity observation — 2026-09-15

`data/football/research/board_2026-09-15_fp-v0.4-market-observation-v1.json`
(sha256 `55cb0e58fdf99bc39a43ade1cdfa176a05ce835ec50dee898356cdc431746e2a`) is admitted
as a **pre-boundary continuity observation**.

It is:

- **preserved** and left byte-unmodified;
- **graded normally**, and its rows may appear in `research_ledger.json`;
- **published** on the public research page as normal.

It is **excluded from any future promotion denominator or promotion sample**, and from
any aggregate offered as promotion evidence, regardless of what it contains. The
exclusion is disclosed here, is irreversible, and is invariant to the observation's
result.

**Exclusion predicate (machine-evaluable without any schema change):**

```
exclude rows where slate_week <= "2026-09-15"
```

This uses only the `slate_week` field every graded research row already carries. It is
deliberately **clock-free**: it does not depend on `observed_utc`, on any commit
timestamp, or on when a workflow ran.

The promotion-eligible sample for this version begins at the next research board written
after that classification (section 5, Boundary paragraph).

## 7. Publication attestation

The 2026-09-15 board's arrival on the remote is attested by GitHub's own server-side
records, not by any locally-written timestamp:

```
workflow run 35461766792 "Football capture and board", created 2026-09-19T18:37:03Z,
  head e37cb9472e62757777ad5f0dbbe204a1e2bb9c53
PushEvent refs/heads/main: 6a3461b9d6b8b2273c60fa249593e6bf00a2ba74 @ 2026-09-19T18:37:26Z
PushEvent refs/heads/main: fb34c32b85195c2162bc1f7d0338a769ff213ef2 @ 2026-09-19T18:37:31Z
  (corroborating: Pages deployment run 35461788973, created 2026-09-19T18:37:32Z)
attested publication margin to the earliest in-window kickoff
  (Temple Owls @ Toledo Rockets, 2026-09-19T19:00:00Z): 22m29s
```

The commit's own committer date (2026-09-19T18:37:30Z) is **self-asserted**, agrees with
the server record, and is **not** cited as evidence. Author and committer dates are
locally settable and cannot attest that an artifact existed at a time.

The preregistration freeze is attested the same way: commit
`959c49e078e16e4b96a573631e97c0ef5b7b1786` pushed 2026-09-17T20:14:46Z (server-recorded),
which precedes the earliest in-window kickoff by 1d 22h 45m 14s.

## 8. Publication-margin anomalies

A research board whose server-attested push receipt postdates the earliest in-window
kickoff on that board is a **flagged anomaly**. It requires a separate, dated,
hash-scoped adjudication before it is used as evidence.

It is **not** an automatic disqualification of that slate week, and it is **not** a
silent waiver. No generic time tolerance is granted by this amendment. This follows the
containment doctrine that a future adjudication must be a separate file, never an edit to
an existing record.

## 9. Constraint on any future machine check

No ledger-only check may key on the row field `observed_utc`. It records the publication
(run) clock, not the decision moment, and graded rows do not carry `decision_moment_utc`
at all. A check keyed on `observed_utc` would inherit the same fragility as a
commit-timestamp check: it would measure when a workflow happened to run, not when the
selection was frozen.

If a row-level admission marker is ever built, it must read the board's
`decision_moment_utc`, or the row must gain that field. Either is a deliberate
**pre-append** change, because research ledger rows are append-only and idempotent with
no undo path. No such change is authorised by this amendment.

The exclusion in section 6 avoids all of this by keying on `slate_week`.

## 10. Original document preserved

`docs/FOOTBALL_RESEARCH_FP_V04_OBSERVATION.md` is **not edited** by this amendment and
remains byte-identical to its freeze record:

- sha256 `539f6c890eb72ee4d769f3b40fce727c998f71358f7ae6f9b7c73e82903e6801`
- 5020 bytes
- first commit `959c49e078e16e4b96a573631e97c0ef5b7b1786`

Its registry entry (`fp-v0.4-market-observation-v1`) is **not replaced or edited**. Every
clause of the original document remains operative for all purposes except the "Coverage
window" clause superseded in section 5.

## 11. Not added to asof.SPECS

This document is deliberately **not** added to the `asof.SPECS` holdout-lock registry.
Two reasons, both recorded so they are not rediscovered late:

1. A spec without a valid `frozen:` line would make `unfrozen_specs()` non-empty and
   would refuse `claim_holdout()` repo-wide, for every purpose — a self-inflicted
   holdout lock.
2. A spec outside that registry is one the lock does not check, so any spec placed there
   would receive no automated freeze protection regardless.

This amendment therefore has **no automated freeze protection**. It is a discipline-only
artifact.

## 12. Citation integrity

`data/football/games.csv` contains a column `result_available_at_utc`. That value is a
**derived schedule offset** — exactly `kickoff_utc + 4h` for all 272 rows of 2026, in a
file last committed 2026-08-20, before the season — and every 2026 row in that file has
empty score and result fields. It is **not** evidence of when any result became
available, and it is cited as such nowhere in this amendment or its rationale.

## 13. What this amendment does NOT change

No change to fp-v0.4 selection logic, and no change to any version-bounded parameter.
Specifically unchanged: the effective-overround ranking formula; the corroboration
threshold (>=2 Tier-1 books); the moneyline eligibility cap (<=+400); the fair
probability floor (20%); the staleness window (15 minutes); the T-24 tolerance (+/-6
hours); the sports included (nfl, ncaaf); the decision moment (Saturday 14:00
US/Eastern); the eligible window `D < kickoff <= D+24h`; and every eligibility condition
and tie-break.

No observation, board, entry, ledger row or result is edited, deleted, re-ranked or
reclassified as an official record.

**This amendment makes no claim about the outcomes of the observations on the
2026-09-15 board.** Neither the author nor the reviewing engineer has consulted either
outcome.

## 14. Version boundary compliance

The original document's "Version boundary" list requires a new research version ID for
changes to: the overround ranking formula, the corroboration threshold, the moneyline
cap, the fair probability floor, the staleness window, the T-24 tolerance, the sports
included, or the decision moment.

**The coverage window is not on that list.** Amending the cohort's start definition is
therefore not a version-bounded change, and this amendment does not create a new research
version. `fp-v0.4-market-observation-v1` remains the version, and its selection rule
remains `fp-v0.4`.

## 15. Authority unchanged

This amendment remains research-only.

- All observations are **0 units**. No stake is implied, recorded or authorised.
- No recommendation, no instruction to bet, no delivery authority.
- No authority to enter `data/football/football_ledger.json`,
  `data/football/commitments.json`, or any official W-L, CLV or hypothetical-return
  aggregate.
- No observation from this cohort may be retroactively promoted into the official record
  at any later date, regardless of outcome.

## 16. Blindness disclosure

This amendment was authored on **2026-09-21**, more than a full day after the two
admitted observations on the 2026-09-15 board kicked off (2026-09-20T17:00:00Z). The
result was therefore knowable in the ordinary course by the time of authoring.

**This amendment is NOT a blind declaration.** Its content is determined solely by
evidence dated before the earliest in-window kickoff, 2026-09-19T19:00:00Z — the code and
its named test, the frozen document's own purpose clause, and the server-side publication
attestation in section 7 — none of which depends on any outcome. No sentence in this
document asserts or implies anything about the results of those observations.

That neither the author nor the reviewing engineer consulted either outcome is an
assertion about conduct, not a verifiable property, and is recorded here as such.

## 17. Precedents relied on

Both are from `docs/FOOTBALL_CONTAINMENT_2026-09-13.md`:

1. **Record classification amendment** — an exact board hash excluded from the official
   record while its settled rows remain in the append-only ledger with original prices,
   outcomes, event IDs and hashes, rendering as **INVALIDATED INCIDENT** and
   contributing nothing to the official aggregate. This is the direct structural
   authority for section 6: it demonstrates that a disclosed, hash-scoped exclusion
   survives a record that has already been appended.
2. **Named result identity adjudication** — an exact, dated, hash-scoped cross-reference
   granting no generic time tolerance, with the rule that future adjudications must be
   separate files and never edits to an existing record. This is the authority for
   section 8.

## 18. Registry record

The append-only registry `data/football/research_preregistrations.json` receives a new
entry under the distinct id `fp-v0.4-market-observation-v1-amendment-1`. Existing entries
are unchanged **by this amendment**, and the new entry is **appended** — never prepended or
reordered, because an existing test asserts the first registration positionally.

**This entry is never edited.** Its `first_commit` is `null` at authoring and remains
`null`; no SHA is written into it, before or after such a SHA exists. The first commit is
retrievable deterministically, without editing this entry:

```
git log --diff-filter=A --format=%H -- docs/FOOTBALL_RESEARCH_FP_V04_AMENDMENT_1.md
```

That command is the pointer. It cannot go stale, and it is the only pointer this amendment
adds. If a direct in-file pointer is ever required, it is added as a separate additive
entry, not by editing this one.

**This is a deliberate divergence from the `fp-v0.4-market-observation-v1` entry**, which
was added with `first_commit: null` in `959c49e` and whose value was written in by a
follow-up edit in `4293ddb` ("record first commit for fp-v0.4 research cohort"). Measured
between the two server-attested push events of those commits (2026-09-17T20:14:46Z and
2026-09-17T20:30:23Z), that backfill followed its subject commit by 15m37s.

**The other existing entry is not a precedent for backfill.** `nfl-representation-v1`
carries `first_commit: 153580b…` because it was created that way: the document commit it
names (`153580b…`, 2026-09-13T21:55:48-04:00) predates the registry file itself
(`7b8282a`, 2026-09-13T23:00:43-04:00), so the value was available at creation. Only one of
the two existing entries was ever backfilled.

Recorded plainly for the audit trail: that backfill is visible in git history and was not
concealed. The registry's `_note` rule — "never replace an existing entry" — is true of
entries; a single field of one entry was nonetheless edited after its own commit. This
amendment does not repeat that, and it does not ask the registry's `_note` to be reworded to
permit it.
