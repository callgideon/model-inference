"""AP-01 (api-identity-2): the account and provider-workspace rows behind a web session, over
0065's SECURITY DEFINER identity functions (SR-AP01-1), so the gateway's `service_role` pool
and the Lab control unit's `infrx_lab_control` login (EXECUTE on named functions only) run the
same store.

Every mutation is bound to its `Idempotency-Key` through 0060's `control_idempotency`
(R270, contracts.md §2): the write and `infrx.control_op_start` (with the write's outcome)
commit in ONE transaction, so

- the first call commits both and its operation is finished `succeeded`;
- a replay (same key, same `input_hash`) answers the first outcome and rolls its own write
  back - a retried grant never re-grants a member revoked since, a retried revocation never
  revokes a later grant;
- the same key with another request is 409 `idempotency_conflict` and writes nothing;
- a refused write (a 404/409 of the domain) claims no key: the caller may fix and retry.

Racing first calls serialize on the key's primary key; the loser replays. Replaces the
natural-key-only idempotency of `console.session.PgIdentity` (the direct-SQL fallback kept
until 0065 merges).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from ..contracts import api, errors
from . import rpc
from .control_ops import RETENTION_S
from .jobstore import Connect, domain_error

#: The lease a synchronous mutation takes on its own operation to finish it.
OWNER, LEASE_S = "api-identity", 60


class Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Account(Row):
    """The signed-in individual as the database has them (never as the request says)."""

    user_id: str
    operator: bool
    verified: bool
    org_id: str | None          # the consumer wallet's organization, else the personal one
    wallet: bool
    suspended: bool
    grant_amount: str | None
    granted_at: datetime | None


class Member(Row):
    user_id: str
    email: str
    role: str
    granted_by: str
    granted_at: datetime
    revoked_at: datetime | None = None


class Provider(Row):
    provider_org_id: str
    slug: str
    display_name: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class Claim:
    """A mutation's `Idempotency-Key`: scoped by 0060 to the actor's tenant + `kind`, stored
    with the request's canonical `input_hash` (`control_ops.input_hash`)."""

    kind: str
    actor: api.Actor
    key: str
    input_hash: str


class IdentityStore(Protocol):
    async def account(self, user_id: str) -> Account | None: ...
    async def signup_grant_enabled(self) -> bool: ...
    async def user_by_email(self, email: str) -> str | None: ...
    async def members(self, provider_org_id: str) -> list[Member]: ...
    async def add_member(self, provider_org_id: str, user_id: str, role: str,
                         granted_by: str, claim: Claim) -> tuple[Member, bool]: ...
    async def revoke_member(self, provider_org_id: str, user_id: str,
                            claim: Claim) -> Member: ...
    async def create_provider(self, slug: str, display_name: str, created_by: str,
                              administrator: str | None,
                              claim: Claim) -> tuple[Provider, Member | None, bool]: ...


MEMBER = "user_id::text, email, role, granted_by, granted_at, revoked_at"
ACCOUNT = ("select is_operator, verified, org_id::text, wallet, suspended, grant_amount, "
           "granted_at from infrx.identity_account(%(user)s::uuid)")
USER_BY_EMAIL = "select infrx.identity_user_by_email(%(email)s)::text"
MEMBERS = f"select {MEMBER} from infrx.identity_members(%(provider)s::uuid)"
GRANT = (f"select {MEMBER}, created from infrx.identity_grant_member("
         "%(provider)s::uuid, %(user)s::uuid, %(role)s, %(by)s)")
REVOKE = f"select {MEMBER} from infrx.identity_revoke_member(%(provider)s::uuid, %(user)s::uuid)"
CREATE_PROVIDER = ("select provider_org_id::text, slug, display_name, created_by, created_at, "
                   "created from infrx.identity_create_provider(%(slug)s, %(name)s, %(by)s)")


def _member(row: tuple) -> Member:
    return Member(user_id=row[0], email=row[1], role=row[2], granted_by=row[3],
                  granted_at=row[4], revoked_at=row[5])


def _provider(row: tuple) -> Provider:
    return Provider(provider_org_id=row[0], slug=row[1], display_name=row[2],
                    created_by=row[3], created_at=row[4])


async def fetch(conn: Any, sql: str, args: Any) -> list[tuple]:
    """One statement's rows on `conn`; a SQL refusal is its typed error (an outage is not)."""
    from psycopg import Error, OperationalError
    try:
        return await (await conn.execute(sql, args)).fetchall()
    except OperationalError:
        raise
    except Error as failed:
        raise domain_error(failed) from None


async def _door(conn: Any, name: str, args: dict[str, Any]) -> Any:
    from psycopg.types.json import Jsonb
    return (await fetch(conn, f"select infrx.control_op_{name}(%s)", (Jsonb(args),)))[0][0]


class _Replayed(Exception):
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome


class PgIdentity:
    """`IdentityStore` over 0065's functions, one connection per call. `signup_grant_enabled`
    reads `infrx.feature_flags` directly: the gateway's question only (the Lab unit never asks)."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def _rows(self, sql: str, args: dict[str, Any]) -> list[tuple]:
        return await rpc.rows(self._connect, sql, args, error=domain_error)

    # --- the write steps a fallback overrides (one connection, inside the claim's transaction)
    async def _grant(self, conn: Any, a: dict[str, Any]) -> tuple:
        """The pair's current membership + whether this statement made it."""
        return (await fetch(conn, GRANT, a))[0]

    async def _revoke(self, conn: Any, a: dict[str, Any]) -> tuple | None:
        return next(iter(await fetch(conn, REVOKE, a)), None)

    async def _create(self, conn: Any, a: dict[str, Any]) -> tuple:
        return (await fetch(conn, CREATE_PROVIDER, a))[0]

    async def _once(self, claim: Claim, write: Callable[[Any], Awaitable[dict[str, Any]]]
                    ) -> tuple[dict[str, Any], bool]:
        """(outcome, replayed): `write` and the claim in one transaction (module docstring)."""
        from psycopg import OperationalError
        try:
            async with rpc.connection(self._connect) as conn:
                try:
                    async with conn.transaction():
                        refusal: errors.DomainError | None = None
                        outcome: dict[str, Any] | None = None
                        try:
                            async with conn.transaction():     # a savepoint for the write alone
                                outcome = await write(conn)
                        except errors.DomainError as exc:
                            refusal = exc
                        started = await _door(conn, "start", {
                            "kind": claim.kind, "actor": claim.actor.model_dump(mode="json"),
                            "idempotency_key": claim.key, "input_hash": claim.input_hash,
                            "outcome": outcome, "retention_s": RETENTION_S})
                        if started["replayed"]:
                            raise _Replayed(started["outcome"])
                        if refusal is not None:
                            raise refusal
                        op_id = started["operation"]["operation_id"]
                        lease = await _door(conn, "lease", {"operation_id": op_id,
                                                            "owner": OWNER, "ttl_s": LEASE_S})
                        await _door(conn, "finish", {"operation_id": op_id,
                                                     "fence": lease["fence"],
                                                     "state": "succeeded"})
                        return outcome or {}, False
                except _Replayed as replayed:
                    return replayed.outcome, True
        except OperationalError:
            raise errors.DependencyUnavailable("the identity store is unreachable",
                                               retry_after_s=5) from None

    async def _granted(self, conn: Any, a: dict[str, Any], role: str) -> tuple[Member, bool]:
        row = await self._grant(conn, {**a, "role": role})
        member = _member(row)
        if member.role != role:
            raise errors.Conflict("the user holds another current role in this workspace")
        return member, row[6]

    # --- the store -------------------------------------------------------------------------
    async def account(self, user_id: str) -> Account | None:
        found = await self._rows(ACCOUNT, {"user": user_id})
        if not found:
            return None
        operator, verified, org_id, wallet, suspended, amount, granted_at = found[0]
        return Account(user_id=user_id, operator=operator, verified=verified, org_id=org_id,
                       wallet=wallet, suspended=suspended, grant_amount=amount,
                       granted_at=granted_at)

    async def signup_grant_enabled(self) -> bool:
        found = await self._rows("select enabled from infrx.feature_flags "
                                 "where name = 'signup_grant'", {})
        return bool(found and found[0][0])

    async def user_by_email(self, email: str) -> str | None:
        """The one account with this address (case folded); none or an ambiguity is None."""
        found = await self._rows(USER_BY_EMAIL, {"email": email})
        return found[0][0] if found else None

    async def members(self, provider_org_id: str) -> list[Member]:
        return [_member(row) for row in await self._rows(MEMBERS, {"provider": provider_org_id})]

    async def add_member(self, provider_org_id: str, user_id: str, role: str,
                         granted_by: str, claim: Claim) -> tuple[Member, bool]:
        """The user's current membership with `role`; `True` when this call made it. Another
        current role is a 409: a role change is a revocation and a new grant (0007)."""
        async def write(conn: Any) -> dict[str, Any]:
            member, created = await self._granted(conn, {
                "provider": provider_org_id, "user": user_id, "by": granted_by}, role)
            return {"member": member.model_dump(mode="json"), "created": created}
        outcome, replayed = await self._once(claim, write)
        return Member.model_validate(outcome["member"]), outcome["created"] and not replayed

    async def revoke_member(self, provider_org_id: str, user_id: str, claim: Claim) -> Member:
        """The revoked membership; a repeat answers the same revocation. Never a member: 404."""
        async def write(conn: Any) -> dict[str, Any]:
            row = await self._revoke(conn, {"provider": provider_org_id, "user": user_id})
            if row is None:
                raise errors.NotFound("no such member")
            return {"member": _member(row).model_dump(mode="json")}
        outcome, _ = await self._once(claim, write)
        return Member.model_validate(outcome["member"])

    async def create_provider(self, slug: str, display_name: str, created_by: str,
                              administrator: str | None,
                              claim: Claim) -> tuple[Provider, Member | None, bool]:
        """The provider with this slug and its first administrator, together; `True` when this
        call created the provider. An existing slug under another name is a 409 - nothing
        existing is renamed or reassigned."""
        async def write(conn: Any) -> dict[str, Any]:
            row = await self._create(conn, {"slug": slug, "name": display_name,
                                            "by": created_by})
            provider = _provider(row)
            if provider.display_name != display_name:
                raise errors.Conflict("a provider with this slug already exists")
            admin = None if administrator is None else (await self._granted(conn, {
                "provider": provider.provider_org_id, "user": administrator,
                "by": created_by}, "administrator"))[0]
            return {"provider": provider.model_dump(mode="json"), "created": row[5],
                    "administrator": admin and admin.model_dump(mode="json")}
        outcome, replayed = await self._once(claim, write)
        admin = outcome["administrator"]
        return (Provider.model_validate(outcome["provider"]),
                None if admin is None else Member.model_validate(admin),
                outcome["created"] and not replayed)
