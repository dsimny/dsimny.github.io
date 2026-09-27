# Mercer Live ML-1 — the operational path (T1 · T2 · T3)

Written 2026-09-27. Scope: how ML-1 evidence is kept, proven and published day
to day. **No change to the `ml1-v1` record schema, the providers, the join
rules, the credit guards or any non-goal.** ML-2 is not started. Nothing here
generates, implies or posts a play.

One amendment to a frozen artefact was unavoidable and is recorded as a dated
amendment in `MERCER_LIVE_ML1_ARCHITECTURE.md` section 20: the daily digest now
also fingerprints the run records (additive; every existing key unchanged).

A useful picture for the whole thing: the capture host is the notary's desk,
the private bucket is the vault, and the committed digest is the public
register. The register lists every sealed envelope by its fingerprint without
opening any of them; anyone later handed an envelope can check it against the
register, and the vault only accepts new envelopes — it never lets one be
swapped.

```
  ESPN (free) ─▶ capture host (Fly machine, always on, no inbound service)
                 host.py serve ─ one capture.py tick/min inside ET windows, --no-odds
                 │   /data/mercer_live  (Fly volume: append-only raw store)
                 │
                 ├─ every 10 min: rclone copy --immutable of SEALED files
                 │        ─▶ R2 bucket ol-mercer-live-raw   (private, bucket lock)
                 │
                 └─ daily ≥06:30 ET: archive.py build (closed day only, append-only
                          check) ─▶ R2 bucket ol-mercer-live-digests/digests/D.json
                                                       │ read-only token
  cron-job.org ─▶ mercer-live-digest.yml (GitHub Actions) ◀┘
                   digest_commit.py validates ─▶ commits ONE path:
                   data/mercer_live/digest/D.json   (public)
```

---

## 1. Findings from the audit (observed, on `main` at `0475172`)

| # | finding | evidence |
|---|---|---|
| F1 | No digest has ever been committed: `git ls-files data/mercer_live` lists only `README.md`. | repository |
| F2 | The smoke workflow writes `--digest` into `$RUNNER_TEMP`, commits nothing, and uploads the whole store as a 7-day artifact. | `mercer-live-smoke.yml` |
| F3 | **Same-hour collision.** Shards are named `<kind>_<ET hour>.jsonl`, so two capture stores that saw the same ET hour hold *different* files at the *same* path. The 2026-09-21 ET evidence came from three separate runners (35644644064, 35673309148, 35682762374 — the last two are 00:46Z and 03:20Z on 09-22 UTC, i.e. 20:46 and 23:20 ET on 09-21). Copying stores over each other, or digesting each and committing the last, silently loses the earlier observations. Reproduced as a negative control in `selftest_mercer_live_ops.py` [1]. | test |
| F4 | **The digest did not cover the run records.** It counted runs, errors, calls and credits *from* the run records but fingerprinted only the shards, so a run record (credit readings, error list, retry evidence) could change after its digest was committed with nothing to show it. This is the one amendment (section 20 of the architecture doc). | `store.py` |
| F5 | The digest carries a wall-clock `generated_at`, so two builds of unchanged evidence differ in bytes. Handled by comparing digests without that one key; the field is kept (schema). | `store.py` |
| F6 | The smoke job `cat`s every shard, run record and digest into the job log and prints every observed field value. | `mercer-live-smoke.yml` |
| F7 | On the capture host, credit bookings would land in the container's copy of `data/odds_credits.json`, never the repository's, so the frozen 5,000 floor would read a stale balance. Irrelevant while odds are off; a **prerequisite before any spend is authorised** (section 9). | `credits.py` + host layout |

## 2. T1 — durable digest path

**Choice: the capture host builds the digest; a separate GitHub Actions job
commits it.** Neither alternative is as safe:

| option | why not |
|---|---|
| host commits (git push / Contents API with a PAT) | the host would hold a credential that can write *any* path — `index.html`, the ledger, the commitment stores. A compromised capture box must not be able to touch the public record. GitHub PATs cannot be scoped to one path. |
| Actions builds the digest | Actions would need read access to the raw bucket, putting raw bytes on public-repo runners — exactly what T3 removes. |
| **host builds, Actions commits (chosen)** | the host holds raw and no repo write; Actions holds repo write and a *read-only* token for the digest bucket only. Each side has one power. |

Rules, all enforced in code and tested:

- **Closed days only.** A digest is built only after the ET day has ended plus
  10 minutes (`archive.closed`), so a committed digest never needs to grow
  because the day was still being written. The DST-end day is 25 hours and is
  handled (tested).
- **Multiple runs per day.** On the host every run writes into the one durable
  store, so the frozen append-only writer handles it natively. Evidence from
  any *other* store (ephemeral runners, the rescued artifacts) enters through
  `archive.py ingest`, which merges at the record level through `Store.append`:
  observation ids kept, duplicates refused and counted, nothing rewritten, a
  run-record conflict or an unparseable source line refused before any write.
- **Accurate proof.** `archive.verify` re-hashes every shard and run record,
  checks line counts, and reports any file for that date that the digest does
  *not* list. The host refuses to publish a digest that does not verify.
- **Append-only succession.** A committed digest may be replaced only by one
  that keeps every committed entry byte-for-byte and only adds entries
  (`archive.supersedes`). Anything else is `REFUSED-DIFFERS`, the run goes red,
  and it needs a human. Git history keeps every version. On the host,
  `verify_prefix` also tells an *appended* shard from a *rewritten* one so the
  refusal says which.
- **Exactly one path.** `digest_commit.py` computes the target
  (`data/mercer_live/digest/<date>.json`) from the validated date, never from
  the candidate; validates schema, keys, the date, the closed day, every shard
  and run path against an allowlist regex (no traversal), and canonical
  formatting. The workflow stages that one literal path, then asserts
  `git diff --cached --name-only` is exactly that path before committing, and
  uses the repository's standard push epilogue (byte-identical, asserted by
  `selftest_workflows.py` / `selftest_push_pattern.py`). No `git add -A`, no
  `index.html`/`feed.xml`, no `git pull`, no force.
- **Repeat execution.** Re-running the host publish with unchanged evidence
  uploads nothing; re-running the workflow commits nothing (`UNCHANGED`).
- **Missing digest is loud.** `MISSING` exits 1; the red run is the alert.

## 3. T2 — durable raw retention

### 3.1 Options evaluated (prices checked 2026-09-27)

| option | monthly cost | reliability | privacy | ops burden | verdict |
|---|---|---|---|---|---|
| Keep capture in GitHub Actions | $0 | scheduler measured 15–23 min late, once 9.5 h silent; no persistent disk | raw on public-repo runners | low | **rejected** (architecture §11 already rules it out) |
| **Fly.io machine + Fly volume → Cloudflare R2** | **≈ $3.50** | always-on process keeps time; volume + daily volume snapshots + off-host bucket | fully private; no inbound port | low — both accounts already exist (Open Ledger Play runs on Fly; the site's DNS is on Cloudflare) | **chosen** |
| Hetzner/DigitalOcean VPS + Backblaze B2 | ≈ $6–8 | good | good | moderate: OS patching, SSH hardening, two new vendor accounts | viable fallback |
| Cloudflare Workers cron + R2 | $0–5 | good | good | requires porting the frozen Python capture to JS | rejected (touches frozen code) |
| Private GitHub repo as the raw store | $0 | fine | fine | git is a poor append-only blob store; unbounded growth | rejected |

Prices: Fly `shared-cpu-1x` 512 MB **$1.94/mo**, volumes **$0.15/GB-mo**,
snapshots $0.08/GB-mo with the first 10 GB free; R2 standard storage
**$0.015/GB-mo with 10 GB-mo free**, 1 M Class A and 10 M Class B operations
free, egress free.

### 3.2 Sizing — a full season is comfortable

ESPN-only (the current authorisation): one game_state record per event per
tick. A college Saturday window is ~810 ticks × ~60–80 FBS events ≈ 60 k
records ≈ 90 MB uncompressed; an NFL Sunday is far smaller. **≈ 150–200 MB a
week, ≈ 2.5–3.5 GB for a full NFL + NCAAF season** including bowls and
playoffs. If odds are later authorised at one-minute cadence, the architecture
doc's measurement (~4 MB/NFL Sunday *compressed*) implies roughly 3–5× more
uncompressed. A **10 GB volume ($1.50/mo)** holds a season with headroom; R2
stays inside or just above its free 10 GB (a full 10 GB extra would be
$0.15/mo). Shards are stored uncompressed on purpose: the digest proves the
exact bytes, and a compressed copy would need a second proof.

### 3.3 Semantics

- **Append-only locally:** the frozen `Store` (never rewrites, refuses duplicate
  ids, atomic run records).
- **Off-host copy:** every 10 minutes the host lists *sealed* files — every run
  record (written once, atomically) and every shard whose ET wall hour ended
  ≥10 min ago (the DST-end 01 hour spans two real hours and is handled) — and
  runs `rclone copy --immutable --checksum`. `copy` never deletes; `--immutable`
  turns a changed file into an **error**, never an overwrite (tested with a
  fake object store). The open hour's shard is copied on the first pass after
  it closes, so a total host loss costs at most ~1 hour 10 minutes of capture.
- **Bucket lock:** an R2 bucket-lock rule on the raw bucket (no prefix, retention
  through the research window) makes deletion and overwrite impossible even
  with the host's read/write token — so a compromised host cannot destroy the
  off-host copy.
- **Third line:** Fly's daily volume snapshots, `snapshot_retention = 60` days.
- **Restore:** copy the day's prefix out of R2 into an empty directory and run
  `archive.py verify --data-dir <dir> --digest data/mercer_live/digest/<date>.json`.
  It passes only if every byte matches the committed digest and nothing
  unlisted is present (drill tested in [8], including the one-flipped-byte
  negative control).

### 3.4 The spend boundary

`host.py` always runs the capture with `--no-odds` and **removes
`ODDS_API_KEY` from the tick's environment**, unless `/data/odds-authorization.json`
exists, parses, names `authorized_by`, `authorized_on`, `expires_on` and
`reason`, and has not expired (ET). No code writes that file; `fly.toml`
configures no Odds API key. Deploying the host therefore **cannot spend a
credit**; turning spend on is a separate human act with an expiry date, and the
frozen floor and daily cap still apply on top.

## 4. The raw evidence already rescued

Hermes's rescue holds 5 artifacts (23 game_state observations, 23 run records),
verified. To bring them under the committed-digest regime (after the host and
buckets exist):

```
# on a trusted machine holding the rescued artifacts, each unzipped to its own dir
python scripts/mercer_live/archive.py ingest \
   --src rescued/35644644064 --src rescued/35673309148 --src rescued/35682762374 \
   --src rescued/36080139388 --src rescued/36080802566 --data-dir durable/
python scripts/mercer_live/archive.py build --data-dir durable/ --date 2026-09-21 --out 2026-09-21.json
python scripts/mercer_live/archive.py build --data-dir durable/ --date 2026-09-24 --out 2026-09-24.json
```

The first command should report `records_written` summing to 23 and
`run_records_copied` summing to 23 (per section 19's table; the in-run retry
adds none). Then copy `durable/raw/…` to the raw bucket and the two digests to
`digests/`, and dispatch the digest workflow for each date. The original
artifact bytes stay exactly where Hermes put them, with their rescue manifest.

## 5. T3 — what a public repository's Actions output exposes

Verified 2026-09-27 against run 36080139388 (job 107900088515), from two
vantage points that were checked to be unauthenticated:
- a browser session whose `api.github.com/rate_limit` read `core.limit = 60`,
  `graphql.limit = 0` — the anonymous quota;
- an external fetcher with no GitHub session.

| surface | unauthenticated | any signed-in GitHub account |
|---|---|---|
| run list, run metadata, artifact names/sizes/expiry (API) | **visible** (listed 5 smoke runs and the artifact `mercer-live-smoke-36080139388`, 8,002 B) | visible |
| job log, web UI | **no** — page shows "Sign in to view logs"; no log line rendered | **yes** (GitHub docs: must be logged in, read access; a public repo grants read to everyone) |
| job log, API (`/actions/jobs/{id}/logs`, `/actions/runs/{id}/logs`) | **no** — `403 "Must have admin rights to Repository."` | yes, per docs |
| step summary | **no** — not rendered on the anonymous run page; the check-run API's `output.summary` is `null` | yes (rendered on the run page) |
| raw observation content | **no** | **yes** — via the job log (`cat` of every shard) and via the artifact |
| artifact download | **no** — web download URL returns 404 anonymously | **yes** — docs: "People who are signed into GitHub and have read access to a repository can download workflow artifacts." |

Not tested: a signed-in download by a *second* account (no second account was
available); the signed-in row rests on GitHub's documentation. The anonymous
API artifact-zip URL was not requested because a success would have downloaded
a file onto Daniel's machine; the web download URL's 404 was used instead.

**Answer: raw observation content is not reachable anonymously, but it is
reachable by anyone who creates a free GitHub account.** For a repository whose
raw stream is private by design, that is public exposure. Content exposed so
far is ESPN game state only (no Odds API call was ever made), in the logs of
the five runs above and in their artifacts until they expire (2026-09-28 to
2026-10-02).

**Remediation (shipped):** the smoke log now prints only `evidence.py` output —
field-presence counts, per-field change counts between consecutive
observations, distinct ids/run ids/observed_at, state histogram, error and
duplicate counts, digest shape — and no value (a canary placed in every value
position of the fixtures never reaches the output; tested). The step summary
carries counts only. The artifact is uploaded **only** as an `age`-encrypted
tarball to a public key held in the repository *variable*
`ML1_ARTIFACT_AGE_RECIPIENT`; with the variable unset, nothing raw is uploaded
at all and the run says so. The retry proof is unchanged.

**Not done, needs Daniel:** deleting the five existing runs' logs (and the
artifacts before they expire). They are cited as freeze evidence in
architecture section 19, and the raw content is preserved in the rescue, so
deletion is a judgement between "evidence trail" and "exposure"; it is
irreversible and is Daniel's call (section 9).

## 6. Files

| file | role |
|---|---|
| `scripts/mercer_live/store.py` | digest adds `run_records` (amendment, §20) |
| `scripts/mercer_live/archive.py` | ingest, verify, verify_prefix, supersedes, sealed, closed, build |
| `scripts/mercer_live/digest_commit.py` | the one repository write, run in Actions |
| `scripts/mercer_live/evidence.py` | value-free smoke evidence |
| `scripts/mercer_live/host.py`, `host_windows.json` | capture-host supervisor, spend boundary, backup, publish |
| `mercer-live-host/Dockerfile`, `mercer-live-host/fly.toml` | host image and machine definition (not deployed) |
| `.github/workflows/mercer-live-digest.yml` | commits one digest per dispatch |
| `.github/workflows/mercer-live-smoke.yml` | T3 log/artifact hygiene |
| `scripts/mercer_live/selftest_mercer_live_ops.py` | the operational-path suite (182 checks) |

## 7. Operating cost

| item | monthly |
|---|---|
| Fly `shared-cpu-1x` 512 MB, always on | $1.94 |
| Fly volume 10 GB | $1.50 |
| Fly volume snapshots (≤10 GB) | $0 |
| Cloudflare R2 (≤10 GB, ops well inside free tier) | $0 (≈$0.15 per extra 10 GB) |
| GitHub Actions (public repo), cron-job.org | $0 |
| Odds API | **0 credits** until separately authorised |
| **total** | **≈ $3.50/month** |

## 8. Deployment — exact steps (each needs Daniel)

Run these from a terminal on your own machine. Never paste a key into chat or
into a file in this repository.

1. **R2 buckets** (Cloudflare dashboard → R2):
   create `ol-mercer-live-raw` and `ol-mercer-live-digests`, both private (no
   public access, no custom domain). On `ol-mercer-live-raw` add a **bucket lock
   rule**, no prefix, retention **until 2028-03-01** (the 2026 season plus the
   2027 season as a research window — change it if you want a different
   window).
2. **Two R2 API tokens** (R2 → Manage API tokens):
   - *host*: Object Read & Write, **applied to the two buckets only**;
   - *digest-reader*: **Object Read only**, applied to `ol-mercer-live-digests` only.
3. **Fly app + volume** (from the repository root):
   ```
   fly apps create ol-mercer-live-ml1
   fly volumes create ml1_data --app ol-mercer-live-ml1 --region iad --size 10
   fly secrets set --app ol-mercer-live-ml1 \
       RCLONE_CONFIG_ML1R2_ACCESS_KEY_ID=<host token id> \
       RCLONE_CONFIG_ML1R2_SECRET_ACCESS_KEY=<host token secret> \
       RCLONE_CONFIG_ML1R2_ENDPOINT=https://<account id>.r2.cloudflarestorage.com
   fly deploy --config mercer-live-host/fly.toml --dockerfile mercer-live-host/Dockerfile \
       --build-arg DEPLOYMENT_COMMIT=$(git rev-parse --short HEAD)
   fly ssh console --app ol-mercer-live-ml1 -C "python scripts/mercer_live/host.py status"
   ```
   `status` must read `odds=off (no authorisation file…)`, `remote_raw=set`,
   `remote_digests=set`. Do **not** set `ODDS_API_KEY`.
4. **GitHub** (Settings → Secrets and variables → Actions):
   secrets `ML1_DIGEST_R2_ACCESS_KEY_ID`, `ML1_DIGEST_R2_SECRET_ACCESS_KEY`
   (the *digest-reader* token); variables `ML1_R2_ENDPOINT`
   (`https://<account id>.r2.cloudflarestorage.com`) and
   `ML1_DIGEST_BUCKET` (`ol-mercer-live-digests`).
5. **Artifact key for the smoke workflow** (optional, only if you want the raw
   smoke store kept): on your machine `age-keygen -o ml1-smoke.key` — keep that
   file private and backed up — and put the printed `age1…` **public** key in the
   variable `ML1_ARTIFACT_AGE_RECIPIENT`.
6. **cron-job.org**: a daily job at **07:05 America/New_York** POSTing to
   `…/actions/workflows/mercer-live-digest.yml/dispatches` with
   `{"ref":"main"}` — the same PAT and shape as the existing jobs.
7. **Backfill + restore drill** (section 4), then dispatch the digest workflow
   with `date=2026-09-21` and `date=2026-09-24`.
8. **After the first live window**: `fly ssh console … status`, check the
   bucket holds the day's sealed files, let the 06:30 publish and 07:05 commit
   run, then do one restore drill from R2 alone.

## 9. Decisions only Daniel can make

1. Approve the hosting choice (Fly + R2, ≈$3.50/mo) or pick the VPS + B2 fallback.
2. The research window → the R2 bucket-lock retention date.
3. Whether to delete the five public smoke runs' logs and artifacts (irreversible).
4. Whether committed digests (public: per-shard hashes, counts, observed_at
   spans, credit totals) are acceptable to publish — they carry no observed
   value.
5. **Before any Odds API spend:** how the host's credit readings reach
   `data/odds_credits.json` (F7) — and then the authorisation file itself
   (who, until when, why).
