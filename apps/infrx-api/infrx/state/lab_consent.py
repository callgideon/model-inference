"""D6J: Lab consent snapshots, PROVIDER_USD budgets with a named payer and the external
submission protocol, over the named RPCs of `0031_lab_consent.sql` (JUDGE-BUDGET).

The protocol is reserve -> intent -> ack. `prepare` snapshots the dataset's grants for the
run's own purpose and reserves its budget limit against its payer; `begin_submit` persists the
ONE intent (`submit:<external_run_id>`) only while the snapshot is still current - call it
immediately before egress. The connector's answer is `accepted` (its batch id), `ambiguous`
(an unknown outcome: quarantined, reconciled later by `accepted` or `failed` after a lookup,
never resubmitted) or `failed`; `finish` settles the cost and releases the rest. A second
`begin_submit` of the same run is `AmbiguousSubmission`, whoever asks.

`provider_org_id` is the caller's server-derived provider (the L2 port checks membership),
never a request field. Disabled unless the `lab_submission` flag row is enabled: prepare and
every move then raise `DependencyUnavailable`. Nothing composes this store yet (J/P wire it).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..contracts import errors
from ..contracts.v2.money_units import ProviderUsd
from .jobstore import Connect
from .lab_data import PgLabDataStore


class PgLabConsentStore:
    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def put_budget(self, *, provider_org_id: str, payer_ref: str, limit: str,
                         actor: str, reason: str) -> dict[str, Any]:
        """A new limit (PROVIDER_USD) for this provider's payer; below held + spent is
        `BudgetExceeded`."""
        return await self._call("lab_put_budget", {
            "provider_org_id": provider_org_id, "payer_ref": payer_ref, "limit": limit,
            "actor": actor, "reason": reason})

    async def budget(self, payer_ref: str, *, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_budget", {"provider_org_id": provider_org_id,
                                               "payer_ref": payer_ref})

    async def prepare(self, external_run_ref: str, *, provider_org_id: str,
                      actor: str) -> dict[str, Any]:
        return await self._call("lab_prepare_submission", {
            "provider_org_id": provider_org_id, "external_run_ref": external_run_ref,
            "actor": actor})

    async def _move(self, external_run_id: str, state: str, provider_org_id: str,
                    **fields: Any) -> dict[str, Any]:
        return await self._call("lab_submission_transition", {
            "provider_org_id": provider_org_id, "external_run_id": external_run_id,
            "state": state, **{k: v for k, v in fields.items() if v is not None}})

    async def begin_submit(self, external_run_id: str, *,
                           provider_org_id: str) -> dict[str, Any]:
        """The intent, persisted before egress: its `submit_key` is the connector's
        idempotency key."""
        return await self._move(external_run_id, "submitting", provider_org_id)

    async def accepted(self, external_run_id: str, external_batch_id: str, *,
                       provider_org_id: str) -> dict[str, Any]:
        return await self._move(external_run_id, "submitted", provider_org_id,
                                external_batch_id=external_batch_id)

    async def ambiguous(self, external_run_id: str, reason: str, *,
                        provider_org_id: str) -> dict[str, Any]:
        return await self._move(external_run_id, "ambiguous", provider_org_id, reason=reason)

    async def finish(self, external_run_id: str, state: str, *, provider_org_id: str,
                     cost: dict[str, str] | None = None) -> dict[str, Any]:
        """`completed` (with its PROVIDER_USD cost), `failed` or `cancelled`: settles the
        cost (default nothing) and releases the rest of the reservation."""
        return await self._move(external_run_id, state, provider_org_id, cost=cost)

    async def submission(self, external_run_id: str, *, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_submission", {"provider_org_id": provider_org_id,
                                                   "external_run_id": external_run_id})


# --- SR-J2-1: the judge ledger (`0036_lab_judge_ledger.sql`) ---------------------------------
@dataclass(frozen=True)
class Consent:
    """The grant a judge run was reserved under (J2's `ConsentRef`)."""

    grant_id: str
    version: int


@dataclass(frozen=True)
class JudgeRun:
    """A judge run row, field for field J2's `LedgerRun` (`infrx.judge.submit`)."""

    run_id: str
    provider_org_id: str
    payer_ref: str
    consent: Consent
    sample_ids: tuple[str, ...]
    media_ids: frozenset[str]
    price_version: str
    reserved: ProviderUsd
    state: str
    submit_key: str | None = None
    external_id: str | None = None
    actual: ProviderUsd | None = None
    sent_ids: tuple[str, ...] = ()


def _run(doc: dict[str, Any] | None) -> JudgeRun | None:
    if doc is None:
        return None
    return JudgeRun(
        run_id=str(doc["run_id"]), provider_org_id=str(doc["provider_org_id"]),
        payer_ref=doc["payer_ref"],
        # a teacher run (0042) is consented per sample of its dataset: its ref stands for it
        consent=Consent(doc["dataset_ref"], 1) if doc.get("dataset_ref") else
        Consent(str(doc["grant_id"]), doc["grant_version"]),
        sample_ids=tuple(doc["sample_ids"]), media_ids=frozenset(doc["media_ids"]),
        price_version=doc["price_version"], reserved=ProviderUsd(doc["reserved"]),
        state=doc["state"], submit_key=doc["submit_key"], external_id=doc["external_id"],
        actual=None if doc["actual"] is None else ProviderUsd(doc["actual"]),
        sent_ids=tuple(doc["sent_sample_ids"]))


class PgJudgeLedger:
    """J2's `JudgeLedger` port on PostgreSQL. The budget is D6J's (`lab_budgets`: one
    PROVIDER_USD cap per provider and named payer), the states the `external_run` machine,
    the flag `lab_submission` (off: every write is `DependencyUnavailable`). A result is any
    object with `run_id`, `sample_id` and `rubric_version` (J2's `JudgeScores`, accepted,
    or `Rejected`, which has no scores); it is stored whole."""

    _call = PgLabDataStore._call

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def db_now(self) -> datetime:
        conn = await self._connect()
        try:
            return (await (await conn.execute("select infrx.now()")).fetchone())[0]
        finally:
            await conn.close()

    async def reserve(self, *, run_id: str, provider_org_id: str, payer_ref: str, consent,
                      sample_ids: Sequence[str], media_ids: frozenset[str],
                      price_version: str, max_cost: ProviderUsd) -> JudgeRun:
        return _run(await self._call("lab_judge_reserve", {
            "run_id": run_id, "provider_org_id": provider_org_id, "payer_ref": payer_ref,
            "grant_id": consent.grant_id, "grant_version": consent.version,
            "sample_ids": list(sample_ids), "media_ids": sorted(media_ids),
            "price_version": price_version, "max_cost": str(max_cost.raw("PROVIDER_USD"))}))

    async def begin_submit(self, run_id: str) -> tuple[JudgeRun, bool]:
        answer = await self._call("lab_judge_begin_submit", {"run_id": run_id})
        return _run(answer["run"]), answer["created"]

    async def record_sent(self, run_id: str, sample_ids: Sequence[str]) -> JudgeRun:
        return _run(await self._call("lab_judge_record_sent", {
            "run_id": run_id, "sample_ids": list(sample_ids)}))

    async def record_submission(self, run_id: str, external_id: str) -> JudgeRun:
        return _run(await self._call("lab_judge_record_submission", {
            "run_id": run_id, "external_id": external_id}))

    async def quarantine(self, run_id: str, reason: str) -> JudgeRun:
        return _run(await self._call("lab_judge_quarantine", {"run_id": run_id,
                                                              "reason": reason}))

    async def release(self, run_id: str, state: str, reason: str) -> JudgeRun:
        return _run(await self._call("lab_judge_release", {
            "run_id": run_id, "state": state, "reason": reason}))

    async def record_results(self, run_id: str, results: Sequence[Any]) -> int:
        return (await self._call("lab_judge_record_results", {"run_id": run_id, "results": [
            {"sample_id": r.sample_id, "rubric_version": r.rubric_version,
             "accepted": hasattr(r, "scores"), "result": _plain(r)} for r in results]}))["inserted"]

    async def settle(self, run_id: str, actual: ProviderUsd) -> JudgeRun:
        answer = await self._call("lab_judge_settle", {
            "run_id": run_id, "actual": str(actual.raw("PROVIDER_USD"))})
        if "refused" in answer:
            raise errors.BudgetExceeded("the provider billed more than the reservation "
                                        "(recorded for an operator)")
        return _run(answer)

    async def run(self, run_id: str) -> JudgeRun | None:
        return _run(await self._call("lab_judge_run", {"run_id": run_id}))

    async def put_calibration(self, calibration: dict[str, Any], *, provider_org_id: str,
                              grantor_org_id: str, judge_model: str,
                              rubric_version: int) -> int:
        """SR-J3-1: J3's `QualityReport.calibration()` of one configuration; the latest one is
        what the Lab's judge-runs door shows."""
        return (await self._call("lab_put_judge_calibration", {
            "provider_org_id": provider_org_id, "grantor_org_id": grantor_org_id,
            "judge_model": judge_model, "rubric_version": rubric_version,
            "calibration": calibration}))["calibration_id"]

    async def sweep(self, older_than_s: int) -> int:
        """`submitting` runs whose worker went silent -> `ambiguous` (then `reconcile`)."""
        return (await self._call("lab_judge_sweep", {"older_than_s": older_than_s}))["expired"]


def _plain(result: Any) -> dict[str, Any]:
    """A dataclass result as JSON-ready data (tuples of scores become lists)."""
    return dataclasses.asdict(result) if dataclasses.is_dataclass(result) else dict(result)
