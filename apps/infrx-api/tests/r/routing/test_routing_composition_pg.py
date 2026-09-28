#!/usr/bin/env python3
"""WR-R1-3-C on the r1 task-local PostgreSQL: the gateway's own composition
(`pilot.connection_pool` + `pilot._rollouts`, as `adapters_from_env` builds them with
`ROLLOUT_ROUTING` on) over a `DATABASE_URL` that logs in as `infrx_runtime` - a dedicated
login, so the pool never `set role` (R127) and SR-R1-1's functions answer. The world is
`test_routing_pg.py`'s (D8's requests world). A 100% canary: C1 is admitted on the
candidate's R62 pin and D9 records the one assignment; a shadow release's duplicate is the
composed `NoShadows` refusal (counted `shadow_failed`, nothing runs, nothing is charged).

T2I/G8's pattern: outside the mutant runner; the fake-level oracle is
`tests/g/test_startup.py::test_rollout_routing__the_router_is_r1_over_d9_on_the_runtime_...`
and its `rollout_router_*` mutants.

    INFRX_D_TASK=r1 uv run --frozen pytest -q tests/r/routing/test_routing_composition_pg.py
"""
from __future__ import annotations

import dataclasses

from infrx.gateway import pilot

from tests.g import support

from ...d import pgharness
from ...d import test_d7_lab_data as t
from ...d import test_d8_requests as q
from ...d import test_d9_rollout as d9
from .test_routing_pg import ALIAS, DB, assignments, conn, hooked, pytestmark  # noqa: F401
from .test_routing import auth

PASSWORD = "r1-local-only"


def composed(conn):
    conn.execute(f"alter role infrx_runtime with login password '{PASSWORD}'")
    dsn = pgharness.dsn(DB).replace(f"postgres:{pgharness.PASSWORD}@",
                                    f"infrx_runtime:{PASSWORD}@")
    settings = support.settings(database_url=dsn, deployment=dataclasses.replace(
        support.BUILD, rollout_routing=True))
    _, connect = pilot.connection_pool(settings)          # closed: one configured connection
    return pilot._rollouts(settings, connect)["rollouts"]


def test_r1_pg_the_composed_router_admits_the_cohort_on_the_runtime_login(conn) -> None:
    ref, body = q.launch(conn, 31, weights=(10_000,))
    try:
        router = composed(conn)
        c1 = auth(q.l2.org(conn, q.C1))
        rid = q.uid(1, 0x5e)
        assert hooked(router, c1, request_id=rid).model_revision == f"{ALIAS}@{q.W['label_2']}"
        assert assignments(conn, rid) == [(body["candidates"][0]["serving_ref"], "cohort")]
        who = conn.execute("select usename from pg_stat_activity where usename = "
                           "'infrx_runtime'").fetchall()
        assert who == []                                  # each call closed its connection
    finally:
        q.ok(conn, d9.MOVE, d9.args(body["policy_id"], "stop", 1))


def test_r1_pg_a_composed_shadow_duplicate_is_refused_and_counted(conn) -> None:
    body = {**q.policy(32, weights=(0,)), "mode": "shadow"}
    ref = t.publish(conn, body)
    q.ok(conn, "lab_release_start", {"provider_org_id": q.NEMO, "policy_ref": ref,
                                     "plan_digest": "sha256:" + "a3" * 32, "decided_by": q.DEV,
                                     "reason": "shadow"})
    conn.execute("update infrx.lab_rollouts set shadow_limit = 1 where policy_id = %s",
                 (body["policy_id"],))
    jobs = "select count(*) from infrx.jobs"
    before = conn.execute(jobs).fetchone()
    try:
        router = composed(conn)
        rid = q.uid(2, 0x5e)
        assert hooked(router, auth(q.l2.org(conn, q.C1)), request_id=rid).model_revision == ALIAS
        assert assignments(conn, rid) == [(body["baseline_ref"], "cohort")]
        counts = {arm: n for (_policy, arm), n in router.counts.items()}
        assert counts == {"baseline": 1, "shadow_failed": 1}, counts
        assert conn.execute(jobs).fetchone() == before
    finally:
        q.ok(conn, d9.MOVE, d9.args(body["policy_id"], "stop", 1))
