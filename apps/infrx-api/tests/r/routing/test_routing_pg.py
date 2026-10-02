#!/usr/bin/env python3
"""R1's real half (WR-R1-3): `Router(PgRoutingReleases(<infrx_runtime login>), ShadowRunner)`
over D9 + SR-R1-1 (0033/0039/0043: `release_active`, `release_eligible`,
`record_rollout_assignment`) on the r1 task-local PostgreSQL. World: D8's requests world
(`tests/d/test_d8_requests.py`: NEMO's alias listed on its prod endpoint, L3's candidate
revision published, C1 holding a current provider_sharing grant to NEMO, BOTH none).

T2I/G8's pattern: outside the mutant runner; the oracles are `test_routing.py`'s mutants.

    INFRX_D_TASK=r1 uv run --frozen pytest -q tests/r/routing/test_routing_pg.py
"""
from __future__ import annotations

import asyncio
import os

import psycopg
import pytest
from infrx.rollouts import routing
from infrx.state import migrations
from infrx.state.lab_rollout import PgRoutingReleases

from ...d import pgharness
from ...d import test_d7_lab_data as t
from ...d import test_d8_requests as q
from ...d import test_d9_rollout as d9
from .test_routing import Shadows, answer, auth, request

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "r1" else \
    "PostgreSQL only on the r1 task-local key (INFRX_D_TASK=r1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_r1"
ALIAS = q.ALIAS


@pytest.fixture
def runtime_dsn(conn):
    """A real `infrx_runtime` session (pgharness.login), not `set session authorization`,
    which needs a superuser and so is refused 42501 on the Supabase image."""
    with pgharness.login("infrx_runtime", DB) as dsn:
        yield dsn


def runtime_login(dsn):
    """The runtime's own login (0021 `infrx_runtime`, D10): no `set role service_role`, only
    what the role holds - the three SR-R1-1 functions are EXECUTE infrx_runtime only."""
    async def connect():
        return await psycopg.AsyncConnection.connect(dsn, autocommit=True,
                                                     prepare_threshold=None)
    return connect


@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        q.seed(connection)
        yield connection


def hooked(router, who, model=ALIAS, request_id=q.uid(1, 0x4e)):
    """One fresh admission through R1's hook (shadows awaited): the request admitted."""
    async def go():
        seen = []

        async def accept(_who, asked, _idem):
            seen.append(asked)
            return answer()
        await routing.hook(accept, router)(who, request(model, request_id), None)
        await asyncio.gather(*router.tasks)
        return seen[0]
    return asyncio.run(go())


def assignments(conn, request_id) -> list:
    return conn.execute("select serving_ref, pinned_by from infrx.lab_rollout_assignments "
                        "where request_id = %s", (request_id,)).fetchall()


def test_r1_pg_the_cohort_holds_pins_win_and_revocation_is_immediate(conn, runtime_dsn) -> None:
    """A 100% canary: C1 (eligible) is admitted on the candidate's R62 pin, again on a repeat,
    and its assignment is D9's one row; an explicit pin is served as asked; BOTH (no grant) is
    ineligible and recorded nowhere; once C1's grant is revoked its next request is on the
    baseline at once and records nothing."""
    ref, body = q.launch(conn, 21, weights=(10_000,))
    try:
        router = routing.Router(PgRoutingReleases(runtime_login(runtime_dsn)), Shadows())
        c1, both = auth(q.l2.org(conn, q.C1)), auth(q.l2.org(conn, q.BOTH))
        pin = f"{ALIAS}@{q.W['label_2']}"
        first, second = q.uid(1, 0x4e), q.uid(2, 0x4e)
        assert hooked(router, c1, request_id=first).model_revision == pin
        assert hooked(router, c1, request_id=first).model_revision == pin     # a retry
        assert hooked(router, c1, request_id=second).model_revision == pin    # the same cohort
        candidate = body["candidates"][0]["serving_ref"]
        assert assignments(conn, first) == [(candidate, "cohort")] == assignments(conn, second)
        explicit = f"{ALIAS}@2026-09-01"
        assert hooked(router, c1, explicit, q.uid(3, 0x4e)).model_revision == explicit
        assert hooked(router, both, request_id=q.uid(4, 0x4e)).model_revision == ALIAS
        assert assignments(conn, q.uid(3, 0x4e)) == [] == assignments(conn, q.uid(4, 0x4e))
        q.ok(conn, "lab_revoke_access_grant", {"actor_user_id": q.C1,
                                               "grantor_org_id": q.l2.org(conn, q.C1),
                                               "recipient_provider_org_id": q.NEMO})
        assert hooked(router, c1, request_id=q.uid(5, 0x4e)).model_revision == ALIAS
        assert assignments(conn, q.uid(5, 0x4e)) == []
        counts = {arm: n for (_policy, arm), n in router.counts.items()}
        assert counts == {"candidate": 3, "ineligible": 2}, counts
        q.ok(conn, "lab_put_access_grant", q.l2.scope(               # C1 grants again
            conn, purposes=["provider_sharing", "training", "external_judging"],
            categories=["request_content", "response_content"]))
    finally:                                   # the endpoint is free for the next case
        q.ok(conn, d9.MOVE, d9.args(body["policy_id"], "stop", 1))


def test_r1_pg_a_shadow_duplicate_never_settles(conn, runtime_dsn) -> None:
    """A shadow release (bound 1): the user's request is admitted unchanged on the baseline and
    recorded once; the provider-funded duplicate runs on the candidate's pin and writes
    nothing - no job, hold, ledger or journal row appears for it."""
    body = {**q.policy(22, weights=(0,)), "mode": "shadow"}
    ref = t.publish(conn, body)
    q.ok(conn, "lab_release_start", {"provider_org_id": q.NEMO, "policy_ref": ref,
                                     "plan_digest": "sha256:" + "a2" * 32, "decided_by": q.DEV,
                                     "reason": "shadow"})
    conn.execute("update infrx.lab_rollouts set shadow_limit = 1 where policy_id = %s",
                 (body["policy_id"],))
    money = ("select (select count(*) from infrx.jobs), (select count(*) from "
             "infrx.credit_wallet_holds), (select count(*) from infrx.credit_ledger)")
    before = conn.execute(money).fetchone()
    shadows = Shadows()
    router = routing.Router(PgRoutingReleases(runtime_login(runtime_dsn)), shadows)
    rid = q.uid(6, 0x4e)
    assert hooked(router, auth(q.l2.org(conn, q.C1)), request_id=rid).model_revision == ALIAS
    assert [(s, r.model_revision) for s, r in shadows.runs] == [
        (body["candidates"][0]["serving_ref"], f"{ALIAS}@{q.W['label_2']}")]
    assert assignments(conn, rid) == [(body["baseline_ref"], "cohort")]
    assert conn.execute(money).fetchone() == before
