#!/usr/bin/env python3
"""
Open Ledger Sports — the evergreen library for The Morning Line (the daily blog).

Twelve bettor-education pieces, written once, reviewed, and committed — NOT
generated at run time. On days with no MLB slate, blog.py publishes the next
unused one in order (rotation state = how many evergreen posts are already in
data/blog_items.json). Deterministic, reproducible, no API calls, no surprises.

Editorial line, same as the site: literacy, not locks. Every piece explains a
mechanism a bettor can verify for themselves. Nothing here promises profit,
recommends a bankroll, or claims an automated check that isn't automated.
When a piece references our own machinery (the ledger, the gates, the watch
list), it describes what the site actually does today — House Rule 8 applies
to the blog too.
"""

POSTS = [
    {
        "slug": "clv-the-scoreboard-that-settles-early",
        "title": "CLV: the score that settles before the game starts",
        "teaser": ("You can lose the bet and still have made a good one. Closing line value is "
                   "how you tell — and it's the only daily verdict that doesn't need a month "
                   "of results to mean something."),
        "body": """
<p>Every bet you place gets judged twice. The second judgment is the one everyone watches:
the game ends, the bet wins or loses. The first happens quietly at first pitch, when the
market posts its final price — and it is by far the more informative of the two.</p>
<p>Say you take a team at +120 in the morning. By game time the price has moved to +105.
The market — every professional group and every sharp dollar acting on it — spent the whole
day deciding your side was better than the price you got. You <strong>beat the close</strong>.
Whether the team then wins is, on any single night, mostly noise. Whether you consistently
get better prices than the close is signal, and it shows up in a sample of dozens, not
thousands.</p>
<h3>Why the close is the benchmark</h3>
<p>The closing line is the sharpest public forecast of a baseball game that exists. It has
absorbed the lineups, the weather, the late scratches, and the opinions of everyone with
money and a model. Decades of betting-market research point the same direction: bettors who
beat the closing price tend to win long-term, and bettors who don't, don't — <em>regardless
of how their last few weeks of results look</em>. Results lie for months. The close starts
telling the truth in weeks.</p>
<h3>How to measure it honestly</h3>
<ul>
<li><strong>Log the price you actually got</strong>, not the price you wish you'd got.</li>
<li><strong>Compare against the true close</strong> — the last pre-first-pitch line, not an
in-play number.</li>
<li><strong>De-vig both prices</strong> before comparing, so you're measuring probability
points, not juice (we cover the de-vig arithmetic in another piece).</li>
<li><strong>Count every bet.</strong> CLV cherry-picked is CLV fabricated.</li>
</ul>
<p>This is exactly why our own pipeline captures the closing line for every game, every day,
and grades closing-line value into the record alongside wins and losses. A pick service that
shows you only its win-loss record is showing you its weather. CLV is the climate.</p>
<p><strong>The uncomfortable corollary:</strong> if you've been winning but consistently
getting worse-than-closing prices, the market is telling you the wins are borrowed. Nobody
wants that verdict early. It's still cheaper than getting it late.</p>
""",
    },
    {
        "slug": "devig-what-minus-110-charges",
        "title": "The vig, de-vigged: what −110 actually charges you",
        "teaser": ("The book's margin hides inside the odds, and you can't measure an edge — "
                   "yours or anyone's — until you strip it out. The arithmetic takes thirty "
                   "seconds and changes how every line looks."),
        "body": """
<p>Ask a casual bettor what −110 means and they'll say "bet 110 to win 100." True, and
incomplete. The complete answer: −110 implies a probability, that probability is inflated,
and the inflation is the book's fee. Until you can strip the fee out, every line you look at
is lying to you a little.</p>
<h3>Step one: odds to implied probability</h3>
<p>For a negative American price, implied probability = odds / (odds + 100), using the
absolute value. So −110 → 110/210 = <strong>52.4%</strong>. For a positive price it's
100 / (odds + 100): +120 → 100/220 = <strong>45.5%</strong>.</p>
<h3>Step two: notice the sum</h3>
<p>Take a game priced −110 / −110. Each side implies 52.4%, and 52.4 + 52.4 =
<strong>104.8%</strong>. Real probabilities sum to 100%; the extra 4.8 points is the
<em>vigorish</em> — the margin the book charges for taking the bet. On a −150/+130 game:
60.0% + 43.5% = 103.5%. That overround is the house edge, spread across both sides.</p>
<h3>Step three: de-vig</h3>
<p>The standard first-pass method is proportional: divide each implied probability by the
total. On the −110/−110 game, 52.4/104.8 → <strong>50.0%</strong> per side — the market
actually thinks it's a coin flip, and charges you as if your side were 52.4%. On −150/+130:
60.0/103.5 → 58.0%, and 43.5/103.5 → 42.0%.</p>
<p>That de-vigged number is the market's real opinion. It's the number any model must beat —
not the posted price. This is why a "51% confident" pick at −110 is not an edge at all: you'd
need 52.4% just to break even. The gap between 51% and 52.4% is not pedantry. Over a season,
it is the entire difference between a winning bettor and a losing one who feels unlucky.</p>
<h3>What this buys you</h3>
<ul>
<li><strong>A break-even table you can carry in your head:</strong> −110 needs 52.4%,
−120 needs 54.5%, −150 needs 60%, +150 needs 40%.</li>
<li><strong>A test for any tout:</strong> if their claimed win rate at their average price
doesn't clear the de-vigged break-even by a real margin, the record is noise or worse.</li>
<li><strong>A test for us too:</strong> our engine prices every pick against the de-vigged
consensus, and when the model and the de-vigged market disagree by more than 12 points, we
assume the market knows something we don't and stand down. The arithmetic above is exactly
the arithmetic in the code.</li>
</ul>
""",
    },
    {
        "slug": "kelly-quarter-kelly-governor",
        "title": "Kelly, quarter-Kelly, and why the governor beats the gas pedal",
        "teaser": ("The Kelly criterion tells you the mathematically optimal bet size — and "
                   "betting it full-strength has ruined more sharp bettors than bad picks "
                   "ever did. Here's the formula, and why we cut it to a quarter."),
        "body": """
<p>The Kelly criterion answers a precise question: given an edge and a price, what fraction
of your bankroll maximizes long-run growth? For a bet at decimal odds <em>b</em>-to-1 with
win probability <em>p</em>, the answer is <strong>f = (bp − q) / b</strong>, where
<em>q = 1 − p</em>. A 55% shot at even money: (1×0.55 − 0.45)/1 = 10% of bankroll.</p>
<p>Kelly is genuinely optimal — under conditions no bettor actually enjoys. It assumes you
know your true win probability exactly. You don't. You have an estimate, produced by a model
with error bars, and here is the trap: <strong>the times your model most overestimates its
edge are precisely the times Kelly tells you to bet biggest.</strong> Full Kelly doesn't
just tolerate your worst errors; it leverages them.</p>
<h3>What overbetting costs</h3>
<p>The damage isn't symmetric. Bet half of Kelly and you get about three-quarters of the
optimal growth with dramatically less variance. Bet <em>double</em> Kelly and expected
growth goes to zero; beyond that, a bettor with a genuine edge still goes broke. The
punishment for oversizing is ruin. The punishment for undersizing is patience. These are
not comparable prices.</p>
<h3>Why a quarter</h3>
<p>Fractional Kelly is the standard professional hedge against estimation error, and the
fraction is a confession of how much you trust your own probabilities. Quarter-Kelly — our
choice — says: assume the model's stated edge is substantially optimistic, size so that
even a halved true edge leaves us on the right side of the growth curve, and accept slower
compounding as the fee for staying alive. Every pick we publish prints the raw quarter-Kelly
suggestion next to the tier cap that actually governs it, and the smaller number wins.
The governor beats the gas pedal — it's on every card because it's the actual policy.</p>
<h3>If you take one thing</h3>
<ul>
<li>Size from your bankroll, not your feelings, and write the rule down before the streak —
hot or cold — that will tempt you to break it.</li>
<li>Whatever your edge estimate, assume it's high. Fraction accordingly.</li>
<li>A flat 1u on everything is not sophisticated, but it beats improvised sizing every
single season. The bettors who blow up are almost never the ones betting too small.</li>
</ul>
""",
    },
    {
        "slug": "park-factors-coors-is-not-a-cheat-code",
        "title": "Park factors: Coors is not a cheat code",
        "teaser": ("Every bettor knows Coors Field inflates runs. The market knows it harder "
                   "than you do. Where park factors actually earn their keep is quieter — "
                   "and it isn't in betting every Rockies over."),
        "body": """
<p>A park factor is a simple ratio: how many runs score in a given ballpark versus a neutral
one. Coors Field sits around the mid-1.20s — a quarter more scoring than average — because
altitude thins the air, fly balls carry, and the huge outfield drops singles in front of
outfielders who have to cover it. At the other end, parks like T-Mobile in Seattle suppress
scoring below 0.95. Our engine applies a static factor for every venue, printed on every
card.</p>
<h3>The trap: known information isn't an edge</h3>
<p>The market prices Coors before you wake up. The average posted total at Coors runs
multiple runs above a normal game — the adjustment is already in the number you're being
offered. Betting the over "because Coors" is paying for information that was free to
everyone. Park factors are <em>necessary</em> for any model to be sane; they are almost
never <em>sufficient</em> to beat a closing total on their own.</p>
<h3>Where the nuance actually lives</h3>
<ul>
<li><strong>Parks move components, not just totals.</strong> Some parks inflate home runs
specifically while suppressing average; some do the reverse. A fly-ball pitcher in a
homer-friendly park is a specific risk the single team-level number blurs.</li>
<li><strong>Factors are estimates with error bars.</strong> A single season of home/road
splits is noisy; multi-year blends lag real changes (fence moves, humidor policies). Ours
are static season approximations and we say so on the Methodology page rather than
pretending otherwise.</li>
<li><strong>Interaction beats the headline.</strong> The interesting question is never "is
this park big?" — it's "does <em>this</em> pitching matchup, <em>this</em> weather, in
<em>this</em> park, add up to a number different from the posted total?" That's a model's
job, not a slogan's.</li>
</ul>
<p><strong>The honest summary:</strong> park factors are table stakes. If a handicapper's
pitch leans on "Coors over" energy, they're selling you the most efficiently priced fact in
baseball. The park number belongs inside a model, next to the pitchers and the weather —
which is where ours lives, visible on every card, doing its quiet fractional work.</p>
""",
    },
    {
        "slug": "why-the-closing-line-is-hard-to-beat",
        "title": "Why the closing line is so hard to beat",
        "teaser": ("The closing number isn't smart because bookmakers are geniuses. It's smart "
                   "because it's a scoreboard of everyone's money, sharpened all day by people "
                   "betting real stakes. Respecting it is a strategy, not a surrender."),
        "body": """
<p>A baseball line opens in the morning as one oddsmaker's informed guess. By first pitch it
has been shot at all day: professional groups bet the discrepancies they find, books move
the number toward the money they respect, slower books copy the sharper ones, and every
lineup card, weather report, and bullpen note gets priced in within minutes of existing.
The close is not an opinion. It is the residue of every opinion that was willing to pay
to be counted.</p>
<h3>The evidence, briefly</h3>
<p>Study after study of betting markets finds the same things: closing prices predict
outcomes better than opening prices; the de-vigged close is very close to an unbiased
probability estimate; and almost nobody's picks beat the close consistently. That last one
is the quiet killer of pick services. Over a season, a bettor who can't get better numbers
than the close is a bettor funding everyone who can.</p>
<h3>What this means in practice</h3>
<ul>
<li><strong>Early beats late, structurally.</strong> Openers and morning lines contain
yesterday's information plus a guess. If you have any genuine signal the market hasn't
priced — an injury read, a weather forecast, a model output — it is worth the most hours
before the close, not minutes.</li>
<li><strong>A huge "edge" against the close is usually a mirage.</strong> If your number
says 58% and the closing market says 45%, the first hypothesis should not be that you've
out-thought everyone. It should be that the market knows something you don't. Our engine
enforces this as a hard rule: past 12 points of disagreement with the de-vigged market,
the play is held, not bet harder.</li>
<li><strong>Anchor to the market, hunt at the margins.</strong> Our model's published
probability is deliberately blended toward the de-vigged consensus — the model gets a vote,
not a veto. The plays worth anything are the small, persistent gaps, caught early, at the
best available price.</li>
</ul>
<p>None of this makes the market unbeatable — closing prices still contain vig, and slow
information (weather, bullpen fatigue, umpires) does leak in late. But the burden of proof
sits with whoever claims to beat the close, and the proof is a logged CLV record. Ours is
being built in public, one captured close at a time. Ask anyone else selling picks for
theirs.</p>
""",
    },
    {
        "slug": "line-shopping-the-only-free-lunch",
        "title": "Line shopping is the only free lunch in this business",
        "teaser": ("You can't buy a better model at the sportsbook counter, but you can buy a "
                   "better price — every day, with no skill required. The half-point and the "
                   "nickel of juice you're leaving behind compound into your entire margin."),
        "body": """
<p>Almost everything in betting is contested — models, angles, information. Price is the
exception. At any moment, the same game is posted at different numbers across US books:
one book's −115 is another's −105, one book's total sits at 8 while three others hang 8.5.
Taking the best of those prices requires no forecasting talent whatsoever, and its value
is mechanical, guaranteed, and paid on every single bet.</p>
<h3>The arithmetic of a dime</h3>
<p>Moving from −115 to −105 on the same side drops your break-even from 53.5% to 51.2% —
2.3 probability points, on a bet whose realistic edge, if you have one at all, is about
that size. Line shopping doesn't supplement your edge; on many bets it <em>is</em> your
edge. A bettor with a mediocre model and excellent prices will beat a bettor with a good
model and lazy prices, season after season.</p>
<h3>Doing it honestly</h3>
<ul>
<li><strong>Compare same bet, same line.</strong> Over 8.5 at −105 and Over 8 at −120 are
different bets, not different prices for one bet. (When our own pipeline ingests best
prices, a totals price only counts if it's quoted at the consensus line — comparing across
lines is how you fool yourself.)</li>
<li><strong>Two or three books is most of the benefit.</strong> The marginal value of the
tenth account is small; the marginal value of the second is enormous.</li>
<li><strong>Log the price you got against the consensus.</strong> The gap is your shopping
profit, and it's measurable — our boards print the best price with the book's name next to
the consensus median for exactly this reason.</li>
</ul>
<p><strong>One caution:</strong> a price wildly better than every other book is sometimes a
stale number about to vanish and sometimes a book that knows its market poorly — but it can
also mean the market moved on news you haven't seen yet. Best price at or near the current
market: free money. Best price against a market that's sprinting away from it: read the
news first.</p>
<p>There is no other place in this business where value is just lying on the counter. Pick
it up.</p>
""",
    },
    {
        "slug": "the-drawdown-you-signed-up-for",
        "title": "The drawdown you signed up for: variance for winning bettors",
        "teaser": ("A genuinely profitable bettor — 54% at standard juice — should expect "
                   "losing months and double-digit-unit drawdowns as a matter of arithmetic, "
                   "not misfortune. If you don't know the size of the storm in advance, "
                   "you'll abandon ship in the middle of it."),
        "body": """
<p>Here is the least-advertised fact in sports betting: take a bettor with a real, durable
edge — 54% winners at −110, flat one-unit stakes, which is an excellent long-run clip —
and simulate their season. The <em>expected</em> profit on 1,000 bets is about 31 units.
The path to it routinely includes losing streaks of eight and nine, multiple losing months,
and drawdowns of 20 units or more from a high-water mark. Not as bad luck. As the ordinary
texture of a winning record.</p>
<h3>The arithmetic of streaks</h3>
<p>A 54% bettor loses any given bet 46% of the time. Over 1,000 bets, the odds of at least
one 8-loss streak are overwhelming — it's not a tail event, it's a near-certainty. Over a
30-bet month, the chance of a losing record is roughly one in three. Three losing months in
a season is unremarkable for a bettor who is genuinely good.</p>
<h3>Why this ruins people</h3>
<p>Because nobody experiences probability; everyone experiences sequence. The 20-unit
drawdown doesn't arrive labeled "ordinary variance." It arrives as a month of your picks
looking stupid, and it makes two ruinous options feel reasonable: <strong>chasing</strong>
(sizing up to win it back — converting ordinary variance into ruin) and
<strong>abandoning</strong> (quitting the method precisely when nothing about it has
changed). The bettors who survive are the ones who decided <em>before the streak</em> what
sizes they'd bet and what evidence would actually change their minds.</p>
<h3>Weather versus climate</h3>
<ul>
<li><strong>Results in samples under a few hundred bets are weather.</strong> Our own site
says a meaningful sample is 500–1,000 picks, and says it right under the ledger — including
when the ledger is losing.</li>
<li><strong>Process metrics converge faster than results.</strong> Closing line value tells
you in weeks what win-rate takes months to say. In a drawdown, CLV is the instrument panel
that tells you whether you're flying level.</li>
<li><strong>Sizing is survival.</strong> The point of small, fixed stakes isn't modesty.
It's that the inevitable 9-loss streak costs 9 units instead of a bankroll.</li>
</ul>
<p>Decide your rules on a calm day. The streak is coming either way; the only question is
whether it finds your discipline written down.</p>
""",
    },
    {
        "slug": "weather-and-totals-what-heat-actually-does",
        "title": "Heat, air, and the over: what weather actually does to a total",
        "teaser": ("A baseball flies measurably farther through hot, thin, humid air — the "
                   "physics is real and the effect sizes are known. The bettor's question is "
                   "narrower: how much of it is already in the number by the time you bet?"),
        "body": """
<p>Of all the folk wisdom in baseball betting, weather is the rare piece with hard physics
underneath. Warm air is less dense than cold air; a fly ball at 90°F carries several feet
farther than the same ball at 60°F. Altitude compounds it. Humid air — counterintuitively —
is lighter than dry air, adding another small push. Ballpark studies put the effect around
a few tenths of a run per ten degrees, concentrated in home-run-adjacent scoring. Wind is
bigger per unit but messier: a straight out-blowing wind can add a run; a strong in-blowing
wind can erase more than that; a crosswind in a park with an odd orientation does something
in between that requires knowing the park's geometry, not just the forecast.</p>
<h3>The parts bettors get wrong</h3>
<ul>
<li><strong>Roofs.</strong> A third of MLB parks are indoors or closable. A hot-day angle
in a retractable-roof city is often an angle about air conditioning.</li>
<li><strong>Forecast at game hour, not at noon.</strong> A night game's weather is not the
afternoon's headline number, and a 7 PM wind forecast made at 8 AM has real error bars.</li>
<li><strong>Interaction with pitcher type.</strong> Hot air helps the ball over the fence,
which matters most against fly-ball pitchers. The same 92° day is a different fact for a
ground-ball sinkerballer.</li>
<li><strong>The market prices the obvious.</strong> By late morning, posted totals in
Cincinnati in July already carry the heat. The plausible edge is in the gaps — forecast
changes after the open, wind effects in odd parks, roof decisions — not in "it's hot, bet
the over."</li>
</ul>
<h3>How we handle it (and why our staked picks ignore it)</h3>
<p>Our engine runs a second, weather-adjusted simulation — temperature plus a hot-day
kicker against fly-ball starters — but it feeds <em>only</em> the totals paper track, where
its win rate and closing-line value are being measured against the market before a single
unit rides on it. Wind speed is fetched and deliberately <em>not</em> modeled, because we
don't have park orientation data and guessing the sign of a wind effect is worse than
skipping it. If the weather signal proves it beats the close, it earns its way into real
picks. If it doesn't, it stays a nice piece of physics. Measuring before staking is the
whole method.</p>
""",
    },
    {
        "slug": "the-umpire-moves-the-total-a-little",
        "title": "The umpire behind the plate moves the total (a little)",
        "teaser": ("Home-plate umpires have measurably different strike zones, and a bigger "
                   "zone quietly deflates scoring. It's a real input with a real size — "
                   "smaller than the tout version, larger than zero, and shrinking every "
                   "season the zone gets policed."),
        "body": """
<p>Every home-plate umpire calls a slightly different game, and pitch-tracking has made the
differences legible: some umps call a zone meaningfully larger than the rulebook, some
smaller, and the spread between the extremes is worth real runs. The mechanism is
straightforward — a wider zone means more called strikes, more pitcher-friendly counts,
fewer walks, shorter innings. Hitter counts and pitcher counts produce wildly different
outcomes, so a zone that shifts the count distribution shifts run scoring with it.</p>
<h3>What the effect is actually worth</h3>
<p>At the extremes, the gap between the most pitcher-friendly and most hitter-friendly
regular umpires has historically been on the order of half a run of expected total — but
most assignments are nowhere near the extremes, and the effect a bettor can act on is
usually a few tenths of a run at best. Three caveats shrink it further:</p>
<ul>
<li><strong>The spread is compressing.</strong> League review and grading have pulled umpire
zones toward the rulebook year over year. Numbers from five seasons ago overstate today's
edge, and any automated-zone future shrinks it toward zero.</li>
<li><strong>Assignments are known, so they're priced.</strong> Plate umpires are public
before first pitch, and sharp totals money reacts within minutes. An umpire angle bet
after the market has moved is a story, not an edge.</li>
<li><strong>Sample sizes lie.</strong> An umpire's "over record" over 30 games is mostly
noise from which parks and pitchers he happened to draw. Zone-based metrics beat
over/under records; both need multi-season samples.</li>
</ul>
<h3>Where it fits in a process</h3>
<p>Umpires belong in the same bucket as weather: a genuinely real, modest, decaying input
whose value depends entirely on catching it before the close prices it. We don't currently
model umpires — our boards say so rather than implying otherwise — and our roadmap treats
it the way it treats weather: it would ship on the paper track first, measured against the
close, and would earn a place in staked picks only with a positive CLV record. An input
that can't beat the close isn't an input. It's trivia.</p>
""",
    },
    {
        "slug": "moneyline-or-run-line",
        "title": "Moneyline or run line? What −1.5 actually buys",
        "teaser": ("The run line looks like a discount on a heavy favorite. What it really "
                   "sells you is exposure to margin of victory — and in a sport where a third "
                   "of games are decided by one run, that's a very specific product."),
        "body": """
<p>Every baseball favorite comes in two packages. The moneyline pays if they win at all;
the −1.5 run line pays only if they win by two or more, at a much friendlier price. The
choice between them isn't taste — it's a bet on <em>how</em> the game gets won, and the
sport has strong opinions about that.</p>
<h3>The one-run problem</h3>
<p>Roughly 28–30% of MLB games are decided by exactly one run. Those games are your
enemy on a −1.5 ticket: the favorite can play well, win, and still lose you the bet.
Worse, one-run outcomes aren't evenly distributed — home favorites win by exactly one
<em>more often than the naive math suggests</em>, because a home team that takes the lead
in the ninth simply stops batting. That walk-off structure clips margin-of-victory outcomes
at one run and quietly taxes home −1.5 tickets. The identical bet on a road favorite doesn't
carry that structural tax, which is why run lines on road favorites and home favorites are
different products wearing the same clothes.</p>
<h3>When the pivot earns its keep</h3>
<ul>
<li><strong>Blowout-shaped matchups:</strong> a big favorite whose edge comes from
run-scoring (not one ace pitcher in a projected 3–2 game) converts wins into multi-run wins
more often, closing the gap between the two tickets.</li>
<li><strong>When the moneyline juice is structural:</strong> laying −200 rents a favorite
at luxury prices — the wins are small and the losses are structural. Our own Rule 2 refuses
straight moneylines past −180 (−170 in day games) and pivots to the run line or passes,
not because −1.5 is generous but because heavy juice is worse.</li>
<li><strong>When the prices say so:</strong> the honest method is to price both tickets —
your model's win probability for the moneyline, its cover probability for the spread — and
take whichever shows the larger de-vigged edge. Sometimes the answer is neither, which is
also an answer.</li>
</ul>
<p><strong>The trap to avoid:</strong> taking −1.5 purely because the moneyline price
offends you. The offense is real, but the run line isn't a discount — it's a different bet
with its own break-even, and the one-run problem never waives its fee. Price it, or pass.</p>
""",
    },
    {
        "slug": "keep-a-ledger-or-youre-guessing",
        "title": "Keep a ledger or you're guessing: records and survivorship bias",
        "teaser": ("Memory is a marketing department for your own decisions. A written ledger "
                   "— every bet, price, stake, and result — is the only defense, and its "
                   "absence is how both bettors and touts fool you. Mostly you."),
        "body": """
<p>Ask a bettor how they're doing this season and you'll usually get a feeling, not a
number. The feeling is generated by memory, and memory is not a recording device — it's a
press office. It over-weights wins, files losses under bad luck, forgets the bets that were
never "really" serious, and rounds "about breakeven" up from numbers that would alarm you
in writing. Psychologists call the components selective recall and hindsight bias.
The betting industry calls them customers.</p>
<h3>What a real ledger contains</h3>
<ul>
<li><strong>Every bet.</strong> Not the confident ones — all of them, logged before the game
starts so the record can't quietly edit itself.</li>
<li><strong>The price and stake actually taken</strong>, so results can be separated from
sizing and closing-line value can be measured against the number you got.</li>
<li><strong>No deletions, no edits.</strong> An entry graded is an entry graded. Aggregates
get recomputed from the full history; history itself is append-only.</li>
</ul>
<p>That last rule is the whole product. It's why our ledger opened at 0–0 in public, posts
losing days with the same font as winning ones, and fingerprints every board before first
pitch. Not because we're virtuous — because a record you <em>can</em> edit is a record
nobody should believe, including its owner.</p>
<h3>Survivorship bias: the industry's engine</h3>
<p>Now scale the memory problem up. Start ten anonymous accounts posting picks; after a
month, delete the six with losing records and sell subscriptions off the four hot ones.
Nobody had to fix a game — variance alone manufactures "documented" 60% cappers on demand,
and the graveyard of deleted accounts is invisible. Screenshots, "last 30 days" windows,
and records that start whenever the streak did are all the same trick at different sizes.
The test for any record, ours included: <strong>is it complete, is it timestamped before
the games, and can you check it yourself?</strong> If any answer is no, you're not looking
at a record. You're looking at an ad.</p>
<p>A notebook and honesty beat a spreadsheet and self-deception. But the spreadsheet with
honesty is where the actual learning starts — it's the only version of you that can't
negotiate with the data.</p>
""",
    },
    {
        "slug": "passing-is-a-position",
        "title": "Passing is a position: why most edges aren't",
        "teaser": ("The hardest bet in gambling is the one you don't place. Most lines are "
                   "priced about right, most 'edges' are estimation error, and the discipline "
                   "to pass — daily, publicly, boringly — is worth more than any angle."),
        "body": """
<p>Open a slate of fifteen games and the temptation is to find the best bet on it. But
"best on the slate" is a relative measure, and the market doesn't pay relative. If every
edge on the board is negative, the best of them is just the smallest mistake available.
Some days — more days than anyone selling picks will admit — the correct portfolio is
nothing.</p>
<h3>Why most edges are mirages</h3>
<p>A model's edge is the gap between its probability and the market's, and that gap
contains two things: genuine disagreement, and error. The market's number has been
sharpened all day by professionals betting real money; your number (and ours) comes with
estimation error that is largest exactly where the model is most confident it sees
something. A small measured edge is, more often than not, measurement. This is why we run
hard gates in front of every allocation — a minimum edge below which nothing is bet, and a
divergence cap past which a "huge" edge is treated as a warning that the market knows
something, not a gift. When nothing clears, nothing is published. The site says
"no qualifying plays today" and means it.</p>
<h3>The economics of volume</h3>
<p>Betting every day at a small disadvantage is a subscription to the vig. A bettor who
fires 300 coin-flips a season at −110 pays roughly 14 units for the entertainment; a bettor
who passes those and bets only the 40 spots with a real edge keeps them. The tout industry
is structurally unable to say this — a service that charges monthly cannot sell you
nothing, so it manufactures a lock on the days the market offers none. The daily pick
exists because of the billing cycle, not the baseball.</p>
<h3>Making patience operational</h3>
<ul>
<li><strong>Decide the gates in advance</strong> — minimum edge, maximum juice, sizing —
and let them, not the slate, decide your volume.</li>
<li><strong>Log your passes.</strong> A no-bet day is a decision with an outcome; our board
publishes the leans that failed the gates, with reasons, so the passing is on the record
too (and the near-misses go to a watch list at zero units rather than getting bet out of
boredom).</li>
<li><strong>Measure discipline, not action.</strong> Over a season, the bets you didn't
place show up as vig you didn't pay. It won't feel like winning. It compounds like it.</li>
</ul>
""",
    },
]
