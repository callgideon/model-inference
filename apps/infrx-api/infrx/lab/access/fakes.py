"""An in-memory `AccessStore` for L2 and the lanes that consume it (C, J, T, L1)."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

from ...contracts.conformance.v2_fakes import FakeProviderDirectory
from ...contracts.v2.records import AccessGrant, ProviderMembership


@dataclasses.dataclass
class FakeAccessStore(FakeProviderDirectory):
    history: list[AccessGrant] = dataclasses.field(default_factory=list)
    rows: dict[str, list[dict[str, Any]]] = dataclasses.field(default_factory=dict)

    def put_grant(self, grant: AccessGrant) -> None:
        self.grants[(grant.grantor_org_id, grant.recipient_provider_org_id)] = grant
        self.history.append(grant)

    def revoke(self, grantor_org_id: str, provider_org_id: str, at: datetime) -> None:
        super().revoke(grantor_org_id, provider_org_id, at)
        self.history.append(self.grants[(grantor_org_id, provider_org_id)])

    async def memberships_for_user(self, user_id: str) -> list[ProviderMembership]:
        return [m for (_, user), m in self.memberships.items() if user == user_id]

    async def grant_history(self, grantor_org_id: str,
                            provider_org_id: str) -> list[AccessGrant]:
        return [g for g in self.history if g.grantor_org_id == grantor_org_id
                and g.recipient_provider_org_id == provider_org_id]

    async def deployment_aggregates(self, provider_org_id: str) -> list[dict[str, Any]]:
        return list(self.rows.get(provider_org_id, ()))
