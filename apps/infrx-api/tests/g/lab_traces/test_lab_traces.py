#!/usr/bin/env python3
"""WR-V1M-2: `/lab/v1/traces`, the provider's trace read - TRACE-TENANT and LAB-ACCESS.

    uv run --frozen pytest -q tests/g/lab_traces
    INFRX_D_TASK=l4 uv run --frozen pytest -q tests/g/lab_traces      # + the PostgreSQL half

Cases taking `world` run on the fake store and, marked `pg`, on PostgreSQL (`INFRX_D_TASK=l4`):
there `LabAccess` is over `PgAccessStore`, the grants are written through lab-sql's RPCs and the
provider's serving versions are read by `PgServing` from the registry the seed built (provider
A = NemoStation, its serving version `checks_credit.SERVING`). The projection and the deletion
ledger are T2I's and T3's in-memory stand-ins (read like `ReplacingMergeTree ... FINAL`); the
ClickHouse adapter's own query runs in `test_lab_traces_stack.py` when a stack is up.
"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrx.contracts import errors
from infrx.contracts.v2 import records as v2
from infrx.gateway.routes import lab_traces as lt
from infrx.media.store import InMemoryObjectStore
from infrx.traces import retention, ship

from .. import support
from ...d import checks_credit as cc
from ...d import pgharness
from ...l.access.conftest import CASE
from ...l.access.worlds import FakeWorld, PgWorld
from ...t.feedback.test_feedback import MemoryProjection as MemoryFeedback
from ...t.retention.test_retention import MemoryStore
from ...t.ship.test_ship import MemoryProjection

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
P = lt.TRACES_PATH
FOREIGN_SERVING = "5e000000-0000-4000-8000-0000000000bb"
FAKE_SERVING = "5e000000-0000-4000-8000-0000000000aa"
BOTH_CONTENT = (v2.DataCategory.request_content, v2.DataCategory.response_content)
METADATA = {*lt.METADATA, "model_id", "access"}
GRANTED = METADATA | set(lt.GRANTED)


def run(coro):
    return asyncio.run(coro)


def token(user: str) -> str:
    return f"eyJ0.{user.replace('-', '')}.c2ln"


class Sessions:
    def __init__(self, w) -> None:
        self.users = {token(u): u for u in (w.DEV_A, w.DEV_B, w.VIEWER_A, w.BOTH,
                                            w.CONSUMER_ONLY)}

    async def user_id(self, value: str) -> str:
        if value not in self.users:
            raise errors.InvalidApiKey("not a live session")
        return self.users[value]


class Serving:
    """`ProviderServing` for the fake world: A's one serving version, B's none."""

    def __init__(self, w) -> None:
        self.by_provider = {w.A: {FAKE_SERVING: w.MODELS[w.A]}, w.B: {}}

    async def serving(self, provider_org_id):
        return self.by_provider.get(provider_org_id, {})


class Rows(MemoryProjection):
    """T2I's in-memory projection plus the two reads `TraceRows` names, recording them."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple] = []

    def _final(self):
        final = {}
        for row in self.inserted:
            key = (row.org_id, row.trace_id)
            if key not in final or row.content_stored >= final[key].content_stored:
                final[key] = row
        return list(final.values())

    async def page(self, serving_version_ids, before, limit):
        self.asked.append(("page", tuple(serving_version_ids), before, limit))
        rows = sorted((r for r in self._final() if r.serving_version_id in serving_version_ids
                       and (before is None or (r.started_at, r.trace_id) < before)),
                      key=lambda r: (r.started_at, r.trace_id), reverse=True)
        return rows[:limit]

    async def request(self, serving_version_ids, request_id):
        self.asked.append(("request", tuple(serving_version_ids), request_id))
        uuid.UUID(request_id)             # ClickHouse refuses a non-UUID {request:UUID}
        return [r for r in self._final() if r.serving_version_id in serving_version_ids
                and r.request_id == request_id]


@dataclasses.dataclass
class Traces:
    """One world's trace side: the rows T2I shipped and what T3 recorded, for A's serving
    version (`mine`) and another provider's."""

    w: object
    mine: str
    rows: Rows
    objects: InMemoryObjectStore
    policy: retention.Retention
    ids: dict

    async def ship(self, name, org, *, age, serving=None, content=b"", loss="none"):
        request_id = f"{len(self.ids) + 1:08x}-0000-4000-8000-000000000001"
        trace_id = f"seg-{len(self.ids):04d}:0"
        key = None
        if content:
            key = ship.content_key(org, trace_id)
            await self.objects.put_if_absent(key, content, "application/json")
        await self.rows.insert([ship.TraceRow(
            org_id=org, trace_id=trace_id, request_id=request_id,
            key_id="3c3c3c3c-0000-4000-8000-000000000003", mode="full",
            started_at=NOW - age, completed_at=NOW - age + timedelta(seconds=2),
            loss_reason=loss, content_complete=loss == "none", content_bytes=len(content),
            content_key=key, content_stored=bool(content), request_schema_version=1,
            model_revision="nemostation/marlin-2b@r1", price_version="pv1",
            serving_version_id=serving or self.mine, rate_card_version="rc1",
            policy_version="dap1")])
        self.ids[name] = request_id


def traces(w) -> Traces:
    """Rows on A's deployment from both consumer organizations (C1 grants A, C2 does not),
    one on another provider's, one lost capture, one deleted, one past its content bound and
    one past its metadata bound (13 months)."""
    mine = cc.SERVING if isinstance(w, PgWorld) else FAKE_SERVING
    rows, fb = Rows(), MemoryFeedback()
    objects = InMemoryObjectStore()
    policy = retention.Retention(MemoryStore(rows, fb), rows, fb, objects, clock=lambda: NOW)
    t = Traces(w, mine, rows, objects, policy, {})
    run(t.ship("granted", w.C1, age=timedelta(minutes=5), content=b'{"prompt":"c1"}'))
    run(t.ship("ungranted", w.C2, age=timedelta(minutes=4), content=b'{"prompt":"c2"}'))
    run(t.ship("lost", w.C1, age=timedelta(minutes=3), loss="spool_full"))
    run(t.ship("foreign", w.C1, age=timedelta(minutes=2), serving=FOREIGN_SERVING,
               content=b'{"prompt":"foreign"}'))
    run(t.ship("deleted", w.C1, age=timedelta(minutes=1), content=b'{"prompt":"gone"}'))
    run(t.ship("expired", w.C1, age=timedelta(days=policy.content_days, minutes=1),
               content=b'{"prompt":"old"}'))
    run(t.ship("ancient", w.C1, age=timedelta(days=400), content=b'{"prompt":"older"}'))
    run(policy.delete(w.C1, t.ids["deleted"], "customer"))
    return t


def grant_content(w, grantor, provider) -> None:
    """The next version of the pair's grant: both content categories, provider_sharing."""
    if isinstance(w, PgWorld):
        w._rpc("lab_put_access_grant", {
            "actor_user_id": w.owner[grantor], "grantor_org_id": grantor,
            "recipient_provider_org_id": provider, "model_ids": [w.MODELS[provider]],
            "categories": [c.value for c in BOTH_CONTENT], "purposes": ["provider_sharing"],
            "retention_days": 30})
        return
    prev = w.store.grants[(grantor, provider)]
    w.store.put_grant(prev.model_copy(update={
        "version": prev.version + 1, "categories": BOTH_CONTENT, "effective_at": w.now(),
        "purposes": (v2.DataPurpose.provider_sharing,)}))


def serving(w):
    if isinstance(w, PgWorld):
        from infrx.state.jobstore import connector
        return lt.PgServing(connector(pgharness.dsn(CASE)))
    return Serving(w)


def client(w, t, *, on_runtime=False):
    app = FastAPI()
    rt = support.runtime()
    deps = lt.LabTraces(Sessions(w), w.access, serving(w), t.rows, t.policy)
    if on_runtime:
        rt.lab_traces = deps
        assert lt.register(app, rt) is deps
    else:
        assert lt.register(app, rt, deps) is deps
    return TestClient(app, raise_server_exceptions=False)


def get(c, user, provider, path="", **params):
    head = {"authorization": f"Bearer {token(user)}"} if user else {}
    return c.get(P + path, params={"provider_org_id": provider, **params}, headers=head)


def listed(c, user, provider, **params):
    answer = get(c, user, provider, **params)
    assert answer.status_code == 200, answer.text
    return answer.json()


def by_request(t, items):
    names = {v: k for k, v in t.ids.items()}
    return {names[item["request_id"]]: item for item in items}


# --- mounting and the session --------------------------------------------------------------
def test_lab_traces__nothing_is_mounted_without_traces():
    """Oracle: no `rt.lab_traces` (LAB_TRACES off), no route."""
    app = FastAPI()
    assert lt.register(app, support.runtime()) is None
    assert not [r for r in app.routes if getattr(r, "path", "").startswith(P)]
    w = FakeWorld()
    assert listed(client(w, traces(w), on_runtime=True), w.DEV_A, w.A)["data"]


def test_lab_traces__identity_and_membership_before_any_row(world):
    """Oracle (LAB-ACCESS): no session 401; consumer-only 403; another provider's member 404;
    the viewer (aggregate health only) 403 - and the projection is never asked."""
    w = world
    t = traces(w)
    c = client(w, t)
    detail = "/" + t.ids["granted"]
    for path in ("", detail):
        assert get(c, None, w.A, path).status_code == 401
        assert get(c, w.CONSUMER_ONLY, w.A, path).json() == {"refusal": "denied"}
        assert get(c, w.CONSUMER_ONLY, w.B, path).status_code == 403
        assert get(c, w.DEV_B, w.A, path).json() == {"refusal": "not_found"}
        assert get(c, w.VIEWER_A, w.A, path).json() == {"refusal": "denied"}
    assert t.rows.asked == []


# --- TRACE-TENANT ---------------------------------------------------------------------------
def test_lab_traces__a_provider_reads_its_own_deployments_requests_only(world):
    """Oracle: A lists the requests on A's serving versions - lost capture included, the
    deleted one, the one past its metadata bound and another provider's never; B (no deployment of its own here) lists none."""
    w = world
    t = traces(w)
    c = client(w, t)
    items = by_request(t, listed(c, w.DEV_A, w.A)["data"])
    assert set(items) == {"granted", "ungranted", "lost", "expired"}
    assert items["lost"]["loss_reason"] == "spool_full"
    assert all(item["serving_version_id"] == t.mine for item in items.values())
    assert listed(c, w.DEV_B, w.B) == {"data": [], "next_cursor": None}
    assert ("page", (t.mine,), None, lt.DEFAULT_LIMIT) in t.rows.asked


def test_lab_traces__without_a_content_grant_rows_are_metadata_only(world):
    """Oracle: a grantor with no current provider_sharing grant for BOTH content categories
    (C2 grants nobody here; C1's seed grant names request_content only) yields metadata -
    no organization, key, size or content - in the list and the detail."""
    w = world
    t = traces(w)
    c = client(w, t)
    for item in listed(c, w.DEV_A, w.A)["data"]:
        assert set(item) == METADATA and item["access"] == "metadata"
        assert w.C1 not in item.values() and w.C2 not in item.values()
    detail = get(c, w.DEV_A, w.A, "/" + t.ids["granted"]).json()
    assert set(detail) == METADATA and "content" not in detail


def test_lab_traces__a_current_grant_releases_content_per_grantor(world):
    """Oracle: once C1 grants A both content categories for provider_sharing, C1's rows carry
    their grantor and content - C2's stay metadata-only; the detail returns C1's content."""
    w = world
    t = traces(w)
    grant_content(w, w.C1, w.A)
    c = client(w, t)
    items = by_request(t, listed(c, w.DEV_A, w.A)["data"])
    assert items["granted"]["access"] == "content" and set(items["granted"]) == GRANTED
    assert (items["granted"]["grantor_org_id"], items["granted"]["content_available"]) \
        == (w.C1, True)
    assert set(items["ungranted"]) == METADATA
    detail = get(c, w.DEV_A, w.A, "/" + t.ids["granted"]).json()
    assert (detail["access"], detail["content"]) == ("content", '{"prompt":"c1"}')
    assert detail["grant_ref"] == items["granted"]["grant_ref"]
    assert detail["grant_ref"].startswith(f"lab:grant:{w.A}:")
    assert "content" not in get(c, w.DEV_A, w.A, "/" + t.ids["ungranted"]).json()
    assert get(c, w.BOTH, w.A, "/" + t.ids["granted"]).json()["grantor_org_id"] == w.C1


def test_lab_traces__a_revoked_grant_is_metadata_only_on_the_next_call(world):
    """Oracle (LAB-ACCESS): the call after the grantor's revocation carries no content."""
    w = world
    t = traces(w)
    grant_content(w, w.C1, w.A)
    c = client(w, t)
    assert get(c, w.DEV_A, w.A, "/" + t.ids["granted"]).json()["access"] == "content"
    w.revoke_grant(w.C1, w.A)
    detail = get(c, w.DEV_A, w.A, "/" + t.ids["granted"]).json()
    assert detail["access"] == "metadata" and "content" not in detail


def test_lab_traces__deleted_and_expired_content_stay_gone(world):
    """Oracle (T3): a deleted request is a 404 even for a granted provider, and content past
    its bound is unavailable while its metadata stays."""
    w = world
    t = traces(w)
    grant_content(w, w.C1, w.A)
    c = client(w, t)
    assert get(c, w.DEV_A, w.A, "/" + t.ids["deleted"]).json() == {"refusal": "not_found"}
    expired = get(c, w.DEV_A, w.A, "/" + t.ids["expired"]).json()
    assert (expired["access"], expired["content_available"], expired["content"]) \
        == ("content", False, None)
    lost = get(c, w.DEV_A, w.A, "/" + t.ids["lost"]).json()
    assert (lost["content_available"], lost["content"]) == (False, None)


def test_lab_traces__a_detail_outside_the_providers_deployments_is_not_found(world):
    """Oracle: another provider's request, an unknown id or a malformed one is a 404."""
    w = world
    t = traces(w)
    grant_content(w, w.C1, w.A)
    c = client(w, t)
    for request_id in (t.ids["foreign"], t.ids["ancient"], "0f000000-0000-4000-8000-00000000000f", "x' OR 1=1"):
        assert get(c, w.DEV_A, w.A, "/" + request_id).json() == {"refusal": "not_found"}


# --- pages ----------------------------------------------------------------------------------
def test_lab_traces__pages_are_bounded_and_cursors_are_the_servers():
    """Oracle: newest first, `limit` rows a page, a server cursor to the next page and none
    after the last; a forged cursor or an out-of-range limit is a 422."""
    w = FakeWorld()
    t = traces(w)
    c = client(w, t)
    first = listed(c, w.DEV_A, w.A, limit=4)    # deleted, lost, ungranted, granted read
    assert list(by_request(t, first["data"])) == ["lost", "ungranted", "granted"]
    assert w.C1 not in base64.urlsafe_b64decode(first["next_cursor"] + "==").decode()
    second = listed(c, w.DEV_A, w.A, limit=4, cursor=first["next_cursor"])
    assert second == {"data": [by_request(t, second["data"])["expired"]], "next_cursor": None}
    naive = base64.urlsafe_b64encode(json.dumps(["2026-09-27T12:00:00", "x:0"]).encode())
    for bad in ({"cursor": "not-a-cursor"}, {"cursor": first["next_cursor"][:-2]},
                {"cursor": naive.decode()},
                {"limit": 0}, {"limit": lt.MAX_LIMIT + 1}, {"limit": "x"}):
        assert get(c, w.DEV_A, w.A, **bad).json() == {"refusal": "invalid"}, bad
