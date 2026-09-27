#!/usr/bin/env python3
"""B1's drills against the real D7 store: `PgLabDataStore` on the task-local PostgreSQL
(0001-0029, D7's world: C1's grant to NEMO for provider_sharing + training and one source)
and the provider_dev endpoint over HTTP on the b1 model-fake port, through
`HttpDevEndpoint`. The content objects are `InMemoryObjectStore`.

Drills (EVAL-DURABLE): a worker killed after the endpoint charged and before it finished;
a connection lost after the charge (the same key replays, no second debit); a durable cancel
mid-batch; duplicate delivery; an exhausted dev wallet, funded again; data revoked mid-run.
Outside the mutant runner (N2/T2I's pattern); the oracles are the fake-world cases' mutants.

    INFRX_D_TASK=b1 uv run --frozen pytest -q tests/b/runner/test_runner_pg.py
"""
from __future__ import annotations

import asyncio
import os

import psycopg
import pytest
from infrx.contracts.tasklocal import local_services
from infrx.datasets.imports import sample_key
from infrx.evaluation import runner
from infrx.evaluation.runner import HttpDevEndpoint, Limits, Runner
from infrx.media.store import InMemoryObjectStore
from infrx.state import migrations
from infrx.state.jobstore import connector
from infrx.state.lab_data import PgLabDataStore

from ...d import pgharness
from ...d import test_d7_lab_data as d7
from ...d import test_l2sql_access as l2
from .world import (DEPLOYMENT, EVALUATOR, NEMO, RATE_CARD, SPEC, Crash, DevWallet, content,
                    eval_run, harness, manifest, serve, uid)

_reason = pgharness.unavailable() if os.environ.get("INFRX_D_TASK") == "b1" else \
    "PostgreSQL only on the b1 task-local key (INFRX_D_TASK=b1)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_b1"
PORT = local_services("b1")["model-fake"].host_port           # 57521
KEY = "provider-dev-test-key"
LIMITS = Limits(lease_s=30, max_attempts=3, dispatch_retries=2, concurrency=2)
N = 4


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def world():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as conn:
        d7.seed(conn)
        yield conn, PgLabDataStore(connector(pgharness.dsn(DB)))


class Case:
    """One run of N samples over the seeded grant, with its own dev wallet and endpoint."""

    def __init__(self, world, n: int, funded: str = "1000") -> None:
        self.conn, self.store = world
        self.objects = InMemoryObjectStore()
        self.manifest = manifest(N, d7.W["grant"], d7.W["source"], dataset=n)
        for i, sample in enumerate(self.manifest["samples"], 1):
            run(self.objects.put_if_absent(sample_key(NEMO, sample["content_digest"]),
                                           content(i), "application/json"))
        dataset = run(self.store.publish(self.manifest, provider_org_id=NEMO, actor="dev@nemo"))
        harness_ref = run(self.store.publish(harness(harness_id=uid(n, 0xa7)),
                                             provider_org_id=NEMO, actor="dev@nemo"))
        self.frozen = run(runner.freeze(self.store, eval_run(dataset, harness_ref, run=n),
                                        evaluator=SPEC, provider_org_id=NEMO, actor="dev@nemo"))
        self.run_id = self.frozen.run.run_id
        self.wallet = DevWallet(funded)
        self.ids = sorted(s["sample_id"] for s in self.manifest["samples"])

    def runner(self, worker="w", endpoint=None) -> Runner:
        return Runner(self.store, self.objects, endpoint or self.endpoint(), DEPLOYMENT,
                      worker_id=worker, limits=LIMITS)

    def endpoint(self) -> HttpDevEndpoint:
        return HttpDevEndpoint(f"http://127.0.0.1:{PORT}", api_key=KEY, model="marlin-dev",
                               rate_card=RATE_CARD)

    def rows(self, sql: str) -> list[tuple]:
        return self.conn.execute(sql, (self.run_id,)).fetchall()

    def results(self) -> list[tuple]:
        return self.rows("select case_id::text, evaluator_ref, attempt from "
                         "infrx.lab_eval_results where run_id = %s order by case_id")


@pytest.fixture
def endpoint():
    holder = {}

    def start(wallet: DevWallet):
        holder["server"] = serve(wallet, PORT, KEY)
    yield start
    if "server" in holder:
        holder["server"].shutdown()
        holder["server"].server_close()


def per_case():
    return RATE_CARD.debit(900, 1000)


def test_b1_pg_duplicate_delivery_scores_each_case_once_and_costs_match_the_wallet(
        world, endpoint) -> None:
    c = Case(world, 1)
    endpoint(c.wallet)

    async def both():
        return await asyncio.gather(c.runner("a").run(c.frozen), c.runner("b").run(c.frozen))

    run(both())
    report = run(c.runner("c").run(c.frozen))
    assert report["state"] == "succeeded" and report["cases"] == {"done": N}
    assert [(r[0], r[1], r[2]) for r in c.results()] == [(i, EVALUATOR, 1) for i in c.ids]
    assert len(c.wallet.calls) == N and len({x["key"] for x in c.wallet.calls}) == N
    assert report["costs"] == {"CREDIT": str(c.wallet.debited)}
    assert c.wallet.debited == RATE_CARD.debit(900 * N, 1000 * N)


def test_b1_pg_a_worker_killed_after_the_charge_recovers_as_a_new_attempt(world, endpoint) -> None:
    """The kill between the endpoint's answer and the finish: the lease expires on the DB
    clock, `lab_recover` puts the case back, attempt 2 finishes it; the killed attempt is
    `expired` with no cost recorded while the wallet shows both debits (the gap is visible,
    bounded by max_attempts)."""
    c = Case(world, 2)
    endpoint(c.wallet)

    class Dying(HttpDevEndpoint):
        async def complete(self, **request):
            await super().complete(**request)
            raise Crash("killed")

    dying = Dying(f"http://127.0.0.1:{PORT}", api_key=KEY, model="m", rate_card=RATE_CARD)
    with pytest.raises(Crash):
        run(Runner(c.store, c.objects, dying, DEPLOYMENT, worker_id="w",
                   limits=Limits(30, 3, 2, 1)).run(c.frozen))
    d7.advance(c.conn, 31)
    assert run(c.store.recover()) >= 1
    report = run(c.runner("w2").run(c.frozen))
    assert report["cases"] == {"done": N}
    assert [r[2] for r in c.results()] == [2, 1, 1, 1]
    assert c.rows("select state, cost_value from infrx.lab_eval_attempts where run_id = %s "
                  "and attempt = 1 order by case_id")[0] == ("expired", None)
    assert c.wallet.debited == RATE_CARD.debit(900 * (N + 1), 1000 * (N + 1))
    assert report["costs"] == {"CREDIT": str(RATE_CARD.debit(900 * N, 1000 * N))}


def test_b1_pg_a_lost_answer_is_resent_under_its_key_without_a_second_debit(
        world, endpoint) -> None:
    c = Case(world, 3)
    endpoint(c.wallet)
    c.wallet.crash_after_charge = True
    original = c.wallet.reply

    def once(**request):
        try:
            return original(**request)
        finally:
            c.wallet.crash_after_charge = False
    c.wallet.reply = once
    report = run(Runner(c.store, c.objects, c.endpoint(), DEPLOYMENT, worker_id="w",
                        limits=Limits(30, 3, 2, 1)).run(c.frozen))
    assert report["cases"] == {"done": N}
    assert c.wallet.calls[0]["key"] == c.wallet.calls[1]["key"]
    assert len(c.wallet.calls) == N + 1 and len(c.wallet.replies) == N
    assert report["costs"] == {"CREDIT": str(c.wallet.debited)}


def test_b1_pg_a_durable_cancel_mid_batch_stops_spending(world, endpoint) -> None:
    c = Case(world, 4)
    endpoint(c.wallet)
    dsn = pgharness.dsn(DB)

    def answer(prompt):
        if prompt.endswith("q2"):
            with psycopg.connect(dsn, autocommit=True) as other:
                l2.call(other, "lab_cancel_run", {"provider_org_id": NEMO, "run_id": c.run_id})
        return "a" + prompt.split("q")[-1]

    c.wallet.answer = answer
    report = run(Runner(c.store, c.objects, c.endpoint(), DEPLOYMENT, worker_id="w",
                        limits=Limits(30, 3, 2, 1)).run(c.frozen))
    assert report["state"] == "cancelled" and report["stopped"] == "cancelled"
    assert [r[0] for r in c.results()] == c.ids[:1] and len(c.wallet.calls) == 2
    assert report["abandoned"] == [c.ids[1]] and report["unrecorded"] == {"CREDIT": str(per_case())}


def test_b1_pg_an_exhausted_dev_wallet_stops_then_resumes_when_funded(world, endpoint) -> None:
    c = Case(world, 5, funded="0")
    endpoint(c.wallet)
    report = run(c.runner().run(c.frozen))
    assert report["stopped"] == "wallet_exhausted" and c.results() == []
    assert sum(report["cases"].values()) == N and "done" not in report["cases"]
    assert not c.wallet.balance.is_negative and c.wallet.debited.is_zero
    c.wallet.balance = type(c.wallet.balance)("1000")          # an operator allocation
    d7.advance(c.conn, 31)
    run(c.store.recover())
    report = run(c.runner().run(c.frozen))
    assert report["cases"] == {"done": N} and len(c.results()) == N


def test_b1_pg_revoked_data_fails_its_cases_without_dispatch(world, endpoint) -> None:
    c = Case(world, 6)
    endpoint(c.wallet)
    revoke = {"actor_user_id": l2.C1, "grantor_org_id": l2.org(c.conn, l2.C1),
              "recipient_provider_org_id": NEMO}
    l2.ok(c.conn, "lab_revoke_access_grant", revoke)
    try:
        report = run(c.runner().run(c.frozen))
        assert report["failures"] == {i: "revoked" for i in c.ids} and c.wallet.calls == []
        assert report["cases"] == {"failed": N} and c.results() == []
    finally:
        l2.ok(c.conn, "lab_put_access_grant", l2.scope(
            c.conn, purposes=["provider_sharing", "training"]))
