"""AP-08 decisions that need no database: a door's refusal as R270 renders it, the frozen
sample, and the start step's refusals. The PostgreSQL proofs are `test_*_pg.py`."""
from __future__ import annotations

import asyncio
import types

from infrx.contracts import api, errors
from infrx.contracts.limits import DEFAULTS
from infrx.judge import start
from infrx.judge.submit import JudgeWiring
from infrx.lab.judge_api.doors import refusal

ROWS = [(f"5a000000-0000-4000-8000-{n:012x}", n == 1) for n in range(1, 8)]
REQUEST = {"run_id": "7a000000-0000-4000-8000-0000000000a1", "provider_org_id": "p",
           "grantor_org_id": "g", "model_id": "m", "judge_model": "j", "payer_ref": "x",
           "requested_by": "u", "rubric_version": 1, "sample_size": 3}
DRY = JudgeWiring(access=None, ledger=None, provider=None, retention=None, rates=None,
                  settings=DEFAULTS)


def failed(sqlstate: str, message: str = "x"):
    return types.SimpleNamespace(sqlstate=sqlstate, diag=types.SimpleNamespace(
        message_primary=message, message_hint=None, constraint_name=None))


def test_ap08_units__a_door_refusal_is_the_status_r270_names():
    """42501 (role/grant) is 403, a budget refusal 429 `budget_exceeded`, a flag-off 503.
    Failure oracle: a role refusal rendered as a 500, a budget refusal as an internal error."""
    assert api.status_of(refusal(failed("42501")))[0] == 403
    budget = refusal(failed("P0001", "budget_exceeded: the hold exceeds the cap"))
    assert (api.status_of(budget)[0], budget.code) == (429, "budget_exceeded")
    assert api.status_of(refusal(failed("55000")))[0] == 503
    assert api.status_of(refusal(failed("P0001", "idempotency_conflict: x")))[0] == 409


def test_ap08_units__the_sample_is_frozen_by_the_run_and_bounded():
    """Failure oracle: a sample larger than the configuration, or one that moves between
    passes or with the rows' order."""
    ids, media = start.frozen_sample(REQUEST["run_id"], ROWS, 3)
    assert len(ids) == 3 and set(ids) <= {r for r, _ in ROWS}
    assert start.frozen_sample(REQUEST["run_id"], ROWS[::-1], 3) == (ids, media)
    assert media == frozenset(r for r in ids if r == ROWS[0][0])
    assert start.frozen_sample("7a000000-0000-4000-8000-0000000000a2", ROWS, 3)[0] != ids


def test_ap08_units__start_refuses_what_it_cannot_grade_and_waits_for_traces():
    async def none(*_):
        return []

    async def some(*_):
        return ROWS

    assert asyncio.run(start.start(REQUEST, wiring=DRY, eligible=none)) is None
    try:
        asyncio.run(start.start({**REQUEST, "rubric_version": 9}, wiring=DRY, eligible=some))
    except errors.InvalidRequest:
        pass
    else:
        raise AssertionError("an ungraded rubric version started")


def test_ap08_units__a_dry_run_worker_refuses_and_counts_it():
    """Failure oracle: dry-run counted as started (it would read as a scored run)."""
    async def queued(limit):
        return [REQUEST]

    async def some(*_):
        return ROWS

    done = asyncio.run(start.start_pass(queued, DRY, some))
    assert done == {"started": 0, "waiting": 0, "refused": 1, "failed": 0}
