"""AP-05: 0062's rows and the store over them (`PgHostingStore`, `FakeHostingStore`).

Every controller write (an allocation, a receipt, a state move) names the `Hold` it is made
under - the operation, its fence, the deployment - and is refused (`Conflict`) unless that is
the live lease of an unfinished operation on that deployment (PostgreSQL: `hosting_fence` in
the same transaction, the 0060 row locked until commit). A `health` receipt is the one
observation made outside an operation.
"""
from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, Protocol

from ...contracts import api, errors
from ...contracts.api import FieldError, Wire
from ...contracts.v2.records import DeploymentRevision, DeploymentState
from ...state.control_ops import FakeControlOps, Started

S = DeploymentState
ACTOR = "lab-hosting"                     # the audit principal of the controller's moves


@dataclasses.dataclass(frozen=True)
class Hold:
    """The lease a controller writes under."""

    operation_id: str
    fence: int
    deployment_revision_id: str


class Hosting(Wire):
    deployment_revision_id: str
    provider_org_id: str
    serving_version_id: str
    endpoint_id: str
    profile_id: str
    request: dict
    operation_id: str
    created_by: str
    created_at: datetime
    expires_at: datetime


class Allocation(Wire):
    allocation_id: str
    deployment_revision_id: str
    slot: str
    port: int
    resource_tag: str
    state: Literal["reserved", "launched", "released"] = "reserved"
    operation_id: str
    reserved_at: datetime
    launched_at: datetime | None = None
    released_at: datetime | None = None


class Receipt(Wire):
    receipt_id: str
    deployment_revision_id: str
    allocation_id: str
    kind: Literal["identity", "smoke", "health"]
    passed: bool
    observed: dict[str, Any]
    reasons: tuple[FieldError, ...] = ()
    operation_id: str | None = None
    checked_at: datetime
    expires_at: datetime


def tag(deployment_revision_id: str) -> str:
    """The stable provider resource tag a launcher finds this deployment's engine by."""
    return f"infrx-hosting-{deployment_revision_id}"


class HostingStore(Protocol):
    async def db_now(self) -> datetime: ...

    async def request(self, deployment: DeploymentRevision, *, profile_id: str, request: dict,
                      expire_after_s: int, actor: api.Actor, key: str,
                      input_hash: str) -> Started:
        """The draft revision, its hosting row and its `deployment.create` operation in one
        transaction; a replayed key answers the first operation (and writes nothing)."""

    async def hosting(self, deployment_revision_id: str) -> Hosting | None: ...
    async def live(self) -> list[Hosting]:
        """Every hosting deployment not retired, or still holding an allocation."""

    async def allocation(self, deployment_revision_id: str) -> Allocation | None:
        """Its newest allocation."""

    async def receipts(self, deployment_revision_id: str) -> list[Receipt]:
        """Its receipts, newest first."""

    async def active(self, deployment_revision_id: str) -> tuple[str, ...]:
        """Its unfinished operations, oldest first."""

    async def allocate(self, hold: Hold, allocation: Allocation) -> Allocation:
        """Reserve the slot; `CapacityUnavailable` while an unreleased allocation holds it.
        (The controller allocates only when the deployment holds none.)"""

    async def move(self, hold: Hold, allocation_id: str,
                   state: Literal["launched", "released"]) -> Allocation: ...

    async def record(self, hold: Hold | None, receipt: Receipt) -> Receipt: ...

    async def transition(self, hold: Hold, deployment_revision_id: str, provider_org_id: str,
                         expected: str, to: str, reason: str) -> DeploymentRevision:
        """0032's audited CAS on the dev revision's state, under the hold."""


# ======================================================================= the fake ===
@dataclasses.dataclass
class FakeHostingStore:
    """In memory over 0060's `FakeControlOps` and L3's `FakeControl` (the 0007 rows), with
    0062's rules: the fence, one unreleased allocation per slot and per deployment, forward
    allocation moves, immutable receipts."""

    ops: FakeControlOps
    control: Any                                   # infrx.lab.control.fakes.FakeControl
    now: Callable[[], datetime]
    hostings: dict[str, Hosting] = dataclasses.field(default_factory=dict)
    allocations: dict[str, Allocation] = dataclasses.field(default_factory=dict)
    receipt_rows: list[Receipt] = dataclasses.field(default_factory=list)

    async def db_now(self) -> datetime:
        return self.now()

    def _fence(self, hold: Hold) -> None:
        row = self.ops._rows.get(hold.operation_id)
        if row is None or row.op.resource_kind != "deployment" \
                or row.op.resource_id != hold.deployment_revision_id:
            raise errors.NotFound("no such operation on this deployment")
        if row.op.fence != hold.fence or row.lease_until is None \
                or row.lease_until <= self.now() or row.op.state in api.TERMINAL_STATES:
            raise errors.Conflict("stale fence: the lease was lost")

    async def request(self, deployment, *, profile_id, request, expire_after_s, actor, key,
                      input_hash):
        from datetime import timedelta
        started = await self.ops.start("deployment.create", actor, key, input_hash,
                                       resource_kind="deployment",
                                       resource_id=deployment.deployment_revision_id)
        if not started.replayed:
            now = self.now()
            self.control.put_now(deployment.model_copy(update={"created_at": now}))
            self.hostings[deployment.deployment_revision_id] = Hosting(
                deployment_revision_id=deployment.deployment_revision_id,
                provider_org_id=deployment.provider_org_id,
                serving_version_id=deployment.serving_version_id,
                endpoint_id=deployment.endpoint_id, profile_id=profile_id, request=request,
                operation_id=started.operation.operation_id,
                created_by=actor.user_id or "operator", created_at=now,
                expires_at=now + timedelta(seconds=expire_after_s))
        return started

    async def hosting(self, deployment_revision_id):
        return self.hostings.get(deployment_revision_id)

    async def live(self):
        return [h for h in self.hostings.values()
                if self.control.deployments[h.deployment_revision_id].state is not S.retired
                or any(a.state != "released" for a in self.allocations.values()
                       if a.deployment_revision_id == h.deployment_revision_id)]

    async def allocation(self, deployment_revision_id):
        mine = [a for a in self.allocations.values()
                if a.deployment_revision_id == deployment_revision_id]
        return max(mine, key=lambda a: a.reserved_at, default=None)

    async def receipts(self, deployment_revision_id):
        return [r for r in reversed(self.receipt_rows)
                if r.deployment_revision_id == deployment_revision_id]

    async def active(self, deployment_revision_id):
        rows = sorted((r.created, r.op.operation_id) for r in self.ops._rows.values()
                      if r.op.resource_kind == "deployment"
                      and r.op.resource_id == deployment_revision_id
                      and r.op.state not in api.TERMINAL_STATES)
        return tuple(op_id for _, op_id in rows)

    async def allocate(self, hold, allocation):
        self._fence(hold)
        if any(a.state != "released" and a.slot == allocation.slot
               for a in self.allocations.values()):
            raise errors.CapacityUnavailable(f"slot {allocation.slot} is taken")
        self.allocations[allocation.allocation_id] = allocation
        return allocation

    async def move(self, hold, allocation_id, state):
        self._fence(hold)
        a = self.allocations[allocation_id]
        stamp = {"launched_at" if state == "launched" else "released_at": self.now()}
        moved = self.allocations[allocation_id] = a.model_copy(update={"state": state, **stamp})
        return moved

    async def record(self, hold, receipt):
        if hold is not None:
            self._fence(hold)
        self.receipt_rows.append(receipt)
        return receipt

    async def transition(self, hold, deployment_revision_id, provider_org_id, expected, to,
                         reason):
        self._fence(hold)
        return await self.control.transition(deployment_revision_id, provider_org_id,
                                             expected=S(expected), to=S(to), actor=ACTOR,
                                             reason=reason)


# ================================================================== PostgreSQL ===
def _refusal(failed: Exception) -> Exception:
    from psycopg import errors as pg

    from ...state.jobstore import domain_error
    if isinstance(failed, pg.UniqueViolation):
        return errors.CapacityUnavailable(getattr(failed.diag, "constraint_name", None)
                                          or "the slot is taken")
    if isinstance(failed, (pg.CheckViolation, pg.NotNullViolation)):
        return errors.StateConflict(getattr(failed.diag, "constraint_name", None) or "check")
    return domain_error(failed)


def _uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _load(kind: type[Wire], row: tuple) -> Any:
    return kind.model_validate({name: (str(v) if isinstance(v, uuid.UUID) else v)
                                for name, v in zip(kind.model_fields, row)})


class PgHostingStore:
    """0062 through the Lab control login; one connection per call (`state.rpc`), and one
    transaction per fenced write (`hosting_fence` first)."""

    def __init__(self, connect) -> None:
        self._connect = connect

    async def _rows(self, sql: str, params: Any = ()) -> list[tuple]:
        from ...state import rpc
        return await rpc.rows(self._connect, sql, params, error=_refusal)

    async def _fenced(self, hold: Hold, sql: str, params: Any) -> list[tuple]:
        from psycopg import Error
        from psycopg.types.json import Jsonb

        from ...state import rpc
        async with rpc.connection(self._connect) as conn:
            try:
                async with conn.transaction():
                    await conn.execute("select infrx.hosting_fence(%s)", (Jsonb({
                        "operation_id": hold.operation_id, "fence": hold.fence,
                        "deployment_revision_id": hold.deployment_revision_id}),))
                    cursor = await conn.execute(sql, params)
                    return await cursor.fetchall() if cursor.description else []
            except Error as failed:
                raise _refusal(failed) from None

    @staticmethod
    def _select(kind: type[Wire], table: str) -> str:
        return f"select {', '.join(kind.model_fields)} from infrx.{table}"

    async def db_now(self) -> datetime:
        return (await self._rows("select infrx.now()"))[0][0]

    async def request(self, deployment, *, profile_id, request, expire_after_s, actor, key,
                      input_hash):
        from ...state import rpc
        return Started.model_validate(await rpc.call(self._connect, "hosting_request", {
            "deployment": deployment.model_dump(mode="json"), "profile_id": profile_id,
            "request": request, "expire_after_s": expire_after_s,
            "actor": actor.model_dump(mode="json"), "idempotency_key": key,
            "input_hash": input_hash}, error=_refusal))

    async def hosting(self, deployment_revision_id):
        if not _uuid(deployment_revision_id):
            return None
        found = await self._rows(self._select(Hosting, "hosting_deployments")
                                 + " where deployment_revision_id = %s", (deployment_revision_id,))
        return _load(Hosting, found[0]) if found else None

    async def live(self):
        columns = ", ".join(f"h.{c}" for c in Hosting.model_fields)
        return [_load(Hosting, row) for row in await self._rows(
            f"select {columns} from infrx.hosting_deployments h join "
            "infrx.deployment_revisions d using (deployment_revision_id) "
            "where d.state <> 'retired' or exists (select 1 from infrx.hosting_allocations a "
            "where a.deployment_revision_id = h.deployment_revision_id "
            "and a.state <> 'released') order by h.created_at")]

    async def allocation(self, deployment_revision_id):
        found = await self._rows(self._select(Allocation, "hosting_allocations")
                                 + " where deployment_revision_id = %s "
                                 "order by reserved_at desc limit 1", (deployment_revision_id,))
        return _load(Allocation, found[0]) if found else None

    async def receipts(self, deployment_revision_id):
        return [_load(Receipt, row) for row in await self._rows(
            self._select(Receipt, "hosting_receipts")
            + " where deployment_revision_id = %s order by recorded desc",
            (deployment_revision_id,))]

    async def active(self, deployment_revision_id):
        from ...state import rpc
        return tuple(await rpc.call(self._connect, "hosting_active", {
            "deployment_revision_id": deployment_revision_id}, error=_refusal))

    async def allocate(self, hold, allocation):
        a = allocation
        found = await self._fenced(hold, "insert into infrx.hosting_allocations (allocation_id, "
                                   "deployment_revision_id, slot, port, resource_tag, "
                                   "operation_id, reserved_at) values (%s, %s, %s, %s, %s, %s, "
                                   f"infrx.now()) returning {', '.join(Allocation.model_fields)}",
                                   (a.allocation_id, a.deployment_revision_id, a.slot, a.port,
                                    a.resource_tag, a.operation_id))
        return _load(Allocation, found[0])

    async def move(self, hold, allocation_id, state):
        stamp = "launched_at" if state == "launched" else "released_at"
        found = await self._fenced(
            hold, f"update infrx.hosting_allocations set state = %s, "
            f"{stamp} = coalesce({stamp}, infrx.now()) where allocation_id = %s "
            f"and deployment_revision_id = %s returning {', '.join(Allocation.model_fields)}",
            (state, allocation_id, hold.deployment_revision_id))
        if not found:
            raise errors.NotFound("no such allocation of this deployment")
        return _load(Allocation, found[0])

    async def record(self, hold, receipt):
        from psycopg.types.json import Jsonb
        r = receipt.model_dump(mode="json")
        sql = ("insert into infrx.hosting_receipts (receipt_id, deployment_revision_id, "
               "allocation_id, kind, passed, observed, reasons, operation_id, checked_at, "
               "expires_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning receipt_id")
        params = (r["receipt_id"], r["deployment_revision_id"], r["allocation_id"], r["kind"],
                  r["passed"], Jsonb(r["observed"]), Jsonb(r["reasons"]), r["operation_id"],
                  receipt.checked_at, receipt.expires_at)
        if hold is None:
            await self._rows(sql, params)
        else:
            await self._fenced(hold, sql, params)
        return receipt

    async def transition(self, hold, deployment_revision_id, provider_org_id, expected, to,
                         reason):
        from psycopg.types.json import Jsonb

        from ...state.lab_control import _deployment
        found = await self._fenced(hold, "select infrx.lab_control_transition(%s)", (Jsonb({
            "deployment_revision_id": deployment_revision_id,
            "provider_org_id": provider_org_id, "expected": expected, "to": to,
            "actor": ACTOR, "reason": reason}),))
        return _deployment(found[0][0])
