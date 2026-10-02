"""AP-03: the console and operator mutation routes (R270) over a recording repository.

    uv run --frozen pytest -q tests/ap03/test_routes.py

What only the route decides is observable here: mounting, the actor from `rt.actors` before
the body, the Idempotency-Key header, status codes, no-store, the R270 envelope for every
failure, the operator refusal before the repository and the feedback switch.
`test_actions_pg.py` composes the same routes over the real repository.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, UTC
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from infrx.console import actions as acts
from infrx.contracts import api, errors
from infrx.contracts.records import AuthorRole, Feedback, FeedbackChannel, Role
from infrx.gateway import control
from infrx.gateway.routes import console_actions, operator_actions

USER = "2b2b2b2b-0000-4000-8000-000000000002"
ORG = "1a1a1a1a-0000-4000-8000-000000000001"
KEY = "3c3c3c3c-0000-4000-8000-000000000003"
REQUEST = "4d4d4d4d-0000-4000-8000-000000000004"
CONSUMER = api.Actor(audience="session", user_id=USER, org_id=ORG)
OPERATOR = CONSUMER.model_copy(update={"operator": True})
SUMMARY = acts.ApiKey(key_id=KEY, name="laptop", prefix="sk-infrx-abcdefgh",
                      created_at="2026-10-01T00:00:00+00:00", revoked_at=None)
IDEM = {"Idempotency-Key": "k-1"}


class Recording:
    """The repository: records each call, answers canned models (or raises `fail`)."""

    def __init__(self, fail: Exception | None = None, replayed: bool = False) -> None:
        self.calls: list[tuple] = []
        self.fail, self.replayed = fail, replayed

    def _answer(self, name, *args):
        self.calls.append((name, *args))
        if self.fail is not None:
            raise self.fail

    async def create_key(self, actor, name, idem):
        self._answer("create_key", actor, name, idem)
        return acts.KeyCreated(key=SUMMARY, secret=None if self.replayed else "sk-infrx-s",
                               secret_returned=not self.replayed, replayed=self.replayed)

    async def revoke_key(self, actor, key_id, idem):
        self._answer("revoke_key", actor, key_id, idem)
        return SUMMARY.model_copy(update={"revoked_at": "2026-10-01T00:00:01+00:00"})

    async def claim_grant(self, actor):
        self._answer("claim_grant", actor)
        return acts.GrantClaim(status="granted", credit=api.Money(amount="10000.00000000",
                                                                  unit="CREDIT"),
                               granted_at="2026-10-01T00:00:00+00:00")

    async def adjust_credit(self, actor, user_id, amount, reason, idem):
        self._answer("adjust_credit", actor, user_id, amount, reason, idem)
        return acts.CreditAdjusted(replayed=self.replayed,
                                   amount=api.Money(amount="2.50000000", unit="CREDIT"))

    async def set_suspension(self, actor, org_id, suspended, reason, idem):
        self._answer("set_suspension", actor, org_id, suspended, reason, idem)
        return acts.SuspensionSet(org_id=org_id, suspended=suspended, replayed=self.replayed)

    async def revoke_key_as_operator(self, actor, key_id, reason, idem):
        self._answer("revoke_key_as_operator", actor, key_id, reason, idem)
        return acts.KeyRevoked(key_id=key_id, replayed=self.replayed)


class Feedbacks:
    """`FeedbackService.accept`: records what the route handed it."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def accept(self, auth, request_id, signal, idem):
        self.calls.append((auth, request_id, signal, idem))
        return Feedback(feedback_id="fb_" + "Q" * 43, request_id=request_id, org_id=auth.org_id,
                        author_principal=auth.principal, author_role=AuthorRole.customer,
                        channel=FeedbackChannel.console, name=signal["name"],
                        value=signal["value"], comment=signal.get("comment"),
                        created_at=datetime(2026, 10, 1, tzinfo=UTC))


def app_for(repo=None, actor: api.Actor | None = CONSUMER, *, feedback=None,
            mount=True, error=None):
    app = FastAPI()
    rt = SimpleNamespace(actors=control.StaticActors(actor, error=error),
                         console_actions=repo if mount else None, feedback=feedback)
    console_actions.register(app, rt)
    operator_actions.register(app, rt)
    return app


def call(app, method, path, *, json=None, headers=None, content=None):
    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://api.test") as client:
            return await client.request(method, path, json=json, content=content,
                                        headers=headers)
    return asyncio.run(main())


def envelope(response, status, code):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] == code, body
    return body["error"]


# ------------------------------------------------------------------------- mounting
def test_routes__nothing_is_mounted_without_the_repository():
    """Default OFF: a composition that built no repository serves none of AP-03's paths."""
    app = app_for(mount=False)
    assert call(app, "POST", "/console/v1/keys", json={"name": "x"}, headers=IDEM) \
        .status_code == 404
    assert call(app, "POST", "/operator/v1/suspensions", json={}, headers=IDEM) \
        .status_code == 404


# ------------------------------------------------------------------------- 03a keys
def test_routes__create_returns_201_once_no_store_with_the_secret_and_200_on_replay():
    """Oracle: the one-time secret is cacheable, or a replay is indistinguishable from a
    first creation."""
    repo = Recording()
    r = call(app_for(repo), "POST", "/console/v1/keys", json={"name": "laptop"}, headers=IDEM)
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    assert r.json()["secret"] == "sk-infrx-s" and r.json()["secret_returned"] is True
    assert repo.calls == [("create_key", CONSUMER, "laptop", "k-1")]
    again = call(app_for(Recording(replayed=True)), "POST", "/console/v1/keys",
                 json={"name": "laptop"}, headers=IDEM)
    assert again.status_code == 200 and again.json()["secret"] is None
    assert again.headers["cache-control"] == "no-store"


def test_routes__create_requires_an_idempotency_key():
    """Oracle: a create without the durable identity reached the repository."""
    repo = Recording()
    envelope(call(app_for(repo), "POST", "/console/v1/keys", json={"name": "x"}), 422,
             errors.InvalidRequest.code)
    assert repo.calls == []


def test_routes__identity_comes_before_the_body():
    """No session: 401 even for an invalid body (an authority field, no name), and the
    repository is never asked. Oracle: the body was validated (422) before the actor was
    established. (Bytes that are not JSON at all are FastAPI's 422 before any dependency.)"""
    repo = Recording()
    r = call(app_for(repo, actor=None), "POST", "/console/v1/keys", json={"user_id": USER},
             headers=IDEM)
    envelope(r, 401, errors.InvalidApiKey.code)
    assert repo.calls == []


def test_routes__a_body_naming_authority_is_refused_in_the_envelope():
    """A user id, org or role in the body is never authority: 422 in the R270 envelope with
    the caller's request id. Oracle: FastAPI's own `{detail}` body, or the field ignored."""
    repo = Recording()
    r = call(app_for(repo), "POST", "/console/v1/keys",
             json={"name": "x", "user_id": USER}, headers={**IDEM, "X-Request-Id": "rid-9"})
    assert envelope(r, 422, errors.InvalidRequest.code)["request_id"] == "rid-9"
    assert repo.calls == []


def test_routes__a_repository_refusal_is_its_status_in_the_envelope():
    """Oracle: a domain refusal rendered as a 500, or without no-store."""
    r = call(app_for(Recording(fail=errors.IdempotencyConflict("used"))), "POST",
             "/console/v1/keys", json={"name": "x"}, headers=IDEM)
    envelope(r, 409, errors.IdempotencyConflict.code)
    bug = call(app_for(Recording(fail=RuntimeError("dsn=postgres://secret"))), "POST",
               "/console/v1/keys", json={"name": "x"}, headers=IDEM)
    assert "secret" not in envelope(bug, 500, "internal")["message"]


def test_routes__revoke_passes_the_path_key_and_an_optional_idempotency_key():
    repo = Recording()
    r = call(app_for(repo), "DELETE", f"/console/v1/keys/{KEY}")
    assert r.status_code == 200 and r.json()["revoked_at"] is not None
    assert r.headers["cache-control"] == "no-store"
    call(app_for(repo), "DELETE", f"/console/v1/keys/{KEY}", headers=IDEM)
    assert repo.calls == [("revoke_key", CONSUMER, KEY, None), ("revoke_key", CONSUMER, KEY,
                                                                  "k-1")]


# ------------------------------------------------------------------------- 03b grant
def test_routes__the_claim_is_the_actors_and_takes_no_body_authority():
    """Oracle: a body naming a user reached the repository (the App's admin client took
    any id it was handed; the API takes only the session's)."""
    repo = Recording()
    r = call(app_for(repo), "POST", "/console/v1/signup-grant/claim")
    assert r.status_code == 200 and r.json()["credit"] == {"amount": "10000.00000000",
                                                           "unit": "CREDIT"}
    assert repo.calls == [("claim_grant", CONSUMER)]
    envelope(call(app_for(repo), "POST", "/console/v1/signup-grant/claim",
                  json={"user_id": USER}), 422, errors.InvalidRequest.code)
    assert len(repo.calls) == 1


# ------------------------------------------------------------------------- 03c feedback
FEEDBACK = f"/console/v1/requests/{REQUEST}/feedback"


def test_routes__feedback_is_503_when_the_feature_is_off():
    """Disabled is explicit: 503 `unavailable`, retryable. Oracle: a 404, or a 201 that
    stored nothing."""
    body = envelope(call(app_for(Recording()), "POST", FEEDBACK,
                         json={"name": "rating", "value": 4}, headers=IDEM), 503,
                    errors.DependencyUnavailable.code)
    assert body["retryable"] is True


def test_routes__feedback_is_authored_by_the_actor_on_its_own_account():
    """The service receives the session's org and individual and a scoped key with a digest
    of the canonical signal. Oracle: provenance from the body, another org's scope, or a
    digest that changes with spelling."""
    service = Feedbacks()
    r = call(app_for(Recording(), feedback=service), "POST", FEEDBACK,
             json={"name": "rating", "value": 4, "comment": "ok"}, headers=IDEM)
    assert r.status_code == 201 and r.json()["request_id"] == REQUEST
    assert r.headers["cache-control"] == "no-store"
    [(auth, request_id, signal, idem)] = service.calls
    assert (auth.org_id, auth.principal, auth.role) == (ORG, USER, Role.owner)
    assert (request_id, signal) == (REQUEST, {"name": "rating", "value": 4, "comment": "ok"})
    assert (idem.org_id, idem.operation, idem.key) == (ORG, "feedback.submit", "k-1")
    spelled = Feedbacks()
    call(app_for(Recording(), feedback=spelled), "POST", FEEDBACK,
         content=b'{ "comment":"ok",  "value":4, "name":"rating"}',
         headers={**IDEM, "content-type": "application/json"})
    assert spelled.calls[0][3].payload_hash == idem.payload_hash
    changed = Feedbacks()
    call(app_for(Recording(), feedback=changed), "POST", FEEDBACK,
         json={"name": "rating", "value": 5, "comment": "ok"}, headers=IDEM)
    assert changed.calls[0][3].payload_hash != idem.payload_hash


def test_routes__feedback_refuses_provenance_and_a_missing_key():
    service = Feedbacks()
    app = app_for(Recording(), feedback=service)
    envelope(call(app, "POST", FEEDBACK, json={"name": "rating", "value": 4,
                                                "channel": "api"}, headers=IDEM), 422,
             errors.InvalidRequest.code)
    envelope(call(app, "POST", FEEDBACK, json={"name": "rating", "value": 4}), 422,
             errors.InvalidRequest.code)
    assert service.calls == []


# ------------------------------------------------------------------------- 03d operator
ADJUST = {"user_id": USER, "amount": "2.5", "reason": "ticket 7"}


def test_routes__operator_mutations_refuse_a_non_operator_before_the_repository():
    """Oracle: a consumer session reached an operator write (the repository and SQL refuse
    too; the route must not depend on them)."""
    repo = Recording()
    app = app_for(repo, actor=CONSUMER)
    envelope(call(app, "POST", "/operator/v1/credit-adjustments", json=ADJUST, headers=IDEM),
             403, errors.Forbidden.code)
    envelope(call(app, "POST", "/operator/v1/suspensions",
                  json={"org_id": ORG, "suspended": True, "reason": "r"}, headers=IDEM),
             403, errors.Forbidden.code)
    envelope(call(app, "POST", "/operator/v1/key-revocations",
                  json={"key_id": KEY, "reason": "r"}, headers=IDEM), 403,
             errors.Forbidden.code)
    assert repo.calls == []


def test_routes__operator_mutations_need_a_reason_and_an_idempotency_key():
    repo = Recording()
    app = app_for(repo, actor=OPERATOR)
    envelope(call(app, "POST", "/operator/v1/credit-adjustments", json=ADJUST), 422,
             errors.InvalidRequest.code)
    envelope(call(app, "POST", "/operator/v1/credit-adjustments",
                  json={**ADJUST, "reason": ""}, headers=IDEM), 422,
             errors.InvalidRequest.code)
    envelope(call(app, "POST", "/operator/v1/credit-adjustments",
                  json={**ADJUST, "amount": "1e3"}, headers=IDEM), 422,
             errors.InvalidRequest.code)
    assert repo.calls == []


def test_routes__operator_mutations_reach_the_repository_with_the_actor():
    repo = Recording()
    app = app_for(repo, actor=OPERATOR)
    r = call(app, "POST", "/operator/v1/credit-adjustments", json=ADJUST, headers=IDEM)
    assert r.status_code == 201 and r.json()["amount"]["unit"] == "CREDIT"
    s = call(app, "POST", "/operator/v1/suspensions",
             json={"org_id": ORG, "suspended": False, "reason": "r"}, headers=IDEM)
    k = call(app, "POST", "/operator/v1/key-revocations", json={"key_id": KEY, "reason": "r"},
             headers=IDEM)
    assert (s.status_code, k.status_code) == (200, 200)
    assert all(x.headers["cache-control"] == "no-store" for x in (r, s, k))
    assert repo.calls == [("adjust_credit", OPERATOR, USER, "2.5", "ticket 7", "k-1"),
                          ("set_suspension", OPERATOR, ORG, False, "r", "k-1"),
                          ("revoke_key_as_operator", OPERATOR, KEY, "r", "k-1")]
    replay = call(app_for(Recording(replayed=True), actor=OPERATOR), "POST",
                  "/operator/v1/credit-adjustments", json=ADJUST, headers=IDEM)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
