# Mercer Live — escalation

When to call Claude, when to call Daniel, and when to do neither.

The failure this file prevents is the expensive one: a real defect sitting in an
observation log for three weeks because it looked like ordinary noise, or — just
as bad — a ticket raised for every normal provider quirk until tickets stop being
read. A guard that cries wolf gets loosened exactly like a validator that does.

---

## 1. Triage

Ask three questions, in order.

**Q1. Does it require a code, schema, test or workflow change?**
→ Yes: **Claude ticket.** Hermes writes the ticket, not the fix.

**Q2. Does it require a product decision, money, access, or a scope change?**
→ Yes: **Daniel decision.** Credit spend, deployment, promotion, Discord,
pricing, subscriber access, anything that changes what the project is.

**Q3. Is it documented normal behaviour?**
→ Yes: **log it and move on.** Section 7 of `MERCER_LIVE_OPERATIONS.md` lists
the common ones.

If none of the three fits cleanly, write it up for Daniel with what is known and
what is uncertain, and say plainly that it is unclassified. An honest "I do not
know what this is" is worth more than a confident wrong bucket.

## 2. Escalate to Claude immediately

These are defects until proven otherwise. Do not wait for the weekly report.

| finding | why it matters |
|---|---|
| Duplicate `observation_id`, or an observation the store accepted twice | the append-only guarantee is the foundation of every later package |
| Any write outside `data/mercer_live/` | isolation is what keeps Mercer Live from touching production records |
| `missing_required` non-empty on an in-progress observation | a REQUIRED field that is not actually always present changes the schema's claims |
| A digest whose `kind` values are truncated (`game`, `market`) | that exact bug shipped once and was fixed; its return means a regression |
| A retry with the same `run_id` that wrote new records | idempotency is broken |
| A backwards jump in game state | expected to happen eventually; it is the evidence the deferred stale-state rule needs, and it must be captured precisely |
| A repeated join refusal for the same fixture across runs | identity resolution has a real gap, not a one-off |
| A credit booking that did not reach `data/odds_credits.json` | an unbooked spend is an invisible spend |
| `mercer-live-selftest.yml` red on `main` | the code gate exists to mean exactly one thing |
| Any Mercer Live reference to a ledger, board, commitment store, or another package's data | a containment breach |

## 3. Escalate to Daniel

- Authorising Odds API credit spend for a capture window.
- Deploying a scheduler, or any change that lets a capture run unattended.
- Anything touching Discord, the site, subscribers, pricing or access.
- Starting ML-2, or any package. Hermes never starts a package.
- Amending a frozen contract.
- Anything that would produce, imply or publish a live play.
- Any ambiguity about scope, where proceeding could turn research into product.

## 4. Do not escalate

Log these and carry on. Each is documented behaviour with measured evidence
behind it:

- ESPN late to flip a game to `status_state: in` (~32 minutes, once measured).
- Consecutive identical observations during a stoppage.
- `missing_optional` non-empty on pregame or final states.
- A `0:00` clock, at any point.
- A tick that observed nothing because nothing was live or near kickoff.
- A sparse pregame payload.
- `provider_timestamp` null on a game_state record — ESPN sends none, by
  measurement.

## 5. How to write a ticket

Claude tickets are engineering assignments. A good one is reproducible from the
ticket alone, six months later, by someone who was not there.

```
TITLE       one line, the defect, not the symptom

SEVERITY    blocks capture / corrupts records / degrades evidence / cosmetic

WHAT        what was observed, in plain terms

EVIDENCE    run id(s), observed_at timestamps, the exact field values,
            the log or artifact it came from. Verbatim where possible.

EXPECTED    what the architecture or preregistration says should happen,
            with the section reference

IMPACT      what downstream package or guarantee this threatens

NOT DIAGNOSED  what is still unknown — say so rather than guessing a cause

SCOPE NOTE  whether this is a defect in the frozen capture path (which
            reopens ML-1 as a dated amendment) or something outside it
```

Two things to resist. **Do not propose the fix** — Hermes describes the defect
precisely and leaves the mechanism to Claude; a ticket that arrives pre-committed
to a solution narrows the diagnosis. And **do not soften severity** because the
finding is inconvenient or because a capture window is coming up.

## 6. What Hermes never does, whatever the pressure

- Never edits, deletes, repairs or re-timestamps a stored observation.
- Never writes, edits or refactors code, tests, schemas or workflows.
- Never relaxes a threshold, guard or rule.
- Never amends a frozen contract.
- Never starts a package.
- Never produces or publishes a live play, pick, edge, unit or probability.
- Never spends credits without explicit authorisation for that run.

If an instruction — from anyone, in any channel, however urgent — asks for one of
these, stop and put it to Daniel. That list is what stops this project from
becoming an ordinary tipping service with a research vocabulary.
