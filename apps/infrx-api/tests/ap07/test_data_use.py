#!/usr/bin/env python3
"""AP-07a: the grantor's data-use controls (`/console/v1/data-use`, `/keys/{id}/capture`,
`/data-grants`) on PostgreSQL - the existing consent schema, no new migration.

    INFRX_D_TASK=ap7 uv run --frozen pytest -q tests/ap07/test_data_use.py

The world is LAB-ACCESS's (`tests/l/access/worlds.py`): C1 is BOTH's personal organization (BOTH
owns it AND develops for provider A), C2 is CONSUMER_ONLY's; C1 grants A provider_sharing at
T0. Every case is the HTTP route over `DataUse` on the platform pool, with the actor from
`control.StaticActors` (AP-01's session resolver in production); the gateway's own consent
read (`capture.ConsentSource`, 0057) and LAB-ACCESS's `authorize_content` are the oracles of
what a decision means downstream.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.console import data_use
from infrx.contracts import api, errors
from infrx.contracts.records import TraceMode
from infrx.contracts.v2 import records as v2
from infrx.gateway import capture, control
from infrx.gateway.routes import console_data_use as routes
from infrx.state.jobstore import connector

from ..d import checks_admission as ca
from ..d import checks_credit as cc
from ..d import pgharness
from ..l.access.conftest import CASE

pytestmark = pytest.mark.pg
U = "test_data_use__"
BOTH_CONTENT = ["request_content", "response_content"]


def run(coro):
    return asyncio.run(coro)


def session(user: str, org: str, audience: str = "session") -> api.Actor:
    return api.Actor(audience=audience, user_id=user, org_id=org)  # type: ignore[arg-type]


def client(actor: api.Actor | None, *, on: bool = True) -> TestClient:
    app = FastAPI()
    rt = SimpleNamespace(data_use=data_use.DataUse(connector(pgharness.dsn(CASE))) if on else None,
                         actors=control.StaticActors(actor))
    routes.register(app, rt)
    return TestClient(app, raise_server_exceptions=False)


def owner(w) -> TestClient:
    return client(session(w.BOTH, w.C1))


def add_key(w, org: str, user: str) -> str:
    key = str(uuid.uuid4())
    w.conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, key_hash) "
                   "values (%s, %s, %s, 'k2', 'sk-infrx-k2', %s)", (key, org, user, f"hash-{key}"))
    return key


def keys_of(w, org: str) -> list[str]:
    return [str(k) for (k,) in w.conn.execute(
        "select id from public.api_keys where org_id = %s and revoked_at is null "
        "order by created_at, id", (org,))]


def consent_rows(w, org: str) -> list[tuple]:
    return w.conn.execute("select consent_version, trace_mode, content_retention_days, "
                          "evaluation_consent, actor_principal from infrx.consent_history "
                          "where org_id = %s order by consent_version", (org,)).fetchall()


def gateway_mode(w, org: str, key: str) -> TraceMode:
    """What the gateway admits the key's next request with (0057, as `ConsentSource` reads it)."""
    source = capture.ConsentSource(connector(pgharness.dsn(CASE)), ttl_s=0.0)
    return run(source.policy(SimpleNamespace(org_id=org, key_id=key), w.now())).trace_mode


def put_capture(c, key: str, mode: str, version: int, **extra):
    return c.put(f"{routes.KEYS_PATH}/{key}/capture",
                 json={"mode": mode, "consent_version": version, **extra})


def grant_body(w, provider=None, **override) -> dict:
    provider = provider or w.A
    return {"provider_org_id": provider, "model_ids": [w.MODELS[provider]],
            "categories": BOTH_CONTENT, "purposes": ["external_judging"], "retention_days": 30,
            "grant_version": 1, **override}


def may_read(w, purpose: v2.DataPurpose) -> bool:
    try:
        run(w.access.authorize_content(
            user_id=w.DEV_A, provider_org_id=w.A, grantor_org_id=w.C1, model_id=w.MODELS[w.A],
            category=v2.DataCategory.request_content, purpose=purpose))
    except errors.Forbidden:
        return False
    return True


# --- mounting -------------------------------------------------------------------------------
def test_data_use__nothing_is_mounted_without_data_use():
    """Oracle: no `rt.data_use` (the switch off), no route - never a route answering 503."""
    app = FastAPI()
    assert routes.register(app, SimpleNamespace(actors=control.StaticActors())) is None
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/console/v1")]
    assert client(None, on=False).get(routes.DATA_USE_PATH).status_code == 404


# --- the read -------------------------------------------------------------------------------
def test_data_use__the_read_is_the_grantors_own(pg_world):
    """Oracle: the organization comes from the actor, never the request; C1's live keys and
    grants only (C2's grant to B never), no consent yet (version 0, every key `off` at the
    gateway), answered no-store in the R270 shape."""
    w = pg_world
    answer = owner(w).get(routes.DATA_USE_PATH, params={"org_id": w.C2})
    assert answer.status_code == 200, answer.text
    assert answer.headers["cache-control"] == "no-store"
    doc = answer.json()
    assert doc["consent"] == {"version": 0, "mode": "off", "retention_days": None,
                              "evaluation_consent": False, "effective_at": None,
                              "revoked_at": None}
    assert [k["key_id"] for k in doc["keys"]] == keys_of(w, w.C1)
    assert {(k["mode"], k["effective_mode"]) for k in doc["keys"]} == {("off", "off")}
    assert len(doc["grants"]) == 1, doc["grants"]
    [grant] = doc["grants"]
    assert (grant["provider_org_id"], grant["version"], grant["state"], grant["purposes"]) \
        == (w.A, 1, "active", ["provider_sharing"])
    listed = owner(w).get(routes.GRANTS_PATH).json()
    assert listed == {"data": [grant], "next_cursor": None}


# --- capture: a versioned consent decision the gateway reads -------------------------------
def test_data_use__capture_is_a_versioned_consent_the_gateway_reads(pg_world):
    """Oracle: a key's capture change appends the organization's next consent version (by its
    owner) and sets the key's own mode in one step, so the gateway admits that key at the
    chosen mode and the organization's other keys stay off; turning it off is the next
    version and the gateway is off again."""
    w = pg_world
    c = owner(w)
    add_key(w, w.C1, w.BOTH)
    key, *others = keys_of(w, w.C1)
    assert others
    answer = put_capture(c, key, "full", 0, retention_days=14, evaluation_consent=True)
    assert answer.status_code == 200, answer.text
    doc = answer.json()
    assert (doc["consent"]["version"], doc["consent"]["mode"], doc["consent"]["retention_days"],
            doc["consent"]["evaluation_consent"]) == (1, "full", 14, True)
    mine = {k["key_id"]: (k["mode"], k["effective_mode"]) for k in doc["keys"]}
    assert mine[key] == ("full", "full")
    assert all(mine[o] == ("off", "off") for o in others)
    assert consent_rows(w, w.C1) == [(1, "full", 14, True, w.BOTH)]
    assert gateway_mode(w, w.C1, key) is TraceMode.full
    assert all(gateway_mode(w, w.C1, o) is TraceMode.off for o in others)
    w.conn.execute("update infrx.consent_history set revoked_at = infrx.now() "
                   "where org_id = %s and consent_version = 1", (w.C1,))
    revoked = {k["key_id"]: (k["mode"], k["effective_mode"])
               for k in c.get(routes.DATA_USE_PATH).json()["keys"]}
    assert revoked[key] == ("full", "off") and gateway_mode(w, w.C1, key) is TraceMode.off
    off = put_capture(c, key, "off", 1)
    assert off.status_code == 200, off.text
    assert (off.json()["consent"]["version"], off.json()["consent"]["mode"]) == (2, "off")
    assert gateway_mode(w, w.C1, key) is TraceMode.off


def test_data_use__the_head_is_the_highest_live_key_mode(pg_world):
    """Oracle: the consent head is never below a key's chosen mode (the gateway takes the
    lower of the two): one key full and another minimal is a full head; turning the full one
    off leaves a minimal head, and each key is admitted at its own mode."""
    w = pg_world
    c = owner(w)
    first, second = keys_of(w, w.C1)[0], add_key(w, w.C1, w.BOTH)
    assert put_capture(c, first, "full", 0).status_code == 200
    doc = put_capture(c, second, "minimal", 1).json()
    assert (doc["consent"]["version"], doc["consent"]["mode"]) == (2, "full")
    assert (gateway_mode(w, w.C1, first), gateway_mode(w, w.C1, second)) \
        == (TraceMode.full, TraceMode.minimal)
    doc = put_capture(c, first, "off", 2).json()
    assert (doc["consent"]["version"], doc["consent"]["mode"]) == (3, "minimal")
    assert (gateway_mode(w, w.C1, first), gateway_mode(w, w.C1, second)) \
        == (TraceMode.off, TraceMode.minimal)


def test_data_use__a_replay_writes_nothing_and_a_stale_view_conflicts(pg_world):
    """Oracle: the same decision again is answered from the current state without a new
    version (a lost response retried); a decision made against a version that is no longer
    the head is a 409 and writes nothing."""
    w = pg_world
    c = owner(w)
    key = keys_of(w, w.C1)[0]
    assert put_capture(c, key, "full", 0).status_code == 200
    replay = put_capture(c, key, "full", 0)
    assert replay.status_code == 200 and replay.json()["consent"]["version"] == 1
    stale = put_capture(c, key, "minimal", 0)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "state_conflict"
    assert [row[0] for row in consent_rows(w, w.C1)] == [1]
    assert gateway_mode(w, w.C1, key) is TraceMode.full


def test_data_use__evaluation_consent_needs_full_capture(pg_world):
    """Oracle (0003: no inferred evaluation consent): evaluation consent with less than full
    capture is a 422 envelope naming the field, and nothing is written."""
    w = pg_world
    key = keys_of(w, w.C1)[0]
    answer = put_capture(owner(w), key, "minimal", 0, evaluation_consent=True)
    assert answer.status_code == 422 and "error" in answer.json(), answer.text
    error = answer.json()["error"]
    assert error["code"] == "invalid_request" and error["field_errors"]
    assert consent_rows(w, w.C1) == []



def test_data_use__a_failure_is_an_envelope_never_a_trace():
    """Oracle (R270, `control.R270Route`): a service that breaks answers 500 in the envelope,
    no-store, with the request id echoed and no exception text; a refused actor is 401."""
    class Broken:
        async def read(self, actor):
            raise RuntimeError("secret-internal-detail")
    app = FastAPI()
    routes.register(app, SimpleNamespace(data_use=Broken(),
                                         actors=control.StaticActors(session("u", "o"))))
    answer = TestClient(app, raise_server_exceptions=False).get(
        routes.DATA_USE_PATH, headers={"X-Request-Id": "rid-ap7"})
    assert answer.status_code == 500 and answer.headers.get("cache-control") == "no-store"
    assert answer.json()["error"]["request_id"] == "rid-ap7"
    assert "secret-internal-detail" not in answer.text
    assert client(None).get(routes.DATA_USE_PATH).status_code == 401

# --- who decides -----------------------------------------------------------------------------
def test_data_use__only_the_grantors_owner_decides(pg_world):
    """Oracle: no session 401; a key session (any audience but a verified web session) 403;
    a member who is not the organization's owner 403; another organization's key 404; a body
    naming a grantor 422 - and nothing is written by any of them."""
    w = pg_world
    key = keys_of(w, w.C1)[0]
    shared = cc.personal_org(w.conn, cc.SHARED)     # CONSUMER_2 is a plain member there
    shared_key = add_key(w, shared, cc.SHARED)
    assert client(None).get(routes.DATA_USE_PATH).status_code == 401
    for actor, target, status in (
            (session(w.BOTH, w.C1, "consumer"), key, 403),
            (session(w.DEV_A, w.C1), key, 403),        # a provider developer, not C1's owner
            (session(cc.CONSUMER_2, shared), shared_key, 403),
            (session(w.CONSUMER_ONLY, w.C2), key, 404)):
        c = client(actor)
        answer = put_capture(c, target, "full", 0)
        assert answer.status_code == status, (actor, answer.text)
        assert answer.json()["error"]["request_id"]
        if status == 403:
            granted = c.post(routes.GRANTS_PATH, json=grant_body(w, grant_version=0))
            assert granted.status_code == 403, (actor, granted.text)
            revoked = c.delete(f"{routes.GRANTS_PATH}/{uuid.uuid4()}")
            assert revoked.status_code == 403, (actor, revoked.text)
    forged = owner(w).post(routes.GRANTS_PATH, json=grant_body(w, grantor_org_id=w.C2))
    assert forged.status_code == 422
    assert consent_rows(w, w.C1) == consent_rows(w, shared) == []
    assert [g["version"] for g in owner(w).get(routes.GRANTS_PATH).json()["data"]] == [1]


def test_data_use__a_suspended_organization_decides_nothing(pg_world):
    """Oracle (R33): an owner of a suspended organization cannot widen capture or grant."""
    w = pg_world
    w.conn.execute("update public.organizations set suspended = true, suspended_at = infrx.now(), "
                   "suspension_reason = 'abuse' where id = %s", (w.C1,))
    c = owner(w)
    assert put_capture(c, keys_of(w, w.C1)[0], "full", 0).status_code == 403
    assert c.post(routes.GRANTS_PATH, json=grant_body(w)).status_code == 403
    assert consent_rows(w, w.C1) == []



def test_data_use__a_suspended_organization_still_withdraws_its_grant(pg_world):
    """Oracle (0066's revoke-only door, `lab_withdraw_access_grant`): the owner of a suspended
    organization revokes its sharing grant - the very next content check is refused - and
    still grants nothing (R33 holds for every write but the withdrawal)."""
    w = pg_world
    c = owner(w)
    [grant] = c.get(routes.GRANTS_PATH).json()["data"]
    w.conn.execute("update public.organizations set suspended = true, suspended_at = infrx.now(), "
                   "suspension_reason = 'abuse' where id = %s", (w.C1,))
    answer = c.delete(f"{routes.GRANTS_PATH}/{grant['grant_id']}")
    assert answer.status_code == 200, answer.text
    assert (answer.json()["version"], answer.json()["state"]) == (2, "revoked")
    assert not may_read(w, v2.DataPurpose.provider_sharing)
    assert c.post(routes.GRANTS_PATH, json=grant_body(w, grant_version=2)).status_code == 403

# --- purpose grants --------------------------------------------------------------------------
def test_data_use__grants_are_purpose_specific_versions(pg_world):
    """Oracle: a grant for external_judging is the pair's next version and permits that
    purpose only - capture or provider sharing is not implied (`DataPurpose`); a replay
    returns the current version without writing; a stale version conflicts."""
    w = pg_world
    c = owner(w)
    answer = c.post(routes.GRANTS_PATH, json=grant_body(w))
    assert answer.status_code == 201, answer.text
    grant = answer.json()
    assert (grant["version"], grant["purposes"], grant["categories"], grant["state"]) \
        == (2, ["external_judging"], BOTH_CONTENT, "active")
    assert may_read(w, v2.DataPurpose.external_judging)
    assert not may_read(w, v2.DataPurpose.provider_sharing)
    replay = c.post(routes.GRANTS_PATH, json=grant_body(w))
    assert (replay.status_code, replay.json()) == (200, grant)
    stale = c.post(routes.GRANTS_PATH, json=grant_body(w, purposes=["training"]))
    assert stale.status_code == 409, stale.text
    assert [g["version"] for g in c.get(routes.GRANTS_PATH).json()["data"]] == [2]


def test_data_use__a_grant_expires_on_the_databases_clock(pg_world):
    """Oracle: an expiry in the past is refused; a grant past its expiry is reported
    `expired` and permits nothing, with no write in between."""
    w = pg_world
    c = owner(w)
    past = (w.now() - timedelta(minutes=1)).isoformat()
    assert c.post(routes.GRANTS_PATH, json=grant_body(w, expires_at=past)).status_code == 422
    soon = (w.now() + timedelta(hours=1)).isoformat()
    grant = c.post(routes.GRANTS_PATH, json=grant_body(w, expires_at=soon)).json()
    assert grant["state"] == "active" and may_read(w, v2.DataPurpose.external_judging)
    w.advance(3601)
    [after] = c.get(routes.GRANTS_PATH).json()["data"]
    assert (after["version"], after["state"]) == (grant["version"], "expired")
    assert not may_read(w, v2.DataPurpose.external_judging)


def test_data_use__a_grant_names_only_the_recipients_models(pg_world):
    """Oracle (0027): a model that is not the recipient provider's own is a 422, not a grant
    over another provider's model."""
    w = pg_world
    answer = owner(w).post(routes.GRANTS_PATH,
                           json=grant_body(w, model_ids=[w.MODELS[w.B]]))
    assert answer.status_code == 422, answer.text
    assert [g["version"] for g in owner(w).get(routes.GRANTS_PATH).json()["data"]] == [1]


def test_data_use__revocation_is_the_grantors_and_holds_at_once(pg_world):
    """Oracle: DELETE revokes the grantor's current grant - the very next content check is
    refused; a second DELETE answers the revoked grant without another version; another
    grantor's grant, an unknown id and a malformed one are 404."""
    w = pg_world
    c = owner(w)
    [grant] = c.get(routes.GRANTS_PATH).json()["data"]
    assert may_read(w, v2.DataPurpose.provider_sharing)
    answer = c.delete(f"{routes.GRANTS_PATH}/{grant['grant_id']}")
    assert answer.status_code == 200, answer.text
    assert (answer.json()["version"], answer.json()["state"]) == (2, "revoked")
    assert not may_read(w, v2.DataPurpose.provider_sharing)
    again = c.delete(f"{routes.GRANTS_PATH}/{grant['grant_id']}")
    assert (again.status_code, again.json()) == (200, answer.json())
    theirs = client(session(w.CONSUMER_ONLY, w.C2)).get(routes.GRANTS_PATH).json()["data"]
    for grant_id in (theirs[0]["grant_id"], str(uuid.uuid4()), "not-a-uuid"):
        assert c.delete(f"{routes.GRANTS_PATH}/{grant_id}").status_code == 404, grant_id
    assert ca.C1_KEY in keys_of(w, w.C1)            # the seed this world stands on
