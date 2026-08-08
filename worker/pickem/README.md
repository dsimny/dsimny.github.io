# Pick'em Worker — launch checklist (~30 minutes, one time)

> CI note: every push touching `worker/` runs the **Worker check** workflow —
> JS syntax, schema SQL, and a no-credentials `wrangler deploy --dry-run`
> bundle. A green check means step 2 below can't fail on syntax or config;
> only the account-side pieces (login, D1 id, secrets) remain live-only.

The pilot ships **dark**: every pipeline step skips cleanly until the secrets
below exist, so this can sit unlaunched indefinitely. Doing these five steps
turns it on; removing the secrets turns it off.

## 1. Create the Discord application + bot
1. https://discord.com/developers/applications → **New Application** → name it
   "Open Ledger Pick'em".
2. On **General Information**, copy the **Public Key** (needed in step 3).
3. On **Bot**: click **Reset Token**, copy the **bot token** (needed in step 5).
   Disable "Public Bot" if you don't want others adding it.
4. On **Installation** (or OAuth2 → URL Generator): scope `bot`, permissions
   **Send Messages** + **Embed Links**. Open the generated URL, add the bot to
   the Open Ledger Sports server.
5. Right-click the channel the pick'em should live in (suggest **#free-pick**
   or a new **#pickem**) → Copy Channel ID (enable Developer Mode in Discord
   settings if you don't see it). Needed in step 5.

## 2. Deploy the Worker
```bash
cd worker/pickem
npx wrangler login
npx wrangler d1 create pickem        # paste the returned database_id into wrangler.toml
npx wrangler d1 execute pickem --file=schema.sql --remote
npx wrangler secret put DISCORD_PUBLIC_KEY   # from step 1.2
npx wrangler secret put READ_TOKEN           # invent one: e.g. `openssl rand -hex 32`
npx wrangler deploy                          # note the deployed URL
```

## 3. Point Discord at the Worker
Discord app → **General Information** → **Interactions Endpoint URL** →
`https://<deployed-url>/interactions` → Save. (Discord sends a signed PING;
the save only succeeds if the Worker verifies it — this is the deploy test.)

## 4. Nothing to register
The buttons are message components, not slash commands — no command
registration step exists.

## 5. GitHub repo settings (Settings → Secrets and variables → Actions)
Secrets:
- `DISCORD_BOT_TOKEN`  — from step 1.3
- `PICKEM_WORKER_URL`  — the deployed URL from step 2
- `PICKEM_READ_TOKEN`  — the same value as the Worker's READ_TOKEN secret

Variables:
- `PICKEM_CHANNEL_ID`  — from step 1.5

## 6. Verify
Trigger "Morning board" the next morning (or wait for cron): the featured-game
post with two buttons should appear in the channel. Press one — the ephemeral
confirmation should count your entry. Next morning's grade run posts results
and the leaderboard, and the site's ledger tab grows a "Community pick'em"
aggregate block.

## Channel rules post (pin something like this)
> 🎯 **Beat the Engine** — one featured game a day. Ride or fade; last press
> before first pitch counts. Graded overnight, standings every morning.
> Points and streaks only: no prizes, nothing staked, nothing owed. Your
> Discord name appears on leaderboards **in this server only** — the public
> site shows aggregate numbers, never names. 21+ · problem gambling help:
> 1-800-GAMBLER.
