"""AP-01 01b-01d: who is calling the web API, and the account and workspace rows behind them.

`SessionActors` is the production `control.ActorSource` (`rt.actors`). Every call, with nothing
cached between calls:

1. a mutation (any method but GET/HEAD/OPTIONS) whose browser `Origin` is not a configured web
   origin is refused (CSRF); Next's server-to-server call carries none;
2. `Authorization: Bearer <session JWT>` is verified by the project's auth server
   (`lab_auth.authenticate` + `GoTrueSessions`, the Lab's one session check - not a second one),
   then the user's account is read (`PgIdentity.account`): the actor is `audience="session"`
   with the personal organization and the operator bit from `profiles.is_operator`;
3. with `keys` configured (the operator door), any other Bearer is an infrx API key resolved by
   `auth.context.AuthResolver`: the actor carries that key's audience. Web routes refuse every
   audience but `session` (`web_session`), so a consumer key at a Lab or console door is a 401.

Nothing in a request names the actor: a body field, query value or header other than the
credential is never read as identity.

`PgIdentity` is the SQL half (the account summary the App's `lib/session.ts` +
`resolveConsumerContext` assemble today, the provider member rows, provider creation), on the
runtime's `service_role` connection like `PgAccessStore` and `PgSignup`. Membership currency and
capability stay `lab.access.LabAccess`'s, judged on the store clock.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from fastapi import Request
from pydantic import BaseModel, ConfigDict

from ..contracts import api, errors
from ..gateway import lab_auth
from ..state import rpc
from ..state.jobstore import Connect, domain_error

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


class IdentityStore(Protocol):
    async def account(self, user_id: str) -> Account | None: ...
    async def signup_grant_enabled(self) -> bool: ...
    async def user_by_email(self, email: str) -> str | None: ...
    async def members(self, provider_org_id: str) -> list[Member]: ...
    async def add_member(self, provider_org_id: str, user_id: str, role: str,
                         granted_by: str) -> tuple[Member, bool]: ...
    async def revoke_member(self, provider_org_id: str, user_id: str) -> Member: ...
    async def create_provider(self, slug: str, display_name: str,
                              created_by: str) -> tuple[Provider, bool]: ...


class Keys(Protocol):
    async def context(self, request: Any) -> Any: ...


def web_session(actor: api.Actor) -> api.Actor:
    """The web routes' door: a verified session, never an API key of any audience."""
    if actor.audience != "session" or actor.user_id is None:
        raise errors.InvalidApiKey("an API key is not a web session")
    return actor


class SessionActors:
    def __init__(self, sessions: lab_auth.Sessions, identity: IdentityStore, *,
                 keys: Keys | None = None, origins: tuple[str, ...] = ()) -> None:
        self.sessions, self.identity, self.keys = sessions, identity, keys
        self.origins = tuple(origin.rstrip("/") for origin in origins)

    async def actor(self, request: Request) -> api.Actor:
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin is not None \
                and origin.rstrip("/") not in self.origins:
            raise errors.Forbidden("cross-origin submission")
        header = request.headers.get("authorization") or ""
        if lab_auth.BEARER.fullmatch(header):
            user_id = await lab_auth.authenticate(request, self.sessions)
            account = await self.identity.account(user_id)
            if account is None:
                raise errors.InvalidApiKey("the session's user has no account")
            return api.Actor(audience="session", user_id=user_id, org_id=account.org_id,
                             operator=account.operator)
        if self.keys is not None and header.startswith("Bearer "):
            context = await self.keys.context(request)
            audience = context.audience.value
            return api.Actor(audience=audience, user_id=context.user_id, org_id=context.org_id,
                             provider_org_id=context.provider_org_id,
                             operator=audience == "operator")
        raise errors.InvalidApiKey("a signed-in session or a scoped credential")


ACCOUNT = """
select p.is_operator, v.verification_evidence_ref is not null,
       coalesce(w.personal_org_id, v.personal_org_id)::text, w.wallet_id is not null,
       coalesce(o.suspended, false), e.amount::text, e.granted_at
  from public.profiles p
  cross join lateral infrx.verified_user(p.id) v
  left join infrx.credit_wallets w on w.owner_user_id = p.id and w.kind = 'consumer'
  left join public.organizations o on o.id = coalesce(w.personal_org_id, v.personal_org_id)
  left join infrx.signup_entitlements e
         on e.user_id = p.id and e.entitlement = 'initial_signup_grant'
 where p.id = %s::uuid"""
MEMBER_COLUMNS = ("m.user_id::text, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at "
                  "from infrx.provider_memberships m join public.profiles p on p.id = m.user_id")
#: The one-current-row rule is 0007's partial unique index (revoked_at is null).
MEMBERS = (f"select {MEMBER_COLUMNS} where m.provider_org_id = %s::uuid "
           "and m.revoked_at is null order by m.granted_at, m.membership_id")
LATEST = (f"select {MEMBER_COLUMNS} where m.provider_org_id = %s::uuid and m.user_id = %s::uuid "
          "order by m.revoked_at is null desc, m.granted_at desc, m.revoked_at desc limit 1")
GRANT = """
insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by)
values (%s::uuid, %s::uuid, %s, %s)
on conflict (provider_org_id, user_id) where revoked_at is null do nothing
returning membership_id"""
REVOKE = """
update infrx.provider_memberships set revoked_at = greatest(infrx.now(), granted_at)
 where provider_org_id = %s::uuid and user_id = %s::uuid and revoked_at is null
returning membership_id"""
PROVIDER_COLUMNS = "provider_org_id::text, slug, display_name, created_by, created_at"
CREATE_PROVIDER = f"""
insert into infrx.provider_orgs (slug, display_name, created_by) values (%s, %s, %s)
on conflict (slug) do nothing returning {PROVIDER_COLUMNS}"""
PROVIDER = f"select {PROVIDER_COLUMNS} from infrx.provider_orgs where slug = %s"


def _member(row: tuple) -> Member:
    return Member(user_id=row[0], email=row[1], role=row[2], granted_by=row[3],
                  granted_at=row[4], revoked_at=row[5])


def _provider(row: tuple) -> Provider:
    return Provider(provider_org_id=row[0], slug=row[1], display_name=row[2],
                    created_by=row[3], created_at=row[4])


class PgIdentity:
    """`IdentityStore` over the runtime's connection. Each write is one statement whose
    natural key (0007's one current membership per pair, the provider slug) makes a retry
    find the first attempt's row instead of making a second.
    ponytail: the natural key is the idempotency record; bind `Idempotency-Key` to 0060's
    `control_idempotency` (api-schema) once it merges, for a scoped key + input hash."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def _rows(self, sql: str, params: tuple = ()) -> list[tuple]:
        return await rpc.rows(self._connect, sql, params, error=domain_error)

    async def account(self, user_id: str) -> Account | None:
        found = await self._rows(ACCOUNT, (user_id,))
        if not found:
            return None
        operator, verified, org_id, wallet, suspended, amount, granted_at = found[0]
        return Account(user_id=user_id, operator=operator, verified=verified, org_id=org_id,
                       wallet=wallet, suspended=suspended, grant_amount=amount,
                       granted_at=granted_at)

    async def signup_grant_enabled(self) -> bool:
        found = await self._rows("select enabled from infrx.feature_flags "
                                 "where name = 'signup_grant'")
        return bool(found and found[0][0])

    async def user_by_email(self, email: str) -> str | None:
        found = await self._rows("select id::text from public.profiles "
                                 "where lower(email) = lower(%s) limit 2", (email,))
        return found[0][0] if len(found) == 1 else None

    async def members(self, provider_org_id: str) -> list[Member]:
        return [_member(row) for row in await self._rows(MEMBERS, (provider_org_id,))]

    async def add_member(self, provider_org_id: str, user_id: str, role: str,
                         granted_by: str) -> tuple[Member, bool]:
        """The user's current membership with `role`; `True` when this call made it. A
        current membership with another role is a 409: a role change is a revocation and a
        new grant (0007), never an update."""
        created = bool(await self._rows(GRANT, (provider_org_id, user_id, role, granted_by)))
        member = _member((await self._rows(LATEST, (provider_org_id, user_id)))[0])
        if member.role != role:
            raise errors.Conflict("the user holds another current role in this workspace")
        return member, created

    async def revoke_member(self, provider_org_id: str, user_id: str) -> Member:
        """The revoked membership; a repeat answers the same revocation. Never a member: 404."""
        await self._rows(REVOKE, (provider_org_id, user_id))
        latest = await self._rows(LATEST, (provider_org_id, user_id))
        if not latest:
            raise errors.NotFound("no such member")
        return _member(latest[0])

    async def create_provider(self, slug: str, display_name: str,
                              created_by: str) -> tuple[Provider, bool]:
        """The provider with this slug; `True` when this call created it. An existing slug
        with another display name is a 409 - nothing existing is renamed or reassigned."""
        made = await self._rows(CREATE_PROVIDER, (slug, display_name, created_by))
        provider = _provider(made[0] if made else (await self._rows(PROVIDER, (slug,)))[0])
        if provider.display_name != display_name:
            raise errors.Conflict("a provider with this slug already exists")
        return provider, bool(made)
