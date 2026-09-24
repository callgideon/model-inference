#!/usr/bin/env python3
"""W5 / ADMISSION-READY + DUR-FENCE + OPS-RECOVER: execution readiness and bounded recovery.

    uv run --frozen pytest -q tests/w/test_w5.py

Service-free, on PREP-WORKER's rig (`test_prep_worker.Prep`: F's fake store and index on one
clock, E2's fake engine in process, M's real `MediaPreparation`) and W2's world
(`test_loop.World`). The durable attach record a pre-D10 store keeps is `Prep.attached`;
`DraftD1Store` is the coordinator's draft decision D1 (the admission records the normalized
source manifest, possibly EMPTY, and the execution-ready marker in one step; a preparation
claim requires the marker; `fail_preparation` is R104's pending third candidate) - a test
double for F2C's `ReadinessStore` and D10's adapter until they are committed, never a
product path.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from infrx.contracts import errors
from infrx.contracts.records import (HoldState, JobState, MediaRef, OutboxKind,
                                     SettlementState, TerminalCause)
from infrx.worker import __main__ as worker_main
from infrx.worker.preparation import PreparationResult
from tests.w.test_prep_worker import COUNT, Prep, within


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- the D1 double
@dataclass(frozen=True)
class Source:
    """F2C's draft `ManifestSource`, the one field the worker reads."""

    ref: MediaRef


@dataclass(frozen=True)
class Readiness:
    """F2C's draft `ExecutionReadiness`: `sources == ()` is a completed EMPTY manifest."""

    sources: tuple[Source, ...]


class DraftD1Store:
    """Coordinator draft D1 over F's fake store (see the module docstring). `admitted` is the
    second half of the one admission transaction: a case calls it with no `await` between it
    and the admission, so no worker can run in between."""

    def __init__(self, store) -> None:
        self.store = store
        self.ready: dict[str, Readiness] = {}
        self.failed: list[tuple[str, TerminalCause]] = []

    def __getattr__(self, name):
        return getattr(self.store, name)

    def admitted(self, request) -> None:
        self.ready[request.request_id] = Readiness(tuple(Source(ref) for ref in request.media))

    async def readiness(self, job_id: str) -> Readiness | None:
        return self.ready.get(job_id)

    async def claim_preparation(self, job_id: str, worker_id: str):
        if job_id not in self.ready:
            raise errors.NotClaimable(f"job {job_id} is not execution-ready")
        return await self.store.claim_preparation(job_id, worker_id)


def d1(prep: Prep) -> DraftD1Store:
    """Put the D1 double under `prep`'s runner (behind `CreditWork` in the CREDIT regime)."""
    store = DraftD1Store(prep.store)
    prep.runner.jobs = worker_main.CreditWork(store) if prep.credit else store
    return store


def job(prep: Prep, request):
    return prep.store.jobs[request.request_id]


def released_once(prep: Prep, request) -> bool:
    """The hold was released - not settled, not still held - and exactly one terminal
    projection was written for the job."""
    return (prep.store.holds[request.request_id].state is HoldState.released
            and prep.store.outbox_kinds(request.request_id).count(
                OutboxKind.usage_projection) == 1)


# --------------------------------------------------------------------------- 1. readiness
@pytest.mark.parametrize("credit", [False, True], ids=["legacy", "credit"])
def test_w5_ready__a_text_job_is_never_prepared_before_its_durable_manifest(tmp_path, credit):
    """RV-05's canary: a text-only job whose admission committed but whose manifest has not
    landed (the gateway's late card/capability rechecks still running) is NOT prepared: the
    engine is never asked, no count is stored, the job stays `preparing` - exactly as a
    video job waits. The attempt ends typed (`not_claimable`, F2C's `not_ready`) at the
    bounded wait, never hangs. Removing the readiness barrier prepares it at once."""
    prep = Prep(tmp_path, credit=credit, attach_wait_s=0.2)

    async def case():
        request = await prep.admit(ready=False)
        result = await within(prep.runner.run(request.request_id), 5.0)
        return request, result

    request, result = run(case())
    assert isinstance(result, PreparationResult), result
    assert (result.cause, result.refusal) == (None, "not_claimable"), result
    assert prep.app.tokenized == [], prep.app.tokenized
    assert job(prep, request).state is JobState.preparing
    assert job(prep, request).prompt_tokens is None


@pytest.mark.parametrize("postcheck", ["passes", "refuses"])
def test_w5_ready__a_late_postcheck_decides_before_anything_is_prepared(tmp_path, postcheck):
    """RV-05 (delayed and rejected postchecks): the worker's attempt starts right after the
    admission commits. When the gateway's recheck then passes, it records the EMPTY manifest
    (an attach of no media) and the worker prepares the job - an empty manifest is completed
    work, not missing work. When it refuses, the gateway cancels the job: nothing was ever
    counted, the hold is released once and the worker's attempt ends typed."""
    prep = Prep(tmp_path, attach_wait_s=1.0)

    async def case():
        request = await prep.admit(ready=False)
        attempt = asyncio.create_task(prep.runner.run(request.request_id))
        await asyncio.sleep(0.2)                  # the recheck, after the admission commit
        if postcheck == "passes":
            prep.attached[request.request_id] = ()
        else:
            await prep.store.cancel(request.org_id, job(prep, request).admission.job_handle)
        return request, await within(attempt, 5.0)

    request, result = run(case())
    if postcheck == "passes":
        assert (result.cause, result.prompt_tokens) == ("prepared", COUNT), result
        assert len(prep.app.tokenized) == 1 and job(prep, request).state is JobState.queued
    else:
        assert result.cause is None and result.refusal is not None, result
        assert prep.app.tokenized == [], prep.app.tokenized
        assert job(prep, request).state is JobState.cancelled
        assert released_once(prep, request)


@pytest.mark.parametrize("credit", [False, True], ids=["legacy", "credit"])
def test_w5_ready__the_d1_marker_is_what_the_worker_reads(tmp_path, credit):
    """On a store with D1's port the committed marker is the barrier, read through the same
    work doors (`CreditWork` in the CREDIT regime): a text job admitted with its EMPTY
    manifest is prepared at once - no attach record exists for it (a pre-D10 store cannot
    write one) and no wait is spent. A job the previous runtime admitted has no marker: its
    claim is refused, nothing is prepared or claimed (`preparation_attempts` stays 0)."""
    prep = Prep(tmp_path, credit=credit)                  # the product wait (10 s)
    store = d1(prep)

    async def case():
        request = await prep.admit(ready=False)
        store.admitted(request)
        began = time.monotonic()
        result = await prep.runner.run(request.request_id)
        took = time.monotonic() - began
        legacy = await prep.admit()                        # attach record, no marker
        return request, result, took, legacy, await prep.runner.run(legacy.request_id)

    request, result, took, legacy, refused = run(case())
    assert (result.cause, result.prompt_tokens) == ("prepared", COUNT) and took < 2.0, \
        (result, took)
    assert job(prep, request).state is JobState.queued
    assert refused.refusal == "not_claimable", refused
    assert job(prep, legacy).preparation_attempts == 0 and len(prep.app.tokenized) == 1


def test_w5_ready__an_unready_job_ends_at_its_preparation_deadline_released_once(tmp_path):
    """ADMISSION-READY's bound: a job that never became ready - a crash between the admission
    and its manifest on a two-phase store, or the previous runtime's job under D1 - is not
    an orphan. The reaper settles it at `preparation_deadline_at` (`preparation_failed`),
    its hold released exactly once, and a second sweep changes nothing."""
    prep = Prep(tmp_path)
    store = d1(prep)

    async def case():
        request = await prep.admit()                       # no marker: never claimable
        assert (await prep.runner.run(request.request_id)).refusal == "not_claimable"
        prep.clock.advance(job(prep, request).budgets.preparation_s + 1)
        first = await store.recover()
        second = await store.recover()
        return request, first, second

    request, first, second = run(case())
    outcome = job(prep, request).outcome
    assert outcome is not None, job(prep, request).state
    assert (outcome.state, outcome.cause, outcome.settlement_state) == (
        JobState.failed, TerminalCause.preparation_failed, SettlementState.released_free)
    assert [o.job_id for o in first] == [request.request_id] and second == ()
    assert released_once(prep, request) and prep.app.tokenized == []
