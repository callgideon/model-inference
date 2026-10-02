#!/usr/bin/env python3
"""AP-00 slice 00d: the `ControlOps` protocol's scenarios, run here on `FakeControlOps` (no
database) and by `test_control_ops.py` on `PgControlOps` over 0060 - one set of rules, two
stores, so the fake other lanes build on is the store they will get. The last cases are
`PgControlOps`'s own half (what it sends, how it reads answers and refusals) on a recording
connection. `test_control_ops_mutants.py`'s Python list runs here.

    uv run --frozen pytest -q tests/d/test_control_ops_units.py
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import psycopg
from infrx.contracts import api, errors
from infrx.state.control_ops import FakeControlOps, PgControlOps, input_hash

from .test_adapter_units import _Conn, _db_error

H1, H2 = input_hash({"body": 1}), input_hash({"body": 2})
FAILURE = api.ErrorBody(code="engine_unready", message="the engine never became ready",
                        request_id="op", retryable=True)


def uid() -> str:
    return str(uuid.uuid4())


def actor(*, provider: str | None = None, org: str | None = None, user: str | None = None,
          audience: str = "session", operator: bool = False) -> api.Actor:
    return api.Actor(audience=audience, provider_org_id=provider, org_id=org,  # type: ignore[arg-type]
                     user_id=user, operator=operator)


def kind() -> str:
    """A kind no other scenario uses (the PostgreSQL world is shared by every check)."""
    return f"probe.k{uuid.uuid4().hex[:12]}"


async def refused(cls: type[BaseException], call) -> BaseException:
    """The awaited call is refused with exactly `cls`; anything else is an assertion."""
    try:
        await call
    except cls as refusal:
        return refusal
    except Exception as other:
        raise AssertionError(f"expected {cls.__name__}, got {type(other).__name__}: "
                             f"{other}") from None
    raise AssertionError(f"{cls.__name__} was not raised")


async def leased(env, k: str | None = None, ttl_s: int = 30, who=None):
    who = who or actor(provider=uid(), user=uid())
    started = await env.ops.start(k or kind(), who, uid(), H1)
    return who, await env.ops.lease(started.operation.operation_id, "w1", ttl_s)


# ----------------------------------------------------------------------- scenarios
async def start_replays_the_same_request_and_refuses_another(env) -> str:
    ops, k, workspace = env.ops, kind(), uid()
    a = actor(provider=workspace, user=uid())
    first = await ops.start(k, a, "key-1", H1, resource_kind="artifact", resource_id="art-1",
                            outcome={"upload_id": "u1"})
    op = first.operation
    assert not first.replayed and first.outcome == {"upload_id": "u1"}, first
    assert (op.state, op.fence, op.phase, op.lease_owner) == ("queued", 0, None, None), op
    assert (op.kind, op.actor, op.resource_kind, op.resource_id) == (k, a, "artifact", "art-1")
    doc = op.doc()
    assert doc.operation_id == op.operation_id and doc.created_at.endswith("Z"), doc
    again = await ops.start(k, a, "key-1", H1)
    assert again.replayed and again.operation.operation_id == op.operation_id, again
    assert again.outcome == {"upload_id": "u1"}, "a replay answers the first outcome"
    teammate = await ops.start(k, actor(provider=workspace, user=uid()), "key-1", H1)
    assert teammate.replayed and teammate.operation.operation_id == op.operation_id, \
        "the key is scoped to the workspace and the action"
    conflict = await refused(errors.IdempotencyConflict, ops.start(k, a, "key-1", H2))
    other_kind = await ops.start(kind(), a, "key-1", H2)
    other_tenant = await ops.start(k, actor(provider=uid(), user=a.user_id), "key-1", H2)
    ids = {op.operation_id, other_kind.operation.operation_id,
           other_tenant.operation.operation_id}
    assert len(ids) == 3 and not other_kind.replayed and not other_tenant.replayed, \
        "another action or another tenant reuses a key"
    return f"replayed x2, {type(conflict).__name__} on a new body, 3 scopes"


async def start_refuses_what_it_cannot_store(env) -> str:
    ops, a, k = env.ops, actor(org=uid(), user=uid(), audience="consumer"), kind()
    for bad in (ops.start("Deploy", a, uid(), H1),               # not a dotted lower-case kind
                ops.start("deployment", a, uid(), H1),           # one segment
                ops.start(k, a, uid(), "md5:abc"),              # not a sha256 hash
                ops.start(k, a, "", H1),                         # no key
                ops.start(k, a, "x" * 256, H1),                  # a key over 255
                ops.start(k, a, uid(), H1, retention_s=3600),    # under the 24 h receipt
                ops.start(k, actor(audience="session"), uid(), H1)):  # an actor with no tenant
        await refused(errors.InvalidRequest, bad)
    longest = await ops.start(k, a, "x" * 255, H1)
    assert longest.operation.state == "queued"
    return "7 unstorable starts refused; a 255-character key stored"


async def an_expired_key_is_refused_never_restarted(env) -> str:
    ops, k, a = env.ops, kind(), actor(provider=uid(), user=uid())
    day = await ops.start(k, a, "day", H1)
    two = await ops.start(k, a, "two", H1, retention_s=2 * 86400)
    await env.advance(86400 + 1)
    await refused(errors.IdempotencyExpired, ops.start(k, a, "day", H1))
    await refused(errors.IdempotencyExpired, ops.start(k, a, "day", H2))
    kept = await ops.get(day.operation.operation_id, a)
    assert kept.state == "queued", "the receipt outlives its key"
    longer = await ops.start(k, a, "two", H1)
    assert longer.replayed and longer.operation.operation_id == two.operation.operation_id
    return "a 24 h key is 410 after a day; a 48 h key still replays"


async def a_lease_is_fenced_and_expires(env) -> str:
    ops = env.ops
    _, first = await leased(env, ttl_s=30)
    op_id = first.operation_id
    assert (first.state, first.fence, first.lease_owner) == ("running", 1, "w1"), first
    assert first.lease_until is not None
    await refused(errors.Conflict, ops.lease(op_id, "w2", 30))
    renewed = await ops.lease(op_id, "w1", 60)
    assert (renewed.fence, renewed.lease_owner) == (1, "w1"), "a renewal keeps the fence"
    moved = await ops.advance(op_id, 1, "capacity", retry_after_s=2)
    assert (moved.state, moved.phase, moved.retry_after_s) == ("running", "capacity", 2), moved
    await refused(errors.Conflict, ops.advance(op_id, 0, "forged"))
    await refused(errors.Conflict, ops.advance(op_id, 2, "future"))
    await env.advance(61)
    await refused(errors.Conflict, ops.advance(op_id, 1, "late"))
    await refused(errors.Conflict, ops.finish(op_id, 1, "succeeded"))
    taken = await ops.lease(op_id, "w2", 30)
    assert (taken.fence, taken.lease_owner, taken.phase) == (2, "w2", "capacity"), taken
    await refused(errors.Conflict, ops.finish(op_id, 1, "succeeded"))
    done = await ops.finish(op_id, 2, "succeeded")
    assert (done.state, done.lease_owner, done.lease_until, done.error) == \
        ("succeeded", None, None, None), done
    await refused(errors.Conflict, ops.lease(op_id, "w3", 30))
    await refused(errors.Conflict, ops.advance(op_id, 2, "after"))
    await refused(errors.Conflict, ops.finish(op_id, 2, "failed", FAILURE))
    other = (await ops.start(kind(), actor(user=uid()), uid(), H1)).operation
    for bad in (ops.lease(other.operation_id, "w1", 0), ops.lease(other.operation_id, "w1", 3601),
                ops.lease(other.operation_id, "", 30), ops.lease(other.operation_id, "w" * 129, 30)):
        await refused(errors.InvalidRequest, bad)
    edge = await ops.lease(other.operation_id, "w" * 128, 3600)
    assert edge.fence == 1
    await refused(errors.InvalidRequest, ops.advance(other.operation_id, 1, "x", retry_after_s=-1))
    assert (await ops.advance(other.operation_id, 1, "x", retry_after_s=0)).retry_after_s == 0
    return "fence 1 -> renewed 1 -> expired -> 2; stale and finished fences refused"


async def finish_records_one_terminal_state(env) -> str:
    ops = env.ops
    _, op = await leased(env)
    op_id, fence = op.operation_id, op.fence
    for bad in (ops.finish(op_id, fence, "running"), ops.finish(op_id, fence, "failed"),
                ops.finish(op_id, fence, "succeeded", FAILURE),
                ops.finish(op_id, fence, "cancelled", FAILURE)):
        await refused(errors.InvalidRequest, bad)
    failed = await ops.finish(op_id, fence, "failed", FAILURE)
    assert (failed.state, failed.error, failed.lease_owner) == ("failed", FAILURE, None), failed
    assert failed.doc().error == FAILURE
    return "failed carries its error; no other terminal state does"


async def cancel_from_each_state(env) -> str:
    ops = env.ops
    a = actor(provider=uid(), user=uid())
    queued = (await ops.start(kind(), a, uid(), H1)).operation
    gone = await ops.cancel(queued.operation_id, a)
    assert (gone.state, gone.fence) == ("cancelled", 0) and gone.cancel_requested_at, gone
    assert (await ops.cancel(queued.operation_id, a)) == gone, "a repeated cancel answers"
    await refused(errors.Conflict, ops.lease(queued.operation_id, "w1", 30))
    _, running = await leased(env, who=a)
    asked = await ops.cancel(running.operation_id, a)
    assert asked.state == "cancel_requested" and asked.lease_owner == "w1", asked
    again = await ops.cancel(running.operation_id, a)
    assert again.cancel_requested_at == asked.cancel_requested_at, "the first instant stays"
    draining = await ops.advance(running.operation_id, running.fence, "draining")
    assert draining.state == "cancel_requested", "a worker never clears a cancellation"
    await env.advance(31)
    resumed = await ops.lease(running.operation_id, "w2", 30)
    assert (resumed.state, resumed.fence) == ("cancel_requested", 2), \
        "a new holder sees the cancellation to reconcile"
    ended = await ops.finish(running.operation_id, 2, "cancelled")
    assert ended.state == "cancelled" and ended.lease_owner is None
    for final in ("succeeded", "failed"):
        _, op = await leased(env, who=a)
        done = await ops.finish(op.operation_id, op.fence, final,
                                FAILURE if final == "failed" else None)
        # 0066 (api-artifacts' request): a cancel that loses the race to the finish answers
        # the finished operation as it is - never an error, never a change
        assert (await ops.cancel(op.operation_id, a)) == done == \
            await ops.get(op.operation_id, a), "a finished operation answers a cancel as it is"
    return "queued->cancelled, running->cancel_requested->cancelled, finished answered as is"


async def reads_belong_to_the_owning_tenant_or_an_operator(env) -> str:
    ops, workspace, user = env.ops, uid(), uid()
    a = actor(provider=workspace, user=user)
    op_id = (await ops.start(kind(), a, uid(), H1)).operation.operation_id
    assert (await ops.get(op_id, actor(provider=workspace, user=uid()))).operation_id == op_id
    root = actor(audience="operator", user=uid(), operator=True)
    assert (await ops.get(op_id, root)).operation_id == op_id, "an operator reads every tenant"
    for stranger in (actor(provider=uid(), user=user), actor(org=uid(), user=user),
                     actor(user=user), actor(provider=uid(), user=uid(), operator=False)):
        await refused(errors.NotFound, ops.get(op_id, stranger))
        await refused(errors.NotFound, ops.cancel(op_id, stranger))
    assert (await ops.get(op_id, a)).state == "queued", "a stranger's cancel changed nothing"
    for missing in ("not-a-uuid", uid()):
        await refused(errors.NotFound, ops.get(missing, a))
        await refused(errors.NotFound, ops.cancel(missing, a))
        await refused(errors.NotFound, ops.lease(missing, "w1", 30))
        await refused(errors.NotFound, ops.advance(missing, 1, "x"))
        await refused(errors.NotFound, ops.finish(missing, 1, "succeeded"))
    org = uid()
    consumer = actor(audience="consumer", org=org, user=uid())
    own = (await ops.start(kind(), consumer, uid(), H1)).operation.operation_id
    assert (await ops.get(own, actor(org=org, user=uid()))).operation_id == own
    lone = actor(user=uid())
    mine = (await ops.start(kind(), lone, uid(), H1)).operation.operation_id
    assert (await ops.get(mine, actor(user=lone.user_id))).operation_id == mine
    await refused(errors.NotFound, ops.get(mine, actor(user=uid())))
    assert (await ops.cancel(own, root)).state == "cancelled", "an operator cancels any tenant's"
    return "workspace, organization and user tenants; operator; strangers and bad ids 404"


async def pending_is_the_unleased_unfinished_work_of_its_kinds(env) -> str:
    ops, k, other = env.ops, kind(), kind()
    a = actor(provider=uid(), user=uid())
    q1 = (await ops.start(k, a, uid(), H1)).operation.operation_id
    q2 = (await ops.start(k, a, uid(), H1)).operation.operation_id
    _, held = await leased(env, k, ttl_s=30, who=a)
    _, asked = await leased(env, k, ttl_s=30, who=a)
    await ops.cancel(asked.operation_id, a)
    _, done = await leased(env, k, who=a)
    await ops.finish(done.operation_id, done.fence, "succeeded")
    dropped = (await ops.start(k, a, uid(), H1)).operation.operation_id
    await ops.cancel(dropped, a)
    elsewhere = (await ops.start(other, a, uid(), H1)).operation.operation_id
    now = await ops.pending([k])
    assert set(now) == {q1, q2} and len(now) == 2, now
    assert await ops.pending([kind()]) == (), "an unknown kind has no work"
    await env.advance(31)
    later = await ops.pending([k])
    assert set(later) == {q1, q2, held.operation_id, asked.operation_id}, later
    assert len(await ops.pending([k], limit=1)) == 1
    assert len(await ops.pending([k], limit=0)) == 1, "a limit is at least one"
    both = await ops.pending([k, other], limit=1000)
    assert set(both) == set(later) | {elsewhere}, both
    for _ in range(101):
        await ops.start(other, a, uid(), H1)
    capped = await ops.pending([other], limit=1000)
    assert len(capped) == 100 and capped[0] == elsewhere, "at most 100, oldest first"
    return f"{len(now)} queued, then {len(later)} with expired leases; limits 1..100"


SCENARIOS = (start_replays_the_same_request_and_refuses_another,
             start_refuses_what_it_cannot_store, an_expired_key_is_refused_never_restarted,
             a_lease_is_fenced_and_expires, finish_records_one_terminal_state,
             cancel_from_each_state, reads_belong_to_the_owning_tenant_or_an_operator,
             pending_is_the_unleased_unfinished_work_of_its_kinds)


def run(scenario, env) -> str:
    """A scenario's every call is a contract step: an exception it did not expect (a typed
    refusal where an answer was due, a database error) is the step's assertion failing."""
    try:
        return asyncio.run(scenario(env))
    except AssertionError:
        raise
    except Exception as other:
        raise AssertionError(f"{scenario.__name__}: unexpected {type(other).__name__}: "
                             f"{other}") from other


# ----------------------------------------------------------------------- the fake
def fake_env():
    clock = SimpleNamespace(now=datetime(2026, 10, 1, 12, tzinfo=UTC))

    async def advance(seconds: float) -> None:
        clock.now += timedelta(seconds=seconds)
    return SimpleNamespace(ops=FakeControlOps(now=lambda: clock.now), advance=advance)


def _fake(scenario) -> None:
    print(run(scenario, fake_env()))


def test_fake__start_replays_the_same_request_and_refuses_another() -> None:
    _fake(start_replays_the_same_request_and_refuses_another)


def test_fake__start_refuses_what_it_cannot_store() -> None:
    _fake(start_refuses_what_it_cannot_store)


def test_fake__an_expired_key_is_refused_never_restarted() -> None:
    _fake(an_expired_key_is_refused_never_restarted)


def test_fake__a_lease_is_fenced_and_expires() -> None:
    _fake(a_lease_is_fenced_and_expires)


def test_fake__finish_records_one_terminal_state() -> None:
    _fake(finish_records_one_terminal_state)


def test_fake__cancel_from_each_state() -> None:
    _fake(cancel_from_each_state)


def test_fake__reads_belong_to_the_owning_tenant_or_an_operator() -> None:
    _fake(reads_belong_to_the_owning_tenant_or_an_operator)


def test_fake__pending_is_the_unleased_unfinished_work_of_its_kinds() -> None:
    _fake(pending_is_the_unleased_unfinished_work_of_its_kinds)


def test_input_hash__is_the_sha256_of_the_canonical_body() -> None:
    assert input_hash({"b": 1, "a": [1, "é"]}) == input_hash({"a": [1, "é"], "b": 1})
    assert input_hash({"a": 1}) != input_hash({"a": 2})
    assert input_hash({"a": 1}) == \
        "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"


# ----------------------------------------------------------- PgControlOps's own half
ROW = {"operation_id": "6a000000-0000-4000-8000-000000000001", "kind": "artifact.verify",
       "state": "running", "phase": None, "resource_kind": None, "resource_id": None,
       "actor": {"audience": "session", "provider_org_id": "p", "user_id": "u"},
       "created_at": "2026-10-01T12:00:00.000000Z", "updated_at": "2026-10-01T12:00:00.000000Z",
       "retry_after_s": None, "error": None, "fence": 3, "lease_owner": "w1",
       "lease_until": "2026-10-01T12:00:30.000000Z", "cancel_requested_at": None}
WHO = actor(provider="p", user="u")
SENT_ACTOR = {"audience": "session", "user_id": "u", "org_id": None, "provider_org_id": "p",
              "role": None, "operator": False}


def _pg(*answers):
    conn = _Conn(list(answers))

    async def connect():
        return conn
    return PgControlOps(connect), conn


def _sent(conn) -> list[tuple[str, dict]]:
    return [(sql, params[0].obj) for sql, params in conn.sent]


def test_pg__each_method_sends_its_function_the_callers_arguments() -> None:
    store, conn = _pg({"operation": ROW, "replayed": True, "outcome": {"u": 1}}, ROW, ROW, ROW,
                      ROW, ROW, ["a", "b"])

    async def calls():
        started = await store.start("artifact.verify", WHO, "k", H1, resource_kind="artifact",
                                    resource_id="r", outcome={"u": 1}, retention_s=90000)
        assert started.replayed and started.outcome == {"u": 1}
        assert started.operation.fence == 3 and started.operation.actor == \
            api.Actor(audience="session", provider_org_id="p", user_id="u")
        await store.lease(ROW["operation_id"], "w1", 30)
        await store.advance(ROW["operation_id"], 3, "capacity", retry_after_s=2)
        await store.finish(ROW["operation_id"], 3, "failed", FAILURE)
        await store.cancel(ROW["operation_id"], WHO)
        got = await store.get(ROW["operation_id"], WHO)
        assert got.lease_owner == "w1" and got.doc().state == "running"
        assert await store.pending(["artifact.verify"], limit=7) == ("a", "b")
    asyncio.run(calls())
    op = ROW["operation_id"]
    assert _sent(conn) == [
        ("select infrx.control_op_start(%s)",
         {"kind": "artifact.verify", "actor": SENT_ACTOR, "idempotency_key": "k",
          "input_hash": H1, "resource_kind": "artifact", "resource_id": "r",
          "outcome": {"u": 1}, "retention_s": 90000}),
        ("select infrx.control_op_lease(%s)", {"operation_id": op, "owner": "w1", "ttl_s": 30}),
        ("select infrx.control_op_advance(%s)",
         {"operation_id": op, "fence": 3, "phase": "capacity", "retry_after_s": 2}),
        ("select infrx.control_op_finish(%s)",
         {"operation_id": op, "fence": 3, "state": "failed",
          "error": FAILURE.model_dump(mode="json")}),
        ("select infrx.control_op_cancel(%s)", {"operation_id": op, "actor": SENT_ACTOR}),
        ("select infrx.control_op_get(%s)", {"operation_id": op, "actor": SENT_ACTOR}),
        ("select infrx.control_op_pending(%s)", {"kinds": ["artifact.verify"], "limit": 7}),
    ]


def test_pg__a_refusal_is_its_typed_error_and_an_outage_is_503() -> None:
    for code, cls in (("idempotency_conflict", errors.IdempotencyConflict),
                      ("idempotency_expired", errors.IdempotencyExpired),
                      ("state_conflict", errors.Conflict), ("not_found", errors.NotFound),
                      ("invalid_request", errors.InvalidRequest)):
        store, _ = _pg(_db_error("P0001", f"{code}: no"))
        asyncio.run(refused(cls, store.get(ROW["operation_id"], WHO)))

    async def down():
        raise psycopg.OperationalError("connection refused")
    outage = asyncio.run(refused(errors.DependencyUnavailable,
                                 PgControlOps(down).pending(["artifact.verify"])))
    assert "connection refused" not in str(outage), "an outage never leaks the DSN error"
