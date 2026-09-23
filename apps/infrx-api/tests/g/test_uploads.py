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
