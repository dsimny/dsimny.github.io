#!/usr/bin/env python3
"""
Open Ledger Sports - self-test for the CI staging blocks, against REAL git.

    python scripts/football/selftest_staging.py

Makes no network call. Every repository it touches is a throwaway under the
system temp directory, created and deleted by this script; it never reads or
writes the real repository except to READ the workflow files whose shell it runs.

WHY THIS EXISTS, AND WHAT IT COST.

    git add a b c_*.json

where the glob matches NOTHING is not a partial success. git exits 128 and
stages NOTHING AT ALL, including `a` and `b`, which were fine. Paired with
`2>/dev/null || true` the step then passes, `git diff --cached --quiet` is true,
and the job goes green having committed nothing.

That is not hypothetical. Between 2026-08-27 and 2026-09-06 football-grade.yml
ran eleven times, succeeded every time, and committed nothing, because
data/football/board_*.json matched no file while every board was still encrypted
and unrevealed. Results were never refreshed, nothing was graded, nothing was
revealed, and two weeks of settled plays sat held - which House Rule 7 calls
fraud. The cause was a shell idiom.

football-grade.yml and football-capture.yml were converted to one path per
`git add` at the time. capture-closing.yml was not, and was only ever saved by
data/closing_*.json happening to match. It does not always: a run where
fetch_closing.py captured nothing leaves the glob empty, and data/odds_credits.json
and odds/ would BOTH have failed to stage with it.

THE INVARIANT, asserted here for EVERY staging block in EVERY workflow, by
running the shipped shell against real git:

  1. an unmatched glob NEVER prevents a sibling path from staging;
  2. when a glob does match, its files ARE staged;
  3. nothing outside the declared set is ever staged.

Plus a NEGATIVE CONTROL per site: the equivalent single-line `git add a b c_*.json`
must, in the same unmatched-glob world, stage nothing at all. A regression test
that cannot demonstrate the bug it prevents is one nobody can trust.

SCOPED TO THE STAGING BLOCK, deliberately. selftest_workflows.py asserts the
declared path SETS, selftest_push_pattern.py asserts the push EPILOGUE, and this
asserts what `git add` actually does with those paths. A contract test scopes to
the execution block it protects unless the invariant is meant to be
repository-wide - otherwise it fires on unrelated lines and gets loosened rather
than heeded, which has already happened three times in this repository.
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

# Never staged by any workflow, in any scenario. A root-level name nothing owns.
DECOY = "UNRELATED_DECOY.txt"

ENV = dict(os.environ,
           GIT_CONFIG_NOSYSTEM="1",
           GIT_TERMINAL_PROMPT="0",
           GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
           GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
           PYTHONIOENCODING="utf-8")

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def code_only(body):
    return "\n".join(l for l in (body or "").splitlines()
                     if not l.lstrip().startswith("#"))


def staging_blocks():
    """{workflow:step -> (runnable staging shell, declared paths)}.

    Every workflow, not a hard-coded list: a staging block added later is
    exactly the one nobody would remember to add here.
    """
    out = {}
    for fn in sorted(os.listdir(WF)):
        if not fn.endswith(".yml"):
            continue
        doc = yaml.safe_load(io.open(os.path.join(WF, fn), encoding="utf-8"))
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                body = code_only(step.get("run"))
                if "git add" not in body:
                    continue
                # Up to the commit: the staging block is what this suite is
                # about, and everything after it needs a remote to run.
                m = re.search(r"^\s*git diff --cached", body, re.M)
                block = body[:m.start()] if m else body
                # GitHub expressions never expand outside Actions. Substituting
                # a fixed date keeps instagram-recovery's single JPEG path
                # runnable rather than skipped.
                block = re.sub(r"\$\{\{[^}]*\}\}", "2026-09-10", block)
                out[f"{fn}:{step.get('name', '?')}"] = (block, declared(block))
    return out


def declared(block):
    """The paths a staging block names, loop form or inline form."""
    loop = re.search(r"for p in (.+?); do", block, re.S)
    if loop:
        raw = loop.group(1).replace("\\", " ").split()
    else:
        raw = []
        for a in re.findall(r"^\s*git add\s+(.+?)(?:\s+2>/dev/null)?(?:\s*\|\|.*)?$",
                            block, re.M):
            raw.extend(a.split())
    paths = [p.strip('"').strip("'") for p in raw
             if p.strip('"') != "$p" and p not in ("-A", ".")]
    # A block may build its path from a shell variable it set two lines up -
    # instagram-recovery stages "data/social/ig_$D.jpg" after D="<date>". The
    # declared text is not a real path until that is substituted, and creating a
    # file literally named ig_$D.jpg would test nothing.
    for var, val in re.findall(r'^\s*([A-Za-z_][A-Za-z0-9_]*)="([^"]*)"',
                               block, re.M):
        paths = [p.replace("$" + var, val) for p in paths]
    return paths


def git(cwd, *args, check_rc=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                       text=True, env=ENV)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{r.stderr}")
    return r


def touch(work, rel, content="x\n"):
    full = os.path.join(work, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    io.open(full, "w", encoding="utf-8", newline="\n").write(content)
    return rel


def materialise(work, path):
    """Create one real file that `path` should match. Returns its repo path."""
    if "*" in path:
        # A concrete match for the glob: data/closing_*.json -> data/closing_X.json
        return touch(work, path.replace("*", "X"))
    if path.endswith("/"):
        return touch(work, path + "sample.json")
    return touch(work, path)


def build(work, paths):
    """A fresh repo with a file for each path, plus the decoy."""
    git(work, "init", "-q", "-b", "main", ".")
    git(work, "config", "user.email", "bot@example.invalid")
    git(work, "config", "user.name", "openledger-bot")
    made = {p: materialise(work, p) for p in paths}
    touch(work, DECOY)
    return made


def run_block(work, block):
    path = os.path.join(work, "_stage.sh")
    io.open(path, "w", encoding="utf-8", newline="\n").write(block)
    r = subprocess.run(["bash", path], cwd=work, capture_output=True, text=True,
                       env=ENV)
    os.remove(path)
    return r


def staged(work):
    out = git(work, "diff", "--cached", "--name-only").stdout.split("\n")
    return sorted(p for p in out if p and p != "_stage.sh")


def covers(staged_list, path):
    """Is at least one staged file attributable to `path`?"""
    if "*" in path:
        pat = re.escape(path).replace(r"\*", "[^/]*")
        return any(re.fullmatch(pat, s) for s in staged_list)
    if path.endswith("/"):
        return any(s.startswith(path) for s in staged_list)
    return path in staged_list


if shutil.which("git") is None or shutil.which("bash") is None:
    print("FAILED: this suite needs `git` and `bash` on PATH; both are present "
          "on ubuntu-latest runners and in Git for Windows.")
    sys.exit(1)

BLOCKS = staging_blocks()
print("[0] the staging blocks under test, taken from the workflows themselves")
check(len(BLOCKS) >= 6, f"found {len(BLOCKS)} staging blocks")
for name, (_, paths) in sorted(BLOCKS.items()):
    globs = [p for p in paths if "*" in p]
    print(f"       {name}")
    print(f"         {len(paths)} paths, {len(globs)} glob(s): {paths}")

# NO MULTI-PATH `git add` MAY CARRY A GLOB, anywhere. This is the shape check;
# the scenarios below are the behaviour check. Both are needed: the shape check
# catches a new block written the old way even if its glob happens to match
# today, which is precisely how capture-closing survived unnoticed.
print("\n[1] no multi-path `git add` carries a glob")
for name, (block, paths) in sorted(BLOCKS.items()):
    for line in block.splitlines():
        m = re.match(r"^\s*git add\s+(.+?)(?:\s+2>/dev/null)?(?:\s*\|\|.*)?$", line)
        if not m:
            continue
        args = [a for a in m.group(1).split() if not a.startswith("-")]
        bad = len(args) > 1 and any("*" in a for a in args)
        check(not bad,
              f"{name}: `git add {' '.join(args)}` mixes a glob with "
              f"{len(args) - 1} sibling path(s) - one unmatched glob would stage "
              f"NOTHING, siblings included"
              if bad else
              f"{name}: no `git add` mixes a glob with siblings ({args})")

tmpdir = tempfile.mkdtemp(prefix="olsstage")
try:
    for name, (block, paths) in sorted(BLOCKS.items()):
        globs = [p for p in paths if "*" in p]
        plain = [p for p in paths if "*" not in p]

        # ---- every declared path present ---------------------------------
        print(f"\n[2] {name}: everything present")
        work = tempfile.mkdtemp(dir=tmpdir)
        build(work, paths)
        r = run_block(work, block)
        s = staged(work)
        check(r.returncode == 0, f"the block exits 0 ({r.returncode})")
        missed = [p for p in paths if not covers(s, p)]
        check(not missed, f"every declared path staged ({missed or 'none missed'})")
        check(DECOY not in s, f"the decoy {DECOY} was NOT staged - nothing "
                              f"outside the declared set is picked up")

        # ---- the glob matches nothing ------------------------------------
        # THE REGRESSION. The old capture-closing line would stage nothing at
        # all here, losing the credit reading with the runner.
        if globs:
            print(f"\n[3] {name}: the glob matches NOTHING")
            work = tempfile.mkdtemp(dir=tmpdir)
            build(work, plain)          # deliberately no file for the glob
            r = run_block(work, block)
            s = staged(work)
            check(r.returncode == 0, f"the block still exits 0 ({r.returncode})")
            missed = [p for p in plain if not covers(s, p)]
            check(not missed,
                  f"every NON-glob sibling still staged despite the unmatched "
                  f"glob {globs} ({missed or 'none missed'})")
            check(not any(covers(s, g) for g in globs),
                  "and the unmatched glob contributed nothing, correctly")
            check(DECOY not in s, f"the decoy {DECOY} was still not staged")

            # ---- NEGATIVE CONTROL ----------------------------------------
            # The same paths, the old way, in the same world. git must exit 128
            # and stage NOTHING - proving scenario 3 above is testing something.
            print(f"\n[4] {name}: negative control - the old single-line form")
            work = tempfile.mkdtemp(dir=tmpdir)
            build(work, plain)
            old = ('git config user.name "openledger-bot"\n'
                   'git config user.email "bot@example.invalid"\n'
                   "git add " + " ".join(paths) + " 2>/dev/null || true\n")
            r = run_block(work, old)
            s = staged(work)
            check(not s,
                  f"`git add {' '.join(paths)}` staged NOTHING AT ALL - not even "
                  f"{plain[0] if plain else 'its siblings'} - which is the "
                  f"eleven-day football-grade bug, reproduced ({s})")

        # ---- nothing present at all --------------------------------------
        print(f"\n[5] {name}: nothing to stage")
        # THE ONLY THING THAT MUST HOLD EVERYWHERE is that nothing is staged.
        # The exit code legitimately differs by form, and both are safe:
        #   loop form   - each `git add` swallows its own miss, exits 0, and the
        #                 `git diff --cached --quiet` below skips the commit;
        #   inline form - `git add` exits 128 and the step goes RED.
        # Loud is fine. The bug this suite exists for is the third outcome: exit
        # 0 having staged nothing, while the paths that DID exist were silently
        # dropped - which only the multi-path-plus-glob shape produces.
        work = tempfile.mkdtemp(dir=tmpdir)
        build(work, [])
        r = run_block(work, block)
        s = staged(work)
        check(s == [],
              f"stages nothing, so `git diff --cached --quiet` correctly skips "
              f"the commit ({s})")
        if "for p in" in block:
            check(r.returncode == 0,
                  f"the loop form tolerates every path being absent and exits 0 "
                  f"({r.returncode})")
        else:
            check(r.returncode != 0,
                  f"the inline form goes RED rather than green-and-empty when a "
                  f"declared path is missing ({r.returncode}) - loud is the "
                  f"acceptable failure here")
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\nstaging selftest: "
      f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
for f in fails:
    print("  - " + f)
sys.exit(1 if fails else 0)
