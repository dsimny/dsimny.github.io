#!/usr/bin/env python3
"""
Open Ledger Sports - the one writer of data/odds_credits.json.

Every Odds API caller appends the balance headers of each call it makes to a
plaintext rolling ledger, so the credit budget stays visible in the repo while
the snapshots that carry the same numbers are still encrypted. Four callers
used to carry their own copy of the append: football/fetch_odds.py,
fetch_closing.py, fetch_data.py and football/fetch_historical_odds.py. This
module is that append, once.

WHAT THIS OWNS: load, append, cap, the optional note, write.

WHAT IT DOES NOT OWN, ON PURPOSE: the ledger path, building the reading,
parsing the headers, choosing its timestamp, and deciding WHEN to book it. Those
differ per caller and the differences carry meaning - fetch_odds stamps before
the request, fetch_closing and fetch_data after the response, and
fetch_historical_odds books one reading per batch so a long backfill cannot
flush the operational readings out of the window. The PATH stays with the
caller too: fetch_odds and fetch_historical_odds pass their CREDIT_LOG constant,
evaluated inside the old failure boundary; fetch_closing and fetch_data build it
from ROOT before calling, exactly where their originals built it, outside it.

PHASE 1 (2026-09-13) CHANGED NO BEHAVIOUR, INCLUDING BEHAVIOUR THAT IS WRONG.
selftest_odds_credits.py holds the four original functions frozen and proves
each caller still matches its own original, byte for byte. What that means:

  - AN UNREADABLE EXISTING LEDGER IS NOT WRITTEN. Malformed JSON, a top level
    that is not an object, a "readings" that is not a list, a path that cannot
    be read: one NOTE line, no exception, the file is never opened for writing,
    its existing bytes are preserved, and record() returns False. Never "repair"
    it - a repair that starts a fresh {"readings": []} silently deletes up to 60
    readings of evidence.
  - A FAILURE AFTER THE FILE IS OPENED FOR WRITING CAN TRUNCATE THE LEDGER. The
    write is open(path, "w") then json.dump(), which streams: open() empties the
    file first, and dump() writes chunk by chunk. If serialisation fails partway
    (e.g. a reading holding a set) or the disk write fails, the NOTE prints and
    record() returns False, but the file is left holding only the chunks written
    before the failure - the start of the new document, cut off, not valid JSON,
    and every key after the failure point gone. No caller builds such a reading
    today. This is a KNOWN HAZARD, pinned by the selftest, not endorsed; making
    the write atomic is a separate task.
  - create_parent IS PER CALLER. fetch_odds and fetch_data created the parent
    directory when it was missing; fetch_closing and fetch_historical_odds did
    not, and dropped the reading with a NOTE.
  - note is written only when given, and then unconditionally (fetch_data's
    fixed text overwrites whatever note was there, as it always did).
  - keep is used as given, unvalidated, in the slice [-keep:]. Every caller
    passes 60. Other values behave as the originals did: 0 keeps EVERYTHING,
    a negative n drops the oldest n, True keeps one, and a non-integer is a
    failure (NOTE, no write).
  - Formatting is json.dump(log, f, indent=1) with the default ensure_ascii, so
    the committed file's bytes do not move.
"""
import json
import os

KEEP = 60


def record(reading, *, path, note=None, keep=KEEP, create_parent=False):
    """Append ONE credit reading to the ledger at `path`.

    Keeps the newest `keep` readings and every other top-level key as it was.
    Writes the top-level note only when `note` is not None. Creates the parent
    directory only when create_parent is True. Never raises: every failure
    prints "NOTE: could not record odds credits: <error>" and returns False.

    Returns True only when the ledger was written. A False does NOT always mean
    the file is unchanged: an unreadable existing ledger is never written, but a
    failure after the file is opened for writing leaves it truncated (see the
    module docstring).
    """
    try:
        log = {"readings": []}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                log = json.load(f)
        log["readings"] = (log.get("readings", []) + [reading])[-keep:]
        if note is not None:
            log["note"] = note
        if create_parent:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=1)
    except Exception as exc:                 # telemetry must never sink a fetch
        print(f"NOTE: could not record odds credits: {exc}")
        return False
    return True
