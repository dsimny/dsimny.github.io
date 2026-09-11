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
cap_bodies = [s.get("run", "") or "" for s in steps(cap_doc, "capture")]
pushes = [i for i, b in enumerate(cap_bodies) if re.search(r"^\s*git push\b", b, re.M)]
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
gr_bodies = [s.get("run", "") or "" for s in steps(gr_doc, "grade")]
gr_push = [i for i, b in enumerate(gr_bodies) if re.search(r"^\s*git push\b", b, re.M)]
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

print(f"\nworkflow-contract selftest: "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
