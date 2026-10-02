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

The store is `state.identity` (the account summary the App's `lib/session.ts` +
`resolveConsumerContext` assemble today, the provider member rows, provider creation, each
mutation bound to its `Idempotency-Key`); `PgIdentity` here is its direct-SQL fallback until
0065 merges. Membership currency and capability stay `lab.access.LabAccess`'s, judged on the
store clock.
"""
from __future__ import annotations

from typing import Any, Protocol

from fastapi import Request

from ..contracts import api, errors
from ..gateway import lab_auth
from ..state import identity as functions
from ..state.identity import (Account, Claim, IdentityStore, Member,  # noqa: F401 - re-exported
                              Provider, fetch)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


# ponytail: the direct-SQL fallback below is deleted at the 0065 merge (SR-AP01-1): the
# composition then builds `state.identity.PgIdentity` over the identity functions.
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
 where p.id = %(user)s::uuid"""
USER_BY_EMAIL = ("select case when count(*) = 1 then min(id::text) end from public.profiles "
                 "where lower(email) = lower(%(email)s)")
MEMBER_COLUMNS = ("m.user_id::text, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at "
                  "from infrx.provider_memberships m join public.profiles p on p.id = m.user_id")
#: The one-current-row rule is 0007's partial unique index (revoked_at is null).
MEMBERS = (f"select {MEMBER_COLUMNS} where m.provider_org_id = %(provider)s::uuid "
           "and m.revoked_at is null order by m.granted_at, m.membership_id")
LATEST = (f"select {MEMBER_COLUMNS} where m.provider_org_id = %(provider)s::uuid "
          "and m.user_id = %(user)s::uuid "
          "order by m.revoked_at is null desc, m.granted_at desc, m.revoked_at desc limit 1")
GRANT = """
insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by)
values (%(provider)s::uuid, %(user)s::uuid, %(role)s, %(by)s)
on conflict (provider_org_id, user_id) where revoked_at is null do nothing
returning membership_id"""
REVOKE = """
update infrx.provider_memberships set revoked_at = greatest(infrx.now(), granted_at)
 where provider_org_id = %(provider)s::uuid and user_id = %(user)s::uuid and revoked_at is null
returning membership_id"""
PROVIDER_COLUMNS = "provider_org_id::text, slug, display_name, created_by, created_at"
CREATE_PROVIDER = f"""
insert into infrx.provider_orgs (slug, display_name, created_by)
values (%(slug)s, %(name)s, %(by)s)
on conflict (slug) do nothing returning {PROVIDER_COLUMNS}"""
PROVIDER = f"select {PROVIDER_COLUMNS} from infrx.provider_orgs where slug = %(slug)s"


class PgIdentity(functions.PgIdentity):
    """The fallback until 0065 merges: `state.identity.PgIdentity`'s rules and claim
    (0060) over direct statements on the runtime's `service_role` connection (as
    `PgAccessStore` and `PgSignup` read), each write step two statements so the second sees
    the first's row."""

    async def account(self, user_id: str) -> Account | None:
        found = await self._rows(ACCOUNT, {"user": user_id})
        if not found:
            return None
        operator, verified, org_id, wallet, suspended, amount, granted_at = found[0]
        return Account(user_id=user_id, operator=operator, verified=verified, org_id=org_id,
                       wallet=wallet, suspended=suspended, grant_amount=amount,
                       granted_at=granted_at)

    async def user_by_email(self, email: str) -> str | None:
        found = await self._rows(USER_BY_EMAIL, {"email": email})
        return found[0][0] if found else None

    async def members(self, provider_org_id: str) -> list[Member]:
        return [Member(user_id=r[0], email=r[1], role=r[2], granted_by=r[3], granted_at=r[4],
                       revoked_at=r[5])
                for r in await self._rows(MEMBERS, {"provider": provider_org_id})]

    async def _grant(self, conn: Any, a: dict[str, Any]) -> tuple:
        created = bool(await fetch(conn, GRANT, a))
        return (*(await fetch(conn, LATEST, a))[0], created)

    async def _revoke(self, conn: Any, a: dict[str, Any]) -> tuple | None:
        await fetch(conn, REVOKE, a)
        return next(iter(await fetch(conn, LATEST, a)), None)

    async def _create(self, conn: Any, a: dict[str, Any]) -> tuple:
        made = await fetch(conn, CREATE_PROVIDER, a)
        return (*(made[0] if made else (await fetch(conn, PROVIDER, a))[0]),
                bool(made))
