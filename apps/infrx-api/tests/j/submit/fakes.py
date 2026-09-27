"""J2's fakes: the D6J ledger (lab-sql-lw2 writes the real one), an in-process provider, and a
T2I content world. Relative-import helper (R48).

`FakeJudgeLedger` is shaped by D6J's brief (09:61) and the frozen `external_run` state machine
(`contracts/lab/states.py`): a PROVIDER_USD budget per named payer, reservations held while a
run is prepared/submitting/submitted/ambiguous, one submit intent per run, quarantine on
ambiguity. Its PostgreSQL twin is schema request SR-J2-1 in the J2 evidence; until it merges
the reservation race is proved here only in one event loop, not under real DB contention.
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

from infrx.contracts import errors
from infrx.contracts.lab.records import submit_key
from infrx.contracts.lab.states import transition
from infrx.contracts.v2 import records as v2
from infrx.contracts.v2.money_units import ProviderUsd
from infrx.judge import dedupe_key
from infrx.judge.submit import LedgerRun, ProviderResults, SubmitRejected
from infrx.media.store import InMemoryObjectStore
from infrx.traces.ship.shipper import TraceRow, content_key
from psycopg.types.json import Jsonb

HELD = frozenset({"prepared", "submitting", "submitted", "ambiguous"})
T0 = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
JUDGED = (v2.DataCategory.request_content, v2.DataCategory.response_content)


def payer(provider_org_id: str) -> str:
    """A provider's named PROVIDER_USD payer (a `lab:payer:` ref, R159)."""
    return f"lab:payer:{provider_org_id}:{'b' * 8}-0000-4000-8000-{'b' * 12}@sha256:{'c' * 64}"


class FakeJudgeLedger:
    """`submit.JudgeLedger` in memory."""

    def __init__(self, budgets: dict[str, ProviderUsd], now: datetime = T0) -> None:
        self.budgets, self.now = budgets, now
        self.runs: dict[str, LedgerRun] = {}
        self.results: dict[tuple, object] = {}
        self.spent: dict[str, ProviderUsd] = {}
        self.audit: list[tuple[str, str, str]] = []

    async def db_now(self) -> datetime:
        return self.now

    def committed(self, payer_ref: str) -> ProviderUsd:
        total = self.spent.get(payer_ref, ProviderUsd.zero())
        for run in self.runs.values():
            if run.payer_ref == payer_ref and run.state in HELD:
                total = total + run.reserved
        return total

    async def reserve(self, *, run_id, provider_org_id, payer_ref, consent, sample_ids,
                      media_ids, price_version, max_cost) -> LedgerRun:
        stored = self.runs.get(run_id)
        if stored is not None:
            if stored.provider_org_id != provider_org_id:
                raise errors.NotFound(f"no judge run {run_id}")
            return stored
        limit = self.budgets.get(payer_ref)
        if limit is None:
            raise errors.BudgetExceeded("no PROVIDER_USD budget for this payer")
        if self.committed(payer_ref) + max_cost > limit:
            raise errors.BudgetExceeded("the reservation exceeds the payer's remaining budget")
        run = LedgerRun(run_id=run_id, provider_org_id=provider_org_id, payer_ref=payer_ref,
                        consent=consent, sample_ids=tuple(sample_ids),
                        media_ids=frozenset(media_ids), price_version=price_version,
                        reserved=max_cost, state="prepared")
        self.runs[run_id] = run
        return run

    def _move(self, run_id: str, target: str, **update) -> LedgerRun:
        run = self.runs[run_id]
        run = dataclasses.replace(run, state=transition("external_run", run.state, target),
                                  **update)
        self.runs[run_id] = run
        return run

    async def begin_submit(self, run_id: str) -> tuple[LedgerRun, bool]:
        run = self.runs[run_id]
        if run.state != "prepared":
            return run, False
        return self._move(run_id, "submitting", submit_key=submit_key(run_id)), True

    async def record_sent(self, run_id: str, sample_ids) -> LedgerRun:
        run = self.runs[run_id]
        if run.state != "submitting" or not set(sample_ids) <= set(run.sample_ids):
            raise errors.StateConflict(f"judge run {run_id} cannot record these samples")
        self.runs[run_id] = run = dataclasses.replace(run, sent_ids=tuple(sample_ids))
        return run

    async def record_submission(self, run_id: str, external_id: str) -> LedgerRun:
        run = self.runs[run_id]
        if run.external_id == external_id:
            return run
        return self._move(run_id, "submitted", external_id=external_id)

    async def quarantine(self, run_id: str, reason: str) -> LedgerRun:
        self.audit.append((run_id, "quarantine", reason))
        return self._move(run_id, "ambiguous")

    async def release(self, run_id: str, state: str, reason: str) -> LedgerRun:
        self.audit.append((run_id, state, reason))
        return self._move(run_id, state)

    async def record_results(self, run_id: str, results) -> int:
        new = 0
        for result in results:
            key = dedupe_key(result)
            if key not in self.results:
                self.results[key], new = result, new + 1
        return new

    async def settle(self, run_id: str, actual: ProviderUsd) -> LedgerRun:
        run = self.runs[run_id]
        if run.state == "completed":
            return run
        if actual > run.reserved:
            raise errors.BudgetExceeded("the provider billed more than the reservation")
        self.spent[run.payer_ref] = self.spent.get(run.payer_ref, ProviderUsd.zero()) + actual
        return self._move(run_id, "completed", actual=actual)

    async def run(self, run_id: str) -> LedgerRun | None:
        return self.runs.get(run_id)


class FakeProvider:
    """`submit.JudgeProvider` in process. `mode`: ok, reject, timeout (nothing accepted),
    lost (accepted, the answer lost). A batch is keyed by the submit key, as the HTTP fake."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode, self.calls = mode, []
        self.batches: dict[str, str] = {}
        self.outputs: dict[str, list[tuple[str, str]]] = {}
        self.done, self.cost = True, ProviderUsd("0.01000000")

    async def submit(self, key: str, items: list[dict]) -> str:
        self.calls.append((key, items))
        if self.mode == "reject":
            raise SubmitRejected("422 invalid batch")
        if self.mode == "timeout":
            raise TimeoutError("no answer")
        batch = self.batches.setdefault(key, f"batch-{len(self.batches) + 1}")
        if self.mode == "lost":
            raise TimeoutError("accepted, but the answer was lost")
        return batch

    async def lookup(self, key: str) -> str | None:
        return self.batches.get(key)

    async def results(self, external_id: str) -> ProviderResults:
        return ProviderResults(done=self.done, items=tuple(self.outputs.get(external_id, ())),
                               cost=self.cost if self.done else None)


class Projection:
    """T2I's projection read (`find`, org bound) over rows in memory."""

    def __init__(self) -> None:
        self.rows: list[TraceRow] = []
        self.reads: list[tuple[str, str]] = []

    async def find(self, org_id: str, request_id: str) -> list[TraceRow]:
        self.reads.append((org_id, request_id))
        return [r for r in self.rows if r.org_id == org_id and r.request_id == request_id]


def rid(n: int) -> str:
    return str(uuid.UUID(int=n, version=4))


async def trace(projection: Projection, objects: InMemoryObjectStore, org_id: str,
                request_id: str, *, stored: bool = True, body: bytes = b'{"q":"a"}') -> None:
    """One shipped trace: its row and (when `stored`) its content object."""
    trace_id = f"seg-1:{len(projection.rows)}"
    key = content_key(org_id, trace_id)
    if stored:
        await objects.put_if_absent(key, body, "application/json")
    projection.rows.append(TraceRow(
        org_id=org_id, trace_id=trace_id, request_id=request_id, key_id=rid(9), mode="full",
        started_at=T0 - timedelta(hours=1), completed_at=T0 - timedelta(hours=1),
        loss_reason="none", content_complete=True, content_bytes=len(body),
        content_key=key if stored else None, content_stored=stored, request_schema_version=1,
        model_revision="rev-1", price_version="p1", serving_version_id=None,
        rate_card_version=None, policy_version=None))


def grant(world, grantor: str, provider: str, *purposes: v2.DataPurpose,
          expires_in: timedelta | None = None, categories=JUDGED) -> None:
    """The next version of a grant, by default over both content categories a judge reads:
    in memory, or through lab-sql's `lab_put_access_grant` on a PostgreSQL world."""
    if hasattr(world, "conn"):
        world.conn.execute("select infrx.lab_put_access_grant(%s)", (Jsonb({
            "actor_user_id": world.owner[grantor], "grantor_org_id": grantor,
            "recipient_provider_org_id": provider, "model_ids": [world.MODELS[provider]],
            "categories": [c.value for c in categories], "purposes": [p.value for p in purposes],
            "retention_days": 30}),))
        return
    prev = world.store.grants.get((grantor, provider))
    now = world.store.now
    world.store.put_grant(v2.AccessGrant(
        grant_id=prev.grant_id if prev else str(uuid.uuid4()),
        version=prev.version + 1 if prev else 1, grantor_org_id=grantor,
        recipient_provider_org_id=provider, model_ids=(world.MODELS[provider],),
        categories=categories, purposes=purposes, retention_days=30, effective_at=now,
        expires_at=now + expires_in if expires_in else None))
