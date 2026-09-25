# Mercer Live — house rules

Fifteen non-negotiables. They are short on purpose: a rule that needs a
paragraph to remember is a rule that gets forgotten at 11pm on a Sunday when a
number looks exciting.

These sit **on top of** the nine Open Ledger Sports house rules in `CLAUDE.md`,
which continue to apply. Where a Mercer Live rule is stricter, the stricter one
wins. Nothing here may be relaxed by Hermes, under any circumstance, for any
reason, including a result.

---

**1. Shadow mode is the default and the current state.** Mercer Live is
research-only. It observes, records and (later) calculates. It publishes
nothing. Leaving shadow mode requires the section-3 promotion review to pass and
be recorded, and Daniel's explicit authorisation. Nothing else lifts it.

**2. No live wager, ever, at this stage.** Not a play, not a pick, not a lean,
not a "worth a look", not a screenshot of a number with a raised eyebrow. In any
channel, including private conversation with Daniel.

**3. Never `LLM → opinion → wager`.** The chain runs
`DATA → MODEL → CIRCUIT BREAKERS → DECISION → LEDGER → MERCER PRESENTATION`, in
that direction only. An LLM may narrate records that already exist. It may never
be the origin of a position, and it may never sit upstream of a decision.

**4. Observations are append-only and immutable.** Never edit, delete, repair,
re-stamp or back-date a stored observation. A stale or wrong payload is recorded
faithfully — that is the correct behaviour for a capture boundary, and the
2026-09-22 stale payload is now evidence rather than a defect. A correction is a
**new** record naming the original by id, with its reason and its own provenance.

**5. No look-ahead, in anything.** Every input to any future signal must be
observable at that signal's `observed_at`. Barred as inputs: final scores, later
drives, later injury news, later line movement, closing odds, provider data
revised after capture, postgame statistics, and anything reconstructed after the
fact unless the original timestamped observation exists in the raw store and is
what is used.

**6. Separate ledgers never mix.** The football model
(`data/football/football_ledger.json`), Mercer Spotlight
(`data/mercer/mercer_ledger.json`) and any future Mercer Live record
(`data/mercer_live/ledger.json`) are three records. None may borrow a number
from another, in any display, total, post, caption or file. Pregame and live are
different strategies.

**7. Only the exact label `pregame` is pregame.** Never infer a phase from
"not live", never truthy-test it. A live in-game price mistaken for a pregame or
closing price would corrupt CLV and every comparison built on it.

**8. A refused join is recorded, never guessed.** `UNJOINED`, `AMBIGUOUS`,
`KICKOFF_MISMATCH` and `UNRESOLVED` are outcomes, not errors to be smoothed
away. A quote that cannot be joined confidently stays unjoined with its reason.

**9. Credits are spent deliberately or not at all.** The floor (5,000 remaining),
the daily cap (4,000 per ET day) and the hourly booking throttle are guards, not
suggestions. Hermes does not enable credit spend without Daniel's explicit
authorisation for that specific run. Every reading is booked through
`scripts/odds_credits.py` into `data/odds_credits.json` — never a second
independent implementation.

**10. Thresholds may be tightened, never loosened, and never because of a
result.** This is Open Ledger house rule 9 restated for live: a gate moves only
through its stated study process, with numbers recorded and a version bump. A
cold streak or a quiet stretch is precisely when the temptation peaks, and the
discipline *is* the product.

**11. A frozen contract is amended, never edited.** `mercer-live-ml1-v1.0` is
frozen. A live defect in the capture path reopens it as a **dated amendment**;
adding a field, a record kind or a provider is a **new package**. Silent edits to
a freeze record make the record worthless — see the 2026-09-25 observation-count
amendment for the pattern.

**12. No Discord output from Mercer Live.** Mode 1 (shadow) is current. Mode 2
would use a webhook name that does not yet exist and is explicitly none of
`DISCORD_WEBHOOK_URL`, `_MEMBERS`, `_LEDGER` or `_ALERTS`. No Mercer Live code
references a Discord webhook, imports a Discord module, or carries a mode
switch, and none may be added.

**13. Ops noise goes to ops, results go to customers.** "Publish your losses" has
always meant *results*. It has never meant broadcasting build failures, stack
traces or capture anomalies to paying members. On 2026-09-11 an alert fallback
chain did exactly that; the chain was removed. Different audiences, different
channels.

**14. No secrets anywhere, in any form.** No API key, token, webhook URL, Discord
secret, GitHub PAT, Whop secret, Cloudflare secret or model-provider key in a
file, a commit, a log, a document or a chat message. Never ask Daniel to paste a
secret into chat or into source. Names of configuration variables are fine;
values never are.

**15. Never commit a locally built `index.html` or `feed.xml`.** Repository rule,
and it has bitten this project before. If a local build touches them:
`git checkout -- index.html feed.xml` before staging. Stage explicit paths.
Never `git add -A`, never `git add .`. Mercer Live owns nothing at the site root
and must never write there.

---

## The one-line test

Before any Mercer Live action, ask: **does this create, imply, or move anyone
closer to a live wager?** If yes, it is out of scope today — write it up and
escalate instead.
