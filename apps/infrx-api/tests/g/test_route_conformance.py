#!/usr/bin/env python3
"""API-MODES / API-STREAM / MEDIA-SEC (G7 item 4): what an external client meets at the
mounted routes, checked against what is documented and discovered.

* Overload and outage in every mode: a full key is a typed 429 with retry guidance, a
  store that cannot admit a typed 503 with retry guidance - in sync, SSE and async alike,
  before any header of a stream. (The bounded body drain is `test_intake_drain.py`'s;
  client disconnect and cancellation per mode are `test_relay_sync.py`, `test_relay_sse.py`
  and `jobs/test_jobs.py`'s - rerun on this code, not restated here.)
* Same-host authenticated uploads: the exact sequence E1C's client sends (bench.upload at
  codex/e1c-client f928103a) is what the mounted routes serve, the finalized reference is
  admitted for its tenant only, and a media URL resolving to a private address is refused
  before any job exists (SSRF).
* The CI check's documentation half: every headless example of the endpoint document
  (`E4B-endpoint.md`) is run against the mounted app and must answer what the document
  says, and names only what `/v1/models` advertises.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

from infrx.gateway.routes import models, uploads

from ..m import support as clips
from . import relay_support as rs, support
from .jobs import world as jw
from .test_catalog_truth import DEPLOYED, priced

# The repository root, through `models/` - which a mutant copy links to the real tree
# (`tests/g/mutants._layout`), so the document is the committed one in either run.
REPO = (pathlib.Path(__file__).resolve().parents[4] / "models").resolve().parent
ENDPOINT_DOC = REPO / "research/plan/evidence/e/E4B-endpoint.md"
PREFER = {"prefer": "respond-async"}
CLIP = clips.mp4(seconds=10.0)


def world_for(**limits):
    config = support.settings(**DEPLOYED, **limits)
    world = jw.JobsWorld(limits=config.pilot, config=config)
    priced(world.catalog)
    rt = world.app.state.runtime
    models.register(world.app, rt)
    uploads.register(world.app, rt, store=world.media)
    return world


def envelope(reply):
    body = reply.json()
    return reply.status, body["error"]["code"], body["error"].get("infrx", {})


# --- overload and outage, per mode ---------------------------------------------------
MODES = (("sync", support.CHAT_PATH, {}, {}), ("sse", support.CHAT_PATH, {"stream": True}, {}),
         ("async", "/v1/jobs", {}, {}), ("prefer", support.CHAT_PATH, {}, PREFER))


@pytest.mark.parametrize("mode,path,extra,headers", MODES, ids=[m[0] for m in MODES])
def test_api_stream__a_full_key_is_a_typed_429_with_retry_guidance_in_every_mode(
        mode, path, extra, headers):
    """One key at its admission limit: the next request, in any mode, is 429
    `capacity_exhausted` with `Retry-After` and `retry_after_s` - a JSON refusal before
    anything is streamed or accepted, the connection closed (the intake's rule)."""
    world = world_for(max_active_jobs_per_key=1)
    assert rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body())).status == 202
    reply = rs.run(jw.send(world.app, "POST", path, body=rs.body(stop="x", **extra),
                           headers=headers))
    status, code, infrx = envelope(reply)
    assert (status, code) == (429, "capacity_exhausted"), reply.body
    assert int(reply.headers["retry-after"]) == infrx["retry_after_s"] > 0
    assert reply.headers["content-type"].startswith("application/json")
    assert reply.headers.get("connection") == "close"
    assert len(world.jobs.jobs) == 1


@pytest.mark.parametrize("mode,path,extra,headers", MODES, ids=[m[0] for m in MODES])
def test_api_stream__a_store_outage_at_admission_is_a_typed_503_in_every_mode(
        mode, path, extra, headers):
    """The store cannot admit: 503 `dependency_unavailable` with retry guidance, never a
    500 and never a stream that started; nothing is admitted."""
    world = world_for()

    async def down(*args, **kw):
        raise ConnectionError("postgresql://infrx:secret@db/infrx is unreachable")

    world.jobs.admit = down
    reply = rs.run(jw.send(world.app, "POST", path, body=rs.body(**extra), headers=headers))
    status, code, infrx = envelope(reply)
    assert (status, code) == (503, "dependency_unavailable"), reply.body
    assert int(reply.headers["retry-after"]) == infrx["retry_after_s"] > 0
    assert b"secret" not in reply.body
    assert world.jobs.jobs == {}


# --- same-host authenticated uploads (E1C's client sequence) ---------------------------
def e1c_upload(world, data=CLIP, mime="video/mp4", headers=None):
    """`bench.upload` (E1C f928103a), request for request, with its own acceptance checks:
    create {max_bytes, bytes, accepted_mime, digest} -> 201 naming `infrx-upload:<handle>`,
    PUT the bytes with their type -> 204, complete with no body -> 200 naming the same
    handle, bytes and digest."""
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    created = rs.run(jw.send(world.app, "POST", "/v1/uploads", headers=headers, body={
        "max_bytes": len(data), "bytes": len(data), "accepted_mime": [mime],
        "digest": digest}))
    assert created.status == 201, created.body
    ticket = created.json()
    handle = ticket["upload_handle"]
    assert ticket["destination_ref"] == "infrx-upload:" + handle and ticket["expires_at"]
    put = rs.run(send_raw(world.app, "PUT", f"/v1/uploads/{handle}", data,
                          {**(headers or support.AUTH), "content-type": mime}))
    assert put.status == 204, put.body
    done = rs.run(jw.send(world.app, "POST", f"/v1/uploads/{handle}/complete",
                          headers=headers))
    assert done.status == 200, done.body
    media = done.json()["media"]
    assert (done.json()["upload_handle"], media["digest"], media["bytes"]) == (
        handle, digest, len(data))
    return handle


async def send_raw(app, method, path, data: bytes, headers: dict):
    """One request with a raw body (`jw.send` sends JSON)."""
    sent = []
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": data, "more_body": False}
        return {"type": "http.disconnect"}

    async def deliver(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "scheme": "http", "client": ("198.51.100.7", 40000),
             "server": ("gw", 8001),
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    await app(scope, receive, deliver)
    return rs.Reply(sent)


def with_upload(handle):
    return [{"role": "user", "content": [
        {"type": "text", "text": "What happens?"},
        {"type": "video_url", "video_url": {"url": "infrx-upload:" + handle}}]}]


def test_media_sec__e1c_s_upload_sequence_is_what_the_mounted_routes_serve():
    """Same host, same bearer: E1C's three calls succeed as its client checks them, and the
    finalized reference is admitted as a job's media for its own tenant."""
    world = world_for()
    handle = e1c_upload(world)
    reply = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(with_upload(handle))))
    assert reply.status == 202, reply.body
    (job,) = world.jobs.jobs.values()
    assert job.id in world.media.by_job                  # the upload's ref, attached


def test_dur_rls__another_tenant_cannot_touch_or_name_an_upload():
    """Org B's key, same process (the upload is live): the upload's PUT, completion and
    use in a chat body are each the unknown handle's 404 - the same code as a handle that
    never existed - and nothing is admitted."""
    world = world_for()
    handle = e1c_upload(world)
    world.app.state.runtime.sb = support.supabase(rows=(jw.OTHER_ROW,))
    theirs = {"authorization": "Bearer sk-infrx-org-b"}          # a key of org B
    unknown = "upl_" + "Q" * 43
    for method, tail, data in (("PUT", "", CLIP), ("POST", "/complete", b"")):
        head = {**theirs, "content-type": "video/mp4"} if data else theirs
        foreign = rs.run(send_raw(world.app, method, f"/v1/uploads/{handle}{tail}", data, head))
        never = rs.run(send_raw(world.app, method, f"/v1/uploads/{unknown}{tail}", data, head))
        assert foreign.status == never.status == 404, (tail, foreign.body)
        assert foreign.json()["error"]["code"] == never.json()["error"]["code"] == "not_found"
    reply = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(with_upload(handle)),
                           headers=theirs))
    assert (reply.status, reply.json()["error"]["code"]) == (404, "not_found"), reply.body
    assert world.jobs.jobs == {}
    # The positive control: the upload is live, and its owner's key is admitted with it.
    mine = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(with_upload(handle))))
    assert mine.status == 202, mine.body


def test_media_sec__a_media_url_resolving_to_a_private_address_is_refused(monkeypatch):
    """SSRF: the fetcher pins and checks the address it will connect to; a name that
    resolves to the metadata service or a private range is refused before any job, hold
    or staged object exists."""
    async def private(host):
        return ["169.254.169.254"]

    monkeypatch.setattr(rs.World, "_resolve", staticmethod(private))
    world = world_for()
    reply = rs.run(jw.send(world.app, "POST", "/v1/jobs", body=rs.body(rs.VIDEO)))
    assert 400 <= reply.status < 500, reply.body
    assert b"169.254" not in reply.body
    assert world.jobs.jobs == {} and not world.objects.objects


# --- the documentation half of the CI check --------------------------------------------
EXAMPLE = re.compile(r"^# (\d+)\. (.+?)\n(.*?)(?=^# \d+\. |^```)", re.M | re.S)
BODY = re.compile(r"-d '(\{.*?\})'")
STATUS = re.compile(r"# (\d{3})(?: (\w+))?")


def documented_examples():
    text = ENDPOINT_DOC.read_text()
    section = text[text.index("## Headless examples"):]
    return [(int(n), title, block) for n, title, block in EXAMPLE.findall(section)]


def test_api_modes__every_documented_example_answers_what_the_document_says():
    """Each headless example of the endpoint document, run in order against one mounted
    app (the dataset's keys included): it names only an advertised alias, parameters and
    mode, and answers the status its comment states (sync/SSE 200 and async 202 when none
    is stated). A documented example that the admission path refuses is a failure."""
    world = world_for()
    entry = discover(world)
    capability = entry["capability"]
    ran = 0
    for number, title, block in documented_examples():
        for line in (b for b in block.split("curl") if BODY.search(b)):
            body = json.loads(BODY.search(line).group(1))
            path = re.search(r'"\$BASE(/v1/[a-z/]+)"', line).group(1)
            headers = dict(re.findall(r"-H '([A-Za-z-]+): ([^']+)'", line))
            headers.pop("Content-Type", None)
            if path == "/v1/uploads":
                reply = rs.run(jw.send(world.app, "POST", path, body=body))
                assert reply.status == 201, (number, reply.body)
                ran += 1
                continue
            async_ = path == "/v1/jobs" or headers.get("Prefer") == "respond-async"
            mode = "async" if async_ else "stream" if body.get("stream") else "sync"
            assert mode in capability["execution_modes"], (number, mode)
            assert body["model"] in entry["aliases"], (number, body["model"])
            assert set(body) <= set(capability["parameters"]), (number, set(body))
            assert body.get("max_tokens", 1) <= capability["max_output_tokens"], number
            stated = STATUS.search(line.split("-d '", 1)[1].split("'", 1)[1])
            expected = int(stated.group(1)) if stated else 202 if async_ else 200
            if not async_ and expected == 200:
                world.during.append(lambda: finish_last(world))
            key = headers.pop("Idempotency-Key", None)
            reply = rs.run(jw.send(world.app, "POST", path, body=body, key=key,
                                   headers={k.lower(): v for k, v in headers.items()}))
            assert reply.status == expected, (number, title, reply.body)
            if expected >= 400 and stated.group(2):
                assert reply.json()["error"]["code"] == stated.group(2), number
            ran += 1
    assert ran >= 5, f"only {ran} documented examples were found and run"


async def finish_last(world):
    """Prepare and run the newest job (a sync or SSE answer waits for its terminal)."""
    job_id = list(world.jobs.jobs)[-1]
    await world.prepare(job_id)
    await world.runner(world.upstream()).run(job_id)


def discover(world) -> dict:
    reply = rs.run(jw.send(world.app, "GET", "/v1/models"))
    assert reply.status == 200, reply.body
    data = reply.json()["data"]
    assert len(data) == 1, data
    return data[0]
