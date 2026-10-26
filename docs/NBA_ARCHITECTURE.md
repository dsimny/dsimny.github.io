# NBA architecture v0.1

Package 1 implements contracts and evidence capture, not a scoring model. The
user's revised `NBA Model V1.docx` (October 6, 2026) supersedes the original audit.
The source document fingerprint is recorded in NBA_PACKAGE1_REPORT.md.

## Boundaries

NBA is a separate package under scripts/nba. Existing MLB, football, frozen
Mercer ML-1, market SQL and public ledgers remain unchanged. No imports from NBA
are added to existing producers. No paid feed, migration, publication or pick
generation is enabled by Package 1.

Raw evidence -> identity and phase validation -> cutoff-filtered features ->
market-independent joint scores -> immutable projection -> canonical market
comparison -> immutable decision -> closing observations -> settlement events.

PostgreSQL's future nba schema is the prediction source of truth. The temporary
encrypted raw-file store is source evidence only, with an importer-ready manifest;
it is not a prediction ledger. Immutable records carry version, cutoff, source
hashes, replay artifacts and a stable order. Corrections append supersession
events. NBA market-eligible beliefs may link to existing model.beliefs; pure
projections and PASS records persist independently of market eligibility.

## Revision decisions

Global preseason/shadow flags are permission ceilings, never game-phase inputs.
Each schedule record declares PRESEASON, REGULAR_SEASON, CUP, PLAY_IN or POSTSEASON.
Cup GROUP, QUARTERFINAL and SEMIFINAL count toward regular-season statistics;
CHAMPIONSHIP does not. Neutral/international venue metadata is independent.
Unknown phase or Cup round refuses classification; do not infer from the date.

Player rotations total 240 regulation minutes per team plus 25 per overtime;
exactly five players are on court. Overtime is included in full-game settlement.
Missing availability is UNKNOWN. Uncalibrated active probabilities are research
assumptions with sensitivity ranges, not production inputs.

Regular screens: spread 1.5 points, total 2.5 points, EV 3%. Preseason: 3, 5, 6%.
Both relevant screens must clear for a research PLAY. LEAN clears one only;
WATCH requires a named pending condition; hard failures override both with PASS.
Shadow PLAY is NOT AN OFFICIAL PLAY. A is withheld until confidence is calibrated.
Oversized-edge sanity review is mandatory; provisional ceilings are frozen in
NBA_PREREG_V01.md and must not be adjusted to retain appealing candidates.

The pure model estimates possessions and PPP with shrunken opponent-adjusted
team/lineup effects and roster-adjusted priors. Four-factor, shot-style, venue and
schedule additions require ablation validation. Shared pace/scenarios create
joint score dependence. Seeded 10,000+ simulations resolve ties through overtime
and retain win/loss/push mass. Parameter uncertainty, predictive uncertainty and
Monte Carlo error are separate outputs. Market-aware E is a separate challenger.

## Package order

1. Contracts, offline fixtures, existing baseline and immediate free raw capture.
2. Canonical identities and complete phase-aware schedules.
3. NBA odds wrapper and persistent provider timestamps.
4. Performance/possession/lineup warehouse.
5. Availability and replacement rotations.
6. Features and roster-adjusted priors.
7. Pure distributions.
8. Market comparison, decisions, sanity review and exposure.
9. Walk-forward evaluation, closing capture and append-only grading.
10. Shadow operations, monitoring and restore rehearsal.
11. Per-market official release only after all 16 readiness gates pass.

Source licensing/retention is a production gate. Private raw capture does not
assert redistribution permission. Current-season injury source coverage may fail;
those failures must persist rather than be filled with an older season's report.

## Readiness

All 16 NBA official-release gates remain FAIL until their evidence is stored:
schedule, odds, timestamps, source terms, availability, rotations/priors,
reproducible projections, leakage controls, immutable ledger, CLV, grading,
calibration, executable EV, exposure, publication, isolation/monitoring/rollback.
Opening night does not promote the system. Package 1 status and blockers are
reported separately; a capture test pass is not an NBA prediction readiness pass.
