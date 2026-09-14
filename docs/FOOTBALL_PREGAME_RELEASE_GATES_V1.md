# NCAA / NFL pregame release gates v1

Status: operational protocol established; neither sport authorized for release.
This is not a strategy preregistration or an assertion of predictive accuracy.
Effective at the commit introducing this document. Changes require a new version
and dated rationale. Completed trials are never silently reclassified.

## Scope and preservation

Separate decisions for `nfl` and `ncaaf`, pregame full-game two-way moneyline only.
No pool of NFL results can certify NCAA or vice versa. Mercer Live and the
human-researched Mercer Spotlight remain separate products with separate records.
Existing studies, 2025 holdout restrictions, boards, commitments and results stay
unchanged. The September 13 incident amendment remains in force. No recommendation
delivery is enabled by this document, a passing test, or a successful private run.

## Gate 1: data readiness

Every eligible observation must have an exact sport, provider event ID, teams,
timezone-aware kickoff and complete two-way prices; repeated or drifting identities
are rejected. Only scheduled Tier A captures can enter this diagnostic. Capture
must precede the explicit decision and kickoff, be within 18–30 hours before
kickoff, and be no older than 60 minutes at the diagnostic decision. Each market
timestamp must be no more than 900 seconds before capture, never after capture.
Require five complete markets for a descriptive consensus. Compute median implied
probabilities before proportional de-vig. Do not average signed American prices.

These are diagnostic observation thresholds, NOT promises of executable prices
or approved strategy thresholds. Book eligibility, the final action window and
freshness at actual delivery must be preregistered separately for the candidate.
Bookmaker-level timestamps cannot substitute for missing market timestamps.
Current failures remain visible; do not relax freshness just to turn a run green.
No new paid endpoint or expanded capture budget is authorized by this protocol.

## Gate 2: strategy registration and evaluation

No replacement pregame candidate is registered or validated. First write one
falsifiable hypothesis supported by information independent of the same-time
market. Price-shopping arithmetic alone is not a forecasting hypothesis.
Record separately for each sport: strategy ID, market, source availability,
training/development/untouched periods, all already-seen data, decision time,
eligible population, exclusions, book allowlist, executable-price rule, fees,
stake convention, deterministic tie-breaks and explicit no-pick behavior.

Before any untouched scoring, freeze the economically meaningful target effect,
sample-size/power analysis, uncertainty calculation that accounts for dependence,
primary endpoint, test multiplicity policy, final evaluation date or stopping
rule, void/missing-price policy and maximum tolerable drawdown. Register numeric
thresholds with the strategy rather than inventing them after seeing returns.
Do not open the restricted 2025 holdout or reuse incident fixtures as proof.
The candidate must beat its preregistered economic criterion under conservative
execution assumptions, including uncertainty. Probability claims additionally
require calibration and scoring against the same-time market baseline. CLV is
supporting evidence, never sufficient alone. Retain all bets, passes and failures.

Until this registration and evaluation exist, strategy gate = BLOCKED, regardless
of operational correctness. A deployment engineer cannot certify this gate by
changing eligibility filters or by collecting one clean weekend.

## Gate 3: private operational commissioning

`scripts/football/private_validation.py` reads existing stored odds with an explicit
as-of time and creates an exclusive, private output outside the public repository.
It records exact source-byte hashes, per-game reasons, per-book timestamp failures,
and separate sport reports. Its fingerprinted Discord preview says PASS and contains
no recommendation. It has no sender, webhook, credentials, results lookup or ledger
writer. Repeated output paths refuse overwriting. Old data runs are clearly marked
replays, not prospective experiments. Missing captures block, never disappear.

The first run is an input/preview diagnostic, NOT the complete future recommendation
pipeline. After Gate 2 supplies an approved candidate adapter, add its immutable
selection envelope: strategy version, sport, event ID, teams, side, market, price,
book, quote timestamp, decision timestamp, kickoff, source hashes and channel tier.
Verify the actual production renderer reproduces that envelope exactly and rejects
changed prices/teams/tier, stale decisions, future inputs and wrong sport/date.

Before reopening: at least two complete operational weekends per sport including
college Saturday and NFL Sunday plus any scheduled standalone games, with zero
unexplained mismatches, unauthorized sends, duplicate deliveries or missed eligible
events. Count expected events against an independently sourced schedule; the odds
feed alone cannot prove completeness. Missed executions remain reported. This is
an operational minimum, not a statistical sample-size claim.

Test dry-run isolation, force-pause refusal, duplicate/retry handling, ambiguous
send acknowledgements, channel routing, message chunk limits, changed kickoff,
provider outage, absent market timestamps and settlement/reveal idempotency. Never
silently retry an uncertain send. Record reservation, acknowledgement and verified
message receipt separately. Preserve original selections through later grading.

## Gate 4: controlled release and ongoing monitoring

For each sport provide a signed-off evidence packet: frozen strategy, source
manifest, complete evaluation including failures, independent review, private-run
reports and receipts, production-renderer tests, current pause state and rollback.
Explicit owner approval is required to reopen. Begin with manual approval of every
candidate. Automatic sending requires a subsequent reviewed release decision.

Critical identity, freshness, provenance, routing or delivery faults must block the
affected batch and reach an operator before another send. Monitor selection → frozen
envelope → message receipt → original-selection settlement. No historical deletion,
reranking, concealed loss, or undisclosed record change is permitted.

## Current disposition

| Gate | NFL | NCAA |
|---|---|---|
| Strict source freshness / independent schedule completeness | Not established | Not established |
| Registered, successfully evaluated pregame strategy | Missing | Missing |
| Input audit and private PASS preview | Implemented; diagnostic only | Implemented; diagnostic only |
| Real candidate-to-production-message commissioning | Blocked on candidate | Blocked on candidate |
| Multi-weekend prospective evidence | Not collected | Not collected |
| Member delivery | Paused | Paused |

Next research deliverable: a candidate specification and feasibility/power analysis,
or a documented decision that available evidence does not support a pregame product.
Next data deliverable: resolve market timestamp availability and independent schedule
coverage without conflating the separate Mercer Live feed with this pregame system.
