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
                     results: list[dict[str, str]], cost: dict[str, str] | None = None
                     ) -> dict[str, Any]:
        return await self._call("lab_finish_attempt", {"lease": lease, "outcome": outcome,
                                                       "results": results, "cost": cost})

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
