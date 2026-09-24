#!/usr/bin/env python3
"""STAND-INS for the content-lifecycle port M6's collector drives (`retention.py`), until
F2C-L commits `contracts.v2.lifecycle` (+ its `fakes.lifecycle.FakeLifecycle`) and D10.b its
PostgreSQL adapter. Neither is committed at the M6 base (f764e396); F2C-L's draft was
READ for its method names, attribute names and refusal vocabulary, never imported.

    INFRX_D_TASK=m6 uv run --frozen pytest -q tests/m/test_lifecycle_standin.py

Two implementations of the coordinator's draft decisions, one world around each:

* **D2** - one durable row per content object (owner, job, identity, generation). Attach
  and delete serialize on that row: `attach` and `claim`/`tombstone` all take its lock, and
  the reference/grace recheck happens INSIDE the tombstone transaction. A tombstoned row
  refuses new use (`content_retiring`); an acknowledged delete makes the key's next
  registration generation + 1, and generation n > 1 lives at `generation_key(key, n)`.
* **D3** - what survives expiry: the job row, its idempotency key, terminal outcome,
  ledger amount and digests. Content (object bytes, database bodies) is removed and the
  row marked (`deleted`, `scrubbed_at`); a database body is emptied by the acknowledgement
  itself, in its transaction (F2C.b's scrub: "the acknowledgement records the scrub"). A
  result's boundary is the job's persisted `result_expires_at`; nothing is recomputed from
  configuration after settlement.

`DraftLifecycle` is in memory (one asyncio lock stands for the row locks, as in F2C-L's
draft). `PgLifecycleStandIn` is the same contract in real SQL on the task-local PostgreSQL
(`tests/d/pgharness`, `INFRX_D_TASK=m6` -> infrx-m6-postgres :55444, database infrx_m6_*),
in a throwaway `m6` schema: real row locks and READ COMMITTED visibility, which is what the
race tests need. It is TEST SUPPORT, not a migration - D10 owns the SQL that replaces it,
and `locking=False` is the deliberately broken variant (no reference lock) the race tests
must catch. The cases at the bottom are the port rules both must satisfy; D10's adapter
joins the `WORLDS` parametrization when it lands.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import DEFAULT_START, FakeClock
from infrx.contracts.records import MediaKind, MediaRef
from infrx.media import store
from infrx.media.fetch import digest_of
from infrx.media.retention import generation_key

GRACE_S = 600.0
CLAIM_TTL_S = 60.0
RETENTION_S = 3_600.0          # P-25 pending: a fixture value, never a product claim
_uuids = itertools.count(1)


def new_id() -> str:
    return f"00000000-0000-4000-8000-{next(_uuids):012x}"


# --- the records (F2C-L's attribute names) ---------------------------------------------
@dataclass(frozen=True)
class Identity:
    org_id: str
    kind: str                 # upload_destination | source | prepared | payload | result
    location: str             # object_store | database
    object_key: str
    job_id: str | None = None
    digest: str | None = None
    bytes: int | None = None


@dataclass(frozen=True)
class Claim:
    content_id: str
    generation: int
    fence: int
    holder: str
    claimed_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class Tomb:
    content_id: str
    generation: int
    fence: int
    location: str
    object_key: str
    tombstoned_at: datetime


@dataclass(frozen=True)
class Row:
    content_id: str
    generation: int
    identity: Identity
    state: str                # live | tombstoned | deleted
    registered_at: datetime
    eligible_at: datetime
    hold_until: datetime | None = None
    claim: Claim | None = None
    tombstoned_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class Page:
    items: tuple
    next_cursor: str | None = None


REFUSALS = {"not_found": errors.NotFound, "not_eligible": errors.NotClaimable,
            "reference_live": errors.NotClaimable, "claim_held": errors.NotClaimable,
            "claim_lost": errors.StaleLease, "content_retiring": errors.DependencyUnavailable}


def refuse(reason: str) -> errors.DomainError:
    """F2C-L's shape: the typed error, carrying the reason as `.refusal`."""
    error = REFUSALS[reason](reason)
    error.refusal = reason
    return error


def cursor_of(row: Row) -> str:
    return f"{row.eligible_at.isoformat()}|{row.content_id}"


def cursor_start(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    at, content_id = cursor.split("|")
    return datetime.fromisoformat(at), content_id


# --- in memory ---------------------------------------------------------------------------
@dataclass
class Job:
    state: str = "live"       # live, or the terminal state
    settled_at: datetime | None = None
    result_expires_at: datetime | None = None
    retain_until: datetime | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Durable:
    """Everything a restart keeps."""

    rows: dict[str, Row] = field(default_factory=dict)
    by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    refs: dict[str, set[tuple[int, str]]] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    bodies: dict[str, dict] = field(default_factory=dict)
    # The row locks. No guarded block awaits, so each is atomic on the event loop already;
    # an `asyncio.Lock` here would also bind to the first loop and break the next `run()`.
    lock: contextlib.nullcontext = field(default_factory=contextlib.nullcontext)


class DraftLifecycle:
    def __init__(self, clock, *, grace_s=GRACE_S, claim_ttl_s=CLAIM_TTL_S,
                 retention_s=RETENTION_S, durable: Durable | None = None) -> None:
        self.clock, self.d = clock, durable or Durable()
        self.grace_s, self.claim_ttl_s, self.retention_s = grace_s, claim_ttl_s, retention_s

    def reopen(self, **changes) -> DraftLifecycle:
        """Another process over the same durable state: nothing is kept per instance.
        `changes`: that process's configuration (a policy change is a redeploy)."""
        options = dict(grace_s=self.grace_s, claim_ttl_s=self.claim_ttl_s,
                       retention_s=self.retention_s) | changes
        return DraftLifecycle(self.clock, durable=self.d, **options)

    def _job_live(self, job_id: str | None, now: datetime, kind: str = "source") -> bool:
        """F2C.b per kind: a result until the persisted `result_expires_at` (none: it keeps
        no result), anything else until settlement + the retention of that instant."""
        job = self.d.jobs.get(job_id)
        if job is None:
            return False
        if job.state == "live":
            return True
        until = (job.result_expires_at or job.settled_at) if kind == "result" \
            else job.retain_until
        return now < until

    def _protected(self, row: Row, now: datetime) -> bool:
        return (row.hold_until is not None and now < row.hold_until) \
            or self._job_live(row.identity.job_id, now, row.identity.kind) \
            or any(gen == row.generation and self._job_live(job, now)
                   for gen, job in self.d.refs.get(row.content_id, ()))

    def _save(self, row: Row, **changes) -> Row:
        row = replace(row, **changes)
        self.d.rows[row.content_id] = row
        return row

    # --- the runtime's side (D10 behind M5/W5/G7) ------------------------------------------
    async def register(self, identity: Identity, *, hold_s: float | None = None) -> Row:
        async with self.d.lock:
            now = self.clock.now()
            fresh = dict(state="live", registered_at=now, claim=None, tombstoned_at=None,
                         deleted_at=None, eligible_at=now + timedelta(seconds=self.grace_s),
                         hold_until=now + timedelta(seconds=hold_s) if hold_s else None)
            key = (identity.location, identity.object_key)
            row = self.d.rows.get(self.d.by_key.get(key, ""))
            if row is None:
                self.d.by_key[key] = content_id = new_id()
                return self._save(Row(content_id=content_id, generation=1, identity=identity,
                                      **fresh))
            if row.state == "tombstoned":
                raise refuse("content_retiring")
            if row.state == "deleted":
                return self._save(row, generation=row.generation + 1, identity=identity, **fresh)
            return row                      # first registration wins: eligibility not reset

    async def attach(self, job_id: str, content_id: str) -> None:
        async with self.d.lock:
            row = self.d.rows.get(content_id)
            if row is None:
                raise refuse("not_found")
            if row.state != "live":
                raise refuse("content_retiring")
            self.d.refs.setdefault(content_id, set()).add((row.generation, job_id))

    async def admit(self, job_id: str, **meta) -> None:
        self.d.jobs[job_id] = Job(meta=dict(meta))

    async def finish(self, job_id: str, state: str, *, result_ttl_s: float | None = None) -> None:
        """Terminalize once: `result_expires_at` and `retain_until` are set HERE, from the
        configuration of this instant, and never recomputed."""
        async with self.d.lock:
            job, now = self.d.jobs[job_id], self.clock.now()
            if job.state != "live":
                return
            job.state, job.settled_at = state, now
            if result_ttl_s is not None:
                job.result_expires_at = now + timedelta(seconds=result_ttl_s)
            job.retain_until = now + timedelta(seconds=self.retention_s)

    async def put_body(self, object_key: str, body: str) -> None:
        self.d.bodies.setdefault(object_key, {"body": body, "scrubbed_at": None})

    async def body(self, object_key: str) -> str | None:
        return self.d.bodies[object_key]["body"]

    async def job(self, job_id: str) -> dict:
        job = self.d.jobs[job_id]
        return {"state": job.state, "settled_at": job.settled_at,
                "result_expires_at": job.result_expires_at, **job.meta}

    async def row(self, content_id: str) -> Row:
        return self.d.rows[content_id]

    async def job_live(self, job_id: str) -> bool:
        return self._job_live(job_id, self.clock.now())

    async def readable(self, content_id: str) -> bool:
        """The read rule (D10/G7): a live row, and a result only before its persisted
        expiry - physical deletion lag never makes expired content readable."""
        row, now = self.d.rows[content_id], self.clock.now()
        if row.state != "live":
            return False
        job = self.d.jobs.get(row.identity.job_id)
        return not (row.identity.kind == "result" and job is not None
                    and job.result_expires_at is not None and now >= job.result_expires_at)

    # --- the port the collector drives -----------------------------------------------------
    def _claimable(self, row: Row, now: datetime) -> bool:
        if row.claim is not None and now < row.claim.expires_at:
            return False
        return row.state == "tombstoned" or (
            row.state == "live" and now >= row.eligible_at and not self._protected(row, now))

    async def candidates(self, *, after: str | None, limit: int) -> Page:
        async with self.d.lock:
            now, start = self.clock.now(), cursor_start(after)
            rows = sorted((r for r in self.d.rows.values() if self._claimable(r, now)),
                          key=lambda r: (r.eligible_at, r.content_id))
            if start is not None:
                rows = [r for r in rows if (r.eligible_at, r.content_id) > start]
            page = tuple(rows[:limit])
            return Page(page, cursor_of(page[-1]) if len(rows) > limit else None)

    def _recheck(self, row: Row, now: datetime) -> None:
        if now < row.eligible_at:
            raise refuse("not_eligible")
        if self._protected(row, now):
            raise refuse("reference_live")

    async def claim(self, content_id: str, generation: int, holder: str) -> Claim:
        async with self.d.lock:
            row, now = self.d.rows[content_id], self.clock.now()
            if row.generation != generation or row.state == "deleted":
                raise refuse("claim_lost")
            if row.claim is not None and now < row.claim.expires_at:
                raise refuse("claim_held")
            if row.state == "live":
                self._recheck(row, now)
            claim = Claim(content_id, generation, (row.claim.fence if row.claim else 0) + 1,
                          holder, now, now + timedelta(seconds=self.claim_ttl_s))
            self._save(row, claim=claim)
            return claim

    async def tombstone(self, claim: Claim) -> Tomb:
        async with self.d.lock:
            row, now = self.d.rows[claim.content_id], self.clock.now()
            if row.generation != claim.generation or row.claim is None \
                    or row.claim.fence != claim.fence or now >= row.claim.expires_at:
                raise refuse("claim_lost")
            if row.state == "live":
                self._recheck(row, now)
                row = self._save(row, state="tombstoned", tombstoned_at=now)
            return Tomb(row.content_id, row.generation, claim.fence, row.identity.location,
                        row.identity.object_key, row.tombstoned_at)

    async def acknowledge_delete(self, tomb: Tomb) -> Row:
        async with self.d.lock:
            row = self.d.rows[tomb.content_id]
            if row.generation != tomb.generation or row.state == "deleted":
                return row                  # an older generation's delete, or a repeat
            if row.state != "tombstoned" or row.claim is None or row.claim.fence != tomb.fence:
                raise refuse("claim_lost")
            entry = self.d.bodies.get(tomb.object_key)
            if row.identity.location == "database" and entry is not None:   # the scrub (F2C.b)
                entry["body"], entry["scrubbed_at"] = None, self.clock.now()
            return self._save(row, state="deleted", deleted_at=self.clock.now())


# --- PostgreSQL --------------------------------------------------------------------------
SCHEMA = """
drop schema if exists m6 cascade;
create schema m6;
create table m6.clock (id int primary key default 1 check (id = 1), now timestamptz not null);
create function m6.now() returns timestamptz language sql stable as 'select now from m6.clock';
create table m6.jobs (
  job_id uuid primary key, state text not null default 'live', settled_at timestamptz,
  result_expires_at timestamptz, retain_until timestamptz, meta jsonb not null default '{}');
create table m6.content (
  content_id uuid primary key, org_id uuid not null, kind text not null,
  location text not null, object_key text not null, job_id uuid,
  generation int not null default 1, state text not null default 'live',
  registered_at timestamptz not null, eligible_at timestamptz not null,
  hold_until timestamptz, fence int not null default 0, holder text,
  claimed_at timestamptz, claim_expires_at timestamptz,
  tombstoned_at timestamptz, deleted_at timestamptz,
  unique (location, object_key));
create table m6.refs (content_id uuid not null references m6.content, generation int not null,
  job_id uuid not null references m6.jobs, primary key (content_id, generation, job_id));
create table m6.bodies (object_key text primary key, body text, scrubbed_at timestamptz);
create function m6.job_live(p_job uuid, p_kind text default 'source') returns boolean
language sql stable as $$
  select coalesce((select j.state = 'live' or m6.now() < case when p_kind = 'result'
                     then coalesce(j.result_expires_at, j.settled_at) else j.retain_until end
                     from m6.jobs j where j.job_id = p_job), false) $$;
create function m6.protected(c m6.content) returns boolean language sql stable as $$
  select m6.now() < coalesce(c.hold_until, '-infinity') or m6.job_live(c.job_id, c.kind)
      or exists (select 1 from m6.refs r where r.content_id = c.content_id
                  and r.generation = c.generation and m6.job_live(r.job_id)) $$;
"""
_ROW = ("content_id, org_id, kind, location, object_key, job_id, generation, state, "
        "registered_at, eligible_at, hold_until, fence, holder, claimed_at, claim_expires_at, "
        "tombstoned_at, deleted_at")


def _row(r) -> Row:
    (content_id, org_id, kind, location, key, job_id, generation, state, registered_at,
     eligible_at, hold_until, fence, holder, claimed_at, claim_expires_at, tombstoned_at,
     deleted_at) = r
    claim = Claim(str(content_id), generation, fence, holder, claimed_at, claim_expires_at) \
        if claimed_at is not None else None
    return Row(str(content_id), generation,
               Identity(str(org_id), kind, location, key, job_id and str(job_id)), state,
               registered_at, eligible_at, hold_until, claim, tombstoned_at, deleted_at)


class PgClock:
    """The database's clock: every session reads `m6.now()`, tests move it."""

    def __init__(self, dsn: str) -> None:
        import psycopg
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.conn.execute("insert into m6.clock (now) values (%s)", (DEFAULT_START,))

    def now(self) -> datetime:
        return self.conn.execute("select m6.now()").fetchone()[0]

    def advance(self, seconds: float) -> datetime:
        return self.conn.execute("update m6.clock set now = now + make_interval(secs => %s) "
                                 "returning now", (float(seconds),)).fetchone()[0]


class PgLifecycleStandIn:
    """`DraftLifecycle`'s contract in SQL. One connection per operation (like
    `state.jobstore.connector`), so concurrent operations are concurrent sessions.
    `locking=False` drops the row lock from attach, claim and tombstone: the check and
    the write are then separate - the "missing reference lock" the race tests catch."""

    def __init__(self, dsn: str, *, grace_s=GRACE_S, claim_ttl_s=CLAIM_TTL_S,
                 retention_s=RETENTION_S, locking: bool = True) -> None:
        self.dsn, self.locking = dsn, locking
        self.grace_s, self.claim_ttl_s, self.retention_s = grace_s, claim_ttl_s, retention_s
        self.lock = " for update" if locking else ""
        self.unavailable = False

    def reopen(self, **changes) -> PgLifecycleStandIn:
        options = dict(grace_s=self.grace_s, claim_ttl_s=self.claim_ttl_s,
                       retention_s=self.retention_s, locking=self.locking) | changes
        return PgLifecycleStandIn(self.dsn, **options)

    async def _connect(self):
        import psycopg
        if self.unavailable:
            raise errors.DependencyUnavailable("the lifecycle database did not answer")
        return await psycopg.AsyncConnection.connect(self.dsn, autocommit=True)

    async def _one(self, sql: str, args=()):
        conn = await self._connect()
        try:
            return await (await conn.execute(sql, args)).fetchone()
        finally:
            await conn.close()

    async def _locked(self, conn, content_id: str) -> Row:
        found = await (await conn.execute(
            f"select {_ROW} from m6.content where content_id = %s{self.lock}",
            (content_id,))).fetchone()
        if found is None:
            raise refuse("not_found")
        return _row(found)

    async def _protected(self, conn, content_id: str) -> bool:
        return (await (await conn.execute("select m6.protected(c) from m6.content c "
                                          "where content_id = %s", (content_id,))).fetchone())[0]

    # --- the runtime's side ------------------------------------------------------------
    async def register(self, identity: Identity, *, hold_s: float | None = None) -> Row:
        conn = await self._connect()
        try:
            async with conn.transaction():
                fresh = ("registered_at = m6.now(), eligible_at = m6.now() + "
                         "make_interval(secs => %(grace)s), hold_until = m6.now() + "
                         "make_interval(secs => %(hold)s), claimed_at = null, "
                         "claim_expires_at = null, holder = null, tombstoned_at = null, "
                         "deleted_at = null, state = 'live'")
                args = dict(id=new_id(), org=identity.org_id, kind=identity.kind,
                            loc=identity.location, key=identity.object_key,
                            job=identity.job_id, grace=self.grace_s, hold=hold_s)
                await conn.execute(
                    "insert into m6.content (content_id, org_id, kind, location, object_key, "
                    "job_id, registered_at, eligible_at, hold_until) values (%(id)s, %(org)s, "
                    "%(kind)s, %(loc)s, %(key)s, %(job)s, m6.now(), m6.now() + "
                    "make_interval(secs => %(grace)s), m6.now() + make_interval(secs => "
                    "%(hold)s)) on conflict (location, object_key) do nothing", args)
                row = _row(await (await conn.execute(
                    f"select {_ROW} from m6.content where location = %(loc)s and "
                    "object_key = %(key)s for update", args)).fetchone())
                if row.state == "tombstoned":
                    raise refuse("content_retiring")
                if row.state == "deleted":
                    await conn.execute(f"update m6.content set generation = generation + 1, "
                                       f"job_id = %(job)s, kind = %(kind)s, {fresh} "
                                       "where content_id = %(cid)s", args | {"cid": row.content_id})
                    row = _row(await (await conn.execute(
                        f"select {_ROW} from m6.content where content_id = %s",
                        (row.content_id,))).fetchone())
                return row
        finally:
            await conn.close()

    async def attach(self, job_id: str, content_id: str, *, hold=None) -> None:
        """`hold`: an awaitable run after the reference is written and before COMMIT - a
        transaction held open, for the race schedules."""
        conn = await self._connect()
        try:
            async with conn.transaction():
                row = await self._locked(conn, content_id)
                if row.state != "live":
                    raise refuse("content_retiring")
                await conn.execute("insert into m6.refs values (%s, %s, %s) on conflict do "
                                   "nothing", (content_id, row.generation, job_id))
                if hold is not None:
                    await hold()
        finally:
            await conn.close()

    async def admit(self, job_id: str, **meta) -> None:
        from psycopg.types.json import Jsonb
        await self._one("insert into m6.jobs (job_id, meta) values (%s, %s) returning 1",
                        (job_id, Jsonb(meta)))

    async def finish(self, job_id: str, state: str, *, result_ttl_s: float | None = None) -> None:
        await self._one(
            "update m6.jobs set state = %(state)s, settled_at = m6.now(), "
            "result_expires_at = m6.now() + make_interval(secs => %(ttl)s), "
            "retain_until = m6.now() + make_interval(secs => %(keep)s) "
            "where job_id = %(job)s and state = 'live' returning 1",
            dict(state=state, ttl=result_ttl_s, keep=self.retention_s, job=job_id))

    async def put_body(self, object_key: str, body: str) -> None:
        await self._one("insert into m6.bodies (object_key, body) values (%s, %s) "
                        "on conflict do nothing returning 1", (object_key, body))

    async def body(self, object_key: str) -> str | None:
        return (await self._one("select body from m6.bodies where object_key = %s",
                                (object_key,)))[0]

    async def job(self, job_id: str) -> dict:
        state, settled, expires, meta = await self._one(
            "select state, settled_at, result_expires_at, meta from m6.jobs where job_id = %s",
            (job_id,))
        return {"state": state, "settled_at": settled, "result_expires_at": expires, **meta}

    async def row(self, content_id: str) -> Row:
        return _row(await self._one(f"select {_ROW} from m6.content where content_id = %s",
                                    (content_id,)))

    async def job_live(self, job_id: str) -> bool:
        return (await self._one("select m6.job_live(%s)", (job_id,)))[0]

    async def readable(self, content_id: str) -> bool:
        return (await self._one(
            "select c.state = 'live' and not (c.kind = 'result' and m6.now() >= "
            "coalesce(j.result_expires_at, 'infinity')) from m6.content c left join m6.jobs j "
            "on j.job_id = c.job_id where c.content_id = %s", (content_id,)))[0]

    # --- the port ----------------------------------------------------------------------
    async def candidates(self, *, after: str | None, limit: int) -> Page:
        start = cursor_start(after)
        conn = await self._connect()
        try:
            rows = await (await conn.execute(
                f"select {_ROW} from m6.content c where (c.claim_expires_at is null or "
                "m6.now() >= c.claim_expires_at) and (c.state = 'tombstoned' or (c.state = "
                "'live' and m6.now() >= c.eligible_at and not m6.protected(c))) and "
                "(%(at)s::timestamptz is null or (c.eligible_at, c.content_id) > "
                "(%(at)s::timestamptz, %(id)s::uuid)) order by c.eligible_at, c.content_id "
                "limit %(n)s", dict(at=start and start[0], id=start and start[1],
                                    n=limit + 1))).fetchall()
        finally:
            await conn.close()
        page = tuple(_row(r) for r in rows[:limit])
        return Page(page, cursor_of(page[-1]) if len(rows) > limit else None)

    async def _recheck(self, conn, row: Row) -> None:
        if (await (await conn.execute("select m6.now() < %s", (row.eligible_at,))).fetchone())[0]:
            raise refuse("not_eligible")
        if await self._protected(conn, row.content_id):
            raise refuse("reference_live")

    async def claim(self, content_id: str, generation: int, holder: str) -> Claim:
        conn = await self._connect()
        try:
            async with conn.transaction():
                row = await self._locked(conn, content_id)
                now = (await (await conn.execute("select m6.now()")).fetchone())[0]
                if row.generation != generation or row.state == "deleted":
                    raise refuse("claim_lost")
                if row.claim is not None and now < row.claim.expires_at:
                    raise refuse("claim_held")
                if row.state == "live":
                    await self._recheck(conn, row)
                fence, claimed, expires = await (await conn.execute(
                    "update m6.content set fence = fence + 1, holder = %s, claimed_at = m6.now(), "
                    "claim_expires_at = m6.now() + make_interval(secs => %s) where content_id = "
                    "%s returning fence, claimed_at, claim_expires_at",
                    (holder, self.claim_ttl_s, content_id))).fetchone()
                return Claim(content_id, generation, fence, holder, claimed, expires)
        finally:
            await conn.close()

    async def tombstone(self, claim: Claim) -> Tomb:
        conn = await self._connect()
        try:
            async with conn.transaction():
                row = await self._locked(conn, claim.content_id)
                now = (await (await conn.execute("select m6.now()")).fetchone())[0]
                if row.generation != claim.generation or row.claim is None \
                        or row.claim.fence != claim.fence or now >= row.claim.expires_at:
                    raise refuse("claim_lost")
                if row.state == "live":
                    await self._recheck(conn, row)
                    await conn.execute("update m6.content set state = 'tombstoned', "
                                       "tombstoned_at = m6.now() where content_id = %s",
                                       (claim.content_id,))
                row = await self._locked(conn, claim.content_id)
                return Tomb(row.content_id, row.generation, claim.fence, row.identity.location,
                            row.identity.object_key, row.tombstoned_at)
        finally:
            await conn.close()

    async def acknowledge_delete(self, tomb: Tomb) -> Row:
        conn = await self._connect()
        try:
            async with conn.transaction():
                row = await self._locked(conn, tomb.content_id)
                if row.generation != tomb.generation or row.state == "deleted":
                    return row
                if row.state != "tombstoned" or row.claim is None or row.claim.fence != tomb.fence:
                    raise refuse("claim_lost")
                if row.identity.location == "database":             # the scrub (F2C.b)
                    await conn.execute("update m6.bodies set body = null, scrubbed_at = "
                                       "m6.now() where object_key = %s", (tomb.object_key,))
                await conn.execute("update m6.content set state = 'deleted', deleted_at = "
                                   "m6.now() where content_id = %s", (tomb.content_id,))
                return await self._locked(conn, tomb.content_id)
        finally:
            await conn.close()


# --- the world a case runs in -------------------------------------------------------------
class Objects(store.InMemoryObjectStore):
    """The object store, counting deletes per key (a key deleted twice is visible)."""

    def __init__(self) -> None:
        super().__init__()
        self.deletes: dict[str, int] = {}

    async def delete(self, key: str) -> None:
        self.deletes[key] = self.deletes.get(key, 0) + 1
        await super().delete(key)


@dataclass
class World:
    port: object
    objects: Objects
    clock: object
    kind: str
    org: str = b.ORG_A

    async def write(self, kind: str, key: str, data: bytes = b"content", *,
                    job: str | None = None, hold_s: float | None = None,
                    location: str = "object_store") -> Row:
        """What a writer does: register the row FIRST, then write the bytes where that
        generation lives (`generation_key`) - or the body, for database content."""
        row = await self.port.register(
            Identity(self.org, kind, location, key, job, digest_of(data), len(data)),
            hold_s=hold_s)
        if location == "database":
            await self.port.put_body(key, data.decode())
        else:
            await self.objects.put_if_absent(generation_key(key, row.generation), data,
                                             "application/octet-stream")
        return row

    async def read(self, row: Row) -> bytes | str | None:
        """Content as a reader gets it: only through the read rule."""
        if not await self.port.readable(row.content_id):
            return None
        if row.identity.location == "database":
            return await self.port.body(row.identity.object_key)
        return await self.objects.get(generation_key(row.identity.object_key, row.generation))

    def restart(self, **configuration) -> World:
        """A new runtime over the same database and bucket; `configuration` is what the
        redeployed process was started with."""
        return World(self.port.reopen(**configuration), self.objects, self.clock, self.kind,
                     self.org)


def fake_world(**options) -> World:
    clock = FakeClock(DEFAULT_START)
    return World(DraftLifecycle(clock, **options), Objects(), clock, "fake")


_PG: dict = {}


def pg_world(**options) -> World:
    """A fresh `m6` schema on the task-local PostgreSQL, or a visible skip."""
    from ..d import pgharness
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    database = f"{pgharness.DATABASE}_retention"
    if not _PG:
        pgharness.ensure()
        pgharness.recreate(database)
        _PG["dsn"] = pgharness.dsn(database)
    if "clock" in _PG:
        _PG["clock"].conn.close()
    with pgharness.connect(database) as conn:
        conn.execute(SCHEMA)
    _PG["clock"] = clock = PgClock(_PG["dsn"])
    return World(PgLifecycleStandIn(_PG["dsn"], **options), Objects(), clock, "pg")


class F2CRuntime:
    """F2C-L's committed reference fake (`contracts.fakes.lifecycle.FakeLifecycle`, 2d5e4743)
    behind the world's runtime hooks: a source is registered `written` with its digest, a
    job references it by being admitted with it (`admit_ready`, one job per attach), and a
    job ends by cancellation. The port calls go straight to the fake. What F2C.a has no
    operation for yet (a row-level hold, database bodies and their scrub, a result expiry:
    slice b) is not offered, so the cases needing it run on the two stand-ins only."""

    def __init__(self, store, harness, refs: dict, jobs: dict) -> None:
        self.store, self.harness, self.refs, self.jobs = store, harness, refs, jobs

    def __getattr__(self, name):
        return getattr(self.store, name)

    def reopen(self, **configuration) -> F2CRuntime:
        assert not configuration, "the F2C fake reopens with its own configuration"
        return F2CRuntime(self.store.reopen(), self.harness, self.refs, self.jobs)

    async def register(self, identity: Identity, *, hold_s: float | None = None):
        from infrx.contracts.v2.lifecycle import ContentIdentity
        assert hold_s is None and identity.kind == "source", "not an F2C.a operation"
        row = await self.store.register(ContentIdentity(
            org_id=identity.org_id, kind=identity.kind, location=identity.location,
            object_key=identity.object_key, digest=identity.digest, bytes=identity.bytes,
            origin="written"))
        self.refs[row.content_id] = MediaRef(
            org_id=identity.org_id, handle="med_" + identity.digest[7:47], kind=MediaKind.url,
            digest=identity.digest, bytes=identity.bytes, mime="video/mp4", duration_s=4.0,
            storage_ref=identity.object_key)
        return row

    async def admit(self, job_id: str, **meta) -> None:
        """Nothing yet: in F2C.a a job exists from the admission that names its sources."""

    async def attach(self, job_id: str, content_id: str) -> None:
        from infrx.contracts.conformance import lifecycle as cases
        request = cases._request(self.harness, (self.refs[content_id],))
        admission, _ = await self.store.admit_ready(request, cases._idem(request, new_id()),
                                                    cases.CARD)
        self.jobs[job_id] = (admission.request_id, admission.job_handle)

    async def finish(self, job_id: str, state: str, *, result_ttl_s: float | None = None) -> None:
        from infrx.contracts.conformance import lifecycle as cases
        assert result_ttl_s is None, "a result expiry is F2C slice b"
        await self.store.jobs.cancel(cases.ORG, self.jobs[job_id][1])

    async def row(self, content_id: str):
        return self.store.d.content[content_id]

    async def job_live(self, job_id: str) -> bool:
        return job_id in self.jobs and self.store._job_live(self.jobs[job_id][0],
                                                            self.harness.clock.now())

    async def readable(self, content_id: str) -> bool:
        return self.store.d.content[content_id].state == "live"


def f2c_world() -> World:
    from infrx.contracts.conformance import lifecycle as cases
    from infrx.contracts.fakes.factories import credit_jobstore_factory
    from infrx.contracts.fakes.lifecycle import FakeLifecycle
    credit = credit_jobstore_factory()
    fake = FakeLifecycle(credit.port, credit.clock, credit.ids, upload_ttl_s=GRACE_S * 6,
                         grace_s=GRACE_S, claim_ttl_s=CLAIM_TTL_S, retention_s=RETENTION_S)
    return World(F2CRuntime(fake, credit, {}, {}), Objects(), credit.clock, "f2c", cases.ORG)


WORLDS = {"f2c": f2c_world, "fake": fake_world, "pg": pg_world}
#: `INFRX_M6_WORLDS=f2c,fake` narrows the worlds: the mutant runs, one pytest process per
#: mutant, then start no container.
SELECTED = tuple(name for name in os.environ.get("INFRX_M6_WORLDS", "f2c,fake,pg").split(",")
                 if name in WORLDS)
#: The worlds with every operation point 2 needs (holds, database content, result expiry).
DRAFT_WORLDS = tuple(name for name in ("fake", "pg") if name in SELECTED)


@pytest.fixture(params=SELECTED)
def make_world(request):
    return WORLDS[request.param]


@pytest.fixture(params=DRAFT_WORLDS)
def make_draft_world(request):
    return WORLDS[request.param]


def run(coroutine):
    return asyncio.run(coroutine)


def key(n: int, part: str = "source", org: str = b.ORG_A) -> str:
    return f"media/{org}/v1/{n:016x}/{part}"


# --- the port rules both stand-ins keep (what D10.b's adapter must pass) ----------------
def test_registration_is_idempotent_and_never_resets_eligibility(make_world):
    world = make_world()
    first = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S - 1)
    again = run(world.write("source", key(1)))
    assert again == first
    world.clock.advance(1)                                          # equality: it has passed
    page = run(world.port.candidates(after=None, limit=10))
    assert [row.content_id for row in page.items] == [first.content_id]


def test_a_tombstoned_key_refuses_new_use_and_a_deleted_one_is_the_next_generation(make_world):
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    tomb = run(world.port.tombstone(run(world.port.claim(row.content_id, 1, "t"))))
    job = new_id()
    run(world.port.admit(job))
    for use in (world.write("source", key(1)), world.port.attach(job, row.content_id)):
        with pytest.raises(errors.DependencyUnavailable) as refused:
            run(use)
        assert refused.value.refusal == "content_retiring"
    run(world.port.acknowledge_delete(tomb))
    reborn = run(world.write("source", key(1), b"again"))
    assert (reborn.content_id, reborn.generation, reborn.state) == (row.content_id, 2, "live")
    assert run(world.objects.get(generation_key(key(1), 2))) == b"again"
    # the delayed acknowledgement of generation 1 changes nothing about generation 2
    assert run(world.port.acknowledge_delete(tomb)).state == "live"


def test_an_expired_claim_cannot_tombstone_and_a_superseded_fence_cannot_acknowledge(make_world):
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    stale = run(world.port.claim(row.content_id, 1, "a"))
    world.clock.advance(CLAIM_TTL_S)                                # equality: expired
    with pytest.raises(errors.StaleLease):
        run(world.port.tombstone(stale))
    fresh = run(world.port.claim(row.content_id, 1, "b"))
    assert fresh.fence == stale.fence + 1
    tomb = run(world.port.tombstone(fresh))
    world.clock.advance(CLAIM_TTL_S)
    newer = run(world.port.claim(row.content_id, 1, "c"))          # re-claim the tombstone
    with pytest.raises(errors.StaleLease):
        run(world.port.acknowledge_delete(tomb))
    assert run(world.port.row(row.content_id)).state == "tombstoned"
    assert run(world.port.acknowledge_delete(run(world.port.tombstone(newer)))).state == "deleted"
