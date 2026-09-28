# ML-1-OPS — Mercer Live operational evidence package

| | |
|---|---|
| Package | **ML-1-OPS**, `mercer-live-ml1-ops-v1.0` |
| Manifest schema | `ml1-ops-manifest-v1` |
| Code | `scripts/mercer_live_ops/` |
| Suite | `scripts/mercer_live_ops/selftest_mercer_live_ops.py` |
| Status | built, tested, **not deployed**; approved by Daniel 2026-09-28 as a separate package |

**`mercer-live-ml1-v1.0` remains frozen and unchanged.** ML-1-OPS does not
touch its record schema, record kinds, canonical ids, join rules, timestamp or
phase semantics, credit rules, store, or the `ml1-v1` digest. It *reads* frozen
ML-1 output and adds operational plumbing around it: durable storage,
archival, restore, evidence, the capture host, and digest publication.

**ML-1-OPS is not ML-2.** It contains no model, pick, probability, edge, unit,
signal or recommendation logic (its suite asserts this by name), posts nothing
to Discord, and spends no Odds API credit unless a separate, expiring
authorisation file exists on the capture host.

**Why a separate package.** A first version (commit `82edc05`, kept in history
as the rejected approach) added a `run_records` field to the frozen digest and
called it a dated amendment. The freeze allows amendments only for a live
defect in the capture path; adding a field is a new package. That change was
reverted, and the integrity it provided now lives in the companion manifest.

A useful picture: the capture host is the notary's desk, the private bucket is
the vault, and the committed digest + manifest are the public register. The
register lists every sealed envelope by fingerprint without opening any; the
vault only accepts new envelopes and never lets one be swapped.

```
  ESPN (free) ─▶ capture host (Fly machine, always on, no inbound service)
                 scripts/mercer_live_ops/host.py serve
                 │   runs the FROZEN scripts/mercer_live/capture.py, --no-odds, inside ET windows
                 │   /data/mercer_live  (Fly volume: the frozen append-only raw store)
                 │
                 ├─ every 10 min: rclone copy --immutable of SEALED files
                 │        ─▶ R2 bucket ol-mercer-live-raw   (private, bucket lock)
                 │
                 └─ daily ≥06:30 ET, closed day only: archive.py build
                          ─▶ R2 ol-mercer-live-digests/  digests/D.json + manifests/D.json
                                                       │ read-only token
  cron-job.org ─▶ mercer-live-digest.yml (GitHub Actions) ◀┘
                   digest_commit.py validates the PAIR ─▶ ONE commit, TWO paths:
                   data/mercer_live/digest/D.json    (frozen ml1-v1, unchanged bytes)
                   data/mercer_live/manifest/D.json  (ml1-ops-manifest-v1)
```

---

## 1. Findings from the audit (observed, on `main` at `0475172`)

| # | finding | evidence |
|---|---|---|
| F1 | No digest has ever been committed: `git ls-files data/mercer_live` lists only `README.md`. | repository |
| F2 | The smoke workflow writes `--digest` into `$RUNNER_TEMP`, commits nothing, and uploads the whole store as a 7-day artifact. | `mercer-live-smoke.yml` |
| F3 | **Same-hour collision.** Shards are named `<kind>_<ET hour>.jsonl`, so two capture stores that saw the same ET hour hold *different* files at the *same* path. The 2026-09-21 ET evidence came from three separate runners (35644644064, 35673309148, 35682762374). Copying stores over each other, or digesting each and committing the last, silently loses the earlier observations. Reproduced as a negative control in the suite, group [1]. | test |
| F4 | **The frozen digest does not fingerprint the run records.** It counts runs, errors, calls and credits *from* them, but a run record could change after its digest was committed with nothing to show it. The suite proves this as a negative control ([3]). Covered by the ML-1-OPS manifest, not by changing the digest. | `store.py` |
| F5 | The frozen digest carries a wall-clock `generated_at`, so two builds of unchanged evidence differ in bytes. ML-1-OPS therefore reuses the published digest bytes when the evidence is unchanged. | `store.py` |
| F6 | The smoke job `cat`s every shard, run record and digest into the job log and prints every observed field value. | `mercer-live-smoke.yml` |
| F7 | On the capture host, credit bookings would land in the container's copy of `data/odds_credits.json`, never the repository's, so the frozen 5,000 floor would read a stale balance. Irrelevant while odds are off; a **prerequisite before any spend is authorised**. | `credits.py` + host layout |

## 2. T1 — the digest + manifest pair

### 2.1 The companion manifest, `ml1-ops-manifest-v1`

`data/mercer_live/manifest/<ET date>.json`, keys exactly:

| key | content |
|---|---|
| `schema_version` | `"ml1-ops-manifest-v1"` |
| `package` | `"mercer-live-ml1-ops-v1.0"` |
| `et_date` | the ET date |
| `generated_at` | UTC build time |
| `ml1_digest` | `{path: "data/mercer_live/digest/<date>.json", sha256, bytes, schema_version: "ml1-v1"}` — the SHA-256 of the **exact bytes** of that day's frozen digest |
| `shards` | every observation shard: `{path, sport, kind, sha256, bytes, lines, parsed}` |
| `run_records` | every run record: `{path, sha256, bytes}` |
| `counts` | shards, run_records, shard_bytes, shard_lines, shard_records, run_record_bytes (must equal the entries) |
| `_note` | a fixed description |

It holds paths, sizes, counts, hashes, dates and version labels only — no
observed value, provider payload, event id, book, price or secret. The suite
checks every string in a built manifest against that allowlist and plants a
canary in every observed value to prove none leaks ([12]).

### 2.2 Evidence binding (one-way, no circle)

```
manifest.ml1_digest.sha256  ──▶  SHA-256(frozen digest file bytes)
frozen digest               ──▶  (nothing; it is unchanged ml1-v1 and never names the manifest)
git commit                  ──▶  both files, together — the durable pairing
```

Cross-checks on every pair (`archive.check_pair`): the manifest's digest
hash/bytes match the digest bytes; both name the same date; both list the same
shard set with identical sha256/bytes/lines/records; the manifest lists at
least as many run records as the digest counted.

### 2.3 Who builds and who commits

**The capture host builds the pair; a separate GitHub Actions job commits it.**

| option | why not |
|---|---|
| host commits (git push / Contents API with a PAT) | the host would hold a credential that can write *any* path — `index.html`, the ledger, the commitment stores. GitHub PATs cannot be scoped to one path. |
| Actions builds the pair | Actions would need read access to the raw bucket, putting raw bytes on public-repo runners — exactly what T3 removes. |
| **host builds, Actions commits (chosen)** | the host holds raw and no repo write; Actions holds repo write and a *read-only* token for the digest bucket only. |

### 2.4 Rules (all enforced in code and tested)

- **Closed days only** (`archive.closed`): ET day ended + 10 minutes; the
  25-hour DST-end day is handled.
- **Multiple runs per day**: on the host every run writes into the one durable
  frozen store. Evidence from other stores (ephemeral runners, the rescued
  artifacts) enters through `archive.py ingest`, record-level through the frozen
  `Store.append`: ids kept, duplicates refused and counted, nothing rewritten,
  a run-record conflict or unparseable source line refused before any write.
- **Grow, never change.** A committed pair is superseded only by a pair that
  keeps every committed shard and run-record entry byte-for-byte and adds new
  evidence. Refused: deletion, replacement, hash change, shrinking, a count
  going down, rewriting a committed fingerprint, and a manifest bound to a
  different digest. On the host, `verify_prefix` also names an *appended* vs a
  *rewritten* shard; both still need a human.
- **Idempotent.** Unchanged evidence reuses the published digest bytes and
  manifest, so re-running publication uploads nothing and the workflow commits
  nothing (`UNCHANGED`).
- **Exactly two paths, one commit.** `digest_commit.py` computes both targets
  from the validated date, never from the candidates, validates both, checks
  the pairing, and writes both or neither. The workflow stages those two
  literal paths, asserts the index holds exactly that pair, commits once, and
  uses the repository's standard push epilogue. No `git add -A`, no
  `index.html`/`feed.xml`/ledger, no `git pull`, no force.
- **Missing is loud**: either candidate absent → `MISSING`, exit 1.

### 2.5 Restore verification

`archive.py restore-verify --data-dir DIR --digest F --manifest F` succeeds only if:

1. the manifest is bound to exactly these digest bytes;
2. the frozen digest independently verifies every shard it covers;
3. every shard **and every run record** matches the manifest;
4. no shard or run record for that date exists that neither lists.

A modified run record fails restore even though the frozen digest alone cannot
detect it (proven by negative control).

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
  `scripts/mercer_live_ops/archive.py restore-verify --data-dir <dir>
  --digest data/mercer_live/digest/<date>.json --manifest data/mercer_live/manifest/<date>.json`
  (section 2.5; drill tested in [8], including the one-flipped-byte negative
  control on a run record).

### 3.4 The spend boundary

`scripts/mercer_live_ops/host.py` always runs the capture with `--no-odds` and **removes
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
python scripts/mercer_live_ops/archive.py ingest \
   --src rescued/35644644064 --src rescued/35673309148 --src rescued/35682762374 \
   --src rescued/36080139388 --src rescued/36080802566 --data-dir durable/
for D in 2026-09-21 2026-09-24; do
  python scripts/mercer_live_ops/archive.py build --data-dir durable/ --date $D \
      --out-digest digest/$D.json --out-manifest manifest/$D.json
  python scripts/mercer_live_ops/archive.py restore-verify --data-dir durable/ \
      --digest digest/$D.json --manifest manifest/$D.json
done
```

The first command should report `records_written` summing to 23 and
`run_records_copied` summing to 23 (per section 19's table; the in-run retry
adds none). Then copy `durable/raw/…` to the raw bucket, the digests to
`digests/` and the manifests to `manifests/`, and dispatch the digest workflow
for each date. The original
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

**Remediation (in ML-1-OPS):** the smoke log now prints only `scripts/mercer_live_ops/evidence.py` output —
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
ML-1 architecture section 19, and the raw content is preserved in the rescue, so
deletion is a judgement between "evidence trail" and "exposure"; it is
irreversible and is Daniel's call (section 9).

## 6. Files

| file | role |
|---|---|
| `scripts/mercer_live_ops/archive.py` | ingest, manifest build, pairing, supersede, verify_prefix, restore-verify, sealed, closed |
| `scripts/mercer_live_ops/digest_commit.py` | the one two-file repository write, run in Actions |
| `scripts/mercer_live_ops/evidence.py` | value-free smoke evidence |
| `scripts/mercer_live_ops/host.py`, `host_windows.json` | capture-host supervisor, spend boundary, backup, publish |
| `scripts/mercer_live_ops/selftest_mercer_live_ops.py` | the ML-1-OPS suite |
| `mercer-live-host/Dockerfile`, `mercer-live-host/fly.toml` | host image and machine definition (not deployed) |
| `.github/workflows/mercer-live-digest.yml` | commits one digest + manifest pair per dispatch |
| `.github/workflows/mercer-live-smoke.yml` | T3 log/artifact hygiene |
| `.github/workflows/mercer-live-selftest.yml` | the gate also runs the ML-1-OPS suite |

Nothing under `scripts/mercer_live/` is modified.

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
   fly ssh console --app ol-mercer-live-ml1 -C "python scripts/mercer_live_ops/host.py status"
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
4. Whether the committed digests (frozen ML-1: per-shard hashes, counts,
   observed_at spans, credit totals) and manifests (hashes, sizes, counts) are
   acceptable to publish — neither carries an observed value.
5. **Before any Odds API spend:** how the host's credit readings reach
   `data/odds_credits.json` (F7) — and then the authorisation file itself
   (who, until when, why).
