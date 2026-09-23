#!/usr/bin/env python3
"""MPILOT: the two media gaps the pilot's separate gateway and worker processes hit.

    uv run --frozen pytest -q tests/m/test_pilot_media.py

Gap 1 - a chat or job naming a finalized `infrx-upload:upl_…` ref was 400 at admission:
`MediaStaging.materialize` took only http(s)/`data:` sources. Now `MediaUploads.materialize`
resolves the ref by `resolve_owned`, the rule `stage` already applied to an upload ref.
The in-process cases are the mutant killers; `test_mpilot__..._over_the_mounted_gateway`
drives the same path through `create_app`'s route table (uploads + jobs) over HTTP.

No network, no decoder, no wall clock except where the mounted gateway's own clock is the
thing a case moves.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.contracts.fakes.state import FakeStreamStore
from infrx.contracts.records import MediaKind
from infrx.gateway.app import create_app
from infrx.media import fetch, store
from infrx.media.store import InMemoryObjectStore
from infrx.scheduling.memory import MemoryScheduler

from ..g import relay_support as rs, support as gs
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
