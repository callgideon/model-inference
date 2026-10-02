"""AP-11b/11c: the consumer-side stages - 01's audiences and account/workspace reads (AP-01),
08's catalog, grant and key (AP-01/AP-03), 09's upload and async job, 10's poll/result and
console request/ledger (AP-02), 11's sync and SSE, 12's capture and data grants (AP-07). Each
drives only FastAPI routes through `ctx` (runner.Context) and asserts what verification.md's
row says before the next step; a half whose package the target does not serve
(`ctx.composed`) is skipped, and the runner reports the stage BLOCKED naming it.

Identities (config `identities`): `<holder>` is a consumer KEY minted here through
`POST /console/v1/keys` (or, isolated without AP-03, a declared fixture); `<holder>_web` is
the same individual's identity-provider session, the only actor the console routes take."""
from __future__ import annotations

import hashlib
import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import Blocked

FINISHED = ("succeeded", "failed", "cancelled")
SETTLED_HOLDS = ("settled", "released")      # a reserve that is no longer held (0006)
TEXT = [{"role": "user", "content": "Describe the scene in one sentence."}]
#: Well formed for no job: an authenticated caller gets the 404 every unknown handle gets.
PROBE_HANDLE = "job_ap11probe"
#: A compact JWT nobody signed: the session door must refuse it 401.
FORGED = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhcDExLWZvcmdlZCJ9.Zm9yZ2Vk"
#: A key-shaped bearer nobody minted: the web door takes sessions, never keys.
KEY_SHAPED = "sk-infrx-ap11000000000000000000000000000000000"


def _json(response) -> dict:
    try:
        found = response.json()
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def s01(ctx) -> None:
    """Distinct audiences on the mounted doors: a consumer key at the data plane, a Lab
    session at the control plane; account/workspace/capability reads wait on AP-01."""
    if ctx.available("consumer_a"):           # minted by 08 (AP-03), or an isolated fixture
        keyed(ctx, "consumer_a")
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
    if ctx.composed("AP-01"):
        identity(ctx, admin["provider_org_id"])


def keyed(ctx, holder: str) -> None:
    """A consumer key authenticates at the data plane and is never a web session."""
    known = ctx.call("GET", "/v1/jobs/{handle}", params={"handle": PROBE_HANDLE}, actor=holder)
    ctx.check("a valid consumer key is authenticated", known.status_code == 404,
              known.status_code)
    if ctx.composed("AP-01"):
        web = ctx.call("GET", "/console/v1/me", actor=holder)
        ctx.check("a consumer key is never a web session (401)", web.status_code == 401,
                  web.status_code)


def identity(ctx, provider: str) -> None:
    """AP-01's reads per actor. They are mounted on the gateway (IDENTITY_API/AUTH_FACADE);
    the Lab unit gains the workspace family with api-identity-2."""
    free = ctx.call("GET", "/auth/v1/availability")
    ctx.check("auth availability is a fresh read", free.status_code == 200 and all(
        (_json(free).get(k) or {}).get("verified_at") for k in ("sign_in", "signup_grant")),
        _json(free))
    me = _json(ctx.call("GET", "/console/v1/me", actor="consumer_a_web"))
    ctx.check("a consumer session reads its own account as a session",
              (me.get("actor") or {}).get("audience") == "session"
              and me.get("state") in ("onboarding", "ready") and not me.get("suspended"), me)
    keyed = ctx.call("GET", "/console/v1/me", headers={"Authorization": f"Bearer {KEY_SHAPED}"})
    ctx.check("a key is never a web session (401)", keyed.status_code == 401, keyed.status_code)
    allowed = _json(ctx.call("GET", "/console/v1/capabilities", actor="consumer_a_web"))
    ctx.check("the account's actions are the server's: no operator console for a consumer",
              (allowed.get("actions") or {}).get("operator_console") is False
              and isinstance(allowed.get("features"), dict), allowed.get("actions"))
    query = {"provider_org_id": provider}
    rows = _json(ctx.call("GET", "/lab/v1/workspaces", actor="provider_admin")).get("data")
    roles = {w.get("provider_org_id"): w.get("role") for w in rows or []}
    ctx.check("the administrator's workspace and role", roles.get(provider) == "administrator",
              roles)
    held = ctx.call("GET", "/lab/v1/capabilities", actor="provider_admin", query=query)
    ctx.check("the administrator's capabilities include member management",
              held.status_code == 200
              and "manage_members" in (_json(held).get("capabilities") or ()), held.status_code)
    members = _json(ctx.call("GET", "/lab/v1/workspaces/{id}/members", params={"id": provider},
                             actor="provider_admin")).get("data") or []
    ctx.check("the member list names the administrator only by membership",
              [m.get("role") for m in members] == ["administrator"], len(members))
    for actor in ("outsider", "consumer_a_web"):
        mine = _json(ctx.call("GET", "/lab/v1/workspaces", actor=actor)).get("data")
        ctx.check(f"{actor} holds no workspace", mine == [], mine)
    other = ctx.call("GET", "/lab/v1/capabilities", actor="outsider", query=query)
    ctx.check("an outsider has no capabilities there", other.status_code in (403, 404),
              other.status_code)
    listed = ctx.call("GET", "/lab/v1/workspaces/{id}/members", params={"id": provider},
                      actor="outsider")
    ctx.check("an outsider cannot list the members", listed.status_code in (403, 404),
              listed.status_code)


def s08(ctx) -> None:
    """The public catalog lists the model under test. In isolated mode that listing is the
    world's declared fixture until stage 07 publishes one through AP-06."""
    listed = ctx.call("GET", "/v1/models")
    rows = {row.get("id"): row for row in _json(listed).get("data", []) if isinstance(row, dict)}
    row = rows.get(ctx.config["model"])
    ctx.require("the catalog lists the model under test",
                listed.status_code == 200 and row is not None, sorted(rows))
    ctx.version("listing", {k: v for k, v in (row or {}).items()
                            if k in ("id", "owned_by", "created", "listing_version")})
    if not (ctx.composed("AP-01") and ctx.composed("AP-03")):
        return
    for holder in ("consumer_a", "consumer_b"):
        web = holder + "_web"
        claim = ctx.mutate(f"08.claim.{holder}", "POST", "/console/v1/signup-grant/claim",
                           actor=web, extract=lambda r: {"http": r.status_code,
                                                         **_pick(r, "status", "credit")})
        ctx.require(f"{holder}'s signup grant is claimed",
                    claim["http"] == 200 and claim["status"] in ("granted", "replayed"), claim)
        again = _json(ctx.call("POST", "/console/v1/signup-grant/claim", actor=web))
        ctx.check(f"{holder}: a second claim replays, never a second grant",
                  again.get("status") == "replayed", again.get("status"))
        me = _json(ctx.call("GET", "/console/v1/me", actor=web))
        grant = me.get("signup_grant") or {}
        ctx.check(f"{holder}: one individual grant in CREDIT on the account",
                  me.get("state") == "ready" and grant.get("state") == "granted"
                  and (grant.get("amount") or {}).get("unit") == "CREDIT", grant)
        mint(ctx, holder, web, f"08.key.{holder}")
    keyed(ctx, "consumer_a")
    private_refused(ctx)
    ctx.publish(org_a=_json(ctx.call("GET", "/console/v1/me", actor="consumer_a_web"))
                .get("actor", {}).get("org_id"))


def private_refused(ctx) -> None:
    """contracts.md §5: a consumer key cannot call a private endpoint - stage 04's, when it
    passed (the keys exist only from here on, so 06 cannot prove it)."""
    try:
        model = ctx.outputs("04").get("private_model")
    except Blocked:
        return
    refused = ctx.call("POST", "/v1/chat/completions", actor="consumer_a", inference=True,
                       json={"model": model, "messages": TEXT, "max_tokens": 4})
    ctx.check("a consumer key cannot call the private endpoint",
              refused.status_code in (403, 404), refused.status_code)


def _pick(response, *names: str) -> dict:
    found = _json(response)
    return {name: found.get(name) for name in names}


def mint(ctx, holder: str, web: str, name: str) -> str:
    """One consumer key through the key API, its secret kept 0600 (never in the state). A
    replay of the same Idempotency-Key is the same key and never re-reveals the secret; a
    secret lost with its acknowledgement is revoked and minted once more under a new name."""
    secret = ctx.identity(holder)["secret"]
    for attempt in (name, name + ".again"):
        def extract(r, attempt=attempt):
            body = _json(r)
            key = body.get("key") or {}
            if r.status_code == 201 and body.get("secret") and key.get("key_id"):
                ctx.keep(secret, body["secret"], key["key_id"])
            return {"status": r.status_code, "key_id": key.get("key_id"),
                    "secret_returned": body.get("secret_returned")}
        made = ctx.mutate(attempt, "POST", "/console/v1/keys", actor=web,
                          json={"name": f"ap11 {holder}"}, extract=extract)
        ctx.require(f"{holder}'s key is created", made["status"] in (200, 201)
                    and made["key_id"], made)
        if ctx.minted(holder) == made["key_id"]:
            break
        ctx.call("DELETE", "/console/v1/keys/{id}", params={"id": made["key_id"]}, actor=web,
                 headers={"Idempotency-Key": ctx.key(attempt) + ".revoke"})
    else:
        raise Blocked(f"BLOCKED[resume] {holder}'s key secret was lost twice with its "
                      "acknowledgement; both keys are revoked - start a new state file")
    replay = _json(ctx.call("POST", "/console/v1/keys", actor=web, json={"name": f"ap11 {holder}"},
                            headers={"Idempotency-Key": ctx.key(attempt)}))
    ctx.check(f"{holder}: a retried creation is the same key, its secret never re-revealed",
              (replay.get("key") or {}).get("key_id") == made["key_id"]
              and replay.get("secret") is None and replay.get("replayed") is True,
              {k: replay.get(k) for k in ("replayed", "secret_returned")})
    if ctx.composed("AP-02"):
        keys = _json(ctx.call("GET", "/console/v1/keys", actor=web)).get("data") or []
        named = [k for k in keys if k.get("name") == f"ap11 {holder}" and not k.get("revoked_at")]
        ctx.check(f"{holder}: one live key despite the retried creation",
                  [k.get("id") for k in named] == [made["key_id"]], len(named))
    ctx.own("key", made["key_id"], {"method": "DELETE", "route": "/console/v1/keys/{id}",
                                    "actor": web})
    return made["key_id"]


def video(ref: str) -> list:
    return [{"role": "user", "content": [
        {"type": "text", "text": "What happens in this clip?"},
        {"type": "video_url", "video_url": {"url": ref}}]}]


def upload(ctx, actor: str, step: str) -> str:
    """The finite-video fixture through the upload API as `actor`: its destination ref."""
    ctx.credential(actor)
    media = ctx.config.get("media") or {}
    if not media.get("clip"):
        raise Blocked("config names no media clip (media.clip): verification.md prerequisite 4")
    clip, mime = Path(media["clip"]).read_bytes(), media.get("mime", "video/mp4")
    ticket = ctx.mutate(f"{step}.upload", "POST", "/v1/uploads", actor=actor, json={
        "bytes": len(clip), "digest": "sha256:" + hashlib.sha256(clip).hexdigest(),
        "accepted_mime": [mime]}, extract=lambda r: {
            "status": r.status_code, **{k: _json(r).get(k)
                                        for k in ("upload_handle", "destination_ref")}})
    ctx.require("the upload ticket is issued 201", ticket["status"] == 201, ticket["status"])
    handle = {"handle": ticket["upload_handle"]}
    ctx.own("upload", ticket["upload_handle"], None)
    put = ctx.mutate(f"{step}.put", "PUT", "/v1/uploads/{handle}", params=handle, actor=actor,
                     content=clip, headers={"Content-Type": mime},
                     extract=lambda r: {"status": r.status_code})
    ctx.require("the bytes are stored 204", put["status"] == 204, put["status"])
    done = ctx.mutate(f"{step}.complete", "POST", "/v1/uploads/{handle}/complete", params=handle,
                      actor=actor, extract=lambda r: {"status": r.status_code,
                                                      "state": _json(r).get("state"),
                                                      "fields": sorted(_json(r))})
    ctx.require("the upload completes 200", done["status"] == 200, done)
    ctx.check("the upload is consumer media, not a model artifact",
              str(ticket["destination_ref"]).startswith("infrx-upload:")
              and not {"artifact_id", "manifest"} & set(done["fields"]), done["fields"])
    return ticket["destination_ref"]


def s09(ctx) -> None:
    actor = "consumer_a"
    body = {"model": ctx.config["model"], "messages": video(upload(ctx, actor, "09"))}

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
    ctx.require("the job succeeded", state == "succeeded", state)
    result = _json(ctx.call("GET", "/v1/jobs/{handle}/result", params=handle, actor=actor))
    ctx.check("the result names the model under test",
              (result.get("response") or {}).get("model") == ctx.config["model"],
              (result.get("response") or {}).get("model"))
    ctx.check("the result reports usage", bool(result.get("usage")), None)
    ctx.credential("consumer_b")
    foreign = ctx.call("GET", "/v1/jobs/{handle}", params=handle, actor="consumer_b")
    ctx.check("consumer B cannot read A's job", foreign.status_code == 404, foreign.status_code)
    if ctx.composed("AP-02"):
        settled(ctx, out["request_id"])


def settled(ctx, request_id: str) -> None:
    """AP-02: the console's request pins agree with the call, and its one CREDIT settlement
    is the ledger's one debit for it; the reserve is no longer held."""
    deadline = time.monotonic() + float(ctx.config.get("poll_timeout_s", 120))
    while True:
        seen = ctx.call("GET", "/console/v1/requests/{id}", params={"id": request_id},
                        actor="consumer_a_web")
        row = _json(seen)
        if row.get("settlement_state") == "settled" or time.monotonic() > deadline:
            break
        time.sleep(float(ctx.config.get("poll_interval_s", 0.5)))
    ctx.require("the console reads the request", seen.status_code == 200
                and row.get("request_id") == request_id, seen.status_code)
    ctx.check("the console's pins agree with the call",
              row.get("requested_model") == ctx.config["model"]
              and row.get("state") == "succeeded", {k: row.get(k) for k in (
                  "requested_model", "model_revision", "state")})
    charged = row.get("charged") or {}
    ctx.check("one CREDIT settlement, the reserve released",
              row.get("settlement_state") == "settled" and charged.get("unit") == "CREDIT"
              and row.get("hold_state") in SETTLED_HOLDS, {k: row.get(k) for k in (
                  "settlement_state", "hold_state")})
    ledger = _json(ctx.call("GET", "/console/v1/credit-ledger", actor="consumer_a_web",
                            query={"limit": 100})).get("data") or []
    debits = [e for e in ledger if e.get("request_id") == request_id]
    ctx.check("the ledger holds exactly that one debit for the request",
              len(debits) == 1 and _amount(debits[0]) == -_amount({"amount": charged}),
              [(e.get("kind"), (e.get("amount") or {}).get("amount")) for e in debits])
    foreign = ctx.call("GET", "/console/v1/requests/{id}", params={"id": request_id},
                       actor="consumer_b_web")
    ctx.check("consumer B's console cannot read A's request", foreign.status_code == 404,
              foreign.status_code)


def _amount(entry: dict) -> Decimal | None:
    try:
        return Decimal(str((entry.get("amount") or {}).get("amount")))
    except (InvalidOperation, TypeError):
        return None


def _sse(response) -> dict:
    frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    chunks = []
    for frame in frames:
        if frame == "[DONE]":
            continue
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
                          "model": _json(r).get("model"), "usage": bool(_json(r).get("usage")),
                          "request_id": r.headers.get("Inference-Id")})
    ctx.check("sync answers 200 in-line, never 202", sync["status"] == 200, sync["status"])
    ctx.check("the answer names its request (Inference-Id)", bool(sync["request_id"]), None)
    ctx.check("sync names the model and reports usage",
              sync["model"] == ctx.config["model"] and sync["usage"], sync)
    sse = ctx.mutate("11.sse", "POST", "/v1/chat/completions", actor=actor,
                     json={**body, "stream": True}, inference=True, extract=_sse)
    ctx.check("SSE is an event stream", sse["status"] == 200 and sse["event_stream"], sse)
    ctx.check("SSE ends with [DONE]", sse["done"], sse)
    ctx.check("SSE carries content for the model",
              sse["content"] and sse["models"] == [ctx.config["model"]], sse)
    ctx.publish(sync_request_id=sync["request_id"])
    if ctx.composed("AP-02") and sync["request_id"]:
        settled(ctx, sync["request_id"])


def s12(ctx) -> None:
    """AP-07: capture on a fresh test key and a scoped provider_sharing + external_judging
    grant, both through the data-use API, then one captured request with that key. The
    earlier keys stay uncaptured (capture is per key; a cached decision never covers them)."""
    web, provider = "consumer_a_web", ctx.identity("provider_admin")["provider_org_id"]
    org = ctx.outputs("08").get("org_a")
    key_id = mint(ctx, "consumer_a_capture", web, "12.key")
    version = ctx.pinned("12.consent_version", lambda: (_json(ctx.call(
        "GET", "/console/v1/data-use", actor=web)).get("consent") or {}).get("version", 0))
    capture = ctx.mutate("12.capture", "PUT", "/console/v1/keys/{id}/capture",
                         params={"id": key_id}, actor=web,
                         json={"mode": "full", "consent_version": version,
                               "retention_days": 30, "evaluation_consent": False},
                         extract=lambda r: {"status": r.status_code, "modes": {
                             k.get("key_id"): k.get("effective_mode")
                             for k in _json(r).get("keys") or []}})
    ctx.require("capture is enabled through the API", capture["status"] == 200, capture["status"])
    earlier = ctx.minted("consumer_a")
    ctx.check("only the test key captures; the earlier key stays uncaptured",
              capture["modes"].get(key_id) == "full"
              and capture["modes"].get(earlier) in ("off", None), capture["modes"])
    def current() -> int:
        return max((g.get("version") or 0 for g in _json(ctx.call(
            "GET", "/console/v1/data-grants", actor=web)).get("data") or []
            if g.get("provider_org_id") == provider), default=0)
    grant = ctx.mutate("12.grant", "POST", "/console/v1/data-grants", actor=web, json={
        "provider_org_id": provider, "model_ids": [ctx.config["model_uuid"]],
        "categories": ["request_content", "response_content", "feedback"],   # 16 reviews it
        "purposes": ["provider_sharing", "external_judging"], "retention_days": 30,
        "grant_version": ctx.pinned("12.grant_version", current)},
        extract=lambda r: {"status": r.status_code, **_pick(r, "grant_id", "version", "state")})
    ctx.require("the grant is written", grant["status"] in (200, 201)
                and grant["state"] == "active", grant)
    ctx.own("data_grant", grant["grant_id"], {"method": "DELETE", "actor": web,
                                              "route": "/console/v1/data-grants/{id}"})
    listed = [g for g in _json(ctx.call("GET", "/console/v1/data-grants", actor=web))
              .get("data") or [] if g.get("grant_id") == grant["grant_id"]]
    ctx.check("the grant is persisted with exactly the purposes asked",
              len(listed) == 1 and sorted(listed[0].get("purposes") or ()) == [
                  "external_judging", "provider_sharing"], listed)
    ctx.publish(grant_id=grant["grant_id"], grantor_org_id=org, capture_key_id=key_id)
    if not ctx.config.get("traces"):
        raise Blocked("BLOCKED[AP-07] the target composes no trace storage (config `traces`: "
                      "TRACE_PUMPS + ClickHouse + LAB_TRACES); "
                      + str(ctx.config.get("traces_missing") or "the capture half waits on it"))
    body = {"model": ctx.config["model"], "messages": TEXT, "max_tokens": 16}
    sent = ctx.mutate("12.captured", "POST", "/v1/chat/completions", actor="consumer_a_capture",
                      json=body, inference=True, extract=lambda r: {
                          "status": r.status_code, "request_id": r.headers.get("Inference-Id")})
    ctx.require("the captured request is answered in-line", sent["status"] == 200, sent)
    ctx.publish(captured_request_id=sent["request_id"])
