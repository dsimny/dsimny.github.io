# Plan — protecting the picks and verifying the record

Status: DRAFT, not started. Written 2026-07-22, ~00:20 ET.
Decided with Daniel: do options 1 + 2 + 4 together (see reasoning below).

## The goal

Publish one free pick a day in full. Deliver the remaining plays to paying
Discord members. Keep the ledger and the methodology as open as they are now,
so the brand thesis survives the paywall.

## Why these three are one job, not three

Hiding the premium cards on the page protects nothing on its own. Two leaks
stay wide open:

1. `morning-board.yml` runs `git add data/`, which commits
   `data/board_<date>.json` — every pick, price and unit size — to a PUBLIC
   repo before first pitch.
2. The engine is public and deterministic (fixed per-date seed). Anyone can
   re-run it against the same free MLB data and reproduce the board exactly.

So the display gate shipped on 2026-07-22 is cosmetic. Charging for picks that
anyone can read would be the exact behaviour this site exists to call out.
Option 1 (commit–reveal) is the mechanism that makes option 4 real.

## Phase 1 — stop the leak — BUILT AND TESTED 2026-07-22, NOT YET PUSHED

Morning run:
- fetch → engine → build site (free pick only) → post to Discord
- commit `index.html`, the ledger, and an ENCRYPTED board blob
  (`data/board_<date>.enc`) plus its SHA-256. Committing pre-game is what
  proves the picks were made before first pitch; encryption is what stops
  anyone reading them.
- do NOT commit the plaintext board or the raw snapshot pre-game.

Grading run:
- decrypt with a repo secret, grade as today, then publish the plaintext
  board and snapshot alongside the ledger entries.

Net effect: everything becomes public eventually, on the same append-only
terms as now. Only the pre-game reveal is withheld. Anyone can verify after
the fact that the revealed board matches the morning's hash.

Implemented in `scripts/crypto_box.py` with Fernet. Tested against nine
scenarios including a deliberately tampered board, a wrong key, a missing key
in CI, and a board with no commitment at all (backwards compatibility).

One real bug found by that testing: the first version graded and THEN checked
the fingerprint, so a tampered board's results reached the append-only ledger
before the mismatch surfaced. Verification now runs before anything is written.
Worth remembering as the general lesson: on an append-only store, validate
before you write, never after.

Still to watch:
- BOARD_ENCRYPTION_KEY has no recovery path. Daniel's backup is the only copy.
- Pushing this requires the secret to be set FIRST, or the CI guard correctly
  refuses to publish and there is no board that day.

## Phase 2 — deliver the premium plays — DONE 2026-07-22

- New mode in `post_discord.py`: `board` — posts every published play with
  full card detail (pick, price, edge, units, breaker log).
- Second webhook secret `DISCORD_WEBHOOK_URL_MEMBERS` for the paid channel.
  The existing `DISCORD_WEBHOOK_URL` keeps carrying the free pick to the
  public channel.
- House rule 2 stands: the free pick is still the cleanest lower-board play,
  never the headliner, and the selection logic stays identical in
  `build_site.py` and `post_discord.py`.
- Both posts must skip gracefully when their webhook is unset, exactly as the
  current code does — a missing webhook must never fail a run.

Built and tested 2026-07-22. Server, both channels, both webhooks and the
invite are all live. Remaining prerequisite: Whop (or equivalent) for payments
and members-channel access, when the record justifies charging.

Phase 1 shipped later the same day, so from the 2026-07-23 board onward the
premium picks really are exclusive rather than merely presented differently.

## Whop, as of 2026-07-22 (pricing section corrected 2026-09-11)

### CORRECTION, 2026-09-11 — what this file used to say was wrong

This section previously read: *"$49/month, NO trial. REPRICED 2026-09-07 from
$30; no member was grandfathered because none exists yet - the checkout URL has
never been live."*

**All three of those claims were false**, and they were caught only because an
affiliate link a friend shared rendered a $30 checkout. What was actually true
on 2026-09-11 before any fix:

- The repricing to $49 **had never been applied in Whop**. The product had
  exactly ONE pricing option and it was $30/month. The $49 existed only in this
  file, in `build_site.py`'s `PREMIUM_PRICE`, and in `docs/`.
- **Five members were already subscribed at $30/month.** The Whop store page
  read "6 joined" with named accounts, and the product listed 5 against that
  plan. The checkout URL had evidently been live and used.
- `plan_KIbsXvPUXlf3X` — recorded here as the checkout link — was the $30 plan.

The lesson worth keeping: a price written in a plan document is not a price. It
had drifted for four days with the real product one click away from selling at
the old rate.

### CONFIRMED — directly observed 2026-09-11

- **$49/month is the price for NEW members.** Verified on the public store page,
  the product page, and the product URL while signed out.
- **Canonical checkout / affiliate destination is the PRODUCT URL:**
  `https://whop.com/open-ledger-sports/open-ledger-sports-premium/`
  Use this, not a `plan_...` URL. A plan-pinned link is frozen to one pricing
  option forever, which is exactly how the old $30 link kept selling after the
  price was supposed to have changed. A product URL follows the current default
  plan, so the next repricing does not strand every shared copy of the link.
  Whop's affiliate parameter works on product URLs.
- **Five existing members are on the $30 plan.** They were NOT migrated and the
  plan was NOT deleted. Grandfathering them at the price they bought at is the
  deliberate choice; moving them silently is the thing not to do.
- **The old $30 plan is OUT OF STOCK, not deleted.** Retired by setting its
  pricing-option **stock to 0** (Whop has no "archive" for pricing options).
  `plan_KIbsXvPUXlf3X` now renders "Out of stock — The seller may restock it."
  with **no payment form at all** — no card fields, no submit. Verified by
  loading the URL signed out, before and after.
- **Setting stock to 0 exhausted the plan immediately** rather than permitting
  zero *further* sales on top of the five already made — i.e. in this one
  observed test, stock counted total uses, not remaining. That is what the
  before/after page load showed and nothing more; it has not been verified
  against Whop's documentation or a second case, so do not generalise it.
- **"Hidden" is not "closed".** Un-ticking *Show on store page* removes the
  option from the picker and the store listing but, by design, leaves the direct
  link fully purchasable — Whop documents this as the mechanism for private
  offers. Hiding the $30 option did not stop it selling; only stock 0 did.
- **All five $30 memberships remained ACTIVE after the stock change.** Confirmed
  by Daniel in Whop -> Customers on 2026-09-11, after stock was set to 0. This
  is his check, not one this repo can make — recorded here because it is the
  item with live money attached (~$150/month) and it is now closed. Taken
  together with the point above: in this one case, exhausting a plan's stock
  blocked new checkouts without disturbing existing subscriptions. One case,
  not a documented guarantee — re-check if the method is ever reused.

### PENDING — not verified, do not treat as done

- **The fake anchor price.** Both the product page and checkout display
  `$61.25` struck through beside `$49` with a "Save 20%" badge. No such discount
  exists: Whop appears to synthesise the anchor as price / 0.8 (the $30 plan
  showed `$37.5` the same way, and 37.50 x 0.8 = 30.00; 61.25 x 0.8 = 49.00).
  Unresolved — the toggle, if there is one, has not been found. This matters
  more here than for a typical seller: a struck-through price nobody was ever
  charged is a manufactured claim, on a brand whose entire pitch is publishing
  the real record including the losses. Decide deliberately rather than leave it.
- **`WHOP_CHECKOUT_URL` is still UNSET, pending Daniel's decision.** That
  variable is the only reason the site is not already advertising the paid tier.
  Setting it makes the upgrade button appear on the next build. Do not set it
  while the anchor-price question is open, and set it to the PRODUCT URL above.

### The rest of the Whop setup (unchanged, from 2026-07-22)

- Product "Open Ledger Sports Member", NO trial. A three day trial hands over
  three full days of a product whose whole value is daily, and the public ledger
  already serves as the free evidence.
- Discord app connected: grants the "Members" role, removes it on cancel.
- "Assign this role after past due bill" left EMPTY on purpose. It was briefly
  set to "Members", which would have granted premium access to people whose
  payment had just failed. Nothing would have errored.
- Event log goes to the private #ols-log channel, so grants and revokes are
  visible rather than silent.
- Identity verified and a bank account connected for payouts (2026-07-22).
- Grant path VERIFIED 2026-07-22 with an outside tester (not the server owner):
  checkout -> membership -> connect Discord -> Whop grants the Members role ->
  #members-only becomes visible. The whole chain works.

Gotchas learned doing it, worth telling every new member up front:
- Checkout does NOT grant the role on its own. The member must connect their
  Discord to Whop afterwards (whop.com -> their membership -> Connect Discord),
  or #ols-log shows "Discord ID: No Discord" and they only see #free-pick.
- Use the "forever" promo codes, not the "100% off first payment" one. The
  latter puts the member on a 30-day trial that renews at full price. One tester
  landed on it; fixed by extending the trial, but the forever codes avoid it.
- A stray premium post reached the public #general once during webhook
  testing. Not from the pipeline (board always posts to #members-only, proven
  by channel_id). Deleted. Keep #general locked or gone.

To go live on the site, set the repo variable WHOP_CHECKOUT_URL to the
**product URL** above — not a `plan_...` link. The upgrade button appears on the
next build, and not before. See PENDING: this is still Daniel's call, and
`build_site.py` already advertises $49, so the site and Whop only agree once
both the variable is set and the price in Whop stays $49.

## Phase 3 — site changes

- Board tab keeps: slate stats, the free pick in full, all leans, all
  scratches, and every reason. These are the proof of no cherry-picking and
  they stay public.
- Premium plays: counts and total exposure only, until graded. After grading
  they publish in full with their breaker logs.
- Methodology, The Rules, and the legal footer: unchanged. Rules 3, 4 and 5
  are not in scope for trimming.

## Phase 4 — third-party verification (option 2) — NEEDS A DECISION FIRST

Researched 2026-07-22. Pikkit verifies by syncing bets directly from
sportsbook accounts; bettors cannot hide losers because the record is pulled
automatically rather than self-reported. That is what makes it credible — and
it means it verifies REAL WAGERS PLACED, not published model picks.

Implication: a Pikkit-verified record requires someone to actually place these
bets with real money at linked sportsbooks, every day, at the published sizes.
That is a financial decision for Daniel alone. It is NOT a prerequisite for
launching phases 1-3, and nothing in this plan assumes it.

If he does not want to place real bets, the substitutes are:
- The commit–reveal hash from phase 1, which already proves the picks were
  published before first pitch and were not edited afterwards. For a model
  publication this is arguably the more relevant proof.
- Researching whether a service exists that verifies published picks rather
  than placed wagers. Not yet investigated.

Roadmap note: Daniel's own ordering puts paid access after roughly a month of
record. The ledger is 0-0 as of this writing. Build the machinery now, charge
when the record has earned it.

## Definition of done

- No plaintext pick is publicly readable before first pitch.
- Every pick still becomes public after grading, win or lose.
- The morning hash lets anyone verify the revealed board was not changed.
- A missing Discord webhook still never fails a run.
- Methodology, leans, scratches, ledger and legal footer unchanged.
