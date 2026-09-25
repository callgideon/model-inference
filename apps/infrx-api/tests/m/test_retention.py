#!/usr/bin/env python3
"""M6: the collector deletes only what the durable lifecycle store says may go (RV-03).

    INFRX_D_TASK=m6 uv run --frozen pytest -q tests/m/test_retention.py
    INFRX_M6_ORACLE=local_only INFRX_D_TASK=m6 uv run --frozen pytest -q tests/m/test_retention.py

The second line runs every case against a local-only liveness view (`LocalOnly`, the shape
of M3's retired `gc.MediaCollector`): they fail, which is the regression record. Each case
runs on F2C's reference adapter and on D10's PostgreSQL (`worlds.py`); time is the store's
clock, moved by hand.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import re
import time
from types import SimpleNamespace

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import lifecycle as cases
from infrx.contracts.conformance.harness import hook
from infrx.contracts.fakes.support import DEFAULT_START
from infrx.contracts.v2.lifecycle import LifecycleRefusal as R, refusal_of
from infrx.media import retention

from . import support
from .worlds import (CLAIM_TTL_S, GRACE_S, ORG, RETENTION_S, UPLOAD_TTL_S,  # noqa: F401
                     key, make_d10_world, make_world, run)

ORACLE = os.environ.get("INFRX_M6_ORACLE", "")
#: The object store's worst-case delete, scaled to the fixture claim TTL (production: 75 s
#: against a claim TTL of at least 300 s).
DELETE_TIMEOUT_S = CLAIM_TTL_S / 6


class LocalOnly:
    """The failure oracle: M3's retired collector's liveness view. It lists the bucket and
    deletes whatever this process's maps do not protect - after a restart, nothing - once
    it has been idle for the grace, counted from this process's first sight of it."""

    def __init__(self, world) -> None:
        self.world, self.seen = world, {}

    async def sweep(self):
        now, gone = self.world.clock.now(), []
        for object_key in await self.world.objects.keys(""):
            if (now - self.seen.setdefault(object_key, now)).total_seconds() >= GRACE_S:
                await self.world.objects.delete(object_key)
                gone.append(("object_store", object_key, 1))
        return SimpleNamespace(deleted=gone)


def collector(world, *, port=None, objects=None, **options):
    if ORACLE == "local_only":
        return LocalOnly(world)
    options.setdefault("holder", "collector-1")
    options.setdefault("delete_timeout_s", DELETE_TIMEOUT_S)
    # The collector measures its lease on a local monotonic clock; here time is the
    # store's, moved by hand, so the collector reads that.
    options.setdefault("clock", lambda: (world.clock.now() - DEFAULT_START).total_seconds())
    return retention.RetentionCollector(port or world.port, objects or world.objects, **options)


def deleted(report) -> list[str]:
    return [object_key for _, object_key, _ in report.deleted]


class Interpose:
    """The port with a hook run before the named calls: the other side of a race."""

    def __init__(self, port, **before) -> None:
        self.port, self.before = port, before

    def __getattr__(self, name):
        target, before = getattr(self.port, name), self.before.get(name)
        if before is None:
            return target

        async def call(*args, **kwargs):
            await before(*args, **kwargs)
            return await target(*args, **kwargs)
        return call


class FailingDeletes:
    """The object store, refusing its next `failures` deletes as S3ObjectStore does."""

    def __init__(self, inner, failures: int) -> None:
        self.inner, self.failures = inner, failures

    async def delete(self, object_key: str) -> None:
        if self.failures:
            self.failures -= 1
            raise errors.DependencyUnavailable("the media object store did not answer")
        await self.inner.delete(object_key)


class Delayed:
    """The first delete is sent and then held in flight until released."""

    def __init__(self, inner) -> None:
        self.inner, self.held = inner, True
        self.sent, self.release = asyncio.Event(), asyncio.Event()

    async def delete(self, object_key: str) -> None:
        if self.held:
            self.held = False
            self.sent.set()
            await self.release.wait()
        await self.inner.delete(object_key)


class LostResponse:
    """The delete reaches the store and the answer does not come back."""

    def __init__(self, inner) -> None:
        self.inner, self.lost = inner, 1

    async def delete(self, object_key: str) -> None:
        await self.inner.delete(object_key)
        if self.lost:
            self.lost -= 1
            raise errors.DependencyUnavailable("the media object store did not answer")


async def until(predicate, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "the schedule never reached its point"
        await asyncio.sleep(0.01)


def advance_to(world, instant, *, before_s: float = 0.0) -> None:
    world.clock.advance((instant - world.clock.now()).total_seconds() - before_s)


def live_source(world, n: int = 1):
    row = run(world.write("source", key(n)))
    return run(world.admit(row)), row


async def refused_as(call, reason: R) -> None:
    with pytest.raises(errors.DomainError) as refused:
        await call
    assert refusal_of(refused.value) is reason, refused.value


def data_url(clip: bytes) -> str:
    return "data:video/mp4;base64," + base64.b64encode(clip).decode()


# --- point 1: durable candidates, protected references, fenced claims ------------------
def test_a_restarted_collector_keeps_a_live_jobs_source(make_world):
    """RV-03: a restarted process knows nothing, and the durable reference still protects
    a live job's source for as long as the job lives - then until its persisted
    `retain_until`, exactly: kept a microsecond before it, collected at it."""
    world = make_world()
    job, row = live_source(world)
    world.clock.advance(GRACE_S * 3)
    world = world.restart()
    sweeper = collector(world)
    for _ in range(2):
        run(sweeper.sweep())
        world.clock.advance(GRACE_S)
    assert run(world.present(row))
    run(world.finish(job))
    (reference,) = run(world.port.references(row.content_id))
    advance_to(world, reference.retain_until, before_s=1e-6)
    run(sweeper.sweep())
    assert run(world.present(row))
    advance_to(world, reference.retain_until)
    run(sweeper.sweep())
    assert key(1) not in world.objects.objects
    assert run(world.row(row.content_id)).state == "deleted"


def test_the_local_only_view_deletes_a_live_jobs_source_after_a_restart(make_world):
    """The failure oracle, kept executable (and on PostgreSQL): a collector whose liveness
    is this process's maps deletes the source of a job the database says is live."""
    world = make_world()
    job, row = live_source(world)
    world = world.restart()
    old = LocalOnly(world)
    run(old.sweep())
    world.clock.advance(GRACE_S)
    run(old.sweep())
    assert run(world.job_live(job))
    assert key(1) not in world.objects.objects


def test_candidates_come_from_the_store_in_bounded_pages(make_world):
    """Nothing is listed from the bucket: the store's candidates, `page_size` at a time,
    until the store says there are no more."""
    world = make_world()
    for n in range(5):
        run(world.write("source", key(n)))
    world.clock.advance(GRACE_S)
    limits = []

    async def asked(*, after, limit):
        limits.append(limit)

    async def suspends(*args):                    # a real store's round trip yields
        await asyncio.sleep(0)
    port = Interpose(world.port, candidates=asked, claim=suspends)
    report = run(collector(world, port=port, page_size=2, concurrency=1).sweep())
    assert sorted(deleted(report)) == sorted(key(n) for n in range(5))
    assert (report.pages, report.max_batch, limits) == (3, 2, [2, 2, 2])
    assert report.max_in_flight == 1
    assert world.objects.objects == {}


@pytest.mark.parametrize("stray", ["secrets/keys.json", "media/../payloads/x.json",
                                   "../media/x/source",
                                   f"media/{ORG}/../../payloads/x.json"])
def test_a_row_naming_a_key_outside_the_media_prefixes_is_never_deleted(make_world, stray):
    """A corrupt or foreign row must not reach the bucket: only keys M writes (media/,
    uploads/, payloads/) and no `..` segment; anything else is kept and reported. D10
    refuses such a row at registration (0019: not under this organization's prefix); on
    the reference adapter the collector is what keeps it."""
    world = make_world()
    if world.kind == "d10":
        with pytest.raises(errors.NotFound):
            run(world.write("source", stray))
        return
    run(world.write("source", stray))
    run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    report = run(collector(world).sweep())
    assert deleted(report) == [key(1)] and report.retained == {"foreign_key": 1}
    assert stray in world.objects.objects


def test_an_attach_before_the_claim_or_the_tombstone_keeps_the_object(make_world):
    """Eligibility is established atomically with attachment: an admission landing after
    the candidate was listed - before its claim, or between claim and tombstone - is seen
    by the store's recheck, and the object stays."""
    world = make_world()
    rows = [run(world.write("source", key(n))) for n in (1, 2)]
    world.clock.advance(GRACE_S)

    async def before_claim(content_id, generation, holder):
        if content_id == rows[0].content_id:
            await world.admit(rows[0])

    async def before_tombstone(claim):
        if claim.content_id == rows[1].content_id:
            await world.admit(rows[1])
    port = Interpose(world.port, claim=before_claim, tombstone=before_tombstone)
    report = run(collector(world, port=port).sweep())
    assert deleted(report) == [] and report.retained == {"reference_live": 2}
    assert [run(world.present(row)) for row in rows] == [True, True]


def test_an_attach_after_the_tombstone_is_refused_and_the_key_comes_back_new(make_world):
    """Once the tombstone commits, admission is refused (`content_retiring`, a retryable
    503) rather than bound to bytes about to vanish; after the acknowledgement the same key
    is a NEW generation, never the deleted one revived."""
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    refusals = []

    class AttachDuringDelete:
        async def delete(self, object_key):
            try:
                await world.admit(row)
            except errors.DependencyUnavailable as refused:
                refusals.append(refusal_of(refused))
            await world.objects.delete(object_key)
    report = run(collector(world, objects=AttachDuringDelete()).sweep())
    assert deleted(report) == [key(1)] and refusals == [R.content_retiring]
    assert run(world.row(row.content_id)).state == "deleted"
    again = run(world.write("source", key(1), b"restaged"))
    assert again.generation == 2 and run(world.present(again))


def test_a_collector_whose_claim_expired_deletes_nothing(make_world):
    """A claim is a lease: a collector that stalls past it cannot tombstone, so it never
    reaches the delete; the next collector's fresh claim does."""
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)

    async def stall(claim):
        world.clock.advance(CLAIM_TTL_S)                  # equality: the lease has lapsed
    report = run(collector(world, port=Interpose(world.port, tombstone=stall)).sweep())
    assert deleted(report) == [] and report.retained == {"claim_lost": 1}
    assert run(world.present(row))
    assert deleted(run(collector(world, holder="collector-2").sweep())) == [key(1)]


@pytest.mark.parametrize("broken", ["candidates", "claim", "tombstone"])
def test_an_unavailable_database_deletes_nothing_and_says_so(make_world, broken):
    """Retain and report: a store that cannot answer ends the pass with nothing deleted."""
    world = make_world()
    rows = [run(world.write("source", key(n))) for n in (1, 2)]
    world.clock.advance(GRACE_S)

    async def down(*args, **kwargs):
        raise errors.DependencyUnavailable("the lifecycle database did not answer")
    report = run(collector(world, port=Interpose(world.port, **{broken: down})).sweep())
    assert report.aborted == "dependency_unavailable" and deleted(report) == []
    assert [run(world.present(row)) for row in rows] == [True, True]


def test_a_lost_delete_acknowledgement_is_finished_by_a_later_pass(make_world):
    """The object is gone and the acknowledgement never reached the store: the row stays
    tombstoned (unreadable), becomes a candidate once the claim lapses, and the delete is
    repeated - harmlessly - before it is acknowledged. The wait is reported."""
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)

    async def lost(tomb):
        raise errors.DependencyUnavailable("the acknowledgement was lost")
    report = run(collector(world, port=Interpose(world.port, acknowledge_delete=lost)).sweep())
    assert deleted(report) == [key(1)] and report.ack_lost == 1
    assert run(world.row(row.content_id)).state == "tombstoned"
    assert deleted(run(collector(world).sweep())) == []        # the claim still holds
    world.clock.advance(CLAIM_TTL_S * 2)
    report = run(collector(world).sweep())
    assert deleted(report) == [key(1)] and report.max_pending_delete_s == CLAIM_TTL_S * 2
    assert run(world.row(row.content_id)).state == "deleted"
    assert world.objects.deletes[key(1)] == 2


def test_a_failed_object_delete_stays_tombstoned_unreadable_and_is_retried(make_world):
    """An object store that refuses the delete ends the pass (it would refuse the rest
    too): the tombstoned object is unreadable although its bytes are still there, the
    untouched one is still live, and a later pass finishes both."""
    world = make_world()
    rows = [run(world.write("source", key(n))) for n in (1, 2)]
    world.clock.advance(GRACE_S)
    report = run(collector(world, objects=FailingDeletes(world.objects, 1),
                           concurrency=1).sweep())
    assert (report.delete_failed, report.aborted) == (1, "object_store_unavailable")
    assert deleted(report) == []
    # which one the store offers first is its choice (equal `eligible_at`: content_id order)
    first, second = sorted(rows, key=lambda row: run(world.row(row.content_id)).state
                           != "tombstoned")
    assert run(world.row(first.content_id)).state == "tombstoned"
    assert not run(world.present(first)) and first.identity.object_key in world.objects.objects
    assert run(world.present(second))
    world.clock.advance(CLAIM_TTL_S)
    assert sorted(deleted(run(collector(world).sweep()))) == [key(1), key(2)]
    assert world.objects.objects == {}


def test_a_delayed_delete_holds_its_key_until_it_is_acknowledged(make_world):
    """A delete held in flight: while its lease lasts no other collector can take the key
    (it is not even a candidate) and no writer can write the key again
    (`content_retiring`, before a byte is written); only once the delete returned and was
    acknowledged is the key written again - generation 2, which the late delete never
    reaches. (A delete about to outlive its lease is not sent: the lease-margin case.)"""
    world = make_world()
    row = run(world.write("source", key(1), b"first"))
    world.clock.advance(GRACE_S)

    async def schedule():
        slow = Delayed(world.objects)
        first = asyncio.ensure_future(collector(world, objects=slow, holder="slow").sweep())
        await asyncio.wait_for(slow.sent.wait(), 10)
        assert deleted(await collector(world, holder="fast").sweep()) == []
        await refused_as(world.write("source", key(1), b"second"), R.content_retiring)
        assert await world.objects.get(key(1)) == b"first"      # the refused writer wrote nothing
        slow.release.set()
        report = await first
        return report, await world.write("source", key(1), b"second")
    report, reborn = run(schedule())
    assert deleted(report) == [key(1)] and run(world.row(row.content_id)).generation == 2
    assert reborn.generation == 2 and run(world.objects.get(key(1))) == b"second"
    world.clock.advance(GRACE_S)
    assert deleted(run(collector(world).sweep())) == [key(1)]
    assert world.objects.objects == {}


@pytest.mark.parametrize("over_s", [0.001, 0.0], ids=["short", "equality"])
def test_the_delete_is_sent_only_while_the_claim_has_a_request_timeout_left(make_world, over_s):
    """D10's rule (0020): the external delete is sent only while the claim still has one
    object-store request timeout left, so it has landed or been abandoned before the lease
    can pass to another collector - what keeps a stalled delete off the key's next
    generation. Short of that margin the object stays tombstoned (unreadable) and a later
    pass deletes it on a fresh claim; at equality the delete is sent."""
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)

    async def slow(claim):
        world.clock.advance(CLAIM_TTL_S - DELETE_TIMEOUT_S + over_s)
    report = run(collector(world, port=Interpose(world.port, tombstone=slow)).sweep())
    if over_s:
        assert deleted(report) == [] and report.retained == {"lease_short": 1}
        assert key(1) in world.objects.objects and not run(world.present(row))
        assert run(world.row(row.content_id)).state == "tombstoned"
        world.clock.advance(DELETE_TIMEOUT_S)                     # the claim has lapsed
        report = run(collector(world).sweep())
    assert deleted(report) == [key(1)] and world.objects.objects == {}
    assert run(world.row(row.content_id)).state == "deleted"


def test_the_grace_boundary_is_exact_on_the_store_clock(make_world):
    world = make_world()
    run(world.write("source", key(1)))
    world.clock.advance(GRACE_S - 0.001)
    assert deleted(run(collector(world).sweep())) == []
    world.clock.advance(0.001)
    assert deleted(run(collector(world).sweep())) == [key(1)]


# --- the reference lock, on D10's own SQL ---------------------------------------------------
# Each variant is one edit to a function 0020 ships, applied to the case's own database: a
# real way to write the tombstone wrong. `before_claim`: the admission is open before the
# sweep starts; `before_tombstone`: it opens after the claim committed.
BROKEN = {
    # the content row read without its lock: claim/tombstone never wait for an admission
    "no_reference_lock": ("content_row", "where content_id = p_id for update;",
                          "where content_id = p_id;"),
    # the reference recheck only in the claim's earlier transaction, not the tombstone's
    "recheck_outside": ("content_tombstone", "  if c.state = 'live' then\n    perform "
                        "infrx.content_recheck(c, v_now);\n", "  if c.state = 'live' then\n"),
    # the tombstone reads the row without FOR UPDATE; claim and admission still lock it
    "tombstone_unlocked": ("content_tombstone",
                           "  c := infrx.content_row((k->>'content_id')::uuid);",
                           "  select * into c from infrx.content_objects "
                           "where content_id = (k->>'content_id')::uuid;"),
}
# (No lock before the claim is no race: the claim's own UPDATE waits for the admission's
# FOR SHARE, and the tombstone's recheck, a later transaction, then sees the reference.)
RACES = [("before_claim", None), ("before_tombstone", None),
         ("before_tombstone", "no_reference_lock"), ("before_tombstone", "recheck_outside"),
         ("before_tombstone", "tombstone_unlocked")]


def break_d10(world, broken: str) -> None:
    """Replace one 0020 function in this case's database with its broken variant."""
    from infrx.state import migrations
    function, old, new = BROKEN[broken]
    text = (migrations.DIR / "0020_content_lifecycle.sql").read_text()
    start = text.index(f"create or replace function infrx.{function}(")
    body = text[start:text.index("end $$;", start) + len("end $$;")]
    assert body.count(old) == 1, (broken, body.count(old))
    world.harness.extra["credit_conn"].execute(body.replace(old, new))


class HeldOpen:
    """A `Connect` whose transaction commits only once `release` is set: an admission
    holding its row locks, its manifest written but not committed."""

    def __init__(self, dsn: str) -> None:
        self.dsn, self.inserted, self.release = dsn, asyncio.Event(), asyncio.Event()

    async def __call__(self):
        import psycopg
        conn = await psycopg.AsyncConnection.connect(self.dsn, prepare_threshold=None)
        await conn.execute("set role service_role")
        held = self

        class Connection:
            async def execute(self, *args, **kwargs):
                return await conn.execute(*args, **kwargs)

            async def close(self):
                try:
                    held.inserted.set()
                    await held.release.wait()
                    with contextlib.suppress(Exception):
                        await conn.commit()
                finally:
                    await conn.close()
        return Connection()


@pytest.mark.parametrize("schedule,broken", RACES,
                         ids=[f"{s}-{b or 'row_lock'}" for s, b in RACES])
def test_pg_an_attach_racing_the_delete_serializes_on_the_content_row(make_d10_world,
                                                                     schedule, broken):
    """D10's PostgreSQL, two sessions: an admission holds its transaction open, its
    manifest written but not committed, while the collector runs into it - before its
    claim, or after the claim committed and before the tombstone. With 0020 as shipped the
    collector waits for the admission and its in-transaction recheck then sees the
    reference. Each broken variant (`BROKEN`: no lock at all, the recheck outside the
    tombstone's transaction, a tombstone that does not lock) deletes the live job's source
    in the schedule that reaches it - the races this lock exists for."""
    from infrx.state.lifecycle import PgLifecycle

    from ..d import pgharness
    world = make_d10_world()
    if broken:
        break_d10(world, broken)
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    conn = world.harness.extra["credit_conn"]

    def waiting() -> bool:
        return conn.execute("select count(*) from pg_stat_activity where wait_event_type = "
                            "'Lock' and datname = current_database()").fetchone()[0] > 0

    async def race():
        held = HeldOpen(pgharness.dsn(world.harness.extra["database"]))
        admitting = []

        async def open_admission(*args):
            request = cases._request(world.harness, (world.refs[row.content_id],))
            admitting.append(asyncio.ensure_future(PgLifecycle(held, retention_s=RETENTION_S)
                                                   .admit_ready(request, cases._idem(
                                                       request, "held"), cases.CARD)))
            await asyncio.wait_for(held.inserted.wait(), 10)
        port = world.port
        if schedule == "before_claim":
            await open_admission()
        else:
            port = Interpose(port, tombstone=open_admission)
        sweeping = asyncio.ensure_future(collector(world, port=port).sweep())
        await until(lambda: sweeping.done() or waiting())
        held.release.set()
        admission, _ = await admitting[0]
        return admission, await sweeping
    admission, report = run(race())
    job = SimpleNamespace(handle=admission.job_handle)
    violated = run(world.job_live(job)) and key(1) not in world.objects.objects
    if broken is None:
        assert not violated and report.retained == {"reference_live": 1}
    else:
        assert violated, report


# --- point 2: every content kind, database content, the persisted result boundary ----------
RESULT_TTL_S = RETENTION_S * 2           # the configured result lifetime at settlement


def job_content(world, rows: dict):
    """What a job writes beside its source, registered before completion: the prepared
    artifact and the staged envelope (objects) and - in the database - its request text."""
    async def written(job):
        rows["prepared"] = await world.write("prepared", key(1, "prepared"), b"frames",
                                             job=job.request_id)
        rows["payload"] = await world.write("payload", f"payloads/{ORG}/{job.request_id}.json",
                                            b'{"messages": "the request"}', job=job.request_id)
        rows["request_text"] = await world.database("payload", job.request_id)
    return written


def succeeded_with_everything(world):
    """A succeeded job and every kind of content it has; the result in the database."""
    hook(world.harness, "retune")(result_ttl_s=RESULT_TTL_S)
    rows = {"source": run(world.write("source", key(1)))}
    job, outcome = run(world.succeed(rows["source"], before_complete=job_content(world, rows)))
    rows["result"] = run(world.database("result", job.request_id))
    return job, outcome, rows


def financial(world, job) -> tuple:
    """The job's record - admission, outcome (state, usage, settlement, expiry) - and the
    wallet: what a scrub must never move."""
    admission, outcome = run(world.jobs.get_owned_credit(ORG, job.handle))
    return admission, outcome, cases._balances(world.harness)


def test_every_content_kind_goes_and_the_financial_metadata_stays(make_world):
    """Uploaded, staged and prepared media, the payload envelope, the request text and the
    result body in the database, each at its persisted boundary (F2C.b): an upload's
    destination once its ticket closed and its grace passed, the result exactly at
    `result_expires_at`, everything else at settlement + the serving retention. The job's
    record and the wallet are exactly what they were (D3): content is scrubbed, never the
    record."""
    world = make_world()
    upload = run(world.destination(b"bytes"))
    job, outcome, rows = succeeded_with_everything(world)
    before = financial(world, job)
    retained = {n: r for n, r in rows.items() if n != "result"}
    (reference,) = run(world.port.references(rows["source"].content_id))
    advance_to(world, reference.retain_until, before_s=1e-6)
    report = run(collector(world).sweep())
    assert deleted(report) == [upload.identity.object_key]     # its window closed long ago
    assert all(run(world.row(r.content_id)).state == "live" for r in rows.values())
    advance_to(world, reference.retain_until)
    report = run(collector(world).sweep())
    assert sorted(deleted(report)) == sorted(r.identity.object_key for r in retained.values())
    assert world.objects.objects == {}
    assert run(world.row(rows["result"].content_id)).state == "live"   # its own, later
    assert run(world.result_read(job)) == "available"
    advance_to(world, outcome.result_expires_at, before_s=1e-6)
    assert deleted(run(collector(world).sweep())) == []
    advance_to(world, outcome.result_expires_at)
    assert deleted(run(collector(world).sweep())) == [f"job_results/{job.request_id}"]
    assert not any(k.startswith(("jobs/", "job_results/")) for k in world.objects.deletes), \
        "database content was sent to the object store"
    assert financial(world, job) == before
    if world.kind == "d10":           # the scrub itself: bodies emptied, rows and sizes kept
        body, scrubbed, size = world.sql("select body, scrubbed_at, bytes from "
                                         "infrx.job_results where request_id = %s",
                                         job.request_id)
        assert (body, scrubbed is not None, size > 0) == ("", True, True)
        messages, text_scrubbed = world.sql(
            "select request_record ? 'messages', content_scrubbed_at from infrx.jobs "
            "where request_id = %s", job.request_id)
        assert (messages, text_scrubbed is not None) == (False, True)


def test_the_persisted_result_expiry_is_the_boundary_not_todays_configuration(make_world):
    """RV-11's cleanup half: the result expires at the `result_expires_at` persisted at
    settlement. A later configuration (a longer result TTL, a redeploy with a longer
    retention) extends nothing, and the result reads `expired` from that instant - before
    any collector has run."""
    world = make_world()
    job, outcome, rows = succeeded_with_everything(world)
    hook(world.harness, "retune")(result_ttl_s=RESULT_TTL_S * 10)
    world = world.restart()
    world.port.retention_s = RETENTION_S * 10
    advance_to(world, outcome.result_expires_at, before_s=1e-6)
    assert run(world.result_read(job)) == "available"
    advance_to(world, outcome.result_expires_at)
    assert run(world.result_read(job)) == "expired"             # before any deletion
    assert run(world.row(rows["result"].content_id)).state == "live"
    report = run(collector(world).sweep())
    assert f"job_results/{job.request_id}" in deleted(report)
    assert run(world.row(rows["result"].content_id)).state == "deleted"
    assert run(world.result_read(job)) == "expired"


def test_a_failed_scrub_keeps_the_expired_result_unreadable_and_is_retried(make_world):
    """The scrub is the acknowledgement's transaction (F2C.b). A database that fails it
    leaves the body in place, the row tombstoned and the result unreadable; the pass says
    so, and a later one finishes it."""
    world = make_world()
    hook(world.harness, "retune")(result_ttl_s=RESULT_TTL_S)
    job, outcome = run(world.succeed())
    result = run(world.database("result", job.request_id))
    advance_to(world, outcome.result_expires_at)

    async def down(tombstone):
        if tombstone.content_id == result.content_id:
            raise errors.DependencyUnavailable("the database did not answer")
    report = run(collector(world, port=Interpose(world.port, acknowledge_delete=down),
                           concurrency=1).sweep())
    assert (report.ack_lost, report.aborted) == (1, "dependency_unavailable")
    assert run(world.row(result.content_id)).state == "tombstoned"
    assert run(world.result_read(job)) == "expired"
    if world.kind == "d10":
        assert world.sql("select body from infrx.job_results where request_id = %s",
                         job.request_id)[0] != ""
    world.clock.advance(CLAIM_TTL_S)
    assert f"job_results/{job.request_id}" in deleted(run(collector(world).sweep()))
    assert run(world.row(result.content_id)).state == "deleted"
    if world.kind == "d10":
        assert world.sql("select body from infrx.job_results where request_id = %s",
                         job.request_id)[0] == ""


def test_late_worker_output_for_an_ended_job_never_becomes_readable(make_world):
    """A worker that lost its lease writes after the job ended and its content was
    collected: the prepared artifact is a new generation nothing live references,
    collected after its own grace; the result, past its persisted expiry, stays `expired`
    - a late result write cannot resurrect it (D10: results are write-once)."""
    world = make_world()
    job, outcome, rows = succeeded_with_everything(world)
    advance_to(world, outcome.result_expires_at)
    run(collector(world).sweep())
    assert world.objects.objects == {}
    late = run(world.write("prepared", key(1, "prepared"), b"late frames", job=job.request_id))
    assert late.generation == rows["prepared"].generation + 1
    if world.kind == "d10":
        with contextlib.suppress(errors.DomainError):
            run(world.jobs.put_result(job.request_id, "a late answer"))
        assert world.sql("select body from infrx.job_results where request_id = %s",
                         job.request_id)[0] == ""
    assert run(world.result_read(job)) == "expired"
    world.clock.advance(GRACE_S)
    assert deleted(run(collector(world).sweep())) == [key(1, "prepared")]


def test_an_interrupted_upload_is_kept_for_its_window_then_collected(make_world):
    """A PUT that stopped half way leaves a partial object at the destination; the open
    ticket's persisted window protects it, and it goes at the window's end - no process
    needs to remember it. (There is no multipart path: `S3ObjectStore` sends one PutObject,
    so no invisible parts exist to abort; see the M6 evidence.)"""
    world = make_world()
    partial = run(world.destination(b"half a cl"))
    world.clock.advance(GRACE_S)
    world = world.restart()
    assert deleted(run(collector(world).sweep())) == []
    assert run(world.present(partial))
    world.clock.advance(UPLOAD_TTL_S - GRACE_S)
    assert deleted(run(collector(world).sweep())) == [partial.identity.object_key]


# --- the runtime's own writers, and the RV-03 probe ------------------------------------------
def media_process(world):
    """A gateway process over the world's store: M5's `MediaUploads` whose ticket
    authority AND content lifecycle are the world's port (the pilot composes D10's
    `PgLifecycle` as both, M5 WR-1)."""
    from .test_uploads import adapter_for
    return adapter_for(objects=world.objects, clock=world.clock, uploads=world.port,
                       content=world.port)


def rows_by_key(world) -> dict:
    """The state of every content row the store holds, by key."""
    if world.kind == "d10":
        return dict(world.harness.extra["credit_conn"].execute(
            "select object_key, state from infrx.content_objects").fetchall())
    return {row.identity.object_key: row.state.value for row in world.port.d.content.values()}


def test_the_runtimes_writers_register_every_object_before_writing_it(make_world):
    """M5/M6's writers: an upload's destination (before the PUT's write), its verified
    source (before the copy), a fetched source (`materialize`) and the staged envelope
    (`stage`, for its request's job) and the prepared artifact (`prepare`) - each has its
    content row, `written`, before its bytes exist, so the collector reaches every object
    the runtime makes."""
    from .test_uploads import CLIP
    world = make_world()
    process = media_process(world)
    order = []
    registering, writing = process.content.register, world.objects.put_if_absent

    async def register(identity):
        order.append(("row", identity.object_key))
        return await registering(identity)

    async def put_if_absent(object_key, data, content_type):
        order.append(("bytes", object_key))
        return await writing(object_key, data, content_type)
    process.content = SimpleNamespace(register=register)
    world.objects.put_if_absent = put_if_absent
    handle = run(process.create_upload(ORG, {"bytes": len(CLIP)}))["upload_handle"]
    run(process.put_upload(ORG, handle, CLIP, "video/mp4"))
    uploaded = run(process.finalize_upload(ORG, handle))
    fetched = run(process.materialize(ORG, data_url(support.mp4(seconds=4.0))))
    request = cases._request(world.harness, (fetched,))
    run(process.stage(ORG, request))
    # And the prepared artifact (`prepare`, for the job), the fix round's R1.
    process.jobs[request.request_id] = ORG
    run(process.attach(request.request_id, (fetched,)))
    (prepared,) = run(process.prepare(request.request_id, "v1"))
    written = [k for step, k in order if step == "bytes"]
    for object_key in written:
        assert ("row", object_key) in order[:order.index(("bytes", object_key))], object_key
    assert set(written) == {process.upload_key(ORG, handle), uploaded.storage_ref,
                            fetched.storage_ref, f"payloads/{ORG}/{request.request_id}.json",
                            prepared.storage_ref}
    assert {k: rows_by_key(world)[k] for k in written} == {k: "live" for k in written}


def test_a_source_fetched_again_is_not_collected_before_its_admission(make_world):
    """Fix round R2/A1 (a known race, strict xfail until wiring request 7 lands): a clip
    fetched again after its first row became eligible gets the old row back (the first
    registration wins, its eligibility never reset), so a collector pass between this
    request's `materialize` and its admission deletes the bytes it just fetched and the
    admission answers `not_found`. Expected once fixed: the admission succeeds and the
    source is still there."""
    world = make_world()
    process = media_process(world)
    source = data_url(support.mp4(seconds=4.0))
    run(process.materialize(ORG, source))
    world.clock.advance(2 * GRACE_S)                 # the first request's row is eligible
    ref = run(process.materialize(ORG, source))      # request 2 fetches the same clip
    run(collector(world).sweep())                    # a pass before request 2's admission
    request = cases._request(world.harness, (ref,))
    run(process.stage(ORG, request))
    run(world.port.admit_ready(request, cases._idem(request, "refetch"), cases.CARD))
    assert run(world.objects.get(ref.storage_ref)) is not None


def test_rv03_probe_the_durable_collector_keeps_a_restarted_gateways_live_upload(make_world):
    """`reproduce.py`'s collector probe (RV-03), rerun against the durable collector: a
    finalized upload, a job admitted on it, the gateway replaced (nothing in memory), a
    collector past the grace. The pre-M6 collector deleted the source here; this one keeps
    it while the job runs and its retention lasts, collects the closed ticket's destination
    and an envelope nothing admitted, and removes the source after the job's retention."""
    from .test_uploads import CLIP
    world = make_world()
    before = media_process(world)
    handle = run(before.create_upload(ORG, {"bytes": len(CLIP)}))["upload_handle"]
    run(before.put_upload(ORG, handle, CLIP, "video/mp4"))
    ref = run(before.finalize_upload(ORG, handle))
    orphan = cases._request(world.harness, (run(before.materialize(ORG, data_url(
        support.mp4(seconds=4.0)))),))
    run(before.stage(ORG, orphan))                 # staged, never admitted
    request = cases._request(world.harness, (ref,))
    admission, _ = run(world.port.admit_ready(request, cases._idem(request, "rv03"),
                                              cases.CARD))
    world = world.restart()
    run(collector(world).sweep())
    world.clock.advance(UPLOAD_TTL_S + GRACE_S)     # past the ticket's window and the grace
    report = run(collector(world, holder="after-restart").sweep())
    assert ref.storage_ref not in deleted(report), "RV-03: a live job's source was deleted"
    assert run(world.objects.get(ref.storage_ref)) == CLIP
    assert before.upload_key(ORG, handle) in deleted(report)
    assert f"payloads/{ORG}/{orphan.request_id}.json" in deleted(report)
    job = SimpleNamespace(handle=admission.job_handle)
    run(world.finish(job))
    world.clock.advance(RETENTION_S)
    assert ref.storage_ref in deleted(run(collector(world).sweep()))


def test_a_write_over_a_key_being_deleted_is_a_retryable_refusal(make_world):
    """The stage-versus-delete race: once the collector's tombstone commits, a writer
    re-creating the same content-addressed key is refused `content_retiring` (a 503 with a
    retry hint) before it writes a byte; after the acknowledgement the same bytes are
    staged again as the key's next generation."""
    world = make_world()
    process = media_process(world)
    source = data_url(support.mp4(seconds=4.0))
    ref = run(process.materialize(ORG, source))
    world.clock.advance(GRACE_S)
    outcomes = []

    class StageDuringDelete:
        async def delete(self, object_key):
            try:
                await process.materialize(ORG, source)
            except errors.DependencyUnavailable as refused:
                outcomes.append((refusal_of(refused), refused.retry_after_s,
                                 object_key in world.objects.objects))
            await world.objects.delete(object_key)
    report = run(collector(world, objects=StageDuringDelete()).sweep())
    assert deleted(report) == [ref.storage_ref]
    assert outcomes == [(R.content_retiring, 5, True)]
    assert run(process.materialize(ORG, source)) == ref
    assert ref.storage_ref in world.objects.objects
    assert rows_by_key(world)[ref.storage_ref] == "live"


# --- point 4: two collectors, a restarted runtime, real outages, load with cleanup -------
def test_two_collectors_at_once_delete_each_object_exactly_once(make_world):
    """Two processes sweep the same store concurrently: the claims divide the work, the
    loser of each claim keeps its hands off (`claim_held`), and every object is deleted
    once - by exactly one of them."""
    world = make_world()
    for n in range(6):
        run(world.write("source", key(n)))
    live_job, live_row = live_source(world, 99)
    world.clock.advance(GRACE_S)

    async def both():
        return await asyncio.gather(collector(world, holder="a", page_size=4).sweep(),
                                    collector(world.restart(), holder="b", page_size=4).sweep())
    first, second = run(both())
    assert set(deleted(first)).isdisjoint(deleted(second))
    assert sorted(deleted(first) + deleted(second)) == sorted(key(n) for n in range(6))
    assert set(world.objects.deletes.values()) == {1}
    assert set(first.retained) | set(second.retained) <= {"claim_held", "claim_lost"}
    assert run(world.present(live_row))


def test_a_delete_whose_answer_was_lost_is_repeated_harmlessly(make_world):
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    report = run(collector(world, objects=LostResponse(world.objects)).sweep())
    assert (report.delete_failed, report.aborted) == (1, "object_store_unavailable")
    assert key(1) not in world.objects.objects
    assert run(world.row(row.content_id)).state == "tombstoned"
    world.clock.advance(CLAIM_TTL_S)
    assert deleted(run(collector(world).sweep())) == [key(1)]
    assert run(world.row(row.content_id)).state == "deleted"


def test_a_runtime_restarted_mid_delete_finishes_it_and_keeps_live_work(make_world):
    """The whole runtime dies between the tombstone and the delete (a crash, a deploy):
    every in-process object is gone. The replacement - new port, new collector - finishes
    the tombstoned delete once the old claim lapses, and never touches a live job's input
    or an object inside its grace."""
    world = make_world()
    doomed = run(world.write("source", key(1)))
    live_job, live_row = live_source(world, 2)
    world.clock.advance(GRACE_S)
    fresh = run(world.write("source", key(3)))

    class Crash(Exception):
        pass

    class DiesBeforeDeleting:
        async def delete(self, object_key):
            raise Crash
    with pytest.raises(Crash):
        run(collector(world, objects=DiesBeforeDeleting()).sweep())
    assert run(world.row(doomed.content_id)).state == "tombstoned"
    world = world.restart()
    assert deleted(run(collector(world, holder="replacement").sweep())) == []  # claim held
    world.clock.advance(CLAIM_TTL_S)
    report = run(collector(world, holder="replacement").sweep())
    assert deleted(report) == [key(1)] and report.max_pending_delete_s == CLAIM_TTL_S
    assert run(world.present(live_row)) and run(world.present(fresh))
    # F2C.d's reconcile rule: the crash does not leave identical media `content_retiring`
    # for ever - the same bytes staged again are the next generation, readable.
    again = run(world.write("source", key(1), b"content"))
    assert (again.content_id, again.generation) == (doomed.content_id, 2)
    assert run(world.present(again))


def test_the_committed_retention_transcripts_hold_for_the_port_the_collector_drives():
    """F2C.d: the RETENTION-DURABLE acceptance transcripts, replayed on the reference
    adapter this suite's `f2c` world wraps (D10's adapter replays the same file)."""
    from infrx.contracts.conformance.acceptance import replay
    from infrx.contracts.fakes.factories import lifecycle_factory
    assert [p for p in replay(lifecycle_factory) if p.startswith("retention_durable__")] == []


def test_the_schedule_survives_a_failed_pass(make_world, caplog):
    """`run()` logs a pass that raised and runs the next one; it never stops collecting."""
    import logging
    world = make_world()
    run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    failures, passes = [1], []

    async def broken(*, after, limit):
        if failures:
            failures.pop()
            raise RuntimeError("a bug in one pass")

    async def sleep(seconds):
        passes.append(seconds)
        if len(passes) == 2:
            raise asyncio.CancelledError
    with caplog.at_level(logging.INFO, logger="infrx.media.retention"):
        with pytest.raises(asyncio.CancelledError):
            run(collector(world, port=Interpose(world.port, candidates=broken)).run(
                5.0, sleep=sleep))
    assert passes == [5.0, 5.0] and "retention sweep failed" in caplog.text
    assert "retention sweep: 1 deleted" in caplog.text
    assert key(1) not in world.objects.objects


def test_one_collector_across_passes_keeps_nothing_between_them(make_world):
    """The production path: `run()` drives ONE collector for every pass. What a pass left
    unfinished (a lost acknowledgement) is the store's to remember, not the instance's: the
    same collector's next pass, once the claim has lapsed, finishes the delete."""
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    lost, passes = [1], []

    async def ack_once_lost(tombstone):
        if lost:
            lost.pop()
            raise errors.DependencyUnavailable("the acknowledgement was lost")

    async def sleep(seconds):
        passes.append(seconds)
        world.clock.advance(CLAIM_TTL_S)
        if len(passes) == 2:
            raise asyncio.CancelledError
    sweeper = collector(world, port=Interpose(world.port, acknowledge_delete=ack_once_lost))
    with pytest.raises(asyncio.CancelledError):
        run(sweeper.run(1.0, sleep=sleep))
    assert run(world.row(row.content_id)).state == "deleted"
    assert world.objects.deletes[key(1)] == 2


CLOSED_PORT = 55471          # the lane's own S3 port, never listened on (a real outage)


def test_pg_an_unreachable_database_fails_the_pass_and_deletes_nothing(make_d10_world, caplog):
    """D10's adapter over a real driver against a port nothing listens on: the pass stops
    typed (`dependency_unavailable`), the schedule logs it and goes on, and nothing is
    deleted."""
    import logging

    from infrx.state.jobstore import connector
    from infrx.state.lifecycle import PgLifecycle

    from ..d import pgharness
    world = make_d10_world()
    run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    dsn = re.sub(r":\d+/", f":{CLOSED_PORT}/", pgharness.dsn(world.harness.extra["database"]),
                 count=1)
    down = PgLifecycle(connector(dsn))
    assert run(collector(world, port=down).sweep()).aborted == "dependency_unavailable"
    passes = []

    async def sleep(seconds):
        passes.append(seconds)
        if len(passes) == 2:
            raise asyncio.CancelledError
    with caplog.at_level(logging.INFO, logger="infrx.media.retention"):
        with pytest.raises(asyncio.CancelledError):
            run(collector(world, port=down).run(1.0, sleep=sleep))
    assert passes == [1.0, 1.0] and caplog.text.count("aborted: dependency_unavailable") == 2
    assert key(1) in world.objects.objects


def test_pg_an_unreachable_object_store_keeps_the_content_tombstoned_and_retried(
        make_d10_world, monkeypatch):
    """The real S3 client against a closed port (an outage, not a mock): the tombstone
    committed, the delete failed typed, the pass stopped (retain and report); the object
    is unreadable, and once a store answers again a later pass finishes it."""
    from infrx.media.s3 import S3ObjectStore
    for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_CONFIG_FILE",
                 "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "m6-closed-port")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "m6-closed-port")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    world = make_d10_world()
    rows = [run(world.write("source", key(n))) for n in (1, 2)]
    world.clock.advance(GRACE_S)
    down = S3ObjectStore.connect("infrx-m6", "test/m6/",
                                 endpoint_url=f"http://127.0.0.1:{CLOSED_PORT}")
    report = run(collector(world, objects=down, concurrency=1).sweep())
    assert (report.delete_failed, report.aborted, deleted(report)) == \
        (1, "object_store_unavailable", [])
    states = sorted(run(world.row(r.content_id)).state.value for r in rows)
    assert states == ["live", "tombstoned"]
    assert sorted(world.objects.objects) == [key(1), key(2)]
    world.clock.advance(CLAIM_TTL_S)
    assert sorted(deleted(run(collector(world).sweep()))) == [key(1), key(2)]


def test_pg_load_and_cleanup_together_never_delete_live_content(make_d10_world):
    """Admissions keep arriving - each a new source plus an OLD eligible object the
    collectors are deleting at that moment - while two collectors sweep with small pages
    and every tenth acknowledgement is lost. Invariant: whatever an admission bound is
    live and present; when the old object is refused (`content_retiring`) the job is
    admitted on its new source alone, never half-bound. Then everything ends and ages out,
    and the collectors remove all of it. The report's bounds (batch <= page, in flight <=
    concurrency) and the pending-delete age are measured and printed for the evidence."""
    from infrx.contracts.limits import DEFAULTS
    world = make_d10_world(limits=DEFAULTS.replace(
        max_active_jobs_per_key=64, max_active_jobs_per_org=64, max_preparing_jobs=64))
    orphans = [run(world.write("source", key(n), b"old %d" % n)) for n in range(60)]
    world.clock.advance(GRACE_S)
    bound, refused, reports, lost, jobs = [], [], [], {"n": 0}, []

    async def flaky_ack(tombstone):
        lost["n"] += 1
        if lost["n"] % 10 == 0:
            raise errors.DependencyUnavailable("the acknowledgement was lost")
    port = Interpose(world.port, acknowledge_delete=flaky_ack)

    async def admissions():
        for i in range(40):
            new = await world.write("source", key(1000 + i), b"new %d" % i)
            old = orphans[-1 - i]            # the collectors walk them from the front
            try:
                jobs.append(await world.admit(new, old))
                bound.extend((new, old))
            except errors.DependencyUnavailable as retiring:
                refused.append(refusal_of(retiring))
                jobs.append(await world.admit(new))
                bound.append(new)
            except errors.NotFound:          # deleted before this admission: not bound
                refused.append(R.not_found)
                jobs.append(await world.admit(new))
                bound.append(new)
            await asyncio.sleep(0)

    async def sweeping(holder):
        for _ in range(6):
            reports.append(await collector(world, port=port, holder=holder, page_size=8,
                                           concurrency=4).sweep())
            world.clock.advance(CLAIM_TTL_S / 2)

    async def together():
        await asyncio.gather(admissions(), sweeping("a"), sweeping("b"))
    started = time.monotonic()
    run(asyncio.wait_for(together(), 180))
    loaded_s = time.monotonic() - started
    for row in bound:
        assert run(world.present(row)), f"a live job lost {row.identity.object_key}"
    assert set(refused) <= {R.content_retiring, R.not_found}
    for job in jobs:
        run(world.finish(job))
    world.clock.advance(RETENTION_S + CLAIM_TTL_S)
    drain = 0                  # passes after recovery: a lost ack ends a pass (retain, report)
    while world.objects.objects and drain < 20:
        drain += 1
        reports.append(run(collector(world, port=port, page_size=8, concurrency=4).sweep()))
        world.clock.advance(CLAIM_TTL_S)
    assert world.objects.objects == {}
    assert max(r.max_batch for r in reports) <= 8 and max(r.max_in_flight for r in reports) <= 4
    pending = max(r.max_pending_delete_s for r in reports)
    assert 0 < pending <= RETENTION_S + 3 * CLAIM_TTL_S
    print("M6-LOAD", {"objects": 100, "bound_old": len(bound) - 40,
                      "refused_old": len(refused),
                      "passes": len(reports), "drain_passes": drain,
                      "deleted": sum(len(r.deleted) for r in reports),
                      "lost_acks": sum(r.ack_lost for r in reports),
                      "lease_short": sum(r.retained["lease_short"] for r in reports),
                      "max_batch": max(r.max_batch for r in reports),
                      "max_in_flight": max(r.max_in_flight for r in reports),
                      "max_pending_delete_s": pending, "load_phase_wall_s": round(loaded_s, 2)})
