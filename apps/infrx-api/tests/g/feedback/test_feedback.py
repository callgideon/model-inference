#!/usr/bin/env python3
"""G4F: `POST /v1/feedback` over D6F's `FeedbackService` - FEEDBACK-ACK, the route half.

    uv run --frozen pytest -q tests/g/feedback/test_feedback.py

The route cases run over a recording stand-in, so what the route hands the service
(author, org, signal, idempotency scope) and what it refuses before any service call are
observable. `test_feedback_pg.py` composes the same route over the real `PgFeedbackService`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI

from infrx.contracts import errors, wire
from infrx.contracts.records import AuthorRole, Feedback, FeedbackChannel
from infrx.gateway.routes import feedback

from .. import support

ORG = support.ORG
TOKEN = support.TOKEN
REQUEST = "4d4d4d4d-0000-4000-8000-000000000004"
PATH = feedback.FEEDBACK_PATH
SIGNAL = {"request_id": REQUEST, "name": "rating", "value": 4, "comment": "close"}
ACCEPTED_KEYS = set(wire.FeedbackAccepted.model_fields)


def identities(rows):
    """PostgREST by key hash: each token is its own row, an unknown one is no row."""
    by_hash = {hashlib.sha256(token.encode()).hexdigest(): row for token, row in rows.items()}

    def handler(request):
        key_hash = request.url.params.get("key_hash", "").removeprefix("eq.")
        return httpx.Response(200, json=[by_hash[key_hash]] if key_hash in by_hash else [])

    return httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(handler))


class Recording:
    """`FeedbackService.accept` that records its call and answers a stored row (or raises
    `fail`). `by_operator` and the principal are on the row so a leak would show."""

    def __init__(self, fail: errors.DomainError | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail = fail

    async def accept(self, auth, request_id, signal, idem):
        self.calls.append((auth, request_id, signal, idem))
        if self.fail is not None:
            raise self.fail
        return Feedback(feedback_id="fb_" + "Q" * 43, request_id=request_id, org_id=auth.org_id,
                        author_principal=auth.principal, author_role=AuthorRole.customer,
                        channel=FeedbackChannel.api, name=signal["name"], value=signal["value"],
                        comment=signal.get("comment"), by_operator=True,
                        created_at="2026-09-27T00:00:00Z")


def mounted(service=None, *, on_runtime=False, rows=None):
    rt = support.runtime(support.settings(), sb=identities(rows or {TOKEN: support.ROW}))
    app = FastAPI()
    if on_runtime:
        rt.feedback = service
        mounted_service = feedback.register(app, rt)
    else:
        mounted_service = feedback.register(app, rt, service=service)
    return app, mounted_service


def post(app, body=SIGNAL, *, token=TOKEN, key="fb-key-1", raw=None, headers=None):
    head = {"content-type": "application/json", **(headers or {})}
    if token:
        head["authorization"] = f"Bearer {token}"
    if key is not None:
        head[wire.HEADER_IDEMPOTENCY_KEY] = key
    content = raw if raw is not None else json.dumps(body).encode()

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gateway.test") as client:
            return await client.post(PATH, content=content, headers=head)

    return asyncio.run(main())


def code(response) -> str | None:
    return (response.json().get("error") or {}).get("code")


# ------------------------------------------------------------------ enablement ---
def test_feedback_ack__no_service_mounts_no_feedback_route():
    """Off by default: without a service nothing is mounted, and the standalone router
    still answers an unknown path in the contract envelope."""
    app, service = mounted(None)
    assert service is None
    answer = post(app)
    assert answer.status_code == 404 and code(answer) == "not_found"


def test_feedback_ack__the_route_mounts_over_the_runtime_service():
    stub = Recording()
    app, service = mounted(stub, on_runtime=True)
    assert service is stub
    assert post(app).status_code == 201 and len(stub.calls) == 1


# ----------------------------------------------------------- provenance, author ---
def test_feedback_ack__the_author_and_org_are_the_keys_never_the_body():
    stub = Recording()
    app, _ = mounted(stub)
    answer = post(app, key="fb-author")
    assert answer.status_code == 201, answer.text
    auth, request_id, signal, idem = stub.calls[0]
    assert (auth.org_id, auth.principal) == (ORG, support.KEY)
    assert request_id == REQUEST
    assert signal == {"name": "rating", "value": 4, "comment": "close"}
    assert (idem.org_id, idem.operation, idem.key) == (ORG, "feedback", "fb-author")
    assert answer.headers[wire.HEADER_INFERENCE_ID] != REQUEST     # this call's own id


@pytest.mark.parametrize("field", ["author_role", "author_principal", "channel", "org_id",
                                   "calibration_set", "by_operator", "rubric_version"])
def test_feedback_ack__a_spoofed_provenance_field_is_refused_before_the_service(field):
    stub = Recording()
    app, _ = mounted(stub)
    answer = post(app, {**SIGNAL, field: "operator"})
    assert answer.status_code == 400 and code(answer) == "invalid_request"
    assert stub.calls == []


@pytest.mark.parametrize("body", [
    {}, {"request_id": REQUEST}, {**SIGNAL, "name": "calibration_label", "value": "correct"},
    {**SIGNAL, "name": "thumb", "value": 1}, {**SIGNAL, "value": 6}, {**SIGNAL, "value": 4.0},
    {**SIGNAL, "request_id": "not-a-uuid"}, {k: v for k, v in SIGNAL.items() if k != "request_id"},
    {**SIGNAL, "comment": "x" * 4001}], ids=[
    "empty", "no_signal", "calibration_label", "thumb_int", "rating_6", "rating_float",
    "bad_request_id", "no_request_id", "long_comment"])
def test_feedback_ack__an_invalid_signal_is_refused_before_the_service(body):
    stub = Recording()
    app, _ = mounted(stub)
    answer = post(app, body)
    assert answer.status_code == 400 and code(answer) == "invalid_request"
    assert stub.calls == []


def test_feedback_ack__a_non_object_body_is_refused_before_the_service():
    stub = Recording()
    app, _ = mounted(stub)
    for raw in (b"[]", b"4", b"{", b""):
        answer = post(app, raw=raw)
        assert answer.status_code == 400 and code(answer) == "invalid_request", raw
    answer = post(app, headers={"content-type": "text/plain"})
    assert answer.status_code == 400 and answer.json()["error"]["param"] == "Content-Type"
    assert stub.calls == []


# ---------------------------------------------------------------- idempotency ---
@pytest.mark.parametrize("key", [None, "", "k" * 256])
def test_feedback_ack__the_idempotency_key_is_required_and_bounded(key):
    stub = Recording()
    app, _ = mounted(stub)
    answer = post(app, key=key)
    assert answer.status_code == 400 and code(answer) == "invalid_request"
    assert answer.json()["error"]["param"] == "Idempotency-Key"
    assert stub.calls == []


def test_feedback_ack__the_payload_digest_is_the_canonical_signal():
    """Same signal in another spelling -> the same digest (a retry replays); another value
    or another request -> another digest (the store answers idempotency_conflict)."""
    stub = Recording()
    app, _ = mounted(stub)
    reordered = json.dumps(dict(reversed(SIGNAL.items())), indent=3).encode()
    for sent in (dict(raw=reordered), {}, dict(body={**SIGNAL, "value": 5}),
                 dict(body={**SIGNAL, "request_id": "5e5e5e5e-0000-4000-8000-000000000005"})):
        assert post(app, **sent).status_code == 201
    digests = [idem.payload_hash for *_, idem in stub.calls]
    assert digests[0] == digests[1]
    assert len({digests[1], digests[2], digests[3]}) == 3
    assert all(d.startswith("sha256:") and len(d) == 71 for d in digests)


# ------------------------------------------------------------ identity, bounds ---
def test_feedback_ack__identity_comes_before_the_body():
    stub = Recording()
    app, _ = mounted(stub)
    for token in (None, "sk-unknown"):
        answer = post(app, raw=b"not json", token=token)
        assert answer.status_code == 401 and code(answer) == "invalid_api_key"
    assert stub.calls == []


def test_feedback_ack__the_body_is_bounded():
    stub = Recording()
    app, _ = mounted(stub)
    answer = post(app, raw=b'{"pad": "' + b"x" * feedback.MAX_FEEDBACK_BYTES + b'"}')
    assert answer.status_code == 413 and code(answer) == "request_too_large"
    # The largest legal signal fits: 4000-character value and comment, each character an
    # astral one that json.dumps escapes to a 12-byte surrogate pair.
    wide = "\U0001F600" * 4000
    assert post(app, {**SIGNAL, "name": "correction", "value": wide,
                      "comment": wide}).status_code == 201
    assert len(stub.calls) == 1


# --------------------------------------------------------- the acknowledgment ---
@pytest.mark.parametrize("failure,status", [
    (errors.DependencyUnavailable("feedback is not enabled"), 503),
    (errors.NotFound("no request owned by org"), 404),
    (errors.IdempotencyConflict("same key, different payload"), 409),
    (errors.OrgSuspended("suspended"), 403)], ids=["disabled", "foreign", "conflict", "suspended"])
def test_feedback_ack__no_ack_before_the_service_accepted(failure, status):
    stub = Recording(fail=failure)
    app, _ = mounted(stub)
    answer = post(app)
    assert answer.status_code == status and code(answer) == failure.code
    assert "feedback_id" not in answer.text


def test_feedback_ack__only_the_frozen_acknowledgment_leaves():
    app, _ = mounted(Recording())
    answer = post(app)
    body = answer.json()
    assert answer.status_code == 201 and set(body) == ACCEPTED_KEYS
    assert body == {"feedback_id": "fb_" + "Q" * 43, "request_id": REQUEST, "channel": "api",
                    "author_role": "customer", "created_at": "2026-09-27T00:00:00Z",
                    "replayed": False}
    assert support.KEY not in answer.text and "by_operator" not in answer.text
