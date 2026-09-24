#!/usr/bin/env python3
"""M5 item 3 / UPLOAD-RESTART at the mounted routes: one composed gateway per process.

    uv run --frozen pytest -q tests/m/test_upload_wiring.py

`create_app` as the cutover composes it (every router), the contract fakes for D's stores,
one object store, one clock, and F2C's reference ticket authority (`FakeLifecycle`, the
deployment's database) handed to each process's `MediaUploads` exactly where M5 wiring
request 1 puts it (`pilot.build_ingress_deps`). Until the coordinator applies that patch the
monkeypatched constructor below IS the patch; `test_..._unwired_...` is today's composition
failing the same sequence. The response bodies are checked against the frozen wire models.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect

import httpx
import pytest
from infrx.contracts import wire
from infrx.contracts.fakes.factories import credit_jobstore_factory
from infrx.contracts.fakes.lifecycle import FakeLifecycle
from infrx.contracts.fakes.support import SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import JobState, MediaKind, UploadState
from infrx.gateway import pilot
from infrx.gateway.app import create_app
from infrx.media import fetch, store, uploads
from infrx.scheduling.memory import MemoryScheduler

from ..g import relay_support as rs, support as gs
from .test_pilot_media import OTHER_ROW, OTHER_TOKEN, Journal
from .test_upload_restart import DIGEST, OTHER_CLIP, UPLOAD
from .test_uploads import CLIP, TTL

#: Wiring request 1 applied: `pilot.build_ingress_deps` takes the lifecycle adapter.
WIRED = "lifecycle" in inspect.signature(pilot.build_ingress_deps).parameters
KEYS = {gs.TOKEN: rs.CONSUMER_ROW, OTHER_TOKEN: OTHER_ROW}
CONSUMER = {"authorization": f"Bearer {gs.TOKEN}"}
OTHER_AUTH = {"authorization": f"Bearer {OTHER_TOKEN}"}
VIDEO = {"content-type": "video/mp4"}


def keyed_identities():
    """PostgREST by key hash: each token its own row (two tenants), an unknown one none."""
    rows = {hashlib.sha256(token.encode()).hexdigest(): row for token, row in KEYS.items()}

    def handler(request):
        key_hash = request.url.params.get("key_hash", "").removeprefix("eq.")
        return httpx.Response(200, json=[rows[key_hash]] if key_hash in rows else [])

    return httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(handler))


class Deployment:
    """What every gateway process shares - the job store, the catalog, the object store,
    the ticket authority (the database) and one clock - and `gateway()`, a new process."""

    def __init__(self, monkeypatch, *, wired: bool = True) -> None:
        self.wired = wired
        harness = credit_jobstore_factory()
        self.jobs, self.clock = harness.port, harness.clock
        self.jobs.catalog = self.catalog = gs.catalog()
        self.objects = store.InMemoryObjectStore()
        self.repository = FakeLifecycle(None, self.clock, SequentialIds())
        self.config = gs.settings()
        self.config.deployment = self.config.deployment.replace(accounting_regime="credit")
        self.config.pilot = self.config.pilot.replace(
            active_rate_card_version=self.catalog.rate_cards[
                gs.IDS.prod_deployment].rate_card_version)
        self.monkeypatch = monkeypatch

    def gateway(self):
        """A new gateway process. Wired: once `build_ingress_deps` takes `lifecycle` (wiring
        request 1 applied) through `create_app`'s adapters, as the pilot hands it D10's
        adapter; until then by the patched constructor."""
        injected = {}
        if self.wired and WIRED:
            injected["lifecycle"] = self.repository.reopen()
        elif self.wired:
            self.monkeypatch.setattr(pilot, "MediaUploads", functools.partial(
                uploads.MediaUploads, uploads=self.repository.reopen(),
                content=self.repository.reopen()))
        app = create_app(self.config, client=gs.upstream(), sb=keyed_identities(),
                         clock=lambda: self.clock.now().timestamp(), catalog=self.catalog,
                         stream=Journal(self.jobs), objects=self.objects, jobs=self.jobs,
                         index=MemoryScheduler(self.clock.now), **injected)
        media = app.state.runtime.media_store
        assert isinstance(media.tickets, FakeLifecycle) is self.wired
        return app


def call(app, method, path, **kw):
    async def once():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, **kw)
    return asyncio.run(once())


def code(response):
    return (response.json().get("error") or {}).get("code")


def job_body(handle):
    return {"model": gs.PUBLIC_MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What happens in this clip?"},
        {"type": "video_url", "video_url": {"url": UPLOAD + handle}}]}]}


def mounted_upload(deployment, data=CLIP, headers=CONSUMER, complete_it=True):
    """create, PUT (and complete), each on a new gateway process; the handle."""
    created = call(deployment.gateway(), "POST", "/v1/uploads", headers=headers,
                   json={"bytes": len(data), "digest": fetch.digest_of(data),
                         "accepted_mime": ["video/mp4"]})
    assert created.status_code == 201, created.text
    handle = created.json()["upload_handle"]
    stored = call(deployment.gateway(), "PUT", f"/v1/uploads/{handle}", content=data,
                  headers={**headers, **VIDEO})
    assert stored.status_code == 204, stored.text
    if complete_it:
        done = call(deployment.gateway(), "POST", f"/v1/uploads/{handle}/complete",
                    headers=headers)
        assert done.status_code == 200, done.text
    return handle


def test_upload_restart__the_mounted_sequence_across_composed_gateways(monkeypatch):
    """Item 3/4 at the mounted routes: create in gateway A (201, the frozen `UploadCreated`
    exactly), PUT in B (204, empty), complete in C (200, the frozen `UploadCompleted`: the
    measured facts, never the object key or the org), the retried completion in D (the
    identical body), then `POST /v1/jobs` naming `infrx-upload:<handle>` in E: 202, and
    the admitted job runs on the upload's verified object."""
    deployment = Deployment(monkeypatch)
    a, b_, c, d, e = (deployment.gateway() for _ in range(5))
    created = call(a, "POST", "/v1/uploads", headers=CONSUMER, json={
        "bytes": len(CLIP), "digest": DIGEST, "accepted_mime": ["video/mp4"]})
    assert created.status_code == 201 and created.headers.get("inference-id")
    ticket = wire.UploadCreated.model_validate(created.json())
    body = created.json()
    assert set(body) == {"upload_handle", "destination_ref", "max_bytes", "accepted_mime",
                         "state", "expires_at"}
    handle = ticket.upload_handle
    assert body["destination_ref"] == UPLOAD + handle and body["state"] == "created"
    assert (body["max_bytes"], body["accepted_mime"]) == (DEFAULTS.max_media_bytes,
                                                          ["video/mp4"])
    stored = call(b_, "PUT", f"/v1/uploads/{handle}", content=CLIP,
                  headers={**CONSUMER, **VIDEO})
    assert stored.status_code == 204 and stored.content == b""
    done = call(c, "POST", f"/v1/uploads/{handle}/complete", headers=CONSUMER)
    assert done.status_code == 200, done.text
    completed = wire.UploadCompleted.model_validate(done.json())
    assert (completed.upload_handle, completed.state) == (handle, UploadState.finalized)
    assert (completed.media.handle, completed.media.kind, completed.media.digest,
            completed.media.bytes, completed.media.mime) == (handle, "upload", DIGEST,
                                                             len(CLIP), "video/mp4")
    assert completed.media.duration_s == pytest.approx(10.0)
    assert "media/" not in done.text and rs.CONSUMER_ROW["org_id"] not in done.text
    retry = call(d, "POST", f"/v1/uploads/{handle}/complete", headers=CONSUMER)
    assert retry.status_code == 200 and retry.content == done.content
    accepted = call(e, "POST", "/v1/jobs", headers=CONSUMER, json=job_body(handle))
    assert accepted.status_code == 202, accepted.text
    (job,) = deployment.jobs.jobs.values()
    (ref,) = job.request.media
    assert (ref.handle, ref.kind, ref.digest, ref.bytes) == (handle, MediaKind.upload, DIGEST,
                                                             len(CLIP))
    assert deployment.objects.objects[ref.storage_ref][1] == CLIP
    assert e.state.runtime.media_store.by_job[job.id] == (ref,)
    for app in (a, b_, c, d):                        # nothing kept in the other processes
        media = app.state.runtime.media_store
        assert (media.refs, media._finalizing, media.by_job) == ({}, {}, {})


def test_upload_restart__unwired_the_mounted_sequence_fails(monkeypatch):
    """Negative control for wiring request 1: `create_app` as composed today (no ticket
    authority handed to `MediaUploads`) issues a ticket in gateway A that gateway B does not
    know - the PUT is `404 not_found` and no byte is stored."""
    deployment = Deployment(monkeypatch, wired=False)
    created = call(deployment.gateway(), "POST", "/v1/uploads", headers=CONSUMER, json={})
    assert created.status_code == 201, created.text
    handle = created.json()["upload_handle"]
    stored = call(deployment.gateway(), "PUT", f"/v1/uploads/{handle}", content=CLIP,
                  headers={**CONSUMER, **VIDEO})
    assert (stored.status_code, code(stored)) == (404, "not_found")
    assert not any(key.startswith("uploads/") for key in deployment.objects.objects)


def test_upload_restart__mounted_refusals_across_composed_gateways(monkeypatch):
    """The refusals at the mounted routes, each from a gateway that did not issue the
    handle, none of them admitting a job: another tenant's key 404 on PUT, completion and
    admission (nothing written); an uncompleted upload 404 at admission; a closed window
    410 at admission and at completion - the same answer on replay, never revived - while a
    job admitted inside the window is still attached; a second PUT of other bytes 409."""
    deployment = Deployment(monkeypatch)
    handle = mounted_upload(deployment)
    pending = mounted_upload(deployment, data=OTHER_CLIP, complete_it=False)
    foreign = [call(deployment.gateway(), "PUT", f"/v1/uploads/{handle}", content=CLIP,
                    headers={**OTHER_AUTH, **VIDEO}),
               call(deployment.gateway(), "POST", f"/v1/uploads/{handle}/complete",
                    headers=OTHER_AUTH),
               call(deployment.gateway(), "POST", "/v1/jobs", headers=OTHER_AUTH,
                    json=job_body(handle))]
    assert [(r.status_code, code(r)) for r in foreign] == [(404, "not_found")] * 3
    assert f"uploads/{rs.CONSUMER_ROW['org_id']}/{handle}" in deployment.objects.objects
    assert not any(key.startswith(f"uploads/{OTHER_ROW['org_id']}/")
                   for key in deployment.objects.objects)
    other_bytes = call(deployment.gateway(), "PUT", f"/v1/uploads/{pending}",
                       content=CLIP, headers={**CONSUMER, **VIDEO})
    assert (other_bytes.status_code, code(other_bytes)) == (409, "state_conflict")
    unfinished = call(deployment.gateway(), "POST", "/v1/jobs", headers=CONSUMER,
                      json=job_body(pending))
    assert (unfinished.status_code, code(unfinished)) == (404, "not_found")
    inside = call(deployment.gateway(), "POST", "/v1/jobs", headers=CONSUMER,
                  json=job_body(handle))
    assert inside.status_code == 202, inside.text
    deployment.clock.advance(TTL)
    for _ in range(2):                                  # a replay revives nothing
        late = [call(deployment.gateway(), "POST", "/v1/jobs", headers=CONSUMER,
                     json=job_body(handle)),
                call(deployment.gateway(), "POST", f"/v1/uploads/{pending}/complete",
                     headers=CONSUMER),
                call(deployment.gateway(), "PUT", f"/v1/uploads/{pending}",
                     content=OTHER_CLIP, headers={**CONSUMER, **VIDEO})]
        assert [(r.status_code, code(r)) for r in late] == [(410, "upload_expired")] * 3
    (job,) = deployment.jobs.jobs.values()             # only the job admitted in time,
    assert job.state is not JobState.cancelled         # and its acceptance was not undone


def test_upload_restart__a_refused_upload_reference_does_not_consume_the_idempotency_key(
        monkeypatch):
    """E1C's question, answered by R91: a request refused before admission - its upload
    handle unknown, another tenant's, unfinished or expired - is not a job and holds
    nothing, so its `Idempotency-Key` stays free. The same key with a corrected handle is
    admitted fresh (no `Idempotency-Replayed`), the refused body under that key is simply
    evaluated again (no replayed refusal), and only once a job exists does the key replay
    it - and a changed body under it is then `idempotency_conflict`."""
    deployment = Deployment(monkeypatch)
    good = mounted_upload(deployment)
    unfinished = mounted_upload(deployment, data=OTHER_CLIP, complete_it=False)
    keyed = {**CONSUMER, "idempotency-key": "item-0001"}
    refusals = [call(deployment.gateway(), "POST", "/v1/jobs", headers=keyed,
                     json=job_body(name)) for name in ("upl_" + "u" * 40, unfinished)]
    assert [(r.status_code, code(r)) for r in refusals] == [(404, "not_found")] * 2
    assert deployment.jobs.jobs == {}
    fixed = call(deployment.gateway(), "POST", "/v1/jobs", headers=keyed, json=job_body(good))
    assert fixed.status_code == 202, fixed.text
    assert "idempotency-replayed" not in fixed.headers
    again = call(deployment.gateway(), "POST", "/v1/jobs", headers=keyed, json=job_body(good))
    assert again.status_code == 202 and again.headers.get("idempotency-replayed") == "true"
    assert again.headers["inference-id"] == fixed.headers["inference-id"]
    changed = call(deployment.gateway(), "POST", "/v1/jobs", headers=keyed,
                   json=job_body(unfinished))
    assert (changed.status_code, code(changed)) == (409, "idempotency_conflict")
    assert len(deployment.jobs.jobs) == 1
    # A refusal under a fresh key is re-evaluated each time: once the upload completes, the
    # very same keyed body is admitted (the refusal was never recorded).
    retried = {**CONSUMER, "idempotency-key": "item-0002"}
    first = call(deployment.gateway(), "POST", "/v1/jobs", headers=retried,
                 json=job_body(unfinished))
    assert (first.status_code, code(first)) == (404, "not_found")
    done = call(deployment.gateway(), "POST", f"/v1/uploads/{unfinished}/complete",
                headers=CONSUMER)
    assert done.status_code == 200, done.text
    later = call(deployment.gateway(), "POST", "/v1/jobs", headers=retried,
                 json=job_body(unfinished))
    assert later.status_code == 202 and "idempotency-replayed" not in later.headers
    assert len(deployment.jobs.jobs) == 2


def test_upload_wiring__the_pilot_composition_hands_media_the_lifecycle_on_its_pool(
        monkeypatch):
    """Wiring request 1, composed from settings: `create_app` builds D10's `PgLifecycle`
    on the SAME pool as D's stores (one database for tickets, content rows and jobs) and
    hands it to `MediaUploads` as both the ticket authority and the content lifecycle; with
    the stores injected (every test world) there is none and the ticket stays in process."""
    if not WIRED:
        pytest.skip("M5 wiring request 1 (coordinator): pilot.build_ingress_deps does not "
                    "take `lifecycle` yet")
    import psycopg
    from infrx.state.lifecycle import PgLifecycle          # D10

    async def unreachable(*args, **kw):
        raise psycopg.OperationalError("the database is unreachable")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", unreachable)
    built = create_app(gs.settings("dev"), client=gs.upstream(), sb=gs.supabase(),
                       objects=store.InMemoryObjectStore(),
                       index=MemoryScheduler(lambda: None))
    media = built.state.runtime.media_store
    assert isinstance(media.tickets, PgLifecycle) and media.content is media.tickets
    assert media.tickets._connect is built.state.runtime.relay.jobs._connect
    alone = Deployment(monkeypatch, wired=False).gateway().state.runtime.media_store
    assert isinstance(alone.tickets, uploads.ProcessUploads) and alone.content is None
