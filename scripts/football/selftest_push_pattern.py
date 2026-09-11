#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the CI push epilogue, against REAL git.

    python scripts/football/selftest_push_pattern.py

Makes no network call. Every repository it touches is a throwaway under the
system temp directory, created and deleted by this script; it never reads or
writes the real repository except to READ the workflow file whose shell it runs.

WHY A SECOND SUITE, WHEN selftest_workflows.py ALREADY READS THESE FILES.
Because text inspection cannot see what git actually does, and the defect that
prompted all of this was invisible in the text. The old line read

    git pull --rebase --autostash || true

and the concern on file was that `|| true` swallowed a failed rebase. It does
not, because there is nothing to swallow: when the rebase succeeds but the
AUTOSTASH POP conflicts, `git pull` EXITS 0. It leaves UU conflict markers in
the working tree and the stash undropped, and the step then staged, committed
and pushed those markers to main. No amount of reading the YAML reveals that -
only running it does. So this suite runs the shipped shell verbatim and looks at
what is on the remote afterwards.

It also pins the retry-exhaustion bug found in grade-ledger's loop: a
`for ... done` whose last command is a successful rebase exits 0, so three lost
races report SUCCESS having pushed nothing.

WHAT IS ASSERTED, by executing the epilogue that football-capture.yml actually
ships:

  1. clean push                  -> exit 0, the commit reaches the remote;
  2. lost race, no conflict      -> rebase, retry, exit 0, commit reaches remote,
                                    and the other bot's commit survives;
  3. lost race, same file        -> exit 1, no rebase left in progress, no
                                    conflict markers anywhere, remote UNCHANGED;
  4. rejected 3 times            -> exit 1, nothing falsely reports success.

Plus a NEGATIVE CONTROL that replays the OLD pattern against scenario 3 and
requires it to publish conflict markers. A regression test that cannot
demonstrate the bug it prevents is a regression test nobody can trust.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
WF = os.path.join(ROOT, ".github", "workflows")

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


# --------------------------------------------------------------------------
# Pulling the shell out of the workflow, rather than restating it here.
# A copy of the epilogue in this file would pass forever while the workflow
# drifted. The point is to run what ships.
# --------------------------------------------------------------------------
def code_only(body):
    return "\n".join(l for l in (body or "").splitlines()
                     if not l.lstrip().startswith("#"))


def push_epilogues(workflow, job):
    """{step name: runnable epilogue} for every retrying push step in a job."""
    doc = yaml.safe_load(io.open(os.path.join(WF, workflow), encoding="utf-8"))
    out = {}
    for step in doc["jobs"][job]["steps"]:
        body = code_only(step.get("run"))
        if "git push" not in body or 'pushed=""' not in body:
            continue
        # From the PUSH_WHAT assignment to the end: the part that is meant to be
        # byte-identical everywhere, and the only part that is runnable without
        # the pipeline's data.
        m = re.search(r'^PUSH_WHAT=.*$', body, re.M)
        out[step["name"]] = body[m.start():] if m else body
    return out


# --------------------------------------------------------------------------
# Throwaway git. GIT_CONFIG_NOSYSTEM and a redirected HOME keep the developer's
# own config - hooks, templates, signing, autoSetupRemote - from reaching in and
# changing what these scenarios prove.
# --------------------------------------------------------------------------
def git(cwd, *args, check_rc=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       text=True, env=ENV)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{r.stderr}")
    return r


def new_world(tmp):
    """A bare remote, our clone, and a second clone standing in for another bot."""
    remote = os.path.join(tmp, "remote.git")
    work = os.path.join(tmp, "work")
    git(tmp, "init", "-q", "--bare", "-b", "main", remote)
    git(tmp, "clone", "-q", remote, work)
    git(work, "config", "user.email", "bot@example.invalid")
    git(work, "config", "user.name", "openledger-bot")
    io.open(os.path.join(work, "credits.json"), "w").write('{"c": 1}\n')
    io.open(os.path.join(work, "snapshot.json"), "w").write('{"s": 1}\n')
    git(work, "add", "-A")
    git(work, "commit", "-qm", "base")
    git(work, "push", "-qu", "origin", "main")
    return remote, work


def other_bot(tmp, remote, fname, content, msg):
    """A second workflow pushing to main while our run is mid-flight."""
    other = os.path.join(tmp, "other")
    git(tmp, "clone", "-q", remote, other)
    git(other, "config", "user.email", "bot@example.invalid")
    git(other, "config", "user.name", "other-bot")
    io.open(os.path.join(other, fname), "w").write(content)
    git(other, "add", "-A")
    git(other, "commit", "-qm", msg)
    git(other, "push", "-q")
    return git(other, "rev-parse", "HEAD").stdout.strip()


def run_shell(work, script):
    path = os.path.join(work, "_step.sh")
    io.open(path, "w", encoding="utf-8", newline="\n").write(script)
    r = subprocess.run(["bash", path], cwd=work, capture_output=True, text=True,
                       env=ENV)
    os.remove(path)
    return r


def markers_anywhere(work):
    """Any file in the working tree carrying a conflict marker."""
    hits = []
    for base, dirs, files in os.walk(work):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            p = os.path.join(base, f)
            try:
                txt = io.open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if re.search(r"^<<<<<<< ", txt, re.M):
                hits.append(os.path.relpath(p, work))
    return hits


def rebase_in_progress(work):
    g = os.path.join(work, ".git")
    return os.path.isdir(os.path.join(g, "rebase-merge")) or \
        os.path.isdir(os.path.join(g, "rebase-apply"))


def remote_head(work):
    git(work, "fetch", "-q", "origin", "main")
    return git(work, "rev-parse", "origin/main").stdout.strip()


ENV = dict(os.environ,
           GIT_CONFIG_NOSYSTEM="1",
           GIT_TERMINAL_PROMPT="0",
           GIT_ASKPASS="echo",
           GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
           GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
           PYTHONIOENCODING="utf-8")

# The OLD pattern, kept only so the negative control can prove it was broken.
# Do not resurrect it. This is the shape that pushed conflict markers to main.
OLD_PATTERN = '''git pull --rebase --autostash || true
git add credits.json
git diff --cached --quiet || git commit -m "Football lines"
git push
'''

if shutil.which("git") is None or shutil.which("bash") is None:
    print("FAILED: this suite needs `git` and `bash` on PATH; both are present "
          "on ubuntu-latest runners and in Git for Windows.")
    sys.exit(1)

EPI = {}
for wf, job in (("football-capture.yml", "capture"),):
    for name, body in push_epilogues(wf, job).items():
        EPI[f"{wf}:{name}"] = body

print(f"[0] the shell under test, taken from the workflows themselves")
check(len(EPI) == 2, f"found 2 retrying push epilogues in football-capture.yml "
                     f"({len(EPI)}: {sorted(EPI)})")
bodies = {re.sub(r'^PUSH_WHAT=.*$', '', b, flags=re.M) for b in EPI.values()}
check(len(bodies) == 1,
      "both epilogues are byte-identical once PUSH_WHAT is removed - the safest "
      "version cannot drift into being the second-safest at one of them")
for name in sorted(EPI):
    print(f"       {name}")

tmpdir = tempfile.mkdtemp(prefix="olspush")
try:
    for name in sorted(EPI):
        script = EPI[name]
        short = name.split(":", 1)[1]

        # ---- 1. clean push ------------------------------------------------
        print(f"\n[1] {short}: clean push, nothing else on main")
        tmp = tempfile.mkdtemp(dir=tmpdir)
        remote, work = new_world(tmp)
        io.open(os.path.join(work, "credits.json"), "w").write('{"c": 2}\n')
        git(work, "add", "credits.json")
        git(work, "commit", "-qm", "Football lines")
        mine = git(work, "rev-parse", "HEAD").stdout.strip()
        r = run_shell(work, script)
        check(r.returncode == 0, f"exit 0 ({r.returncode})")
        check(remote_head(work) == mine, "the commit reached the remote")

        # ---- 2. lost race, non-conflicting --------------------------------
        print(f"\n[2] {short}: another bot pushed a DIFFERENT file first")
        tmp = tempfile.mkdtemp(dir=tmpdir)
        remote, work = new_world(tmp)
        io.open(os.path.join(work, "credits.json"), "w").write('{"c": 2}\n')
        git(work, "add", "credits.json")
        git(work, "commit", "-qm", "Football lines")
        theirs = other_bot(tmp, remote, "closing.json", '{"closing": 1}\n',
                           "Closing lines")
        r = run_shell(work, script)
        check(r.returncode == 0, f"exit 0 after rebasing onto the other bot "
                                 f"({r.returncode})")
        head = git(work, "rev-parse", "HEAD").stdout.strip()
        check(remote_head(work) == head, "our commit reached the remote")
        check(theirs in git(work, "log", "--format=%H").stdout,
              "and the other bot's commit is still there - nothing was clobbered")
        check(os.path.exists(os.path.join(work, "closing.json")),
              "the other bot's file survives in the tree")
        check("failed to push" in r.stderr or "rejected" in r.stderr
              or "fetch" in (r.stdout + r.stderr),
              "the first push really was rejected - this exercised the retry")

        # ---- 3. lost race, SAME file conflicts ----------------------------
        print(f"\n[3] {short}: another bot changed the SAME file - must stop loudly")
        tmp = tempfile.mkdtemp(dir=tmpdir)
        remote, work = new_world(tmp)
        io.open(os.path.join(work, "credits.json"), "w").write('{"c": 2}\n')
        git(work, "add", "credits.json")
        git(work, "commit", "-qm", "Football lines")
        theirs = other_bot(tmp, remote, "credits.json", '{"c": 99}\n',
                           "Closing lines")
        r = run_shell(work, script)
        check(r.returncode == 1, f"exit 1 ({r.returncode})")
        check("::error::" in (r.stdout + r.stderr),
              "an ::error annotation names the conflict")
        check("auto-resolving" in (r.stdout + r.stderr),
              "and says it is NOT auto-resolving")
        check(not rebase_in_progress(work),
              "no rebase left in progress - the abort ran")
        leaked = markers_anywhere(work)
        check(not leaked, f"NO conflict markers anywhere in the tree ({leaked})")
        check(remote_head(work) == theirs,
              "the remote is untouched - nothing was pushed over the other bot")

        # ---- 4. rejected every time ---------------------------------------
        print(f"\n[4] {short}: every push rejected - must not report success")
        tmp = tempfile.mkdtemp(dir=tmpdir)
        remote, work = new_world(tmp)
        before = remote_head(work)
        hook = os.path.join(remote, "hooks", "pre-receive")
        io.open(hook, "w", encoding="utf-8", newline="\n").write("#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        io.open(os.path.join(work, "credits.json"), "w").write('{"c": 2}\n')
        git(work, "add", "credits.json")
        git(work, "commit", "-qm", "Football lines")
        r = run_shell(work, script)
        check(r.returncode == 1, f"exit 1, not a false success ({r.returncode})")
        check("still rejected after 3" in (r.stdout + r.stderr),
              "the exhaustion guard fired - this is the bug grade-ledger's loop has")
        check(git(work, "rev-parse", "origin/main").stdout.strip() == before,
              "nothing reached the remote")

    # ---- NEGATIVE CONTROL ------------------------------------------------
    # The whole reason this suite exists. Replay scenario 3 against the pattern
    # that was removed and require it to do the damage. If this ever stops
    # failing, the scenario has drifted and scenario 3 is no longer proving
    # anything.
    print("\n[5] negative control: the REMOVED pattern must still show the bug")
    tmp = tempfile.mkdtemp(dir=tmpdir)
    remote, work = new_world(tmp)
    io.open(os.path.join(work, "credits.json"), "w").write('{"c": 2}\n')
    theirs = other_bot(tmp, remote, "credits.json", '{"c": 99}\n', "Closing lines")
    r = run_shell(work, OLD_PATTERN)
    check(r.returncode == 0,
          f"`git pull --rebase --autostash` exits 0 on a stash-pop conflict "
          f"({r.returncode}) - `|| true` was never the thing hiding this")
    pushed_head = remote_head(work)
    check(pushed_head != theirs, "the old pattern pushed something over the other bot")
    blob = git(work, "show", f"{pushed_head}:credits.json").stdout
    check("<<<<<<<" in blob,
          "and what it published to main was raw CONFLICT MARKERS - the exact "
          "corruption the new epilogue prevents")
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\npush-pattern selftest: "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
