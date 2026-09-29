"""L2-SQL: the PostgreSQL `AccessStore` (`infrx.lab.access`, lab-access lane) over the named
RPCs of the L2-SQL migration, plus the grantor's two writes.

Rows are the stored truth, current or not: the L2 service decides currency and capability on
its own clock with the frozen contract predicates, so nothing here filters by time or role.
The one membership read (R156) is `lab_provider_memberships`; `membership` is its latest row
for one provider. The current grant is the latest version.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..contracts.v2.records import AccessGrant, ProviderMembership
from .jobstore import Connect, domain_error

#: What `lab_provider_memberships` adds for the Lab shell (R156); not a membership field.
DISPLAY_ONLY = ("provider_name",)


class PgAccessStore:
    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @property
    def restrictions(self):
        """WR-DS5-1: N3's `lineage.reconcile` passes this store as its `directory`; give it
        the same `restrictions` port `PgLabDataStore` carries, over the same connection, so
        `lineage.restrictions_of` (which no longer reaches into a store's `_connect`) still
        finds 0041 here without a composition-side patch."""
        from .lab_content import PgSampleRestrictions
        return PgSampleRestrictions(self._connect)

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

    async def db_now(self) -> datetime:
        """The database clock (`infrx.now()`, R7) the L2 service judges currency on."""
        conn = await self._connect()
        try:
            cursor = await conn.execute("select infrx.now()")
            (now,) = await cursor.fetchone()
        finally:
            await conn.close()
        return now

    async def membership_rows(self, user_id: str) -> list[dict[str, Any]]:
        """The R156 read as stored, display name included (the Lab shell's list)."""
        return await self._call("lab_provider_memberships", {"user_id": user_id})

    async def memberships_for_user(self, user_id: str) -> list[ProviderMembership]:
        return [ProviderMembership.model_validate(
                    {k: v for k, v in row.items() if k not in DISPLAY_ONLY})
                for row in await self.membership_rows(user_id)]

    async def membership(self, provider_org_id: str, user_id: str) -> ProviderMembership | None:
        """The latest row for this provider: 0007 allows one unrevoked row per pair, so a
        current membership is always the latest one."""
        rows = [m for m in await self.memberships_for_user(user_id)
                if m.provider_org_id == provider_org_id]
        return rows[-1] if rows else None

    async def grant_history(self, grantor_org_id: str,
                            provider_org_id: str) -> list[AccessGrant]:
        return [AccessGrant.model_validate(row) for row in await self._call(
            "lab_access_grants", {"grantor_org_id": grantor_org_id,
                                  "recipient_provider_org_id": provider_org_id})]

    async def current_grant(self, grantor_org_id: str,
                            provider_org_id: str) -> AccessGrant | None:
        history = await self.grant_history(grantor_org_id, provider_org_id)
        return history[-1] if history else None

    async def deployment_aggregates(self, provider_org_id: str) -> Sequence[Mapping[str, Any]]:
        return await self._call("lab_deployment_aggregates", {"provider_org_id": provider_org_id})

    async def put_grant(self, actor_user_id: str, grant: Mapping[str, Any]) -> AccessGrant:
        """A new version of the grantor's grant to one provider (owner only, in SQL)."""
        return AccessGrant.model_validate(await self._call(
            "lab_put_access_grant", {**grant, "actor_user_id": actor_user_id}))

    async def revoke_grant(self, actor_user_id: str, grantor_org_id: str,
                           provider_org_id: str) -> AccessGrant:
        return AccessGrant.model_validate(await self._call("lab_revoke_access_grant", {
            "actor_user_id": actor_user_id, "grantor_org_id": grantor_org_id,
            "recipient_provider_org_id": provider_org_id}))
