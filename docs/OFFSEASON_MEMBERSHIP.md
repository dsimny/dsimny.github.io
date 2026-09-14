# Off-season membership — content structure and open decisions

Written 2026-09-14. Planning document: nothing here is live, and nothing here may
be promised to members until it is. The MLB regular season ends **2026-09-27**;
from the next day the members board has no baseball.

## 1. The problem, stated plainly

After 2026-09-27 a $49/month member receives, today:

- D.J. Mercer Spotlight football picks — the weekly premium featured selection,
  human-researched, **no guaranteed volume** (Week 1 was one pick).
- After commissioning, and only then: NFL and NCAA developmental plays,
  human-researched, flat 0.25u, each approved, capped at 1u per day across both.
- Nothing else. The automated football pipeline is paused and no other sport has a
  validated strategy. Mercer Live is capture-only.

A membership that justifies itself only by pick volume cannot be honest about
this. The structure below is designed to be worth something **without** claiming
any unproven strategy wins.

## 2. Proposed weekly structure (off-season, and football weeks generally)

| item | cadence | what it is | honesty constraint |
|---|---|---|---|
| **D.J. Mercer football picks** | when a pick qualifies | Spotlight card: pick, case for, case against, walk-away number; later, developmental cohort plays | no volume promise; developmental plays labelled as such |
| **The Pass List** | weekly | the games Mercer researched and declined, with the reason | already part of the Spotlight card format |
| **Model Lab update** | weekly | what was built, tested and learned: cohort status, research runs, results including nulls and failures | no result is described beyond what it shows; failures publish like successes |
| **Market notes** | weekly | descriptive line movement and market structure for the week's notable games | describes, never explains; no "sharp money" claims (same rule as `odds/`) |
| **Betting education** | weekly | a piece from, or added to, the evergreen library (CLV, de-vig, variance, bankroll, why most picks lose) | no claims about our own edge |
| **Ledger review** | monthly | every record, every cohort, separately: wins, losses, CLV, calibration | never combined into one number |
| **Early access to new strategies** | when one exists | a newly registered strategy's plays reach members first, labelled developmental | "early access" means timing and visibility, not a promise that one will exist |

What should **not** be offered: a guaranteed number of plays, "model picks" from
an unregistered strategy, parlays or props with no product surface, or any
performance claim the ledger does not support.

## 3. What has to be built for this to be real

1. A members-channel post type for the Model Lab update and market notes
   (Discord, members webhook, idempotent, no actionable picks).
2. A football market-notes page or post generated from captures, descriptive only.
3. New evergreen education pieces (the blog library has 12 and recycles after
   that — `CLAUDE.md`, "KNOWN, NOT FIXED").
4. Mercer developmental cohort activation (checklist in
   `MERCER_DEVELOPMENTAL_RELEASE_CONTROLS.md` section 7) if football selections
   are to go beyond the Spotlight.
5. A next sport only after its own registered strategy and evaluation — no sport
   is scheduled, and none should be promised.

## 4. Decisions only Daniel can make (billing, signup, pricing, Whop)

These need a manual decision and, where noted, manual action in Whop. Nothing in
the repository changes billing.

| # | decision | options | note |
|---|---|---|---|
| B1 | What happens to renewals after 2026-09-27 | keep $49 with the structure above / pause billing until a sport resumes / lower off-season price / offer cancel-or-stay notice | the seasonal note (MEMBER_COMMS asset 3) should go out ~2026-09-20 and must match this choice |
| B2 | New signups during the off-season | keep open / close the checkout (unset `WHOP_CHECKOUT_URL`) / waitlist | unsetting the variable hides both site buttons on the next rebuild |
| B3 | Whop product description and onboarding | re-paste MEMBER_COMMS asset 1 as updated 2026-09-14 | manual in Whop |
| B4 | Discord `#start-here` pin | re-paste asset 2 | manual in Discord |
| B5 | Legacy members on $30/month | grandfather / migrate with notice | `PLAN-paid-tier.md` records 5 legacy members |
| B6 | Whop's struck-through "$61.25, Save 20%" anchor | find and disable the setting, or decide deliberately to accept it | `PLAN-paid-tier.md` records that no such price ever existed (Whop appears to show price ÷ 0.8); a reference price nobody paid is a manufactured claim |
| B7 | Whop auto-refunds | confirm disabled | `REFUND_CANCELLATION_POLICY.md` still marks this UNVERIFIED |
| B8 | Send the correction clarification | approve / edit / do not send MEMBER_COMMS asset 5 | members were told "football recommendations" are paused while Mercer continued |
