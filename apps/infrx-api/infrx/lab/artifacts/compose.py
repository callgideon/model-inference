"""AP-04 composition (WR-AP04-2): the artifact surface on 0060's durable operations.

`DurableOps` is AP-04's operation port (`store.ControlOps`, what `uploads`/`imports`/the
routes call) over api-schema's 0060 store (`state.control_ops`: `PgControlOps`, and
`FakeControlOps` in tests). It replaces `store.MemoryControlOps` wherever AP-04 is composed:
an operation now outlives the process that started or worked it.
"""
from __future__ import annotations

from ...contracts import api, errors
from ...state import control_ops
from .store import Operation

#: who reads/cancels for the system: the routes scope a read to the caller's workspace
#: themselves (`routes/lab_artifacts.py`), the worker reads nothing
SYSTEM = api.Actor(audience="operator", operator=True)


def _op(op: control_ops.Operation) -> Operation:
    return Operation(op.doc(), op.actor, op.resource_kind or "", "", "", "", op.fence,
                     op.lease_owner)


class DurableOps:
    """AP-04's `ControlOps` over 0060's. The mapping: `start`'s scope is 0060's own (the
    actor's tenant + kind); a lease another holder has (or a finished operation) is None,
    not a refusal; a lost fence is `StaleLease`. Every `advance` first renews the caller's
    own lease (the heartbeat: one phase is one file, a whole verification may outlast the
    ttl)."""

    def __init__(self, ops: control_ops.ControlOps) -> None:
        self.ops = ops
        self.held: dict[tuple[str, int], tuple[str, int]] = {}   # (op, fence) -> (owner, ttl)

    async def start(self, *, kind: str, resource_kind: str, resource_id: str,
                    actor: api.Actor, scope: str, key: str,
                    input_hash: str) -> tuple[Operation, bool]:
        started = await self.ops.start(kind, actor, key, input_hash,
                                       resource_kind=resource_kind, resource_id=resource_id)
        return _op(started.operation), started.replayed

    async def get(self, operation_id: str) -> Operation:
        return _op(await self.ops.get(operation_id, SYSTEM))

    async def lease(self, operation_id: str, owner: str, ttl_s: int) -> int | None:
        try:
            op = await self.ops.lease(operation_id, owner, ttl_s)
        except errors.Conflict:
            return None
        self.held[(operation_id, op.fence)] = (owner, ttl_s)
        return op.fence

    async def advance(self, operation_id: str, fence: int, phase: str) -> Operation:
        try:
            if (operation_id, fence) in self.held:
                await self.ops.lease(operation_id, *self.held[(operation_id, fence)])
            return _op(await self.ops.advance(operation_id, fence, phase))
        except errors.Conflict:
            raise errors.StaleLease("a newer lease holds this operation") from None

    async def finish(self, operation_id: str, fence: int, state: str,
                     error: api.ErrorBody | None = None) -> Operation:
        self.held.pop((operation_id, fence), None)
        try:
            return _op(await self.ops.finish(operation_id, fence, state, error))
        except errors.Conflict:
            raise errors.StaleLease("a newer lease holds this operation") from None

    async def cancel(self, operation_id: str) -> Operation:
        return _op(await self.ops.cancel(operation_id, SYSTEM))


__all__ = ["DurableOps"]
