#!/usr/bin/env python3
"""AP-01 01b-01d: the session actor (`infrx.console.session.SessionActors`), the console's
`me`/capabilities, the Lab's workspaces/capabilities/members and the operator's provider
onboarding - the API-IDENTITY failure matrix (expired token, forged provider, wrong audience,
revoked member, developer at the operator door, self-promotion, cross-origin submission).

    uv run --frozen pytest -q tests/ap01/test_identity.py                       # in memory
    INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_identity.py      # + PostgreSQL

Cases taking `world` run in memory and, marked `pg`, on the ap1 harness (`worlds.py`).
"""
from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from infrx.auth.context import auth_context
from infrx.config import DEPLOYMENT_DEFAULTS, Settings
from infrx.console.session import SessionActors
from infrx.contracts import errors
from infrx.contracts.v2.records import ROLE_CAPABILITIES, ProviderRole
from infrx.gateway.lab_auth import GoTrueSessions
from infrx.gateway.routes import console_me, lab_workspaces, operator_providers

from tests.ap01.worlds import (A, ADMIN_A, B, CONSUMER, DEV_A, DEV_B, EMAIL, FRESH, GRANT,
                               NAME, NEMO, OPERATOR, SLUG, SUSPENDED, UNVERIFIED, VIEWER_A)

APP = "https://app.example"
KEY = "sk-infrx-0123456789abcdefghij0123456789abcdefgh"
KEY_ORG = "b2b2b2b2-0000-4000-8000-000000000001"


def run(coro):
    return asyncio.run(coro)


class Keys:
    """`AuthResolver.context` for one infrx API key of `audience`."""

    def __init__(self, audience: str, user_id: str | None = None) -> None:
        self.audience, self.user_id = audience, user_id

    async def context(self, request):
        if request.headers.get("authorization") != f"Bearer {KEY}":
            raise errors.InvalidApiKey("unknown key")
        return auth_context(audience=self.audience, org_id=KEY_ORG,
                            key_id="c3c3c3c3-0000-4000-8000-000000000001", user_id=self.user_id)


def api(world, *, keys=None, idp=None, **switches) -> httpx.AsyncClient:
    gotrue = httpx.AsyncClient(base_url="http://gotrue.test",
                               transport=idp or httpx.ASGITransport(world.stub.app))
    rt = SimpleNamespace(
        actors=SessionActors(GoTrueSessions(gotrue, world.stub.apikey), world.identity,
                             keys=keys, origins=(APP,)),
        identity=world.identity, lab_access=world.access,
        settings=Settings(deployment=dataclasses.replace(DEPLOYMENT_DEFAULTS, **switches)))
    app = FastAPI()
    for module in (console_me, lab_workspaces, operator_providers):
        module.register(app, rt)
    return httpx.AsyncClient(base_url="http://api.test", transport=httpx.ASGITransport(app))


def as_(world, user: str, **extra) -> dict:
    return {"authorization": f"Bearer {world.stub.session(user)['access_token']}", **extra}


def mutation(world, user: str, key: str = "idem-1", **extra) -> dict:
    return as_(world, user, **{"idempotency-key": key, **extra})


def code(answer: httpx.Response) -> tuple[int, str | None]:
    """(status, R270 error code or None) - one value to compare, so a wrong answer is an
    assertion and never a KeyError."""
    body = answer.json() if answer.content else {}
    return answer.status_code, (body.get("error") or {}).get("code")


def caps(role: str) -> list[str]:
    return sorted(c.value for c in ROLE_CAPABILITIES[ProviderRole(role)])


def members(world, client, provider, user) -> dict:
    async def go():
        return (await client.get(f"/lab/v1/workspaces/{provider}/members",
                                 headers=as_(world, user))).json()
    return go()


# --- 01b: the session actor and the console account -------------------------------------------
def test_identity__me_is_the_sessions_own_account(world):
    """Oracle: `me` is the verified session's own account - a forged `user_id` or `org_id`
    in the query is not read - with the grant as an exact CREDIT string, never cacheable."""
    async def go():
        async with api(world) as c:
            return await c.get("/console/v1/me", params={"user_id": FRESH, "org_id": KEY_ORG},
                               headers=as_(world, CONSUMER))
    answer = run(go())
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["actor"] == {"audience": "session", "user_id": CONSUMER,
                             "org_id": world.org(CONSUMER), "provider_org_id": None,
                             "role": None, "operator": False}
    assert (body["state"], body["suspended"]) == ("ready", False)
    assert body["signup_grant"]["state"] == "granted"
    assert body["signup_grant"]["amount"] == {"amount": GRANT, "unit": "CREDIT"}
    assert answer.headers["cache-control"] == "no-store"


def test_identity__me_states_are_server_owned(world):
    """Oracle: verified / onboarding / suspended / operator come from the database, never the
    client: an unconfirmed email is `unverified` with no grant, a verified user without the
    grant is `onboarding`, a suspended organization reads suspended, the operator bit is the
    profile's."""
    async def go():
        async with api(world) as c:
            return {user: (await c.get("/console/v1/me", headers=as_(world, user))).json()
                    for user in (UNVERIFIED, FRESH, SUSPENDED, OPERATOR)}
    me = run(go())
    assert (me[UNVERIFIED]["state"], me[UNVERIFIED]["signup_grant"]) == (
        "unverified", {"state": "not_granted", "amount": None, "granted_at": None})
    assert me[FRESH]["state"] == "onboarding"
    assert (me[SUSPENDED]["state"], me[SUSPENDED]["suspended"]) == ("ready", True)
    assert me[OPERATOR]["actor"]["operator"] is True
    assert {me[u]["actor"]["operator"] for u in (UNVERIFIED, FRESH, SUSPENDED)} == {False}


def test_identity__a_key_or_a_dead_session_is_not_a_web_session(fake_world):
    """Oracle: wrong audience and expired token - no credential, a consumer API key (even
    with the key resolver configured), an unknown or signed-out session are 401 at every web
    door; an auth server that cannot answer is 503, never 401."""
    world = fake_world
    session = world.stub.session(CONSUMER)["access_token"]
    world.stub.access.pop(session)                     # signed out at the auth server
    doors = ("/console/v1/me", "/console/v1/capabilities", "/lab/v1/workspaces",
             f"/lab/v1/capabilities?provider_org_id={A}", f"/lab/v1/workspaces/{A}/members")

    async def go():
        answers = []
        async with api(world, keys=Keys("consumer", CONSUMER)) as c:
            for door in doors:
                for headers in ({}, {"authorization": f"Bearer {KEY}"},
                                {"authorization": f"Bearer {session}"},
                                {"authorization": "Bearer a.b.c"}):
                    answers.append((door, code(await c.get(door, headers=headers))))
        world.stub.down = True
        async with api(world) as c:
            down = code(await c.get("/console/v1/me", headers={
                "authorization": "Bearer x.y.z"}))
        return answers, down
    answers, down = run(go())
    assert {answer for _, answer in answers} == {(401, "invalid_api_key")}, answers
    assert down == (503, "dependency_unavailable")


def test_identity__console_capabilities_follow_the_account_and_the_switches(world):
    """Oracle: the allowed actions are the server's (a suspended account may revoke a key but
    not create one; only a verified user without the grant may claim it) and each feature's
    availability is the deployment switch's, `dedicated_endpoints` honestly disabled."""
    async def go():
        async with api(world, feedback_api=True) as c:
            return {user: (await c.get("/console/v1/capabilities",
                                       headers=as_(world, user))).json()
                    for user in (CONSUMER, SUSPENDED, FRESH, UNVERIFIED, OPERATOR)}
    got = run(go())
    assert got[CONSUMER]["actions"] == {"create_key": True, "revoke_key": True,
                                        "claim_signup_grant": False, "operator_console": False}
    assert got[SUSPENDED]["actions"]["create_key"] is False
    assert got[SUSPENDED]["actions"]["revoke_key"] is True
    assert got[FRESH]["actions"]["claim_signup_grant"] is True
    assert got[FRESH]["actions"]["create_key"] is False
    assert got[UNVERIFIED]["actions"]["claim_signup_grant"] is False
    assert got[OPERATOR]["actions"]["operator_console"] is True
    features = {k: (v["state"], v["reason"]) for k, v in got[CONSUMER]["features"].items()}
    assert features == {"feedback": ("configured", None),
                        "trace_export": ("disabled", "switch_off"),
                        "dedicated_endpoints": ("disabled", "not_offered")}


# --- 01c: Lab workspaces, capabilities and members --------------------------------------------
def test_identity__workspaces_are_current_memberships_with_their_capabilities(world):
    """Oracle: the workspace list is the user's current provider memberships with the role's
    capability set (the Python table the Lab will consume); owning a consumer organization is
    no workspace."""
    async def go():
        async with api(world) as c:
            return [(await c.get("/lab/v1/workspaces", headers=as_(world, user))).json()
                    for user in (DEV_A, CONSUMER)]
    dev, consumer = run(go())
    assert dev == {"data": [{"provider_org_id": A, "provider_name": NAME[A],
                             "role": "developer", "capabilities": caps("developer")}],
                   "next_cursor": None}
    assert consumer == {"data": [], "next_cursor": None}


def test_identity__lab_capabilities_are_the_members_own(world):
    """Oracle: forged provider - capabilities for the member's own workspace only; another
    provider, a consumer, or a made-up id is the same 404 (nothing confirmed); each Lab
    feature's availability is its switch's."""
    async def go():
        async with api(world, lab_control=True) as c:
            own = await c.get("/lab/v1/capabilities", params={"provider_org_id": A},
                              headers=as_(world, DEV_A))
            refused = [code(await c.get("/lab/v1/capabilities",
                                        params={"provider_org_id": provider},
                                        headers=as_(world, user)))
                       for user, provider in ((DEV_A, B), (CONSUMER, A), (DEV_A, "nope"),
                                              (DEV_B, A))]
            return own, refused
    own, refused = run(go())
    body = own.json()
    assert (body["provider_org_id"], body["role"], body["capabilities"]) == (
        A, "developer", caps("developer"))
    assert body["features"]["control"]["state"] == "configured"
    assert body["features"]["traces"] == {**body["features"]["traces"], "state": "disabled"}
    assert refused == [(404, "not_found")] * 4


def test_identity__members_are_read_by_members_only(world):
    """Oracle: any current member reads the workspace's current members; a member of another
    provider gets the same 404 as a stranger."""
    async def go():
        async with api(world) as c:
            viewer = await c.get(f"/lab/v1/workspaces/{A}/members", headers=as_(world, VIEWER_A))
            other = await c.get(f"/lab/v1/workspaces/{A}/members", headers=as_(world, DEV_B))
            return viewer, other
    viewer, other = run(go())
    assert viewer.status_code == 200, viewer.text
    assert sorted((m["email"], m["role"]) for m in viewer.json()["data"]) == sorted(
        [(EMAIL[ADMIN_A], "administrator"), (EMAIL[DEV_A], "developer"),
         (EMAIL[VIEWER_A], "viewer")])
    assert code(other) == (404, "not_found")


def test_identity__an_administrator_adds_a_member_once(world):
    """Oracle: an administrator's grant is one membership: a retry answers the same row
    (200), another role for a current member is 409, an unknown address 404; the new member
    then sees the workspace."""
    path = f"/lab/v1/workspaces/{A}/members"
    grant = {"email": EMAIL[CONSUMER].upper(), "role": "viewer"}      # addresses fold case

    async def go():
        async with api(world) as c:
            first = await c.post(path, json=grant, headers=mutation(world, ADMIN_A))
            again = await c.post(path, json=grant, headers=mutation(world, ADMIN_A))
            other_role = await c.post(path, json={**grant, "role": "developer"},
                                      headers=mutation(world, ADMIN_A, "idem-2"))
            unknown = await c.post(path, json={"email": "nobody@example.com", "role": "viewer"},
                                   headers=mutation(world, ADMIN_A, "idem-3"))
            seen = (await c.get("/lab/v1/workspaces", headers=as_(world, CONSUMER))).json()
            return first, again, other_role, unknown, seen
    first, again, other_role, unknown, seen = run(go())
    assert first.status_code == 201, first.text
    assert (first.json()["user_id"], first.json()["role"]) == (CONSUMER, "viewer")
    assert again.status_code == 200 and again.json() == first.json()
    assert code(other_role) == (409, "state_conflict")
    assert code(unknown) == (404, "not_found")
    assert [(w["provider_org_id"], w["role"]) for w in seen["data"]] == [(A, "viewer")]


def test_identity__only_an_administrator_changes_members_and_never_their_own(world):
    """Oracle: no self-elevation - a developer or viewer cannot grant or revoke (403), a
    developer cannot promote themself, an administrator cannot change their own membership,
    a member of another provider is a 404; nothing is written by any refusal."""
    path = f"/lab/v1/workspaces/{A}/members"

    async def go():
        async with api(world) as c:
            answers = [
                code(await c.post(path, json={"email": EMAIL[DEV_A], "role": "administrator"},
                                  headers=mutation(world, DEV_A))),
                code(await c.post(path, json={"email": EMAIL[CONSUMER], "role": "viewer"},
                                  headers=mutation(world, VIEWER_A))),
                code(await c.delete(f"{path}/{VIEWER_A}", headers=mutation(world, DEV_A))),
                code(await c.post(path, json={"email": EMAIL[ADMIN_A], "role": "developer"},
                                  headers=mutation(world, ADMIN_A))),
                code(await c.delete(f"{path}/{ADMIN_A}", headers=mutation(world, ADMIN_A))),
                code(await c.post(path, json={"email": EMAIL[DEV_B], "role": "viewer"},
                                  headers=mutation(world, DEV_B))),
            ]
            return answers, await members(world, c, A, ADMIN_A)
    answers, after = run(go())
    assert answers == [(403, "forbidden")] * 5 + [(404, "not_found")]
    assert sorted((m["email"], m["role"]) for m in after["data"]) == sorted(
        [(EMAIL[ADMIN_A], "administrator"), (EMAIL[DEV_A], "developer"),
         (EMAIL[VIEWER_A], "viewer")])


def test_identity__a_revoked_member_is_refused_at_once(world):
    """Oracle: revoked member - the revocation takes effect on the very next call (no cache):
    the revoked developer's capabilities and members reads are 404 and the workspace is gone
    from their list; a repeated revocation answers the same row; a never-member is 404."""
    path = f"/lab/v1/workspaces/{A}/members"

    async def go():
        async with api(world) as c:
            revoked = await c.delete(f"{path}/{DEV_A}", headers=mutation(world, ADMIN_A))
            again = await c.delete(f"{path}/{DEV_A}", headers=mutation(world, ADMIN_A))
            stranger = code(await c.delete(f"{path}/{CONSUMER}",
                                           headers=mutation(world, ADMIN_A, "idem-2")))
            after = [code(await c.get(door, headers=as_(world, DEV_A))) for door in (
                f"/lab/v1/capabilities?provider_org_id={A}", f"{path}")]
            listed = (await c.get("/lab/v1/workspaces", headers=as_(world, DEV_A))).json()
            current = await members(world, c, A, ADMIN_A)
            return revoked, again, stranger, after, listed, current
    revoked, again, stranger, after, listed, current = run(go())
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["user_id"] == DEV_A and revoked.json()["revoked_at"] is not None
    assert again.status_code == 200 and again.json() == revoked.json()
    assert stranger == (404, "not_found")
    assert after == [(404, "not_found")] * 2
    assert listed["data"] == []
    assert sorted(m["email"] for m in current["data"]) == [EMAIL[ADMIN_A], EMAIL[VIEWER_A]]


def test_identity__member_mutations_need_an_idempotency_key_and_the_origin(fake_world):
    """Oracle: R270 - a mutation without `Idempotency-Key` is a 422 naming it; a browser
    submission from another origin is a 403 (CSRF); neither writes."""
    world, path = fake_world, f"/lab/v1/workspaces/{A}/members"
    grant = {"email": EMAIL[CONSUMER], "role": "viewer"}

    async def go():
        async with api(world) as c:
            bare = await c.post(path, json=grant, headers=as_(world, ADMIN_A))
            evil = await c.post(path, json=grant, headers=mutation(
                world, ADMIN_A, origin="https://evil.example"))
            own = await c.post(path, json=grant, headers=mutation(world, ADMIN_A, origin=APP))
            return bare, evil, own
    bare, evil, own = run(go())
    assert code(bare) == (422, "invalid_request")
    assert [f["field"] for f in bare.json()["error"]["field_errors"]] == ["Idempotency-Key"]
    assert code(evil) == (403, "forbidden")
    assert own.status_code == 201


# --- 01d: operator provider onboarding ---------------------------------------------------------
def test_identity__an_operator_creates_a_provider_once(world):
    """Oracle: the operator creates a new provider and its first administrator once; the
    retry answers the same provider (200); the same slug with another name is 409; the named
    administrator then holds the workspace."""
    body = {"slug": "internal-test", "display_name": "Internal test",
            "administrator_email": EMAIL[FRESH]}

    async def go():
        async with api(world) as c:
            first = await c.post("/operator/v1/providers", json=body,
                                 headers=mutation(world, OPERATOR))
            again = await c.post("/operator/v1/providers", json=body,
                                 headers=mutation(world, OPERATOR))
            renamed = await c.post("/operator/v1/providers",
                                   json={**body, "display_name": "Other"},
                                   headers=mutation(world, OPERATOR, "idem-2"))
            seen = (await c.get("/lab/v1/workspaces", headers=as_(world, FRESH))).json()
            return first, again, renamed, seen
    first, again, renamed, seen = run(go())
    assert first.status_code == 201, first.text
    provider = first.json()["provider"]
    assert (provider["slug"], provider["display_name"]) == ("internal-test", "Internal test")
    assert first.json()["administrator"]["role"] == "administrator"
    assert again.status_code == 200 and again.json() == first.json()
    assert code(renamed) == (409, "state_conflict")
    assert [(w["provider_org_id"], w["role"]) for w in seen["data"]] == [
        (provider["provider_org_id"], "administrator")]


def test_identity__only_an_operator_creates_providers(fake_world):
    """Oracle: developer at the operator door - a provider developer, a consumer session, a
    consumer key are 403; no credential is 401; the operator without `Idempotency-Key` is 422;
    an operator-audience key is the operator."""
    world = fake_world
    body = {"slug": "keyed", "display_name": "Keyed"}

    async def go():
        async with api(world, keys=Keys("consumer", CONSUMER)) as c:
            refused = [code(await c.post("/operator/v1/providers", json=body, headers=h))
                       for h in (mutation(world, DEV_A), mutation(world, CONSUMER),
                                 {"authorization": f"Bearer {KEY}", "idempotency-key": "k"},
                                 {"idempotency-key": "k"}, as_(world, OPERATOR))]
        async with api(world, keys=Keys("operator")) as c:
            keyed = await c.post("/operator/v1/providers", json=body, headers={
                "authorization": f"Bearer {KEY}", "idempotency-key": "k"})
        return refused, keyed
    refused, keyed = run(go())
    assert refused == [(403, "forbidden")] * 3 + [(401, "invalid_api_key"),
                                                  (422, "invalid_request")]
    assert keyed.status_code == 201, keyed.text
    assert keyed.json()["provider"]["created_by"] == f"operator-key:{KEY_ORG}"


@pytest.mark.pg
def test_identity_pg__an_existing_provider_is_never_reassigned(pg_world):
    """Oracle: 01d - onboarding never touches an existing provider: NemoStation's slug with
    another name is 409, with its own name it answers the existing provider (no new row), and
    the NemoStation model keeps its owner either way."""
    world = pg_world

    async def go():
        async with api(world) as c:
            renamed = await c.post("/operator/v1/providers", json={
                "slug": SLUG[NEMO], "display_name": "Someone else"},
                headers=mutation(world, OPERATOR))
            same = await c.post("/operator/v1/providers", json={
                "slug": SLUG[NEMO], "display_name": NAME[NEMO]},
                headers=mutation(world, OPERATOR, "idem-2"))
            return renamed, same
    renamed, same = run(go())
    assert code(renamed) == (409, "state_conflict")
    assert same.status_code == 200 and same.json()["provider"]["provider_org_id"] == NEMO
    assert world.model_owner() == NEMO
    assert world.conn.execute("select count(*) from infrx.provider_orgs").fetchone()[0] == 3
