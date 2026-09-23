#!/usr/bin/env python3
"""MPILOT: the two media gaps the pilot's separate gateway and worker processes hit.

    uv run --frozen pytest -q tests/m/test_pilot_media.py

Gap 1 - a chat or job naming a finalized `infrx-upload:upl_…` ref was 400 at admission:
`MediaStaging.materialize` took only http(s)/`data:` sources. Now `MediaUploads.materialize`
resolves the ref by `resolve_owned`, the rule `stage` already applied to an upload ref.
The in-process cases are the mutant killers; `test_mpilot__..._over_the_mounted_gateway`
drives the same path through `create_app`'s route table (uploads + jobs) over HTTP.

Gap 2 - the attach (`by_job`) and the processing-cache index were process-local, so the
pilot's worker (another process over the same `PROCESSING_CACHE_DIR`) could not resolve
`local_uri` for media the gateway prepared. Now the attach is durable (`PgAttachments`, D2's
staged tables) and a cache miss that knows the media type is found on disk by content hash.
"A second process" is a second adapter with nothing in memory over the same object store,
cache directory and attach record; the `_pg` cases run the real record on PostgreSQL
(`INFRX_D_TASK=d4`) and skip visibly without Docker.

No network, no decoder, no wall clock except where the mounted gateway's own clock is the
thing a case moves.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.contracts.fakes.state import FakeStreamStore
from infrx.contracts.records import MediaKind
from infrx.gateway.app import create_app
from infrx.media import fetch, prepare
from infrx.media.attachments import PgAttachments
from infrx.media.store import InMemoryObjectStore
from infrx.scheduling.memory import MemoryScheduler
from infrx.state.jobstore import connector
from infrx.worker import AttemptRunner
from infrx.worker.fakes import FakeUpstream

from ..d import pgharness
from ..g import relay_support as rs, support as gs
from . import support
from .test_prepare import data_url, request_with
from .test_uploads import CLIP, TTL, adapter_for, arrive, created, finalized, run

UPLOAD = "infrx-upload:"


def chat_naming(adapter, handle, org_id=b.ORG_A):
    """The validated request G1 hands the relay: the caller's `infrx-upload:` ref still in
    the message part, `media` empty."""
    return request_with(adapter, UPLOAD + handle, org_id=org_id)


def admitted(adapter, request, org_id=b.ORG_A):
    return run(adapter.prepare_request(org_id, request))


# --- gap 1: the ref resolves to the finalized upload ----------------------------------
def test_mpilot__a_chat_naming_a_finalized_upload_is_prepared_from_the_store():
    """The prepared record names the upload's own ref - the verified digest, the size and
    the measured duration finalize recorded - at the same content-addressed `source` key a
    `data:` URL of the same bytes gets; the message names the handle, and nothing was
    fetched. `stage` then accepts exactly that record."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    prepared = admitted(adapter, chat_naming(adapter, handle))
    assert prepared.media == (ref,)
    assert ref.kind is MediaKind.upload and ref.digest == fetch.digest_of(CLIP)
    assert ref.duration_s == pytest.approx(10.0) and ref.bytes == len(CLIP)
    same_bytes = run(adapter.materialize(b.ORG_A, data_url(CLIP)))
    assert ref.storage_ref == same_bytes.storage_ref == \
        f"media/{b.ORG_A}/v1/{ref.digest.split(':')[1][:16]}/source"
    assert prepared.messages[0]["content"][1] == {"type": "video_url",
                                                  "video_url": {"ref": handle}}
    assert UPLOAD not in str(prepared.messages)
    assert adapter.fetcher.resolve.calls == []                     # resolved, never fetched
    assert run(adapter.stage(b.ORG_A, prepared)) == (ref,)


def test_mpilot__another_orgs_upload_is_not_found_at_admission():
    """MEDIA-SEC / R61(1): the org is the key's; another org's handle is `not_found`,
    exactly like an unknown one, and nothing is prepared."""
    adapter = adapter_for()
    handle, _ = finalized(adapter)
    with pytest.raises(errors.NotFound):
        admitted(adapter, chat_naming(adapter, handle, org_id=b.ORG_B), org_id=b.ORG_B)


def test_mpilot__an_unfinalized_upload_is_refused_at_admission():
    """An upload whose bytes arrived but which was never completed names nothing: there is
    no verified object (`not_found`). A handle indexed while its upload is still open (a
    squat, seeded as M3's R82 case does) is `invalid_request` - never the squatter's ref."""
    adapter = adapter_for()
    handle = created(adapter)
    arrive(adapter, handle, CLIP)
    with pytest.raises(errors.NotFound):
        admitted(adapter, chat_naming(adapter, handle))
    adapter.refs[(b.ORG_A, handle)] = b.media(b.ORG_A, handle=handle, kind=MediaKind.upload)
    with pytest.raises(errors.InvalidRequest):
        admitted(adapter, chat_naming(adapter, handle))


def test_mpilot__an_upload_past_its_window_is_upload_expired_at_admission():
    """R22 (proposed ruling in the MPILOT evidence): the window bounds use as well as
    completion, so past `expires_at` the ref is `410 upload_expired` - at admission and at
    staging alike - and a second before it the upload is still usable."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    adapter.clock.advance(TTL - 1)
    assert admitted(adapter, chat_naming(adapter, handle)).media == (ref,)
    adapter.clock.advance(1)
    with pytest.raises(errors.UploadExpired) as expired:
        admitted(adapter, chat_naming(adapter, handle))
    assert errors.http_status(expired.value.code) == 410
    with pytest.raises(errors.UploadExpired):
        run(adapter.stage(b.ORG_A, b.request(adapter.harness, refs=(ref,))))


def test_mpilot__an_upload_over_the_media_bound_is_refused_at_admission():
    """An upload finalized under one bound and used under a tighter one (a restart with a
    smaller `MAX_MEDIA_BYTES`) is `413`: the request's byte budget spends an upload's size
    like any source's."""
    adapter = adapter_for()
    handle, _ = finalized(adapter)
    adapter.profile = dataclasses.replace(adapter.profile, max_bytes=len(CLIP) - 1)
    with pytest.raises(errors.RequestTooLarge):
        admitted(adapter, chat_naming(adapter, handle))


@pytest.mark.parametrize("change", ["replaced", "deleted"])
def test_mpilot__an_upload_whose_object_changed_is_refused_at_admission(change):
    """The ref must name the object finalize verified: bytes replaced behind the store, or
    collected, are `not_found` - never a job over other content."""
    adapter = adapter_for()
    handle, ref = finalized(adapter)
    if change == "replaced":
        adapter.objects.seed(ref.storage_ref, b"other bytes")
    else:
        run(adapter.objects.delete(ref.storage_ref))
    with pytest.raises(errors.NotFound):
        admitted(adapter, chat_naming(adapter, handle))


# --- gap 1 over the mounted gateway --------------------------------------------------
class Journal(FakeStreamStore):
    """The contract journal plus D4's `usage()` (the pilot's journal readiness probe)."""

    async def usage(self):
        return {"reserved_bytes": 0, "stored_bytes": 0, "charged_bytes": 0, "chunks": 0}


OTHER_TOKEN = "sk-infrx-mpilot-other"
OTHER_ROW = {**rs.CONSUMER_ROW, "id": "6f6f6f6f-0000-4000-8000-000000000006",
             "org_id": "5e5e5e5e-0000-4000-8000-000000000005",
             "user_id": "7a7a7a7a-0000-4000-8000-000000000007"}


def mounted():
    """`create_app` as the cutover composes it (every router in `ROUTERS`), the contract
    fakes standing in for D's stores, M's real `MediaUploads` over an in-memory object store,
    the CREDIT regime at the fixture's approved card with its seeded consumer wallet."""
    harness = credit_jobstore_factory()
    jobs, clock = harness.port, harness.clock
    jobs.catalog = catalog = gs.catalog()
    config = gs.settings()
    config.deployment = config.deployment.replace(accounting_regime="credit")
    config.pilot = config.pilot.replace(active_rate_card_version=catalog.rate_cards[
        gs.IDS.prod_deployment].rate_card_version)
    app = create_app(config, client=gs.upstream(), sb=gs.supabase(rows=(rs.CONSUMER_ROW,)),
                     clock=lambda: clock.now().timestamp(), catalog=catalog,
                     stream=Journal(jobs), objects=InMemoryObjectStore(), jobs=jobs,
                     index=MemoryScheduler(clock.now))
    return app, jobs


def job_body(handle):
    return {"model": gs.PUBLIC_MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What happens in this clip?"},
        {"type": "video_url", "video_url": {"url": UPLOAD + handle}}]}]}


def test_mpilot__an_upload_named_in_a_job_over_the_mounted_gateway():
    """create -> PUT -> complete -> `POST /v1/jobs` naming `infrx-upload:<handle>`: 202, and
    the admitted job runs on the upload's verified object (its content hash, its
    content-addressed key), staged and attached. Then the four refusals, each before
    anything is admitted: another org's handle 404, an uncompleted upload 404, a window
    that closed 410, bytes over a tightened bound 413."""
    app, jobs = mounted()
    rt = app.state.runtime
    media = rt.media_store
    auth = gs.AUTH

    async def upload(client, *, complete=True, headers=auth):
        ticket = await client.post("/v1/uploads", headers=headers,
                                   json={"accepted_mime": ["video/mp4"]})
        assert ticket.status_code == 201, ticket.text
        handle = ticket.json()["upload_handle"]
        put = await client.put(f"/v1/uploads/{handle}", content=CLIP,
                               headers={**headers, "content-type": "video/mp4"})
        assert put.status_code == 204, put.text
        if complete:
            done = await client.post(f"/v1/uploads/{handle}/complete", headers=headers)
            assert done.status_code == 200, done.text
        return handle

    async def script():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            handle = await upload(client)
            accepted = await client.post("/v1/jobs", headers=auth, json=job_body(handle))
            open_handle = await upload(client, complete=False)
            unfinalized = await client.post("/v1/jobs", headers=auth,
                                            json=job_body(open_handle))
            media.profile, pinned = dataclasses.replace(media.profile,
                                                        max_bytes=len(CLIP) - 1), media.profile
            oversize = await client.post("/v1/jobs", headers=auth, json=job_body(handle))
            media.profile = pinned
            media.now, wall = (lambda: datetime.now(UTC) + timedelta(seconds=TTL + 60)), \
                media.now
            expired = await client.post("/v1/jobs", headers=auth, json=job_body(handle))
            media.now = wall
            rt.sb = gs.supabase(rows=(OTHER_ROW,))          # a key of another org
            foreign = await client.post(
                "/v1/jobs", headers={"authorization": f"Bearer {OTHER_TOKEN}"},
                json=job_body(handle))
            return handle, accepted, unfinalized, oversize, expired, foreign

    handle, accepted, unfinalized, oversize, expired, foreign = asyncio.run(script())
    assert accepted.status_code == 202, accepted.text
    for reply, status, code in ((unfinalized, 404, "not_found"),
                                (oversize, 413, "request_too_large"),
                                (expired, 410, "upload_expired"),
                                (foreign, 404, "not_found")):
        error = reply.json().get("error") or {}
        assert (reply.status_code, error.get("code")) == (status, code), reply.text
    assert len(jobs.jobs) == 1                                 # no refusal admitted a job
    (job,) = jobs.jobs.values()
    (ref,) = job.request.media
    assert (ref.handle, ref.kind, ref.org_id) == (handle, MediaKind.upload, rs.CONSUMER_ROW["org_id"])
    assert ref.digest == fetch.digest_of(CLIP) and ref.bytes == len(CLIP)
    assert ref.storage_ref == f"media/{ref.org_id}/v1/{ref.digest.split(':')[1][:16]}/source"
    assert media.objects.objects[ref.storage_ref][1] == CLIP
    assert media.by_job[job.id] == (ref,)                      # staged and attached
    assert job.request.messages[0]["content"][1]["video_url"] == {"ref": handle}
    assert [key for key in media.objects.objects if key.startswith("payloads/")] == \
        [f"payloads/{ref.org_id}/{job.id}.json"]               # nor staged a payload


# --- gap 2: a second process resolves the attach and the local file -------------------
def two_processes(tmp_path, durable):
    """The pilot box: the gateway (prepares, stages, attaches) and a second process with
    nothing in memory over the same object store, cache directory and attach record."""
    gateway = adapter_for(tmp_path)
    worker = adapter_for(tmp_path, objects=gateway.objects)
    gateway.attachments = worker.attachments = durable
    return gateway, worker


def attach(gateway, body=CLIP, org_id=b.ORG_A, admit=None):
    """Materialize, stage, admit and attach - the gateway's half of a job. `admit(prepared)`
    commits the job row on PostgreSQL; the job table `job_org` reads is the relay's (R55)."""
    prepared = run(gateway.prepare_request(org_id, request_with(gateway, data_url(body),
                                                                org_id=org_id)))
    refs = run(gateway.stage(org_id, prepared))
    if admit is not None:
        admit(prepared)
    gateway.jobs[prepared.request_id] = org_id
    run(gateway.attach(prepared.request_id, refs))
    return prepared.request_id, refs


def second_process_resolves(tmp_path, durable, pg=None):
    gateway, worker = two_processes(tmp_path, durable)
    if pg is not None:
        gateway.harness = pg                  # requests on the database's clock and ids
    job_id, refs = attach(gateway, admit=pg and admitted_on(pg))
    prepared = run(gateway.prepare(job_id, "v1"))
    assert worker.by_job == {} and worker.cache.entries == {}          # nothing in memory
    assert run(worker.attached(job_id)) == refs
    uri = worker.local_uri(prepared[0])
    assert uri == gateway.local_uri(prepared[0])
    with open(uri.removeprefix("file://"), "rb") as handle:
        assert handle.read() == CLIP
    assert run(worker.prepare(job_id, "v1")) == prepared               # re-derived there
    return gateway, worker, job_id


def test_mpilot__a_second_process_resolves_the_attach_and_the_local_file(tmp_path):
    """Gap 2: the job's staged refs and the prepared clip's `file://` path resolve in a
    process that attached and prepared nothing - the worker's position on the pilot box."""
    second_process_resolves(tmp_path, support.Durable())


def test_mpilot__a_cache_file_that_is_not_the_hash_is_not_served(tmp_path):
    """The disk is found by the path the key builds and trusted only for the key's content
    hash: other bytes at that path (a 64-bit prefix collision, a botched copy) are a miss,
    never another tenant's or another clip's frames."""
    gateway, worker = two_processes(tmp_path, support.Durable())
    job_id, _ = attach(gateway)
    ref = run(gateway.prepare(job_id, "v1"))[0]
    path = gateway.local_uri(ref).removeprefix("file://")
    with open(path, "wb") as handle:
        handle.write(support.mp4(seconds=11.0))
    with pytest.raises(errors.NotFound):
        worker.local_uri(ref)


def test_mpilot__a_cache_file_past_its_life_is_not_served_by_another_process(tmp_path):
    """7 days is a retention obligation, and it holds across processes: the life of a file
    another process wrote is counted from when it was written (its mtime, which `put` sets
    to the entry's time), not from when this process first looked."""
    gateway, worker = two_processes(tmp_path, support.Durable())
    job_id, _ = attach(gateway)
    ref = run(gateway.prepare(job_id, "v1"))[0]
    path = gateway.local_uri(ref).removeprefix("file://")
    worker.cache.clock.now = gateway.cache.clock.now + TTL - 1
    assert worker.local_uri(ref) == "file://" + path
    fresh = adapter_for(tmp_path, objects=gateway.objects)                # a third process
    fresh.cache.clock.now = gateway.cache.clock.now + TTL
    with pytest.raises(errors.NotFound):
        fresh.local_uri(ref)
    assert not os.path.exists(path)                                       # and it is gone


def test_mpilot__a_worker_runs_a_video_job_prepared_in_another_process(tmp_path):
    """The pilot's worker, end to end: G2's world admits a `video_url` chat and the gateway's
    preparation writes the shared cache; W's real attempt runner and engine adapter run the
    job with `local_uri` from a process that prepared nothing. The engine is handed the
    `file://` path under the tenant's root and the chat is answered."""
    world = rs.World()
    world.media.cache = prepare.ProcessingCache(str(tmp_path))
    worker = prepare.MediaPreparation(InMemoryObjectStore(),
                                      cache=prepare.ProcessingCache(str(tmp_path)))
    upstream = FakeUpstream(clock=world.clock,
                            limits=world.limits.replace(processing_cache_dir=str(tmp_path)))

    async def work():
        job_id = await world.prepare()
        runner = AttemptRunner(jobs=world.jobs, stream=world.stream,
                               engine=upstream.engine(local_uri=worker.local_uri),
                               clock=world.clock, worker_id="worker-b",
                               count_prompt_tokens=lambda work: upstream.prompt_tokens,
                               put_result=world.put_result, limits=world.limits)
        return await runner.run(job_id)

    world.during.append(work)
    reply = rs.run(rs.call(world.app, rs.body(rs.VIDEO)))
    assert reply.status == 200, reply.body
    (ref,) = world.media.prepared_by_job[world.only_job().id]
    (sent,) = upstream.requests
    part = sent["messages"][0]["content"][1]
    assert part == {"type": "video_url", "video_url": {"url": world.media.local_uri(ref)}}
    assert part["video_url"]["url"].startswith(f"file://{tmp_path}/{ref.org_id}/v1/")
    assert list(worker.cache.entries) == [(ref.org_id, ref.digest, "v1")]   # found on disk


def test_mpilot__the_pilot_composition_records_the_attach_on_its_pool(monkeypatch):
    """`create_app` from settings hands M's store the durable attach record on the SAME
    pool as D's stores (so the worker's `load_work` and M's attach read one database); with
    the stores injected (every test world) there is no record and the attach stays in
    process, as before."""
    import psycopg

    async def unreachable(*args, **kw):
        raise psycopg.OperationalError("postgresql://infrx:secret@db/infrx is unreachable")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", unreachable)
    built = create_app(gs.settings("dev"), client=gs.upstream(), sb=gs.supabase(),
                       objects=InMemoryObjectStore(), index=MemoryScheduler(lambda: None))
    rt = built.state.runtime
    assert isinstance(rt.media_store.attachments, PgAttachments)
    assert rt.media_store.attachments._connect is rt.relay.jobs._connect
    given, _ = mounted()
    assert given.state.runtime.media_store.attachments is None


# --- gap 2 on PostgreSQL: the real attach record ------------------------------------------
def postgres():
    """A fresh migrated, seeded database (tests/d's rig, `INFRX_D_TASK`), its JobStore
    harness and `PgAttachments` over it - or a visible skip."""
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    from ..d import pgstore
    harness = pgstore.factory()
    harness.extra["grant"](b.ORG_A, "100")
    harness.extra["grant"](b.ORG_B, "100")
    return harness, PgAttachments(connector(pgharness.dsn(harness.extra["database"])))


def admitted_on(harness):
    """The admission D2 commits: the prepared request becomes the job row the attach's
    foreign key names."""
    def admit(prepared):
        run(harness.port.admit(prepared, b.idem(prepared, key=prepared.request_id)))
    return admit


def test_mpilot_pg__a_second_process_resolves_the_attach_and_the_local_file(tmp_path):
    """Gap 2 on the real record: attached in one process, read in another from D2's
    `staged_media`/`job_media` rows."""
    harness, durable = postgres()
    second_process_resolves(tmp_path, durable, pg=harness)


def test_mpilot_pg__each_job_reads_back_its_own_refs_in_order(tmp_path):
    """r1 R46/q23 across processes: a job resolves its own refs or nothing - never another
    job's, never an unknown job's - and in the order they were attached."""
    harness, durable = postgres()
    gateway, worker = two_processes(tmp_path, durable)
    gateway.harness = harness
    mine, my_refs = attach(gateway, admit=admitted_on(harness))
    theirs, their_refs = attach(gateway, body=support.mp4(seconds=7.0),
                                admit=admitted_on(harness))
    assert run(worker.attached(mine)) == my_refs
    assert run(worker.attached(theirs)) == their_refs
    assert run(worker.attached(harness.ids.uuid())) is None
    assert run(durable.get("not-a-job-id")) is None
    two = (my_refs[0], their_refs[0])
    job = b.request(harness)
    admitted_on(harness)(job)
    run(durable.put(job.request_id, two))
    assert run(durable.get(job.request_id)) == two


def test_mpilot_pg__an_attach_is_write_once_and_tenant_bound(tmp_path):
    """R55 in the database and immutability: a ref of another organization cannot be bound
    to the job (the composite foreign key), and a job bound to its refs cannot be re-bound
    to others - both refused with nothing written."""
    harness, durable = postgres()
    gateway, _ = two_processes(tmp_path, durable)
    gateway.harness = harness
    job_id, refs = attach(gateway, admit=admitted_on(harness))
    foreign = run(gateway.materialize(b.ORG_B, data_url(support.mp4(seconds=5.0))))
    with pytest.raises(errors.NotFound):
        run(durable.put(job_id, (foreign,)))
    other = run(gateway.materialize(b.ORG_A, data_url(support.mp4(seconds=6.0))))
    with pytest.raises(errors.Conflict):
        run(durable.put(job_id, (other,)))
    assert run(durable.get(job_id)) == refs
    run(durable.put(job_id, refs))                                   # the same is a no-op
    assert run(durable.get(job_id)) == refs
    # a handle already recorded is bound only for the content it was recorded with
    forged = refs[0].model_copy(update={"digest": fetch.digest_of(b"other content")})
    job = b.request(harness)
    admitted_on(harness)(job)
    with pytest.raises(errors.Conflict):
        run(durable.put(job.request_id, (forged,)))
    assert run(durable.get(job.request_id)) is None


def test_mpilot_pg__the_exported_mpilot_cases_run_on_postgresql(tmp_path):
    """Item 3 on PostgreSQL: the two exported cases MPILOT added, against `MediaUploads`
    whose attach record is `PgAttachments` on the D harness's database. `admitted` commits
    the staged request as the job row the attach's foreign key names (D2's `infrx.admit`);
    the store's clock is the database's, so the window moves with it."""
    from infrx.contracts.conformance import SUITES, Harness
    from infrx.contracts.conformance import services
    from infrx.contracts.records import NormalizedRequest
    from psycopg.types.json import Jsonb

    from .test_uploads import conformance_factory

    def factory(limits=None, **_kw):
        pg, durable = postgres()
        base = conformance_factory(limits)
        adapter, conn, store = base.port, pg.extra["conn"], pg.extra["store"]
        adapter.attachments, adapter.now = durable, pg.clock.now

        def admitted(job_id, org_id):
            adapter.jobs[job_id] = org_id
            stored = adapter.objects.objects[adapter.staged_payload(job_id).ref][1]
            request = NormalizedRequest.model_validate_json(stored)
            conn.execute("select infrx.admit(%s)", (Jsonb(store._admit_args(
                "legacy_usd", request, b.idem(request, key=job_id))),))

        return Harness(port=adapter, clock=pg.clock, ids=base.ids,
                       extra={**base.extra, "admitted": admitted})

    ours = (services.media_sec__an_upload_is_usable_only_within_its_window,
            services.media_parity__an_attach_outlives_the_process_that_made_it)
    assert set(ours) <= set(SUITES["mediastore"][0]())
    for case in ours:
        asyncio.run(case(factory))
