# Mercer Live — the Discord plan

**Not implemented. Nothing in this file is active or may be activated by Hermes.**

No Mercer Live code references a Discord webhook, imports a Discord module, or
carries a mode switch. The self-test asserts it. Adding any of those is a Claude
change requiring Daniel's authorisation and, for anything beyond Mode 2, a
passed promotion review.

Authoritative text: `docs/MERCER_LIVE_V0.1_PREREGISTRATION.md` sections 18, 19
and 20.

---

## The three modes

### Mode 1 — Shadow · **CURRENT**

**No Discord output of any kind.** Not a pick, not an alert, not a "heads up",
not a status ping, not a quiet note in an admin channel. Mercer Live is silent.

This is v0.1, and it is where the system is today.

### Mode 2 — Private beta · not built

Output only to a **restricted test/admin channel**, through a webhook whose
environment-variable name **does not exist yet** and is explicitly **none of**
`DISCORD_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL_MEMBERS`,
`DISCORD_WEBHOOK_URL_LEDGER` or `DISCORD_WEBHOOK_URL_ALERTS`.

Reusing an existing webhook name would be the single easiest way to leak a live
research signal into a paying channel, so the separation is structural rather
than procedural. The repository has already been bitten once by a fallback chain
routing the wrong content to the wrong audience — on 2026-09-11 an ops stack
trace was broadcast into `#members-only` because an alert secret was unset, and
the chain was removed.

Mode 2 is **explanation over records only**. It reports what already happened
and what the ledger already says. It does not produce anything actionable.

Entry condition: the section-3 promotion review passes.

### Mode 3 — Public / member live desk · not built

Only after the promotion passes **and is recorded**. This is the mode that would
constitute a product. It is the furthest thing from current state and should be
treated as a destination, not a plan.

---

## The persona boundary

This is the part most likely to be eroded by good intentions, so it is stated as
two lists.

**D.J. Mercer may eventually:**

- explain a deterministic signal the engine already produced
- summarise model state
- say why a play qualified, or why it failed a gate
- summarise the record, including the losses
- describe observed game state

**D.J. Mercer may never:**

- invent an unmodelled play
- override a circuit breaker
- change an allocation
- turn a PASS into a PLAY
- manufacture confidence
- conceal losing history

The test that captures all of it: **if the LLM is unavailable, the engine behaves
identically.** The persona is a renderer over records. If removing Mercer would
change a decision, Mercer has become upstream of the decision, and the chain has
inverted into `LLM → opinion → wager`.

## Copy bars

The football copy bars apply to **every** Mercer Live surface until a promotion
has been recorded. Barred: `edge`, `+EV`, `the model likes`, and anything else
that asserts an expectation the project has not established.

This is Open Ledger house rule 4 and 8 doing their job: site and channel copy
must describe what the system actually does today. Today it observes.

## Legal boundary

Any future actionable posting carries, **verbatim from the existing project
copy**: the analytics-not-a-sportsbook framing, 21+, 1-800-GAMBLER, and the
no-guarantee / not-betting-advice language.

Not a paraphrase. Verbatim. Nothing public is implemented now.

## What Hermes must not do

- Must not create, request, or configure a Discord webhook for Mercer Live.
- Must not post Mercer Live content — observations, anomalies, summaries or
  anything else — into any existing Discord channel.
- Must not route Mercer Live alerts through the ops alert channel. That channel
  belongs to the pipeline workflows; Mercer Live has no production scheduler and
  therefore nothing to alert about.
- Must not discuss a live game in a way that reads as a recommendation in any
  channel, under the persona or otherwise.

Mercer Live's output today is a report to Daniel and tickets to Claude. That is
the whole distribution list.
