"""AP-03 (wave 7): the console's and the operator console's mutations, owned by the API.

Replaces what the App does itself today: `apps/app/lib/services/actions.ts` (key create and
revoke through the session's `.from("api_keys")`, with a per-process replay Map),
`apps/app/lib/keys.ts` (secret generation and hashing: `operations.service.new_secret` /
`hash_key` are the same `sk-infrx-` + 40 base62 and sha256 hex), `app/(auth)/grant.ts` (the
service-role `claim_signup_grant`) and `app/(console)/admin/operator-port.ts` (the operator
RPCs). Every rule stays where it was: the existing SQL decides.

The actor is an `infrx.contracts.api.Actor` from `rt.actors`, never a body. Each write is
one transaction on a `service_role` connection whose JWT subject is the actor's verified
individual (`request.jwt.claims`, transaction-local), so `consumer_may_create_key`,
`is_org_owner` and `console_operator` judge exactly the session they judged in the browser.

Key creation's durable identity is `infrx.audit_entries.idempotency_key` (the
`OperatorSession._once` pattern; no new table): scope `key.create:<org>:<sha256(client
key)>`, the key id derived from it, one advisory lock per scope so racing API instances
serialize. The first success reveals the secret; a replay answers the same key with
`secret: null, secret_returned: false`; another body under the scope is a 409.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import Field, ValidationError

from ..contracts import api, codec, errors, wire
from ..contracts.records import AuthContext, IdempotencyRef, Role
from ..operations.service import PREFIX_CHARS, hash_key, new_secret, stable_id
from ..state import rpc, signup
from ..state.jobstore import Connect, domain_error

MAX_NAME = 200                  # apps/app/lib/contracts/types.ts MAX_KEY_NAME_CHARS
MAX_IDEMPOTENCY_KEY = 200       # 0025 console_operator's bound (the App's too)
MAX_REASON = 500                # audit_entries.reason CHECK
CAMPAIGN = "consumer-v1"        # app/(auth)/flow.ts SIGNUP_CAMPAIGN
CREATE = "key.create"
FEEDBACK_OPERATION = "feedback.submit"      # 0038 submit_feedback's operation
_AMOUNT = Field(pattern=r"^-?\d+(\.\d+)?$", max_length=40)
_REASON = Field(min_length=1, max_length=MAX_REASON)


# --------------------------------------------------------------------- wire
class ApiKey(api.Wire):
    key_id: str
    name: str
    prefix: str
    created_at: str
    revoked_at: str | None = None


class KeyCreate(api.Wire):
    name: str = Field(min_length=1, max_length=MAX_NAME)


class KeyCreated(api.Wire):
    """`secret` is set only on the call that created the row; a replay carries null and
    `secret_returned: false` - a lost secret is a revoke and a new key, never a re-reveal."""

    key: ApiKey
    secret: str | None
    secret_returned: bool
    replayed: bool


GrantStatus = Literal["granted", "replayed", "unverified", "identity_reused", "rollout_hold",
                      "retired"]


class GrantClaim(api.Wire):
    """`claim_signup_grant`'s answer: a grant (`credit` + `granted_at`) or why not."""

    status: GrantStatus
    credit: api.Money | None = None
    granted_at: str | None = None


class FeedbackCreate(api.Wire):
    name: str
    value: bool | int | str
    comment: str | None = None


class CreditAdjustment(api.Wire):
    user_id: uuid.UUID
    amount: str = _AMOUNT
    reason: str = _REASON


class CreditAdjusted(api.Wire):
    replayed: bool
    amount: api.Money


class Suspension(api.Wire):
    org_id: uuid.UUID
    suspended: bool
    reason: str = _REASON


class SuspensionSet(api.Wire):
    org_id: str
    suspended: bool
    replayed: bool


class KeyRevocation(api.Wire):
    key_id: uuid.UUID
    reason: str = _REASON


class KeyRevoked(api.Wire):
    key_id: str
    replayed: bool


# --------------------------------------------------------------------- refusals
def consumer(actor: api.Actor) -> tuple[str, str]:
    """(user, org) of a signed-in individual with an account; a machine credential is not
    a console session."""
    if actor.audience != "session" or not actor.user_id or not actor.org_id:
        raise errors.Forbidden("a signed-in individual with an account is required")
    return actor.user_id, actor.org_id


def operator(actor: api.Actor) -> str:
    """The operator's individual. The SQL re-checks `profiles.is_operator` of this subject."""
    if actor.audience != "session" or not actor.operator or not actor.user_id:
        raise errors.Forbidden("operator authority is required")
    return actor.user_id


def bounded(value: str | None, what: str, limit: int) -> str:
    if not value or len(value) > limit or not value.isprintable() or not value.strip():
        raise errors.InvalidRequest(f"{what} is 1..{limit} printable characters")
    return value


def _scope(kind: str, org_id: str, key: str) -> str:
    return f"{kind}:{org_id}:{hashlib.sha256(key.encode()).hexdigest()}"


def _typed(failed: Exception) -> Exception:
    state = getattr(failed, "sqlstate", None)
    if state == "42501" and str(failed).startswith("forbidden:"):   # console_operator's
        return errors.Forbidden("operator authority is required")     # (a missing grant: 500)
    if state == "23505":                    # a used audit idempotency key
        return errors.IdempotencyConflict("this idempotency key recorded another write")
    return domain_error(failed)


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def _key(row) -> ApiKey:
    key_id, name, prefix, created_at, revoked_at = row
    return ApiKey(key_id=str(key_id), name=name, prefix=prefix,
                  created_at=created_at.isoformat(), revoked_at=_iso(revoked_at))


_KEY = ("select id, name, prefix, created_at, revoked_at from public.api_keys "
        "where id = %s and org_id = %s and audience = 'consumer'")


# --------------------------------------------------------------------- repository
class ConsoleActions:
    """The PostgreSQL repository behind `routes/console_actions.py` and
    `routes/operator_actions.py`. `connect` is the runtime's `service_role` `Connect`."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    @asynccontextmanager
    async def _as(self, user_id: str, role: str | None = None):
        """One transaction acting as `user_id` (and as `role`, transaction-local, when the
        functions are granted to `authenticated` only); database errors typed, an outage a
        503."""
        from psycopg import Error, OperationalError
        try:
            async with rpc.connection(self._connect) as conn, conn.transaction():
                await conn.execute("select set_config('request.jwt.claims', %s, true)",
                                   (json.dumps({"sub": user_id, "role": "authenticated"}),))
                if role is not None:
                    await conn.execute(f"set local role {role}")
                yield conn
        except OperationalError:
            raise errors.DependencyUnavailable("the account database is unreachable",
                                               retry_after_s=5) from None
        except Error as failed:
            raise _typed(failed) from None

    @staticmethod
    async def _one(conn, sql: str, params: tuple = ()) -> Any:
        """The first row (a tuple), or None."""
        return await (await conn.execute(sql, params)).fetchone()

    # --- 03a keys -------------------------------------------------------------
    async def create_key(self, actor: api.Actor, name: str, idempotency_key: str | None
                         ) -> KeyCreated:
        user_id, org_id = consumer(actor)
        label = bounded(name.strip(), "name", MAX_NAME)
        scope = _scope(CREATE, org_id, bounded(idempotency_key, "Idempotency-Key",
                                                MAX_IDEMPOTENCY_KEY))
        key_id, request = stable_id(CREATE, scope), {"name": label}
        async with self._as(user_id) as conn:
            await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (scope,))
            prior = await self._one(conn, "select after from infrx.audit_entries "
                                          "where idempotency_key = %s", (scope,))
            if prior is not None:
                if prior[0].get("request") != request:
                    raise errors.IdempotencyConflict("this key was used for a different key")
                row = await self._one(conn, _KEY, (key_id, org_id))
                if row is None:
                    raise errors.NotFound("the key this request created is gone")
                return KeyCreated(key=_key(row), secret=None, secret_returned=False,
                                  replayed=True)
            may = await self._one(
                conn, "select public.consumer_may_create_key(), public.is_org_owner(o.id), "
                      "o.suspended from public.organizations o where o.id = %s", (org_id,))
            if may is None or not may[1]:
                raise errors.Forbidden("the account is not the individual's to manage")
            if not may[0]:
                raise errors.Forbidden("verify your email address first")
            if may[2]:
                raise errors.OrgSuspended("a suspended account receives no new key")
            secret = new_secret()
            row = await self._one(
                conn, "insert into public.api_keys (id, org_id, created_by, user_id, name, "
                      "prefix, key_hash, audience) values (%s, %s, %s, %s, %s, %s, %s, "
                      "'consumer') returning id, name, prefix, created_at, revoked_at",
                (key_id, org_id, user_id, user_id, label, secret[:PREFIX_CHARS],
                 hash_key(secret)))
            from psycopg.types.json import Jsonb
            await conn.execute(
                "insert into infrx.audit_entries (id, actor_principal, action, target_org_id, "
                "reason, after, idempotency_key) values (%s, %s, 'admin_key_issue', %s, %s, "
                "%s, %s)",
                (key_id, f"console:{user_id}", org_id, "consumer key created in the console",
                 Jsonb({"operation": CREATE, "request": request,
                        "result": {"key_id": key_id}}), scope))
        return KeyCreated(key=_key(row), secret=secret, secret_returned=True, replayed=False)

    async def revoke_key(self, actor: api.Actor, key_id: str, idempotency_key: str | None
                         ) -> ApiKey:
        """One-way and repeatable (the first `revoked_at` answers again); allowed while
        suspended (R33). Another account's key is not_found. The scoped Idempotency-Key is
        the audit row's: reusing it for another key is the unique index's 409."""
        user_id, org_id = consumer(actor)
        try:
            key_id = str(uuid.UUID(key_id))
        except ValueError:
            raise errors.NotFound("no such key for this account") from None
        scope = None if idempotency_key is None else _scope(
            "key.revoke", org_id, bounded(idempotency_key, "Idempotency-Key",
                                          MAX_IDEMPOTENCY_KEY))
        async with self._as(user_id) as conn:
            if await self._one(conn, _KEY + " for update", (key_id, org_id)) is None:
                raise errors.NotFound("no such key for this account")
            await conn.execute("select infrx.revoke_key(%s, %s, %s, %s)",
                               (key_id, f"console:{user_id}", "revoked by the account owner",
                                scope))
            row = await self._one(conn, _KEY, (key_id, org_id))
        return _key(row)

    # --- 03b grant ------------------------------------------------------------
    async def claim_grant(self, actor: api.Actor) -> GrantClaim:
        """The one-time 10,000 CREDIT grant of the session's own individual (R71: keyed by
        the individual, so every retry, instance and campaign lands on one grant)."""
        if actor.audience != "session" or not actor.user_id:
            raise errors.Forbidden("a signed-in individual is required")
        async with self._as(actor.user_id) as conn:
            status, = await self._one(conn, signup.CLAIM, (
                actor.user_id, CAMPAIGN, stable_id("signup-grant", actor.user_id)))
            row = await self._one(conn, signup.GRANT_ROW, (actor.user_id,))
        if status not in (signup.GRANTED, signup.REPLAYED):
            return GrantClaim(status=status)
        return GrantClaim(status=status, credit=api.Money(amount=row[2], unit="CREDIT"),
                          granted_at=_iso(row[6]))

    # --- 03d operator ---------------------------------------------------------
    async def _operator_call(self, actor: api.Actor, sql: str, args: tuple, reason: str,
                             idempotency_key: str | None) -> dict:
        user_id = operator(actor)
        bounded(reason, "reason", MAX_REASON)
        key = bounded(idempotency_key, "Idempotency-Key", MAX_IDEMPOTENCY_KEY)
        # 0025 grants the three functions to `authenticated` only (is_operator() inside
        # decides), exactly as PostgREST calls them for a browser session.
        async with self._as(user_id, "authenticated") as conn:
            doc, = await self._one(conn, sql, (*args, reason, key))
        return doc

    async def adjust_credit(self, actor: api.Actor, user_id: str, amount: str, reason: str,
                            idempotency_key: str | None) -> CreditAdjusted:
        doc = await self._operator_call(
            actor, "select public.operator_adjust_credit(%s, %s, %s, %s)", (user_id, amount),
            reason, idempotency_key)
        return CreditAdjusted(replayed=doc["replayed"],
                              amount=api.Money(amount=doc["amount"], unit="CREDIT"))

    async def set_suspension(self, actor: api.Actor, org_id: str, suspended: bool, reason: str,
                             idempotency_key: str | None) -> SuspensionSet:
        doc = await self._operator_call(
            actor, "select public.operator_set_suspension(%s, %s, %s, %s)",
            (org_id, suspended), reason, idempotency_key)
        return SuspensionSet(org_id=org_id, suspended=suspended, replayed=doc["replayed"])

    async def revoke_key_as_operator(self, actor: api.Actor, key_id: str, reason: str,
                                     idempotency_key: str | None) -> KeyRevoked:
        doc = await self._operator_call(
            actor, "select public.operator_revoke_key(%s, %s, %s)", (key_id,), reason,
            idempotency_key)
        return KeyRevoked(key_id=key_id, replayed=doc["replayed"])


# --------------------------------------------------------------------- 03c feedback
async def submit_feedback(service, actor: api.Actor, request_id: str, body: FeedbackCreate,
                          idempotency_key: str | None) -> wire.FeedbackAccepted:
    """The session's feedback on its own account's request, over D6F's `FeedbackService`
    (`rt.feedback`; None = the feature is off, an explicit 503). Provenance is the server's:
    the actor's org and individual, operation `feedback.submit`, a digest of the canonical
    signal (0038's), so a respelled retry replays and a changed signal conflicts."""
    user_id, org_id = consumer(actor)
    if service is None:
        raise errors.DependencyUnavailable("feedback is not enabled")
    key = bounded(idempotency_key, "Idempotency-Key", MAX_IDEMPOTENCY_KEY)
    try:
        submission = wire.FeedbackSubmission.model_validate(
            {**body.model_dump(exclude_none=True), "request_id": request_id})
    except ValidationError as exc:
        raise errors.InvalidRequest(f"invalid feedback: {exc.error_count()} errors") from None
    digest = "sha256:" + hashlib.sha256(codec.compact_bytes(submission)).hexdigest()
    auth = AuthContext(org_id=org_id, key_id=user_id, principal=user_id,
                       role=Role.operator if actor.operator else Role.owner,
                       entitlement_version=0)
    stored = await service.accept(
        auth, submission.request_id,
        submission.model_dump(mode="json", exclude={"request_id"}, exclude_none=True),
        IdempotencyRef(org_id=org_id, operation=FEEDBACK_OPERATION, key=key,
                       payload_hash=digest))
    # ponytail: `replayed` stays false - `FeedbackService.accept` does not report it (the
    # G4F route's WR-G4F-2 gap); the same feedback_id on a retry is the replay's proof.
    return wire.FeedbackAccepted(feedback_id=stored.feedback_id, request_id=stored.request_id,
                                 channel=stored.channel, author_role=stored.author_role,
                                 created_at=stored.created_at)
