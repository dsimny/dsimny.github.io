#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the football CI/monitoring contract.

    python scripts/football/selftest_workflows.py

Makes no network call and reads nothing outside .github/workflows/.

WHY THIS EXISTS. The football monitoring architecture is a set of placement
decisions, and every one of them is invisible in the code it protects:

  * capture_isolation belongs in football-capture.yml because that workflow
    writes data/football/odds/ - and team_join does NOT belong there, because
    its three inputs are untouched by a capture and checking them 26 times a day
    would blame a capture for something it did not cause;
  * team_join belongs in football-grade.yml, AFTER the push, because House Rule 7
    publishes a graded play win or lose and a disagreeing reference table must
    not withhold the week's reveal;
  * football-data-daily.yml is read-only and alerts to the private ops channel
    ONLY, because an internal data-integrity failure must never surface in a
    customer channel.

None of that is enforced by anything that runs. A well-meaning cleanup that
"tidies" the monitors into one place, or a copy-paste that adds a members
webhook fallback so the alert "always lands somewhere", would break the design
silently and stay broken until the day it mattered. So the contract is asserted
here, in the same style as every other football suite.

COMMENTS ARE NOT CODE. Each workflow's prose explains, by name, the monitor it
deliberately does NOT run and the webhooks it deliberately does NOT pass. A
naive substring scan over the raw file would read those explanations as the
thing they warn against, so scans run over `code_lines()` - comment-only lines
removed - or over the parsed YAML, which drops comments entirely.
"""
import io
import os
import re
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
WF = os.path.join(ROOT, ".github", "workflows")

CAPTURE = "football-capture.yml"
GRADE = "football-grade.yml"
DAILY = "football-data-daily.yml"
SELFTEST = "football-selftest.yml"

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def raw(name):
    return io.open(os.path.join(WF, name), encoding="utf-8").read()


def code_lines(name):
    """The workflow with comment-only lines removed.

    Not a YAML parse: step ordering and shell bodies matter here, and both are
    easiest to reason about as text. Only whole-line comments are dropped - a
    '#' inside a quoted string is left alone, because stripping it would need a
    parser and no assertion below depends on that case.
    """
    return "\n".join(l for l in raw(name).splitlines()
                     if not l.lstrip().startswith("#"))


def parsed(name):
    return yaml.safe_load(raw(name))


def shell(step):
    """A step's shell body with its own comment lines removed.

    The same "comments are not code" problem as code_lines(), one level down. The
    daily alert step carries the comment `# NO --detail-file, DELIBERATELY.`, and
    the first version of the assertion below read that as proof a log tail WAS
    attached — the test failed on a workflow that was correct. A run body pulled
    out of the parsed YAML is a single scalar string, so the comments inside it
    survive the parse; they have to be stripped here.
    """
    return "\n".join(l for l in (step or "").splitlines()
                     if not l.lstrip().startswith("#"))


def steps(doc, job):
    return doc["jobs"][job]["steps"]


def strings(node):
    """Every string anywhere in a parsed workflow, comments already gone."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)


def no_percapture_alert(doc, job, label):
    """The pre-existing Discord alert job must stay quiet for an integrity-only
    failure.

    Both pipelines already end in an `alert` job keyed on
    `needs.<job>.result != 'success'`, and it attaches a 20-line log tail. Drop a
    failing integrity step into that job and every affected run Discord-alerts:
    26 a day for a persistent capture fault. The instruction was explicit — no
    per-capture Discord alerts, the daily monitor is the notification layer — so
    the step publishes a job output and the alert job stands down on it.

    The suppression is only SOUND because the integrity step carries no `if:`.
    Every other step in these jobs runs on `!cancelled()`; this one runs on the
    default `success()`, so it executes only when everything before it passed.
    `integrity_failed=true` can therefore only mean the integrity check was the
    sole failure. Give the step an `if: !cancelled()` and the output could go
    true alongside a genuine pipeline failure, silencing a real alert — which is
    why that absence is asserted, not assumed.
    """
    j = doc["jobs"][job]
    step = [s for s in j["steps"] if "id" in s and s["id"] == "integrity"]
    check(len(step) == 1, f"{label}: the integrity step is addressable as "
                          f"`id: integrity` ({len(step)})")
    check(j.get("outputs", {}).get("integrity_failed") ==
          "${{ steps.integrity.outputs.failed }}",
          f"{label}: the job exports integrity_failed from that step")
    check(bool(step) and 'echo "failed=true" >> "$GITHUB_OUTPUT"' in (step[0].get("run") or ""),
          f"{label}: the failure path records failed=true before exiting")
    check(bool(step) and "if" not in step[0],
          f"{label}: the integrity step carries NO `if:` - default success() is "
          f"what makes the suppression sound")
    cond = " ".join((doc["jobs"]["alert"].get("if") or "").split())
    check(f"needs.{job}.outputs.integrity_failed != 'true'" in cond,
          f"{label}: the alert job stands down on an integrity-only failure "
          f"({cond})")


print("[1] football-capture.yml runs capture_isolation, and only that monitor")
cap = code_lines(CAPTURE)
check("scripts/football/selftest_capture_isolation.py" in cap,
      "capture runs selftest_capture_isolation.py")
check("selftest_team_join" not in cap,
      "capture does NOT run selftest_team_join.py - its inputs are not written "
      "here, so a failure would name the wrong culprit")

# AFTER THE FINAL PUSH. Placement is the whole point; merely being present is
# not the contract. The monitor must see the bytes this run pushed.
cap_doc = parsed(CAPTURE)
cap_bodies = [shell(s.get("run")) for s in steps(cap_doc, "capture")]
# `git push` is no longer at the start of a line - it lives inside
# `if git push; then ...` in the retry loop - so this matches it anywhere in the
# comment-stripped body rather than anchoring to the margin.
pushes = [i for i, b in enumerate(cap_bodies) if re.search(r"\bgit push\b", b)]
mon = [i for i, b in enumerate(cap_bodies) if "selftest_capture_isolation.py" in b]
check(len(mon) == 1, f"exactly one integrity step in capture ({len(mon)})")
check(bool(pushes) and bool(mon) and mon[0] > max(pushes),
      f"the integrity step (step {mon[0] if mon else '-'}) runs AFTER every "
      f"git push (steps {pushes}) - it sees the pushed workspace")
check(bool(mon) and mon[0] == len(cap_bodies) - 1,
      "the integrity step is the LAST step in the capture job")

print("\n[2] a capture monitor failure changes nothing and tells nobody")
cap_mon = shell(cap_bodies[mon[0]] if mon else "")
for banned, why in (("git push", "must not push"),
                    ("git commit", "must not commit"),
                    ("git revert", "must not undo the push"),
                    ("git reset", "must not rewrite history"),
                    ("post_discord", "must not post to Discord")):
    check(banned not in cap_mon, f"the capture integrity step {why} ({banned} absent)")
check("pushed data remains committed" in cap_mon,
      "the capture failure message states that pushed data remains committed")
check("::error" in cap_mon, "the capture integrity step emits an ::error annotation")
no_percapture_alert(cap_doc, "capture", "capture")

print("\n[3] football-grade.yml runs team_join, after the push")
gr = code_lines(GRADE)
check("scripts/football/selftest_team_join.py" in gr, "grade runs selftest_team_join.py")
gr_doc = parsed(GRADE)
gr_bodies = [shell(s.get("run")) for s in steps(gr_doc, "grade")]
gr_push = [i for i, b in enumerate(gr_bodies) if re.search(r"\bgit push\b", b)]
gr_mon = [i for i, b in enumerate(gr_bodies) if "selftest_team_join.py" in b]
check(len(gr_mon) == 1, f"exactly one team_join step in grade ({len(gr_mon)})")
check(bool(gr_push) and bool(gr_mon) and gr_mon[0] > max(gr_push),
      f"team_join (step {gr_mon[0] if gr_mon else '-'}) runs AFTER the push "
      f"(step {max(gr_push) if gr_push else '-'})")
# PUSH-FIRST IS THE DELIBERATE CHOICE, pinned so a later "improvement" that
# gates the push on this check has to argue with a failing test rather than
# slip through review. House Rule 7 outranks a reference-table disagreement.
gr_mon_body = shell(gr_bodies[gr_mon[0]] if gr_mon else "")
for banned in ("git push", "git commit", "git revert", "git reset", "post_discord"):
    check(banned not in gr_mon_body,
          f"the grade integrity step does not run {banned}")
check("remain committed and published" in gr_mon_body,
      "the grade failure message states graded results remain published")
no_percapture_alert(gr_doc, "grade", "grade")

print("\n[4] football-data-daily.yml runs both monitors")
day = code_lines(DAILY)
day_doc = parsed(DAILY)
check("capture_isolation" in day and "team_join" in day,
      "the daily monitor names both suites")
day_bodies = [s.get("run", "") or "" for s in steps(day_doc, "monitor")]
loop = [shell(b) for b in day_bodies if "capture_isolation" in b and "team_join" in b]
check(bool(loop), "both monitors run from one aggregating step")
check(bool(loop) and "set +e" in loop[0],
      "the loop runs under `set +e` so the second monitor runs after the first fails")

print("\n[5] the daily monitor is dispatch-only, with no GitHub cron")
# `on:` parses as the BOOLEAN True in PyYAML (YAML 1.1 treats `on` as a bool),
# which is why this reads day_doc[True] rather than day_doc["on"].
triggers = day_doc[True]
check(set(triggers) == {"workflow_dispatch"},
      f"workflow_dispatch is the only trigger ({sorted(triggers)})")
check("schedule" not in triggers,
      "no `schedule:` - GitHub cron has measured hours late here; cron-job.org "
      "dispatches this at 02:30 America/New_York instead")
check(not re.search(r"^\s*schedule:", day, re.M),
      "no `schedule:` key anywhere in the daily workflow")

print("\n[6] the daily alert reaches the private ops channel, and nowhere else")
day_strings = list(strings(day_doc))
webhooks = set()
for s in day_strings:
    webhooks |= set(re.findall(r"DISCORD_WEBHOOK_URL\w*", s))
check(webhooks == {"DISCORD_WEBHOOK_URL_ALERTS"},
      f"the only webhook named anywhere in the parsed workflow is "
      f"DISCORD_WEBHOOK_URL_ALERTS ({sorted(webhooks)})")
joined = "".join(day_strings)
for fallback in ("DISCORD_WEBHOOK_URL_MEMBERS", "DISCORD_WEBHOOK_URL }", "WHOP"):
    check(fallback not in joined,
          f"no {fallback} fallback - an ops failure must never reach a customer "
          f"channel")
alert = [shell(b) for b in day_bodies if "post_discord.py alert" in b]
check(len(alert) == 1, f"exactly one alert is sent per run ({len(alert)})")
check(bool(alert) and "--detail-file" not in alert[0],
      "no raw log tail is attached to the alert")
check(bool(alert) and "actions/runs/" in alert[0],
      "the alert carries the run URL")
check(bool(alert) and "No data was modified" in alert[0],
      "the alert states that no data was modified")
check(bool(alert) and re.search(r"^\s*exit 1\s*$", alert[0], re.M),
      "the alert step exits 1, so an undeliverable alert still fails the run")

print("\n[7] the daily monitor cannot write to the repository")
check(day_doc.get("permissions") == {"contents": "read"},
      f"permissions are contents: read ({day_doc.get('permissions')})")
check(all("permissions" not in j for j in day_doc["jobs"].values()),
      "no job re-grants a wider permission than the workflow-level read")
for banned in ("git push", "git commit", "git add", "git tag", "peter-evans"):
    check(banned not in day, f"the daily workflow never runs {banned}")
check("uses: actions/checkout@v4" in day, "it checks out the repository read-only")

print("\n[8] the code gate and the data monitors stay separate")
st = code_lines(SELFTEST)
check("capture_isolation" not in st and "team_join" not in st,
      "football-selftest.yml runs NEITHER data-coupled suite - a red run there "
      "means a code regression, never live-data drift")
suites = re.search(r'SUITES="([^"]+)"', st)
check(bool(suites) and "workflows" in suites.group(1).split(),
      f"this contract suite itself runs in the hermetic gate "
      f"({suites.group(1) if suites else 'no SUITES list found'})")
check(bool(suites) and "push_pattern" in suites.group(1).split(),
      "and so does the real-git push-pattern harness, which proves what text "
      "inspection cannot")

# --------------------------------------------------------------------------
print("\n[9] no push in this repository can corrupt main")
#
# WHAT THIS REPLACED, at five sites across four workflows. Each ran, BEFORE
# staging:
#     git pull --rebase --autostash || true
# The tree was dirty by construction at that point, so --autostash was the only
# way the pull could move at all. It hid a failure mode nobody had considered:
# when the rebase succeeds but the AUTOSTASH POP conflicts, `git pull` exits 0,
# leaving UU markers in the working tree and the stash undropped. `|| true` was
# never the thing swallowing it. The step then staged, committed and pushed raw
# conflict markers to main.
#
# And two workflows that already had the right ORDERING still had the wrong
# LOOP: a `for ... done` whose last command is a successful rebase exits 0, so
# three rejected pushes reported SUCCESS having pushed nothing.
#
# These assertions pin the SHAPE, for every push site at once so a new one
# cannot quietly arrive without the epilogue. selftest_push_pattern.py pins the
# BEHAVIOUR by running each shipped epilogue against real git, including a
# negative control that replays the old pattern and requires it to publish
# markers. Neither suite is sufficient alone: text cannot see git's exit codes,
# and behaviour tests cannot see a site that never adopted the epilogue.

# EXACT staged sets, per site. Not "contains" - broadening a staged set is how
# index.html, a plaintext board or someone else's generated file ends up in the
# wrong commit. Ownership is the point: grade-ledger owns the site build, the
# football workflows own football/, instagram-recovery owns one JPEG.
STAGED = {
    ("football-capture.yml", "Commit captures"):
        ["data/football/odds/", "data/odds_credits.json"],
    ("football-capture.yml", "Commit the board and pages"):
        ["data/football/board_*.enc", "data/football/commitments.json",
         "data/football/game_commitments.json", "data/post_status.json",
         "football/"],
    ("football-grade.yml", "Commit"):
        ["data/football/football_ledger.json", "data/football/commitments.json",
         "data/football/ncaaf_results.json", "data/football/nfl_results.json",
         "data/football/board_*.json", "data/mercer/", "football/"],
    ("capture-closing.yml", "Commit closing lines"):
        ["data/closing_*.json", "data/odds_credits.json", "odds/"],
    ("grade-ledger.yml", "Commit ledger and site"):
        ["data/", "index.html", "feed.xml", "blog/", "picks/"],
    ("instagram-recovery.yml", "Commit and push the card"):
        ["data/social/ig_$D.jpg"],
}
# The two bare-push workflows, deliberately untouched for now: a bare push is a
# reliability question, not the demonstrated corruption path. Listing them means
# a NEW bare push anywhere else fails this suite instead of going unnoticed.
BARE_PUSH_OK = {("morning-board.yml", "Commit board and site"),
                ("rebuild-site.yml", "Commit site")}


def push_sites():
    """{(workflow, step): comment-stripped shell} for every step that pushes."""
    out = {}
    for fn in sorted(os.listdir(WF)):
        if not fn.endswith(".yml"):
            continue
        doc = parsed(fn)
        for job in doc.get("jobs", {}).values():
            for step in job.get("steps", []) or []:
                body = shell(step.get("run"))
                if re.search(r"\bgit push\b", body):
                    out[(fn, step.get("name", "<unnamed>"))] = body
    return out


def staged_paths(body):
    """The paths a step stages, whether added inline or through the loop."""
    loop = re.search(r"for p in (.+?); do", body, re.S)
    if loop:
        raw_paths = loop.group(1).replace("\\", " ").split()
    else:
        raw_paths = []
        for a in re.findall(r"^\s*git add\s+(.+?)(?:\s+2>/dev/null)?(?:\s*\|\|.*)?$",
                            body, re.M):
            raw_paths.extend(a.split())
    return [p.strip('"').strip("'").replace('${{ steps.d.outputs.date }}', '$D')
            for p in raw_paths if p.strip('"') != "$p"]


SITES = push_sites()
retrying = {k: v for k, v in SITES.items() if 'pushed=""' in v}
bare = {k for k in SITES if k not in retrying}

print(f"       {len(SITES)} push sites: {len(retrying)} retrying, {len(bare)} bare")
check(bare == BARE_PUSH_OK,
      f"the only bare pushes left are the two known ones ({sorted(bare)})")
# The two bare sites are not being redesigned here - a bare push is a
# reliability question, not the demonstrated corruption path - but they must not
# become a back door either. A second `git push` appearing inside an existing
# known step would not change the site set above, so count them.
for site in sorted(bare):
    b = SITES[site]
    check(b.count("git push") == 1,
          f"{site[0]}:{site[1]}: still exactly one bare `git push` "
          f"({b.count('git push')})")
    check("--force" not in b and not re.search(r"git push\s+-f\b", b),
          f"{site[0]}:{site[1]}: never force-pushes")
check(set(STAGED) <= set(SITES),
      f"every site in the staged-set table still exists "
      f"({sorted(set(STAGED) - set(SITES))} missing)")

# NOT ONE WORKFLOW, ANYWHERE, may reintroduce the pattern - including the two
# bare-push ones and any workflow added later.
for fn in sorted(os.listdir(WF)):
    if not fn.endswith(".yml"):
        continue
    body = code_lines(fn)
    check("--autostash" not in body, f"{fn}: no --autostash anywhere")
    check("git pull" not in body, f"{fn}: no `git pull` anywhere - sync happens "
                                  f"after the commit, never before the staging")

epilogues = {}
for (fn, name), body in sorted(retrying.items()):
    site = f"{fn}:{name}"

    # -- nothing may be hidden, forced, or auto-resolved --
    swallowed = [l.strip() for l in body.splitlines()
                 if re.search(r"git (pull|fetch|rebase|push)\b", l)
                 and re.search(r"\|\|\s*true", l)
                 and "rebase --abort" not in l]
    # `git rebase --abort 2>/dev/null || true` is the one exempt `|| true`. It is
    # cleanup on a path that exits 1 two lines later, and it must tolerate "no
    # rebase in progress" - what git says when the rebase refused to START (an
    # unstaged tracked file, say). Swallowing the CLEANUP hides nothing;
    # swallowing the SYNC is what published conflict markers.
    check(not swallowed, f"{site}: no `|| true` on a git sync command ({swallowed})")
    check("--force" not in body and not re.search(r"git push\s+-f\b", body),
          f"{site}: never force-pushes")
    for auto in ("--theirs", "--ours", "-X ours", "-X theirs", "-Xours", "-Xtheirs",
                 "rerere"):
        check(auto not in body, f"{site}: no automatic conflict resolution ({auto})")

    # -- explicit staging, exactly as owned --
    check("git add -A" not in body and not re.search(r"git add\s+\.\s*$", body, re.M),
          f"{site}: no `git add -A` and no `git add .`")
    if (fn, name) in STAGED:
        check(staged_paths(body) == STAGED[(fn, name)],
              f"{site}: stages EXACTLY {STAGED[(fn, name)]} "
              f"(found {staged_paths(body)})")

    # -- ordering: stage, commit, then sync --
    check(body.index("git add") < body.index("git commit") < body.index("git push"),
          f"{site}: order is add -> commit -> push")

    # -- the retry contract --
    check("for i in 1 2 3; do" in body, f"{site}: retries up to 3 times")
    check("if git push; then pushed=yes; break; fi" in body,
          f"{site}: `pushed` is set ONLY after a successful push")
    check("git fetch origin main || exit 1" in body,
          f"{site}: a failed fetch stops the step rather than being ignored")
    check("git rebase origin/main" in body, f"{site}: rebases onto the fetched main")
    check("git rebase --abort" in body, f"{site}: aborts a conflicted rebase")
    check("auto-resolving" in body and "::error::" in body,
          f"{site}: announces the conflict as an ::error and says it is not "
          f"auto-resolving")
    guard = re.search(r'if \[ -z "\$pushed" \]; then\n(.+?)\n\s*fi', body, re.S)
    check(bool(guard) and "exit 1" in guard.group(1) and "::error::" in guard.group(1),
          f"{site}: THE EXHAUSTION GUARD - three rejected pushes emit an ::error "
          f"and exit 1. Without it the loop's last command is a successful "
          f"rebase, so the step reports success having pushed nothing")
    check(body.count("git push") == 1,
          f"{site}: exactly one `git push`, inside the retry "
          f"({body.count('git push')})")

    m = re.search(r'^PUSH_WHAT=.*$', body, re.M)
    check(bool(m), f"{site}: names itself in PUSH_WHAT for the error messages")
    if m:
        # Up to the guard's closing `fi`, not to the end of the step: two sites
        # legitimately do more afterwards (grade-ledger and instagram-recovery
        # both record the pushed SHA as a step output, which downstream jobs
        # check out). The EPILOGUE is what must be identical; what a step does
        # with a successful push is its own business.
        tail = body[m.start():]
        cut = tail.index('if [ -z "$pushed" ]; then')
        cut = tail.index("fi", cut) + 2
        epilogues[site] = re.sub(r'^PUSH_WHAT=.*$', '', tail[:cut], flags=re.M)

check(len(set(epilogues.values())) == 1,
      f"all {len(epilogues)} epilogues are BYTE-IDENTICAL once PUSH_WHAT is "
      f"removed - the safest version cannot drift into being the second-safest "
      f"at one of them ({len(set(epilogues.values()))} distinct)")

# OWNERSHIP. Only the two site-building workflows may commit index.html/feed.xml.
for (fn, name), body in sorted(SITES.items()):
    if fn in ("grade-ledger.yml", "morning-board.yml", "rebuild-site.yml"):
        continue
    for never in ("index.html", "feed.xml"):
        check(never not in body,
              f"{fn}:{name}: never stages {never} - this workflow does not own it")

print(f"\nworkflow-contract selftest: "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
