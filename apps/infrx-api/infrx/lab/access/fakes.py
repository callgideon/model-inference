"""An in-memory `AccessStore` for L2 and the lanes that consume it (C, J, T, L1)."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from ...contracts.conformance.v2_fakes import FakeProviderDirectory
from ...contracts.v2.records import AccessGrant
from . import DatasetUse


@dataclasses.dataclass
class FakeAccessStore(FakeProviderDirectory):
    """`now` stands for the database clock (R7): the store, never its caller, says what time
    it is, so a case moves it here. `provider_names` are the display names R156's read adds."""

    now: datetime = dataclasses.field(kw_only=True)
    provider_names: dict[str, str] = dataclasses.field(default_factory=dict)
    history: list[AccessGrant] = dataclasses.field(default_factory=list)
    rows: dict[str, list[dict[str, Any]]] = dataclasses.field(default_factory=dict)

    async def db_now(self) -> datetime:
        return self.now

    def put_grant(self, grant: AccessGrant) -> None:
        self.grants[(grant.grantor_org_id, grant.recipient_provider_org_id)] = grant
        self.history.append(grant)

    def revoke(self, grantor_org_id: str, provider_org_id: str, at: datetime) -> None:
        super().revoke(grantor_org_id, provider_org_id, at)
        self.history.append(self.grants[(grantor_org_id, provider_org_id)])

    async def membership_rows(self, user_id: str) -> list[dict[str, Any]]:
        return [{**m.model_dump(), "provider_name": self.provider_names[provider]}
                for (provider, user), m in self.memberships.items() if user == user_id]

    async def grant_history(self, grantor_org_id: str,
                            provider_org_id: str) -> list[AccessGrant]:
        return [g for g in self.history if g.grantor_org_id == grantor_org_id
                and g.recipient_provider_org_id == provider_org_id]

    async def deployment_aggregates(self, provider_org_id: str) -> list[dict[str, Any]]:
        return list(self.rows.get(provider_org_id, ()))


@dataclasses.dataclass
class FakeDatasets:
    """D7's `DatasetSources` in memory: (provider, dataset_ref) -> its uses."""

    uses_by_ref: dict[tuple[str, str], tuple[DatasetUse, ...]] = dataclasses.field(
        default_factory=dict)

    async def uses(self, provider_org_id: str, dataset_ref: str) -> tuple[DatasetUse, ...]:
        return self.uses_by_ref.get((provider_org_id, dataset_ref), ())
