"""AP-11's layer-1 fixtures: a contract fake of the routes mounted on the base, served through
`httpx.MockTransport`, so the runner's own decisions (state, resume, modes, exit codes,
redaction, BLOCKED semantics) are proved without a stack. The isolated run on the ap11 stack
(`world.py`) is the real-service proof; this fake never stands in for it."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # `api_lifecycle` as a package

MODEL = "nemostation/marlin-2b"
KEYS = {"sk-infrx-alphaSECRET0001": "org-a", "sk-infrx-betaSECRET00002": "org-b"}
SESSIONS = {"eyJhbGci.eyJzdWIi.adminSIG": ("admin", True), "eyJhbGci.eyJzdWIi.outsideSIG": ("x", False)}
PROVIDER = "b0000001-0000-4000-8000-000000000001"


class FakeGateway:
    """The base's consumer routes and the Lab session read, as their wire contracts state
    them. `lose_ack` names "METHOD path" whose NEXT call commits and then times out (a lost
    acknowledgement: the server did the work, the client never heard)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []
        self.jobs: dict[str, dict] = {}
        self.idem: dict[tuple[str, str], tuple[str, str]] = {}
        self.lose_ack: set[str] = set()
        self.models = [MODEL]
        self.created = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def posts(self, path: str) -> list[str | None]:
        return [key for method, p, key in self.sent if method == "POST" and p == path]

    def _org(self, request) -> str | None:
        return KEYS.get(request.headers.get("authorization", "").removeprefix("Bearer "))

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.sent.append((method, path, request.headers.get("idempotency-key")))
        answer = self.route(request, method, path)
        if f"{method} {path}" in self.lose_ack:
            self.lose_ack.discard(f"{method} {path}")
            raise httpx.ReadTimeout("the acknowledgement was lost", request=request)
        return answer

    def route(self, request, method: str, path: str) -> httpx.Response:
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "nemostation"} for m in self.models]})
        if path == "/lab/v1/control/models":
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in SESSIONS:
                return httpx.Response(401, json={"refusal": "unauthenticated"})
            member = SESSIONS[token][1] and request.url.params.get("provider_org_id") == PROVIDER
            return httpx.Response(200 if member else 403,
                                  json={"models": []} if member else {"refusal": "not_a_member"})
        org = self._org(request)
        if org is None:
            return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})
        if method == "POST" and path == "/v1/uploads":
            handle = f"up_{uuid.uuid4().hex}"
            return httpx.Response(201, json={"upload_handle": handle,
                                             "destination_ref": f"infrx-upload:{handle}"})
        if method == "PUT" and path.startswith("/v1/uploads/"):
            return httpx.Response(204)
        if method == "POST" and path.endswith("/complete"):
            return httpx.Response(200, json={"upload_handle": path.split("/")[3],
                                             "state": "finalized", "media": {"kind": "video"}})
        if method == "POST" and path in ("/v1/jobs", "/v1/chat/completions"):
            return self.infer(request, org, path)
        if method == "GET" and path.startswith("/v1/jobs/"):
            handle = path.split("/")[3]
            job = self.jobs.get(handle)
            if job is None or job["org"] != org:
                return httpx.Response(404, json={"error": {"code": "not_found"}})
            if path.endswith("/result"):
                return httpx.Response(200, json={
                    "job_handle": handle, "request_id": job["request_id"], "state": "succeeded",
                    "cause": "completed", "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                    "response": {"model": job["model"], "object": "chat.completion",
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
            if seen != digest:
                return httpx.Response(409, json={"error": {"code": "idempotency_conflict"}})
            replayed = True
        else:
            self.created += 1
            handle = f"job_{uuid.uuid4().hex}"
            self.jobs[handle] = {"org": org, "request_id": str(uuid.uuid4()),
                                 "model": body["model"]}
            if key is not None:
                self.idem[(org, key)] = (digest, handle)
        job = self.jobs[handle]
        if path == "/v1/jobs":
            return httpx.Response(202, json={"job_handle": handle,
                                             "request_id": job["request_id"], "state": "queued",
                                             "idempotency_replayed": replayed})
        if body.get("stream"):
            chunk = {"object": "chat.completion.chunk", "model": job["model"],
                     "choices": [{"delta": {"content": "a van"}}]}
            text = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"object": "chat.completion", "model": job["model"],
                                         "choices": [{"message": {"content": "a van"}}],
                                         "usage": {"prompt_tokens": 9, "completion_tokens": 3}})


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


def private(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def files(tmp_path):
    """(config, secrets, state, out) for an isolated run against the fake; each a path."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    config = {"target": "fake-gateway", "model": MODEL,
              "origins": {"gateway": "http://gateway.test", "lab": "http://lab.test"},
              "identities": {
                  "consumer_a": {"audience": "consumer", "secret": "consumer_a_key"},
                  "consumer_b": {"audience": "consumer", "secret": "consumer_b_key"},
                  "provider_admin": {"audience": "session", "secret": "admin_session",
                                     "provider_org_id": PROVIDER},
                  "outsider": {"audience": "session", "secret": "outsider_session",
                               "provider_org_id": PROVIDER}},
              "fixtures": {"consumer_a_key": "layer-1 fake", "consumer_b_key": "layer-1 fake",
                           "admin_session": "layer-1 fake", "outsider_session": "layer-1 fake",
                           "listing": "layer-1 fake"},
              "media": {"clip": str(clip), "mime": "video/mp4"}, "poll_timeout_s": 2}
    secrets = {"consumer_a_key": "sk-infrx-alphaSECRET0001",
               "consumer_b_key": "sk-infrx-betaSECRET00002",
               "admin_session": "eyJhbGci.eyJzdWIi.adminSIG",
               "outsider_session": "eyJhbGci.eyJzdWIi.outsideSIG"}
    return (private(tmp_path / "config.json", config), private(tmp_path / "secrets.json", secrets),
            tmp_path / "run" / "state.json", tmp_path / "out")
