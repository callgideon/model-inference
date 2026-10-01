"""LAB-API: who is calling a Lab route - the Lab's forwarded Supabase session, never a key.

The Lab server holds only the anon key (R156), so it reaches infrx-api as the signed-in user:
it forwards that user's Supabase access token as `Authorization: Bearer <jwt>`. Every Lab call
then, with nothing cached between calls:

1. `authenticate`: the header must be a compact JWT. An infrx API key (audiences consumer,
   operator, provider_dev) is refused here and never forwarded - the Lab audience is never a
   key audience, and `gateway/ingress`'s key auth is never consulted;
2. `Sessions.user_id`: the token is verified by the project's own auth server
   (`GoTrueSessions`: `GET {SUPABASE_URL}/auth/v1/user` with the project key as `apikey`), which
   accepts only a live, `authenticated` session - signature, expiry and sign-out included;
3. `member`: the actor is the user's **current** provider membership from `LabAccess` (L2, on
   the store's clock), for the provider the call names. A consumer-only user is a 403 on every
   route; a provider the user is not a member of is a 404 (a forged id confirms nothing); a
   role without the operation's capability is a 403.

`refusal` renders every failure as the Lab port's fixed reason (`port.ts` REFUSALS plus
`unauthenticated`); the token never reaches a log line or a response body.

The session families' shared helpers live here too (A8: they were in `routes/lab_evaluations`
and copied in `routes/lab_control`): `lab_actor` (the `Actor` of a call), `require` (a write's
role, checked once its record is found), `held` (a ref not held is the form's 422) and
`lab_body` (the bounded, validated JSON body).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Protocol, TypeVar

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from ..contracts import errors
from ..contracts.v2.records import (ROLE_CAPABILITIES, ProviderCapability, ProviderMembership,
                                    ProviderRole)
from .routes import intake

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)

log = logging.getLogger("infrx.gateway.lab")
USER_PATH = "/auth/v1/user"
SESSION = "authenticated"             # a signed-in user's `aud` and `role` (anon is neither)
#: A compact JWS, bounded. Anything else - an API key, a cookie blob - is not a session.
BEARER = re.compile(r"Bearer ([A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{1,8192}\.[A-Za-z0-9_-]{1,4096})")
NO_STORE = {"cache-control": "no-store"}
MAX_BODY_BYTES = 16_384                  # a Lab form: ids, refs and a few numbers
#: (error kind, status, reason), most specific first; anything else is 503 `unavailable`.
REFUSALS = ((errors.InvalidApiKey, 401, "unauthenticated"), (errors.NotFound, 404, "not_found"),
            (errors.Forbidden, 403, "denied"), (errors.InvalidRequest, 422, "invalid"),
            (errors.Conflict, 409, "conflict"), (errors.Gone, 410, "gone"))


class Sessions(Protocol):
    async def user_id(self, token: str) -> str:
        """The verified user's id; `InvalidApiKey` for anything but a live signed-in session,
        `DependencyUnavailable` when the verifier cannot answer."""


class GoTrueSessions:
    """The project's auth server decides (R-proposal LAB-AUTH): no JWT secret or crypto
    dependency here, and a signed-out session is refused at once.
    ponytail: one auth-server round trip per Lab call; verify locally against the project's
    JWKS (a crypto dependency) if Lab traffic ever makes that matter."""

    def __init__(self, client: httpx.AsyncClient, apikey: str) -> None:
        self.client, self.apikey = client, apikey

    async def user_id(self, token: str) -> str:
        try:
            answer = await self.client.get(USER_PATH, headers={
                "apikey": self.apikey, "authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            raise errors.DependencyUnavailable("the session verifier did not answer") from None
        if answer.status_code in (401, 403):
            raise errors.InvalidApiKey("not a live session")
        if answer.status_code != 200:
            raise errors.DependencyUnavailable("the session verifier did not answer")
        user = answer.json()
        if user.get("aud") != SESSION or user.get("role") != SESSION \
                or not isinstance(user.get("id"), str):
            raise errors.InvalidApiKey("not a signed-in user's session")
        return user["id"]


class Actor(BaseModel):
    """Always the session's current membership, never a request value."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    provider_org_id: str
    user_id: str
    role: ProviderRole


async def authenticate(request, sessions: Sessions) -> str:
    """The verified user id behind the forwarded session token."""
    match = BEARER.fullmatch(request.headers.get("authorization") or "")
    if match is None:
        raise errors.InvalidApiKey("a Lab call carries the user's session token")
    return await sessions.user_id(match.group(1))


async def member(access, user_id: str, provider_org_id: str,
                 capability: ProviderCapability) -> ProviderMembership:
    """The user's current membership of `provider_org_id`, holding `capability`."""
    workspaces = await access.workspaces(user_id)
    if not workspaces:
        raise errors.Forbidden("no provider workspace: consumer accounts use the App")
    membership = next((w.membership for w in workspaces
                       if w.membership.provider_org_id == provider_org_id), None)
    if membership is None:
        raise errors.NotFound("no such provider workspace")
    if capability not in ROLE_CAPABILITIES[membership.role]:
        raise errors.Forbidden(f"this provider role does not hold {capability}")
    return membership


async def lab_actor(request, sessions: Sessions, access,
                    capability: ProviderCapability) -> Actor:
    """The session's user and current membership of the named provider."""
    user_id = await authenticate(request, sessions)
    membership = await member(
        access, user_id, request.query_params.get("provider_org_id", ""), capability)
    return Actor(provider_org_id=membership.provider_org_id, user_id=user_id,
                 role=membership.role)


def require(who: Actor, capability: ProviderCapability) -> None:
    """The role's capability, for a write addressed to a record: checked once the record is
    found, so a 404 is the same whatever the role (the Lab fakes' order, B4-J02/R4)."""
    if capability not in ROLE_CAPABILITIES[who.role]:
        raise errors.Forbidden(f"this provider role does not hold {capability}")


async def held(answer: Awaitable[T]) -> T:
    """A ref in a form that the provider does not hold is a wrong form (422), not a missing
    page (B4-J02/J03)."""
    try:
        return await answer
    except errors.NotFound:
        raise errors.InvalidRequest("the form names what the provider does not hold") from None


async def lab_body(request: Request, rt: Any, model: type[M],
                   max_bytes: int = MAX_BODY_BYTES) -> M:
    """A JSON object body, bounded, validated by `model` (422 otherwise)."""
    intake.check_content_type(request)
    raw = await intake.read_body(request, max_bytes=max_bytes,
                                 timeout_s=rt.settings.pilot.intake_timeout_s, clock=rt.clock)
    try:
        return model.model_validate(intake.parse_object(intake.decode_utf8(raw)))
    except ValidationError:
        raise errors.InvalidRequest("invalid body") from None


def ok(content, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status_code, headers=NO_STORE)


def status_of(exc: Exception) -> tuple[int, str]:
    """The (status, reason) every Lab family answers `exc` with."""
    return next(((status, reason) for kind, status, reason in REFUSALS
                 if isinstance(exc, kind)), (503, "unavailable"))


def refusal(exc: Exception) -> JSONResponse:
    status, reason = status_of(exc)
    if not isinstance(exc, errors.DomainError):   # a bug: its type only - a message or
        log.error("lab route failed: %s", type(exc).__name__)   # traceback may echo the token
    return JSONResponse({"refusal": reason}, status_code=status, headers=NO_STORE)


def guarded(handler):
    """Nothing but a record or a fixed refusal leaves a Lab handler. Not `functools.wraps`:
    FastAPI would read the handler's own signature through `__wrapped__`."""
    async def wrapped(request: Request):
        try:
            return await handler(request)
        except Exception as exc:                 # noqa: BLE001 - rendered, never re-raised
            return refusal(exc)
    return wrapped
