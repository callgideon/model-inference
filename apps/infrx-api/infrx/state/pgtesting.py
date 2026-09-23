"""The conformance `Harness` for the PostgreSQL JobStore - TEST SUPPORT, never wired.

The fakes' factories (`contracts/fakes/factories.py`) are the reference; this is the same
contract over a real task-local database. It lives beside the adapter so another track's
suite (E3B's drills) can build the same rig on its own PostgreSQL:

    factory = make_jobstore_factory(fresh_database, dsn_for)
    harness = factory()            # a fresh migrated, seeded, FROZEN database per call

`fresh_database() -> str` returns the name of an empty database carrying every migration,
`infrx/state/test_clock.sql` and `seed(conn)` (see `tests/d/pgstore.py`, which clones a
template); `dsn_for(name) -> str` is a superuser/owner DSN for it.

What a hook does to the database, and why some of them step around a guard:

* `grant`, `balance`, `active_jobs`, `outbox`, `outbox_kinds`, `journal_bytes`,
  `suspend_org`, `revoke_key`, `unentitle`/`entitle`, `retune` are ordinary writes/reads.
* `publish` is one committed chunk through D4's real `append` (the fence, the chunk and the
  publication marker in one transaction), as the fakes' hook is; `stream` is the
  `PgStreamStore` on the same database (`make_streamstore_factory` makes it the port).
* `unrevoke_key` and `set_price` UNDO things production can never undo (0009 makes a
  revocation one-way; `price_versions` is immutable). They disable exactly that one
  trigger for one statement, as the table owner, in the test database only - they model
  the fake's in-memory "withdraw/restore" and prove nothing about the guards, which
  `tests/d/checks*.py` prove on their own.
* The clock is the database's: `infrx_test.freeze` pins `infrx.now()`, and `advance`
  moves it for every session (R7), so `harness.clock.now()` IS the store's `db_now`.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable

from ..contracts import errors, money
from ..contracts.conformance import Harness
from ..contracts.conformance import builders as b
from ..contracts.fakes.support import (DEFAULT_START, CrashAfterCommit, FailurePlan,
                                       SequentialIds)
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import (ChunkEventType, EngineEvent, NormalizedRequest, OutboxEvent,
                                 OutboxKind, PriceSnapshot)
from .jobstore import PgJobStore, connector
from .journal import PgStreamStore

USERS = {b.ORG_A: "1a1a1a1a-0000-4000-8000-0000000000a1",
         b.ORG_B: "2b2b2b2b-0000-4000-8000-0000000000b2"}
KEYS = {b.KEY_A: b.ORG_A, b.KEY_B: b.ORG_B}


def seed(conn) -> None:
    """The builders' world as rows: two organizations with one owner each, KEY_A in ORG_A
    and KEY_B in ORG_B (a key belongs to ONE organization in PostgreSQL - R59-6), and the
    `DEFAULT_PRICE` row for `MODEL`."""
    for org, user in USERS.items():
        conn.execute("insert into auth.users (id, email) values (%s, %s)",
                     (user, f"owner-{org[:4]}@example.com"))
        conn.execute("insert into public.organizations (id, name, slug, created_by) "
                     "values (%s, %s, %s, %s)", (org, f"org {org[:4]}", f"org-{org[:8]}", user))
        conn.execute("insert into public.org_members (org_id, user_id, role) "
                     "values (%s, %s, 'owner')", (org, user))
    for key, org in KEYS.items():
        conn.execute("insert into public.api_keys (id, org_id, created_by, name, prefix, "
                     "key_hash) values (%s, %s, %s, 'k', 'sk-infrx-conform', %s)",
                     (key, org, USERS[org], f"hash-{key}"))
    _insert_price(conn, b.DEFAULT_PRICE)


def _insert_price(conn, snapshot: PriceSnapshot) -> None:
    conn.execute(
        "insert into infrx.price_versions (price_version, model_revision, "
        "input_rate_per_million, output_rate_per_million, token_rules_version, "
        "effective_from, captured_at) values (%s, %s, %s, %s, %s, "
        "least(%s::timestamptz, infrx.now()), %s)",
        (snapshot.price_version, snapshot.model_revision, snapshot.input_rate_per_million,
         snapshot.output_rate_per_million, snapshot.token_rules_version,
         snapshot.captured_at, snapshot.captured_at))


class PgClock:
    """`FakeClock`'s interface over the database clock, frozen at `start`."""

    def __init__(self, conn, start: datetime = DEFAULT_START) -> None:
        self._conn = conn
        self._now, = conn.execute("select infrx_test.freeze(%s)", (start,)).fetchone()

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now, = self._conn.execute("select infrx_test.advance(%s)",
                                        (float(seconds),)).fetchone()
        return self._now

    def at(self, seconds: float) -> datetime:
        return self._now + timedelta(seconds=seconds)


class FailingJobStore:
    """The `FailurePlan` around a real store: `fail` raises before the call (nothing
    committed), `crash_after_commit` lets the transaction commit and then loses the
    answer - exactly a process dying between COMMIT and the response."""

    def __init__(self, store: PgJobStore, plan: FailurePlan) -> None:
        self.store, self.plan = store, plan

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.store, name)
        if not callable(target) or name.startswith("_"):
            return target

        async def call(*args, **kw):
            self.plan.before(name)
            result = await target(*args, **kw)
            self.plan.after_commit(name)
            return result
        return call


class WorkerResults(PgJobStore):
    """TEST RIG (R30): the conformance builders name a result as an opaque text
    (`results/test/result.json`); the real worker stores its result first (`put_result`) and
    completes with the `infrx-result:<job_id>` reference it gets back, which is all the store
    accepts. This does the same with the builder's text, for the lease's job, so a case's
    proposal reaches the settlement the way a worker's does. Replays stay identical (the same
    text is the same stored result); a proposal without a reference is sent unchanged."""

    async def _terminalize(self, lease, outcome, regime: str) -> dict:
        if outcome.result_ref is not None and not outcome.result_ref.startswith("infrx-result:"):
            ref = await self.put_result(lease.job_id, outcome.result_ref)
            outcome = outcome.model_copy(update={"result_ref": ref})
        return await super()._terminalize(lease, outcome, regime)


def assert_test_database(conn) -> None:
    """The same gate as the movable clock (0003): only a task-local `infrx_<task>`
    database may have a production guard stepped around (review SEC-1)."""
    ok, name = conn.execute("select current_database() like 'infrx@_%' escape '@', "
                            "current_database()").fetchone()
    if not ok:
        raise RuntimeError(f"refusing to disable a guard in {name!r}: not a task-local "
                           "infrx_<task> test database")


def _without_trigger(conn, table: str, trigger: str, sql: str, params: tuple) -> None:
    assert_test_database(conn)
    with conn.transaction():
        conn.execute(f"alter table {table} disable trigger {trigger}")
        conn.execute(sql, params)
        conn.execute(f"alter table {table} enable trigger {trigger}")


def hooks(conn, store: PgJobStore, stream: PgStreamStore | None = None) -> dict[str, Callable]:
    def grant(org_id: str, amount) -> Decimal:
        value = money.parse(amount) if not isinstance(amount, Decimal) else amount
        if value < 0:
            raise errors.InvalidRequest(f"a credit grant must not be negative: {value}")
        if value > 0:          # the legacy ledger refuses a zero row; a zero grant is a no-op
            conn.execute("insert into public.credit_ledger (org_id, delta_usd, kind, reason) "
                         "values (%s, %s, 'grant', 'conformance grant')", (org_id, value))
        return balance(org_id)["ledger"]

    def balance(org_id: str) -> dict:
        row = conn.execute("select ledger_total, reserved_total, available from infrx.wallets "
                           "where org_id = %s", (org_id,)).fetchone() or (money.ZERO,) * 3
        return {"ledger": row[0], "reserved": row[1], "available": row[2], "zero": money.ZERO}

    def active_jobs(org_id: str | None = None, key_id: str | None = None) -> list:
        rows = conn.execute(
            "select request_record from infrx.jobs where state in ('preparing','queued',"
            "'running') and (%s::uuid is null or org_id = %s::uuid) and "
            "(%s::uuid is null or key_id = %s::uuid) order by created_at, request_id",
            (org_id, org_id, key_id, key_id)).fetchall()
        return [SimpleNamespace(request=NormalizedRequest.model_validate(r))
                for r, in rows]

    def outbox(aggregate_id: str | None = None) -> list[OutboxEvent]:
        rows = conn.execute(
            "select event_id, aggregate_id, kind, version, payload, available_at "
            "from infrx.outbox where %s::uuid is null or aggregate_id = %s::uuid "
            "order by created_at, array_position(array['prepare_dispatch',"
            "'inference_dispatch','usage_projection','trace_projection','feedback_projection',"
            "'judge_projection','callback_delivery'], kind), event_id",
            (aggregate_id, aggregate_id)).fetchall()
        return [OutboxEvent(event_id=str(e), aggregate_id=str(a), kind=k, version=v,
                            payload=p, available_at=t) for e, a, k, v, p, t in rows]

    def outbox_kinds(aggregate_id: str) -> list[OutboxKind]:
        return [event.kind for event in outbox(aggregate_id)]

    def journal_bytes() -> int:
        # The same sum admission checks (0011 `journal_bytes_charged`), read as the owner.
        return conn.execute("select infrx.journal_bytes_charged()").fetchone()[0]

    def revoke_key(key_id: str) -> None:
        conn.execute("update public.api_keys set revoked_at = infrx.now() "
                     "where id = %s and revoked_at is null", (key_id,))

    def unrevoke_key(key_id: str) -> None:
        _without_trigger(conn, "public.api_keys", "api_keys_identity_guard",
                         "update public.api_keys set revoked_at = null where id = %s",
                         (key_id,))

    def suspend_org(org_id: str) -> None:
        conn.execute("update public.organizations set suspended = true, "
                     "suspended_at = infrx.now(), suspension_reason = 'other' where id = %s",
                     (org_id,))

    def _entitled_set(org_id: str) -> list[str] | None:
        row = conn.execute("select model_ids from infrx.org_entitlements where org_id = %s",
                           (org_id,)).fetchone()
        return None if row is None else row[0]

    def _set_entitlement(org_id: str, models: list[str] | None) -> None:
        conn.execute("insert into infrx.org_entitlements (org_id, model_ids) values (%s, %s) "
                     "on conflict (org_id) do update set model_ids = excluded.model_ids, "
                     "entitlement_version = infrx.org_entitlements.entitlement_version + 1",
                     (org_id, models))

    def unentitle(org_id: str, model_revision: str) -> None:
        current = _entitled_set(org_id)
        if current is None:        # the default set: every priced model
            current = [m for m, in conn.execute(
                "select distinct model_revision from infrx.price_versions").fetchall()]
        _set_entitlement(org_id, [m for m in current if m != model_revision])

    def entitle(org_id: str, model_revision: str) -> None:
        current = _entitled_set(org_id)
        if current is not None and model_revision not in current:
            _set_entitlement(org_id, [*current, model_revision])

    def set_price(model_revision: str, snapshot: PriceSnapshot | None) -> None:
        """Withdraw every effective price of `model_revision`, then make `snapshot` the
        effective one (its row is inserted, or restored and corrected in place)."""
        assert_test_database(conn)
        with conn.transaction():
            conn.execute("alter table infrx.price_versions disable trigger "
                         "price_versions_immutable")
            conn.execute("update infrx.price_versions set effective_to = infrx.now() "
                         "where model_revision = %s and effective_to is null "
                         "and effective_from < infrx.now()", (model_revision,))
            if snapshot is not None:
                found = conn.execute("select 1 from infrx.price_versions where "
                                     "price_version = %s", (snapshot.price_version,)).fetchone()
                if found:
                    conn.execute(
                        "update infrx.price_versions set model_revision = %s, "
                        "input_rate_per_million = %s, output_rate_per_million = %s, "
                        "token_rules_version = %s, captured_at = %s, effective_to = null, "
                        "effective_from = least(%s::timestamptz, infrx.now()) "
                        "where price_version = %s",
                        (snapshot.model_revision, snapshot.input_rate_per_million,
                         snapshot.output_rate_per_million, snapshot.token_rules_version,
                         snapshot.captured_at, snapshot.captured_at, snapshot.price_version))
                else:
                    _insert_price(conn, snapshot)
            conn.execute("alter table infrx.price_versions enable trigger "
                         "price_versions_immutable")

    async def publish(lease):
        """One committed chunk through the real `append` (the fakes' hook, same payload)."""
        return await stream.append(lease, (EngineEvent(type=ChunkEventType.delta,
                                                       payload={"content": "x"}),))

    def retune(**changes) -> PilotSettings:
        store.limits = store.limits.replace(**changes)
        if stream is not None:
            stream.limits = store.limits
        return store.limits

    return {"grant": grant, "balance": balance, "active_jobs": active_jobs, "outbox": outbox,
            "outbox_kinds": outbox_kinds, "journal_bytes": journal_bytes,
            "revoke_key": revoke_key, "unrevoke_key": unrevoke_key, "suspend_org": suspend_org,
            "unentitle": unentitle, "entitle": entitle, "set_price": set_price,
            "retune": retune, "publish": publish,
            "unsettleable": lambda: dict(store.unsettleable)}


def make_jobstore_factory(fresh_database: Callable[[], str], dsn_for: Callable[[str], str],
                          *, keep: int = 6) -> Callable[..., Harness]:
    """`factory(limits=None) -> Harness`, a fresh frozen database per call. The last `keep`
    connections stay open (a case may hold two harnesses); older ones are closed."""
    import psycopg
    opened: deque = deque()

    def factory(limits: PilotSettings | None = None, **_: object) -> Harness:
        name = fresh_database()
        conn = psycopg.connect(dsn_for(name), autocommit=True)
        opened.append(conn)
        while len(opened) > keep:
            opened.popleft().close()
        clock = PgClock(conn)
        store = WorkerResults(connector(dsn_for(name)), limits=limits or DEFAULTS)
        stream = PgStreamStore(connector(dsn_for(name)), limits=limits or DEFAULTS)
        plan = FailurePlan()
        extra = hooks(conn, store, stream)
        extra["store"] = store
        extra["stream"] = FailingJobStore(stream, plan)
        return Harness(port=FailingJobStore(store, plan), clock=clock, ids=SequentialIds(),
                       failures=plan, extra=extra)

    return factory


def make_streamstore_factory(fresh_database: Callable[[], str], dsn_for: Callable[[str], str],
                             **kw: Any) -> Callable[..., Harness]:
    """The same rig with the `PgStreamStore` as the port and the JobStore as `jobs`, one
    `FailurePlan` for both (`crash_after_commit("append")` commits, then raises)."""
    jobs_factory = make_jobstore_factory(fresh_database, dsn_for, **kw)

    def factory(limits: PilotSettings | None = None, **_: object) -> Harness:
        harness = jobs_factory(limits)
        return Harness(port=harness.extra["stream"], clock=harness.clock, ids=harness.ids,
                       failures=harness.failures, extra={**harness.extra, "jobs": harness.port})

    return factory


__all__ = ["CrashAfterCommit", "FailingJobStore", "PgClock", "WorkerResults", "hooks",
           "make_jobstore_factory", "make_streamstore_factory", "seed"]
