#!/usr/bin/env python3
"""WR-L4-1: `/lab/v1/control` over L3's control operations - LAB-ACCESS and SPLIT-CONTRACT,
the route half.

    uv run --frozen pytest -q tests/g/lab_control

L3 (`infrx/lab/control`, lab-access-lw2) is not on the base, so the operations are the
recording fake below, shaped by the L3 brief and the Lab's `port.ts`; the membership under the
actor is L2's `LabAccess` over the fake store (its PostgreSQL half is tests/g/lab_auth's).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.gateway.routes import lab_control as lc
from infrx.lab.access import DeploymentAggregate

from .. import support
from ...l.access.worlds import FakeWorld

P = lc.CONTROL_PREFIX
ADMIN_A = "a1000000-0000-4000-8000-0000000000ad"
DIGEST = "sha256:" + "a" * 64
AT = datetime(2026, 9, 27, tzinfo=timezone.utc)
REGISTRATION = {"name": "marlin-2b-ft", "artifact_digest": DIGEST, "schema_version": "v1",
                "runtime": "vllm-0.11"}
REV = "0d000000-0000-4000-8000-00000000000d"


def token(user: str) -> str:
    """A distinct JWT-shaped token per user (the verifier below maps it back)."""
    return f"eyJ0.{user.replace('-', '')}.c2ln"


class Sessions:
    async def user_id(self, value: str) -> str:
        if value not in _USERS:
            raise errors.InvalidApiKey("not a live session")
        return _USERS[value]


_USERS: dict[str, str] = {}


def world() -> FakeWorld:
    w = FakeWorld()
    w.store.memberships[(w.A, ADMIN_A)] = v2.ProviderMembership(
        provider_org_id=w.A, user_id=ADMIN_A, role=v2.ProviderRole.administrator,
        granted_by="ops", granted_at=w.now() - timedelta(days=1))
    for user in (w.DEV_A, w.DEV_B, w.VIEWER_A, w.BOTH, w.CONSUMER_ONLY, ADMIN_A):
        _USERS[token(user)] = user
    return w


def deployment(provider: str, **update) -> lc.Deployment:
    return lc.Deployment(
        deployment_revision_id=REV, model_id="marlin-2b-ft", serving_version_id=provider,
        revision_label="r1", runtime="vllm-0.11", schema_version="v1", rate_card_version=None,
        environment="dev", visibility="private", state="active", smoke="none",
        created_at=AT).model_copy(update=update)


class Ops:
    """`ControlOperations` recording every call; each record names the provider it was
    asked for (in `serving_version_id` / `model_id`), so a leak across providers shows."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail = fail

    def _call(self, *call):
        self.calls.append(call)
        if self.fail is not None:
            raise self.fail

    async def models(self, actor):
        self._call("models", actor)
        return [lc.Model(model_id=actor.provider_org_id, revision_label="r1",
                         artifact_digest=DIGEST, schema_version="v1", runtime="vllm-0.11",
                         registered_at=AT)]

    async def deployments(self, actor):
        self._call("deployments", actor)
        return [deployment(actor.provider_org_id)]

    async def proposals(self, actor):
        self._call("proposals", actor)
        return [lc.Proposal(proposal_id=REV, kind="publish", deployment_revision_id=REV,
                            state="proposed", proposed_at=AT, decided_at=None)]

    async def register(self, actor, registration):
        self._call("register", actor, registration)
        return deployment(actor.provider_org_id, model_id=registration.name)

    async def smoke(self, actor, deployment_revision_id):
        self._call("smoke", actor, deployment_revision_id)
        return deployment(actor.provider_org_id, smoke="passed")

    async def propose(self, actor, kind, deployment_revision_id):
        self._call("propose", actor, kind, deployment_revision_id)
        return lc.Proposal(proposal_id=REV, kind=kind,
                           deployment_revision_id=deployment_revision_id, state="proposed",
                           proposed_at=AT, decided_at=None)


def client(w, ops=None, *, on_runtime=False, operations=True):
    app = FastAPI()
    rt = support.runtime()
    control = lc.LabControl(Sessions(), w.access, (ops or Ops()) if operations else None)
    if on_runtime:
        rt.lab_control = control
        mounted = lc.register(app, rt)
    else:
        mounted = lc.register(app, rt, control)
    assert mounted is control
    return TestClient(app, raise_server_exceptions=False)


def call(c, user, method, path, provider, body=None, headers=None, raw=None):
    head = {"authorization": f"Bearer {token(user)}"} if user else {}
    head.update(headers or {})
    if raw is not None:
        return c.request(method, f"{P}/{path}", params={"provider_org_id": provider},
                         content=raw, headers={"content-type": "application/json", **head})
    return c.request(method, f"{P}/{path}", params={"provider_org_id": provider}, json=body,
                     headers=head)


ROUTES = (("GET", "models", None), ("GET", "deployments", None), ("GET", "proposals", None),
          ("GET", "aggregates", None), ("POST", "register", REGISTRATION),
          ("POST", f"deployments/{REV}/smoke", None),
          ("POST", "proposals", {"kind": "publish", "deployment_revision_id": REV}))


# --- mounting ------------------------------------------------------------------------------
def test_lab_control__nothing_is_mounted_without_a_control():
    """Oracle: no `rt.lab_control` (LAB_CONTROL off), no route - a 404, never a stand-in."""
    app = FastAPI()
    assert lc.register(app, support.runtime()) is None
    assert not [r for r in app.routes if getattr(r, "path", "").startswith(P)]
    w = world()
    c = client(w, on_runtime=True)
    assert call(c, w.DEV_A, "GET", "models", w.A).status_code == 200


# --- identity first, the actor from the session ------------------------------------------
def test_lab_control__every_route_needs_the_session_before_anything_else():
    """Oracle: no token, or an API key, is a 401 on every route and nothing is asked - not
    even the body, so an invalid one is still a 401, not a 422."""
    w, ops = world(), Ops()
    c = client(w, ops)
    for method, path, body in ROUTES:
        for headers in ({}, {"authorization": f"Bearer {support.TOKEN}"}):
            for sent in (body, {"forged": True}):
                answer = call(c, None, method, path, w.A, sent, headers)
                assert (answer.status_code, answer.json()) \
                    == (401, {"refusal": "unauthenticated"})
            answer = call(c, None, method, path, w.A, headers=headers, raw="{")   # AP-00
            assert (answer.status_code, answer.json()) == (401, {"refusal": "unauthenticated"})
    assert ops.calls == []


def test_lab_control__a_consumer_only_user_is_denied_on_every_route():
    """Oracle (LAB-ACCESS): 403 everywhere, whichever provider id the call names."""
    w, ops = world(), Ops()
    c = client(w, ops)
    for method, path, body in ROUTES:
        for provider in (w.A, w.B):
            answer = call(c, w.CONSUMER_ONLY, method, path, provider, body)
            assert (answer.status_code, answer.json()) == (403, {"refusal": "denied"})
    assert ops.calls == []


def test_lab_control__another_providers_workspace_is_not_found_on_every_route():
    """Oracle (LAB-ACCESS, cross-provider): B's member naming A's id gets a 404 and the
    operations are never asked; A's own member reaches A's records only."""
    w, ops = world(), Ops()
    c = client(w, ops)
    for method, path, body in ROUTES:
        answer = call(c, w.DEV_B, method, path, w.A, body)
        assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"})
    assert ops.calls == []
    assert call(c, w.DEV_A, "GET", "models", w.A).json()["data"][0]["model_id"] == w.A


def test_lab_control__the_actor_is_the_sessions_membership_never_the_body():
    """Oracle: the operations get (provider, user, role) from the verified session and the
    current membership; a body naming a provider, user or role is refused (422)."""
    w, ops = world(), Ops()
    c = client(w, ops)
    answer = call(c, w.BOTH, "POST", "register", w.A, REGISTRATION)
    assert answer.status_code == 201 and answer.json()["serving_version_id"] == w.A
    (name, actor, registration), = ops.calls
    assert (name, actor) == ("register", lc.Actor(provider_org_id=w.A, user_id=w.BOTH,
                                                  role="developer"))
    assert registration == lc.Registration(**REGISTRATION)
    for forged in ({"provider_org_id": w.B}, {"user_id": ADMIN_A}, {"role": "administrator"}):
        answer = call(c, w.BOTH, "POST", "register", w.A, {**REGISTRATION, **forged})
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    assert len(ops.calls) == 1


def test_lab_control__each_operation_needs_its_capability():
    """Oracle: viewer reads; developer also registers and smokes; only an administrator
    proposes (contracts/v2 ROLE_CAPABILITIES). A refusal never reaches the operations."""
    w = world()
    expected = {w.VIEWER_A: (200, 200, 200, 200, 403, 403, 403),
                w.DEV_A: (200, 200, 200, 200, 201, 200, 403),
                ADMIN_A: (200, 200, 200, 200, 201, 200, 201)}
    for user, statuses in expected.items():
        ops = Ops()
        c = client(w, ops)
        got = tuple(call(c, user, m, path, w.A, body).status_code for m, path, body in ROUTES)
        assert got == statuses, user
        assert len(ops.calls) == statuses.count(200) + statuses.count(201) - 1  # aggregates: L2
    ops = Ops()
    c = client(w, ops)
    for path, field in (("models", "artifact_digest"), ("deployments", "environment"),
                        ("proposals", "proposal_id")):
        assert field in call(c, w.VIEWER_A, "GET", path, w.A).json()["data"][0], path
    assert call(c, ADMIN_A, "POST", f"deployments/{REV}/smoke", w.A).json()["smoke"] == "passed"
    answer = call(c, ADMIN_A, "POST", "proposals", w.A,
                  {"kind": "rollback", "deployment_revision_id": REV})
    assert answer.json()["kind"] == "rollback"
    assert ops.calls[-2:] == [("smoke", lc.Actor(provider_org_id=w.A, user_id=ADMIN_A,
                                                 role="administrator"), REV),
                              ("propose", lc.Actor(provider_org_id=w.A, user_id=ADMIN_A,
                                                   role="administrator"), "rollback", REV)]


def test_lab_control__a_revoked_membership_is_refused_on_the_next_call():
    """Oracle: nothing is cached - the call after the revocation is a 403."""
    w = world()
    c = client(w)
    assert call(c, w.DEV_A, "GET", "deployments", w.A).status_code == 200
    w.revoke_membership(w.A, w.DEV_A)
    assert call(c, w.DEV_A, "GET", "deployments", w.A).status_code == 403


# --- the operations' answers ----------------------------------------------------------------
def test_lab_control__operation_refusals_are_the_lab_ports_reasons():
    """Oracle: L3's typed refusals reach the Lab as port.ts's fixed reasons."""
    w = world()
    for exc, status, reason in ((errors.NotFound(), 404, "not_found"),
                                (errors.Forbidden(), 403, "denied"),
                                (errors.InvalidRequest(), 422, "invalid"),
                                (errors.StateConflict(), 409, "conflict"),
                                (errors.DependencyUnavailable(), 503, "unavailable")):
        c = client(w, Ops(fail=exc))
        for method, path, body in ROUTES:
            if path == "aggregates":
                continue
            answer = call(c, ADMIN_A, method, path, w.A, body)
            assert answer.status_code == status, path
            assert answer.json() == {"refusal": reason}, path


def test_lab_control__without_l3_wired_operations_are_unavailable_and_health_is_not(caplog):
    """Oracle: LAB_CONTROL on before L3 merges answers 503 for L3's operations - an expected
    state, not a failure to log - after the session and membership checks, while aggregate
    health (L2, merged) is served."""
    w = world()
    c = client(w, operations=False)
    caplog.set_level("ERROR")
    for method, path, body in ROUTES:
        answer = call(c, ADMIN_A, method, path, w.A, body)
        assert answer.status_code == (200 if path == "aggregates" else 503), path
    assert call(c, w.DEV_B, "GET", "models", w.A).status_code == 404
    assert caplog.records == []


def test_lab_control__aggregates_carry_no_customer_identity():
    """Oracle (LAB-ACCESS): the health rows are L2's closed `DeploymentAggregate` - no
    organization, key, user or request field reaches the Lab."""
    w = world()
    answer = call(client(w), w.VIEWER_A, "GET", "aggregates", w.A)
    rows = answer.json()["data"]
    assert answer.status_code == 200 and rows
    assert all(set(row) == set(DeploymentAggregate.model_fields) for row in rows)
    assert [row["deployment_revision_id"] for row in rows] == w.REVISIONS[w.A]
    assert answer.headers.get("cache-control") == "no-store"


def test_lab_control__a_body_is_json_bounded_and_valid_before_the_operations():
    """Oracle: a bad digest, a wrong kind, a non-JSON or oversized body is a 422 and the
    operations are never asked."""
    w, ops = world(), Ops()
    c = client(w, ops)
    for path, body, headers in (
            ("register", {**REGISTRATION, "artifact_digest": "sha256:xyz"}, None),
            ("register", {**REGISTRATION, "name": ""}, None),
            ("proposals", {"kind": "promote", "deployment_revision_id": REV}, None),
            ("register", {**REGISTRATION,
                          "runtime": "x" * lc.lab_auth.MAX_BODY_BYTES}, None)):
        answer = call(c, ADMIN_A, "POST", path, w.A, body, headers)
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"}), path
    text = json.dumps(REGISTRATION)
    for raw, content_type in ((text, "text/plain"), (text, "application/merge-patch+json"),
                              (text + " " * lc.lab_auth.MAX_BODY_BYTES, None)):
        answer = call(c, ADMIN_A, "POST", "register", w.A, raw=raw,
                      headers={"content-type": content_type} if content_type else None)
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    assert ops.calls == []
    assert call(c, ADMIN_A, "POST", "register", w.A, raw=text + " " * 64).status_code == 201
