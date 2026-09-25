#!/usr/bin/env python3
"""A3 WR-1 (APP-JOURNEY, CATALOG-TRUTH): the App Docs examples against the MOUNTED routes.

`apps/app/tests/a/examples.test.ts` runs every Docs snippet (curl, Python, JavaScript) against a
loopback fake and commits what they send as `apps/app/tests/a/example-calls.json`. This replays
that record, call for call and in order, against one mounted app (G7's `world_for`: jobs, models
and uploads routes over the contract fakes), so a Docs example that the admission path refuses,
or that answers another status than the App expects, fails here too. Handles are threaded from
the answers; `{clip}` and `{upload}` are this world's clip and upload.
"""
from __future__ import annotations

import base64
import json
import pathlib

from infrx.contracts.conformance import builders as b
from infrx.contracts.records import Usage
from infrx.gateway.routes import models, uploads

from . import relay_support as rs, support
from .jobs import world as jw
from .test_alias_pricing import credit_settings
from .test_catalog_truth import priced
from .test_route_conformance import CLIP, discover, send_raw

APP_CALLS = (pathlib.Path(__file__).resolve().parents[3] / "app" / "tests" / "a"
             / "example-calls.json")
CLIP_URL = "data:video/mp4;base64," + base64.b64encode(CLIP).decode()


def credit_world():
    """`test_route_conformance.world_for` in the consumer launch regime (CREDIT, the approved
    card): jobs, models and uploads mounted over one catalog."""
    world = jw.JobsWorld(regime=rs.CREDIT, config=credit_settings())
    priced(world.catalog)
    rt = world.app.state.runtime
    models.register(world.app, rt)
    uploads.register(world.app, rt, store=world.media)
    return world


async def finish_new(world, before: set):
    """Prepare, run and settle the job this request admitted, as a worker would in the CREDIT
    regime (the relay's sync/SSE wait returns once the settling transaction commits)."""
    (job_id,) = set(world.jobs.jobs) - before
    await world.prepare(job_id)
    lease = await world.jobs.claim(job_id, "worker-a")
    await world.commit(lease, "Two people ", "unload boxes.")
    ref = await world.put_result(job_id, "Two people unload boxes.")
    await world.jobs.complete_credit(lease, b.outcome(job_id, world, tokens=Usage.of(1200, 5),
                                                      result_ref=ref))


def substitute(body, upload: str | None):
    text = json.dumps(body).replace("data:video/mp4;base64,{clip}", CLIP_URL)
    if upload is not None:
        text = text.replace("infrx-upload:{upload}", "infrx-upload:" + upload)
    return json.loads(text)


def test_app_journey__every_docs_example_is_served_by_the_mounted_routes():
    record = json.loads(APP_CALLS.read_text())
    world = credit_world()
    entry = discover(world)
    assert record["model"] in entry["aliases"], record["model"]
    job = upload = None
    ran = 0
    for example, calls in record["examples"].items():
        if example == "revoke":
            # The key is revoked (the API Keys page's write) and the identity cache has
            # expired: the next request with it is refused (P-26).
            rt = world.app.state.runtime
            rt.sb = support.supabase(rows=({**world.row, "revoked_at": "2026-09-20T12:00:00Z"},))
            world.clock.advance(rt.settings.key_ttl + 1)
        for call in calls:
            headers = {k: v for k, v in call["headers"].items()
                       if k not in ("authorization", "content-type")}
            route, method = call["route"], call["method"]
            path = route.replace("{handle}", upload if route.startswith("/v1/uploads/") else job or "")
            if method == "PUT":
                reply = rs.run(send_raw(world.app, "PUT", path, CLIP, {
                    **support.AUTH, "content-type": call["headers"]["content-type"]}))
            else:
                body = substitute(call["body"], upload) if call["body"] is not None else None
                if route == "/v1/uploads":
                    body = {**body, "max_bytes": len(CLIP), "bytes": len(CLIP)}
                before = set(world.jobs.jobs)
                if route == "/v1/chat/completions" and call["status"] == 200:
                    world.during.append(lambda: finish_new(world, before))
                key = headers.pop("idempotency-key", None)
                reply = rs.run(jw.send(world.app, method, path, body=body, key=key,
                                       headers=headers))
            assert reply.status == call["status"], (example, method, route, reply.body[:400])
            answer = reply.json() if reply.body[:1] == b"{" else {}
            if route == "/v1/uploads":
                upload = answer["upload_handle"]
            if route == "/v1/jobs":
                if job is None:
                    rs.run(finish_new(world, before))     # the job the example then polls
                job = answer["job_handle"]
                assert answer["idempotency_replayed"] is (example == "resume"), example
            if route == "/v1/jobs/{handle}":
                assert answer["state"] == "succeeded", answer
            if route == "/v1/jobs/{handle}/result":
                assert answer["response"]["choices"][0]["message"]["content"], answer
            if example == "revoke":
                assert answer["error"]["code"] == "invalid_api_key", answer
            ran += 1
    assert ran >= 13, f"only {ran} recorded calls were replayed"
