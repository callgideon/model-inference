"""D4: the PostgreSQL `StreamStore` - the one output journal of both regimes (0017).

Every operation is one call (one transaction on the database clock, R7); this module only
shapes records and maps refusals, with `PgJobStore`'s `_call`/`domain_error`/`_answer`.

`append` answers the rows `infrx.append` COMMITTED: psycopg returns a statement's result
only once the server has sent ReadyForQuery, which in autocommit follows the commit - so
the chunks this returns are durable, and they are the only notification a relay gets
(W2 request 6: relay committed chunks only).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import Chunk, Cursor, EngineEvent, Lease, TerminalOutcome
from .jobstore import Connect, PgJobStore, _outcome

#: `records.Chunk`'s fields, in the order `finalize_in_transaction` selects them.
_CHUNK_FIELDS = ("job_id", "generation", "sequence", "event_type", "payload", "bytes",
                 "persisted_at", "expires_at")


def _journalable(value: Any) -> bool:
    """What jsonb can store (review M1): no NUL character in any string, key or value
    (22P05), and no NaN or infinity (22P02). Anything else JSON can say, it can."""
    if isinstance(value, str):
        return "\x00" not in value
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_journalable(key) and _journalable(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_journalable(item) for item in value)
    return True


class PgStreamStore:
    """`ports.StreamStore` over PostgreSQL. `limits` is the store's own configuration
    (event size, chunk TTL, reconciliation window), sent with every append - never a
    caller's."""

    def __init__(self, connect: Connect, *, limits: PilotSettings = DEFAULTS) -> None:
        self._db = PgJobStore(connect)
        self.limits = limits

    def _append_args(self, lease: Lease, events: tuple[EngineEvent, ...]) -> dict[str, Any]:
        return {"lease": lease.model_dump(mode="json"),
                "events": [{"type": event.type.value, "payload": event.payload}
                           for event in events],
                "limits": {"journal_event_max_bytes": self.limits.journal_event_max_bytes,
                           "journal_chunk_ttl_s": self.limits.journal_chunk_ttl_s,
                           "unknown_usage_reconcile_s": self.limits.unknown_usage_reconcile_s}}

    async def append(self, lease: Lease, events: tuple[EngineEvent, ...]) -> tuple[Chunk, ...]:
        """The fence, then the batch, the stored bytes and the publication marker, in one
        transaction; the chunks returned are the committed rows. A refusal that followed a
        committed R29 terminalization (and its terminal event) is raised here (R39).

        A payload jsonb cannot store (a NUL character, NaN, an infinity) is refused
        `journal_write_failed` before anything is sent, the whole batch with it - typed, as
        R25 refuses an oversize event, instead of a raw database error the worker cannot
        classify. (The fake stores such payloads: recorded delta, coordinator request.)"""
        if not all(_journalable(event.payload) for event in events):
            raise errors.JournalWriteFailed("an event carries a NUL character or a non-finite "
                                            "number, which the journal cannot store")
        answer = await self._db._call("append", self._append_args(lease, events))
        rows = PgJobStore._answer(answer)["chunks"]
        return tuple(Chunk.model_validate(row) for row in rows)

    async def read_owned(self, org_id: str, job_handle: str, cursor: Cursor | None = None,
                         limit: int = 100) -> tuple[tuple[Chunk, ...], Cursor | None]:
        """Bounded (at most 1000), tenant-bound replay in cursor order; `invalid_cursor`
        past the head, `replay_gap` below the prune watermark, `journal_expired` once
        nothing is left. The next cursor is the last returned, or the one passed."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise errors.InvalidRequest(f"limit must be a positive integer, not {limit!r}")
        doc = await self._db._call("read_journal", {
            "org_id": org_id, "job_handle": job_handle, "limit": limit,
            "cursor": None if cursor is None else {"generation": cursor.generation,
                                                    "sequence": cursor.sequence}})
        page = tuple(Chunk.model_validate(row) for row in doc["chunks"])
        return page, (page[-1].cursor if page else cursor)

    async def finalize_in_transaction(self, outcome: TerminalOutcome) -> Chunk:
        """Read back the terminal event the terminalizing transaction wrote (0017's trigger,
        R30). `outcome` is a lookup key: unknown job `not_found`; not terminal, or not the
        committed outcome, `state_conflict`; an expired journal `journal_expired`."""
        rows = await self._db._query(
            "select infrx.job_admission(j.request_id)->'outcome', "
            "j.journal_pruned_generation is not null and not exists (select 1 from "
            "infrx.stream_chunks x where x.job_id = j.request_id), c.job_id::text, "
            "c.generation, c.sequence, c.event_type, c.payload, c.bytes, c.committed_at, "
            "c.expires_at "
            "from infrx.jobs j left join infrx.stream_chunks c on c.job_id = j.request_id "
            "and c.event_type = 'terminal' where j.request_id = %s", (outcome.job_id,))
        if not rows:
            raise errors.NotFound(f"no job {outcome.job_id}")
        stored, expired, *chunk = rows[0]
        if stored is None:
            raise errors.StateConflict("finalize_in_transaction runs after the terminal "
                                       "outcome, inside the settling transaction")
        if expired:
            raise errors.JournalExpired(f"the journal of job {outcome.job_id} has expired")
        if outcome != _outcome(stored):
            raise errors.StateConflict("that is not the committed outcome for this job")
        if chunk[0] is None:
            raise errors.StateConflict(f"job {outcome.job_id} has no terminal event")
        return Chunk.model_validate(dict(zip(_CHUNK_FIELDS, chunk)))

    async def expire(self, now: datetime | None = None) -> int:
        """Prune chunks past their TTL on the database clock; `now` is a bound at most (R7).
        Returns how many chunks were removed."""
        return int(await self._db._call("expire_journal", {
            "now": None if now is None else now.isoformat()}))

    async def usage(self) -> dict[str, int]:
        """`{reserved_bytes, stored_bytes, charged_bytes, chunks}` - the journal readiness
        probe's body (G1R request 1) and the journal-bytes gauge's source."""
        return (await self._db._query("select infrx.journal_usage()", ()))[0][0]


__all__ = ["PgStreamStore"]
