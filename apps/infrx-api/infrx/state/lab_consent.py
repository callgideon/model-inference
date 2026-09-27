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

from typing import Any

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
