#!/usr/bin/env python3
"""WR-R4-1: `/lab/v1/releases` and `/lab/v1/optimizations` - LAB-ACCESS and ROLLOUT-PIN, the
route half.

    uv run --frozen pytest -q tests/g/lab_releases

D9 is R2's `ReleaseStore` port (`PgReleaseStore` on lab-sql-lw2, not merged: the fake below
keeps its row per policy revision, CAS on a fence). The read models WR-R4-1 asks of lab-sql
and the proposal store (WR-R4-2) are small fakes. The membership under the actor is L2's
`LabAccess` over the LAB-ACCESS fake world; the session verifier is a minimal fake of
`lab_auth.Sessions` (lab-api, batch #4).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.contracts.lab import records as lab
from infrx.contracts.v2 import records as v2
from infrx.gateway.routes import lab_releases as lr
from infrx.rollouts import control as r2

from .. import support
from ...l.access.worlds import FakeWorld

ADMIN_A, ADMIN_B = ("a1000000-0000-4000-8000-0000000000ad", "b1000000-0000-4000-8000-0000000000ad")
STARTED = datetime(2026, 9, 27, 10, tzinfo=timezone.utc)


def token(user: str) -> str:
    return f"eyJ0.{user.replace('-', '')}.c2ln"


class Sessions:
    """`lab_auth.Sessions`: a distinct JWT-shaped token per user."""

    def __init__(self, users) -> None:
        self.users = {token(u): u for u in users}

    async def user_id(self, value: str) -> str:
        if value not in self.users:
            raise errors.InvalidApiKey("not a live session")
        return self.users[value]


def policy(provider: str, n: int) -> tuple[str, dict]:
    """A `lab.rollout_policy.1` revision of `provider` and its ref."""
    serving = f"lab:serving:{provider}:{n:08x}-0000-4000-8000-00000000002{n}@sha256:"
    record = {"schema": "lab.rollout_policy.1", "provider_org_id": provider,
              "policy_id": f"{n:08x}-0000-4000-8000-00000000006e", "version": 1,
              "created_at": "2026-09-27T10:00:00Z",
              "endpoint_id": f"{n:08x}-0000-4000-8000-00000000006f",
              "baseline_ref": serving + "f" * 64, "mode": "canary", "cohort": "account",
              "candidates": [{"serving_ref": serving + "b" * 64, "weight_bp": 1_000}]}
    return lab.ref_of(record), record


def release(ref: str, record: dict, *, state="running", fence=1, verdict="expand") -> dict:
    """port.ts's `Release`, snake_case, as the read model serves it."""
    return {"policy_ref": ref, "endpoint_id": record["endpoint_id"], "version": 1,
            "baseline_ref": record["baseline_ref"], "mode": "canary", "cohort": "account",
            "candidates": record["candidates"], "state": state, "fence": fence,
            "plan_digest": "sha256:" + "9" * 64, "plan": {"horizon_s": 86_400},
            "started_at": "2026-09-27T10:00:00Z",
            "progress": {"assignments": [{"serving_ref": record["baseline_ref"],
                                          "pinned_by": "cohort", "requests": 900}]},
            "verdict": None if verdict is None else {
                "action": verdict,
                "reasons": [] if verdict == "expand" else ["report_inconclusive"],
                "evidence_refs": [], "evaluated_at": "2026-09-28T10:00:00Z"}}


class Records:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, list]] = {}

    def of(self, provider):
        return self.rows.setdefault(provider, {"releases": [], "decisions": [], "variants": []})

    async def releases(self, provider_org_id):
        return list(self.of(provider_org_id)["releases"])

    async def decisions(self, provider_org_id):
        return list(self.of(provider_org_id)["decisions"])

    async def variants(self, provider_org_id):
        return list(self.of(provider_org_id)["variants"])


class Proposals:
    """WR-R4-2: one pending proposal per policy revision."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, dict]] = []

    async def proposals(self, provider_org_id):
        return [p for owner, p in self.rows if owner == provider_org_id]

    async def add(self, provider_org_id, proposal):
        if any(p["policy_ref"] == proposal["policy_ref"] and p["state"] == "proposed"
               for _, p in self.rows):
            raise errors.StateConflict("a proposal is pending for this release")
        self.rows.append((provider_org_id, proposal))
        return proposal


class D9:
    """R2's `ReleaseStore`: D9's row per policy revision (not provider-scoped: the route
    scopes first); `reads` records every lookup."""

    def __init__(self) -> None:
        self.rows: dict[str, r2.Release] = {}
        self.reads: list[str] = []

    def put(self, ref: str, state: str, fence: int) -> None:
        self.rows[ref] = r2.Release(state=state, fence=fence,
                                    plan_digest="sha256:" + "9" * 64, started_at=STARTED)

    async def release(self, policy_ref):
        self.reads.append(policy_ref)
        if policy_ref not in self.rows:
            raise errors.NotFound("no such release")
        return self.rows[policy_ref]


class World(FakeWorld):
    def __init__(self) -> None:
        super().__init__()
        for provider, admin in ((self.A, ADMIN_A), (self.B, ADMIN_B)):
            self.store.memberships[(provider, admin)] = v2.ProviderMembership(
                provider_org_id=provider, user_id=admin, role=v2.ProviderRole.administrator,
                granted_by="ops", granted_at=self.now() - timedelta(days=1))
        self.sessions = Sessions((self.DEV_A, self.DEV_B, self.VIEWER_A, self.CONSUMER_ONLY,
                                  ADMIN_A, ADMIN_B))
        self.records, self.proposals, self.d9 = Records(), Proposals(), D9()
        self.refs = {}
        for provider, n, state, fence, verdict in (
                (self.A, 1, "running", 2, "expand"), (self.A, 2, "running", 1, "hold"),
                (self.A, 3, "approved", 4, "expand"), (self.A, 4, "rolled_back", 3, "rollback"),
                (self.B, 5, "running", 1, "expand")):
            ref, record = policy(provider, n)
            self.refs[n] = ref
            self.records.of(provider)["releases"].append(
                release(ref, record, state=state, fence=fence, verdict=verdict))
            self.d9.put(ref, state, fence)
        self.records.of(self.A)["decisions"].append(
            {"policy_ref": self.refs[4], "decision": "rollback", "reasons": ["error_rate"],
             "evidence_refs": [], "decided_by": "controller",
             "decided_at": "2026-09-28T09:00:00Z"})
        self.records.of(self.A)["variants"].append(
            {"variant_ref": "lab:variant:x", "changes": ["quantization"],
             "comparison": {"outcome": "inconclusive", "optimization_claimed": False}})

    def releases(self, **absent) -> lr.LabReleases:
        ports = {"records": self.records, "proposals": self.proposals, "store": self.d9}
        return lr.LabReleases(self.sessions, self.access, **{**ports, **absent})

    def client(self, *, on_runtime=False, **absent) -> TestClient:
        app, rt = FastAPI(), support.runtime()
        x = self.releases(**absent)
        if on_runtime:
            rt.lab_releases = x
            assert lr.register(app, rt) is x
        else:
            assert lr.register(app, rt, x) is x
        return TestClient(app, raise_server_exceptions=False)

    def proposal(self, n: int = 1, kind: str = "expand", fence: int | None = None, **over):
        fence = self.d9.rows[self.refs[n]].fence if fence is None else fence
        return {"kind": kind, "policy_ref": self.refs[n], "fence": fence, **over}


def call(c, user, method, path, body=None, *, provider=None, headers=None, raw=None):
    head = {"authorization": f"Bearer {token(user)}"} if user else {}
    head.update(headers or {})
    params = {"provider_org_id": provider or FakeWorld.A}
    if raw is not None:
        return c.request(method, path, params=params, content=raw,
                         headers={"content-type": "application/json", **head})
    return c.request(method, path, params=params, json=body, headers=head)


def routes(w):
    return (("GET", lr.RELEASES_PATH, None), ("GET", lr.OPTIMIZATIONS_PATH, None),
            ("POST", lr.RELEASES_PATH + "/proposals", w.proposal()))


PROPOSE = lr.RELEASES_PATH + "/proposals"


# --- mounting ------------------------------------------------------------------------------
def test_lab_releases__nothing_is_mounted_without_the_switch():
    """Oracle: no `rt.lab_releases` (LAB_RELEASES off), no route - a 404, never a stand-in."""
    app = FastAPI()
    assert lr.register(app, support.runtime()) is None
    assert not [r for r in app.routes
                if getattr(r, "path", "").startswith(("/lab/v1/releases", "/lab/v1/optim"))]
    w = World()
    assert call(w.client(on_runtime=True), w.VIEWER_A, "GET", lr.RELEASES_PATH).status_code == 200


# --- LAB-ACCESS -----------------------------------------------------------------------------
def test_lab_releases__every_route_needs_the_session_before_anything_else():
    """Oracle: no token, or an API key, is a 401 on every route and nothing is asked - not even
    the body, so an invalid one is still a 401."""
    w = World()
    c = w.client()
    for method, path, body in routes(w):
        for headers in ({}, {"authorization": f"Bearer {support.TOKEN}"}):
            for sent in (body, {"forged": True}):
                answer = call(c, None, method, path, sent, headers=headers)
                assert (answer.status_code, answer.json()) \
                    == (401, {"refusal": "unauthenticated"}), path
    assert (w.proposals.rows, w.d9.reads) == ([], [])


def test_lab_releases__a_consumer_only_user_is_denied_and_another_provider_is_not_found():
    """Oracle (LAB-ACCESS): a user with no provider membership is a 403 on every route; B's
    member naming A is a 404 on every route; nothing is asked."""
    w = World()
    c = w.client()
    for method, path, body in routes(w):
        answer = call(c, w.CONSUMER_ONLY, method, path, body)
        assert (answer.status_code, answer.json()) == (403, {"refusal": "denied"}), path
        answer = call(c, ADMIN_B, method, path, body)
        assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"}), path
    assert (w.proposals.rows, w.d9.reads) == ([], [])


def test_lab_releases__every_role_reads_and_only_an_administrator_proposes():
    """Oracle: a viewer and a developer read both surfaces and are refused (403) a proposal
    on their provider's release before D9 is read; an administrator's proposal is a 201."""
    w = World()
    c = w.client()
    for user in (w.VIEWER_A, w.DEV_A):
        got = [call(c, user, m, path, body).status_code for m, path, body in routes(w)]
        assert got == [200, 200, 403], user
        answer = call(c, user, "POST", PROPOSE, w.proposal(4, "rollback"))
        assert (answer.status_code, answer.json()) == (403, {"refusal": "denied"})
    assert (w.proposals.rows, w.d9.reads) == ([], [])
    assert call(c, ADMIN_A, "POST", PROPOSE, w.proposal()).status_code == 201


# --- ROLLOUT-PIN: records as stored, proposals only through D9's fence ----------------------
def test_lab_releases__the_records_are_the_providers_own_as_stored():
    """Oracle: releases (pins, cohort, progress, verdict), decisions, proposals and variants
    are the provider's read models verbatim, never another provider's; nothing is cached."""
    w = World()
    c = w.client()
    answer = call(c, w.VIEWER_A, "GET", lr.RELEASES_PATH)
    assert answer.json() == {"data": {"releases": w.records.of(w.A)["releases"],
                                      "decisions": w.records.of(w.A)["decisions"],
                                      "proposals": []}}
    assert answer.headers.get("cache-control") == "no-store"
    assert w.refs[5] not in json.dumps(answer.json())
    assert call(c, w.VIEWER_A, "GET", lr.OPTIMIZATIONS_PATH).json() \
        == {"data": w.records.of(w.A)["variants"]}
    assert call(c, ADMIN_B, "GET", lr.OPTIMIZATIONS_PATH, provider=w.B).json() == {"data": []}
    made = call(c, ADMIN_A, "POST", PROPOSE, w.proposal()).json()
    assert call(c, w.VIEWER_A, "GET", lr.RELEASES_PATH).json()["data"]["proposals"] == [made]


def test_lab_releases__a_proposal_names_the_revision_d9_holds_now():
    """Oracle (ROLLOUT-PIN): the proposal carries the fence the page showed and is stored
    `proposed` at the store's clock; D9's row is not moved by it (an operator decides via
    R2); a fence D9 has moved past - even one the read model still shows - is a 409."""
    w = World()
    c = w.client()
    shown = w.records.of(w.A)["releases"][0]["fence"]
    w.d9.put(w.refs[1], "running", shown + 1)            # D9 moved on since the page was read
    answer = call(c, ADMIN_A, "POST", PROPOSE, w.proposal(fence=shown))
    assert (answer.status_code, answer.json()) == (409, {"refusal": "conflict"})
    assert w.proposals.rows == []
    answer = call(c, ADMIN_A, "POST", PROPOSE, w.proposal(fence=shown + 1))
    assert answer.status_code == 201
    made = answer.json()
    assert {k: made[k] for k in ("kind", "policy_ref", "fence", "state", "decided_at")} == {
        "kind": "expand", "policy_ref": w.refs[1], "fence": shown + 1, "state": "proposed",
        "decided_at": None}
    assert made["proposed_at"] == w.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    assert w.proposals.rows[-1][1]["proposed_by"] == ADMIN_A   # the session's user (0043)
    assert w.d9.rows[w.refs[1]] == r2.Release(state="running", fence=shown + 1,
                                              plan_digest="sha256:" + "9" * 64,
                                              started_at=STARTED)


def test_lab_releases__only_an_expand_verdict_on_a_running_release_can_be_proposed():
    """Oracle (ROLLOUT-PIN): expanding a release whose verdict holds (an inconclusive report)
    or one already approved is a 409; nothing is proposed on a rolled-back release; a
    rollback of a running or approved release is a proposal; one is pending at a time."""
    w = World()
    c = w.client()
    for n, kind in ((2, "expand"), (3, "expand"), (4, "rollback"), (4, "expand")):
        answer = call(c, ADMIN_A, "POST", PROPOSE, w.proposal(n, kind))
        assert (answer.status_code, answer.json()) == (409, {"refusal": "conflict"}), (n, kind)
    assert w.proposals.rows == []
    for n in (2, 3):
        assert call(c, ADMIN_A, "POST", PROPOSE, w.proposal(n, "rollback")).status_code == 201
    again = call(c, ADMIN_A, "POST", PROPOSE, w.proposal(2, "rollback"))
    assert (again.status_code, again.json()) == (409, {"refusal": "conflict"})
    assert len(w.proposals.rows) == 2


def test_lab_releases__another_providers_policy_is_not_found_and_d9_is_not_read():
    """Oracle (LAB-ACCESS): A's administrator naming B's policy revision - D9 holds it - is a
    404, like an unknown one, and D9 is never asked about it."""
    w = World()
    c = w.client()
    other = {**w.proposal(5), "policy_ref": w.refs[5]}
    unknown = {**w.proposal(1), "policy_ref": policy(w.A, 9)[0]}
    for body in (other, unknown):
        answer = call(c, ADMIN_A, "POST", PROPOSE, body)
        assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"})
    assert (w.proposals.rows, w.d9.reads) == ([], [])


def test_lab_releases__an_unknown_policy_is_not_found_whatever_the_role():
    """Oracle (LAB-ACCESS; R4 journey: "existence is not confirmed by role"): a viewer or a
    developer naming another provider's or an unknown policy revision is a 404, as an
    administrator is - never a 403 - and D9 is not read."""
    w = World()
    c = w.client()
    other = {**w.proposal(5), "policy_ref": w.refs[5]}
    unknown = {**w.proposal(1), "policy_ref": policy(w.A, 9)[0]}
    for user in (w.VIEWER_A, w.DEV_A):
        for body in (other, unknown):
            answer = call(c, user, "POST", PROPOSE, body)
            assert (answer.status_code, answer.json()) == (404, {"refusal": "not_found"}), user
    assert (w.proposals.rows, w.d9.reads) == ([], [])


# --- the ports not merged yet, and the body -------------------------------------------------
@pytest.mark.parametrize("absent", ("records", "proposals", "store"))
def test_lab_releases__an_unwired_port_is_unavailable_after_the_access_checks(absent, caplog):
    """Oracle: LAB_RELEASES on before a table merges answers 503 where the port is needed -
    an expected state, not a bug to log - and still 404 for another provider."""
    w = World()
    c = w.client(**{absent: None})
    caplog.set_level("ERROR")
    expected = {"records": (503, 503, 503), "proposals": (503, 200, 503),
                "store": (200, 200, 503)}[absent]
    for (method, path, body), wanted in zip(routes(w), expected):
        assert call(c, ADMIN_A, method, path, body).status_code == wanted, (absent, path)
        assert call(c, ADMIN_B, method, path, body).status_code == 404
    assert caplog.records == []


def test_lab_releases__a_body_is_json_and_exactly_a_proposal():
    """Oracle: a non-JSON body, a kind other than expand/rollback, a fence that is not a
    non-negative integer, a malformed ref, or a body naming a provider or decider is a 422 and
    nothing is asked."""
    w = World()
    c = w.client()
    answer = call(c, ADMIN_A, "POST", PROPOSE, raw=json.dumps(w.proposal()),
                  headers={"content-type": "text/plain"})
    assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"})
    for over in ({"kind": "promote"}, {"fence": -1}, {"fence": "2"}, {"fence": 2.5},
                 {"policy_ref": "lab:policy:x"}, {"provider_org_id": w.B},
                 {"decided_by": ADMIN_A}):
        answer = call(c, ADMIN_A, "POST", PROPOSE, {**w.proposal(), **over})
        assert (answer.status_code, answer.json()) == (422, {"refusal": "invalid"}), over
    assert (w.proposals.rows, w.d9.reads) == ([], [])


def test_lab_releases__a_releases_progress_is_d9s_live_null_only_before_one_is_observed():
    """WR-LIVE-PAGE (R244): `pilot.ReleaseRecords` shows each release's `progress` as D9's Live
    of that revision (`PgReleaseStore.live(policy_ref)`, 0054) in port.ts's shape - per arm
    counts and p99, quality coverage, the candidate arm's spend in its own unit (R246), health,
    the database clock as UTC - and null only while Live is None (nothing assigned yet). No
    per-serving tally is readable yet: `assignments` is empty, never invented."""
    import asyncio

    from infrx.gateway import pilot
    from infrx.lab.workers.__main__ import plan_key
    from infrx.media.store import InMemoryObjectStore
    from infrx.state.lab_rollout import Release, ReleaseListing
    from tests.r.control import test_control as r2w

    objects, lives, asked = InMemoryObjectStore(), {}, []
    refs = [r2w.POLICY_REF, r2w.POLICY_REF.replace("sha256:", "sha256:0", 1)[:-1]]
    asyncio.run(objects.put_if_absent(plan_key(r2w.P, r2w.POLICY.policy_id),
                                      r2w.plan().model_dump_json().encode(), "x"))

    class D9:
        async def releases_in(self, states=(), *, provider_org_id):
            return [ReleaseListing(policy_id=r2w.POLICY.policy_id, provider_org_id=r2w.P,
                                   endpoint_id="e", policy_ref=ref, latest_decision=None,
                                   release=Release(state="running", fence=1, plan_digest="d",
                                                   started_at=r2w.START)) for ref in refs]

        async def live(self, policy_ref):
            asked.append(policy_ref)
            return lives.get(policy_ref)

    class D7:
        async def resolve(self, ref, *, provider_org_id):
            return r2w.POLICY

    records = pilot.ReleaseRecords(D9(), D7(), objects, None)   # variants: unread here
    assert [r["progress"] for r in asyncio.run(records.releases(r2w.P))] == [None, None]
    assert asked == refs, "each release's Live is read for its own revision"
    lives[refs[0]] = r2w.live(requests=40, errors_=2, p99=950, covered=7, spent="3.50000000",
                              unit="PROVIDER_USD", healthy=False)
    first, second = [r["progress"] for r in asyncio.run(records.releases(r2w.P))]
    assert second is None
    assert first == {
        "observed_until": "2026-09-28T09:55:00Z",
        "baseline": {"requests": 9_000, "errors": 0, "p99_ms": 8_000},
        "candidate": {"requests": 40, "errors": 2, "p99_ms": 950},
        "quality_covered": 7, "spent": {"amount": "3.50000000", "unit": "PROVIDER_USD"},
        "candidate_healthy": False, "assignments": []}, first
