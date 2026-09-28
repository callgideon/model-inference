"""D8: the pipeline ledgers over `0042_lab_d8_ledgers.sql` - P1's `LabelLog`, P3's
`RunLedger` and B3's `CheckpointLedger`, answer for answer their lanes' fakes
(`tests/p/annotations/world.py` FakeLabelLog, `tests/p/training/world.py` FakeRunLedger,
`tests/b/checkpoints/world.py` FakeCheckpointLedger).

Every rule is in the RPCs on the database clock; these adapters carry arguments and turn
refusals into typed errors. `provider_org_id` is always the server's (the L2 port), never a
request field. Money is D6J's: a run reservation holds PROVIDER_USD on the payer's
`lab_budgets` row. Nothing composes these yet (the P1/P3/B3 wirings).
"""
from __future__ import annotations

from typing import Any

from .jobstore import Connect
from .lab_consent import PgJudgeLedger, _run
from .lab_data import PgLabDataStore


class PgLabelLog:
    """P1's append-only label and review log: one event per (provider, key) for ever."""

    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def append(self, event: dict[str, Any], *, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_label_append", {"provider_org_id": provider_org_id,
                                                     "event": event})

    async def events(self, dataset_ref: str, *, provider_org_id: str) -> list[dict[str, Any]]:
        return await self._call("lab_label_events", {"provider_org_id": provider_org_id,
                                                     "dataset_ref": dataset_ref})


class PgRunLedger:
    """P3's external-run lifecycle (a CAS along F3's `external_run` machine; `expected=None`
    creates from the published record) and one D6J PROVIDER_USD reservation per submit key,
    settled once (`cost=None`: unknown - the whole reservation counts as spent)."""

    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def get(self, external_run_id: str, *, provider_org_id: str) -> dict | None:
        return await self._call("lab_external_run_get", {
            "provider_org_id": provider_org_id, "external_run_id": external_run_id})

    async def move(self, external_run_id: str, *, provider_org_id: str, expected: str | None,
                   target: str, **fields: Any) -> dict:
        return await self._call("lab_external_run_move", {
            "provider_org_id": provider_org_id, "external_run_id": external_run_id,
            "expected": expected, "target": target, "fields": fields})

    async def reserve(self, key: str, *, provider_org_id: str, payer_ref: str,
                      limit: str) -> dict:
        return await self._call("lab_run_reserve", {
            "provider_org_id": provider_org_id, "key": key, "payer_ref": payer_ref,
            "limit": limit})

    async def settle(self, key: str, *, provider_org_id: str, cost: str | None) -> dict:
        return await self._call("lab_run_settle", {"provider_org_id": provider_org_id,
                                                   "key": key, "cost": cost})

    async def release(self, key: str, *, provider_org_id: str) -> dict:
        return await self._call("lab_run_release", {"provider_org_id": provider_org_id,
                                                    "key": key})

    async def note(self, key: str, body: dict, *, provider_org_id: str) -> dict:
        return await self._call("lab_pipeline_note", {"provider_org_id": provider_org_id,
                                                      "key": key, "body": body})

    async def noted(self, key: str, *, provider_org_id: str) -> dict | None:
        return await self._call("lab_pipeline_noted", {"provider_org_id": provider_org_id,
                                                       "key": key})


class PgTeacherLedger(PgJudgeLedger):
    """P2's teacher batches on J2's one egress path (SR-P2-1, amended): `reserve` takes the
    batch's `ConsentRef(dataset_ref, 1)` and snapshots every sample's grant (external_judging
    AND training), `record_sent` re-checks them; the rest of the run's life is J2's ledger.
    `record_failures`/`failures` is the append-only per-item failure log."""

    async def reserve(self, *, run_id: str, provider_org_id: str, payer_ref: str, consent,
                      sample_ids, media_ids: frozenset[str], price_version: str, max_cost):
        return _run(await self._call("lab_teacher_reserve", {
            "run_id": run_id, "provider_org_id": provider_org_id, "payer_ref": payer_ref,
            "dataset_ref": consent.grant_id, "sample_ids": list(sample_ids),
            "price_version": price_version, "max_cost": str(max_cost.raw("PROVIDER_USD"))}))

    async def record_sent(self, run_id: str, sample_ids):
        return _run(await self._call("lab_teacher_record_sent", {
            "run_id": run_id, "sample_ids": list(sample_ids)}))

    async def record_failures(self, run_id: str, failures) -> int:
        return (await self._call("lab_teacher_record_failures", {"run_id": run_id, "failures": [
            {"sample_id": s, "reason": r} for s, r in failures]}))["inserted"]

    async def failures(self, run_id: str) -> list[tuple[str, str]]:
        return [(f["sample_id"], f["reason"])
                for f in await self._call("lab_teacher_failures", {"run_id": run_id})]


def _models():
    from ..evaluation.checkpoints import CheckpointEvent, Subscription   # B3's (merged beside)
    return CheckpointEvent, Subscription


class PgCheckpointLedger:
    """B3's `CheckpointLedger`: the signed event once per (provider, checkpoint id), the first
    rejection reason, subscriptions and one decision per (subscription, checkpoint)."""

    _call = PgLabDataStore._call
    db_now = PgJudgeLedger.db_now

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def record_event(self, event):
        body = await self._call("lab_checkpoint_record_event", {
            "event": event.model_dump(mode="json", by_alias=True)})
        return type(event).model_validate(body)

    async def event(self, checkpoint_id: str, *, provider_org_id: str):
        return _models()[0].model_validate(await self._call("lab_checkpoint_event", {
            "provider_org_id": provider_org_id, "checkpoint_id": checkpoint_id}))

    async def events(self, external_run_ref: str, *, provider_org_id: str) -> list:
        event_type = _models()[0]
        return [(event_type.model_validate(row["event"]), row["rejected"])
                for row in await self._call("lab_checkpoint_events", {
                    "provider_org_id": provider_org_id, "external_run_ref": external_run_ref})]

    async def reject(self, checkpoint_id: str, reason: str, *, provider_org_id: str) -> None:
        await self._call("lab_checkpoint_reject", {"provider_org_id": provider_org_id,
                                                   "checkpoint_id": checkpoint_id,
                                                   "reason": reason})

    async def add_subscription(self, subscription):
        return type(subscription).model_validate(await self._call("lab_checkpoint_subscribe", {
            "subscription": subscription.model_dump(mode="json", by_alias=True)}))

    async def subscriptions(self, external_run_ref: str, *, provider_org_id: str) -> list:
        subscription_type = _models()[1]
        return [subscription_type.model_validate(s) for s in await self._call(
            "lab_checkpoint_subscriptions", {"provider_org_id": provider_org_id,
                                             "external_run_ref": external_run_ref})]

    async def listing(self, *, provider_org_id: str) -> list[dict[str, Any]]:
        """WR-B4-2: the provider's subscriptions (no evaluator spec, no subscriber) with their
        decisions (checkpoint, step, receipt state, decision)."""
        return await self._call("lab_checkpoint_listing", {"provider_org_id": provider_org_id})

    async def decisions(self, subscription_id: str) -> dict[str, dict[str, Any]]:
        return await self._call("lab_checkpoint_decisions", {"subscription_id": subscription_id})

    async def decide(self, subscription_id: str, checkpoint_id: str, *, state: str,
                     reason: str | None, run_id: str | None) -> dict[str, Any]:
        return await self._call("lab_checkpoint_decide", {
            "subscription_id": subscription_id, "checkpoint_id": checkpoint_id,
            "state": state, "reason": reason, "run_id": run_id})

