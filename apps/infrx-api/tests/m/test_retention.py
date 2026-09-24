#!/usr/bin/env python3
"""M6: the collector deletes only what the durable lifecycle store says may go (RV-03).

    INFRX_D_TASK=m6 uv run --frozen pytest -q tests/m/test_retention.py
    INFRX_M6_ORACLE=local_only INFRX_D_TASK=m6 uv run --frozen pytest -q tests/m/test_retention.py

The second line runs every case against the pre-M6 collector (`gc.MediaCollector`, liveness
from process maps): they fail, which is the regression record. Each case runs on both
stand-ins of the port (`test_lifecycle_standin`: in memory, and real SQL on the task-local
PostgreSQL); time is the store's clock, moved by hand.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from types import SimpleNamespace

import pytest
from infrx.contracts import errors
from infrx.media import gc, retention, uploads

from .test_lifecycle_standin import (CLAIM_TTL_S, GRACE_S, RETENTION_S, key,  # noqa: F401
                                     make_draft_world, make_world, new_id, pg_world, run)

ORACLE = os.environ.get("INFRX_M6_ORACLE", "")


class LocalOnly:
    """The pre-M6 collector as a restarted process runs it: `gc.MediaCollector` over a fresh
    `MediaUploads`, whose maps are empty, so `is_live` is asked about no job (RV-03)."""

    def __init__(self, world) -> None:
        adapter = uploads.MediaUploads(world.objects, now=world.clock.now)
        self.inner = gc.MediaCollector(adapter, is_live=world.port.job_live, grace_s=GRACE_S)

    async def sweep(self):
        swept = await self.inner.sweep()
        return SimpleNamespace(deleted=[("object_store", k, 1) for k in swept.deleted])


def collector(world, *, port=None, objects=None, **options):
    if ORACLE == "local_only":
        return LocalOnly(world)
    options.setdefault("holder", "collector-1")
    return retention.RetentionCollector(port or world.port, objects or world.objects, **options)


def deleted(report) -> list[str]:
    return [object_key for _, object_key, _ in report.deleted]


class Interpose:
    """The port with a hook run before the named calls: the other side of a race."""

    def __init__(self, port, **before) -> None:
        self.port, self.before = port, before

    def __getattr__(self, name):
        target, hook = getattr(self.port, name), self.before.get(name)
        if hook is None:
            return target

        async def call(*args, **kwargs):
            await hook(*args, **kwargs)
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


async def until(predicate, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "the schedule never reached its point"
        await asyncio.sleep(0.01)


def live_source(world, n: int = 1):
    job = new_id()
    run(world.port.admit(job))
    row = run(world.write("source", key(n)))
    run(world.port.attach(job, row.content_id))
    return job, row


# --- point 1: durable candidates, protected references, fenced claims ------------------
def test_a_restarted_collector_keeps_a_live_jobs_source(make_world):
    """RV-03: a restarted process knows nothing, and the durable reference still protects
    a live job's source for as long as the job lives - then until its persisted
    `retain_until`, exactly: kept one second before it, collected at it."""
    world = make_world()
    job, row = live_source(world)
    world.clock.advance(GRACE_S * 3)
    world = world.restart()
    sweeper = collector(world)
    for _ in range(2):
        run(sweeper.sweep())
        world.clock.advance(GRACE_S)
    assert run(world.read(row)) == b"content"
    run(world.port.finish(job, "failed"))
    world.clock.advance(RETENTION_S - 1)
    run(sweeper.sweep())
    assert run(world.read(row)) == b"content"
    world.clock.advance(1)
    run(sweeper.sweep())
    assert run(world.objects.get(key(1))) is None
    assert run(world.port.row(row.content_id)).state == "deleted"


def test_the_local_only_view_deletes_a_live_jobs_source_after_a_restart(make_world):
    """The failure oracle, kept executable (and on PostgreSQL): the pre-M6 collector, with
    liveness from process maps, deletes the source of a job the database says is live."""
    world = make_world()
    job, row = live_source(world)
    world = world.restart()
    old = LocalOnly(world)
    run(old.sweep())
    world.clock.advance(GRACE_S)
    run(old.sweep())
    assert run(world.port.job_live(job))
    assert run(world.objects.get(key(1))) is None


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
                                   "../media/x/source"])
def test_a_row_naming_a_key_outside_the_media_prefixes_is_never_deleted(make_world, stray):
    """A corrupt or foreign row must not reach the bucket: only keys M writes (media/,
    uploads/, payloads/) and no `..` segment; anything else is kept and reported."""
    world = make_world()
    run(world.write("source", stray))
    run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    report = run(collector(world).sweep())
    assert deleted(report) == [key(1)] and report.retained == {"foreign_key": 1}
    assert stray in world.objects.objects


def test_an_attach_before_the_claim_or_the_tombstone_keeps_the_object(make_world):
    """Eligibility is established atomically with attachment: an attach landing after the
    candidate was listed - before its claim, or between claim and tombstone - is seen by
    the store's recheck, and the object stays."""
    world = make_world()
    rows = [run(world.write("source", key(n))) for n in (1, 2)]
    world.clock.advance(GRACE_S)
    jobs = [new_id(), new_id()]
    for job in jobs:
        run(world.port.admit(job))

    async def before_claim(content_id, generation, holder):
        if content_id == rows[0].content_id:
            await world.port.attach(jobs[0], content_id)

    async def before_tombstone(claim):
        if claim.content_id == rows[1].content_id:
            await world.port.attach(jobs[1], claim.content_id)
    port = Interpose(world.port, claim=before_claim, tombstone=before_tombstone)
    report = run(collector(world, port=port).sweep())
    assert deleted(report) == [] and report.retained == {"reference_live": 2}
    assert [run(world.read(row)) for row in rows] == [b"content", b"content"]


def test_an_attach_after_the_tombstone_is_refused_and_the_key_comes_back_new(make_world):
    """Once the tombstone commits, new use is refused (`content_retiring`) rather than
    bound to bytes about to vanish; after the acknowledgement the same key is a NEW
    generation, never the deleted one revived."""
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    job = new_id()
    run(world.port.admit(job))
    refusals = []

    class AttachDuringDelete:
        async def delete(self, object_key):
            try:
                await world.port.attach(job, row.content_id)
            except errors.DependencyUnavailable as refused:
                refusals.append(refused.refusal)
            await world.objects.delete(object_key)
    report = run(collector(world, objects=AttachDuringDelete()).sweep())
    assert deleted(report) == [key(1)] and refusals == ["content_retiring"]
    assert run(world.port.row(row.content_id)).state != "live"
    again = run(world.write("source", key(1), b"restaged"))
    assert again.generation == 2 and run(world.read(again)) == b"restaged"


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
    assert run(world.read(row)) == b"content"
    assert run(world.port.row(row.content_id)).state == "live"
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
    assert [run(world.read(row)) for row in rows] == [b"content", b"content"]


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
    assert run(world.port.row(row.content_id)).state == "tombstoned"
    assert run(world.read(row)) is None
    assert deleted(run(collector(world).sweep())) == []        # the claim still holds
    world.clock.advance(CLAIM_TTL_S * 2)
    report = run(collector(world).sweep())
    assert deleted(report) == [key(1)] and report.max_pending_delete_s == CLAIM_TTL_S * 2
    assert run(world.port.row(row.content_id)).state == "deleted"
    assert world.objects.deletes[key(1)] == 2


def test_a_failed_object_delete_stays_tombstoned_unreadable_and_is_retried(make_world):
    """An object store that refuses the delete ends the pass (it would refuse the rest
    too): the tombstoned object is unreadable although its bytes are still there, the
    untouched one is still live, and a later pass finishes both."""
    world = make_world()
    first, second = (run(world.write("source", key(n))) for n in (1, 2))
    world.clock.advance(GRACE_S)
    report = run(collector(world, objects=FailingDeletes(world.objects, 1),
                           concurrency=1).sweep())
    assert (report.delete_failed, report.aborted) == (1, "object_store_unavailable")
    assert deleted(report) == []
    assert run(world.port.row(first.content_id)).state == "tombstoned"
    assert run(world.read(first)) is None and key(1) in world.objects.objects
    assert run(world.read(second)) == b"content"
    world.clock.advance(CLAIM_TTL_S)
    assert sorted(deleted(run(collector(world).sweep()))) == [key(1), key(2)]
    assert world.objects.objects == {}


def test_a_delayed_delete_cannot_remove_the_next_generation_at_the_same_key(make_world):
    """A delete held in flight past its collector's lease; another collector finishes that
    generation, the same key is written again - and the delayed delete then lands. It
    names generation 1's object only, so generation 2's bytes survive it; generation 2 is
    later collected at its own name."""
    world = make_world()
    run(world.write("source", key(1), b"first"))
    world.clock.advance(GRACE_S)

    async def schedule():
        slow = Delayed(world.objects)
        first = asyncio.ensure_future(collector(world, objects=slow, holder="slow").sweep())
        await asyncio.wait_for(slow.sent.wait(), 10)
        world.clock.advance(CLAIM_TTL_S)
        await collector(world, holder="fast").sweep()
        reborn = await world.write("source", key(1), b"second")
        slow.release.set()
        await first
        return reborn
    reborn = run(schedule())
    assert reborn.generation == 2 and run(world.read(reborn)) == b"second"
    world.clock.advance(GRACE_S)
    assert deleted(run(collector(world).sweep())) == [key(1)]
    assert world.objects.objects == {}


def test_the_grace_boundary_is_exact_on_the_store_clock(make_world):
    world = make_world()
    run(world.write("source", key(1)))
    world.clock.advance(GRACE_S - 0.001)
    assert deleted(run(collector(world).sweep())) == []
    world.clock.advance(0.001)
    assert deleted(run(collector(world).sweep())) == [key(1)]


@pytest.mark.parametrize("locking", [True, False], ids=["row_lock", "no_reference_lock"])
def test_pg_an_attach_racing_the_delete_serializes_on_the_content_row(locking):
    """Real PostgreSQL, two sessions: an attach holds its transaction open with the
    reference written but not committed while a collector runs. With the row lock the
    collector waits for it and then sees the reference; without it (the deliberately
    broken stand-in) the live job's source is deleted - the race this lock exists for."""
    world = pg_world(locking=locking)
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    job = new_id()
    run(world.port.admit(job))

    def waiting() -> bool:
        return world.clock.conn.execute(
            "select count(*) from pg_stat_activity where wait_event_type = 'Lock' "
            "and datname = current_database()").fetchone()[0] > 0

    async def race():
        inserted, commit = asyncio.Event(), asyncio.Event()

        async def hold():
            inserted.set()
            await commit.wait()
        attaching = asyncio.ensure_future(world.port.attach(job, row.content_id, hold=hold))
        await asyncio.wait_for(inserted.wait(), 10)
        sweeping = asyncio.ensure_future(collector(world).sweep())
        await until(lambda: sweeping.done() or waiting())
        commit.set()
        await attaching
        return await sweeping
    report = run(race())
    violated = run(world.port.job_live(job)) and run(world.read(row)) is None
    if locking:
        assert not violated and report.retained == {"reference_live": 1}
    else:
        assert violated


# --- point 2: every content kind, database content, the persisted result boundary ----------
RESULT_TTL_S = RETENTION_S * 2           # the configured result lifetime at settlement


def upload_key(world) -> str:
    return f"uploads/{world.org}/upl_{'u' * 22}"


def content_of(world, job: str) -> dict:
    """One succeeded job's content in every form the collector must reach."""
    return {
        "source": run(world.write("source", key(1))),
        "prepared": run(world.write("prepared", key(1, "prepared"), b"frames", job=job)),
        "payload": run(world.write("payload", f"payloads/{world.org}/{job}.json",
                                   b'{"messages": "the request"}', job=job)),
        "request_text": run(world.write("payload", f"jobs/{job}", b"the request text",
                                        job=job, location="database")),
        "result": run(world.write("result", f"job_results/{job}", b"the answer", job=job,
                                  location="database")),
    }


METADATA = {"idempotency_key": "idem-1", "debit_credit": "400",
            "payload_digest": "sha256:" + "a" * 64, "result_digest": "sha256:" + "b" * 64}


def test_every_content_kind_goes_and_the_financial_metadata_stays(make_draft_world):
    """Uploaded, staged and prepared media, the payload envelope, the request text and the
    result body in the database: all kept until the job's persisted boundary, all removed
    at it. The job row - state, settlement, expiry, idempotency key, debit, digests - is
    exactly what it was (D3): content is scrubbed and marked, never the record."""
    world = make_draft_world()
    job = new_id()
    run(world.port.admit(job, **METADATA))
    rows = content_of(world, job)
    run(world.port.attach(job, rows["source"].content_id))
    rows["upload"] = run(world.write("upload_destination", upload_key(world), b"bytes",
                                     hold_s=GRACE_S * 2))
    run(world.port.finish(job, "succeeded", result_ttl_s=RESULT_TTL_S))
    settled = run(world.port.job(job))
    world.clock.advance(RESULT_TTL_S - 1)
    report = run(collector(world).sweep())
    assert deleted(report) == [upload_key(world)]           # its window closed long ago
    assert all(run(world.read(row)) for name, row in rows.items() if name != "upload")
    world.clock.advance(1)
    report = run(collector(world).sweep())
    assert sorted(deleted(report)) == sorted(row.identity.object_key for name, row
                                             in rows.items() if name != "upload")
    assert world.objects.objects == {}
    assert not any(key.startswith("job") for key in world.objects.deletes), \
        "database content was sent to the object store"
    for name in ("request_text", "result"):
        assert run(world.port.body(rows[name].identity.object_key)) is None, name
    assert run(world.port.job(job)) == settled


def test_the_persisted_result_expiry_is_the_boundary_not_todays_configuration(make_draft_world):
    """RV-11's cleanup half: the result expires at the `result_expires_at` persisted at
    settlement. A redeploy with a longer retention extends nothing, and the result is
    unreadable from that instant - before any collector has run."""
    world = make_draft_world()
    job = new_id()
    run(world.port.admit(job, **METADATA))
    rows = content_of(world, job)
    run(world.port.finish(job, "succeeded", result_ttl_s=RESULT_TTL_S))
    expires = run(world.port.job(job))["result_expires_at"]
    world = world.restart(retention_s=RESULT_TTL_S * 10, grace_s=GRACE_S * 10)
    world.clock.advance(RESULT_TTL_S - 1)
    assert run(world.read(rows["result"])) == "the answer"
    world.clock.advance(1)
    assert world.clock.now() == expires
    assert run(world.read(rows["result"])) is None                 # before any deletion
    assert run(world.port.body(rows["result"].identity.object_key)) == "the answer"
    report = run(collector(world).sweep())
    assert f"job_results/{job}" in deleted(report) and key(1, "prepared") in deleted(report)
    assert run(world.port.body(rows["result"].identity.object_key)) is None


def test_a_failed_scrub_keeps_the_expired_result_unreadable_and_is_retried(make_draft_world):
    """The scrub is the acknowledgement's transaction (F2C.b). A database that fails it
    leaves the body in place, the row tombstoned and the result unreadable; the pass says
    so, and a later one finishes it."""
    world = make_draft_world()
    job = new_id()
    run(world.port.admit(job, **METADATA))
    result = run(world.write("result", f"job_results/{job}", b"the answer", job=job,
                             location="database"))
    run(world.port.finish(job, "succeeded", result_ttl_s=RESULT_TTL_S))
    world.clock.advance(RESULT_TTL_S)

    async def down(tombstone):
        raise errors.DependencyUnavailable("the database did not answer")
    report = run(collector(world, port=Interpose(world.port, acknowledge_delete=down)).sweep())
    assert (report.ack_lost, report.aborted, deleted(report)) == (1, "dependency_unavailable", [])
    assert run(world.port.row(result.content_id)).state == "tombstoned"
    assert run(world.read(result)) is None
    assert run(world.port.body(f"job_results/{job}")) == "the answer"
    world.clock.advance(CLAIM_TTL_S)
    assert deleted(run(collector(world).sweep())) == [f"job_results/{job}"]
    assert run(world.port.body(f"job_results/{job}")) is None
    assert run(world.port.row(result.content_id)).state == "deleted"


def test_late_worker_output_for_an_ended_job_never_becomes_readable(make_draft_world):
    """A worker that lost its lease writes after the job ended and its content was
    collected: the prepared artifact is a new row that nothing live references, collected
    after its own grace; the result, past its persisted expiry, is never readable - the
    late write cannot resurrect it."""
    world = make_draft_world()
    job = new_id()
    run(world.port.admit(job, **METADATA))
    result = run(world.write("result", f"job_results/{job}", b"the answer", job=job,
                             location="database"))
    run(world.port.finish(job, "succeeded", result_ttl_s=RESULT_TTL_S))
    world.clock.advance(RESULT_TTL_S)
    run(collector(world).sweep())
    late_result = run(world.write("result", f"job_results/{job}", b"a late answer", job=job,
                                  location="database"))
    late_frames = run(world.write("prepared", key(1, "prepared"), b"late frames", job=job))
    assert late_result.generation == result.generation + 1
    assert run(world.read(late_result)) is None and run(world.read(result)) is None
    assert run(world.read(late_frames)) == b"late frames"         # unreferenced, not secret
    world.clock.advance(GRACE_S)
    report = run(collector(world).sweep())
    assert sorted(deleted(report)) == sorted([f"job_results/{job}", key(1, "prepared")])
    assert world.objects.objects == {}


def test_an_interrupted_upload_is_kept_for_its_window_then_collected(make_draft_world):
    """A PUT that stopped half way leaves a partial object at the destination; the open
    ticket's persisted window protects it, and it goes at the window's end - no process
    needs to remember it. (There is no multipart path: `S3ObjectStore` sends one PutObject,
    so no invisible parts exist to abort; see the M6 evidence.)"""
    world = make_draft_world()
    partial = run(world.write("upload_destination", upload_key(world), b"half a cl",
                              hold_s=GRACE_S * 2))
    world.clock.advance(GRACE_S)
    world = world.restart()
    assert deleted(run(collector(world).sweep())) == []
    assert run(world.read(partial)) == b"half a cl"
    world.clock.advance(GRACE_S)
    assert deleted(run(collector(world).sweep())) == [upload_key(world)]


# --- point 4: two collectors, a restarted runtime, a real outage, load with cleanup ------
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
    assert run(world.read(live_row)) == b"content"


class LostResponse:
    """The delete reaches the store and the answer does not come back."""

    def __init__(self, inner) -> None:
        self.inner, self.lost = inner, 1

    async def delete(self, object_key: str) -> None:
        await self.inner.delete(object_key)
        if self.lost:
            self.lost -= 1
            raise errors.DependencyUnavailable("the media object store did not answer")


def test_a_delete_whose_answer_was_lost_is_repeated_harmlessly(make_world):
    world = make_world()
    row = run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    report = run(collector(world, objects=LostResponse(world.objects)).sweep())
    assert (report.delete_failed, report.aborted) == (1, "object_store_unavailable")
    assert key(1) not in world.objects.objects
    assert run(world.port.row(row.content_id)).state == "tombstoned"
    world.clock.advance(CLAIM_TTL_S)
    assert deleted(run(collector(world).sweep())) == [key(1)]
    assert run(world.port.row(row.content_id)).state == "deleted"


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
    assert run(world.port.row(doomed.content_id)).state == "tombstoned"
    world = world.restart()
    assert deleted(run(collector(world, holder="replacement").sweep())) == []  # claim held
    world.clock.advance(CLAIM_TTL_S)
    report = run(collector(world, holder="replacement").sweep())
    assert deleted(report) == [key(1)] and report.max_pending_delete_s == CLAIM_TTL_S
    assert run(world.read(live_row)) == b"content" and run(world.read(fresh)) == b"content"
    # F2C.d's reconcile rule: the crash does not leave identical media `content_retiring`
    # for ever - the same bytes staged again are the next generation, readable.
    again = run(world.write("source", key(1), b"content"))
    assert (again.content_id, again.generation) == (doomed.content_id, 2)
    assert run(world.read(again)) == b"content"


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


def test_pg_an_unreachable_database_fails_the_pass_and_deletes_nothing(caplog):
    """A real driver against a port nothing listens on (the lane's own, idle S3 port):
    the pass fails, the schedule logs it and goes on, and nothing is deleted."""
    import logging
    world = pg_world()
    run(world.write("source", key(1)))
    world.clock.advance(GRACE_S)
    down = world.port.reopen()
    down.dsn = re.sub(r":\d+/", ":55471/", world.port.dsn, count=1)
    passes = []

    async def sleep(seconds):
        passes.append(seconds)
        if len(passes) == 2:
            raise asyncio.CancelledError
    with caplog.at_level(logging.INFO, logger="infrx.media.retention"):
        with pytest.raises(asyncio.CancelledError):
            run(collector(world, port=down).run(1.0, sleep=sleep))
    assert passes == [1.0, 1.0] and caplog.text.count("retention sweep failed") == 2
    assert key(1) in world.objects.objects


def test_pg_load_and_cleanup_together_never_delete_live_content():
    """Admissions keep arriving - new sources, and attaches to OLD eligible objects the
    collectors are deleting at that moment - while two collectors sweep with small pages
    and every tenth acknowledgement is lost. Invariant: whatever an admission managed to
    attach is live and readable; whatever it could not was refused, never half-bound.
    Then everything ends and ages out, and the collectors remove all of it. The report's
    bounds (batch <= page, in flight <= concurrency) and the pending-delete age are
    measured and printed for the evidence."""
    world = pg_world()
    orphans = [run(world.write("source", key(n), b"old")) for n in range(60)]
    world.clock.advance(GRACE_S)
    attached, refused, reports, lost = [], [], [], {"n": 0}

    async def flaky_ack(tombstone):
        lost["n"] += 1
        if lost["n"] % 10 == 0:
            raise errors.DependencyUnavailable("the acknowledgement was lost")
    port = Interpose(world.port, acknowledge_delete=flaky_ack)

    async def admissions():
        for i in range(40):
            job = new_id()
            await world.port.admit(job)
            row = await world.write("source", key(1000 + i), b"new")
            await world.port.attach(job, row.content_id)
            attached.append((job, row))
            old = orphans[-1 - i]            # the collectors walk them from the front
            try:
                await world.port.attach(job, old.content_id)
                attached.append((job, old))
            except errors.DependencyUnavailable as retiring:
                refused.append(retiring.refusal)
            await asyncio.sleep(0)

    async def sweeping(holder):
        for _ in range(6):
            reports.append(await collector(world, port=port, holder=holder, page_size=8,
                                           concurrency=4).sweep())
            world.clock.advance(CLAIM_TTL_S / 2)

    async def together():
        await asyncio.gather(admissions(), sweeping("a"), sweeping("b"))
    started = time.monotonic()
    run(asyncio.wait_for(together(), 120))
    loaded_s = time.monotonic() - started
    for job, row in attached:
        assert run(world.read(row)) is not None, f"live job {job} lost {row.identity.object_key}"
    assert set(refused) <= {"content_retiring"}
    for job in {job for job, _ in attached}:
        run(world.port.finish(job, "failed"))
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
    print("M6-LOAD", {"objects": 100, "attached_old": len(attached) - 40,
                      "refused_old": len(refused),
                      "passes": len(reports), "drain_passes": drain,
                      "deleted": sum(len(r.deleted) for r in reports),
                      "lost_acks": sum(r.ack_lost for r in reports),
                      "max_batch": max(r.max_batch for r in reports),
                      "max_in_flight": max(r.max_in_flight for r in reports),
                      "max_pending_delete_s": pending, "load_phase_wall_s": round(loaded_s, 2)})
