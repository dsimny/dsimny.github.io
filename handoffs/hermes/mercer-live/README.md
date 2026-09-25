# Mercer Live — Hermes handoff

Start here. This directory is the durable operating brief for **Hermes**, the
operations assistant for Mercer Live. Read this file, then
`HERMES_BOOTSTRAP_PROMPT.md`, then the rest in any order.

Written 2026-09-25, immediately after ML-1 was frozen and merged to `main`.

---

## What Mercer Live is

Mercer Live is Open Ledger Sports' experimental live-football observation
system, covering NFL and NCAA FBS. The eventual product concept is a D.J. Mercer
Discord agent that watches live games, evaluates live markets with a
deterministic model, and may one day publish qualified live plays.

**That agent does not exist and is not being built yet.** What exists today is
the first link in the chain: a capture boundary that writes down what the
providers said and when we asked them. Nothing more.

The whole system is governed by a pre-registration frozen on 2026-09-14, before
any live observation existed: `docs/MERCER_LIVE_V0.1_PREREGISTRATION.md`.

## Current status

| | |
|---|---|
| Package | ML-1, the Live Observation Boundary |
| State | **FROZEN** as `mercer-live-ml1-v1.0` |
| Freeze commit | `984293f` |
| Merged to main | `795fb1d`, 2026-09-25 |
| Mode | Shadow. Research only |
| Odds API credits spent to date | **zero** |
| Live plays published | **none, and none are authorised** |

## What is implemented

- A capture process, `scripts/mercer_live/capture.py`, that reads ESPN's public
  scoreboard for live game state and can optionally read The Odds API for live
  market quotes.
- A canonical event identity that joins the two providers safely, and names
  every refusal rather than guessing.
- An append-only observation store with deterministic record ids, confined
  writes, and committed daily digests that fingerprint the raw stream.
- An explicit phase label on every market quote, so a live quote can never be
  mistaken for a pregame or closing one.
- Credit protections: a balance floor, a daily cap, and a booking throttle.
- Two read-only GitHub workflows: a hermetic code gate and a manual smoke
  capture.
- 229 hermetic self-test checks, plus live validation across five real runs.

## What is NOT implemented

None of this exists, and Hermes must not create any of it:

- No model, no probability, no forecast, no expected value.
- No candidate generation, no signal, no pick, no recommendation.
- No units, no stake, no bankroll, no exposure.
- No live ledger and no grading of anything live.
- No Discord posting of any kind for Mercer Live.
- No LLM involvement anywhere in the capture path.
- No production scheduler. Capture runs only when someone or something
  deliberately invokes it.
- No public surface. Nothing from Mercer Live appears on the website.

## What Hermes should do first

> **Operate and observe the frozen ML-1 live observation system while
> accumulating real football observations. Do not generate or publish live
> betting recommendations.**

Concretely, in order:

1. Read `MERCER_LIVE_HOUSE_RULES.md`. It is short and it is binding.
2. Read `MERCER_LIVE_SYSTEM.md` so the architecture and its boundaries are
   clear before touching anything.
3. Use `MERCER_LIVE_OPERATIONS.md` as the playbook during live football
   windows, after games, and weekly.
4. Produce the weekly system-health report described in that playbook.
5. When something looks wrong, use `MERCER_LIVE_ESCALATION.md` to decide
   whether it is a Claude ticket or normal operation, and write a precise
   ticket rather than a fix.

## What Hermes must not do

- Must not write, edit or refactor code, tests, schemas or workflows.
- Must not begin ML-2 or any later package.
- Must not produce a pick, a probability, an edge, a unit or a recommendation,
  in any channel, even informally in conversation.
- Must not enable Discord posting for Mercer Live.
- Must not spend Odds API credits without explicit authorisation from Daniel.
- Must not edit, delete, repair or re-timestamp any stored observation.
- Must not relax a threshold, guard or rule because of what results look like.
- Must not amend a frozen contract. That is a Claude change with Daniel's
  approval.

## Ownership boundaries

**Hermes owns** operational monitoring, system-health reporting, anomaly
detection, observation bookkeeping, roadmap and status maintenance, and
preparing engineering assignments for Claude.

**Claude owns** code changes, architecture changes, schemas, tests, provider
integrations, ML packages, and frozen-package amendments.

**Daniel owns** product direction, promotion decisions, subscriber access, the
Discord and customer experience, pricing, and authorisation of material scope
changes.

When those overlap, the narrower owner wins and the decision goes up, never
sideways.

## The files here

| file | what it is for |
|---|---|
| `README.md` | this orientation |
| `HERMES_BOOTSTRAP_PROMPT.md` | the prompt Daniel pastes to start Hermes |
| `MERCER_LIVE_SYSTEM.md` | architecture and the boundaries that matter |
| `MERCER_LIVE_OPERATIONS.md` | the operator playbook |
| `MERCER_LIVE_HOUSE_RULES.md` | the fifteen non-negotiables |
| `MERCER_LIVE_FREEZE.md` | what `mercer-live-ml1-v1.0` means |
| `MERCER_LIVE_ROADMAP.md` | ML-1 through ML-8, documented, not built |
| `MERCER_LIVE_SHADOW_PROTOCOL.md` | the future promotion gate |
| `MERCER_LIVE_DISCORD_PLAN.md` | future Discord shape, not implemented |
| `MERCER_LIVE_ESCALATION.md` | when to call Claude, and when not to |

Source of truth for the system itself lives in the repository, not here:
`docs/MERCER_LIVE_V0.1_PREREGISTRATION.md`,
`docs/MERCER_LIVE_ML1_ARCHITECTURE.md`,
`docs/MERCER_LIVE_AUDIT_2026-09-14.md`. Where this handoff and those documents
disagree, **those documents win** and the disagreement is a Claude ticket.
