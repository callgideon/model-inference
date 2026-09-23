"""E3B.b: the crash-boundary drill list, as executable cases.

Every drill is written against the PORTS (`JobStore`, `StreamStore`, `Scheduler`) and is
parametrised over the store it runs on:

* `fake`     - the merged contract fakes. Runs today; proves the drill and its assertions
               (a fake-only result is *implemented*, never *integrated*).
* `postgres` - the real PostgreSQL JobStore/StreamStore adapter. It does not exist on this
               base (the D2-D5 RPCs are `infrx.unimplemented` stubs), so each drill is
               PENDING with the D task that delivers the operation it crashes.

Where a real store does exist it is used directly: Q2's `ValkeyScheduler` on E2's Valkey
(queue loss and rebuild, index saturation), migrations 0001-0005 on E2's PostgreSQL (the
schema's settlement and tenant backstops), M2's probe and fetcher (malformed and hostile
media) and the composition root's startup refusal (the legacy shared-key fallback).

Accounting conservation is asserted after every drill that moves money: for each tenant,
ledger = granted - sum(settled debits), reserved = sum(holds still reserved), available
never negative - computed from port reads, not from a fake's attributes.

Drill ids are `E3B-DR-nn`; the evidence maps each to its oracle (04).
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stack                                            # noqa: E402

import harness                                          # noqa: E402

from infrx.contracts import errors                      # noqa: E402
from infrx.contracts.conformance import builders as b   # noqa: E402
from infrx.contracts.fakes.factories import jobstore_factory  # noqa: E402
from infrx.contracts.fakes.support import CrashAfterCommit  # noqa: E402
from infrx.contracts.limits import DEFAULTS             # noqa: E402
from infrx.contracts.records import (ExecutionMode, IndexEvent, JobState,  # noqa: E402
                                     OutboxKind, SettlementState, TerminalCause)

GRANT = "5"
BACKENDS = ("fake", "postgres")


def rig(backend: str, *unblock: str, **limits):
    """The store a drill runs on. `postgres` is pending on the D tasks named - but only
    while the D RPCs are still stubs: the day they are implemented this FAILS, so a drill
    cannot stay silently pending behind a store that already exists."""
    if backend == "postgres":
        stubs = stack.unimplemented_rpcs()
        if stubs == 0:
            pytest.fail("the D RPCs are implemented: wire rig('postgres') to the real "
                        "adapter (E3B phase 2)")
        stack.pending(*unblock, why=f"no PostgreSQL JobStore/StreamStore adapter on this "
                                    f"base: {stubs} infrx RPCs are infrx.unimplemented stubs")
    h = jobstore_factory(limits=DEFAULTS.replace(**limits) if limits else None)
    h.extra["grant"](b.ORG_A, GRANT)
    h.extra["grant"](b.ORG_B, GRANT)
    return h


def run(body):
    return asyncio.run(body())


async def admit(h, *, org=b.ORG_A, key=b.KEY_A, idem_key="k1"):
    request = b.request(h, org_id=org, key_id=key)
    return request, await h.port.admit(request, b.idem(request, idem_key))


async def running(h, admission, worker="w1"):
    lease = await h.port.claim_preparation(admission.request_id, worker)
    await h.port.prepared(lease, ())
    return await h.port.claim(admission.request_id, worker)


async def assert_conserved(h, org, handles):
    """ledger = granted - settled debits; reserved = holds still reserved; available >= 0."""
    debit, reserved = Decimal(0), Decimal(0)
    for handle in handles:
        admission, outcome = await h.port.get_owned(org, handle)
        if outcome is None or outcome.settlement_state is SettlementState.held_unknown:
            reserved += admission.maximum_hold
        if outcome is not None:
            debit += outcome.debit
    balance = h.extra["balance"](org)
    assert balance["ledger"] == Decimal(GRANT) - debit, (balance, debit)
    assert balance["reserved"] == reserved, (balance, reserved)
    assert balance["available"] >= 0, balance


def usage_projections(h, job_id):
    return h.extra["outbox_kinds"](job_id).count(OutboxKind.usage_projection)


# ------------------------------------------------------------------ acceptance

@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr01_acceptance_crash_after_commit_retries_to_one_identity(backend):
    """DUR-ADMIT: the process dies after the admission committed and before it answered.
    The retry with the same idempotency key is the SAME accepted job - one job, one hold
    of exactly its maximum, one prepare dispatch - and nothing was reserved twice."""
    h = rig(backend, "D2")

    async def body():
        h.failures.crash_after_commit("admit")
        request = b.request(h)
        idem = b.idem(request, "k1")
        with pytest.raises(CrashAfterCommit):
            await h.port.admit(request, idem)
        again = await h.port.admit(request, idem)
        assert again.replayed and again.request_id == request.request_id
        assert len(h.extra["active_jobs"](b.ORG_A)) == 1
        assert h.extra["outbox_kinds"](request.request_id) == [OutboxKind.prepare_dispatch]
        assert h.extra["balance"](b.ORG_A)["reserved"] == again.maximum_hold > 0
        await assert_conserved(h, b.ORG_A, [again.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr02_a_refused_admission_leaves_nothing_behind(backend):
    """DUR-ADMIT / DUR-CAP / API-AUTH: a revoked key, an unpriced model, an empty wallet and
    a full key each refuse with their typed error and leave no job, hold, dispatch or
    journal reservation - the invalid call cannot leak capacity or credit."""
    h = rig(backend, "D2", max_active_jobs_per_key=1)

    async def refused(expected, **kw):
        request = b.request(h, **kw)
        with pytest.raises(expected):
            await h.port.admit(request, b.idem(request, str(uuid.uuid4())))
        assert h.extra["outbox"](request.request_id) == []

    async def body():
        journal_before = h.extra["journal_bytes"]()
        h.extra["revoke_key"](b.KEY_B)
        await refused(errors.InvalidApiKey, org_id=b.ORG_B, key_id=b.KEY_B)
        h.extra["unrevoke_key"](b.KEY_B)
        h.extra["set_price"](b.MODEL, None)
        await refused(errors.InvalidRequest)
        h.extra["set_price"](b.MODEL, b.DEFAULT_PRICE)
        _, kept = await admit(h)                          # fills the key's one slot
        await refused(errors.CapacityExhausted)
        assert h.extra["balance"](b.ORG_B)["reserved"] == 0
        assert len(h.extra["active_jobs"]()) == 1
        assert h.extra["journal_bytes"]() - journal_before == \
            DEFAULTS.journal_job_reserve_bytes            # the one kept job's reservation
        await assert_conserved(h, b.ORG_A, [kept.job_handle])
        await assert_conserved(h, b.ORG_B, [])
    run(body)


# ------------------------------------------------------------------ preparation / dispatch

@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr03_a_lost_preparation_worker_is_redispatched_and_fenced(backend):
    """DUR-FENCE (preparation): the preparing worker dies; after its short lease the reaper
    re-dispatches preparation, the dead worker's late `prepared` is refused, and the new
    worker queues the job exactly once."""
    h = rig(backend, "D3")

    async def body():
        _, admission = await admit(h)
        dead = await h.port.claim_preparation(admission.request_id, "w1")
        h.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        await h.port.recover()
        kinds = h.extra["outbox_kinds"](admission.request_id)
        assert kinds == [OutboxKind.prepare_dispatch] * 2, kinds
        live = await h.port.claim_preparation(admission.request_id, "w2")
        with pytest.raises(errors.StaleLease):
            await h.port.prepared(dead, ())
        queued = await h.port.prepared(live, ())
        assert queued.state is JobState.queued
        assert h.extra["outbox_kinds"](admission.request_id).count(
            OutboxKind.inference_dispatch) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr04_a_claim_whose_answer_was_lost_is_requeued_once(backend):
    """DUR-FENCE / DUR-OUTBOX (dispatch): the claim committed but the worker never got the
    lease. After lease expiry the job is requeued as a prepublication retry with ONE new
    inference dispatch, and the next claim is a new generation."""
    h = rig(backend, "D3")

    async def body():
        _, admission = await admit(h)
        lease = await h.port.claim_preparation(admission.request_id, "w1")
        await h.port.prepared(lease, ())
        h.failures.crash_after_commit("claim")
        with pytest.raises(CrashAfterCommit):
            await h.port.claim(admission.request_id, "w1")
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await h.port.recover()
        events = [item for item in produced if isinstance(item, IndexEvent)]
        assert [(e.job_id, e.attempt) for e in events] == [(admission.request_id, 1)]
        second = await h.port.claim(admission.request_id, "w2")
        assert second.generation == 2
        assert h.extra["outbox_kinds"](admission.request_id).count(
            OutboxKind.inference_dispatch) == 2
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


# ------------------------------------------------------------------ output

@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr05_a_stale_generation_cannot_append(backend):
    """DUR-FENCE (append): generation 1 loses its lease and the job is requeued; the SAME
    worker id claims generation 2. Generation 1's append is refused and stores nothing;
    only generation 2's output is readable. (Same worker id on purpose: only the
    generation, not the owner, distinguishes the two.)"""
    h = rig(backend, "D3", "D4")

    async def body():
        _, admission = await admit(h)
        old = await running(h, admission, "w1")
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        await h.port.recover()
        new = await h.port.claim(admission.request_id, "w1")
        assert new.generation == old.generation + 1
        stream = h.extra["stream"]
        with pytest.raises((errors.StaleLease, errors.AlreadyTerminal)):
            await stream.append(old, b.events("stale"))
        await stream.append(new, b.events("fresh"))
        chunks, _cursor = await stream.read_owned(b.ORG_A, admission.job_handle, None)
        assert {c.generation for c in chunks} == {new.generation}, chunks
        assert all("stale" not in str(c.payload) for c in chunks)
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr06_a_worker_lost_after_publication_is_never_regenerated(backend):
    """DUR-OUTPUT: the worker dies after its first committed chunk. The reaper fails the job
    `lost_after_publication` (no second attempt, no early success) and, usage unknown, the
    hold stays reserved for reconciliation rather than becoming a debit."""
    h = rig(backend, "D4", "D5")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("first"))
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        produced = await h.port.recover()
        assert not [item for item in produced if isinstance(item, IndexEvent)]
        _, outcome = await h.port.get_owned(b.ORG_A, admission.job_handle)
        assert (outcome.state, outcome.cause) == (JobState.failed,
                                                  TerminalCause.lost_after_publication)
        assert outcome.settlement_state is SettlementState.held_unknown and outcome.debit == 0
        with pytest.raises((errors.StaleLease, errors.AlreadyTerminal)):
            await h.extra["stream"].append(lease, b.events("late"))
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr07_a_duplicate_settlement_settles_once(backend):
    """DUR-SETTLE / CREDIT-SPEND: the settling commit's answer is lost; the identical retry
    replays the committed outcome, a different proposal is refused, a late cancel returns
    the same outcome - one usage projection, one debit, conservation exact."""
    h = rig(backend, "D5")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("answer"))
        proposal = b.outcome(admission.request_id, h)
        h.failures.crash_after_commit("complete")
        with pytest.raises(CrashAfterCommit):
            await h.port.complete(lease, proposal)
        first = (await h.port.get_owned(b.ORG_A, admission.job_handle))[1]
        assert first.settlement_state is SettlementState.settled and first.debit > 0
        assert await h.port.complete(lease, proposal) == first
        with pytest.raises(errors.AlreadyTerminal):
            await h.port.complete(lease, b.outcome(admission.request_id, h,
                                                   tokens=b.usage(1, 1)))
        assert await h.port.cancel(b.ORG_A, admission.job_handle) == first
        assert (await h.port.get_owned(b.ORG_A, admission.job_handle))[1] == first
        assert usage_projections(h, admission.request_id) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr08_a_foreign_tenant_cannot_read_cancel_or_see_a_result(backend):
    """Tenant isolation of results (BACKEND-JOURNEY "foreign calls leave nothing"): beta
    asking for alpha's handle gets `not_found` from status, replay and cancel alike - the
    same answer as an unknown handle - and alpha's job is untouched."""
    h = rig(backend, "D4", "G3")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        await h.extra["stream"].append(lease, b.events("private"))
        handle = admission.job_handle
        with pytest.raises(errors.NotFound):
            await h.port.get_owned(b.ORG_B, handle)
        with pytest.raises(errors.NotFound):
            await h.extra["stream"].read_owned(b.ORG_B, handle, None)
        with pytest.raises(errors.NotFound):
            await h.port.cancel(b.ORG_B, handle)
        state, outcome = await h.port.get_owned(b.ORG_A, handle)
        assert (state.state, outcome) == (JobState.running, None)
        await assert_conserved(h, b.ORG_A, [handle])
        await assert_conserved(h, b.ORG_B, [])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr09_cancellation_beats_a_late_completion(backend):
    """DUR-SETTLE / API-MODES: cancel while running, then the worker's completion arrives.
    One winner (the cancel), the completion is fenced, nothing is debited for unreported
    work and the hold is released."""
    h = rig(backend, "D3", "D5")

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        cancelled = await h.port.cancel(b.ORG_A, admission.job_handle)
        assert cancelled.state is JobState.cancelled and cancelled.debit == 0
        with pytest.raises((errors.AlreadyTerminal, errors.StaleLease)):
            await h.port.complete(lease, b.outcome(admission.request_id, h))
        assert usage_projections(h, admission.request_id) == 1
        await assert_conserved(h, b.ORG_A, [admission.job_handle])
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_e3b_dr10_journal_backpressure_refuses_an_oversized_event_whole(backend):
    """Backpressure (API-STREAM / DUR-OUTPUT): an event over `JOURNAL_EVENT_MAX_BYTES` is
    `journal_write_failed`, and nothing of it - not a prefix - is stored."""
    h = rig(backend, "D4", journal_event_max_bytes=64)

    async def body():
        _, admission = await admit(h)
        lease = await running(h, admission)
        with pytest.raises(errors.JournalWriteFailed):
            await h.extra["stream"].append(lease, b.events("x" * 200))
        chunks, _cursor = await h.extra["stream"].read_owned(b.ORG_A, admission.job_handle,
                                                             None)
        assert chunks == ()
    run(body)


def test_e3b_dr11_client_disconnect_mid_stream_is_pending():
    """API-MODES / API-STREAM: a sync or SSE client that disconnects mid-generation must
    cancel or detach per mode, with no orphan execution. It is a route behaviour, so it is
    pending exactly as long as no metered route is mounted, and fails once one is."""
    if stack.ingress_is_mounted():
        pytest.fail("the pilot ingress is mounted: write the disconnect drill body now")
    stack.pending("G1R", "G2", why="the sync/SSE relay that sees the disconnect is G2's, "
                                   "and no metered route is mounted")


# ------------------------------------------------------------------ saturation and queue

def test_e3b_dr12_a_full_valkey_index_refuses_without_writing(valkey_index):
    """DUR-CAP / saturation, on the real Valkey (E2's, Q2's adapter): past the item cap an
    enqueue is `capacity_exhausted` with a retry hint, and the index is unchanged."""
    port, _h, _client = valkey_index(max_items=2)

    async def body():
        for _ in range(2):
            assert await port.enqueue(_event(_h, uuid.uuid4()))
        before = await port.snapshot()
        with pytest.raises(errors.CapacityExhausted) as caught:
            await port.enqueue(_event(_h, uuid.uuid4()))
        assert caught.value.retry_after_s == 1
        assert await port.snapshot() == before
    run(body)


def test_e3b_dr13_losing_the_queue_index_loses_no_accepted_job(valkey_index):
    """DUR-OUTBOX / queue rebuild, on the real Valkey: four accepted jobs are queued, one runs
    to completion; Valkey is SIGKILLed (it keeps no data by design) and comes back empty.
    Rebuilt from the durable snapshot, every still-queued job is dispatchable exactly once
    and the finished one is never re-dispatched.

    The snapshot is read from the fake JobStore here; producing it from PostgreSQL is Q3's
    reconciler (pending), so this proves the index half of DUR-OUTBOX only."""
    port, h, client = valkey_index()

    async def body():
        admissions = []
        for index in range(4):
            _, admission = await admit(h, idem_key=f"k{index}")
            lease = await h.port.claim_preparation(admission.request_id, "prep")
            await h.port.prepared(lease, ())
            admissions.append(admission)
            assert await port.enqueue(_event(h, admission.request_id))
        first = await port.claim_candidate("w1")
        lease = await h.port.claim(first.job_id, "w1")
        await port.acknowledge(first)
        await h.extra["stream"].append(lease, b.events("done"))
        await h.port.complete(lease, b.outcome(first.job_id, h))

        with harness.Faults() as faults:
            faults.kill_container("valkey")
        harness.wait_valkey()
        await client.connection_pool.disconnect()
        assert await port.depth() == 0, "the index survived a SIGKILL: not a loss drill"

        snapshot = []
        for admission in admissions:
            state, _outcome = await h.port.get_owned(b.ORG_A, admission.job_handle)
            if state.state is JobState.queued:
                snapshot.append(_event(h, admission.request_id))
        assert await port.rebuild(tuple(snapshot)) == 3
        dispatched = []
        for _ in range(len(admissions) + 1):         # bounded: a duplicate cannot loop
            candidate = await port.claim_candidate("w2")
            if candidate is None:
                break
            dispatched.append(candidate.job_id)
            await port.acknowledge(candidate)
        else:
            raise AssertionError(f"the index kept dispatching: {dispatched}")
        expected = {a.request_id for a in admissions} - {first.job_id}
        assert sorted(dispatched) == sorted(expected), dispatched
        await assert_conserved(h, b.ORG_A, [a.job_handle for a in admissions])
    run(body)


def _event(h, job_id):
    return IndexEvent(event_id=h.ids.event_id(), job_id=str(job_id), org_id=b.ORG_A,
                      key_id=b.KEY_A, kind=OutboxKind.inference_dispatch,
                      execution_mode=ExecutionMode.stream, available_at=h.clock.now(),
                      attempt=0)


@pytest.fixture
def valkey_index():
    """Q2's adapter on E2's Valkey, in a namespace of our own under `harness.VALKEY_PREFIX`, removed
    afterwards (E2's suite asserts the shared prefix is left empty). Returns a factory so a
    drill can set the caps."""
    state = harness.load_state()
    if not state or not harness.owned_containers():
        pytest.skip(f"no {harness.PROJECT} stack: run `tests/integration/run.py --layer 3`")
    from valkey.asyncio import Valkey

    from infrx.scheduling.valkey import ValkeyScheduler
    namespace = f"{harness.VALKEY_PREFIX}{{e3b-{uuid.uuid4().hex}}}"

    def make(**caps):
        h = rig("fake")
        client = Valkey.from_url(harness.valkey_url())
        return ValkeyScheduler(client, h.clock.now, limits=DEFAULTS, namespace=namespace,
                               **caps), h, client
    yield make
    sync = harness.valkey_client()
    leftovers = list(sync.scan_iter(f"{namespace}*"))
    if leftovers:
        sync.delete(*leftovers)


# ------------------------------------------------------------------ media

def test_e3b_dr14_malformed_media_is_a_typed_refusal_not_a_crash():
    """MEDIA-SEC / MEDIA-PARITY (malformed): M2's real probe refuses garbage, a truncated
    ISO box and an EBML header with an oversized integer as `unsupported_media` - never an
    unhandled exception that would surface as a 500."""
    from infrx.media.probe import probe
    ebml_uint = b"\x1a\x45\xdf\xa3" + b"\x42\x86" + b"\x88" + b"\xff" * 200
    for hostile in (b"not a video at all", b"\x00\x00\x00\x18ftypisom",
                    b"\x00\x00\xff\xffmoov" + b"\x00" * 16, ebml_uint):
        with pytest.raises(errors.UnsupportedMedia):
            probe(hostile)


def test_e3b_dr15_internal_media_urls_are_refused_before_any_connection():
    """MEDIA-SEC: loopback (E2's own PostgreSQL port), link-local metadata and an IPv4-mapped
    IPv6 loopback are refused by M2's fetcher, and the transport never sees a request."""
    import httpx

    from infrx.media.fetch import MediaFetcher
    seen = []
    transport = httpx.MockTransport(lambda request: seen.append(request) or
                                    httpx.Response(200, content=b"x"))
    fetcher = MediaFetcher(transport=transport)

    async def body():
        for url in (f"http://127.0.0.1:{harness.PORTS['postgres']}/clip.mp4",
                    "http://169.254.169.254/latest/meta-data/clip.mp4",
                    "http://[::ffff:127.0.0.1]/clip.mp4", "http://localhost/clip.mp4"):
            with pytest.raises(errors.DomainError):
                await fetcher.fetch(url)
    run(body)
    assert seen == []


# ------------------------------------------------------------------ legacy fallback

def _pilot_settings(tmp_path, **kw):
    from infrx.config import Settings
    return Settings(usage_log=str(tmp_path / "usage.jsonl"),
                    supabase_url="https://fake.supabase.invalid", supabase_key="service-role",
                    pilot=DEFAULTS.replace(infrx_mode="pilot",
                                           database_url="postgresql:///infrx_e3b"), **kw)


def test_e3b_dr16_pilot_refuses_the_legacy_shared_key(tmp_path):
    """Forced fallback to legacy unmetered ingress (R51): a pilot configured with the shared
    gateway key (R51's forbidden setting) - the legacy path that bypasses per-tenant metering - refuses to
    start, naming the setting and not its value."""
    from unittest import mock
    from infrx.config import RuntimeMisconfigured, validate_runtime
    from infrx.gateway import app as composition
    from infrx.gateway.routes import health, ingress, models
    # G1R: a complete pilot configuration validates only as the cutover composes it.
    with mock.patch.object(composition, "ROUTERS", (health, models, ingress)):
        assert validate_runtime(_pilot_settings(tmp_path)) == "pilot"
    with pytest.raises(RuntimeMisconfigured) as refused:
        validate_runtime(_pilot_settings(tmp_path, legacy_key="shared-legacy-key"))
    # assembled from parts: tests/integration's production-pointer guard scans this file
    assert "GATEWAY" "_API_KEY" in str(refused.value)
    assert "shared-legacy-key" not in str(refused.value)


def test_e3b_dr17_a_pilot_gateway_does_not_serve_chat_through_the_legacy_route(tmp_path):
    """Forced fallback to legacy unmetered ingress: in `pilot` mode `/v1/chat/completions`
    must be the metered ingress, never the legacy F1 chat route (no durable admission, no
    hold). Today the composition root mounts the legacy route in every mode - the documented
    pre-cutover state - so this is pending on G1R with the observation recorded."""
    from infrx.config import RuntimeMisconfigured
    from infrx.gateway.app import create_app
    try:
        app = create_app(_pilot_settings(tmp_path))
    except RuntimeMisconfigured as refused:
        # G1R: pilot refuses to start while chat would be served by the legacy route.
        assert "legacy route" in str(refused)
        return
    served = {route.path: route.endpoint.__module__ for route in app.routes
              if getattr(route, "path", "") == "/v1/chat/completions"}
    if served and all(module.endswith(".ingress") for module in served.values()):
        return
    stack.pending("G1R", why=f"pilot mode serves /v1/chat/completions from {served}")
