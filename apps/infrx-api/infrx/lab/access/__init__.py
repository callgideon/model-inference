"""L2: provider roles and purpose-specific data access (LAB-ACCESS).

The database is the authority: lab-sql's L2-SQL owns the tables, RLS and named RPCs, and its
repository (`infrx/state/lab_access.py`, `PgAccessStore`) implements `AccessStore`. This
module is the privileged server entry point's half. A server entry point runs above RLS, so
every operation here reads the **current** membership and grant at call time and combines them
with the frozen contract predicates (`contracts.v2`), default deny. It caches nothing and takes
no "as of" time. The clock is the store's (`infrx.now()`, R7/R79: there is no second clock),
read AFTER the rows it judges, so a revocation or grant committed before the read is never in
that clock's future: a revocation denies the very next call, including queued work.

`LabAccess` is also the port C, J and T consume for grant history and source ids, and the one
membership read the Lab shell uses (R156).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ...contracts import errors
from ...contracts.v2.ports import ProviderDirectory, authorize_content_read
from ...contracts.v2.records import (AccessGrant, DataCategory, DataPurpose,
                                     ProviderCapability, ProviderMembership)


class AccessStore(ProviderDirectory, Protocol):
    """lab-sql's RPC seam (WR-L2-1 as amended by WR-L2-3). Rows are the stored truth, current
    or not; this service decides currency on the store's clock."""

    async def db_now(self) -> datetime:
        """The database clock (`select infrx.now()`), the only clock currency is judged on."""

    async def membership_rows(self, user_id: str) -> Sequence[Mapping[str, Any]]:
        """R156's one membership read (`lab_provider_memberships`): the user's rows, current
        or revoked, each a `ProviderMembership` plus the provider's display `provider_name`."""

    async def grant_history(self, grantor_org_id: str,
                            provider_org_id: str) -> Sequence[AccessGrant]:
        """Every version of the grantor's grants to this provider, oldest first."""

    async def deployment_aggregates(self, provider_org_id: str) -> Sequence[Mapping[str, Any]]:
        """The provider's own deployments' operational aggregates, one row per window."""


class DeploymentAggregate(BaseModel):
    """The default, redacted read: counts per own deployment and window. Closed, so a
    row that carries any identity column (user, org, key, request) is refused rather
    than passed through."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    deployment_revision_id: str
    window_start: datetime
    window_end: datetime
    requests: int = Field(ge=0)
    errors: int = Field(ge=0)
    p95_latency_ms: int | None = Field(default=None, ge=0)


class Workspace(BaseModel):
    """A provider workspace the user may select (L1, R156): a current membership and the
    provider's display name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    membership: ProviderMembership
    provider_name: str = Field(min_length=1)


class LabAccess:
    """Named provider operations. `user_id` is the server-verified session identity."""

    def __init__(self, store: AccessStore) -> None:
        self.store = store

    async def _member(self, user_id: str, provider_org_id: str,
                      capability: ProviderCapability) -> None:
        """A current member of this provider, or a 404: a forged or foreign workspace id
        confirms nothing (every role holds `read_aggregate_health`). A member whose role
        lacks `capability` is a 403, whatever else the call names."""
        membership = await self.store.membership(provider_org_id, user_id)
        now = await self.store.db_now()
        if membership is None or not membership.permits(
                ProviderCapability.read_aggregate_health, now, provider_org_id):
            raise errors.NotFound("no such provider workspace")
        if not membership.permits(capability, now, provider_org_id):
            raise errors.Forbidden(f"this provider role does not hold {capability}")

    async def workspaces(self, user_id: str) -> tuple[Workspace, ...]:
        """The provider workspaces this user may select (L1), with their names. A
        consumer-only user has none; owning a consumer organization is never a provider
        membership."""
        rows = await self.store.membership_rows(user_id)
        now = await self.store.db_now()
        found = (Workspace(provider_name=row["provider_name"], membership={
                     k: v for k, v in row.items() if k != "provider_name"}) for row in rows)
        return tuple(w for w in found if w.membership.is_current(now))

    async def aggregates(self, user_id: str,
                         provider_org_id: str) -> tuple[DeploymentAggregate, ...]:
        await self._member(user_id, provider_org_id, ProviderCapability.read_aggregate_health)
        return tuple(DeploymentAggregate.model_validate(row)
                     for row in await self.store.deployment_aggregates(provider_org_id))

    async def grant_history(self, user_id: str, provider_org_id: str,
                            grantor_org_id: str) -> tuple[AccessGrant, ...]:
        """Every version, revoked ones included: audit and source ids for C/J/T. The rows
        name the grantor's organization and scope, so the viewer (aggregate health only) is
        refused, as for content. History is evidence, never authorization -
        `authorize_content` is."""
        await self._member(user_id, provider_org_id, ProviderCapability.manage_dev_deployment)
        return tuple(await self.store.grant_history(grantor_org_id, provider_org_id))

    async def authorize_content(self, *, user_id: str, provider_org_id: str,
                                grantor_org_id: str, model_id: str, category: DataCategory,
                                purpose: DataPurpose) -> AccessGrant:
        """Individual content (trace, export, evaluation, training input): a current
        developer+ membership **and** the current grant naming this model, category and
        purpose. Returns the grant, whose id and version are the source reference."""
        grant = await self.store.current_grant(grantor_org_id, provider_org_id)
        membership = await self.store.membership(provider_org_id, user_id)
        now = await self.store.db_now()
        authorize_content_read(membership=membership, grant=grant, now=now,
                               provider_org_id=provider_org_id, model_id=model_id,
                               category=category, purpose=purpose)
        return grant
