"""OLP-M1 Database Foundation -- full suite runner.

    python tests/run_all.py

Boots PostgreSQL, applies migrations 001-019, runs every acceptance, security
and concurrency test, and prints the package's required report format.
Exit code is non-zero if anything fails.
"""

import sys
import pathlib
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import harness as h
from test_acceptance import ACCEPTANCE
from test_security import SECURITY, SECURITY_EXTRA
from test_concurrency import CONCURRENCY


def run_group(title, tests, results):
    print(f"\n{title}")
    print("-" * len(title))
    for tid, name, fn in tests:
        try:
            detail = fn()
            results.append((tid, name, "PASS", detail or ""))
            suffix = f"  ({detail})" if detail else ""
            print(f"  PASS  {tid:<9} {name}{suffix}")
        except Exception as exc:
            tb = traceback.format_exc(limit=3)
            results.append((tid, name, "FAIL", str(exc)))
            print(f"  FAIL  {tid:<9} {name}")
            for line in str(exc).strip().splitlines()[:6]:
                print(f"          {line}")
            if "--traceback" in sys.argv:
                print(tb)


def main():
    results = []

    print("OLP-M1 DATABASE FOUNDATION -- TEST RUN")
    print("=" * 60)
    print(f"database: {h.db_uri().split('@')[-1]}")

    print("\nApplying migrations")
    print("-" * 19)
    try:
        applied = h.migrate(verbose=True)
        migrations_ok = True
    except Exception as exc:
        print(f"  MIGRATION FAILURE: {exc}")
        migrations_ok = False
        applied = []

    if not migrations_ok:
        print("\nMigrations: FAIL -- aborting.")
        return 1

    server_version = h.scalar(h.connect(), "SHOW server_version")
    print(f"  {len(applied)} files applied against PostgreSQL {server_version}")

    run_group("Acceptance tests", ACCEPTANCE, results)
    run_group("Concurrency tests", CONCURRENCY, results)
    run_group("Security tests", SECURITY, results)
    run_group("Additional authorization tests", SECURITY_EXTRA, results)

    # ---- summary -----------------------------------------------------------
    def tally(tests):
        ids = {t[0] for t in tests}
        subset = [r for r in results if r[0] in ids]
        passed = sum(1 for r in subset if r[2] == "PASS")
        return passed, len(subset)

    acc = tally(ACCEPTANCE)
    con = tally(CONCURRENCY)
    sec = tally(SECURITY)
    ext = tally(SECURITY_EXTRA)

    total_pass = sum(1 for r in results if r[2] == "PASS")
    total = len(results)
    failures = [r for r in results if r[2] == "FAIL"]

    def verdict(ok):
        return "PASS" if ok else "FAIL"

    def group_ok(ids):
        return all(r[2] == "PASS" for r in results if r[0] in ids)

    print("\n" + "=" * 60)
    print("OLP-M1 DATABASE FOUNDATION")
    print("=" * 60)
    print(f"Migrations: {verdict(migrations_ok)}")
    print(f"Schema constraints: {verdict(group_ok({'M1-T12','M1-T21','M1-T31'}))}")
    print(f"Chapter baseline: {verdict(group_ok({'M1-T31','M1-T17'}))}")
    print(f"Ticket placement: {verdict(group_ok({'M1-T05','M1-T07','M1-T08'}))}")
    print(f"Market revalidation: {verdict(group_ok({'M1-T06','M1-T13','M1-T23','M1-T32'}))}")
    print(f"Escrow accounting: {verdict(group_ok({'M1-T01','M1-T02','M1-T03','M1-T11'}))}")
    print(f"Settlement: {verdict(group_ok({'M1-T01','M1-T02','M1-T03'}))}")
    print(f"Settlement idempotency: {verdict(group_ok({'M1-T19','M1-T20','M1-T15'}))}")
    print(f"Correction audit: {verdict(group_ok({'M1-T26','M1-T27'}))}")
    print(f"Deficit handling: {verdict(group_ok({'M1-T10'}))}")
    print(f"Bankruptcy exit: {verdict(group_ok({'M1-T33','M1-T34','M1-T34b'}))}")
    print(f"Concurrency placement: {verdict(group_ok({'M1-T04','M1-T07c','M1-T17c'}))}")
    print(f"Concurrency settlement: {verdict(group_ok({'M1-T09','M1-T09b','M1-T16'}))}")
    print(f"Authorization: {verdict(group_ok({t[0] for t in SECURITY} | {t[0] for t in SECURITY_EXTRA}))}")

    # The 29 test IDs the package's section 33 table actually enumerates.
    ENUMERATED = {
        "M1-T01", "M1-T02", "M1-T03", "M1-T04", "M1-T05", "M1-T06", "M1-T07",
        "M1-T08", "M1-T09", "M1-T10", "M1-T11", "M1-T12", "M1-T13", "M1-T15",
        "M1-T16", "M1-T17", "M1-T19", "M1-T20", "M1-T21", "M1-T23", "M1-T26",
        "M1-T27", "M1-T29", "M1-T30", "M1-T31", "M1-T32", "M1-T33", "M1-T34",
        "M1-T35",
    }
    seen = {r[0] for r in results}
    missing = sorted(ENUMERATED - seen)
    enum_pass = sum(1 for r in results if r[0] in ENUMERATED and r[2] == "PASS")

    print(f"\nAcceptance tests (section 33 enumerated):\n"
          f"{enum_pass}/{len(ENUMERATED)} PASS")
    if missing:
        print(f"  NOT IMPLEMENTED: {', '.join(missing)}")

    print(f"\nBy suite grouping:")
    print(f"Acceptance tests:\n{acc[0]}/{acc[1]} PASS")
    print(f"\nConcurrency tests:\n{con[0]}/{con[1]} PASS")
    print(f"\nSecurity tests:\n{sec[0]}/{sec[1]} PASS")
    print(f"\nAdditional authorization tests:\n{ext[0]}/{ext[1]} PASS")
    print(f"\nTOTAL: {total_pass}/{total} PASS")

    if failures:
        print("\nFAILURES:")
        for tid, name, _, err in failures:
            lines = (err or "").strip().splitlines()
            first = lines[0][:200] if lines else "(assertion failed with no message)"
            print(f"  {tid} {name}: {first}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
