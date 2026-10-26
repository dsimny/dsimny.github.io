# NBA data contract v0.1

Contract ID: nba-contract-v0.1. Frozen for Package 1; amendments get a new version.

## Identity and phase

Canonical games use authoritative league game ID plus explicit provider crosswalks,
never team names plus slate week. Teams and players use stable IDs; aliases carry
provider and validity interval. Unknown or multiple matches quarantine the game.
Home/away, actual venue, neutral site and schedule revisions are separate facts.
Phase is supplied by schedule evidence, not a global flag or calendar heuristic.
Cup rounds explicitly determine regular-season inclusion. PLAY_IN and PRESEASON
are excluded from regular-season ratings. Postseason training is separately labeled.

## Time

All stored times are timezone-aware UTC. Preserve event time, source published/
updated time, request start, response received, availability, ingested and decision
cutoff separately. Display America/New_York with offset. Future source times are
retained as anomalous raw facts but blocked from features. Prospective facts must
be available and ingested by cutoff. Retrospective data with proof of historical
availability is labeled BACKTEST. Unknown historical availability blocks a claim.

## Prices and settlement

Spread is added to the selected team's score. Odds are finite American prices
<= -100 or >= 100. Decimal conversion is 1+price/100 for positives and
1+100/abs(price) for negatives. No-vig pairs use the same book, game, period and
opposite selections at the same underlying line. Provider and observation times
are distinct and both persist; an unchanged successful poll remains evidence.

For decimal d, EV per unit = p_win*(d-1)-p_loss. Push refunds risk. Fair decimal
is 1+p_loss/p_win; conditional non-push break-even is 1/d. Full-game scoring includes
overtime, per declared book rules; tied regulation scores are not final. Corrections
append, rather than overwrite. CLV uses the last valid observation before actual
start with its age, never an in-play price. Unlike-line price CLV is unavailable
unless an explicitly labeled conversion has been validated.

## Raw evidence contract

Schema: nba-raw-v1. Each observation has UUID, run ID, source ID/kind, URL, response
status, fetch start, observed_at, nullable provider_last_modified/header_date,
HTTP content type, unencrypted SHA-256/byte length, encrypted object SHA-256/length,
object-relative path, diagnostic error code and report links observed on the page.
HTTP Date/Last-Modified are header provenance, not player-status publication times.
Never infer historical availability from either header alone.

Use a new observation record per request, including unchanged content and failures.
Objects and observation files are exclusive-create and never rewritten; one immutable
run manifest lists all observations in that run. UUIDs identify records, not chronology:
use recorded observation timestamps and run ordinals. Downloads are encrypted using
Fernet, with a key outside Git. Verify encrypted hash, decrypt, then verify raw hash
and byte count before import. Raw files are outside all configured Git worktrees.
Failed/non-2xx responses are preserved but never called current valid source data.
An HTTP 200 page can still be a challenge or empty shell; semantic coverage remains
UNVERIFIED until a later source parser establishes it.

Source URLs are HTTPS NBA domains only, without embedded credentials or query secrets.
Redirects are revalidated before fetching. No API keys, odds requests, cookies or
authorization headers. A bounded 8 MiB response and 30-second timeout protect the lane.
Report-link discovery never treats an older season report as current; the importer
must validate game date, season, player ID and publication time.

## Synthetic fixtures

Fixtures in tests/nba/fixtures are hand-authored synthetic cases, never real captured
NBA evidence. They cover repeat matchups, neutral/phase/Cup classification, DST,
provider timestamp separation, future availability and unchanged raw responses.
Real raw capture remains encrypted outside Git. Fixtures do not establish provider
coverage, licensing, model accuracy or production readiness.
