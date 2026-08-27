"""The ingestion worker.

Every write goes through a service-role RPC. The worker holds no business rules
of its own -- it does not decide what a postponement is, when a quote is stale,
or whether a price is worth recording. Those live in the database, where the
ledger can enforce them. This is deliberate: a second implementation of the
rules in application code is how the two drift apart.

Production shape: the same two entry points run from a Supabase Edge Function,
a cron container, or anything else that can hold a service-role connection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .provider import OddsProvider


@dataclass
class IngestResult:
    run_id: Optional[str]
    events_upserted: int = 0
    events_created: int = 0
    tickets_voided: int = 0
    snapshots_written: int = 0
    snapshots_skipped: int = 0
    snapshots_failed: int = 0
    errors: Optional[list] = None

    def __str__(self) -> str:
        return (
            f"events={self.events_upserted} (new {self.events_created}) "
            f"quotes written={self.snapshots_written} skipped={self.snapshots_skipped} "
            f"failed={self.snapshots_failed} voided_tickets={self.tickets_voided}"
        )


def _scalar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None


def ingest_schedule(conn, provider: OddsProvider) -> IngestResult:
    """Pull fixtures and upsert them.

    Schedule changes are not handled here -- ingest_event_rpc delegates them to
    reschedule_event_rpc, which owns postponement policy and may void tickets.
    """
    run_id = _scalar(
        conn,
        "SELECT public.start_ingestion_run_rpc(%s, 'SCHEDULE'::public.ingestion_kind)",
        (provider.name,),
    )
    result = IngestResult(run_id=run_id)

    try:
        for ev in provider.fetch_schedule():
            payload = _scalar(
                conn,
                """SELECT public.ingest_event_rpc(
                       %s, %s, %s, %s::timestamptz, %s, %s, %s)""",
                (
                    ev.source_event_id, ev.home_team, ev.away_team,
                    ev.scheduled_start, ev.sport, ev.league, provider.name,
                ),
            )
            if isinstance(payload, str):
                payload = json.loads(payload)

            result.events_upserted += 1
            if payload.get("created"):
                result.events_created += 1
            result.tickets_voided += int(payload.get("tickets_voided") or 0)

        _scalar(
            conn,
            """SELECT public.finish_ingestion_run_rpc(
                   %s, 'SUCCEEDED'::public.ingestion_status, NULL, %s)""",
            (run_id, result.events_upserted),
        )
    except Exception as exc:
        # The run is marked FAILED on its own connection state, then the error
        # is re-raised: a silent failed ingestion is worse than a loud one.
        conn.rollback() if not conn.autocommit else None
        _scalar(
            conn,
            """SELECT public.finish_ingestion_run_rpc(
                   %s, 'FAILED'::public.ingestion_status, %s, 0)""",
            (run_id, str(exc)[:2000]),
        )
        raise

    return result


def ingest_odds(conn, provider: OddsProvider, batch_size: int = 500) -> IngestResult:
    """Pull current prices and append the ones that carry information."""
    run_id = _scalar(
        conn,
        "SELECT public.start_ingestion_run_rpc(%s, 'ODDS'::public.ingestion_kind)",
        (provider.name,),
    )
    result = IngestResult(run_id=run_id, errors=[])

    try:
        # source_event_id -> internal uuid. Quotes for unknown fixtures are
        # dropped rather than guessed at.
        with conn.cursor() as cur:
            cur.execute("SELECT source_event_id, id FROM public.events")
            index = {src: eid for src, eid in cur.fetchall()}

        rows = []
        for q in provider.fetch_odds():
            event_id = index.get(q.source_event_id)
            if event_id is None:
                result.errors.append(
                    {"row": q.source_event_id, "error": "UNKNOWN_EVENT: not ingested yet"}
                )
                result.snapshots_failed += 1
                continue

            rows.append({
                "event_id": str(event_id),
                "market_type": q.market_type,
                "selection": q.selection,
                "line": None if q.line is None else str(q.line),
                "price": q.price,
                "sportsbook": q.sportsbook,
                "source_provider": provider.name,
                "captured_at": q.captured_at.isoformat() if q.captured_at else None,
                "is_in_play": q.is_in_play,
            })

        for start in range(0, len(rows), batch_size):
            chunk = rows[start:start + batch_size]
            payload = _scalar(
                conn,
                "SELECT public.ingest_market_snapshots_rpc(%s::jsonb, %s)",
                (json.dumps(chunk), run_id),
            )
            if isinstance(payload, str):
                payload = json.loads(payload)

            result.snapshots_written += payload["written"]
            result.snapshots_skipped += payload["skipped"]
            result.snapshots_failed += payload["failed"]
            result.errors.extend(payload["errors"])

        _scalar(
            conn,
            """SELECT public.finish_ingestion_run_rpc(
                   %s, 'SUCCEEDED'::public.ingestion_status, NULL, 0)""",
            (run_id,),
        )
    except Exception as exc:
        _scalar(
            conn,
            """SELECT public.finish_ingestion_run_rpc(
                   %s, 'FAILED'::public.ingestion_status, %s, 0)""",
            (run_id, str(exc)[:2000]),
        )
        raise

    return result


def poll_once(conn, provider: OddsProvider) -> tuple:
    """One full cycle: schedule, then odds. What a cron tick would run."""
    return ingest_schedule(conn, provider), ingest_odds(conn, provider)
