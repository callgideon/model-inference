#!/usr/bin/env python3
"""G4U: the owned-upload routes over M3's store - MEDIA-SEC (bounds, projection,
enablement) and DUR-RLS (the route half: identity from the key, tenant scope).

    uv run --frozen pytest -q tests/g/test_uploads.py

What the store guarantees is M3's and proven in `tests/m/test_uploads.py`; these cases
prove the HTTP translation of its answers and what only the route can hold. No network,
no sleeps: identities come from a PostgREST stand-in keyed by token hash, the object
store is in memory, and time moves only when a case moves it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib

import httpx
from fastapi import FastAPI

from infrx.contracts import errors
from infrx.contracts.fakes.support import FakeClock
from infrx.gateway.routes import intake, uploads, validate
from infrx.media import fetch, store as objects
from infrx.media import uploads as media

from ..m import support as clips
from . import support

CLIP = clips.mp4(seconds=10.0)
DIGEST = fetch.digest_of(CLIP)
FIXTURES = pathlib.Path(uploads.__file__).parents[2] / "contracts" / "fixtures" / "v1"
ORG_A, ORG_B = support.ORG, "1a1a1a1a-0000-4000-8000-00000000000b"
TOKEN_A = support.TOKEN
TOKEN_B, TOKEN_DEV, TOKEN_OPERATOR, TOKEN_REVOKED = (
    support.TOKEN + suffix for suffix in ("-org-b", "-provider", "-operator", "-revoked"))
KEYS = {TOKEN_A: support.ROW,
        TOKEN_B: {**support.ROW, "id": "3c3c3c3c-0000-4000-8000-00000000000b", "org_id": ORG_B},
        TOKEN_DEV: support.PROVIDER_ROW, TOKEN_OPERATOR: support.OPERATOR_ROW,
        TOKEN_REVOKED: {**support.ROW, "revoked_at": "2026-09-01T00:00:00Z"}}
UNKNOWN_HANDLE = "upl_" + "Q" * 43
CREATE = uploads.UPLOADS_PATH


def put_path(handle):
    return f"{uploads.UPLOADS_PATH}/{handle}"


def complete_path(handle):
    return f"{uploads.UPLOADS_PATH}/{handle}/complete"


def identities():
    """PostgREST by key hash: each token is its own row, an unknown one is no row."""
    rows = {hashlib.sha256(token.encode()).hexdigest(): row for token, row in KEYS.items()}

    def handler(request):
        key_hash = request.url.params.get("key_hash", "").removeprefix("eq.")
        return httpx.Response(200, json=[rows[key_hash]] if key_hash in rows else [])

    return httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(handler))


class Clock:
    """The app's epoch clock; the slow-body case moves it."""

    def __init__(self) -> None:
        self.now = 1_790_000_000.0

    def __call__(self) -> float:
        return self.now


def media_store(limits):
    """M3's adapter over an in-memory object store and its own `FakeClock`."""
    clock = FakeClock()
    adapter = media.MediaUploads(objects.InMemoryObjectStore(), now=clock.now, limits=limits)
    adapter.clock = clock
    return adapter


def mounted(*, with_store=True, slots=None, on_runtime=False, **pilot):
    """(app, runtime, store, slots) with only the upload router mounted - it stands alone
    until the coordinator composes it."""
    clock = Clock()
    rt = support.runtime(support.settings(**pilot), sb=identities(), clock=clock)
    store = media_store(rt.settings.pilot) if with_store else None
    slots = slots if slots is not None else intake.LargeBodies(limit=1, threshold=64)
    app = FastAPI()
    if on_runtime:
        rt.media_store, rt.large_bodies = store, slots
        uploads.register(app, rt)
    else:
        uploads.register(app, rt, store=store, large_bodies=slots)
    return app, rt, store, slots


def run(app, script):
    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gateway.test") as client:
            return await script(client)

    return asyncio.run(main())


def bearer(token=TOKEN_A, **headers):
    return {"authorization": f"Bearer {token}", **headers} if token else dict(headers)


async def create(client, token=TOKEN_A, **constraints):
    return await client.post(CREATE, json=constraints, headers=bearer(token))


async def put(client, handle, data=CLIP, token=TOKEN_A, mime="video/mp4"):
    return await client.put(put_path(handle), content=data,
                            headers=bearer(token, **{"content-type": mime}))


async def complete(client, handle, token=TOKEN_A, body=None):
    return await client.post(complete_path(handle), headers=bearer(token),
                             **({} if body is None else {"json": body}))


def code_of(response) -> str | None:
    """The envelope's code, or None - assertion-shaped, so a missing envelope is a failed
    assertion rather than a `KeyError`."""
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:
        return None


def refusal(response) -> dict:
    """The envelope minus the request id, which differs per request by design."""
    error = dict(response.json().get("error", {}))
    assert error.pop("request_id", None) == response.headers.get("inference-id")
    return error


# --- item 1: enabled only when the store exists ------------------------------------
def test_media_sec__no_store_mounts_no_upload_route():
    """09: an adapter's routes exist only when its dependency service does. Every upload
    path is the contract's `not_found`, with a request id - including a wrong method."""
    app, _, _, _ = mounted(with_store=False)

    async def script(client):
        return [await create(client), await put(client, UNKNOWN_HANDLE),
                await complete(client, UNKNOWN_HANDLE), await client.get(CREATE)]

    for response in run(app, script):
        assert response.status_code == 404, response.text
        assert code_of(response) == "not_found"
        assert response.headers.get("inference-id")


def test_media_sec__the_routes_mount_over_the_runtime_store_and_its_shared_slots():
    """Integration request (a): `register(app, rt)` finds `rt.media_store` and the one
    `rt.large_bodies` chat uses, so the per-process large-body bound is shared."""
    app, _, store, slots = mounted(on_runtime=True)
    held = slots.slot()
    held.account(slots.threshold + 1)

    async def script(client):
        created = await create(client)
        assert created.status_code == 201, created.text
        return created.json()["upload_handle"], await put(client, created.json()["upload_handle"])

    handle, refused = run(app, script)
    assert handle in store.uploads
    assert refused.status_code == 429 and code_of(refused) == "capacity_exhausted"
    held.release()
    assert slots.in_flight == 0


# --- item 2: POST /v1/uploads ------------------------------------------------------
def test_dur_rls__each_audience_creates_in_its_own_org():
    """R66: the org is the key row's. A consumer key and a provider dev key each create
    in their own organization, and the answer carries the request id."""
    app, _, store, _ = mounted()

    async def script(client):
        return [await create(client, token, max_bytes=1024) for token in (TOKEN_A, TOKEN_DEV)]

    for response, org in zip(run(app, script), (ORG_A, support.IDS.provider_org)):
        assert response.status_code == 201, response.text
        assert response.headers.get("inference-id")
        assert store.uploads[response.json()["upload_handle"]].org_id == org


def test_dur_rls__an_operator_key_owns_no_upload():
    """An operator credential runs no inference (catalog's rule), so it creates nothing
    and cannot write or complete an upload a consumer key made in the same org."""
    app, _, store, _ = mounted()

    async def script(client):
        refused = await create(client, TOKEN_OPERATOR)
        made = await create(client)
        assert made.status_code == 201, made.text
        handle = made.json()["upload_handle"]
        return handle, [refused, await put(client, handle, token=TOKEN_OPERATOR),
                        await complete(client, handle, token=TOKEN_OPERATOR)]

    handle, answers = run(app, script)
    for response in answers:
        assert response.status_code == 403 and code_of(response) == "forbidden", response.text
    assert list(store.uploads) == [handle]
    assert store.uploads[handle].state == "created"
    assert store.upload_key(ORG_A, handle) not in store.objects.objects


def test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer_an_upload():
    """Identity from the headers first, on every route: no key, an unknown key and a
    revoked one are each 401 before one byte of the body is read."""
    app, _, store, _ = mounted()
    for token in (None, support.TOKEN + "-unknown", TOKEN_REVOKED):
        bodies = [clips.Chunks([b"x" * 1024] * 8) for _ in range(3)]

        async def script(client, token=token, bodies=bodies):
            return [
                await client.post(CREATE, content=bodies[0],
                                  headers=bearer(token, **{"content-type": "application/json"})),
                await client.put(put_path(UNKNOWN_HANDLE), content=bodies[1],
                                 headers=bearer(token, **{"content-type": "video/mp4"})),
                await client.post(complete_path(UNKNOWN_HANDLE), content=bodies[2],
                                  headers=bearer(token))]

        for response, body in zip(run(app, script), bodies):
            assert body.read == 0, f"{response.request.method} {response.request.url.path} read a body"
            assert response.status_code == 401 and code_of(response) == "invalid_api_key"
    assert store.uploads == {}


def test_dur_rls__the_body_names_no_org_and_nothing_the_contract_lacks():
    """R17/R66: the body is the constraint object. An org, a purpose, a filename, a
    checksum or a content type is an unknown field - 400, never honoured - and nothing
    is created."""
    app, _, store, _ = mounted()
    fields = {"org_id": ORG_B, "purpose": "video", "filename": "clip.mp4", "sha256": DIGEST,
              "content_type": "video/mp4"}

    async def script(client):
        return [await create(client, **{name: value}) for name, value in fields.items()]

    for response in run(app, script):
        assert response.status_code == 400 and code_of(response) == "invalid_request"
    assert store.uploads == {}


def test_media_sec__the_ticket_carries_exactly_the_frozen_fields():
    """R47/R61(1): the answer's keys are the frozen fixture's, the destination is exactly
    `infrx-upload:` + the handle (no org, no path, no URL), and times are RFC 3339 `Z`."""
    fixture = json.loads((FIXTURES / "upload_created.json").read_text())
    app, _, store, _ = mounted()
    response = run(app, lambda client: create(client, max_bytes=1024, bytes=len(CLIP),
                                              digest=DIGEST, accepted_mime=["video/mp4"]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == set(fixture)
    assert body["destination_ref"] == "infrx-upload:" + body["upload_handle"]
    assert (body["max_bytes"], body["accepted_mime"], body["state"]) == (1024, ["video/mp4"],
                                                                          "created")
    assert body["expires_at"].endswith("Z")
    assert store.uploads[body["upload_handle"]].org_id == ORG_A


def test_media_sec__no_store_field_outside_the_frozen_ticket_leaves():
    """R47 at the port boundary: the ticket is validated against the frozen model before
    it leaves, so a store that answered with more - an org, an object key, as a durable
    row would - is an `internal_error`, and neither value reaches the caller."""
    app, _, store, _ = mounted()
    issued = store.create_upload

    async def leaky(org_id, constraints):
        return {**await issued(org_id, constraints), "org_id": org_id,
                "storage_key": f"uploads/{org_id}/secret"}

    store.create_upload = leaky
    response = run(app, create)
    assert response.status_code == 500 and code_of(response) == "internal_error"
    assert ORG_A not in response.text and "uploads/" not in response.text
