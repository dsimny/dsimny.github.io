# Refund and cancellation policy

Decided by Daniel 2026-09-06. Written to be as explicit as the betting
methodology, and for the same reason: a rule that only exists in someone's head
gets applied differently under pressure than it would have been in the calm.

**Whop's current behaviour was checked against Whop's own documentation by
Daniel on 2026-09-06** — the cancellation default, the seller's responsibility
for product/service refunds, the configurable auto-refund feature, and the
Resolution Center's seven-day window. Those are his findings; this document
records the Open Ledger decisions that follow from them. If Whop's mechanics
change, section 6 governs and this file needs re-checking rather than trusting.

Customer-facing short version: `docs/MEMBER_COMMS.md` §4. The two must say the
same thing; if they ever diverge, THIS file is the policy and the short version
is the summary that needs fixing.

Product: **Open Ledger Sports Member, $49/month, no trial.**

---

## 1. Cancellation — access runs to the end of the paid period

Cancelling stops the next renewal. It does **not** revoke Discord or member
access immediately; the member keeps access until the period they have already
paid for ends.

This matches Whop's normal cancellation behaviour (its API defaults to
`at_period_end`; immediate revocation exists but must be explicitly chosen, and
we do not choose it). It is also simply the fairest rule for a prepaid monthly
membership — someone who has paid for a month has bought that month.

## 2. Mid-cycle refunds — no prorating

Once a billing period has begun, unused days are not refunded because someone
cancels part-way through. They keep the access they paid for (section 1) rather
than getting money back for it.

Exceptions, which are refunded:

- a duplicate charge or other payment error;
- refunds required by Whop, the payment networks, or applicable law;
- a genuine Open Ledger **delivery** failure we cannot reasonably correct
  (section 4).

**Never a ground for refund: wins, losses, profitability, or dissatisfaction
with betting results.** That is not a hard line taken for our convenience — it
is the only line consistent with what the product says before purchase. The
public copy states plainly that no expectation claim is made and that two
published studies failed to establish an edge. A refund granted because the
plays lost would quietly concede a promise we were careful never to make.

## 3. Accidental renewals — 48-hour courtesy window, on an objective condition

A full refund is available when **both** hold:

1. the member contacts us within **48 hours** of the renewal charge; **and**
2. **no new member-only slate or premium content has been published since that
   renewal.**

The second condition is deliberately about DELIVERY, not usage. "If they did not
use it" is unprovable — we cannot know whether somebody read Discord, and a rule
that depends on an unknowable fact gets decided by whoever is in the better mood
that day.

**AND IT IS AUDITABLE FROM THE REPO, WHICH IS THE POINT.**
`data/post_status.json` records every member delivery with a UTC timestamp:

    {"date": "2026-09-01", "mode": "fb_slate", "result": "posted",
     "http_status": 204, "detail": "9 messages",
     "at_utc": "2026-09-05T18:09:54Z"}

`mode: "fb_slate"` is the members' full slate; `fb_free` is the public play and
does not count as member content. So the question "was member content delivered
after this renewal?" is answered by a timestamp in a committed file, not by
recollection. Check it before deciding, and quote it if the answer is no.

If member content **has** been delivered since the renewal, the normal
no-refund-for-the-current-period rule applies, subject to the section 2
exceptions.

## 4. First-purchase dissatisfaction — no performance refund, but a delivery guarantee

There is **no** refund for a losing play or a disappointing record. The product
disclaims that promise before purchase, in public, repeatedly.

There **is** a delivery guarantee, and it is real:

- If a paying member cannot obtain the advertised member access because of an
  Open Ledger technical failure, and we cannot correct it **within 24 hours of
  them contacting support**, they get a full refund.
- The same applies if the product was materially not delivered as described.

The distinction is the whole policy in one line: **we guarantee delivery, never
results.**

Note the most likely access problem is not a technical failure at all — it is a
member who has not connected their Discord account to Whop, which is why that
instruction leads every onboarding asset (`MEMBER_COMMS.md`). Walk them through
that first; the 24-hour clock is for faults on our side.

## 5. Abuse protection — courtesy is not a loophole

- **One** accidental-renewal courtesy refund (section 3) per customer per
  rolling 12 months.
- Repeated join / cancel / refund cycling makes a customer ineligible for
  further **discretionary** refunds and for promotional offers.

This restriction **never** overrides a refund that Whop, a payment network, or
applicable law requires. Discretion only ever narrows what we grant as a
courtesy; it cannot narrow an obligation.

## 6. Platform precedence — Whop wins where Whop is required

Open Ledger's policy governs **product and service** refund requests: whether we
refund a membership, on what grounds, within what window.

Whop's requirements take precedence for the **payment layer**: payment
processing, payment errors, fraud controls, card-network rights, chargebacks,
and anything applicable law requires. Where the two conflict on those, Whop and
the networks win, and this policy yields.

Say this to customers rather than hiding it. A policy that implies we can
override a card network's chargeback rights is a policy that will be
contradicted in public at the worst possible moment.

---

## Operational rules

These are not customer-facing, and they are the part most likely to be skipped.

### Never ignore a Whop Resolution Center case

Whop gives the seller **seven days** to accept, deny, or request more
information. If the seller does not respond, **Whop can decide the case
itself.** Silence is therefore not a neutral act — it is a decision to let
someone else decide, and it will not reliably go our way.

Treat a Resolution Center notification like the football capture window: a
deadline that cannot be recovered once it passes.

### PRE-LAUNCH CHECK: disable automatic refunds for this product

**Status: UNVERIFIED — Daniel must confirm before checkout goes live.**

Whop supports configurable automatic refunds. If an auto-refund threshold is set
high enough to cover a $49 membership, Whop could approve automatically a
request this policy intends to evaluate by hand — and section 3's 48-hour rule
depends on a fact (was a slate delivered since the renewal?) that only a human
checking `post_status.json` can establish.

**Disable automatic refunds for this product and evaluate requests manually.**

This cannot be checked from the repo; it lives in the Whop account. Verify it
before `WHOP_CHECKOUT_URL` is set, not after.

### Support route: Whop, canonically

Billing, cancellation, refund and access support all go through the member's
**Whop membership/order**, not Discord DMs or email.

The reason is auditability, not convenience: Whop ties the thread to the actual
customer and the actual purchase. A refund argument conducted across Discord DMs
has no record either side can point at, and this is a brand whose entire claim
is that its records are checkable.

---

## Launch checklist

- [ ] Automatic refunds **disabled** for the membership product in Whop
- [ ] Support route wording live in both onboarding assets (`MEMBER_COMMS.md`)
- [ ] Customer-facing short version published at checkout (`MEMBER_COMMS.md` §4)
- [ ] Someone owns Resolution Center notifications and will see them inside 7 days
- [ ] This policy reachable from the site or the Whop product page before purchase

---

Open Ledger Sports provides sports analytics and information only. It is not a
sportsbook and does not accept wagers. Nothing here is betting advice and no
outcome is guaranteed. 21+. If gambling is causing problems for you or someone
you know, call or text 1-800-GAMBLER.

*The footer is here because the checklist above says this policy should be
reachable before purchase — which makes it customer-facing, and House Rule 5
puts the legal footer on every customer-facing page without exception. If this
document is ever published as a web page rather than read in the repo, the
footer travels with it.*
