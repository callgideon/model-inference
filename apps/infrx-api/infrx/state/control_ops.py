"""AP-00 slice 00d (R270/R271, migration 0060): the durable store of long control operations.

New: no generic operation store existed (the job store, the Lab leases of 0016/0029 and the
operator audit-log idempotency are each their own domain's); this one is shared by every
wave-7 long operation (artifact verification/import, deployment, ...).

The `ControlOps` protocol (method names fixed by the wave-7 plan; `PgControlOps` over 0060
and `FakeControlOps` in memory obey the same rules, proven by one scenario set run on both,
`tests/d/test_control_ops_units.py` and `tests/d/test_control_ops.py`):

    start(kind, actor, idempotency_key, input_hash, *, resource_kind=None, resource_id=None,
          outcome=None, retention_s=86400) -> Started(operation, replayed, outcome)
        The key's scope is the actor's tenant (provider workspace, else organization, else
        user) + `kind`. Same key + same `input_hash` (`input_hash(body)`) replays the first
        operation and `outcome`; another hash -> IdempotencyConflict (409); a key past its
        retention (>= 24 h) -> IdempotencyExpired (410), never a second operation. A new
        operation is `queued`, fence 0. An unstorable kind (dotted lower case,
        `deployment.create`), hash, key (1-255 chars), retention or tenant-less actor ->
        InvalidRequest.
    lease(operation_id, owner, ttl_s) -> Operation
        ttl 1-3600 s. A free or expired lease is granted with fence + 1 (queued -> running;
        cancel_requested stays so the holder reconciles); the holder renews its live lease
        under the same fence; another holder's live lease or a finished operation -> Conflict.
    advance(operation_id, fence, phase, retry_after_s=None) -> Operation
        Only under the live lease's fence (else Conflict); never changes the state.
    finish(operation_id, fence, state, error=None) -> Operation
        state succeeded | failed | cancelled; `error` (api.ErrorBody) iff failed (else
        InvalidRequest); the live fence only (else Conflict); clears the lease.
    cancel(operation_id, actor) -> Operation
        queued -> cancelled; running -> cancel_requested (first instant kept); any other
        state answers as it is - a repeat, or a cancel that lost the race to the worker's
        finish (0066, api-artifacts' request; 0060 refused succeeded/failed with Conflict).
    get(operation_id, actor) -> Operation
    pending(kinds, limit=25) -> tuple[operation_id, ...]
        The oldest unfinished operations of those kinds no live lease holds (a controller's
        discovery after a restart); limit clamped to 1-100.

Read authority (get, cancel): the operation's owning tenant or an operator actor; anyone
else, an unknown id or a non-uuid id -> NotFound (404). Every id is a string uuid. The actor
always comes from `rt.actors` (R270), never from a request field.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from ..contracts import api, codec, errors
from . import rpc
from .jobstore import domain_error

if TYPE_CHECKING:
    from .jobstore import Connect

RETENTION_S = 86_400                     # contracts.md §2: receipts answer for at least 24 h
TERMINAL = api.TERMINAL_STATES
FINISHED = ("succeeded", "failed", "cancelled")


def input_hash(body: Any) -> str:
    """The canonical input hash an `Idempotency-Key` is stored with: sha256 of the body's
    compact canonical JSON (`infrx.contracts.codec`, sorted keys)."""
    return "sha256:" + hashlib.sha256(codec.compact_bytes(body)).hexdigest()


class Operation(api.Wire):
    """One stored operation: R270's `OperationDoc` fields plus the actor, the lease and the
    cancellation instant (a worker's view; `doc()` is the wire's)."""

    operation_id: str
    kind: str
    state: api.OperationState
    phase: str | None = None
    resource_kind: str | None = None
    resource_id: str | None = None
    actor: api.Actor
    created_at: str
    updated_at: str
    retry_after_s: int | None = None
    error: api.ErrorBody | None = None
    fence: int = 0
    lease_owner: str | None = None
    lease_until: str | None = None
    cancel_requested_at: str | None = None

    def doc(self) -> api.OperationDoc:
        return api.OperationDoc(**{k: getattr(self, k) for k in api.OperationDoc.model_fields})


class Started(api.Wire):
    operation: Operation
    replayed: bool
    outcome: dict[str, Any] | None = None


class ControlOps(Protocol):
    async def start(self, kind: str, actor: api.Actor, idempotency_key: str, input_hash: str,
                    *, resource_kind: str | None = None, resource_id: str | None = None,
                    outcome: dict[str, Any] | None = None,
                    retention_s: int = RETENTION_S) -> Started: ...

    async def lease(self, operation_id: str, owner: str, ttl_s: int) -> Operation: ...

    async def advance(self, operation_id: str, fence: int, phase: str,
                      retry_after_s: int | None = None) -> Operation: ...

    async def finish(self, operation_id: str, fence: int, state: str,
                     error: api.ErrorBody | None = None) -> Operation: ...

    async def cancel(self, operation_id: str, actor: api.Actor) -> Operation: ...

    async def get(self, operation_id: str, actor: api.Actor) -> Operation: ...

    async def pending(self, kinds: Sequence[str], limit: int = 25) -> tuple[str, ...]: ...


class PgControlOps:
    """`ControlOps` over 0060's functions, one connection per call (`state.rpc`). A SQL
    refusal is its typed error; an unreachable database is a retryable 503."""

    def __init__(self, connect: Connect) -> None:
        self._connect = connect

    async def _call(self, name: str, args: dict[str, Any]) -> Any:
        return await rpc.call(self._connect, f"control_op_{name}", args, error=domain_error,
                              unreachable="the control operation store is unreachable")

    async def start(self, kind: str, actor: api.Actor, idempotency_key: str, input_hash: str,
                    *, resource_kind: str | None = None, resource_id: str | None = None,
                    outcome: dict[str, Any] | None = None,
                    retention_s: int = RETENTION_S) -> Started:
        return Started.model_validate(await self._call("start", {
            "kind": kind, "actor": actor.model_dump(mode="json"),
            "idempotency_key": idempotency_key, "input_hash": input_hash,
            "resource_kind": resource_kind, "resource_id": resource_id, "outcome": outcome,
            "retention_s": retention_s}))

    async def lease(self, operation_id: str, owner: str, ttl_s: int) -> Operation:
        return Operation.model_validate(await self._call("lease", {
            "operation_id": operation_id, "owner": owner, "ttl_s": ttl_s}))

    async def advance(self, operation_id: str, fence: int, phase: str,
                      retry_after_s: int | None = None) -> Operation:
        return Operation.model_validate(await self._call("advance", {
            "operation_id": operation_id, "fence": fence, "phase": phase,
            "retry_after_s": retry_after_s}))

    async def finish(self, operation_id: str, fence: int, state: str,
                     error: api.ErrorBody | None = None) -> Operation:
        return Operation.model_validate(await self._call("finish", {
            "operation_id": operation_id, "fence": fence, "state": state,
            "error": error.model_dump(mode="json") if error else None}))

    async def cancel(self, operation_id: str, actor: api.Actor) -> Operation:
        return Operation.model_validate(await self._call("cancel", {
            "operation_id": operation_id, "actor": actor.model_dump(mode="json")}))

    async def get(self, operation_id: str, actor: api.Actor) -> Operation:
        return Operation.model_validate(await self._call("get", {
            "operation_id": operation_id, "actor": actor.model_dump(mode="json")}))

    async def pending(self, kinds: Sequence[str], limit: int = 25) -> tuple[str, ...]:
        return tuple(await self._call("pending", {"kinds": list(kinds), "limit": limit}))


# ------------------------------------------------------------------------ the fake
KIND = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
HASH = re.compile(r"sha256:[0-9a-f]{64}")


def owner_of(actor: api.Actor) -> str | None:
    """0060's `infrx.control_owner`: provider workspace, else organization, else user."""
    if actor.provider_org_id is not None:
        return f"provider:{actor.provider_org_id}"
    if actor.org_id is not None:
        return f"org:{actor.org_id}"
    if actor.user_id is not None:
        return f"user:{actor.user_id}"
    return None


def _ts(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class _Key:
    input_hash: str
    operation_id: str
    outcome: dict[str, Any] | None
    expires_at: datetime


@dataclass
class _Row:
    op: Operation
    created: datetime
    lease_until: datetime | None = None


@dataclass
class FakeControlOps:
    """`ControlOps` in memory for other lanes' tests, with 0060's rules and refusals (the
    scenario set of tests/d/test_control_ops_units.py passes on both)."""

    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _rows: dict[str, _Row] = field(default_factory=dict)
    _keys: dict[tuple[str, str], _Key] = field(default_factory=dict)

    def _row(self, operation_id: str) -> _Row:
        row = self._rows.get(operation_id)
        if row is None:
            raise errors.NotFound("no such operation")
        return row

    def _visible(self, operation_id: str, actor: api.Actor) -> _Row:
        row = self._row(operation_id)
        if not (actor.operator or owner_of(row.op.actor) == owner_of(actor)):
            raise errors.NotFound("no such operation")
        return row

    def _put(self, row: _Row, **changes: Any) -> Operation:
        now = self.now()
        if "lease_until" in changes:
            row.lease_until = changes["lease_until"]
            changes["lease_until"] = row.lease_until and _ts(row.lease_until)
        row.op = row.op.model_copy(update={**changes, "updated_at": _ts(now)})
        return row.op

    def _live(self, row: _Row, fence: int) -> None:
        if row.op.fence != fence or row.lease_until is None or row.lease_until <= self.now():
            raise errors.Conflict("stale fence: the lease was lost")

    async def start(self, kind: str, actor: api.Actor, idempotency_key: str, input_hash: str,
                    *, resource_kind: str | None = None, resource_id: str | None = None,
                    outcome: dict[str, Any] | None = None,
                    retention_s: int = RETENTION_S) -> Started:
        owner, now = owner_of(actor), self.now()
        if owner is None:
            raise errors.InvalidRequest("an operation needs an actor that names a tenant")
        if (not KIND.fullmatch(kind) or not HASH.fullmatch(input_hash)
                or not 1 <= len(idempotency_key) <= 255 or retention_s < RETENTION_S):
            raise errors.InvalidRequest("not a startable operation")
        scope = (f"{owner}/{kind}", idempotency_key)
        key = self._keys.get(scope)
        if key is not None:
            if key.expires_at <= now:
                raise errors.IdempotencyExpired("this Idempotency-Key has expired")
            if key.input_hash != input_hash:
                raise errors.IdempotencyConflict("this Idempotency-Key was used with a "
                                                 "different request")
            return Started(operation=self._rows[key.operation_id].op, replayed=True,
                           outcome=key.outcome)
        op = Operation(operation_id=str(uuid.uuid4()), kind=kind, state="queued",
                       resource_kind=resource_kind, resource_id=resource_id, actor=actor,
                       created_at=_ts(now), updated_at=_ts(now))
        self._rows[op.operation_id] = _Row(op, now)
        self._keys[scope] = _Key(input_hash, op.operation_id, outcome,
                                 now + timedelta(seconds=retention_s))
        return Started(operation=op, replayed=False, outcome=outcome)

    async def lease(self, operation_id: str, owner: str, ttl_s: int) -> Operation:
        row, now = self._row(operation_id), self.now()
        if not 1 <= len(owner) <= 128 or not 1 <= ttl_s <= 3600:
            raise errors.InvalidRequest("a lease needs an owner and a ttl of 1-3600 s")
        if row.op.state in TERMINAL:
            raise errors.Conflict("the operation is finished")
        until = now + timedelta(seconds=ttl_s)
        live = row.lease_until is not None and row.lease_until > now
        if live and row.op.lease_owner == owner:
            return self._put(row, lease_until=until)
        if live:
            raise errors.Conflict("the operation is leased by another worker")
        return self._put(row, lease_owner=owner, lease_until=until, fence=row.op.fence + 1,
                         state="running" if row.op.state == "queued" else row.op.state)

    async def advance(self, operation_id: str, fence: int, phase: str,
                      retry_after_s: int | None = None) -> Operation:
        row = self._row(operation_id)
        self._live(row, fence)
        if retry_after_s is not None and retry_after_s < 0:
            raise errors.InvalidRequest("not a phase or retry interval")
        return self._put(row, phase=phase, retry_after_s=retry_after_s)

    async def finish(self, operation_id: str, fence: int, state: str,
                     error: api.ErrorBody | None = None) -> Operation:
        row = self._row(operation_id)
        if state not in FINISHED:
            raise errors.InvalidRequest("an operation finishes succeeded, failed or cancelled")
        self._live(row, fence)
        if (state == "failed") != (error is not None):
            raise errors.InvalidRequest("a failed operation carries an error, no other does")
        return self._put(row, state=state, error=error, lease_owner=None, lease_until=None)

    async def cancel(self, operation_id: str, actor: api.Actor) -> Operation:
        row = self._visible(operation_id, actor)
        if row.op.state in ("queued", "running"):
            return self._put(row, cancel_requested_at=_ts(self.now()),
                             state="cancelled" if row.op.state == "queued" else "cancel_requested")
        return row.op

    async def get(self, operation_id: str, actor: api.Actor) -> Operation:
        return self._visible(operation_id, actor).op

    async def pending(self, kinds: Sequence[str], limit: int = 25) -> tuple[str, ...]:
        now = self.now()
        free = sorted((row.created, row.op.operation_id) for row in self._rows.values()
                      if row.op.kind in kinds and row.op.state not in TERMINAL
                      and (row.lease_until is None or row.lease_until <= now))
        return tuple(op_id for _, op_id in free[:min(max(limit, 1), 100)])
