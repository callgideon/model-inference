"""AP-02: the console's read repositories - the App's PostgREST reads, moved behind FastAPI.

Replaces `apps/app/app/(console)/billing/credit-reads.ts` (wallet, ledger, credits-in, legacy USD,
jobs, key options), `usage/[requestId]/request-reads.ts` (one request and its result),
`admin/operator-reads.ts` (the operator projections) and the Teams/API-keys page reads. No new
accounting: every figure comes from the existing console views and functions (0005, 0008, 0021,
0024, 0025) exactly as the App reads them.

Each read runs as the ACTOR, the way PostgREST runs the App's: one read-only transaction,
`set local role authenticated` and the verified user's id as the JWT subject (both claim forms,
as `tests/d/checks._jwt`). So the database's own guards scope it - `auth.uid()` picks the
wallet/org in `consumer_*`, RLS and `is_operator()` gate the views - and a caller-named id
outside the actor's reach reads as nothing (404), never as another tenant's row. The org id an
org-scoped read needs is the actor's (server-derived, R270) and is bound last.

Failures are typed, never an empty answer: an unreachable database is 503 `unavailable`, a
refusal the SQL names maps to its domain error, anything else from the database is 503, and a
row that is not exactly what its relation promises is a 500 (no figure is guessed). Money stays
a decimal string with its unit (R270 `Money`); "spent" is exact (`money_units`, Decimal).

The connection must be able to `set role authenticated` (Supabase's `authenticator`/`postgres`
can; the dedicated `infrx_runtime` login cannot) - the composition's wiring names the DSN.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from infrx.contracts import api, errors
from infrx.contracts.v2 import money_units
from infrx.state import rpc
from infrx.state.jobstore import Connect, domain_error

from . import cursor as cursors

#: Σ of at most this many grant/adjustment entries makes "spent" (credit-reads.ts CREDITS_IN_BOUND).
CREDITS_IN_BOUND = 100
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})

ResultAccess = Literal["pending", "held_unknown", "no_result", "unavailable", "expired", "available"]
T = TypeVar("T")


# ------------------------------------------------------------------------------------ DTOs
class Credits(api.Wire):
    """The individual's CREDIT wallet. No wallet yet is `wallet_id: null` with zero figures (the
    database's own answer); `spent` is null when it cannot be computed exactly."""
    wallet_id: str | None
    ledger_total: api.Money
    reserved_total: api.Money
    available: api.Money
    spent: api.Money | None
    signup_granted_at: str | None


class LedgerEntry(api.Wire):
    entry_id: str
    created_at: str
    kind: str
    amount: api.Money
    request_id: str | None
    reason: str


class LegacyStatement(api.Wire):
    """Historical USD, its own unit, never summed with CREDIT (R72)."""
    balance: api.Money
    entry_count: int
    rollout_hold: bool
    as_of: str


class RequestSummary(api.Wire):
    """One owned request. Execution (`state`) and settlement (`settlement_state`) are separate
    fields; money is in the request's own unit; `result_access` is F2C.b's read outcome over the
    persisted fields (the database's clock decides available vs expired)."""
    request_id: str
    created_at: str
    requested_model: str
    model_revision: str
    execution_mode: str
    state: str
    outcome_cause: str | None
    accounting_regime: str
    settlement_state: str | None
    usage_certainty: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    hold: api.Money | None
    hold_state: str | None
    charged: api.Money | None
    result_access: ResultAccess
    result_expires_at: str | None


class RequestResult(api.Wire):
    request_id: str
    text: str
    expires_at: str | None


class KeySummary(api.Wire):
    """Never the secret or its hash."""
    id: str
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class Member(api.Wire):
    user_id: str
    email: str | None
    role: str
    joined_at: str


class OperatorAccount(api.Wire):
    user_id: str
    org_id: str
    wallet_id: str
    email: str | None
    suspended: bool
    suspension_reason: str | None
    ledger_total: api.Money
    reserved_total: api.Money
    available: api.Money
    signup_granted_at: str | None


class WalletDrift(api.Wire):
    wallet_id: str
    kind: str
    ledger_drift: api.Money
    reserved_drift: api.Money


class UnknownUsage(api.Wire):
    """A request whose usage is unknown: the hold is quarantined, never charged."""
    request_id: str
    org_id: str
    created_at: str
    reconcile_after: str | None
    hold: api.Money | None


class AuditEntry(api.Wire):
    id: str
    at: str
    actor: str
    action: str
    target_org_id: str | None
    reason: str
    idempotency_key: str | None


# --------------------------------------------------------------------------- row readers
def _malformed(what: str) -> errors.InternalError:
    return errors.InternalError(f"console read: {what}")


def _instant(value: object) -> str:
    """One comparable UTC form, as credit-reads.ts renders it (microseconds, `Z`)."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _malformed("a timestamp is not a UTC instant")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _opt_instant(value: object) -> str | None:
    return None if value is None else _instant(value)


def _id(value: object) -> str:
    if not isinstance(value, uuid.UUID):
        raise _malformed("an id is not a uuid")
    return str(value)


def _opt_id(value: object) -> str | None:
    return None if value is None else _id(value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise _malformed("a text field is not text")
    return value


def _opt_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _money(value: object, unit: str) -> api.Money:
    """Exact text in its unit; a float or a malformed amount is refused, never rounded."""
    if not isinstance(value, str):
        raise _malformed("money is not text")
    try:
        amount = money_units.parse_amount(value, unit)
    except (ValueError, TypeError):
        raise _malformed("money is not an exact amount") from None
    return api.Money(amount=str(amount), unit=unit)  # type: ignore[arg-type]


def _opt_money(value: object, unit: str) -> api.Money | None:
    return None if value is None else _money(value, unit)


def _count(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _malformed("a count is not a non-negative integer")
    return value


def _flag(value: object) -> bool:
    if not isinstance(value, bool):
        raise _malformed("a flag is not a boolean")
    return value


def _credit(row: Mapping[str, Any], name: str) -> money_units.Credit:
    return money_units.Credit(_money(row[name], money_units.CREDIT).amount)


def result_access(state: str, settlement_state: str | None, usage_certainty: str | None,
                  expires_at: object, available: bool) -> ResultAccess:
    """F2C.b `read_outcome` over the persisted fields (request-view-model.ts `resultAccessOf`)."""
    if state not in _TERMINAL:
        return "pending"
    if settlement_state == "held_unknown":
        return "held_unknown"
    if state != "succeeded" or usage_certainty != "authoritative":
        return "no_result"
    if expires_at is None:
        return "unavailable"
    return "available" if available else "expired"


def _request(row: Mapping[str, Any]) -> RequestSummary:
    regime = _text(row["accounting_regime"])
    try:
        unit = money_units.unit_of(regime)
    except ValueError:
        raise _malformed("an unknown accounting regime") from None
    state = _text(row["state"])
    settlement = _opt_text(row["settlement_state"])
    certainty = _opt_text(row["usage_certainty"])
    return RequestSummary(
        request_id=_id(row["request_id"]), created_at=_instant(row["created_at"]),
        requested_model=_text(row["requested_model"]), model_revision=_text(row["model_revision"]),
        execution_mode=_text(row["execution_mode"]), state=state,
        outcome_cause=_opt_text(row["outcome_cause"]), accounting_regime=regime,
        settlement_state=settlement, usage_certainty=certainty,
        prompt_tokens=_count(row["prompt_tokens"]),
        completion_tokens=_count(row["completion_tokens"]),
        hold=_opt_money(row["hold"], unit), hold_state=_opt_text(row["hold_state"]),
        charged=_opt_money(row["charged"], unit),
        result_access=result_access(state, settlement, certainty, row["result_expires_at"],
                                    _flag(row["result_available"])),
        result_expires_at=_opt_instant(row["result_expires_at"]))


# ------------------------------------------------------------------------------ requests
def page_size(limit: str | None) -> int:
    """R270: 25 by default, 1..100 or `invalid_request` (never clamped silently)."""
    if limit is None:
        return api.DEFAULT_PAGE_SIZE
    if not limit.isdigit() or not 1 <= int(limit) <= api.MAX_PAGE_SIZE:
        raise errors.InvalidRequest(f"limit must be 1..{api.MAX_PAGE_SIZE}", param="limit")
    return int(limit)


def _request_id(value: str) -> str:
    """The canonical id, or `not_found` for anything that is not one (a malformed id and a
    foreign one read the same)."""
    found = value.lower()
    if not _UUID.match(found):
        raise errors.NotFound("no such request")
    return found


def _instant_filter(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise errors.InvalidRequest(f"{name} is not an ISO-8601 instant", param=name) from None
    if parsed.tzinfo is None:
        raise errors.InvalidRequest(f"{name} needs a UTC offset", param=name)
    return parsed.astimezone(UTC).isoformat()


def request_filters(model: str | None, key_id: str | None, from_: str | None,
                    to: str | None) -> dict[str, str | None]:
    """`consumer_jobs`' 0024 filters, validated and canonical (they are part of the cursor's
    scope). The window is half-open: [from, to)."""
    if model is not None and not 0 < len(model) <= 200:
        raise errors.InvalidRequest("model is 1..200 characters", param="model")
    if key_id is not None:
        key_id = key_id.lower()
        if not _UUID.match(key_id):
            raise errors.InvalidRequest("key_id is not a key id", param="key_id")
    return {"model": model, "key_id": key_id, "from": _instant_filter(from_, "from"),
            "to": _instant_filter(to, "to")}


def _refusal(failed: Exception) -> Exception:
    """A database error as the typed answer: a refusal the SQL names -> its domain error; the
    database refusing this actor -> 403; anything else, unreachable included -> 503."""
    mapped = domain_error(failed)
    if isinstance(mapped, errors.DomainError):
        return mapped
    if getattr(failed, "sqlstate", None) == "42501":
        return errors.Forbidden("the database refused this actor")
    return errors.DependencyUnavailable("console read failed", retry_after_s=5)


Fetch = Callable[[Any, tuple[str, ...] | None, int], Awaitable[list[tuple[tuple[str, ...], T]]]]


class ConsoleReads:
    """The console read port `routes/console_reads.py` serves (rt.console_reads)."""

    def __init__(self, connect: Connect, cursor_secret: bytes) -> None:
        if len(cursor_secret) < cursors.MIN_SECRET_BYTES:
            raise ValueError("the console cursor secret needs at least 16 bytes")
        self._connect, self._secret = connect, cursor_secret

    # ------------------------------------------------------------------ the actor session
    @asynccontextmanager
    async def _as(self, actor: api.Actor) -> AsyncIterator[Any]:
        """One read-only transaction as the verified user (PostgREST's session, R271)."""
        from psycopg import Error
        user = _user(actor)
        claims = json.dumps({"sub": user, "role": "authenticated"})
        try:
            async with rpc.connection(self._connect) as conn, conn.transaction():
                await conn.execute("set transaction read only")
                await conn.execute("set local role authenticated")
                await conn.execute(
                    "select set_config('request.jwt.claims', %s, true), "
                    "set_config('request.jwt.claim.sub', %s, true), "
                    "set_config('request.jwt.claim.role', 'authenticated', true)", (claims, user))
                yield conn
        except Error as failed:
            raise _refusal(failed) from None

    @staticmethod
    async def _rows(conn: Any, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute(sql, params)
        return await cur.fetchall()

    async def _page(self, actor: api.Actor, operation: str, sort: str,
                    filters: Mapping[str, str | None], limit: str | None, token: str | None,
                    fetch: Fetch[T], *, operator: bool = False) -> api.ListPage[T]:
        """One keyset page (credit-reads.ts `rpcPage`): `limit + 1` rows asked, capped at 100;
        the extra row only says there is more, and at the cap a full page carries a cursor (the
        next page may be empty). The cursor resumes after the last row SHOWN."""
        size = page_size(limit)
        scope = cursors.scope(actor, operation, filters, sort)
        after = None if token is None else cursors.decode(self._secret, scope, token)
        ask = min(size + 1, api.MAX_PAGE_SIZE)
        async with self._as(actor) as conn:
            if operator:
                await _require_operator(conn, actor)
            found = await fetch(conn, after, ask)
        shown = found[:size]
        more = len(found) > size or (ask == size and len(found) == size)
        nxt = cursors.encode(self._secret, scope, shown[-1][0]) if more and shown else None
        return api.ListPage[Any](data=tuple(item for _, item in shown), next_cursor=nxt)

    # ------------------------------------------------------------------------- consumer
    async def credits(self, actor: api.Actor) -> Credits:
        async with self._as(actor) as conn:
            rows = await self._rows(conn, "select wallet_id, ledger_total, reserved_total, "
                                          "available, signup_granted_at from "
                                          "public.console_wallet_summary(%s::uuid)",
                                    (_user(actor),))
            if len(rows) != 1:
                raise _malformed("the wallet summary is one row")
            (row,) = rows
            spent, ins = None, []
            if row["wallet_id"] is not None:
                ins = await self._rows(conn, "select amount from public.console_credit_ledger "
                                             "where wallet_id = %s and kind <> 'inference_debit' "
                                             "limit %s", (row["wallet_id"], CREDITS_IN_BOUND + 1))
        if row["wallet_id"] is not None and len(ins) <= CREDITS_IN_BOUND:
            came_in = money_units.total((_credit(r, "amount") for r in ins), money_units.Credit)
            spent = api.Money(amount=str(came_in - _credit(row, "ledger_total")), unit="CREDIT")
        return Credits(wallet_id=_opt_id(row["wallet_id"]),
                       ledger_total=_money(row["ledger_total"], "CREDIT"),
                       reserved_total=_money(row["reserved_total"], "CREDIT"),
                       available=_money(row["available"], "CREDIT"), spent=spent,
                       signup_granted_at=_opt_instant(row["signup_granted_at"]))

    async def credit_ledger(self, actor: api.Actor, limit: str | None,
                            token: str | None) -> api.ListPage[LedgerEntry]:
        async def fetch(conn, after, ask):
            rows = await self._rows(conn, "select * from public.consumer_credit_ledger(%s, %s)",
                                    (after[0] if after else None, ask))
            return [((_text(r["cursor"]),), _entry(r)) for r in rows]
        return await self._page(actor, "credit-ledger", "created_at.desc,entry_id.desc", {},
                                limit, token, fetch)

    async def legacy_statement(self, actor: api.Actor) -> LegacyStatement:
        org = _org(actor)
        async with self._as(actor) as conn:
            rows = await self._rows(conn, "select balance, entry_count, rollout_hold, as_of "
                                          "from public.console_legacy_usd_statement(%s::uuid)",
                                    (org,))
        if len(rows) != 1:
            raise _malformed("the legacy statement is one row")
        (row,) = rows
        count = _count(row["entry_count"])
        if count is None:
            raise _malformed("the legacy statement has no entry count")
        return LegacyStatement(balance=_money(row["balance"], "USD"), entry_count=count,
                               rollout_hold=_flag(row["rollout_hold"]),
                               as_of=_instant(row["as_of"]))

    async def requests(self, actor: api.Actor, limit: str | None, token: str | None,
                       filters: dict[str, str | None]) -> api.ListPage[RequestSummary]:
        async def fetch(conn, after, ask):
            rows = await self._rows(
                conn, "select * from public.consumer_jobs(p_after => %s, p_limit => %s, "
                      "p_model => %s, p_key_id => %s::uuid, p_from => %s::timestamptz, "
                      "p_to => %s::timestamptz)",
                (after[0] if after else None, ask, filters["model"], filters["key_id"],
                 filters["from"], filters["to"]))
            return [((_text(r["cursor"]),), _request(r)) for r in rows]
        return await self._page(actor, "requests", "created_at.desc,request_id.asc", filters,
                                limit, token, fetch)

    async def _job(self, conn: Any, request_id: str) -> RequestSummary:
        rows = await self._rows(conn, "select * from public.consumer_jobs(p_limit => 1, "
                                      "p_request_id => %s::uuid)", (request_id,))
        if not rows or str(rows[0]["request_id"]) != request_id:
            raise errors.NotFound("no such request")
        return _request(rows[0])

    async def request(self, actor: api.Actor, request_id: str) -> RequestSummary:
        found = _request_id(request_id)
        async with self._as(actor) as conn:
            return await self._job(conn, found)

    async def result(self, actor: api.Actor, request_id: str) -> RequestResult:
        """The owned result, only when the request's read outcome is `available` (the content
        of a success whose usage is unreconciled is withheld, U4 WR-U4-2)."""
        found = _request_id(request_id)
        async with self._as(actor) as conn:
            job = await self._job(conn, found)
            access = job.result_access
            if access in ("pending", "held_unknown"):
                raise errors.ResultPending("the result is not ready")
            if access == "no_result":
                raise errors.NotFound("this request has no result")
            if access == "unavailable":
                raise errors.DependencyUnavailable("the result has no persisted expiry")
            if access == "expired":
                raise errors.ResultExpired("the result expired")
            rows = await self._rows(conn, "select public.consumer_job_result(%s::uuid) as text",
                                    (found,))
        return RequestResult(request_id=found, text=_text(rows[0]["text"]),
                             expires_at=job.result_expires_at)

    async def keys(self, actor: api.Actor, limit: str | None,
                   token: str | None) -> api.ListPage[KeySummary]:
        org = _org(actor)

        async def fetch(conn, after, ask):
            at, kid = after if after else (None, None)
            rows = await self._rows(
                conn, "select id, name, prefix, created_at, last_used_at, revoked_at "
                      "from public.api_keys where (%(at)s::timestamptz is null or "
                      "(created_at, id) < (%(at)s::timestamptz, %(id)s::uuid)) "
                      "and org_id = %(org)s::uuid order by created_at desc, id desc "
                      "limit %(ask)s", {"at": at, "id": kid, "org": org, "ask": ask})
            return [((r["created_at"].isoformat(), str(r["id"])), _key(r)) for r in rows]
        return await self._page(actor, "keys", "created_at.desc,id.desc", {}, limit, token, fetch)

    async def members(self, actor: api.Actor, limit: str | None,
                      token: str | None) -> api.ListPage[Member]:
        org = _org(actor)

        async def fetch(conn, after, ask):
            at, uid = after if after else (None, None)
            rows = await self._rows(
                conn, "select m.user_id, p.email, m.role, m.created_at from public.org_members m "
                      "left join public.profiles p on p.id = m.user_id "
                      "where (%(at)s::timestamptz is null or "
                      "(m.created_at, m.user_id) > (%(at)s::timestamptz, %(id)s::uuid)) "
                      "and m.org_id = %(org)s::uuid order by m.created_at, m.user_id "
                      "limit %(ask)s", {"at": at, "id": uid, "org": org, "ask": ask})
            return [((r["created_at"].isoformat(), str(r["user_id"])),
                     Member(user_id=_id(r["user_id"]), email=_opt_text(r["email"]),
                            role=_text(r["role"]), joined_at=_instant(r["created_at"])))
                    for r in rows]
        return await self._page(actor, "members", "created_at.asc,user_id.asc", {}, limit,
                                token, fetch)

    # ------------------------------------------------------------------------- operator
    async def accounts(self, actor: api.Actor, limit: str | None,
                       token: str | None) -> api.ListPage[OperatorAccount]:
        # ponytail: console_admin_orgs counts 30 days of usage per joined org; pilot volume.
        async def fetch(conn, after, ask):
            at, wid = after if after else (None, None)
            rows = await self._rows(
                conn, "select w.wallet_id, w.owner_user_id, w.org_id, w.ledger_total, "
                      "w.reserved_total, w.available, w.signup_granted_at, w.updated_at, "
                      "o.owner_email, o.suspended, o.suspension_reason "
                      "from public.console_credit_wallets w "
                      "join public.console_admin_orgs o on o.org_id = w.org_id "
                      "where w.kind = 'consumer' and (%(at)s::timestamptz is null or "
                      "(w.updated_at, w.wallet_id) < (%(at)s::timestamptz, %(id)s::uuid)) "
                      "order by w.updated_at desc, w.wallet_id desc limit %(ask)s",
                {"at": at, "id": wid, "ask": ask})
            return [((r["updated_at"].isoformat(), str(r["wallet_id"])), _account(r))
                    for r in rows]
        return await self._page(actor, "accounts", "updated_at.desc,wallet_id.desc", {}, limit,
                                token, fetch, operator=True)

    async def wallet_drift(self, actor: api.Actor, limit: str | None,
                           token: str | None) -> api.ListPage[WalletDrift]:
        async def fetch(conn, after, ask):
            rows = await self._rows(
                conn, "select wallet_id, kind, ledger_drift, reserved_drift "
                      "from public.operator_wallet_drift where (%(id)s::uuid is null or "
                      "wallet_id > %(id)s::uuid) order by wallet_id limit %(ask)s",
                {"id": after[0] if after else None, "ask": ask})
            return [((str(r["wallet_id"]),),
                     WalletDrift(wallet_id=_id(r["wallet_id"]), kind=_text(r["kind"]),
                                 ledger_drift=_money(r["ledger_drift"], "CREDIT"),
                                 reserved_drift=_money(r["reserved_drift"], "CREDIT")))
                    for r in rows]
        return await self._page(actor, "wallet-drift", "wallet_id.asc", {}, limit, token, fetch,
                                operator=True)

    async def unknown_usage(self, actor: api.Actor, limit: str | None,
                            token: str | None) -> api.ListPage[UnknownUsage]:
        async def fetch(conn, after, ask):
            at, rid = after if after else (None, None)
            rows = await self._rows(
                conn, "select request_id, org_id, created_at, reconcile_after, unit, hold "
                      "from public.operator_unknown_usage where (%(at)s::timestamptz is null or "
                      "(created_at, request_id) > (%(at)s::timestamptz, %(id)s::uuid)) "
                      "order by created_at, request_id limit %(ask)s",
                {"at": at, "id": rid, "ask": ask})
            return [((r["created_at"].isoformat(), str(r["request_id"])), _unknown(r))
                    for r in rows]
        return await self._page(actor, "unknown-usage", "created_at.asc,request_id.asc", {},
                                limit, token, fetch, operator=True)

    async def audit(self, actor: api.Actor, limit: str | None,
                    token: str | None) -> api.ListPage[AuditEntry]:
        async def fetch(conn, after, ask):
            at, aid = after if after else (None, None)
            rows = await self._rows(
                conn, "select id, at, actor_principal, action, target_org_id, reason, "
                      "idempotency_key from public.operator_audit where "
                      "(%(at)s::timestamptz is null or (at, id) < (%(at)s::timestamptz, "
                      "%(id)s::uuid)) order by at desc, id desc limit %(ask)s",
                {"at": at, "id": aid, "ask": ask})
            return [((r["at"].isoformat(), str(r["id"])),
                     AuditEntry(id=_id(r["id"]), at=_instant(r["at"]),
                                actor=_text(r["actor_principal"]), action=_text(r["action"]),
                                target_org_id=_opt_id(r["target_org_id"]),
                                reason=_text(r["reason"]),
                                idempotency_key=_opt_text(r["idempotency_key"])))
                    for r in rows]
        return await self._page(actor, "audit", "at.desc,id.desc", {}, limit, token, fetch,
                                operator=True)


# ------------------------------------------------------------------------------ helpers
def _user(actor: api.Actor) -> str:
    """Only a verified web session reads the console; a key audience is refused (R270)."""
    if actor.audience != "session" or not actor.user_id or not _UUID.match(actor.user_id):
        raise errors.Forbidden("the console reads need a signed-in individual")
    return actor.user_id


def _org(actor: api.Actor) -> str:
    _user(actor)
    if not actor.org_id or not _UUID.match(actor.org_id):
        raise errors.NotFound("no personal organization")
    return actor.org_id


async def _require_operator(conn: Any, actor: api.Actor) -> None:
    """Operator authority twice: the server-derived actor AND the database's own check."""
    if not actor.operator:
        raise errors.Forbidden("operator authority is required")
    cur = await conn.execute("select public.is_operator()")
    (yes,) = await cur.fetchone()
    if yes is not True:
        raise errors.Forbidden("operator authority is required")


def _entry(r: Mapping[str, Any]) -> LedgerEntry:
    return LedgerEntry(entry_id=_id(r["entry_id"]), created_at=_instant(r["created_at"]),
                       kind=_text(r["kind"]), amount=_money(r["amount"], "CREDIT"),
                       request_id=_opt_id(r["request_id"]), reason=_text(r["reason"]))


def _key(r: Mapping[str, Any]) -> KeySummary:
    return KeySummary(id=_id(r["id"]), name=_text(r["name"]), prefix=_text(r["prefix"]),
                      created_at=_instant(r["created_at"]),
                      last_used_at=_opt_instant(r["last_used_at"]),
                      revoked_at=_opt_instant(r["revoked_at"]))


def _account(r: Mapping[str, Any]) -> OperatorAccount:
    return OperatorAccount(
        user_id=_id(r["owner_user_id"]), org_id=_id(r["org_id"]), wallet_id=_id(r["wallet_id"]),
        email=_opt_text(r["owner_email"]), suspended=_flag(r["suspended"]),
        suspension_reason=_opt_text(r["suspension_reason"]),
        ledger_total=_money(r["ledger_total"], "CREDIT"),
        reserved_total=_money(r["reserved_total"], "CREDIT"),
        available=_money(r["available"], "CREDIT"),
        signup_granted_at=_opt_instant(r["signup_granted_at"]))


def _unknown(r: Mapping[str, Any]) -> UnknownUsage:
    unit = r["unit"]
    if unit not in ("CREDIT", "USD"):
        raise _malformed("an unknown-usage hold has no unit")
    return UnknownUsage(request_id=_id(r["request_id"]), org_id=_id(r["org_id"]),
                        created_at=_instant(r["created_at"]),
                        reconcile_after=_opt_instant(r["reconcile_after"]),
                        hold=_opt_money(r["hold"], unit))
