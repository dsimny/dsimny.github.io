# Member communications — launch assets

Written by Daniel 2026-09-06 and committed here rather than left in a chat log,
because each of these is needed at a moment when there is no time to draft it:
the first subscriber arrives, or the season turns. Improvising customer-facing
copy under time pressure is how a transparency brand ends up saying something it
cannot support.

Three assets, and the accuracy review that follows them. The copy below is
Daniel's, reproduced verbatim; the review is separate on purpose so nobody has
to guess which words were signed off.

Related: `PLAN-paid-tier.md` (the Whop setup and its gotchas),
`docs/FOOTBALL_LAUNCH.md` §J (premium copy), `CLAUDE.md` House Rules 4, 5, 7, 8.

WHY THE DISCORD-LINKING STEP IS THE FIRST THING SAID. Access is granted by the
Whop app assigning a Discord role. Checkout alone does NOT do it — the member
must connect their Discord to Whop afterwards, or `#ols-log` records "Discord
ID: No Discord" and they see only `#free-pick`. That is the single most likely
support ticket, it was hit by a real tester in July, and it is the reason this
instruction leads rather than sits in a FAQ.

---

## 1. Whop post-checkout onboarding

> Welcome to Open Ledger Sports.
>
> Your membership is active, but there is one important step required before you
> can see the Members board in Discord.
>
> **Connect your Discord account to Whop.**
>
> Purchasing the membership does not automatically unlock the Members channel
> until your Discord account is connected.
>
> Once connected, the Open Ledger Sports Members role should be added
> automatically and you'll gain access to #members-only.
>
> **What you receive**
>
> Your membership is not just access to a handful of premium picks.
>
> During football season, members receive the full reasoned slate: every covered
> game Open Ledger evaluates, with the market numbers, model context, analysis,
> and written reasoning behind it.
>
> That means a typical football week can include dozens of evaluated games —
> including games where the conclusion is PASS.
>
> From that complete slate, Open Ledger may designate a smaller number of
> committed premium plays that meet the required standards for an official
> position. Those plays become part of the permanent, gradeable record.
>
> The distinction matters:
>
> The slate is the analytical product. The committed plays are the
> accountability mechanism.
>
> We do not manufacture official plays simply to create more action or make a
> subscription appear busier. If nothing meets the standard, nothing gets
> promoted into a play.
>
> **What we are — and are not — claiming**
>
> Open Ledger Sports does not claim that its football plays are proven to beat
> the market.
>
> Our published research has not established that this market can currently be
> out-forecast at the point when we are able to act, and we will not pretend
> otherwise.
>
> That is why the product is built around transparency rather than promises:
>
> * See the games we evaluated.
> * See the reasoning.
> * See the numbers available when the decision was made.
> * See what we passed on.
> * See which positions were actually committed.
> * Then see the results — including the losses.
>
> The methodology, research, limitations, and record remain public so you can
> judge the process for yourself rather than relying on marketing claims.
>
> Open Ledger is built differently from a traditional picks service:
>
> * Every published play is committed before the game.
> * Wins and losses both go on the public record.
> * Held plays are revealed after grading.
> * No play is manufactured just because there are games on the schedule.
> * Passing is a position too.
> * The methodology, limitations, and record remain public.
>
> We are not asking you to trust screenshots or selective results.
> Check the ledger. Check the methodology. Judge the process for yourself.
>
> **Need access help?**
>
> If you purchased a membership but only see #free-pick, first confirm that your
> Discord account is connected inside Whop.
>
> After connecting, refresh Discord and look for the Members role and
> #members-only channel.
>
> `SUPPORT CONTACT — SET BEFORE LAUNCH`
>
> Open Ledger Sports provides sports analytics and information only. It is not a
> sportsbook and does not accept wagers. No outcome is guaranteed. 21+. If
> gambling is causing problems for you or someone you know, call 1-800-GAMBLER.

Use in two places: the Whop post-purchase experience, and a permanent pinned
`#start-here` message in Discord.

## 2. Discord pinned version

> **New member? Start here.**
>
> If you purchased Open Ledger Sports but cannot see #members-only, your
> membership may be active without your Discord account being connected yet.
>
> Go to Whop → connect your Discord account.
>
> After the connection is complete, the Members role should be added
> automatically and #members-only will appear.
>
> Your membership gives you access to the full member slate during the sports
> and markets Open Ledger is actively covering — including the games evaluated,
> the reasoning behind them, PASS decisions, and any premium plays that qualify
> for an official position.
>
> Open Ledger does not claim those plays are proven to beat the market. The
> research, methodology, limitations, and results are published so you can
> evaluate that claim for yourself.
>
> Remember what you are buying: the process and the record, not a promise of
> winners.
>
> Every committed play is eventually revealed and graded. Wins stay. Losses
> stay. Passes stay passes.
>
> If you still cannot access the channel after connecting Discord, contact us
> at `SUPPORT CONTACT — SET BEFORE LAUNCH`.
>
> 21+ • Analytics only • Not a sportsbook • No guarantees • 1-800-GAMBLER

## 3. Seasonal transition (send ~5–7 days before 2026-09-27)

> **A quick note about what changes after September 27**
>
> Open Ledger Sports follows the sports that are currently in season, so the
> rhythm of the member board changes as the calendar changes.
>
> As the MLB regular season ends, our normal daily baseball schedule will wind
> down and the primary focus will shift toward football.
>
> That means you may notice an important difference:
>
> Baseball can create opportunities almost every day. Football is naturally more
> concentrated around the weekly schedule.
>
> We do not intend to manufacture additional plays simply to make the membership
> feel busier.
>
> If the board produces qualifying plays, they will be published.
> If the evidence says pass, we pass.
> The standard does not change because the schedule does.
>
> This is part of the Open Ledger promise: the product follows the process
> rather than forcing the process to satisfy a content calendar.
>
> As always, you can judge that process through the public record, including the
> wins, losses, passes, methodology, and limitations.
>
> Thank you for being part of Open Ledger Sports as we move into football
> season.
>
> Every pick on the record. Every rule in public.
>
> 21+ • Sports analytics only • Open Ledger Sports is not a sportsbook and does
> not accept wagers • No outcome is guaranteed • 1-800-GAMBLER

---

## Accuracy review, checked against the code 2026-09-06

Every factual claim above was verified against what the pipeline actually does,
because member-facing copy that overstates the product is the one thing this
brand cannot recover from.

**Verified true as written:**

| claim | where it is enforced |
|---|---|
| Every published play is committed before the game | `board.py` writes an encrypted board and publishes its SHA-256 before kickoff |
| Wins and losses both go on the public record | `football_ledger.json`, append-only; the first two premium plays are both losses and both published |
| Held plays are revealed after grading | `football-grade.yml` flips the commitment off the ledger, not off a date |
| No play is manufactured just because there are games | House Rule 6; `board.py` publishes the no-play message when nothing qualifies |
| The methodology, limitations and record remain public | `/football/`, the two published research results, and the NO MARKET list |
| Legal footer, 21+, 1-800-GAMBLER | present in all three pieces (House Rule 5) |

**THREE GAPS WERE RAISED IN REVIEW AND ALL THREE ARE NOW CLOSED** (Daniel,
2026-09-06). Recorded rather than quietly edited away, because the first one was
a commercial error rather than a wording one and is worth not repeating.

**1. It described the MLB product, and undersold football. FIXED.** The original
"plays that clear the Open Ledger process but are held back from the public Free
Pick" is accurate for baseball and wrong for football. `discord.py slate` sends
`#members-only` **the entire reasoned slate — every covered game with its
numbers and its write-up, dozens in a college week — plus the committed premium
play.**

The replacement states the distinction directly: *the slate is the analytical
product, the committed plays are the accountability mechanism.* That is the
correct way round and it is what makes the price defensible. The old wording
sold four picks a month, which is both inaccurate and the framing this brand
loses on — against anyone willing to promise winners, honesty is a handicap;
against research, it is the moat (`FOOTBALL_LAUNCH.md` §J).

**2. It did not carry football's no-expectation claim. FIXED.** A dedicated
"What we are — and are not — claiming" section now says the published research
has not established that this market can be out-forecast at the point we can
act. `FOOTBALL_PIPELINE.md` §1 requires that said plainly, and the public page
already said it — member-facing copy saying LESS than the public page is the
wrong way round. The paying reader is now told at least as much as the
non-paying one.

**3. "Contact us for help" named no route. FIXED with a MARKER, not an
invention.** Both places now carry a literal `SUPPORT CONTACT — SET BEFORE
LAUNCH` placeholder. Guessing an address or channel would have shipped a support
promise pointing nowhere, which is worse than an obvious blank. Grep for that
string before publishing either asset:

    git grep "SUPPORT CONTACT — SET BEFORE LAUNCH"

Decide which route is actually owned operationally — a dedicated Discord support
channel, Whop's own support button, or an Open Ledger email — and replace both
occurrences with the exact one.

## Still open

**Refund and cancellation policy — the next thing to write.** Nothing in this
repo covers mid-cycle cancellation, refund requests, accidental renewals, or
whether access continues through a period already paid for. Whop has defaults
that may or may not be what is wanted. This is the remaining customer-facing
rule most likely to create ambiguity once money changes hands, and it should be
made as explicit as the betting methodology. A transparency brand handles its
first refund request badly exactly once.
