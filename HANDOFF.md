# Open Ledger Sports — Handoff Package

_Snapshot for a fresh assistant (e.g. ChatGPT) to continue the distribution-channels
rollout. Written 2026-07-25. No secrets are in this file or the repo._

## How to use this package
1. Read this file (the live status + resume steps).
2. Read **CLAUDE.md** — the full project brief (architecture, house rules, engine, config). It is the source of truth; this file only adds the *current rollout state* that isn't in it yet.
3. Skim **scripts/** and **.github/workflows/** for the code.

**What an AI assistant can and can't do here:** you can reason about code, draft edits, write the exact API calls, and guide the human through the Meta / X / GitHub consoles. You **cannot** run the pipeline, read or set GitHub secrets, or see live tokens — those steps happen in the browser/consoles by the human (Daniel). Don't ask for secret values; hand back instructions instead.

## The project in 30 seconds
MLB picks site with a Monte Carlo engine and an **append-only public ledger** — the brand is radical transparency ("the aquarium, not the magic show"). Daily GitHub Actions pipeline: fetch data → run engine (encrypted board + SHA-256 commitment) → build single-file `index.html` → post free pick to Discord/RSS/email, held plays to members. Nightly: grade vs final scores, reveal the board, post the record. Full detail in CLAUDE.md.

- Public repo: **github.com/dsimny/dsimny.github.io** (GitHub Pages serves `index.html`)
- Live site: **https://openledgersports.com**
- Discord: **discord.gg/8EVazMtydq**

## Current focus — four distribution channels

| Channel | State | What's left |
|---|---|---|
| **Discord** | ✅ Live (board + recap posting 200) | Nothing |
| **Email** (Resend) | 🟡 Code live, not sending | Set repo **variable** `RESEND_SEGMENT_ID` to the real all-subscribers segment id, add a contact, trigger Morning board, confirm inbox |
| **X / Twitter** | 🟡 Auth works, blocked on billing | Fund X API credits (see Parked, below) |
| **Facebook** | 🔵 **IN PROGRESS — resume here** | Generate a long-lived Page token, add to GitHub, test |

Live status is machine-readable in **`data/post_status.json`** (per-channel `posted` / `failed` / `no_config` with HTTP codes). Latest: `facebook no_config`, `x failed 402`, email not configured.

---

## ▶ RESUME HERE: Facebook page-posting token

**Goal:** obtain a **long-lived Page access token** + the **Page ID**, then set them in GitHub as `FB_PAGE_ACCESS_TOKEN` (secret) and `FB_PAGE_ID` (variable). The posting code (`scripts/post_social.py facebook`) and the workflow step (`grade-ledger.yml`) already exist and are pushed — this is pure credential wiring.

**Already done:**
- Meta app **"Open Ledger Sports"** created — **App ID `985283234524727`** — with the **"Manage everything on your Page"** use case (this bundles `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`).
- The **Open Ledger Sports Facebook Page** exists and the signed-in account is its admin.
- We are in the **Graph API Explorer** (`developers.facebook.com/tools/explorer`).

**Key gotcha:** a Page token from the basic tools is **short-lived (~1 hour)** — useless for a daily cron. We need a **long-lived** one (those effectively don't expire). Posting to your **own** Page as admin needs **no App Review**.

**Steps:**
1. **Graph API Explorer** → Meta App = *Open Ledger Sports* → click **Generate Access Token** → in the popup, authorize and **check the Open Ledger Sports Page**. Do NOT use "Add a Permission" (it shows "not configurable"; the perms ride along from the use case). Result: a short-lived **user** token.
2. **Get the App Secret** — App dashboard → **App settings → Basic** → copy **App Secret** (and confirm App ID `985283234524727`).
3. **Exchange for a long-lived user token** (open this URL in the browser, substituting values):
   ```
   https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=985283234524727&client_secret=APP_SECRET&fb_exchange_token=SHORT_LIVED_USER_TOKEN
   ```
   (Or: Tools → **Access Token Debugger** → paste the token → **Extend Access Token**.)
4. **Get the permanent Page token + Page ID** — with the long-lived user token:
   ```
   https://graph.facebook.com/v21.0/me/accounts?access_token=LONG_LIVED_USER_TOKEN
   ```
   The returned Page object's `access_token` is a **permanent** Page token (Page tokens derived from a long-lived user token don't expire); its `id` is the Page ID.
5. **(Optional) sanity-check** the Page token: `https://graph.facebook.com/v21.0/me?access_token=PAGE_TOKEN` should return the Page's name/id.
6. **Set in GitHub** — repo `dsimny/dsimny.github.io` → Settings → Secrets and variables → Actions:
   - **Secret** `FB_PAGE_ACCESS_TOKEN` = the permanent Page token
   - **Variable** `FB_PAGE_ID` = the Page ID
7. **Test** — Actions → **Grade ledger** → **Run workflow**. Then check `data/post_status.json` for `facebook: posted` (with the post id) or `failed` + reason. ⚠️ This also re-posts **one duplicate Discord recap** — `post_discord.py` is intentionally not idempotent. The Facebook post itself *is* idempotent, so re-runs won't double-post.

If it 403s: confirm the account is the Page admin and `pages_manage_posts` was granted; regenerate the token if needed.

---

## Parked items (not blocking Facebook)

**X — fund credits.** Auth is confirmed working (first live post returned `402 credits depleted` — authenticated, refused only for billing). X has **no free tier**; it's pay-per-use, credits bought in the console. Posting is **$0.015/request**, but **$0.200 if the post contains a URL** — so the X post deliberately carries **no link** (`build_x_text` in `post_social.py`); do not re-add it. To go live: **console.x.com → Billing → Credits**, buy credits, **set a spending limit** (leave auto-recharge off/capped). One post/day is negligible. Until funded, the nightly X step logs a harmless `failed 402`.

**Email — set the segment id.** Get the real **all-subscribers segment id** from Resend → **Segments** tab (NOT a code sample — the value tried earlier, `5e4d5e4d-...`, was the docs placeholder). Set it as repo **variable** `RESEND_SEGMENT_ID`, add yourself as a contact in that segment, trigger **Morning board**, confirm the pick lands in your inbox. Sending domain `send.openledgersports.com` is already verified; `RESEND_API_KEY` is set.

---

## What's already built (this rollout)
- **`scripts/send_email.py`** — daily free-pick email via Resend broadcast; body is the exact `feed_items.json` item (never premium). Idempotent using mode `"email"` in `post_status.json` (kept distinct from Discord's `"pick"` so it can't be clobbered). Wired into `morning-board.yml`.
- **`scripts/post_social.py`** — daily record to **X** (text, OAuth 1.0a, ≤280, no URL) and **Facebook** (Graph API `POST /{page-id}/feed`). Reads `data/ledger.json`; posts losses too; carries 21+/1-800-GAMBLER. Idempotent per platform. Wired into `grade-ledger.yml`.
- **`requirements.txt`** adds `requests-oauthlib` (X signing).
- Dry-run any builder without credentials, e.g. `python scripts/post_social.py x <date> --dry-run` or `python scripts/send_email.py pick <date> --dry-run`.

## Config reference (names only — never commit values)
- **Secrets:** `BOARD_ENCRYPTION_KEY` (required, unrecoverable if lost), `ODDS_API_KEY`, `DISCORD_WEBHOOK_URL` / `_MEMBERS` / `_LEDGER`, `RESEND_API_KEY`, `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_SECRET`, `FB_PAGE_ACCESS_TOKEN`.
- **Variables:** `SITE_URL`, `DISCORD_INVITE_URL`, `WHOP_CHECKOUT_URL`, `RESEND_SEGMENT_ID`, `EMAIL_FROM`, `FB_PAGE_ID`.

## Hard rules a new assistant MUST respect (from CLAUDE.md)
- **NEVER commit a locally built `index.html` / `feed.xml`.** CI rebuilds them from the day's (encrypted) board; committing a stale local build reverts the live free pick. Before committing, `git checkout -- index.html feed.xml`, then `git add` only the specific source files (not `-A`).
- **Ledger is append-only** — never edit or delete a graded entry; publish losses too.
- **Legal footer everywhere** — 21+, 1-800-GAMBLER, "analytics not a sportsbook", no-guarantee language. Don't remove or soften.
- **Discord poster is NOT idempotent** (re-runs re-post). Email + social **are** idempotent.
- **Dates are US Eastern** throughout.
- Don't re-add the site URL to the X post (13× cost).

## Environment notes (Daniel's machine)
- Working copy: `C:\Users\Desert\OneDrive\Documents\PROJECTS\CLAUDE\claude-code-projects\openledger-sports`
- `python` on PATH is a broken Windows Store stub — real interpreter: `C:\Users\Desert\AppData\Local\Programs\Python\Python312\python.exe`.
- Deps: `pip install -r requirements.txt` (numpy, requests, tzdata, cryptography, requests-oauthlib).
