"""WR-B-5 on the real D7 store: the worker composed with `LAB_EVAL_WORKER` on pumps the Lab
outbox's `eval_run` events into `EvalRuns` (B1's `resume` + `Runner`) and recovers expired
leases, on the b1 task-local PostgreSQL (0001-0029, D7's seeded grant) with the dev endpoint
over HTTP on the b1 model-fake port. The evaluator and target sources are the test's: the
worker refuses to start without them until WR-B-2(b) / WR-B-3 exist.

    INFRX_D_TASK=b1 uv run --frozen pytest -q tests/w/test_worker_lab_eval_pg.py

Oracles: an event `freeze` creates that the worker never runs (or runs without acknowledging);
and B1's recheck - a worker killed mid-run, the grant revoked, the lease recovered: the
redelivered event must end every case `revoked` (a handler that froze again would be refused
for ever and the run would never end). Outside the mutant runner (B1's `_pg` pattern); the
handler's decisions are killed by `worker_main_mutants.py` on the unit cases.
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.config import from_env
from infrx.scheduling.memory import MemoryScheduler
from infrx.worker import __main__ as worker_main

from ..b.runner import test_runner_pg as b1
from ..b.runner.test_runner_pg import KEY, N, PORT, RATE_CARD, Case, Dying, restore, revoke
from ..b.runner.world import DEPLOYMENT, SPEC, Crash
from ..d import pgharness
from ..d import test_d7_lab_data as d7
from .test_worker_main import environment, utc_now

# B1's b1 world (module-scoped: this module's own database), its dev endpoint and its skip.
world, endpoint, pytestmark = b1.world, b1.endpoint, b1.pytestmark


def run(coroutine):
    return asyncio.run(coroutine)


def composed(tmp_path, monkeypatch, case: Case, target):
    """`compose` with `LAB_EVAL_WORKER` on over the b1 database; the Lab objects are the
    case's (the worker's media store); the steps as `every` would run them."""
    steps, asked = {}, []

    def every(interval_s, step, what):
        steps[what] = step
        return asyncio.sleep(0)
    monkeypatch.setattr(worker_main, "every", every)

    async def evaluators(ref):
        asked.append(ref)
        return SPEC

    async def targets(ref):
        return target(), DEPLOYMENT
    env = environment(tmp_path, INFRX_MODE="dev", LAB_EVAL_WORKER="1",
                      DATABASE_URL=pgharness.dsn(pgharness.DATABASE + "_b1"))
    service, _ = worker_main.compose(from_env(env), objects=case.objects,
                                     index=MemoryScheduler(utc_now),
                                     evaluators=evaluators, targets=targets)
    for name in ("lab_eval", "lab_recover"):
        run(service.housekeeping[name]())
    assert set(steps) == {"lab eval", "lab recover"}
    return steps, asked


def pending(case: Case) -> int:
    return d7.count(case.conn, "select count(*) from infrx.lab_outbox where kind = 'eval_run' "
                               "and acknowledged_at is null and payload->>'run_id' = %s",
                    case.run_id)


def test_worker_lab_eval_pg__an_eval_run_event_is_worked_to_the_end_and_acknowledged(
        tmp_path, monkeypatch, world, endpoint) -> None:
    c = Case(world, 31)
    endpoint(c.wallet)
    steps, asked = composed(tmp_path, monkeypatch, c, c.endpoint)
    assert pending(c) == 1
    report = run(steps["lab eval"]())
    assert report["indexed"] == report["acknowledged"] >= 1, report
    assert pending(c) == 0 and asked == [c.frozen.run.evaluator_ref]
    assert [row[0] for row in c.results()] == c.ids and len(c.wallet.calls) == N
    assert run(steps["lab eval"]())["read"] == 0                # acknowledged: not again
    assert run(steps["lab recover"]()) == 0


def test_worker_lab_eval_pg__a_redelivery_after_a_revocation_resumes_and_ends_revoked(
        tmp_path, monkeypatch, world, endpoint) -> None:
    c = Case(world, 32)
    endpoint(c.wallet)
    dying = [Dying(f"http://127.0.0.1:{PORT}", api_key=KEY, model="m", rate_card=RATE_CARD)]
    steps, _ = composed(tmp_path, monkeypatch, c, lambda: dying.pop() if dying else c.endpoint())
    with pytest.raises(Crash):
        run(steps["lab eval"]())                                # the worker dies mid-run
    assert pending(c) == 1                                      # never acknowledged
    revoke(c.conn)
    try:
        d7.advance(c.conn, 31)                                  # lease and claim both lapse
        assert run(steps["lab recover"]()) >= 1
        report = run(steps["lab eval"]())                       # the redelivery
        assert report["acknowledged"] >= 1 and pending(c) == 0, report
        states = dict(c.rows("select state, count(*)::int from infrx.lab_eval_cases "
                             "where run_id = %s group by state"))
        assert states == {"failed": N} and c.results() == []
        assert len(c.wallet.calls) == 1                         # only the killed attempt paid
    finally:
        restore(c.conn)


def test_worker_lab_eval_pg__a_redelivery_before_recover_is_not_acknowledged(
        tmp_path, monkeypatch, world, endpoint) -> None:
    """1-F1: the outbox claim lapses (claimed_at + 30 s) before the dead attempt's case
    leases are reaped, so the redelivery can arrive before `lab recover`. It works what is
    pending, finds the dead attempt's cases still `leased` and must NOT acknowledge (recover
    emits no event, so the run would be orphaned); after recover the next redelivery ends it."""
    from infrx.contracts import errors
    c = Case(world, 33)
    endpoint(c.wallet)
    dying = [Dying(f"http://127.0.0.1:{PORT}", api_key=KEY, model="m", rate_card=RATE_CARD)]
    steps, _ = composed(tmp_path, monkeypatch, c, lambda: dying.pop() if dying else c.endpoint())
    with pytest.raises(Crash):
        run(steps["lab eval"]())                                # the worker dies mid-run
    d7.advance(c.conn, 31)                                      # claim and lease both lapse
    with pytest.raises(errors.ResultPending):
        run(steps["lab eval"]())                                # redelivered, no recover yet
    states = dict(c.rows("select state, count(*)::int from infrx.lab_eval_cases "
                         "where run_id = %s group by state"))
    assert states.get("leased", 0) >= 1 and "pending" not in states, states
    assert pending(c) == 1                                      # still owed a delivery
    assert run(steps["lab recover"]()) >= 1
    d7.advance(c.conn, 31)                                      # the relay's window again
    report = run(steps["lab eval"]())
    assert report["acknowledged"] >= 1 and pending(c) == 0, report
    assert [row[0] for row in c.results()] == c.ids
