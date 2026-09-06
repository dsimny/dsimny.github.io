# The D.J. Mercer Spotlight — spec and operating manual

Written 2026-09-06. This is the editorial layer of Open Ledger Sports for the
football season: human-researched NFL and college football picks under the
D.J. Mercer byline, on their own append-only record.

It sits BESIDE the model's football pipeline (`docs/FOOTBALL_PIPELINE.md`),
never inside it. Three layers, three different questions:

| layer | question it answers | where |
|---|---|---|
| Open Ledger Model | what the system sees | `/football/` |
| D.J. Mercer Spotlight | what the analyst sees | `/football/mercer/` |
| The ledger | what actually happened | both, separately |

Neither record is allowed to borrow credibility from the other.

## 0. What D.J. Mercer is

A pen name for the site's editorial voice: sports enthusiast, independent
analyst. **No credentials are claimed and none may be added to the copy.**
The About page says outright that it is a pen name. The record is the only
evidence offered, and it is built so that it cannot be flattered (section 3).

The same byline is intended for *Don't Trust the Screenshot*; the Spotlight is
the practical expression of that book's argument.

## 1. The section

`/football/mercer/`, all rendered by `scripts/football/mercer.py render`:

| page | what it is |
|---|---|
| `/football/mercer/` | **Spotlight.** Profile, the three record tiles, the current week's card in full, the week archive. |
| `/football/mercer/<slate_week>/` | **One week.** Mercer's Card, Game Spotlight, Mercer's Leans, The Pass List. |
| `/football/mercer/record/` | **The Mercer Ledger.** Every graded pick, and how the record works. |
| `/football/mercer/about/` | **About D.J. Mercer.** Profile, philosophy, The Case Against. |

### The card, and the thing that makes it Mercer's

Every official pick renders four blocks, and the first two are **required by the
validator** — a card missing either one does not publish at all:

```
THE PICK               Georgia -6.5  (-110, taken at DraftKings)
WHY MERCER LIKES IT    the affirmative case                     [required]
THE CASE AGAINST       the strongest legitimate reason it is wrong [required]
WHAT WOULD CHANGE MY MIND   injury, line movement, weather, personnel
STATUS                 OFFICIAL PICK
```

Most pick sellers tell you why they are right. Enforcing "the case against" in
code is what turns that difference from a claim into a property of the page.

## 2. The taxonomy (requirement 4)

Three kinds, in three separate lists, with the status validated on top so a
mislabelled entry is refused rather than silently graded:

- **OFFICIAL** — in `picks`. Fingerprinted, graded, on the record. Must carry
  positive units; a 0-unit "official" pick is refused and pointed at `leans`.
- **LEAN** — in `leans`. Published opinion. No units, never graded.
- **PASS** — in `passes`. A tempting number deliberately avoided, with the
  reason. No units, never graded.

Every week page prints "Only OFFICIAL picks touch the record" above the leans,
and each lean and pass row carries its status as a chip.

House Rule 6 applies unchanged: a week with nothing worth playing publishes
"no official pick this week", never a manufactured one.

## 2b. Operating rules

Adopted 2026-09-06, before the first card. These govern how the Spotlight is
run, not how the code behaves, except where noted.

### Public commitment

**A Mercer selection is not considered publicly committed until its fingerprint
has been pushed to the public Open Ledger repository before kickoff. A
fingerprint existing only on a local machine is not public evidence.**

There are two timestamps and only the second one counts to a reader:

| | what it is | who can verify it |
|---|---|---|
| internal commitment | `commit` writes the SHA-256 into `data/mercer/commitments.json` | nobody but the author |
| **public commitment** | that file is pushed to GitHub | anyone, forever |

The tool enforces the first and cannot enforce the second, because pushing is a
network act outside it. So it is an operating rule with a mechanical
consequence: a pick whose fingerprint reaches the public repository after
kickoff is worth exactly as much as a screenshot, and should be voided by hand
rather than counted. Commit and push in the same sitting.

### Week 1 commissioning rule

**For the inaugural Mercer card, no additional official selections may be added
after the first public Week 1 card is pushed.**

This is a Week 1 rule only. Its purpose is to test one clean pass through
research → publication → commitment → kickoff → grading → ledger → public
display with no state changes midway. Even a selection that would be perfectly
legal under the commit gate (its game has not started) is out of scope once the
card is public.

It may be reviewed after one complete cycle succeeds. Nothing in the code
enforces it, and nothing should: `commit` is deliberately incremental so that
mid-week additions work in normal weeks.

**CONSEQUENCE FOR WEEK 1, MEASURED 2026-09-06.** One push before the earliest
kickoff means the whole card is committed before **Sun 2026-09-13 1:00pm ET**.
The Monday night game (Denver at Kansas City, Mon 8:15pm ET) has a T-24 capture
window that does not open until **Sun 2:15pm ET** - after that deadline. So a
Monday game cannot be committed on its own T-24 pricing under this rule; it
would be committed roughly 25 hours before its freshest capture exists, and
probably before its final injury designations.

That is not an argument against the rule. It is the cost of the rule, and it has
to be paid knowingly: either a Monday game is excluded from the commissioning
card, or it is committed on materially staler information than the Sunday games
on the same card. The decision is made before the card is written, never after
Sunday's games have started.

**DECIDED 2026-09-06: the Monday game is excluded from the inaugural card.**
Denver at Kansas City is out of Week 1 for **operational timing only**. It is
not a PASS, it carries no status of any kind, it is not fingerprinted, and
nothing about it is a judgement on the matchup. A commissioning exclusion and a
PASS are different things and must never be recorded as the same thing: a PASS
says a play was considered and declined, an exclusion says the clock made the
question unanswerable. Conflating them would put a handicapping opinion on the
record that was never formed.

### The independent football case

**A thesis must survive this question: "Would I still want this position if I had
never seen the current betting line?"**

The number matters enormously. That is not what this rule is about. It is about
the order in which the reasoning happens. Seeing +8, or a total of 46, and then
assembling a story that justifies it is the single easiest way to produce
analysis that looks rigorous and is actually backwards. The football case has to
exist on its own first. Only then is the market asked whether it offers a price
worth taking.

The practical test at writing time: strike every reference to the current number
from the case-for and the case-against. If what remains is not a position, there
was no thesis, only a reaction to a price.

This is why the walk-away boundary is recorded separately from the thesis. The
thesis says what is believed about the game; the boundary says at what number
that belief stops being actionable. A thesis that cannot be stated without its
price is not eligible.

### Conviction ladder

| level | meaning |
|---|---|
| **Standard** | Qualifies for the official Mercer ledger. |
| **Strong** | Materially stronger than an ordinary qualifying play. |
| **Spotlight** | Reserved for genuinely exceptional setups, and expected to be rare. There may be weeks with zero Spotlight selections. |

**Conviction never changes stake automatically, and it is not a probability
estimate.** It is an ordering of the author's own confidence, published so that
the record can later be read by conviction level and the ladder checked against
results. If Spotlight picks do not outperform Standard ones over a real sample,
that is a finding about the ladder and it gets published like any other.

A label that appears every week means nothing. Spotlight is expected to be rare,
and its rarity is the only thing that makes it informative.

## 2c. Mercer Research Filter v1.0 - FROZEN 2026-09-06

Thresholds for triaging a slate into research tiers. **Frozen for the remainder
of Week 1 and not to be recalibrated from this week's distribution again.**
Re-deriving a cut from the same data it is applied to is how a filter becomes a
description of one week rather than a rule.

### WHAT THESE GRADES MEAN, AND WHAT THEY DO NOT

**These grades measure whether a game is sufficiently researched to evaluate.
They do not measure expected betting value, confidence, or probability of
winning.**

A game graded A on all three is a game where the evidence is good enough to form
a view. It is not a game more likely to win, not a game more likely to be
selected, and not a game with a better price. A grade is a statement about our
information, not about the teams and not about the market's accuracy. Nothing in
this filter may ever be cited as a reason a selection is strong.

### The three grades

**Data quality** - how settled the availability picture is.

| grade | rule |
|---|---|
| A | 6 or fewer unresolved Questionable designations across both teams |
| B | 7 to 10 |
| C | 11 or more |

Counted from the ESPN injury feed across both teams. A team the feed does not
cover fails this outright: health is never inferred from silence.

**Market stability** - how settled the number is.

| grade | rule |
|---|---|
| A | spread dispersion <= 0.5 pts AND moneyline movement <= 2.0 probability points |
| B | dispersion <= 1.5 pts AND movement <= 3.0 pp |
| C | anything wider |

Dispersion is the widest spread across books **on a single side of the line**.
Measuring it over every quoted point mixes the favourite's -3.5 with the
underdog's +3.5 and reports 7 points of disagreement on a unanimous market; that
error was made and corrected on 2026-09-06. Movement is measured in implied
probability points, never in raw American odds, which are discontinuous at
+/-100.

**Research interest** - whether there is a question worth answering.

| grade | rule |
|---|---|
| A | spread magnitude <= 3 AND (dispersion >= 1.0 pt OR movement >= 1.5 pp) |
| B | spread <= 3, or dispersion >= 1.0 pt |
| C | neither |

### Tier gates

Hard gates send a game to Tier C: kickoff outside the commissioning window,
no spread quoted, fewer than 8 books (NFL) or 5 (college), spread magnitude
above 10.5, or dispersion above 3.0 pts. Soft gates send it to Tier B: injury
source missing for either team, no usable prior-season profile, no total
quoted, or fewer than 5 injury entries on either side. Tier A is additionally
capped as a research budget, not a threshold; games below the cap are recorded
as ranked-out, not as deficient.

### Provenance of the numbers

The boundaries were set on 2026-09-06 against the Week 1 distribution, and the
Questionable B/C boundary sits on a natural cluster break in that week's counts
(5, 5, 7, 10, 10, 11, 11, 12, 13). That is honest for a first pass and is
exactly why it is frozen now: a threshold re-cut every week against the week it
judges is not a filter.

Any change is a version bump with the reason recorded, following the precedent
of House Rule 9 for the model's gates.

## 3. The record, and why it cannot be flattered

### Requirement 1 — ledger separation

`mercer.py` writes exactly two files: `data/mercer/mercer_ledger.json` and
`data/mercer/commitments.json`. It never opens `football_ledger.json`,
`ledger.json`, `daily_ledger.json`, `totals_ledger.json` or `watchlist.json`.
The self-test greps the source to prove it, and asserts there is exactly one
writer for each of its own two files.

### Requirements 2 and 8 — pregame commitment, and the late-pick gate

`commit` computes `sha256(canonical JSON of the pick)` and stamps it with a UTC
timestamp, once per pick id, **never re-stamped**. Before stamping anything it
runs `commit_gate`, which refuses when:

- the game cannot be resolved against ESPN,
- **kickoff has already passed** — the message names backfilling as the reason,
- the game's season type can never be graded (preseason, postseason), so an
  official pick on it could only ever book VOID.

A refusal exits non-zero. This is what makes a backfilled record impossible:
every historical opinion would fail the second test. The grader keeps its own
"stamped after kickoff → VOID" rule as defence in depth for a hand-edited
commitments file, but in normal operation nothing late is ever stamped at all.

Editing a pick that is already stamped does not reissue the stamp. `commit`
reports it and goes red.

### Requirement 3 — immutable grading

A pick id already in the ledger is skipped entirely; nothing is recomputed. The
self-test grades twice and asserts the entries are byte-identical, then edits a
graded pick's line, price and stake, regrades, and asserts **no field moved**.

Because the card file is prose a person can still edit, the week page renders a
graded pick's **selection, price and stake from the ledger**, not from the card,
and prints a red "this card was edited after the pick was graded" block when the
two diverge. The grading run also goes red.

### Requirement 9 — line provenance

The ledger stores `market`, `selection`, `line`, `side`, `team`, `price`,
`units`, `book` and `conviction` exactly as fingerprinted. Grading reads those
fields rather than re-fetching a market, so **a later closing number never
replaces the number Mercer took**. `book` is optional but expected; it renders
on the card as "Taken at" and in the record table as its own column.

### Requirement 5 — units and ROI

Settlement reuses `settle.py` unchanged, which already replays all 13 real NFL
ties. Pushes return the stake exactly (a spread on the number, a total on the
number, a tie on the moneyline). ROI is **unrounded** P/L divided by units
risked on decided picks; void stakes are excluded from both sides. An earlier
version rounded P/L before dividing, which shifted ROI by up to 0.005/risked;
the self-test now pins the arithmetic at several prices and stakes.

### Requirement 6 — split records

NFL, College Football and Combined are computed separately and rendered as
three tiles on the hub and on the record page, with the void count shown.

### Requirement 10 — loss visibility

Losses render as full cards carrying every section a win carries, with their
full negative P/L, and appear in the record table identically. The self-test
extracts the losing card and asserts each block is present.

### Requirement 11 — legal language

Every page carries the standard footer, and a responsible-gambling line renders
**at the point of decision** — directly under the picks and under the record
table — matching `build_site.py`'s RG_BLOCK placement. The self-test asserts
21+, 1-800-GAMBLER, not-a-sportsbook and no-guarantee on all four pages, and
that no barred expectation word ("edge", "+EV", "the model likes") appears in
any body.

### Requirement 12 — generated-file safety

`mercer.py` writes only under `football/mercer/`. It never touches the root
`index.html` or `feed.xml`. The self-test walks the whole output tree and fails
on any file written elsewhere. The CI commit steps stage explicit paths.

## 4. Requirement 7 — weeks and dates

**Slate weeks are Tuesday-anchored in EASTERN TIME, and this deliberately
differs from `market.slate_week`.** The model takes the UTC date, which puts
Monday Night Football in the following week: a Monday 20:15 ET kickoff is
Tuesday 00:15 UTC, so the anchor lands on the game's own date and splits the
NFL week that began on Thursday. Measured:

| kickoff | `market.slate_week` | `mercer.slate_week` |
|---|---|---|
| Thu 2026-09-10 20:20 ET | 2026-09-08 | 2026-09-08 |
| Sun 2026-09-13 20:20 ET | 2026-09-08 | 2026-09-08 |
| **Mon 2026-09-14 20:15 ET** | **2026-09-15** | **2026-09-08** |
| Tue 2026-09-15 19:00 ET | 2026-09-15 | 2026-09-15 |
| Wed 2026-09-16 19:00 ET | 2026-09-15 | 2026-09-15 |

`market.slate_week` is **not changed**: it is the model's frozen grouping,
`football_ledger.json` is keyed by it, and re-anchoring it mid-season would
re-split weeks the model has already graded.

Other date handling:

- **Tuesday and Wednesday games** anchor to their own Tuesday, which is what
  those words mean. Midweek college football works.
- **Postseason and preseason** are refused at `commit` (they can never grade)
  and book VOID with the reason if one reaches grading anyway. Neither breaks
  a render.
- **Cross-year weeks** work (a Thursday in January anchors to the prior Tuesday).
- A pick whose kickoff falls outside its file's slate week is rejected.
- The hub shows **the week containing today**, falling back to the most recent
  week under way, then to the newest card. Drafting next week's card early does
  not displace the live one.
- Only directories named `YYYY-MM-DD` are treated as weeks, so `football/mercer/`
  itself is never mistaken for a slate week by the model's hub renderer.

## 5. The files

```
data/mercer/weeks/<slate_week>.json   one hand-written card per slate week
data/mercer/weeks/_template.json      the template (ignored: not a date)
data/mercer/commitments.json          per-pick fingerprints, append-only
data/mercer/mercer_ledger.json        the graded record, append-only
football/mercer/                      rendered pages (committed by CI)
```

### One pick

| field | required | notes |
|---|---|---|
| `id` | yes | unique forever; convention `<slate_week>-<sport>-<nn>` |
| `status` | no | defaults to `OFFICIAL`; any other value in `picks` is refused |
| `sport` | yes | `nfl` or `ncaaf` |
| `away`, `home` | yes | NFL: anything `teams.py` knows. College: ESPN's display name or a unique prefix. |
| `kickoff_utc` | yes | ISO-8601 UTC. Finds the game (±36h) and checks the slate week. |
| `espn_event_id` | no | pins the game when `check` reports an ambiguity; names must still agree |
| `market` | yes | `spread`, `total`, `moneyline` |
| `team` | spread/ML | written exactly as `away` or `home` |
| `line` | spread/total | spread: the number added to `team`'s score (`-3` favourite, `+3` dog) |
| `side` | total | `over` / `under` |
| `price` | yes | integer American price |
| `book` | no | where the number was taken. Recorded, rendered, never replaced. |
| `units` | no | default 1, must be positive |
| `conviction` | no | `standard`, `strong` or `spotlight` — **never a number** |
| `mercer_number` | no | free text, shown as "Mercer's number (opinion)" |
| `why` | **yes** | the affirmative case |
| `case_against` | **yes** | the strongest legitimate reason it could be wrong |
| `changes_my_mind` | no | what would flip it |

Retired field names (`worries`, `watching`, `verdict`, `confidence`) are refused
by name with a pointer to their replacement, so an old card fails loudly instead
of silently dropping its prose.

## 6. Operating a week

```
cp data/mercer/weeks/_template.json data/mercer/weeks/2026-09-08.json
#   ...edit; delete the _note...

python scripts/football/mercer.py check          # resolves every game, and says
                                                 # which picks could not be stamped
python scripts/football/mercer.py commit --dry-run   # optional rehearsal
python scripts/football/mercer.py commit         # fingerprints, refuses late ones
python scripts/football/mercer.py render
git add data/mercer football/mercer
git commit -m "Mercer card 2026-09-08"
git push                                         # the push is the public timestamp
```

`check` needs the ESPN results store to know about the games. If it reports
"no NFL game within 36h", fetch that date first:
`python scripts/football/espn_nfl.py --dates 20260913`.

Adding a Sunday pick to a card that already has Thursday's is the same steps;
only the new pick is stamped, and only if its game has not started.

Grading is automatic: `football-grade.yml` runs `mercer.py grade` then `render`
daily at 11:00Z and commits `data/mercer/` and `football/`.
`football-capture.yml` re-renders hourly.

On this machine use the full interpreter path
(`C:\Users\Desert\AppData\Local\Programs\Python\Python312\python.exe`); `python`
on PATH is the Store stub.

## 7. Deliberately NOT in v1

- **No automated "Mercer + Model" boost.** Agreement between Mercer's side and
  the model's play will be **counted first**. It is never described as making a
  pick stronger, and never increases a stake, until incremental value is
  demonstrated. The About page says so in those terms.
- **No retroactive historical record.** The ledger starts at the first publicly
  committed pick, and the record page states the date it opened. The commit gate
  makes backfilling impossible rather than merely discouraged.
- **No fake confidence precision.** Three conviction levels, no percentages.
- **No CLV on Mercer's picks.** The capture holds moneylines only and Mercer
  will mostly play spreads and totals. The record page says "not yet tracked".
- **No Discord post** for the card yet.

## 8. Follow-up TODO — the model's Monday-night week boundary

**NOT addressed in this commit, deliberately.** `market.slate_week` is unchanged
and keeps its UTC anchor; only the Mercer record uses the ET anchor described in
section 4.

The open question is whether the model wants the same fix:

- `market.slate_week` takes the UTC date, so a Monday 20:15 ET kickoff
  (Tuesday 00:15 UTC) anchors to the FOLLOWING Tuesday and is grouped away from
  the Thursday-to-Sunday games it belongs with.
- Today this is mostly latent. The model's selection rule ranks within a slate
  week, so a Monday-night game competes against the next week's pool rather than
  its own. It also means a week's board can be written and decided before its
  own Monday nighter is considered.
- It cannot be changed casually: `data/football/football_ledger.json` and
  `data/football/commitments.json` are keyed by `slate_week`, so re-anchoring
  re-labels weeks that have already been graded and fingerprinted. Under House
  Rule 1 that is a rewrite of the record, not a bug fix.
- If it is ever changed, it needs its own commit with: a migration decision for
  existing keys (almost certainly "new anchor applies from date X forward, old
  entries keep their labels"), a re-run of `selftest_board.py`, and a note in
  the version-history table.

Raised 2026-09-06 while building the Spotlight. Evidence and the measured
comparison table are in section 4.

## 8b. Follow-up defect — the grade workflow's `git add` fails atomically

**PRE-EXISTING, NOT INTRODUCED HERE, AND DELIBERATELY NOT FIXED IN THIS COMMIT.**

`football-grade.yml`'s commit step stages an explicit list of pathspecs:

    git add data/football/football_ledger.json             data/football/commitments.json             data/football/ncaaf_results.json             data/football/nfl_results.json             data/football/board_*.json             data/mercer/             football/ 2>/dev/null || true

`git add` refuses the WHOLE command if any single pathspec matches nothing, so
one missing file stages nothing at all. `2>/dev/null || true` then swallows the
error, `git diff --cached --quiet` sees an empty index, and the run finishes
green having committed nothing.

Measured 2026-09-06: `data/football/commitments.json` does not exist yet (no
board has been built on `main`), and `data/football/board_*.json` matches
nothing, so the step currently stages nothing. Verified against `HEAD` that the
same shape and the same missing pathspecs predate the Mercer work; adding
`data/mercer/` neither caused it nor worsened it, since that path does exist.

It becomes latent as soon as the first board is built, which is why it has not
been noticed. The fix, when someone takes it, is one of:

- `git add -A` over the specific directories, or
- a loop that adds each pathspec independently, or
- `git add --ignore-errors`, though that masks genuinely wrong paths too.

Worth doing in its own commit with its own reasoning, because the current
explicit list is a deliberate safety property (it is what stops a plaintext
`board_*.json` being staged before its reveal) and a careless widening would
throw that away.

## 9. Self-test

```
python scripts/football/selftest_mercer.py
MERCER_KEEP=1 python scripts/football/selftest_mercer.py   # keep the pages
```

123 assertions against a fixture results store in a temp directory, each
section numbered to the requirement it proves. Run it after touching
`mercer.py` or `settle.py`.
