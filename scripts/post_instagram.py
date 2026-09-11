#!/usr/bin/env python3
"""
Open Ledger Sports — Instagram recap caption + publishing transport.

PHASE 2A: the transport exists and is fully tested offline, but NOTHING calls it
yet. grade-ledger.yml is untouched, no credential is configured, and no Meta
request has ever been made from this repository. Wiring is Phase 2B.

TWO LEDGERS, NEVER MIXED. select_recap() picks the strategy; _qualified() and
_daily() build from disjoint keys and never reference each other's figures.
Exactly one running record appears in a caption.

WHY THE COPY IS IMPORTED, NOT RESTATED. LEGAL, DISCLOSURE_FB and
DISCLOSURE_FB_STAKED come from post_social.py, whose wording is guarded by 206
offline checks — that an allocated pick is never called paper-only, that "real
stake" never appears. A second copy here would drift.

Three deliberate differences from the Facebook post: no URL (Instagram renders
caption links as inert text, so the caption points at the profile bio instead —
which makes the bio link a launch prerequisite); no emoji (the vendored font
renders ✅ and ❌ as the same tofu box); and an explicit no-wager sentence on
every branch, since ps.LEGAL carries "Analytics, not betting advice" but not the
no-bets half that house rule 5 requires.

────────────────────────────────────────────────────────────────────────────
DUPLICATE PREVENTION IS ANCHORED IN META, NOT IN A LOCAL FILE.

Publishing happens AFTER the pipeline's commit step, so anything this module
writes locally may never reach the repository — a runner that dies between
media_publish and the next commit would leave no trace, and a naive retry would
post the same card twice.

So every caption carries a deterministic reference derived only from the settled
recap date and its strategy:

    OLS-QUAL-2026-05-01     OLS-DAILY-2026-06-01

and every run STARTS by reading recent Instagram media and looking for that
string. If it is already live, the media id is recovered, `posted` is recorded
and nothing is published. Meta's own feed is the authority; the status file is
advisory telemetry that makes operations legible, never the thing that decides.

The reference is appended OUTSIDE the caption trim ladder (see build_caption),
so no rung can drop it and it can appear exactly once.
────────────────────────────────────────────────────────────────────────────

NO NETWORK AT IMPORT. `requests` is imported inside _default_transport, which is
only built on the first real call. The suite asserts this against the AST.

NO TOKEN ANYWHERE. The token is passed as a request field and is never placed in
a log line, an exception message, a status row or a test fixture. _scrub()
redacts it from anything derived from a response before it is printed or stored.

Run:  python scripts/post_instagram.py caption   <YYYY-MM-DD>
      python scripts/post_instagram.py publish   <YYYY-MM-DD> --image-url URL
      python scripts/post_instagram.py reconcile   <YYYY-MM-DD>
      python scripts/post_instagram.py publish   <YYYY-MM-DD> --image-url URL --strict
      python scripts/post_instagram.py sync-status

--strict IS THE PRODUCTION MODE. Without it the CLI is a diagnostic tool and
exits 0 for anything that is not an outright error, which is convenient locally
and wrong in a pipeline: a run that published nothing because the credentials
were missing, or because the image never appeared, is a channel going quietly
dark, not a success. With --strict those become exit 1 so CI turns red.

    state            plain   --strict
    posted             0        0
    nothing_to_post    0        0      (a quiet night is not a failure)
    no_config          0        1
    pending_media      0        1      (after bounded reachability retries)
    failed             1        1      (always a failure, in either mode)

DURABILITY, STATED PLAINLY:

  * META'S PUBLISHED FEED IS THE DURABLE AUTHORITY for duplicate prevention.
    Every caption carries OLS-<KIND>-<DATE>; every run reads the feed for it
    before creating anything. Nothing local is trusted for that decision.

  * pending_publish and failed rows written AFTER the pipeline's push are LOST
    when the GitHub-hosted runner is discarded. They are operational breadcrumbs
    within a single job, not history.

  * LOSING THEM CANNOT CAUSE A DUPLICATE. A fresh runner with no status file at
    all behaves identically: it reads the feed, finds the reference, records
    posted and publishes nothing.

  * AN ORPHAN CONTAINER MAY BE ABANDONED. If a run creates a container and dies
    before publishing, the creation id is lost with the runner. Nothing
    republishes it; Meta expires unpublished containers after 24 hours. The next
    run builds a fresh one. The cost is one unused container against a 50-100/day
    quota — cheap insurance against the alternative, which is resuming an
    unverified container from unreliable local state.

  * sync-status is what makes the advisory history durable: run BEFORE the
    commit, it writes yesterday's confirmed publications into the tree so the
    existing `git add data/` carries them. The history lands one day in arrears
    and that is fine, because nothing depends on it.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import post_social as ps  # noqa: E402

# ------------------------------------------------------------------- copy --
CAPTION_LIMIT = 2200

BIO_LINE = "Full ledger — every pick, every result — at the link in our bio."
NO_WAGER = "Open Ledger Sports does not place or accept wagers."
_NO_WAGER_MARK = "does not place or accept wagers"

RESULT_WORD = {"WIN": "WIN", "LOSS": "LOSS", "VOID": "VOID"}
MAX_ROWS = 6

# The deterministic reference. Derived ONLY from the settled recap's date and
# strategy, so the same night always produces the same string on every machine
# and every rerun — which is what makes it usable as a remote idempotency key.
REF_PREFIX = "Ledger ref "
REF_KIND = {"qualified": "QUAL", "daily": "DAILY"}
REF_RE = re.compile(r"OLS-(QUAL|DAILY)-(\d{4}-\d{2}-\d{2})")


def ledger_ref(kind, date):
    """`OLS-QUAL-2026-05-01`. Raises on an unknown strategy rather than
    inventing a reference that reconciliation would never match."""
    if kind not in REF_KIND:
        raise ValueError(f"unknown recap kind {kind!r}")
    return f"OLS-{REF_KIND[kind]}-{date}"


def find_ref(text):
    """The reference inside a caption, or None. Used to match our own posts when
    reading back the live feed."""
    m = REF_RE.search(text or "")
    return m.group(0) if m else None


REF_KIND_BACK = {"QUAL": "qualified", "DAILY": "daily"}


def parse_ref(ref):
    """(kind, date) from a reference, or None if it is not one of ours.

    Stricter than find_ref on purpose. sync-status folds whatever it finds in a
    PUBLIC feed into local state, so it validates the calendar date rather than
    trusting the shape: `OLS-QUAL-2026-13-45` matches the regex and is not a
    date, and a caption is user-editable text that must never be able to write a
    junk row.
    """
    m = REF_RE.fullmatch((ref or "").strip())
    if not m:
        return None
    kind, date = REF_KIND_BACK[m.group(1)], m.group(2)
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None
    return kind, date


# --------------------------------------------------------------- caption --
def _entry_line(e, amount):
    """`WIN · Boston Red Sox ML (-134) — Final 4-2 (+0.75u)`

    Book names inside `pick` are kept verbatim: the price source is a fact about
    the graded entry and stating it is the point of an auditable ledger. Text
    attribution only — no bookmaker mark, logo or link anywhere.
    """
    word = RESULT_WORD.get(e.get("result", ""), "—")
    score = e.get("final_score") or "void"
    return f"{word} · {e.get('pick', '')} — Final {score} ({amount})"


def _rows(entries, amount_fn, cap):
    lines = [_entry_line(e, amount_fn(e)) for e in entries[:cap]]
    if len(entries) > cap:
        lines.append(f"+{len(entries) - cap} more in the ledger.")
    return lines


def _assemble(blocks):
    return "\n\n".join(b for b in blocks if b)


def _with_no_wager(body_blocks):
    joined = "\n".join(b for b in body_blocks if b)
    if _NO_WAGER_MARK in joined:
        return body_blocks
    return body_blocks + [NO_WAGER]


def _qualified(r, cap=MAX_ROWS, roi=True):
    day = ps._day_record(r["day_w"], r["day_l"], r["day_v"])
    running = ps._running(r) if roi else f"{r['record']} · {r['units_net']:+.2f}u net"
    blocks = [
        f"Open Ledger Sports — results for {r['nice']}",
        "\n".join(_rows(r["entries"], lambda e: f"{e['pnl']:+.2f}u", cap)),
        f"Day: {day} · {r['day_pnl']:+.2f}u\n"
        f"Running ledger (Qualified Plays): {running}",
    ]
    return _assemble(_with_no_wager(blocks + [ps.LEGAL]) + [BIO_LINE])


def _daily(r, cap=MAX_ROWS, roi=True, comparison=True):
    day = ps._day_record(r["day_w"], r["day_l"], r["day_v"])
    staked = r["staked_units"]

    if staked:
        # A human authorised an allocation, so the OFFICIAL allocated result
        # leads and the paper figure survives only as a labelled comparison.
        rows = _rows(r["entries"],
                     lambda e: f"{(e.get('pnl_staked') or 0):+.2f}u allocated", cap)
        net = r["staked_net"] if r["staked_net"] is not None else 0.0
        ledger = [
            f"Day: {day} · {r['staked_pnl']:+.2f}u on a {staked:.2f}u recorded allocation",
            f"Daily Pick ledger (recorded allocation): {r['record']} · {net:+.2f}u net",
        ]
        if comparison:
            ledger.append(
                f"Paper comparison, flat {ps._basis_str(r)}u basis "
                f"(not the recorded allocation): {ps._running_daily(r)}")
        disclosure = ps.DISCLOSURE_FB_STAKED.format(stake=f"{staked:.2f}")
    else:
        rows = _rows(r["entries"],
                     lambda e: f"{(e.get('pnl_paper') or 0):+.2f}u paper", cap)
        running = (ps._running_daily(r) if roi
                   else f"{r['record']} · {(r['paper_units_net'] or 0.0):+.2f}u")
        ledger = [
            f"Day: {day} · {r['day_pnl_paper']:+.2f}u paper",
            f"Daily Pick ledger (paper): {running}",
        ]
        disclosure = ps.DISCLOSURE_FB.format(basis=ps._basis_str(r))

    blocks = [
        f"Open Ledger Sports — Daily Pick result for {r['nice']}",
        "\n".join(rows),
        "\n".join(ledger),
        disclosure,
    ]
    return _assemble(_with_no_wager(blocks + [ps.LEGAL]) + [BIO_LINE])


def build_caption(date):
    """The caption for `date`, or None when nothing settled.

    TRIM ORDER IS A SAFETY PROPERTY. If the text ever exceeds 2200 characters the
    LEDGER DETAIL degrades first — paper comparison, then running ROI, then the
    number of result rows. The disclosure, the legal paragraph, the no-wager
    sentence and the bio line are never candidates at any rung.

    THE REFERENCE IS APPENDED OUTSIDE THE LADDER. No rung builds it, so no rung
    can drop it, and it cannot be emitted twice — the exactly-once and
    survives-every-trim guarantees are structural rather than tested-into-place.
    """
    kind, r = ps.select_recap(date)
    if kind is None:
        return None
    tail = REF_PREFIX + ledger_ref(kind, date)

    if kind == "qualified":
        ladder = [lambda: _qualified(r),
                  lambda: _qualified(r, roi=False),
                  lambda: _qualified(r, cap=3, roi=False),
                  lambda: _qualified(r, cap=1, roi=False)]
    else:
        ladder = [lambda: _daily(r),
                  lambda: _daily(r, comparison=False),
                  lambda: _daily(r, comparison=False, roi=False),
                  lambda: _daily(r, cap=3, comparison=False, roi=False),
                  lambda: _daily(r, cap=1, comparison=False, roi=False)]

    text = ladder[0]() + "\n\n" + tail
    for rung in ladder:
        text = rung() + "\n\n" + tail
        if len(text) <= CAPTION_LIMIT:
            return text
    return text


# ------------------------------------------------------------- transport --
GRAPH = "https://graph.facebook.com/v26.0"

ENV_IG_USER = "IG_USER_ID"
ENV_IG_TOKEN = "IG_ACCESS_TOKEN"

RECONCILE_LIMIT = 25          # >3 weeks at one post a day
POLL_ATTEMPTS = 10
POLL_SECONDS = 6
REDACTED = "«redacted»"

# Bounded retries on the image URL. raw.githubusercontent serves a blob as soon
# as the commit lands, so one attempt is almost always enough — but "almost" is
# doing work in a pipeline, and the alternative to retrying is a false
# pending_media that in strict mode turns the run red. Bounded, because an
# unbounded wait would hold a runner open on a genuinely missing file.
IMAGE_ATTEMPTS = 5
IMAGE_BACKOFF_SECONDS = 3

# Injectables. The suite replaces all three; production leaves them None and the
# real implementations are built lazily, so importing this module opens no socket
# and reads no credential.
TRANSPORT = None
SLEEP = None
STATUS_PATH = os.path.join(ROOT, "data", "instagram_status.json")
STATUS_KEEP = 120             # a dedicated file, so retention is generous


SECRET_MIN_LEN = 12          # shorter values are field names and ids, not tokens


def sanitize(text, params=None, data=None):
    """Redact every credential-length value we were asked to SEND out of a
    message we are about to keep.

    A pure function on purpose: the suite exercises it directly, so the
    redaction can be proven without importing `requests` or touching a socket.
    """
    out = "" if text is None else str(text)
    for v in list((params or {}).values()) + list((data or {}).values()):
        if isinstance(v, str) and len(v) >= SECRET_MIN_LEN:
            out = out.replace(v, REDACTED)
    return out[:300]


def _default_transport():
    """Built on first use, never at import. Returns (status, payload, snippet).

    IT NEVER RAISES, AND THAT IS A SECURITY PROPERTY, not a convenience.

    A `requests` network exception embeds the full request URL in its message:

        HTTPSConnectionPool(host=...): Max retries exceeded with url:
        /v26.0/<ig>/media?access_token=<THE ACTUAL TOKEN>

    The GET calls (feed read, container status) must carry the token as a query
    parameter, so an uncaught exception would print the live token into the job
    log — and grade-ledger.yml's alert job tails 20 lines of that log straight
    into a Discord channel. That is a token walking into a chat room.

    So every exception is caught here, every string value this call was handed is
    redacted out of the message, and the failure is returned as HTTP 0, which
    classify() treats as transient and therefore retryable next run.
    """
    import requests

    def call(method, url, params=None, data=None, timeout=30):
        try:
            r = requests.request(method, url, params=params, data=data,
                                 timeout=timeout)
        except Exception as exc:
            return 0, {}, sanitize(str(exc), params, data)
        try:
            payload = r.json()
        except Exception:
            payload = {}
        # A response can echo the request back, so it is scrubbed too.
        return r.status_code, payload, sanitize(r.text, params, data)
    return call


def _transport():
    global TRANSPORT
    if TRANSPORT is None:
        TRANSPORT = _default_transport()
    return TRANSPORT


def _sleep(seconds):
    if SLEEP is not None:
        return SLEEP(seconds)
    import time
    return time.sleep(seconds)


def _scrub(value, token=None):
    """Redact the token from anything derived from a request or response before
    it is printed or persisted. Everything that reaches a log line or a status
    row goes through here — that is the single choke point."""
    out = "" if value is None else str(value)
    if token:
        out = out.replace(token, REDACTED)
    return out


def config():
    """(ig_user_id, token) or None. Reading os.environ is confined to this one
    function so there is exactly one place a credential can enter."""
    ig = os.environ.get(ENV_IG_USER, "").strip()
    tok = os.environ.get(ENV_IG_TOKEN, "").strip()
    if not ig or not tok:
        return None
    return ig, tok


# ---------------------------------------------------------------- status --
def _load_status():
    if os.path.exists(STATUS_PATH):
        try:
            with open(STATUS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"version": 1, "posts": []}
    return {"version": 1, "posts": []}


def status_for(date):
    """The most recent row for a date, or None."""
    rows = [p for p in _load_status().get("posts", []) if p.get("date") == date]
    return rows[-1] if rows else None


def record(date, kind, ref, result, creation_id=None, media_id=None,
           http_status=None, detail="", token=None):
    """Append the outcome. ADVISORY ONLY — duplicate prevention is the remote
    feed, not this file. Never records a token; telemetry must never break a run.

    A `posted` row is only ever written by _finish_posted(), which requires a
    media id from Meta."""
    try:
        log = _load_status()
        log.setdefault("version", 1)
        log["posts"] = [p for p in log.get("posts", []) if p.get("date") != date]
        log["posts"].append({
            "date": date, "kind": kind, "ref": ref, "result": result,
            "creation_id": creation_id, "media_id": media_id,
            "http_status": http_status,
            "detail": _scrub(detail, token)[:300],
            "at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        log["posts"] = sorted(log["posts"],
                              key=lambda p: p["at_utc"])[-STATUS_KEEP:]
        os.makedirs(os.path.dirname(os.path.abspath(STATUS_PATH)), exist_ok=True)
        with open(STATUS_PATH, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:
        print(f"NOTE: could not record Instagram status: {_scrub(exc, token)}")


# ------------------------------------------------------------ Meta calls --
def classify(http_status, payload):
    """Map a Meta error onto a retry decision.

    token/permission are terminal — a retry cannot fix a revoked token or a
    missing scope, and hammering the endpoint just burns quota. rate/transient
    are retryable next run. Codes per Meta's error-handling reference.
    """
    err = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
    code, sub = err.get("code"), err.get("error_subcode")
    # HTTP 0 is _default_transport's signal that the request never completed —
    # DNS, TLS or a dropped connection. Retryable, and never a token problem.
    if http_status == 0:
        return "transient", code, sub
    if code == 190 or sub in (463, 467):
        return "token", code, sub
    if code in (10, 200, 803) or (isinstance(code, int) and 200 <= code <= 299):
        return "permission", code, sub
    if code in (4, 17, 32, 613):
        return "rate", code, sub
    if code in (1, 2) or (isinstance(http_status, int) and http_status >= 500):
        return "transient", code, sub
    return "fatal", code, sub


def _err_detail(payload, snippet, token):
    err = (payload or {}).get("error", {}) if isinstance(payload, dict) else {}
    msg = err.get("message") or snippet or ""
    return _scrub(msg, token)


def image_reachable(url, transport=None, attempts=None):
    """HEAD the public image, with BOUNDED retries.

    A container is NEVER created against an unreachable URL — Meta would return
    ERROR and burn the container. The retry exists because a false negative here
    turns a healthy run red under --strict, and the bound exists because a real
    missing file must not hold the runner open.
    """
    t = transport or _transport()
    n = IMAGE_ATTEMPTS if attempts is None else attempts
    status = None
    for i in range(max(1, n)):
        try:
            status, _, _ = t("HEAD", url)
        except Exception:
            status = None
        if isinstance(status, int) and 200 <= status < 300:
            return True, status
        if i < max(1, n) - 1:
            _sleep(IMAGE_BACKOFF_SECONDS)
    return False, status


def recent_media(ig_user, token, limit=RECONCILE_LIMIT, transport=None):
    """Recent published media with captions. THE RECONCILIATION READ."""
    t = transport or _transport()
    status, payload, snippet = t(
        "GET", f"{GRAPH}/{ig_user}/media",
        params={"fields": "id,caption,timestamp", "limit": limit,
                "access_token": token})
    if not (isinstance(status, int) and 200 <= status < 300):
        return None, status, payload, snippet
    return (payload or {}).get("data", []), status, payload, snippet


def find_published(ig_user, token, ref, transport=None):
    """(media_id, http_status, payload, snippet) — media_id None when absent.
    Returns ('__ERROR__', ...) sentinel semantics via data=None on failure."""
    data, status, payload, snippet = recent_media(ig_user, token,
                                                  transport=transport)
    if data is None:
        return None, status, payload, snippet
    for m in data:
        if find_ref(m.get("caption") or "") == ref:
            return m.get("id"), status, payload, snippet
    return None, status, payload, snippet


def create_container(ig_user, token, image_url, caption, transport=None):
    t = transport or _transport()
    return t("POST", f"{GRAPH}/{ig_user}/media",
             data={"image_url": image_url, "caption": caption,
                   "access_token": token})


def container_status(creation_id, token, transport=None):
    t = transport or _transport()
    status, payload, snippet = t("GET", f"{GRAPH}/{creation_id}",
                                 params={"fields": "status_code",
                                         "access_token": token})
    code = (payload or {}).get("status_code") if isinstance(payload, dict) else None
    return code, status, payload, snippet


def media_publish(ig_user, token, creation_id, transport=None):
    t = transport or _transport()
    return t("POST", f"{GRAPH}/{ig_user}/media_publish",
             data={"creation_id": creation_id, "access_token": token})


# ------------------------------------------------------------ orchestration --
class Result:
    """What a run did, for the CLI and the tests. Carries no token."""

    def __init__(self, state, detail="", media_id=None, creation_id=None,
                 http_status=None, calls=0):
        self.state, self.detail = state, detail
        self.media_id, self.creation_id = media_id, creation_id
        self.http_status, self.calls = http_status, calls

    def __repr__(self):
        return (f"Result({self.state!r}, media_id={self.media_id!r}, "
                f"creation_id={self.creation_id!r}, calls={self.calls})")


# ---------------------------------------------------------------- exit codes --
# States that mean "nothing is wrong, there was simply nothing to do".
_ALWAYS_OK = ("posted", "nothing_to_post")
# States that are tolerable while poking at this locally, but are real failures
# in the pipeline: an unconfigured or image-less production run published
# nothing, and pretending that is success is how a channel goes quietly dark.
_LENIENT_ONLY = ("no_config", "pending_media")


def exit_code(state, strict=False):
    """Map a run state onto a process exit code.

    A pure function so the suite can assert every branch of the table directly
    rather than by launching the CLI twelve times.
    """
    if state in _ALWAYS_OK:
        return 0
    if not strict and state in _LENIENT_ONLY:
        return 0
    return 1


def publish(date, image_url, transport=None, image_attempts=None):
    """Publish `date`'s recap card, exactly once, ever.

    ORDER IS THE WHOLE DESIGN:
      1. nothing settled            -> nothing_to_post, zero Meta calls
      2. no credentials             -> no_config,       zero Meta calls
      3. REMOTE RECONCILIATION      -> the reference is already live? recover the
                                       media id and stop. This is what survives a
                                       runner dying after media_publish.
      4. pending_publish recovery   -> resume at publish rather than rebuilding
      5. image not reachable        -> pending_media, and NO container is created
      6. create -> poll -> persist pending_publish -> publish -> posted
    """
    t = transport or _transport()
    calls = [0]

    def call(*a, **kw):
        calls[0] += 1
        return t(*a, **kw)

    kind, r = ps.select_recap(date)
    if kind is None:
        record(date, None, None, "nothing_to_post")
        return Result("nothing_to_post", "no settled entries", calls=0)

    ref = ledger_ref(kind, date)
    cfg = config()
    if cfg is None:
        # Zero Meta calls: nothing below this line runs.
        record(date, kind, ref, "no_config",
               detail=f"{ENV_IG_USER} or {ENV_IG_TOKEN} unset")
        return Result("no_config", "credentials unset", calls=0)
    ig_user, token = cfg

    prior = status_for(date)
    if prior and prior.get("result") == "posted" and prior.get("media_id"):
        return Result("posted", "already recorded locally",
                      media_id=prior["media_id"], calls=0)

    # ---- 3. remote reconciliation, before anything is created ----
    media_id, http, payload, snippet = find_published(ig_user, token, ref,
                                                      transport=call)
    if media_id is None and payload is not None and not (
            isinstance(http, int) and 200 <= http < 300):
        kindly, code, sub = classify(http, payload)
        record(date, kind, ref, "failed", http_status=http,
               detail=f"reconcile {kindly} {code}/{sub}: "
                      f"{_err_detail(payload, snippet, token)}", token=token)
        return Result("failed", f"reconcile {kindly}", http_status=http,
                      calls=calls[0])
    if media_id:
        record(date, kind, ref, "posted", media_id=media_id, http_status=http,
               detail="reconciled from the live feed", token=token)
        return Result("posted", "reconciled", media_id=media_id,
                      http_status=http, calls=calls[0])

    caption = build_caption(date)

    # ---- 4. resume a container we already created ----
    if prior and prior.get("result") == "pending_publish" and prior.get("creation_id"):
        cid = prior["creation_id"]
        code, http, payload, snippet = container_status(cid, token, transport=call)
        if code == "PUBLISHED":
            # Reconciliation missed it (older than the window). Do NOT publish.
            record(date, kind, ref, "posted", creation_id=cid, http_status=http,
                   detail="container already PUBLISHED", token=token)
            return Result("posted", "container already published",
                          creation_id=cid, http_status=http, calls=calls[0])
        if code == "FINISHED":
            return _do_publish(date, kind, ref, ig_user, token, cid, call, calls)
        # ERROR / EXPIRED / anything else: abandon it and build fresh below.
        record(date, kind, ref, "failed", creation_id=cid, http_status=http,
               detail=f"stale container {code}", token=token)

    # ---- 5. the image must be publicly reachable FIRST ----
    ok, http = image_reachable(image_url, transport=call,
                               attempts=image_attempts)
    if not ok:
        record(date, kind, ref, "pending_media", http_status=http,
               detail=f"image URL not reachable after "
                      f"{IMAGE_ATTEMPTS if image_attempts is None else image_attempts}"
                      f" attempts; no container created")
        return Result("pending_media", "image not reachable", http_status=http,
                      calls=calls[0])

    # ---- 6. create ----
    http, payload, snippet = create_container(ig_user, token, image_url,
                                              caption, transport=call)
    if not (isinstance(http, int) and 200 <= http < 300):
        kindly, code, sub = classify(http, payload)
        record(date, kind, ref, "failed", http_status=http,
               detail=f"create {kindly} {code}/{sub}: "
                      f"{_err_detail(payload, snippet, token)}", token=token)
        return Result("failed", f"create {kindly}", http_status=http,
                      calls=calls[0])
    cid = (payload or {}).get("id")
    if not cid:
        record(date, kind, ref, "failed", http_status=http,
               detail="create returned no container id", token=token)
        return Result("failed", "no container id", http_status=http,
                      calls=calls[0])

    # ---- poll ----
    code = None
    for attempt in range(POLL_ATTEMPTS):
        code, http, payload, snippet = container_status(cid, token, transport=call)
        if code in ("FINISHED", "ERROR", "EXPIRED", "PUBLISHED"):
            break
        _sleep(POLL_SECONDS)
    if code == "PUBLISHED":
        record(date, kind, ref, "posted", creation_id=cid, http_status=http,
               detail="container reported PUBLISHED", token=token)
        return Result("posted", "already published", creation_id=cid,
                      http_status=http, calls=calls[0])
    if code != "FINISHED":
        record(date, kind, ref, "failed", creation_id=cid, http_status=http,
               detail=f"container {code or 'timeout'}", token=token)
        return Result("failed", f"container {code or 'timeout'}",
                      creation_id=cid, http_status=http, calls=calls[0])

    return _do_publish(date, kind, ref, ig_user, token, cid, call, calls)


def _do_publish(date, kind, ref, ig_user, token, cid, call, calls):
    """Persist pending_publish BEFORE publishing.

    THE CRASH WINDOW THIS CLOSES: if the runner dies between media_publish
    returning 200 and this process writing `posted`, the next run's remote
    reconciliation finds the reference in the live caption and stops. Writing
    pending_publish first also means a death *before* the response leaves a
    creation id to resume from rather than an orphan.
    """
    record(date, kind, ref, "pending_publish", creation_id=cid,
           detail="about to call media_publish", token=token)

    http, payload, snippet = media_publish(ig_user, token, cid, transport=call)
    if not (isinstance(http, int) and 200 <= http < 300):
        kindly, code, sub = classify(http, payload)
        record(date, kind, ref, "failed", creation_id=cid, http_status=http,
               detail=f"publish {kindly} {code}/{sub}: "
                      f"{_err_detail(payload, snippet, token)}", token=token)
        return Result("failed", f"publish {kindly}", creation_id=cid,
                      http_status=http, calls=calls[0])

    media_id = (payload or {}).get("id")
    if not media_id:
        # 2xx without an id is not proof of publication, so this is NOT `posted`.
        record(date, kind, ref, "failed", creation_id=cid, http_status=http,
               detail="publish returned no media id", token=token)
        return Result("failed", "no media id", creation_id=cid,
                      http_status=http, calls=calls[0])

    record(date, kind, ref, "posted", creation_id=cid, media_id=media_id,
           http_status=http, detail="published", token=token)
    return Result("posted", "published", media_id=media_id, creation_id=cid,
                  http_status=http, calls=calls[0])


class SyncResult:
    """What a sync-status run folded in. Carries no token."""

    def __init__(self, state, detail="", scanned=0, refs=0, merged=0,
                 unchanged=0, http_status=None):
        self.state, self.detail = state, detail
        self.scanned, self.refs = scanned, refs
        self.merged, self.unchanged = merged, unchanged
        self.http_status = http_status

    def __repr__(self):
        return (f"SyncResult({self.state!r}, scanned={self.scanned}, "
                f"refs={self.refs}, merged={self.merged}, "
                f"unchanged={self.unchanged})")


def sync_status(transport=None, limit=RECONCILE_LIMIT):
    """Fold confirmed publications from the live feed into the advisory status
    file. READ-ONLY AGAINST META: it creates no container and publishes nothing.

    WHY THIS EXISTS. The publishing step runs AFTER the pipeline's only commit,
    and a GitHub-hosted runner is destroyed when the job ends — so a `posted`
    row written by publish() is discarded with the runner and never reaches the
    repository. It does not "ride along with the next run"; nothing carries it.
    The status history was therefore write-only in practice.

    sync-status closes that by running BEFORE the commit: it asks Meta what is
    actually live, writes those confirmations locally, and the existing
    `git add data/` sweeps them into that day's normal commit. The history
    becomes durable one day in arrears, which is the correct trade — the file is
    telemetry, and the thing that must never be lost (duplicate prevention) does
    not depend on it at all.

    A FAILED READ CHANGES NOTHING. If the feed cannot be read, the existing file
    is left exactly as it was rather than being truncated or half-written.
    """
    cfg = config()
    if cfg is None:
        return SyncResult("no_config", f"{ENV_IG_USER} or {ENV_IG_TOKEN} unset")
    ig_user, token = cfg

    data, http, payload, snippet = recent_media(ig_user, token, limit=limit,
                                                transport=transport)
    if data is None:
        kindly, code, sub = classify(http, payload)
        return SyncResult("failed", f"feed read {kindly} {code}/{sub}: "
                                    f"{_err_detail(payload, snippet, token)}",
                          http_status=http)

    # Newest wins per reference. Meta returns the feed newest-first, but ordering
    # is not promised anywhere in the docs, so pick explicitly on `timestamp`
    # when it is present rather than trusting position.
    best = {}
    for m in data:
        if not isinstance(m, dict):
            continue                       # a malformed row is skipped, not fatal
        parsed = parse_ref(find_ref(m.get("caption") or ""))
        mid = m.get("id")
        if not parsed or not mid:
            continue                       # unrelated or captionless media
        kind, date = parsed
        ts = m.get("timestamp") or ""
        prior = best.get(date)
        if prior is None or ts > prior[2]:
            best[date] = (kind, mid, ts)

    merged = unchanged = 0
    for date, (kind, mid, _ts) in sorted(best.items()):
        row = status_for(date)
        if row and row.get("result") == "posted" and row.get("media_id") == mid:
            unchanged += 1
            continue
        record(date, kind, ledger_ref(kind, date), "posted", media_id=mid,
               http_status=http, detail="confirmed live by sync-status",
               token=token)
        merged += 1

    return SyncResult("ok", f"{merged} merged, {unchanged} already recorded",
                      scanned=len(data), refs=len(best), merged=merged,
                      unchanged=unchanged, http_status=http)


def reconcile(date, transport=None):
    """Read-only: is this date's reference already live? Creates nothing."""
    kind, r = ps.select_recap(date)
    if kind is None:
        return Result("nothing_to_post", "no settled entries")
    ref = ledger_ref(kind, date)
    cfg = config()
    if cfg is None:
        return Result("no_config", "credentials unset")
    ig_user, token = cfg
    media_id, http, payload, snippet = find_published(ig_user, token, ref,
                                                      transport=transport)
    if media_id:
        return Result("posted", "reconciled", media_id=media_id,
                      http_status=http, calls=1)
    return Result("absent", "reference not in the recent feed",
                  http_status=http, calls=1)


# -------------------------------------------------------------------- CLI --
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    mode = args[0] if args else "caption"
    default = (datetime.now(ps.ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    date = args[1] if len(args) > 1 else default

    if mode == "caption":
        text = build_caption(date)
        if text is None:
            print(f"No settled entries for {date} — nothing to caption.")
            return 0
        print(text)
        print(f"\n[{len(text)} characters]", file=sys.stderr)
        return 0

    if mode == "sync-status":
        res = sync_status()
        print(f"[{res.state}] {res.detail} "
              f"(scanned={res.scanned}, refs={res.refs})")
        # Advisory telemetry must never block the ledger commit that follows it,
        # so a failed read is reported and tolerated. `failed` still exits 1 so
        # the workflow can surface a warning without stopping.
        return 0 if res.state in ("ok", "no_config") else 1

    if mode == "reconcile":
        res = reconcile(date)
        print(f"[{res.state}] {res.detail}"
              + (f" media_id={res.media_id}" if res.media_id else ""))
        return 0

    if mode == "publish":
        url = ""
        for f in flags:
            if f.startswith("--image-url="):
                url = f.split("=", 1)[1]
        if not url:
            print("publish requires --image-url=<public JPEG URL>")
            return 2
        if "--dry-run" in flags:
            kind, r = ps.select_recap(date)
            if kind is None:
                print(f"No settled entries for {date}.")
                return 0
            print(f"[dry-run] would publish {ledger_ref(kind, date)} "
                  f"from {url} — no Meta call made.")
            return 0
        strict = "--strict" in flags
        res = publish(date, url)
        # ONE LINE, ALWAYS THIS SHAPE, ALWAYS SANITISED. The workflow's alert
        # path lifts the final line of this output verbatim, so it must never
        # carry anything that has not been through sanitize()/_scrub().
        print(f"[{res.state}] {res.detail} (calls={res.calls})"
              + (f" media_id={res.media_id}" if res.media_id else ""))
        return exit_code(res.state, strict)

    print(f"Unknown mode {mode!r}. Use caption | publish | reconcile.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
