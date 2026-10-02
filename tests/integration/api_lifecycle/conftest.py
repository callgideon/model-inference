"""AP-11's layer-1 fixtures: a contract fake of the routes on the base - the data plane, and
(11c) the wave-7 families AP-01/02/03/04/07/08 as their R270 wire contracts state them -
served through `httpx.MockTransport`, so the runner's own decisions (state, resume, modes,
exit codes, redaction, BLOCKED semantics) and each stage's assertions are proved without a
stack. The isolated run on the ap11 stack (`world.py`) is the real-service proof; this fake
never stands in for it."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # `api_lifecycle` as a package

MODEL = "nemostation/marlin-2b"
MODEL_UUID = "d0000001-0000-4000-8000-000000000001"
KEYS = {"sk-infrx-alphaSECRET0001": "org-a", "sk-infrx-betaSECRET00002": "org-b"}
SESSIONS = {"eyJhbGci.eyJzdWIi.adminSIG": ("admin", True), "eyJhbGci.eyJzdWIi.outsideSIG": ("x", False)}
#: web sessions -> (user, org); the admin and the outsider are Lab users with accounts too
WEB = {"eyJhbGci.eyJzdWIi.alphaWEB": ("u-alpha", "org-a"),
       "eyJhbGci.eyJzdWIi.betaWEB": ("u-beta", "org-b"),
       "eyJhbGci.eyJzdWIi.adminSIG": ("u-admin", "org-admin"),
       "eyJhbGci.eyJzdWIi.outsideSIG": ("u-outsider", "org-outsider")}
PROVIDER = "b0000001-0000-4000-8000-000000000001"
COMPOSED = ["AP-01", "AP-02", "AP-03", "AP-04", "AP-07", "AP-08"]
CHARGE = "3"
_V4 = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
PAYER = f"lab:payer:{PROVIDER}:{_V4}@sha256:[0-9a-f]{{64}}"


#: Product defects the fake can be told to have, each one a stage's failure oracle.
DEFECTS = ("replay_new_job", "conflict_accepted", "artifact_upload", "foreign_read",
           "result_model", "no_usage", "sse_no_done", "sse_plain", "sse_wrong_model",
           "sync_async", "sync_no_usage", "no_member", "outsider_member", "forged_accepted",
           "anon_allowed", "key_unauthenticated",
           # 11c: the wave-7 families
           "key_is_session", "consumer_operator", "outsider_workspace", "outsider_members",
           "grant_twice", "key_twice", "secret_rerevealed", "replay_other_key",
           "grant_not_on_account", "hold_left", "double_debit",
           "console_foreign", "capture_all_keys", "grant_not_persisted", "uncaptured_traced",
           "trace_no_content", "trace_wrong_pins", "content_after_revoke",
           "judge_after_revoke", "dry_run_sends", "dry_run_scored", "run_replay_new",
           "judge_charges_consumer", "budget_spent", "review_twice", "review_not_human",
           "calibrated_on_one", "op_fails", "op_replay_new", "artifact_missing_file",
           "revision_mutable", "revision_unpinned", "zero_elapsed", "no_inference_id")


class FakeGateway:
    """The base's consumer routes and the Lab session read, as their wire contracts state
    them. `lose_ack` names "METHOD path" whose NEXT call commits and then times out (a lost
    acknowledgement: the server did the work, the client never heard). `defects` turns on
    named product defects (DEFECTS)."""

    def __init__(self) -> None:
        self.defects: set[str] = set()
        self.sent: list[tuple[str, str, str | None]] = []
        self.jobs: dict[str, dict] = {}
        self.idem: dict[tuple[str, str], tuple[str, str]] = {}
        self.lose_ack: set[str] = set()
        self.models = [MODEL]
        self.created = 0
        self.keys = dict(KEYS)               # secret -> org (fixture keys and minted ones)
        self.minted: dict[str, dict] = {}    # key id -> {org, name, secret, revoked, mode}
        self.key_idem: dict[tuple[str, str], str] = {}
        self.granted: set[str] = set()       # orgs whose signup grant is claimed
        self.requests: dict[str, dict] = {}  # request id -> {org, key}
        self.traces: dict[str, dict] = {}    # request id -> {org}
        self.consent = 0
        self.grants: dict[str, dict] = {}    # grant id -> {org, version, state, purposes}
        self.objects: dict[str, dict] = {}   # id -> anything the AP-04/08 fakes keep
        self.runs: list[str] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def posts(self, path: str) -> list[str | None]:
        return [key for method, p, key in self.sent if method == "POST" and p == path]

    def _org(self, request) -> str | None:
        return self.keys.get(request.headers.get("authorization", "").removeprefix("Bearer "))

    def _web(self, request) -> tuple[str, str] | None:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token in WEB:
            return WEB[token]
        if "key_is_session" in self.defects and token.startswith("sk-"):
            return ("u-key", "org-key")
        return None

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.sent.append((method, path, request.headers.get("idempotency-key")))
        answer = self.route(request, method, path)
        if f"{method} {path}" in self.lose_ack:
            self.lose_ack.discard(f"{method} {path}")
            raise httpx.ReadTimeout("the acknowledgement was lost", request=request)
        return answer

    def route(self, request, method: str, path: str) -> httpx.Response:
        found = wave7(self, request, method, path)
        if found is not None:
            return found
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "nemostation"} for m in self.models]})
        if path == "/lab/v1/control/models":
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in SESSIONS and "forged_accepted" not in self.defects:
                return httpx.Response(401, json={"refusal": "unauthenticated"})
            member = SESSIONS.get(token, ("", True))[1] \
                and request.url.params.get("provider_org_id") == PROVIDER
            member = (member and "no_member" not in self.defects) \
                or (not member and "outsider_member" in self.defects)
            return httpx.Response(200 if member else 403,
                                  json={"models": []} if member else {"refusal": "not_a_member"})
        org = self._org(request)
        if path == "/v1/jobs/job_ap11probe" and (
                (org is None and "anon_allowed" in self.defects)
                or (org is not None and "key_unauthenticated" in self.defects)):
            return httpx.Response(404 if org is None else 401, json={"error": {}})
        if org is None:
            return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})
        if method == "POST" and path == "/v1/uploads":
            handle = f"up_{uuid.uuid4().hex}"
            return httpx.Response(201, json={"upload_handle": handle,
                                             "destination_ref": f"infrx-upload:{handle}"})
        if method == "PUT" and path.startswith("/v1/uploads/"):
            return httpx.Response(204)
        if method == "POST" and path.endswith("/complete"):
            return httpx.Response(200, json={
                "upload_handle": path.split("/")[3], "state": "finalized",
                "media": {"kind": "video"},
                **({"artifact_id": "art_1"} if "artifact_upload" in self.defects else {})})
        if method == "POST" and path in ("/v1/jobs", "/v1/chat/completions"):
            return self.infer(request, org, path)
        if method == "GET" and path.startswith("/v1/jobs/"):
            handle = path.split("/")[3]
            job = self.jobs.get(handle)
            if job is None or (job["org"] != org and "foreign_read" not in self.defects):
                return httpx.Response(404, json={"error": {"code": "not_found"}})
            if path.endswith("/result"):
                return httpx.Response(200, json={
                    "job_handle": handle, "request_id": job["request_id"], "state": "succeeded",
                    "cause": "completed", "usage": None if "no_usage" in self.defects
                    else {"prompt_tokens": 9, "completion_tokens": 3},
                    "response": {"model": "other" if "result_model" in self.defects
                                 else job["model"], "object": "chat.completion",
                                 "choices": [{"message": {"content": "a van"}}]}})
            return httpx.Response(200, json={"job_handle": handle, "state": "succeeded",
                                             "request_id": job["request_id"]})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def infer(self, request, org: str, path: str) -> httpx.Response:
        body = json.loads(request.content)
        digest = hashlib.sha256(request.content).hexdigest()
        key = request.headers.get("idempotency-key")
        replayed = False
        if key is not None and (org, key) in self.idem:
            seen, handle = self.idem[(org, key)]
            if seen != digest and "conflict_accepted" not in self.defects:
                return httpx.Response(409, json={"error": {"code": "idempotency_conflict"}})
            replayed = True
        if not replayed or "replay_new_job" in self.defects:
            self.created += 1
            handle = f"job_{uuid.uuid4().hex}"
            self.jobs[handle] = {"org": org, "request_id": str(uuid.uuid4()),
                                 "model": body["model"]}
            if key is not None:
                self.idem[(org, key)] = (digest, handle)
        job = self.jobs[handle]
        self.requests.setdefault(job["request_id"], {"org": org, "key": request.headers.get(
            "authorization", "").removeprefix("Bearer ")})
        mode = next((k["mode"] for k in self.minted.values()
                     if k["secret"] == self.requests[job["request_id"]]["key"]), "off")
        if path != "/v1/jobs" and (mode == "full" or "uncaptured_traced" in self.defects):
            self.traces.setdefault(job["request_id"], {"org": org})
        if path == "/v1/jobs":
            return httpx.Response(202, json={"job_handle": handle,
                                             "request_id": job["request_id"], "state": "queued",
                                             "idempotency_replayed": replayed})
        if body.get("stream"):
            chunk = {"object": "chat.completion.chunk", "choices": [{"delta": {"content": "x"}}],
                     "model": "other" if "sse_wrong_model" in self.defects else job["model"]}
            text = f"data: {json.dumps(chunk)}\n\n" + \
                ("" if "sse_no_done" in self.defects else "data: [DONE]\n\n")
            kind = "application/json" if "sse_plain" in self.defects else "text/event-stream"
            return httpx.Response(200, text=text, headers={"content-type": kind})
        return httpx.Response(202 if "sync_async" in self.defects else 200, headers={
            "Inference-Id": "" if "no_inference_id" in self.defects else job["request_id"]},
            json={
            "object": "chat.completion", "model": job["model"],
            "choices": [{"message": {"content": "a van"}}],
            **({} if "sync_no_usage" in self.defects
               else {"usage": {"prompt_tokens": 9, "completion_tokens": 3}})})


def _ok(status: int = 200, body=None, **headers) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {}, headers=headers)


def _refused(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": code}})


def _body(request) -> dict:
    return json.loads(request.content or b"{}")


def _money(amount, unit: str = "CREDIT") -> dict:
    return {"amount": str(amount), "unit": unit}


def wave7(fake: FakeGateway, request, method: str, path: str) -> httpx.Response | None:
    """The wave-7 families (R270 envelopes, Idempotency-Key replays); None = not one of them."""
    web, key = fake._web(request), request.headers.get("idempotency-key")
    query = request.url.params
    if path == "/auth/v1/availability":
        fresh = {"state": "configured", "verified_at": "2026-10-02T00:00:00Z"}
        return _ok(body={"sign_in": fresh, "sign_up": fresh, "recovery": fresh,
                         "signup_grant": fresh, "captcha_required": False})
    if path.startswith("/put/"):                        # the presigned object store
        return _ok(200)
    if path.startswith(("/console/", "/lab/v1/workspaces", "/lab/v1/capabilities",
                        "/lab/v1/judge", "/lab/v1/artifacts", "/lab/v1/operations",
                        "/lab/v1/control/model-projects")) \
            or (path.startswith("/lab/v1/traces") and method != "GET"
                or path.endswith("/feedback")):
        if web is None:
            return _refused(401, "invalid_api_key")
        return console(fake, request, method, path, web, key, query) \
            or lab(fake, request, method, path, web, key, query) \
            or _refused(404, "not_found")
    if path.startswith("/lab/v1/traces"):
        return traces(fake, request, path, web, query)
    return None


def console(fake, request, method, path, web, key, query):
    d, (user, org) = fake.defects, web
    owned = {i: k for i, k in fake.minted.items() if k["org"] == org}
    if path == "/console/v1/me":
        granted = org in fake.granted and "grant_not_on_account" not in d
        return _ok(body={"actor": {"audience": "session", "user_id": user, "org_id": org},
                         "state": "ready" if granted else "onboarding", "suspended": False,
                         "signup_grant": {"state": "granted", "amount": _money(10000)}
                         if granted else {"state": "not_granted"}})
    if path == "/console/v1/capabilities":
        return _ok(body={"actions": {"operator_console": "consumer_operator" in d,
                                     "create_key": org in fake.granted},
                         "features": {"feedback": {"state": "disabled"}}})
    if path == "/console/v1/signup-grant/claim":
        again = org in fake.granted and "grant_twice" not in d
        fake.granted.add(org)
        return _ok(body={"status": "replayed" if again else "granted",
                         "credit": None if again else _money(10000)})
    if path == "/console/v1/keys" and method == "POST":
        seen = fake.key_idem.get((org, key))
        if seen is not None:
            row = fake.minted[seen]
            if "key_twice" in d:                    # a second live row behind a true replay
                fake.minted[str(uuid.uuid4())] = {**row, "secret": f"sk-infrx-{uuid.uuid4().hex}"}
            shown = str(uuid.uuid4()) if "replay_other_key" in d else seen
            return _ok(200, {"key": {"key_id": shown, "name": row["name"]}, "replayed": True,
                             "secret": row["secret"] if "secret_rerevealed" in d else None,
                             "secret_returned": "secret_rerevealed" in d})
        key_id, secret = str(uuid.uuid4()), f"sk-infrx-{uuid.uuid4().hex}"
        fake.minted[key_id] = {"org": org, "name": _body(request)["name"], "secret": secret,
                               "revoked": False, "mode": "off"}
        fake.keys[secret], fake.key_idem[(org, key)] = org, key_id
        return _ok(201, {"key": {"key_id": key_id, "name": _body(request)["name"]},
                         "secret": secret, "secret_returned": True, "replayed": False})
    if path == "/console/v1/keys":
        return _ok(body={"data": [{"id": i, "name": k["name"], "revoked_at": None}
                                  for i, k in owned.items() if not k["revoked"]],
                         "next_cursor": None})
    if path.startswith("/console/v1/keys/") and method == "DELETE":
        fake.minted[path.split("/")[4]]["revoked"] = True
        return _ok()
    if path.startswith("/console/v1/requests/"):
        rid = path.split("/")[4]
        row = fake.requests.get(rid)
        if row is None or (row["org"] != org and "console_foreign" not in d):
            return _refused(404, "not_found")
        return _ok(body={"request_id": rid, "requested_model": MODEL, "state": "succeeded",
                         "settlement_state": "settled", "charged": _money(CHARGE),
                         "hold_state": "held" if "hold_left" in d else "settled"})
    if path == "/console/v1/credit-ledger":
        ledger = [{"request_id": rid, "kind": "inference_debit", "amount": _money("-" + CHARGE)}
                  for rid, row in fake.requests.items() if row["org"] == org]
        return _ok(body={"data": ledger * (2 if "double_debit" in d else 1),
                         "next_cursor": None})
    if path == "/console/v1/credits":
        spent = int(CHARGE) * sum(1 for row in fake.requests.values() if row["org"] == org)
        spent += len(fake.runs) * ("judge_charges_consumer" in d)
        left = _money(10000 - spent)
        return _ok(body={"available": left, "ledger_total": left, "reserved_total": _money(0)})
    if path == "/console/v1/data-use" or path.endswith("/capture"):
        if method == "PUT":
            fake.minted[path.split("/")[4]]["mode"] = _body(request)["mode"]
            fake.consent += 1
        return _ok(body={"consent": {"version": fake.consent}, "grants": [], "keys": [
            {"key_id": i, "effective_mode": "full" if "capture_all_keys" in d else k["mode"]}
            for i, k in owned.items()]})
    if path == "/console/v1/data-grants" and method == "POST":
        body = _body(request)
        grant_id = str(uuid.uuid4())
        fake.grants[grant_id] = {"org": org, "version": body["grant_version"] + 1,
                                 "state": "active", "purposes": body["purposes"],
                                 "provider_org_id": body["provider_org_id"]}
        return _ok(201, {"grant_id": grant_id, **fake.grants[grant_id]})
    if path == "/console/v1/data-grants":
        return _ok(body={"data": [{"grant_id": i, **g} for i, g in fake.grants.items()
                                  if g["org"] == org and "grant_not_persisted" not in d],
                         "next_cursor": None})
    if path.startswith("/console/v1/data-grants/") and method == "DELETE":
        fake.grants[path.split("/")[4]]["state"] = "revoked"
        return _ok(body={"grant_id": path.split("/")[4], "state": "revoked"})
    return None


def _member(user: str, query) -> bool:
    return user == "u-admin" and query.get("provider_org_id") == PROVIDER


def _granted(fake, purpose: str) -> bool:
    return any(g["state"] == "active" and purpose in g["purposes"]
               for g in fake.grants.values())


def lab(fake, request, method, path, web, key, query):
    d, (user, _) = fake.defects, web
    if path == "/lab/v1/workspaces":
        mine = user == "u-admin" or (user == "u-outsider" and "outsider_workspace" in d)
        return _ok(body={"data": [{"provider_org_id": PROVIDER, "role": "administrator"}]
                         if mine else [], "next_cursor": None})
    if path == "/lab/v1/capabilities":
        return _ok(body={"capabilities": ["manage_members", "read_traces"]}) \
            if _member(user, query) else _refused(404, "not_found")
    if path.endswith("/members"):
        if user != "u-admin" and "outsider_members" not in d:
            return _refused(403, "forbidden")
        return _ok(body={"data": [{"role": "administrator"}], "next_cursor": None})
    if not _member(user, query):
        return _refused(403, "forbidden")
    objects, op_of = fake.objects, lambda i, state, res=None: {
        "operation_id": i, "kind": "artifact.verify", "state": state, "resource_id": res,
        "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z"}
    if path == "/lab/v1/control/model-projects":
        return _ok(201, {"project_id": objects.setdefault(("project", key), str(uuid.uuid4()))})
    if path == "/lab/v1/artifacts/uploads":
        upload = objects.setdefault(("upload", key), str(uuid.uuid4()))
        objects[upload] = _body(request)["files"]
        return _ok(201, {"upload_id": upload})
    if path.endswith("/parts"):
        return _ok(body={"url": f"http://s3.test/put/{path.split('/')[5]}/"
                                f"{_body(request)['relative_path']}", "method": "PUT"})
    if path.endswith("/complete"):
        upload = path.split("/")[5]
        op = objects.setdefault(("op", key if "op_replay_new" not in d else uuid.uuid4()),
                                str(uuid.uuid4()))
        objects[op] = objects.setdefault(("artifact", upload), str(uuid.uuid4()))
        objects[objects[op]] = objects[upload]
        return _ok(202, op_of(op, "queued"), Location=f"/lab/v1/operations/{op}")
    if path.startswith("/lab/v1/operations/"):
        op = path.split("/")[4]
        return _ok(body=op_of(op, "failed" if "op_fails" in d else "succeeded", objects[op]))
    if path.startswith("/lab/v1/artifacts/"):
        files = list(objects[path.split("/")[4]])
        files = files[1:] if "artifact_missing_file" in d else files
        from infrx.lab.artifacts.manifest import Manifest
        return _ok(body={"artifact_id": path.split("/")[4], "source": "upload",
                         "files": files, "compatibility": {"supported": True},
                         "manifest_sha256": Manifest.model_validate(
                             {"files": objects[path.split("/")[4]]}).digest})
    if path.endswith("/revisions"):
        revision = objects.setdefault(("revision", key if "revision_mutable" not in d
                                       else uuid.uuid4()), str(uuid.uuid4()))
        return _ok(201, {"serving_version_id": revision, "model_revision": "toy@1",
                         "artifact_id": _body(request)["artifact_id"],
                         "profile": {} if "revision_unpinned" in d else {"gpu": "L40S"}})
    return judge(fake, request, method, path, key, query)


def judge(fake, request, method, path, key, query):
    d, runs = fake.defects, fake.objects.setdefault("runs", {})
    if path == "/lab/v1/judge/models":
        return _ok(body={"data": [], "availability": {"state": "unavailable",
                                                      "reason": "no approved judge rate (P-10)"}})
    if path == "/lab/v1/judge/rubrics":
        return _ok(body={"data": [{"version": 1, "criteria": [{"name": "grounded"}],
                                   "output_schema": {"type": "object"}}], "next_cursor": None})
    if path == "/lab/v1/judge/configs":
        if not _granted(fake, "external_judging"):
            return _refused(403, "forbidden")
        return _ok(201, {"config_id": fake.objects.setdefault(("config", key), str(uuid.uuid4()))})
    if path == "/lab/v1/judge/estimates":
        return _ok(body={"authorizes_spend": False, "samples_max": 1, "priced": False})
    spent = _money("0.01" if "budget_spent" in d else "0", "PROVIDER_USD")
    budget = {"limit": _money("1.00", "PROVIDER_USD"), "reserved": spent, "settled": spent}
    if path.startswith("/lab/v1/judge/budgets/"):
        if not re.fullmatch(PAYER, path.split("/", 5)[5]):      # 0029's lab_ref_parts
            return _refused(403, "forbidden")
        fake.objects["payer"] = path.split("/", 5)[5]
        return _ok(body={"payer_ref": fake.objects["payer"], **budget})
    if path == "/lab/v1/judge/budgets":
        return _ok(body={"data": [{"payer_ref": fake.objects.get("payer"), **budget}]})
    if path == "/lab/v1/judge/runs" and method == "POST":
        if not _granted(fake, "external_judging") and "judge_after_revoke" not in d:
            return _refused(403, "forbidden")
        fresh = key not in runs or "run_replay_new" in d
        run = runs[key] = str(uuid.uuid4()) if fresh else runs[key]
        if fresh:
            fake.runs.append(run)
        return _ok(202, {"operation_id": run, "kind": "judge.run", "state": "queued",
                         "resource_id": run, "created_at": "2026-10-02T00:00:00Z",
                         "updated_at": "2026-10-02T00:00:00Z"},
                   Location=f"/lab/v1/judge/runs/{run}")
    if path.endswith("/results"):
        return _ok(body={"data": [{"state": "scored"}] if "dry_run_scored" in d else [],
                         "next_cursor": None})
    if path.startswith("/lab/v1/judge/runs/"):
        sent = "dry_run_sends" in d
        return _ok(body={"domain_state": "queued", "sent": int(sent),
                         "settled": _money("0.01", "PROVIDER_USD") if sent else None})
    if path == "/lab/v1/judge/calibration":
        calibrated = "calibrated_on_one" in d
        return _ok(body={"state": "calibrated" if calibrated else "insufficient",
                         "labels": 1, "required": 1 if calibrated else 20})
    if path.endswith("/reviews"):
        review = fake.objects.setdefault(("review", key if "review_twice" not in d
                                          else uuid.uuid4()), str(uuid.uuid4()))
        fake.objects.setdefault("reviews", {})[review] = path.split("/")[4]
        return _ok(201, {"review_id": review, "verdict": "pass", "provenance":
                         "judge" if "review_not_human" in d else "human"})
    if path.endswith("/feedback"):
        rid = path.split("/")[4]
        return _ok(body={"signals": [], "reviews": [
            {"review_id": i, "request_id": r, "provenance": "human"}
            for i, r in fake.objects.get("reviews", {}).items() if r == rid]})
    return None


def traces(fake, request, path, web, query) -> httpx.Response:
    d = fake.defects
    if web is None:
        return _refused(401, "unauthenticated")
    if not _member(web[0], query):
        return _refused(403, "forbidden")
    shared = _granted(fake, "provider_sharing") or "content_after_revoke" in d
    revoked = any(g["state"] == "revoked" for g in fake.grants.values())

    def item(rid: str) -> dict:
        row = {"request_id": rid, "model_id": "other" if "trace_wrong_pins" in d else MODEL_UUID,
               "serving_version_id": "d0000003-0000-4000-8000-000000000003",
               "price_version": "rc_marlin2b", "mode": "full",
               "elapsed_ms": 0 if "zero_elapsed" in d else 41.5,
               "access": "content" if shared else "metadata",
               "access_state": "content" if shared else ("revoked" if revoked else "metadata")}
        if shared and "trace_no_content" not in d:
            row["content"] = json.dumps({"messages": [{"content": "Describe the scene in one "
                                                                  "sentence."}]})
        return row
    if path == "/lab/v1/traces":
        return _ok(body={"data": [item(r) for r in fake.traces], "next_cursor": None})
    rid = path.split("/")[4]
    return _ok(body=item(rid)) if rid in fake.traces else _refused(404, "not_found")


@pytest.fixture(autouse=True)
def _no_fsync(monkeypatch):
    """Layer 1 proves the runner's decisions; fsync's durability is not observable here and
    costs ~20 ms per state save on this disk (~170 saves a full run)."""
    monkeypatch.setattr(os, "fsync", lambda fd: None)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


def private(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def fast_tmp(tmp_path):
    """tmp_path, on tmpfs when the host has one: a full run saves its 0600 state ~90 times
    and an atomic rename on a disk filesystem costs ~10 ms (4x the whole run)."""
    shm = Path("/dev/shm")
    if not shm.is_dir():
        yield tmp_path
        return
    path = Path(tempfile.mkdtemp(prefix="infrx-ap11-l1-", dir=shm))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def files(fast_tmp):
    """(config, secrets, state, out) for an isolated run against the fake; each a path."""
    tmp_path = fast_tmp
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "config.json").write_text('{"architectures": ["Toy"]}')
    (artifact / "model.safetensors").write_bytes(b"\x08\x00" * 32)
    config = {"target": "fake-gateway", "model": MODEL,
              "origins": {"gateway": "http://gateway.test", "lab": "http://lab.test"},
              "identities": {
                  "consumer_a": {"audience": "consumer", "secret": "consumer_a_key"},
                  "consumer_b": {"audience": "consumer", "secret": "consumer_b_key"},
                  "provider_admin": {"audience": "session", "secret": "admin_session",
                                     "provider_org_id": PROVIDER},
                  "outsider": {"audience": "session", "secret": "outsider_session",
                               "provider_org_id": PROVIDER}},
              "fixtures": {"admin_session": "layer-1 fake", "outsider_session": "layer-1 fake",
                           "alpha_session": "layer-1 fake", "beta_session": "layer-1 fake",
                           "listing": "layer-1 fake"},
              "media": {"clip": str(clip), "mime": "video/mp4"}, "poll_timeout_s": 2,
              "trace_timeout_s": 1, "poll_interval_s": 0.01, "composed": COMPOSED,
              "traces": True, "judge": "dry_run", "model_uuid": MODEL_UUID,
              "artifact": {"dir": str(artifact)}}
    config["identities"].update({
        "consumer_a_web": {"audience": "session", "secret": "alpha_session"},
        "consumer_b_web": {"audience": "session", "secret": "beta_session"},
        "consumer_a_capture": {"audience": "consumer", "secret": "consumer_a_capture_key"}})
    secrets = {"admin_session": "eyJhbGci.eyJzdWIi.adminSIG",
               "outsider_session": "eyJhbGci.eyJzdWIi.outsideSIG",
               "alpha_session": "eyJhbGci.eyJzdWIi.alphaWEB",
               "beta_session": "eyJhbGci.eyJzdWIi.betaWEB"}
    return (private(tmp_path / "config.json", config), private(tmp_path / "secrets.json", secrets),
            tmp_path / "run" / "state.json", tmp_path / "out")
