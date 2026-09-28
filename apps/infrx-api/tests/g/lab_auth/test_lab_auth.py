#!/usr/bin/env python3
"""LAB-API auth seam (`infrx.gateway.lab_auth`): the Lab's forwarded Supabase session, then
the actor from `LabAccess` membership - LAB-ACCESS, the route half.

    uv run --frozen pytest -q tests/g/lab_auth
    INFRX_D_TASK=l4 uv run --frozen pytest -q tests/g/lab_auth      # + the PostgreSQL half

Cases taking `world` run on the fake store and, marked `pg`, on `PgAccessStore` (2+2+1:
providers A and B, developers of each, A's viewer, BOTH in both products, CONSUMER_ONLY).
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from infrx.contracts import errors
from infrx.contracts.v2.records import ProviderCapability as Cap
from infrx.gateway import lab_auth

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.c2lnbmF0dXJl"
API_KEY = "sk-infrx-g1-test"
USER = "2b2b2b2b-0000-4000-8000-000000000002"


def run(coro):
    return asyncio.run(coro)


class Headers:
    def __init__(self, **headers) -> None:
        self.headers = headers


class Sessions:
    """`lab_auth.Sessions` over a token -> user map, recording what it was asked."""

    def __init__(self, users=None) -> None:
        self.users, self.asked = dict(users or {}), []

    async def user_id(self, token: str) -> str:
        self.asked.append(token)
        if token not in self.users:
            raise errors.InvalidApiKey("not a live session")
        return self.users[token]


def gotrue(handler):
    return lab_auth.GoTrueSessions(
        httpx.AsyncClient(base_url="https://fake.supabase.co",
                          transport=httpx.MockTransport(handler)), "service-role")


# --- the token: a session JWT, never an API key ------------------------------------------
def test_lab_auth__only_a_bearer_session_jwt_reaches_the_verifier():
    """Oracle: an infrx API key (audience consumer/operator) is never a Lab credential and is
    never forwarded; nor is a missing, non-Bearer or malformed header."""
    sessions = Sessions({JWT: USER})
    for headers in ({}, {"authorization": f"Bearer {API_KEY}"},
                    {"authorization": f"Basic {JWT}"}, {"authorization": f"Bearer {JWT}.x"},
                    {"authorization": f"Bearer {JWT} {JWT}"}):
        with pytest.raises(errors.InvalidApiKey):
            run(lab_auth.authenticate(Headers(**headers), sessions))
    assert sessions.asked == []
    assert run(lab_auth.authenticate(Headers(authorization=f"Bearer {JWT}"), sessions)) == USER
    assert sessions.asked == [JWT]


def test_lab_auth__gotrue_answers_only_for_a_live_authenticated_session():
    """Oracle: the verifier asks the project's auth server about THIS token (with the
    project key as `apikey`), accepts only an `authenticated` user, reads any refusal as
    unauthenticated and any outage as unavailable - never as a user."""
    seen = []

    def answer(status, body=None):
        def handler(request):
            seen.append(request)
            return httpx.Response(status, json=body if body is not None else {})
        return gotrue(handler)

    user = {"id": USER, "aud": "authenticated", "role": "authenticated"}
    assert run(answer(200, user).user_id(JWT)) == USER
    assert seen[-1].url.path == "/auth/v1/user"
    assert seen[-1].headers["authorization"] == f"Bearer {JWT}"
    assert seen[-1].headers["apikey"] == "service-role"
    for status, body in ((401, None), (403, None), (200, {**user, "role": "anon"}),
                         (200, {**user, "aud": "lab"}), (200, {**user, "id": None})):
        with pytest.raises(errors.InvalidApiKey):
            run(answer(status, body).user_id(JWT))
    with pytest.raises(errors.DependencyUnavailable):
        run(answer(500).user_id(JWT))

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(errors.DependencyUnavailable):
        run(gotrue(down).user_id(JWT))


# --- the actor: current provider membership, re-derived on every call ----------------------
def test_lab_auth__a_consumer_only_user_is_denied_everywhere(world):
    """Oracle: owning a consumer organization is never a provider membership (R156): the
    consumer-only user is a 403 whatever provider id or capability the call names."""
    w = world
    for provider in (w.A, w.B, "not-a-provider"):
        with pytest.raises(errors.Forbidden):
            run(lab_auth.member(w.access, w.CONSUMER_ONLY, provider,
                                Cap.read_aggregate_health))


def test_lab_auth__another_providers_workspace_is_not_found(world):
    """Oracle: B's developer naming A's id learns nothing (404, not 403); BOTH is a member of
    A only, not of the provider its consumer organization has a grant with."""
    w = world
    with pytest.raises(errors.NotFound):
        run(lab_auth.member(w.access, w.DEV_B, w.A, Cap.read_aggregate_health))
    with pytest.raises(errors.NotFound):
        run(lab_auth.member(w.access, w.BOTH, w.B, Cap.read_aggregate_health))
    assert run(lab_auth.member(w.access, w.BOTH, w.A, Cap.manage_dev_deployment)
               ).provider_org_id == w.A


def test_lab_auth__the_role_must_hold_the_capability(world):
    """Oracle: the viewer reads aggregate health only; a developer manages dev deployments but
    proposes nothing (contracts/v2 ROLE_CAPABILITIES)."""
    w = world
    viewer = run(lab_auth.member(w.access, w.VIEWER_A, w.A, Cap.read_aggregate_health))
    assert (viewer.provider_org_id, viewer.role) == (w.A, "viewer")
    with pytest.raises(errors.Forbidden):
        run(lab_auth.member(w.access, w.VIEWER_A, w.A, Cap.manage_dev_deployment))
    assert run(lab_auth.member(w.access, w.DEV_A, w.A, Cap.manage_dev_deployment)).role \
        == "developer"
    with pytest.raises(errors.Forbidden):
        run(lab_auth.member(w.access, w.DEV_A, w.A, Cap.propose_publication))


def test_lab_auth__a_revocation_takes_effect_on_the_next_call(world):
    """Oracle: nothing is cached between calls - the call after the revocation is refused."""
    w = world
    assert run(lab_auth.member(w.access, w.BOTH, w.A, Cap.manage_dev_deployment))
    w.revoke_membership(w.A, w.BOTH)
    with pytest.raises(errors.Forbidden):             # no workspace left: consumer-only now
        run(lab_auth.member(w.access, w.BOTH, w.A, Cap.read_aggregate_health))


# --- the refusal: fixed reasons, statuses the Lab adapter maps -------------------------------
def test_lab_auth__refusals_are_the_lab_ports_reasons():
    """Oracle: 401 unauthenticated, 404 not_found, 403 denied, 422 invalid, 409 conflict, and
    anything else - an outage or a bug - 503 unavailable; the body is the reason only."""
    cases = ((errors.InvalidApiKey(), 401, "unauthenticated"),
             (errors.NotFound(), 404, "not_found"), (errors.Forbidden(), 403, "denied"),
             (errors.OrgSuspended(), 403, "denied"), (errors.InvalidRequest(), 422, "invalid"),
             (errors.RequestTooLarge(), 422, "invalid"), (errors.StateConflict(), 409, "conflict"),
             (errors.IdempotencyConflict(), 409, "conflict"),
             (errors.DependencyUnavailable(), 503, "unavailable"),
             (RuntimeError("bug"), 503, "unavailable"))
    for exc, status, reason in cases:
        response = lab_auth.refusal(exc)
        assert (response.status_code, response.body) == (status, b'{"refusal":"%s"}' % reason.encode())
        assert response.headers.get("cache-control") == "no-store"
    assert lab_auth.ok({}).headers.get("cache-control") == "no-store"


def test_lab_auth__the_token_is_never_logged(caplog):
    """Oracle: a failure whose text carries the token (a driver echoing its request) leaves as
    `unavailable` and the log names the exception type only."""
    class Leaky:
        async def user_id(self, token):
            raise RuntimeError(f"verifier failed for Bearer {token}")

    @lab_auth.guarded
    async def handler(request):
        await lab_auth.authenticate(request, Leaky())

    caplog.set_level(logging.DEBUG)
    response = run(handler(Headers(authorization=f"Bearer {JWT}")))
    assert response.status_code == 503
    assert caplog.records and all(JWT not in r.getMessage() and not r.exc_info
                                  for r in caplog.records)
