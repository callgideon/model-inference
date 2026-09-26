#!/usr/bin/env python3
"""W5 wiring 4 on PostgreSQL: a VIDEO admitted through `admit_ready` and prepared behind its
marker (W5 note 6; 0-W5AW-1, 0-W5AW-2, 2-W5W-A1).

    INFRX_D_TASK=w5 INFRX_M_S3_ENDPOINT=http://127.0.0.1:55497 INFRX_M_S3_LOCAL_CREDS=1 \\
        uv run --frozen pytest -q tests/g/test_relay_readiness_pg.py

The pilot box of `tests/w/test_worker_main.py` (a fresh CREDIT database, the lane's Valkey
and MinIO, E2's fake vLLM, the gateway composed by `create_app` in this process); each case
skips visibly without those services. Two shapes of video: an owned upload and a
URL-fetched (`data:`) clip.
"""
from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from infrx.config import from_env
from infrx.media.fetch import digest_of
from infrx.worker import __main__ as worker_main

from ..m.test_uploads import CLIP
from ..w.test_worker_main import answer, box, stop_worker, until  # noqa: F401 (fixture)
from .test_relay_readiness import FETCHED

AUTH = {"authorization": "Bearer sk-i2b"}
MODEL = "nemostation/marlin-2b"
SHAPES = ["upload", FETCHED]


def video(url: str) -> list:
    return [{"role": "user", "content": [{"type": "text", "text": "What happens?"},
                                         {"type": "video_url", "video_url": {"url": url}}]}]


async def source(client, shape: str) -> str:
    """The clip as the request names it: a finalized upload, or inline for M to fetch."""
    if shape != "upload":
        return "data:video/mp4;base64," + base64.b64encode(CLIP).decode()
    made = await client.post("/v1/uploads", headers=AUTH, json={
        "bytes": len(CLIP), "digest": digest_of(CLIP), "accepted_mime": ["video/mp4"]})
    assert made.status_code == 201, made.text
    handle = made.json()["upload_handle"]
    put = await client.put(f"/v1/uploads/{handle}", content=CLIP,
                           headers={**AUTH, "content-type": "video/mp4"})
    assert put.status_code == 204, put.text
    done = await client.post(f"/v1/uploads/{handle}/complete", headers=AUTH)
    assert done.status_code == 200, done.text
    return "infrx-upload:" + handle


@pytest.mark.parametrize("shape", SHAPES)
def test_w5_pg__the_worker_process_runs_a_video_admit_ready_admitted(box, shape):  # noqa: F811 (box: the fixture imported above)
    """The round trip across two processes: `POST /v1/jobs` with a video through the
    composed gateway (`admit_ready`: the marker and a one-source manifest), prepared and run
    by `python -m infrx.worker` behind the marker, settled. Oracle: without the marker the
    worker's claim is `not_ready` and the job never leaves preparation; a fetched source
    with no content row is refused 404 at admission."""
    box.start_worker()

    async def case():
        box.start_engine()
        await answer(box.port, "/readyz", 200, within_s=60)
        async with box.relay(), httpx.AsyncClient(transport=httpx.ASGITransport(
                app=box.gateway()), base_url="http://gw") as client:
            url = await source(client, shape)
            accepted = await client.post("/v1/jobs", headers=AUTH,
                                         json={"model": MODEL, "messages": video(url)})
            assert accepted.status_code == 202, accepted.text
            handle, request_id = (accepted.json()[k] for k in ("job_handle", "request_id"))

            async def terminal():
                status = (await client.get(f"/v1/jobs/{handle}", headers=AUTH)).json()
                return status if status["state"] in ("succeeded", "failed", "cancelled") \
                    else None
            return request_id, await until(terminal, within_s=90)

    request_id, status = asyncio.run(case())
    log = box.log.read_text()
    doc = box.conn.execute("select infrx.readiness_doc(%s)", (request_id,)).fetchone()[0]
    assert doc is not None and len(doc["sources"]) == 1, doc
    assert status and (status["state"], status["cause"]) == ("succeeded", "completed"), \
        (status, log[-4000:])
    assert box.row(request_id) == ("succeeded", "completed", "settled")
    refused = [line for line in log.splitlines() if "refused" in line or "not claimed" in line]
    assert refused == [], refused
    assert stop_worker(box.worker) == 0, log[-2000:]


@pytest.mark.parametrize("shape", SHAPES)
def test_w5_pg__a_claim_the_moment_the_marker_commits_prepares_the_manifest(box, shape):  # noqa: F811 (box: the fixture imported above)
    """0-W5AW-1 / 2-W5W-A1: the marker opens W5's barrier as `admit_ready` commits, before
    the relay's attach. The worker's own preparation (the `PreparationRunner`
    `infrx.worker.__main__.compose` builds: `PgLifecycle`'s marker-gated claim, M's
    `MediaPreparation` over `PgAttachments`), run in exactly that window, prepares the
    manifest: 0019's `admit_ready` writes the job's `job_media` rows with the marker, and the
    relay's later attach is a write-once no-op. Oracle: a store whose manifest is not the
    attach record the worker reads (the `job_media` rows gone in that window) answers
    `not_found` here - recorded in the fix-round evidence."""
    seen: dict = {}

    async def case():
        box.start_engine()
        service, pool = worker_main.compose(from_env(box.settings))
        await pool.open(wait=True, timeout=30)
        runner = service.preparation.runner
        app = box.gateway()
        readiness = app.state.runtime.relay.readiness
        admit_ready = readiness.admit_ready

        async def then_the_worker(request, idem, expectation):
            admission, ready = await admit_ready(request, idem, expectation)
            seen["manifest"] = [s.ref.handle for s in ready.sources]
            attached = await runner.media.attached(admission.request_id)
            seen["attached"] = None if attached is None else [r.handle for r in attached]
            seen["result"] = await runner.run(admission.request_id)
            return admission, ready

        readiness.admit_ready = then_the_worker
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://gw") as client:
                url = await source(client, shape)
                accepted = await client.post("/v1/jobs", headers=AUTH,
                                             json={"model": MODEL, "messages": video(url)})
        finally:
            await pool.close()
            await service.engine.client.aclose()
        return accepted

    accepted = asyncio.run(case())
    assert accepted.status_code == 202, accepted.text
    assert "result" in seen, "the relay did not admit through admit_ready"
    result = seen["result"]
    assert len(seen["manifest"]) == 1 and seen["attached"] == seen["manifest"], seen
    assert (result.cause, result.refusal) == ("prepared", None) and result.prompt_tokens, result
    request_id = accepted.json()["request_id"]
    assert box.row(request_id)[0] == "queued", box.row(request_id)
