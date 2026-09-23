#!/usr/bin/env python3
"""G4U: the owned-upload routes over M3's store - MEDIA-SEC (bounds, projection,
enablement) and DUR-RLS (the route half: identity from the key, tenant scope).

    uv run --frozen pytest -q tests/g/uploads/test_uploads.py

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
import re

import httpx
import pytest
from fastapi import FastAPI

from infrx.contracts import errors
from infrx.contracts.fakes.support import FakeClock
from infrx.gateway.routes import ingress, intake, uploads, validate
from infrx.media import fetch, store as objects
from infrx.media import uploads as media

from ...m import support as clips
from .. import support

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


def mounted(*, with_store=True, slots=None, on_runtime=False, on_ingress=False, **pilot):
    """(app, runtime, store, slots) with only the upload router mounted - it stands alone
    until the coordinator composes it. `on_runtime` puts the store and slots on the
    runtime; `on_ingress` puts the slots only on the ingress's deps, as a composition that
    sets `IngressDeps.large_bodies` alone would."""
    clock = Clock()
    rt = support.runtime(support.settings(**pilot), sb=identities(), clock=clock)
    store = media_store(rt.settings.pilot) if with_store else None
    slots = slots if slots is not None else intake.LargeBodies(limit=1, threshold=64)
    app = FastAPI()
    if on_runtime or on_ingress:
        rt.media_store = store
        if on_ingress:
            rt.ingress = ingress.IngressDeps(large_bodies=slots)
        else:
            rt.large_bodies = slots
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


def test_media_sec__without_a_runtime_pool_uploads_count_against_the_ingress_pool():
    """A composition that sets only `IngressDeps.large_bodies` still has one bound: the
    router falls back to the ingress's pool before it would mint a second one."""
    app, _, _, slots = mounted(on_ingress=True)
    held = slots.slot()
    held.account(slots.threshold + 1)

    async def script(client):
        return await put(client, created_handle(await create(client)))

    refused = run(app, script)
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
    is created. An org named in the query or a header changes nothing either: the upload
    is the key's org's."""
    app, _, store, _ = mounted()
    fields = {"org_id": ORG_B, "purpose": "video", "filename": "clip.mp4", "sha256": DIGEST,
              "content_type": "video/mp4"}

    async def script(client):
        refused = [await create(client, **{name: value}) for name, value in fields.items()]
        before = dict(store.uploads)
        named = await client.post(CREATE, params={"org_id": ORG_B}, json={}, headers=bearer(
            **{"x-org-id": ORG_B, "x-infrx-org": ORG_B}))
        return refused, before, named

    refused, before, named = run(app, script)
    for response in refused:
        assert response.status_code == 400 and code_of(response) == "invalid_request"
    assert before == {}
    assert named.status_code == 201, named.text
    assert store.uploads[named.json()["upload_handle"]].org_id == ORG_A


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



def test_media_sec__a_control_body_is_bounded():
    """Create and complete read at most `MAX_CONTROL_BYTES` (plus one chunk) of an
    authenticated body - a constraint object is four fields - and create nothing."""
    app, _, store, _ = mounted()
    bodies = [clips.Chunks([b" " * 1024] * 16) for _ in range(2)]

    async def script(client):
        return [await client.post(CREATE, content=bodies[0],
                                  headers=bearer(**{"content-type": "application/json"})),
                await client.post(complete_path(UNKNOWN_HANDLE), content=bodies[1],
                                  headers=bearer())]

    for response, body in zip(run(app, script), bodies):
        assert body.read <= uploads.MAX_CONTROL_BYTES + 1024, body.read
        assert response.status_code == 413 and code_of(response) == "request_too_large"
    assert store.uploads == {}


def test_media_sec__a_slow_control_body_is_cut_at_the_deadline():
    """The intake deadline bounds create and complete too: a small body that keeps
    arriving, too slowly, is `deadline_exceeded` on the app's clock (moved, never slept),
    and nothing is created or finalized."""
    app, rt, store, _ = mounted()

    def slow():
        return clips.Chunks([b"{", b" " * 100, b" " * 100, b"}"],
                            on_chunk=lambda: setattr(rt.clock, "now", rt.clock.now + 20))

    async def script(client):
        handle = created_handle(await create(client))
        return handle, [
            await client.post(CREATE, content=slow(),
                              headers=bearer(**{"content-type": "application/json"})),
            await client.post(complete_path(handle), content=slow(), headers=bearer())]

    handle, answers = run(app, script)
    for response in answers:
        assert response.status_code == 504 and code_of(response) == "deadline_exceeded", \
            response.text
    assert list(store.uploads) == [handle] and store.uploads[handle].state == "created"



def test_media_sec__no_store_value_outside_the_frozen_ticket_leaves():
    """R61(1)/R47: the values are checked too, exactly. A destination that is an object key
    carrying the org, or keeps the scheme but not the value, and a handle that is not
    `upl_` + 22..64 in full (short, or a valid handle with a suffix) are each an
    `internal_error` that closes the connection, and neither value is rendered."""
    app, _, store, _ = mounted()
    issued = store.create_upload

    def suffixed(ticket, org):
        handle = f"{ticket['upload_handle']}/{org}"
        return {**ticket, "upload_handle": handle, "destination_ref": "infrx-upload:" + handle}

    rewrites = (
        lambda ticket, org: {**ticket, "destination_ref":
                             f"s3://bucket/uploads/{org}/{ticket['upload_handle']}"},
        lambda ticket, org: {**ticket, "destination_ref":
                             f"infrx-upload:{org}/{ticket['upload_handle']}"},
        lambda ticket, org: {**ticket, "upload_handle": "upl_x",
                             "destination_ref": "infrx-upload:upl_x"},
        suffixed)

    for rewrite in rewrites:
        async def leaky(org_id, constraints, rewrite=rewrite):
            return rewrite(await issued(org_id, constraints), org_id)

        store.create_upload = leaky
        response = run(app, create)
        assert response.status_code == 500 and code_of(response) == "internal_error", \
            response.text
        assert ORG_A not in response.text and "upl_x" not in response.text
        assert response.headers.get("connection") == "close"


def test_media_sec__every_refusal_closes_the_connection():
    """The intake's rule for refusals made while a body may still be arriving: the socket
    closes. An operator key (403), a malformed or unknown handle (404) and a type the
    destination does not store (400) are all refused before the read, on every route. An
    answer that succeeded keeps the connection."""
    app, _, store, _ = mounted()

    async def script(client):
        made = await create(client)
        handle = created_handle(made)
        refused = [await create(client, TOKEN_OPERATOR),
                   await put(client, handle, token=TOKEN_OPERATOR),
                   await complete(client, handle, token=TOKEN_OPERATOR),
                   await put(client, "upl_short"), await complete(client, "upl_short"),
                   await put(client, UNKNOWN_HANDLE, mime="application/json"),
                   await client.put(put_path(UNKNOWN_HANDLE), content=b"x" * 64,
                                    headers=bearer(**{"content-type": "video/mp4"}))]
        return refused, [made, await put(client, handle), await complete(client, handle)]

    refused, succeeded = run(app, script)
    assert [response.status_code for response in refused] == [403, 403, 403, 404, 404, 400,
                                                              404]
    for response in refused:
        assert response.headers.get("connection") == "close", response.request.url
    assert [response.status_code for response in succeeded] == [201, 204, 200]
    for response in succeeded:
        assert response.headers.get("connection") != "close", response.request.url


def test_dur_rls__the_router_reads_no_org_from_the_query_or_headers():
    """R66, structurally: whatever name a caller uses, the router reads nothing from the
    query string or cookies, and from the request only the handle path parameter and the
    content type - so no header or query name can become an org."""
    source = pathlib.Path(uploads.__file__).read_text()
    reads = re.findall(r"request\.(?:query_params|headers|cookies|path_params)\S*", source)
    assert sorted(set(reads)) == ['request.headers.get("content-type")',
                                  'request.path_params["handle"]'], reads

# --- item 3: PUT /v1/uploads/{handle}, the constrained destination -------------------
def created_handle(client_answer) -> str:
    assert client_answer.status_code == 201, client_answer.text
    return client_answer.json()["upload_handle"]


def test_media_sec__a_chunked_upload_over_the_cap_stops_reading():
    """M3 limit 10: the running total stops at MAX_MEDIA_BYTES - at most one chunk past
    it is read, whatever the body declares (a chunked one declares nothing) - and nothing
    is stored."""
    app, _, store, slots = mounted(max_media_bytes=4096)
    body = clips.Chunks([b"x" * 1024] * 16)

    async def script(client):
        handle = created_handle(await create(client))
        return handle, await client.put(put_path(handle), content=body,
                                        headers=bearer(**{"content-type": "video/mp4"}))

    handle, response = run(app, script)
    assert body.read <= 4096 + 1024, f"{body.read} bytes read past a 4096-byte cap"
    assert response.status_code == 413 and code_of(response) == "request_too_large"
    assert store.upload_key(ORG_A, handle) not in store.objects.objects
    assert slots.in_flight == 0


def test_media_sec__a_slow_upload_is_cut_at_the_deadline():
    """The intake deadline bounds the destination too: a body that keeps arriving, too
    slowly, is `deadline_exceeded` on the app's clock (moved, never slept), and nothing is
    stored."""
    app, rt, store, slots = mounted()
    body = clips.Chunks([b"x" * 100] * 4, on_chunk=lambda: setattr(rt.clock, "now",
                                                                  rt.clock.now + 20))

    async def script(client):
        handle = created_handle(await create(client))
        return handle, await client.put(put_path(handle), content=body,
                                        headers=bearer(**{"content-type": "video/mp4"}))

    handle, response = run(app, script)
    assert response.status_code == 504 and code_of(response) == "deadline_exceeded"
    assert store.upload_key(ORG_A, handle) not in store.objects.objects
    assert slots.in_flight == 0


def test_media_sec__a_hung_store_is_cut_at_the_deadline():
    """The store call runs under the intake deadline too, so a store that does not take
    the bytes in time is `deadline_exceeded` and gives the shared slot back, rather than
    pinning it for as long as the store hangs. The fake store is slow in real time (1 s
    against a 0.2 s deadline): `asyncio.wait_for` has no injectable clock."""
    app, _, store, slots = mounted(intake_timeout_s=0.2)
    stored = store.put_upload

    async def slow(*args):
        await asyncio.sleep(1.0)
        return await stored(*args)

    store.put_upload = slow

    async def script(client):
        handle = created_handle(await create(client))
        return handle, await put(client, handle)

    handle, response = run(app, script)
    assert response.status_code == 504 and code_of(response) == "deadline_exceeded", \
        response.text
    assert store.upload_key(ORG_A, handle) not in store.objects.objects
    assert slots.in_flight == 0

def test_media_sec__large_uploads_hold_a_shared_slot_until_stored():
    """The per-process large-body bound (`LargeBodies`): with every slot taken a large PUT
    is 429 with retry guidance - before its body is read when it declares its length, and
    after at most the threshold plus one chunk when it is chunked (a chunked body is
    known to be large only by its running total). Otherwise it holds one slot until the
    store has the bytes, and every exit gives it back."""
    app, _, store, slots = mounted()
    during = []
    stored = store.put_upload

    async def spy(*args):
        during.append(slots.in_flight)
        return await stored(*args)

    store.put_upload = spy
    held = slots.slot()
    held.account(slots.threshold + 1)

    chunked = clips.Chunks([b"x" * 30] * 10)

    async def refused(client):
        handle = created_handle(await create(client))
        return handle, [await put(client, handle), await client.put(
            put_path(handle), content=chunked, headers=bearer(**{"content-type": "video/mp4"}))]

    handle, answers = run(app, refused)
    for response in answers:
        assert response.status_code == 429 and code_of(response) == "capacity_exhausted"
        assert response.headers.get("retry-after")
    assert chunked.read <= slots.threshold + 30, chunked.read
    assert during == []
    held.release()
    response = run(app, lambda client: put(client, handle))
    assert response.status_code == 204, response.text
    assert during == [1], "the slot was not held while the store took the bytes"
    assert slots.in_flight == 0


def test_media_sec__a_malformed_handle_is_not_found_before_any_byte():
    """The handle is `upl_` + 22..64 url-safe characters or it is not one: an org-qualified
    reference, a traversal, an encoded slash or a short handle is the same `not_found` as
    an unknown handle - never echoed - and costs no body read."""
    app, _, store, _ = mounted()
    forms = ("upl_short", "infrx-upload:" + UNKNOWN_HANDLE, f"{ORG_A}:{UNKNOWN_HANDLE}",
             "upl_" + "." * 22, UNKNOWN_HANDLE + "%2F..", "..%2F" + UNKNOWN_HANDLE)

    async def script(client):
        unknown = await put(client, UNKNOWN_HANDLE)
        answers = []
        for form in forms:
            for path in (put_path(form), complete_path(form)):
                body = clips.Chunks([b"x" * 1024] * 2)
                answers.append((path, body, await client.request(
                    "PUT" if path == put_path(form) else "POST", path, content=body,
                    headers=bearer(**{"content-type": "video/mp4"}))))
        return unknown, answers

    unknown, answers = run(app, script)
    assert unknown.status_code == 404
    for path, body, response in answers:
        assert body.read == 0, f"{path} read the body"
        assert response.status_code == 404 and refusal(response) == refusal(unknown), path
        assert "Q" * 22 not in response.text
    assert store.uploads == {}


def test_media_sec__the_destination_takes_only_media_types():
    """The destination stores only an allowed media type (the container probe at
    completion stays authoritative): JSON, text, a lookalike or no type at all is
    `unsupported_media` before a byte is read."""
    app, _, store, _ = mounted()

    async def script(client):
        handle = created_handle(await create(client))
        answers = []
        for mime in ("application/json", "text/plain", "video/mp4x", None):
            body = clips.Chunks([CLIP])
            headers = bearer(**({"content-type": mime} if mime else {}))
            answers.append((body, await client.put(put_path(handle), content=body,
                                                   headers=headers)))
        return handle, answers

    handle, answers = run(app, script)
    for body, response in answers:
        assert body.read == 0
        assert response.status_code == 400 and code_of(response) == "unsupported_media"
    assert store.upload_key(ORG_A, handle) not in store.objects.objects


def test_media_sec__the_destination_is_write_once_over_http():
    """M3's answers in the contract's envelope: the same bytes again is 204, other bytes
    409, a finalized upload takes no bytes (409), and an upload whose window closed is
    410 `upload_expired` (R22)."""
    app, _, store, _ = mounted()

    async def script(client):
        handle = created_handle(await create(client))
        first, again = await put(client, handle), await put(client, handle)
        other = await put(client, handle, data=CLIP + b"\x00")
        done = await complete(client, handle)
        after = await put(client, handle)
        late = created_handle(await create(client))
        store.clock.advance(store.limits.processing_cache_ttl_s + 1)
        return first, again, other, done, after, await put(client, late)

    first, again, other, done, after, expired = run(app, script)
    assert (first.status_code, again.status_code) == (204, 204), (first.text, again.text)
    assert first.headers.get("inference-id") and first.content == b""
    assert other.status_code == 409 and code_of(other) == "state_conflict"
    assert done.status_code == 200, done.text
    assert after.status_code == 409 and code_of(after) == "state_conflict"
    assert expired.status_code == 410 and code_of(expired) == "upload_expired"
    assert expired.json()["error"]["type"] == "gone_error"


def test_dur_rls__another_orgs_upload_is_the_unknown_handles_404():
    """Tenant scope over HTTP: another org's key writing to or completing an upload gets
    the envelope an unknown handle gets (modulo request id) - never `forbidden`, which
    would confirm the handle exists - even naming the owner's org in the query and in
    headers, and the owner's upload is untouched and usable."""
    app, _, store, _ = mounted()
    claims = {"x-org-id": ORG_A, "x-infrx-org": ORG_A}

    async def script(client):
        handle = created_handle(await create(client))
        foreign = [await client.put(put_path(handle), params={"org_id": ORG_A}, content=CLIP,
                                    headers=bearer(TOKEN_B, **{"content-type": "video/mp4"},
                                                   **claims)),
                   await client.post(complete_path(handle), params={"org_id": ORG_A},
                                     headers=bearer(TOKEN_B, **claims))]
        unknown = [await put(client, UNKNOWN_HANDLE, token=TOKEN_B),
                   await complete(client, UNKNOWN_HANDLE, token=TOKEN_B)]
        untouched = (store.uploads[handle].state,
                     store.upload_key(ORG_A, handle) in store.objects.objects)
        return foreign, unknown, untouched, [await put(client, handle),
                                             await complete(client, handle)]

    foreign, unknown, untouched, owner = run(app, script)
    for theirs, nobodys in zip(foreign, unknown):
        assert theirs.status_code == 404 and refusal(theirs) == refusal(nobodys)
    assert untouched == ("created", False)
    assert [response.status_code for response in owner] == [204, 200]


# --- item 4: POST /v1/uploads/{handle}/complete ------------------------------------
def test_media_sec__completion_projects_the_ref():
    """R47: the answer is the frozen projection - the handle, the state and the facts the
    caller can verify - never the ref: no object key, no org. A retry of a completed
    upload answers the identical body."""
    fixture = json.loads((FIXTURES / "upload_completed.json").read_text())
    app, _, store, _ = mounted()

    async def script(client):
        handle = created_handle(await create(client, bytes=len(CLIP), digest=DIGEST,
                                             accepted_mime=["video/mp4"]))
        assert (await put(client, handle)).status_code == 204
        return handle, await complete(client, handle), await complete(client, handle)

    handle, done, retry = run(app, script)
    assert done.status_code == 200, done.text
    assert done.headers.get("inference-id")
    body = done.json()
    assert set(body) == set(fixture) and set(body["media"]) == set(fixture["media"])
    assert (body["upload_handle"], body["state"]) == (handle, "finalized")
    assert (body["media"]["digest"], body["media"]["bytes"]) == (DIGEST, len(CLIP))
    ref = store.refs[(ORG_A, handle)]
    assert ref.storage_ref not in done.text and ORG_A not in done.text
    assert retry.status_code == 200 and retry.content == done.content


def test_media_sec__completion_takes_no_fields():
    """R17: the constraints were fixed at create, so completion takes an empty body or
    `{}` and nothing else - a field is 400 and the upload stays completable."""
    app, _, store, _ = mounted()

    async def script(client):
        handle = created_handle(await create(client))
        assert (await put(client, handle)).status_code == 204
        refused = [await complete(client, handle, body=fields) for fields in
                   ({"max_bytes": 1}, {"digest": DIGEST}, {"accepted_mime": ["video/webm"]})]
        state = store.uploads[handle].state
        return refused, state, await complete(client, handle, body={})

    refused, state, done = run(app, script)
    for response in refused:
        assert response.status_code == 400 and code_of(response) == "invalid_request"
    assert state == "created"
    assert done.status_code == 200, done.text


def test_media_sec__completion_refusals_leave_in_the_envelope():
    """M3's completion answers over HTTP: before any bytes arrive it is 400 and the upload
    stays completable; bytes that do not match the declared digest are
    `unsupported_media` and stay refused (409 on a retry); bytes over the cap that arrived behind the
    destination are 413 without being downloaded."""
    app, _, store, _ = mounted()

    async def script(client):
        early = created_handle(await create(client))
        before = await complete(client, early)
        assert (await put(client, early)).status_code == 204
        later = await complete(client, early)
        mismatch = created_handle(await create(client, digest=fetch.digest_of(b"other")))
        assert (await put(client, mismatch)).status_code == 204
        refused = [await complete(client, mismatch), await complete(client, mismatch)]
        oversize = created_handle(await create(client, max_bytes=len(CLIP)))
        store.objects.seed(store.upload_key(ORG_A, oversize), CLIP + b"\x00" * 64)
        return before, later, refused, await complete(client, oversize)

    before, later, (refused, again), oversize = run(app, script)
    assert before.status_code == 400 and code_of(before) == "invalid_request"
    assert later.status_code == 200, later.text
    assert refused.status_code == 400 and code_of(refused) == "unsupported_media"
    # Refused stays refused: the retry is not a second chance at the digest (M3 aborts).
    assert again.status_code == 409 and code_of(again) == "state_conflict", again.text
    assert oversize.status_code == 413 and code_of(oversize) == "request_too_large"


# --- item 5: the M3/G2 seam ----------------------------------------------------------
def test_dur_rls__a_completed_upload_is_usable_only_by_its_org():
    """R82 / R61(1): an upload completed over HTTP with org A's key is named in the frozen
    form the ingress accepts, resolves for org A to the finalized ref, is `not_found` for
    org B, and an upload not yet completed resolves for nobody."""
    app, _, store, _ = mounted()

    async def script(client):
        ticket = await create(client)
        handle = created_handle(ticket)
        assert (await put(client, handle)).status_code == 204
        assert (await complete(client, handle)).status_code == 200
        pending = created_handle(await create(client))
        assert (await put(client, pending)).status_code == 204
        return ticket.json()["destination_ref"], handle, pending

    ref, handle, pending = run(app, script)
    assert validate.check_video_ref({"url": ref}) == (ref, False)
    owned = asyncio.run(store.resolve_owned(ORG_A, handle))
    assert (owned.org_id, owned.handle, owned.digest, owned.kind) == (ORG_A, handle, DIGEST,
                                                                      "upload")
    for org, unusable in ((ORG_B, handle), (ORG_A, pending)):
        with pytest.raises(errors.NotFound):
            asyncio.run(store.resolve_owned(org, unusable))


# --- item 6: the handshake a client can write from the frozen contract ---------------
def test_media_sec__the_upload_handshake_uses_only_the_frozen_names():
    """E1B limit 5 / G1R limit 6, the server side: a client knowing only the frozen names
    (`upload_handle`, `destination_ref`, `media`) and paths creates, PUTs the clip to
    `/v1/uploads/{upload_handle}` with its bearer and type, completes with no body, and
    names the upload in a chat body the ingress's shape checks accept."""
    app, _, _, _ = mounted()
    video = {"content-type": "video/mp4"}

    async def script(client):
        ticket = await client.post("/v1/uploads", headers=bearer(), json={
            "bytes": len(CLIP), "digest": DIGEST, "accepted_mime": ["video/mp4"]})
        assert ticket.status_code == 201, ticket.text
        handle = ticket.json()["upload_handle"]
        stored = await client.put(f"/v1/uploads/{handle}", content=CLIP,
                                  headers=bearer(**video))
        return ticket.json(), stored, await client.post(f"/v1/uploads/{handle}/complete",
                                                        headers=bearer())

    ticket, stored, done = run(app, script)
    assert stored.status_code == 204 and done.status_code == 200, (stored.text, done.text)
    assert set(done.json()) == {"upload_handle", "state", "media"}
    assert done.json()["media"]["digest"] == DIGEST
    chat = {"model": support.PUBLIC_MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "What happens in this clip?"},
        {"type": "video_url", "video_url": {"url": ticket["destination_ref"]}}]}]}
    messages, inline = validate.check_messages(chat, fetch.ALLOWED_MIME)
    assert inline == {}
    assert messages[0]["content"][1]["video_url"]["url"] == "infrx-upload:" + ticket["upload_handle"]
