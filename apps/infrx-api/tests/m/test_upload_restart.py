#!/usr/bin/env python3
"""M5 / UPLOAD-RESTART (RV-02): an upload's ticket outlives the process that issued it.

    uv run --frozen pytest -q tests/m/test_upload_restart.py

"A process" is a new `MediaUploads` with nothing in memory over what a deployment shares:
the object store, the attach record and the ticket authority. Here the authority is F2C's
reference adapter - `FakeLifecycle.reopen()` is another process over the same durable state;
`test_upload_restart_stack.py` runs the sequence on D10's PostgreSQL, MinIO and separate OS
processes. The negative control is M3's authority, `ProcessUploads`, one per process: the
same sequence fails at its second step.

A "crash" is the process dying right after one durable step commits (`Died` is raised
after the step returns, and nothing else in that process runs); the next step is taken by
another process. No network, no wall clock: one `FakeClock` for the whole deployment.
"""
from __future__ import annotations

import asyncio
import gc
import os
import sys
import types

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import Harness
from infrx.contracts.conformance import builders as b
from infrx.contracts.conformance import lifecycle as lifecycle_cases
from infrx.contracts.fakes.lifecycle import FakeLifecycle
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.records import MediaKind, UploadState
from infrx.media import fetch, store, uploads

from . import support
from .test_prepare import request_with
from .test_uploads import CLIP, TTL, DeclaredFacts, adapter_for, run, source_key

DIGEST = fetch.digest_of(CLIP)
OTHER = OTHER_CLIP = support.mp4(seconds=20.0)
UPLOAD = "infrx-upload:"


class Died(Exception):
    """The process died: the step before it committed, nothing after it ran."""


class World:
    """One deployment: the object store, the attach record and the ticket authority every
    gateway process shares, and one clock. The authority is F2C's reference adapter
    (`reference`), D10's `PgLifecycle` on the task-local database (`postgres`: its clock is
    the database's, moved by the case), or - `durable=False` - M3's authority, a
    `ProcessUploads` in each process, which is what RV-02 found composed."""

    def __init__(self, *, durable: bool = True, authority: str = "reference") -> None:
        self.objects = store.InMemoryObjectStore()
        self.attachments = support.Durable()
        self.jobs: dict[str, str] = {}              # job id -> org: the relay's record (R55)
        self.conn = None
        if durable and authority == "postgres":
            from infrx.state import migrations, pgtesting

            from ..d import pgharness, pgstore
            harness = pgtesting.make_lifecycle_factory(
                pgstore.fresh_database, pgharness.dsn, migrations.SEED_MARLIN.read_text(),
                upload_window_s=TTL)()
            self.clock, self.conn = harness.clock, harness.extra["conn"]
            self.repository = types.SimpleNamespace(reopen=harness.extra["reopen"])
        else:
            self.clock = FakeClock()
            self.repository = FakeLifecycle(None, self.clock, SequentialIds()) \
                if durable else None

    def process(self, tmp_path=None, *, objects=None, uploads=None, cls=None):
        """A new gateway process: a new adapter over the shared state, nothing in memory."""
        options = {}
        if self.repository is not None:
            options["uploads"] = uploads or self.repository.reopen()
            options["content"] = self.repository.reopen()
        if cls is not None:
            options["cls"] = cls
        adapter = adapter_for(tmp_path, objects=objects or self.objects, clock=self.clock,
                              jobs=self.jobs, **options)
        adapter.attachments = self.attachments
        return adapter

    def ticket(self, handle):
        """The ticket as the authority holds it (for PostgreSQL, its row)."""
        if self.conn is None:
            return self.repository.d.tickets[handle]
        row = self.conn.execute("select to_jsonb(u) from infrx.media_uploads u "
                                "where handle = %s", (handle,)).fetchone()[0]
        return types.SimpleNamespace(
            state=UploadState(row["state"]),
            received=row["received_at"] and types.SimpleNamespace(
                bytes=row["received_bytes"], digest=row["received_digest"]),
            finalized=row["finalized_at"] and types.SimpleNamespace(digest=row["digest"]))

    def content_keys(self) -> list[str]:
        """The object keys the authority's content rows name."""
        if self.conn is None:
            return sorted(key for _, key in self.repository.d.by_key)
        return sorted(row[0] for row in self.conn.execute(
            "select object_key from infrx.content_objects"))

    def keys(self, prefix):
        return sorted(key for key in self.objects.objects if key.startswith(prefix))


class DiesAfter:
    """`inner` (a repository or an object store) whose `step` commits and then the process
    dies. For the object store `prefix` names which write it dies after."""

    def __init__(self, inner, step: str, prefix: str = "") -> None:
        self.inner, self.step, self.prefix = inner, step, prefix

    def __getattr__(self, name):
        attribute = getattr(self.inner, name)
        if name != self.step:
            return attribute

        async def committed_then_died(*args, **kw):
            answer = await attribute(*args, **kw)
            if str(args[0] if args else "").startswith(self.prefix) or name != "put_if_absent":
                raise Died(name)
            return answer

        return committed_then_died


# PostgreSQL only on a task-local harness the run names (`INFRX_D_TASK`, as the MPILOT PG
# runner does): a default run - and a mutant's copy - never contends for another lane's port.
_pg = "INFRX_D_TASK is not set" if not os.environ.get("INFRX_D_TASK") else None
if _pg is None:
    from ..d import pgharness as _pgharness
    _pg = _pgharness.unavailable()
AUTHORITIES = ["reference", pytest.param("postgres", marks=pytest.mark.skipif(
    bool(_pg), reason=f"D10's PgLifecycle needs the task-local PostgreSQL: {_pg}"))]


@pytest.fixture(params=AUTHORITIES)
def world(request):
    """The deployment, over each durable authority: F2C's reference and D10's PostgreSQL."""
    return World(authority=request.param)


def create(process, org_id=b.ORG_A, **constraints):
    return run(process.create_upload(org_id, constraints))["upload_handle"]


def put(process, handle, data=CLIP, org_id=b.ORG_A):
    run(process.put_upload(org_id, handle, data, "video/mp4"))


def complete(process, handle, org_id=b.ORG_A):
    return run(process.finalize_upload(org_id, handle))


def resolve(process, handle, org_id=b.ORG_A):
    return run(process.resolve_owned(org_id, handle))


def destination(handle, org_id=b.ORG_A):
    return f"uploads/{org_id}/{handle}"


def admit(world, process, handle, org_id=b.ORG_A):
    """The gateway's half of a job naming the upload: admission's resolution
    (`prepare_request`), `stage`, the relay's admission record and the attach."""
    prepared = run(process.prepare_request(org_id, request_with(process, UPLOAD + handle,
                                                                org_id=org_id)))
    staged = run(process.stage(org_id, prepared))
    world.jobs[prepared.request_id] = org_id
    run(process.attach(prepared.request_id, staged))
    return prepared.request_id, staged


# --- the failure oracle --------------------------------------------------------------------
def test_upload_restart__the_process_local_authority_fails_the_sequence():
    """Negative control (RV-02). With M3's authority in each process, the handle process A
    issued is unknown to B - the PUT is `not_found` and writes nothing - and an upload
    completed in one process does not resolve in the next while its bytes are still
    stored: the gateway replacement RV-02 reproduced."""
    world = World(durable=False)
    a, b_ = world.process(), world.process()
    handle = create(a)
    with pytest.raises(errors.NotFound):
        put(b_, handle)
    assert world.keys("uploads/") == []
    put(a, handle)
    ref = complete(a, handle)
    with pytest.raises(errors.NotFound):
        resolve(world.process(), handle)
    assert world.objects.objects[ref.storage_ref][1] == CLIP


def process_uploads(*, reopen_is_a_new_process: bool = False):
    """A lifecycle-conformance factory over `ProcessUploads`. `reopen` is the same instance
    (one process's memory standing in for a shared store), or - the negative control - a
    new, empty one: what another gateway process holds. `upload_ttl_s` is the window the
    acceptance transcript assumes (`acceptance.CONFIG`)."""
    def factory(limits=None, *, upload_ttl_s: float = TTL, **_):
        clock, ids = FakeClock(), SequentialIds()

        def fresh():
            return uploads.ProcessUploads(now=clock.now, new_handle=ids.upload_handle,
                                          window_s=upload_ttl_s)

        port = fresh()
        return Harness(port=port, clock=clock, ids=ids, extra={
            "reopen": fresh if reopen_is_a_new_process else (lambda: port)})
    return factory


UPLOAD_CASES = [case for case in lifecycle_cases.cases()
                if case.__name__.startswith("upload_restart__")]


@pytest.mark.parametrize("case", UPLOAD_CASES, ids=[case.__name__ for case in UPLOAD_CASES])
def test_the_f2c_upload_cases_pass_on_the_process_local_repository(case):
    """`ProcessUploads` is the port, refusal for refusal: every F2C UPLOAD-RESTART case
    passes on it within one process (so the unit suites above test the port's semantics)."""
    asyncio.run(case(process_uploads()))


def test_the_f2c_restart_case_fails_on_a_process_local_repository():
    """...and F2C's own restart oracle fails it the moment `reopen` is another process:
    the acknowledgement in the second process is `not_found`."""
    with pytest.raises(errors.NotFound):
        asyncio.run(lifecycle_cases.upload_restart__every_step_survives_a_new_process(
            process_uploads(reopen_is_a_new_process=True)))
    assert lifecycle_cases.upload_restart__every_step_survives_a_new_process in UPLOAD_CASES


def test_the_f2c_acceptance_transcript_replays_on_the_process_local_repository():
    """F2C.d acceptance: every committed UPLOAD-RESTART transcript step - each port call's
    normalized answer or typed refusal, per process, with every clock move - is answered
    identically by `ProcessUploads` (the readiness and retention cases need ports it does
    not implement: D10's `PgLifecycle` replays those)."""
    from infrx.contracts.conformance import acceptance
    uploads_cases = [name for name in acceptance.committed()["cases"]
                     if name.startswith("upload_restart__")]
    assert len(uploads_cases) == len(UPLOAD_CASES)
    problems = [problem for problem in acceptance.replay(process_uploads())
                if problem.startswith("upload_restart__")]
    assert problems == []


# --- the sequence ---------------------------------------------------------------------------
def test_upload_restart__create_put_complete_and_use_each_in_another_process(tmp_path, world):
    """Create in A, PUT in B, complete in C, resolve and admit in D, prepare in E. The ref
    is the one C measured (the verified digest and size, the probed type and duration, the
    content-addressed source key); D's admission and staging name exactly it; E, which
    touched nothing else, prepares the clip. No process keeps anything about the upload."""
    a, b_, c, d, e = (world.process(tmp_path) for _ in range(5))
    handle = create(a, bytes=len(CLIP), digest=DIGEST, accepted_mime=["video/mp4"])
    put(b_, handle)
    ref = complete(c, handle)
    assert (ref.org_id, ref.handle, ref.kind, ref.digest, ref.bytes, ref.mime) == (
        b.ORG_A, handle, MediaKind.upload, DIGEST, len(CLIP), "video/mp4")
    assert ref.duration_s == pytest.approx(10.0) and ref.storage_ref == source_key(ref)
    assert resolve(d, handle) == ref
    job_id, staged = admit(world, d, handle)
    assert staged == (ref,)
    prepared = run(e.prepare(job_id, "v1"))
    assert prepared[0].handle == handle and prepared[0].duration_s == pytest.approx(10.0)
    assert world.ticket(handle).state is UploadState.finalized
    # every object the sequence wrote - destination, source, staged envelope, prepared
    # artifact (M6) - has its content row (F2C, for M6's collector), and nothing else does
    assert world.content_keys() == world.keys("")
    assert {ref.storage_ref, destination(handle), prepared[0].storage_ref} <= \
        set(world.content_keys())
    for process in (a, b_, c):
        assert (process.refs, process._finalizing) == ({}, {})
    assert world.keys("media/") == [prepared[0].storage_ref, ref.storage_ref]


# --- a crash after each durable step ---------------------------------------------------
@pytest.mark.parametrize("crash", ["receipt", "destination", "source", "completion"])
def test_upload_restart__a_crash_after_each_durable_step_is_recovered_elsewhere(crash, world):
    """The process dies right after one durable step; another process takes the next call.

    * receipt - the PUT's receipt committed, the destination never written: completion is
      a 400 that leaves the upload open, and the retried PUT (same bytes) finishes it;
    * destination - the bytes written, the 204 lost: the retried PUT is a no-op;
    * source - the verified copy written, `complete` never ran: the retry completes it over
      the same object (write-once, one source object);
    * completion - committed, the 200 lost: the retry answers the same finalized ticket.
    """
    handle = create(world.process())
    repository = world.repository.reopen()
    dying = {"receipt": lambda: world.process(uploads=DiesAfter(repository, "acknowledge_put")),
             "destination": lambda: world.process(
                 objects=DiesAfter(world.objects, "put_if_absent", "uploads/")),
             "source": lambda: world.process(
                 objects=DiesAfter(world.objects, "put_if_absent", "media/")),
             "completion": lambda: world.process(uploads=DiesAfter(repository, "complete"))
             }[crash]()
    if crash in ("receipt", "destination"):
        with pytest.raises(Died):
            put(dying, handle)
        assert world.ticket(handle).received is not None             # durable before bytes
        assert world.keys("uploads/") == ([] if crash == "receipt" else [destination(handle)])
        if crash == "receipt":
            with pytest.raises(errors.InvalidRequest):
                complete(world.process(), handle)
            assert world.ticket(handle).state is UploadState.created
            with pytest.raises(errors.Conflict):                  # the receipt fixed them
                put(world.process(), handle, OTHER)
            assert world.keys("uploads/") == []
        put(world.process(), handle)                                  # the client's retry
    else:
        put(world.process(), handle)
        with pytest.raises(Died):
            complete(dying, handle)
        assert world.ticket(handle).state is (UploadState.created if crash == "source"
                                              else UploadState.finalized)
    ref = complete(world.process(), handle)
    assert resolve(world.process(), handle) == ref
    assert world.keys("media/") == [ref.storage_ref]
    assert world.objects.objects[ref.storage_ref][1] == CLIP
    assert world.ticket(handle).finalized.digest == DIGEST


# --- duplicate, reordered and concurrent calls -----------------------------------------
def test_upload_restart__duplicate_reordered_and_concurrent_calls(world):
    """Completion before any bytes is a 400 that leaves the upload open; the same bytes
    twice is one receipt and one object; other bytes after the receipt are a conflict that
    writes nothing; two processes completing at once give one ref and one source object; a
    completed upload takes no more bytes and a repeated completion answers the same ref."""
    handle = create(world.process())
    with pytest.raises(errors.InvalidRequest):
        complete(world.process(), handle)
    put(world.process(), handle)
    put(world.process(), handle)
    with pytest.raises(errors.Conflict):
        put(world.process(), handle, OTHER)
    assert world.objects.objects[destination(handle)][1] == CLIP
    one, two = world.process(), world.process()

    async def both():
        return await asyncio.gather(one.finalize_upload(b.ORG_A, handle),
                                    two.finalize_upload(b.ORG_A, handle))

    first, second = run(both())
    assert first == second and world.keys("media/") == [first.storage_ref]
    assert complete(world.process(), handle) == first
    with pytest.raises(errors.Conflict):
        put(world.process(), handle)


# --- the window --------------------------------------------------------------------------
def test_upload_restart__the_window_is_the_tickets_and_an_admitted_job_outlives_it(tmp_path,
                                                                                   world):
    """From `expires_at` on - in any process - a finalized upload is `upload_expired` for
    new use and completion, and an open one takes neither bytes nor completion: a replay
    does not revive it, though the bytes are still stored. A job admitted before the window
    closed still attaches (the relay's replay) and prepares in another process."""
    handle = create(world.process())
    put(world.process(), handle)
    ref = complete(world.process(), handle)
    pending = create(world.process())
    put(world.process(), pending, OTHER)
    job_id, staged = admit(world, world.process(), handle)
    world.clock.advance(TTL - 1)
    assert resolve(world.process(), handle) == ref
    world.clock.advance(1)
    for use in (lambda p: resolve(p, handle), lambda p: complete(p, handle),
                lambda p: complete(p, pending), lambda p: put(p, pending, OTHER),
                lambda p: admit(world, p, handle)):
        with pytest.raises(errors.UploadExpired):
            use(world.process())
    assert world.objects.objects[destination(pending)][1] == OTHER    # stored, not revived
    run(world.process().attach(job_id, staged))                       # the replayed attach
    prepared = run(world.process(tmp_path).prepare(job_id, "v1"))
    assert prepared[0].handle == handle and prepared[0].digest == ref.digest


# --- two tenants and forgeries ----------------------------------------------------------
def test_upload_restart__another_tenant_is_not_found_in_every_process(world):
    """Tenant B holding A's handle gets the unknown handle's `not_found` from every step in
    every process - put (nothing written), complete, resolve, admission, staging A's ref as
    sent or relabelled - and A's upload is untouched and usable."""
    handle = create(world.process())
    for step in (lambda p: put(p, handle, org_id=b.ORG_B),
                 lambda p: complete(p, handle, org_id=b.ORG_B)):
        with pytest.raises(errors.NotFound):
            step(world.process())
    assert world.keys("uploads/") == [] and world.ticket(handle).received is None
    put(world.process(), handle)
    ref = complete(world.process(), handle)
    for claim in (ref, ref.model_copy(update={"org_id": b.ORG_B})):
        other = world.process()
        request = b.request(other.harness, org_id=b.ORG_B, key_id=b.KEY_B, refs=(claim,))
        with pytest.raises(errors.NotFound):
            run(other.stage(b.ORG_B, request))
    for step in (lambda p: resolve(p, handle, org_id=b.ORG_B),
                 lambda p: admit(world, p, handle, org_id=b.ORG_B)):
        with pytest.raises(errors.NotFound):
            step(world.process())
    assert resolve(world.process(), handle) == ref


def test_upload_restart__forged_digest_or_a_second_finalize_never_replaces_accepted_bytes(
        world):
    """Failure oracle: once accepted, a handle names its bytes. A destination rewritten
    behind the store makes a second completion a conflict; a request claiming other content
    under the handle is refused; an attach naming a source key the digest does not build, or
    whose object is gone, binds nothing; nor does one whose other fields are the caller's -
    a duration, size or type the store did not measure, or a handle it never issued (R82:
    the bound ref is the store's record) - and the accepted ticket and object never change."""
    handle = create(world.process())
    put(world.process(), handle)
    ref = complete(world.process(), handle)
    world.objects.seed(destination(handle), OTHER)
    with pytest.raises(errors.Conflict):
        complete(world.process(), handle)
    forged = ref.model_copy(update={"digest": fetch.digest_of(OTHER)})
    process = world.process()
    with pytest.raises(errors.UnsupportedMedia):
        run(process.stage(b.ORG_A, b.request(process.harness, refs=(forged,))))
    job_id = process.harness.ids.uuid()
    world.jobs[job_id] = b.ORG_A
    theirs = create(world.process(), b.ORG_B)                  # the same bytes, tenant B's
    put(world.process(), theirs, org_id=b.ORG_B)
    their_ref = complete(world.process(), theirs, org_id=b.ORG_B)
    for claim in (ref.model_copy(update={"storage_ref": source_key(forged)}),
                  forged.model_copy(update={"storage_ref": source_key(forged)}),
                  ref.model_copy(update={"storage_ref": their_ref.storage_ref}),
                  ref.model_copy(update={"duration_s": 0.5}),
                  ref.model_copy(update={"bytes": 1}),
                  ref.model_copy(update={"mime": "video/webm"}),
                  ref.model_copy(update={"handle": "upl_" + "Z" * 22})):
        with pytest.raises(errors.NotFound):
            run(world.process().attach(job_id, (claim,)))
    assert run(world.attachments.get(job_id)) is None
    assert resolve(world.process(), handle) == ref
    assert world.objects.objects[ref.storage_ref][1] == CLIP
    assert world.ticket(handle).finalized.digest == DIGEST


def test_a_completion_without_a_measured_duration_is_refused():
    """F2C: a finalized upload carries the duration M measured. A source without one is
    `invalid_request` and the ticket stays open for a completion that has it."""
    repository = uploads.ProcessUploads(now=FakeClock().now,
                                        new_handle=SequentialIds().upload_handle, window_s=TTL)
    ticket = run(repository.create(b.ORG_A, uploads.UploadConstraints(
        max_bytes=len(CLIP), accepted_mime=("video/mp4",))))
    handle = ticket.upload_handle
    run(repository.acknowledge_put(b.ORG_A, handle, bytes=len(CLIP), digest=DIGEST))
    ref = b.media(b.ORG_A, handle=handle, kind=MediaKind.upload).model_copy(update={
        "digest": DIGEST, "bytes": len(CLIP), "mime": "video/mp4", "duration_s": None})
    with pytest.raises(errors.InvalidRequest):
        run(repository.complete(b.ORG_A, handle, ref))
    assert repository.records[handle].state is UploadState.created
    done = run(repository.complete(b.ORG_A, handle, ref.model_copy(update={"duration_s": 10.0})))
    assert done.state is UploadState.finalized


# --- memory -----------------------------------------------------------------------------------
TICKETS = 400


def held(process) -> int:
    """Bytes reachable from one process's adapter, the deployment's shared adapters left out
    - the object store, the attach record, the content rows and, when it is durable, the
    ticket authority (the fake stands in for PostgreSQL). Code is not state."""
    shared = {id(process.objects), id(process.attachments), id(process.content)}
    if not isinstance(process.tickets, uploads.ProcessUploads):
        shared.add(id(process.tickets))
    seen, stack, total = shared, [process], 0
    while stack:
        item = stack.pop()
        if id(item) in seen or isinstance(item, (type, types.ModuleType, types.FunctionType,
                                                 types.CodeType)):
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        stack.extend(gc.get_referents(item))
    return total


def growth(world, tickets: int = TICKETS) -> tuple[int, object]:
    """Create, PUT and complete `tickets` uploads in ONE process: how much more that process
    holds afterwards than after its first three."""
    process = world.process(cls=DeclaredFacts)
    for number in range(3):                              # every path warm first
        handle = create(process)
        put(process, handle, b"warm-%d" % number)
        complete(process, handle)
    before = held(process)
    for number in range(tickets):
        handle = create(process)
        put(process, handle, b"clip-%06d" % number)
        complete(process, handle)
    return held(process) - before, process


def test_upload_restart__many_finalized_tickets_cost_the_process_no_memory(world):
    """Acceptance: memory stays bounded under many finalized tickets. With the durable
    authority, 400 completions leave the process holding no more than before (every
    per-upload dictionary empty); the process-local authority - the negative control -
    holds a record per ticket, over 1 KiB each."""
    # 100 on PostgreSQL: the claim is per ticket, and each costs three real transactions
    durable, process = growth(world, TICKETS if world.conn is None else 100)
    assert (process.refs, process._finalizing, process.payloads,
            process.by_job) == ({}, {}, {}, {})
    assert durable <= 0, f"the process holds {durable} more bytes"
    local, process = growth(World(durable=False))
    assert len(process.uploads) == TICKETS + 3
    assert local > TICKETS * 1024, f"the control holds only {local} more bytes"
    print(f"\nheld by one process after more finalized tickets: durable "
          f"({'reference' if world.conn is None else 'postgres'}) {durable:+d} B, "
          f"process-local ({TICKETS}) {local:+d} B")

