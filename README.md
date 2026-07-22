# Open Ledger Sports — deploy kit

Every pick on the record. Every rule in public.

A fully automated MLB picks site: every morning it fetches live data, runs
10,000 Monte Carlo simulations per game, applies eight risk circuit breakers,
prices everything against market lines, and publishes the board. Every night
it grades yesterday's picks against final scores and updates the append-only
public ledger. No hands required.

## Your 15 minutes of setup

1. **Create the repo.** On github.com: New repository → name it `openledger`
   (public) → create. Upload everything in this folder (drag-and-drop works:
   "uploading an existing file"), or `git push` it if you're comfortable
   with git. Make sure the `.github` folder comes along — it's the automation.

2. **Turn on GitHub Pages.** Repo → Settings → Pages → Source: "Deploy from a
   branch" → Branch: `main`, folder `/ (root)` → Save. Your site is now live at
   `https://<your-username>.github.io/openledger/`.

3. **Allow the bots to commit.** Repo → Settings → Actions → General →
   Workflow permissions → select **"Read and write permissions"** → Save.
   (This lets the daily runs commit the board and ledger back to the repo.)

4. **First run.** Repo → Actions tab → "Morning board" → Run workflow.
   Two minutes later the site has today's board. From tomorrow it runs itself:
   board at 15:10 UTC (~11 AM ET), grading at 08:10 UTC (~4 AM ET).

5. **Optional — live odds.** Get a free key at the-odds-api.com (500
   credits/month free; $30/mo for real volume). Repo → Settings → Secrets and
   variables → Actions → New repository secret → name `ODDS_API_KEY`, paste the
   key. Without it the board still publishes, but without edge/EV/Kelly and
   with Rules 2/8 inactive — the site says so honestly on every card.

6. **Optional — custom domain.** Buy openledgersports.com, then Settings →
   Pages → Custom domain, and add the DNS records GitHub shows you.

7. **Optional — Discord.** In your Discord server: channel → Edit channel →
   Integrations → Webhooks → New Webhook → Copy Webhook URL. Save it as repo
   secret `DISCORD_WEBHOOK_URL`. The morning run then posts the Free Pick of
   the Day (full embed: play, confidence, edge, breaker log) and the nightly
   run posts the graded results and running ledger — wins and losses alike.
   Also set a repo *variable* `SITE_URL` (Settings → Secrets and variables →
   Actions → Variables) to your Pages URL so posts link back to the site.
   No webhook set? The posts are skipped; the board is unaffected.

## How it works

```
morning-board.yml (daily 15:10 UTC)
  fetch_data.py   → data/snapshot_<date>.json   (MLB Stats API + The Odds API)
  engine.py       → data/board_<date>.json      (10k sims/game + breakers)
  build_site.py   → index.html                  (the site, incl. auto-written analysis)
  post_discord.py → free-pick embed to Discord  (optional)

grade-ledger.yml (daily 08:10 UTC)
  grade.py        → data/ledger.json            (final scores → W/L/VOID, units, ROI)
  build_site.py   → index.html                  (ledger tab refreshed)
  post_discord.py → results recap to Discord    (optional)
```

The ledger is append-only: entries are written once, never edited, and the
aggregates (record, units, ROI) are recomputed from the full history every
night. The daily seed makes every board reproducible.

## House rules (encoded, not aspirational)

- The free pick is a clean mid-board play, never the headliner.
- No allocation under a 2-point edge; Rule 8 holds any pick where model and
  de-vigged market disagree by 12+ points.
- Daily exposure capped at 10u; sizing = min(tier, quarter-Kelly).
- Publish scratches, leans, and losing days as prominently as wins.
- Never claim an automated check that isn't automated.

## Roadmap

- Statcast feeds to automate Rules 3/5/6 (velocity/spin trends, road wOBA)
- Closing-line-value (CLV) tracking once the odds feed logs opening vs closing
- Third-party record verification (Pikkit / Juice Reel)
- NRFI market (activates the two dormant breakers)
