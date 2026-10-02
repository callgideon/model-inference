#!/usr/bin/env python3
"""AP-10 10b (API-EVAL, register row 27 / WR-C6-B1-FLAKE) in B1's fake world: the eval
worker's runs (`worker.__main__.LAB_EVAL_LIMITS`, concurrency 2) killed with BOTH attempts
in flight after the endpoint charged them. Row 27 read "a killed attempt charged twice";
the reproduction shows two killed attempts - two cases - each charged once under its own
key. The authoritative debit is the endpoint's settlement per Idempotency-Key
(`attempt:<run>:<case>:<n>:<call>`), and no key is ever debited twice; D7 records neither
killed attempt's cost (`expired`), the gap B1 bounds by `max_attempts` (here: one paid
attempt per concurrent worker per kill). The real D7 + worker composition half is
`test_row27_pg.py`.

    uv run --frozen pytest -q tests/ap10/test_row27.py
"""
from __future__ import annotations

import threading

import pytest

from infrx.worker.__main__ import LAB_EVAL_LIMITS

from ..b.runner.test_runner import World, per_case, run
from ..b.runner.world import RATE_CARD, Crash


def killed_in_flight(n: int) -> tuple[World, object, list[str]]:
    """A run of `n` cases whose worker dies once its concurrent attempts were all charged
    (the endpoint holds each answer until every worker reached it, then charges, then the
    process dies): answers (world, frozen, the charged keys)."""
    w = World(n=n)
    frozen = w.freeze()
    together = threading.Barrier(LAB_EVAL_LIMITS.concurrency, timeout=5)
    answer = w.wallet.answer

    def held(prompt):
        together.wait()
        return answer(prompt)
    w.wallet.answer, w.wallet.crash_after_charge = held, True
    with pytest.raises(Crash):
        run(w.runner(limits=LAB_EVAL_LIMITS).run(frozen))
    w.wallet.answer, w.wallet.crash_after_charge = answer, False
    return w, frozen, [call["key"] for call in w.wallet.calls]


def test_ap10_row27_a_kill_with_both_attempts_in_flight_is_two_cases_each_charged_once():
    """The flake's 2-call runs, made deterministic: two calls, two keys, two cases, attempt 1
    call 0 each - and the wallet debited exactly two answers. After recovery and a
    revocation the redelivery ends every case `revoked` with no further call."""
    w, frozen, keys = killed_in_flight(3)
    run_id = frozen.run.run_id
    assert len(keys) == len(set(keys)) == LAB_EVAL_LIMITS.concurrency == 2, keys
    assert {k.rsplit(":", 3)[0] for k in keys} == {f"attempt:{run_id}"}
    assert len({k.split(":")[2] for k in keys}) == 2, "two cases, not one case twice"
    assert all(k.endswith(":1:0") for k in keys), "attempt 1, call 0 - one call each"
    assert str(w.wallet.debited) == str(RATE_CARD.debit(900 * 2, 1000 * 2))
    w.store.revoke(w.grant)
    w.store.now += 31
    assert run(w.store.recover()) == 2
    report = run(w.runner(limits=LAB_EVAL_LIMITS, worker="w2").run(frozen))
    assert report["cases"] == {"failed": 3} and set(report["failures"].values()) == {"revoked"}
    assert len(w.wallet.calls) == 2, "the redelivery after the revocation called nothing"


def test_ap10_row27_after_the_kill_every_key_is_debited_once_and_the_gap_is_the_kill():
    """Without the revocation the recovered cases are new paid attempts (B1's rule): every
    case is scored once, every key is debited once (`N + 2` keys, `N + 2` answers), D7's
    recorded cost is the `N` finished attempts and the unrecorded rest is exactly the two
    killed attempts - one per concurrent worker."""
    n = 3
    w, frozen, _ = killed_in_flight(n)
    w.store.now += 31
    assert run(w.store.recover()) == 2
    report = run(w.runner(limits=LAB_EVAL_LIMITS, worker="w2").run(frozen))
    keys = [call["key"] for call in w.wallet.calls]
    assert report["cases"] == {"done": n} and len(w.store.results) == n
    assert len(keys) == len(set(keys)) == n + 2, keys
    assert str(w.wallet.debited) == str(RATE_CARD.debit(900 * (n + 2), 1000 * (n + 2)))
    assert report["costs"] == {"CREDIT": str(RATE_CARD.debit(900 * n, 1000 * n))}
    expired = [a for a in w.store.attempts.values() if a["state"] == "expired"]
    assert len(expired) == 2 and str(RATE_CARD.debit(900, 1000)) == per_case()
