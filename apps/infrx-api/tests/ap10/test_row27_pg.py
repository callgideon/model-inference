#!/usr/bin/env python3
"""AP-10 10b on the real D7 store and the worker's own composition (register row 27,
WR-C6-B1-FLAKE): `tests/w/test_worker_lab_eval_pg.py`'s revocation case, on ap10's
PostgreSQL with the dev endpoint over HTTP on a free loopback port.

Row 27: that case's `assert len(c.wallet.calls) == 1` failed 1-2 of 17-20 runs ("a killed
attempt charged twice"). Root cause (reproduced here): the worker composes `Runner` with
`LAB_EVAL_LIMITS.concurrency == 2`, so two cases are leased at once; the dying endpoint
charges each and the second's request sometimes reaches the endpoint before the first
crash tears the loop down. The two calls carry two keys of two cases (attempt 1, call 0
each); none is a second debit. `test_..._deterministic` holds both in flight and shows the
old oracle's `== 1` false every time; `test_..._repeated` reruns the original timing.

    INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_row27_pg.py
"""
from __future__ import annotations

import asyncio
import os

import pytest

from infrx.evaluation.runner import HttpDevEndpoint
from infrx.worker.__main__ import LAB_EVAL_LIMITS

from ..b.runner import test_runner_pg as b1
from ..b.runner.test_runner_pg import KEY, N, RATE_CARD, restore, revoke
from ..b.runner.world import Crash, serve
from ..d import pgharness
from ..d import test_d7_lab_data as d7
from ..w import test_worker_lab_eval_pg as w
from ..w.test_worker_main import free_port

TASK = os.environ.get("INFRX_D_TASK")
_reason = pgharness.unavailable() if TASK == "ap10" else \
    "PostgreSQL only on the ap10 task-local key (INFRX_D_TASK=ap10)"
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
PORT = free_port()
world = b1.world                                   # B1's world, on this key's PostgreSQL


class Case(b1.Case):
    def endpoint(self) -> HttpDevEndpoint:
        return HttpDevEndpoint(f"http://127.0.0.1:{PORT}", api_key=KEY, model="marlin-dev",
                               rate_card=RATE_CARD)


class Dying(HttpDevEndpoint):
    """B1's `Dying` (charged, then the process dies); `together` holds each charged call
    until every concurrent worker's call was charged too."""

    def __init__(self, *args, together: bool, **kw) -> None:
        super().__init__(*args, **kw)
        self.together, self.barrier = together, None

    async def complete(self, **request):
        await super().complete(**request)
        if self.together:
            self.barrier = self.barrier or asyncio.Barrier(LAB_EVAL_LIMITS.concurrency)
            await asyncio.wait_for(self.barrier.wait(), 10)
        raise Crash("killed")


def killed_then_revoked(tmp_path, monkeypatch, world, n: int, together: bool) -> Case:
    """The w case's sequence: the composed worker dies mid-run, the grant is revoked, the
    lease and claim lapse, `lab recover`, the redelivery."""
    c = Case(world, n)
    server = serve(c.wallet, PORT, KEY)
    try:
        dying = [Dying(f"http://127.0.0.1:{PORT}", api_key=KEY, model="m", rate_card=RATE_CARD,
                       together=together)]
        steps, _ = w.composed(tmp_path, monkeypatch, c,
                              lambda: dying.pop() if dying else c.endpoint())
        with pytest.raises(Crash):
            w.run(steps["lab eval"]())
        assert w.pending(c) == 1
        revoke(c.conn)
        try:
            d7.advance(c.conn, 31)
            assert w.run(steps["lab recover"]()) >= 1
            report = w.run(steps["lab eval"]())
            assert report["acknowledged"] >= 1 and w.pending(c) == 0, report
            states = dict(c.rows("select state, count(*)::int from infrx.lab_eval_cases "
                                 "where run_id = %s group by state"))
            assert states == {"failed": N} and c.results() == []
        finally:
            restore(c.conn)
    finally:
        server.shutdown()
        server.server_close()
    return c


def charged_once_each(c: Case) -> list[str]:
    """Row 27's corrected oracle: every charged key is a killed attempt's first call of a
    distinct case, no key twice, at most one per concurrent worker, and the wallet's debit
    is exactly one answer per key; D7 recorded no cost for those `expired` attempts."""
    keys = [call["key"] for call in c.wallet.calls]
    expired = {f"attempt:{c.run_id}:{case}:{n}:0" for case, n in c.rows(
        "select case_id::text, attempt from infrx.lab_eval_attempts "
        "where run_id = %s and state = 'expired'")}
    assert 1 <= len(keys) <= LAB_EVAL_LIMITS.concurrency, keys
    assert len(set(keys)) == len(keys) and set(keys) <= expired, (keys, expired)
    assert str(c.wallet.debited) == str(RATE_CARD.debit(900 * len(keys), 1000 * len(keys)))
    assert c.rows("select count(*) from infrx.lab_eval_attempts where run_id = %s "
                  "and state = 'expired' and cost_value is not null") == [(0,)]
    return keys


def test_ap10_row27_pg__both_attempts_in_flight_deterministic(tmp_path, monkeypatch, world):
    c = killed_then_revoked(tmp_path, monkeypatch, world, 270, together=True)
    keys = charged_once_each(c)
    assert len(keys) == 2, "the old oracle `len(c.wallet.calls) == 1` is false here"
    assert len({k.split(":")[2] for k in keys}) == 2, "two cases - not one charged twice"


@pytest.mark.parametrize("n", range(271, 291))
def test_ap10_row27_pg__the_original_timing_repeated(tmp_path, monkeypatch, world, n):
    charged_once_each(killed_then_revoked(tmp_path, monkeypatch, world, n, together=False))
