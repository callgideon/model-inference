"""L2: provider roles and purpose-specific data access (LAB-ACCESS).

The database is the authority: lab-sql's L2-SQL owns the tables, RLS and named RPCs,
and its repository (`infrx/state/lab_access.py`) implements `AccessStore`. This module
is the privileged server entry point's half. A server entry point runs above RLS, so
every operation here reads the **current** membership and grant at call time and
combines them with the frozen contract predicates (`contracts.v2`), default deny. It
caches nothing and takes no "as of" time — the clock is the service's own — so a
revocation denies the very next call, including work already queued under the grant.

`LabAccess` is also the port C, J and T consume for grant history and source ids.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ...contracts import errors
from ...contracts.v2.ports import ProviderDirectory, authorize_content_read
from ...contracts.v2.records import (AccessGrant, DataCategory, DataPurpose,
                                     ProviderCapability, ProviderMembership)


class AccessStore(ProviderDirectory, Protocol):
    """lab-sql's RPC seam (schema request WR-L2-1). Rows are the stored truth, current
    or not; this service decides currency on its own clock."""

    async def memberships_for_user(self, user_id: str) -> Sequence[ProviderMembership]: ...

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LabAccess:
    """Named provider operations. `user_id` is the server-verified session identity."""

    def __init__(self, store: AccessStore, clock: Callable[[], datetime] = _utcnow) -> None:
        self.store, self.clock = store, clock

    async def _member(self, user_id: str, provider_org_id: str) -> ProviderMembership:
        """A current member of this provider, or a 404: a forged or foreign workspace id
        confirms nothing (every role holds `read_aggregate_health`)."""
        membership = await self.store.membership(provider_org_id, user_id)
        if membership is None or not membership.permits(
                ProviderCapability.read_aggregate_health, self.clock(), provider_org_id):
            raise errors.NotFound("no such provider workspace")
        return membership

    async def workspaces(self, user_id: str) -> tuple[ProviderMembership, ...]:
        """The provider workspaces this user may select (L1). A consumer-only user has none;
        owning a consumer organization is never a provider membership."""
        now = self.clock()
        return tuple(m for m in await self.store.memberships_for_user(user_id)
                     if m.is_current(now))

    async def aggregates(self, user_id: str,
                         provider_org_id: str) -> tuple[DeploymentAggregate, ...]:
        await self._member(user_id, provider_org_id)
        return tuple(DeploymentAggregate.model_validate(row)
                     for row in await self.store.deployment_aggregates(provider_org_id))

    async def grant_history(self, user_id: str, provider_org_id: str,
                            grantor_org_id: str) -> tuple[AccessGrant, ...]:
        """Every version, revoked ones included: audit and source ids for C/J/T. History
        is evidence, never authorization — `authorize_content` is."""
        await self._member(user_id, provider_org_id)
        return tuple(await self.store.grant_history(grantor_org_id, provider_org_id))

    async def authorize_content(self, *, user_id: str, provider_org_id: str,
                                grantor_org_id: str, model_id: str, category: DataCategory,
                                purpose: DataPurpose) -> AccessGrant:
        """Individual content (trace, export, evaluation, training input): a current
        developer+ membership **and** the current grant naming this model, category and
        purpose. Returns the grant, whose id and version are the source reference."""
        grant = await self.store.current_grant(grantor_org_id, provider_org_id)
        authorize_content_read(membership=await self.store.membership(provider_org_id, user_id),
                               grant=grant, now=self.clock(), provider_org_id=provider_org_id,
                               model_id=model_id, category=category, purpose=purpose)
        return grant
