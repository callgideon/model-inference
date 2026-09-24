#!/usr/bin/env python3
"""E1C / RV-07: bench.upload against the REAL mounted upload router, and the fake held to it.

    apps/infrx-api/.venv/bin/python -m pytest -q models/marlin2b/tests/test_upload_conformance.py

The review reproduced that the fake-gateway suite passed while the client got a 400 from
the actual route: the fake tested the client's own protocol. Here the client runs against
G4U's router over M3's store, mounted in-process exactly as `tests/g/uploads` mounts it
(httpx.ASGITransport, no network, no server), and the fake answers the same probe
sequence with the same statuses and key sets, so the two cannot drift apart silently.
"""
import asyncio, os, pathlib, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
# apps/infrx-api from any checkout depth; the mutant runner links `apps` beside its copy.
API = next(str(p / "apps" / "infrx-api") for p in pathlib.Path(HERE).parents
           if (p / "apps" / "infrx-api" / "infrx").is_dir())
sys.path.insert(0, API)

import httpx

import bench
from fake_gateway import FakeGateway
from tests.g.uploads.test_uploads import CLIP, ORG_A, TOKEN_A, bearer, mounted

BASE = "http://gateway.test/v1"


def clip_file(tmp, data=CLIP):
    path = os.path.join(tmp, "clip.mp4")
    with open(path, "wb") as f:
        f.write(data)
    return path


def cfg_for(token=TOKEN_A):
    return {"base": BASE, "headers_for": lambda tenant: {"content-type": "application/json",
                                                          "accept": "text/event-stream",
                                                          **bearer(token)},
            "key": (token,)}


def real_router():
    """(app, close) - G4U over M3's in-memory store, as its own suite mounts it."""
    app, rt, store, _ = mounted()

    async def close():
        await rt.client.aclose()
        await rt.sb.aclose()
    return app, store, close


def run_client(transport, script):
    """(script(client), seen): every request's (method, host, path, bearer?) seen."""
    seen = []

    async def record(request):
        seen.append((request.method, request.url.host, request.url.path,
                     bool(request.headers.get("authorization"))))

    async def main():
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test",
                                     event_hooks={"request": [record]}) as client:
            return await script(client)
    return asyncio.run(main()), seen


def test_upload_speaks_the_mounted_contract_through_the_real_router():
    """Oracle: the pre-E1C client sent purpose/filename/sha256/content_type and got a 400
    at create (RV-07); this fails on it. Passing means create -> authenticated PUT on the
    gateway's own origin -> empty completion, and the store holds the finalized bytes."""
    app, store, close = real_router()
    with tempfile.TemporaryDirectory() as tmp:
        path, row = clip_file(tmp), {}

        async def script(client):
            try:
                return await bench.upload(client, cfg_for(), path, row)
            finally:
                await close()

        handle, seen = run_client(httpx.ASGITransport(app=app), script)
    assert bench.HANDLE_OK.fullmatch(handle), handle
    assert row["upload_status"] == 200 and row["upload_stage"] == "finalized", row
    assert [(m, p) for m, _, p, _ in seen] == [
        ("POST", "/v1/uploads"), ("PUT", f"/v1/uploads/{handle}"),
        ("POST", f"/v1/uploads/{handle}/complete")]
    assert all(host == "gateway.test" and auth for _, host, _, auth in seen), seen
    ref = asyncio.run(store.resolve_owned(ORG_A, handle))
    assert ref.digest == "sha256:" + bench.sha256(CLIP).hexdigest() and ref.bytes == len(CLIP)


# The probe sequence both servers answer. Each step is (label, method, path, json body or
# None, raw content or None, extra headers); `{h}` is the handle the first create issued.
def probes(digest, size):
    create = {"max_bytes": size, "bytes": size, "accepted_mime": ["video/mp4"], "digest": digest}
    return (
        ("legacy create fields", "POST", "/v1/uploads",
         {"purpose": "video", "filename": "c.mp4", "bytes": size, "sha256": digest[7:],
          "content_type": "video/mp4"}, None, {}),
        ("create", "POST", "/v1/uploads", create, None, {}),
        ("complete before any byte", "POST", "/v1/uploads/{h}/complete", None, None, {}),
        ("put wrong media type", "PUT", "/v1/uploads/{h}", None, CLIP,
         {"content-type": "text/plain"}),
        ("put", "PUT", "/v1/uploads/{h}", None, CLIP, {"content-type": "video/mp4"}),
        ("complete with fields", "POST", "/v1/uploads/{h}/complete",
         {"sha256": digest[7:], "bytes": size}, None, {}),
        ("complete", "POST", "/v1/uploads/{h}/complete", None, None, {}),
        ("unknown handle", "POST", "/v1/uploads/upl_" + "Q" * 43 + "/complete", None, None, {}),
        ("anonymous create", "POST", "/v1/uploads", create, None, {"authorization": ""}),
    )


def answers(transport, close=None):
    digest = "sha256:" + bench.sha256(CLIP).hexdigest()

    async def script(client):
        out, handle = [], None
        try:
            for label, method, path, body, content, extra in probes(digest, len(CLIP)):
                headers = {k: v for k, v in {**bearer(), **extra}.items() if v}
                r = await client.request(method, path.replace("{h}", handle or ""),
                                         json=body, content=content, headers=headers)
                data = r.json() if r.content else None
                if label == "create":
                    handle = data["upload_handle"]
                keys = sorted(data) if isinstance(data, dict) else None
                code = data.get("error", {}).get("code") if isinstance(data, dict) else None
                media = sorted(data["media"]) if isinstance(data, dict) and "media" in data \
                    else None
                out.append((label, r.status_code, keys, code, media))
            return out
        finally:
            if close:
                await close()
    return run_client(transport, script)[0]


def test_the_fake_gateway_answers_the_upload_probes_like_the_real_router():
    """Oracle: a fake that accepts the legacy fields, answers a URL instead of a handle, or
    takes completion fields lets an incompatible client pass the fake suite (RV-07). Every
    probe's status, error code and answer keys must match the mounted router's."""
    app, _, close = real_router()
    real = answers(httpx.ASGITransport(app=app), close)
    fake = answers(FakeGateway().transport())
    assert [r[:2] for r in real] == [
        ("legacy create fields", 400), ("create", 201), ("complete before any byte", 400),
        ("put wrong media type", 400), ("put", 204), ("complete with fields", 400),
        ("complete", 200), ("unknown handle", 404), ("anonymous create", 401)], real
    for want, got in zip(real, fake):
        # error.code is compared as well: `invalid_request` vs `unsupported_media` matters
        assert got == want, f"fake drifted from the real router on {want[0]}: {got} != {want}"


def test_no_returned_origin_ever_receives_the_bytes_or_the_bearer():
    """Oracle: the previous client PUT to whatever `url` the answer named, with any headers
    the answer supplied - a hostile or compromised answer could collect the object and, with
    one header, the key. The destination is built from --base-url, never from the answer."""
    foreign = "https://collector.invalid/steal?X-Amz-Signature=CANARY"
    gw = FakeGateway(upload_url=foreign)
    with tempfile.TemporaryDirectory() as tmp:
        path, row = clip_file(tmp), {}
        handle, seen = run_client(gw.transport(),
                                  lambda client: bench.upload(client, cfg_for(), path, row))
    assert bench.HANDLE_OK.fullmatch(handle)
    assert {host for _, host, _, _ in seen} == {"gateway.test"}, seen
    assert ("PUT", "gateway.test", f"/v1/uploads/{handle}", True) in seen
    assert not gw.foreign, "a request left for an origin the answer named"


def test_an_answer_outside_the_contract_is_refused_before_any_byte_moves():
    """Oracle: a destination_ref that is not `infrx-upload:<handle>`, or a completion that
    names another handle or other bytes, must end the upload as UploadFailed - never a
    reference sent to chat for bytes this client cannot vouch for."""
    cases = {"destination_ref": {"bad_destination_ref": "https://collector.invalid/x"},
             "completion handle": {"complete_override": {"upload_handle": "upl_" + "Z" * 30}},
             "completion digest": {"complete_override": {"media_digest": "sha256:" + "0" * 64}},
             "completion bytes": {"complete_override": {"media_bytes": 1}}}
    for why, knobs in cases.items():
        gw = FakeGateway(**knobs)
        with tempfile.TemporaryDirectory() as tmp:
            path, row = clip_file(tmp), {}

            async def script(client):
                try:
                    await bench.upload(client, cfg_for(), path, row)
                except bench.UploadFailed:
                    return "refused"
                return "accepted"
            verdict, seen = run_client(gw.transport(), script)
        assert verdict == "refused", why
        if why == "destination_ref":
            assert [m for m, _, _, _ in seen] == ["POST"], f"{why}: bytes moved after a bad ticket"
