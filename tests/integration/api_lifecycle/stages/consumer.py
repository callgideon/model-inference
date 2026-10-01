"""AP-11b: the stages whose routes are mounted on the base - 01's audience checks, 08's
catalog, 09's upload and async job, 10's poll/result, 11's sync and SSE. Each drives only
FastAPI routes through `ctx` (runner.Context) and asserts what verification.md's row says
before the next step; the console/account halves stay BLOCKED until AP-01/02/03 land."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import Blocked

FINISHED = ("succeeded", "failed", "cancelled")
TEXT = [{"role": "user", "content": "Describe the scene in one sentence."}]
#: Well formed for no job: an authenticated caller gets the 404 every unknown handle gets.
PROBE_HANDLE = "job_ap11probe"
#: A compact JWT nobody signed: the session door must refuse it 401.
FORGED = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhcDExLWZvcmdlZCJ9.Zm9yZ2Vk"


def _json(response) -> dict:
    try:
        found = response.json()
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def s01(ctx) -> None:
    """Distinct audiences on the mounted doors: a consumer key at the data plane, a Lab
    session at the control plane; account/workspace/capability reads wait on AP-01."""
    ctx.credential("consumer_a")
    known = ctx.call("GET", "/v1/jobs/{handle}", params={"handle": PROBE_HANDLE},
                     actor="consumer_a")
    ctx.check("a valid consumer key is authenticated", known.status_code == 404,
              known.status_code)
    anonymous = ctx.call("GET", "/v1/jobs/{handle}", params={"handle": PROBE_HANDLE})
    ctx.check("a missing key is refused 401", anonymous.status_code == 401,
              anonymous.status_code)
    admin = ctx.identity("provider_admin")
    query = {"provider_org_id": admin["provider_org_id"]}
    member = ctx.call("GET", "/lab/v1/control/models", origin="lab", actor="provider_admin",
                      query=query)
    ctx.check("a member session reads its workspace", member.status_code == 200,
              member.status_code)
    ctx.identity("outsider")
    outsider = ctx.call("GET", "/lab/v1/control/models", origin="lab", actor="outsider",
                        query=query)
    ctx.check("a non-member session is refused", outsider.status_code in (403, 404),
              outsider.status_code)
    forged = ctx.call("GET", "/lab/v1/control/models", origin="lab", query=query,
                      headers={"Authorization": f"Bearer {FORGED}"})
    ctx.check("a forged session is refused 401", forged.status_code == 401, forged.status_code)


def s08(ctx) -> None:
    """The public catalog lists the model under test. In isolated mode that listing is the
    world's declared fixture until stage 07 publishes one through AP-06."""
    listed = ctx.call("GET", "/v1/models")
    rows = {row.get("id"): row for row in _json(listed).get("data", []) if isinstance(row, dict)}
    model = ctx.config["model"]
    ctx.require("the catalog lists the model under test",
                listed.status_code == 200 and model in rows, sorted(rows))
    ctx.version("listing", {k: v for k, v in rows[model].items()
                            if k in ("id", "owned_by", "created", "listing_version")})


def video(ref: str) -> list:
    return [{"role": "user", "content": [
        {"type": "text", "text": "What happens in this clip?"},
        {"type": "video_url", "video_url": {"url": ref}}]}]


def s09(ctx) -> None:
    actor = "consumer_a"
    ctx.credential(actor)
    media = ctx.config.get("media") or {}
    if not media.get("clip"):
        raise Blocked("config names no media clip (media.clip): verification.md prerequisite 4")
    clip, mime = Path(media["clip"]).read_bytes(), media.get("mime", "video/mp4")
    ticket = ctx.mutate("09.upload", "POST", "/v1/uploads", actor=actor, json={
        "bytes": len(clip), "digest": "sha256:" + hashlib.sha256(clip).hexdigest(),
        "accepted_mime": [mime]}, extract=lambda r: {
            "status": r.status_code, **{k: _json(r).get(k)
                                        for k in ("upload_handle", "destination_ref")}})
    ctx.require("the upload ticket is issued 201", ticket["status"] == 201, ticket["status"])
    handle = {"handle": ticket["upload_handle"]}
    ctx.own("upload", ticket["upload_handle"], None)
    put = ctx.mutate("09.put", "PUT", "/v1/uploads/{handle}", params=handle, actor=actor,
                     content=clip, headers={"Content-Type": mime},
                     extract=lambda r: {"status": r.status_code})
    ctx.require("the bytes are stored 204", put["status"] == 204, put["status"])
    done = ctx.mutate("09.complete", "POST", "/v1/uploads/{handle}/complete", params=handle,
                      actor=actor, extract=lambda r: {"status": r.status_code,
                                                      "state": _json(r).get("state"),
                                                      "fields": sorted(_json(r))})
    ctx.require("the upload completes 200", done["status"] == 200, done)
    ctx.check("the upload is consumer media, not a model artifact",
              str(ticket["destination_ref"]).startswith("infrx-upload:")
              and not {"artifact_id", "manifest"} & set(done["fields"]), done["fields"])
    body = {"model": ctx.config["model"], "messages": video(ticket["destination_ref"])}

    def found(out: dict) -> bool:
        again = ctx.call("GET", "/v1/jobs/{handle}", params={"handle": out["job_handle"]},
                         actor=actor)
        return again.status_code == 200

    job = ctx.mutate("09.job", "POST", "/v1/jobs", actor=actor, json=body, inference=True,
                     reconcile=found, extract=lambda r: {
                         "status": r.status_code, **{k: _json(r).get(k) for k in (
                             "job_handle", "request_id", "idempotency_replayed")}})
    ctx.require("the job is accepted 202", job["status"] == 202, job["status"])
    ctx.own("job", job["job_handle"], None)
    key = {"Idempotency-Key": ctx.key("09.job")}
    replay = ctx.call("POST", "/v1/jobs", actor=actor, json=body, headers=key, inference=True)
    ctx.check("a replay returns the same job", replay.status_code == 202
              and _json(replay).get("job_handle") == job["job_handle"], replay.status_code)
    clash = ctx.call("POST", "/v1/jobs", actor=actor, json={**body, "max_tokens": 7},
                     headers=key, inference=True)
    ctx.check("a changed body under the key is 409", clash.status_code == 409,
              clash.status_code)
    ctx.publish(job_handle=job["job_handle"], request_id=job["request_id"])


def s10(ctx) -> None:
    actor, out = "consumer_a", ctx.outputs("09")
    handle = {"handle": out["job_handle"]}
    deadline = time.monotonic() + float(ctx.config.get("poll_timeout_s", 120))
    while True:
        status = ctx.call("GET", "/v1/jobs/{handle}", params=handle, actor=actor)
        state = _json(status).get("state")
        if state in FINISHED or time.monotonic() > deadline:
            break
        time.sleep(float(ctx.config.get("poll_interval_s", 0.5)))
    ctx.require("the job reached a terminal state", state in FINISHED, state)
    ctx.check("the job succeeded", state == "succeeded", state)
    result = _json(ctx.call("GET", "/v1/jobs/{handle}/result", params=handle, actor=actor))
    ctx.check("the result names the model under test",
              (result.get("response") or {}).get("model") == ctx.config["model"],
              (result.get("response") or {}).get("model"))
    ctx.check("the result reports usage", bool(result.get("usage")), None)
    ctx.credential("consumer_b")
    foreign = ctx.call("GET", "/v1/jobs/{handle}", params=handle, actor="consumer_b")
    ctx.check("consumer B cannot read A's job", foreign.status_code == 404, foreign.status_code)


def _sse(response) -> dict:
    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    chunks = []
    for frame in frames[:-1]:
        try:
            chunks.append(json.loads(frame))
        except ValueError:
            continue
    text = "".join(str((choice.get("delta") or {}).get("content") or "")
                   for chunk in chunks for choice in chunk.get("choices", []))
    return {"status": response.status_code,
            "event_stream": response.headers.get("content-type", "").startswith(
                "text/event-stream"),
            "done": bool(frames) and frames[-1] == "[DONE]", "content": bool(text),
            "models": sorted({chunk.get("model") for chunk in chunks if chunk.get("model")})}


def s11(ctx) -> None:
    actor = "consumer_a"
    ctx.credential(actor)
    body = {"model": ctx.config["model"], "messages": TEXT, "max_tokens": 16}
    sync = ctx.mutate("11.sync", "POST", "/v1/chat/completions", actor=actor, json=body,
                      inference=True, extract=lambda r: {
                          "status": r.status_code, "object": _json(r).get("object"),
                          "model": _json(r).get("model"), "usage": bool(_json(r).get("usage"))})
    ctx.check("sync answers 200 in-line, never 202", sync["status"] == 200, sync["status"])
    ctx.check("sync names the model and reports usage",
              sync["model"] == ctx.config["model"] and sync["usage"], sync)
    sse = ctx.mutate("11.sse", "POST", "/v1/chat/completions", actor=actor,
                     json={**body, "stream": True}, inference=True, extract=_sse)
    ctx.check("SSE is an event stream", sse["status"] == 200 and sse["event_stream"], sse)
    ctx.check("SSE ends with [DONE]", sse["done"], sse)
    ctx.check("SSE carries content for the model",
              sse["content"] and sse["models"] == [ctx.config["model"]], sse)
