"""D7: the PostgreSQL Lab catalog and evaluation coordination over the named RPCs of
`0029_lab_data.sql` - the real adapter behind F3's `FakeLabCatalog` rules.

* Catalog: `publish` stores a record's RFC 8785 bytes; the SQL re-derives the digest, so the
  ref is `records.ref_of` (content-addressed, immutable). `resolve` is provider-scoped:
  another provider's ref is `NotFound`, the same answer as an unknown one.
* Rights: `accessible_samples` reads each sample's grant at its CURRENT version for the
  purpose (a manifest is audit evidence, never a standing permission).
* Evaluation: runs, cases, fenced attempt leases, one result per run/case/evaluator,
  checkpoint receipts, and a Lab outbox whose four calls are the store half that D2's
  `OutboxRelay` pumps (`dispatch_pending`, `acknowledge_dispatch`, `release_dispatch`,
  `record_dispatch_error`).

The 0034 follow-up adds: registered evaluators (`put_evaluator`/`evaluator`, R167's
upgrade: a run and its results name one of the provider's), a failed attempt's `error` code,
`run_results` (cases, attempts with cost and error, results - B2's input), `release` (a 402
gives the lease back without consuming the attempt), write-once B2 reports by digest, and
`uses` - H1's `DatasetSources` port (R172) over the dataset's captured grants.

The `provider_org_id` every call takes is the caller's server-derived provider (the L2
port), never a request field. Nothing composes this store yet (B1/N1 wire it).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, NamedTuple

from ..contracts import errors
from ..contracts.lab import records
from ..contracts.v2.records import AccessGrant
from ..lab.access import DatasetUse
from .jobstore import Connect, domain_error


def grant_ref(grant: AccessGrant) -> str:
    """The immutable ref of one grant VERSION: sha256 of the canonical `{grant_id, version}`
    (the SQL's `infrx.lab_grant_ref` spells the same bytes)."""
    digest = hashlib.sha256(records.canonical(
        {"grant_id": grant.grant_id, "version": grant.version})).hexdigest()
    return f"lab:grant:{grant.recipient_provider_org_id}:{grant.grant_id}@sha256:{digest}"


class LabEvent(NamedTuple):
    event_id: str
    kind: str
    provider_org_id: str
    payload: dict[str, Any]


class PgLabDataStore:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def _call(self, function: str, args: dict[str, Any]) -> Any:
        from psycopg import Error
        from psycopg.types.json import Jsonb
        conn = await self._connect()
        try:
            cursor = await conn.execute(f"select infrx.{function}(%s)", (Jsonb(args),))
            (result,) = await cursor.fetchone()
        except Error as failed:
            raise domain_error(failed) from None
        finally:
            await conn.close()
        return result

    # --- catalog (D7.a) ---------------------------------------------------------------------
    async def register_source(self, *, provider_org_id: str, source_id: str,
                              content_digest: str, grant_ref: str, actor: str) -> str:
        return (await self._call("lab_register_source", {
            "provider_org_id": provider_org_id, "source_id": source_id,
            "content_digest": content_digest, "grant_ref": grant_ref, "actor": actor}))["ref"]

    async def publish(self, payload: dict[str, Any], *, provider_org_id: str,
                      actor: str) -> str:
        """The record's ref; the contract refuses first (`LabRejected`), then the SQL."""
        if records.parse(payload).provider_org_id != provider_org_id:
            raise errors.Forbidden("a provider publishes only its own records")
        return (await self._call("lab_publish", {
            "provider_org_id": provider_org_id, "actor": actor,
            "body": records.canonical(payload).decode()}))["ref"]

    async def resolve(self, ref: str, *, provider_org_id: str) -> records.LabRecord:
        row = await self._call("lab_resolve", {"provider_org_id": provider_org_id, "ref": ref})
        return records.parse(json.loads(row["body"]))

    async def accessible_samples(self, dataset_ref: str, *, provider_org_id: str,
                                 purpose: str) -> list[str]:
        return await self._call("lab_accessible_samples", {
            "provider_org_id": provider_org_id, "dataset_ref": dataset_ref, "purpose": purpose})

    # --- evaluation (D7.b) ------------------------------------------------------------------
    async def create_run(self, run_ref: str, *, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_create_run", {"provider_org_id": provider_org_id,
                                                   "run_ref": run_ref})

    async def run_status(self, run_id: str, *, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_run_status", {"provider_org_id": provider_org_id,
                                                   "run_id": run_id})

    async def cancel_run(self, run_id: str, *, provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_cancel_run", {"provider_org_id": provider_org_id,
                                                   "run_id": run_id})

    async def lease_case(self, run_id: str, *, provider_org_id: str, worker_id: str,
                         lease_s: float) -> dict[str, Any] | None:
        return await self._call("lab_lease_case", {
            "provider_org_id": provider_org_id, "run_id": run_id, "worker_id": worker_id,
            "lease_s": lease_s})

    async def heartbeat(self, lease: dict[str, Any], *, lease_s: float) -> dict[str, Any]:
        return await self._call("lab_heartbeat", {"lease": lease, "lease_s": lease_s})

    async def finish(self, lease: dict[str, Any], *, outcome: str,
                     results: list[dict[str, str]], cost: dict[str, str] | None = None,
                     error: str | None = None) -> dict[str, Any]:
        """`error` is a failed attempt's code (e.g. `bound:requests`), stored with it."""
        args = {"lease": lease, "outcome": outcome, "results": results, "cost": cost}
        if error is not None:
            args["error"] = error
        return await self._call("lab_finish_attempt", args)

    async def release(self, lease: dict[str, Any]) -> dict[str, Any]:
        """Give a live lease back without consuming its attempt (a 402); stale is
        `StaleLease`."""
        return await self._call("lab_release_attempt", {"lease": lease})

    async def run_results(self, run_id: str, *, provider_org_id: str) -> dict[str, Any]:
        """The run's status plus `case_states`, `attempt_rows` (cost, error) and `results`."""
        return await self._call("lab_run_results", {"provider_org_id": provider_org_id,
                                                    "run_id": run_id})

    async def put_evaluator(self, spec: dict[str, Any], *, provider_org_id: str,
                            evaluator_id: str, actor: str) -> str:
        """The evaluator's ref: its id and the sha256 of the spec's RFC 8785 bytes."""
        return (await self._call("lab_put_evaluator", {
            "provider_org_id": provider_org_id, "evaluator_id": evaluator_id, "actor": actor,
            "body": records.canonical(spec).decode()}))["ref"]

    async def evaluator(self, ref: str, *, provider_org_id: str) -> dict[str, Any]:
        row = await self._call("lab_evaluator", {"provider_org_id": provider_org_id,
                                                 "ref": ref})
        return json.loads(row["body"])

    async def put_eval_report(self, report: dict[str, Any], *, provider_org_id: str,
                              actor: str) -> str:
        """Store a B2 report write-once; its `report_digest` must be its content's."""
        body = records.canonical({k: v for k, v in report.items() if k != "report_digest"})
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        if report.get("report_digest", digest) != digest:
            raise errors.InvalidRequest("the report's digest is not its content's")
        return (await self._call("lab_put_eval_report", {
            "provider_org_id": provider_org_id, "actor": actor,
            "body": body.decode()}))["report_digest"]

    async def eval_report(self, report_digest: str, *, provider_org_id: str) -> dict[str, Any]:
        row = await self._call("lab_eval_report", {"provider_org_id": provider_org_id,
                                                   "report_digest": report_digest})
        return {**json.loads(row["body"]), "report_digest": row["report_digest"]}

    async def put_variant_comparison(self, comparison: dict[str, Any], *, provider_org_id: str,
                                     actor: str) -> str:
        """WR-R3-2: store R3's `infrx.variant_comparison.1` beside its B2 report, write-once
        by the sha256 of its RFC 8785 bytes (the digest is returned)."""
        return (await self._call("lab_put_variant_comparison", {
            "provider_org_id": provider_org_id, "actor": actor,
            "body": records.canonical(comparison).decode()}))["comparison_digest"]

    async def variant_comparisons(self, report_digest: str, *,
                                  provider_org_id: str) -> list[dict[str, Any]]:
        """The comparisons resting on the provider's report, oldest first."""
        return [json.loads(row["body"]) for row in await self._call(
            "lab_variant_comparisons", {"provider_org_id": provider_org_id,
                                        "report_digest": report_digest})]

    # --- H1's DatasetSources port (R172) --------------------------------------------------
    async def uses(self, provider_org_id: str, dataset_ref: str) -> tuple[DatasetUse, ...]:
        """Every (grantor, model, category) of the grant versions the provider's OWN
        dataset was captured under; none for another provider's or an unknown ref."""
        return tuple(DatasetUse.model_validate(row) for row in await self._call(
            "lab_dataset_uses", {"provider_org_id": provider_org_id, "dataset_ref": dataset_ref}))

    async def recover(self) -> int:
        return (await self._call("lab_recover", {}))["expired"]

    async def receive_checkpoint(self, *, provider_org_id: str, checkpoint_id: str,
                                 external_run_ref: str, artifact_digest: str) -> dict[str, Any]:
        return await self._call("lab_receive_checkpoint", {
            "provider_org_id": provider_org_id, "checkpoint_id": checkpoint_id,
            "external_run_ref": external_run_ref, "artifact_digest": artifact_digest})

    async def transition_checkpoint(self, checkpoint_id: str, state: str, *,
                                    provider_org_id: str) -> dict[str, Any]:
        return await self._call("lab_checkpoint_transition", {
            "provider_org_id": provider_org_id, "checkpoint_id": checkpoint_id,
            "state": state})

    # --- the Lab outbox: the store half of `outbox.OutboxRelay` -----------------------------
    async def dispatch_pending(self, *, limit: int, worker_id: str,
                               redelivery_s: float) -> list[LabEvent]:
        return [LabEvent(**row) for row in await self._call("lab_outbox_pending", {
            "limit": limit, "worker_id": worker_id, "redelivery_s": redelivery_s})]

    async def acknowledge_dispatch(self, event_ids: list[str], *, worker_id: str) -> int:
        return await self._call("lab_outbox_ack", {"event_ids": list(event_ids),
                                                   "worker_id": worker_id})

    async def release_dispatch(self, event_ids: list[str]) -> None:
        await self._call("lab_outbox_release", {"event_ids": list(event_ids)})

    async def record_dispatch_error(self, event_id: str, error: str) -> None:
        await self._call("lab_outbox_error", {"event_id": event_id, "error": error})
