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
mutation bound to its `Idempotency-Key`), `state.identity.PgIdentity` over 0065's identity
functions. Membership currency and capability stay `lab.access.LabAccess`'s, judged on the
store clock.
"""
from __future__ import annotations

from typing import Any, Protocol, cast

from fastapi import Request

from ..contracts import api, errors
from ..gateway import lab_auth
from ..state.identity import (Account, Claim, IdentityStore, Member,  # noqa: F401 - re-exported
                              Provider)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class Keys(Protocol):
    async def context(self, request: Any) -> Any: ...


class Users(Protocol):
    async def user(self, token: str) -> dict[str, Any]: ...


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

    async def email(self, request: Request) -> str | None:
        """WR-AP09-ME-EMAIL: the session's address as the auth server holds it (display only;
        the database functions carry no address). ponytail: a second auth-server round trip
        per `me`; carry the answer over from `actor` if that call ever matters."""
        match = lab_auth.BEARER.fullmatch(request.headers.get("authorization") or "")
        if match is None:
            raise errors.InvalidApiKey("a signed-in session")
        email = (await cast(Users, self.sessions).user(match.group(1))).get("email")
        return email if isinstance(email, str) and email else None
