# OLP-M1 Package #1 — Deviations & Implementation Decisions

No architectural rule in the frozen contract was changed, relaxed, or worked
around. Nothing in section 41 (Stop Conditions) was triggered.

What follows is every place the implementation adds something the contract did
not literally spell out, or resolves an ambiguity in it. Each is additive and
strengthens an existing rule rather than loosening one. Items 1 and 2 are the
only ones that could affect how you deploy; the rest are internal.

---

## 1. `pgcrypto` is attempted, and `gen_random_uuid()` is asserted either way

**Contract:** section 4 mandates `CREATE EXTENSION IF NOT EXISTS pgcrypto` and
`gen_random_uuid()` defaults.

**Implemented:** migration 001 attempts the extension. If the contrib module is
absent it falls back to the core `gen_random_uuid()` that PostgreSQL 13+ ships
in `pg_catalog`, and then *hard-asserts* that the function resolves — the
migration fails loudly if it does not.

**Why:** on Supabase, `pgcrypto` installs normally and this is a no-op. The
bundled PostgreSQL used by the test harness is a trimmed build without contrib
modules. Rather than edit the migration for testing (which would mean the
tested artifact is not the shipped artifact), the migration is portable and the
UUID contract is verified rather than assumed. No `uuid-ossp` is involved.

**Deployment impact:** none on Supabase.

---

## 2. `public.system_settings` — one row, four policy numbers

**Contract:** section 21 requires a *"configured TTL"*; section 25 sets
`MIN_VIABLE_WAGER = 100 LC`; section 22 fixes the max ticket at
`0.10 × settled`; section 7 fixes starting capital at 10,000.

**Implemented:** a single-row table (`CHECK (id)` pins it to one row) holding
`snapshot_ttl_seconds` (120), `max_ticket_fraction` (0.1000),
`min_viable_wager` (100.00), `default_starting_capital` (10000.00).

**Why:** the contract calls these values *configured*, and they are read by
three different RPCs plus a view. Duplicating literals across five places is how
they drift. All defaults are exactly the contract's values, so behaviour is
identical to hard-coding them.

**Note:** `max_ticket_fraction` is deliberately server-side and not client
supplied. Changing it is an `UPDATE`, not a code change — but it *is* a risk
policy change and should be treated as one.

---

## 3. Immutability is enforced by trigger, not only by revoked grants

**Contract:** section 15 — ticket results are *"append-once and immutable"*;
section 13 — accepted economics *"immutable after insert"*; section 11 —
snapshots are never overwritten; section 27 — revoke client mutations.

**Implemented:** the REVOKEs in section 27 are all present. On top of them,
`BEFORE UPDATE OR DELETE` triggers raise on:

| Table | Rule |
|---|---|
| `wallet_transactions` | no UPDATE, no DELETE, ever |
| `ticket_results` | no UPDATE, no DELETE, ever |
| `ticket_result_adjustments` | no UPDATE, no DELETE, ever |
| `event_schedule_history` | no UPDATE, no DELETE, ever |
| `tickets` | only `status` / `closed_at` / `settled_at` may move |
| `risk_reservations` | only `status` / `released_at`, and release is one-way |
| `market_snapshots` | only `is_closing_snapshot`, and only false → true |
| `events` | `original_scheduled_start` frozen |

**Why:** grants stop a *role*. They cannot stop the table **owner**, and it is
the owner that a future migration or a mistaken admin session runs as. Test
`SEC-X01` proves both layers: `service_role` is refused by its grants (see
deviation 13), and `postgres` — which no grant can restrain — is still refused
by these triggers when it tries to rewrite a settled result or delete a wallet
transaction.

---

## 4. `adjustment_seq` on `ticket_result_adjustments`

**Contract:** section 16 defines the table without a sequence; section 24 says
to load *"ordered adjustments"*; section 12 forbids relying on `captured_at`
alone for snapshots.

**Implemented:** added `adjustment_seq BIGINT GENERATED ALWAYS AS IDENTITY
UNIQUE`, and both the correction RPC and the effective-result view order by it.

**Why:** exactly the reasoning behind `ingest_seq` in section 11. Two
corrections applied inside the same transaction share a `created_at`, and the
effective result depends on a *total* order. Ordering by timestamp alone would
make the effective result non-deterministic under a tie. Test `M1-T27` asserts
the derived chain.

`history_seq` was added to `event_schedule_history` for the same reason.

---

## 5. A no-op correction is refused

**Contract:** section 24 describes the correction flow but does not say what
happens when the new result equals the current effective result.

**Implemented:** raises `CORRECTION_NO_CHANGE`.

**Why:** section 2 forbids *silently* resolving settlement conflicts. Appending
a zero-delta adjustment row would pollute the audit trail with entries that
assert nothing. Raising is explicit, and idempotent retries are unaffected —
they are matched on `correction_idempotency_key` before this check is reached
(test `M1-T26`).

**If you disagree:** this is the one deviation with a defensible opposite
answer. Removing the check is a two-line change in migration 016.

---

## 6. A favourable correction can restore `DEFICIT → ACTIVE`

**Contract:** section 24 says to *"mark DEFICIT if negative"*. It does not say
what happens when a later correction makes the chapter solvent again.

**Implemented:** if available capital is back at or above zero and the chapter
is `DEFICIT`, it returns to `ACTIVE`.

**Why:** without this a chapter that was pushed into deficit by a grading error
would stay stranded after the error was itself corrected, with no path out
except bankruptcy — which `declare_bankruptcy_rpc` would then refuse, because
the chapter is solvent again. That is a dead end, not a policy.

`ACTIVE → BUST` and `DEFICIT → BUST` remain solely the bankruptcy RPC's job.

---

## 7. `VOID` releases escrow as `VOIDED`, not `RELEASED`

**Contract:** section 23 says *"release reservation"*; section 14's enum offers
`ACTIVE`, `RELEASED`, `VOIDED`.

**Implemented:** `VOID` settlements set the reservation to `VOIDED`; `WIN` /
`LOSS` / `PUSH` set `RELEASED`.

**Why:** both drop out of the `ACTIVE` escrow sum identically, so the
accounting is unchanged. This uses the `VOIDED` enum value for the case it was
obviously created for, and keeps postponement voids distinguishable from
ordinary settlements in the audit trail (test `M1-T11`).

---

## 8. Placement rejects sub-cent risk and excess precision

**Contract:** section 22 lists the rejection cases (null, ≤ 0, > available,
> max ticket).

**Implemented:** all four, plus `p_risk <> round(p_risk, 2)` →
`INVALID_RISK`, and a computed profit below `0.01` → `INVALID_RISK`.

**Why:** `risk NUMERIC(12,2)` would silently round a third decimal, meaning the
accepted ticket would not match what the user submitted. And
`potential_profit` carries `CHECK (potential_profit > 0)`; a risk small enough
to round profit to `0.00` would fail as an opaque constraint violation instead
of a clear rejection.

---

## 9. Ticket `status` is not changed by a correction

**Contract:** section 24 lists the correction steps; none of them is a ticket
status transition.

**Implemented:** as written — a ticket corrected to `VOID` keeps the `status`
its original settlement gave it. `public.ticket_effective_results` is the
authoritative source for the current effective outcome.

**Why:** `status` records what happened to the ticket in its own lifecycle;
the effective result is derived state. Mutating `status` on correction would
put two sources of truth in disagreement.

**Flagging this one:** if the product wants a corrected-to-void ticket to
*display* as voided, read `effective_result` from the view rather than
`tickets.status`. This is a UI contract worth settling in Package #2.

---

## 10. Error taxonomy

Business rejections raise `P0001` with a stable `CODE: message` prefix
(`MARKET_MOVED`, `INSUFFICIENT_CAPITAL`, `SNAPSHOT_STALE`,
`SETTLEMENT_CONFLICT`, `CHAPTER_ALREADY_OPEN`, `EVENT_STARTED`, …). Auth
failures use `insufficient_privilege` so they are distinguishable from business
logic. The contract did not specify a taxonomy; the client will need one.

`CHAPTER_NOT_AVAILABLE` is returned both when a chapter does not exist and when
it belongs to another user, so placement cannot be used to probe for other
users' chapter IDs (tests `M1-T35`, `SEC-T01`).

---

## 11. Test-harness files are quarantined outside `migrations/`

`db/testkit/000_local_auth_shim.sql` reproduces what Supabase provides natively
(`auth.users`, `auth.uid()` and the three API roles) so migrations 001–019 can
be executed **unmodified** on a bare PostgreSQL.

They are deliberately *not* numbered into `migrations/` and must never be
applied to Supabase. The shim's `auth.uid()` is a faithful copy of Supabase's,
reading the same `request.jwt.claim.sub` / `request.jwt.claims` GUCs.

Critically, the testkit grants **no table privileges to any role** — migration
012 is the sole authority on every privilege decision, so the harness cannot
mask an authorization regression. An earlier `001_local_service_grants.sql`,
which mirrored Supabase's default `GRANT ALL ... TO service_role`, was deleted
once deviation 13 made those grants explicit: it would have masked exactly the
difference that real-Supabase testing exposed.

---

## 12. Test count: the contract's own numbers disagree

Section 33's table enumerates **29** tests (IDs T01–T35 with T14, T18, T22,
T24, T25, T28 absent). Section 40's example report shows `35/35`.

All 29 enumerated tests are implemented and pass. The `35/35` figure appears to
be illustrative; no test was invented to reach it and none was dropped to avoid
it. Six additional tests were added beyond the enumerated set — `M1-T07c`,
`M1-T09b`, `M1-T17c`, `M1-T34b`, `SEC-X01` and `SEC-X02` — bringing the suite to
40 cases. Totals are reported honestly as **29/29 enumerated acceptance + 5/5
security + 6 additional**, not as `35/35`.


---

## 13. `service_role` table privileges are stated, not inherited

**Contract:** section 27 revokes direct mutations from `anon` and
`authenticated`. Section 28 lists which functions `service_role` may execute.
Neither says what `service_role` should hold at the *table* level.

**Originally implemented:** nothing — the assumption was that Supabase's
platform default (`ALTER DEFAULT PRIVILEGES ... GRANT ALL ... TO service_role`)
would apply.

**That assumption was wrong**, and running the suite against a real Supabase
stack proved it: every test failed with `permission denied for table events`
and similar. Default privileges did not cover these tables. Worse, the
privilege set differed between environments, which means a passing local run
guaranteed nothing about production.

**Now implemented:** migration 012 revokes `service_role` to a known state and
then grants deliberately:

| Tables | `service_role` holds |
|---|---|
| `users`, `ledger_chapters`, `wallet_transactions`, `tickets`, `risk_reservations`, `ticket_results`, `ticket_result_adjustments` | `SELECT` only |
| `events`, `market_snapshots` | `SELECT, INSERT, UPDATE` (ingestion, Package #2) |
| `event_schedule_history` | `SELECT, INSERT` |
| `system_settings` | `SELECT, UPDATE` |

The trusted backend ingests market data and reads the ledger; it never writes
financial truth directly. Settlement and corrections run through the
`SECURITY DEFINER` RPCs, which are owned by `postgres` and work regardless of
these grants — so least privilege here costs nothing functionally.

**Consequence for `SEC-X01`:** `service_role` attempting `UPDATE
ticket_results` now fails at the *grant* layer (`permission denied`) rather
than the trigger layer. The test was extended to assert both layers — the
grant stops `service_role`, and the append-only trigger still stops the table
**owner**, which no grant can restrain.

---

## 14. `olp_log_schedule_change()` is `SECURITY DEFINER`

Section 10 requires that every change to `current_scheduled_start` produces a
history row. With `service_role` now holding least privilege, a caller could
lack `INSERT` on `event_schedule_history` and the audit write would fail.

An audit trail that can be defeated by revoking a grant is not an audit trail,
so the trigger function runs as its owner. It remains impossible to *modify* a
history row afterwards — the append-only trigger still applies.
