# Blog post draft — the football research

**Status: DRAFT, not published.** Body below is HTML in the same shape as
`scripts/blog_evergreen.py` entries (`slug` / `title` / `teaser` / `body`), so it
can drop straight into the blog machinery.

**Publishing note.** `blog.py --post FILE` publishes a hand-written piece
for today, outranking both automatic paths. The ready-to-publish copy of this
post lives at `blog/drafts/football-research.html` — a valid HTML file with its
title and teaser in comments, so it can be opened in a browser and read exactly
as it will publish.

To publish:

    python scripts/blog.py --post blog/drafts/football-research.html

That appends it to `data/blog_items.json` as kind `feature`, renders the post
page and index, and regenerates `feed.xml` — the same path every other post
takes. Publishing on a game day means the slate post does not run for that date;
the board itself is unaffected and still on the site.

---

- **slug:** `we-built-a-football-model-it-does-not-beat-the-market`
- **title:** We built a football model. It doesn't beat the market.
- **teaser:** Two independent tests, both negative, published in full. Here is
  what we spent, what we found, what we got wrong, and what football will
  actually be on this site.

---

```html
<p>Baseball season is ending, so we spent the last stretch building a football
model. It does not work. We are publishing the whole thing anyway, because a
ledger that only shows the wins is not a ledger.</p>

<p>Two tests. Both negative. Here is what happened.</p>

<h3>Test one: can the model out-forecast the market?</h3>

<p>We built a market-blind NFL model — Elo plus an opponent-adjusted efficiency
ridge, fitted on 2010&ndash;2021, validated once on 2022&ndash;2024. Market-blind
means it never saw a sportsbook number while it was being fitted. Then we bought
three seasons of historical prices and asked one question: at the moment we could
actually have bet, 24 hours before kickoff, does the model beat the market?</p>

<p>No. On every market, in every season. Nine out of nine.</p>

<p>On the moneyline across 852 games, the model scored 0.63434 in log loss. The
market scored <strong>0.60908</strong>. Lower is better, so the market won by
0.025 — roughly the distance between our model and a fifteen-book consensus that
has priced injuries, weather, line movement and everything else we never
ingested.</p>

<p>Losing head-to-head does not by itself prove a model is worthless. A weak
model can still add something on top of a strong one. So we tested that too, and
the model's contribution was statistically indistinguishable from zero on all
three markets. That test was <em>in-sample</em>, which biases it in the model's
favour. Even with a thumb on the scale, it added nothing.</p>

<p>The model is not broken, incidentally. Against football baselines it is
perfectly ordinary — it beats always-pick-the-home-team comfortably. It knows
real things about football. The market already knows all of them, a day before
kickoff, and more besides.</p>

<h3>Test two: forget forecasting. Can we just get a better price?</h3>

<p>That first result kills one idea, not every idea. Beating the market's opinion
and beating the market's <em>price</em> are different problems. You do not need a
better forecast to profit from a better number.</p>

<p>And the numbers are genuinely different across books. Measuring 23 books over
12,462 prices, the best available number beat the consensus median by
<strong>1.27 percentage points</strong> of implied probability on average. Sixty
percent of the time it cleared a full point. That is real dispersion, and it is
free money if it clears the vig.</p>

<p>So we pre-registered a second test before running it — hypothesis, filters,
book list, and the exact bar it had to clear, all written down and frozen first —
bought the closing prices we were missing, and ran it.</p>

<p>It does not clear the vig. Expected value came out <strong>&minus;1.05%</strong>
overall and <strong>&minus;2.64%</strong> in the season we had held back for
validation.</p>

<h3>Why we believe the number</h3>

<p>This is the part that convinced us, and it is arithmetic anyone can check.</p>

<p>The measured overround on these markets — the house's cut — is
<strong>4.26 points</strong>, or about 2.13 per side. Shopping for the best price
recovers 1.27 of that. Which leaves roughly 0.9 points of vig still being paid.
Measured expected value: &minus;1.05%.</p>

<p><strong>The result is the vig.</strong> Line shopping recovers about half of
it and no more. Not a mysterious edge that failed to appear — a toll, measured,
that we did not fully escape.</p>

<p>One more check mattered. We ran a control arm alongside the real one: same
pipeline, side chosen by a coin flip. It won 49.3% of the time against a
predicted 49.7%. If our price maths were broken, that is where it would have
shown up first and loudest. It held.</p>

<h3>What we got wrong</h3>

<p>Our own pre-registration made a prediction that turned out backwards, and
since we wrote it down in advance we are stuck reporting it.</p>

<p>We expected closing line value to look <em>good</em> even while expected value
looked bad — because shopping for the best of many books should beat a consensus
almost automatically. It did not. CLV came out <strong>negative</strong>.</p>

<p>The reason is worth knowing if you shop lines yourself. Our rule chose the side
where one book disagreed most with everyone else. That book is usually not
clever; it is slow. Its number drifts back toward consensus before kickoff. So
hunting the biggest outlier means systematically buying the price most likely to
move against you.</p>

<p>We would not have found that without the control arm, and we would not have
had to admit it without the pre-registration. Both earned their keep.</p>

<h3>What football will actually be here</h3>

<p>Not picks sold as an edge. We have now tested that claim twice, by two
different mechanisms, and it failed twice.</p>

<p>What we will publish is the full slate with real reasoning on every game that
carries a real market — how many books are pricing it, where the consensus sits
once the vig is stripped out, what the best available number is and who has it,
and how much you are paying to play. Plus one play a week we would actually act
on, chosen by a rule fixed in advance and published before kickoff.</p>

<p>It is graded in public, win or lose, at zero units, in its own ledger. And it
comes with no claim of positive expectation, because we do not have one and will
not pretend otherwise. If that ever changes it will be because a new test with its
own pre-registration says so, in public, before we sell anything on it.</p>

<p>What we can honestly say is narrower and still useful: on that play, the toll
to enter is about a quarter of what the raw consensus charges. That is execution,
not prediction. It is what is left when you have measured everything else and been
honest about the answer.</p>

<h3>The point</h3>

<p>Passing is a position. We say that on days when nothing on the board clears our
gates. This time it applied to an entire sport, a model, and two months of work.</p>

<p>Both write-ups are in the repository in full, with the code to reproduce them.
Neither has a number in it we would not show you.</p>
```
