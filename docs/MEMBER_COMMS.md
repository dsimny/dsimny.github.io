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
> As a member, you'll get access to the plays that clear the Open Ledger process
> but are held back from the public Free Pick.
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
> Your membership gives you access to the member board during the sports and
> markets Open Ledger is actively publishing.
>
> Remember what you are buying: the process and the record, not a promise of
> winners.
>
> Every committed play is eventually revealed and graded. Wins stay. Losses
> stay. Passes stay passes.
>
> If you still cannot access the channel after connecting Discord, contact us
> for help.
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

**TWO GAPS WORTH CLOSING BEFORE THE FIRST SUBSCRIBER, neither of which makes
the copy false — both make it describe the WRONG PRODUCT or say less than the
rules require.**

**1. It describes the MLB product, and undersells football.** "the plays that
clear the Open Ledger process but are held back from the public Free Pick" is an
accurate description of the MLB membership. It is not what a football member
actually receives. `discord.py slate` sends `#members-only` **the entire reasoned
slate — every covered game with its numbers and its write-up, ~57 in a college
week — plus the one premium play.** The held play is the accountability
mechanism; the slate is the product, and it is the whole reason the price is
defensible (see `FOOTBALL_LAUNCH.md` §J). As written, the copy sells four picks
a month.

**2. It does not carry football's no-expectation claim.** "the process and the
record, not a promise of winners" is close and is not a violation. But
`FOOTBALL_PIPELINE.md` §1 requires copy to say plainly that no expectation claim
is made, and the site already does: *"We make no claim that these plays win. Two
pre-registered studies, both published in full, found this market cannot be
out-forecast at the moment we can act."* Member-facing copy saying less than the
public page is the wrong way round — the person who has paid should be told at
least as much as the person who has not.

**Minor:** §2 ends "contact us for help" without saying how. Name the channel or
the email, or it becomes a support ticket about how to file a support ticket.

## Still open

**Refund and cancellation policy.** Nothing in this repo covers what happens
when someone cancels mid-month or asks for their money back. Whop has defaults
that may or may not be what is wanted. This is the remaining customer-facing
rule most likely to create ambiguity once money changes hands, and a
transparency brand handles its first refund request badly exactly once.
